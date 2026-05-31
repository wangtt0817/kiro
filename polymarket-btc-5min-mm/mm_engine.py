#!/usr/bin/env python3
"""
mm_engine.py — One configurable market-making engine that powers the MM-flavoured
directions (1 pure-MM, 2 rebate-farm, 5 MM+skew, 6 combined book).

A single engine with feature flags keeps the four MM directions honest and
comparable: they differ ONLY by which knobs are turned on, which is exactly the
point the analysis wants to make.

Feature flags
-------------
hedge        : run the perp delta hedge with mark-to-settlement accounting
rebate       : accrue Polymarket liquidity-reward score while quoting
signal_skew  : lean the quote centre toward the fresh CEX fair value (Direction 5)

The per-second fill model uses the informed taker flow from the synthetic window, so
makers get adversely selected unless their spread / skew protects them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from mm_core import (
    DigitalOptionEngine, MarketWindow, MMConfig, WindowResult,
    TokenBook, HedgeBook, hedge_target_qty, mm_half_spread, WINDOW_SECONDS,
)


@dataclass
class MarketMakerConfig(MMConfig):
    # --- hedging ---
    hedge: bool = True
    delta_band_btc: float = 0.0005      # rehedge threshold (BTC)
    hedge_slippage_bps: float = 1.0
    max_delta_per_token: float = 0.02   # clamp the digital delta (gamma explodes at ATM expiry)

    # --- expiry risk control ---
    tau_safe_s: float = 60.0            # stop quoting + freeze the hedge inside this window
    unwind_near_expiry: bool = True     # flatten inventory once inside tau_safe
    unwind_cost: float = 0.005          # cost (prob units) to flatten the token book

    # --- rebate farming ---
    rebate: bool = False
    reward_pool_per_window: float = 2.0  # $ liquidity reward available this window
    reward_max_spread_c: float = 3.0     # max scoring distance from mid (cents)
    size_cap: float = 100.0
    competitor_score: float = 4000.0     # aggregate score of all other MMs (per window)

    # --- directional skew (Direction 5) ---
    signal_skew: bool = False
    skew_gain: float = 0.6               # how hard to lean toward fresh fair
    skew_clip: float = 0.03              # max lean (prob units)


def _reward_score(price: float, size: float, adj_mid: float,
                  max_spread_c: float, size_cap: float, dt: float) -> float:
    """Polymarket-style order score: q(d)=max(0,1-(d/max_spread)^2), d in cents."""
    d_c = abs(price - adj_mid) * 100.0
    if d_c > max_spread_c:
        return 0.0
    q = max(0.0, 1.0 - (d_c / max_spread_c) ** 2)
    return min(size, size_cap) * q * dt


def run_mm_window(w: MarketWindow, cfg: MarketMakerConfig) -> WindowResult:
    """Run the market maker across a single 5-minute window."""
    book = TokenBook()
    hedge = HedgeBook(slippage_bps=cfg.hedge_slippage_bps)
    my_reward_score = 0.0
    n_fills = 0
    gas = 0.0
    unwound = False

    for t in range(w.n):
        t_rem = w.t_remaining(t)
        T_rem_yr = w.t_remaining_years(t)
        observed_mid = w.mkt_mid[t]

        # --- expiry risk control: stop quoting & freeze hedge inside tau_safe ---
        # A digital option's delta -> infinity at-the-money as T -> 0, so a perp hedge
        # becomes impossible (the gamma blow-up the design doc warns about). Real desks
        # pull quotes and freeze/flatten near expiry instead of chasing delta.
        if t_rem < cfg.tau_safe_s:
            if cfg.unwind_near_expiry and not unwound:
                # flatten the token book at the mid (paying a small taker cost) and
                # flatten the hedge, then hold flat to settlement.
                if abs(book.tokens) > 1e-9:
                    if book.tokens > 0:
                        book.trade("sell", max(0.01, observed_mid - cfg.unwind_cost), book.tokens)
                    else:
                        book.trade("buy", min(0.99, observed_mid + cfg.unwind_cost), -book.tokens)
                if cfg.hedge:
                    hedge.rehedge_to(0.0, w.S[t])
                    gas += cfg.gas_per_hedge
                unwound = True
            continue

        # --- quote centre ---
        quote_center = observed_mid
        if cfg.signal_skew:
            # lean toward the fresh CEX-implied fair value (Direction 5's whole idea)
            fresh_fair = w.fair[t]
            lean = cfg.skew_gain * (fresh_fair - observed_mid)
            lean = max(-cfg.skew_clip, min(cfg.skew_clip, lean))
            quote_center = observed_mid + lean

        # --- spread + inventory skew ---
        hs = mm_half_spread(cfg, t_rem, w.sigma, book.tokens)
        inv_skew = cfg.gamma_inv * (book.tokens / cfg.inv_cap)

        bid_price = max(0.01, min(0.99, quote_center - hs - inv_skew))
        ask_price = max(0.01, min(0.99, quote_center + hs - inv_skew))

        inv_ratio = book.tokens / cfg.inv_cap
        bid_size = max(0.0, cfg.base_size * (1.0 - inv_ratio))
        ask_size = max(0.0, cfg.base_size * (1.0 + inv_ratio))

        # --- fills against informed taker flow ---
        # a sell-taker hits the best bid; we get it if our bid is at/inside top of book
        if w.taker_sell[t] and bid_size > 0 and bid_price >= w.best_bid[t] - 1e-9:
            fill = min(bid_size, w.taker_size[t])
            book.trade("buy", bid_price, fill)
            n_fills += 1
        # a buy-taker lifts the best ask; we get it if our ask is at/inside top of book
        if w.taker_buy[t] and ask_size > 0 and ask_price <= w.best_ask[t] + 1e-9:
            fill = min(ask_size, w.taker_size[t])
            book.trade("sell", ask_price, fill)
            n_fills += 1

        # --- delta hedge (mark-to-settlement) ---
        if cfg.hedge:
            delta = DigitalOptionEngine.delta(w.S[t], w.K, T_rem_yr, w.sigma)
            delta = max(-cfg.max_delta_per_token, min(cfg.max_delta_per_token, delta))
            target = hedge_target_qty(book.tokens, delta)
            band = cfg.delta_band_btc * max(0.2, t_rem / WINDOW_SECONDS)
            if abs(target - hedge.qty) > band:
                hedge.rehedge_to(target, w.S[t])
                gas += cfg.gas_per_hedge

        # --- rebate score accrual ---
        if cfg.rebate:
            adj_mid = quote_center
            if bid_size > 0:
                my_reward_score += _reward_score(bid_price, bid_size, adj_mid,
                                                  cfg.reward_max_spread_c, cfg.size_cap, 1.0)
            if ask_size > 0:
                my_reward_score += _reward_score(ask_price, ask_size, adj_mid,
                                                  cfg.reward_max_spread_c, cfg.size_cap, 1.0)

    # --- settle ---
    token_pnl = book.settle(w.settle_up)
    hedge_pnl = hedge.close(w.settle_price) if cfg.hedge else 0.0

    rebate_pnl = 0.0
    if cfg.rebate and my_reward_score > 0:
        share = my_reward_score / (my_reward_score + cfg.competitor_score)
        rebate_pnl = share * cfg.reward_pool_per_window

    total = token_pnl + hedge_pnl + rebate_pnl - gas

    return WindowResult(
        pnl=total,
        spread_pnl=token_pnl,
        rebate_pnl=rebate_pnl,
        hedge_pnl=hedge_pnl,
        fee_gas=gas,
        traded=(n_fills > 0),
        edge=0.0,
        n_fills=n_fills,
        n_hedges=hedge.n_hedges,
        extra={
            "end_inventory": book.tokens,
            "reward_score": my_reward_score,
        },
    )


__all__ = ["MarketMakerConfig", "run_mm_window"]
