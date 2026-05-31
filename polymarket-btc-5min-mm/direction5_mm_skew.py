#!/usr/bin/env python3
"""
Direction 5 — Market Making + Directional Skew  (hybrid, the target state).

Thesis
------
Run Direction 1's market maker as the base (collect spread, hedge delta), but instead
of quoting symmetrically around the STALE observed mid, lean the quote centre toward the
FRESH CEX-implied fair value. Two benefits at once:

  1. Reduces adverse selection — we stop quoting a bid that is too high (or an ask that
     is too low) when the fresh price says the market is about to move.
  2. Adds directional alpha — the inventory we accumulate is tilted the *right* way,
     so the leftover (unhedged) directional exposure is, on average, profitable.

It is literally Direction 1 with `signal_skew=True`; comparing D5 vs D1 isolates the
value of the Layer-1/Layer-2 signal applied to a maker book rather than as taker bets.
"""

from __future__ import annotations

import random
from typing import List

from mm_core import SimConfig, simulate_window, WindowResult, compute_metrics
from mm_engine import MarketMakerConfig, run_mm_window

NAME = "D5 MM + skew"


def make_config() -> MarketMakerConfig:
    return MarketMakerConfig(
        base_size=10.0,
        inv_cap=50.0,
        k_vol=0.6,
        k_inv=0.02,
        buffer_fee_gas=0.005,
        gamma_inv=0.015,
        # continuous perp hedge OFF (digitals are short gamma — see Direction 1).
        # Inventory is steered the RIGHT way by the directional skew below, then
        # flattened inside tau_safe.
        hedge=False,
        delta_band_btc=0.0005,
        hedge_slippage_bps=1.0,
        gas_per_hedge=0.002,
        tau_safe_s=60.0,
        rebate=False,
        signal_skew=True,          # <-- the only change vs Direction 1
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
    print(json.dumps(compute_metrics(NAME, run()), indent=2))
