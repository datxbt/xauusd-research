# strategy_2026 — re-tuning the session breakout for the 2026 regime

Self-contained study. It imports the shared engine from the parent directory
and **modifies nothing there** — the original eight-window strategy, its EA and
its results stand untouched.

```bash
python strategy_2026/optimize_2026.py --scan
python strategy_2026/optimize_2026.py --validate rep2026_1 rep2026_2 rep2026_3 rep2026_4 rep2026_5 regime_2026
python strategy_2026/decompose.py --validate rep2026_1 rep2026_2 rep2026_3 rep2026_4 rep2026_5 regime_2026 --walkforward
```

## Why re-tune at all

The eight windows were chosen on 2024–2025. 2026 is a different market:

| | 2024 | 2025 | 2026 (Jan–Jul) |
|---|---|---|---|
| annualised vol | 15.1% | 19.3% | **32.9%** |
| daily range | 1.38% | 1.72% | **2.81%** |
| avg spread | $0.056 | $0.037 | **$0.091** |
| direction | +27% | +65% | **−7%** |
| mean opening range | $6.11 | $11.60 | **$23.10** |

Bracket width nearly quadrupled. A target multiple tuned when ranges were $6
is making a different bet when ranges are $23.

## The hazard, and the protocol built around it

2026 has **150 real sessions**. Choosing the best of 216 window configs there
will always produce a flattering number. So the protocol was fixed in advance:

- **Select** on real 2026 only.
- **Validate** on block-bootstrapped 2026-regime replicates — same volatility,
  spreads and tick behaviour, different ordering of days. Never used to choose.
- Adopt a candidate **only** if it beats the incumbent on the *median* replicate.

Four candidates, defined before looking: `A_incumbent` (unchanged),
`B_top8` (best 8 hours by 2026 net), `C_stable` (positive in both halves of
2026), `D_pruned` (incumbent minus its 2026 losers).

## Results

**Real 2026** — in-sample for B/C/D, so not evidence:

| candidate | trades | win rate | PF | net |
|---|---|---|---|---|
| A_incumbent | 1,167 | 36.7% | 1.17 | $4,598 |
| B_top8 | 1,149 | 39.8% | **1.55** | **$13,946** |
| C_stable | 1,138 | 42.6% | 1.48 | $11,955 |
| D_pruned | 1,018 | 37.9% | 1.26 | $5,947 |

**Six 2026-regime replicates** — the deciding table:

| candidate | median net | worst | best | median PF | median Sharpe |
|---|---|---|---|---|---|
| B_top8 | **$13,072** | $8,356 | $21,913 | 1.54 | 4.97 |
| C_stable | $10,719 | $7,953 | $18,236 | 1.46 | 4.93 |
| D_pruned | $6,355 | $2,939 | $10,426 | 1.30 | 3.49 |
| A_incumbent | $4,825 | $2,902 | $8,654 | 1.19 | 2.72 |

All three beat the incumbent on 6/6 replicates.

### …but that result is close to circular

Replicate blocks are matched **by time of day**. "Hour 00 was good in 2026" is
baked into every replicate, because an 00:00 block is only ever swapped for
another day's 00:00 block. These runs test robustness to *day ordering*; they
cannot test whether the hour choice was a 150-session fluke. A portfolio
selected by hour will beat the incumbent there almost by construction.

So `decompose.py` splits the re-tune into two claims that fail differently.

## What is durable: geometry

Keep the **incumbent hours**, change only bracket length and target:

| variant | median net | median PF |
|---|---|---|
| G_r60_t3 | **$9,265** | 1.31 |
| G_r30_t3 | $8,321 | 1.32 |
| G_r60_t2 | $7,840 | 1.28 |
| G_r15_t3 | $6,379 | 1.30 |
| A_incumbent | $4,825 | 1.19 |
| G_r30_t1 | $3,388 | 1.16 |
| G_r15_t1 | $1,731 | 1.10 |

Target multiple orders monotonically **t3 > t2 > t1** at every bracket length.
That is a one-parameter, three-level dose-response driven by a mechanism —
higher volatility means more follow-through, so let winners run further. It
involves **no hour selection at all**, and it captures **54%** of the total gain.

This is the part to trust.

## What is fitted: the hour list

`B_top8` = hours **0, 1, 2, 5, 8, 14, 15, 18** (all `t3`, all `r30`/`r60`).
Against the incumbent's 0, 1, 2, 4, 5, 6, 13, 14 — five hours survive; 4, 6, 13
are dropped and 8, 15, 18 added. `h13` was the worst incumbent window in 2026
at **−$9.06/trade**, consistent with it going negative in the earlier study.

A genuine walk-forward inside 2026 — choose on Jan–Apr (84 sessions), score on
May–Jul (66 sessions):

| portfolio | H1 (in-sample) | H2 (out of sample) |
|---|---|---|
| picked on H1 | PF 1.63, $10,416 | **PF 1.39, $3,530** |
| A_incumbent | PF 1.17, $2,980 | PF 1.17, $1,618 |

