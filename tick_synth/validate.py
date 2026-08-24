#!/usr/bin/env python3
"""
Does the synthetic tape behave like the real one?

Compares a synthetic run against real months on the properties a backtest
actually depends on -- not on how pretty the chart looks:

  liquidity   ticks per day, and their shape across the hours of the day
  costs       tick-weighted mean / median spread, overall and by session
  returns     5-minute return sd, kurtosis, tail quantiles
  memory      autocorrelation of returns (should be ~0) and of |returns|
              (should be clearly positive -- volatility clustering)
  daily       daily range and close-to-close move

The sub-bar builder from backtest_vwap_pullback is reused, so the numbers are
computed off exactly the same representation the strategies see, and the npz
cache is shared with the backtests.

    python tick_synth/validate.py --real Monthly_Tick_Data --synth tick_synth/output/rep00 \
        --real-from 2025-01 --real-to 2025-12
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from common import log                                    # noqa: E402
from backtest_vwap_pullback import load_subbars           # noqa: E402
from common import discover_months                        # noqa: E402

SUB_SEC = 5
BAR_SEC = 300


def _group_last(values: np.ndarray, key: np.ndarray):
    starts = np.concatenate([[0], np.flatnonzero(np.diff(key)) + 1])
    ends = np.append(starts[1:], key.size)
    return values[ends - 1], starts, ends


class Accum:
    """Streams sub-bars from many months into one set of summary statistics."""

    def __init__(self):
        self.hour_ticks = np.zeros(24)
        self.hour_spread_w = np.zeros(24)
        self.hour_spread_n = np.zeros(24)
        self.spread_hist = np.zeros(4001)
        self.day_ticks: list = []
        self.day_range: list = []
        self.day_move: list = []
        self.day_no: list = []          # epoch day number, for plotting
        self.day_close: list = []
        self.rets: list = []

    def feed(self, bars: dict) -> None:
        idx = bars["idx"]
        if idx.size == 0:
            return
        sec = idx * SUB_SEC
        n = bars["n"].astype(np.float64)
        hour = (sec % 86_400) // 3_600
        day = sec // 86_400
        spread = bars["ac"] - bars["bc"]

        self.hour_ticks += np.bincount(hour, weights=n, minlength=24)
        self.hour_spread_w += np.bincount(hour, weights=spread * n, minlength=24)
        self.hour_spread_n += np.bincount(hour, weights=n, minlength=24)
        sb = np.clip(np.rint(spread * 1000).astype(np.int64), 0, 4000)
        self.spread_hist += np.bincount(sb, weights=n, minlength=4001)

        # per day
        dstarts = np.concatenate([[0], np.flatnonzero(np.diff(day)) + 1])
        dends = np.append(dstarts[1:], day.size)
        self.day_ticks.extend(np.add.reduceat(n, dstarts).tolist())
        hi = np.maximum.reduceat(bars["h"], dstarts)
        lo = np.minimum.reduceat(bars["l"], dstarts)
        op = bars["o"][dstarts]
        cl = bars["c"][dends - 1]
        self.day_range.extend(((hi - lo) / op * 100).tolist())
        self.day_move.extend(((cl - op) / op * 100).tolist())
        self.day_no.extend(day[dstarts].tolist())
        self.day_close.extend(cl.tolist())

        # 5-minute closes -> log returns, never spanning a day boundary
        bar = sec // BAR_SEC
        close, bstarts, bends = _group_last(bars["c"], bar)
        bday = day[bends - 1]
        r = np.diff(np.log(close))
        same = bday[1:] == bday[:-1]
        self.rets.append(r[same])

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        r = np.concatenate(self.rets) if self.rets else np.zeros(0)
        r = r[np.isfinite(r)]
        day_ticks = np.asarray(self.day_ticks)
        day_ticks = day_ticks[day_ticks > 1000]            # ignore holiday stubs
        w = self.hour_spread_n
        hour_spread = np.divide(self.hour_spread_w, w, out=np.zeros(24), where=w > 0)

        cum = np.cumsum(self.spread_hist)
        tot = max(cum[-1], 1.0)
        med = float(np.searchsorted(cum, 0.5 * tot)) / 1000.0
        p90 = float(np.searchsorted(cum, 0.9 * tot)) / 1000.0
        mean_spread = float(self.hour_spread_w.sum() / max(self.hour_spread_n.sum(), 1))

        out = {
            "days": float(day_ticks.size),
            "ticks/day": float(day_ticks.mean()) if day_ticks.size else np.nan,
            "ticks/day sd": float(day_ticks.std()) if day_ticks.size else np.nan,
            "spread mean": mean_spread,
            "spread median": med,
            "spread p90": p90,
            "spread 07-16 UTC": float(hour_spread[7:17].mean()),
            "spread 21-23 UTC": float(hour_spread[21:24].mean()),
            "5m ret sd (bp)": float(r.std() * 1e4),
            "5m ret kurtosis": _kurt(r),
            "5m |ret| p99 (bp)": float(np.quantile(np.abs(r), 0.99) * 1e4) if r.size else np.nan,
            "acf(r) lag1": _acf(r, 1),
            "acf(r) lag12": _acf(r, 12),
            "acf(|r|) lag1": _acf(np.abs(r), 1),
            "acf(|r|) lag12": _acf(np.abs(r), 12),
            "daily range %": float(np.mean(self.day_range)) if self.day_range else np.nan,
            "daily |move| %": float(np.mean(np.abs(self.day_move))) if self.day_move else np.nan,
        }
        out["_hour_ticks"] = self.hour_ticks / max(day_ticks.size, 1)
        out["_hour_spread"] = hour_spread
        return out


def _kurt(x: np.ndarray) -> float:
    if x.size < 4:
        return np.nan
    d = x - x.mean()
    m2 = float(np.mean(d * d))
    m4 = float(np.mean(d ** 4))
    return m4 / (m2 * m2) - 3.0 if m2 > 0 else np.nan


def _acf(x: np.ndarray, lag: int) -> float:
    if x.size <= lag + 2:
        return np.nan
    a, b = x[:-lag], x[lag:]
    a = a - a.mean()
    b = b - b.mean()
    den = np.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / den) if den > 0 else np.nan


def scan_accum(data_dir: str, from_month: str | None, to_month: str | None,
               cache_dir: str | None, chunksize: int, label: str) -> Accum:
    """Stream every month under `data_dir` into one Accum."""
    months = discover_months(data_dir, from_month, to_month)
    if not months:
        raise SystemExit(f"no monthly csv found under {data_dir}")
    acc = Accum()
    for i, (key, path) in enumerate(months, 1):
        log(f"  [{label} {i}/{len(months)}] {key}")
        acc.feed(load_subbars(path, SUB_SEC, chunksize, cache_dir, verbose=False))
    return acc


def scan(data_dir: str, from_month: str | None, to_month: str | None,
         cache_dir: str | None, chunksize: int, label: str) -> dict:
    return scan_accum(data_dir, from_month, to_month, cache_dir, chunksize,
                      label).summary()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real", default="Monthly_Tick_Data")
    ap.add_argument("--synth", required=True)
    ap.add_argument("--real-from", default=None)
    ap.add_argument("--real-to", default=None)
    ap.add_argument("--synth-from", default=None)
    ap.add_argument("--synth-to", default=None)
    ap.add_argument("--cache-dir", default=None,
                    help="shared sub-bar cache (same one the backtests use)")
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--out", default=None, help="write the comparison to csv")
    args = ap.parse_args(argv)

    real = scan(args.real, args.real_from, args.real_to, args.cache_dir,
                args.chunksize, "real")
    syn = scan(args.synth, args.synth_from, args.synth_to, args.cache_dir,
               args.chunksize, "synth")

    keys = [k for k in real if not k.startswith("_")]
    rows = []
    for k in keys:
        a, b = real[k], syn[k]
        ratio = b / a if (a not in (0, None) and np.isfinite(a) and a != 0) else np.nan
        rows.append({"metric": k, "real": a, "synth": b, "synth/real": ratio})
    df = pd.DataFrame(rows)
    print("\n=== real vs synthetic ===")
    print(df.to_string(index=False, float_format=lambda v: f"{v:,.4f}"))

    hours = pd.DataFrame({
        "hour": np.arange(24),
        "real ticks": real["_hour_ticks"], "synth ticks": syn["_hour_ticks"],
        "real spread": real["_hour_spread"], "synth spread": syn["_hour_spread"]})
    print("\n=== mean ticks and spread by hour of day (UTC) ===")
    print(hours.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        df.to_csv(args.out, index=False)
        hours.to_csv(os.path.splitext(args.out)[0] + "_by_hour.csv", index=False)
        log(f"wrote {args.out}")

    print("\nwhat to look for: ticks/day and spread within ~10% of real; "
          "5m sd and kurtosis in the same ballpark; acf(r) near zero in both; "
          "acf(|r|) positive in both -- if it is ~0 in the synthetic run the "
          "volatility clustering did not survive, and vol-sensitive strategies "
          "will be flattered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
