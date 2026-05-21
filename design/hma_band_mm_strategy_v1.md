# HMA-Band 做市策略 (HMA-Band Market Making Strategy)

## 完整设计文档 v1.0

> **核心理念**：做市的本质是赌均值回归。把"均值"显性化为 HMA200，把"什么时候停"用价格相对均值的偏离量来判断。**简单到经得起推敲，是这个策略的最大优势**。

---

## 1. 设计哲学

### 1.1 一句话概括

> 价格围绕 HMA200 做市，距离均值越远越警惕，进入"趋势区"立即只去库存——**做市的本质就是赌价格回到均值**。

### 1.2 与之前方案的关键不同

| 维度 | 之前方案（ADX/趋势引擎） | 本方案（HMA-Band） |
|------|------|------|
| 趋势判断方式 | 统计指标（ADX 抽象） | **几何位置（价格 vs 均值）** |
| 与做市契合度 | 间接 | **直接** —— 做市本就绕均值挂单 |
| 方向偏置来源 | 需要 DI+/DI- 单独计算 | **天然来自偏离量符号** |
| 参数数量 | 8 个 | **6 个核心 + 3 个风控 = 9 个** |
| 是否经过验证 | 自创权重 | **HMA + ATR + Keltner 通道思想，经典套路** |

### 1.3 三个不变的原则

1. **每个问题只用一个指标回答**（不做加权融合）
2. **状态切换必须有确认**（防止假突破抖动）
3. **极端事件硬规则一票否决**（不参与正常流程）


---

## 2. 核心架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                  HMA-Band 做市策略架构                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │  Layer 0: 极端事件保护（一票否决）                           │     │
│  │  - 1m 波幅 > 100 bps → 全平                                │     │
│  │  - 日亏 > 150 bps → 停机                                   │     │
│  │  - 回撤 > 3% → 停机                                        │     │
│  └───────────────────────┬───────────────────────────────────┘     │
│                          │                                          │
│  ┌───────────────────────▼───────────────────────────────────┐     │
│  │  Layer 1: 指标层（HMA + ATR）                               │     │
│  │  - HMA(200, 1m): 中心均值                                  │     │
│  │  - ATR(14, 1m): 自适应波动率                                │     │
│  │  - 输出: 中心价、内层带宽、外层带宽、间距宽度                  │     │
│  └───────────────────────┬───────────────────────────────────┘     │
│                          │                                          │
│  ┌───────────────────────▼───────────────────────────────────┐     │
│  │  Layer 2: 状态机层（带防抖确认）                             │     │
│  │   NORMAL ⟷ WARN ⟷ STOP                                    │     │
│  │   每次切换需要 N 根 K 线收盘价确认                            │     │
│  └───────────────────────┬───────────────────────────────────┘     │
│                          │                                          │
│  ┌───────────────────────▼───────────────────────────────────┐     │
│  │  Layer 3: 决策层                                            │     │
│  │  - NORMAL: 满载做市                                         │     │
│  │  - WARN: 降仓 40% + 间距加宽 1.5x                          │     │
│  │  - STOP: 仅去库存                                           │     │
│  └───────────────────────┬───────────────────────────────────┘     │
│                          │                                          │
│  ┌───────────────────────▼───────────────────────────────────┐     │
│  │  Layer 4: 报价生成（围绕 HMA200，自带方向偏置）              │     │
│  │  - 中心价 = HMA200 + 库存偏移修正                            │     │
│  │  - 间距 = 1.2 × ATR                                         │     │
│  │  - 方向偏置 = 价格相对 HMA 的偏离 / ATR                      │     │
│  └───────────────────────────────────────────────────────────┘     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```


---

## 3. Layer 1: 指标层（HMA + ATR）

### 3.1 HMA（Hull Moving Average）计算

**为什么用 HMA 而不是 EMA/SMA？**

HMA 由 Alan Hull 设计，公式上做了"减二次平滑"，达到了：
- 比 SMA/EMA **延迟更小**（约为 SMA 的 1/3）
- 比 EMA **更平滑**（不会被单根 K 线扰动）

HMA 公式：
```
HMA(n) = WMA(2 × WMA(n/2) - WMA(n), sqrt(n))
```

具体到 HMA(200)：
```
HMA(200) = WMA(2 × WMA(100) - WMA(200), 14)   # sqrt(200) ≈ 14
```

### 3.2 实现代码

```python
def wma(prices: list, period: int) -> float:
    """加权移动平均，最近的权重最大"""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    
    weights = list(range(1, period + 1))  # [1, 2, ..., period]
    weighted_sum = sum(p * w for p, w in zip(prices[-period:], weights))
    weight_total = sum(weights)
    return weighted_sum / weight_total


def hma(prices: list, period: int) -> float:
    """Hull Moving Average"""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    
    half_period = period // 2
    sqrt_period = int(round(period ** 0.5))
    
    # Step 1: 计算两条 WMA 的差值序列
    wma_half_series = []
    wma_full_series = []
    
    for i in range(period, len(prices) + 1):
        window = prices[:i]
        wma_half_series.append(wma(window, half_period))
        wma_full_series.append(wma(window, period))
    
    diff_series = [2 * h - f for h, f in zip(wma_half_series, wma_full_series)]
    
    # Step 2: 对差值序列做 sqrt(period) 的 WMA
    return wma(diff_series, sqrt_period)
