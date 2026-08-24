#!/usr/bin/env python3
"""
Per-day tick cache: the raw material the block bootstrap resamples.

The monthly csv files are far too slow to draw from at random, so one pass
splits them into a compact npz per calendar (UTC) day:

    ms         int32   milliseconds since midnight UTC  (the source files are
                       already millisecond-stamped, so this is lossless)
    bid_milli  int32   bid in 1/1000 price units        (exact: the quote grid)
    spr_milli  uint16  ask - bid in 1/1000 price units

roughly 10 bytes per tick before compression.  index.json records, per day,
the first / last mid and the overnight log return that led into it, so the
generator can splice days together without inventing gap behaviour.

    python tick_synth/daycache.py --from-month 2024-01 --to-month 2026-07
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import NS_PER_DAY, discover_months, iter_ticks, log

DEFAULT_CACHE = "tick_synth/cache/days"
INDEX = "index.json"


def day_key(day_no: int) -> str:
    return str(np.datetime64(int(day_no), "D"))


def day_dow(day_no: int) -> int:
    """Monday = 0 (epoch day 0 was a Thursday)."""
    return int((day_no + 3) % 7)


class DayCacheBuilder:
    def __init__(self, cache_dir: str, overwrite: bool):
        self.dir = cache_dir
        self.overwrite = overwrite
        os.makedirs(cache_dir, exist_ok=True)
        self.index: dict = {}
        path = os.path.join(cache_dir, INDEX)
        if os.path.exists(path) and not overwrite:
            with open(path, encoding="utf-8") as fh:
                self.index = json.load(fh)
        self.cur_day = None
        self.buf: list = []
        self.prev_close = None
        self.n_days = 0

    def _flush(self) -> None:
        if self.cur_day is None or not self.buf:
            self.cur_day, self.buf = None, []
            return
        ms = np.concatenate([b[0] for b in self.buf])
        bid_milli = np.concatenate([b[1] for b in self.buf])
        spr_milli = np.concatenate([b[2] for b in self.buf])
        key = day_key(self.cur_day)

        first_mid = (bid_milli[0] + spr_milli[0] / 2.0) / 1000.0
        last_mid = (bid_milli[-1] + spr_milli[-1] / 2.0) / 1000.0
        gap = (float(np.log(first_mid / self.prev_close))
               if self.prev_close else 0.0)

        np.savez_compressed(os.path.join(self.dir, f"{key}.npz"),
                            ms=ms, bid_milli=bid_milli, spr_milli=spr_milli)
        self.index[key] = {"dow": day_dow(self.cur_day), "n": int(ms.size),
                           "first_mid": first_mid, "last_mid": last_mid,
                           "open_gap": gap}
        self.prev_close = last_mid
        self.n_days += 1
        self.cur_day, self.buf = None, []

    def feed(self, ns: np.ndarray, bid: np.ndarray, ask: np.ndarray) -> None:
        if ns.size == 0:
            return
        day = ns // NS_PER_DAY
        ms = ((ns % NS_PER_DAY) // 1_000_000).astype(np.int32)
        bid_milli = np.rint(bid * 1000.0).astype(np.int32)
        spr_milli = np.clip(np.rint((ask - bid) * 1000.0), 0, 65535).astype(np.uint16)

        edges = np.flatnonzero(np.diff(day)) + 1
        for a, b in zip(np.concatenate([[0], edges]),
                        np.concatenate([edges, [day.size]])):
            d = int(day[a])
            if self.cur_day is not None and d != self.cur_day:
                self._flush()
            self.cur_day = d
            self.buf.append((ms[a:b], bid_milli[a:b], spr_milli[a:b]))

    def finish(self) -> None:
        self._flush()
        with open(os.path.join(self.dir, INDEX), "w", encoding="utf-8") as fh:
            json.dump(self.index, fh, indent=1, sort_keys=True)


# --------------------------------------------------------------------------
# read side
# --------------------------------------------------------------------------
class DayPool:
    """Random access to cached days, with a small LRU of decoded arrays."""

    def __init__(self, cache_dir: str = DEFAULT_CACHE, from_month: str | None = None,
                 to_month: str | None = None, min_ticks: int = 1000,
                 max_cached: int = 48):
        with open(os.path.join(cache_dir, INDEX), encoding="utf-8") as fh:
            index = json.load(fh)
        self.dir = cache_dir
        self.max_cached = max_cached
        self._mem: dict = {}
        self._order: list = []

        self.index = {}
        for key, rec in index.items():
            month = key[:7]
            if from_month and month < from_month:
                continue
            if to_month and month > to_month:
                continue
            if rec["n"] < min_ticks:
                continue
            self.index[key] = rec
        if not self.index:
            raise SystemExit(f"no cached days in {cache_dir} for the requested range")

        self.by_dow: dict = {}
        for key, rec in sorted(self.index.items()):
            self.by_dow.setdefault(rec["dow"], []).append(key)
        self.keys = sorted(self.index)

    def has_dow(self, dow: int) -> bool:
        return bool(self.by_dow.get(dow))

    def pick(self, rng, dow: int | None = None) -> str:
        pool = self.by_dow.get(dow) if dow is not None else self.keys
        if not pool:
            pool = self.keys
        return pool[int(rng.integers(len(pool)))]

    def load(self, key: str) -> dict:
        hit = self._mem.get(key)
        if hit is not None:
            return hit
        with np.load(os.path.join(self.dir, f"{key}.npz")) as z:
            ms = z["ms"].astype(np.int64)
            bid = z["bid_milli"].astype(np.float64) / 1000.0
            spr = z["spr_milli"].astype(np.float64) / 1000.0
        day = {"tod_ns": ms * 1_000_000, "mid": bid + spr / 2.0, "spr": spr,
               "open_gap": self.index[key]["open_gap"]}
        self._mem[key] = day
        self._order.append(key)
        while len(self._order) > self.max_cached:
            self._mem.pop(self._order.pop(0), None)
        return day


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="Monthly_Tick_Data")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    ap.add_argument("--from-month", default=None)
    ap.add_argument("--to-month", default=None)
    ap.add_argument("--chunksize", type=int, default=2_000_000)
    ap.add_argument("--overwrite", action="store_true",
                    help="rebuild days that are already cached")
    args = ap.parse_args(argv)

    months = discover_months(args.data_dir, args.from_month, args.to_month)
    if not months:
        log(f"no monthly csv found under {args.data_dir}")
        return 1

    b = DayCacheBuilder(args.cache_dir, args.overwrite)
    t0 = time.time()
    for i, (key, path) in enumerate(months, 1):
        if not args.overwrite and any(k.startswith(key) for k in b.index):
            log(f"[{i}/{len(months)}] {key}  already cached, skipping")
            continue
        t1 = time.time()
        for ns, bid, ask in iter_ticks(path, args.chunksize):
            b.feed(ns, bid, ask)
        b._flush()
        log(f"[{i}/{len(months)}] {key}  cached in {time.time() - t1:.1f}s")
    b.finish()
    log(f"{b.n_days} days written to {args.cache_dir} in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
