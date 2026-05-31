# 策略阈值配置文件
# 修改本文件后需要重启策略进程，运行中的 Python 进程不会自动热加载配置。

# =========================
# Gate 交易所选币过滤
# =========================
# Gate 候选币 24H USD 成交额下限。
GATE_SELECTION_MIN_24H_USDT = 1_000_000
# Gate 候选币 24H 价格涨幅下限，单位：%。
GATE_SELECTION_MIN_24H_CHANGE = 5
# Gate 热门币通道 24H USD 成交额下限；该通道不要求 24H 涨幅为正。
GATE_HOT_SELECTION_MIN_24H_USDT = 10_000_000
# Gate 热门币通道最近 1m K 线单根成交额下限，单位：U。
GATE_HOT_SELECTION_MIN_1M_USDT = 10_000
# Gate 热门币通道最近 N 根 1m K 线中，至少多少根满足成交额要求。
GATE_HOT_SELECTION_REQUIRED_1M_COUNT = 7
# 最近 1m K 线单根成交额下限，单位：U。
GATE_SELECTION_MIN_1M_USDT = 8_000
# 最近 N 根 1m K 线中，至少多少根满足成交额要求。
GATE_SELECTION_REQUIRED_1M_COUNT = 7
# Gate 选币检查最近多少根 1m K 线。
GATE_SELECTION_KLINE_LIMIT = 10

# =========================
# XBT 单机器人盈利榜选币
# =========================
# XBT 榜单（按 1h 日化 / 收益率融合排序）取前 N 个币种作为候选；0 表示关闭 XBT 选币。
XBT_TOP_NUM = 3
# XBT 同一币种至少要有多少台机器人在榜，才认可（一致性过滤，>=2 可过滤单台运气）。
XBT_MIN_ROBOT_COUNT = 2

# =========================
# Grafana 数据与采样
# =========================
# 自动重启挡板使用的 Grafana 数据窗口。
GRAFANA_RESTART_HOUR = "1m"
# Grafana 自动重启分数缓存时间，单位：秒。
GRAFANA_RESTART_CACHE_SECONDS = 60
# 策略主判断使用的 Grafana 数据窗口。
GRAFANA_MARKET_HOUR = "30m"
# Grafana 主判断分数缓存时间，单位：秒。
GRAFANA_MARKET_CACHE_SECONDS = 55
# Grafana 30m 低收益参考线；只用于停机观察满窗口后的回收判断，不作为开仓/加仓过滤。
GRAFANA_MIN_30M_PROFIT = 50
# Grafana/账号历史采样间隔，单位：秒。
SYMBOL_SAMPLE_INTERVAL_SECONDS = 60
# 加仓、停机观察、减仓决策间隔，单位：秒。
SYMBOL_DECISION_INTERVAL_SECONDS = 60
# 每个币种内存历史保留时长，单位：秒；方案2+方案3需要至少60分钟历史。
SYMBOL_HISTORY_KEEP_SECONDS = 90 * 60
# 需要满多少历史数据才进入完整 15m 判断，单位：秒。
SYMBOL_HISTORY_READY_SECONDS = 15 * 60
# 预热期最少需要多少历史数据，单位：秒。
SYMBOL_WARMUP_READY_SECONDS = 5 * 60
# 同一币种历史采样点合并窗口，单位：秒。
SYMBOL_HISTORY_MERGE_SECONDS = 30
# 斜率和回撤计算的短窗口，单位：秒。
SYMBOL_SLOPE_SHORT_SECONDS = 5 * 60
# 斜率和回撤计算的长窗口，单位：秒。
SYMBOL_SLOPE_LONG_SECONDS = 15 * 60
# Grafana 多周期趋势判断窗口，单位：秒。
GRAFANA_TREND_1M_SECONDS = 60
GRAFANA_TREND_3M_SECONDS = 3 * 60
GRAFANA_TREND_5M_SECONDS = 5 * 60
GRAFANA_TREND_15M_SECONDS = 15 * 60
GRAFANA_TREND_30M_SECONDS = 30 * 60
GRAFANA_TREND_60M_SECONDS = 60 * 60
# Grafana 1m紧急停机阈值，单位：U。
GRAFANA_EMERGENCY_D1M = -10
# Grafana 1m极速加仓阈值，单位：U。
GRAFANA_FAST_ADD_D1M = 10
# Grafana 3m紧急停机阈值，单位：U。
GRAFANA_EMERGENCY_D3M = -15
# Grafana 1m转弱且加速度恶化的停机阈值，单位：U。
GRAFANA_EMERGENCY_WEAK_D1M = -5
# Grafana 紧急停机加速度阈值，单位：U。
GRAFANA_EMERGENCY_ACCEL = -10
# Grafana 深亏禁用阈值，单位：U。
GRAFANA_BAN_SCORE = -100
# Grafana 减仓预警3m阈值，单位：U。
GRAFANA_REDUCE_D3M = -5
# Grafana 减仓预警加速度阈值，单位：U。
GRAFANA_REDUCE_ACCEL = -10
# Grafana 顶部减速加速度阈值，单位：U。
GRAFANA_TOP_ACCEL = -15
# Grafana 新开仓3m上涨阈值，单位：U。
GRAFANA_OPEN_D3M = 3
# Grafana 假恢复试探开仓3m上涨阈值，单位：U。
GRAFANA_FAKE_RECOVERY_D3M = 5
# Grafana 假恢复试探开仓加速度阈值，单位：U。
GRAFANA_FAKE_RECOVERY_ACCEL = 5
# Grafana 满仓继续加仓5m上涨阈值，单位：U。
GRAFANA_FULL_ADD_D5M = 10
# Grafana 满仓继续加仓3m上涨阈值，单位：U。
GRAFANA_FULL_ADD_D3M = 5
# Grafana 满仓继续加仓加速度阈值，单位：U。
GRAFANA_FULL_ADD_ACCEL = 5

