# Runbook — operating the session breakout

Everything here assumes a **demo account first**. Nothing in this project has
traded a live dollar.

---

## 0. One-time setup

**Use one EA for both configurations.** `XAUUSD_SessionBreakout_2026.mq5`
carries an `ORIGINAL` preset identical to the parent EA's windows, plus every
bug fix. Running it with `InpPreset = ORIGINAL` means switching later is a
dropdown, not a file swap.

1. Copy `strategy_2026/XAUUSD_SessionBreakout_2026.mq5` into
   `MQL5/Experts/` in your terminal's data folder
   (File → Open Data Folder), then compile it in MetaEditor (F7).

2. Terminal settings:
   - Tools → Options → Expert Advisors → **Allow algorithmic trading**
   - Tools → Options → Charts → **Max bars in chart ≥ 10000**
     (the switcher needs ~8000 M5 bars)
   - Open an **XAUUSD M5 chart** and scroll back a few months so the terminal
     caches history

3. Verify the clock — the single setting that silently breaks everything:

   ```bash
   python strategy_2026/regime_switch.py --diagnose
   ```

   Confirm `implies GMT offset ~+0.0h`. On Exness it is 0 year-round, verified
   across both daylight-saving transitions. **On any other broker, check it.**

4. Seed the switch state:

   ```bash
   python strategy_2026/regime_switch.py --set-current ORIGINAL
   ```

---

## 1. Back-test before trading it

Strategy Tester, and it must be **"Every tick based on real ticks"** —
anything else models intrabar sequencing synthetically, which matters when
entries are stop orders and exits are SL/TP.

| setting | value |
|---|---|
| Expert | XAUUSD_SessionBreakout_2026 |
| Symbol | XAUUSD |
| Period | M5 |
| Modelling | Every tick based on real ticks |
| Dates | 2024.01.01 → today |
| Deposit | 10000 (not 1000 — see step 3) |
| `InpPreset` | ORIGINAL |
| `InpGMTOffsetHours` | 0 |
| `InpMaxSpreadUSD` | 0.30 |

Sanity checks on the report — if these do not hold, something is wrong:

- **~5,300 trades** over the full period. Far fewer means the spread filter or
  the GMT offset is wrong.
- **Win rate ~37%**, **profit factor ~1.2**, **avg win : avg loss ~2:1**
- **Maximal position holding time < 24h**, and with the 21:50 flatten **no
  position should be open at the 22:00 rollover** — check the deals for any
  non-zero Swap. The exception is a handful of market holidays (Good Friday,
  Juneteenth, July 4th) where the market shuts before the Friday cutoff and
  the position cannot be closed until it reopens.

---

## 2. Start the forward test

Attach to an **XAUUSD M5 chart**, algorithmic trading enabled.

Recommended starting inputs:

```
InpPreset            = ORIGINAL
InpGMTOffsetHours    = 0
InpBaseLots          = 0.02
InpUseVolTargeting   = true
InpTargetVolPct      = 0.816
InpMaxSpreadUSD      = 0.30
InpMaxOpenPositions  = 8
InpDailyFlatUTCH     = 21
InpDailyFlatUTCM     = 50
InpFridayCloseUTCH   = 20
InpVerboseLog        = true
```

Check the Experts log on the first day. You should see the preset name, the
assumed GMT offset, one "armed" line per window, and one volatility-scale line
per day.

---

## 3. Sizing — read this before funding anything

The backtest figures quoted throughout this project use **0.02 lots per window
on a $10,000 account**. Seven or eight concurrent windows at 0.02 lots is
0.14–0.16 lots ≈ 14–16 oz ≈ **$65,000+ notional at current gold prices**.

On a $1,000 account that is ~65:1 effective exposure, which is why the earlier
$1,000 tester runs showed 30%+ drawdowns on a strategy whose drawdown is ~9%
at the intended size.

Rule of thumb: **$10,000 per 0.02 base lots.** Scale both together.

---

## 4. Weekly routine

Once a week, Sunday before the open, when you are flat anyway:

```bash
python strategy_2026/regime_switch.py --apply
```

Weekly is the right cadence — it catches every switch daily checking finds
(5 in 30 months, ~2 per year) and agrees with daily on 97.4% of sessions.
Daily means looking 650 times to act 5 times; monthly starts missing turns.

Read the ACTION line. Most weeks it says STAY.

Optionally, for context on what is coming:

```bash
python strategy_2026/regime_switch.py --prompt --out today_prompt.txt
```

Paste `today_prompt.txt` into Claude Opus **with web search enabled**. Its most
reliable output is the **dated event calendar** — FOMC, CPI, NFP — which tells
you which sessions carry scheduled risk. Treat its volatility number as a
second opinion against the AR(1) baseline printed in the same prompt, and
treat any directional call outside 45–55% with suspicion.

Keep the JSON line it emits. After a couple of months, score them: if the
model is not beating AR(1) on mean absolute error, keep it for the calendar
and ignore its number.

---

## 5. If it says SWITCH

Only act on a switch that has held for **more than one check**. A single
reading over the threshold is noise; the band exists to prevent flip-flopping.

1. Wait until flat — after 21:50 UTC and before the 00:00 window arms
2. Confirm no open positions and no pending orders remain
3. Change `InpPreset` in the EA's inputs and press OK
4. Record it: `python strategy_2026/regime_switch.py --apply`

Never switch with positions open. The two presets use the same magic-number
base, so the new configuration will adopt orphaned positions from the old one.

---

## 6. What to watch for

| symptom | likely cause |
|---|---|
| far fewer trades than expected | GMT offset wrong, or `InpMaxSpreadUSD` too tight |
| positions held over 24h, not a holiday | Friday cutoff not firing — check `InpFridayCloseUTCH` |
| "price already outside" in the log every day | EA started mid-session; normal on the first day only |
| switcher falls back to sessions.csv | terminal closed, or M5 history not cached |
| drawdown far worse than backtest | account too small for the lot size — see step 3 |

---

## 7. Standing limits

- Validated on **one instrument**, ~30 months, with the last three weeks of
  data not yet incorporated.
- The whole edge is a ~21% margin over breakeven reward:risk. Slippage, not
  spread or commission, is what would erase it.
- The 2026 tuning is a **volatility bet**: it returns 0.42× the original in
  2025-like conditions and 2.20× in 2026-like conditions.
- The highest-value untried test remains a **second instrument**. If session
  breakouts are real microstructure they appear in silver or an index; if they
  are gold-2024-2026 noise they will not.
