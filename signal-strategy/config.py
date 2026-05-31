"""signal-strategy — 全局配置与真实数据标定常量

所有常量均来自 analysis_outputs/ 的真实交叉分析 (2026-04-27 ~ 2026-05-27, 30天)：
  - deep_cross_analysis.json          STEP 6/7/8/10
  - grafana_profit_loss_gate_state_summary.json   状态 + stress 信号命中
  - 盈利数据.txt                       STEP 9 时段热力

⚠️ 重要：这些数字是历史样本统计，并非未来保证。所有阈值都应：
   1. 用滚动窗口动态复核（季度级）
   2. 在样本外 (recent_1m) 上验证后才上线
"""

# ============================================================
# 资金 / 成本
# ============================================================
INITIAL_CAPITAL = 10_000.0      # 账户初始权益 (U)
TAKER_FEE = 0.0005              # 单边吃单手续费 (Gate 永续 ~0.05%)
SLIPPAGE = 0.0003              # 单边滑点估计
ROUND_TRIP_COST = 2 * (TAKER_FEE + SLIPPAGE)   # 一进一出总成本 ≈ 0.16%

# 仓位 / 风控
BASE_RISK_FRAC = 0.010         # 单笔基础仓位 (占当前权益)
MAX_POSITION_FRAC = 0.05       # 单笔硬上限
MAX_DAILY_LOSS_FRAC = 0.04     # 单日亏损上限 (触发后当日停手)
MAX_CONCURRENT = 6             # 最大同时持仓数

# K线口径
BAR_MINUTES = 30               # 1 根 K线 = 30 分钟
DEFAULT_HOLD_BARS = 6          # 默认持仓 6 根 (=3 小时)

# ============================================================
# 方向 A: 反追涨均值回归 — STEP 7 滞后性分析
# 按"信号出现时价格过去3h已涨幅"分桶，给出方向与历史 edge
#   (low, high]      ->  side,  edge_pct(历史中位 fwd 收益, 已折算方向), hold_bars
# 数据来源 (后续3根/6根 K线收益中位数):
#   <1%   : up3 50.3%, fwd6 -0.16%      -> 中性, 不交易
#   1-3%  : up3 50.8%, fwd6 +0.09%      -> 轻多
#   3-5%  : up3 51.0%, fwd6 +0.25%      -> 跟随做多 (甜区)
#   5-10% : up3 44.8%, fwd3 -0.65%      -> fade 做空
#   >10%  : up3 45.9%, fwd6 -1.88%      -> 强 fade 做空
# ============================================================
LAG_BUCKETS = [
    # (past_ret_low, past_ret_high, side, raw_edge_pct, hold_bars)
    (-1e9,  1.0,  "FLAT",  0.00, 0),    # 价格未启动: 信号滞后但无方向, 不交易
    (1.0,   3.0,  "LONG",  0.09, 6),    # 小启动: 轻仓跟随
    (3.0,   5.0,  "LONG",  0.25, 6),    # 明显上涨: 跟随甜区
    (5.0,   10.0, "SHORT", 0.65, 3),    # 大幅上涨: fade (用 3 根, edge 在 fwd3)
    (10.0,  1e9,  "SHORT", 1.88, 6),    # 暴涨后: 强 fade
]

# 方向 A 入场门槛: 历史 edge 必须覆盖往返成本的倍数
A_EDGE_COST_MULT = 2.0         # raw_edge_pct/100 >= ROUND_TRIP_COST * 2 才入场

# ============================================================
# 方向 B: 恐慌反转抄底 — STEP 6 分层 (超大盈利 >1000U)
#   过去3h跌幅中位 -11.24%, 未来1根 +3.04%, 上涨概率 54.1%
# 触发条件: 急跌 + 大额盈利信号 + 止跌确认
# ============================================================
CAPITULATION_PAST_DROP = -8.0   # 过去3h跌幅 <= -8% (capitulation)
CAPITULATION_MIN_SCORE = 500.0  # gf_score_30m >= 500U (大额事件代理)
CAPITULATION_STOP_FALLING = 0.0 # ret_5m >= 0 (不再创新低, 止跌确认)
CAPITULATION_EDGE_PCT = 3.04    # 历史未来1根收益中位
CAPITULATION_HOLD_BARS = 2      # 反弹快且短, 持 1-3 根
CAPITULATION_STOP_PCT = 3.0     # 再创新低 -3% 止损