```

### 3.3 ATR 计算（标准实现）

```python
def atr(klines: list, period: int = 14) -> float:
    """Average True Range"""
    if len(klines) < period + 1:
        return 0
    
    trs = []
    for i in range(1, len(klines)):
        tr = max(
            klines[i].high - klines[i].low,
            abs(klines[i].high - klines[i-1].close),
            abs(klines[i].low - klines[i-1].close)
        )
        trs.append(tr)
    
    return sum(trs[-period:]) / period
```


### 3.4 双层区间定义

```
                                价格
                                  │
                                  ▼
          ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ HMA + 3.0×ATR  ← 外层（趋势区上轨）
          
          ─────────────────────────────────────── HMA + 1.5×ATR  ← 内层（警戒区上轨）
          
          ════════════════════════════════════════ HMA200         ← 中枢
          
          ─────────────────────────────────────── HMA - 1.5×ATR  ← 内层（警戒区下轨）
          
          ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ HMA - 3.0×ATR  ← 外层（趋势区下轨）
```

```python
def compute_bands(hma_value, atr_value):
    """计算双层区间边界"""
    inner_upper = hma_value + 1.5 * atr_value
    inner_lower = hma_value - 1.5 * atr_value
    outer_upper = hma_value + 3.0 * atr_value
    outer_lower = hma_value - 3.0 * atr_value
    
    return {
        'inner_upper': inner_upper,
        'inner_lower': inner_lower,
        'outer_upper': outer_upper,
        'outer_lower': outer_lower,
    }
```

### 3.5 为什么用 ATR 倍数而不是固定百分比？

| 区间定义方式 | 优点 | 缺点 |
|------|------|------|
| 固定 ±0.5% | 简单 | 不同币种/不同时段不适用 |
| 固定 ±50bps | 简单 | 同上 |
| **K × ATR** | **自适应波动率，跨币种通用** | **无明显缺点** |
| K × 标准差 | 类似 ATR | 计算自身有参数 |

**结论**：用 ATR 倍数。波动放大时区间自动放大，波动收缩时区间自动收紧。**这是这个方案抗过拟合的关键**。


---

## 4. Layer 2: 状态机层（带防抖确认）

### 4.1 状态定义

| 状态 | 含义 | 价格位置 | 行为 |
|------|------|---------|------|
| `NORMAL` | 震荡市，做市黄金窗口 | 内层区间内 | 满载做市 |
| `WARN` | 价格偏离警戒，可能转趋势 | 内层外、外层内 | 降仓 + 加宽间距 |
| `STOP` | 趋势已确认，做市危险 | 外层之外 | 仅去库存，不开新仓 |

### 4.2 状态切换规则（带 K 线确认）

**核心原则**：用 K 线**收盘价**判断状态，避免 tick 噪音和假突破。

```
NORMAL → WARN：连续 3 根 1m K 线收盘价 在内层之外
WARN → STOP：连续 2 根 1m K 线收盘价 在外层之外
STOP → WARN：连续 3 根 1m K 线收盘价 回到外层之内
WARN → NORMAL：连续 3 根 1m K 线收盘价 回到内层之内
```

**为什么 NORMAL→WARN 需要 3 根，但 WARN→STOP 只要 2 根？**

- 进入更危险状态：**快确认**（2根，保护资金）
- 退出危险状态：**慢确认**（3根，避免假回归）
- 这个不对称设计很关键：**亏损保护永远比利润机会优先**

### 4.3 状态机实现

```python
class RegimeStateMachine:
    """带确认延迟的市场状态机"""
    
    NORMAL = 'NORMAL'
    WARN = 'WARN'
    STOP = 'STOP'
    
    def __init__(self):
        self.current_state = self.NORMAL
        self.pending_state = None
        self.pending_count = 0
    
    def update(self, last_close, bands) -> str:
        """
        每根 1m K 线收盘后调用一次
        last_close: 刚收盘的 K 线收盘价
        bands: {'inner_upper', 'inner_lower', 'outer_upper', 'outer_lower'}
        返回当前状态
        """
        # 判断当前 K 线收盘价位置
        in_inner = bands['inner_lower'] <= last_close <= bands['inner_upper']
        in_outer = bands['outer_lower'] <= last_close <= bands['outer_upper']
        
        # 计算"应该进入的目标状态"
        if not in_outer:
            target = self.STOP
        elif not in_inner:
            target = self.WARN
        else:
            target = self.NORMAL
        
        # 状态切换需要的确认次数
        confirm_required = self._get_confirm_required(self.current_state, target)
        
        if target == self.current_state:
            self.pending_state = None
            self.pending_count = 0
            return self.current_state
        
        # 累积 pending 计数
        if self.pending_state == target:
            self.pending_count += 1
        else:
            self.pending_state = target
            self.pending_count = 1
        
        # 达到确认次数，正式切换
        if self.pending_count >= confirm_required:
            self.current_state = target
            self.pending_state = None
            self.pending_count = 0
        
        return self.current_state
    
    def _get_confirm_required(self, from_state, to_state) -> int:
        """根据切换方向决定确认K线数"""
        # 进入更危险状态：快（2根）
        if (from_state == self.NORMAL and to_state == self.WARN):
            return 3
        if (from_state == self.WARN and to_state == self.STOP):
            return 2
        # 退出危险状态：慢（3根）
        if (from_state == self.STOP and to_state == self.WARN):
            return 3
        if (from_state == self.WARN and to_state == self.NORMAL):
            return 3
        # 跨越式切换（如 NORMAL → STOP，理论上不应该发生但保险起见）
        return 3
