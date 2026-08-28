#!/usr/bin/env python3
"""
Cash actually extracted from a FUNDED account, per E8 product.

Passing the challenge is not the objective - withdrawing is. The three
products gate payouts very differently, and for a strategy whose profit is
concentrated in a few large days the gate matters more than the pass rate:

  One   40% best-day rule, net profit > 50% of the daily drawdown
  Pro   no consistency rule at all, but only 2%/day counts toward profit,
        and only 50% of profit is requestable (rest is a buffer)
  Sig   35% best-day rule, 5 profitable days (>=0.3%) between payouts,
        and a permanent buffer equal to the EOD drawdown

Modelling notes / assumptions, all conservative-leaning and flagged:
  * "total generated profits" for a best-day rule is read as profit since
    the last payout; profitable-day counters reset on payout (stated for
    Signature, assumed for One).
  * Signature's 2% daily limit is a SOFT PAUSE - the day is truncated, the
    account is not breached. Modelled by capping the day's loss at -2%.
  * Payout caps on Signature are acknowledged but not modelled (unspecified),
    so Signature's figure here is an UPPER bound.
  * News restriction on One's Performance stage is not modelled; it is a
    compliance hazard for a pending-stop-order strategy, not a P&L term.

    python strategy_2026/payout_sim.py
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

# product, split, target-stage rules
PRODUCTS = {
    # dd = hard floor below the trailing/static reference
    # daily = hard daily breach (None = none); soft_daily = pause, not breach
    "One $100k":  dict(dd=0.04, kind="trail",  daily=0.03,  soft_daily=None,
                       cap=None, split=0.80, gate_best=0.40,
                       min_profit=0.015, buffer=0.0,   requestable=1.00, min_pdays=0),
    "One $100k (100% split)":
                  dict(dd=0.04, kind="trail",  daily=0.03,  soft_daily=None,
                       cap=None, split=1.00, gate_best=0.40,
                       min_profit=0.015, buffer=0.0,   requestable=1.00, min_pdays=0),
    "Pro $100k":  dict(dd=0.08, kind="static", daily=0.025, soft_daily=None,
                       cap=0.02, split=0.80, gate_best=None,
                       min_profit=0.010, buffer=0.0,   requestable=0.50, min_pdays=0),
    "Pro $100k (100% split)":
                  dict(dd=0.08, kind="static", daily=0.025, soft_daily=None,
                       cap=0.02, split=1.00, gate_best=None,
                       min_profit=0.010, buffer=0.0,   requestable=0.50, min_pdays=0),
    # pay_caps: per-payout ceiling on CASH RECEIVED, as a fraction of the
    # initial balance. E8 Signature caps the 1st-2nd, 3rd-4th and 5th+ payouts
    # at fixed dollar amounts; profit above the cap is not lost, it stays in
    # the account and is withdrawable at the next request. The last entry
    # applies to every payout beyond the list.
    #   $50k:  1,250 / 1,250 / 2,250 / 2,250 / 3,250+
    #   $100k: 2,250 / 2,250 / 3,250 / 3,250 / 4,250+
    "Sig $50k":   dict(dd=0.04, kind="eod",    daily=None,  soft_daily=0.02,
                       cap=None, split=0.80, gate_best=0.35,
                       min_profit=0.0025, buffer=0.04, requestable=1.00, min_pdays=5,
                       pay_caps=[0.025, 0.025, 0.045, 0.045, 0.065]),
    "Sig $100k":  dict(dd=0.03, kind="eod",    daily=None,  soft_daily=0.02,
                       cap=None, split=0.80, gate_best=0.35,
                       min_profit=0.0030, buffer=0.03, requestable=1.00, min_pdays=5,
                       pay_caps=[0.0225, 0.0225, 0.0325, 0.0325, 0.0425]),
}


def run(close, trough, r, start, max_days):
    """
    Simulate one funded account from `start`. Returns
    (cash_withdrawn, days_survived, n_payouts, breached).
    All figures are fractions of the INITIAL balance.
    """
    bal, hwm = 1.0, 1.0
    floor = 1.0 - r["dd"]
    static = r["kind"] == "static"
    cash, payouts = 0.0, 0
    since = []                       # daily P&L since the last payout
    pdays = 0                        # profitable days since last payout
    first_payout_done = False
    n = len(close)

    for i in range(start, min(n, start + max_days)):
        day, low = close[i], min(close[i], trough[i])

        if r["soft_daily"] is not None:            # soft pause: truncate the day
            day = max(day, -r["soft_daily"])
            low = max(low, -r["soft_daily"])

        if r["daily"] is not None and low < -r["daily"]:
            return cash, i - start + 1, payouts, True
        if bal + low <= floor:
            return cash, i - start + 1, payouts, True

        if r["cap"] is not None:
            day = min(day, r["cap"])               # Pro: only 2%/day counts

        bal += day
        if bal <= floor:
            return cash, i - start + 1, payouts, True
        if not static:
            hwm = max(hwm, bal)
            floor = min(1.0, hwm - r["dd"])
        elif first_payout_done:
            floor = 1.0                            # Pro: floor moves up after payout 1

        since.append(day)
        if day >= 0.003:
            pdays += 1

        # ---- payout gate ----
        profit = bal - 1.0
        if profit <= r["min_profit"]:
            continue
        if r["min_pdays"] and first_payout_done and pdays < r["min_pdays"]:
            continue
        if r["gate_best"] is not None:
            gross = sum(x for x in since if x > 0) or 1e-9
            tot = sum(since)
            if tot <= 0 or max(since) / tot > r["gate_best"]:
                continue                            # too concentrated, wait
        requestable = profit * r["requestable"] - r["buffer"]
        if requestable <= 0:
            continue

        paid = requestable * r["split"]
        caps = r.get("pay_caps")
        if caps:
            ceiling = caps[min(payouts + 1, len(caps)) - 1]
            if paid > ceiling:
                paid = ceiling
                requestable = ceiling / r["split"]   # the rest stays in the account
        cash += paid
        bal -= requestable
        hwm = max(1.0, bal)
        floor = min(1.0, hwm - r["dd"]) if not static else 1.0
        payouts += 1
        first_payout_done = True
        since, pdays = [], 0

    return cash, min(n - start, max_days), payouts, False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lots", default="0.02,0.025,0.03")
    ap.add_argument("--max-days", type=int, default=250)
    ap.add_argument("--portfolio", default="ORIGINAL")
    ap.add_argument("--series", default=None, help="reuse a saved daily series")
    args = ap.parse_args(argv)

    s = pd.read_csv(args.series or os.path.join(RESULTS, "daily_per_lot_halt5m.csv"),
                    index_col=0, header=[0, 1])
    df = s[args.portfolio].dropna()
    close_pl = df["close"].to_numpy(float)
    trough_pl = df["trough"].to_numpy(float)
    n = len(close_pl)

    rows = []
    for lp in [float(x) for x in args.lots.split(",")]:
        k = lp / 10_000.0
        c, t = close_pl * k, trough_pl * k
        for name, r in PRODUCTS.items():
            res = [run(c, t, r, st, args.max_days) for st in range(n)]
            cash = np.array([x[0] for x in res])
            pay = np.array([x[2] for x in res])
            br = np.array([x[3] for x in res])
            rows.append({"lots_per_10k": lp, "product": name,
                         "mean_cash_pct": cash.mean() * 100,
                         "median_cash_pct": np.median(cash) * 100,
                         "pct_with_a_payout": (pay > 0).mean() * 100,
                         "mean_payouts": pay.mean(),
                         "breach_pct": br.mean() * 100})
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RESULTS, "payout_sim.csv"), index=False)
    for lp, g in res.groupby("lots_per_10k"):
        print(f"\n=== {args.portfolio} @ {lp} lots/$10k - funded account, "
              f"{args.max_days} trading days ===")
        print(g.drop(columns=["lots_per_10k"]).to_string(
            index=False, float_format=lambda v: f"{v:,.2f}"))
    print("\ncash figures are % of the account's initial balance, withdrawn to your pocket")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
