# Handoff — XAUUSD session breakout project

Paste this into a new chat to restore full context. Written 2026-08-27.

---

## 1. What this project is

Research on XAUUSD (gold) tick data. Two strategies were built and tested:

1. **VWAP pullback (5-minute)** — the original request. **Dead.** Lost money in
   all 15 parameter sets, negative even with zero costs. Falsified: reversion
   probability *falls* with stretch size (53.8% at 0.2-0.3% → 8.2% at
   1.5-2.0%), the opposite of the premise. Scripts were later removed.

2. **ORB session breakout** — built after the VWAP work failed, from a
   pre-registered market-structure hypothesis rather than from data mining.
   This is the live project. Details below.

Whole project is in `C:\Users\thanh\Downloads\TradingProject`, git repo,
branch `main`.

---

## 2. The data

`Monthly_Tick_Data/{year}/Exness_XAUUSD_Raw_Spread_{Y}_{M}/*.csv` — 20 GB,
79 monthly files, **2020-02 through 2026-07** (2020-01 is partial, starts
on the 29th).

```
"Exness","Symbol","Timestamp","Bid","Ask"
"exness","XAUUSD_Raw_Spread","2024-01-01 23:05:09.965Z",2065.007,2065.76
```

Timestamps are **UTC**, fixed 24-char format. ~176M ticks in 2024-2026 alone.

`tick_synth/` (85 GB) is a synthetic tick generator — block bootstrap and
parametric. It has its own README. Key outputs: `rep2026_1..5` and
`regime_2026` (2026-regime replicates), `null30` (30-minute block null tape).
Generated tapes can be deleted and reproduced from `manifest.json`.

`market_context/sessions_full.csv` — per-session stats 2020-2026 (realized
vol, range, spread, trendiness). Built by `session_context.py`.

---

## 3. The strategy

Opening-range breakout, one trade per window per session, direction-neutral.

**Per window `(hour H, range R minutes, target multiple T)`:**
1. Bracket = high/low of mid price over `[H:00, H:00+R)`
2. Buy Stop at the high, Sell Stop at the low (manual OCO — first fill cancels
   the other)
3. Stop loss = the opposite side of the bracket (a fixed price)
4. Take profit = **actual fill** ± `T × bracket width`
5. Cancel unfilled orders 4h after the bracket closes
6. Skip if width outside 0.05%-2.00% of price, spread > $0.30/oz, price
   already outside the bracket, Sunday, or 8 positions already open
7. **Flat at 21:50 UTC**, and Fridays at 20:00 UTC

**Presets** (EA input `InpPreset`):

| preset | windows |
|---|---|
| `GEO_2026_NO_H13` *(default)* | hours 0,1,2,4,5,6,14 — all 60m brackets, 3× targets |
| `GEO_2026` | same + h13 |
| `TOP8_2026` | hours 0,1,2,5,8,14,15,18 — the 2026 re-selection, most fitted |
| `ORIGINAL` | the 2024-25 config: h00_r30_t1, h01_r60_t3, h02_r15_t3, h04_r30_t3, h05_r60_t2, h06_r60_t3, h13_r30_t3, h14_r15_t2 |

**Sizing:** `lots = 0.02 × (0.816 / trailing_20day_vol)` per $10,000, capped
⅓×–3×. Trailing vol = mean of daily realized vol, where daily vol =
`stdev(M5 log returns) × sqrt(n) × 100`.

---

## 4. Findings, in order of importance

### 4.1 It FAILS out of sample on 2020-2023 — the decisive negative

1,008 trading days, ~7,750 trades — a **larger sample than it was built on**.

| | PF | net |
|---|---|---|
| ORIGINAL | **0.93** | −$2,518 |
| TUNED | **0.95** | −$1,791 |

By year: 0.85, 0.94, 1.02, 0.95. Only 4 of 24 hours were profitable at all —
the mechanism is **absent**, not merely mis-located. The 8 selected hours were
positive in 2/8 here vs 2/16 for the rejected ones; average rank 10.8 of 24
where 11.5 = no skill; rank correlation with 2026 = **+0.048**.

**Neither costs nor volatility explains it:**
- At modern spreads it still loses (−$1,575 / −$963)
- 2024 ran at 0.205% mean relative range and made PF 1.19; **2021 ran at
  0.206% and made 0.94; 2022 at 0.203% made 1.02**. Same volatility, opposite
  outcomes.