```


---

## 5. Layer 3: 决策层

每个状态对应明确的行为参数，**不再需要复杂的多信号融合**。

### 5.1 状态-行为映射

| 状态 | 仓位系数 | 间距倍数 | 是否开新仓 | 中心价 | 方向偏置 |
|------|---------|---------|----------|-------|---------|
| `NORMAL` | 1.0 | 1.0x | 是 | HMA200 | 可能（基于偏离量）|
| `WARN` | 0.4 | 1.5x | 是 | HMA200 | 强（顺回归）|
| `STOP` | 0.0 | — | 仅去库存 | 当前 mid | 无 |

### 5.2 实现代码

```python
def decide_action(state: str, deviation_atr: float):
    """
    state: NORMAL / WARN / STOP
    deviation_atr: (price - hma) / atr，价格距 HMA 多少个 ATR
    """
    if state == 'STOP':
        return {
            'action': 'INVENTORY_ONLY',
            'size_factor': 0.0,
            'grid_widen': None,
            'direction_bias': 0.0,  # STOP 状态下方向由库存决定
        }
    
    if state == 'WARN':
        return {
            'action': 'TRADE',
            'size_factor': 0.4,
            'grid_widen': 1.5,
            # WARN 状态下，方向偏置更激进（赌回归）
            'direction_bias': clamp(-deviation_atr / 1.5, -0.6, 0.6),
        }
    
    # NORMAL
    return {
        'action': 'TRADE',
        'size_factor': 1.0,
        'grid_widen': 1.0,
        # NORMAL 状态下，方向偏置温和
        'direction_bias': clamp(-deviation_atr / 3.0, -0.3, 0.3),
    }
```

### 5.3 方向偏置的核心逻辑

**这是 HMA 方案最优雅的地方**：

```
deviation_atr = (price - HMA200) / ATR

价格在 HMA 上方 → deviation_atr > 0 → direction_bias < 0 → 偏卖（赌回归）
价格在 HMA 下方 → deviation_atr < 0 → direction_bias > 0 → 偏买（赌回归）
价格刚好在 HMA → deviation_atr = 0 → direction_bias = 0 → 对称做市
```

**这就是均值回归思想的几何化表达**：价格离均值越远，越坚定地反方向挂单。

不需要 DI+/DI-，不需要动量计算，**位置本身就是信号**。


---

## 6. Layer 4: 报价生成

### 6.1 中心价计算

**重要决策**：报价中心是 mid，**不是 HMA200**。

为什么？
- HMA200 反映的是过去 200 分钟的中枢
- 当前 mid 才是真实可成交的价格
- 用 HMA 当报价中心会导致 spread 偏离市价，几乎不会成交

**HMA 的作用是判断"该不该做、间距多宽、偏向哪侧"，不是直接当报价中心**。

```python
def compute_center_price(mid, hma, atr, decision, inventory_pressure):
    """
    最终中心价 = mid + 方向偏移
    方向偏移 = (HMA回归偏置 + 库存清理偏置) × 最大偏移幅度
    """
    # HMA 偏置（基于价格距 HMA 的位置）
    hma_bias = decision['direction_bias']  # -1 ~ +1
    
    # 库存偏置（库存超限时强制覆盖 HMA 偏置）
    if abs(inventory_pressure) > 0.7:
        # 库存触及硬限附近，必须优先去库存
        combined_bias = -inventory_pressure  # 多头库存→偏卖，反之亦然
    else:
        # 正常情况：HMA 偏置为主，库存为辅
        combined_bias = 0.7 * hma_bias + 0.3 * (-inventory_pressure)
    
    combined_bias = clamp(combined_bias, -1.0, 1.0)
    
    # 最大偏移 = 1 倍 ATR（保守）
    max_shift_bps = atr / mid * 10000
    shift_bps = combined_bias * max_shift_bps
    
    center = mid * (1 - shift_bps / 10000)
    return center, shift_bps
```

### 6.2 间距计算

```python
def compute_grid_interval(atr, mid, decision):
    """间距 = max(成本下限, 1.2 × ATR × 状态加宽倍数)"""
    
    # 成本下限
    cost_floor_bps = (FEE_BPS * 2) + SLIPPAGE_BPS
    
    # ATR 驱动间距
    atr_bps = atr / mid * 10000
    base_interval = GRID_INTERVAL_K * atr_bps  # K = 1.2
    
    # 状态加宽
    final_interval = base_interval * decision['grid_widen']
    
    return max(cost_floor_bps, final_interval)
