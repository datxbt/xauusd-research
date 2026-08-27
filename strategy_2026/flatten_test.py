#!/usr/bin/env python3
"""
Does flattening before the 22:00 rollover beat the 23:57 UTC close?

The 23:57 flatten was never a trading decision - it fell out of defining
sessions as UTC calendar days back in the first backtester, and was inherited
all the way into the EA. That leaves every still-open position exposed to:

  * the 21:58-22:00 halt (no ticks at all for 2-3 minutes)
  * a spread spike to ~2.9x the daytime baseline right after it
  * the overnight swap, charged at rollover: -$56.32/lot on LONGS, shorts free

Closing earlier avoids all three and exits in the tightest-spread hour of the
day (21:00). The cost is truncating trades that are still running.

This resolves it by re-running the exit scan with the forced close moved
earlier, and charging swap only when a position is actually open at 22:00.
The shared engine is not modified - only the exit horizon is overridden here.

    python strategy_2026/flatten_test.py
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

import backtest_orb as ob                       # noqa: E402
import optimize_time_windows as tw              # noqa: E402
from tickdata import EPS, NS_PER_SEC, discover_files, load_subbars, split_sessions  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
BIG = np.iinfo(np.int64).max

ORIGINAL = ["h00_r30_t1", "h01_r60_t3", "h02_r15_t3", "h04_r30_t3",
            "h05_r60_t2", "h06_r60_t3", "h13_r30_t3", "h14_r15_t2"]
TUNED = [f"h{h:02d}_r60_t3" for h in (0, 1, 2, 4, 5, 6, 14)]

ROLLOVER_SEC = 22 * 3600
SWAP_LONG_PER_LOT = -563.2 * 0.001 * 100        # points x point x contract, live rate
SWAP_SHORT_PER_LOT = 0.0


def run_session_flat(s, p, sub_sec: int, flat_sec: int) -> list:
    """
    ob.run_session_orb with the forced close moved to `flat_sec` (UTC second
    of day). Identical in every other respect - entry, stop, target, filters.
    """
    sub = s.sub
    ns = sub["idx"] * sub_sec * NS_PER_SEC
    tod = (ns % (86_400 * NS_PER_SEC)) // NS_PER_SEC

    a0 = p.anchor_hour * 3600
    a1 = a0 + p.range_minutes * 60
    in_range = (tod >= a0) & (tod < a1)
    if in_range.sum() < 10:
        return []

    hi = float(sub["h"][in_range].max())
    lo = float(sub["l"][in_range].min())
    size = hi - lo
    if size <= EPS:
        return []
    ref = float(sub["c"][in_range][-1])
    range_pct = size / max(ref, EPS) * 100.0
    if not (p.min_range_pct <= range_pct <= p.max_range_pct):
        return []

    scan = np.flatnonzero((tod >= a1) & (tod <= a1 + p.max_wait_hours * 3600))
    if scan.size == 0:
        return []
    i0, i1 = int(scan[0]), int(scan[-1])

    up = np.flatnonzero(sub["ah"][i0:i1 + 1] >= hi)
    dn = np.flatnonzero(sub["bl"][i0:i1 + 1] <= lo)
    u = int(up[0]) if up.size else BIG
    d = int(dn[0]) if dn.size else BIG
    if u == BIG and d == BIG or u == d:
        return []

    if u < d:
        side, k = 1, i0 + u
        entry = max(hi, float(sub["ao"][k])) + p.slippage
        stop, target = lo, 0.0
        target = entry + p.target_mult * size
    else:
        side, k = -1, i0 + d
        entry = min(lo, float(sub["bo"][k])) - p.slippage
        stop = hi
        target = entry - p.target_mult * size
    if p.stop_mult != 1.0:
        stop = entry - side * p.stop_mult * size

    # ---- the only change: cap the forward scan at the flatten time ----
    end = int(np.searchsorted(tod, flat_sec, side="right"))
    end = min(end, sub["idx"].size)
    if end <= k + 1:
        return []                                # entry lands at/after the flatten

    if side > 0:
        adverse, favourable, close_px = sub["bl"][k:end], sub["bh"][k:end], sub["bc"][k:end]
        hit_sl = np.flatnonzero(adverse <= stop)
        hit_tp = np.flatnonzero(favourable >= target)
    else:
        adverse, favourable, close_px = sub["ah"][k:end], sub["al"][k:end], sub["ac"][k:end]
        hit_sl = np.flatnonzero(adverse >= stop)
        hit_tp = np.flatnonzero(favourable <= target)

    si = int(hit_sl[0]) if hit_sl.size else BIG
    ti = int(hit_tp[0]) if hit_tp.size else BIG
    if si == BIG and ti == BIG:
        rel, exit_px, reason = len(close_px) - 1, float(close_px[-1]), "forced_close"
    elif si <= ti:
        rel, exit_px, reason = si, float(stop), "sl"
        if p.slippage:
            exit_px += -p.slippage if side > 0 else p.slippage
    else:
        rel, exit_px, reason = ti, float(target), "tp"

    return [{"param": p.name, "session": s.day_ns, "side": side,
             "entry_time": int(ns[k]), "exit_time": int(ns[k + rel]),
             "bars_held": int(rel), "entry_price": entry, "exit_price": exit_px,
             "stop_price": stop, "target_price": target, "range_size": size,
             "range_pct": range_pct, "exit_reason": reason,
             "price_pnl": (exit_px - entry) * side, "risk_price": abs(entry - stop)}]


def collect_all(files, grid, args, flat_secs):
    """One pass over the tape, every flatten time evaluated per session."""
    out = {fs: [] for fs in flat_secs}
    carry = None
    for i, path in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {os.path.basename(path)}", file=sys.stderr, flush=True)
        sub = load_subbars(path, 5, args.chunksize, args.cache_dir, False)
        sessions, carry = split_sessions(sub, 5, 5, 0, 100, carry, final=(i == len(files)))
        for s in sessions:
            for p in grid:
                for fs in flat_secs:
                    out[fs].extend(run_session_flat(s, p, 5, fs))
    return {fs: pd.DataFrame(v) for fs, v in out.items()}


def score(raw: pd.DataFrame, names, lots, equity) -> dict:
    f = raw[raw["param"].isin(names)].copy()
    if f.empty:
        return {}
    f["pnl"] = f.price_pnl * 100 * lots - 7.0 * lots
    et = pd.to_datetime(f.entry_time, unit="ns")
    xt = pd.to_datetime(f.exit_time, unit="ns")
    roll = et.dt.normalize() + pd.Timedelta(seconds=ROLLOVER_SEC)
    open_at_roll = (et <= roll) & (xt >= roll)
    mult = np.where(roll.dt.dayofweek == 2, 3, 1)              # triple swap Wednesday
    swap = np.where(open_at_roll & (f.side > 0), SWAP_LONG_PER_LOT * lots * mult, 0.0)
    f["swap"] = swap
    f["net"] = f.pnl + f.swap

    d = f.groupby(xt.dt.date)["net"].sum()
    eq = equity + d.cumsum()
    dd = (eq - eq.cummax()).min()
    r = d / equity
    w, l = f[f.net > 0], f[f.net < 0]
    return {"trades": len(f), "win_rate": (f.net > 0).mean() * 100,
            "pf": w.net.sum() / abs(l.net.sum()) if len(l) else np.nan,
            "gross": f.pnl.sum(), "swap": f.swap.sum(), "net": f.net.sum(),
            "per_trade": f.net.mean(), "held_at_roll": int(open_at_roll.sum()),
            "maxDD": dd, "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="flatten time comparison")
    ap.add_argument("--from-month", default="2024-01")
    ap.add_argument("--to-month", default="2026-07")
    ap.add_argument("--flats", default="20:00,21:00,21:50,22:30,23:57")
    ap.add_argument("--lots", type=float, default=0.02)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)
    os.makedirs(RESULTS, exist_ok=True)

    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0])
            if p.name in sorted(set(ORIGINAL + TUNED))]
    files = discover_files(os.path.join(ROOT, "Monthly_Tick_Data"),
                           args.from_month, args.to_month)
    print(f"{len(files)} months, {args.from_month}..{args.to_month}", file=sys.stderr)

    specs = args.flats.split(",")
    secs = {s_: int(s_.split(":")[0]) * 3600 + int(s_.split(":")[1]) * 60 for s_ in specs}
    frames = collect_all(files, grid, args, list(secs.values()))
    rows = []
    for spec in specs:
        raw = frames[secs[spec]]
        for nm, ws in [("ORIGINAL", ORIGINAL), ("TUNED", TUNED)]:
            rows.append({"flatten": spec, "portfolio": nm, **score(raw, ws, args.lots, args.equity)})

    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RESULTS, "flatten_comparison.csv"), index=False)
    fmt = lambda v: f"{v:,.2f}"
    for nm in ("ORIGINAL", "TUNED"):
        print(f"\n=== {nm} ===")
        t = res[res.portfolio == nm].drop(columns=["portfolio"])
        print(t.to_string(index=False, float_format=fmt))
        base = t[t.flatten == "23:57"]
        if len(base):
            b = base.iloc[0]
            print(f"\n  vs the 23:57 baseline (net {b.net:,.0f}):")
            for _, r in t.iterrows():
                if r.flatten == "23:57":
                    continue
                print(f"    {r.flatten}  net {r.net:>9,.0f}  ({r.net - b.net:+,.0f}, "
                      f"{(r.net / b.net - 1) * 100:+.1f}%)   PF {r.pf:.3f} vs {b.pf:.3f}"
                      f"   maxDD {r.maxDD:>8,.0f} vs {b.maxDD:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
