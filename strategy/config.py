"""
MemeMax BTC/ETH High-Frequency Pair Trading Strategy - Configuration
"""

# ============================================================
# Exchange Configuration
# ============================================================
EXCHANGE_NAME = "mememax"
BASE_URL = "https://api.mememax.io"  # Replace with actual MemeMax API URL
API_KEY = ""       # Fill in your API key
API_SECRET = ""    # Fill in your API secret

# Trading pairs
BTC_SYMBOL = "BTC/USDT:USDT"  # BTC perpetual
ETH_SYMBOL = "ETH/USDT:USDT"  # ETH perpetual

# ============================================================
# Fee Structure
# ============================================================
MAKER_FEE = 0.00005   # 0.005%
TAKER_FEE = 0.0015    # 0.15%

# Round-trip cost in best case (4 Maker orders).
# DO NOT use this alone for profit target — it ignores slippage,
# Taker fallback, funding, and oracle drift. Use CostModel instead.
ROUND_TRIP_COST_BEST_CASE = MAKER_FEE * 4  # 0.02%

# ============================================================
# Cost Model (used to compute dynamic profit target)
# ============================================================
# These defaults represent observed/estimated costs per round.
# All values are in decimal (0.0001 = 0.01%).
#
# When the bot is running, slippage is auto-measured from real fills
# and overrides the default. Funding cost is computed from real funding
# rates and current hold duration.

# Default open+close slippage estimate before we have live data
# Conservative estimate for a Perp DEX with moderate liquidity
DEFAULT_SLIPPAGE_PER_ROUND = 0.0004  # 0.04% (combined open + close, both legs)

# Default oracle / price drift on a DEX
DEFAULT_ORACLE_DRIFT = 0.0001  # 0.01%

# Probability that we'll be forced to use Taker on one leg (close failure)
# Used to add expected Taker premium into the target
TAKER_FALLBACK_PROBABILITY = 0.05  # 5% of rounds estimated to need Taker

# Safety margin: extra buffer above computed cost
# This is what protects us when the cost model under-estimates reality
SAFETY_MARGIN_MULTIPLIER = 1.5  # Target = computed_cost * 1.5

# Minimum acceptable target profit (floor)
# If computed target falls below this, skip the trade entirely
MIN_TARGET_PROFIT = 0.0008  # 0.08% — below this, no edge

# Maximum target profit (ceiling)
# If computed target exceeds this, market is too unfavorable, skip
MAX_TARGET_PROFIT = 0.005   # 0.50% — above this, signal probably won't hit

# ============================================================
# Strategy Parameters
# ============================================================

# --- Signal Generation ---
EMA_PERIOD = 20              # EMA window (in minutes / candles)
SCAN_INTERVAL = 2            # Seconds between each scan cycle

# --- Entry / Exit Thresholds (as % of ratio deviation from EMA) ---
# These are RAISED from the previous 0.05% to ensure the signal has
# enough room above the dynamic cost target.
# Open threshold should be >= MIN_TARGET_PROFIT * 1.5 to leave room.
OPEN_THRESHOLD = 0.0012      # 0.12% deviation to open (was 0.05%)
CLOSE_THRESHOLD = 0.0001     # 0.01% deviation to close (near mean)
ADD_THRESHOLD_2 = 0.0018     # 0.18% for 2nd layer (was 0.08%)
ADD_THRESHOLD_3 = 0.0025     # 0.25% for 3rd layer (was 0.12%)

# --- Take Profit & Stop Loss ---
# TARGET_PROFIT is now a HARD FLOOR; the dynamic CostModel will compute
# a higher target if conditions warrant. The trader will use:
#   actual_target = max(TARGET_PROFIT, cost_model.required_target())
TARGET_PROFIT = 0.0008       # 0.08% MINIMUM gross profit per round (was 0.05%)
STOP_LOSS = 0.0025           # 0.25% max loss per round (was 0.15%)

# --- Timing ---
MAX_HOLD_SECONDS = 1800      # 30 minutes max hold time
LEG2_TIMEOUT = 3             # 3 seconds to fill 2nd leg
LEG1_TIMEOUT = 10            # 10 seconds to fill 1st leg
CLOSE_TIMEOUT = 5            # 5 seconds for close order to fill
MAX_CLOSING_SECONDS = 60     # Max time in CLOSING_BOTH before forcing taker on both

# ============================================================
# Position Sizing
# ============================================================
LEVERAGE = 2                         # 2x leverage
POSITION_SIZE_RATIO = 0.10           # 10% of equity per leg per layer
MAX_LAYERS = 3                       # Max 3 concurrent layers
MAX_MARGIN_USAGE = 0.60              # Max 60% margin utilization

# ============================================================
# Risk Management
# ============================================================
MAX_DAILY_LOSS_PCT = 0.01            # 1% daily loss -> stop trading
MAX_CONSECUTIVE_LOSSES = 5           # 5 consecutive losses -> pause 30min
PAUSE_AFTER_CONSECUTIVE_LOSS = 1800  # 30 min pause (seconds)
HOURLY_LOSS_PAUSE_PCT = 0.005        # 0.5% hourly loss -> pause 1 hour
HOURLY_PAUSE_DURATION = 3600         # 1 hour pause (seconds)

# ============================================================
# Order Management
# ============================================================
# Place limit orders at best bid/ask +/- offset ticks
MAKER_OFFSET_TICKS = 1       # How many ticks inside spread to place order
ORDER_REFRESH_INTERVAL = 1   # Seconds before refreshing unfilled orders

# Aggressive close mode (CLOSING_REMAINING state) — how far inside spread
AGGRESSIVE_CLOSE_DEPTH = 0.30  # 30% into spread, max 50% before becoming taker-equivalent

# Slippage tracking
SLIPPAGE_WINDOW_SIZE = 50    # Track last 50 trades for rolling slippage estimate
