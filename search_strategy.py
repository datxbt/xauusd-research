#!/usr/bin/env python3
"""
Hypothesis-driven search for a deployable VWAP-reversion variant.

Protocol (fixed before looking at any result)
---------------------------------------------
  TRAIN   2024-01 .. 2025-06   (18 months)  - search here
  HOLDOUT 2025-07 .. 2026-06   (12 months)  - evaluated ONCE, for the single
                                              config selected on TRAIN

Selection rule, declared up front:
  1. at least MIN_TRADES trades on TRAIN
  2. the 95% bootstrap CI (resampling whole sessions) of mean session P&L must
     lie entirely above zero
  3. among survivors, pick the highest CI LOWER bound - the most conservative
     estimate, not the biggest backtest number

If nothing satisfies (1) and (2), the correct answer is "no deployable variant
found", and the holdout is not touched at all.

The grids come from the failure diagnosis, not from slice mining:
  * small stretches only - reversion probability is highest at 0.2-0.4%
  * stop_mult < 1 - the reward/risk region the original sweep never tested
  * alternative targets - the moving VWAP target is what shrinks the wins
  * a hard time stop - unresolved trades turn into directional carry
  * a causal chop filter - trailing efficiency ratio, computed live

    python search_strategy.py --stage a --cache-dir <dir>
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import time
from dataclasses import replace

import numpy as np
import pandas as pd

from backtest_vwap_pullback import (Params, TRADE_COLUMNS, apply_money_management,
                                    compute_metrics, discover_files, load_subbars,
                                    run_session, split_sessions)

TRAIN = ("2024-01", "2025-06")
HOLDOUT = ("2025-07", "2026-06")
MIN_TRADES = 200
BOOT = 20000
SEED = 20240101


def stage_a_grid() -> list:
    """Exit geometry only - no filters.  Is there any payoff structure that works?"""
    grid = []
    for thr, sm, tp in itertools.product(
            [0.2, 0.3, 0.4],
            [0.6, 0.8, 1.0, 1.25],
            [("vwap_dynamic", 1.0), ("vwap_static", 1.0),
             ("fixed_r", 1.0), ("fixed_r", 1.5)]):
        mode, r = tp
        grid.append(Params(name=f"A_t{thr:g}_s{sm:g}_{mode}{r if mode == 'fixed_r' else ''}",
                           threshold_pct=thr, stop_mult=sm, tp_mode=mode, tp_r=r,
                           max_hold_bars=0))
    return grid


def stage_b_grid() -> list:
    """Best geometry family plus the causal filters."""
    grid = []
    for thr, sm, tp, eff, hold in itertools.product(
            [0.25, 0.35],
            [0.6, 0.8, 1.0],
            [("fixed_r", 1.0), ("fixed_r", 1.5), ("vwap_static", 1.0)],
            [0.0, 0.30, 0.45],
            [12, 24, 0]):
        mode, r = tp
        grid.append(Params(
            name=f"B_t{thr:g}_s{sm:g}_{mode}{r if mode == 'fixed_r' else ''}"
                 f"_e{eff:g}_h{hold}",
            threshold_pct=thr, stop_mult=sm, tp_mode=mode, tp_r=r,
            max_efficiency=eff, max_hold_bars=hold))
    return grid


def stage_c_grid() -> list:
    """Continuation instead of reversion: trade the break AWAY from VWAP."""
    grid = []
    for thr, sm, r, meff, hold in itertools.product(
            [0.2, 0.35, 0.5],
            [0.4, 0.6, 0.8],
            [1.5, 2.0, 3.0],
            [0.0, 0.35],
            [12, 24, 48]):
        grid.append(Params(
            name=f"C_t{thr:g}_s{sm:g}_r{r:g}_me{meff:g}_h{hold}",
            mode="follow", threshold_pct=thr, stop_mult=sm,
            tp_mode="fixed_r", tp_r=r, min_efficiency=meff, max_hold_bars=hold))
    return grid


def collect(files: list, grid: list, args, label: str) -> pd.DataFrame:
    """Run every config over every session in `files`."""
    trades: list = []
    carry = None
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
                trades.extend(run_session(s, p))
    print(f"  {label}: {len(trades):,} trades in {time.time() - t0:.0f}s", file=sys.stderr)
    return pd.DataFrame(trades, columns=TRADE_COLUMNS)


def evaluate(raw: pd.DataFrame, grid: list, equity: float, rng) -> pd.DataFrame:
    """Per-config metrics plus a session-level bootstrap CI on mean session P&L."""
    rows = []
    for p in grid:
        one = raw[raw["param"] == p.name]
        if one.empty:
            continue
        one = apply_money_management(one.copy(), p, equity)
        met = compute_metrics(one, equity)
        sess = one.groupby("session_date")["pnl"].sum().to_numpy()
        if len(sess) >= 20:
            idx = rng.integers(0, len(sess), (BOOT, len(sess)))
            means = sess[idx].mean(axis=1)
            lo, hi = np.percentile(means, [2.5, 97.5])
        else:
            lo = hi = np.nan
        rows.append({"param": p.name, "trades": met["trades"], "sessions": len(sess),
                     "win_rate": met["win_rate_pct"], "pf": met["profit_factor"],
                     "net_pnl": met["net_pnl"], "exp_usd": met["expectancy"],
                     "exp_R": met["expectancy_R"], "mean_session": sess.mean(),
                     "ci_lo": lo, "ci_hi": hi, "max_dd_pct": met["max_drawdown_pct"],
                     "sharpe": met["sharpe_daily"]})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="protocol-driven strategy search")
    ap.add_argument("--stage", choices=["a", "b", "c"], default="a")
    ap.add_argument("--data-dir", default="Monthly_Tick_Data")
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--subbar-seconds", type=int, default=5)
    ap.add_argument("--bar-minutes", type=int, default=5)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--min-session-bars", type=int, default=100)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--outdir", default="vwap_pullback_results")
    ap.add_argument("--holdout", action="store_true",
                    help="also evaluate the selected config on the holdout period")
    ap.add_argument("--select", default=None,
                    help="skip the search and evaluate this config name directly")
    args = ap.parse_args(argv)

    rng = np.random.default_rng(SEED)
    grid = {'a': stage_a_grid, 'b': stage_b_grid, 'c': stage_c_grid}[args.stage]()
    if args.select:
        grid = [p for p in grid if p.name == args.select] or grid
    os.makedirs(args.outdir, exist_ok=True)

    train_files = discover_files(args.data_dir, *TRAIN)
    print(f"stage {args.stage}: {len(grid)} configs, TRAIN {TRAIN[0]}..{TRAIN[1]} "
          f"({len(train_files)} files)", file=sys.stderr)

    tr = evaluate(collect(train_files, grid, args, "train"), grid, args.equity, rng)
    tr = tr.sort_values("ci_lo", ascending=False)
    tr.to_csv(os.path.join(args.outdir, f"search_stage{args.stage}_train.csv"), index=False)

    fmt = lambda v: f"{v:,.2f}"
    print(f"\n=== TRAIN results, stage {args.stage} (sorted by CI lower bound) ===")
    cols = ["param", "trades", "sessions", "win_rate", "pf", "net_pnl", "exp_usd",
            "mean_session", "ci_lo", "ci_hi"]
    print(tr[cols].head(20).to_string(index=False, float_format=fmt))

    ok = tr[(tr["trades"] >= MIN_TRADES) & (tr["ci_lo"] > 0)]
    print(f"\nconfigs meeting the pre-declared rule "
          f"(>= {MIN_TRADES} trades AND 95% CI entirely above zero): {len(ok)} / {len(tr)}")
    if ok.empty:
        print("\nVERDICT: no variant qualifies on TRAIN. Holdout not touched.")
        return 0

    pick = ok.iloc[0]["param"]
    print(f"\nselected on TRAIN: {pick}")
    if not args.holdout:
        print("(re-run with --holdout to score it out of sample)")
        return 0

    sel = [p for p in grid if p.name == pick]
    ho_files = discover_files(args.data_dir, *HOLDOUT)
    ho = evaluate(collect(ho_files, sel, args, "holdout"), sel, args.equity, rng)
    ho.to_csv(os.path.join(args.outdir, f"search_stage{args.stage}_holdout.csv"), index=False)
    print(f"\n=== HOLDOUT {HOLDOUT[0]}..{HOLDOUT[1]} for {pick} ===")
    print(ho[cols].to_string(index=False, float_format=fmt))
    r = ho.iloc[0]
    verdict = ("survives: holdout CI is entirely above zero"
               if r["ci_lo"] > 0 else
               "FAILS out of sample: holdout CI includes zero" if r["ci_hi"] > 0 else
               "FAILS out of sample: holdout is significantly negative")
    print(f"\nVERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
