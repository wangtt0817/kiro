"""
Signal Generator - Monitors ETH/BTC ratio and generates trading signals.
"""

import time
import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SignalDirection(Enum):
    """Trading signal direction."""
    LONG_ETH_SHORT_BTC = "long_eth_short_btc"   # ratio is low, expect reversion up
    SHORT_ETH_LONG_BTC = "short_eth_long_btc"   # ratio is high, expect reversion down
    CLOSE = "close"                              # ratio back to mean, close position
    NONE = "none"                                # no signal


@dataclass
class Signal:
    direction: SignalDirection
    deviation: float          # Current deviation from EMA (as ratio, e.g., 0.0006 = 0.06%)
    ratio: float              # Current ETH/BTC ratio
    ema: float                # Current EMA value
    strength: int             # 1=normal, 2=add layer, 3=strong add
    timestamp: float


class RatioSignalGenerator:
    """
    Generates trading signals based on ETH/BTC price ratio deviation from EMA.
    
    Uses exponential moving average of the ratio. When ratio deviates beyond
    threshold, signals to open a mean-reversion position.
    """

    def __init__(
        self,
        ema_period: int = 20,
        open_threshold: float = 0.0005,
        close_threshold: float = 0.0001,
        add_threshold_2: float = 0.0008,
        add_threshold_3: float = 0.0012,
    ):
        self.ema_period = ema_period
        self.open_threshold = open_threshold
        self.close_threshold = close_threshold
        self.add_threshold_2 = add_threshold_2
        self.add_threshold_3 = add_threshold_3

        # EMA state
        self._ema: Optional[float] = None
        self._multiplier = 2.0 / (ema_period + 1)
        
        # History for monitoring
        self._ratio_history: deque = deque(maxlen=1000)
        self._signal_count = 0

    @property
    def ema(self) -> Optional[float]:
        return self._ema

    @property
    def ready(self) -> bool:
        """Whether enough data has been collected to generate signals."""
        return self._ema is not None and len(self._ratio_history) >= self.ema_period

    def update(self, eth_price: float, btc_price: float) -> Signal:
        """
        Feed new prices and get signal.
        
        Args:
            eth_price: Current ETH mark price
            btc_price: Current BTC mark price
            
        Returns:
            Signal object indicating direction and strength
        """
        if btc_price <= 0 or eth_price <= 0:
            return Signal(
                direction=SignalDirection.NONE,
                deviation=0.0,
                ratio=0.0,
                ema=0.0,
                strength=0,
                timestamp=time.time()
            )

        # Calculate ratio
        ratio = eth_price / btc_price
        self._ratio_history.append((time.time(), ratio))

        # Update EMA
        if self._ema is None:
            self._ema = ratio
        else:
            self._ema = ratio * self._multiplier + self._ema * (1 - self._multiplier)

        # Calculate deviation (signed)
        deviation = (ratio - self._ema) / self._ema

        # Don't signal until we have enough history
        if not self.ready:
            return Signal(
                direction=SignalDirection.NONE,
                deviation=deviation,
                ratio=ratio,
                ema=self._ema,
                strength=0,
                timestamp=time.time()
            )

        # Generate signal
        signal = self._evaluate_signal(deviation, ratio)
        
        if signal.direction != SignalDirection.NONE:
            self._signal_count += 1
            logger.debug(
                f"Signal #{self._signal_count}: {signal.direction.value} | "
                f"deviation={deviation*100:.4f}% | ratio={ratio:.8f} | ema={self._ema:.8f}"
            )

        return signal

    def _evaluate_signal(self, deviation: float, ratio: float) -> Signal:
        """Evaluate deviation and return appropriate signal."""
        
        abs_dev = abs(deviation)
        now = time.time()

        # Close signal: deviation back near zero
        if abs_dev < self.close_threshold:
            return Signal(
                direction=SignalDirection.CLOSE,
                deviation=deviation,
                ratio=ratio,
                ema=self._ema,
                strength=0,
                timestamp=now
            )

        # Open / Add signals
        if abs_dev >= self.add_threshold_3:
            strength = 3
        elif abs_dev >= self.add_threshold_2:
            strength = 2
        elif abs_dev >= self.open_threshold:
            strength = 1
        else:
            # Between close and open threshold - no action
            return Signal(
                direction=SignalDirection.NONE,
                deviation=deviation,
                ratio=ratio,
                ema=self._ema,
                strength=0,
                timestamp=now
            )

        # Determine direction (mean reversion)
        if deviation < 0:
            # Ratio below EMA -> expect ratio to rise -> long ETH / short BTC
            direction = SignalDirection.LONG_ETH_SHORT_BTC
        else:
            # Ratio above EMA -> expect ratio to fall -> short ETH / long BTC
            direction = SignalDirection.SHORT_ETH_LONG_BTC

        return Signal(
            direction=direction,
            deviation=deviation,
            ratio=ratio,
            ema=self._ema,
            strength=strength,
            timestamp=now
        )

    def get_stats(self) -> dict:
        """Get signal generator statistics."""
        if not self._ratio_history:
            return {"signals_generated": 0, "ratio_samples": 0}
        
        ratios = [r for _, r in self._ratio_history]
        return {
            "signals_generated": self._signal_count,
            "ratio_samples": len(ratios),
            "current_ema": self._ema,
            "last_ratio": ratios[-1] if ratios else None,
            "ratio_min_20": min(ratios[-20:]) if len(ratios) >= 20 else None,
            "ratio_max_20": max(ratios[-20:]) if len(ratios) >= 20 else None,
        }
