#!/usr/bin/env python3
"""
Re-tune the session breakout portfolio for the 2026 XAUUSD regime.

The original eight windows were selected on 2024-2025: annualised vol ~15-19%,
daily range 1.4-1.7%, spreads $0.037-0.056. 2026 is a different market -
vol ~33%, range 2.8%, spreads $0.08-0.12, and gold fell 7% after two bull
years. This asks whether the portfolio should change, and answers it without
fooling itself.

THE HAZARD
----------
2026 has ~150 real sessions. Picking the best of 216 window configs on 150
sessions will always produce a flattering number and usually produces
nothing else. Real 2026 data alone cannot validate a 2026-tuned portfolio.

THE PROTOCOL  (fixed before any result was inspected)
-----------------------------------------------------
  SELECT on real 2026 only.
  VALIDATE on synthetic 2026-regime replicates - block-bootstrap tapes drawn
  from the 2026 pool, so they carry 2026's volatility, spreads and tick
  behaviour but a different ordering of days. Replicates are never used to
  choose anything.

  Four candidate portfolios, defined in advance:
    A_incumbent  the existing eight windows, unchanged (the thing to beat)
    B_top8       best config per hour, top 8 hours by real-2026 net P&L
    C_stable     hours positive in BOTH halves of 2026 (sign stability)
    D_pruned     incumbent minus windows that lost money in 2026

  A 2026-tuned portfolio is adopted ONLY if it beats A_incumbent on the
  MEDIAN synthetic replicate. Beating it on real 2026 alone is what
  overfitting looks like, not evidence.

Nothing in the parent directory is modified; the original strategy stands.

    python strategy_2026/optimize_2026.py --scan
    python strategy_2026/optimize_2026.py --validate rep2026_0 rep2026_1 ...
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

import backtest_orb as ob                      # noqa: E402
import optimize_time_windows as tw             # noqa: E402
from tickdata import CONTRACT_SIZE             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

REGIME_FROM, REGIME_TO = "2026-01", "2026-07"
SPLIT = "2026-05-01"                            # H1 / H2 boundary inside 2026
MIN_TRADES_HOUR = 100                           # per-hour minimum on real 2026

INCUMBENT = ["h00_r30_t1", "h01_r60_t3", "h02_r15_t3", "h04_r30_t3",
             "h05_r60_t2", "h06_r60_t3", "h13_r30_t3", "h14_r15_t2"]


# --------------------------------------------------------------------------
def run_grid(data_dir: str, label: str, args, grid=None) -> pd.DataFrame:
    """Back-test a window grid over one tape."""
    a = SimpleNamespace(subbar_seconds=5, bar_minutes=5, chunksize=args.chunksize,
                        min_session_bars=100, cache_dir=args.cache_dir)
    grid = grid if grid is not None else tw.window_grid([1.0, 2.0, 3.0])
    files = ob.discover_files(data_dir, args.from_month, args.to_month)
    if not files:
        raise SystemExit(f"no csv under {data_dir} for {args.from_month}..{args.to_month}")
    raw = tw.pnl_frame(ob.collect(files, grid, a, label), args.lots, args.commission)
    raw["date"] = pd.to_datetime(raw["date"])
    return raw


def portfolio_metrics(raw: pd.DataFrame, names: list, equity: float) -> dict:
    f = raw[raw["param"].isin(names)]
    if f.empty:
        return {"trades": 0}
    d = f.groupby("date")["pnl"].sum()
    eq = equity + d.cumsum()
    dd = eq - eq.cummax()
    r = d / equity
    w, l = f[f.pnl > 0], f[f.pnl < 0]
    return {
        "windows": len(names), "trades": len(f), "days": len(d),
        "win_rate": (f.pnl > 0).mean() * 100,
        "pf": w.pnl.sum() / abs(l.pnl.sum()) if len(l) else np.inf,
        "net": d.sum(), "per_trade": f.pnl.mean(),
        "rr": (w.pnl.mean() / abs(l.pnl.mean())) if len(l) and len(w) else np.nan,
        "maxDD": dd.min(),
        "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan,
    }


def best_per_hour(raw: pd.DataFrame, subset=None) -> pd.DataFrame:
    f = raw if subset is None else raw[subset]
    g = f.groupby(["hour", "param"])["pnl"].agg(["sum", "size"]).reset_index()
    g = g[g["size"] >= MIN_TRADES_HOUR // 4]
    if g.empty:
        return g
    return g.loc[g.groupby("hour")["sum"].idxmax()].sort_values("sum", ascending=False)


# --------------------------------------------------------------------------
def do_scan(args) -> None:
    raw = run_grid(args.real_dir, "real2026", args)
    raw.to_csv(os.path.join(RESULTS, "scan_2026_trades.csv"), index=False)

    hour_tot = raw.groupby("hour")["pnl"].agg(net="sum", trades="size",
                                              per_trade="mean").reset_index()
    h1 = raw["date"] < SPLIT
    a = raw[h1].groupby("hour")["pnl"].sum().rename("net_H1")
    b = raw[~h1].groupby("hour")["pnl"].sum().rename("net_H2")
    hour_tot = hour_tot.merge(a, on="hour", how="left").merge(b, on="hour", how="left")
    hour_tot["both_halves"] = (hour_tot.net_H1 > 0) & (hour_tot.net_H2 > 0)
    hour_tot.to_csv(os.path.join(RESULTS, "hour_profile_2026.csv"), index=False)

    # ---- candidate portfolios, all defined from REAL 2026 only ----
    bp = best_per_hour(raw)
    top8 = list(bp.head(8)["param"])

    stable_hours = list(hour_tot[hour_tot.both_halves]["hour"])
    bp_stable = bp[bp.hour.isin(stable_hours)]
    stable = list(bp_stable.head(8)["param"])

    inc_pnl = raw[raw.param.isin(INCUMBENT)].groupby("param")["pnl"].sum()
    pruned = [w for w in INCUMBENT if inc_pnl.get(w, 0) > 0]

    cands = {"A_incumbent": INCUMBENT, "B_top8": top8,
             "C_stable": stable, "D_pruned": pruned}
    with open(os.path.join(RESULTS, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump(cands, f, indent=2)

    fmt = lambda v: f"{v:,.2f}"
    print("\n=== hour profile on REAL 2026 (%d sessions) ==="
          % raw["date"].nunique())
    print(hour_tot.sort_values("net", ascending=False)
          .to_string(index=False, float_format=fmt))

    print("\n=== incumbent windows on REAL 2026 ===")
    print(raw[raw.param.isin(INCUMBENT)].groupby("param")
          .agg(trades=("pnl", "size"), net=("pnl", "sum"), per_trade=("pnl", "mean"),
               win=("pnl", lambda x: (x > 0).mean() * 100))
          .to_string(float_format=fmt))

    print("\n=== candidate portfolios ===")
    for k, v in cands.items():
        print(f"  {k:<13} {len(v)} windows: {', '.join(v) if v else '(empty)'}")

    rows = [{"candidate": k, **portfolio_metrics(raw, v, args.equity)}
            for k, v in cands.items() if v]
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RESULTS, "candidates_real2026.csv"), index=False)
    print("\n=== candidates on REAL 2026 (in-sample for B/C/D - not evidence) ===")
    print(res.to_string(index=False, float_format=fmt))
    print("\nNext: validate on synthetic 2026 replicates, which none of these saw.")


def do_validate(args) -> None:
    with open(os.path.join(RESULTS, "candidates.json"), encoding="utf-8") as f:
        cands = json.load(f)
    keep = sorted({w for v in cands.values() for w in v})
    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0]) if p.name in keep]

    rows = []
    for name in args.validate:
        d = os.path.join(ROOT, "tick_synth", "output", name)
        if not os.path.isdir(d):
            print(f"  skipping {name}: not found", file=sys.stderr)
            continue
        sub = SimpleNamespace(**{**vars(args), "cache_dir": None})
        raw = run_grid(d, name, sub, grid=grid)
        for k, v in cands.items():
            if v:
                rows.append({"replicate": name, "candidate": k,
                             **portfolio_metrics(raw, v, args.equity)})

    if not rows:
        raise SystemExit("no replicates scored")
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RESULTS, "candidates_replicates.csv"), index=False)

    fmt = lambda v: f"{v:,.2f}"
    print("\n=== per replicate ===")
    print(res.pivot(index="replicate", columns="candidate", values="net")
          .to_string(float_format=fmt))

    agg = res.groupby("candidate").agg(
        reps=("net", "size"), median_net=("net", "median"), mean_net=("net", "mean"),
        sd_net=("net", "std"), worst=("net", "min"), best=("net", "max"),
        median_pf=("pf", "median"), median_sharpe=("sharpe", "median"),
        median_dd=("maxDD", "median")).sort_values("median_net", ascending=False)
    print("\n=== across replicates (the deciding table) ===")
    print(agg.to_string(float_format=fmt))

    inc = agg.loc["A_incumbent", "median_net"] if "A_incumbent" in agg.index else np.nan
    print("\n=== verdict, against the pre-declared rule ===")
    for k in agg.index:
        if k == "A_incumbent":
            continue
        beat = res[res.candidate == k].set_index("replicate")["net"] > \
               res[res.candidate == "A_incumbent"].set_index("replicate")["net"]
        print(f"  {k:<13} median {agg.loc[k,'median_net']:>9,.0f} vs incumbent "
              f"{inc:>9,.0f}  ->  {'ADOPT' if agg.loc[k,'median_net'] > inc else 'reject'}"
              f"   (wins on {int(beat.sum())}/{len(beat)} replicates)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="2026-regime re-tune of the breakout portfolio")
    ap.add_argument("--scan", action="store_true", help="select candidates on real 2026")
    ap.add_argument("--validate", nargs="*", default=None,
                    help="synthetic 2026 replicate directory names under tick_synth/output")
    ap.add_argument("--real-dir", default=os.path.join(ROOT, "Monthly_Tick_Data"))
    ap.add_argument("--from-month", default=REGIME_FROM)
    ap.add_argument("--to-month", default=REGIME_TO)
    ap.add_argument("--lots", type=float, default=0.02)
    ap.add_argument("--commission", type=float, default=7.0)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)

    os.makedirs(RESULTS, exist_ok=True)
    if args.scan:
        do_scan(args)
    if args.validate:
        do_validate(args)
    if not args.scan and not args.validate:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
