#!/usr/bin/env python3
"""
Empirical profile of the real XAUUSD tick stream.

One streaming pass over the monthly files produces a small .npz holding
everything the parametric generator needs and everything the validator
compares against:

  * tick arrival intensity per minute-of-week -- this is what carries the
    session structure: the weekend close, the thin Asian hours, the 13:00 UTC
    burst, the daily rollover break
  * bid-ask spread distribution per hour-of-week, plus a per-minute mean so a
    sampled spread can be rescaled to the exact minute
  * per-tick log-return variance per minute-of-week (intraday vol seasonality)
  * the pooled distribution of EWMA-standardised per-tick returns -- the fat
    tails, kept non-parametrically instead of assuming a shape
  * the distribution of gap returns across breaks longer than --gap-sec
    (weekend and rollover gaps), used to open each synthetic day
  * per-month opening / closing mid, used to re-anchor synthetic price levels

    python tick_synth/tickprofile.py --out tick_synth/profiles/xauusd.npz
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (HOW, MOW, NS_PER_DAY, NS_PER_SEC, SPREAD_BINS, SPREAD_UNIT,
                    discover_months, iter_ticks, log, minute_of_week)

VAR_BUCKET_SEC = 300           # horizon the variance ratio is measured over
STD_BINS = 4000
STD_CLIP = 25.0
EWMA_LAMBDA = 0.99
GAP_RESERVOIR = 50_000


class Profiler:
    def __init__(self, gap_sec: float, seed: int):
        self.gap_ns = int(gap_sec * NS_PER_SEC)
        self.rng = np.random.default_rng(seed)

        self.mow_ticks = np.zeros(MOW, dtype=np.int64)
        self.mow_spread_sum = np.zeros(MOW, dtype=np.float64)
        self.mow_spread_n = np.zeros(MOW, dtype=np.int64)
        self.how_spread_hist = np.zeros((HOW, SPREAD_BINS), dtype=np.int64)
        self.mow_r2 = np.zeros(MOW, dtype=np.float64)
        self.mow_rn = np.zeros(MOW, dtype=np.int64)
        self.std_hist = np.zeros(STD_BINS, dtype=np.int64)

        self.days = set()
        self.gap_dt: list = []
        self.gap_r: list = []
        self.gap_seen = 0

        # variance-ratio accumulator: realized VAR_BUCKET_SEC variance vs the
        # sum of per-tick variances inside the same bucket
        self.vr_num = 0.0
        self.vr_den = 0.0
        self.vr_bucket = None
        self.vr_s1 = 0.0
        self.vr_s2 = 0.0

        self.prev_ns = None
        self.prev_mid = None
        self.ewma_var = None
        self.n_ticks = 0
        self.months: list = []
        self.month_open: list = []
        self.month_close: list = []

    # ------------------------------------------------------------------
    def _gap_sample(self, dt: np.ndarray, r: np.ndarray) -> None:
        """Reservoir-sample the weekend / rollover gap returns."""
        for d, x in zip(dt, r):
            self.gap_seen += 1
            if len(self.gap_dt) < GAP_RESERVOIR:
                self.gap_dt.append(float(d))
                self.gap_r.append(float(x))
            else:
                j = int(self.rng.integers(self.gap_seen))
                if j < GAP_RESERVOIR:
                    self.gap_dt[j], self.gap_r[j] = float(d), float(x)

    def _feed_innovations(self, r: np.ndarray) -> None:
        """Histogram r / EWMA-sigma: the standardised innovations."""
        r2 = r * r
        if self.ewma_var is None:
            self.ewma_var = max(float(r2.mean()), 1e-18)
        # seed the filter with the carried state, then drop it again
        seeded = np.concatenate([[self.ewma_var], r2])
        var = (pd.Series(seeded).ewm(alpha=1.0 - EWMA_LAMBDA, adjust=False)
               .mean().to_numpy())
        prev = var[:-1]                      # variance known before each tick
        self.ewma_var = float(var[-1])
        z = np.clip(r / np.sqrt(np.maximum(prev, 1e-20)), -STD_CLIP, STD_CLIP)
        zb = np.clip(((z + STD_CLIP) / (2 * STD_CLIP) * STD_BINS).astype(np.int64),
                     0, STD_BINS - 1)
        self.std_hist += np.bincount(zb, minlength=STD_BINS)

    def _feed_variance_ratio(self, rns: np.ndarray, r: np.ndarray) -> None:
        """
        Accumulate realized VAR_BUCKET_SEC variance against the sum of squared
        tick returns in the same bucket.  Their ratio says how much of the real
        multi-tick move is trend rather than independent jitter -- the
        generator reproduces it with an AR(1) on the tick returns.
        """
        b = rns // (VAR_BUCKET_SEC * NS_PER_SEC)
        rel = (b - b[0]).astype(np.int64)
        m = int(rel[-1]) + 1
        s1 = np.bincount(rel, weights=r, minlength=m)
        s2 = np.bincount(rel, weights=r * r, minlength=m)
        if self.vr_bucket is not None:
            if self.vr_bucket == b[0]:
                s1[0] += self.vr_s1
                s2[0] += self.vr_s2
            else:
                self.vr_num += self.vr_s1 ** 2
                self.vr_den += self.vr_s2
        self.vr_num += float((s1[:-1] ** 2).sum())
        self.vr_den += float(s2[:-1].sum())
        self.vr_bucket, self.vr_s1, self.vr_s2 = int(b[-1]), float(s1[-1]), float(s2[-1])

    def feed(self, ns: np.ndarray, bid: np.ndarray, ask: np.ndarray) -> None:
        if ns.size == 0:
            return
        self.n_ticks += ns.size
        mid = (bid + ask) / 2.0
        spread = ask - bid
        mow = minute_of_week(ns)

        self.mow_ticks += np.bincount(mow, minlength=MOW)
        self.mow_spread_sum += np.bincount(mow, weights=spread, minlength=MOW)
        self.mow_spread_n += np.bincount(mow, minlength=MOW)

        sb = np.clip(np.rint(spread / SPREAD_UNIT).astype(np.int64), 0, SPREAD_BINS - 1)
        flat = (mow // 60) * SPREAD_BINS + sb
        self.how_spread_hist += np.bincount(
            flat, minlength=HOW * SPREAD_BINS).reshape(HOW, SPREAD_BINS)

        self.days.update(np.unique(ns // NS_PER_DAY).tolist())

        # per-tick log returns, carried across chunk and file boundaries
        if self.prev_mid is None:
            r = np.diff(np.log(mid))
            dt = np.diff(ns)
            rmow, rns = mow[1:], ns[1:]
        else:
            r = np.diff(np.log(np.concatenate([[self.prev_mid], mid])))
            dt = np.diff(np.concatenate([[self.prev_ns], ns]))
            rmow, rns = mow, ns
        self.prev_ns, self.prev_mid = int(ns[-1]), float(mid[-1])
        if r.size == 0:
            return

        gap = dt > self.gap_ns
        if gap.any():
            self._gap_sample(dt[gap] / NS_PER_SEC, r[gap])
        keep = ~gap
        r, rmow, rns = r[keep], rmow[keep], rns[keep]
        if r.size == 0:
            return

        self.mow_r2 += np.bincount(rmow, weights=r * r, minlength=MOW)
        self.mow_rn += np.bincount(rmow, minlength=MOW)
        self._feed_innovations(r)
        self._feed_variance_ratio(rns, r)

    # ------------------------------------------------------------------
    def finish(self, path: str, meta: dict) -> None:
        dow_days = np.zeros(7, dtype=np.int64)
        for d in self.days:
            dow_days[int((d + 3) % 7)] += 1          # epoch day 0 was a Thursday
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        meta = dict(meta, n_ticks=int(self.n_ticks), n_days=len(self.days),
                    gap_seen=int(self.gap_seen))
        np.savez_compressed(
            path,
            mow_ticks=self.mow_ticks,
            mow_spread_sum=self.mow_spread_sum,
            mow_spread_n=self.mow_spread_n,
            how_spread_hist=self.how_spread_hist,
            mow_r2=self.mow_r2,
            mow_rn=self.mow_rn,
            std_hist=self.std_hist,
            std_clip=np.float64(STD_CLIP),
            vr_num=np.float64(self.vr_num),
            vr_den=np.float64(self.vr_den),
            vr_bucket_sec=np.int64(VAR_BUCKET_SEC),
            dow_days=dow_days,
            gap_dt=np.asarray(self.gap_dt, dtype=np.float64),
            gap_r=np.asarray(self.gap_r, dtype=np.float64),
            months=np.asarray(self.months, dtype=object),
            month_open=np.asarray(self.month_open, dtype=np.float64),
            month_close=np.asarray(self.month_close, dtype=np.float64),
            meta=np.asarray(json.dumps(meta)),
        )
        log(f"wrote {path}  ({self.n_ticks:,} ticks, {len(self.days)} days)")


def load_profile(path: str) -> dict:
    with np.load(path, allow_pickle=True) as z:
        p = {k: z[k] for k in z.files}
    p["meta"] = json.loads(str(p["meta"]))
    p["months"] = [str(m) for m in p["months"]]
    return p


def derived(p: dict) -> dict:
    """Rates, sigmas and spread laws implied by a raw profile."""
    dow_days = np.maximum(p["dow_days"], 1)
    per_min_days = np.repeat(dow_days, 1440).astype(np.float64)
    rate = p["mow_ticks"] / per_min_days                       # ticks per minute

    sigma = np.zeros(MOW)
    ok = p["mow_rn"] > 30
    sigma[ok] = np.sqrt(p["mow_r2"][ok] / p["mow_rn"][ok])     # per-tick return sd
    how_r2 = p["mow_r2"].reshape(HOW, 60).sum(1)
    how_rn = p["mow_rn"].reshape(HOW, 60).sum(1)
    how_sigma = np.where(how_rn > 30, np.sqrt(how_r2 / np.maximum(how_rn, 1)), 0.0)
    sigma[~ok] = np.repeat(how_sigma, 60)[~ok]
    fallback = float(np.median(sigma[sigma > 0])) if (sigma > 0).any() else 1e-6
    sigma[sigma <= 0] = fallback

    mow_spread = np.where(p["mow_spread_n"] > 0,
                          p["mow_spread_sum"] / np.maximum(p["mow_spread_n"], 1), 0.0)
    hist = p["how_spread_hist"].astype(np.float64)
    how_tot = hist.sum(1)
    how_spread_mean = np.where(
        how_tot > 0,
        (hist * (np.arange(SPREAD_BINS) * SPREAD_UNIT)).sum(1) / np.maximum(how_tot, 1),
        0.0)
    scale = np.ones(MOW)
    hs = np.repeat(how_spread_mean, 60)
    good = (hs > 0) & (mow_spread > 0)
    scale[good] = mow_spread[good] / hs[good]

    cdf = np.cumsum(hist, axis=1)
    tot = cdf[:, -1:].copy()
    tot[tot == 0] = 1.0
    spread_cdf = cdf / tot

    vr = float(p["vr_num"]) / max(float(p["vr_den"]), 1e-300)
    vr = float(np.clip(vr, 0.25, 6.0))

    zh = p["std_hist"].astype(np.float64)
    zc = np.cumsum(zh)
    z_cdf = zc / max(float(zc[-1]), 1.0)
    clip = float(p["std_clip"])
    z_edges = np.linspace(-clip, clip, zh.size + 1)

    centres = (z_edges[:-1] + z_edges[1:]) / 2.0
    z_var = float((zh * centres * centres).sum() / max(zh.sum(), 1.0))

    return {"rate": rate, "sigma": sigma, "spread_cdf": spread_cdf,
            "spread_scale": scale, "mow_spread": mow_spread,
            "how_spread_mean": how_spread_mean,
            "z_cdf": z_cdf, "z_edges": z_edges, "z_var": max(z_var, 1e-9),
            "var_ratio": vr}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="Monthly_Tick_Data")
    ap.add_argument("--out", default="tick_synth/profiles/xauusd.npz")
    ap.add_argument("--from-month", default=None)
    ap.add_argument("--to-month", default=None)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--gap-sec", type=float, default=300.0,
                    help="a return spanning a break longer than this is a gap "
                         "return, not a tick return")
    ap.add_argument("--seed", type=int, default=19990304)
    args = ap.parse_args(argv)

    months = discover_months(args.data_dir, args.from_month, args.to_month)
    if not months:
        log(f"no monthly csv found under {args.data_dir}")
        return 1

    prof = Profiler(args.gap_sec, args.seed)
    t0 = time.time()
    for i, (key, path) in enumerate(months, 1):
        t1 = time.time()
        first = last = None
        n0 = prof.n_ticks
        for ns, bid, ask in iter_ticks(path, args.chunksize):
            if ns.size:
                if first is None:
                    first = float((bid[0] + ask[0]) / 2.0)
                last = float((bid[-1] + ask[-1]) / 2.0)
            prof.feed(ns, bid, ask)
        prof.months.append(key)
        prof.month_open.append(first if first is not None else np.nan)
        prof.month_close.append(last if last is not None else np.nan)
        log(f"[{i}/{len(months)}] {key}  {prof.n_ticks - n0:,} ticks "
            f"in {time.time() - t1:.1f}s")

    prof.finish(args.out, {"data_dir": os.path.abspath(args.data_dir),
                           "from_month": months[0][0], "to_month": months[-1][0],
                           "gap_sec": args.gap_sec, "seed": args.seed,
                           "runtime_sec": round(time.time() - t0, 1)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
