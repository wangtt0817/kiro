#!/usr/bin/env python3
"""
run_all_directions.py — Run all six strategy directions on the SAME synthetic market
and print a single comparison table + per-leg P&L attribution.

Usage:
    python run_all_directions.py                # default 3000 windows
    python run_all_directions.py 8000 123       # n_windows seed

All directions share one SimConfig so the comparison is apples-to-apples. Remember:
these are SYNTHETIC results meant to validate the LOGIC and relative ranking of the
directions, not to forecast live P&L. Replace mm_core.simulate_window with a real
historical replay to get tradeable numbers.
"""

from __future__ import annotations

import sys
import json

from mm_core import SimConfig, compute_metrics, format_metrics_table

import direction1_pure_mm as d1
import direction2_rebate_farm as d2
import direction3_directional_edge as d3
import direction4_cex_signal as d4
import direction5_mm_skew as d5
import direction6_combined_book as d6


def main() -> None:
    n_windows = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    sim_cfg = SimConfig()

    print(f"Polymarket BTC 5-min — 6-direction backtest")
    print(f"windows={n_windows}  seed={seed}  sigma={sim_cfg.sigma_annual}  "
          f"mkt_lag={sim_cfg.mkt_lag_s}s  taker_informed={sim_cfg.taker_informed}\n")

    runs = [
        (d1.NAME, d1.run(n_windows, seed, sim_cfg)),
        (d2.NAME, d2.run(n_windows, seed, sim_cfg)),
        (d3.NAME, d3.run(n_windows, seed, sim_cfg)),
        # D4 standalone is the naive oracle-lag taker WITH realistic latency+cost,
        # shown to demonstrate it is not viable on its own.
        (d4.NAME + " [+2s,1c]", d4.run(n_windows, seed, sim_cfg, latency_s=2, taker_cost=0.01)),
        (d5.NAME, d5.run(n_windows, seed, sim_cfg)),
        (d6.NAME, d6.run(n_windows, seed, sim_cfg)),
    ]

    rows = [compute_metrics(name, res) for name, res in runs]
    print(format_metrics_table(rows))

    print("\nPer-leg P&L attribution ($):")
    print(f"  {'Direction':<26}{'spread':>10}{'rebate':>10}{'hedge':>10}{'fee/gas':>10}")
    for r in rows:
        print(f"  {r['name']:<26}{r['spread_pnl']:>10.1f}{r['rebate_pnl']:>10.1f}"
              f"{r['hedge_pnl']:>10.1f}{r['fee_gas']:>10.1f}")

    # machine-readable dump
    with open("results_all_directions.json", "w") as f:
        json.dump(rows, f, indent=2)
    print("\nWrote results_all_directions.json")


if __name__ == "__main__":
    main()
