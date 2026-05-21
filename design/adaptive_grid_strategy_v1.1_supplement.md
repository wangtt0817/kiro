# 趋势自适应网格做市策略 v1.1 补全

## 针对三大问题的系统性改进

> 本文档是 `adaptive_grid_strategy_v1.md` 的补全升级，解决三个核心缺陷：
> 1. 趋势判断不够及时（缺加速度、缺放量检测）
> 2. 风控逻辑不全面（缺止损、缺极端行情应对）
> 3. 缺少 1m/5m 多周期验证（纯 tick 级信号无 K 线确认）

---

## A. 趋势判断增强：加速度 + 放量 + 反转检测

### A.1 当前缺陷分析

| 问题 | 原 v1.0 设计 | 后果 |
|------|-------------|------|
| 无加速度信号 | 只看位移，不看速度变化率 | 趋势加速时反应不够 |
| 无成交量突变 | 不区分放量突破 vs 缩量漂移 | 虚假突破误判 |
| EWMA平滑太重 | 半衰期3秒在快速V反转中滞后 | 错过反转点2-3秒 |
| 300s动量太慢 | 对1m/5m短线是"历史" | 无法捕捉短周期反转 |


### A.2 新增：趋势加速度信号

```python
class TrendAccelerationDetector:
    """检测趋势是在加速还是减速——加速意味着趋势正在形成"""
    
    def __init__(self):
        self.trend_buffer = RingBuffer(maxlen=30)  # 保存30秒的trend_score历史
    
    def update(self, trend_score):
        self.trend_buffer.append(trend_score)
    
    def compute_acceleration(self) -> float:
        """
        返回加速度因子 ∈ [0, 2.0]
        0 = 趋势在减速或反转
        1.0 = 正常速度
        2.0 = 趋势在强加速
        """
        if len(self.trend_buffer) < 15:
            return 1.0  # 数据不足，不调整
        
        trend_now = self.trend_buffer[-1]
        trend_5s = self.trend_buffer[-5]
        trend_15s = self.trend_buffer[-15]
        
        # 短期加速度（5秒内趋势变化速度）
        accel_short = (trend_now - trend_5s) / 5.0
        # 中期加速度（15秒内趋势变化速度）
        accel_mid = (trend_now - trend_15s) / 15.0
        
        # 三个条件同向且递增 → 趋势正在加速形成
        if (sign(accel_short) == sign(accel_mid) == sign(trend_now) 
            and abs(accel_short) > abs(accel_mid)):
            # 加速确认：加速越快，因子越大
            factor = 1.0 + min(1.0, abs(accel_short) * 10)
            return factor
        
        # 加速度与趋势方向相反 → 趋势在减速
        elif sign(accel_short) != sign(trend_now):
            return max(0.3, 1.0 - abs(accel_short) * 5)
        
        return 1.0
```


### A.3 新增：成交量突变检测

```python
class VolumeSurgeDetector:
    """
    检测成交量是否突然放大
    放量 + 趋势同向 = 真突破
    放量 + 趋势反向 = 可能反转
    缩量 + 趋势 = 虚假突破，趋势评分应衰减
    """
    
    def __init__(self):
        self.volume_10s_buffer = RingBuffer(maxlen=60)  # 60个10s窗口
    
    def update(self, volume_10s: float):
        self.volume_10s_buffer.append(volume_10s)
    
    def detect(self) -> tuple:
        """
        返回: (surge_level, volume_ratio)
        surge_level: 'CLIMAX' | 'SURGE' | 'ELEVATED' | 'NORMAL' | 'DRY'
        volume_ratio: 当前量/平均量
        """
        if len(self.volume_10s_buffer) < 12:
            return 'NORMAL', 1.0
        
        current = self.volume_10s_buffer[-1]
        avg_60s = mean(self.volume_10s_buffer[-6:])   # 近60秒均量
        avg_300s = mean(self.volume_10s_buffer[-30:]) # 近5分钟均量
        
        ratio = current / (avg_300s + 1e-10)
        
        if ratio > 5.0:
            return 'CLIMAX', ratio    # 天量：可能是高潮/反转
        elif ratio > 3.0:
            return 'SURGE', ratio     # 放量突破
        elif ratio > 1.5:
            return 'ELEVATED', ratio  # 量能抬升
        elif ratio < 0.3:
            return 'DRY', ratio       # 缩量：趋势不可信
        else:
            return 'NORMAL', ratio
    
    def adjust_trend_confidence(self, trend_score, surge_level, volume_ratio):
        """根据量价关系调整趋势置信度"""
        if surge_level == 'SURGE' or surge_level == 'ELEVATED':
            # 放量且趋势明确 → 置信度提升
            return min(1.0, abs(trend_score) * 1.3)
        elif surge_level == 'CLIMAX':
            # 天量可能是高潮反转 → 趋势置信度降低
            return abs(trend_score) * 0.5
        elif surge_level == 'DRY':
            # 缩量趋势 → 不可信
            return abs(trend_score) * 0.4
        else:
            return abs(trend_score)
```


### A.4 新增：快速反转检测

