# tick_synth — synthetic XAUUSD tick data for backtesting

Builds synthetic tick files out of the real monthly tick data in
`Monthly_Tick_Data/`. Output is written in the **exact** Exness CSV dialect
and directory layout, so every existing script reads it with no changes:

```bash
python backtest_orb.py --data-dir tick_synth/output/rep00 --outdir orb_results/rep00
```

```
"Exness","Symbol","Timestamp","Bid","Ask"
"exness","XAUUSD_Raw_Spread","2025-04-01 00:00:00.274Z",2873.658,2873.695
```

## Why

One history is one draw. Every number in `orb_results/` and
`vwap_pullback_results/` is a single realisation, and the pre-registered
CI in those scripts resamples *sessions* — it cannot resample the tape
itself. This folder gives you two things that history alone cannot:

1. **Alternative histories** — many tapes with the same microstructure, so an
   edge can be scored against the spread of outcomes it *could* have had.
2. **A null tape** — a market with real costs, real tick spacing and real
   volatility, but with the multi-hour directional structure deliberately
   removed. A breakout or pullback edge should largely die there. If it
   doesn't, the P&L is coming from something other than the stated mechanism.

## Files

| file | what it does |
|---|---|
| `common.py` | tick format, month discovery, the CSV writer |
| `tickprofile.py` | one pass over the real tape → a ~130 KB statistical profile |
| `daycache.py` | one pass over the real tape → a compact npz per calendar day |
| `synth.py` | the generator (`--method block` / `--method parametric`) |
| `validate.py` | real vs synthetic on the stats a backtest depends on |
| `plots.py` | the same comparison as charts, plus one day at tick resolution |

## Quick start

```bash
python tick_synth/daycache.py --from-month 2024-01 --to-month 2026-07
```

```bash
python tick_synth/synth.py --method block --months 2025-01:2025-12 --out tick_synth/output/rep00 --seed 0
```

```bash
python tick_synth/validate.py --real Monthly_Tick_Data --synth tick_synth/output/rep00 --real-from 2025-01 --real-to 2025-12
```

Ten replicates, then the ORB backtest on each:

```bash
for i in $(seq 0 9); do python tick_synth/synth.py --method block --months 2024-01:2026-06 --out tick_synth/output/rep$i --seed $i; done
```

## Charts

```bash
python tick_synth/plots.py --synth tick_synth/output/rep00 --real Monthly_Tick_Data --real-from 2025-01 --real-to 2025-12 --outdir tick_synth/charts/rep00
```

Six PNGs, real drawn in black on top of every run:

* `price_paths.png` — daily close, and the same paths rebased to 100. Pass
  several `--synth` directories and the second panel becomes the fan of
  histories the strategy could have faced.
* `hourly.png` — ticks and spread by hour of day, with 07:00 and 13:00 UTC
  marked. This is the session shape the breakout strategies depend on.
* `returns.png` — 5-minute return density on a log axis, and a QQ plot against
  real. The QQ plot is where a generator's tails are caught: `block` sits on
  the diagonal, `parametric` bends away past ±15 bp.
* `acf.png` — autocorrelation of returns (near zero) and of |returns|
  (volatility clustering) out to 30 bars.
* `daily.png` — daily range and daily |move| distributions.
* `intraday.png` — one whole day tick-by-tick: mid, spread, ticks per minute.
  Defaults to the middle day of the first run, or pass `--intraday-day
  2025-04-16`.

`--real` is optional; without it you get the synthetic run on its own and no
QQ panel.

## Method 1: `block` — block bootstrap of the real tape

Each synthetic day is stitched from blocks of **real** ticks taken from real
days at the same time of day and (by default) the same weekday. A block
contributes its log **returns**, inter-arrival times and spreads — never its
price level — so the spliced path is continuous and every seam carries a
return that actually happened. Needs `daycache.py` to have run first.

`--block-minutes` is the knob that decides what a run *means*:

* **`1440` (default, whole days)** — alternative histories. Within-day
  structure is fully intact; only which day happened when is resampled.
  These are the runs to use for a confidence interval on an edge you have
  already found.
* **`5`–`60`** — a null world. Structure longer than the block is destroyed
  by construction. Run the strategy here and most of the edge should vanish.