The split is clean in **time**, not conditions: 2020-2023 all ≤1.02,
2024-2026 all ≥1.17. The windows were selected on 2024-01..2025-06. Working
only in the training window plus the adjacent year is the textbook signature
of overfitting.

> **A correction made mid-conversation:** an earlier "needs relative range
> > 0.3%" conclusion was an artifact of comparing 2020-2023 across all 216
> configs against 2024-2026 from a different window set. Computed
> consistently it collapses. Don't resurrect it.

### 4.2 What still stands

- **Null-tape test passed.** On `null30` (30-min blocks, multi-hour structure
  destroyed) PF drops 1.21 → 0.95 while trade count (5,021 vs 5,010) and
  reward:risk (1.98 vs 2.04) hold. Only the win rate collapses, 37.2% → 32.4%,
  against a 33.3% breakeven. So the 2024-2026 P&L is a real pattern requiring
  genuine directional persistence — not a cost artifact or a bug.
- **Six 2026-regime replicates**: TUNED beats ORIGINAL 6/6, median $9,727 vs
  $4,825. *But this is near-circular* — blocks are matched by time of day, so
  "hour 00 was good in 2026" is baked in. It tests day-ordering, not selection.
- **Three engines agree**: Python, MT5 trial, MT5 live — within 0.5% on trade
  count and 0.1pp on win rate.
- **Forward test Aug 2-25 2026** (out of sample, real ticks, paired): TUNED
  PF 1.90 / $976 vs ORIGINAL PF 1.44 / $511; P(TUNED better) = 96.5% on 17
  days. Both beat their own backtests, so August was a kind month.

### 4.3 Current confidence

**~30%** that a real tradeable edge exists, expected live PF nearer **1.05**
than the 1.20 measured in-sample. 12-month P(profit) ≈ 45%. (Was 67% before
the 2020-2023 test.)

### 4.4 Regime facts

| year | ann. vol | daily range | avg spread | direction |
|---|---|---|---|---|
| 2024 | 15.1% | 1.38% | $0.056 | +27% |
| 2025 | 19.3% | 1.72% | $0.037 | +65% |
| 2026 H1 | 32.9% | 2.81% | $0.091 | −7% |

Forecastable (monthly AR1): realized vol 0.70, spread 0.87, ticks 0.67,
range 0.64. **Not** forecastable: direction 0.12, trendiness 0.07. This is why
a causal trend filter could never work — trendiness has no persistence.

Latest live reading (2026-08-24): trailing vol **1.263**, spot ~4670 (up from
4044 on Jul 31, +15% in under four weeks).

---

## 5. Microstructure discovered

- **Rollover is 21:58-22:00 UTC**, not midnight. **Zero ticks** for 2-3
  minutes, then spread spikes to ~2.9× the daytime baseline. 21:00 is the
  *tightest* hour, 22:00 the widest.
- **Swap**: `swap_long = -563.2 points = -$56.32/lot/night`, **shorts free**,
  triple on Wednesday. Never modelled in the Python engine — all quoted P&L
  is optimistic by ~3.3%.
- **Flatten timing tested** (`strategy_2026/flatten_test.py`): the
  21:50→22:30 window adds **+$8/+$25 gross** across 31 months while swap on
  positions carried through costs $629. For TUNED, 21:50 beats 23:57 on net
  (+3.0%), PF (1.214 vs 1.203), drawdown (−9%) and Sharpe. For ORIGINAL it's
  a wash. **EA now defaults to 21:50.**

---

## 6. Technical gotchas (hard-won — do not re-derive)

- **GMT offset = 0** on Exness (trial *and* live), year-round, verified three
  independent ways: bracket prices vs UTC tick data (median error $0.018 at
  offset 0, $9-19 at every other offset), no DST shift across both 2025
  transitions, and the live terminal clock. A wrong offset silently trades a
  completely different strategy.
- **`mt5.copy_rates_range` is broken on build 6140** — returns
  `-2 Invalid params` for every date argument, tz-aware or naive. Use
  **`copy_rates_from_pos`**.
- **The terminal caps bar requests around 8,000** (Max bars in chart). Larger
  requests fail. `regime_switch.py` has a back-off ladder.
- **Spread filter must be in USD, not points.** Gold is 3-digit here, so the
  old `InpMaxSpreadPoints = 60` meant **$0.06** and blocked 100% of Jan-Jul
  2024 and 86% of 2026. Now `InpMaxSpreadUSD = 0.30`.