It shrinks — 1.63 → 1.39 — but still clears the incumbent out of sample. And
selecting on Jan–Apr alone reproduced the full-2026 pick *exactly*, which is
more stability than 84 sessions had any right to give.

Encouraging, not conclusive: 66 out-of-sample sessions.

## Recommendation

**Adopt the geometry change. Treat the hour change as provisional.**

Minimum-regret configuration — incumbent hours, 2026 geometry:

```
h00_r60_t3  h01_r60_t3  h02_r60_t3  h04_r60_t3
h05_r60_t3  h06_r60_t3  h13_r60_t3  h14_r60_t3
```

One parameter changed, backed by a monotone dose-response, no hour selection.
Median $9,265 vs the incumbent's $4,825 across replicates.

If you also accept the hour re-selection (`B_top8`, median $13,072), understand
you are buying the other 46% with a choice validated on 66 genuinely
out-of-sample sessions and a near-circular replicate test.

Either way `h13` should go — it lost money in 2026 on real data, on the
replicates, and in the original 2024–2026 study.

## The EA

`XAUUSD_SessionBreakout_2026.mq5` — a standalone Expert Advisor. The original
`XAUUSD_SessionBreakout.mq5` in the parent directory is untouched; run either,
or both side by side (they use different magic-number ranges).

Pick the portfolio with one input, weakest assumption first:

| `InpPreset` | windows | median net across replicates |
|---|---|---|
| `GEO_2026_NO_H13` *(default)* | 7 | $9,727 |
| `GEO_2026` | 8 — exactly as tested | $9,265 |
| `TOP8_2026` | 8 — 2026 hour re-selection | $13,072 |
| `ORIGINAL` | the 2024–2025 config | $4,825 |

`GEO_2026_NO_H13` is the default because it wins on median P&L, profit factor
(1.38 vs 1.31) and Sharpe (4.25 vs 3.94) — though it beats the 8-window version
on only 3 of 6 replicates, so the margin is real but slim. `TOP8_2026` scores
highest and is the most fitted; the README section above says what you are
buying with it.

Everything else carries over from the fixed original: the stale-position sweep
that stops trades riding a weekend, the Friday cutoff, `InpMaxSpreadUSD` in
USD/oz rather than points, per-side stops-level checks, and the take profit
re-anchored to the actual fill.

### State across re-initialisation

`OnInit` runs far more often than once. A restart, a reconnect, a recompile, an
edit to any input and **every change of chart timeframe** all land there. Two
things follow, and both were wrong until they were fixed:

- The EA now **adopts** whatever is already on the server instead of re-arming
  from scratch. It rebuilds the window from the resting orders — high and low
  from the pending prices, expiry recomputed, `armedDay`/`doneDay` inferred —
  then cancels prior-day leftovers and prunes duplicate stops. Previously each
  re-init placed a second bracket on top of the live one, so flipping
  M1 → M5 → M15 left three copies of the same trade and multiplied size until
  the daily-loss limit tripped.
- The day's opening equity is **persisted** in a terminal global variable keyed
  by `InpMagicBase`, rather than re-sampled. Re-sampling walked the daily-loss
  baseline down with every restart, so the halt measured from the restart rather
  than from the day — which is no protection at all, silently.

`BuildRange`, `LoadPreset` and `ProcessWindow` are untouched, so the entry
geometry is identical to the tested build and every table above still describes
it. Leaving the terminal on one timeframe is no longer necessary; M5 or M15 is
fine, and the EA is tick-driven either way.

## The news-safe variant

`XAUUSD_SessionBreakout_NewsSafe.mq5`. A *funded* FTMO Standard account may not
open or close a position on a USD-targeted instrument within ±2 minutes of six
named US releases, and XAUUSD is targeted. A resting buy stop counts: if price
tags it inside the window that is an opening trade, whenever it was placed. The
Swing account is exempt but costs about 10% more and caps at $25k, which is what
makes an EA-side solution worth building.

Generated from the base EA by anchored substitution, so the geometry is provably
identical and the backtests carry over. Magic base is 8830000, so both can run
side by side. Three sources, because any one can fail alone:

| source | input | what it is |
|---|---|---|
| calendar | `InpUseCalendar` | MQL5 `CalendarValueHistory`, matched on event name and currency |
| schedule | `InpUseSchedule` | fixed `InpNewsScheduleET` = `08:30,14:00`, fallback when the feed is empty |
| file | `InpNewsCsvFile` | offline timestamps — `us_macro_releases.csv` |

Ahead of a window it pulls its pendings and applies `InpNewsPosMode`
(`FLATTEN` / `DETACH_STOPS` / `HOLD`) to any open position, then re-arms
afterwards if the window has not expired. ET→UTC follows **US** DST, not the
broker's EET.

