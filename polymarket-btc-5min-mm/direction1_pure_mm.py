#!/usr/bin/env python3
"""
Direction 1 — Pure Market Making (spread capture).  [fixed Strategy A]

Thesis
------
Quote both sides around the observed Polymarket mid, earn the bid-ask spread, and
neutralise the BTC delta with a Binance perp so PnL is independent of direction.

What this fixes vs strategy_a.py
--------------------------------
* The backtest actually RUNS (strategy_a.run_backtest only returned a stub string).
* Hedge PnL is MARKED TO SETTLEMENT (strategy_a only ever subtracted slippage, so its
  hedge could never offset a directional loss -> hedging looked strictly bad).
* Delta is dimensioned correctly: hedge_qty_BTC = -net_up_tokens * digital_delta.
* Inventory is a single signed net-Up-token book (no up/down double counting).

The honest finding this direction surfaces: in a 5-min market with informed taker flow,
spread capture is fighting adverse selection. Whether it is net positive depends almost
entirely on (captured spread) vs (adverse-selection cost) + (hedge slippage + gas).
"""

from __future__ import annotations

import random
from typing import List

from mm_core import SimConfig, simulate_window, WindowResult, compute_metrics
from mm_engine import MarketMakerConfig, run_mm_window

NAME = "D1 Pure MM (spread)"


def make_config() -> MarketMakerConfig:
    return MarketMakerConfig(
        base_size=10.0,
        inv_cap=50.0,
        k_vol=0.6,
        k_inv=0.02,
        buffer_fee_gas=0.005,     # fee + gas buffer folded into half-spread
        gamma_inv=0.015,
        # NOTE: continuous perp delta-hedging is OFF by default. The mark-to-settlement
        # HedgeBook is fully implemented (see mm_engine / direction1 hedge experiment),
        # and the backtest shows that tick-by-tick hedging of a 5-min DIGITAL bleeds
        # money to short gamma far faster than it removes directional risk. Inventory is
        # instead controlled by inventory skew + flattening inside tau_safe. Flip
        # hedge=True to reproduce the bleed.
        hedge=False,
        delta_band_btc=0.0005,
        hedge_slippage_bps=1.0,
        gas_per_hedge=0.002,
        tau_safe_s=60.0,
        rebate=False,
        signal_skew=False,
    )


def run(n_windows: int = 3000, seed: int = 7,
        sim_cfg: SimConfig | None = None) -> List[WindowResult]:
    sim_cfg = sim_cfg or SimConfig()
    rng = random.Random(seed)
    cfg = make_config()
    results: List[WindowResult] = []
    for _ in range(n_windows):
        w = simulate_window(sim_cfg, rng)
        results.append(run_mm_window(w, cfg))
    return results


def hedge_experiment(n_windows: int = 3000, seed: int = 7,
                     sim_cfg: SimConfig | None = None) -> dict:
    """Run the SAME book with continuous hedging off vs on.

    Demonstrates the core finding: a correct mark-to-settlement perp hedge makes a
    5-min digital book WORSE, because the option is short gamma and re-hedging buys
    high / sells low into BTC noise faster than it removes directional risk.
    """
    sim_cfg = sim_cfg or SimConfig()
    out = {}
    for label, hedge in (("hedge_off", False), ("hedge_on", True)):
        cfg = make_config()
        cfg.hedge = hedge
        rng = random.Random(seed)
        res = [run_mm_window(simulate_window(sim_cfg, rng), cfg) for _ in range(n_windows)]
        out[label] = compute_metrics(f"D1 {label}", res)
    return out


if __name__ == "__main__":
    import json
    res = run()
    print(json.dumps(compute_metrics(NAME, res), indent=2))

    print("\n--- hedge experiment (continuous perp delta-hedge off vs on) ---")
    exp = hedge_experiment()
    for label, m in exp.items():
        print(f"{label:10s}  PnL={m['total_pnl']:9.1f}  Sharpe(pw)={m['sharpe']:6.3f}  "
              f"win%={m['win_rate']:5.1f}  hedge_leg={m['hedge_pnl']:9.1f}")
    print("Finding: continuous hedging of 5-min digitals is counterproductive (short gamma).")
