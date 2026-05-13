"""
MemeMax BTC/ETH High-Frequency Pair Trading Strategy
=====================================================

Entry point for the trading bot.

Usage:
    python main.py

Strategy Summary:
    - Monitor ETH/BTC price ratio deviation from 20-period EMA
    - When ratio deviates > 0.05%, open hedged position (Maker limit orders only)
    - Close when ratio reverts to mean (target 0.05% gross profit)
    - All orders are Maker to minimize fees (0.005% per trade)
    - Round-trip cost: 0.02% (4 maker orders)
    - Net profit target: 0.03% per round
    - Goal: maximize trading volume with consistent small profits

Key Design Decisions:
    - ETH leg placed first (typically less liquid on MemeMax)
    - BTC leg placed immediately after ETH fills
    - If BTC leg fails to fill in 3s, abort and unwind ETH
    - Max 3 concurrent layers
    - Daily loss limit: 1% -> stop trading
    - 5 consecutive losses -> 30min pause
"""

import asyncio
import logging
import sys
import time
from datetime import datetime

from config import *
from exchange_client import ExchangeClient
from pair_trader import HighFreqPairTrader

# ================================================================
# Logging Setup
# ================================================================

def setup_logging():
    """Configure logging to console and file."""
    
    log_format = "%(asctime)s | %(levelname)-5s | %(name)-12s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # File handler
    log_filename = f"trading_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(log_filename)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    return logging.getLogger(__name__)


# ================================================================
# Stats Reporter
# ================================================================

async def stats_reporter(trader: HighFreqPairTrader, interval: int = 60):
    """Periodically report statistics."""
    logger = logging.getLogger("stats")
    
    while True:
        await asyncio.sleep(interval)
        
        stats = trader.get_stats()
        risk = stats.get("risk", {})
        
        logger.info("=" * 50)
        logger.info("PERIODIC STATS REPORT")
        logger.info(f"  Runtime: {stats['runtime_hours']:.2f} hours")
        logger.info(f"  Total Volume: ${stats['total_volume']:,.0f}")
        logger.info(f"  Volume/Hour: ${stats['volume_per_hour']:,.0f}")
        logger.info(f"  Total Rounds: {stats['total_rounds']}")
        logger.info(f"  Active Rounds: {stats['active_rounds']}")
        logger.info(f"  Win Rate: {risk.get('win_rate', 0)*100:.1f}%")
        logger.info(f"  Total PnL: ${risk.get('total_pnl', 0):.4f}")
        logger.info(f"  Daily PnL: ${risk.get('daily_pnl', 0):.4f}")
        logger.info(f"  Consecutive Losses: {risk.get('consecutive_losses', 0)}")
        logger.info(f"  Paused: {risk.get('is_paused', False)}")
        logger.info("=" * 50)


# ================================================================
# Main
# ================================================================

async def main():
    """Main entry point."""
    
    logger = setup_logging()
    
    logger.info("=" * 60)
    logger.info("  MemeMax BTC/ETH High-Frequency Pair Trader")
    logger.info("=" * 60)
    logger.info(f"  Exchange: {EXCHANGE_NAME}")
    logger.info(f"  BTC Symbol: {BTC_SYMBOL}")
    logger.info(f"  ETH Symbol: {ETH_SYMBOL}")
    logger.info(f"  Maker Fee: {MAKER_FEE*100:.3f}%")
    logger.info(f"  Taker Fee: {TAKER_FEE*100:.3f}%")
    logger.info(f"  Leverage: {LEVERAGE}x")
    logger.info(f"  EMA Period: {EMA_PERIOD}")
    logger.info(f"  Open Threshold: {OPEN_THRESHOLD*100:.3f}%")
    logger.info(f"  Target Profit: {TARGET_PROFIT*100:.3f}%")
    logger.info(f"  Stop Loss: {STOP_LOSS*100:.3f}%")
    logger.info(f"  Max Hold: {MAX_HOLD_SECONDS}s")
    logger.info(f"  Max Layers: {MAX_LAYERS}")
    logger.info(f"  Max Daily Loss: {MAX_DAILY_LOSS_PCT*100:.2f}%")
    logger.info("=" * 60)
    
    # Validate config
    if not API_KEY or not API_SECRET:
        logger.error("API_KEY and API_SECRET must be set in config.py!")
        logger.error("Please fill in your MemeMax API credentials.")
        sys.exit(1)
    
    # Initialize exchange client
    client = ExchangeClient(
        api_key=API_KEY,
        api_secret=API_SECRET,
        base_url=BASE_URL,
    )
    
    # Initialize trader
    trader = HighFreqPairTrader(client)
    
    # Run trader + stats reporter concurrently
    try:
        await asyncio.gather(
            trader.run(),
            stats_reporter(trader, interval=60),
        )
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        await trader.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
