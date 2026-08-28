#!/usr/bin/env python3
"""
What does the FTMO news filter cost?

FTMO restricts opening or closing a trade on a targeted instrument - XAUUSD
is one, via USD - within 2 minutes either side of six named US releases:

    Federal Funds Rate & Statement    14:00 ET,  8x/year
    FOMC Meeting Minutes             14:00 ET,  8x/year
    Non-Farm Employment Change       08:30 ET,  monthly
    Unemployment Rate & Wages        08:30 ET,  with NFP
    CPI y/y                          08:30 ET,  monthly
    Advance GDP q/q                  08:30 ET,  quarterly

A Stop Loss or Take Profit firing inside the window counts as a close, so a
resting bracket is exposed whether or not you meant to trade the news.
XAUUSD_SessionBreakout_NewsSafe.mq5 avoids that by pulling its pendings
before the window and re-arming after. This measures what that costs.

SCENARIOS
`real` is the measurement: the six named releases, on the dates they actually
landed, from --events (see us_macro_releases.csv). Everything else brackets
it from above by blacking out MORE than FTMO does, and the gap between `real`
and the bounds is what tells you whether the bounds were worth anything:

    fomc_wed   14:00 ET every Wednesday   (~52 days/yr vs the real 16)
    fomc_all   14:00 ET every weekday     (~250 days/yr)
    am_all     08:30 ET every weekday     (~250 days/yr)
    all_all    both, every weekday        (the EA's schedule-fallback mode)

Each is an upper bound on the corresponding real cost, kept so the effect of
over-blocking stays visible next to the true number.

The blackout modelled is the EA's, not the rule's: InpNewsPadBefore/After of
5 minutes, and positions flattened 60s before the window opens. That is wider
than the 2-minute rule on purpose, so this measures the shipped behaviour.

    python strategy_2026/news_sim.py --cache-dir <dir>
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import flatten_anchor as fa                       # noqa: E402
import ftmo_sim as fs                             # noqa: E402
import optimize_time_windows as tw                # noqa: E402
from tickdata import EPS, NS_PER_SEC, discover_files, load_subbars, split_sessions  # noqa: E402

RESULTS = os.path.join(HERE, "results")
BIG = np.iinfo(np.int64).max

AM_ET = (8, 30)      # NFP, CPI, Unemployment & Wages, Advance GDP
PM_ET = (14, 0)      # Fed Funds Rate & Statement, FOMC Minutes

SCENARIOS = ["none", "real", "fomc_wed", "fomc_all", "am_all", "all_all"]


# --------------------------------------------------------------------------
# blackout windows, in UTC seconds-of-day
# --------------------------------------------------------------------------
def _et_to_utc_sec(hh: int, mm: int, d: dt.date) -> int:
    """New York clock -> UTC second-of-day. EDT = UTC-4, EST = UTC-5."""
    return (hh + (4 if fa.us_dst(d) else 5)) * 3600 + mm * 60


def load_events(path: str) -> dict:
    """{date: {"am", "pm"}} from a `YYYY-MM-DD,slot` file."""
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.split("#")[0].strip()
            if not line or line.lower().startswith("date"):
                continue
            parts = line.replace(",", " ").split()
            out.setdefault(dt.date.fromisoformat(parts[0]), set()).add(parts[1].lower())
    return out


def blackouts(d: dt.date, scenario: str, pad_min: int, fomc_days, events) -> list:
    """[(start, end), ...] padded, UTC seconds-of-day."""
    if scenario == "none":
        return []
    if d.weekday() >= 5:
        return []

    ev = []
    if scenario == "real":
        # The measurement, not a bound: only the six releases FTMO names, on
        # the dates they actually landed.
        slots = events.get(d) if events else None
        if not slots:
            return []
        if "am" in slots:
            ev.append(AM_ET)
        if "pm" in slots:
            ev.append(PM_ET)
    elif scenario == "fomc_wed":
        hit = (d in fomc_days) if fomc_days is not None else (d.weekday() == 2)
        if hit:
            ev.append(PM_ET)
    elif scenario == "fomc_all":
        ev.append(PM_ET)
    elif scenario == "am_all":
        ev.append(AM_ET)
    elif scenario == "all_all":
        ev += [AM_ET, PM_ET]

    pad = pad_min * 60
    return [(_et_to_utc_sec(h, m, d) - pad, _et_to_utc_sec(h, m, d) + pad)
            for h, m in ev]


def _block_at(blocks, sec: int):
    for b in blocks:
        if b[0] <= sec <= b[1]:
            return b
    return None


# --------------------------------------------------------------------------
# entry, with the EA's defer-and-re-arm behaviour
# --------------------------------------------------------------------------
# fa.session_entries() finds the FIRST break and stops. The news EA can be
# forced past that one - its pendings are pulled before the window - so the
# scan has to be resumable. Bracket construction below is fa's, unchanged;
# only the scan loop is new.
def bracket(s, p, sub_sec: int):
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

    return dict(sub=sub, ns=ns, tod=tod, hi=hi, lo=lo, size=size,
                a1=a1, range_pct=range_pct)


def entry(b, p, blocks):
    """The trade this portfolio actually takes, given the day's blackouts."""
    sub, tod = b["sub"], b["tod"]
    hi, lo, size = b["hi"], b["lo"], b["size"]
    limit = b["a1"] + p.max_wait_hours * 3600
    start = b["a1"]

    for _ in range(len(blocks) + 2):        # can only defer once per blackout
        scan = np.flatnonzero((tod >= start) & (tod <= limit))
        if scan.size == 0:
            return None
        i0, i1 = int(scan[0]), int(scan[-1])

        up = np.flatnonzero(sub["ah"][i0:i1 + 1] >= hi)
        dn = np.flatnonzero(sub["bl"][i0:i1 + 1] <= lo)
        u = int(up[0]) if up.size else BIG
        d = int(dn[0]) if dn.size else BIG
        if (u == BIG and d == BIG) or u == d:
            return None
        k = i0 + min(u, d)

        blk = _block_at(blocks, int(tod[k]))
        if blk is None:
            break

        # The break landed inside a blackout, so no order was live to take it.
        # Re-arm at the far edge - but only if the bracket is still intact.
        # If the release itself broke the range we sit the day out, which is
        # the whole point: that break is the trade we are not allowed to have.
        b_end = blk[1]
        if b_end > limit:
            return None
        j = int(np.searchsorted(tod, b_end, side="left"))
        if j >= tod.size:
            return None
        if float(sub["ac"][j]) >= hi or float(sub["bc"][j]) <= lo:
            return None
        # Strictly PAST the edge. Resuming at b_end re-tests the bar sitting on
        # it, and that bar is still inside the (inclusive) blackout - so a bar
        # whose high tags `hi` while its close falls back inside defers to the
        # same block forever. The EA cannot fill on the edge bar either.
        start = b_end + 1
    else:
        return None                          # never resolved; treat as no trade

    if u < d:
        side = 1
        ent = max(hi, float(sub["ao"][k])) + p.slippage
        stop, target = lo, None
        target = ent + p.target_mult * size
    else:
        side = -1
        ent = min(lo, float(sub["bo"][k])) - p.slippage
        stop = hi
        target = ent - p.target_mult * size
    if p.stop_mult != 1.0:
        stop = ent - side * p.stop_mult * size

    if side > 0:
        adverse, favourable, close_px = sub["bl"][k:], sub["bh"][k:], sub["bc"][k:]
        hit_sl = np.flatnonzero(adverse <= stop)
        hit_tp = np.flatnonzero(favourable >= target)
    else:
        adverse, favourable, close_px = sub["ah"][k:], sub["al"][k:], sub["ac"][k:]
        hit_sl = np.flatnonzero(adverse >= stop)
        hit_tp = np.flatnonzero(favourable <= target)

    return dict(p=p, tod=tod, ns=b["ns"], k=k, side=side, entry=ent, stop=stop,
                target=target, close_px=close_px, range_pct=b["range_pct"],
                si=int(hit_sl[0]) if hit_sl.size else BIG,
                ti=int(hit_tp[0]) if hit_tp.size else BIG,
                n=int(sub["idx"].size))


