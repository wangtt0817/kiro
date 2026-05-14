"""
Cost Model — dynamically compute the *true* cost of a round trip,
so the strategy never opens a position whose target profit can't cover
real-world execution costs.

Why this matters
================
The naive view: "4 Maker orders × 0.005% = 0.02% cost, target 0.05%, done."
The real view: that 0.02% ignores
  - bid/ask spread slippage on both legs (open + close)
  - probability of one leg falling back to Taker (0.15%) on close
  - funding rate cost during hold time (8h+ holds)
  - oracle / price-drift cost on a Perp DEX
  - safety margin for tail events

This module measures those costs from live trading data and exposes a
single number: the minimum gross profit the round must aim for to be
worth taking.

Usage
=====
    cost_model = CostModel(config)
    cost_model.record_trade_slippage(actual_pnl_pct, expected_pnl_pct)
    cost_model.update_funding_rates(btc_fr, eth_fr)

    # Before opening a new round:
    target = cost_model.required_target_profit(
        direction="long_eth_short_btc",
        expected_hold_seconds=600,
    )
    if signal.deviation < target:
        skip()  # Not enough edge
    else:
        open_position()
"""

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Deque

logger = logging.getLogger(__name__)


@dataclass
class TradeCostRecord:
    """One round's actual realized costs, used to refine future estimates."""
    timestamp: float
    expected_gross_pnl_pct: float   # What we projected at exit signal
    actual_gross_pnl_pct: float     # What we actually got (before fees)
    fees_paid_pct: float            # Maker + any Taker actually paid
    funding_paid_pct: float         # Net funding cost during hold
    forced_taker: bool              # Did one leg require Taker fallback?
    hold_seconds: float


