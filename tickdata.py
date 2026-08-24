#!/usr/bin/env python3
"""
Tick data engine for XAUUSD research - strategy agnostic.

Streams Exness raw tick CSVs into `--subbar-seconds` sub-bars that keep bid and
ask extremes separately, groups them into trading sessions of N-minute bars,
and provides the shared money-management and metric helpers.

Nothing here knows about any particular strategy. Signal logic lives in the
strategy modules (see backtest_orb.py); this module only turns ticks into
sessions and trades into statistics.

  load_subbars(path, sub_sec, chunksize, cache_dir)  ticks  -> sub-bars
  split_sessions(sub, ...)                           sub-bars -> [Session]
  apply_money_management(df, params, equity)         trades -> sized P&L
  compute_metrics(df, equity)                        trades -> summary dict
  discover_files(data_dir, from_month, to_month)     month-filtered csv list

`params` is duck-typed: any object exposing lots, risk_pct, max_lots and
commission_per_lot works.
"""

from __future__ import annotations

import glob
import math
import os
import re
import sys
import time

import numpy as np
import pandas as pd

NS_PER_SEC = 1_000_000_000
NS_PER_DAY = 86_400 * NS_PER_SEC
CONTRACT_SIZE = 100.0          # ounces of gold per 1.00 lot
EPS = 1e-12
BIG = np.iinfo(np.int64).max


# --------------------------------------------------------------------------
# tick -> sub-bar compression
# --------------------------------------------------------------------------

def _group_starts(keys: np.ndarray) -> np.ndarray:
    """Offset of the first element of every run of equal values in a sorted array."""
    if keys.size == 0:
        return np.empty(0, dtype=np.int64)
    flags = np.empty(keys.size, dtype=bool)
    flags[0] = True
    np.not_equal(keys[1:], keys[:-1], out=flags[1:])
    return np.flatnonzero(flags).astype(np.int64)


def _aggregate(idx: np.ndarray, bid: np.ndarray, ask: np.ndarray) -> dict:
    """Compress sorted ticks into one row per distinct sub-bar index."""
    starts = _group_starts(idx)
    ends = np.append(starts[1:], idx.size)
    mid = (bid + ask) * 0.5
    return {
        "idx": idx[starts],
        "o": mid[starts],
        "h": np.maximum.reduceat(mid, starts),
        "l": np.minimum.reduceat(mid, starts),
        "c": mid[ends - 1],
        "bh": np.maximum.reduceat(bid, starts),
        "bl": np.minimum.reduceat(bid, starts),
        "ah": np.maximum.reduceat(ask, starts),
        "al": np.minimum.reduceat(ask, starts),
        "bo": bid[starts],
        "ao": ask[starts],
        "bc": bid[ends - 1],
        "ac": ask[ends - 1],
        "n": (ends - starts).astype(np.int64),
    }


def _merge_duplicates(bars: dict) -> dict:
    """Collapse rows that share a sub-bar index (can happen at chunk seams)."""
    starts = _group_starts(bars["idx"])
    if starts.size == bars["idx"].size:
        return bars
    ends = np.append(starts[1:], bars["idx"].size)
    return {
        "idx": bars["idx"][starts],
        "o": bars["o"][starts],
        "h": np.maximum.reduceat(bars["h"], starts),
        "l": np.minimum.reduceat(bars["l"], starts),
        "c": bars["c"][ends - 1],
        "bh": np.maximum.reduceat(bars["bh"], starts),
        "bl": np.minimum.reduceat(bars["bl"], starts),
        "ah": np.maximum.reduceat(bars["ah"], starts),
        "al": np.minimum.reduceat(bars["al"], starts),
        "bo": bars["bo"][starts],
        "ao": bars["ao"][starts],
        "bc": bars["bc"][ends - 1],
        "ac": bars["ac"][ends - 1],
        "n": np.add.reduceat(bars["n"], starts),
    }