Under `GEO_2026_NO_H13` the 08:30 releases land in the 11:00–15:00 UTC gap
between armed windows and cannot reach an entry; only 14:00 × h14 is exposed,
about 16 days a year.

## Sizing against prop-firm rules

Four simulators, all replaying rule sets over the same per-lot daily
close-and-trough series from every possible start date, so the rates are
frequencies over start dates rather than one path:

| script | question |
|---|---|
| `ftmo_sim.py` | FTMO against E8 — a 10% *static* floor against a 4% *trailing* one |
| `news_sim.py` | what the news filter costs, per blackout scenario |
| `payout_sim.py` | cash actually withdrawn, including E8 Signature's payout caps |
| `prop_sizing.py` | lot ladders under each rule set |

The floor geometry dominates everything else. At the same size FTMO's static
floor contains this strategy's drawdown and E8 Signature's trailing floor does
not — 0.0% funded breach against 47.4%.

On a $100k FTMO 2-Step, ORIGINAL, news filter on, +$0.15/oz, 80% split, 4.5%
daily halt (`results/lot_ladder_full.csv`, 40 sizes, 7 shown):

| lots | maxDD | pass | median days | breach | E[cash] |
|---|---|---|---|---|---|
| 0.10 | 6.32% | 80.0% | 142 | 0.0% | $11,647 |
| 0.11 | 6.95% | 84.2% | 130 | 0.0% | $13,672 |
| **0.12** | **7.58%** | **86.3%** | **122** | **0.0%** | **$15,309** |
| 0.13 | 8.21% | 87.2% | 108 | 29.1% | $14,106 |
| 0.14 | 8.84% | 86.1% | 98 | 34.9% | $14,093 |
| 0.16 | 13.34% | 83.4% | 89 | 43.4% | $15,255 |
| 0.17 | 13.61% | 84.0% | 84 | 43.5% | $16,330 |

**0.13–0.16 are strictly dominated by 0.12** — less expected cash *and* 29–43%
breach. Expected cash does not beat 0.12 again until 0.17. So:

```
InpBaseLots        = 0.12
InpMaxDailyLossPct = 4.5
InpUseVolTargeting = false
```

The halt never fires at 0.12 (worst observed day 4.11%), so it costs nothing and
exists purely as tail insurance. It does not unlock more size: below ~0.145 lots
it never triggers, and above 0.16 it *deepens* max drawdown — 9.47% → 13.34%
across one 0.01 step — by closing days that would partly have recovered.

Two cautions. The news filter is strongly non-linear in size: at 0.02 lots/$10k
it is nearly free (−0.30pp pass, breach unchanged), at 0.03 it pushes funded
breach from 58.0% to 71.8%. And every number here is a frequency over start
dates *inside 2024–2026* — the window the parent README's §5.3 shows does not
replicate. They say how much size this return series tolerates, not whether the
series recurs.

**One expectation to set.** The replicate medians above were measured at a
fixed 0.02 lots. The EA ships with volatility targeting on and the reference
left at 0.816%, so at 2026-level volatility it trades roughly half size and
live P&L scales down with it. That is deliberate risk control, not a different
edge — raising `InpTargetVolPct` raises risk proportionally.

Before running it live, back-test it in the Strategy Tester on **"Every tick
based on real ticks"** and reconcile against `results/`. On Exness set
`InpGMTOffsetHours = 0`; on any other broker verify it first.

## Standing limits

- 150 real sessions. Everything here is thin.
- Replicates reuse 2026 returns; they cannot invent conditions gold has not had.
- This tunes *for* a regime that is forecastable only in volatility, not
  direction — see `regime_analysis.py` in the parent.
- Untested on any other instrument.

## Files

| file | what it does |
|---|---|
| `optimize_2026.py` | selection on real 2026, validation on replicates |
| `decompose.py` | geometry-vs-hours split, walk-forward inside 2026 |
| `results/hour_profile_2026.csv` | every UTC hour's 2026 P&L, split by half-year |
| `results/candidates.json` | the four portfolios, as selected |
| `results/candidates_replicates.csv` | per-replicate scores |
| `results/decompose_replicates.csv` | geometry-variant scores |
| `results/walkforward_2026.csv` | Jan–Apr → May–Jul test |
| `ftmo_sim.py` | FTMO against E8 on one daily series |
| `news_sim.py` | cost of the release filter, per blackout scenario |
| `payout_sim.py` | cash withdrawn from a funded account, incl. payout caps |
| `prop_sizing.py` | lot ladders under each rule set |
| `us_macro_releases.csv` | 109 observed release dates, `YYYY-MM-DD,slot` |
| `results/lot_ladder_full.csv` | 0.01→0.40 lots in 0.01 steps, $100k FTMO 2-Step |
| `results/news_sim.csv` | pass/breach by blackout scenario |
| `results/ftmo_sim.csv` | FTMO 1-Step against 2-Step, three sizes |
| `results/payout_sim.csv` | per-product withdrawal simulation |
