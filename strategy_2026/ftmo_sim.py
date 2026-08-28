#!/usr/bin/env python3
"""
FTMO vs E8, on the same daily series.

The structural difference that matters: FTMO's floors are wide enough to
contain this strategy's own drawdown, and E8's are not.

    strategy (ORIGINAL, 0.02 lots/$10k):  worst day -4.39%,  max DD 8.33%

    FTMO 2-Step   daily 5%,  max loss 10% STATIC from initial
    FTMO 1-Step   daily 3%,  max loss 10% EOD-trailing (ratchets up only)
    E8 One        daily 3%,  dynamic 4% (locks at initial)
    E8 Pro        daily 2.5%, static 8% (jumps to initial after payout 1)
    E8 Signature  no daily,  EOD dynamic 3-4% (locks at initial)

FTMO 2-Step costs two phases (10% then 5%) but the funded account then has
no profit target and no Best Day rule. FTMO 1-Step has a 50% Best Day rule,
which - unlike E8's - is explicitly NOT a breach: you simply keep trading
until the concentration dilutes.

    python strategy_2026/ftmo_sim.py
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

PHASES = {
    # (target, daily, maxloss, kind)  kind: static | eod_trail
    "FTMO 2-Step P1":   (0.10, 0.05, 0.10, "static"),
    "FTMO 2-Step P2":   (0.05, 0.05, 0.10, "static"),
    "FTMO 1-Step":      (0.10, 0.03, 0.10, "eod_trail"),
}


def phase(close, trough, start, target, daily, maxloss, kind, max_days):
    """Return (passed, days_used, end_index)."""
    bal, hwm = 1.0, 1.0
    floor = 1.0 - maxloss
    n = len(close)
    for i in range(start, min(n, start + max_days)):
        day_start = bal
        d, low = close[i], min(close[i], trough[i])
        # daily limit is measured from the day's opening balance
        if low < -daily:
            return False, i - start + 1, i
        if bal + low <= floor:
            return False, i - start + 1, i
        bal += d
        if bal <= floor:
            return False, i - start + 1, i
        if kind == "eod_trail":
            hwm = max(hwm, bal)
            floor = max(floor, hwm - maxloss)      # ratchets up, never down
        if bal >= 1.0 + target:
            return True, i - start + 1, i
    return False, min(n - start, max_days), min(n, start + max_days) - 1


def funded_cash(close, trough, start, daily, maxloss, kind, best_day_gate,
                split, max_days, min_profit):
    """Cash withdrawn from a funded FTMO account, as a fraction of initial."""
    bal, hwm = 1.0, 1.0
    floor = 1.0 - maxloss
    cash, payouts, since = 0.0, 0, []
    n = len(close)
    for i in range(start, min(n, start + max_days)):
        d, low = close[i], min(close[i], trough[i])
        if low < -daily:
            return cash, payouts, True
        if bal + low <= floor:
            return cash, payouts, True
        bal += d
        if bal <= floor:
            return cash, payouts, True
        if kind == "eod_trail":
            hwm = max(hwm, bal)
            floor = max(floor, hwm - maxloss)
        since.append(d)
        profit = bal - 1.0
        if profit <= min_profit:
            continue
        if best_day_gate is not None:
            pos = [x for x in since if x > 0]
            if pos and max(pos) / sum(pos) > best_day_gate:
                continue                            # keep trading, not a breach
        cash += profit * split
        bal -= profit
        # FTMO 1-Step: floor fully resets to 90% of initial after a withdrawal
        hwm = max(1.0, bal)
        floor = 1.0 - maxloss
        payouts += 1
        since = []
    return cash, payouts, False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lots", default="0.02,0.03,0.04")
    ap.add_argument("--portfolio", default="ORIGINAL")
    ap.add_argument("--max-days", type=int, default=250)
    ap.add_argument("--split", type=float, default=0.80)
    args = ap.parse_args(argv)

    s = pd.read_csv(os.path.join(RESULTS, "daily_per_lot_halt5m.csv"),
                    index_col=0, header=[0, 1])
    df = s[args.portfolio].dropna()
    c0 = df["close"].to_numpy(float)
    t0 = df["trough"].to_numpy(float)
    n = len(c0)

    rows = []
    for lp in [float(x) for x in args.lots.split(",")]:
        k = lp / 10_000.0
        c, t = c0 * k, t0 * k

        # ---- 2-Step: chain phase 1 -> phase 2 ----
        ok2, days2 = 0, []
        for st in range(n):
            p1, d1, e1 = phase(c, t, st, *PHASES["FTMO 2-Step P1"], args.max_days)
            if not p1:
                continue
            p2, d2, _ = phase(c, t, min(e1 + 1, n - 1), *PHASES["FTMO 2-Step P2"],
                              args.max_days)
            if p2:
                ok2 += 1
                days2.append(d1 + d2)
        # ---- 1-Step ----
        ok1, days1 = 0, []
        for st in range(n):
            p, d, _ = phase(c, t, st, *PHASES["FTMO 1-Step"], args.max_days)
            if p:
                ok1 += 1
                days1.append(d)

        # ---- funded stage ----
        f2 = [funded_cash(c, t, st, 0.05, 0.10, "static", None,
                          args.split, args.max_days, 0.01) for st in range(n)]
        f1 = [funded_cash(c, t, st, 0.03, 0.10, "eod_trail", 0.50,
                          args.split, args.max_days, 0.01) for st in range(n)]

        for nm, ok, dd, f in [("FTMO 2-Step", ok2, days2, f2),
                              ("FTMO 1-Step", ok1, days1, f1)]:
            cash = np.array([x[0] for x in f])
            br = np.array([x[2] for x in f])
            rows.append({"lots_per_10k": lp, "product": nm,
                         "pass_pct": ok / n * 100,
                         "median_days": float(np.median(dd)) if dd else np.nan,
                         "funded_cash_pct": cash.mean() * 100,
                         "funded_breach_pct": br.mean() * 100,
                         "mean_payouts": np.mean([x[1] for x in f])})
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RESULTS, "ftmo_sim.csv"), index=False)
    for lp, g in res.groupby("lots_per_10k"):
        print(f"\n=== {args.portfolio} @ {lp} lots/$10k "
              f"(split {args.split:.0%}, {args.max_days}d funded window) ===")
        print(g.drop(columns=["lots_per_10k"]).to_string(
            index=False, float_format=lambda v: f"{v:,.2f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
