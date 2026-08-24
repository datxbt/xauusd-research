#!/usr/bin/env python3
"""
VWAP Pullback (5-Minute) backtester for Exness XAUUSD raw tick data.

Strategy
--------
The session VWAP resets every trading day.  When a 5-minute close stretches
THRESHOLD % away from the session VWAP we fade the move:

    Long  : close <= VWAP * (1 - threshold)
    Short : close >= VWAP * (1 + threshold)

The fill happens on the open of the *next* 5-minute bar (a long pays the ask,
a short pays the bid).  The target is the session VWAP itself - which keeps
moving while the trade is open - and the stop sits `stop_mult` x threshold
beyond the entry price.  Anything still open at the session close is flattened.

Data handling
-------------
Every tick file is streamed once and compressed into `--subbar-seconds`
sub-bars that keep bid and ask extremes separately.  Signals are computed on
the 5-minute bars aggregated from those sub-bars, while stop/target hits are
resolved sub-bar by sub-bar, so intrabar path order is respected far better
than in a plain OHLC backtest.  When a single sub-bar touches both the stop and
the target, the stop is assumed to come first (conservative).

Usage
-----
    python backtest_vwap_pullback.py --from-month 2025-01 --to-month 2025-12
    python backtest_vwap_pullback.py --sweep --cache-dir bar_cache
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import re
import sys
import time
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

NS_PER_SEC = 1_000_000_000
NS_PER_DAY = 86_400 * NS_PER_SEC
CONTRACT_SIZE = 100.0          # ounces of gold per 1.00 lot
EPS = 1e-12
BIG = np.iinfo(np.int64).max


# --------------------------------------------------------------------------
# parameters
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Params:
    name: str = "base"
    # signal
    threshold_pct: float = 0.75      # % deviation from VWAP that triggers a fade
    stop_mult: float = 2.0           # stop distance = stop_mult * threshold
    stop_pct: float = 0.0            # explicit stop % (overrides stop_mult when > 0)
    tp_offset_pct: float = 0.0       # take profit this % short of VWAP
    confirm: str = "none"            # none | bar | engulfing
    vwap_price: str = "typical"      # typical | close | ohlc4
    vwap_weight: str = "equal"       # equal | ticks
    # session / timing filters
    skip_open_bars: int = 6          # ignore the first N bars (VWAP is noisy there)
    no_entry_last_bars: int = 12     # no new entries in the last N bars
    max_hold_bars: int = 0           # 0 = hold until VWAP / stop / session end
    cooldown_bars: int = 0           # wait N bars after a trade closes
    max_trades_per_session: int = 0  # 0 = unlimited
    max_vwap_slope_pct: float = 0.0  # 0 = off; skips strongly trending moments
    slope_lookback: int = 12
    # causal regime filter: trailing efficiency ratio of the last N bars must be
    # AT MOST this (0 = chop, 1 = straight line).  Uses only elapsed bars.
    max_efficiency: float = 0.0      # 0 = off
    min_efficiency: float = 0.0      # 0 = off; require a TRENDING tape instead
    efficiency_lookback: int = 12
    mode: str = "fade"               # fade = revert to VWAP | follow = continuation
    exclude_hours: tuple = ()        # UTC hours in which no new trade is opened
    # exit geometry
    tp_mode: str = "vwap_dynamic"    # vwap_dynamic | vwap_static | fixed_r
    tp_r: float = 1.0                # reward/risk multiple when tp_mode = fixed_r
    # execution
    lots: float = 0.10
    risk_pct: float = 0.0            # > 0 -> size from equity risk instead of fixed lots
    max_lots: float = 100.0
    commission_per_lot: float = 7.0  # round turn, USD per 1.00 lot
    slippage: float = 0.0            # USD per side, on top of the real spread

    def stop_fraction(self) -> float:
        if self.stop_pct > 0:
            return self.stop_pct / 100.0
        return self.stop_mult * self.threshold_pct / 100.0


TRADE_COLUMNS = [
    "param", "session", "side", "entry_time", "exit_time", "entry_bar", "exit_bar",
    "bars_held", "entry_price", "exit_price", "stop_price", "target_at_entry",
    "vwap_at_signal", "dev_pct", "exit_reason", "price_pnl", "risk_price",
]


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
class Session:
    """One trading session: 5-minute bars plus the sub-bars behind them."""

    __slots__ = ("day_ns", "t", "o", "h", "l", "c", "n", "s0", "s1",
                 "bar_bh", "bar_bl", "bar_ah", "bar_al", "sub", "sub_sec", "_vwap")

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
        self._vwap: dict = {}

    def __len__(self) -> int:
        return int(self.t.size)

    def get_vwap(self, price_mode: str, weight_mode: str) -> np.ndarray:
        key = (price_mode, weight_mode)
        if key not in self._vwap:
            if price_mode == "close":
                px = self.c
            elif price_mode == "ohlc4":
                px = (self.o + self.h + self.l + self.c) / 4.0
            else:
                px = (self.h + self.l + self.c) / 3.0
            w = self.n if weight_mode == "ticks" else np.ones_like(px)
            self._vwap[key] = np.cumsum(px * w) / np.maximum(np.cumsum(w), EPS)
        return self._vwap[key]


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
def _confirms(p: Params, side: int, k: int, o: np.ndarray, c: np.ndarray) -> bool:
    """Optional reversal confirmation on the signal bar."""
    if p.confirm == "none":
        return True
    if k < 1:
        return False
    if p.confirm == "bar":
        return bool(c[k] > o[k]) if side > 0 else bool(c[k] < o[k])
    if p.confirm == "engulfing":
        if side > 0:
            return bool(c[k] > o[k] and c[k - 1] < o[k - 1]
                        and c[k] >= o[k - 1] and o[k] <= c[k - 1])
        return bool(c[k] < o[k] and c[k - 1] > o[k - 1]
                    and c[k] <= o[k - 1] and o[k] >= c[k - 1])
    return True


def _resolve_exit(s: Session, p: Params, side: int, j: int, stop: float,
                  tp_arr: np.ndarray, last_bar: int):
    """
    Walk forward from the entry bar until the stop, the target or the time
    limit is reached.  Bar extremes pre-filter the candidates, sub-bars decide.

    Returns (exit bar, exit sub-bar, exit price, reason).
    """
    sub = s.sub
    if side > 0:                       # long -> exits happen on the bid
        adverse, favourable, close_px = sub["bl"], sub["bh"], sub["bc"]
        bar_adv, bar_fav = s.bar_bl, s.bar_bh
        hit_stop = bar_adv[j:last_bar + 1] <= stop
        hit_tp = bar_fav[j:last_bar + 1] >= tp_arr[j:last_bar + 1]
    else:                              # short -> exits happen on the ask
        adverse, favourable, close_px = sub["ah"], sub["al"], sub["ac"]
        bar_adv, bar_fav = s.bar_ah, s.bar_al
        hit_stop = bar_adv[j:last_bar + 1] >= stop
        hit_tp = bar_fav[j:last_bar + 1] <= tp_arr[j:last_bar + 1]

    for rel in np.flatnonzero(hit_stop | hit_tp):
        m = j + int(rel)
        tp = tp_arr[m]
        a, b = int(s.s0[m]), int(s.s1[m])
        adv, fav = adverse[a:b], favourable[a:b]
        if side > 0:
            sl_where = np.flatnonzero(adv <= stop)
            tp_where = np.flatnonzero(fav >= tp)
        else:
            sl_where = np.flatnonzero(adv >= stop)
            tp_where = np.flatnonzero(fav <= tp)
        if sl_where.size == 0 and tp_where.size == 0:
            continue                                     # bar extreme was a false positive
        sl_i = int(sl_where[0]) if sl_where.size else BIG
        tp_i = int(tp_where[0]) if tp_where.size else BIG
        if sl_i <= tp_i:                                 # tie -> stop first (conservative)
            return m, a + sl_i, float(stop), "sl"
        return m, a + tp_i, float(tp), "tp"

    reason = "time_stop" if last_bar < len(s) - 1 else "session_end"
    last_sub = int(s.s1[last_bar]) - 1
    return last_bar, last_sub, float(close_px[last_sub]), reason


def run_session(s: Session, p: Params) -> list:
    """Generate the trades of one session (price levels only; sizing comes later)."""
    nb = len(s)
    if nb < p.skip_open_bars + 5:
        return []

    vwap = s.get_vwap(p.vwap_price, p.vwap_weight)
    o, c = s.o, s.c
    thr = p.threshold_pct / 100.0
    stop_frac = p.stop_fraction()
    tp_off = p.tp_offset_pct / 100.0
    last_entry_bar = nb - 1 - max(p.no_entry_last_bars, 1)
    if last_entry_bar < 1:
        return []

    # while bar m is trading, the known VWAP is the one from the close of bar m-1
    prev_vwap = np.empty(nb)
    prev_vwap[0] = vwap[0]
    prev_vwap[1:] = vwap[:-1]
    tp_long = prev_vwap * (1.0 - tp_off)
    tp_short = prev_vwap * (1.0 + tp_off)

    slope = None
    if p.max_vwap_slope_pct > 0:
        lb = max(p.slope_lookback, 1)
        slope = np.zeros(nb)
        if nb > lb:
            slope[lb:] = np.abs(vwap[lb:] - vwap[:-lb]) / np.maximum(vwap[lb:], EPS) * 100.0

    # trailing efficiency ratio: net move / total path over the last N bars.
    # Strictly backward looking, so it can be evaluated live at bar k.
    eff = None
    if p.max_efficiency > 0:
        lb = max(p.efficiency_lookback, 2)
        eff = np.ones(nb)                                # unknown -> blocks entries
        if nb > lb:
            path = np.concatenate([[0.0], np.cumsum(np.abs(np.diff(c)))])
            travel = path[lb:] - path[:-lb]
            eff[lb:] = np.abs(c[lb:] - c[:-lb]) / np.maximum(travel, EPS)

    eff_min = None
    if p.min_efficiency > 0:
        lb = max(p.efficiency_lookback, 2)
        eff_min = np.zeros(nb)                       # unknown -> blocks entries
        if nb > lb:
            path = np.concatenate([[0.0], np.cumsum(np.abs(np.diff(c)))])
            travel = path[lb:] - path[:-lb]
            eff_min[lb:] = np.abs(c[lb:] - c[:-lb]) / np.maximum(travel, EPS)

    hours = None
    if p.exclude_hours:
        hours = (s.t // (3600 * NS_PER_SEC)) % 24

    trades = []
    k = max(p.skip_open_bars, 1)
    while k <= last_entry_bar:
        v = vwap[k]
        dev = (c[k] - v) / max(v, EPS)
        follow = p.mode == "follow"
        if dev <= -thr:
            side = -1 if follow else 1
        elif dev >= thr:
            side = 1 if follow else -1
        else:
            k += 1
            continue
        if not _confirms(p, side, k, o, c):
            k += 1
            continue
        if slope is not None and slope[k] > p.max_vwap_slope_pct:
            k += 1
            continue
        if eff is not None and eff[k] > p.max_efficiency:
            k += 1
            continue
        if eff_min is not None and eff_min[k] < p.min_efficiency:
            k += 1
            continue

        j = k + 1                                       # fill on the next bar's open
        if hours is not None and int(hours[j]) in p.exclude_hours:
            k += 1
            continue
        first = int(s.s0[j])
        if side > 0:
            entry = float(s.sub["ao"][first]) + p.slippage
            stop = entry * (1.0 - stop_frac)
        else:
            entry = float(s.sub["bo"][first]) - p.slippage
            stop = entry * (1.0 + stop_frac)

        last_bar = nb - 1
        if p.max_hold_bars > 0:
            last_bar = min(last_bar, j + p.max_hold_bars - 1)

        # target geometry: chase the live VWAP, freeze it at entry, or take a
        # fixed multiple of the risk
        tp_mode = "fixed_r" if p.mode == "follow" else p.tp_mode
        if tp_mode == "vwap_static":
            frozen = float(tp_long[j] if side > 0 else tp_short[j])
            tp_arr = np.full(nb, frozen)
        elif tp_mode == "fixed_r":
            risk = abs(entry - stop)
            tp_arr = np.full(nb, entry + side * p.tp_r * risk)
        else:
            tp_arr = tp_long if side > 0 else tp_short

        m, sub_i, exit_px, reason = _resolve_exit(s, p, side, j, stop, tp_arr, last_bar)
        if p.slippage and reason == "sl":               # stops slip against the position
            exit_px = exit_px - p.slippage if side > 0 else exit_px + p.slippage

        trades.append({
            "param": p.name,
            "session": s.day_ns,
            "side": side,
            "entry_time": int(s.t[j]),
            "exit_time": int(s.sub["idx"][sub_i]) * s.sub_sec * NS_PER_SEC,
            "entry_bar": j,
            "exit_bar": m,
            "bars_held": m - j + 1,
            "entry_price": entry,
            "exit_price": float(exit_px),
            "stop_price": stop,
            "target_at_entry": float(tp_arr[j]),
            "vwap_at_signal": float(v),
            "dev_pct": dev * 100.0,
            "exit_reason": reason,
            "price_pnl": (float(exit_px) - entry) * side,
            "risk_price": abs(entry - stop),
        })

        if p.max_trades_per_session and len(trades) >= p.max_trades_per_session:
            break
        k = m + 1 + p.cooldown_bars

    return trades


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


def format_summary(p: Params, m: dict, extra: str = "") -> str:
    if not m.get("trades"):
        return f"--- {p.name} --- no trades\n"
    lines = [
        f"--- {p.name} ---",
        f"threshold {p.threshold_pct:.2f}%   stop {p.stop_fraction() * 100:.2f}%   "
        f"confirm={p.confirm}   vwap={p.vwap_price}/{p.vwap_weight}",
        f"trades          {m['trades']:>14,}   per day       {m['trades_per_day']:>12.2f}",
        f"win rate        {m['win_rate_pct']:>13.2f}%   profit factor {m['profit_factor']:>12.3f}",
        f"hit VWAP (TP)   {m['tp_rate_pct']:>13.2f}%   stopped out   {m['sl_rate_pct']:>11.2f}%",
        f"net P&L         {m['net_pnl']:>14,.2f}   commissions   {m['commission_paid']:>12,.2f}",
        f"gross profit    {m['gross_profit']:>14,.2f}   gross loss    {m['gross_loss']:>12,.2f}",
        f"expectancy      {m['expectancy']:>14,.2f}   in R          {m['expectancy_R']:>12.3f}",
        f"avg win         {m['avg_win']:>14,.2f}   avg loss      {m['avg_loss']:>12,.2f}",
        f"max drawdown    {m['max_drawdown']:>14,.2f}   ({m['max_drawdown_pct']:.2f}%)",
        f"equity          {m['start_equity']:>14,.2f} -> {m['end_equity']:,.2f}  "
        f"({m['return_pct']:+.2f}%, CAGR {m['cagr_pct']:.2f}%)",
        f"sharpe (daily)  {m['sharpe_daily']:>14.3f}   avg bars held {m['avg_bars_held']:>12.1f}",
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# driver
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


def build_param_grid(args) -> list:
    base = Params(
        threshold_pct=args.threshold_pct, stop_mult=args.stop_mult, stop_pct=args.stop_pct,
        tp_offset_pct=args.tp_offset_pct, confirm=args.confirm,
        vwap_price=args.vwap_price, vwap_weight=args.vwap_weight,
        skip_open_bars=args.skip_open_bars, no_entry_last_bars=args.no_entry_last_bars,
        max_hold_bars=args.max_hold_bars, cooldown_bars=args.cooldown_bars,
        max_trades_per_session=args.max_trades_per_session,
        max_vwap_slope_pct=args.max_vwap_slope_pct, slope_lookback=args.slope_lookback,
        max_efficiency=args.max_efficiency, efficiency_lookback=args.efficiency_lookback,
        exclude_hours=tuple(int(x) for x in args.exclude_hours.split(",") if x.strip()),
        tp_mode=args.tp_mode, tp_r=args.tp_r,
        lots=args.lots, risk_pct=args.risk_pct, max_lots=args.max_lots,
        commission_per_lot=args.commission_per_lot, slippage=args.slippage,
    )
    if not args.sweep:
        return [replace(base, name=f"thr{base.threshold_pct:g}_sl{base.stop_fraction() * 100:g}")]
    slopes = [float(x) for x in args.sweep_slopes.split(",")] if args.sweep_slopes else [None]
    grid = []
    for thr in [float(x) for x in args.sweep_thresholds.split(",")]:
        for sm in [float(x) for x in args.sweep_stops.split(",")]:
            for sl in slopes:
                name = f"thr{thr:g}_x{sm:g}" + (f"_slope{sl:g}" if sl is not None else "")
                grid.append(replace(base, name=name, threshold_pct=thr, stop_mult=sm,
                                    stop_pct=0.0,
                                    **({"max_vwap_slope_pct": sl} if sl is not None else {})))
    return grid


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="VWAP pullback (5-minute) tick backtester",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    d = ap.add_argument_group("data")
    d.add_argument("--data-dir", default="Monthly_Tick_Data")
    d.add_argument("--from-month", default=None, help="inclusive, e.g. 2025-01")
    d.add_argument("--to-month", default=None, help="inclusive, e.g. 2025-12")
    d.add_argument("--subbar-seconds", type=int, default=5)
    d.add_argument("--bar-minutes", type=int, default=5)
    d.add_argument("--session-start-hour", type=int, default=0,
                   help="UTC hour at which the session VWAP resets")
    d.add_argument("--chunksize", type=int, default=2_000_000)
    d.add_argument("--cache-dir", default=None, help="store/reuse compressed sub-bars")
    d.add_argument("--min-session-bars", type=int, default=100)

    s = ap.add_argument_group("strategy")
    s.add_argument("--threshold-pct", type=float, default=0.75)
    s.add_argument("--stop-mult", type=float, default=2.0)
    s.add_argument("--stop-pct", type=float, default=0.0, help="overrides --stop-mult when > 0")
    s.add_argument("--tp-offset-pct", type=float, default=0.0)
    s.add_argument("--confirm", choices=["none", "bar", "engulfing"], default="none")
    s.add_argument("--vwap-price", choices=["typical", "close", "ohlc4"], default="typical")
    s.add_argument("--vwap-weight", choices=["equal", "ticks"], default="equal")
    s.add_argument("--skip-open-bars", type=int, default=6)
    s.add_argument("--no-entry-last-bars", type=int, default=12)
    s.add_argument("--max-hold-bars", type=int, default=0)
    s.add_argument("--cooldown-bars", type=int, default=0)
    s.add_argument("--max-trades-per-session", type=int, default=0)
    s.add_argument("--max-vwap-slope-pct", type=float, default=0.0,
                   help="skip signals when the VWAP trends faster than this (0 = off)")
    s.add_argument("--slope-lookback", type=int, default=12)
    s.add_argument("--max-efficiency", type=float, default=0.0,
                   help="causal chop filter: trailing efficiency ratio must be <= this (0 = off)")
    s.add_argument("--efficiency-lookback", type=int, default=12)
    s.add_argument("--exclude-hours", default="",
                   help="comma separated UTC hours to skip, e.g. 14,19")
    s.add_argument("--tp-mode", choices=["vwap_dynamic", "vwap_static", "fixed_r"],
                   default="vwap_dynamic")
    s.add_argument("--tp-r", type=float, default=1.0)

    e = ap.add_argument_group("execution")
    e.add_argument("--equity", type=float, default=10_000.0)
    e.add_argument("--lots", type=float, default=0.10)
    e.add_argument("--risk-pct", type=float, default=0.0,
                   help="risk-based sizing; overrides --lots when > 0")
    e.add_argument("--max-lots", type=float, default=100.0)
    e.add_argument("--commission-per-lot", type=float, default=7.0, help="round turn, USD")
    e.add_argument("--slippage", type=float, default=0.0, help="USD per side, on top of spread")

    o = ap.add_argument_group("output")
    o.add_argument("--outdir", default="vwap_pullback_results")
    o.add_argument("--sweep", action="store_true")
    o.add_argument("--sweep-thresholds", default="0.3,0.5,0.75,1.0,1.5")
    o.add_argument("--sweep-stops", default="1.0,1.5,2.0,3.0")
    o.add_argument("--sweep-slopes", default=None,
                   help="also sweep --max-vwap-slope-pct, e.g. 0,0.2,0.4")
    o.add_argument("--no-trades-csv", action="store_true")
    o.add_argument("--no-plot", action="store_true")
    o.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    verbose = not args.quiet

    files = discover_files(args.data_dir, args.from_month, args.to_month)
    if not files:
        print(f"no csv files found under {args.data_dir}", file=sys.stderr)
        return 1
    grid = build_param_grid(args)
    if verbose:
        print(f"{len(files)} tick file(s), {len(grid)} parameter set(s)", file=sys.stderr)

    all_trades: list = []
    sessions_seen = 0
    carry = None
    t_start = time.time()

    for i, path in enumerate(files, 1):
        if verbose:
            print(f"[{i}/{len(files)}] {os.path.basename(path)}", file=sys.stderr, flush=True)
        sub = load_subbars(path, args.subbar_seconds, args.chunksize, args.cache_dir, verbose)
        sessions, carry = split_sessions(sub, args.subbar_seconds, args.bar_minutes,
                                         args.session_start_hour, args.min_session_bars,
                                         carry, final=False)
        for sess in sessions:
            sessions_seen += 1
            for p in grid:
                all_trades.extend(run_session(sess, p))

    if carry is not None and carry["idx"].size:           # flush the trailing session
        sessions, _ = split_sessions({k: np.empty(0, dtype=v.dtype) for k, v in carry.items()},
                                     args.subbar_seconds, args.bar_minutes,
                                     args.session_start_hour, args.min_session_bars,
                                     carry, final=True)
        for sess in sessions:
            sessions_seen += 1
            for p in grid:
                all_trades.extend(run_session(sess, p))

    if verbose:
        print(f"{sessions_seen} sessions processed in {time.time() - t_start:.1f}s",
              file=sys.stderr)
    if not all_trades:
        print("no trades generated", file=sys.stderr)
        return 0

    os.makedirs(args.outdir, exist_ok=True)
    raw = pd.DataFrame(all_trades, columns=TRADE_COLUMNS)

    sizing = (f"risk {args.risk_pct:.2f}% of equity per trade" if args.risk_pct > 0
              else f"{args.lots:.2f} lots fixed")
    report = [
        "VWAP Pullback (5-minute) backtest",
        f"data      : {os.path.abspath(args.data_dir)}",
        f"files     : {len(files)}  ({os.path.basename(files[0])} .. {os.path.basename(files[-1])})",
        f"sessions  : {sessions_seen}   bars: {args.bar_minutes}m   "
        f"fills resolved on {args.subbar_seconds}s sub-bars",
        f"session   : VWAP resets at {args.session_start_hour:02d}:00 UTC",
        f"costs     : real bid/ask spread + {args.commission_per_lot} USD/lot round turn"
        f" + {args.slippage} USD/side slippage",
        f"sizing    : {sizing}   start equity {args.equity:,.2f}",
        "",
    ]

    sweep_rows = []
    best_name = best_metrics = best_df = None
    for p in grid:
        one = raw[raw["param"] == p.name].copy()
        if one.empty:
            report.append(f"--- {p.name} --- no trades\n")
            continue
        one = apply_money_management(one, p, args.equity)
        met = compute_metrics(one, args.equity)

        fmt = lambda v: f"{v:,.2f}"
        by_year = one.assign(year=one["exit_dt"].dt.year).groupby("year").agg(
            trades=("pnl", "size"), net_pnl=("pnl", "sum"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100))
        by_side = one.assign(dir=np.where(one["side"] > 0, "long", "short")).groupby("dir").agg(
            trades=("pnl", "size"), net_pnl=("pnl", "sum"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100))
        by_exit = one.groupby("exit_reason")["pnl"].agg(["size", "sum", "mean"])
        extra = ("\nby year:\n" + by_year.to_string(float_format=fmt)
                 + "\n\nby side:\n" + by_side.to_string(float_format=fmt)
                 + "\n\nby exit:\n" + by_exit.to_string(float_format=fmt))
        report.append(format_summary(p, met, extra))
        sweep_rows.append({"param": p.name, "threshold_pct": p.threshold_pct,
                           "stop_pct": p.stop_fraction() * 100, **met})

        if best_metrics is None or met["net_pnl"] > best_metrics["net_pnl"]:
            best_name, best_metrics, best_df = p.name, met, one

        if not args.no_trades_csv:
            tag = f"_{p.name}" if len(grid) > 1 else ""
            cols = ["session_date", "side", "entry_dt", "exit_dt", "bars_held", "entry_price",
                    "exit_price", "stop_price", "target_at_entry", "vwap_at_signal", "dev_pct",
                    "exit_reason", "lots", "commission", "pnl", "r_multiple", "equity"]
            one[cols].to_csv(os.path.join(args.outdir, f"trades{tag}.csv"), index=False)
            daily = one.groupby("session_date")["pnl"].sum()
            pd.DataFrame({"pnl": daily, "equity": args.equity + daily.cumsum()}).to_csv(
                os.path.join(args.outdir, f"equity_daily{tag}.csv"))
            one.assign(month=one["exit_dt"].dt.to_period("M")).groupby("month").agg(
                trades=("pnl", "size"), net_pnl=("pnl", "sum"),
                win_rate=("pnl", lambda x: (x > 0).mean() * 100)).to_csv(
                os.path.join(args.outdir, f"monthly{tag}.csv"))

    if sweep_rows:
        sweep = pd.DataFrame(sweep_rows).sort_values("net_pnl", ascending=False)
        sweep.to_csv(os.path.join(args.outdir, "summary_by_param.csv"), index=False)
        if len(grid) > 1:
            cols = ["param", "trades", "win_rate_pct", "profit_factor", "net_pnl",
                    "expectancy", "max_drawdown_pct", "sharpe_daily"]
            report.append("=== parameter sweep (sorted by net P&L) ===\n"
                          + sweep[cols].to_string(index=False,
                                                  float_format=lambda v: f"{v:,.2f}") + "\n")

    text = "\n".join(report)
    with open(os.path.join(args.outdir, "summary.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)

    if not args.no_plot and best_df is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            daily = best_df.groupby("session_date")["pnl"].sum()
            curve = args.equity + daily.cumsum()
            x = pd.to_datetime(curve.index)
            dd = curve.to_numpy() - np.maximum.accumulate(curve.to_numpy())
            fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
            ax[0].plot(x, curve.to_numpy(), lw=1.2)
            ax[0].set_title(f"VWAP pullback equity curve - {best_name}")
            ax[0].set_ylabel("equity (USD)")
            ax[0].grid(alpha=.3)
            ax[1].fill_between(x, dd, 0, color="crimson", alpha=.5)
            ax[1].set_ylabel("drawdown (USD)")
            ax[1].grid(alpha=.3)
            fig.tight_layout()
            fig.savefig(os.path.join(args.outdir, "equity_curve.png"), dpi=120)
            plt.close(fig)
        except Exception as exc:                          # plotting is optional
            print(f"(plot skipped: {exc})", file=sys.stderr)

    print(f"\nresults written to {os.path.abspath(args.outdir)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