```python
class ReversalDetector:
    """
    检测 V 形反转——做市策略最危险的场景之一
    趋势突然反转时，刚建立的逆势库存立即变成顺势利润
    但如果不及时调整，原来的顺势侧又会变成新的逆势侧
    """
    
    def __init__(self):
        self.trend_peak = 0.0
        self.trend_peak_time = 0
    
    def check_reversal(self, trend_score, timestamp) -> dict:
        """
        返回: {
            'is_reversing': bool,
            'reversal_speed': float,  # 反转速度（越大越急）
            'action': str  # 'NONE' | 'WIDEN_ALL' | 'FLATTEN'
        }
        """
        # 更新峰值
        if abs(trend_score) > abs(self.trend_peak):
            self.trend_peak = trend_score
            self.trend_peak_time = timestamp
        
        # 从峰值回落多少
        if abs(self.trend_peak) > 0.5:  # 只在有明确趋势后检测反转
            retreat = self.trend_peak - trend_score  # 正值=从多头峰值回落
            retreat_ratio = retreat / (abs(self.trend_peak) + 1e-10)
            time_since_peak = timestamp - self.trend_peak_time
            
            if retreat_ratio > 0.6 and time_since_peak < 10:
                # 10秒内从峰值回撤60%以上 → 急速反转
                reversal_speed = retreat_ratio / (time_since_peak + 1)
                return {
                    'is_reversing': True,
                    'reversal_speed': reversal_speed,
                    'action': 'WIDEN_ALL'  # 两侧都加宽，等信号明确
                }
            elif retreat_ratio > 1.2 and time_since_peak < 20:
                # 20秒内趋势完全翻转（从+0.7变成-0.1）
                return {
                    'is_reversing': True,
                    'reversal_speed': retreat_ratio / (time_since_peak + 1),
                    'action': 'FLATTEN'  # 考虑平掉逆势库存
                }
        
        # 峰值衰减（超过30秒峰值重置）
        if timestamp - self.trend_peak_time > 30:
            self.trend_peak *= 0.9
        
        return {'is_reversing': False, 'reversal_speed': 0, 'action': 'NONE'}
```

### A.5 改进后的趋势引擎（v1.1 版）

```python
def compute_trend_score_v11(self):
    """v1.1: 在v1.0基础上增加加速度、量价、反转检测"""
    
    # === 原v1.0信号（保留） ===
    raw_trend, raw_confidence = self.compute_trend_score_v10()
    
    # === 新增：加速度调整 ===
    accel_factor = self.acceleration_detector.compute_acceleration()
    
    # === 新增：量价验证 ===
    surge_level, vol_ratio = self.volume_detector.detect()
    vol_adjusted_confidence = self.volume_detector.adjust_trend_confidence(
        raw_trend, surge_level, vol_ratio
    )
    
    # === 新增：反转检测 ===
    reversal = self.reversal_detector.check_reversal(raw_trend, time.now())
    
    # === 综合调整 ===
    if reversal['is_reversing']:
        # 反转中：趋势评分快速衰减，间距两侧加宽
        effective_trend = raw_trend * 0.3  # 趋势衰减70%
        effective_confidence = 0.3
    else:
        # 正常：加速度放大趋势效果
        effective_trend = raw_trend * min(accel_factor, 1.5)
        effective_confidence = vol_adjusted_confidence
    
    # EWMA平滑调整：v1.1用更短的半衰期（1.5秒而非3秒）
    α_trend_v11 = 1 - exp(-1 / 1.5)
    smoothed = α_trend_v11 * effective_trend + (1 - α_trend_v11) * self.prev_trend
    
    return smoothed, effective_confidence, {
        'accel_factor': accel_factor,
        'surge_level': surge_level,
        'reversal': reversal,
    }
```


---

## B. 完整风控体系：六层止损 + 动态仓位

### B.1 风控缺陷总结（v1.0）

| 缺失项 | 风险等级 | 说明 |
|--------|---------|------|
| 单笔止损 | 致命 | 单笔浮亏无上限 |
| 单日止损 | 严重 | 只有小时级，无日级硬止 |
| 连续亏损熔断 | 严重 | 连亏不降频 |
| 最大回撤止损 | 严重 | 从峰值回撤无上限 |
| 极端行情全平 | 致命 | 黑天鹅时无逃生机制 |
| 仓位衰减 | 中等 | 亏损后不自动降仓 |
| 资金费率 | 中等 | 永续合约funding极端不处理 |
| 流动性枯竭 | 严重 | spread暴涨无强制退出 |

### B.2 六层风控体系