```

### 6.3 完整挂单序列生成

```python
def generate_quotes(mid, hma, atr, state_decision, inventory_pressure):
    """生成完整的双边挂单序列"""
    
    # === STOP 状态：仅去库存 ===
    if state_decision['action'] == 'INVENTORY_ONLY':
        return _generate_inventory_orders(mid, atr, inventory_pressure)
    
    # === 计算中心、间距、偏置 ===
    center, shift_bps = compute_center_price(
        mid, hma, atr, state_decision, inventory_pressure
    )
    interval_bps = compute_grid_interval(atr, mid, state_decision)
    
    # === 总层数预算 ===
    total_layers = BASE_GRID_ORDERS  # 6
    
    # === 非对称分配 ===
    # bias > 0 → 看多 → 卖单多（赌价格继续走然后回归赚价差）
    # 错了！bias 在我们的定义中：>0 偏卖，<0 偏买
    # bias > 0 → 偏卖 → 卖单多
    bias = state_decision['direction_bias']
    bid_layers = max(1, round(total_layers * (0.5 - bias * 0.4)))
    ask_layers = max(1, round(total_layers * (0.5 + bias * 0.4)))
    
    # === 单层 size ===
    base_size = BASE_ORDER_SIZE * state_decision['size_factor']
    
    # === 生成挂单 ===
    bids = []
    for i in range(bid_layers):
        price = center * (1 - (i + 1) * interval_bps / 10000)
        bids.append(Order('buy', price, base_size))
    
    asks = []
    for i in range(ask_layers):
        price = center * (1 + (i + 1) * interval_bps / 10000)
        asks.append(Order('sell', price, base_size))
    
    return bids, asks


def _generate_inventory_orders(mid, atr, inventory_pressure):
    """STOP 状态下，仅生成去库存方向的远端挂单"""
    if abs(inventory_pressure) < 0.1:
        return [], []  # 无库存，不挂单
    
    atr_bps = atr / mid * 10000
    
    if inventory_pressure > 0:
        # 多头库存，仅挂卖单（去库存）
        # 离 mid 1 倍 ATR 起，逐渐放远
        asks = []
        for i in range(3):
            offset = (1.0 + i * 0.5) * atr_bps
            price = mid * (1 + offset / 10000)
            asks.append(Order('sell', price, BASE_ORDER_SIZE * 0.5))
        return [], asks
    else:
        # 空头库存，仅挂买单
        bids = []
        for i in range(3):
            offset = (1.0 + i * 0.5) * atr_bps
            price = mid * (1 - offset / 10000)
            bids.append(Order('buy', price, BASE_ORDER_SIZE * 0.5))
        return bids, []
```


---

## 7. Layer 0: 极端事件保护（最小化）

**原则**：尽可能少的硬规则，每条都必须有清晰理由。

### 7.1 三条硬规则

```python
class ExtremeEventGuard:
    """极端事件保护：3 条硬规则一票否决"""
    
    # 单根 1m K 线波幅熔断
    JUMP_BPS_1M = 100              # 1m 内波动 > 100bps（1%）
    
    # 单日累计亏损硬止损
    DAILY_LOSS_HARD_BPS = 150      # 日亏 150bps 停机
    
    # 最大回撤硬止损
    MAX_DRAWDOWN_PCT = 3.0         # 从权益峰值回撤 3% 停机
    
    def check(self, current_state) -> dict:
        """
        每个 tick 调用，返回 {action, reason} 或 None
        """
        # 黑天鹅：1m 急剧波动
        if current_state.last_1m_range_bps > self.JUMP_BPS_1M:
            return {
                'action': 'FLAT_AND_PAUSE',
                'reason': f'1m波幅{current_state.last_1m_range_bps:.0f}bps',
                'cooldown_sec': 600,
            }
        
        # 日亏硬止
        if current_state.daily_pnl_bps < -self.DAILY_LOSS_HARD_BPS:
            return {
                'action': 'SHUTDOWN',
                'reason': f'日亏{current_state.daily_pnl_bps:.0f}bps',
            }
        
        # 回撤硬止
        if current_state.drawdown_pct > self.MAX_DRAWDOWN_PCT:
            return {
                'action': 'SHUTDOWN',
                'reason': f'回撤{current_state.drawdown_pct:.1f}%',
            }
        
        return None  # 无极端事件
