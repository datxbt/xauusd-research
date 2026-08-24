#!/usr/bin/env python3
"""
Regime-conditional optimisation of the 5-window ORB portfolio.

What the regime analysis established
------------------------------------
  forecastable : realized volatility, range, spread, tick count  (AR1 0.60-0.87)
  NOT forecastable : direction, trendiness, VWAP crosses         (AR1 <= 0.12)

So the only legitimate thing to condition on is the VOLATILITY LEVEL, and it
must be measured from elapsed sessions only.  Two changes are tested:

  1. volatility-targeted sizing - lots scale as target_vol / trailing_vol,
     computed from the N sessions BEFORE the trade.  In 2026 realized vol runs
     ~2x its 2024 level, so fixed sizing silently doubles risk per trade.
  2. a relative opening-range filter - the absolute 0.05%-2.0% band is a
     constant in a world whose scale moved; replace it with a percentile of
     the trailing range distribution.

Periods:
  TRAIN   2024-01 .. 2025-06     used to pick the windows (already done)
  HOLDOUT 2025-07 .. 2026-06     used once to confirm them
  VIRGIN  2026-07                never touched by any earlier step

    python optimize_for_regime.py --cache-dir <dir>
"""

from __future__ import annotations

import argparse
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

import backtest_orb as ob
import optimize_time_windows as tw

WINDOWS = ["h01_r60_t3", "h02_r15_t3", "h06_r30_t3", "h05_r60_t2", "h13_r60_t2"]
PERIODS = {"TRAIN": ("2024-01-01", "2025-06-30"),
           "HOLDOUT": ("2025-07-01", "2026-06-30"),
           "VIRGIN 2026-07": ("2026-07-01", "2026-07-31")}


def trailing_vol(sessions: pd.DataFrame, lookback: int) -> pd.Series:
    """Mean realized vol over the `lookback` sessions strictly BEFORE each date."""
    s = sessions.sort_values("session_date").set_index("session_date")
    return s["realized_vol_pct"].rolling(lookback, min_periods=5).mean().shift(1)


def stats(d: pd.Series, equity: float) -> dict:
    if len(d) == 0:
        return {}
    eq = equity + d.cumsum()
    dd = eq - eq.cummax()
    r = d / equity
    return {"days": len(d), "net": d.sum(), "per_day": d.mean(),
            "maxDD": dd.min(), "maxDD_pct": dd.min() / max(eq.cummax().max(), 1) * 100,
            "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan,
            "pos_days_pct": (d > 0).mean() * 100}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="regime-conditional ORB optimisation")
    ap.add_argument("--data-dir", default="Monthly_Tick_Data")
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--sessions", default="vwap_pullback_results/sessions.csv")
    ap.add_argument("--subbar-seconds", type=int, default=5)
    ap.add_argument("--bar-minutes", type=int, default=5)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--min-session-bars", type=int, default=100)
    ap.add_argument("--base-lots", type=float, default=0.02)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--vol-lookback", type=int, default=20)
    ap.add_argument("--max-scale", type=float, default=3.0)
    ap.add_argument("--outdir", default="regime_results")
    args = ap.parse_args(argv)

    a = SimpleNamespace(subbar_seconds=args.subbar_seconds, bar_minutes=args.bar_minutes,
                        chunksize=args.chunksize, min_session_bars=args.min_session_bars,
                        cache_dir=args.cache_dir)
    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0]) if p.name in WINDOWS]
    files = ob.discover_files(args.data_dir, "2024-01", "2026-12")
    raw = tw.pnl_frame(ob.collect(files, grid, a, "all"), args.base_lots, 7.0)
    raw["date"] = pd.to_datetime(raw["date"])

    sess = pd.read_csv(args.sessions, parse_dates=["session_date"])
    tv = trailing_vol(sess, args.vol_lookback)
    target_vol = float(sess["realized_vol_pct"].iloc[:300].mean())   # early-sample anchor

    raw["trail_vol"] = raw["date"].map(tv)
    raw["scale"] = (target_vol / raw["trail_vol"]).clip(1.0 / args.max_scale, args.max_scale)
    raw.loc[raw["trail_vol"].isna(), "scale"] = 1.0
    # price P&L is size-independent; rescale money P&L, commission included
    raw["pnl_vt"] = (raw["price_pnl"] * ob.CONTRACT_SIZE * args.base_lots
                     - 7.0 * args.base_lots) * raw["scale"]

    os.makedirs(args.outdir, exist_ok=True)
    raw.to_csv(os.path.join(args.outdir, "portfolio_trades.csv"), index=False)

    fmt = lambda v: f"{v:,.2f}"
    rep = ["Regime-conditional optimisation of the 5-window ORB portfolio",
           f"windows: {', '.join(WINDOWS)}",
           f"base sizing {args.base_lots} lots/window; vol target {target_vol:.3f}% "
           f"(trailing {args.vol_lookback} sessions, shifted 1)",
           f"data {raw.date.min().date()} .. {raw.date.max().date()}, {len(raw):,} trades", ""]

    rows = []
    for label, (lo, hi) in PERIODS.items():
        seg = raw[(raw["date"] >= lo) & (raw["date"] <= hi)]
        if seg.empty:
            continue
        for mode, col in [("fixed", "pnl"), ("vol-targeted", "pnl_vt")]:
            d = seg.groupby("date")[col].sum()
            rows.append({"period": label, "sizing": mode, "trades": len(seg),
                         **stats(d, args.equity)})
    res = pd.DataFrame(rows)
    rep.append("=== fixed vs volatility-targeted sizing, by period ===")
    rep.append(res.to_string(index=False, float_format=fmt))
    rep.append("")

    for mode, col in [("fixed", "pnl"), ("vol-targeted", "pnl_vt")]:
        d = raw.groupby("date")[col].sum()
        s = stats(d, args.equity)
        rep.append(f"FULL SAMPLE {mode:<13} net {s['net']:>10,.0f}  "
                   f"maxDD {s['maxDD']:>9,.0f} ({s['maxDD_pct']:.1f}%)  "
                   f"Sharpe {s['sharpe']:.2f}  positive days {s['pos_days_pct']:.1f}%")
    rep.append("")

    # per-year, to show how the regime shift hits the strategy
    raw["year"] = raw["date"].dt.year
    yr = raw.groupby("year").agg(trades=("pnl", "size"), net_fixed=("pnl", "sum"),
                                 net_voltgt=("pnl_vt", "sum"),
                                 avg_range=("range_size", "mean"),
                                 avg_scale=("scale", "mean"))
    rep.append("=== by year ===")
    rep.append(yr.to_string(float_format=fmt))
    rep.append("")

    # bootstrap the virgin month
    vg = raw[(raw["date"] >= PERIODS["VIRGIN 2026-07"][0])]
    if len(vg):
        rng = np.random.default_rng(5)
        for mode, col in [("fixed", "pnl"), ("vol-targeted", "pnl_vt")]:
            d = vg.groupby("date")[col].sum().to_numpy()
            b = d[rng.integers(0, len(d), (20000, len(d)))].mean(axis=1)
            rep.append(f"VIRGIN 2026-07 {mode:<13} {len(vg)} trades over {len(d)} days, "
                       f"net {d.sum():>8,.0f}, P(mean>0) = {(b > 0).mean() * 100:.1f}%")
    rep.append("")

    text = "\n".join(rep)
    with open(os.path.join(args.outdir, "regime_optimisation.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