```python
class RiskManager:
    """
    六层递进式风控体系
    层级越高 = 触发条件越极端 = 处置越激烈
    
    L1: 单笔止损（微观）
    L2: 连续亏损熔断（短期）
    L3: 时间窗口止损（中期）
    L4: 最大回撤保护（宏观）
    L5: 极端行情应急（黑天鹅）
    L6: 环境适用性过滤（背景）
    """
    
    def __init__(self, config):
        # ===== L1: 单笔止损 =====
        self.SINGLE_STOP_BPS = 50          # 单笔浮亏50bps强平
        self.SINGLE_MAX_HOLD_SEC = 180     # 单笔最多持仓3分钟
        self.SINGLE_TRAIL_STOP_BPS = 30    # 浮盈回撤30bps保护利润
        
        # ===== L2: 连续亏损 =====
        self.MAX_CONSECUTIVE_LOSSES = 5    # 连亏5笔触发
        self.LOSS_COOLDOWN_SEC = 300       # 冷却5分钟
        self.LOSS_SIZE_DECAY = 0.5         # 恢复后仓位减半
        self.LOSS_RESET_WINS = 3           # 连赢3笔恢复正常仓位
        
        # ===== L3: 时间窗口止损 =====
        self.LOSS_5MIN_BPS = -30           # 5分钟亏30bps → 降频
        self.LOSS_HOURLY_BPS = -80         # 小时亏80bps → 暂停1h
        self.LOSS_DAILY_BPS = -200         # 日亏200bps → 暂停当日
        self.LOSS_DAILY_HARD_BPS = -300    # 日亏300bps → 全平+停机
        
        # ===== L4: 最大回撤 =====
        self.DRAWDOWN_WARN_PCT = 2.0       # 回撤2% → 仓位减半
        self.DRAWDOWN_STOP_PCT = 3.5       # 回撤3.5% → 暂停
        self.DRAWDOWN_HARD_PCT = 5.0       # 回撤5% → 全平+停机
        
        # ===== L5: 极端行情 =====
        self.BLACK_SWAN_1M_BPS = 200       # 1m K线波幅>200bps
        self.BLACK_SWAN_5M_BPS = 500       # 5m K线波幅>500bps
        self.SPREAD_EXTREME_X = 8.0        # spread是正常的8倍
        self.DEPTH_COLLAPSE_RATIO = 0.2    # 盘口深度骤降至正常20%
        
        # ===== L6: 环境过滤 =====
        self.FUNDING_RATE_LIMIT = 0.1      # funding > 0.1%/8h
        self.MIN_BOOK_DEPTH_USD = 5000     # 前5档 < $5000
        self.MAX_SPREAD_BPS = 30           # 常态spread > 30bps不做
        self.MAINTENANCE_HOURS = [(4, 5)]  # UTC 4:00-5:00 不做（结算时段）
        
        # 状态追踪
        self.equity_peak = 0.0
        self.consecutive_losses = 0
        self.daily_pnl_bps = 0.0
        self.hourly_pnl_bps = 0.0
        self.five_min_pnl_bps = 0.0
        self.size_multiplier = 1.0
```


    def check(self, state) -> dict:
        """
        主检查函数，返回风控决策
        返回: {
            'action': str,       # CONTINUE|REDUCE|PAUSE|FLAT_ALL|SHUTDOWN
            'reason': str,
            'size_mult': float,  # 仓位乘数 0~1
            'cooldown_sec': int  # 冷却时间
        }
        """
        result = {'action': 'CONTINUE', 'reason': '', 
                  'size_mult': 1.0, 'cooldown_sec': 0}
        
        # --- L5 最优先：极端行情（黑天鹅） ---
        if state.last_1m_range_bps > self.BLACK_SWAN_1M_BPS:
            return {'action': 'FLAT_ALL', 
                    'reason': f'黑天鹅: 1m波幅{state.last_1m_range_bps:.0f}bps',
                    'size_mult': 0, 'cooldown_sec': 600}
        
        if state.last_5m_range_bps > self.BLACK_SWAN_5M_BPS:
            return {'action': 'FLAT_ALL',
                    'reason': f'黑天鹅: 5m波幅{state.last_5m_range_bps:.0f}bps',
                    'size_mult': 0, 'cooldown_sec': 600}
        
        if state.spread_ratio > self.SPREAD_EXTREME_X:
            return {'action': 'FLAT_ALL',
                    'reason': f'流动性枯竭: spread={state.spread_ratio:.1f}x',
                    'size_mult': 0, 'cooldown_sec': 300}
        
        if state.depth_ratio < self.DEPTH_COLLAPSE_RATIO:
            return {'action': 'FLAT_ALL',
                    'reason': f'深度崩塌: 仅剩正常{state.depth_ratio*100:.0f}%',
                    'size_mult': 0, 'cooldown_sec': 300}
        
        # --- L4：最大回撤 ---
        drawdown = self._compute_drawdown(state.equity)
        if drawdown > self.DRAWDOWN_HARD_PCT:
            return {'action': 'SHUTDOWN',
                    'reason': f'回撤{drawdown:.1f}%超硬限',
                    'size_mult': 0, 'cooldown_sec': 86400}
        elif drawdown > self.DRAWDOWN_STOP_PCT:
            return {'action': 'PAUSE',
                    'reason': f'回撤{drawdown:.1f}%暂停',
                    'size_mult': 0, 'cooldown_sec': 3600}
        elif drawdown > self.DRAWDOWN_WARN_PCT:
            result['size_mult'] = min(result['size_mult'], 0.5)
            result['reason'] = f'回撤{drawdown:.1f}%降仓'
        
        # --- L3：时间窗口止损 ---
        if state.daily_pnl_bps < self.LOSS_DAILY_HARD_BPS:
            return {'action': 'SHUTDOWN',
                    'reason': f'日亏{state.daily_pnl_bps:.0f}bps硬止',
                    'size_mult': 0, 'cooldown_sec': 86400}
        elif state.daily_pnl_bps < self.LOSS_DAILY_BPS:
            return {'action': 'PAUSE',
                    'reason': f'日亏{state.daily_pnl_bps:.0f}bps暂停',
                    'size_mult': 0, 'cooldown_sec': 14400}
        elif state.hourly_pnl_bps < self.LOSS_HOURLY_BPS:
            return {'action': 'PAUSE',
                    'reason': f'时亏{state.hourly_pnl_bps:.0f}bps暂停',
                    'size_mult': 0, 'cooldown_sec': 3600}
        elif state.five_min_pnl_bps < self.LOSS_5MIN_BPS:
            result['size_mult'] = min(result['size_mult'], 0.5)
            result['reason'] = f'5min亏{state.five_min_pnl_bps:.0f}bps降频'
        
        # --- L2：连续亏损 ---
        if state.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            return {'action': 'PAUSE',
                    'reason': f'连亏{state.consecutive_losses}笔冷却',
                    'size_mult': 0, 'cooldown_sec': self.LOSS_COOLDOWN_SEC}
        elif state.consecutive_losses >= 3:
            decay = self.LOSS_SIZE_DECAY ** (state.consecutive_losses - 2)
            result['size_mult'] = min(result['size_mult'], decay)
        
        # --- L1：单笔止损 ---
        for pos in state.open_positions:
            # 浮亏止损
            if pos.unrealized_pnl_bps < -self.SINGLE_STOP_BPS:
                result['action'] = 'FORCE_CLOSE_SINGLE'
                result['reason'] = f'单笔浮亏{pos.unrealized_pnl_bps:.0f}bps止损'
                result['close_position_id'] = pos.id
                return result
            # 超时止损
            if pos.hold_seconds > self.SINGLE_MAX_HOLD_SEC:
                result['action'] = 'FORCE_CLOSE_SINGLE'
                result['reason'] = f'持仓{pos.hold_seconds}s超时平仓'
                result['close_position_id'] = pos.id
                return result
            # 移动止盈（浮盈回撤保护）
            if pos.max_unrealized_pnl_bps > 20:  # 曾浮盈超20bps
                trail_loss = pos.max_unrealized_pnl_bps - pos.unrealized_pnl_bps
                if trail_loss > self.SINGLE_TRAIL_STOP_BPS:
                    result['action'] = 'FORCE_CLOSE_SINGLE'
                    result['reason'] = f'移动止盈: 浮盈回撤{trail_loss:.0f}bps'
                    result['close_position_id'] = pos.id
                    return result
        
        # --- L6：环境过滤 ---
        if abs(state.funding_rate) > self.FUNDING_RATE_LIMIT:
            result['size_mult'] = min(result['size_mult'], 0.5)
        if state.book_depth_usd < self.MIN_BOOK_DEPTH_USD:
            result['action'] = 'PAUSE'
            result['reason'] = '流动性不足'
            return result
        
        return result
    
    def _compute_drawdown(self, current_equity):
        """计算从峰值的回撤百分比"""
        self.equity_peak = max(self.equity_peak, current_equity)
        if self.equity_peak == 0:
            return 0.0
        return (self.equity_peak - current_equity) / self.equity_peak * 100
