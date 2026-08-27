#!/usr/bin/env python3
"""
Separate the durable part of the 2026 re-tune from the fitted part.

WHY THIS EXISTS
---------------
optimize_2026.py validates candidates on block-bootstrapped 2026 replicates.
Those replicates draw blocks matched BY TIME OF DAY, so "hour 00 was good in
2026" is baked into every one of them. They test robustness to the ordering
of days; they cannot test whether the choice of hours was a 150-session
fluke. A portfolio picked by hour on real 2026 will beat the incumbent on
those replicates almost by construction.

So the re-tune is split into two claims that fail differently:

  GEOMETRY  2026's higher volatility rewards longer brackets and larger
            targets. This is one decision applied to every window, driven by
            a mechanism, and it is testable without choosing hours at all.

  HOURS     the specific set of eight hours that scored best on 150 sessions.
            This is 216-way selection on a small sample and is the part most
            likely to be noise.

Two tests:
  1. Uniform-geometry portfolios keep the INCUMBENT hours and change only the
     bracket length and target. If these capture most of the gain, the hours
     never needed touching.
  2. A walk-forward inside 2026 - choose on Jan-Apr, score on May-Jul. Small,
     but genuinely out of sample for the hour choice.

    python strategy_2026/decompose.py --validate rep2026_1 ... --walkforward
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import optimize_2026 as opt                     # noqa: E402  (same folder)
import optimize_time_windows as tw              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
INCUMBENT_HOURS = [0, 1, 2, 4, 5, 6, 13, 14]


def uniform(hours, rng_min: int, tgt: float) -> list:
    return [f"h{h:02d}_r{rng_min}_t{tgt:g}" for h in hours]


def build_candidates() -> dict:
    with open(os.path.join(RESULTS, "candidates.json"), encoding="utf-8") as f:
        base = json.load(f)
    cands = {"A_incumbent": base["A_incumbent"], "B_top8": base["B_top8"]}
    # geometry-only variants: incumbent hours, one bracket/target choice for all
    for rmin in (15, 30, 60):
        for tgt in (1.0, 2.0, 3.0):
            cands[f"G_r{rmin}_t{tgt:g}"] = uniform(INCUMBENT_HOURS, rmin, tgt)
    return cands


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="split the 2026 re-tune into geometry vs hours")
    ap.add_argument("--validate", nargs="*", default=[])
    ap.add_argument("--walkforward", action="store_true")
    ap.add_argument("--real-dir", default=os.path.join(ROOT, "Monthly_Tick_Data"))
    ap.add_argument("--from-month", default="2026-01")
    ap.add_argument("--to-month", default="2026-07")
    ap.add_argument("--lots", type=float, default=0.02)
    ap.add_argument("--commission", type=float, default=7.0)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)
    os.makedirs(RESULTS, exist_ok=True)
    fmt = lambda v: f"{v:,.2f}"

    cands = build_candidates()
    keep = sorted({w for v in cands.values() for w in v})
    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0]) if p.name in keep]

    # ---------- test 1: geometry vs hours, across replicates ----------
    if args.validate:
        rows = []
        for name in args.validate:
            d = os.path.join(ROOT, "tick_synth", "output", name)
            if not os.path.isdir(d):
                continue
            raw = opt.run_grid(d, name, args, grid=grid)
            for k, v in cands.items():
                rows.append({"replicate": name, "candidate": k,
                             **opt.portfolio_metrics(raw, v, args.equity)})
        res = pd.DataFrame(rows)
        res.to_csv(os.path.join(RESULTS, "decompose_replicates.csv"), index=False)
        agg = res.groupby("candidate").agg(
            reps=("net", "size"), median_net=("net", "median"),
            worst=("net", "min"), best=("net", "max"),
            median_pf=("pf", "median"), median_sharpe=("sharpe", "median"),
            median_trades=("trades", "median")).sort_values("median_net", ascending=False)
        print("\n=== geometry-only variants vs the fitted hour pick, on 2026 replicates ===")
        print("G_* keep the INCUMBENT hours and change only bracket length / target.")
        print(agg.to_string(float_format=fmt))

        inc = agg.loc["A_incumbent", "median_net"]
        top = agg.loc["B_top8", "median_net"]
        best_g = agg.drop(index=["A_incumbent", "B_top8"])["median_net"].max()
        bg = agg.drop(index=["A_incumbent", "B_top8"])["median_net"].idxmax()
        share = (best_g - inc) / max(top - inc, 1e-9) * 100
        print(f"\n  incumbent            {inc:>10,.0f}")
        print(f"  best geometry-only   {best_g:>10,.0f}   ({bg})")
        print(f"  fitted hour pick     {top:>10,.0f}   (B_top8)")
        print(f"  -> changing geometry alone captures {share:.0f}% of the gain, "
              f"with no hour selection at all")

    # ---------- test 2: walk-forward inside 2026 ----------
    if args.walkforward:
        raw = opt.run_grid(args.real_dir, "real2026", args)
        h1 = raw[raw["date"] < opt.SPLIT]
        h2 = raw[raw["date"] >= opt.SPLIT]
        bp = opt.best_per_hour(h1)
        picked = list(bp.head(8)["param"])
        print(f"\n=== walk-forward inside 2026 ===")
        print(f"  chosen on Jan-Apr ({h1['date'].nunique()} sessions): {', '.join(picked)}")
        rows = []
        for k, v in [("picked_on_H1", picked), ("A_incumbent", cands["A_incumbent"]),
                     ("B_top8 (saw all 2026)", cands["B_top8"])]:
            rows.append({"portfolio": k, "period": "H1 Jan-Apr (in-sample)",
                         **opt.portfolio_metrics(h1, v, args.equity)})
            rows.append({"portfolio": k, "period": "H2 May-Jul (OUT of sample)",
                         **opt.portfolio_metrics(h2, v, args.equity)})
        wf = pd.DataFrame(rows)
        wf.to_csv(os.path.join(RESULTS, "walkforward_2026.csv"), index=False)
        print(wf[["portfolio", "period", "trades", "win_rate", "pf", "net",
                  "per_trade"]].to_string(index=False, float_format=fmt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
