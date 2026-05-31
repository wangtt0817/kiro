"""Polymarket WC 2026 backtest — v003 (clean baseline).

Fixes vs v002.x:
  1. BUY_NO is no longer dead code (separate Kelly leg + share accounting).
  2. No look-ahead in entry decisions (strategies see strictly past data).
  3. Share-based PnL (shares = stake / effective_price), with explicit
     spread + fee at entry and exit.
  4. Configurable cost model (fee, half-spread by price tier).
  5. Portfolio-level constraints (per-team and gross exposure caps).
  6. Walk-forward harness with Probabilistic / Deflated Sharpe.
  7. Synthetic dataset generator so the engine runs without external files.
"""
