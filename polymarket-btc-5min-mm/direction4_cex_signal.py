#!/usr/bin/env python3
"""
Direction 4 — Oracle-Lag / CEX-Momentum SIGNAL module (NOT a standalone arb).

Position taken in the analysis
------------------------------
Pure "oracle-lag" arbitrage (see CEX tick up -> instantly buy Up before Chainlink
updates) is a sub-100ms latency race against co-located bots. We deliberately do NOT
play that game. Instead the CEX momentum is kept as ONE confirmation dimension that
Direction 3 (directional edge) and Direction 5 (MM + skew) consume.

This module therefore provides:
  * the three Layer-2 confirmation signals from the design doc
        - CEX spot momentum            (weight 0.30)
        - Polymarket tick velocity     (weight 0.35)
        - order-book imbalance         (weight 0.35)
  * a fused confirmation score in [-1, 1]
  * a demonstration that a NAIVE oracle-lag taker, once you add realistic execution
    latency + taker spread, gives back its edge — which is exactly why we relegate CEX
    momentum to a confirmation signal rather than a standalone strategy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Tuple

from mm_core import (
    SimConfig, simulate_window, MarketWindow, WindowResult, compute_metrics,
)

NAME = "D4 CEX-lag taker (naive)"

# signal weights from the design doc
W_OB, W_VEL, W_CEX = 0.35, 0.35, 0.30


@dataclass
class Signal:
    direction: int      # +1 bullish (Up), -1 bearish (Down), 0 neutral
    confidence: float   # 0..1


def cex_momentum_signal(w: MarketWindow, t: int,
                        win_s: int = 10, thresh: float = 0.0003) -> Signal:
    """CEX spot momentum vs the recent past and vs the window open."""
    if t < win_s:
        return Signal(0, 0.0)
    mom = (w.S[t] - w.S[t - win_s]) / w.S[t - win_s]
    vs_open = (w.S[t] - w.K) / w.K
    if abs(mom) < thresh:
        return Signal(0, 0.0)
    direction = 1 if mom > 0 else -1
    conf = min(1.0, abs(mom) / (thresh * 6))
    if (mom > 0) != (vs_open > 0):     # momentum disagrees with level => discount
        conf *= 0.7
    return Signal(direction, conf)


def tick_velocity_signal(w: MarketWindow, t: int,
                         win_s: int = 30, thresh: float = 0.01) -> Signal:
    """Polymarket mid velocity — following informed flow already in the book."""
    if t < win_s:
        return Signal(0, 0.0)
    vel = (w.mkt_mid[t] - w.mkt_mid[t - win_s])
    if abs(vel) < thresh:
        return Signal(0, 0.0)
    return Signal(1 if vel > 0 else -1, min(1.0, abs(vel) / (thresh * 5)))


def orderbook_imbalance_signal(w: MarketWindow, t: int,
                               rng: random.Random | None = None) -> Signal:
    """Synthetic order-book imbalance.

    In the live system this comes from top-of-book bid/ask volumes. In simulation we
    proxy it from the gap between fresh fair value and the (stale) market mid plus
    noise — i.e. real buying pressure tends to appear where the book is mispriced.
    """
    gap = w.fair[t] - w.mkt_mid[t]
    noise = (rng.gauss(0.0, 0.15) if rng else 0.0)
    imb = max(-1.0, min(1.0, gap * 8.0 + noise))
    if abs(imb) < 0.30:
        return Signal(0, 0.0)
    return Signal(1 if imb > 0 else -1, min(1.0, abs(imb)))


def confirmation_score(w: MarketWindow, t: int, rng: random.Random | None = None) -> float:
    """Fuse the three signals into a signed score in [-1, 1]."""
    ob = orderbook_imbalance_signal(w, t, rng)
    vel = tick_velocity_signal(w, t)
    cex = cex_momentum_signal(w, t)
    return (W_OB * ob.direction * ob.confidence
            + W_VEL * vel.direction * vel.confidence
            + W_CEX * cex.direction * cex.confidence)


# ------------------------------------------------------------------
# Demonstration: why naive oracle-lag is NOT pursued as a standalone
# ------------------------------------------------------------------
def run(n_windows: int = 3000, seed: int = 7,
        sim_cfg: SimConfig | None = None,
        latency_s: int = 0, taker_cost: float = 0.0) -> List[WindowResult]:
    """A naive oracle-lag taker.

    At a fixed decision time it buys the side that CEX momentum points to, paying the
    market's best ask/bid (taker). `latency_s` shifts our information back in time and
    `taker_cost` adds the spread we cross — turning the knobs that a real co-location
    race would impose. With latency/cost > 0 the edge evaporates, which is the point.
    """
    sim_cfg = sim_cfg or SimConfig()
    rng = random.Random(seed)
    results: List[WindowResult] = []
    decision_t = 150
    size = 10.0

    for _ in range(n_windows):
        w = simulate_window(sim_cfg, rng)
        t_info = max(0, decision_t - latency_s)
        sig = cex_momentum_signal(w, t_info)
        if sig.direction == 0:
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        if sig.direction > 0:   # buy Up at the ask (+ latency/cost penalty)
            entry = min(0.99, w.best_ask[decision_t] + taker_cost)
            payoff = 1.0 if w.settle_up else 0.0
            pnl = size * (payoff - entry)
            won = w.settle_up
        else:                   # buy Down == sell Up at the bid
            entry = max(0.01, w.best_bid[decision_t] - taker_cost)
            payoff = 1.0 if w.settle_up else 0.0
            pnl = size * (entry - payoff)
            won = not w.settle_up

        results.append(WindowResult(pnl=pnl, spread_pnl=pnl, traded=True,
                                    won=won, edge=abs(w.fair[decision_t] - w.mkt_mid[decision_t])))
    return results


if __name__ == "__main__":
    import json
    print("Naive oracle-lag taker under increasing realism (latency + taker cost):\n")
    scenarios = [
        ("perfect (0 latency, 0 cost)", 0, 0.0),
        ("+2s latency",                 2, 0.0),
        ("+2s latency, 1c taker cost",  2, 0.01),
        ("+5s latency, 2c taker cost",  5, 0.02),
    ]
    rows = []
    for label, lat, cost in scenarios:
        res = run(latency_s=lat, taker_cost=cost)
        m = compute_metrics(f"{NAME} [{label}]", res)
        rows.append(m)
        print(f"{label:32s} win%={m['win_rate']:5.1f}  PnL={m['total_pnl']:9.1f}  "
              f"Sharpe={m['sharpe']:5.2f}")
    print("\nConclusion: edge collapses once latency + taker cost are realistic ->")
    print("CEX momentum is used only as a CONFIRMATION signal (Layer 2), not a strategy.")
