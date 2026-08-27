#!/usr/bin/env python3
"""
What is the best time to flatten, measured from the daily halt?

The Exness XAUUSD daily break is anchored to 17:00 New York, so in UTC it
moves with US DST (verified per-day against raw ticks, 2024-2026):

    US DST (summer)   20:58 -> ~22:01 UTC
    US standard       21:58 -> ~23:01 UTC

The EA flattens at a fixed 21:50 UTC, which is INSIDE the summer break on
~66% of trading days. OnTick is tick-driven, so nothing fires during the
halt and CloseEverything() runs on the first tick after the reopen - the
widest-spread minute of the day, and past the swap charge.

flatten_test.py could not see this: it truncates at searchsorted(tod,
flat_sec) and exits at the last bar at or before the flatten, so in summer
it silently exits at 20:57:5x - an hour early, pre-halt, at a good price.

This scans the flatten time as an OFFSET BEFORE THE HALT, so the same
trading rule is tested in both seasons, plus three fixed-UTC controls:

    backtest_2150  fixed 21:50, last bar at or before  (what flatten_test.py reports)
    ea_live_2150   fixed 21:50, first bar at or after  (what the EA actually does)
    utc_2357       the old session-end baseline

    python strategy_2026/flatten_anchor.py --cache-dir <dir>
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_time_windows as tw              # noqa: E402
from tickdata import EPS, NS_PER_SEC, discover_files, load_subbars, split_sessions  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
BIG = np.iinfo(np.int64).max

ORIGINAL = ["h00_r30_t1", "h01_r60_t3", "h02_r15_t3", "h04_r30_t3",
            "h05_r60_t2", "h06_r60_t3", "h13_r30_t3", "h14_r15_t2"]
TUNED = [f"h{h:02d}_r60_t3" for h in (0, 1, 2, 4, 5, 6, 14)]

SWAP_LONG_PER_LOT = -563.2 * 0.001 * 100        # points x point x contract, live rate

HALT_SUMMER = 20 * 3600 + 58 * 60               # verified against raw ticks
HALT_WINTER = 21 * 3600 + 58 * 60


def _nth_dow(y: int, m: int, dow: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    c = 0
    while True:
        if d.weekday() == dow:
            c += 1
            if c == n:
                return d
        d += dt.timedelta(1)


def us_dst(d: dt.date) -> bool:
    """US DST: 2nd Sunday in March to 1st Sunday in November."""
    return _nth_dow(d.year, 3, 6, 2) <= d < _nth_dow(d.year, 11, 6, 1)


def halt_start(d: dt.date) -> int:
    return HALT_SUMMER if us_dst(d) else HALT_WINTER


def session_entries(s, p, sub_sec: int):
    """Bracket + entry for one (session, window). Independent of the flatten."""
    sub = s.sub
    ns = sub["idx"] * sub_sec * NS_PER_SEC
    tod = (ns % (86_400 * NS_PER_SEC)) // NS_PER_SEC

    a0 = p.anchor_hour * 3600
    a1 = a0 + p.range_minutes * 60
    in_range = (tod >= a0) & (tod < a1)
    if in_range.sum() < 10:
        return None

    hi = float(sub["h"][in_range].max())
    lo = float(sub["l"][in_range].min())
    size = hi - lo
    if size <= EPS:
        return None
    ref = float(sub["c"][in_range][-1])
    range_pct = size / max(ref, EPS) * 100.0
    if not (p.min_range_pct <= range_pct <= p.max_range_pct):
        return None

    scan = np.flatnonzero((tod >= a1) & (tod <= a1 + p.max_wait_hours * 3600))
    if scan.size == 0:
        return None
    i0, i1 = int(scan[0]), int(scan[-1])

    up = np.flatnonzero(sub["ah"][i0:i1 + 1] >= hi)
    dn = np.flatnonzero(sub["bl"][i0:i1 + 1] <= lo)
    u = int(up[0]) if up.size else BIG
    d = int(dn[0]) if dn.size else BIG
    if (u == BIG and d == BIG) or u == d:
        return None

    if u < d:
        side, k = 1, i0 + u
        entry = max(hi, float(sub["ao"][k])) + p.slippage
        stop = lo
        target = entry + p.target_mult * size
    else:
        side, k = -1, i0 + d
        entry = min(lo, float(sub["bo"][k])) - p.slippage
        stop = hi
        target = entry - p.target_mult * size
    if p.stop_mult != 1.0:
        stop = entry - side * p.stop_mult * size

    # Resolve SL/TP once over the whole remaining session; the flatten only
    # decides which of those hits is still reachable. This is what makes a
    # wide offset scan cheap - the bracket and entry are computed once.
    if side > 0:
        adverse, favourable, close_px = sub["bl"][k:], sub["bh"][k:], sub["bc"][k:]
        hit_sl = np.flatnonzero(adverse <= stop)
        hit_tp = np.flatnonzero(favourable >= target)
    else:
        adverse, favourable, close_px = sub["ah"][k:], sub["al"][k:], sub["ac"][k:]
        hit_sl = np.flatnonzero(adverse >= stop)
        hit_tp = np.flatnonzero(favourable <= target)

    return dict(p=p, tod=tod, ns=ns, k=k, side=side, entry=entry, stop=stop,
                target=target, close_px=close_px, range_pct=range_pct,
                si=int(hit_sl[0]) if hit_sl.size else BIG,
                ti=int(hit_tp[0]) if hit_tp.size else BIG,
                n=int(sub["idx"].size))


def resolve(e, forced_idx: int):
    """Exit given the forced-close bar index (absolute)."""
    k = e["k"]
    if forced_idx <= k:
        return None                              # entry lands at/after the flatten
    lim = forced_idx - k                         # last reachable relative index
    si = e["si"] if e["si"] <= lim else BIG
    ti = e["ti"] if e["ti"] <= lim else BIG
    p = e["p"]

    if si == BIG and ti == BIG:
        rel, exit_px, reason = lim, float(e["close_px"][lim]), "forced_close"
    elif si <= ti:
        rel, exit_px, reason = si, float(e["stop"]), "sl"
        if p.slippage:
            exit_px += -p.slippage if e["side"] > 0 else p.slippage
    else:
        rel, exit_px, reason = ti, float(e["target"]), "tp"

    return {"param": p.name, "side": e["side"],
            "entry_time": int(e["ns"][k]), "exit_time": int(e["ns"][k + rel]),
            "entry_price": e["entry"], "exit_price": exit_px,
            "range_pct": e["range_pct"], "exit_reason": reason,
            "price_pnl": (exit_px - e["entry"]) * e["side"]}


def forced_index(tod, spec, sess_date, n) -> int:
    kind, val = spec
    if kind == "anchor":
        t = halt_start(sess_date) - val * 60
        return int(np.searchsorted(tod, t, side="right")) - 1
    if kind == "utc_pre":
        return int(np.searchsorted(tod, val, side="right")) - 1
    # utc_post: first bar at or after T - what a tick-driven EA actually does
    i = int(np.searchsorted(tod, val, side="left"))
    return min(i, n - 1)


def collect(files, grid, specs, args):
    out = {name: [] for name in specs}
    carry = None
    for i, path in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {os.path.basename(path)}", file=sys.stderr, flush=True)
        sub = load_subbars(path, 5, args.chunksize, args.cache_dir, False)
        sessions, carry = split_sessions(sub, 5, 5, 0, 100, carry, final=(i == len(files)))
        for s in sessions:
            sess_date = pd.Timestamp(s.day_ns, unit="ns").date()
            for p in grid:
                e = session_entries(s, p, 5)
                if e is None:
                    continue
                for name, spec in specs.items():
                    fi = forced_index(e["tod"], spec, sess_date, e["n"])
                    r = resolve(e, fi)
                    if r:
                        out[name].append(r)
    return {k: pd.DataFrame(v) for k, v in out.items()}


def score(raw, names, lots, equity) -> dict:
    f = raw[raw["param"].isin(names)].copy()
    if f.empty:
        return {}
    f["pnl"] = f.price_pnl * 100 * lots - 7.0 * lots
    et = pd.to_datetime(f.entry_time, unit="ns")
    xt = pd.to_datetime(f.exit_time, unit="ns")
    # Swap is charged at the halt, which moves with US DST.
    roll_sec = et.dt.date.map(halt_start)
    roll = et.dt.normalize() + pd.to_timedelta(roll_sec, unit="s")
    open_at_roll = (et <= roll) & (xt >= roll)
    mult = np.where(roll.dt.dayofweek == 2, 3, 1)              # triple swap Wednesday
    f["swap"] = np.where(open_at_roll & (f.side > 0), SWAP_LONG_PER_LOT * lots * mult, 0.0)
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
    ap = argparse.ArgumentParser(description="halt-anchored flatten optimisation")
    ap.add_argument("--from-month", default="2024-01")
    ap.add_argument("--to-month", default="2026-07")
    ap.add_argument("--offsets", default="0,2,5,8,10,15,20,25,30,40,50,60,75,90,105,120,150,180")
    ap.add_argument("--lots", type=float, default=0.02)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)
    os.makedirs(RESULTS, exist_ok=True)

    offs = [int(x) for x in args.offsets.split(",")]
    specs = {f"halt-{o}m": ("anchor", o) for o in offs}
    specs["backtest_2150"] = ("utc_pre", 21 * 3600 + 50 * 60)
    specs["ea_live_2150"] = ("utc_post", 21 * 3600 + 50 * 60)
    specs["utc_2357"] = ("utc_pre", 23 * 3600 + 57 * 60)

    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0])
            if p.name in sorted(set(ORIGINAL + TUNED))]
    files = discover_files(os.path.join(ROOT, "Monthly_Tick_Data"),
                           args.from_month, args.to_month)
    print(f"{len(files)} months, {args.from_month}..{args.to_month}; "
          f"{len(specs)} flatten rules", file=sys.stderr)

    frames = collect(files, grid, specs, args)
    rows = []
    for name in specs:
        for nm, ws in [("ORIGINAL", ORIGINAL), ("TUNED", TUNED)]:
            rows.append({"rule": name, "portfolio": nm,
                         **score(frames[name], ws, args.lots, args.equity)})
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RESULTS, "flatten_anchor.csv"), index=False)

    fmt = lambda v: f"{v:,.2f}"
    for nm in ("ORIGINAL", "TUNED"):
        t = res[res.portfolio == nm].drop(columns=["portfolio"])
        print(f"\n=== {nm} ===")
        print(t.to_string(index=False, float_format=fmt))
        anc = t[t.rule.str.startswith("halt-")]
        if len(anc):
            best = anc.loc[anc.net.idxmax()]
            print(f"\n  best anchored flatten: {best.rule}  net {best.net:,.0f}  "
                  f"PF {best.pf:.3f}  maxDD {best.maxDD:,.0f}  Sharpe {best.sharpe:.2f}")
            for ctl in ("backtest_2150", "ea_live_2150", "utc_2357"):
                c = t[t.rule == ctl]
                if len(c):
                    c = c.iloc[0]
                    print(f"    vs {ctl:<14} net {c.net:>9,.0f}  "
                          f"({best.net - c.net:+,.0f})  PF {c.pf:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
