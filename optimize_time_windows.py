#!/usr/bin/env python3
"""
Time-window optimisation for the ORB strategy.

Sweeps the session anchor across all 24 UTC hours x 3 opening-range lengths x 3
target multiples (216 windows), then breaks the result down by day of week.

Every window is scored on TRAIN and HOLDOUT separately.  A ranking table built
on one period only is a curve fit; the columns that matter are `net_ho` and
`both` (positive in both periods), plus the train->holdout rank correlation at
the bottom, which says whether picking a window on history predicts anything
at all.

Weekday cells hold roughly 130 sessions each, so they are the thinnest and the
most overfit-prone numbers in the whole study - treat them as indicative only.

    python optimize_time_windows.py --cache-dir <dir>
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

import backtest_orb as ob
from backtest_orb import OrbParams

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def window_grid(targets) -> list:
    return [OrbParams(name=f"h{h:02d}_r{r}_t{t:g}", anchor_hour=h, range_minutes=r,
                      target_mult=t)
            for h, r, t in itertools.product(range(24), [15, 30, 60], targets)]


def pnl_frame(raw: pd.DataFrame, lots: float, comm: float) -> pd.DataFrame:
    raw = raw.copy()
    raw["pnl"] = raw["price_pnl"] * ob.CONTRACT_SIZE * lots - comm * lots
    ts = pd.to_datetime(raw["session"], unit="ns")
    raw["weekday"] = ts.dt.day_name()
    raw["date"] = ts.dt.date
    parts = raw["param"].str.extract(r"h(\d+)_r(\d+)_t([\d.]+)")
    raw["hour"] = parts[0].astype(int)
    raw["rng"] = parts[1].astype(int)
    raw["tgt"] = parts[2].astype(float)
    return raw


def per_window(raw: pd.DataFrame) -> pd.DataFrame:
    g = raw.groupby("param")["pnl"]
    out = pd.DataFrame({"trades": g.size(), "net": g.sum(), "exp": g.mean(),
                        "win_rate": g.apply(lambda x: (x > 0).mean() * 100),
                        "pf": g.apply(lambda x: x[x > 0].sum() / abs(x[x < 0].sum())
                                      if (x < 0).any() else np.inf)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ORB time-window optimisation")
    ap.add_argument("--data-dir", default="Monthly_Tick_Data")
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--subbar-seconds", type=int, default=5)
    ap.add_argument("--bar-minutes", type=int, default=5)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--min-session-bars", type=int, default=100)
    ap.add_argument("--lots", type=float, default=0.10)
    ap.add_argument("--commission-per-lot", type=float, default=7.0)
    ap.add_argument("--targets", default="1,2,3")
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--outdir", default="orb_results")
    args = ap.parse_args(argv)

    targets = [float(x) for x in args.targets.split(",")]
    grid = window_grid(targets)
    a = SimpleNamespace(subbar_seconds=args.subbar_seconds, bar_minutes=args.bar_minutes,
                        chunksize=args.chunksize, min_session_bars=args.min_session_bars,
                        cache_dir=args.cache_dir)
    os.makedirs(args.outdir, exist_ok=True)

    print(f"sweeping {len(grid)} windows (24 hours x 3 range lengths x "
          f"{len(targets)} targets)", file=sys.stderr)
    tr = pnl_frame(ob.collect(ob.discover_files(args.data_dir, *ob.TRAIN), grid, a, "train"),
                   args.lots, args.commission_per_lot)
    ho = pnl_frame(ob.collect(ob.discover_files(args.data_dir, *ob.HOLDOUT), grid, a, "holdout"),
                   args.lots, args.commission_per_lot)

    T, H = per_window(tr), per_window(ho)
    m = T.join(H, lsuffix="_tr", rsuffix="_ho").reset_index()
    m[["hour", "rng", "tgt"]] = m["param"].str.extract(r"h(\d+)_r(\d+)_t([\d.]+)")
    m["hour"] = m["hour"].astype(int)
    m["rng"] = m["rng"].astype(int)
    m["both"] = (m["net_tr"] > 0) & (m["net_ho"] > 0)
    m = m[m["trades_tr"] >= args.min_trades]
    m.to_csv(os.path.join(args.outdir, "time_window_scan.csv"), index=False)

    fmt = lambda v: f"{v:,.2f}"
    rep = ["ORB time-window optimisation",
           f"windows scanned: {len(m)} with >= {args.min_trades} train trades",
           f"TRAIN {ob.TRAIN[0]}..{ob.TRAIN[1]}   HOLDOUT {ob.HOLDOUT[0]}..{ob.HOLDOUT[1]}",
           f"sizing {args.lots} lots, commission {args.commission_per_lot}/lot round turn", ""]

    cols = ["param", "trades_tr", "pf_tr", "net_tr", "exp_tr",
            "trades_ho", "pf_ho", "net_ho", "exp_ho", "both"]
    rep.append("=== top 20 windows by TRAIN net P&L (and what they did next) ===")
    rep.append(m.nlargest(20, "net_tr")[cols].to_string(index=False, float_format=fmt))
    rep.append("")
    rep.append("=== top 20 windows by HOLDOUT net P&L ===")
    rep.append(m.nlargest(20, "net_ho")[cols].to_string(index=False, float_format=fmt))
    rep.append("")
    rep.append("=== windows profitable in BOTH periods ===")
    both = m[m["both"]].sort_values("net_tr", ascending=False)
    rep.append(f"{len(both)} / {len(m)} windows"
               f"  ({len(both) / max(len(m), 1) * 100:.1f}%; ~25% expected by chance)")
    rep.append(both[cols].head(25).to_string(index=False, float_format=fmt))
    rep.append("")

    sp = m["net_tr"].corr(m["net_ho"], method="spearman")
    pe = m["net_tr"].corr(m["net_ho"])
    rep.append("=== does picking a window on TRAIN predict HOLDOUT? ===")
    rep.append(f"Spearman {sp:+.3f}   Pearson {pe:+.3f}")
    top = m.nlargest(20, "net_tr")
    bot = m.nsmallest(20, "net_tr")
    rep.append(f"top-20 train -> holdout: {(top.net_ho > 0).sum()}/20 positive, "
               f"median net {top.net_ho.median():,.0f}")
    rep.append(f"bottom-20 train -> holdout: {(bot.net_ho > 0).sum()}/20 positive, "
               f"median net {bot.net_ho.median():,.0f}")
    rep.append("")

    # ---- hour profile, pooled over range lengths and targets -----------------
    hp = m.groupby("hour").agg(windows=("param", "size"), net_tr=("net_tr", "sum"),
                               net_ho=("net_ho", "sum"), exp_tr=("exp_tr", "mean"),
                               exp_ho=("exp_ho", "mean"))
    hp["both_sign"] = np.sign(hp["net_tr"]) == np.sign(hp["net_ho"])
    rep.append("=== anchor hour profile (pooled across range lengths & targets) ===")
    rep.append(hp.to_string(float_format=fmt))
    rep.append("")

    # ---- day of week ---------------------------------------------------------
    def wd(raw, label):
        d = raw[raw["param"].str.startswith("h13_r60")]
        g = d.groupby("weekday")["pnl"]
        t = pd.DataFrame({f"trades_{label}": g.size(), f"net_{label}": g.sum(),
                          f"exp_{label}": g.mean(),
                          f"win_{label}": g.apply(lambda x: (x > 0).mean() * 100)})
        return t.reindex(WEEKDAYS)

    rep.append("=== day of week, for the pre-registered 13:00 UTC / 60-min window ===")
    rep.append(wd(tr, "tr").join(wd(ho, "ho")).to_string(float_format=fmt))
    rep.append("")

    allw = tr.groupby("weekday")["pnl"].agg(["size", "sum", "mean"]).reindex(WEEKDAYS)
    allh = ho.groupby("weekday")["pnl"].agg(["size", "sum", "mean"]).reindex(WEEKDAYS)
    rep.append("=== day of week, pooled over ALL windows ===")
    rep.append(allw.join(allh, lsuffix="_tr", rsuffix="_ho").to_string(float_format=fmt))
    rep.append("")

    # ---- hour x weekday, the thinnest cut ------------------------------------
    hw_tr = tr.pivot_table(index="weekday", columns="hour", values="pnl", aggfunc="mean")
    hw_ho = ho.pivot_table(index="weekday", columns="hour", values="pnl", aggfunc="mean")
    agree = (np.sign(hw_tr) == np.sign(hw_ho))
    rep.append("=== hour x weekday: mean P&L per trade, TRAIN ===")
    rep.append(hw_tr.reindex(WEEKDAYS).to_string(float_format=fmt))
    rep.append("")
    rep.append(f"sign agreement between TRAIN and HOLDOUT across the "
               f"{agree.size} hour x weekday cells: {agree.to_numpy().mean() * 100:.1f}% "
               f"(50% = coin flip)")
    rep.append("")

    text = "\n".join(rep)
    with open(os.path.join(args.outdir, "time_window_report.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)

    # ---- heatmap -------------------------------------------------------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
        for ax, (frame, lbl) in zip(axes, [(hw_tr, "TRAIN"), (hw_ho, "HOLDOUT")]):
            f = frame.reindex(WEEKDAYS)
            v = np.nanmax(np.abs(f.to_numpy())) or 1
            im = ax.imshow(f.to_numpy(), cmap="RdYlGn", vmin=-v, vmax=v, aspect="auto")
            ax.set_yticks(range(len(WEEKDAYS)), WEEKDAYS)
            ax.set_xticks(range(len(f.columns)), f.columns)
            ax.set_title(f"ORB mean P&L per trade by anchor hour (UTC) x weekday - {lbl}")
            fig.colorbar(im, ax=ax, label="USD/trade")
        axes[-1].set_xlabel("anchor hour (UTC)")
        fig.tight_layout()
        fig.savefig(os.path.join(args.outdir, "time_window_heatmap.png"), dpi=130)
        plt.close(fig)
        print(f"\nheatmap -> {os.path.join(args.outdir, 'time_window_heatmap.png')}",
              file=sys.stderr)
    except Exception as exc:
        print(f"(plot skipped: {exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