```

### 7.2 为什么不再加更多风控？

之前 v1.1 设计了"六层风控"（单笔/连亏/5min/小时/日/回撤/黑天鹅/环境），但：

| 风控层 | 是否保留 | 理由 |
|--------|---------|------|
| 单笔止损 | ❌ 删除 | **状态机的 STOP 状态就是单笔保护**——价格脱离外层后立即停止开新仓，已有库存通过去库存机制处理 |
| 连亏熔断 | ❌ 删除 | 状态机已经在不利环境时降仓/停止，连亏自然减少 |
| 小时止损 | ❌ 删除 | 与日止损功能重叠，用日止损覆盖 |
| 日亏硬止 | ✅ 保留 | 终极保护 |
| 回撤止损 | ✅ 保留 | 终极保护 |
| 黑天鹅 | ✅ 保留 | 状态机依赖 K 线（1分钟）确认，1m 内的瞬间崩盘需要单独处理 |
| 环境过滤 | ❌ 删除 | 状态机的 STOP 状态已经覆盖 |

**结论**：从 v1.1 的 ~25 条风控规则减到 **3 条**。


---

## 8. 完整执行循环

### 8.1 主类

```python
class HMABandMarketMaker:
    """HMA-Band 做市策略主类"""
    
    # ========== 核心参数（共 6 个） ==========
    HMA_PERIOD = 200              # HMA 周期（基于 1m K线）
    ATR_PERIOD = 14               # ATR 周期
    INNER_BAND_K = 1.5            # 内层带宽 = 1.5 × ATR
    OUTER_BAND_K = 3.0            # 外层带宽 = 3.0 × ATR
    GRID_INTERVAL_K = 1.2         # 网格间距 = 1.2 × ATR
    BASE_GRID_ORDERS = 6          # 单侧基础挂单层数
    
    # ========== 极端事件参数（共 3 个） ==========
    JUMP_BPS_1M = 100             # 1m K线波幅熔断
    DAILY_LOSS_HARD_BPS = 150     # 日亏硬止
    MAX_DRAWDOWN_PCT = 3.0        # 回撤硬止
    
    def __init__(self, config):
        self.kline_buffer = KlineBuffer(period_sec=60, maxlen=300)
        self.state_machine = RegimeStateMachine()
        self.guard = ExtremeEventGuard()
        self.order_manager = OrderManager(config)
        
        self.last_1m_close_time = 0
    
    async def on_tick(self, market_data):
        """每个 tick 调用"""
        
        # === Phase 0: 喂数据 ===
        self.kline_buffer.update_tick(
            price=market_data.mid_price,
            volume=market_data.volume,
            is_buy=market_data.is_buy,
            timestamp=market_data.timestamp,
        )
        
        # === Phase 1: 极端事件检查（最高优先级） ===
        guard_action = self.guard.check(self._build_state(market_data))
        if guard_action:
            await self._handle_extreme_event(guard_action)
            return
        
        # === Phase 2: 1m K 线刚收盘？更新状态机 ===
        current_minute = int(market_data.timestamp // 60)
        if current_minute > self.last_1m_close_time and len(self.kline_buffer.buffer) >= self.HMA_PERIOD:
            self._update_state_machine()
            self.last_1m_close_time = current_minute
        
        # === Phase 3: 计算指标 ===
        klines = list(self.kline_buffer.buffer)
        if len(klines) < self.HMA_PERIOD:
            return  # warmup 中
        
        prices = [k.close for k in klines]
        hma_value = hma(prices, self.HMA_PERIOD)
        atr_value = atr(klines, self.ATR_PERIOD)
        bands = compute_bands(hma_value, atr_value)
        
        # === Phase 4: 当前状态 + 决策 ===
        current_state = self.state_machine.current_state
        deviation_atr = (market_data.mid_price - hma_value) / (atr_value + 1e-10)
        decision = decide_action(current_state, deviation_atr)
        
        # === Phase 5: 生成报价 ===
        inventory_pressure = self._compute_inventory_pressure(market_data.equity)
        new_bids, new_asks = generate_quotes(
            mid=market_data.mid_price,
            hma=hma_value,
            atr=atr_value,
            state_decision=decision,
            inventory_pressure=inventory_pressure,
        )
        
        # === Phase 6: 惰性更新 ===
        if self._should_requote(new_bids, new_asks):
            await self.order_manager.update_orders(new_bids, new_asks)
    
    def _update_state_machine(self):
        """1m K线收盘后更新状态机"""
        klines = list(self.kline_buffer.buffer)
        if len(klines) < self.HMA_PERIOD:
            return
        
        prices = [k.close for k in klines]
        hma_value = hma(prices, self.HMA_PERIOD)
        atr_value = atr(klines, self.ATR_PERIOD)
        bands = compute_bands(hma_value, atr_value)
        
        last_close = klines[-1].close
        new_state = self.state_machine.update(last_close, bands)
        
        if new_state != self.state_machine.current_state:
            log.info(f"状态切换: {self.state_machine.current_state} → {new_state}")
    
    async def _handle_extreme_event(self, guard_action):
        """处理极端事件"""
        if guard_action['action'] == 'SHUTDOWN':
            await self.order_manager.cancel_all()
            await self._market_close_all_positions()
            self.state = 'SHUTDOWN'
            log.critical(f"停机: {guard_action['reason']}")
        elif guard_action['action'] == 'FLAT_AND_PAUSE':
            await self.order_manager.cancel_all()
            await self._market_close_all_positions()
            self.cooldown_until = time.now() + guard_action['cooldown_sec']
            log.error(f"全平+冷却: {guard_action['reason']}")
```


### 8.2 决策流程图

```
每个 tick 到达
    │
    ▼
┌─────────────────────────────┐
│  Phase 0: 喂数据到 K 线缓冲   │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐     ┌──────────────────┐
│  Phase 1: 极端事件检查        │ ──→ │ 触发？全平/停机   │
│  - 1m 波幅 > 100bps           │     └──────────────────┘
│  - 日亏 > 150bps              │
│  - 回撤 > 3%                  │
└──────────────┬──────────────┘
               │ 无极端事件
               ▼
┌─────────────────────────────┐
│  Phase 2: 1m K线刚收盘？      │
│  是 → 更新状态机              │
│  否 → 跳过                    │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  Phase 3: 计算 HMA + ATR     │
│  + 双层区间                   │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  Phase 4: 根据状态决策        │
│  NORMAL → 满载                │
│  WARN → 降仓40% + 加宽1.5x   │
│  STOP → 仅去库存              │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  Phase 5: 生成挂单           │
│  - 中心价 = mid + 偏移        │
│  - 间距 = 1.2 × ATR × 加宽    │
│  - 非对称层数（基于 bias）    │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  Phase 6: 惰性撤补单         │
│  变化 < 阈值 → 不动           │
│  变化 ≥ 阈值 → 撤补           │
└─────────────────────────────┘
```


---

## 9. 完整参数表

### 9.1 核心参数（共 6 个）

| 参数名 | 默认值 | 含义 | 调优范围 |
|--------|--------|------|---------|
| `HMA_PERIOD` | 200 | HMA 周期（基于 1m K 线） | 100~250 |
| `ATR_PERIOD` | 14 | ATR 周期 | 10~20 |
| `INNER_BAND_K` | 1.5 | 内层带宽倍数 | 1.0~2.0 |
| `OUTER_BAND_K` | 3.0 | 外层带宽倍数 | 2.5~4.0 |
| `GRID_INTERVAL_K` | 1.2 | 网格间距倍数 | 0.8~1.5 |
| `BASE_GRID_ORDERS` | 6 | 单侧基础挂单层数 | 4~8 |

### 9.2 极端事件参数（共 3 个）

| 参数名 | 默认值 | 含义 |
|--------|--------|------|
| `JUMP_BPS_1M` | 100 | 1m K线波幅熔断阈值（bps） |
| `DAILY_LOSS_HARD_BPS` | 150 | 日亏硬止阈值（bps） |
| `MAX_DRAWDOWN_PCT` | 3.0 | 最大回撤硬止阈值（%） |

### 9.3 状态机确认参数（固定，共 2 个）

| 参数名 | 默认值 | 含义 |
|--------|--------|------|
| `CONFIRM_BARS_TO_DANGER` | 2 | 进入更危险状态的确认 K 线数 |
| `CONFIRM_BARS_TO_SAFE` | 3 | 退出危险状态的确认 K 线数 |

### 9.4 报价相关常量（与策略类型无关，共 3 个）

| 参数名 | 默认值 | 含义 |
|--------|--------|------|
| `FEE_BPS` | 2 | 单边手续费（bps） |
| `SLIPPAGE_BPS` | 1 | 滑点缓冲（bps） |
| `BASE_ORDER_SIZE` | 按权益 1-2% | 单层基础下单量 |

**总参数数：6 + 3 + 2 + 3 = 14 个**（其中常量 3 个不需要调优）。

**真正需要回测调优的参数：6 个**。

### 9.5 与之前方案的参数对比

| 方案 | 总参数 | 需调优参数 | 过拟合风险 |
|------|-------|-----------|-----------|
| v1.0 (纯自创) | ~30 | ~25 | 极高 |
| v1.1 (六层风控+多周期) | ~78 | ~60 | 灾难性 |
| v1.1 ADX混合 | ~25 | ~15 | 高 |
| **本方案 (HMA-Band)** | **14** | **6** | **低** |


---

## 10. 回测验证要点

### 10.1 验收标准（Gate）

只有**全部通过**才算通过验证：

| Gate | 检验项 | 合格标准 | 不通过的处置 |
|------|-------|---------|------------|
| **G1** | 净 PnL（扣费后） | > 0 | 重新设计，不上线 |
| **G2** | Sharpe Ratio（日频） | > 1.0 | 参数调优 |
| **G3** | 最大回撤 | < 5% | 风控参数收紧 |
| **G4** | NORMAL 状态命中率 | > 50% | HMA 周期/带宽调优 |
| **G5** | 状态切换频率 | < 30次/天 | 确认 K 线数增加 |
| **G6** | 假突破比例 | < 30%（进入WARN后回到NORMAL） | 内层带宽调宽 |
| **G7** | STOP 状态期间无新仓 | 100% | 代码 bug |

### 10.2 必须做的鲁棒性测试

#### 1. 参数扰动测试

每个核心参数 ±20% 扰动，**性能变化必须 < 30%**：

```python
扰动测试：
HMA_PERIOD: [160, 180, 200, 220, 240]
ATR_PERIOD: [11, 13, 14, 15, 17]
INNER_BAND_K: [1.2, 1.35, 1.5, 1.65, 1.8]
OUTER_BAND_K: [2.4, 2.7, 3.0, 3.3, 3.6]

如果 PnL 在某个参数变化下骤降 50%，说明对该参数过拟合。
```

#### 2. 跨市场测试

**用同一套参数**（仅 ATR 自适应）在多个币种回测：
- BTC/USDT
- ETH/USDT
- SOL/USDT
- 任选一个低流动性币种

**要求**：每个市场都不亏（不要求都赚同样多）。

#### 3. Walk-Forward 测试

```
用前 60 天数据调参 → 后 30 天独立验证
向前推 30 天，重新训练 → 再后 30 天验证
重复 6-12 次

样本外平均 Sharpe > 0.8 才算合格
```

#### 4. 极端日测试

选 5-10 个历史"极端日"，单独测试：
- 2020-3-12 加密市场崩盘
- 2022-5 LUNA 崩盘
- 2022-11 FTX 崩盘
- 任何 BTC 单日波动 > 5% 的日子

**要求**：极端日**不爆仓**（最大回撤 < 5%）。即使亏损也接受，但必须有"日亏硬止"或"黑天鹅熔断"被触发的记录。

### 10.3 回测中需要警惕的"假阳性"

| 假阳性来源 | 警示信号 | 处理 |
|-----------|---------|------|
| **挂单一定成交** | Maker 成交率 > 80% | 加入概率成交模型 |
| **不模拟链上延迟** | 撤单立即生效 | 注入随机延迟 |
| **不模拟自影响** | 大单不动深度 | 加入冲击成本模型 |
| **数据回看偏差** | 用未来数据计算指标 | HMA/ATR 必须严格用历史 K 线 |

### 10.4 回测指标完整列表

```python
required_metrics = {
    # 收益类
    'net_pnl_bps': '净 PnL（扣费）',
    'sharpe_daily': '日频夏普',
    'max_drawdown_pct': '最大回撤',
    'profit_factor': '盈亏比',
    
    # 状态分布
    'time_in_normal_pct': '震荡市占比',
    'time_in_warn_pct': '警戒市占比',
    'time_in_stop_pct': '趋势市占比',
    
    # 状态机健康
    'state_switches_per_day': '每日状态切换次数',
    'avg_normal_duration_min': '平均震荡市持续时长',
    'false_breakout_rate': '假突破比例',
    
    # 执行质量
    'maker_fill_rate': 'Maker 成交率',
    'avg_holding_minutes': '平均持仓时长',
    'inventory_breach_count': '库存超限次数',
    
    # 极端事件
    'jump_triggered_count': '跳跃熔断触发次数',
    'daily_stop_count': '日亏硬止次数',
}
```


---

## 11. 与之前方案的全面对比

### 11.1 演进历程

```
v1.0 (自创 trend_score)
  ↓ 加补丁：发现不够准
v1.1 (加速度+量价+反转+六层风控+多周期)
  ↓ 反思：参数过多，逻辑冲突
混合方案 (ADX + ATR + tick信号)
  ↓ 进一步反思：仍然有冲突，参数仍多
本方案 (HMA-Band) ← 最终选择
```

### 11.2 核心对比表

| 维度 | v1.0 | v1.1 | ADX混合 | **HMA-Band** |
|------|------|------|---------|------------|
| 趋势判断方式 | tanh(动量+OBI+流) | 多周期+加速度 | ADX/DI | **位置(price vs HMA)** |
| 趋势/震荡区分 | 无 | 复杂多层 | ADX阈值 | **几何区间** |
| 参数总数 | ~30 | ~78 | ~25 | **14** |
| 需调优参数 | ~25 | ~60 | ~15 | **6** |
| 状态机 | 无 | 离散+连续混合 | 三状态 | **三状态+确认** |
| 方向偏置来源 | 多信号融合 | 多周期共振 | DI差值 | **天然来自偏离量** |
| 抗操纵 | 弱（OBI被spoof） | 弱 | 中（看成交价） | **强（看K线收盘价）** |
| 假突破鲁棒性 | 差 | 中 | 中 | **强（双层+确认）** |
| 历史验证 | 零 | 零 | ADX/ATR有 | **HMA+ATR都有** |
| 与做市契合度 | 中 | 中 | 间接 | **直接** |
| 实盘成功概率（主观） | 20% | 10% | 50% | **70%** |

### 11.3 为什么 HMA-Band 是最优解？

**1. 哲学层面**
- 做市的本质 = 赌均值回归
- HMA200 显性化"均值"
- 区间显性化"回归是否还在工作"
- **没有任何一层是冗余的**

**2. 数学层面**
- HMA 比 SMA/EMA 滞后小，比 EMA 更平滑（Alan Hull 的设计目标）
- ATR 自适应让区间随波动率自动调整
- 双层 + 确认 K 线 = 防止假突破抖动
- 这套组合是 **Keltner Channel 的优化版本**，经典套路有数十年验证

**3. 工程层面**
- 6 个核心参数（不算极端事件保护和常量）
- 状态机职责单一，没有逻辑冲突
- 每个模块只回答一个问题
- 易于单元测试，易于回测验证

**4. 可扩展性**
- 不同市场只需调 ATR_PERIOD 和 HMA_PERIOD
- 不同风险偏好只需调极端事件参数
- 信号本身（HMA + ATR）跨市场通用


---

## 12. 已知局限与诚实声明

### 12.1 这个方案不能解决什么问题

**1. 滞后是 HMA 的本质**
- HMA 比 SMA 快，但仍然是"过去 200 根 K 线"的反映
- 真趋势刚爆发时，HMA200 还在原位
- 所以"价格脱离区间"会比"趋势真正确认"晚约 2-5 根 1m K 线
- **接受这个滞后**：不要试图用更短的 HMA 周期来"解决"它，会引入更多噪音

**2. 极端 V 形反转**
- 价格冲出外层 → STOP → 立即急速回归 → 状态机还在 STOP
- 这种情况下，会**错过回归利润**
- 但同时也避免了**回归失败的损失**——这是合理代价

**3. 高频假突破（罕见但存在）**
- 价格反复进出内层但从不进外层
- 状态机会在 NORMAL ⟷ WARN 之间切换
- 但因为有 3 根 K 线确认，频率不会太高（日均 < 30 次）

### 12.2 这个方案的"信仰前提"

**必须承认**：策略的有效性建立在以下假设上，**如果这些假设不成立，策略一定失效**：

| 假设 | 说明 |
|------|------|
| 价格在中短周期内有均值回归倾向 | 大部分时间 BTC/ETH 等主流币是均值回归的 |
| HMA200(1m) 是有效的"中枢" | 经验上是，但不同市场可能需要调 |
| ATR 是合理的波动率度量 | 经过几十年验证 |
| 状态机的"确认 K 线"能过滤大部分噪音 | 数学上确认次数与抖动率是线性关系 |

**如果发现回测中策略不赚钱，应该首先检查这些假设是否在该市场成立，而不是急于增加复杂度。**

### 12.3 不要做什么（避免过拟合的禁忌）

- ❌ **不要**为不同市场设置不同 HMA 周期（除非有强理由）
- ❌ **不要**让状态机参数依赖时间段（如"美盘用2根，亚盘用3根"）
- ❌ **不要**把内外层带宽改成"动态"（如"高波动时用2倍ATR，低波动时用1倍"）
- ❌ **不要**在状态机外加"机器学习预测层"
- ❌ **不要**因为某个特殊行情（如 LUNA 崩盘日）单独调参

**牢记**：每多一个条件分支，过拟合风险翻倍。


---

## 13. 落地路线

### 13.1 开发阶段

| 阶段 | 周期 | 内容 | 验收 |
|------|------|------|------|
| **Week 1** | 5 天 | HMA + ATR + 双层带宽计算 | 与 TA-Lib 对比，误差 < 0.1% |
| **Week 2** | 5 天 | 状态机 + 确认逻辑 | 单元测试覆盖 NORMAL/WARN/STOP 全部转换 |
| **Week 3** | 5 天 | 报价生成器 + 极端事件保护 | 集成测试，模拟 100 个 K 线场景 |
| **Week 4** | 5 天 | 回测框架（含成交建模、延迟） | 回测 30 天历史数据 |
| **Week 5-6** | 10 天 | 参数调优 + 鲁棒性测试 | 通过 G1-G7 全部 Gate |
| **Week 7-8** | 10 天 | Paper Trading（实盘信号，不真实下单） | 信号触发频率合理 |
| **Week 9+** | 持续 | 小资金实盘验证 | 2 周 PnL > 0 |

### 13.2 实盘启动前必做的检查清单

```
□ HMA + ATR 计算在 30 天历史数据上无 NaN/异常
□ 状态机在所有边界情况下不会卡住
□ 极端事件保护在历史极端日（2020-3-12 等）正确触发
□ 参数扰动测试 ±20% 内 PnL 变化 < 30%
□ Walk-forward 6 次以上，样本外 Sharpe > 0.8
□ Paper Trade 2 周，实盘信号与回测一致性 > 90%
□ 紧急停机机制（kill switch）测试通过
□ 监控告警接入（Telegram/Discord）
□ 资金规模初始 < $500，验证后再放量
```

### 13.3 实盘运行的监控指标

**日级**（每天复盘）：
- 净 PnL、Sharpe
- 每日状态分布（NORMAL/WARN/STOP 时长占比）
- 极端事件触发次数

**实时**（关键时刻告警）：
- 状态切换为 STOP 时通知
- 极端事件触发时通知
- 单笔超过 N bps 浮亏时通知
- 日亏达 100bps（接近硬止）时通知

---

## 14. 总结

### 一句话回顾

> **价格围绕 HMA200 做市，距离均值越远越警惕，超出外层立即只去库存。简单到经得起推敲——这就是这个策略的最大优势。**

### 核心创新点

| 创新 | 价值 |
|------|------|
| 用几何位置代替统计指标 | 更直接，更直观 |
| 双层区间 + K 线确认 | 防假突破，比"立即停"鲁棒 |
| 偏离量天然就是方向偏置 | 不需要单独的方向指标 |
| 6 个核心参数 | 抗过拟合 |
| 3 条极端事件硬规则 | 风控简洁 |

### 与之前所有方案的关系

```
v1.0 → v1.1 → 混合 → HMA-Band
精巧但虚 → 全面但乱 → 混合但抖 → 简单且稳
```

### 最后的诚实

这个方案**不能保证赚钱**——任何策略都不能。但它在以下方面比之前所有版本都更可靠：

1. ✅ **更容易回测验证**（参数少）
2. ✅ **更容易调试**（每个模块职责单一）
3. ✅ **更难过拟合**（基于经典指标）
4. ✅ **更经得起推敲**（每个设计都有清晰理由）
5. ✅ **更接近做市本质**（赌均值回归）

**如果这个方案回测都不赚钱，那应该重新考虑做市策略本身的可行性，而不是继续加补丁。**

---

*文档版本: v1.0*  
*创建日期: 2026-05-21*  
*基于：HMA + ATR + 双层带宽 + 状态机 + 极简风控*  
*核心参数: 6 个（HMA_PERIOD, ATR_PERIOD, INNER_BAND_K, OUTER_BAND_K, GRID_INTERVAL_K, BASE_GRID_ORDERS）*
