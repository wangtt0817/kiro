#!/usr/bin/env python3
"""
Direction 2 — Rebate Farming (liquidity-reward optimised).  [fixed Strategy B]

Thesis
------
Polymarket Liquidity Rewards are the primary P&L. Quote tight to the mid to maximise
the reward score q(d)=max(0,1-(d/max_spread)^2); accept that the trading leg may bleed a
little, because the rebate more than covers it. Hedge the resulting inventory.

What this fixes vs strategy_b.py
--------------------------------
* The rebate is ACTUALLY booked. strategy_b defined calculate_rebate() but never called
  it inside any loop, so rebate_pnl stayed 0 — i.e. its core income source contributed
  nothing. Here we accrue reward score every second the quote rests and convert it to a
  cash reward at window close via my_share = my_score / (my_score + competitor_score).
* Hedging is wired in (strategy_b had a delta_band in config but no hedge engine at all).
* Competition is modelled as an exogenous aggregate competitor_score instead of the
  circular `total_score = my_score * 10`.

Because we quote tighter (to score rewards) we take MORE adverse selection than
Direction 1 — the rebate has to pay for that. This direction shows whether it does.
"""

from __future__ import annotations

import random
from typing import List

from mm_core import SimConfig, simulate_window, WindowResult, compute_metrics
from mm_engine import MarketMakerConfig, run_mm_window

NAME = "D2 Rebate farm"


def make_config() -> MarketMakerConfig:
    return MarketMakerConfig(
        base_size=10.0,
        inv_cap=80.0,             # rebate subsidy lets us carry more inventory
        k_vol=0.6,
        k_inv=0.02,
        buffer_fee_gas=0.002,     # quote TIGHTER than D1 to score rewards
        gamma_inv=0.015,
        # continuous perp hedge OFF (see Direction 1 finding: digitals are short gamma).
        # Tighter quoting here means MORE adverse selection than D1 -> the rebate must
        # pay for it. Inventory risk is controlled by skew + tau_safe flatten.
        hedge=False,
        delta_band_btc=0.0003,    # used only if hedge is re-enabled
        hedge_slippage_bps=1.0,
        gas_per_hedge=0.002,
        tau_safe_s=60.0,
        rebate=True,
        reward_pool_per_window=2.0,
        reward_max_spread_c=3.0,
        size_cap=100.0,
        competitor_score=4000.0,
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


if __name__ == "__main__":
    import json
    res = run()
    m = compute_metrics(NAME, res)
    print(json.dumps(m, indent=2))
    print(f"\nrebate as share of gross: "
          f"{m['rebate_pnl']:.2f} of total {m['total_pnl']:.2f}")
