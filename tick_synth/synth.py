#!/usr/bin/env python3
"""
Synthetic XAUUSD tick generator.

Writes monthly csv files in the exact Exness dialect and directory layout, so
any existing backtest runs against them unchanged:

    python backtest_orb.py --data-dir tick_synth/output/rep00 ...

Two methods, for two different questions.

--------------------------------------------------------------------------
 block  -- stationary block bootstrap of the REAL tick stream
--------------------------------------------------------------------------
Each synthetic day is stitched out of blocks of real ticks taken from real
days at the SAME time of day (and, by default, the same weekday).  Blocks
contribute their log RETURNS, inter-arrival times and spreads, never their
price levels, so the spliced path is continuous and every seam carries a real
return.  What survives: intraday liquidity and volatility seasonality, the
real spread process, the real fat tails, the weekend closure.

--block-minutes is the knob that decides what the run means:

  * >= the strategy's holding horizon (1440 = whole days, the default):
    alternative histories.  Within-day structure is intact; only which day
    happened when is resampled.  Use these runs for confidence intervals on
    an edge you already found -- how much of the equity curve was the draw.

  * << the holding horizon (5-60 minutes): a NULL world.  Multi-hour
    directional structure is destroyed by construction while costs, tick
    spacing and volatility stay real.  A session-breakout
    edge should largely DIE here.  If it does not, the edge is not coming
    from the mechanism you think it is -- suspect the cost model, the fill
    logic, or leakage.

--------------------------------------------------------------------------
 parametric -- simulate from the profile, no real ticks reused
--------------------------------------------------------------------------
Inhomogeneous Poisson arrivals at the per-minute-of-week empirical rate;
per-tick returns drawn from the empirical standardised-innovation
distribution, scaled by per-minute-of-week volatility and an AR(1) log-vol
factor for clustering; spreads drawn from the per-hour-of-week empirical
distribution through a Gaussian copula so they stay persistent.  Nothing of
the real path is reused, so this is the right input for stress tests:
--vol-mult 1.5, --spread-mult 2.0, --intensity-mult 0.5.  --vol-mult scales
REALIZED volatility, so thinning the tape with --intensity-mult does not
quietly shrink it too.

Every run writes manifest.json (method, seed, source range, every knob) next
to the data.

    python tick_synth/synth.py --method block --months 2025-01:2025-12 \
        --out tick_synth/output/rep00 --seed 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy.special import ndtr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (NS_PER_DAY, NS_PER_MIN, SPREAD_BINS, SPREAD_UNIT,
                    TickWriter, log, minute_of_week, month_days, month_path,
                    quantize_book)
from daycache import DEFAULT_CACHE, DayPool, day_dow
from tickprofile import derived, load_profile


@dataclass
class Knobs:
    method: str = "block"
    block_minutes: int = 1440
    match_weekday: bool = True
    vol_mult: float = 1.0
    spread_mult: float = 1.0
    intensity_mult: float = 1.0
    reanchor: str = "month"          # month | none
    vol_of_vol: float = 0.35         # parametric only: sd of log-vol factor
    vol_phi: float = 0.9995          # parametric only: log-vol persistence
    spread_phi: float = 0.999        # parametric only: spread copula persistence
    ret_ar1: float | None = None     # parametric only: None = take it from the
                                     # profile's measured variance ratio
    seed: int = 1


# --------------------------------------------------------------------------
# block bootstrap
# --------------------------------------------------------------------------
def _block_day(pool: DayPool, rng, dow: int, k: Knobs):
    """Ticks for one synthetic day: (tod_ns, log-returns, spreads)."""
    step = int(k.block_minutes) * NS_PER_MIN
    ns_parts, ret_parts, spr_parts = [], [], []
    src = None
    for t0 in range(0, NS_PER_DAY, step):
        t1 = min(t0 + step, NS_PER_DAY)
        if src is None or k.block_minutes < 1440:
            src = pool.pick(rng, dow if k.match_weekday else None)
        d = pool.load(src)
        a = int(np.searchsorted(d["tod_ns"], t0, side="left"))
        b = int(np.searchsorted(d["tod_ns"], t1, side="left"))
        if b - a < 1:
            continue
        lm = np.log(d["mid"][a:b])
        seam = float(lm[0] - np.log(d["mid"][a - 1])) if a > 0 else d["open_gap"]
        ns_parts.append(d["tod_ns"][a:b])
        ret_parts.append(np.concatenate([[seam], np.diff(lm)]))
        spr_parts.append(d["spr"][a:b])
    if not ns_parts:
        return None
    return (np.concatenate(ns_parts),
            np.concatenate(ret_parts) * k.vol_mult,
            np.concatenate(spr_parts) * k.spread_mult)


def gen_block(pool: DayPool, months: list, k: Knobs, out_dir: str) -> dict:
    rng = np.random.default_rng(k.seed)
    level = None
    stats = {}
    for month in months:
        anchor = _month_anchor(pool, month)
        if k.reanchor == "month" or level is None:
            level = anchor
        path = month_path(out_dir, month)
        t0 = time.time()
        with TickWriter(path) as w:
            for day_ns in month_days(month):
                dow = day_dow(int(day_ns // NS_PER_DAY))
                if not pool.has_dow(dow):
                    continue                      # market shut on this weekday
                got = _block_day(pool, rng, dow, k)
                if got is None:
                    continue
                tod, ret, spr = got
                mid = level * np.exp(np.cumsum(ret))
                level = float(mid[-1])
                bid, ask = quantize_book(mid, spr)
                w.write(day_ns + tod, bid, ask)
            stats[month] = w.rows
        log(f"  {month}  {w.rows:,} ticks  {time.time() - t0:.1f}s  -> {path}")
    return stats


def _month_anchor(pool: DayPool, month: str) -> float:
    same = [pool.index[key]["first_mid"] for key in pool.keys if key[:7] == month]
    if same:
        return float(same[0])
    return float(pool.index[pool.keys[0]]["first_mid"])


# --------------------------------------------------------------------------
# parametric
# --------------------------------------------------------------------------
def _ar1(rng, n: int, phi: float, sd: float, x0: float | None = None) -> np.ndarray:
    """
    AR(1) path with stationary standard deviation `sd`, continued from `x0`.

    pandas' ewm(adjust=False) is exactly y_t = phi*y_(t-1) + (1-phi)*s_t, which
    is the recursion we want once the innovations are pre-scaled -- and it runs
    in C, which matters at a few hundred thousand ticks per day.
    """
    if n == 0:
        return np.empty(0)
    eta = rng.normal(0.0, sd * np.sqrt(1.0 - phi * phi), n)
    s = eta / (1.0 - phi)
    s[0] = phi * x0 + eta[0] if x0 is not None else rng.normal(0.0, sd)
    return pd.Series(s).ewm(alpha=1.0 - phi, adjust=False).mean().to_numpy()


def _ar1_filter(z: np.ndarray, a: float) -> np.ndarray:
    """y_t = a*y_(t-1) + sqrt(1-a^2)*z_t -- keeps Var(y) = Var(z)."""
    if z.size == 0 or abs(a) < 1e-6:
        return z
    s = z * np.sqrt(1.0 - a * a) / (1.0 - a)
    return pd.Series(s).ewm(alpha=1.0 - a, adjust=False).mean().to_numpy()


def _sample_from_cdf(u: np.ndarray, cdf: np.ndarray, edges: np.ndarray,
                     rng=None) -> np.ndarray:
    """
    Inverse-CDF sampling.  With `rng` the draw is jittered uniformly inside the
    chosen bin (right for a binned continuous variable); without it the bin's
    left edge is returned (right for the spread, whose bins ARE the quote grid).
    """
    j = np.clip(np.searchsorted(cdf, u, side="left"), 0, cdf.size - 1)
    lo = edges[j]
    if rng is None:
        return lo
    return lo + (edges[j + 1] - lo) * rng.random(u.size)


def _ret_ar1(dv: dict, k: Knobs) -> float:
    """
    Tick-return autocorrelation implied by the real variance ratio: for an
    AR(1), Var(sum of m returns) / (m * Var(r)) -> (1+a)/(1-a).
    """
    if k.ret_ar1 is not None:
        return float(np.clip(k.ret_ar1, -0.9, 0.95))
    vr = dv["var_ratio"]
    return float(np.clip((vr - 1.0) / (vr + 1.0), -0.5, 0.9))


def _parametric_day(dv: dict, rng, day_ns: int, k: Knobs, spread_state: float):
    mow_day = minute_of_week(day_ns + np.arange(1440, dtype=np.int64) * NS_PER_MIN)
    lam = dv["rate"][mow_day] * k.intensity_mult
    counts = rng.poisson(np.maximum(lam, 0.0))
    n = int(counts.sum())
    if n == 0:
        return None, spread_state

    minute = np.repeat(np.arange(1440, dtype=np.int64), counts)
    tod = np.sort(minute * NS_PER_MIN
                  + (rng.random(n) * NS_PER_MIN).astype(np.int64))
    mow = mow_day[np.minimum(tod // NS_PER_MIN, 1439)]

    # returns: empirical innovations, given the real short-horizon trendiness
    # by an AR(1), scaled by seasonal sigma and an AR(1) log-vol factor.
    # `norm` undoes the variance the two multiplicative factors add, so the
    # per-tick return variance still equals the profiled sigma^2.
    z = _sample_from_cdf(rng.random(n), dv["z_cdf"], dv["z_edges"], rng)
    u = _ar1_filter(z, _ret_ar1(dv, k))
    x = _ar1(rng, n, k.vol_phi, k.vol_of_vol)
    mult = np.exp(x - 0.5 * k.vol_of_vol ** 2)      # E[mult] = 1
    norm = np.sqrt(dv["z_var"] * np.exp(k.vol_of_vol ** 2))
    # thinning the tape would otherwise thin realized volatility with it, so
    # --vol-mult is defined on realized interval vol, not on the single tick
    vscale = k.vol_mult / np.sqrt(max(k.intensity_mult, 1e-9))
    ret = dv["sigma"][mow] * mult * u / norm * vscale

    # spreads: persistent uniform through a Gaussian copula, then empirical
    g = _ar1(rng, n, k.spread_phi, 1.0, x0=spread_state)
    u = np.clip(ndtr(g), 1e-9, 1 - 1e-9)
    spr = np.empty(n)
    how = mow // 60
    edges = np.arange(SPREAD_BINS + 1) * SPREAD_UNIT
    for h in np.unique(how):
        sel = how == h
        spr[sel] = _sample_from_cdf(u[sel], dv["spread_cdf"][h], edges)
    spr *= dv["spread_scale"][mow] * k.spread_mult
    return (tod, ret, spr), float(g[-1])


def gen_parametric(prof: dict, months: list, k: Knobs, out_dir: str) -> dict:
    dv = derived(prof)
    rng = np.random.default_rng(k.seed)
    gap_r = prof["gap_r"]
    opens = {m: o for m, o in zip(prof["months"], prof["month_open"])
             if np.isfinite(o)}
    fallback = float(next(iter(opens.values()))) if opens else 2000.0

    level, spread_state, stats = None, 0.0, {}
    for month in months:
        anchor = float(opens.get(month, fallback))
        if k.reanchor == "month" or level is None:
            level = anchor
        path = month_path(out_dir, month)
        t0 = time.time()
        with TickWriter(path) as w:
            for day_ns in month_days(month):
                got, spread_state = _parametric_day(dv, rng, int(day_ns), k,
                                                    spread_state)
                if got is None:
                    continue
                tod, ret, spr = got
                if gap_r.size:                    # open the day with a real gap
                    ret[0] += float(gap_r[rng.integers(gap_r.size)]) * k.vol_mult
                mid = level * np.exp(np.cumsum(ret))
                level = float(mid[-1])
                bid, ask = quantize_book(mid, spr)
                w.write(int(day_ns) + tod, bid, ask)
            stats[month] = w.rows
        log(f"  {month}  {w.rows:,} ticks  {time.time() - t0:.1f}s  -> {path}")
    return stats


# --------------------------------------------------------------------------
def parse_months(spec: str) -> list:
    """'2025-01:2025-12' or '2025-03' or a comma-separated mix."""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            a, b = part.split(":")
            cur = np.datetime64(a, "M")
            while cur <= np.datetime64(b, "M"):
                out.append(str(cur))
                cur += 1
        else:
            out.append(str(np.datetime64(part, "M")))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--method", choices=("block", "parametric"), default="block")
    ap.add_argument("--months", required=True,
                    help="calendar to generate, e.g. 2025-01:2025-12")
    ap.add_argument("--out", required=True, help="output directory for this run")
    ap.add_argument("--seed", type=int, default=1)

    b = ap.add_argument_group("block bootstrap")
    b.add_argument("--cache-dir", default=DEFAULT_CACHE)
    b.add_argument("--source-from", default=None,
                   help="first source month to draw days from")
    b.add_argument("--source-to", default=None,
                   help="last source month to draw days from")
    b.add_argument("--block-minutes", type=int, default=1440,
                   help="1440 = alternative histories; 5-60 = null world")
    b.add_argument("--any-weekday", action="store_true",
                   help="draw source blocks from any weekday, not the matching one")

    p = ap.add_argument_group("parametric")
    p.add_argument("--profile", default="tick_synth/profiles/xauusd.npz")
    p.add_argument("--vol-of-vol", type=float, default=0.35)
    p.add_argument("--vol-phi", type=float, default=0.9995)
    p.add_argument("--spread-phi", type=float, default=0.999)
    p.add_argument("--ret-ar1", type=float, default=None,
                   help="tick-return autocorrelation; default is whatever the "
                        "profile's 5-minute variance ratio implies")

    s = ap.add_argument_group("stress knobs (both methods)")
    s.add_argument("--vol-mult", type=float, default=1.0)
    s.add_argument("--spread-mult", type=float, default=1.0)
    s.add_argument("--intensity-mult", type=float, default=1.0,
                   help="parametric only: scales tick arrival rate")
    s.add_argument("--reanchor", choices=("month", "none"), default="month",
                   help="reset the synthetic price level to the real month open")
    args = ap.parse_args(argv)

    months = parse_months(args.months)
    if not months:
        log("nothing to generate")
        return 1
    k = Knobs(method=args.method, block_minutes=args.block_minutes,
              match_weekday=not args.any_weekday, vol_mult=args.vol_mult,
              spread_mult=args.spread_mult, intensity_mult=args.intensity_mult,
              reanchor=args.reanchor, vol_of_vol=args.vol_of_vol,
              vol_phi=args.vol_phi, spread_phi=args.spread_phi,
              ret_ar1=args.ret_ar1, seed=args.seed)

    os.makedirs(args.out, exist_ok=True)
    log(f"{args.method}: {len(months)} months -> {args.out}")
    t0 = time.time()
    if args.method == "block":
        pool = DayPool(args.cache_dir, args.source_from, args.source_to)
        log(f"  source pool: {len(pool.keys)} days "
            f"({pool.keys[0]} .. {pool.keys[-1]})")
        source = {"cache_dir": os.path.abspath(args.cache_dir),
                  "pool_days": len(pool.keys),
                  "pool_from": pool.keys[0], "pool_to": pool.keys[-1]}
        stats = gen_block(pool, months, k, args.out)
    else:
        prof = load_profile(args.profile)
        dv = derived(prof)
        log(f"  variance ratio {dv['var_ratio']:.2f} -> tick-return AR(1) "
            f"{_ret_ar1(dv, k):.3f}")
        source = {"profile": os.path.abspath(args.profile),
                  "profile_meta": prof["meta"],
                  "var_ratio": round(dv["var_ratio"], 4),
                  "effective_ret_ar1": round(_ret_ar1(dv, k), 4)}
        stats = gen_parametric(prof, months, k, args.out)

    manifest = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "knobs": asdict(k), "months": months, "source": source,
                "ticks_per_month": stats,
                "total_ticks": int(sum(stats.values())),
                "runtime_sec": round(time.time() - t0, 1)}
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    log(f"done: {manifest['total_ticks']:,} ticks in {manifest['runtime_sec']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
