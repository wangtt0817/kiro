"""PM足球策略 — 全局配置"""

BASE_DIR = "/home/ubuntu/polymarket-football"
DATA_DIR = f"{BASE_DIR}/data/clean"
RESULTS_DIR = f"{BASE_DIR}/results"

# 资金参数
INITIAL_CAPITAL = 10000.0
FEE_RATE = 0.02

# Kelly 参数
KELLY_FRAC = 0.30          # 保守 Kelly 分数 (提高以增加仓位)
KELLY_FRAC_AGGRESSIVE = 0.6  # 激进 Kelly 分数 (提高以增加仓位)
MAX_BET_PCT = 0.025        # 最大单笔仓位 (占初始资金)
MAX_BET_PCT_AGGRESSIVE = 0.06
MAX_DAILY_LOSS_PCT = 0.05  # 每日亏损上限

# 做市参数
FILL_RATE = 0.12           # 模拟成交率 (保守策略) - 保持原始值保护趋势策略
FILL_RATE_AGGRESSIVE = 0.38 # 激进策略成交率 (更紧的价差 → 更高成交率)
MIN_BID = 0.003            # 最低挂单价 (降低以覆盖更多低价市场)
SLIPPAGE = 0.002           # 滑点 (0.2%)
LIQUIDITY_THRESHOLD = 0.05 # 流动性阈值 (价格<5%时流动性差)

# 趋势参数
TREND_WINDOWS = [3, 5, 7, 14]  # 多时间框架窗口

# World Cup 2026 日期
from datetime import datetime
WC_START = datetime(2026, 6, 11)
EPL_END = datetime(2026, 5, 25)

# 策略状态标记
STRATEGY_STATUS = {
    "S6_Conservative":     "✅ 推荐 — 保守做市",
    "S6_Aggressive":       "✅ 推荐 — 激进做市",
    "S11_Trend":           "✅ 推荐 — 多时间框架趋势",
    "S19_HighConviction":  "❌ 淘汰 — 信号质量不足",
    "S20_DynamicKelly":    "⚠️ 实验 — 回测偏差",
    "S22_Combo":           "✅ 推荐 — 做市+趋势组合",
    "S23_AggressiveKelly": "⚠️ 实验 — 回测偏差",
    "S24_MultiWindow":     "✅ 推荐 — 四窗口趋势",
    "S25_DynamicSpread":   "✅ 最优 — 动态价差做市",
    "S26_RSIContrarian":   "🧪 新策略 — RSI反转",
    "S27_MeanRevert":      "🧪 新策略 — 均值回归",
    "S28_SmartMM":         "🧪 新策略 — 智能做市",
}

# 推荐配置
RECOMMENDED_ALLOCATION = {
    "S25_DynamicSpread": 0.40,
    "S22_Combo": 0.30,
    "S24_MultiWindow": 0.30,
}
