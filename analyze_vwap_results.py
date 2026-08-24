#!/usr/bin/env python3
"""
Slice the VWAP pullback trade logs to find which circumstances actually pay.

Reads every trades*.csv produced by backtest_vwap_pullback.py, optionally joins
the per-session context from session_context.py, and reports expectancy by
hour, weekday, session block, stretch size, trend/range regime, volatility and
spread.

Because the parameter sets trade the same underlying events, pooled statistics
are descriptive only.  The `consistent` column is the honest signal: the share
of parameter sets in which that bucket beats zero on its own.

    python analyze_vwap_results.py --results-dir vwap_pullback_results
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

MIN_TRADES = 30          # buckets thinner than this are reported but flagged
SESSION_BLOCKS = [(0, 7, "00-06 Asia"), (7, 13, "07-12 London"),
                  (13, 21, "13-20 New York"), (21, 24, "21-23 Late")]


def load_trades(results_dir: str) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(results_dir, "trades*.csv")))
    if not files:
        raise SystemExit(f"no trades*.csv in {results_dir}")
    frames = []
    for f in files:
        m = re.match(r"trades_?(.*)\.csv", os.path.basename(f))
        df = pd.read_csv(f, parse_dates=["entry_dt", "exit_dt"])
        df["param"] = m.group(1) or "base"
        frames.append(df)
    t = pd.concat(frames, ignore_index=True)
    t["session_date"] = pd.to_datetime(t["session_date"]).dt.date
    t["dir"] = np.where(t["side"] > 0, "long", "short")
    t["hour"] = t["entry_dt"].dt.hour
    t["weekday"] = t["entry_dt"].dt.day_name()
    t["month"] = t["entry_dt"].dt.month
    t["year"] = t["entry_dt"].dt.year
    t["abs_dev"] = t["dev_pct"].abs()
    t["hold_min"] = (t["exit_dt"] - t["entry_dt"]).dt.total_seconds() / 60.0
    t["block"] = pd.cut(t["hour"], bins=[b[0] for b in SESSION_BLOCKS] + [24],
                        right=False, labels=[b[2] for b in SESSION_BLOCKS])
    return t


def qbucket(s: pd.Series, q: int, fmt: str = "{:.2f}") -> pd.Series:
    """Quantile buckets labelled with their own edges (robust to ties)."""
    try:
        cut = pd.qcut(s, q, duplicates="drop")
    except ValueError:
        return pd.Series(["all"] * len(s), index=s.index)
    return cut.apply(lambda iv: f"{fmt.format(iv.left)} - {fmt.format(iv.right)}"
                     if pd.notna(iv) else "na")


def slice_stats(t: pd.DataFrame, key: str, sort_by_index: bool = True) -> pd.DataFrame:
    """Pooled expectancy per bucket plus cross-parameter consistency."""
    g = t.groupby(key, observed=True)
    out = pd.DataFrame({
        "trades": g.size(),
        "win_rate": g["pnl"].apply(lambda x: (x > 0).mean() * 100),
        "exp_usd": g["pnl"].mean(),
        "exp_R": g["r_multiple"].mean(),
        "total_usd": g["pnl"].sum(),
        "pf": g["pnl"].apply(lambda x: x[x > 0].sum() / abs(x[x < 0].sum())
                             if (x < 0).any() else np.inf),
        "tp_rate": g["exit_reason"].apply(lambda x: (x == "tp").mean() * 100),
        "sl_rate": g["exit_reason"].apply(lambda x: (x == "sl").mean() * 100),
    })
    # t-stat of mean pnl within each bucket (pooled; overlapping samples)
    out["t_stat"] = g["pnl"].apply(
        lambda x: x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) else np.nan)
    # share of parameter sets in which this bucket is profitable on its own
    per_param = t.groupby([key, "param"], observed=True)["pnl"].agg(["mean", "size"])
    per_param = per_param[per_param["size"] >= 5]
    out["consistent"] = per_param.groupby(level=0, observed=True)["mean"].apply(
        lambda x: (x > 0).mean() * 100)
    out["thin"] = out["trades"] < MIN_TRADES
    return out.sort_index() if sort_by_index else out.sort_values("exp_usd", ascending=False)


def fmt_table(df: pd.DataFrame) -> str:
    show = df.copy()
    show["consistent"] = show["consistent"].round(0)
    cols = ["trades", "win_rate", "exp_usd", "exp_R", "total_usd", "pf",
            "tp_rate", "sl_rate", "t_stat", "consistent"]
    return show[cols].to_string(float_format=lambda v: f"{v:,.2f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="condition analysis for the VWAP pullback backtest")
    ap.add_argument("--results-dir", default="vwap_pullback_results")
    ap.add_argument("--sessions", default="vwap_pullback_results/sessions.csv")
    ap.add_argument("--params", default=None,
                    help="comma separated parameter names to keep (default: all)")
    ap.add_argument("--out", default="vwap_pullback_results/condition_analysis.txt")
    ap.add_argument("--quantiles", type=int, default=5)
    args = ap.parse_args(argv)

    t = load_trades(args.results_dir)
    if args.params:
        keep = set(args.params.split(","))
        t = t[t["param"].isin(keep)]
    n_params = t["param"].nunique()

    rep = [
        "VWAP pullback - which conditions pay?",
        f"trade logs : {n_params} parameter set(s), {len(t):,} trades, "
        f"{t['session_date'].nunique()} sessions",
        f"baseline   : pooled expectancy {t['pnl'].mean():,.2f} USD/trade "
        f"({t['r_multiple'].mean():.3f} R), win rate {(t['pnl'] > 0).mean() * 100:.2f}%",
        "",
        "`consistent` = % of parameter sets where the bucket is profitable on its own.",
        "Parameter sets overlap heavily, so t_stat is descriptive, not a clean test.",
        f"Buckets under {MIN_TRADES} trades are flagged `thin`.",
        "",
    ]

    def section(title: str, table: pd.DataFrame):
        rep.append(f"=== {title} ===")
        rep.append(fmt_table(table))
        rep.append("")

    section("entry hour (UTC)", slice_stats(t, "hour"))
    section("session block", slice_stats(t, "block"))
    section("weekday", slice_stats(t, "weekday").reindex(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Sunday"]).dropna(how="all"))
    section("direction", slice_stats(t, "dir"))
    section("year", slice_stats(t, "year"))

    t["dev_bucket"] = qbucket(t["abs_dev"], args.quantiles)
    section("stretch from VWAP at signal (|dev| %)", slice_stats(t, "dev_bucket"))

    t["hour_dir"] = t["block"].astype(str) + " / " + t["dir"]
    section("session block x direction", slice_stats(t, "hour_dir"))

    # ---- session character -------------------------------------------------
    if os.path.exists(args.sessions):
        s = pd.read_csv(args.sessions)
        s["session_date"] = pd.to_datetime(s["session_date"]).dt.date
        t = t.merge(s, on="session_date", how="left", suffixes=("", "_sess"))
        have = t["trendiness"].notna()
        rep.append(f"session context joined for {have.mean() * 100:.1f}% of trades\n")
        t = t[have]

        for col, label, fmt in [
            ("trendiness", "trend vs range (|close-open| / range: 0 = chop, 1 = trend)", "{:.2f}"),
            ("range_pct", "session range (% of open)", "{:.2f}"),
            ("realized_vol_pct", "realized volatility (%)", "{:.2f}"),
            ("vwap_crosses", "number of VWAP crosses (range days cross often)", "{:.0f}"),
            ("avg_spread", "average spread (USD)", "{:.2f}"),
            ("max_dev_pct", "furthest the day stretched from VWAP (%)", "{:.2f}"),
            ("ret_pct", "session return (%)", "{:+.2f}"),
        ]:
            t[f"b_{col}"] = qbucket(t[col], args.quantiles, fmt)
            section(label, slice_stats(t, f"b_{col}"))

        # trading with vs against the day's direction
        t["with_trend"] = np.where(np.sign(t["ret_pct"]) == np.sign(t["side"]),
                                   "with day's move", "against day's move")
        section("trade direction vs the day's net move", slice_stats(t, "with_trend"))

        # the single most useful cut: chop days only, split by direction
        calm = t[t["trendiness"] <= t["trendiness"].quantile(0.4)]
        if len(calm):
            section("range days only (bottom 40% trendiness), by session block",
                    slice_stats(calm, "block"))
    else:
        rep.append(f"(no {args.sessions} - run session_context.py for regime analysis)\n")

    # ---- best / worst ------------------------------------------------------
    combos = []
    for key in ["hour", "weekday", "dir", "dev_bucket"] + \
               ([c for c in t.columns if c.startswith("b_")] if "b_trendiness" in t else []):
        st = slice_stats(t, key)
        for idx, row in st.iterrows():
            combos.append({"dimension": key, "bucket": str(idx), **row.to_dict()})
    ranked = pd.DataFrame(combos)
    ranked = ranked[~ranked["thin"]].sort_values("exp_usd", ascending=False)
    cols = ["dimension", "bucket", "trades", "win_rate", "exp_usd", "exp_R", "pf", "consistent"]
    rep.append("=== most profitable buckets (>= %d trades) ===" % MIN_TRADES)
    rep.append(ranked.head(15)[cols].to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    rep.append("")
    rep.append("=== least profitable buckets ===")
    rep.append(ranked.tail(10)[cols].to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    rep.append("")

    ranked.to_csv(os.path.join(args.results_dir, "condition_ranking.csv"), index=False)
    text = "\n".join(rep)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"\nwritten to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