def forced_idx(e, sess_date, blocks, flat_lead_sec: int) -> int:
    """Daily flatten, or the pre-news flatten if one comes first."""
    tod = e["tod"]
    fi = fa.forced_index(tod, ("anchor", 5), sess_date, e["n"])
    entry_sec = int(tod[e["k"]])
    for b0, _ in blocks:
        cut = b0 - flat_lead_sec
        if cut <= entry_sec:
            continue
        ni = int(np.searchsorted(tod, cut, side="right")) - 1
        fi = min(fi, ni)
    return fi


# --------------------------------------------------------------------------
def collect(files, grid, args, fomc_days, events):
    out = {sc: [] for sc in SCENARIOS}
    carry = None
    for i, path in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {os.path.basename(path)}", file=sys.stderr, flush=True)
        sub = load_subbars(path, 5, args.chunksize, args.cache_dir, False)
        sessions, carry = split_sessions(sub, 5, 5, 0, 100, carry, final=(i == len(files)))
        for s in sessions:
            sess_date = pd.Timestamp(s.day_ns, unit="ns").date()
            for p in grid:
                b = bracket(s, p, 5)
                if b is None:
                    continue
                for sc in SCENARIOS:
                    blocks = blackouts(sess_date, sc, args.pad, fomc_days, events)
                    e = entry(b, p, blocks)
                    if e is None:
                        continue
                    r = fa.resolve(e, forced_idx(e, sess_date, blocks, args.flat_lead))
                    if r:
                        out[sc].append(r)
    return {k: pd.DataFrame(v) for k, v in out.items()}


