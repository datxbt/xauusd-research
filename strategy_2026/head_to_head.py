#!/usr/bin/env python3
"""
Original vs 2026-tuned, across every synthetic 2026 tape.

Runs exactly the two configurations the EA ships as presets:

  ORIGINAL          the 2024-2025 eight-window portfolio
  GEO_2026_NO_H13   the 2026 geometry, seven windows (EA default)

against:
  * real 2026                       - the baseline
  * six 2026-regime replicates      - block-bootstrapped from the 2026 pool,
                                      same volatility and spreads, different
                                      ordering of days
  * the null tape (2026 portion)    - 30-minute blocks, multi-hour structure
                                      destroyed. BOTH portfolios should die
                                      here. If the tuned one survives, its
                                      edge is not coming from breakout
                                      continuation.

Sizing is a fixed 0.02 lots per window for both, so the comparison is not
distorted by the volatility-targeting multiplier.

    python strategy_2026/head_to_head.py
"""

from __future__ import annotations

import argparse
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_2026 as opt          # noqa: E402
import optimize_time_windows as tw   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

ORIGINAL = ["h00_r30_t1", "h01_r60_t3", "h02_r15_t3", "h04_r30_t3",
            "h05_r60_t2", "h06_r60_t3", "h13_r30_t3", "h14_r15_t2"]
TUNED = [f"h{h:02d}_r60_t3" for h in (0, 1, 2, 4, 5, 6, 14)]

REPLICATES = ["rep2026_1", "rep2026_2", "rep2026_3", "rep2026_4",
              "rep2026_5", "regime_2026"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="original vs 2026-tuned on synthetic 2026")
    ap.add_argument("--replicates", nargs="*", default=REPLICATES)
    ap.add_argument("--null", default="null30", help="null tape dir (2026 portion is used)")
    ap.add_argument("--skip-real", action="store_true")
    ap.add_argument("--lots", type=float, default=0.02)
    ap.add_argument("--commission", type=float, default=7.0)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    args = ap.parse_args(argv)
    os.makedirs(RESULTS, exist_ok=True)

    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0])
            if p.name in sorted(set(ORIGINAL + TUNED))]

    def score(data_dir, label, frm, to):
        a = SimpleNamespace(chunksize=args.chunksize, cache_dir=None, lots=args.lots,
                            commission=args.commission, equity=args.equity,
                            from_month=frm, to_month=to)
        raw = opt.run_grid(data_dir, label, a, grid=grid)
        out = []
        for nm, ws in [("ORIGINAL", ORIGINAL), ("TUNED_2026", TUNED)]:
            out.append({"tape": label, "config": nm,
                        **opt.portfolio_metrics(raw, ws, args.equity)})
        return out

    rows = []
    if not args.skip_real:
        rows += score(os.path.join(ROOT, "Monthly_Tick_Data"), "REAL 2026",
                      "2026-01", "2026-07")
    for name in args.replicates:
        d = os.path.join(ROOT, "tick_synth", "output", name)
        if os.path.isdir(d):
            rows += score(d, name, "2026-01", "2026-07")
        else:
            print(f"  skipping {name}: not found", file=sys.stderr)
    nd = os.path.join(ROOT, "tick_synth", "output", args.null)
    if os.path.isdir(nd):
        rows += score(nd, "NULL30 (2026 part)", "2026-01", "2026-06")

    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RESULTS, "head_to_head.csv"), index=False)
    fmt = lambda v: f"{v:,.2f}"

    print("\n=== per tape, %.2f lots/window fixed ===" % args.lots)
    cols = ["tape", "config", "trades", "win_rate", "pf", "net", "per_trade",
            "rr", "maxDD", "sharpe"]
    print(res[cols].to_string(index=False, float_format=fmt))

    piv = res.pivot(index="tape", columns="config", values="net")
    piv["tuned_minus_orig"] = piv.TUNED_2026 - piv.ORIGINAL
    piv["ratio"] = piv.TUNED_2026 / piv.ORIGINAL.replace(0, np.nan)
    print("\n=== net P&L, side by side ===")
    print(piv.to_string(float_format=fmt))

    reps = res[res.tape.isin(args.replicates)]
    if len(reps):
        agg = reps.groupby("config").agg(
            tapes=("net", "size"), median_net=("net", "median"),
            worst=("net", "min"), best=("net", "max"),
            median_pf=("pf", "median"), median_sharpe=("sharpe", "median"),
            median_dd=("maxDD", "median"))
        print("\n=== across the %d synthetic 2026 replicates ===" % (len(reps) // 2))
        print(agg.to_string(float_format=fmt))
        p = reps.pivot(index="tape", columns="config", values="net")
        print(f"\ntuned beats original on {int((p.TUNED_2026 > p.ORIGINAL).sum())}"
              f"/{len(p)} replicates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