# ============================================================
# 方向 C: 趋势/震荡判别 — state_summary 的 stress / stable 指标
#   震荡(range): 均值回归有效;  趋势/承压(trend/stress): 关闭逆势
# ============================================================
REGIME_TREND_AMP_15M = 6.0      # amp_15m >= 6 -> 趋势/承压 (state E/A 高振幅)
REGIME_TREND_VOL_RATIO = 3.0    # vol_ratio_5v30 >= 3 -> 放量承压
REGIME_TREND_RET_5M = 3.0       # |ret_5m| >= 3% -> 单边动量
REGIME_RANGE_AMP_15M = 4.0      # amp_15m < 4 -> 偏震荡
REGIME_RANGE_VOL_LOW = 0.6      # vol_ratio 下界
REGIME_RANGE_VOL_HIGH = 1.5     # vol_ratio 上界
# 各 regime 对各方向的仓位乘子
REGIME_MULT = {
    # regime:        A(reversion)  B(capitulation)
    "range":        {"A": 1.0,  "B": 0.8},
    "trend":        {"A": 0.3,  "B": 0.5},   # 趋势市禁止重仓 fade
    "neutral":      {"A": 0.7,  "B": 0.7},
}

# ============================================================
# 方向 D: 时段执行闸门 — STEP 9 UTC 时段热力 (净盈利, 胜率)
# 乘子 0.0 = 禁止开新仓; 1.0 = 黄金时段全仓
# 标定逻辑: 净亏损且胜率<=0.50 的时段禁止; 其余按胜率分档
# ============================================================
SESSION_MULT = {
    0: 0.20,  1: 0.20,  2: 0.65,  3: 0.80,  4: 0.50,  5: 0.90,
    6: 0.85,  7: 0.00,  8: 0.85,  9: 0.85, 10: 1.00, 11: 0.85,
    12: 1.00, 13: 0.90, 14: 1.00, 15: 0.90, 16: 1.00, 17: 0.70,
    18: 0.50, 19: 0.00, 20: 0.65, 21: 0.00, 22: 0.50, 23: 0.00,
}
# 参考 (净盈利U / 胜率)，仅注释，不参与计算:
#   黄金: 10(+202K/61%) 16(+255K/60%) 14(+238K/59%) 12(+134K/60%)
#   禁止: 23(-542K/44%) 07(-266K/50%) 19(-77K/52%) 21(-25K/48%)

# ============================================================
# 方向 E: 标的白/黑名单 — STEP 8 代币级别盈亏
# ============================================================
WHITELIST = {
    "lab_usdt", "skyai_usdt", "beat_usdt", "bsb_usdt", "wld_usdt",
    "near_usdt", "saga_usdt", "bio_usdt", "ub_usdt", "hype_usdt",
    "dogs_usdt", "vvv_usdt", "swarms_usdt", "zerebro_usdt", "grass_usdt",
}
# 结构性亏损 — 禁止信号跟随 (btc_usdt 单独 -390K)
BLACKLIST = {
    "btc_usdt", "gua_usdt", "zkj_usdt", "siren_usdt", "esports_usdt",
    "dam_usdt", "genius_usdt", "fartcoin_usdt", "aia_usdt", "inx_usdt",
}
UNIVERSE_MULT = {"white": 1.0, "neutral": 0.6, "black": 0.0}

# ============================================================
# 全局 Stress 过滤 — signal_* 命中统计 (坏结果占比)
#   gate_5m_down_3 : 触发时 61.1% 是坏结果
#   amp_15m>=6     : 59.7% 坏
#   vol_ratio>=3   : 48.3% 坏
# 命中任一 stress 信号则直接跳过该 bar (除非是方向B的止跌反弹)
# ============================================================
STRESS_RET_5M_DOWN = -3.0       # ret_5m <= -3%
STRESS_AMP_15M = 6.0
STRESS_VOL_RATIO = 3.0

# ============================================================
# 方向 F: 足球策略真实退出修复 (football_fix.py)
# ============================================================
FOOTBALL_FILL_RATE_REAL = 0.10  # 真实低流动性成交率 (替代 0.28-0.55)
FOOTBALL_FEE_RATE = 0.02
FOOTBALL_SLIPPAGE = 0.002