- **Running two EA instances needs different `InpMagicBase`** (8820000 /
  8830000). Same base means each closes the other's trades.
- **The weekend leak**: a clock-window flatten alone fails when the market
  shuts before the window (holidays). Fixed by
  `CloseStaleFromEarlierSessions()`, which closes anything opened on a prior
  UTC day. Took >24h holds from 2.55% to 0.73%, and the residual is
  holiday closures that cannot be closed while the market is shut.
- Sizing: **$10,000 per 0.02 base lots.** 7-8 windows × 0.02 lots ≈ $65,000
  notional; on a $1,000 account that's 65:1 and produces 30% drawdowns on a
  strategy whose real drawdown is ~9%.

---

## 7. Files

**Engine (root):** `tickdata.py` (tick→sub-bar loader, sessions, metrics),
`backtest_orb.py` (ORB engine + pre-registered protocol),
`optimize_time_windows.py` (216-window scan), `session_context.py`,
`regime_analysis.py`, `optimize_for_regime.py`, `run_synth_backtest.py`,
`XAUUSD_SessionBreakout.mq5` (original EA).

**strategy_2026/:**
| file | purpose |
|---|---|
| `XAUUSD_SessionBreakout_2026.mq5` | the EA — 4 presets, all fixes, 21:50 flatten |
| `TrailingVol.mq5` | MT5 script printing trailing vol + preset verdict |
| `regime_switch.py` | weekly preset switcher, live MT5 data, hysteresis + state |
| `trailing_vol.py` | trailing vol from stored data |
| `optimize_2026.py` | 2026 selection + replicate validation |
| `decompose.py` | geometry-vs-hours split, walk-forward |
| `head_to_head.py` | ORIGINAL vs TUNED on synthetic tapes |
| `test_2020_2023.py` | the out-of-sample test that failed |
| `flatten_test.py` | flatten-time comparison |
| `forecast_prompt.md` | LLM volatility-forecast prompt template |
| `RUNBOOK.md` | operating procedure |
| `MANUAL_RULES.md` | manual execution rules |

**Stale artifact:** a strategy spec page was published at
`claude.ai/code/artifact/1c26af50-02aa-4ea5-b4d5-62f4da442603` **before** the
2020-2023 test. It still shows PF 1.21 / Sharpe 2.73 and none of the negative
findings. Either update it or do not share it.

Engine detail: ticks stream into 5-second sub-bars keeping bid/ask extremes
separately; signals on the bracket, exits resolved sub-bar by sub-bar; on a
tie within one sub-bar the **stop** wins. Longs exit on the bid, shorts on the
ask. Costs = real spread in the data + $7/lot round turn. Verified against raw
ticks by hand.

---

## 8. Where things stand / what to do next

**Not deployed.** Demo forward test only.

Open items, most valuable first:

1. **Explain or accept the 2020-2023 failure.** This is the crux. Either the
   effect genuinely emerged around 2024 (unfalsifiable with current data) or
   the 2024-2026 result is overfit. Nothing yet distinguishes them.
2. **Second instrument** (silver / index / FX). Note the asymmetry: confirming
   is strong evidence, failing is weak — gold-specific structure is plausible.
   The user pushed back on this and was right.
3. Forward test both presets in parallel — **keep both running regardless of
   what the switcher says**, or the paired comparison loses its power. Send
   reports monthly; ~4-6 months for a statistically meaningful read.
4. Re-run the tester with the 21:50 flatten and confirm **zero non-zero Swap
   entries** in the deals.

**Preset switch rule** (in `regime_switch.py`, run weekly):
`trailing vol < 1.1` → ORIGINAL · `1.1-1.3` → hold · `> 1.3` → TUNED_2026.
Hysteresis needs state, kept in `results/switch_state.json`. Currently seeded
to ORIGINAL; the rule's own history would say TUNED_2026 (path-dependent).
Switching earns ~5% over always-ORIGINAL but with 28% deeper drawdown, so I
recommended using it as a monitor, not an autopilot.

---

## 9. Working style that worked here

The user is sharp and pushes back well — twice they identified real errors in
my reasoning (the cross-instrument test being weaker than I claimed; the
range-% comparison being apples-to-oranges). Both times they were right.

Pre-register tests before running them. Report negative results plainly and
lead with them. Quantify rather than assert — nearly every question in this
project was settled by running something, not by arguing. Distinguish "real
pattern within the fitted window" from "effect that will persist"; only the
first has ever been established here.
