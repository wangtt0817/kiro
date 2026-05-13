"""
Risk Manager - Controls daily loss limits, consecutive losses, and margin usage.
"""

import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """Record of a completed trade round."""
    pnl: float               # Profit/loss in USDT
    pnl_pct: float           # Profit/loss as percentage
    fees_paid: float         # Total fees paid
    open_time: float         # Timestamp when opened
    close_time: float        # Timestamp when closed
    hold_duration: float     # Seconds held
    direction: str           # "long_eth_short_btc" or "short_eth_long_btc"
    layers: int              # Number of layers used
    forced_close: bool       # Whether it was force-closed (timeout/stoploss)


class RiskManager:
    """
    Manages risk constraints for the high-frequency pair trading strategy.
    
    Controls:
    - Daily P&L limits
    - Consecutive loss handling
    - Margin utilization
    - Position count limits
    """

    def __init__(
        self,
        initial_equity: float,
        max_daily_loss_pct: float = 0.01,
        max_consecutive_losses: int = 5,
        pause_after_consecutive_loss: int = 1800,
        hourly_loss_pause_pct: float = 0.005,
        hourly_pause_duration: int = 3600,
        max_margin_usage: float = 0.60,
        max_layers: int = 3,
    ):
        self.initial_equity = initial_equity
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.pause_after_consecutive_loss = pause_after_consecutive_loss
        self.hourly_loss_pause_pct = hourly_loss_pause_pct
        self.hourly_pause_duration = hourly_pause_duration
        self.max_margin_usage = max_margin_usage
        self.max_layers = max_layers

        # State
        self._trades: List[TradeResult] = []
        self._daily_pnl: float = 0.0
        self._hourly_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._current_layers: int = 0
        self._pause_until: float = 0.0
        self._day_start: float = time.time()
        self._hour_start: float = time.time()
        self._total_pnl: float = 0.0
        self._total_volume: float = 0.0
        self._total_trades: int = 0

    @property
    def is_paused(self) -> bool:
        """Whether trading is currently paused due to risk limits."""
        return time.time() < self._pause_until

    @property
    def pause_remaining(self) -> float:
        """Seconds remaining in pause."""
        return max(0, self._pause_until - time.time())

    @property
    def can_open_new(self) -> bool:
        """Whether a new position layer can be opened."""
        if self.is_paused:
            return False
        if self._current_layers >= self.max_layers:
            return False
        return True

    def check_margin(self, current_margin_usage: float) -> bool:
        """Check if margin usage is within limits."""
        return current_margin_usage < self.max_margin_usage

    def can_add_layer(self, signal_strength: int) -> bool:
        """
        Check if we can add another layer based on current state and signal strength.
        
        Args:
            signal_strength: 1, 2, or 3
        """
        if not self.can_open_new:
            return False
        # Only open layer N if signal strength >= N
        return signal_strength > self._current_layers

    def on_layer_opened(self):
        """Called when a new layer is successfully opened."""
        self._current_layers += 1
        logger.info(f"Layer opened. Current layers: {self._current_layers}")

    def on_layer_closed(self):
        """Called when a layer is closed."""
        self._current_layers = max(0, self._current_layers - 1)

    def on_all_closed(self):
        """Called when all layers are closed."""
        self._current_layers = 0

    def record_trade(self, result: TradeResult):
        """
        Record a completed trade and check risk limits.
        """
        self._trades.append(result)
        self._total_trades += 1
        self._total_pnl += result.pnl
        self._total_volume += abs(result.pnl / result.pnl_pct) if result.pnl_pct != 0 else 0

        # Update daily P&L
        self._check_day_reset()
        self._daily_pnl += result.pnl

        # Update hourly P&L
        self._check_hour_reset()
        self._hourly_pnl += result.pnl

        # Track consecutive losses
        if result.pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # Check limits
        self._check_limits()

        logger.info(
            f"Trade recorded: PnL={result.pnl:.4f} USDT ({result.pnl_pct*100:.4f}%) | "
            f"Daily PnL={self._daily_pnl:.4f} | "
            f"Consecutive losses={self._consecutive_losses} | "
            f"Total trades={self._total_trades}"
        )

    def _check_limits(self):
        """Check all risk limits and pause if needed."""
        
        # Daily loss limit
        daily_loss_pct = abs(self._daily_pnl) / self.initial_equity
        if self._daily_pnl < 0 and daily_loss_pct >= self.max_daily_loss_pct:
            # Stop for the rest of the day
            seconds_until_midnight = self._seconds_until_next_day()
            self._pause_until = time.time() + seconds_until_midnight
            logger.warning(
                f"DAILY LOSS LIMIT HIT: {daily_loss_pct*100:.2f}% | "
                f"Pausing until next day ({seconds_until_midnight/3600:.1f}h)"
            )
            return

        # Hourly loss limit
        hourly_loss_pct = abs(self._hourly_pnl) / self.initial_equity
        if self._hourly_pnl < 0 and hourly_loss_pct >= self.hourly_loss_pause_pct:
            self._pause_until = time.time() + self.hourly_pause_duration
            logger.warning(
                f"HOURLY LOSS LIMIT HIT: {hourly_loss_pct*100:.2f}% | "
                f"Pausing for {self.hourly_pause_duration/60:.0f} min"
            )
            return

        # Consecutive losses
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._pause_until = time.time() + self.pause_after_consecutive_loss
            logger.warning(
                f"CONSECUTIVE LOSSES: {self._consecutive_losses} | "
                f"Pausing for {self.pause_after_consecutive_loss/60:.0f} min"
            )
            self._consecutive_losses = 0  # Reset after pause
            return

    def _check_day_reset(self):
        """Reset daily counters if a new day started."""
        now = time.time()
        if now - self._day_start > 86400:  # 24 hours
            self._daily_pnl = 0.0
            self._day_start = now
            logger.info("Daily P&L counter reset")

    def _check_hour_reset(self):
        """Reset hourly counters if a new hour started."""
        now = time.time()
        if now - self._hour_start > 3600:  # 1 hour
            self._hourly_pnl = 0.0
            self._hour_start = now

    def _seconds_until_next_day(self) -> float:
        """Seconds until 00:00 UTC next day."""
        now = time.time()
        # Roughly calculate - stop for at least 8 hours
        return 28800  # 8 hours default

    def get_stats(self) -> dict:
        """Get risk manager statistics."""
        wins = sum(1 for t in self._trades if t.pnl > 0)
        losses = sum(1 for t in self._trades if t.pnl <= 0)
        
        return {
            "total_trades": self._total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / max(1, self._total_trades),
            "total_pnl": self._total_pnl,
            "daily_pnl": self._daily_pnl,
            "consecutive_losses": self._consecutive_losses,
            "current_layers": self._current_layers,
            "is_paused": self.is_paused,
            "pause_remaining_sec": self.pause_remaining,
        }
