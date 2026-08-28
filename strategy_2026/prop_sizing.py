#!/usr/bin/env python3
"""
Position sizing against E8 prop-firm rule sets.

Generates the daily P&L series under the halt-5m flatten (the shipped rule),
then simulates each E8 account type at a range of lot sizes.

A useful simplification: daily P&L is linear in lots, so the *percentage*
return series depends only on lots-per-$10,000, not on account size. Account
size therefore only enters through the drawdown percentage (Signature scales
it) and the fee. One simulation per (size_per_10k, ruleset) covers every
account size.

Evaluation is simulated from every possible start day in the sample and run
until pass, breach, or the end of the data (censored, counted as not passed).

    python strategy_2026/prop_sizing.py --cache-dir <dir>
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import flatten_anchor as fa                      # noqa: E402
import optimize_time_windows as tw               # noqa: E402
from tickdata import discover_files              # noqa: E402

RESULTS = os.path.join(HERE, "results")


# --------------------------------------------------------------------------
# rule sets
# --------------------------------------------------------------------------
# dd_kind: "trail" = trails closed high-water, locks at initial balance
#          "static" = fixed floor at initial - dd
#          "eod"   = same as trail but only updated at the daily close
#
# Because the engine is daily, "trail" and "eod" behave identically here; the
# difference is intraday, which a daily series cannot see. See the caveat in
# the report output.
ACCOUNTS = {
    "One 4%DD":   dict(target=0.06, dd=0.04, daily=0.030, dd_kind="trail",  cap=None),
    "One 6%DD":   dict(target=0.09, dd=0.06, daily=0.045, dd_kind="trail",  cap=None),
    "One 8%DD":   dict(target=0.12, dd=0.08, daily=0.060, dd_kind="trail",  cap=None),
    "One 10%DD":  dict(target=0.15, dd=0.10, daily=0.075, dd_kind="trail",  cap=None),
    "One 14%DD":  dict(target=0.21, dd=0.14, daily=0.092, dd_kind="trail",  cap=None),
    "Pro":        dict(target=0.08, dd=0.08, daily=0.025, dd_kind="static", cap=0.02),
    "Sig 25-50k": dict(target=0.06, dd=0.04, daily=None,  dd_kind="eod",    cap=None),
    "Sig 100k+":  dict(target=0.06, dd=0.03, daily=None,  dd_kind="eod",    cap=None),
}


def daily_per_lot(from_month: str, to_month: str, cache_dir, chunksize: int):
    """
    Per-lot daily P&L under the halt-5m flatten, as two series per portfolio:

      close  - realised P&L at the end of the day
      trough - the WORST running realised P&L reached during the day, by
               ordering trades by exit time and taking the minimum of the
               cumulative sum.

    `trough` exists because a floor breach is checked on equity, not on the
    daily close: a day can dip through the drawdown floor and recover. It is
    still a lower bound on severity - it counts realised P&L only, so open
    positions marked against you are not included.
    """
    specs = {"halt-5m": ("anchor", 5)}
    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0])
            if p.name in sorted(set(fa.ORIGINAL + fa.TUNED))]
    files = discover_files(os.path.join(ROOT, "Monthly_Tick_Data"), from_month, to_month)
    print(f"{len(files)} months for the daily series", file=sys.stderr)
    args = argparse.Namespace(cache_dir=cache_dir, chunksize=chunksize)
    raw = fa.collect(files, grid, specs, args)["halt-5m"]

    out = {}
    for nm, ws in [("ORIGINAL", fa.ORIGINAL), ("TUNED", fa.TUNED)]:
        f = raw[raw["param"].isin(ws)].copy()
        f["per_lot"] = f.price_pnl * 100 - 7.0            # swap is 0 pre-halt
        f["xt"] = pd.to_datetime(f.exit_time, unit="ns")
        f = f.sort_values("xt")
        g = f.groupby(f.xt.dt.date)["per_lot"]
        close = g.sum().sort_index()
        trough = g.apply(lambda v: min(0.0, v.cumsum().min())).sort_index()
        out[nm] = pd.DataFrame({"close": close, "trough": trough})
    return out


def simulate(pct: np.ndarray, trough: np.ndarray, rule: dict,
             max_days: int, intraday: bool) -> tuple:
    """
    Run an evaluation from every start day. `pct` is the daily return and
    `trough` the worst intra-day excursion, both as a fraction of the INITIAL
    balance (linear in lots, so size-independent).

    With intraday=True the drawdown floor and the daily-loss rule are both
    tested against the intra-day trough rather than the close, which is what
    a live account actually experiences.
    """
    n = len(pct)
    passes, breaches, days_pass, days_attempt = 0, 0, [], []
    target, dd, daily, cap = rule["target"], rule["dd"], rule["daily"], rule["cap"]
    static = rule["dd_kind"] == "static"

    for s in range(n):
        bal = 1.0
        hwm = 1.0
        floor = 1.0 - dd
        done = False
        for i in range(s, min(n, s + max_days)):
            day = pct[i]
            worst = min(day, trough[i]) if intraday else day
            # daily hard breach is measured from the day's starting balance
            if daily is not None and worst < -daily:
                breaches += 1
                days_attempt.append(i - s + 1)
                done = True
                break
            # equity can pierce the floor intra-day and recover by the close
            if intraday and bal + worst <= floor:
                breaches += 1
                days_attempt.append(i - s + 1)
                done = True
                break
            if cap is not None:
                day = min(day, cap)            # Pro: only 2%/day counts
            bal += day
            if bal <= floor:
                breaches += 1
                days_attempt.append(i - s + 1)
                done = True
                break
            if not static:
                hwm = max(hwm, bal)
                floor = min(1.0, hwm - dd)     # trails EOD, locks at initial
            if bal >= 1.0 + target:
                passes += 1
                days_pass.append(i - s + 1)
                days_attempt.append(i - s + 1)
                done = True
                break
        if not done:
            days_attempt.append(min(n - s, max_days))   # censored
    tot = n
    return (passes / tot, breaches / tot,
            float(np.median(days_pass)) if days_pass else np.nan,
            float(np.mean(days_attempt)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E8 sizing table")
    ap.add_argument("--from-month", default="2024-01")
    ap.add_argument("--to-month", default="2026-07")
    ap.add_argument("--lots", default="0.02,0.03,0.04,0.05,0.06,0.08,0.10,0.12")
    ap.add_argument("--max-days", type=int, default=250)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--series", default=None, help="reuse a saved daily series")
    args = ap.parse_args(argv)
    os.makedirs(RESULTS, exist_ok=True)

    spath = args.series or os.path.join(RESULTS, "daily_per_lot_halt5m.csv")
    if os.path.exists(spath):
        s = pd.read_csv(spath, index_col=0, header=[0, 1])
        series = {pf: s[pf].dropna() for pf in s.columns.levels[0]}
        print(f"reusing {spath}", file=sys.stderr)
    else:
        series = daily_per_lot(args.from_month, args.to_month,
                               args.cache_dir, args.chunksize)
        pd.concat(series, axis=1).to_csv(spath)

    lots = [float(x) for x in args.lots.split(",")]
    rows = []
    for pf, df in series.items():
        close = df["close"].to_numpy(dtype=float)
        trough = df["trough"].to_numpy(dtype=float)
        for lp10k in lots:
            # daily return as a fraction of the account:
            #   $ per day = per_lot * (size/10000 * lp10k);  /size  ->  per_lot*lp10k/10000
            k = lp10k / 10_000.0
            for an, rule in ACCOUNTS.items():
                for intraday in (False, True):
                    p, b, dpass, datt = simulate(close * k, trough * k, rule,
                                                 args.max_days, intraday)
                    rows.append({"portfolio": pf, "lots_per_10k": lp10k, "account": an,
                                 "intraday": intraday,
                                 "pass_rate": p * 100, "breach_rate": b * 100,
                                 "median_days_to_pass": dpass,
                                 "exp_days_per_funded": datt / p if p > 0 else np.nan,
                                 "exp_attempts": 1 / p if p > 0 else np.nan})
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RESULTS, "prop_sizing.csv"), index=False)

    for pf in series:
        for intraday in (False, True):
            t = res[(res.portfolio == pf) & (res.intraday == intraday)]
            lbl = "INTRADAY equity (realistic)" if intraday else "daily close only (optimistic)"
            print(f"\n{'='*80}\n{pf}  -  {lbl}"
                  f"\n  pass% / median days to pass / expected days per funded\n{'='*80}")
            for an in ACCOUNTS:
                g = t[t.account == an]
                cells = "  ".join(
                    f"{r.pass_rate:5.1f}%/{('%3.0f' % r.median_days_to_pass) if r.median_days_to_pass == r.median_days_to_pass else ' na'}/"
                    f"{('%4.0f' % r.exp_days_per_funded) if r.exp_days_per_funded == r.exp_days_per_funded else '  na'}"
                    for _, r in g.iterrows())
                print(f"  {an:<11} {cells}")
            print(f"  {'lots/$10k':<11} " + "  ".join(f"{l:>14.2f}" for l in lots))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
