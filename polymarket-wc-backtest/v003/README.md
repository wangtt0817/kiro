# v003 — Clean engine + 6 strategy directions + portfolio

A from-scratch rewrite of `backtest_engine_v002.x.py` that (1) fixes the bugs
found in the v002 audit, (2) implements six distinct strategy *directions* on a
shared, correctly-accounted engine, and (3) combines them with a portfolio
layer. It ships a seeded synthetic dataset so everything runs without external
files.

## Why v002 was broken (recap)

| # | Issue | Effect |
|---|---|---|
| 1 | `BUY_NO` fed the wrong `model_prob` into `kelly_size`, which returned 0 | Every NO trade silently dropped → engine was long-only |
| 2 | S1/S5 filtered entries on `price[t+1]` | Look-ahead bias |
| 3 | `odds_implied_prob` was one static snapshot used for 29 days | S2/S9/S10 were the same decision repeated |
| 4 | `np.random.random()` gated signals | Non-reproducible |
| 5 | Sharpe annualized per-trade by `√252` | Wrong; "best of 10" treated as significant |
| 6 | mixed share/notional PnL, only entry fee debited | Cash accounting inconsistent |
| 7 | required a dataset file not in the repo | Couldn't run |

## The engine (`engine.py`)

* Share-based PnL: `shares = stake / effective_fill`, fees at entry **and** exit.
* Separate Kelly per leg — `kelly_fraction_yes = (p_model - p_market)/(1 - p_market)`,
  `kelly_fraction_no = (p_market - p_model)/p_market`. NO trades size and settle.
* Cost model: `taker_fee_rate` + `half_spread(p)` (wider for tail prices).
* Portfolio constraints: per-team and gross exposure caps.
* Raw primitives `enter()` / `close_at_fill()` (explicit fill + stake) power the
  market-making and arbitrage strategies; `open_position()` / `close_position()`
  are mid+Kelly convenience wrappers.
* Drawdown tracked on **equity** (cash + mark-to-market), not cash.

Accounting is locked by `test_engine.py` (12 invariants), including
`Σ trade.pnl == final_capital - initial_capital` for both legs.

## The six directions (`registry.DIRECTIONS`)

| Code | Direction | File | Idea | Edge source |
|------|-----------|------|------|-------------|
| D1 | Structural arb | `strategies_arb.py` | Strict Dutch-book detector + book-normalization mean reversion | book should sum to $1 |
| D2 | **Model value** | `strategies_model.py` | Tournament Monte-Carlo champion prob vs PM mid | fundamentals the price ignores |
| D3 | Cross-market / CLV | `strategies_crossmarket.py` | Trade PM toward a **daily** de-vigged sharp line; track CLV | PM lags sharp books |
| D4 | Market making | `strategies_mm.py` | Two-sided quoting with inventory skew (A-S lite) | spread capture |
| D5 | Event-driven | `strategies_event.py` | Absolute price-shock continuation | under-reaction to news |

The model layer (D2) is the real alpha source because it injects information the
price series doesn't contain:

* `ratings.py` — ELO ratings → expected goals.
* `poisson.py` — double-Poisson + Dixon-Coles low-score correction → match
  outcome probabilities (memoized).
* `tournament.py` — group stage (draws) + knockout (penalties) Monte Carlo →
  championship probabilities.

## Two design lessons baked into the strategies

1. **Hold, don't churn.** Closing and reopening a small-edge position every day
   re-pays the spread daily and turns a real edge into a loss. `strat_utils.hold_loop`
   enters on a strong edge (`entry_threshold`) and holds until it closes
   (`exit_threshold`), so spread cost scales with *conviction changes*, not days.
   This single change moved D2/D3 from deeply negative to net-positive after costs.
2. **Avoid the long-tail trap.** A 0.5c spread on a 0.10 contract is a 5% round
   trip; on a 0.02 long-shot it's >100%. Strategies apply a `min_price` filter,
   and event detection uses **absolute** moves (a 2c jump is several σ above the
   daily noise floor) rather than relative ones.

## Portfolio layer (`portfolio.py`)

`combine(series_by_strategy, method)` aligns the per-strategy dated PnL, computes
`equal` / `inverse_vol` / `vol_target` weights, and reports Sharpe, Probabilistic
Sharpe, drawdown, and the **correlation matrix**. The runner combines only the
deployable directions (legacy strategies are shown in the metrics table as a
cautionary contrast — they churn daily and bleed under realistic costs).

> Caveat: `inverse_vol` over-weights near-zero-vol strategies (the arb book), and
> `vol_target` then needs large leverage to hit the target. `equal` is the sane
> default for display.

## Quick start

```bash
# All strategies + equal-weight portfolio on synthetic data
python run.py --synthetic

# Just the new directions, 3-fold walk-forward, vol-targeted portfolio
python run.py --synthetic --group directions --walk-forward 3 --portfolio vol_target

# Stress with a 2% taker fee
python run.py --synthetic --fee 0.02

# Real data (v002 JSON layout; enriched fields used if present)
python run.py --data ~/polymarket-wc-backtest/data/clean/wc2026_full_dataset.json \
    --group directions
```

The runner prints: full-sample metrics (with direction + PSR/Deflated-Sharpe),
per-strategy diagnostics (Dutch-arb days, model top-5, average CLV, event
signals), an optional walk-forward table, and the portfolio summary with its
correlation matrix.

## Statistics (`walkforward.py`)

* Walk-forward folds (`--walk-forward N`).
* **Probabilistic Sharpe** `PSR(SR*=0)` and **Deflated Sharpe** vs
  `n_trials = #strategies tested`, so the "best of N" Sharpe stops looking
  significant on ~60 daily observations. On the synthetic sample DSR ≈ 0 for
  every strategy — by design: the honest message is *you cannot prove
  significance from 60 days and 9 candidates*, no matter how good the in-sample
  Sharpe looks.

## Files

```
v003/
├── engine.py                 # share-based engine, cost model, caps, raw primitives
├── ratings.py                # ELO ratings -> expected goals
├── poisson.py                # double-Poisson + Dixon-Coles match model
├── tournament.py             # group + knockout Monte Carlo -> champion probs
├── synth_data.py             # seeded dataset: ratings, p_true, odds_series, events
├── strat_utils.py            # price helpers + hold_loop (entry/exit hysteresis)
├── strategies.py             # legacy-clean S2/S3/S5/S6 (no look-ahead/random)
├── strategies_arb.py         # D1 structural arbitrage
├── strategies_model.py       # D2 model value
├── strategies_crossmarket.py # D3 cross-market / CLV
├── strategies_mm.py          # D4 market making
├── strategies_event.py       # D5 event-driven
├── portfolio.py              # D6 meta-allocation + correlations
├── registry.py               # single source of truth for all strategies
├── run.py                    # CLI runner
├── test_engine.py            # 12 accounting invariants
├── test_models.py            # 13 model-layer tests
├── test_strategies_ext.py    # 11 strategy + portfolio tests
└── README.md                 # this file
```

## Honest limitations

* Single horizon per position (no trailing stop / scale-out yet).
* Slippage proportional to order size needs real orderbook depth (not in the
  v002 schema); the cost model leaves a hook.
* The synthetic data is *internally consistent* (the model that generates
  `p_true` is the model D2 uses), so D2/D3 look strong there. On real data the
  model is only as good as the ratings fed into it — the synthetic results
  prove the plumbing, not a live edge.
* Results are tiny in $ because the synthetic mispricings are small; the point
  is sign, turnover, and correlation structure, not the headline ROI.
```
