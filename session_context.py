#!/usr/bin/env python3
"""
Build a per-session context table for the VWAP pullback study.

Re-uses the tick loader from backtest_vwap_pullback.py and writes one row per
trading session describing the *character* of that day (trend vs range,
volatility, spread, how far price stretched from VWAP).  Join it to trades.csv
on `session_date` to ask which circumstances the strategy actually likes.

    python session_context.py --out vwap_pullback_results/sessions.csv
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

from backtest_vwap_pullback import (EPS, NS_PER_SEC, discover_files, load_subbars,
                                    split_sessions)


def session_row(s) -> dict:
    """Descriptive stats for one session (all prices are mid unless noted)."""
    o, h, l, c = float(s.o[0]), float(s.h.max()), float(s.l.min()), float(s.c[-1])
    rng = max(h - l, EPS)
    vwap = s.get_vwap("typical", "equal")
    dev = (s.c - vwap) / np.maximum(vwap, EPS) * 100.0

    ret5 = np.diff(np.log(np.maximum(s.c, EPS)))
    rv = float(np.std(ret5, ddof=1) * np.sqrt(len(s.c)) * 100) if ret5.size > 2 else np.nan

    # spread proxy: ask - bid at every sub-bar close
    spread = s.sub["ac"] - s.sub["bc"]

    # how often price crossed back through VWAP -> range days oscillate
    side = np.sign(s.c - vwap)
    crosses = int(np.count_nonzero(np.diff(side[side != 0]) != 0)) if np.any(side != 0) else 0

    ts = pd.Timestamp(s.day_ns, unit="ns")
    return {
        "session_date": ts.date(),
        "weekday": ts.day_name(),
        "bars": len(s),
        "ticks": int(s.n.sum()),
        "open": o, "high": h, "low": l, "close": c,
        "range_pct": (h - l) / o * 100.0,
        "ret_pct": (c - o) / o * 100.0,
        # efficiency ratio: 1.0 = straight-line trend day, ~0 = choppy range day
        "trendiness": abs(c - o) / rng,
        "vwap_drift_pct": float((vwap[-1] - vwap[0]) / max(vwap[0], EPS) * 100.0),
        "realized_vol_pct": rv,
        "max_dev_pct": float(np.max(np.abs(dev))),
        "mean_abs_dev_pct": float(np.mean(np.abs(dev))),
        "vwap_crosses": crosses,
        "avg_spread": float(np.mean(spread)),
        "median_spread": float(np.median(spread)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="per-session context for the VWAP study")
    ap.add_argument("--data-dir", default="Monthly_Tick_Data")
    ap.add_argument("--from-month", default=None)
    ap.add_argument("--to-month", default=None)
    ap.add_argument("--subbar-seconds", type=int, default=5)
    ap.add_argument("--bar-minutes", type=int, default=5)
    ap.add_argument("--session-start-hour", type=int, default=0)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--min-session-bars", type=int, default=100)
    ap.add_argument("--out", default="vwap_pullback_results/sessions.csv")
    ap.add_argument("--hourly-out", default="vwap_pullback_results/hourly_context.csv")
    args = ap.parse_args(argv)

    files = discover_files(args.data_dir, args.from_month, args.to_month)
    if not files:
        print(f"no csv files under {args.data_dir}", file=sys.stderr)
        return 1

    rows, hourly = [], []
    carry = None
    t0 = time.time()
    for i, path in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {os.path.basename(path)}", file=sys.stderr, flush=True)
        sub = load_subbars(path, args.subbar_seconds, args.chunksize, args.cache_dir, True)
        sessions, carry = split_sessions(sub, args.subbar_seconds, args.bar_minutes,
                                         args.session_start_hour, args.min_session_bars,
                                         carry, final=(i == len(files)))
        for s in sessions:
            rows.append(session_row(s))
            # per-hour absolute VWAP stretch, to see when the setup even appears
            vwap = s.get_vwap("typical", "equal")
            dev = np.abs((s.c - vwap) / np.maximum(vwap, EPS) * 100.0)
            hrs = pd.to_datetime(s.t, unit="ns").hour
            spread_bar = np.add.reduceat(s.sub["ac"] - s.sub["bc"], s.s0) / np.maximum(
                (s.s1 - s.s0), 1)
            for h in np.unique(hrs):
                m = hrs == h
                hourly.append({"session_date": pd.Timestamp(s.day_ns, unit="ns").date(),
                               "hour": int(h), "mean_abs_dev_pct": float(dev[m].mean()),
                               "bar_range_pct": float(np.mean((s.h[m] - s.l[m])
                                                              / np.maximum(s.c[m], EPS)) * 100),
                               "avg_spread": float(np.mean(spread_bar[m]))})

    df = pd.DataFrame(rows).sort_values("session_date")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    df.to_csv(args.out, index=False)
    pd.DataFrame(hourly).to_csv(args.hourly_out, index=False)
    print(f"{len(df)} sessions -> {args.out}  ({time.time() - t0:.0f}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
