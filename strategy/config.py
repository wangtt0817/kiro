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

# Total cost per round (4 maker orders: open 2 legs + close 2 legs)
ROUND_TRIP_COST = MAKER_FEE * 4  # 0.02%

# ============================================================
# Strategy Parameters
# ============================================================

# --- Signal Generation ---
EMA_PERIOD = 20              # EMA window (in minutes / candles)
SCAN_INTERVAL = 2            # Seconds between each scan cycle

# --- Entry / Exit Thresholds (as % of ratio) ---
OPEN_THRESHOLD = 0.0005      # 0.05% deviation to open
CLOSE_THRESHOLD = 0.0001     # 0.01% deviation to close (near mean)
ADD_THRESHOLD_2 = 0.0008     # 0.08% for 2nd layer
ADD_THRESHOLD_3 = 0.0012     # 0.12% for 3rd layer

# --- Take Profit & Stop Loss ---
TARGET_PROFIT = 0.0005       # 0.05% gross profit per round
STOP_LOSS = 0.0015           # 0.15% max loss per round

# --- Timing ---
MAX_HOLD_SECONDS = 1800      # 30 minutes max hold time
LEG2_TIMEOUT = 3             # 3 seconds to fill 2nd leg
LEG1_TIMEOUT = 10            # 10 seconds to fill 1st leg
CLOSE_TIMEOUT = 5            # 5 seconds for close order to fill

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
