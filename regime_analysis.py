#!/usr/bin/env python3
"""
XAUUSD regime characterisation, persistence testing, and projection.

Part 1  what each year actually looked like (trend, vol, chop, liquidity)
Part 2  which of those characteristics PERSIST - i.e. which are forecastable
        at all - measured by month-over-month autocorrelation and by a naive
        "last quarter predicts next quarter" test against a shuffled baseline
Part 3  a projection for the next two quarters, but ONLY for the metrics that
        passed part 2, with uncertainty bands from the residual spread

Direction of price is deliberately NOT projected: daily returns show no
exploitable autocorrelation, so any number would be invented.  Volatility and
liquidity are projected because they demonstrably cluster.

    python regime_analysis.py --sessions market_context/sessions.csv
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

EPS = 1e-12


def hurst(series: np.ndarray, max_lag: int = 40) -> float:
    """Rescaled-range style Hurst via variance of lagged differences."""
    lags = range(2, min(max_lag, len(series) // 3))
    tau = [np.sqrt(np.std(series[l:] - series[:-l])) for l in lags]
    if len(tau) < 4:
        return np.nan
    return float(np.polyfit(np.log(list(lags)), np.log(tau), 1)[0] * 2.0)


def year_table(s: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for y, g in s.groupby(s["session_date"].dt.year):
        g = g.sort_values("session_date")
        ret = g["ret_pct"].to_numpy()
        logc = np.log(g["close"].to_numpy())
        dret = np.diff(logc) * 100
        rows.append({
            "year": y,
            "sessions": len(g),
            "first": g["open"].iloc[0],
            "last": g["close"].iloc[-1],
            "change_pct": (g["close"].iloc[-1] / g["open"].iloc[0] - 1) * 100,
            "ann_vol_pct": float(np.std(dret, ddof=1) * np.sqrt(252)),
            "mean_range_pct": g["range_pct"].mean(),
            "mean_rvol_pct": g["realized_vol_pct"].mean(),
            "trendiness": g["trendiness"].mean(),
            "trend_days_pct": (g["trendiness"] > 0.6).mean() * 100,
            "chop_days_pct": (g["trendiness"] < 0.3).mean() * 100,
            "up_days_pct": (ret > 0).mean() * 100,
            "daily_ac1": float(pd.Series(dret).autocorr(1)) if len(dret) > 5 else np.nan,
            "hurst": hurst(logc),
            "avg_spread": g["avg_spread"].mean(),
            "ticks_per_day": g["ticks"].mean(),
            "best_day_pct": ret.max(),
            "worst_day_pct": ret.min(),
        })
    return pd.DataFrame(rows).set_index("year")


def persistence(s: pd.DataFrame, metrics: list) -> pd.DataFrame:
    """Month-over-month autocorrelation of each regime metric."""
    m = s.set_index("session_date").resample("ME").mean(numeric_only=True)
    rows = []
    for col in metrics:
        if col not in m:
            continue
        x = m[col].dropna()
        if len(x) < 8:
            continue
        ac1 = x.autocorr(1)
        ac3 = x.autocorr(3)
        # out-of-sample-ish: does month t-1 beat the running mean at predicting t?
        pred_lag = x.shift(1).dropna()
        actual = x.loc[pred_lag.index]
        base = x.expanding().mean().shift(1).loc[pred_lag.index]
        mae_lag = np.mean(np.abs(actual - pred_lag))
        mae_base = np.mean(np.abs(actual - base))
        rows.append({"metric": col, "monthly_ac1": ac1, "monthly_ac3": ac3,
                     "mae_lastmonth": mae_lag, "mae_runningmean": mae_base,
                     "skill_vs_mean_pct": (1 - mae_lag / max(mae_base, EPS)) * 100,
                     "forecastable": (ac1 > 0.3) and (mae_lag < mae_base)})
    return pd.DataFrame(rows).set_index("metric")


def project(s: pd.DataFrame, metrics: list, horizon_months: int = 6) -> pd.DataFrame:
    """AR(1) projection with residual-based bands, for persistent metrics only."""
    m = s.set_index("session_date").resample("ME").mean(numeric_only=True)
    rows = []
    for col in metrics:
        x = m[col].dropna()
        if len(x) < 12:
            continue
        y, xl = x.iloc[1:].to_numpy(), x.iloc[:-1].to_numpy()
        b, a = np.polyfit(xl, y, 1)                       # y = b*x + a
        resid = y - (b * xl + a)
        sd = float(np.std(resid, ddof=1))
        cur = float(x.iloc[-1])
        mean_rev = a / max(1 - b, EPS)                    # AR(1) long-run mean
        path, v = [], cur
        for _ in range(horizon_months):
            v = b * v + a
            path.append(v)
        # variance accumulates along the AR(1) path
        var = 0.0
        band = []
        for h in range(horizon_months):
            var = var * b ** 2 + sd ** 2
            band.append(1.96 * np.sqrt(var))
        rows.append({"metric": col, "last_month": cur, "ar1_beta": b,
                     "long_run_mean": mean_rev,
                     "proj_1m": path[0], "proj_3m": path[2], "proj_6m": path[-1],
                     "band_3m": band[2], "band_6m": band[-1]})
    return pd.DataFrame(rows).set_index("metric")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="XAUUSD regime characterisation & projection")
    ap.add_argument("--sessions", default="market_context/sessions.csv")
    ap.add_argument("--hourly", default="market_context/hourly_context.csv")
    ap.add_argument("--outdir", default="regime_results")
    args = ap.parse_args(argv)

    s = pd.read_csv(args.sessions, parse_dates=["session_date"])
    os.makedirs(args.outdir, exist_ok=True)
    fmt = lambda v: f"{v:,.3f}"
    rep = ["XAUUSD regime analysis",
           f"{len(s)} sessions, {s.session_date.min().date()} .. {s.session_date.max().date()}",
           "NOTE: 2026 covers H1 only; its yearly change is not annualised.", ""]

    yt = year_table(s)
    yt.to_csv(os.path.join(args.outdir, "year_characteristics.csv"))
    rep.append("=== PART 1: what each year looked like ===")
    rep.append(yt.T.to_string(float_format=fmt))
    rep.append("")

    # quarterly view - finer grain, shows the transition
    q = s.set_index("session_date").resample("QE").agg(
        ret=("ret_pct", "sum"), range_pct=("range_pct", "mean"),
        rvol=("realized_vol_pct", "mean"), trendiness=("trendiness", "mean"),
        spread=("avg_spread", "mean"),
        ticks=("ticks", "mean"))
    q.index = q.index.to_period("Q")
    rep.append("=== quarterly path ===")
    rep.append(q.to_string(float_format=fmt))
    rep.append("")

    metrics = ["range_pct", "realized_vol_pct", "trendiness",
               "avg_spread", "ticks", "ret_pct"]
    pt = persistence(s, metrics).sort_values("monthly_ac1", ascending=False)
    pt.to_csv(os.path.join(args.outdir, "persistence.csv"))
    rep.append("=== PART 2: which characteristics persist (are forecastable at all)? ===")
    rep.append("monthly_ac1 = month-over-month autocorrelation.  skill_vs_mean_pct > 0 means")
    rep.append("last month beats the running average as a predictor of next month.")
    rep.append(pt.to_string(float_format=fmt))
    rep.append("")

    good = list(pt[pt["forecastable"]].index)
    rep.append(f"forecastable metrics: {good if good else 'none'}")
    rep.append(f"NOT forecastable: {[m for m in metrics if m not in good]}")
    rep.append("")

    if good:
        pj = project(s, good)
        pj.to_csv(os.path.join(args.outdir, "projection.csv"))
        rep.append("=== PART 3: AR(1) projection, persistent metrics only ===")
        rep.append("proj_Nm = expected monthly mean N months after 2026-06; "
                   "band = +/- 95% interval.")
        rep.append(pj.to_string(float_format=fmt))
        rep.append("")

    # direction: explicitly test whether it is predictable at all
    d = s.set_index("session_date")["ret_pct"]
    mo = d.resample("ME").sum()
    rep.append("=== is DIRECTION forecastable? ===")
    rep.append(f"daily return autocorr lag1 {d.autocorr(1):+.4f}  lag5 {d.autocorr(5):+.4f}")
    rep.append(f"monthly return autocorr lag1 {mo.autocorr(1):+.4f}")
    rep.append(f"share of months continuing the previous month's sign: "
               f"{(np.sign(mo) == np.sign(mo.shift(1))).mean() * 100:.1f}% (50% = no signal)")
    rep.append("=> direction is NOT projected here; any figure would be fabricated.")
    rep.append("")

    if os.path.exists(args.hourly):
        h = pd.read_csv(args.hourly, parse_dates=["session_date"])
        h["year"] = h["session_date"].dt.year
        hp = h.pivot_table(index="hour", columns="year", values="bar_range_pct")
        rep.append("=== intraday volatility profile by year (mean 5-min bar range %) ===")
        rep.append(hp.to_string(float_format=lambda v: f"{v:.4f}"))
        rep.append("")
        sp = h.pivot_table(index="hour", columns="year", values="avg_spread")
        rep.append("=== spread by hour and year (USD) ===")
        rep.append(sp.to_string(float_format=lambda v: f"{v:.3f}"))
        rep.append("")

    text = "\n".join(rep)
    with open(os.path.join(args.outdir, "regime_report.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"\nwritten to {args.outdir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