```


### B.3 动态仓位管理

```python
class DynamicPositionSizer:
    """
    根据当前状态动态调整仓位大小
    核心思想：赚钱时可以维持/微增，亏钱时必须缩减
    """
    
    def __init__(self, config):
        self.base_size = config.BASE_ORDER_SIZE
        self.min_size_ratio = 0.1   # 最小不低于基础的10%
        self.max_size_ratio = 1.5   # 最大不超过基础的150%
    
    def compute_size(self, risk_result, mtf_result, vol_state) -> float:
        """
        综合多个维度计算最终仓位
        """
        multiplier = 1.0
        
        # 1. 风控衰减（最重要）
        multiplier *= risk_result['size_mult']
        
        # 2. 多周期共振/冲突
        if mtf_result:
            multiplier *= mtf_result['size_multiplier']
        
        # 3. 波动率调整：高波动时减仓
        z_vol = vol_state['z_vol']
        if z_vol > 3.0:
            multiplier *= max(0.3, 1.0 - (z_vol - 3.0) * 0.2)
        
        # 4. 时段调整：亚洲盘/欧盘/美盘活跃度不同
        hour_factor = self._get_hour_factor()
        multiplier *= hour_factor
        
        # 硬限制
        multiplier = max(self.min_size_ratio, min(self.max_size_ratio, multiplier))
        
        return self.base_size * multiplier
    
    def _get_hour_factor(self) -> float:
        """不同时段的流动性差异"""
        hour_utc = datetime.utcnow().hour
        if hour_utc in range(13, 21):   # 美盘时段 (UTC 13-21)
            return 1.0                   # 流动性最好
        elif hour_utc in range(7, 13):  # 欧盘时段
            return 0.9
        elif hour_utc in range(0, 7):   # 亚盘时段
            return 0.7
        else:
            return 0.6                   # 凌晨低流动性
```

### B.4 风控处置动作详细定义

| 动作 | 触发后行为 | 恢复条件 |
|------|-----------|---------|
| `CONTINUE` | 正常交易 | — |
| `REDUCE` | 仓位乘以 size_mult | 连赢 N 笔或时间窗口到期 |
| `FORCE_CLOSE_SINGLE` | 立即市价平掉指定仓位 | 平仓后继续 |
| `PAUSE` | 撤所有挂单，不开新仓 | 冷却时间到期 + 环境恢复正常 |
| `FLAT_ALL` | 市价全部平仓 + 撤单 | 人工确认 或 冷却时间到期 + 环境检查通过 |
| `SHUTDOWN` | 全平 + 停机 + 发告警通知 | 仅人工重启 |

---

## C. 多周期验证层：1m / 5m / 15m K线结构

### C.1 设计目标

```
15m K线 → 决定"是否适合做市"（背景过滤）
 5m K线 → 决定"趋势方向偏置"（方向层）
 1m K线 → 决定"当前节奏和形态"（节奏层）
 tick级  → 决定"具体挂单参数"（执行层）
```

做市策略不需要精确"抄底摸顶"，但需要知道：
- 大方向是什么？（避免逆大势做市）
- 当前是在趋势中还是震荡中？（决定做市强度）
- 有没有关键形态出现？（避免在突破点逆势挂单）


### C.2 K线数据结构

```python
@dataclass
class Kline:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float    # 主动买量
    sell_volume: float   # 主动卖量
    trade_count: int     # 成交笔数
    
    @property
    def body(self) -> float:
        return self.close - self.open
    
    @property
    def body_ratio(self) -> float:
        """实体占全K线的比例"""
        full_range = self.high - self.low
        if full_range == 0:
            return 0
        return abs(self.body) / full_range
    
    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)
    
    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low
    
    @property
    def is_bullish(self) -> bool:
        return self.close > self.open
    
    @property
    def volume_imbalance(self) -> float:
        """买卖量不平衡 -1~+1"""
        total = self.buy_volume + self.sell_volume
        if total == 0:
            return 0
        return (self.buy_volume - self.sell_volume) / total


