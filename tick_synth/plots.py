#!/usr/bin/env python3
"""
Charts for synthetic tick runs.

Draws the synthetic tape, and -- when a real directory is given -- draws the
real one on top of it, so the question "does this look like gold?" gets a
picture instead of only a ratio table.  Any number of runs can be passed at
once, which is the point of the fan chart: ten replicates of the same calendar
show the spread of histories the strategy could have faced.

Figures written to --outdir:

  price_paths.png   daily close of every run, real in black
  hourly.png        ticks and spread by hour of day -- the session shape
  returns.png       5-minute return density (log y) and a QQ plot vs real
  acf.png           autocorrelation of returns and |returns| out to 30 bars
  daily.png         daily range and daily |move| distributions
  intraday.png      one whole day at tick resolution: mid, spread, tick rate

    python tick_synth/plots.py --synth tick_synth/output/rep00 \
        --real Monthly_Tick_Data --real-from 2025-01 --real-to 2025-12 \
        --outdir tick_synth/charts/rep00
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from common import NS_PER_DAY, discover_months, iter_ticks, log   # noqa: E402
from validate import BAR_SEC, _acf, scan_accum                    # noqa: E402

REAL_STYLE = {"color": "black", "lw": 1.4, "zorder": 5}
FIG = (12, 6)


def _daily_series(acc):
    """(dates, closes) for one run, in time order."""
    if not acc.day_no:
        return np.empty(0, dtype="datetime64[D]"), np.empty(0)
    day = np.asarray(acc.day_no, dtype=np.int64)
    close = np.asarray(acc.day_close, dtype=np.float64)
    order = np.argsort(day, kind="stable")
    return day[order].astype("datetime64[D]"), close[order]


def _rets(acc) -> np.ndarray:
    r = np.concatenate(acc.rets) if acc.rets else np.zeros(0)
    return r[np.isfinite(r)]


# --------------------------------------------------------------------------
def plot_price_paths(runs: list, real, outdir: str) -> None:
    fig, ax = plt.subplots(2, 1, figsize=(13, 8), sharex=False,
                           gridspec_kw={"height_ratios": [3, 2]})
    for label, acc in runs:
        d, c = _daily_series(acc)
        ax[0].plot(d, c, lw=1.0, alpha=0.85, label=label)
        if c.size:
            ax[1].plot(np.arange(c.size), c / c[0] * 100.0, lw=1.0, alpha=0.85)
    if real is not None:
        d, c = _daily_series(real)
        ax[0].plot(d, c, label="real", **REAL_STYLE)
        if c.size:
            ax[1].plot(np.arange(c.size), c / c[0] * 100.0, label="real",
                       **REAL_STYLE)

    ax[0].set_title("daily close -- synthetic runs vs real")
    ax[0].set_ylabel("price")
    ax[0].legend(fontsize=8, ncol=3)
    ax[0].grid(alpha=0.3)
    ax[1].set_title("same paths rebased to 100 at the first day "
                    "(the fan of histories)")
    ax[1].set_xlabel("trading day")
    ax[1].set_ylabel("index")
    ax[1].grid(alpha=0.3)
    _save(fig, outdir, "price_paths.png")


def plot_hourly(runs: list, real, outdir: str) -> None:
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    hours = np.arange(24)
    for label, acc in runs:
        s = acc.summary()
        ax[0].plot(hours, s["_hour_ticks"], marker="o", ms=3, lw=1.2, label=label)
        ax[1].plot(hours, s["_hour_spread"] * 100, marker="o", ms=3, lw=1.2,
                   label=label)
    if real is not None:
        s = real.summary()
        ax[0].plot(hours, s["_hour_ticks"], label="real", marker="o", ms=3,
                   **REAL_STYLE)
        ax[1].plot(hours, s["_hour_spread"] * 100, label="real", marker="o",
                   ms=3, **REAL_STYLE)

    for a, title, ylab in ((ax[0], "mean ticks per hour of day", "ticks"),
                           (ax[1], "mean spread by hour of day", "points")):
        a.set_title(title)
        a.set_xlabel("hour (UTC)")
        a.set_ylabel(ylab)
        a.set_xticks(range(0, 24, 2))
        a.grid(alpha=0.3)
        a.legend(fontsize=8)
    for a in ax:                       # the two session opens the strategies use
        for h in (7, 13):
            a.axvline(h, color="crimson", ls=":", lw=1, alpha=0.6)
    _save(fig, outdir, "hourly.png")


def plot_returns(runs: list, real, outdir: str) -> None:
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    rr = _rets(real) if real is not None else np.zeros(0)
    ref = rr if rr.size else _rets(runs[0][1])
    lim = float(np.quantile(np.abs(ref), 0.9995)) * 1e4 if ref.size else 50.0
    bins = np.linspace(-lim, lim, 201)

    for label, acc in runs:
        r = _rets(acc) * 1e4
        if r.size:
            ax[0].hist(r, bins=bins, density=True, histtype="step", lw=1.2,
                       label=label)
    if rr.size:
        ax[0].hist(rr * 1e4, bins=bins, density=True, histtype="step",
                   label="real", color="black", lw=1.4)
    ax[0].set_yscale("log")
    ax[0].set_title("5-minute log return density (log y -- the tails)")
    ax[0].set_xlabel("return (bp)")
    ax[0].set_ylabel("density")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)

    if rr.size:
        q = np.linspace(0.001, 0.999, 999)
        rq = np.quantile(rr, q) * 1e4
        for label, acc in runs:
            r = _rets(acc)
            if r.size:
                ax[1].plot(rq, np.quantile(r, q) * 1e4, lw=1.2, label=label)
        lo, hi = rq[0], rq[-1]
        ax[1].plot([lo, hi], [lo, hi], color="black", ls="--", lw=1,
                   label="y = x")
        ax[1].set_title("QQ: synthetic vs real 5-minute returns")
        ax[1].set_xlabel("real quantile (bp)")
        ax[1].set_ylabel("synthetic quantile (bp)")
        ax[1].legend(fontsize=8)
        ax[1].grid(alpha=0.3)
    else:
        ax[1].axis("off")
        ax[1].text(0.5, 0.5, "pass --real for a QQ plot", ha="center")
    _save(fig, outdir, "returns.png")


def plot_acf(runs: list, real, outdir: str, max_lag: int = 30) -> None:
    lags = np.arange(1, max_lag + 1)
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    series = list(runs) + ([("real", real)] if real is not None else [])
    for label, acc in series:
        r = _rets(acc)
        if r.size < max_lag + 3:
            continue
        style = REAL_STYLE if label == "real" else {"lw": 1.2}
        ax[0].plot(lags, [_acf(r, k) for k in lags], marker="o", ms=3,
                   label=label, **style)
        ax[1].plot(lags, [_acf(np.abs(r), k) for k in lags], marker="o", ms=3,
                   label=label, **style)

    ax[0].set_title("acf of 5-minute returns  (should sit near zero)")
    ax[1].set_title("acf of |5-minute returns|  (volatility clustering)")
    for a in ax:
        a.axhline(0, color="grey", lw=0.8)
        a.set_xlabel(f"lag (x {BAR_SEC // 60} min)")
        a.set_ylabel("autocorrelation")
        a.legend(fontsize=8)
        a.grid(alpha=0.3)
    _save(fig, outdir, "acf.png")


def plot_daily(runs: list, real, outdir: str) -> None:
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    series = list(runs) + ([("real", real)] if real is not None else [])
    for i, (field, title) in enumerate((("day_range", "daily range %"),
                                        ("day_move", "daily |move| %"))):
        vals = []
        for label, acc in series:
            v = np.abs(np.asarray(getattr(acc, field), dtype=np.float64))
            vals.append((label, v[np.isfinite(v)]))
        hi = max((np.quantile(v, 0.99) for _, v in vals if v.size), default=1.0)
        bins = np.linspace(0, hi, 60)
        for label, v in vals:
            if not v.size:
                continue
            style = ({"color": "black", "lw": 1.4} if label == "real"
                     else {"lw": 1.2})
            ax[i].hist(v, bins=bins, density=True, histtype="step",
                       label=f"{label} (mean {v.mean():.2f})", **style)
        ax[i].set_title(title)
        ax[i].set_xlabel("%")
        ax[i].set_ylabel("density")
        ax[i].legend(fontsize=8)
        ax[i].grid(alpha=0.3)
    _save(fig, outdir, "daily.png")


# --------------------------------------------------------------------------
def load_one_day(data_dir: str, date: str, chunksize: int):
    """Every tick of one calendar day, straight from the csv."""
    day_no = int(np.datetime64(date, "D").astype(np.int64))
    lo, hi = day_no * NS_PER_DAY, (day_no + 1) * NS_PER_DAY
    months = discover_months(data_dir, date[:7], date[:7])
    if not months:
        return None
    ns_p, bid_p, ask_p = [], [], []
    for ns, bid, ask in iter_ticks(months[0][1], chunksize):
        m = (ns >= lo) & (ns < hi)
        if m.any():
            ns_p.append(ns[m])
            bid_p.append(bid[m])
            ask_p.append(ask[m])
        if ns.size and ns[0] >= hi:
            break
    if not ns_p:
        return None
    return (np.concatenate(ns_p), np.concatenate(bid_p), np.concatenate(ask_p))


def plot_intraday(data_dir: str, label: str, date: str, outdir: str,
                  chunksize: int) -> None:
    got = load_one_day(data_dir, date, chunksize)
    if got is None:
        log(f"  no ticks for {date} in {data_dir}, skipping intraday chart")
        return
    ns, bid, ask = got
    hours = (ns % NS_PER_DAY) / 3_600e9
    mid = (bid + ask) / 2.0

    step = max(1, ns.size // 80_000)               # keep the figure drawable
    fig, ax = plt.subplots(3, 1, figsize=(13, 9), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1, 1]})
    ax[0].plot(hours[::step], mid[::step], lw=0.7)
    ax[0].set_ylabel("mid")
    ax[0].set_title(f"{label} -- {date} ({ns.size:,} ticks)")

    ax[1].plot(hours[::step], (ask - bid)[::step] * 100, lw=0.5, color="darkorange")
    ax[1].set_ylabel("spread (pts)")

    minute = ((ns % NS_PER_DAY) // 60_000_000_000).astype(np.int64)
    rate = np.bincount(minute, minlength=1440)
    ax[2].bar(np.arange(1440) / 60.0, rate, width=1 / 60.0, color="steelblue")
    ax[2].set_ylabel("ticks/min")
    ax[2].set_xlabel("hour (UTC)")

    for a in ax:
        a.grid(alpha=0.3)
        for h in (7, 13):
            a.axvline(h, color="crimson", ls=":", lw=1, alpha=0.6)
    ax[0].set_xlim(0, 24)
    ax[0].set_xticks(range(0, 25, 2))
    _save(fig, outdir, "intraday.png")


def _save(fig, outdir: str, name: str) -> None:
    fig.tight_layout()
    path = os.path.join(outdir, name)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log(f"  wrote {path}")


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synth", nargs="+", required=True,
                    help="one or more synthetic run directories")
    ap.add_argument("--real", default=None,
                    help="real data directory to draw on top (optional)")
    ap.add_argument("--real-from", default=None)
    ap.add_argument("--real-to", default=None)
    ap.add_argument("--synth-from", default=None)
    ap.add_argument("--synth-to", default=None)
    ap.add_argument("--outdir", default="tick_synth/charts")
    ap.add_argument("--cache-dir", default=None,
                    help="sub-bar cache; give each run its OWN directory")
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--intraday-day", default=None,
                    help="YYYY-MM-DD to draw tick-by-tick; default is the "
                         "middle day of the first run")
    ap.add_argument("--no-intraday", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)
    runs = []
    for d in args.synth:
        label = os.path.basename(os.path.normpath(d))
        cache = os.path.join(args.cache_dir, label) if args.cache_dir else None
        runs.append((label, scan_accum(d, args.synth_from, args.synth_to,
                                       cache, args.chunksize, label)))
    real = None
    if args.real:
        cache = os.path.join(args.cache_dir, "real") if args.cache_dir else None
        real = scan_accum(args.real, args.real_from, args.real_to, cache,
                          args.chunksize, "real")

    plot_price_paths(runs, real, args.outdir)
    plot_hourly(runs, real, args.outdir)
    plot_returns(runs, real, args.outdir)
    plot_acf(runs, real, args.outdir)
    plot_daily(runs, real, args.outdir)

    if not args.no_intraday:
        date = args.intraday_day
        if date is None:
            days, _ = _daily_series(runs[0][1])
            date = str(days[days.size // 2]) if days.size else None
        if date:
            plot_intraday(args.synth[0], runs[0][0], date, args.outdir,
                          args.chunksize)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
