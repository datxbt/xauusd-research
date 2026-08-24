#!/usr/bin/env python3
"""
Opening Range Breakout (ORB) for XAUUSD tick data.

PRE-REGISTERED HYPOTHESIS  (written before any result was inspected)
--------------------------------------------------------------------
Overnight information accumulates while liquidity is thin.  When a major
session opens, that information is repriced in a burst of institutional order
flow, and the direction of the burst persists for longer than a coin flip.
Therefore: define a range over the first R minutes after a session open, and
trade the first break of that range in the direction of the break.

This is a mechanism-driven prior from market microstructure, NOT a pattern
mined from this dataset.

RULES (fixed in advance)
------------------------
  * Anchor at a session open: 07:00 UTC (London) or 13:00 UTC (New York).
  * Opening range = highest / lowest mid price in [anchor, anchor + R).
  * Entry: the FIRST break of either side, and only one trade per session.
      long  when ask >= range_high  -> fill at max(range_high, ask)
      short when bid <= range_low   -> fill at min(range_low, bid)
  * Stop  = the opposite side of the opening range.
  * Target = entry +/- m * range_size.
  * The break must occur within MAX_WAIT hours of the range closing, else skip.
  * Flat at session end regardless.
  * Costs: the real bid/ask spread in the tick data + commission.

PASS / FAIL BAR (fixed in advance)
---------------------------------------------------------------
  TRAIN 2024-01..2025-06, HOLDOUT 2025-07..2026-06.
  A config is a candidate only if, on TRAIN, it has >= 150 trades AND its 95%
  bootstrap CI (resampling sessions) of mean session P&L lies wholly above
  zero.  The single best candidate by CI lower bound is then scored ONCE on
  the holdout.  Nothing qualifying on TRAIN => report "no", leave holdout shut.

The full 18-config grid is reported, not just the best, so the family-level
result is visible rather than the luckiest cell.

    python backtest_orb.py --cache-dir <dir> --holdout
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from tickdata import (CONTRACT_SIZE, EPS, NS_PER_SEC, apply_money_management,
                      compute_metrics, discover_files, load_subbars, split_sessions)

TRAIN = ("2024-01", "2025-06")
HOLDOUT = ("2025-07", "2026-06")
MIN_TRADES = 150
BOOT = 20000
SEED = 19990304
BIG = np.iinfo(np.int64).max


@dataclass(frozen=True)
class OrbParams:
    name: str
    anchor_hour: int = 13            # UTC session open
    range_minutes: int = 30
    target_mult: float = 2.0         # target = m * opening range size
    stop_mult: float = 1.0           # stop = opposite side of the range
    max_wait_hours: float = 4.0      # break must happen this soon after the range
    min_range_pct: float = 0.05      # ignore degenerate ranges
    max_range_pct: float = 2.0       # ignore already-exhausted ranges
    lots: float = 0.10
    commission_per_lot: float = 7.0
    slippage: float = 0.0
    # kept so the shared money-management helper can be reused unchanged
    risk_pct: float = 0.0
    max_lots: float = 100.0


def run_session_orb(s, p: OrbParams, sub_sec: int) -> list:
    """At most one breakout trade for this session."""
    sub = s.sub
    ns = sub["idx"] * sub_sec * NS_PER_SEC
    tod = (ns % (86_400 * NS_PER_SEC)) // NS_PER_SEC          # UTC second of day

    a0 = p.anchor_hour * 3600
    a1 = a0 + p.range_minutes * 60
    in_range = (tod >= a0) & (tod < a1)
    if in_range.sum() < 10:                                    # no data in the window
        return []

    hi = float(sub["h"][in_range].max())
    lo = float(sub["l"][in_range].min())
    size = hi - lo
    if size <= EPS:
        return []
    ref = float(sub["c"][in_range][-1])
    range_pct = size / max(ref, EPS) * 100.0
    if not (p.min_range_pct <= range_pct <= p.max_range_pct):
        return []

    scan = np.flatnonzero((tod >= a1) & (tod <= a1 + p.max_wait_hours * 3600))
    if scan.size == 0:
        return []
    i0, i1 = int(scan[0]), int(scan[-1])

    up = np.flatnonzero(sub["ah"][i0:i1 + 1] >= hi)
    dn = np.flatnonzero(sub["bl"][i0:i1 + 1] <= lo)
    u = int(up[0]) if up.size else BIG
    d = int(dn[0]) if dn.size else BIG
    if u == BIG and d == BIG:
        return []
    # both sides broken inside the same sub-bar -> unknowable order, skip it
    if u == d:
        return []

    if u < d:
        side, k = 1, i0 + u
        entry = max(hi, float(sub["ao"][k])) + p.slippage
        stop = lo
        target = entry + p.target_mult * size
    else:
        side, k = -1, i0 + d
        entry = min(lo, float(sub["bo"][k])) - p.slippage
        stop = hi
        target = entry - p.target_mult * size

    if p.stop_mult != 1.0:                                     # widen/tighten the stop
        stop = entry - side * p.stop_mult * size

    # ---- walk forward to the stop, the target, or the session close ----------
    end = sub["idx"].size
    if side > 0:
        adverse, favourable, close_px = sub["bl"][k:end], sub["bh"][k:end], sub["bc"][k:end]
        hit_sl = np.flatnonzero(adverse <= stop)
        hit_tp = np.flatnonzero(favourable >= target)
    else:
        adverse, favourable, close_px = sub["ah"][k:end], sub["al"][k:end], sub["ac"][k:end]
        hit_sl = np.flatnonzero(adverse >= stop)
        hit_tp = np.flatnonzero(favourable <= target)

    si = int(hit_sl[0]) if hit_sl.size else BIG
    ti = int(hit_tp[0]) if hit_tp.size else BIG
    if si == BIG and ti == BIG:
        rel, exit_px, reason = len(close_px) - 1, float(close_px[-1]), "session_end"
    elif si <= ti:                                             # tie -> stop first
        rel, exit_px, reason = si, float(stop), "sl"
        if p.slippage:
            exit_px += -p.slippage if side > 0 else p.slippage
    else:
        rel, exit_px, reason = ti, float(target), "tp"

    return [{
        "param": p.name,
        "session": s.day_ns,
        "side": side,
        "entry_time": int(ns[k]),
        "exit_time": int(ns[k + rel]),
        "bars_held": int(rel),
        "entry_price": entry,
        "exit_price": exit_px,
        "stop_price": stop,
        "target_price": target,
        "range_size": size,
        "range_pct": range_pct,
        "exit_reason": reason,
        "price_pnl": (exit_px - entry) * side,
        "risk_price": abs(entry - stop),
    }]


def build_grid() -> list:
    grid = []
    for anchor, rmin, tmult in itertools.product([7, 13], [15, 30, 60], [1.0, 2.0, 3.0]):
        grid.append(OrbParams(name=f"ORB_{anchor:02d}h_r{rmin}_t{tmult:g}",
                              anchor_hour=anchor, range_minutes=rmin, target_mult=tmult))
    return grid


def collect(files, grid, args, label) -> pd.DataFrame:
    trades, carry = [], None
    t0 = time.time()
    for i, path in enumerate(files, 1):
        print(f"  [{label} {i}/{len(files)}] {os.path.basename(path)}",
              file=sys.stderr, flush=True)
        sub = load_subbars(path, args.subbar_seconds, args.chunksize, args.cache_dir, False)
        sessions, carry = split_sessions(sub, args.subbar_seconds, args.bar_minutes,
                                         0, args.min_session_bars, carry,
                                         final=(i == len(files)))
        for s in sessions:
            for p in grid:
                trades.extend(run_session_orb(s, p, args.subbar_seconds))
    print(f"  {label}: {len(trades):,} trades in {time.time() - t0:.0f}s", file=sys.stderr)
    return pd.DataFrame(trades)


def evaluate(raw: pd.DataFrame, grid, equity: float, rng) -> pd.DataFrame:
    rows = []
    for p in grid:
        one = raw[raw["param"] == p.name] if len(raw) else raw
        if one.empty:
            continue
        one = apply_money_management(one.copy(), p, equity)
        met = compute_metrics(one, equity)
        sess = one.groupby("session_date")["pnl"].sum().to_numpy()
        if len(sess) >= 20:
            idx = rng.integers(0, len(sess), (BOOT, len(sess)))
            m = sess[idx].mean(axis=1)
            lo, hi = np.percentile(m, [2.5, 97.5])
        else:
            lo = hi = np.nan
        rows.append({"param": p.name, "trades": met["trades"], "sessions": len(sess),
                     "win_rate": met["win_rate_pct"], "pf": met["profit_factor"],
                     "net_pnl": met["net_pnl"], "exp_usd": met["expectancy"],
                     "exp_R": met["expectancy_R"], "mean_session": sess.mean(),
                     "ci_lo": lo, "ci_hi": hi, "max_dd_pct": met["max_drawdown_pct"],
                     "sharpe": met["sharpe_daily"], "tp_rate": met["tp_rate_pct"]})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pre-registered opening range breakout test")
    ap.add_argument("--data-dir", default="Monthly_Tick_Data")
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--subbar-seconds", type=int, default=5)
    ap.add_argument("--bar-minutes", type=int, default=5)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--min-session-bars", type=int, default=100)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--outdir", default="orb_results")
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args(argv)

    rng = np.random.default_rng(SEED)
    grid = build_grid()
    os.makedirs(args.outdir, exist_ok=True)
    fmt = lambda v: f"{v:,.2f}"
    cols = ["param", "trades", "win_rate", "tp_rate", "pf", "net_pnl", "exp_usd",
            "mean_session", "ci_lo", "ci_hi", "max_dd_pct"]

    tr = evaluate(collect(discover_files(args.data_dir, *TRAIN), grid, args, "train"),
                  grid, args.equity, rng).sort_values("ci_lo", ascending=False)
    tr.to_csv(os.path.join(args.outdir, "orb_train.csv"), index=False)
    print(f"\n=== ORB TRAIN {TRAIN[0]}..{TRAIN[1]} - all {len(grid)} configs ===")
    print(tr[cols].to_string(index=False, float_format=fmt))
    print(f"\nfamily: positive net P&L {(tr.net_pnl > 0).sum()}/{len(tr)}   "
          f"PF>1 {(tr.pf > 1).sum()}/{len(tr)}   median PF {tr.pf.median():.3f}")

    ok = tr[(tr["trades"] >= MIN_TRADES) & (tr["ci_lo"] > 0)]
    print(f"\nmeeting the pre-declared bar (>= {MIN_TRADES} trades AND CI above zero): "
          f"{len(ok)}/{len(tr)}")
    if ok.empty:
        print("\nVERDICT: nothing qualifies on TRAIN. Holdout not touched.")
        return 0

    pick = ok.iloc[0]["param"]
    print(f"\nselected on TRAIN: {pick}")
    if not args.holdout:
        print("(re-run with --holdout to score it out of sample)")
        return 0

    sel = [p for p in grid if p.name == pick]
    ho = evaluate(collect(discover_files(args.data_dir, *HOLDOUT), sel, args, "holdout"),
                  sel, args.equity, rng)
    ho.to_csv(os.path.join(args.outdir, "orb_holdout.csv"), index=False)
    print(f"\n=== HOLDOUT {HOLDOUT[0]}..{HOLDOUT[1]} for {pick} ===")
    print(ho[cols].to_string(index=False, float_format=fmt))
    r = ho.iloc[0]
    print("\nVERDICT: " + ("SURVIVES - holdout CI is entirely above zero" if r["ci_lo"] > 0
                           else "fails out of sample - holdout CI includes zero"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