Other knobs: `--any-weekday` (draw from any weekday instead of the matching
one), `--source-from/--source-to` (restrict the pool to one regime — pair this
with `regime_analysis.py`), `--vol-mult`, `--spread-mult`.

## Method 2: `parametric` — simulate from the profile

No real ticks are reused. Arrivals are an inhomogeneous Poisson process at
the empirical per-minute-of-week rate; per-tick returns are drawn from the
empirical standardised-innovation distribution, given the real short-horizon
trendiness by an AR(1) calibrated to the measured 5-minute variance ratio,
and scaled by per-minute-of-week volatility times an AR(1) log-vol factor;
spreads come from the empirical per-hour-of-week distribution through a
Gaussian copula so they stay persistent instead of flickering.

```bash
python tick_synth/tickprofile.py --out tick_synth/profiles/xauusd.npz
python tick_synth/synth.py --method parametric --months 2025-01:2025-12 --out tick_synth/output/stress_wide --profile tick_synth/profiles/xauusd.npz --spread-mult 2.0 --vol-mult 1.5 --intensity-mult 0.5
```

This is the method for stress tests — a wider book, a faster or thinner
tape, higher volatility — because those knobs are free of any real path.
`--vol-mult` scales *realised* volatility, so thinning the tape with
`--intensity-mult` does not quietly shrink it as well.

## What survives, and what does not

Measured on a 11-day sample (`validate.py`, synth/real ratio):

| | block, 1440 | block, 30 min | parametric |
|---|---|---|---|
| ticks/day | 1.00 | 1.04 | 1.05 |
| spread mean / median / p90 | 1.00 | 1.00 | 1.00 |
| 5-min return sd | 0.98 | 1.02 | 0.97 |
| 5-min kurtosis | 1.10 | 1.04 | **2.11** |
| acf(&#124;r&#124;) lag 1 — vol clustering | 1.08 | 0.98 | 0.99 |
| daily range | 1.02 | 1.10 | 1.09 |

Read that as: both block modes reproduce the tape closely. The parametric
tape is right on liquidity, costs and volatility but runs **fatter-tailed**
at the 5-minute horizon and drifts more over a day, because its AR(1)
trendiness applies at every horizon while the real market mean-reverts at
longer ones. Use `parametric` for cost and volatility sensitivity; use
`block` for anything where the shape of the day matters.

Nothing here reproduces: news events landing on scheduled dates, the real
sequence of regimes, or any cross-asset relationship. A synthetic tape is
for measuring **dispersion and robustness**, never for discovering an edge.
Do not fit parameters on it.

## Costs

| | per real month |
|---|---|
| `daycache.py` | ~3 s per 2 M ticks, ~4 bytes/tick on disk (~700 MB for 2024–2026) |
| `tickprofile.py` | ~3 s per 2 M ticks, 130 KB total |
| `synth.py` | ~10 s and ~335 MB per synthetic month |

335 MB per month is the real constraint: ten replicate years is ~40 GB.
Generate a run, back-test it, delete it — `manifest.json` in every output
directory records the method, the seed, the source range and every knob, so
any run can be reproduced exactly rather than kept.

## Gotchas

* The pool only knows weekdays it has seen. Saturdays are absent from the
  source, so no synthetic Saturday is written — the weekend closure is
  preserved automatically.
* Price levels are re-anchored to the real month's opening price at the start
  of every month (`--reanchor none` to let the level drift freely). Only the
  level is touched; log returns are untouched.
* The `TRAIN` / `HOLDOUT` month ranges are hardcoded inside `backtest_orb.py`
  and `backtest_vwap_pullback.py`, so generate the same calendar you want to
  test — a synthetic `2024-01:2026-06` splits exactly the way the real one does.
* **Give every run its own `--cache-dir`.** `load_subbars` keys the sub-bar
  cache on the file *stem* only, and a synthetic `2025-04` has the same stem
  as the real one — so pointing `backtest_orb.py`, `backtest_vwap_pullback.py`
  or `validate.py` at a synthetic directory while reusing the real run's cache
  silently back-tests the real data. Use `--cache-dir cache/rep00`, or leave
  it off entirely.
