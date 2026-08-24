#!/usr/bin/env python3
"""
Shared plumbing for the tick synthesizer: the on-disk tick format, month
discovery, a streaming reader, and a writer that reproduces the Exness CSV
layout byte-for-byte so synthetic files drop straight into the existing
backtests.
"""

from __future__ import annotations

import csv
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

NS_PER_SEC = 1_000_000_000
NS_PER_MIN = 60 * NS_PER_SEC
NS_PER_DAY = 86_400 * NS_PER_SEC

QUOTE_TICK = 0.001             # price quantum in the source files
SPREAD_UNIT = 0.001            # spread histograms are binned in quote ticks
SPREAD_BINS = 2000             # -> spreads up to 2.000 price units are resolved
MIN_SPREAD = 0.001             # never emit a crossed or zero-width book

MOW = 7 * 1440                 # minute-of-week bins
HOW = 7 * 24                   # hour-of-week bins

BROKER = "exness"
SYMBOL = "XAUUSD_Raw_Spread"
HEADER = ["Exness", "Symbol", "Timestamp", "Bid", "Ask"]
STEM = "Exness_XAUUSD_Raw_Spread"

MONTH_RE = re.compile(r"(\d{4})[_-](\d{2})")


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
def discover_months(data_dir: str, from_month: str | None = None,
                    to_month: str | None = None) -> list:
    """[(YYYY-MM, path)] for every monthly csv under `data_dir`, in time order."""
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
    return keyed


def month_path(out_dir: str, month: str) -> str:
    """Mirror the source layout: <out>/<YYYY>/<STEM>_<YYYY>_<MM>/<STEM>_<YYYY>_<MM>.csv"""
    year, mm = month.split("-")
    name = f"{STEM}_{year}_{mm}"
    d = os.path.join(out_dir, year, name)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{name}.csv")


def month_days(month: str) -> np.ndarray:
    """Midnight-UTC ns for every calendar day in `month`."""
    start = np.datetime64(month + "-01", "D")
    end = (start.astype("datetime64[M]") + 1).astype("datetime64[D]")
    days = np.arange(start, end, dtype="datetime64[D]")
    return days.astype("datetime64[ns]").astype(np.int64)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def iter_ticks(csv_path: str, chunksize: int = 2_000_000):
    """Stream one monthly file as sorted (ns, bid, ask) chunks."""
    reader = pd.read_csv(
        csv_path,
        usecols=["Timestamp", "Bid", "Ask"],
        dtype={"Bid": np.float64, "Ask": np.float64},
        chunksize=chunksize,
    )
    for chunk in reader:
        ts = pd.to_datetime(chunk["Timestamp"], format="ISO8601", utc=True)
        ns = ts.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]").astype(np.int64)
        bid = chunk["Bid"].to_numpy(dtype=np.float64)
        ask = chunk["Ask"].to_numpy(dtype=np.float64)
        if ns.size and not np.all(np.diff(ns) >= 0):
            order = np.argsort(ns, kind="stable")
            ns, bid, ask = ns[order], bid[order], ask[order]
        yield ns, bid, ask


def minute_of_week(ns: np.ndarray) -> np.ndarray:
    """0 = Monday 00:00 UTC .. 10079 = Sunday 23:59 UTC."""
    minutes = ns // NS_PER_MIN
    # 1970-01-01 was a Thursday (dow 3) -> shift by 3 days to put Monday at 0
    return ((minutes + 3 * 1440) % MOW).astype(np.int64)


def second_of_day(ns: np.ndarray) -> np.ndarray:
    return (ns % NS_PER_DAY) // NS_PER_SEC


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
def fmt_timestamps(ns: np.ndarray) -> np.ndarray:
    """int64 ns -> '2025-03-02 23:05:00.071Z' (millisecond resolution)."""
    s = ns.astype("datetime64[ns]").astype("datetime64[ms]").astype(str)
    return np.char.add(np.char.replace(s, "T", " "), "Z")


class TickWriter:
    """Appends ticks to one monthly csv in the exact source dialect."""

    def __init__(self, path: str):
        self.path = path
        self.fh = open(path, "w", newline="", encoding="utf-8")
        self.rows = 0
        self.fh.write(",".join(f'"{h}"' for h in HEADER) + "\n")

    def write(self, ns: np.ndarray, bid: np.ndarray, ask: np.ndarray) -> None:
        if ns.size == 0:
            return
        df = pd.DataFrame({
            "Exness": BROKER,
            "Symbol": SYMBOL,
            "Timestamp": fmt_timestamps(ns),
            "Bid": np.round(bid, 3),
            "Ask": np.round(ask, 3),
        })
        df.to_csv(self.fh, header=False, index=False, quoting=csv.QUOTE_NONNUMERIC,
                  lineterminator="\n")
        self.rows += len(df)

    def close(self) -> None:
        self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def quantize_book(mid: np.ndarray, spread: np.ndarray):
    """Efficient mid + half-spread each side, snapped to the quote grid."""
    spread = np.maximum(spread, MIN_SPREAD)
    bid = np.round((mid - spread / 2.0) / QUOTE_TICK) * QUOTE_TICK
    ask = np.round((mid + spread / 2.0) / QUOTE_TICK) * QUOTE_TICK
    ask = np.maximum(ask, bid + QUOTE_TICK)
    return bid, ask


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
