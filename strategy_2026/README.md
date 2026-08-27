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