class CostModel:
    """
    Computes the minimum required gross profit for a round to be worth taking.

    Maintains a rolling window of recent trades to learn actual slippage,
    rather than assuming a fixed value. This adapts to the venue's real
    behavior over time.
    """

    def __init__(
        self,
        maker_fee: float,
        taker_fee: float,
        default_slippage: float,
        default_oracle_drift: float,
        taker_fallback_probability: float,
        safety_margin_multiplier: float,
        min_target_profit: float,
        max_target_profit: float,
        slippage_window_size: int = 50,
    ):
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.default_slippage = default_slippage
        self.default_oracle_drift = default_oracle_drift
        self.taker_fallback_probability = taker_fallback_probability
        self.safety_margin_multiplier = safety_margin_multiplier
        self.min_target_profit = min_target_profit
        self.max_target_profit = max_target_profit

        # Rolling window of realized slippage for dynamic estimation
        self._slippage_history: Deque[float] = deque(maxlen=slippage_window_size)
        self._taker_fallback_history: Deque[bool] = deque(maxlen=slippage_window_size)

        # Latest funding rates (updated by trader on each tick)
        self._btc_funding_rate: float = 0.0
        self._eth_funding_rate: float = 0.0
        self._funding_interval_seconds: float = 8 * 3600  # 8 hours

    # ============================================================
    # Cost Components (each returns a decimal, e.g. 0.0002 = 0.02%)
    # ============================================================

    def fee_cost(self) -> float:
        """
        Expected fee cost for one round-trip (4 orders).

        Accounts for the probability that one close leg is forced to Taker
        when Maker fails 5 times.

        Best case (all Maker):
            4 × 0.005% = 0.02%
        Worst case (one Taker fallback on close):
            3 × 0.005% + 1 × 0.15% = 0.165%

        Expected cost = best × (1-p) + worst × p
        """
        best_case = 4 * self.maker_fee
        worst_case = 3 * self.maker_fee + 1 * self.taker_fee

        # Use empirical fallback rate if we have enough data
        if len(self._taker_fallback_history) >= 10:
            empirical_p = sum(self._taker_fallback_history) / len(self._taker_fallback_history)
            p = empirical_p
        else:
            p = self.taker_fallback_probability

        expected = best_case * (1 - p) + worst_case * p
        return expected

    def slippage_cost(self) -> float:
        """
        Expected slippage cost for one round-trip (open 2 legs + close 2 legs).

        Uses the rolling average of past trades' slippage if available.
        Falls back to the configured default for the first N trades.
        """
        if len(self._slippage_history) >= 10:
            avg = sum(self._slippage_history) / len(self._slippage_history)
            # Use max(avg, default) to avoid being too optimistic if early
            # trades happened to have low slippage
            return max(avg, self.default_slippage * 0.5)
        return self.default_slippage

    def funding_cost(self, direction: str, hold_seconds: float) -> float:
        """
        Expected funding cost for the given direction and hold time.

        Funding is settled every 8 hours. If we hold less than that, no
        funding is paid. If we hold longer, we pay (or receive) per
        settlement period crossed.

        For a pair trade (long one PERP + short the other):
            - Long leg PAYS funding when rate is positive
            - Short leg RECEIVES funding when rate is positive

        Net cost depends on direction:
          long_eth_short_btc: pay ETH funding, receive BTC funding
          short_eth_long_btc: receive ETH funding, pay BTC funding

        Returns POSITIVE = cost (we pay), NEGATIVE = income (we receive).
        """
        # Number of funding settlements crossed during expected hold
        settlements = max(0, int(hold_seconds / self._funding_interval_seconds))

        if settlements == 0:
            return 0.0  # Closed before any funding settlement

        if direction == "long_eth_short_btc":
            # Long ETH pays eth_fr, Short BTC receives btc_fr
            net_per_settlement = self._eth_funding_rate - self._btc_funding_rate
        elif direction == "short_eth_long_btc":
            # Short ETH receives eth_fr, Long BTC pays btc_fr
            net_per_settlement = self._btc_funding_rate - self._eth_funding_rate
        else:
            return 0.0

        return net_per_settlement * settlements

    def oracle_cost(self) -> float:
        """
        Expected price drift due to oracle update lag on the DEX.
        For now use a fixed estimate; could be measured empirically later.
        """
        return self.default_oracle_drift

    # ============================================================
    # Combined Target
    # ============================================================

    def required_target_profit(
        self,
        direction: str = "long_eth_short_btc",
        expected_hold_seconds: float = 600,
    ) -> float:
        """
        Minimum gross profit (as decimal, e.g. 0.001 = 0.10%) the round
        must aim for to be worth opening.

        Returns a floor: the actual exit can happen earlier if conditions
        improve, but the entry decision must use this number.
        """
        fee = self.fee_cost()
        slip = self.slippage_cost()
        funding = self.funding_cost(direction, expected_hold_seconds)
        oracle = self.oracle_cost()

        # Funding can be NEGATIVE (income), which lowers required target.
        # But cap it at 0 — never let funding income reduce target below
        # the basic execution cost; real funding can flip mid-trade.
        funding_for_target = max(0.0, funding)

        base_cost = fee + slip + funding_for_target + oracle

        # Apply safety margin
        target = base_cost * self.safety_margin_multiplier

        # Clamp to [min, max]
        target = max(target, self.min_target_profit)
        target = min(target, self.max_target_profit)

        return target

    def cost_breakdown(
        self,
        direction: str = "long_eth_short_btc",
        expected_hold_seconds: float = 600,
    ) -> dict:
        """Return a detailed breakdown for logging / monitoring."""
        fee = self.fee_cost()
        slip = self.slippage_cost()
        funding = self.funding_cost(direction, expected_hold_seconds)
        oracle = self.oracle_cost()
        target = self.required_target_profit(direction, expected_hold_seconds)

        return {
            "fee_pct": fee * 100,
            "slippage_pct": slip * 100,
            "funding_pct": funding * 100,
            "oracle_pct": oracle * 100,
            "base_cost_pct": (fee + slip + max(0, funding) + oracle) * 100,
            "safety_margin": self.safety_margin_multiplier,
            "required_target_pct": target * 100,
            "samples_collected": len(self._slippage_history),
        }

    # ============================================================
    # Learning From Real Trades
    # ============================================================

    def record_trade(self, record: TradeCostRecord):
        """
        Update the rolling estimates from an actual completed trade.

        Slippage is computed as the gap between what we expected and
        what we actually got, after subtracting fees and funding.
        """
        # Slippage = expected - actual (pre-fee, pre-funding)
        # If we expected 0.10% gross and got 0.08% gross, slippage is 0.02%
        slippage = record.expected_gross_pnl_pct - record.actual_gross_pnl_pct
        slippage = max(0.0, slippage)  # Never negative; positive surprise isn't slippage

        self._slippage_history.append(slippage)
        self._taker_fallback_history.append(record.forced_taker)

        if len(self._slippage_history) % 10 == 0:
            avg_slip = sum(self._slippage_history) / len(self._slippage_history)
            taker_rate = sum(self._taker_fallback_history) / len(self._taker_fallback_history)
            logger.info(
                f"CostModel updated: avg_slippage={avg_slip*100:.4f}%, "
                f"taker_fallback_rate={taker_rate*100:.1f}% over "
                f"{len(self._slippage_history)} trades"
            )

    def update_funding_rates(self, btc_funding_rate: float, eth_funding_rate: float):
        """Update latest known funding rates (per 8-hour period, decimal)."""
        self._btc_funding_rate = btc_funding_rate
        self._eth_funding_rate = eth_funding_rate

    # ============================================================
    # Stats
    # ============================================================

    def get_stats(self) -> dict:
        return {
            "slippage_samples": len(self._slippage_history),
            "avg_slippage_pct": (
                sum(self._slippage_history) / len(self._slippage_history) * 100
                if self._slippage_history else None
            ),
            "taker_fallback_rate": (
                sum(self._taker_fallback_history) / len(self._taker_fallback_history)
                if self._taker_fallback_history else None
            ),
            "btc_funding_rate_pct": self._btc_funding_rate * 100,
            "eth_funding_rate_pct": self._eth_funding_rate * 100,
        }
