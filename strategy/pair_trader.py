"""
High-Frequency BTC/ETH Pair Trader - Core execution engine.

Strategy: 
- Monitor ETH/BTC ratio deviation from short-term EMA
- When deviation exceeds threshold, open hedged position (long one, short the other)
- Close when ratio reverts to mean
- All orders are Maker (limit) to minimize fees (0.005% vs 0.15% taker)
- Target: 0.05% gross profit per round, 0.03% net after fees
- High frequency: as many rounds per day as possible for volume
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum

from config import *
from exchange_client import ExchangeClient, OrderSide, OrderStatus, Order, OrderBook
from signal_generator import RatioSignalGenerator, SignalDirection, Signal
from risk_manager import RiskManager, TradeResult

logger = logging.getLogger(__name__)


class TradeState(Enum):
    """State machine for each trade round."""
    IDLE = "idle"
    OPENING_LEG1 = "opening_leg1"       # Placing 1st leg (ETH - less liquid)
    OPENING_LEG2 = "opening_leg2"       # Placing 2nd leg (BTC - more liquid)
    HOLDING = "holding"                 # Both legs filled, waiting for exit signal
    CLOSING_BOTH = "closing_both"       # Closing both legs simultaneously
    CLOSING_REMAINING = "closing_remaining"  # One leg closed, urgently closing the other
    ABORTING = "aborting"               # Aborting: only 1 leg filled, need to unwind
    DONE = "done"                       # Round complete


@dataclass
class TradeLeg:
    """One leg of the pair trade."""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    amount: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    order_id: str = ""
    close_order_id: str = ""       # Separate field for close order
    filled: bool = False
    closed: bool = False           # Whether this leg has been closed
    entry_time: float = 0.0
    close_order_time: float = 0.0  # When close order was placed
    close_retries: int = 0         # Number of close retries


@dataclass
class TradeRound:
    """A complete pair trade round (open + close both legs)."""
    id: int = 0
    state: TradeState = TradeState.IDLE
    direction: SignalDirection = SignalDirection.NONE
    
    # Legs
    eth_leg: TradeLeg = field(default_factory=TradeLeg)
    btc_leg: TradeLeg = field(default_factory=TradeLeg)
    
    # Timing
    signal_time: float = 0.0
    open_time: float = 0.0
    close_time: float = 0.0
    
    # P&L
    entry_ratio: float = 0.0
    exit_ratio: float = 0.0
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    
    # Layer info
    layer: int = 1


class HighFreqPairTrader:
    """
    Main trading engine.
    
    Execution flow:
    1. Signal generator detects ratio deviation
    2. Place Maker order on less-liquid leg (ETH) first
    3. Once filled, immediately place Maker order on BTC leg
    4. If BTC leg not filled in timeout, abort (cancel + unwind ETH)
    5. Once both filled, monitor for close signal
    6. Close both legs with Maker orders when ratio reverts
    """

    def __init__(self, client: ExchangeClient):
        self.client = client
        
        # Signal generator
        self.signal_gen = RatioSignalGenerator(
            ema_period=EMA_PERIOD,
            open_threshold=OPEN_THRESHOLD,
            close_threshold=CLOSE_THRESHOLD,
            add_threshold_2=ADD_THRESHOLD_2,
            add_threshold_3=ADD_THRESHOLD_3,
        )
        
        # Risk manager (will be initialized with actual equity)
        self.risk_mgr: Optional[RiskManager] = None
        
        # Active trade rounds
        self._active_rounds: List[TradeRound] = []
        self._round_counter: int = 0
        
        # Stats
        self._total_volume: float = 0.0
        self._start_time: float = time.time()
        self._running: bool = False

    async def initialize(self):
        """Initialize the trader with exchange data."""
        equity = await self.client.get_equity()
        self.risk_mgr = RiskManager(
            initial_equity=equity,
            max_daily_loss_pct=MAX_DAILY_LOSS_PCT,
            max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
            pause_after_consecutive_loss=PAUSE_AFTER_CONSECUTIVE_LOSS,
            hourly_loss_pause_pct=HOURLY_LOSS_PAUSE_PCT,
            hourly_pause_duration=HOURLY_PAUSE_DURATION,
            max_margin_usage=MAX_MARGIN_USAGE,
            max_layers=MAX_LAYERS,
        )
        logger.info(f"Trader initialized. Equity: ${equity:.2f}")

    async def run(self):
        """Main loop - runs indefinitely."""
        await self.initialize()
        self._running = True
        
        logger.info("=" * 60)
        logger.info("HIGH-FREQ BTC/ETH PAIR TRADER STARTED")
        logger.info(f"  Maker fee: {MAKER_FEE*100:.3f}%")
        logger.info(f"  Round-trip cost: {ROUND_TRIP_COST*100:.3f}%")
        logger.info(f"  Target profit: {TARGET_PROFIT*100:.3f}%")
        logger.info(f"  Open threshold: {OPEN_THRESHOLD*100:.3f}%")
        logger.info(f"  Scan interval: {SCAN_INTERVAL}s")
        logger.info("=" * 60)

        while self._running:
            try:
                await self._tick()
                await asyncio.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                logger.info("Shutdown requested...")
                self._running = False
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(5)  # Brief pause on error

        # Cleanup: close all positions
        await self._emergency_close_all()
        logger.info("Trader stopped.")

    async def stop(self):
        """Gracefully stop the trader."""
        self._running = False

    async def _tick(self):
        """
        One cycle of the strategy:
        1. Fetch prices
        2. Update signal
        3. Manage active rounds (check timeouts, fills)
        4. Open new rounds if signal present and risk allows
        """
        # 1. Fetch current prices
        btc_price = await self.client.get_mark_price(BTC_SYMBOL)
        eth_price = await self.client.get_mark_price(ETH_SYMBOL)

        if btc_price <= 0 or eth_price <= 0:
            return

        # 2. Update signal generator
        signal = self.signal_gen.update(eth_price, btc_price)

        # 3. Manage active rounds
        await self._manage_active_rounds(signal, eth_price, btc_price)

        # 4. Check for new entry
        if signal.direction in (SignalDirection.LONG_ETH_SHORT_BTC, SignalDirection.SHORT_ETH_LONG_BTC):
            await self._try_open_new_round(signal, eth_price, btc_price)

    # ================================================================
    # Opening Logic
    # ================================================================

    async def _try_open_new_round(self, signal: Signal, eth_price: float, btc_price: float):
        """Attempt to open a new trade round."""
        
        # Risk checks
        if not self.risk_mgr.can_add_layer(signal.strength):
            return

        margin_usage = await self.client.get_margin_usage()
        if not self.risk_mgr.check_margin(margin_usage):
            logger.debug("Margin limit reached, skipping new round")
            return

        # Calculate position size
        equity = await self.client.get_equity()
        notional_per_leg = equity * POSITION_SIZE_RATIO * LEVERAGE

        eth_amount = notional_per_leg / eth_price
        btc_amount = notional_per_leg / btc_price

        # Create new round
        self._round_counter += 1
        trade_round = TradeRound(
            id=self._round_counter,
            direction=signal.direction,
            signal_time=time.time(),
            entry_ratio=eth_price / btc_price,
            layer=self.risk_mgr._current_layers + 1,
        )

        # Determine sides based on direction
        if signal.direction == SignalDirection.LONG_ETH_SHORT_BTC:
            eth_side = OrderSide.BUY
            btc_side = OrderSide.SELL
        else:
            eth_side = OrderSide.SELL
            btc_side = OrderSide.BUY

        trade_round.eth_leg = TradeLeg(
            symbol=ETH_SYMBOL,
            side=eth_side,
            amount=eth_amount,
        )
        trade_round.btc_leg = TradeLeg(
            symbol=BTC_SYMBOL,
            side=btc_side,
            amount=btc_amount,
        )

        # Start opening: place LEG1 (ETH first - typically less liquid)
        trade_round.state = TradeState.OPENING_LEG1
        success = await self._place_leg_order(trade_round, leg="eth")
        
        if success:
            self._active_rounds.append(trade_round)
            logger.info(
                f"Round #{trade_round.id} | Opening LEG1 (ETH {eth_side.value}) | "
                f"deviation={signal.deviation*100:.4f}%"
            )
        else:
            logger.warning(f"Round #{trade_round.id} | Failed to place LEG1")

    async def _place_leg_order(self, trade_round: TradeRound, leg: str) -> bool:
        """
        Place a Maker limit order for a leg.
        
        Strategy: place order 1 tick inside the spread to get priority
        while still being a maker order.
        """
        if leg == "eth":
            leg_info = trade_round.eth_leg
        else:
            leg_info = trade_round.btc_leg

        try:
            ob = await self.client.get_orderbook(leg_info.symbol)
            
            # Calculate maker-friendly price
            if leg_info.side == OrderSide.BUY:
                # Buying: place at best_bid + 1 tick (top of book, still maker)
                price = ob.best_bid
            else:
                # Selling: place at best_ask - 1 tick
                price = ob.best_ask

            order = await self.client.place_limit_order(
                symbol=leg_info.symbol,
                side=leg_info.side,
                amount=leg_info.amount,
                price=price,
            )

            leg_info.order_id = order.order_id
            leg_info.entry_price = price
            leg_info.entry_time = time.time()
            return True

        except Exception as e:
            logger.error(f"Error placing {leg} order: {e}")
            return False

    # ================================================================
    # Active Round Management
    # ================================================================

    async def _manage_active_rounds(self, signal: Signal, eth_price: float, btc_price: float):
        """Check all active rounds for fills, timeouts, and close signals."""
        
        rounds_to_remove = []

        for trade_round in self._active_rounds:
            
            if trade_round.state == TradeState.OPENING_LEG1:
                await self._check_leg1_fill(trade_round)

            elif trade_round.state == TradeState.OPENING_LEG2:
                await self._check_leg2_fill(trade_round)

            elif trade_round.state == TradeState.HOLDING:
                await self._check_exit_conditions(trade_round, signal, eth_price, btc_price)

            elif trade_round.state == TradeState.CLOSING_BOTH:
                await self._check_closing_both(trade_round)

            elif trade_round.state == TradeState.CLOSING_REMAINING:
                await self._check_closing_remaining(trade_round)

            elif trade_round.state == TradeState.ABORTING:
                await self._handle_abort(trade_round)

            elif trade_round.state == TradeState.DONE:
                rounds_to_remove.append(trade_round)

        # Clean up completed rounds
        for r in rounds_to_remove:
            self._active_rounds.remove(r)

    async def _check_leg1_fill(self, trade_round: TradeRound):
        """Check if LEG1 (ETH) has been filled."""
        leg = trade_round.eth_leg
        elapsed = time.time() - leg.entry_time

        order = await self.client.get_order_status(leg.order_id, leg.symbol)

        if order.status == OrderStatus.FILLED:
            # LEG1 filled! Immediately place LEG2
            leg.filled = True
            leg.entry_price = order.price  # Use actual fill price
            trade_round.state = TradeState.OPENING_LEG2
            
            success = await self._place_leg_order(trade_round, leg="btc")
            if not success:
                # Can't place BTC leg -> abort
                trade_round.state = TradeState.ABORTING
                logger.warning(f"Round #{trade_round.id} | LEG2 placement failed, aborting")

        elif elapsed > LEG1_TIMEOUT:
            # Timeout: cancel and give up
            await self.client.cancel_order(leg.order_id, leg.symbol)
            trade_round.state = TradeState.DONE
            logger.debug(f"Round #{trade_round.id} | LEG1 timeout, cancelled")

    async def _check_leg2_fill(self, trade_round: TradeRound):
        """Check if LEG2 (BTC) has been filled."""
        leg = trade_round.btc_leg
        elapsed = time.time() - leg.entry_time

        order = await self.client.get_order_status(leg.order_id, leg.symbol)

        if order.status == OrderStatus.FILLED:
            # Both legs filled! Enter HOLDING state
            leg.filled = True
            leg.entry_price = order.price
            trade_round.state = TradeState.HOLDING
            trade_round.open_time = time.time()
            
            self.risk_mgr.on_layer_opened()
            
            # Track volume
            eth_notional = trade_round.eth_leg.amount * trade_round.eth_leg.entry_price
            btc_notional = trade_round.btc_leg.amount * trade_round.btc_leg.entry_price
            self._total_volume += eth_notional + btc_notional
            
            logger.info(
                f"Round #{trade_round.id} | OPEN COMPLETE | "
                f"ETH {trade_round.eth_leg.side.value}@{trade_round.eth_leg.entry_price:.2f} | "
                f"BTC {trade_round.btc_leg.side.value}@{trade_round.btc_leg.entry_price:.2f}"
            )

        elif elapsed > LEG2_TIMEOUT:
            # Timeout: cancel BTC leg and abort (unwind ETH)
            await self.client.cancel_order(leg.order_id, leg.symbol)
            trade_round.state = TradeState.ABORTING
            logger.warning(f"Round #{trade_round.id} | LEG2 timeout, aborting")

    async def _check_exit_conditions(
        self, trade_round: TradeRound, signal: Signal, eth_price: float, btc_price: float
    ):
        """Check if holding position should be closed."""
        
        hold_time = time.time() - trade_round.open_time
        current_ratio = eth_price / btc_price
        
        # Calculate current P&L of this round
        pnl_pct = self._calc_round_pnl_pct(trade_round, eth_price, btc_price)

        should_close = False
        reason = ""

        # Condition 1: Target profit reached
        if pnl_pct >= TARGET_PROFIT:
            should_close = True
            reason = f"TARGET HIT ({pnl_pct*100:.4f}%)"

        # Condition 2: Signal says close (ratio reverted)
        elif signal.direction == SignalDirection.CLOSE:
            if pnl_pct > ROUND_TRIP_COST:  # Only close if profitable after fees
                should_close = True
                reason = f"SIGNAL CLOSE ({pnl_pct*100:.4f}%)"

        # Condition 3: Stop loss
        elif pnl_pct <= -STOP_LOSS:
            should_close = True
            reason = f"STOP LOSS ({pnl_pct*100:.4f}%)"

        # Condition 4: Max hold time
        elif hold_time > MAX_HOLD_SECONDS:
            should_close = True
            reason = f"TIMEOUT ({hold_time:.0f}s)"

        if should_close:
            logger.info(f"Round #{trade_round.id} | Closing: {reason}")
            await self._start_close(trade_round)

    def _calc_round_pnl_pct(
        self, trade_round: TradeRound, eth_price: float, btc_price: float
    ) -> float:
        """
        Calculate unrealized P&L percentage for a round.
        
        For LONG_ETH_SHORT_BTC:
            ETH leg P&L = (current - entry) / entry
            BTC leg P&L = (entry - current) / entry  (short)
            Combined = weighted average
            
        Simplified: we track ratio change since entry
        """
        entry_ratio = trade_round.entry_ratio
        current_ratio = eth_price / btc_price

        if trade_round.direction == SignalDirection.LONG_ETH_SHORT_BTC:
            # Profit when ratio goes UP (ETH outperforms BTC)
            pnl_pct = (current_ratio - entry_ratio) / entry_ratio
        else:
            # Profit when ratio goes DOWN (BTC outperforms ETH)
            pnl_pct = (entry_ratio - current_ratio) / entry_ratio

        return pnl_pct

    # ================================================================
    # Closing Logic - SIMULTANEOUS BOTH LEGS
    # ================================================================
    #
    # Key Design: Place close orders for BOTH legs at the same time.
    # 
    # Problem: If we close sequentially (ETH first, then BTC), and BTC
    # close fails, we have a naked single-leg position = DANGEROUS.
    #
    # Solution: 
    # 1. Place Maker close orders on BOTH legs simultaneously
    # 2. Track each leg's close status independently
    # 3. If one leg fills but the other doesn't within timeout:
    #    → Enter CLOSING_REMAINING state
    #    → Aggressively close the remaining leg (refresh price every 1s)
    #    → After MAX retries, use Taker (market order) as last resort
    # 4. NEVER leave a single-leg position open unmanaged

    MAX_CLOSE_RETRIES = 5          # Max Maker retry attempts per leg
    CLOSE_LEG_TIMEOUT = 3          # Seconds before refreshing a close order
    FORCE_TAKER_AFTER_RETRIES = 5  # Use Taker after this many Maker failures

    async def _start_close(self, trade_round: TradeRound):
        """
        Start closing BOTH legs simultaneously with Maker orders.
        """
        trade_round.state = TradeState.CLOSING_BOTH
        
        # Place close order for ETH leg
        await self._place_close_order(trade_round, "eth")
        # Place close order for BTC leg
        await self._place_close_order(trade_round, "btc")
        
        logger.info(f"Round #{trade_round.id} | Close orders placed on BOTH legs")

    async def _place_close_order(self, trade_round: TradeRound, leg_name: str):
        """Place a Maker limit close order for one leg."""
        if leg_name == "eth":
            leg = trade_round.eth_leg
        else:
            leg = trade_round.btc_leg
        
        if leg.closed:
            return  # Already closed
        
        close_side = OrderSide.SELL if leg.side == OrderSide.BUY else OrderSide.BUY
        
        try:
            ob = await self.client.get_orderbook(leg.symbol)
            
            # Maker price: we want to be at the top of the book
            if close_side == OrderSide.SELL:
                price = ob.best_ask  # Sell at best ask (maker, will be filled when someone buys)
            else:
                price = ob.best_bid  # Buy at best bid (maker, will be filled when someone sells)
            
            order = await self.client.place_limit_order(
                symbol=leg.symbol,
                side=close_side,
                amount=leg.amount,
                price=price,
                reduce_only=True,
            )
            
            leg.close_order_id = order.order_id
            leg.close_order_time = time.time()
            
        except Exception as e:
            logger.error(f"Round #{trade_round.id} | Error placing {leg_name} close: {e}")

    async def _check_closing_both(self, trade_round: TradeRound):
        """
        Monitor both legs closing simultaneously.
        
        Possible outcomes:
        - Both fill → DONE (best case)
        - One fills, other pending → CLOSING_REMAINING (handle urgently)
        - Both timeout → refresh both orders
        """
        eth_leg = trade_round.eth_leg
        btc_leg = trade_round.btc_leg
        
        # Check ETH leg close status
        if not eth_leg.closed and eth_leg.close_order_id:
            eth_order = await self.client.get_order_status(eth_leg.close_order_id, eth_leg.symbol)
            if eth_order.status == OrderStatus.FILLED:
                eth_leg.closed = True
                eth_leg.exit_price = eth_order.price
                logger.info(f"Round #{trade_round.id} | ETH leg closed @ {eth_order.price:.2f}")
        
        # Check BTC leg close status
        if not btc_leg.closed and btc_leg.close_order_id:
            btc_order = await self.client.get_order_status(btc_leg.close_order_id, btc_leg.symbol)
            if btc_order.status == OrderStatus.FILLED:
                btc_leg.closed = True
                btc_leg.exit_price = btc_order.price
                logger.info(f"Round #{trade_round.id} | BTC leg closed @ {btc_order.price:.2f}")
        
        # === Decision Logic ===
        
        # Case 1: Both closed → Done!
        if eth_leg.closed and btc_leg.closed:
            trade_round.close_time = time.time()
            self._finalize_round(trade_round)
            return
        
        # Case 2: One closed, other still pending → URGENT! Close remaining ASAP
        if eth_leg.closed and not btc_leg.closed:
            logger.warning(
                f"Round #{trade_round.id} | ⚠️ ETH closed but BTC still open! "
                f"Entering CLOSING_REMAINING mode"
            )
            # Cancel existing BTC order and enter aggressive close mode
            if btc_leg.close_order_id:
                await self.client.cancel_order(btc_leg.close_order_id, btc_leg.symbol)
            trade_round.state = TradeState.CLOSING_REMAINING
            btc_leg.close_retries = 0
            await self._aggressive_close_leg(trade_round, "btc")
            return
        
        if btc_leg.closed and not eth_leg.closed:
            logger.warning(
                f"Round #{trade_round.id} | ⚠️ BTC closed but ETH still open! "
                f"Entering CLOSING_REMAINING mode"
            )
            if eth_leg.close_order_id:
                await self.client.cancel_order(eth_leg.close_order_id, eth_leg.symbol)
            trade_round.state = TradeState.CLOSING_REMAINING
            eth_leg.close_retries = 0
            await self._aggressive_close_leg(trade_round, "eth")
            return
        
        # Case 3: Neither closed yet, check timeouts and refresh
        now = time.time()
        
        if not eth_leg.closed and (now - eth_leg.close_order_time) > self.CLOSE_LEG_TIMEOUT:
            # Refresh ETH close order at new price
            if eth_leg.close_order_id:
                await self.client.cancel_order(eth_leg.close_order_id, eth_leg.symbol)
            await self._place_close_order(trade_round, "eth")
            eth_leg.close_retries += 1
        
        if not btc_leg.closed and (now - btc_leg.close_order_time) > self.CLOSE_LEG_TIMEOUT:
            # Refresh BTC close order at new price
            if btc_leg.close_order_id:
                await self.client.cancel_order(btc_leg.close_order_id, btc_leg.symbol)
            await self._place_close_order(trade_round, "btc")
            btc_leg.close_retries += 1
        
        # Case 4: Too many retries on both → force taker on both
        if eth_leg.close_retries >= self.FORCE_TAKER_AFTER_RETRIES:
            await self._force_taker_close(trade_round, "eth")
        if btc_leg.close_retries >= self.FORCE_TAKER_AFTER_RETRIES:
            await self._force_taker_close(trade_round, "btc")

    async def _check_closing_remaining(self, trade_round: TradeRound):
        """
        CRITICAL STATE: One leg is already closed, we MUST close the other ASAP.
        
        This is the dangerous state - we have a naked single-leg position.
        Strategy: aggressive Maker refresh every 1 second, escalate to Taker if needed.
        """
        eth_leg = trade_round.eth_leg
        btc_leg = trade_round.btc_leg
        
        # Determine which leg still needs closing
        if not eth_leg.closed:
            remaining_leg = eth_leg
            remaining_name = "eth"
        elif not btc_leg.closed:
            remaining_leg = btc_leg
            remaining_name = "btc"
        else:
            # Both closed somehow
            trade_round.close_time = time.time()
            self._finalize_round(trade_round)
            return
        
        # Check if our close order filled
        if remaining_leg.close_order_id:
            order = await self.client.get_order_status(
                remaining_leg.close_order_id, remaining_leg.symbol
            )
            if order.status == OrderStatus.FILLED:
                remaining_leg.closed = True
                remaining_leg.exit_price = order.price
                trade_round.close_time = time.time()
                logger.info(
                    f"Round #{trade_round.id} | Remaining {remaining_name.upper()} leg "
                    f"closed @ {order.price:.2f} (after {remaining_leg.close_retries} retries)"
                )
                self._finalize_round(trade_round)
                return
        
        # Not filled yet - check if we should refresh or escalate
        elapsed = time.time() - remaining_leg.close_order_time
        
        if remaining_leg.close_retries >= self.FORCE_TAKER_AFTER_RETRIES:
            # ==========================================
            # LAST RESORT: Use Taker (market-like order)
            # ==========================================
            logger.warning(
                f"Round #{trade_round.id} | ⚠️⚠️ {remaining_name.upper()} leg "
                f"failed {remaining_leg.close_retries} Maker attempts! "
                f"FORCING TAKER CLOSE (will cost 0.15% but prevents naked exposure)"
            )
            await self._force_taker_close(trade_round, remaining_name)
            
        elif elapsed > 1.0:  # Refresh every 1 second (aggressive!)
            # Cancel old order and place new one at updated price
            if remaining_leg.close_order_id:
                await self.client.cancel_order(remaining_leg.close_order_id, remaining_leg.symbol)
            remaining_leg.close_retries += 1
            await self._aggressive_close_leg(trade_round, remaining_name)

    async def _aggressive_close_leg(self, trade_round: TradeRound, leg_name: str):
        """
        Aggressively try to close a leg - place order INSIDE the spread for faster fill.
        
        Unlike normal Maker orders (at best bid/ask), this places the order
        slightly inside the spread to increase fill probability while still 
        being a Maker order.
        """
        if leg_name == "eth":
            leg = trade_round.eth_leg
        else:
            leg = trade_round.btc_leg
        
        close_side = OrderSide.SELL if leg.side == OrderSide.BUY else OrderSide.BUY
        
        try:
            ob = await self.client.get_orderbook(leg.symbol)
            spread = ob.best_ask - ob.best_bid
            
            # Place INSIDE the spread (more aggressive = faster fill)
            # Example: if bid=100, ask=101, and we're selling:
            #   Normal maker: sell at 101 (best ask)
            #   Aggressive:   sell at 100.5 (mid-spread) — still maker, fills faster
            if close_side == OrderSide.SELL:
                # Sell below best ask (closer to mid) for faster fill
                price = ob.best_ask - spread * 0.3  # 30% into spread
                price = max(price, ob.best_bid)     # Don't go below bid (would be taker)
            else:
                # Buy above best bid (closer to mid) for faster fill
                price = ob.best_bid + spread * 0.3
                price = min(price, ob.best_ask)     # Don't go above ask

            order = await self.client.place_limit_order(
                symbol=leg.symbol,
                side=close_side,
                amount=leg.amount,
                price=price,
                reduce_only=True,
            )
            
            leg.close_order_id = order.order_id
            leg.close_order_time = time.time()
            
            logger.debug(
                f"Round #{trade_round.id} | Aggressive close {leg_name.upper()} "
                f"@ {price:.2f} (retry #{leg.close_retries})"
            )
            
        except Exception as e:
            logger.error(f"Round #{trade_round.id} | Aggressive close {leg_name} error: {e}")

    async def _force_taker_close(self, trade_round: TradeRound, leg_name: str):
        """
        LAST RESORT: Force close using Taker order (market-like).
        
        This costs 0.15% instead of 0.005%, but guarantees execution.
        Used only when Maker close has failed multiple times and we have
        a dangerous naked single-leg position.
        """
        if leg_name == "eth":
            leg = trade_round.eth_leg
        else:
            leg = trade_round.btc_leg
        
        if leg.closed:
            return
        
        close_side = OrderSide.SELL if leg.side == OrderSide.BUY else OrderSide.BUY
        
        try:
            # Cancel any existing order
            if leg.close_order_id:
                await self.client.cancel_order(leg.close_order_id, leg.symbol)
            
            ob = await self.client.get_orderbook(leg.symbol)
            
            # Price that guarantees immediate fill (cross the spread)
            if close_side == OrderSide.SELL:
                # Sell at best_bid (will execute immediately as taker)
                price = ob.best_bid
            else:
                # Buy at best_ask (will execute immediately as taker)
                price = ob.best_ask
            
            order = await self.client.place_limit_order(
                symbol=leg.symbol,
                side=close_side,
                amount=leg.amount,
                price=price,
                reduce_only=True,
            )
            
            # Wait briefly for fill confirmation
            await asyncio.sleep(1)
            status = await self.client.get_order_status(order.order_id, leg.symbol)
            
            if status.status == OrderStatus.FILLED:
                leg.closed = True
                leg.exit_price = status.price
                logger.warning(
                    f"Round #{trade_round.id} | {leg_name.upper()} FORCE CLOSED (Taker) "
                    f"@ {status.price:.2f}"
                )
                
                # Check if both legs now closed
                eth_leg = trade_round.eth_leg
                btc_leg = trade_round.btc_leg
                if eth_leg.closed and btc_leg.closed:
                    trade_round.close_time = time.time()
                    self._finalize_round(trade_round, forced_taker=True)
            else:
                # Even taker didn't fill? Extremely rare. Keep order open.
                leg.close_order_id = order.order_id
                leg.close_order_time = time.time()
                logger.error(
                    f"Round #{trade_round.id} | {leg_name.upper()} Taker order not filled! "
                    f"Order still open. Will retry next tick."
                )
                
        except Exception as e:
            logger.error(f"Round #{trade_round.id} | Force taker close error: {e}")

    def _finalize_round(self, trade_round: TradeRound, forced_taker: bool = False):
        """Calculate final P&L and record the trade."""
        eth_leg = trade_round.eth_leg
        btc_leg = trade_round.btc_leg

        # ETH P&L
        if eth_leg.side == OrderSide.BUY:
            eth_pnl = (eth_leg.exit_price - eth_leg.entry_price) * eth_leg.amount
        else:
            eth_pnl = (eth_leg.entry_price - eth_leg.exit_price) * eth_leg.amount

        # BTC P&L
        if btc_leg.side == OrderSide.BUY:
            btc_pnl = (btc_leg.exit_price - btc_leg.entry_price) * btc_leg.amount
        else:
            btc_pnl = (btc_leg.entry_price - btc_leg.exit_price) * btc_leg.amount

        gross_pnl = eth_pnl + btc_pnl

        # Fees calculation:
        # Open: 2 maker orders (always)
        # Close: depends on whether taker was forced
        open_notional = eth_leg.amount * eth_leg.entry_price + btc_leg.amount * btc_leg.entry_price
        close_notional = eth_leg.amount * eth_leg.exit_price + btc_leg.amount * btc_leg.exit_price
        
        open_fees = open_notional * MAKER_FEE  # Open is always Maker
        
        if forced_taker:
            # Worst case: one leg closed as Taker
            # Assume the remaining leg used Taker, other leg used Maker
            close_fees = (close_notional / 2) * MAKER_FEE + (close_notional / 2) * TAKER_FEE
        else:
            close_fees = close_notional * MAKER_FEE  # Both legs Maker
        
        total_fees = open_fees + close_fees
        net_pnl = gross_pnl - total_fees
        total_notional = open_notional
        pnl_pct = net_pnl / total_notional if total_notional > 0 else 0

        trade_round.gross_pnl = gross_pnl
        trade_round.fees = total_fees
        trade_round.net_pnl = net_pnl
        trade_round.state = TradeState.DONE

        # Track volume (open + close)
        self._total_volume += open_notional + close_notional

        # Record in risk manager
        result = TradeResult(
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            fees_paid=total_fees,
            open_time=trade_round.open_time,
            close_time=trade_round.close_time,
            hold_duration=trade_round.close_time - trade_round.open_time,
            direction=trade_round.direction.value,
            layers=trade_round.layer,
            forced_close=forced_taker,
        )
        self.risk_mgr.record_trade(result)
        self.risk_mgr.on_layer_closed()

        status = "⚠️ TAKER USED" if forced_taker else "✅"
        logger.info(
            f"Round #{trade_round.id} | CLOSED {status} | "
            f"Gross={gross_pnl:.4f} | Fees={total_fees:.4f} | Net={net_pnl:.4f} USDT | "
            f"Duration={result.hold_duration:.1f}s | "
            f"Volume=${open_notional + close_notional:.0f}"
        )

    # ================================================================
    # Abort & Emergency
    # ================================================================

    async def _handle_abort(self, trade_round: TradeRound):
        """
        Abort a partially opened round.
        Only ETH leg is filled, need to unwind it with a Maker order.
        """
        eth_leg = trade_round.eth_leg
        
        if not eth_leg.filled:
            # Nothing to unwind
            trade_round.state = TradeState.DONE
            return

        # Place opposite order to close ETH leg
        close_side = OrderSide.SELL if eth_leg.side == OrderSide.BUY else OrderSide.BUY
        
        try:
            ob = await self.client.get_orderbook(ETH_SYMBOL)
            price = ob.best_ask if close_side == OrderSide.SELL else ob.best_bid
            
            order = await self.client.place_limit_order(
                symbol=ETH_SYMBOL,
                side=close_side,
                amount=eth_leg.amount,
                price=price,
                reduce_only=True,
            )
            
            # Wait briefly for fill
            await asyncio.sleep(2)
            status = await self.client.get_order_status(order.order_id, ETH_SYMBOL)
            
            if status.status == OrderStatus.FILLED:
                trade_round.state = TradeState.DONE
                logger.info(f"Round #{trade_round.id} | Abort complete, ETH unwound")
            else:
                # Cancel and retry next tick
                await self.client.cancel_order(order.order_id, ETH_SYMBOL)
                # Keep in ABORTING state for next tick retry
                
        except Exception as e:
            logger.error(f"Round #{trade_round.id} | Abort error: {e}")

    async def _emergency_close_all(self):
        """Emergency close all positions on shutdown."""
        logger.warning("EMERGENCY CLOSE ALL POSITIONS")
        try:
            await self.client.cancel_all_orders()
            positions = await self.client.get_all_positions()
            for pos in positions:
                if pos.size > 0:
                    close_side = OrderSide.SELL if pos.side == "long" else OrderSide.BUY
                    ob = await self.client.get_orderbook(pos.symbol)
                    price = ob.best_bid if close_side == OrderSide.SELL else ob.best_ask
                    await self.client.place_limit_order(
                        symbol=pos.symbol,
                        side=close_side,
                        amount=pos.size,
                        price=price,
                        reduce_only=True,
                    )
        except Exception as e:
            logger.error(f"Emergency close error: {e}")

    # ================================================================
    # Stats
    # ================================================================

    def get_stats(self) -> dict:
        """Get comprehensive strategy statistics."""
        runtime = time.time() - self._start_time
        signal_stats = self.signal_gen.get_stats()
        risk_stats = self.risk_mgr.get_stats() if self.risk_mgr else {}

        return {
            "runtime_hours": runtime / 3600,
            "total_volume": self._total_volume,
            "volume_per_hour": self._total_volume / max(1, runtime / 3600),
            "active_rounds": len(self._active_rounds),
            "total_rounds": self._round_counter,
            "signal": signal_stats,
            "risk": risk_stats,
        }
