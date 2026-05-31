"""Polymarket WC 2026 backtest — v003.

Part 1 (clean baseline, fixes vs v002.x):
  1. BUY_NO is no longer dead code (separate Kelly leg + share accounting).
  2. No look-ahead in entry decisions (strategies see strictly past data).
  3. Share-based PnL with explicit spread + fee at entry and exit.
  4. Configurable cost model (fee, half-spread by price tier).
  5. Portfolio-level constraints (per-team and gross exposure caps).
  6. Walk-forward harness with Probabilistic / Deflated Sharpe.
  7. Synthetic dataset generator so the engine runs without external files.

Part 2 (six strategy directions on the shared engine):
  D1 strategies_arb         structural arbitrage (Dutch book + normalization)
  D2 strategies_model       model value (ELO -> Poisson -> tournament MC)
  D3 strategies_crossmarket cross-market / closing-line value
  D4 strategies_mm          market making with inventory skew
  D5 strategies_event       event-driven absolute-shock continuation
  D6 portfolio              meta-allocation (equal / inverse-vol / vol-target)

Key cross-cutting lessons encoded here:
  * hold-until-edge-closes (strat_utils.hold_loop) beats daily churn after costs
  * filter the long-tail (min_price) and detect events by ABSOLUTE moves
"""
