# xauusd-research

Strategy research on XAUUSD (spot gold) built directly on Exness raw tick data
— roughly 176 million ticks spanning 2024-01 to 2026-07 — plus `tick_synth`, a
synthetic tick generator for testing whether a result is an edge or a draw.

Every study here follows the same protocol, and the protocol is the point.

## The protocol

```
TRAIN    2024-01 .. 2025-06   (18 months)  — search here, freely
HOLDOUT  2025-07 .. 2026-06   (12 months)  — opened ONCE, for one config
```

A hypothesis is written down **before** any result is inspected, together with
the bar it has to clear: a minimum trade count, and a bootstrap confidence
interval on mean session P&L that lies wholly above zero. Only the single best
TRAIN candidate is scored out of sample. If nothing clears the bar on TRAIN,
the holdout stays shut and the answer is "no".

The full parameter grid is always reported, not just the winning cell, so the
family-level result stays visible rather than the luckiest member of it.

Costs are the real bid/ask spread carried in the tick data, plus commission.
Fills pay the correct side of the book.

## Strategies

**Opening range breakout** ([backtest_orb.py](backtest_orb.py)) — bracket the
high/low of the first R minutes after a session open, trade the first break,
stop at the opposite side, target a multiple of the range width. The prior is
microstructural: overnight information accumulates in thin liquidity and gets
repriced in a burst when a major session opens.

**VWAP pullback** ([backtest_vwap_pullback.py](backtest_vwap_pullback.py)) —
the session VWAP resets daily; when a 5-minute close stretches a threshold
away from it, fade the move back toward VWAP. Entry on the next bar's open,
target the (still moving) VWAP, stop beyond the entry.

## Layout

| path | what it is |
|---|---|
| [backtest_orb.py](backtest_orb.py) | pre-registered ORB test, 18-config grid |
| [backtest_vwap_pullback.py](backtest_vwap_pullback.py) | VWAP pullback backtester + the shared tick loader |
| [search_strategy.py](search_strategy.py) | hypothesis-driven search for a deployable VWAP variant |
| [session_context.py](session_context.py) | per-session character table (trend/range, vol, spread) to join onto trades |
| [analyze_vwap_results.py](analyze_vwap_results.py) | which circumstances actually pay — expectancy by hour, weekday, regime |
| [optimize_time_windows.py](optimize_time_windows.py) | 216-window ORB sweep (24 hours × 3 ranges × 3 targets), scored on both periods |
| [regime_analysis.py](regime_analysis.py) | what each year looked like, which characteristics persist, and a projection for the ones that do |
| [optimize_for_regime.py](optimize_for_regime.py) | conditions the ORB portfolio on volatility — the only forecastable input |
| [run_synth_backtest.py](run_synth_backtest.py) | runs the 8-window ORB portfolio across synthetic replicates |
| [XAUUSD_SessionBreakout.mq5](XAUUSD_SessionBreakout.mq5) | MT5 EA: the 8-window portfolio, live |
| [tick_synth/](tick_synth/) | synthetic tick generator — see its own [README](tick_synth/README.md) |

Results live in `orb_results/`, `vwap_pullback_results/`, `regime_results/`
and `spread_stats/`, and are committed as the research record.

## What regime_analysis established

This one result shapes everything downstream, so it belongs up front:

| forecastable (AR1 0.60–0.87) | not forecastable (AR1 ≤ 0.12) |
|---|---|
| realized volatility, range, spread, tick count | direction, trendiness, VWAP crosses |

So volatility level is the only legitimate thing to condition position sizing
or window selection on. The EA sizes inversely to trailing realized volatility
for exactly this reason. Anything that conditions on predicted *direction* is
fitting noise.

## tick_synth

One history is one draw, and the session-level bootstrap in these scripts
resamples sessions — it cannot resample the tape itself. `tick_synth` builds
synthetic tick files in the exact Exness dialect and directory layout, so any
backtest here reads them with no changes:

```bash
python backtest_orb.py --data-dir tick_synth/output/rep00 --outdir orb_results/rep00
```

Two uses. **Alternative histories** (whole-day blocks) give the spread of
outcomes an edge could have had. **A null tape** (short blocks) keeps real
costs, tick spacing and volatility but destroys multi-hour directional
structure — a breakout edge should largely die there, and if it doesn't, the
P&L is coming from something other than the stated mechanism.

Full method, validation numbers and gotchas: [tick_synth/README.md](tick_synth/README.md).

## Data

The tick data is **not** in this repository — it is ~19 GB of vendor data, and
the generated tapes and caches add ~64 GB more. All of it is gitignored.
Expected layout:

```
Monthly_Tick_Data/<YYYY>/Exness_XAUUSD_Raw_Spread_<YYYY>_<MM>/*.csv
```

```
"Exness","Symbol","Timestamp","Bid","Ask"
"exness","XAUUSD_Raw_Spread","2025-03-02 23:05:00.071Z",2873.618,2873.655
```

Synthetic runs are reproducible rather than stored: every output directory
carries a `manifest.json` recording the method, seed, source range and every
knob, so a deleted tape can be regenerated byte-for-byte.

## Requirements

Python 3.11+, `numpy`, `pandas`, `scipy`, `matplotlib`. The MT5 EA needs
MetaTrader 5 — read its header before running, `InpGMTOffsetHours` must match
your broker's server clock or it trades the wrong hours entirely.

## A caveat worth keeping

Synthetic tapes measure dispersion and robustness. They are not for
discovering an edge, and no parameter should ever be fitted on them.