# =========================
# 加仓/新增开仓阈值
# =========================
# 单币最多允许多少个运行中机器人。
MAX_SYMBOL_ACTIVE_COUNT = 15
# 同币种新增开仓冷却时间，单位：秒。
SYMBOL_OPEN_COOLDOWN_SECONDS = 15 * 60
# 已有币种加仓冷却时间，单位：秒。
SYMBOL_ADD_COOLDOWN_SECONDS = 5 * 60
# 普通停机/减仓冷却时间，单位：秒。
SYMBOL_STOP_COOLDOWN_SECONDS = 5 * 60
# 允许加仓的 15m 最大回撤，单位：%。
ALLOCATION_MAX_DRAWDOWN_15M = 10
# 加仓复制等待位后延迟启动时间，单位：秒。
ALLOCATION_START_DELAY_SECONDS = 120
# 新增开仓复制等待位后延迟启动时间，单位：秒。
TOP_TEST_START_DELAY_SECONDS = 60

# =========================
# 停机观察、减仓、重启
# =========================
# 停机观察最长观察窗口，单位：秒；超过仍弱势则回收到 1_usdt。
STANDBY_MAX_SECONDS = 30 * 60
# 停机观察重启后的保护期，单位：秒。
STANDBY_RESTART_PROTECT_SECONDS = 10 * 60
# 停机观察记录写入后，允许机器人状态从运行变停机的宽限期，单位：秒。
STANDBY_RUNNING_GRACE_SECONDS = 60
# 收益保护阈值，普通减仓不动收益超过该值的机器人，单位：%。
PROFIT_PROTECT_THRESHOLD = 10
# 判定 frozen 的 15m 回撤阈值，单位：%。
STOP_DRAWDOWN_15M = 10
# 6 小时内止损次数达到该值，直接回收到等待位。
STOP_LOSS_DOWNGRADE_COUNT = 2
# 止损次数统计窗口，单位：分钟。
STOP_LOSS_DOWNGRADE_WINDOW_MINUTES = 360
# 自动启动后多久内不视为手动开机，单位：秒。
AUTO_START_DETECTION_WINDOW_SECONDS = 300
# 手动开机后暂停策略自动重启的冷却时间，单位：分钟。
MANUAL_START_COOLDOWN_MINUTES = 30

# =========================
# 价格止损补停
# =========================
# 盘口价格快速下跌触发停机的阈值，单位：%。
PRICE_STOP_THRESHOLD_PERCENT = -3.0
# 触发价格止损后，多久检查是否还有运行中机器人需要补停，单位：秒。
PRICE_STOP_SUPPLEMENT_CHECK_SECONDS = 20

# =========================
# 等待位回收
# =========================
# 手动停机超过该时间后回收到 1_usdt，单位：分钟。
MANUAL_STOP_RECYCLE_MINUTES = 5
# 命中特定限制日志后，停机多久回收到 1_usdt，单位：分钟。
LIMIT_SILENCE_WAIT_MINUTES = 30
# 止损出错且收益低于该值时回收到 1_usdt，单位：%。
FAILED_STOP_LOSS_RECYCLE_PROFIT_PERCENT = -4
# 运行时长回收规则；格式：(运行超过多少分钟, 收益率小于等于多少%)。
RUNTIME_RECYCLE_RULES = [
    (60 * 1, 0.3),
    (60 * 3, 1),
    (60 * 6, 2),
    (60 * 12, 4),
    (60 * 24, 6),
    (60 * 48, 8),
]

# =========================
# 淘汰策略
# =========================
# frozen 币种低收益淘汰检查间隔，单位：秒。
ELIMINATE_STRATEGY_INTERVAL_SECONDS = 11111
# 每次淘汰最多回收多少个机器人。
ELIMINATE_STRATEGY_COUNT = 2

# =========================
# 循环与复制参数
# =========================
# 后台巡检循环空闲等待时间，单位：秒。
LOOP_SLEEP_SECONDS = 10
# 复制参数中的 bias window。
COPY_BIAS_WINDOW = 20
# 复制参数 open 的随机区间下限。
COPY_OPEN_MIN = 4
# 复制参数 open 的随机区间上限。
COPY_OPEN_MAX = 5
# 复制参数 close 的随机区间下限。
COPY_CLOSE_MIN = 0.9
# 复制参数 close 的随机区间上限。
COPY_CLOSE_MAX = 1.2
# 初始资金异常时使用的默认杠杆。
COPY_DEFAULT_LEVER = 2
# 初始资金异常时使用的默认止损。
COPY_DEFAULT_STOP_LOSS = 0.03
# 动态杠杆计算的目标资金基数。
COPY_LEVER_BALANCE_TARGET = 380
# 动态杠杆下限。
COPY_MIN_LEVER = 2
# 动态杠杆上限。
COPY_MAX_LEVER = 3
# 动态止损基础值；最终止损 = 基础值 + 杠杆 / 100。
COPY_STOP_LOSS_BASE = 0.02
