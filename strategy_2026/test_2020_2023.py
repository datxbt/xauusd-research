#!/usr/bin/env python3
"""
Out-of-sample test on 2020-2023 — the cleanest one available.

The eight windows were selected on 2024-01..2025-06 and confirmed on
2025-07..2026-06. Nothing in 2020-2023 was used for anything: not selection,
not validation, not the synthetic tapes. It is a different era of the same
instrument, which is exactly the discrimination that was missing:

    real gold-specific effect   vs   pattern fitted to 30 months

Two questions, and they fail differently:

  1. Does the PORTFOLIO make money on 2020-2023?
  2. Do the SAME HOURS come out on top? (the fragile half - hour selection
     could not be tested by the replicates, which match blocks by time of day)

    python strategy_2026/test_2020_2023.py
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
SELECTED_HOURS = [0, 1, 2, 4, 5, 6, 13, 14]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="2020-2023 out-of-sample test")
    ap.add_argument("--from-month", default="2020-02")   # 2020-01 is partial
    ap.add_argument("--to-month", default="2023-12")
    ap.add_argument("--lots", type=float, default=0.02)
    ap.add_argument("--commission", type=float, default=7.0)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)
    os.makedirs(RESULTS, exist_ok=True)
    fmt = lambda v: f"{v:,.2f}"

    raw = opt.run_grid(os.path.join(ROOT, "Monthly_Tick_Data"), "2020-2023", args)
    raw["year"] = raw["date"].dt.year
    raw.to_csv(os.path.join(RESULTS, "oos_2020_2023_trades.csv"), index=False)

    # ---- 1. the portfolios ----
    print("\n=== PORTFOLIOS on 2020-2023 (never seen) ===")
    rows = []
    for nm, ws in [("ORIGINAL (8)", ORIGINAL), ("TUNED_2026 (7)", TUNED)]:
        rows.append({"portfolio": nm, **opt.portfolio_metrics(raw, ws, args.equity)})
    res = pd.DataFrame(rows)
    print(res[["portfolio", "trades", "days", "win_rate", "pf", "net",
               "per_trade", "rr", "maxDD", "sharpe"]].to_string(index=False, float_format=fmt))

    print("\n=== by year ===")
    yr = []
    for y, g in raw.groupby("year"):
        for nm, ws in [("ORIGINAL", ORIGINAL), ("TUNED_2026", TUNED)]:
            m = opt.portfolio_metrics(g, ws, args.equity)
            yr.append({"year": y, "portfolio": nm, "trades": m.get("trades", 0),
                       "win_rate": m.get("win_rate", np.nan), "pf": m.get("pf", np.nan),
                       "net": m.get("net", np.nan)})
    y = pd.DataFrame(yr)
    print(y.pivot(index="year", columns="portfolio", values=["pf", "net"])
          .to_string(float_format=fmt))

    # ---- 2. do the same hours win? ----
    hp = raw.groupby("hour")["pnl"].agg(net="sum", trades="size", per_trade="mean")
    hp["selected_2024_25"] = hp.index.isin(SELECTED_HOURS)
    hp = hp.sort_values("net", ascending=False)
    hp.to_csv(os.path.join(RESULTS, "oos_2020_2023_hours.csv"))
    print("\n=== hour profile on 2020-2023 (pooled over range/target) ===")
    print(hp.to_string(float_format=fmt))

    sel = hp[hp.selected_2024_25]
    oth = hp[~hp.selected_2024_25]
    print(f"\n  the 8 hours picked on 2024-25:  {int((sel.net > 0).sum())}/8 positive here"
          f"   mean per-trade {sel.per_trade.mean():+.2f}")
    print(f"  the other 16 hours:             {int((oth.net > 0).sum())}/{len(oth)} positive"
          f"   mean per-trade {oth.per_trade.mean():+.2f}")

    ranks = hp.reset_index().reset_index().set_index("hour")["index"]
    sel_rank = ranks[ranks.index.isin(SELECTED_HOURS)].mean()
    print(f"  average rank of the selected hours: {sel_rank + 1:.1f} of 24"
          f"   (11.5 = no skill)")

    # rank correlation against the 2024-2026 hour profile, if available
    prev = os.path.join(RESULTS, "hour_profile_2026.csv")
    if os.path.exists(prev):
        p = pd.read_csv(prev).set_index("hour")["per_trade"]
        common = hp.index.intersection(p.index)
        r = hp.loc[common, "per_trade"].corr(p.loc[common], method="spearman")
        print(f"\n  Spearman of hour ranking, 2020-2023 vs 2026: {r:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