class KlineBuffer:
    """维护多根K线的滚动缓冲区"""
    
    def __init__(self, period_sec: int, maxlen: int = 100):
        self.period_sec = period_sec
        self.buffer = deque(maxlen=maxlen)
        self.current_kline = None
        self.current_start = 0
    
    def update_tick(self, price, volume, is_buy, timestamp):
        """每个tick调用，自动聚合成K线"""
        bucket = int(timestamp // self.period_sec) * self.period_sec
        
        if bucket != self.current_start:
            # 新K线开始，保存上一根
            if self.current_kline:
                self.buffer.append(self.current_kline)
            self.current_kline = Kline(
                timestamp=bucket, open=price, high=price,
                low=price, close=price, volume=volume,
                buy_volume=volume if is_buy else 0,
                sell_volume=0 if is_buy else volume,
                trade_count=1
            )
            self.current_start = bucket
        else:
            # 更新当前K线
            k = self.current_kline
            k.high = max(k.high, price)
            k.low = min(k.low, price)
            k.close = price
            k.volume += volume
            if is_buy:
                k.buy_volume += volume
            else:
                k.sell_volume += volume
            k.trade_count += 1
    
    def last(self, n: int) -> list:
        """获取最近n根已完成的K线"""
        return list(self.buffer)[-n:]
    
    def atr(self, period: int) -> float:
        """计算ATR"""
        klines = self.last(period + 1)
        if len(klines) < 2:
            return 0
        trs = []
        for i in range(1, len(klines)):
            tr = max(
                klines[i].high - klines[i].low,
                abs(klines[i].high - klines[i-1].close),
                abs(klines[i].low - klines[i-1].close)
            )
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 0
```


### C.3 多周期趋势分析器

```python
class MultiTimeframeAnalyzer:
    """
    多周期趋势分析：15m背景 → 5m方向 → 1m节奏
    """
    
    def __init__(self):
        self.kline_1m = KlineBuffer(period_sec=60, maxlen=60)
        self.kline_5m = KlineBuffer(period_sec=300, maxlen=24)
        self.kline_15m = KlineBuffer(period_sec=900, maxlen=16)
    
    def update_tick(self, price, volume, is_buy, timestamp):
        """每个tick喂入，自动聚合三个时间级别"""
        self.kline_1m.update_tick(price, volume, is_buy, timestamp)
        self.kline_5m.update_tick(price, volume, is_buy, timestamp)
        self.kline_15m.update_tick(price, volume, is_buy, timestamp)
    
    def analyze(self) -> dict:
        """
        每根1m K线结束时调用
        返回多周期综合分析结果
        """
        # === 15m 级别：环境/背景 ===
        regime_15m = self._analyze_regime_15m()
        
        # === 5m 级别：方向 ===
        direction_5m = self._analyze_direction_5m()
        
        # === 1m 级别：节奏 ===
        rhythm_1m = self._analyze_rhythm_1m()
        
        # === 多周期共振 ===
        resonance = self._compute_resonance(regime_15m, direction_5m, rhythm_1m)
        
        return {
            'regime': regime_15m,
            'direction_5m': direction_5m,
            'rhythm_1m': rhythm_1m,
            'resonance': resonance,
            # 最终输出给策略的三个关键值
            'mtf_trend_bias': resonance['final_bias'],       # -1~+1
            'mtf_confidence': resonance['confidence'],       # 0~1
            'size_multiplier': resonance['size_factor'],     # 0~1.5
            'should_trade': regime_15m['tradeable'],         # bool
        }
    
    def _analyze_regime_15m(self) -> dict:
        """
        15m级别：市场环境判断
        RANGE = 适合做市（最好的环境）
        TREND = 需要小心（降频降仓）
        EXTREME = 不做（太危险）
        """
        klines = self.kline_15m.last(8)
        if len(klines) < 4:
            return {'type': 'RANGE', 'tradeable': True, 'strength': 0}
        
        atr = self.kline_15m.atr(8)
        current_price = klines[-1].close
        
        # 效率比：方向性移动 / 总波动
        directional = abs(klines[-1].close - klines[-4].close)
        total_range = sum(k.high - k.low for k in klines[-4:])
        efficiency = directional / (total_range + 1e-10)
        
        # 波动率相对水平
        vol_pct = atr / current_price
        
        if vol_pct > 0.015:  # 15m ATR > 1.5%
            return {'type': 'EXTREME', 'tradeable': False, 'strength': efficiency}
        elif efficiency > 0.55:
            return {'type': 'TREND', 'tradeable': True, 'strength': efficiency}
        else:
            return {'type': 'RANGE', 'tradeable': True, 'strength': efficiency}
    
    def _analyze_direction_5m(self) -> dict:
        """
        5m级别：方向判断
        用 EMA 交叉 + 高低点结构 + 量价
        """
        klines = self.kline_5m.last(12)
        if len(klines) < 6:
            return {'trend': 0.0, 'strength': 0.0, 'structure': 'NEUTRAL'}
        
        closes = [k.close for k in klines]
        
        # EMA 3/8 交叉
        ema3 = self._ema(closes, 3)
        ema8 = self._ema(closes, 8)
        atr = self.kline_5m.atr(8)
        ema_diff = (ema3 - ema8) / (atr + 1e-10)
        
        # 高低点结构
        recent = klines[-6:]
        hh = sum(1 for i in range(1, len(recent)) 
                 if recent[i].high > recent[i-1].high)
        hl = sum(1 for i in range(1, len(recent)) 
                 if recent[i].low > recent[i-1].low)
        ll = sum(1 for i in range(1, len(recent)) 
                 if recent[i].low < recent[i-1].low)
        lh = sum(1 for i in range(1, len(recent)) 
                 if recent[i].high < recent[i-1].high)
        
        structure_score = (hh + hl - ll - lh) / 10.0
        
        # 量价验证
        vol_trend = sum(k.volume_imbalance for k in klines[-3:]) / 3.0
        
        # 综合
        trend = (0.4 * tanh(ema_diff) + 
                 0.35 * structure_score + 
                 0.25 * vol_trend)
        trend = clamp(trend, -1.0, 1.0)
        
        # 结构判断
        if trend > 0.4:
            structure = 'UPTREND'
        elif trend < -0.4:
            structure = 'DOWNTREND'
        else:
            structure = 'NEUTRAL'
        
        return {'trend': trend, 'strength': abs(trend), 'structure': structure}
    
    def _analyze_rhythm_1m(self) -> dict:
        """
        1m级别：短线节奏
        - 最近几根K线的形态
        - 动量是否在加速/减速
        - 是否出现反转信号
        """
        klines = self.kline_1m.last(10)
        if len(klines) < 5:
            return {'momentum': 0, 'pattern': None, 'exhaustion': False}
        
        # 短期动量（最近3根K线方向）
        bodies = [(k.close - k.open) / (self.kline_1m.atr(10) + 1e-10) 
                  for k in klines[-3:]]
        momentum = 0.5 * bodies[-1] + 0.3 * bodies[-2] + 0.2 * bodies[-3]
        
        # 形态检测
        pattern = self._detect_1m_patterns(klines[-5:])
        
        # 耗竭信号（放量长上/下影线）
        last = klines[-1]
        atr = self.kline_1m.atr(10)
        exhaustion = False
        
        # 多头耗竭：放量 + 长上影 + 在高位
        if (last.upper_wick > atr * 1.5 and 
            last.volume > 2 * mean([k.volume for k in klines[-5:-1]])):
            exhaustion = True
        # 空头耗竭：放量 + 长下影 + 在低位
        if (last.lower_wick > atr * 1.5 and 
            last.volume > 2 * mean([k.volume for k in klines[-5:-1]])):
            exhaustion = True
        
        return {
            'momentum': tanh(momentum),
            'pattern': pattern,
            'exhaustion': exhaustion
        }
    
    def _detect_1m_patterns(self, klines) -> str:
        """检测1m级别关键形态"""
        if len(klines) < 3:
            return None
        
        last = klines[-1]
        prev = klines[-2]
        atr = self.kline_1m.atr(10)
        
        # 看涨吞没
        if (prev.body < 0 and last.body > 0 and
            last.close > prev.open and last.open < prev.close and
            abs(last.body) > atr * 0.8):
            return 'BULLISH_ENGULF'
        
        # 看跌吞没
        if (prev.body > 0 and last.body < 0 and
            last.close < prev.open and last.open > prev.close and
            abs(last.body) > atr * 0.8):
            return 'BEARISH_ENGULF'
        
        # 锤子线（底部反转）
        if (last.lower_wick > abs(last.body) * 2.5 and
            last.upper_wick < abs(last.body) * 0.5):
            return 'HAMMER'
        
        # 射击之星（顶部反转）
        if (last.upper_wick > abs(last.body) * 2.5 and
            last.lower_wick < abs(last.body) * 0.5):
            return 'SHOOTING_STAR'
        
        # 内包线（收敛，即将突破）
        if (last.high < prev.high and last.low > prev.low):
            return 'INSIDE_BAR'
        
        return None
    
    def _compute_resonance(self, regime, direction_5m, rhythm_1m) -> dict:
        """
        多周期共振计算
        """
        # 基础偏置来自5m方向
        base_bias = direction_5m['trend']
        
        # 1m节奏验证
        m1_agrees = (sign(rhythm_1m['momentum']) == sign(base_bias))
        
        # 共振强度
        if m1_agrees and abs(base_bias) > 0.3:
            confidence = min(1.0, direction_5m['strength'] + 0.2)
            size_factor = 1.2
        elif not m1_agrees and abs(base_bias) > 0.3:
            # 1m和5m方向冲突 → 可能在反转，缩仓
            confidence = 0.3
            size_factor = 0.5
            base_bias *= 0.5  # 衰减偏置
        else:
            confidence = 0.5
            size_factor = 1.0
        
        # 1m形态加分
        pattern = rhythm_1m['pattern']
        if pattern in ('BULLISH_ENGULF', 'HAMMER') and base_bias > 0:
            confidence = min(1.0, confidence + 0.2)
        elif pattern in ('BEARISH_ENGULF', 'SHOOTING_STAR') and base_bias < 0:
            confidence = min(1.0, confidence + 0.2)
        elif pattern in ('BULLISH_ENGULF', 'HAMMER') and base_bias < 0:
            # 反转形态与5m趋势冲突 → 高度警惕
            size_factor *= 0.5
        
        # 1m耗竭信号 → 趋势可能结束
        if rhythm_1m['exhaustion']:
            size_factor *= 0.6
            confidence *= 0.6
        
        # 15m环境修正
        if regime['type'] == 'EXTREME':
            size_factor = 0.0
        elif regime['type'] == 'TREND' and regime['strength'] > 0.6:
            size_factor *= 0.6  # 强趋势环境降仓
        
        return {
            'final_bias': clamp(base_bias, -1.0, 1.0),
            'confidence': confidence,
            'size_factor': clamp(size_factor, 0.0, 1.5),
        }
    
    @staticmethod
    def _ema(data, period):
        """计算EMA最后一个值"""
        alpha = 2.0 / (period + 1)
        ema_val = data[0]
        for d in data[1:]:
            ema_val = alpha * d + (1 - alpha) * ema_val
        return ema_val
```


### C.4 整合到主策略循环

```python
class TrendAdaptiveGridStrategy_v11:
    """
    v1.1 主策略：在v1.0基础上整合三大改进
    """
    
    def __init__(self, config):
        # 原有引擎
        self.vol_engine = VolatilityEngine(config)
        self.trend_engine = TrendEngine_v11(config)  # 升级版
        self.inventory_engine = InventoryEngine(config)
        self.quote_generator = AdaptiveQuoteGenerator(config)
        self.order_manager = OrderManager(config)
        
        # === v1.1 新增 ===
        self.risk_manager = RiskManager(config)
        self.mtf_analyzer = MultiTimeframeAnalyzer()
        self.position_sizer = DynamicPositionSizer(config)
        
        # 多周期分析结果缓存（每根1m K线更新一次）
        self.mtf_result = {
            'mtf_trend_bias': 0.0,
            'mtf_confidence': 0.5,
            'size_multiplier': 1.0,
            'should_trade': True,
        }
        self.last_1m_update = 0
    
    async def on_tick(self, market_data):
        """v1.1 主循环"""
        
        # === Phase 0: 更新所有数据（每tick） ===
        self.vol_engine.update(market_data.mid_price)
        self.mtf_analyzer.update_tick(
            market_data.mid_price, 
            market_data.volume,
            market_data.is_buy,
            market_data.timestamp
        )
        
        # === Phase 1: 风控检查（最高优先级） ===
        risk_state = self._build_risk_state(market_data)
        risk_decision = self.risk_manager.check(risk_state)
        
        if risk_decision['action'] == 'SHUTDOWN':
            await self._shutdown(risk_decision['reason'])
            return
        elif risk_decision['action'] == 'FLAT_ALL':
            await self._flat_all(risk_decision['reason'])
            return
        elif risk_decision['action'] == 'PAUSE':
            await self._pause(risk_decision['reason'], risk_decision['cooldown_sec'])
            return
        elif risk_decision['action'] == 'FORCE_CLOSE_SINGLE':
            await self._force_close(risk_decision.get('close_position_id'))
            # 平仓后继续执行后续逻辑
        
        # === Phase 2: 多周期分析（每根1m K线更新一次） ===
        current_minute = int(market_data.timestamp // 60)
        if current_minute != self.last_1m_update:
            self.mtf_result = self.mtf_analyzer.analyze()
            self.last_1m_update = current_minute
            
            # 环境否决
            if not self.mtf_result['should_trade']:
                await self.order_manager.cancel_all()
                log.warning("15m环境不适合做市，暂停")
                return
        
        # === Phase 3: 跳跃检测 ===
        if self.vol_engine.detect_jump(market_data):
            self._on_jump()
            return
        
        # === Phase 4: 计算三引擎输出 ===
        σ_fast, σ_mid, σ_slow, z_vol = self.vol_engine.get_state()
        
        # v1.1 趋势引擎（含加速度+量价+反转检测）
        tick_trend, tick_confidence, diagnostics = \
            self.trend_engine.compute_trend_score_v11(market_data)
        
        inventory_pressure = self.inventory_engine.compute()
        
        # === Phase 5: 多周期融合 ===
        # tick级信号 × 70% + K线级偏置 × 30%
        effective_trend = (
            0.70 * tick_trend * tick_confidence +
            0.30 * self.mtf_result['mtf_trend_bias'] * self.mtf_result['mtf_confidence']
        )
        
        # === Phase 6: 动态仓位计算 ===
        effective_size = self.position_sizer.compute_size(
            risk_result=risk_decision,
            mtf_result=self.mtf_result,
            vol_state={'z_vol': z_vol}
        )
        
        # === Phase 7: 生成报价 ===
        new_bids, new_asks = self.quote_generator.generate(
            mid=market_data.mid_price,
            σ_mid=σ_mid,
            z_vol=z_vol,
            trend_score=effective_trend,
            inventory_pressure=inventory_pressure,
            order_size=effective_size,  # v1.1: 动态仓位
        )
        
        # === Phase 8: 反转特殊处理 ===
        if diagnostics['reversal']['action'] == 'WIDEN_ALL':
            # 反转期间两侧都加宽50%
            new_bids = self._widen_orders(new_bids, 1.5)
            new_asks = self._widen_orders(new_asks, 1.5)
        elif diagnostics['reversal']['action'] == 'FLATTEN':
            # 严重反转：撤逆势侧，只保留去库存方向
            new_bids, new_asks = self._flatten_adverse_side(
                new_bids, new_asks, inventory_pressure
            )
        
        # === Phase 9: 惰性更新 ===
        if self.order_manager.should_requote(new_bids, new_asks):
            await self.order_manager.update_orders(new_bids, new_asks)
    
    async def _shutdown(self, reason):
        log.critical(f"策略停机: {reason}")
        await self.order_manager.cancel_all()
        await self._market_close_all_positions()
        self.state = 'SHUTDOWN'
        # 发送告警通知（Telegram/Discord/Email）
        await self._send_alert(f"SHUTDOWN: {reason}")
    
    async def _flat_all(self, reason):
        log.error(f"全平仓: {reason}")
        await self.order_manager.cancel_all()
        await self._market_close_all_positions()
        self.state = 'PAUSED'
        await self._send_alert(f"FLAT ALL: {reason}")
    
    async def _pause(self, reason, cooldown_sec):
        log.warning(f"暂停{cooldown_sec}s: {reason}")
        await self.order_manager.cancel_all()
        self.state = 'PAUSED'
        self.resume_time = time.now() + cooldown_sec
```


---

## D. 完整决策流程图（v1.1）

```
每个 Tick 到达
    │
    ▼
┌─────────────────────────┐
│  Phase 0: 数据更新       │  更新σ + 喂K线聚合器
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐     ┌─────────────┐
│  Phase 1: 风控检查       │──→ │  SHUTDOWN?   │──→ 全平+停机+告警
│  (六层递进式)            │     │  FLAT_ALL?   │──→ 全平+冷却
│                         │     │  PAUSE?      │──→ 撤单+等待恢复
│                         │     │  CLOSE_ONE?  │──→ 平单笔+继续
└────────────┬────────────┘     └─────────────┘
             │ CONTINUE / REDUCE
             ▼
┌─────────────────────────┐
│  Phase 2: 多周期分析     │  每分钟更新一次
│  15m环境 → 5m方向 → 1m节奏│
│  输出: bias/confidence/size│
└────────────┬────────────┘
             │ should_trade = True?
             ▼
┌─────────────────────────┐
│  Phase 3: 跳跃检测       │  z_tick > 6 → 撤近端+冷却
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  Phase 4: 三引擎计算     │
│  波动率: σ_fast/mid/slow │
│  趋势: score + 加速度    │
│  + 量价验证 + 反转检测    │
│  库存: pressure          │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  Phase 5: 多周期融合     │  tick×70% + K线×30%
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  Phase 6: 动态仓位       │  风控×周期×波动率×时段
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  Phase 7: 生成报价       │  非对称层数+间距+中心价
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  Phase 8: 反转处理       │  反转时加宽/减侧
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  Phase 9: 惰性更新       │  变化>阈值才撤补
└─────────────────────────┘
```

---

## E. v1.0 → v1.1 改进总结

| 维度 | v1.0 | v1.1 |
|------|------|------|
| **趋势判断** | 10s/60s/300s tick动量 | + 加速度 + 量价验证 + 反转检测 |
| **时间框架** | 纯tick级（无K线） | 15m环境→5m方向→1m节奏→tick执行 |
| **信号平滑** | EWMA半衰期3s（太慢） | 1.5s + 反转时直接衰减 |
| **风控止损** | 跳跃熔断+库存硬限+小时亏损 | 六层体系（单笔/连亏/日限/回撤/黑天鹅/环境）|
| **仓位管理** | 固定BASE_SIZE | 动态衰减（风控×周期×波动×时段） |
| **极端行情** | 跳跃后冷却30s | 黑天鹅全平+日亏硬止+回撤停机+流动性退出 |
| **反转应对** | 无 | 反转检测→两侧加宽/平仓 |
| **量价关系** | 无 | 放量确认/缩量衰减/天量警惕 |
| **K线形态** | 无 | 吞没/锤子/射击星/内包线 |
| **环境过滤** | 无 | 15m EXTREME不做/TREND降仓 |
| **单笔管理** | 无止损无止盈 | 50bps止损/3min超时/移动止盈 |
| **日级保护** | 无 | 200bps暂停/300bps停机 |

---

## F. 参数速查表（v1.1 新增）

### F.1 风控参数

| 参数 | 值 | 说明 |
|------|-----|------|
| SINGLE_STOP_BPS | 50 | 单笔浮亏止损 |
| SINGLE_MAX_HOLD_SEC | 180 | 单笔最大持仓时间 |
| SINGLE_TRAIL_STOP_BPS | 30 | 浮盈回撤保护 |
| MAX_CONSECUTIVE_LOSSES | 5 | 连亏熔断 |
| LOSS_5MIN_BPS | -30 | 5分钟亏损降频 |
| LOSS_HOURLY_BPS | -80 | 小时亏损暂停 |
| LOSS_DAILY_BPS | -200 | 日亏损暂停 |
| LOSS_DAILY_HARD_BPS | -300 | 日亏损停机 |
| DRAWDOWN_WARN_PCT | 2.0% | 回撤降仓 |
| DRAWDOWN_STOP_PCT | 3.5% | 回撤暂停 |
| DRAWDOWN_HARD_PCT | 5.0% | 回撤停机 |
| BLACK_SWAN_1M_BPS | 200 | 1m黑天鹅阈值 |
| BLACK_SWAN_5M_BPS | 500 | 5m黑天鹅阈值 |
| SPREAD_EXTREME_X | 8.0 | spread异常倍数 |

### F.2 多周期参数

| 参数 | 值 | 说明 |
|------|-----|------|
| MTF_TICK_WEIGHT | 0.70 | tick级信号权重 |
| MTF_KLINE_WEIGHT | 0.30 | K线级信号权重 |
| 15M_EXTREME_VOL | 1.5% | 15m ATR/价格 极端阈值 |
| 15M_TREND_EFFICIENCY | 0.55 | 15m 趋势效率比阈值 |
| 5M_EMA_FAST | 3 | 5m 快EMA |
| 5M_EMA_SLOW | 8 | 5m 慢EMA |
| 1M_LOOKBACK | 10 | 1m 回溯K线数 |
| RESONANCE_AGREE_BOOST | 1.2 | 共振时size放大 |
| RESONANCE_CONFLICT_DECAY | 0.5 | 冲突时size衰减 |

### F.3 趋势增强参数

| 参数 | 值 | 说明 |
|------|-----|------|
| EWMA_HALFLIFE_V11 | 1.5s | 趋势平滑半衰期（比v1.0的3s更快）|
| ACCEL_BOOST_MAX | 1.5 | 加速度最大放大倍数 |
| VOLUME_SURGE_THRESHOLD | 3.0x | 放量突破倍数 |
| VOLUME_CLIMAX_THRESHOLD | 5.0x | 天量/高潮倍数 |
| REVERSAL_RETREAT_RATIO | 0.6 | 反转回撤比例阈值 |
| REVERSAL_TIME_WINDOW | 10s | 反转检测时间窗口 |

---

*v1.1 补全完成。本文档与 adaptive_grid_strategy_v1.md 配合使用，v1.md 定义基础架构，本文档定义增强层。*
