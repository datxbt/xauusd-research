#!/usr/bin/env python3
"""
Trailing realised volatility - the number the preset switch runs on.

DEFINITION (identical to the EA's TrailingRealizedVol / DailyRealizedVol)

  For each completed UTC session:
      r_i           = log(close_i / close_{i-1})   over M5 closes in that day
      daily_vol_pct = stdev(r, ddof=1) * sqrt(n) * 100

  trailing_vol = mean of daily_vol_pct over the last N completed sessions
                 (N = 20 by default, and strictly BEFORE today - no look-ahead)

It is a DAILY figure in percent, not annualised. Multiply by sqrt(252) for an
annualised number: 1.24 daily -> roughly 20% annualised.

    python strategy_2026/trailing_vol.py
    python strategy_2026/trailing_vol.py --lookback 20 --history 15
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SWITCH_DOWN = 1.1     # below this: run ORIGINAL
SWITCH_UP   = 1.3     # above this: run TUNED_2026 (hysteresis band between)


def verdict(v: float) -> str:
    if np.isnan(v):
        return "unknown"
    if v < SWITCH_DOWN:
        return "ORIGINAL"
    if v > SWITCH_UP:
        return "TUNED_2026"
    return "hold current (inside the %.1f-%.1f band)" % (SWITCH_DOWN, SWITCH_UP)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="trailing realised volatility and preset verdict")
    ap.add_argument("--sessions", default=os.path.join(ROOT, "market_context", "sessions.csv"))
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--history", type=int, default=12, help="months of monthly means to show")
    args = ap.parse_args(argv)

    if not os.path.exists(args.sessions):
        print(f"missing {args.sessions} - run session_context.py first", file=sys.stderr)
        return 1

    s = pd.read_csv(args.sessions, parse_dates=["session_date"]).sort_values("session_date")
    s = s[["session_date", "realized_vol_pct", "range_pct"]].dropna()

    # strictly backward looking, exactly as the EA does it
    s["trailing"] = s["realized_vol_pct"].rolling(args.lookback, min_periods=5).mean().shift(1)

    latest_date = s["session_date"].iloc[-1].date()
    # the value an EA would use on the session AFTER the last one in the file
    current = float(s["realized_vol_pct"].tail(args.lookback).mean())

    print(f"data through {latest_date}   lookback {args.lookback} sessions\n")
    print("=== last %d sessions ===" % min(args.history, len(s)))
    tail = s.tail(min(args.history, len(s)))
    for _, r in tail.iterrows():
        t = r["trailing"]
        print(f"  {r['session_date'].date()}  daily {r['realized_vol_pct']:>6.3f}   "
              f"trailing {t:>6.3f}" % () if not np.isnan(t) else
              f"  {r['session_date'].date()}  daily {r['realized_vol_pct']:>6.3f}   trailing    n/a")

    print(f"\n=== monthly means ===")
    m = s.set_index("session_date")["realized_vol_pct"].resample("ME").mean().tail(args.history)
    for k, v in m.items():
        bar = "#" * int(round(v * 12))
        print(f"  {k.strftime('%Y-%m')}  {v:>6.3f}  {bar}")

    print(f"\n=== yearly means ===")
    for y, g in s.groupby(s["session_date"].dt.year):
        print(f"  {y}  {g['realized_vol_pct'].mean():>6.3f}   "
              f"(mean daily range {g['range_pct'].mean():.2f}%)")

    print("\n" + "=" * 62)
    print(f"CURRENT trailing vol ({args.lookback} sessions to {latest_date}): {current:.3f}")
    print(f"annualised equivalent: {current * np.sqrt(252):.1f}%")
    print(f"switch rule  ->  {verdict(current)}")
    print("=" * 62)
    print(f"\n  < {SWITCH_DOWN}      run ORIGINAL")
    print(f"  {SWITCH_DOWN} - {SWITCH_UP}   hold whichever you are already running (hysteresis)")
    print(f"  > {SWITCH_UP}      run TUNED_2026")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