def build_subbars(csv_path: str, sub_sec: int, chunksize: int, verbose: bool = True) -> dict:
    """Stream one monthly tick file into sub-bars."""
    step = sub_sec * NS_PER_SEC
    parts: list = []
    carry_ns = carry_bid = carry_ask = None
    n_ticks = 0
    t0 = time.time()

    reader = pd.read_csv(
        csv_path,
        usecols=["Timestamp", "Bid", "Ask"],
        dtype={"Bid": np.float64, "Ask": np.float64},
        chunksize=chunksize,
    )
    for chunk in reader:
        n_ticks += len(chunk)
        ts = pd.to_datetime(chunk["Timestamp"], format="ISO8601", utc=True)
        ns = ts.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]").astype(np.int64)
        bid = chunk["Bid"].to_numpy(dtype=np.float64)
        ask = chunk["Ask"].to_numpy(dtype=np.float64)

        if carry_ns is not None:
            ns = np.concatenate([carry_ns, ns])
            bid = np.concatenate([carry_bid, bid])
            ask = np.concatenate([carry_ask, ask])
            carry_ns = carry_bid = carry_ask = None

        if ns.size and not np.all(np.diff(ns) >= 0):       # defensive: rare unsorted rows
            order = np.argsort(ns, kind="stable")
            ns, bid, ask = ns[order], bid[order], ask[order]

        idx = ns // step
        if idx.size == 0:
            continue
        # hold back the last, still-open sub-bar so it can absorb the next chunk
        cut = int(np.searchsorted(idx, idx[-1], side="left"))
        if cut > 0:
            parts.append(_aggregate(idx[:cut], bid[:cut], ask[:cut]))
        carry_ns, carry_bid, carry_ask = ns[cut:], bid[cut:], ask[cut:]

    if carry_ns is not None and carry_ns.size:
        parts.append(_aggregate(carry_ns // step, carry_bid, carry_ask))

    if not parts:
        bars = {k: np.empty(0, dtype=np.float64) for k in
                ("o", "h", "l", "c", "bh", "bl", "ah", "al", "bo", "ao", "bc", "ac")}
        bars["idx"] = np.empty(0, dtype=np.int64)
        bars["n"] = np.empty(0, dtype=np.int64)
    else:
        bars = _merge_duplicates({k: np.concatenate([p[k] for p in parts]) for k in parts[0]})

    if verbose:
        print(f"    {n_ticks:,} ticks -> {bars['idx'].size:,} sub-bars "
              f"in {time.time() - t0:.1f}s", file=sys.stderr, flush=True)
    return bars


def load_subbars(csv_path: str, sub_sec: int, chunksize: int, cache_dir: str | None,
                 verbose: bool = True) -> dict:
    if not cache_dir:
        return build_subbars(csv_path, sub_sec, chunksize, verbose)
    os.makedirs(cache_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    cache = os.path.join(cache_dir, f"{stem}_s{sub_sec}.npz")
    if os.path.exists(cache):
        with np.load(cache) as z:
            bars = {k: z[k] for k in z.files}
        if verbose:
            print(f"    cache hit ({bars['idx'].size:,} sub-bars)", file=sys.stderr, flush=True)
        return bars
    bars = build_subbars(csv_path, sub_sec, chunksize, verbose)
    np.savez(cache, **bars)
    return bars


# --------------------------------------------------------------------------
# sub-bars -> sessions of 5-minute bars


# --------------------------------------------------------------------------
# sub-bars -> sessions of N-minute bars
# --------------------------------------------------------------------------

class Session:
    """One trading session: N-minute bars plus the sub-bars behind them."""

    __slots__ = ("day_ns", "t", "o", "h", "l", "c", "n", "s0", "s1",
                 "bar_bh", "bar_bl", "bar_ah", "bar_al", "sub", "sub_sec")

    def __init__(self, day_ns: int, sub: dict, bar_key: np.ndarray, sub_sec: int):
        self.day_ns = int(day_ns)
        self.sub = sub
        self.sub_sec = sub_sec
        starts = _group_starts(bar_key)
        ends = np.append(starts[1:], bar_key.size)
        self.s0, self.s1 = starts, ends
        self.t = sub["idx"][starts] * sub_sec * NS_PER_SEC
        self.o = sub["o"][starts]
        self.h = np.maximum.reduceat(sub["h"], starts)
        self.l = np.minimum.reduceat(sub["l"], starts)
        self.c = sub["c"][ends - 1]
        self.n = np.add.reduceat(sub["n"], starts).astype(np.float64)
        self.bar_bh = np.maximum.reduceat(sub["bh"], starts)
        self.bar_bl = np.minimum.reduceat(sub["bl"], starts)
        self.bar_ah = np.maximum.reduceat(sub["ah"], starts)
        self.bar_al = np.minimum.reduceat(sub["al"], starts)

    def __len__(self) -> int:
        return int(self.t.size)

def _slice_subbars(sub: dict, a: int, b: int) -> dict:
    return {k: v[a:b] for k, v in sub.items()}


def _make_session(sub: dict, a: int, b: int, day_ns: int, bar_key: np.ndarray,
                  sub_sec: int) -> Session:
    return Session(day_ns, _slice_subbars(sub, a, b), bar_key[a:b], sub_sec)


def split_sessions(sub: dict, sub_sec: int, bar_minutes: int, session_start_hour: int,
                   min_session_bars: int, carry: dict | None, final: bool = False):
    """
    Split sub-bars into sessions.  The last (possibly still open) session is
    withheld and returned so it can be merged with the next file, unless
    `final` is set.
    """
    if carry is not None and carry["idx"].size:
        sub = {k: np.concatenate([carry[k], sub[k]]) for k in sub}
    if sub["idx"].size == 0:
        return [], sub

    sub_ns = sub["idx"] * sub_sec * NS_PER_SEC
    off = session_start_hour * 3600 * NS_PER_SEC
    day = (sub_ns - off) // NS_PER_DAY
    bar_key = (sub_ns - off) // (bar_minutes * 60 * NS_PER_SEC)

    starts = _group_starts(day)
    ends = np.append(starts[1:], day.size)
    if not final:
        starts, ends = starts[:-1], ends[:-1]

    sessions = []
    for a, b in zip(starts, ends):
        s = _make_session(sub, int(a), int(b), int(day[a]) * NS_PER_DAY + off, bar_key, sub_sec)
        if len(s) >= min_session_bars:
            sessions.append(s)

    leftover = ({k: np.empty(0, dtype=v.dtype) for k, v in sub.items()} if final
                else _slice_subbars(sub, int(ends[-1]) if ends.size else 0, sub["idx"].size))
    return sessions, leftover


# --------------------------------------------------------------------------
# strategy


# --------------------------------------------------------------------------
# money management + metrics
# --------------------------------------------------------------------------

def apply_money_management(df: pd.DataFrame, p: Params, start_equity: float) -> pd.DataFrame:
    df = df.sort_values(["exit_time", "entry_time"], kind="stable").reset_index(drop=True)
    price_pnl = df["price_pnl"].to_numpy()
    risk_price = df["risk_price"].to_numpy()

    if p.risk_pct > 0:                       # position size follows the compounding equity
        equity = start_equity
        lots_list, net_list = [], []
        for pp, rp in zip(price_pnl, risk_price):
            lots = (equity * p.risk_pct / 100.0) / (max(rp, EPS) * CONTRACT_SIZE)
            lots = float(min(max(lots, 0.01), p.max_lots))
            pnl = pp * CONTRACT_SIZE * lots - p.commission_per_lot * lots
            equity += pnl
            lots_list.append(lots)
            net_list.append(pnl)
        lots_arr = np.array(lots_list)
        net = np.array(net_list)
    else:
        lots_arr = np.full(len(df), p.lots)
        net = price_pnl * CONTRACT_SIZE * lots_arr - p.commission_per_lot * lots_arr

    df["lots"] = lots_arr
    df["commission"] = p.commission_per_lot * lots_arr
    df["pnl"] = net
    df["equity"] = start_equity + np.cumsum(net)
    df["r_multiple"] = np.where(risk_price > EPS, price_pnl / np.maximum(risk_price, EPS), np.nan)
    df["entry_dt"] = pd.to_datetime(df["entry_time"], unit="ns")
    df["exit_dt"] = pd.to_datetime(df["exit_time"], unit="ns")
    df["session_date"] = pd.to_datetime(df["session"], unit="ns").dt.date
    return df


def compute_metrics(df: pd.DataFrame, start_equity: float) -> dict:
    if df.empty:
        return {"trades": 0}
    pnl = df["pnl"].to_numpy()
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    equity = np.concatenate([[start_equity], df["equity"].to_numpy()])
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    dd_pct = dd / np.maximum(peak, EPS) * 100.0

    daily = df.groupby("session_date")["pnl"].sum()
    daily_ret = daily.to_numpy() / start_equity
    sharpe = (daily_ret.mean() / daily_ret.std(ddof=1) * math.sqrt(252)
              if len(daily_ret) > 2 and daily_ret.std(ddof=1) > 0 else float("nan"))
    span_days = max((df["exit_dt"].max() - df["entry_dt"].min()).days, 1)
    end_equity = start_equity + pnl.sum()
    cagr = (((end_equity / start_equity) ** (365.25 / span_days) - 1) * 100
            if end_equity > 0 else float("nan"))

    return {
        "trades": int(len(df)),
        "win_rate_pct": float(len(wins) / len(df) * 100),
        "net_pnl": float(pnl.sum()),
        "gross_profit": float(wins.sum()),
        "gross_loss": float(losses.sum()),
        "profit_factor": (float(wins.sum() / abs(losses.sum())) if losses.size else float("inf")),
        "expectancy": float(pnl.mean()),
        "expectancy_R": float(df["r_multiple"].mean(skipna=True)),
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "largest_win": float(pnl.max()),
        "largest_loss": float(pnl.min()),
        "max_drawdown": float(dd.min()),
        "max_drawdown_pct": float(dd_pct.min()),
        "start_equity": float(start_equity),
        "end_equity": float(end_equity),
        "return_pct": float((end_equity / start_equity - 1) * 100),
        "cagr_pct": float(cagr),
        "sharpe_daily": float(sharpe),
        "trading_days": int(len(daily)),
        "trades_per_day": float(len(df) / max(len(daily), 1)),
        "avg_bars_held": float(df["bars_held"].mean()),
        "commission_paid": float(df["commission"].sum()),
        "tp_rate_pct": float((df["exit_reason"] == "tp").mean() * 100),
        "sl_rate_pct": float((df["exit_reason"] == "sl").mean() * 100),
    }


# --------------------------------------------------------------------------
# file discovery
# --------------------------------------------------------------------------

MONTH_RE = re.compile(r"(\d{4})[_-](\d{2})")


def discover_files(data_dir: str, from_month: str | None, to_month: str | None) -> list:
    keyed = []
    for f in glob.glob(os.path.join(data_dir, "**", "*.csv"), recursive=True):
        m = MONTH_RE.search(os.path.basename(f))
        key = f"{m.group(1)}-{m.group(2)}" if m else os.path.basename(f)
        if from_month and key < from_month:
            continue
        if to_month and key > to_month:
            continue
        keyed.append((key, f))
    keyed.sort()
    return [f for _, f in keyed]
