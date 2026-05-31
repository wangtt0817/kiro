#!/usr/bin/env python3
"""
Direction 6 — Combined Net-Inventory Hedged Book  (A + B + skew on ONE book).

Thesis
------
A real market-making desk does not run "the spread strategy" and "the rebate strategy"
as two separate bots that each hedge their own leg (paying slippage twice). It runs ONE
quoting book whose P&L is:

        spread/settlement PnL  +  liquidity rebate  -  ONE shared hedge cost  -  gas

Direction 6 turns on every feature of the shared engine simultaneously:
  * spread capture            (like Direction 1)
  * liquidity-reward accrual  (like Direction 2)
  * directional skew          (like Direction 5)
  * a single net-inventory TokenBook hedged by a single HedgeBook

This is the most realistic representation of the business and the natural end-state if
Directions 1/2/5 each prove additive. Comparing D6 to the individual legs shows whether
the combination is synergistic (shared hedge saves cost) or whether the legs fight each
other (tighter rebate quoting increases adverse selection the skew must then offset).
"""

from __future__ import annotations

import random
from typing import List

from mm_core import SimConfig, simulate_window, WindowResult, compute_metrics
from mm_engine import MarketMakerConfig, run_mm_window

NAME = "D6 Combined book"


def make_config() -> MarketMakerConfig:
    return MarketMakerConfig(
        base_size=10.0,
        inv_cap=80.0,
        k_vol=0.6,
        k_inv=0.02,
        buffer_fee_gas=0.003,      # between D1 (0.005) and D2 (0.002)
        gamma_inv=0.015,
        # single shared book; continuous perp hedge OFF (short-gamma finding).
        # The "shared hedge account" benefit is realised by NETTING the spread and
        # rebate legs onto ONE inventory (one flatten, one set of fees) rather than
        # running two bots that each hedge separately.
        hedge=False,
        delta_band_btc=0.0004,
        hedge_slippage_bps=1.0,
        gas_per_hedge=0.002,
        tau_safe_s=60.0,
        # rebate leg
        rebate=True,
        reward_pool_per_window=2.0,
        reward_max_spread_c=3.0,
        size_cap=100.0,
        competitor_score=4000.0,
        # directional skew leg
        signal_skew=True,
        skew_gain=0.6,
        skew_clip=0.03,
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


if __name__ == "__main__":
    import json
    m = compute_metrics(NAME, run())
    print(json.dumps(m, indent=2))
    print(f"\nleg breakdown:  spread={m['spread_pnl']}  rebate={m['rebate_pnl']}  "
          f"hedge={m['hedge_pnl']}  gas/fees={m['fee_gas']}")
