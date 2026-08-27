# Manual execution rules

Everything the EA does, written out for a human. All times are **UTC**.
Vietnam local time is UTC+7 — local equivalents are given in brackets.

Read the workload section at the bottom before committing to this. Running all
seven or eight windows by hand is 25–30 interactions a day spread across 23
hours, and the realistic manual version is a subset.

---

## 1. The windows

Each window is `(hour H, range length R, target multiple T)`.

**GEO_2026_NO_H13** — the current default, 7 windows, all 60-minute brackets
with 3× targets:

| window | bracket forms | you act at | local |
|---|---|---|---|
| h00 | 00:00–01:00 | **01:00** | 08:00 |
| h01 | 01:00–02:00 | **02:00** | 09:00 |
| h02 | 02:00–03:00 | **03:00** | 10:00 |
| h04 | 04:00–05:00 | **05:00** | 12:00 |
| h05 | 05:00–06:00 | **06:00** | 13:00 |
| h06 | 06:00–07:00 | **07:00** | 14:00 |
| h14 | 14:00–15:00 | **15:00** | 22:00 |

**ORIGINAL** — 8 windows, mixed geometry (the 2024–25 configuration):

| window | bracket | act at | local | T |
|---|---|---|---|---|
| h00 | 00:00–00:30 | 00:30 | 07:30 | 1× |
| h01 | 01:00–02:00 | 02:00 | 09:00 | 3× |
| h02 | 02:00–02:15 | 02:15 | 09:15 | 3× |
| h04 | 04:00–04:30 | 04:30 | 11:30 | 3× |
| h05 | 05:00–06:00 | 06:00 | 13:00 | 2× |
| h06 | 06:00–07:00 | 07:00 | 14:00 | 3× |
| h13 | 13:00–13:30 | 13:30 | 20:30 | 3× |
| h14 | 14:00–14:15 | 14:15 | 21:15 | 2× |

---

## 2. At the moment the bracket closes

**Measure the bracket.** On the **M1 chart**, take the highest high and the
lowest low of every bar from `H:00` up to but not including `H:00 + R`.

MT5 bars are bid-priced while the strategy is defined on mid, so add **half the
current spread to both** the high and the low. Both shift up by the same
amount, so the width is unchanged. At today's spreads that is 3–6 cents — do it
for fidelity, but it changes little.

```
HIGH  = highest M1 high in the window + spread/2
LOW   = lowest  M1 low  in the window + spread/2
WIDTH = HIGH - LOW
```

**Then run the skip checks. Any one of these means do nothing for that window
today:**

| check | skip if |
|---|---|
| bracket too narrow | `WIDTH / ((HIGH+LOW)/2) × 100` **< 0.05%** |
| bracket too wide | same figure **> 2.00%** |
| spread too wide | current spread **> $0.30/oz** |
| already broken out | current **ask ≥ HIGH** or **bid ≤ LOW** — you missed it |
| Sunday | skip the whole session |
| too many open | you already have 8 positions |

The "already broken out" check matters. If price has left the bracket before
you place the orders, do **not** place just the far side — you would be taking
the wrong direction. Sit the day out for that window.

---

## 3. Place the orders

Two pending stop orders, same lot size:

```
BUY STOP   at HIGH    SL = LOW     TP = HIGH + (T × WIDTH)
SELL STOP  at LOW     SL = HIGH    TP = LOW  - (T × WIDTH)
```

Note the stop loss is **the opposite side of the bracket** — a fixed price, not
a distance from entry.

Set the pending order **Expiration** to `Specified` and put it at
`bracket close + 4 hours`. MT5 will then cancel unfilled orders for you, which
removes one whole class of manual work.

**Worked example.** h01 bracket 01:00–02:00 forms high 4680.00, low 4665.00
(after the half-spread shift). WIDTH = 15.00, T = 3.

```
width% = 15.00 / 4672.50 × 100 = 0.32%      -> inside 0.05–2.00%, proceed
BUY  STOP 4680.00   SL 4665.00   TP 4680.00 + 45.00 = 4725.00
SELL STOP 4665.00   SL 4680.00   TP 4665.00 - 45.00 = 4620.00
expiry 06:00 UTC
```