def daily_series(raw: pd.DataFrame, names) -> pd.DataFrame:
    """Per-lot close/trough by day - the same construction prop_sizing uses.

    `trades` rides along so an execution-cost penalty, which is charged per
    trade, can be applied downstream without re-running the tick pass. The
    modelled cost here is Exness Raw's own spread (already in the ask/bid
    arrays) plus $7/lot; a prop firm's feed is wider and that difference is
    the penalty."""
    f = raw[raw["param"].isin(names)].copy()
    f["per_lot"] = f.price_pnl * 100 - 7.0            # swap is 0 pre-halt
    f["xt"] = pd.to_datetime(f.exit_time, unit="ns")
    f = f.sort_values("xt")
    g = f.groupby(f.xt.dt.date)["per_lot"]
    return pd.DataFrame({"close": g.sum().sort_index(),
                         "trough": g.apply(lambda v: min(0.0, v.cumsum().min())).sort_index(),
                         "trades": g.size().sort_index().astype(float)})


def _arrays(series: pd.DataFrame, k: float, extra_spread: float):
    """close/trough scaled to lots, with the execution penalty charged per trade.

    The penalty lands on the day the trades closed, using the `trades` column,
    rather than as a flat haircut. `trough` gets the whole day's cost applied
    up front, which is the conservative ordering: it assumes the cost was paid
    before the day reached its worst point.
    """
    cost = extra_spread * 100.0 * series["trades"].to_numpy(float)
    return ((series["close"].to_numpy(float) - cost) * k,
            (series["trough"].to_numpy(float) - cost) * k)


