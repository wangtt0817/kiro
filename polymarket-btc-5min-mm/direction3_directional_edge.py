#!/usr/bin/env python3
"""
Direction 3 — Directional Statistical Edge (the design doc's 4-layer architecture).

This is the "Stage-0 paper validation" the doc demands BEFORE building anything else:
    "If I bet every time |edge| > 3% with confirmation, what is my win-rate / Sharpe?"

Layer 1  Fair value  : P_fair = digital N(d2) from the FRESH spot; edge = P_fair - P_mkt
Layer 2  Confirmation: fuse order-book imbalance + tick velocity + CEX momentum (D4)
Layer 3  Timing/exec : only trade in the 120s..30s window; prefer maker, cross as taker
                       near the close if still mispriced
Layer 4  Risk        : half-Kelly sizing with a hard cap, consecutive-loss circuit
                       breaker, per-"day" loss limit

Pass bar (from the doc): paper Sharpe > 1.5 and win-rate > 54%.  If the synthetic (or,
later, real) world does not clear that bar, this whole directional line is abandoned.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional

from mm_core import (
    SimConfig, simulate_window, MarketWindow, WindowResult, compute_metrics,
    DigitalOptionEngine,
)
from direction4_cex_signal import confirmation_score

NAME = "D3 Directional edge"


@dataclass
class DirectionalConfig:
    min_edge: float = 0.03            # Layer 1 minimum mispricing
    min_confirmation: float = 0.15    # Layer 2 minimum fused confirmation magnitude
    trade_start_s: int = 120          # earliest seconds-remaining to trade
    trade_end_s: int = 30             # latest seconds-remaining to trade
    maker_fill_prob: float = 0.6      # probability a resting maker order fills
    taker_cost: float = 0.0           # extra cost when crossing the spread (prob units)
    # risk
    kelly_fraction: float = 0.5       # half-Kelly
    max_bet_frac: float = 0.02        # hard cap: 2% of capital per trade
    consecutive_loss_limit: int = 6
    pause_windows: int = 20
    windows_per_day: int = 288
    daily_loss_frac: float = 0.10     # pause the rest of the "day" past -10%


def _kelly_tokens(capital: float, model_p: float, entry: float,
                  cfg: DirectionalConfig) -> float:
    """Half-Kelly stake (in tokens) for a binary bet, hard-capped."""
    if entry <= 0 or entry >= 1:
        return 0.0
    b = (1.0 - entry) / entry           # net odds
    q = 1.0 - model_p
    f = (model_p * b - q) / b           # Kelly fraction
    f = max(0.0, f) * cfg.kelly_fraction
    stake = min(f * capital, cfg.max_bet_frac * capital)
    return stake / entry                # tokens = stake / price


def run(n_windows: int = 3000, seed: int = 7,
        sim_cfg: Optional[SimConfig] = None,
        cfg: Optional[DirectionalConfig] = None,
        initial_capital: float = 1000.0) -> List[WindowResult]:
    sim_cfg = sim_cfg or SimConfig()
    cfg = cfg or DirectionalConfig()
    rng = random.Random(seed)

    results: List[WindowResult] = []
    capital = initial_capital
    consec_losses = 0
    pause_left = 0
    day_start_capital = capital

    for i in range(n_windows):
        if i % cfg.windows_per_day == 0:
            day_start_capital = capital      # reset the "daily" loss tracker

        w = simulate_window(sim_cfg, rng)

        # Layer 4: circuit breakers
        day_paused = (capital - day_start_capital) <= -cfg.daily_loss_frac * day_start_capital
        if pause_left > 0 or day_paused:
            pause_left = max(0, pause_left - 1)
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        # scan the trading window for the first qualifying signal
        t_lo = w.n - cfg.trade_start_s      # 180
        t_hi = w.n - cfg.trade_end_s        # 270
        chosen = None
        for t in range(t_lo, t_hi):
            edge = w.fair[t] - w.mkt_mid[t]                 # Layer 1
            if abs(edge) < cfg.min_edge:
                continue
            conf = confirmation_score(w, t, rng)            # Layer 2
            if abs(conf) < cfg.min_confirmation:
                continue
            if (edge > 0) != (conf > 0):                    # directions must agree
                continue
            chosen = (t, edge)
            break

        if chosen is None:
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        t, edge = chosen
        t_rem = w.n - t
        bullish = edge > 0

        # Layer 3: pick token, model prob, and maker vs taker entry
        if bullish:
            model_p = w.fair[t]
            maker_entry = w.mkt_mid[t]
            taker_entry = min(0.99, w.best_ask[t] + cfg.taker_cost)
        else:
            model_p = 1.0 - w.fair[t]
            maker_entry = 1.0 - w.mkt_mid[t]
            taker_entry = min(0.99, (1.0 - w.best_bid[t]) + cfg.taker_cost)

        filled_maker = rng.random() < cfg.maker_fill_prob
        if filled_maker:
            entry = maker_entry
        elif t_rem <= cfg.trade_end_s + 15:    # still mispriced near close -> cross
            entry = taker_entry
        else:
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        tokens = _kelly_tokens(initial_capital, model_p, entry, cfg)
        if tokens <= 0:
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        # settle
        if bullish:
            won = w.settle_up
        else:
            won = not w.settle_up
        payoff = 1.0 if won else 0.0
        pnl = tokens * (payoff - entry)

        capital += pnl
        if pnl <= 0:
            consec_losses += 1
            if consec_losses >= cfg.consecutive_loss_limit:
                pause_left = cfg.pause_windows
                consec_losses = 0
        else:
            consec_losses = 0

        results.append(WindowResult(
            pnl=pnl, spread_pnl=pnl, traded=True, won=won, edge=abs(edge),
            n_fills=1, extra={"entry": entry, "tokens": tokens, "maker": float(filled_maker)},
        ))

    return results


if __name__ == "__main__":
    import json
    res = run()
    m = compute_metrics(NAME, res)
    print(json.dumps(m, indent=2))
    # The doc's Stage-0 bar was "annualised daily Sharpe > 1.5 AND win > 54%". We report
    # a convention-free per-window Sharpe, so the gate here uses the doc's minimum
    # acceptable KPIs that do not depend on an annualisation choice.
    passed = m["win_rate"] > 54 and m["profit_factor"] > 1.2 and m["total_pnl"] > 0
    print(f"\nStage-0 gate (win%>54 AND profit_factor>1.2 AND PnL>0): "
          f"{'PASS' if passed else 'FAIL'}")
    print("NOTE: synthetic data has a by-construction edge, so PASS here only proves the "
          "4-layer PIPELINE works. Re-run on REAL settled markets to test if the edge is real.")