---

## 4. When one side fills

**This is the part that cannot be skipped.**

1. **Cancel the opposite pending order immediately.** MT5 has no native OCO. If
   you leave it, price can reverse through the bracket and fill the other side
   too, leaving you long and short at once.
2. **Re-anchor the take profit to the actual fill.** If the buy stop filled at
   4680.60 rather than 4680.00, the correct TP is `4680.60 + 45.00 = 4725.60`,
   not 4725.00. The stop loss stays at LOW — do not move it.

Set a price alert at each pending level so you are told when one triggers.

After that, leave the position alone. Stop and target sit at the broker and
work whether you are watching or not.

---

## 5. Daily and weekly

| when | do |
|---|---|
| **21:50 UTC** (04:50 local) | **Close every open position and cancel every pending order.** No exceptions. |
| **Friday 20:00 UTC** (Sat 03:00 local) | Same, and do not re-open. Nothing runs over a weekend. |
| Sunday | No trading at all. |

Flatten at **21:50, before the 22:00 rollover** — not at the UTC day end. The
rollover brings a 2-3 minute halt with no ticks, a spread spike to ~2.9x the
daytime level, and the overnight swap (-$56.32/lot on longs, shorts free).
Across 2024-2026 holding through it added +$25 of gross P&L and cost $629 of
swap. Testing 21:50 against the old 23:57 close gave +3.0% net, PF 1.214 vs
1.203, and a 9% shallower drawdown for the default preset.

---

## 6. Position size

```
lots = base × (0.816 / trailing_20day_volatility),  capped between ⅓× and 3×
```

Get `trailing_20day_volatility` from:

```bash
python strategy_2026/regime_switch.py
```

Base size is **0.02 lots per window per $10,000 of account**. Seven windows at
0.02 lots is 0.14 lots ≈ 14 oz ≈ $65,000 notional at current gold prices — on a
$1,000 account that is 65:1, which is how you get a 30% drawdown from a
strategy whose real drawdown is ~9%.

Recompute the multiplier once a week, not per trade.

---

## 7. Daily checklist

```
[ ] 01:00  h00 — measure, check, place, set 05:00 expiry
[ ] 02:00  h01 — measure, check, place, set 06:00 expiry
[ ] 03:00  h02 — measure, check, place, set 07:00 expiry
[ ] 05:00  h04 — measure, check, place, set 09:00 expiry
[ ] 06:00  h05 — measure, check, place, set 10:00 expiry
[ ] 07:00  h06 — measure, check, place, set 11:00 expiry
[ ] 15:00  h14 — measure, check, place, set 19:00 expiry
[ ] on any fill — cancel the sibling, re-anchor the TP
[ ] 21:50  close everything (before the 22:00 rollover)
[ ] Friday 20:00 — close everything, stay flat until Monday
```

---

## 8. The workload problem

Seven windows means **14 order placements, up to 7 OCO cancellations, 7 TP
re-anchorings and a daily flatten** — 25–30 interactions spread from 01:00 to
21:50 UTC.

The arming times are all in Vietnamese waking hours (08:00–22:00 local), which
is the good news. The bad news is the **21:50 UTC flatten lands at 04:50 local**
and fills can arrive at any hour in between.

A realistic manual version is a **subset**. Pick two or three windows whose
arming times suit you, and accept that:

- Fewer windows means less diversification. The cross-window correlation of
  0.041 is what made the portfolio work; a two-window version is far noisier.
- No subset has been backtested as a standalone portfolio. You would be
  trading something that has never been measured.

If you want the strategy as tested, it has to be automated. Manual execution is
best used to **learn the mechanics** for a week before handing it to the EA —
which is a genuinely good reason to do it.

---

## 9. Before you spend the effort

The 2020–2023 test found no edge across four years and ~7,750 trades, and
neither costs nor volatility explained it. Current confidence that a real
tradeable edge exists is roughly **30%**, with expected profit factor nearer
1.05 than the 1.20 measured in-sample.

That does not make manual practice pointless — but it is worth knowing before
committing to 25 interactions a day.
