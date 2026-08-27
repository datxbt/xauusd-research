#!/usr/bin/env python3
"""
Adaptive preset switch: measure trailing volatility, decide which EA preset
should be running, and remember the decision.

Hysteresis needs STATE. "Hold whichever you are already running" is
meaningless unless something records what that is, so this keeps a small
JSON file and prints an action rather than just a number.

    switch UP   to TUNED_2026 when trailing vol > 1.3
    switch DOWN to ORIGINAL   when trailing vol < 1.1
    otherwise hold

DATA SOURCES, in order of preference
  1. a running MT5 terminal, via the MetaTrader5 package  (live, default)
  2. --csv  a CSV of M5 bars exported from MT5
  3. --sessions  the project's market_context/sessions.csv  (historical only)

USAGE
    python strategy_2026/regime_switch.py                    # check, don't change
    python strategy_2026/regime_switch.py --apply            # check and record
    python strategy_2026/regime_switch.py --set-current ORIGINAL
    python strategy_2026/regime_switch.py --history
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(HERE, "results", "switch_state.json")

ORIGINAL, TUNED = "ORIGINAL", "TUNED_2026"
SWITCH_DOWN, SWITCH_UP = 1.1, 1.3
LOOKBACK = 20
MAX_BARS = 8000        # terminals reject much more than this; 8000 M5 ~ 28 sessions


# --------------------------------------------------------------------------
# volatility
# --------------------------------------------------------------------------
def daily_vol_from_m5(df: pd.DataFrame) -> pd.Series:
    """df needs a UTC DatetimeIndex and a 'close' column. One value per UTC day."""
    out = {}
    for day, g in df.groupby(df.index.date):
        c = g["close"].to_numpy(dtype=float)
        if len(c) < 31:                      # need a real session, not a stub
            continue
        r = np.diff(np.log(np.maximum(c, 1e-12)))
        if len(r) < 20:
            continue
        out[day] = float(np.std(r, ddof=1) * np.sqrt(len(r)) * 100.0)
    return pd.Series(out).sort_index()


def from_mt5(symbol: str, gmt_offset: int, days: int) -> pd.Series:
    """
    Pull M5 bars from a running terminal.

    Uses copy_rates_from_pos, NOT copy_rates_range. On some builds (6140 seen
    in the wild) copy_rates_range returns -2 "Invalid params" for every date
    argument, tz-aware or naive. Position-based fetching sidesteps date
    handling entirely and is what the terminal is happiest with.
    """
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}. "
                           "Open the terminal and log in first.")
    try:
        si = mt5.symbol_info(symbol)
        if si is None:
            names = [s.name for s in (mt5.symbols_get() or [])
                     if symbol.rstrip("0123456789.").upper()[:6] in s.name.upper()]
            raise RuntimeError(f"symbol {symbol} not found. Close matches: "
                               f"{names[:10] or 'none'} - pass --symbol with the exact name")
        if not si.visible:
            mt5.symbol_select(symbol, True)

        # 288 M5 bars per full day, plus slack for weekends and holidays.
        # The terminal refuses requests larger than its cached history / the
        # "Max bars in chart" setting, so step down until one succeeds.
        want = min(int(days * 288 * 1.6) + 1000, MAX_BARS)
        rates = None
        for n in (want, 8000, 6000, 4000, 2500, 1500):
            if n > want:
                continue
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n)
            if rates is not None and len(rates):
                break
        if rates is None or len(rates) == 0:
            raise RuntimeError(
                f"no M5 bars returned: {mt5.last_error()}. Open an M5 chart for "
                f"{symbol} and scroll back so the terminal caches history, or raise "
                "Tools > Options > Charts > Max bars in chart.")
        df = pd.DataFrame(rates)
        # bar times are SERVER time; shift to UTC so sessions bucket correctly
        df["time"] = pd.to_datetime(df["time"], unit="s") - pd.Timedelta(hours=gmt_offset)
        return daily_vol_from_m5(df.set_index("time")[["close"]])
    finally:
        mt5.shutdown()


def diagnose(symbol: str, gmt_offset: int) -> int:
    """Print what the terminal can and cannot do, for troubleshooting."""
    import MetaTrader5 as mt5
    from datetime import datetime as _dt, timezone as _tz
    ok = mt5.initialize()
    print(f"initialize()        {ok}  {mt5.last_error()}")
    if not ok:
        print("  -> open the MT5 terminal, log in, and leave it running")
        return 1
    ti, ai = mt5.terminal_info(), mt5.account_info()
    if ti:
        print(f"terminal            {ti.name}  connected={ti.connected}")
    if ai:
        print(f"account             {ai.login} @ {ai.server}")
    si = mt5.symbol_info(symbol)
    print(f"symbol {symbol:<12} {'FOUND' if si else 'MISSING'}"
          + (f"  visible={si.visible} digits={si.digits}" if si else ""))
    if si and not si.visible:
        print(f"  symbol_select     {mt5.symbol_select(symbol, True)}")
    tick = mt5.symbol_info_tick(symbol)
    if tick:
        st = _dt.fromtimestamp(tick.time, tz=_tz.utc)
        print(f"server clock        {st}  (bid {tick.bid})")
        drift = (st - _dt.now(_tz.utc)).total_seconds() / 3600.0
        print(f"  implies GMT offset ~{drift + gmt_offset:+.1f}h vs your --gmt-offset {gmt_offset}")
    r = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 100)
    print(f"copy_rates_from_pos {0 if r is None else len(r)} bars  {mt5.last_error()}")
    if r is not None and len(r):
        print(f"  newest bar        {_dt.fromtimestamp(r[-1]['time'], tz=_tz.utc)}")
    mt5.shutdown()
    return 0


def from_csv(path: str, gmt_offset: int) -> pd.Series:
    df = pd.read_csv(path)
    tcol = next((c for c in df.columns
                 if c.lower() in ("time", "date", "datetime", "<date>", "timestamp")), None)
    ccol = next((c for c in df.columns if c.lower() in ("close", "<close>")), None)
    if tcol is None or ccol is None:
        raise SystemExit(f"{path}: need a time column and a close column, got {list(df.columns)}")
    df[tcol] = pd.to_datetime(df[tcol], errors="coerce")
    df = df.dropna(subset=[tcol])
    df["time"] = df[tcol] - pd.Timedelta(hours=gmt_offset)
    return daily_vol_from_m5(df.set_index("time").rename(columns={ccol: "close"})[["close"]])


def from_sessions(path: str) -> pd.Series:
    s = pd.read_csv(path, parse_dates=["session_date"]).sort_values("session_date")
    return pd.Series(s["realized_vol_pct"].to_numpy(),
                     index=s["session_date"].dt.date.to_numpy())


def ar1_baseline(vol: pd.Series, hist_path: str | None = None,
                 horizons=(5, 10, 20)) -> str:
    """
    AR(1) baseline - the number any forecast has to beat.

    Parameters are fitted on the LONGEST series available (the project's
    sessions.csv, ~660 sessions) because a live MT5 pull only reaches back
    about 28 sessions - far too few to estimate a mean-reversion coefficient.
    The projection is then anchored at the live trailing value.
    """
    hist = None
    if hist_path and os.path.exists(hist_path):
        try:
            h = pd.read_csv(hist_path, parse_dates=["session_date"]).sort_values("session_date")
            hist = h["realized_vol_pct"].to_numpy(dtype=float)
        except Exception:
            hist = None
    src = "sessions.csv"
    if hist is None or len(hist) < 40:
        hist = vol.to_numpy(dtype=float)
        src = "live series"
    if len(hist) < 40:
        return "  (insufficient history for an AR(1) baseline)"

    b, a = np.polyfit(hist[:-1], hist[1:], 1)
    lr = a / max(1 - b, 1e-9)
    resid = hist[1:] - (b * hist[:-1] + a)
    sd = float(np.std(resid, ddof=1))
    cur = float(vol.tail(20).mean())

    lines = [f"  fitted on {len(hist)} sessions from {src}; anchored at live trailing {cur:.3f}",
             f"  AR(1) beta {b:.3f}, long-run mean {lr:.3f}, residual sd {sd:.3f}"]
    for h_ in horizons:
        v, var = cur, 0.0
        for _ in range(h_):
            v = b * v + a
            var = var * b ** 2 + sd ** 2
        band = 1.28 * np.sqrt(var) / np.sqrt(20)
        lines.append(f"  {h_:>2} sessions ahead: trailing_vol ~ {v:.3f}"
                     f"  (80% ~ {v - band:.3f} to {v + band:.3f})")
    return chr(10).join(lines)


def emit_prompt(vol: pd.Series, trailing: float, current: str, spot,
                out_path: str | None = None) -> int:
    tpl_path = os.path.join(HERE, "forecast_prompt.md")
    if not os.path.exists(tpl_path):
        raise SystemExit(f"missing template {tpl_path}")
    tpl = open(tpl_path, encoding="utf-8").read()
    body = tpl.split("## THE PROMPT", 1)[1].split("## Scoring what comes back", 1)[0]
    body = body.strip()
    if body.endswith("---"):
        body = body[:-3].rstrip()

    last10 = ", ".join(f"{d}:{v:.3f}" for d, v in vol.tail(10).items())
    repl = {
        "{{AS_OF}}": str(vol.index[-1]),
        "{{LAST10}}": last10,
        "{{TRAILING}}": f"{trailing:.3f}",
        "{{ANNUALISED}}": f"{trailing * np.sqrt(252):.1f}",
        "{{SPOT}}": (f"{spot:.3f}" if spot else "unavailable"),
        "{{CURRENT_PRESET}}": current,
        "{{AR1_BASELINE}}": ar1_baseline(
            vol, os.path.join(ROOT, "market_context", "sessions.csv")),
    }
    for k, v in repl.items():
        body = body.replace(k, v)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(body + chr(10))
        print(f"prompt written to {out_path} ({len(body):,} chars) - paste it into "
              "Claude Opus with web search on", file=sys.stderr)
        return 0
    # the Windows console is cp1252; never let an em-dash crash the run
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(body)
    return 0


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
def load_state() -> dict:
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"current": ORIGINAL, "since": None, "last_check": None, "history": []}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


def decide(trailing: float, current: str) -> tuple:
    if np.isnan(trailing):
        return current, "no reading - cannot decide"
    if trailing > SWITCH_UP and current != TUNED:
        return TUNED, f"trailing {trailing:.3f} above {SWITCH_UP}"
    if trailing < SWITCH_DOWN and current != ORIGINAL:
        return ORIGINAL, f"trailing {trailing:.3f} below {SWITCH_DOWN}"
    if trailing > SWITCH_UP:
        return current, f"trailing {trailing:.3f} above {SWITCH_UP}, already on it"
    if trailing < SWITCH_DOWN:
        return current, f"trailing {trailing:.3f} below {SWITCH_DOWN}, already on it"
    return current, f"trailing {trailing:.3f} inside the {SWITCH_DOWN}-{SWITCH_UP} band"


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="adaptive EA preset switch on trailing volatility")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--gmt-offset", type=int, default=0, help="server time = GMT + this")
    ap.add_argument("--lookback", type=int, default=LOOKBACK)
    ap.add_argument("--csv", default=None, help="M5 bar CSV exported from MT5")
    ap.add_argument("--sessions", default=None, help="fallback: market_context/sessions.csv")
    ap.add_argument("--apply", action="store_true", help="record the decision in the state file")
    ap.add_argument("--set-current", choices=[ORIGINAL, TUNED], default=None,
                    help="declare what is running now, without deciding")
    ap.add_argument("--history", action="store_true", help="print the decision log and exit")
    ap.add_argument("--diagnose", action="store_true", help="probe the MT5 connection and exit")
    ap.add_argument("--out", default=None, help="write the prompt to this file (UTF-8)")
    ap.add_argument("--prompt", action="store_true",
                    help="emit the forecasting prompt filled with current readings")
    args = ap.parse_args(argv)

    if args.diagnose:
        return diagnose(args.symbol, args.gmt_offset)

    st = load_state()

    if args.history:
        print(f"current: {st['current']}  since {st.get('since')}")
        for h in st.get("history", [])[-25:]:
            print(f"  {h['date']}  vol {h['trailing']:.3f}  {h['action']}")
        return 0

    if args.set_current:
        st["current"] = args.set_current
        st["since"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_state(st)
        print(f"recorded: currently running {args.set_current}")
        return 0

    # ---- get the volatility series ----
    src = None
    try:
        if args.csv:
            vol, src = from_csv(args.csv, args.gmt_offset), f"csv {args.csv}"
        elif args.sessions:
            vol, src = from_sessions(args.sessions), f"sessions {args.sessions}"
        else:
            vol, src = from_mt5(args.symbol, args.gmt_offset, args.lookback * 3 + 30), "live MT5"
    except Exception as exc:
        fallback = os.path.join(ROOT, "market_context", "sessions.csv")
        if os.path.exists(fallback):
            print(f"live source unavailable ({exc})\n  falling back to {fallback}\n",
                  file=sys.stderr)
            vol, src = from_sessions(fallback), "sessions.csv (HISTORICAL - not live)"
        else:
            raise SystemExit(f"no usable data source: {exc}")

    vol = vol.dropna()
    if len(vol) < 5:
        raise SystemExit(f"only {len(vol)} sessions of volatility - need more history")

    window = vol.tail(args.lookback)
    trailing = float(window.mean())
    latest = vol.index[-1]

    if args.prompt:
        spot = None
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                t = mt5.symbol_info_tick(args.symbol)
                spot = (t.bid + t.ask) / 2 if t else None
                mt5.shutdown()
        except Exception:
            pass
        return emit_prompt(vol, trailing, st["current"], spot, args.out)

    hist = st.get("history", [])
    if hist and hist[-1].get("to") and hist[-1]["to"] != st["current"]:
        print(f"WARNING: state file says {st['current']} but the last log entry ended on "
              f"{hist[-1]['to']}. Re-set it with --set-current before trusting this.",
              file=sys.stderr)

    new, why = decide(trailing, st["current"])
    switching = (new != st["current"])

    print(f"source            {src}")
    print(f"sessions used     {len(window)} (through {latest})")
    print("\nlast 8 sessions:")
    for d, v in vol.tail(8).items():
        print(f"  {d}   {v:.3f}")
    print(f"\nTRAILING VOL      {trailing:.3f}   (annualised {trailing * np.sqrt(252):.1f}%)")
    print(f"currently running {st['current']}   since {st.get('since') or 'unset'}")
    print(f"reason            {why}")
    print("\n" + "=" * 58)
    print(f"ACTION:  {'SWITCH TO ' + new if switching else 'STAY ON ' + st['current']}")
    print("=" * 58)
    if switching:
        print("\nSwitch safely:")
        print("  1. wait until flat - after 00:00 UTC, before the 00:00 window arms")
        print("  2. remove the running EA, confirm no orders or positions remain")
        print("  3. attach with InpPreset set to the new value")
        print("  4. re-run this with --apply to record it")

    if args.apply:
        st["history"].append({"date": str(latest), "trailing": round(trailing, 4),
                              "from": st["current"], "to": new,
                              "action": ("switch to " + new) if switching else f"stay {new}"})
        st["current"] = new
        if switching:
            st["since"] = str(latest)
        st["last_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        save_state(st)
        print("\nstate recorded.")
    elif switching:
        print("\n(dry run - re-run with --apply to record)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
