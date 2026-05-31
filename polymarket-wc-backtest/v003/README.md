# v003 — Clean Baseline

This folder is a from-scratch rewrite of `backtest_engine_v002.x.py` that fixes
the bugs found during the v002 audit and ships a working synthetic dataset so
the engine actually runs without external files.

## What was wrong in v002

| # | Issue | Effect |
|---|---|---|
| 1 | `BUY_NO` branch passed `model_prob = entry - edge` into `kelly_size`, which always returned 0 | Every NO trade was silently dropped → engine was effectively long-only |
| 2 | S1 and S5 filtered entries on `exit_price = price[date_idx + 1]` | Look-ahead bias; "edge" was conditioned on tomorrow already being good |
| 3 | `odds_implied_prob` was a single static snapshot used as a constant for 29 days | S2 / S9 / S10 were the same decision repeated; only ~1 effective signal |
| 4 | Several strategies gated entries on `np.random.random()` | Non-reproducible, mixes signal with noise |
| 5 | Sharpe annualized per-trade PnL by `√252` | Annualization factor wrong because trades are not daily |
| 6 | One-line `raw_pnl = size * (exit - entry) / entry` | OK numerically for YES but obscures share accounting and breaks for NO |
| 7 | No selection-bias correction | "Best of 10" Sharpe reported as if it were a single test |
| 8 | Required `~/polymarket-wc-backtest/data/clean/wc2026_full_dataset.json` | File not in repo; backtest can't run as shipped |

## What v003 changes

* **Engine** (`engine.py`)
  * Share-based PnL: `shares = stake / effective_fill_price`, fees realized at entry and exit.
  * Separate Kelly fractions for YES (`(p_model - p_market) / (1 - p_market)`) and NO (`(p_market - p_model) / p_market`). NO trades now actually size and settle.
  * Cost model split into `taker_fee_rate` + `half_spread(p)`, with a wider half-spread for tail prices (`p < 0.05`).
  * Portfolio constraints: per-team and total gross exposure caps.
  * Mark-to-market function so equity curves are sampled daily, not per-trade.
* **Strategies** (`strategies.py`)
  * Four reference strategies, all deterministic, all using strictly past data:
    `S2_CrossPlatform`, `S3_Momentum`, `S5_MeanReversion`, `S6_LongTail`.
  * `S2` de-vigs the bookmaker probabilities before comparing to PM mid (subtle but important).
  * Strategies emit both YES and NO orders and the engine respects them.
* **Walk-forward** (`walkforward.py`)
  * `n_splits` consecutive folds; metrics computed per fold.
  * Probabilistic Sharpe (`PSR(SR* = 0)`) and Deflated Sharpe (against `n_trials = number of strategies tested`) so we stop pretending the "best of 10" Sharpe is significant.
* **Synthetic dataset** (`synth_data.py`)
  * Seeded Dirichlet championship probabilities + OU drift + bookmaker overround.
  * Same JSON schema as the v002 file, so the same reshape works for real data later.

## Quick start

```bash
# Run all strategies on synthetic data
python -m polymarket-wc-backtest.v003.run --synthetic

# Walk-forward, write JSON output
python -m polymarket-wc-backtest.v003.run --synthetic --walk-forward 3 \
    --output /tmp/v003_results.json

# Real data (legacy v002 JSON layout)
python -m polymarket-wc-backtest.v003.run \
    --data ~/polymarket-wc-backtest/data/clean/wc2026_full_dataset.json \
    --walk-forward 3
```

The runner prints (1) the full-sample table, (2) a walk-forward aggregate
table when `--walk-forward > 1`, and (3) a sanity line confirming both
`BUY_YES` and `BUY_NO` trades were executed.

## How to migrate a v002 strategy

1. Copy the relevant function from `backtest_engine_v002.2.py`.
2. Remove any `np.random.random()` gates — replace with explicit thresholds.
3. Replace the in-loop `exit_price = get_price_at_index(..., date_idx + 1, ...)`
   inside the entry filter with a *post-decision* close call (`engine.close_position`).
4. Use `engine.open_position(strategy, team, DIR_YES_or_NO, mid, model_prob, date, horizon)`
   instead of `execute_trade`.
5. Register the new function in `strategies.REGISTRY`.

## Known limitations

* Single horizon per strategy (default 1 day). A position-management layer that
  trails stops or scales out is straightforward to add but out of scope here.
* Slippage proportional to order size requires real orderbook depth which is
  not part of the v002 schema. The cost model leaves a hook for it.
* The synthetic dataset is intentionally "easy": de-vigged consensus is close
  to the true probability, so `S2_CrossPlatform` should look like the strongest
  strategy in synthetic results. On real data it almost certainly won't.

## Files

```
v003/
├── __init__.py        # package marker + change summary
├── engine.py          # share-based engine, cost model, portfolio constraints
├── strategies.py      # four reference strategies (no look-ahead, no random)
├── walkforward.py     # walk-forward + PSR / Deflated Sharpe
├── synth_data.py      # seeded synthetic WC dataset (no external file)
├── run.py             # CLI entry point
└── README.md          # this file
```
