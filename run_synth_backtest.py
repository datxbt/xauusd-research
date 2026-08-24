#!/usr/bin/env python3
"""
Run the 8-window ORB portfolio across synthetic tape replicates.

Each replicate is an alternative history with the same microstructure, so the
spread of results is the honest confidence interval on the edge - one the
session-level bootstrap in the original study could not produce, because it
resamples sessions rather than the tape itself.

Every run gets its own sub-bar cache. `load_subbars` keys the cache on the
file stem alone, and a synthetic 2025-04 has the same stem as the real one,
so a shared cache would silently re-test the real data.

    python run_synth_backtest.py --runs rep00 rep01 rep02 rep03 --real
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd

import backtest_orb as ob
import optimize_time_windows as tw

WINDOWS = ["h00_r30_t1", "h01_r60_t3", "h02_r15_t3", "h04_r30_t3",
           "h05_r60_t2", "h06_r60_t3", "h13_r30_t3", "h14_r15_t2"]
SYNTH_ROOT = os.path.join("tick_synth", "output")


def portfolio_stats(raw: pd.DataFrame, equity: float, lots: float,
                    vol_target: bool, sessions: pd.DataFrame | None) -> dict:
    if raw.empty:
        return {}
    raw = raw.copy()
    raw["date"] = pd.to_datetime(raw["date"])

    if vol_target and sessions is not None:
        tv = (sessions.sort_values("session_date").set_index("session_date")
              ["realized_vol_pct"].rolling(20, min_periods=5).mean().shift(1))
        target = float(sessions["realized_vol_pct"].iloc[:300].mean())
        scale = (target / raw["date"].map(tv)).clip(1 / 3, 3).fillna(1.0)
        raw["pnl"] = (raw["price_pnl"] * ob.CONTRACT_SIZE * lots - 7.0 * lots) * scale

    d = raw.groupby("date")["pnl"].sum()
    eq = equity + d.cumsum()
    dd = eq - eq.cummax()
    r = d / equity
    w, l = raw[raw.pnl > 0], raw[raw.pnl < 0]
    return {
        "trades": len(raw),
        "days": len(d),
        "win_rate": (raw.pnl > 0).mean() * 100,
        "pf": w.pnl.sum() / abs(l.pnl.sum()) if len(l) else np.inf,
        "net": d.sum(),
        "per_trade": raw.pnl.mean(),
        "avg_win": w.pnl.mean() if len(w) else 0.0,
        "avg_loss": l.pnl.mean() if len(l) else 0.0,
        "rr": (w.pnl.mean() / abs(l.pnl.mean())) if len(l) and len(w) else np.nan,
        "maxDD": dd.min(),
        "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan,
        "pos_days": (d > 0).mean() * 100,
        "tp_rate": (raw.exit_reason == "tp").mean() * 100,
        "sl_rate": (raw.exit_reason == "sl").mean() * 100,
    }


def run_one(data_dir: str, label: str, args) -> tuple:
    cache = os.path.join(args.cache_root, label) if args.cache_root else None
    a = SimpleNamespace(subbar_seconds=5, bar_minutes=5, chunksize=args.chunksize,
                        min_session_bars=100, cache_dir=cache)
    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0]) if p.name in WINDOWS]
    files = ob.discover_files(data_dir, args.from_month, args.to_month)
    if not files:
        print(f"  {label}: no csv under {data_dir}", file=sys.stderr)
        return None, None
    t0 = time.time()
    raw = tw.pnl_frame(ob.collect(files, grid, a, label), args.lots, 7.0)
    print(f"  {label}: {len(files)} months, {len(raw):,} trades, {time.time()-t0:.0f}s",
          file=sys.stderr)
    return raw, files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ORB portfolio over synthetic tapes")
    ap.add_argument("--runs", nargs="*", default=None,
                    help="subdirectories of tick_synth/output (default: all rep*)")
    ap.add_argument("--real", action="store_true", help="also run the real tape as baseline")
    ap.add_argument("--real-dir", default="Monthly_Tick_Data")
    ap.add_argument("--from-month", default=None)
    ap.add_argument("--to-month", default=None)
    ap.add_argument("--lots", type=float, default=0.02)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-root", default=None,
                    help="parent dir for per-run sub-bar caches (omit = no caching)")
    ap.add_argument("--outdir", default="orb_results")
    args = ap.parse_args(argv)

    runs = args.runs
    if not runs:
        runs = sorted(d for d in os.listdir(SYNTH_ROOT)
                      if os.path.isdir(os.path.join(SYNTH_ROOT, d)))
    os.makedirs(args.outdir, exist_ok=True)

    rows, trades_by_run = [], {}

    if args.real:
        raw, _ = run_one(args.real_dir, "REAL", args)
        if raw is not None:
            trades_by_run["REAL"] = raw
            rows.append({"run": "REAL", "kind": "real",
                         **portfolio_stats(raw, args.equity, args.lots, False, None)})

    for name in runs:
        d = os.path.join(SYNTH_ROOT, name)
        raw, _ = run_one(d, name, args)
        if raw is None:
            continue
        trades_by_run[name] = raw
        rows.append({"run": name, "kind": "synthetic",
                     **portfolio_stats(raw, args.equity, args.lots, False, None)})

    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(args.outdir, "synth_replicates.csv"), index=False)

    fmt = lambda v: f"{v:,.2f}"
    cols = ["run", "trades", "days", "win_rate", "pf", "net", "per_trade",
            "rr", "maxDD", "sharpe", "pos_days", "tp_rate"]
    print("\n=== 8-window ORB portfolio, %.2f lots/window, $%s account ==="
          % (args.lots, f"{args.equity:,.0f}"))
    print(res[cols].to_string(index=False, float_format=fmt))

    syn = res[res.kind == "synthetic"]
    if len(syn) >= 2:
        print("\n=== dispersion across synthetic replicates ===")
        for m in ["trades", "win_rate", "pf", "net", "per_trade", "sharpe", "maxDD"]:
            v = syn[m].to_numpy(dtype=float)
            print(f"  {m:<11} mean {v.mean():>10,.2f}   sd {v.std(ddof=1):>9,.2f}   "
                  f"min {v.min():>10,.2f}   max {v.max():>10,.2f}")
        if (res.kind == "real").any():
            for m in ["pf", "net", "per_trade", "sharpe"]:
                real = float(res.loc[res.kind == "real", m].iloc[0])
                v = syn[m].to_numpy(dtype=float)
                pct = (v < real).mean() * 100
                print(f"  real {m} = {real:,.3f} sits at the {pct:.0f}th percentile "
                      f"of the synthetic spread")

    for name, raw in trades_by_run.items():
        raw.to_csv(os.path.join(args.outdir, f"synth_trades_{name}.csv"), index=False)
    print(f"\nwritten to {args.outdir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