def ftmo_row(challenge: pd.DataFrame, funded: pd.DataFrame, lots_per_10k: float,
             max_days: int, split: float, extra_spread: float = 0.0) -> dict:
    """pass %, funded cash and breach for FTMO 2-Step - ftmo_sim's own engine.

    Two series, because the rule is two-sided. FTMO's own FAQ is explicit that
    you may trade freely through news during the Challenge and Verification;
    the restriction binds only on the funded Account. So the phases run on the
    UNFILTERED series and only the funded stage runs on the filtered one.
    Blacking out the whole history, as the first pass did, understated it.
    """
    k = lots_per_10k / 10_000.0
    c, t = _arrays(challenge, k, extra_spread)
    cf, tf = _arrays(funded, k, extra_spread)
    n = min(len(c), len(cf))

    ok, days = 0, []
    for st in range(n):
        p1, d1, e1 = fs.phase(c, t, st, *fs.PHASES["FTMO 2-Step P1"], max_days)
        if not p1:
            continue
        p2, d2, _ = fs.phase(c, t, min(e1 + 1, n - 1), *fs.PHASES["FTMO 2-Step P2"], max_days)
        if p2:
            ok += 1
            days.append(d1 + d2)

    f = [fs.funded_cash(cf, tf, st, 0.05, 0.10, "static", None, split, max_days, 0.01)
         for st in range(n)]
    cash = np.array([x[0] for x in f])
    br = np.array([x[2] for x in f])
    pass_pct = ok / n * 100
    cash_pct = cash.mean() * 100
    return {"sessions": n, "pass_pct": pass_pct,
            "median_days": float(np.median(days)) if days else np.nan,
            "funded_cash_pct": cash_pct,
            "funded_breach_pct": br.mean() * 100,
            "e_cash_pct": pass_pct / 100 * cash_pct}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-month", default="2024-01")
    ap.add_argument("--to-month", default="2026-07")
    ap.add_argument("--portfolios", default="TUNED,ORIGINAL")
    ap.add_argument("--lots", default="0.02,0.03")
    ap.add_argument("--extra-spread", type=float, default=0.0,
                    help="USD/oz of spread above the Exness Raw feed the ticks came "
                         "from, charged once per round trip")
    ap.add_argument("--pad", type=int, default=5, help="blackout padding, minutes each side")
    ap.add_argument("--flat-lead", type=int, default=60, help="pre-news flatten, seconds")
    ap.add_argument("--max-days", type=int, default=250)
    ap.add_argument("--split", type=float, default=0.80)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--events", default=None,
                    help="YYYY-MM-DD,slot file (slot = am|pm) driving the `real` scenario")
    ap.add_argument("--fomc-dates", default=None,
                    help="file of YYYY-MM-DD lines; replaces the Wednesday proxy")
    args = ap.parse_args(argv)

    fomc_days = None
    if args.fomc_dates:
        with open(args.fomc_dates) as fh:
            fomc_days = {dt.date.fromisoformat(l.strip()) for l in fh
                         if l.strip() and not l.startswith("#")}
        print(f"{len(fomc_days)} FOMC dates loaded - fomc_wed now means those days",
              file=sys.stderr)

    grid = [p for p in tw.window_grid([1.0, 2.0, 3.0])
            if p.name in sorted(set(fa.ORIGINAL + fa.TUNED))]
    files = discover_files(os.path.join(ROOT, "Monthly_Tick_Data"),
                           args.from_month, args.to_month)
    print(f"{len(files)} months, {len(SCENARIOS)} scenarios", file=sys.stderr)

    events = load_events(args.events) if args.events else None
    if events:
        print(f"{len(events)} release dates loaded for the `real` scenario", file=sys.stderr)
    else:
        print("no --events file: `real` will be empty and match `none`", file=sys.stderr)

    raw = collect(files, grid, args, fomc_days, events)

    ports = {"ORIGINAL": fa.ORIGINAL, "TUNED": fa.TUNED}
    series, rows = {}, []
    for pname in args.portfolios.split(","):
        names = ports[pname]
        for sc in SCENARIOS:
            if raw[sc].empty:
                continue
            s = daily_series(raw[sc], names)
            series[(pname, sc)] = s
            base = series.get((pname, "none"), s)
            for lp in [float(x) for x in args.lots.split(",")]:
                r = ftmo_row(base, s, lp, args.max_days, args.split, args.extra_spread)
                r.update({"portfolio": pname, "scenario": sc, "lots_per_10k": lp,
                          "trades": int(raw[sc]["param"].isin(names).sum())})
                rows.append(r)

    res = pd.DataFrame(rows)[["portfolio", "scenario", "lots_per_10k", "trades",
                              "sessions", "pass_pct", "median_days",
                              "funded_cash_pct", "funded_breach_pct", "e_cash_pct"]]
    os.makedirs(RESULTS, exist_ok=True)
    res.to_csv(os.path.join(RESULTS, "news_sim.csv"), index=False)

    wide = pd.concat(series, axis=1)
    wide.to_csv(os.path.join(RESULTS, "daily_per_lot_news.csv"))

    for (pname, lp), g in res.groupby(["portfolio", "lots_per_10k"]):
        base = g[g.scenario == "none"].iloc[0]
        g = g.copy()
        g["d_trades"] = g.trades - base.trades
        g["d_e_cash"] = g.e_cash_pct - base.e_cash_pct
        g["d_breach"] = g.funded_breach_pct - base.funded_breach_pct
        print(f"\n=== {pname} @ {lp} lots/$10k, FTMO 2-Step "
              f"({args.pad}m pad, {args.max_days}d window) ===")
        print(g[["scenario", "trades", "d_trades", "pass_pct", "funded_breach_pct",
                 "d_breach", "e_cash_pct", "d_e_cash"]].to_string(
            index=False, float_format=lambda v: f"{v:,.2f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
