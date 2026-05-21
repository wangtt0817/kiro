# HMA-Band 做市策略 v2.0

## 针对 v1 的根本性修复

> **核心变化**：放弃"单一 HMA200 作为中枢"的设计哲学。改用**双中枢漂移检测**，并用**简化的两状态机**消除"逆势加仓"陷阱。

---

## 0. v1 问题速览

详细诊断见 `hma_band_mm_strategy_v1.md` 第 11 节自审。三类核心问题：

| 类别 | 问题 | v2 的修复方向 |
|------|------|--------------|
| **致命** | HMA 滞后 + 中枢迁移期持续亏损 | 双中枢漂移检测 |
| **致命** | WARN 状态"逆势加仓"陷阱 | 简化为 TRADE/DEFEND/HALT |
| **严重** | 状态切换 5 分钟空窗无保护 | tick 级快速止损 |
| **中等** | 中心价 mid 与判断指标 HMA 不一致 | 信号和执行同步 |
| **轻** | 6 层、1.5/3.0 等数值缺乏依据 | 从其他参数推导 |




---

## 1. 设计哲学的根本转变

### 1.1 v1 的核心错误

v1 假设："HMA200 是市场的真实中枢，价格围绕它震荡。"

**实际不成立**：
- HMA200 滞后于 200 分钟
- 加密市场的中枢可以在 30-60 分钟内迁移
- HMA 追赶期间，所有判断都基于"过期的中枢"
- 此时 v1 的 WARN 状态会**在新中枢下方逆势卖出**或**新中枢上方逆势买入**

### 1.2 v2 的新哲学

> **单一均线无法既准又快。**用**两条均线的相对位置**来判断市场所处的"模式"。

```
短期 VWAP(30min) ≈ HMA200    → 中枢稳定，做市黄金期
短期 VWAP(30min) 偏离 HMA200 → 中枢正在迁移，不做市
```

**关键洞察**：均值回归只在"短期中枢"附近有效。**长期中枢只用来判断"短期中枢是否还在合理位置"**。

### 1.3 v1 → v2 设计差异表

| 维度 | v1 | v2 |
|------|-----|-----|
| 中枢 | HMA200 单一 | 双中枢（VWAP30 + HMA200）|
| 状态数 | 3 (NORMAL/WARN/STOP) | 3 (TRADE/DEFEND/HALT) |
| WARN 行为 | 半仓 + 逆势加仓 | **取消**，直接改为 DEFEND |
| 报价中心 | mid（但判断用HMA）| mid（判断也基于VWAP和mid）|
| 方向偏置基于 | mid - HMA200 | mid - VWAP30 |
| 库存切换 | 0.7 硬阈值 | sigmoid 平滑 |
| 层数 | 拍脑袋6层 | 从间距推导 |
| 熔断阈值 | 固定100bps | 5×ATR 自适应 |
| 单笔保护 | 无 | tick级浮亏止损 |
| 跨市场 | 假装通用 | 明确适用性边界 |



---

## 2. v2 完整架构

```
┌─────────────────────────────────────────────────────────────────┐
│                       HMA-Band v2 架构                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Layer 0: 极端事件 + tick级保护（一票否决）                      │
│   - 1m 波幅 > 5×ATR → 全平                                      │
│   - 单笔浮亏 > 2×ATR → 立即平该笔                                │
│   - 日亏 > 150bps / 回撤 > 3% → 停机                             │
│                       │                                          │
│  Layer 1: 双中枢指标层                                            │
│   - VWAP30: 30 分钟成交量加权均价（短期中枢，反应快）             │
│   - HMA200: 200 分钟 Hull 均值（长期中枢，平滑）                 │
│   - ATR(14): 自适应波动率                                        │
│                       │                                          │
│  Layer 2: 漂移检测 + 状态机                                       │
│   ▸ |VWAP30 - HMA200| < drift_threshold × ATR  → 中枢稳定        │
│   ▸ |VWAP30 - HMA200| ≥ drift_threshold × ATR  → 中枢漂移        │
│   ▸ 状态机：TRADE / DEFEND / HALT（仅3态，无 WARN）              │
│                       │                                          │
│  Layer 3: 决策层                                                 │
│   - TRADE：双边做市，方向偏置基于 mid 相对 VWAP30                 │
│   - DEFEND：仅去库存（中枢漂移 OR 价格离 VWAP 太远）              │
│   - HALT：完全暂停（极端事件）                                    │
│                       │                                          │
│  Layer 4: 报价生成                                                │
│   - 中心价 = mid + (库存清仓 + 短期回归) 偏移                     │
│   - 间距 = 1.2 × ATR                                             │
│   - 层数 = 自动推导（不再是参数）                                  │
│   - 库存压力用 sigmoid，无硬切换                                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```



---

## 3. Layer 1: 双中枢指标系统

### 3.1 为什么是 VWAP30 + HMA200？

| 指标 | 时间尺度 | 角色 | 关键特性 |
|------|---------|------|---------|
| VWAP30 | 30 分钟 | **短期公允价中枢** | 反应快，反映"当前真实成交价"|
| HMA200 | 200 分钟 | **长期参考中枢** | 平滑，反映"过去几小时的中心" |
| 两者之差 | — | **中枢迁移检测器** | 它们一致 = 稳定，分歧 = 迁移 |

### 3.2 VWAP30 计算

```python
def compute_vwap_30min(klines):
    """
    30 分钟成交量加权均价
    用最近 30 根 1m K 线的 (close × volume) / volume
    """
    if len(klines) < 30:
        klines = klines  # 数据不足时用全部
    else:
        klines = klines[-30:]
    
    total_pv = sum(k.close * k.volume for k in klines)
    total_v = sum(k.volume for k in klines)
    
    if total_v == 0:
        return klines[-1].close  # 极端情况fallback
    
    return total_pv / total_v
```

### 3.3 中枢漂移检测

```python
def detect_centroid_drift(vwap30, hma200, atr):
    """
    返回: 'STABLE' | 'DRIFTING'
    
    drift_distance: VWAP 与 HMA 的距离，以 ATR 为单位
    """
    drift_distance_atr = abs(vwap30 - hma200) / (atr + 1e-10)
    
    if drift_distance_atr > DRIFT_THRESHOLD_ATR:  # 默认 2.0
        return 'DRIFTING', drift_distance_atr
    return 'STABLE', drift_distance_atr
```

### 3.4 这个设计如何解决 v1 问题 1（HMA 滞后）

**场景**：价格从 $50,000 涨到 $51,000 后稳定

| 时间 | mid | VWAP30 | HMA200 | drift | v1 行为 | v2 行为 |
|------|-----|--------|--------|-------|--------|--------|
| T+0 | 51000 | 50100 | 50050 | 0.2σ | NORMAL（基于HMA错） | TRADE（VWAP还没追上）|
| T+10min | 51000 | 50500 | 50080 | 1.5σ | WARN（错过半小时机会）| **DRIFTING → DEFEND**|
| T+30min | 51000 | 50900 | 50180 | 2.5σ | STOP | DRIFTING |
| T+60min | 51000 | 51000 | 50450 | 1.9σ | STOP（错过1小时震荡）| DRIFTING |
| T+90min | 51000 | 51000 | 50700 | 1.0σ | STOP | **STABLE → TRADE 恢复** |

**关键差别**：v2 在 T+90min 就能恢复做市，v1 要等到 HMA 完全追上（约 T+150min+）才恢复。



---

## 4. Layer 2: 简化状态机

### 4.1 状态定义（3 态，但语义和 v1 完全不同）

| 状态 | 触发条件 | 行为 | 关键差异 |
|------|---------|------|---------|
| `TRADE` | 中枢稳定 + 价格离 VWAP30 < 3×ATR | **正常双边做市** | 唯一开新仓的状态 |
| `DEFEND` | 中枢漂移 OR 价格离 VWAP30 ≥ 3×ATR | **仅去库存，不开新仓** | 取代 v1 的 WARN+STOP |
| `HALT` | 极端事件触发 | **全平+冷却** | 等同 v1 的极端事件保护 |

**v1 的 WARN 状态被彻底移除**。v1 中"WARN = 半仓+逆势加仓"是策略最大的亏损来源。v2 中只要不在 TRADE 状态，就**不开任何新仓**。

### 4.2 状态切换规则（用 K 线收盘价确认，避免 tick 噪音）

```
TRADE → DEFEND：以下任一条件，连续 2 根 1m K 线收盘价满足
    (a) 中枢漂移：|VWAP30 - HMA200| ≥ 2.0 × ATR
    (b) 价格远离：|mid - VWAP30| ≥ 3.0 × ATR

DEFEND → TRADE：必须同时满足，连续 3 根 1m K 线收盘价
    (a) 中枢稳定：|VWAP30 - HMA200| < 1.5 × ATR  ← 注意有滞回
    (b) 价格回归：|mid - VWAP30| < 2.0 × ATR  ← 注意有滞回

任意 → HALT：极端事件触发（即时，不需要确认）
HALT → TRADE：冷却结束 + 5 分钟 warmup
```

**滞回带（hysteresis）的重要性**：
- 进入阈值 2.0×ATR，退出阈值 1.5×ATR
- 价格在 1.5-2.0×ATR 之间时**保持原状态**
- 这是消除 v1 的"边界跳变"问题（v1 问题 8）的标准做法

### 4.3 为什么不再需要 5 分钟确认？

v1 用 3+2 根 K 线确认（共 5 分钟），是为了避免假突破。但代价是 5 分钟的裸露窗口。

v2 的解决方案：
1. **2 根 K 线进入 DEFEND**（v1 是 3 根）→ 反应更快
2. **滞回带**消除频繁切换 → 不再需要长时间确认
3. **Layer 0 的 tick 级单笔止损**填补剩余空窗 → 见第 5 节

### 4.4 状态机伪代码

```python
class RegimeStateMachine:
    TRADE = 'TRADE'
    DEFEND = 'DEFEND'
    HALT = 'HALT'
    
    def __init__(self):
        self.state = self.TRADE
        self.pending = None
        self.pending_count = 0
        self.halt_until = 0
    
    def update(self, mid, vwap30, hma200, atr, timestamp):
        # 优先处理 HALT 恢复
        if self.state == self.HALT:
            if timestamp >= self.halt_until:
                self.state = self.TRADE
                self.pending = None
            return self.state
        
        # 计算应该进入的状态
        drift_ratio = abs(vwap30 - hma200) / (atr + 1e-10)
        deviation_ratio = abs(mid - vwap30) / (atr + 1e-10)
        
        if self.state == self.TRADE:
            # 进入 DEFEND 的条件（任一）
            should_defend = (drift_ratio >= 2.0) or (deviation_ratio >= 3.0)
            target = self.DEFEND if should_defend else self.TRADE
            confirm_required = 2  # 进入危险 = 快确认
        else:  # DEFEND
            # 退回 TRADE 的条件（必须都满足，且滞回带）
            should_trade = (drift_ratio < 1.5) and (deviation_ratio < 2.0)
            target = self.TRADE if should_trade else self.DEFEND
            confirm_required = 3  # 退出危险 = 慢确认
        
        if target == self.state:
            self.pending = None
            self.pending_count = 0
        else:
            if self.pending == target:
                self.pending_count += 1
            else:
                self.pending = target
                self.pending_count = 1
            
            if self.pending_count >= confirm_required:
                self.state = target
                self.pending = None
        
        return self.state
    
    def trigger_halt(self, cooldown_sec, timestamp):
        self.state = self.HALT
        self.halt_until = timestamp + cooldown_sec + 300  # +5min warmup
```



---

## 5. Layer 0: 三层风控（修复 v1 问题 3、9、11）

### 5.1 责任划分明确化（修复 v1 问题 11）

```
┌──────────────────────────────────────────────────────────────┐
│  L0-A: tick级单笔保护（最快，每个tick检查）                    │
│   - 单笔浮亏 > 2×ATR → 立即市价平该笔                          │
│   - 单笔持仓 > 30 分钟 → 强制平仓                              │
│  目的：填补状态机切换的空窗期                                    │
├──────────────────────────────────────────────────────────────┤
│  L0-B: 1m级跳跃熔断（每根 K 线收盘后检查）                      │
│   - 单根 1m K 线波幅 > 5×ATR → HALT 600 秒                     │
│  目的：黑天鹅保护                                                │
├──────────────────────────────────────────────────────────────┤
│  L0-C: 日级硬止损（每分钟检查）                                  │
│   - 日累计亏损 > 150 bps → SHUTDOWN（人工恢复）                 │
│   - 从权益峰值回撤 > 3% → SHUTDOWN                             │
│  目的：终极保护                                                  │
└──────────────────────────────────────────────────────────────┘
```

**优先级**：L0-A → L0-B → L0-C → 状态机 → 报价生成

任意一层触发都覆盖下层逻辑，**没有歧义**。

### 5.2 L0-A：tick级单笔保护（修复 v1 问题 3）

```python
class TickLevelGuard:
    SINGLE_STOP_ATR = 2.0     # 单笔浮亏 2×ATR 止损
    SINGLE_MAX_HOLD_SEC = 1800  # 单笔最多持仓 30 分钟
    
    def check_positions(self, positions, current_atr_bps, timestamp):
        """每个 tick 调用，返回需要立即平仓的 position id 列表"""
        to_close = []
        threshold_bps = self.SINGLE_STOP_ATR * current_atr_bps
        
        for pos in positions:
            # 浮亏止损
            if pos.unrealized_pnl_bps < -threshold_bps:
                to_close.append({
                    'pos_id': pos.id,
                    'reason': f'单笔浮亏{pos.unrealized_pnl_bps:.0f}bps>{threshold_bps:.0f}'
                })
            # 超时止损
            elif (timestamp - pos.open_time) > self.SINGLE_MAX_HOLD_SEC:
                to_close.append({
                    'pos_id': pos.id,
                    'reason': f'持仓{(timestamp - pos.open_time)/60:.0f}分钟超时'
                })
        return to_close
```

**为什么这个能填补 5 分钟空窗？**

v1 场景：突然趋势 → 状态机要 5 分钟才切到 STOP → 期间持续被吃单。

v2 场景：第一笔被吃后浮亏开始累积，2-3 分钟内任何一笔浮亏到 2×ATR 立即平掉。**最坏情况单笔亏 2×ATR**（约 60-100bps），不会让单笔亏损无限放大。

### 5.3 L0-B：自适应熔断（修复 v1 问题 9）

```python
class JumpGuard:
    JUMP_THRESHOLD_ATR = 5.0   # 1m 波幅 > 5×ATR 熔断
    HALT_COOLDOWN_SEC = 600    # 冷却 10 分钟
    
    def check(self, last_1m_kline, current_atr):
        """每根 1m K 线收盘后调用"""
        range_bps = (last_1m_kline.high - last_1m_kline.low) / last_1m_kline.close * 10000
        atr_bps = current_atr / last_1m_kline.close * 10000
        
        if range_bps > self.JUMP_THRESHOLD_ATR * atr_bps:
            return {
                'action': 'HALT',
                'reason': f'1m波幅{range_bps:.0f}bps>{self.JUMP_THRESHOLD_ATR}×ATR',
                'cooldown_sec': self.HALT_COOLDOWN_SEC,
            }
        return None
```

**与 v1 的区别**：v1 是固定 100bps，v2 是 5×ATR。在高波动期 ATR=80bps 时，v2 阈值是 400bps，不会被正常波动误触发。

### 5.4 L0-C：日级硬止损（保留 v1 设计）

```python
class DailyGuard:
    DAILY_LOSS_HARD_BPS = 150
    MAX_DRAWDOWN_PCT = 3.0
    
    def check(self, daily_pnl_bps, drawdown_pct):
        if daily_pnl_bps < -self.DAILY_LOSS_HARD_BPS:
            return {'action': 'SHUTDOWN', 'reason': f'日亏{daily_pnl_bps:.0f}bps'}
        if drawdown_pct > self.MAX_DRAWDOWN_PCT:
            return {'action': 'SHUTDOWN', 'reason': f'回撤{drawdown_pct:.1f}%'}
        return None
```



---

## 6. Layer 4: 报价生成（修复 v1 问题 4、5、6、8）

### 6.1 报价中心：明确的层级（修复 v1 问题 6）

v1 的矛盾：用 mid 当中心但用 HMA 判断状态。v2 的解决：

```
最终中心价 = mid + 短期回归偏移 + 库存清仓偏移

分量1：短期回归偏移 = -k1 × (mid - VWAP30) / ATR
       目的：基于"mid 相对短期中枢"的回归（短期均值回归）
       
分量2：库存清仓偏移 = -k2 × inventory_pressure
       目的：库存超限时强制平仓方向
       平滑：用 sigmoid，无硬阈值切换
```

**关键改变**：
- 不再用 HMA200 做方向偏置（HMA 只用于检测中枢漂移，**不直接影响报价**）
- 短期回归基于 VWAP30，这是真正"快速可成交"的中枢
- 信号和执行**完全同步**

### 6.2 短期回归偏移计算

```python
def compute_reversion_offset(mid, vwap30, atr):
    """
    基于 mid 相对 VWAP30 的偏离做回归偏置
    """
    if atr == 0:
        return 0.0
    
    deviation_atr = (mid - vwap30) / atr
    
    # 用 tanh 做连续映射，最大偏置 0.5
    # mid 距 VWAP30 偏 1×ATR 时，偏置约 -0.38
    # mid 距 VWAP30 偏 2×ATR 时，偏置约 -0.50
    bias = -0.5 * tanh(deviation_atr)
    return bias  # 范围约 [-0.5, +0.5]
```

**注意**：这是 TRADE 状态下才用的偏置。在 DEFEND 状态下完全不用此偏置（因为已经判定为"非震荡市"）。

### 6.3 库存压力的 sigmoid 平滑（修复 v1 问题 4）

```python
def compute_inventory_offset(inventory_pressure):
    """
    inventory_pressure ∈ [-1, +1]，正值表示多头库存
    
    用 sigmoid 平滑曲线：
    - 库存 0 时偏置接近 0
    - 库存 ±0.5 时偏置约 ±0.4
    - 库存 ±1.0 时偏置接近 ±0.95
    
    没有硬阈值切换，行为连续
    """
    # 用 4×inventory_pressure 让中等库存就有显著偏置
    sigmoid_value = 2 / (1 + exp(-4 * inventory_pressure)) - 1
    return -sigmoid_value  # 负号：多头库存→偏卖
```

**对比 v1 的 0.7 硬阈值**：
- v1：库存从 0.69→0.71 时，偏置从 +0.14 跳到 -0.71
- v2：库存连续平滑变化，0.69→0.71 时偏置变化约 0.05

### 6.4 中心价合成

```python
def compute_center_price(mid, vwap30, atr, inventory_pressure, state):
    """合成最终报价中心价"""
    if state != 'TRADE':
        # DEFEND 状态：中心价就是 mid，但报价方向只走去库存方向
        return mid
    
    # 两个偏置分量
    reversion = compute_reversion_offset(mid, vwap30, atr)
    inventory = compute_inventory_offset(inventory_pressure)
    
    # 加权合成（短期回归权重 50%，库存清理权重 50%）
    combined_bias = 0.5 * reversion + 0.5 * inventory
    combined_bias = clamp(combined_bias, -0.7, 0.7)
    
    # 最大偏移 = 1×ATR
    max_shift_bps = atr / mid * 10000
    shift_bps = combined_bias * max_shift_bps
    
    return mid * (1 - shift_bps / 10000)
```

### 6.5 间距和层数（修复 v1 问题 5）

```python
def compute_grid_layout(atr, mid):
    """
    间距：1.2 × ATR，但有成本下限
    层数：从间距和外层带宽推导，不再是参数
    """
    # 间距
    cost_floor_bps = (FEE_BPS * 2) + SLIPPAGE_BPS
    atr_bps = atr / mid * 10000
    interval_bps = max(cost_floor_bps, GRID_INTERVAL_K * atr_bps)
    
    # 层数：覆盖到 STOP 阈值（3×ATR）即可
    # 超过这个范围价格已经触发 DEFEND，挂远端层数无意义
    coverage_atr = STOP_THRESHOLD_ATR  # 3.0
    coverage_bps = coverage_atr * atr_bps
    
    layers = max(2, min(5, ceil(coverage_bps / interval_bps)))
    # 通常等于 2 或 3 层
    
    return interval_bps, layers
```

**为什么不再是 6 层？**
- 远端层覆盖范围超过 3×ATR 时，要么状态机已切到 DEFEND，要么熔断已触发
- 远端层永远不会成交，徒增撤补单成本
- 推导后通常是 **2-3 层/侧**，符合常识

### 6.6 报价生成完整流程

```python
def generate_quotes(mid, vwap30, atr, state, inventory_pressure):
    """生成完整双边挂单"""
    
    if state == 'HALT':
        return [], []  # 全平已在 HALT 触发时执行
    
    if state == 'DEFEND':
        return generate_defend_orders(mid, atr, inventory_pressure)
    
    # state == 'TRADE'
    center = compute_center_price(mid, vwap30, atr, inventory_pressure, state)
    interval_bps, layers_per_side = compute_grid_layout(atr, mid)
    
    # 双边对称挂单（方向偏置已经反映在 center 里）
    base_size = BASE_ORDER_SIZE
    
    bids = []
    for i in range(layers_per_side):
        price = center * (1 - (i + 1) * interval_bps / 10000)
        bids.append(Order('buy', price, base_size))
    
    asks = []
    for i in range(layers_per_side):
        price = center * (1 + (i + 1) * interval_bps / 10000)
        asks.append(Order('sell', price, base_size))
    
    return bids, asks


def generate_defend_orders(mid, atr, inventory_pressure):
    """DEFEND 状态：仅生成去库存方向的远端单"""
    if abs(inventory_pressure) < 0.1:
        return [], []  # 无库存，不挂单
    
    atr_bps = atr / mid * 10000
    layers = 2  # DEFEND 状态固定 2 层
    
    if inventory_pressure > 0:  # 多头库存→挂卖单
        asks = []
        for i in range(layers):
            offset = (1.5 + i * 0.5) * atr_bps  # 起步距 mid 1.5×ATR
            price = mid * (1 + offset / 10000)
            asks.append(Order('sell', price, BASE_ORDER_SIZE))
        return [], asks
    else:  # 空头库存→挂买单
        bids = []
        for i in range(layers):
            offset = (1.5 + i * 0.5) * atr_bps
            price = mid * (1 - offset / 10000)
            bids.append(Order('buy', price, BASE_ORDER_SIZE))
        return bids, []
```



---

## 7. 完整执行循环

### 7.1 主类骨架

```python
class HMABandMarketMakerV2:
    """v2 主策略：双中枢 + 简化状态机 + tick级保护"""
    
    # ===== 核心参数（10 个） =====
    SHORT_VWAP_PERIOD = 30        # VWAP 时间窗（分钟）
    HMA_PERIOD = 200              # HMA 周期（分钟）
    ATR_PERIOD = 14               # ATR 周期
    DRIFT_THRESHOLD_ATR = 2.0     # 中枢漂移阈值（×ATR）
    STOP_THRESHOLD_ATR = 3.0      # 价格远离阈值（×ATR）
    GRID_INTERVAL_K = 1.2         # 间距系数
    SINGLE_STOP_ATR = 2.0         # 单笔止损（×ATR）
    JUMP_THRESHOLD_ATR = 5.0      # 熔断阈值（×ATR）
    DAILY_LOSS_HARD_BPS = 150     # 日亏硬止损
    MAX_DRAWDOWN_PCT = 3.0        # 回撤硬止损
    
    def __init__(self, config):
        self.kline_buffer = KlineBuffer(period_sec=60, maxlen=300)
        self.state_machine = RegimeStateMachine()
        self.tick_guard = TickLevelGuard()
        self.jump_guard = JumpGuard()
        self.daily_guard = DailyGuard()
        self.order_manager = OrderManager(config)
        self.last_1m_close_time = 0
    
    async def on_tick(self, market_data):
        """每个 tick 调用"""
        # === Phase 0: 喂数据 ===
        self.kline_buffer.update_tick(...)
        
        # === Phase 1A: tick级单笔保护（最高优先级） ===
        atr_value = atr(self.kline_buffer.last(50), self.ATR_PERIOD)
        positions_to_close = self.tick_guard.check_positions(
            self.order_manager.positions,
            atr_value / market_data.mid_price * 10000,
            market_data.timestamp,
        )
        for pos_action in positions_to_close:
            await self.order_manager.market_close(pos_action['pos_id'])
            log.warning(f"tick止损: {pos_action['reason']}")
        
        # === Phase 1B: 日级保护 ===
        daily = self.daily_guard.check(self.daily_pnl_bps, self.drawdown_pct)
        if daily:
            await self._shutdown(daily['reason'])
            return
        
        # === Phase 2: 1m K 线刚收盘？ ===
        current_minute = int(market_data.timestamp // 60)
        if current_minute > self.last_1m_close_time:
            await self._on_1m_close(market_data, atr_value)
            self.last_1m_close_time = current_minute
        
        # === Phase 3: warmup 检查 ===
        if len(self.kline_buffer.buffer) < self.HMA_PERIOD:
            return  # 数据不足
        
        # === Phase 4: 计算指标 ===
        klines = list(self.kline_buffer.buffer)
        prices = [k.close for k in klines]
        vwap30 = compute_vwap_30min(klines)
        hma200 = hma(prices, self.HMA_PERIOD)
        
        # === Phase 5: 当前状态决策 ===
        state = self.state_machine.state  # 已在 Phase 2 更新
        
        # === Phase 6: 生成报价 ===
        inventory_pressure = self._compute_inventory_pressure()
        new_bids, new_asks = generate_quotes(
            mid=market_data.mid_price,
            vwap30=vwap30,
            atr=atr_value,
            state=state,
            inventory_pressure=inventory_pressure,
        )
        
        # === Phase 7: 惰性更新 ===
        if self._should_requote(new_bids, new_asks):
            await self.order_manager.update_orders(new_bids, new_asks)
    
    async def _on_1m_close(self, market_data, atr_value):
        """1m K 线收盘后调用"""
        klines = list(self.kline_buffer.buffer)
        last_close = klines[-1].close
        
        # B 层熔断检查
        jump_result = self.jump_guard.check(klines[-1], atr_value)
        if jump_result:
            self.state_machine.trigger_halt(
                jump_result['cooldown_sec'],
                market_data.timestamp,
            )
            await self.order_manager.cancel_all()
            await self._market_close_all_positions()
            log.error(f"熔断: {jump_result['reason']}")
            return
        
        # 正常状态机更新
        if len(klines) >= self.HMA_PERIOD:
            prices = [k.close for k in klines]
            vwap30 = compute_vwap_30min(klines)
            hma200 = hma(prices, self.HMA_PERIOD)
            
            old_state = self.state_machine.state
            new_state = self.state_machine.update(
                last_close, vwap30, hma200, atr_value, market_data.timestamp,
            )
            if old_state != new_state:
                log.info(f"状态: {old_state} → {new_state}")
```

### 7.2 决策流程图

```
每个 tick
   ↓
┌──────────────────────────────┐
│ Phase 1A: tick级单笔止损        │ → 触发：市价平该笔
└────────────┬─────────────────┘
             ↓
┌──────────────────────────────┐
│ Phase 1B: 日级硬止损            │ → 触发：SHUTDOWN
└────────────┬─────────────────┘
             ↓
┌──────────────────────────────┐
│ Phase 2: 1m K线刚收盘？         │
│  是 → 熔断检查 + 状态机更新     │
│  否 → 跳过                      │
└────────────┬─────────────────┘
             ↓
┌──────────────────────────────┐
│ Phase 4: 计算 VWAP30 + HMA200   │
└────────────┬─────────────────┘
             ↓
┌──────────────────────────────┐
│ Phase 5-6: 根据状态生成报价     │
│  TRADE → 双边做市               │
│  DEFEND → 仅去库存              │
│  HALT → 不挂单                  │
└────────────┬─────────────────┘
             ↓
┌──────────────────────────────┐
│ Phase 7: 惰性撤补单             │
└──────────────────────────────┘
```



---

## 8. 完整参数表

### 8.1 核心参数（10 个，对比 v1 的 14 个）

| 参数名 | 默认值 | 含义 | 调优范围 | 类型 |
|--------|--------|------|---------|------|
| `SHORT_VWAP_PERIOD` | 30 | 短期 VWAP 时间窗（分钟） | 20~60 | 周期 |
| `HMA_PERIOD` | 200 | HMA 周期（分钟） | 150~250 | 周期 |
| `ATR_PERIOD` | 14 | ATR 周期 | 10~20 | 周期 |
| `DRIFT_THRESHOLD_ATR` | 2.0 | 中枢漂移阈值 | 1.5~2.5 | 阈值 |
| `STOP_THRESHOLD_ATR` | 3.0 | 价格远离阈值 | 2.5~3.5 | 阈值 |
| `GRID_INTERVAL_K` | 1.2 | 间距系数 | 0.8~1.5 | 阈值 |
| `SINGLE_STOP_ATR` | 2.0 | 单笔止损（×ATR） | 1.5~3.0 | 风控 |
| `JUMP_THRESHOLD_ATR` | 5.0 | 熔断阈值（×ATR） | 4.0~7.0 | 风控 |
| `DAILY_LOSS_HARD_BPS` | 150 | 日亏硬止损 | 100~250 | 风控 |
| `MAX_DRAWDOWN_PCT` | 3.0 | 回撤硬止损 | 2.0~5.0 | 风控 |

### 8.2 推导参数（不需调优）

| 参数 | 公式 | 说明 |
|------|------|------|
| 内层退出阈值 | DRIFT_THRESHOLD_ATR × 0.75 = 1.5 | 滞回带 |
| 价格回归退出 | STOP_THRESHOLD_ATR × 0.67 = 2.0 | 滞回带 |
| 单侧层数 | ceil(STOP × ATR / 间距) ≈ 2-3 | 自动推导 |
| HALT 冷却 | 600 秒（固定） | + 5min warmup |
| 单笔超时 | 1800 秒（固定） | 30 分钟 |

### 8.3 v1 → v2 参数变化

| v1 参数 | v2 处置 |
|---------|---------|
| INNER_BAND_K = 1.5 | **删除**（被 DRIFT_THRESHOLD_ATR 替代）|
| OUTER_BAND_K = 3.0 | **重命名为** STOP_THRESHOLD_ATR |
| BASE_GRID_ORDERS = 6 | **删除**（自动推导）|
| CONFIRM_BARS_TO_DANGER = 2 | 保留（不暴露为参数）|
| CONFIRM_BARS_TO_SAFE = 3 | 保留（不暴露为参数）|
| FEE_BPS, SLIPPAGE_BPS | 保留（市场常量）|
| BASE_ORDER_SIZE | 保留（资金管理）|
| JUMP_BPS_1M = 100 | **改为** JUMP_THRESHOLD_ATR = 5.0 |
| 新增 | SHORT_VWAP_PERIOD = 30 |
| 新增 | DRIFT_THRESHOLD_ATR = 2.0 |
| 新增 | SINGLE_STOP_ATR = 2.0 |

### 8.4 参数总数对比

| 方案 | 总参数 | 真正需调优 | 复杂度 |
|------|-------|-----------|--------|
| v1.0 (自创信号) | ~30 | ~25 | 高 |
| v1.1 (六层风控) | ~78 | ~60 | 灾难 |
| v1.0 HMA-Band | 14 | 6 | 低 |
| **v2.0 双中枢** | **10** | **6-7** | **低** |



---

## 9. 适用性边界（修复 v1 问题 7）

### 9.1 明确的适用条件

**本策略仅适用于满足以下条件的市场**：

| 条件 | 阈值 | 检验方法 |
|------|------|---------|
| 主流币种 | BTC / ETH / SOL / BNB | 流动性充足，盘口稳定 |
| 1m ATR / 价格 | 0.03% ~ 0.20% | 过去 7 天平均 |
| 日均成交量 | > $100M | 链上或CEX |
| 盘口深度 | top10 > $50K | 实时检查 |

**不适用**：
- 山寨币（1m ATR > 0.3% 时状态机会疯狂切换）
- 流动性不足的交易对（撤补单成本占比过高）
- 重大事件前后（FOMC、CPI、黑天鹅日）

### 9.2 适用性自检（程序内置）

```python
def check_market_eligibility(klines_recent_7d):
    """每日 UTC 0:00 调用，决定今日是否运行策略"""
    
    if len(klines_recent_7d) < 7 * 24 * 60:  # 7 天 1m K 线
        return False, "数据不足"
    
    # 检查 1m ATR / 价格比例
    atrs = []
    for day in split_by_day(klines_recent_7d):
        atr_value = atr(day, 14)
        avg_price = mean(k.close for k in day)
        atrs.append(atr_value / avg_price)
    
    avg_atr_ratio = mean(atrs)
    
    if avg_atr_ratio > 0.002:  # 0.2%
        return False, f"波动率过高 ({avg_atr_ratio*100:.3f}%)"
    if avg_atr_ratio < 0.0003:  # 0.03%
        return False, f"波动率过低，做市利润不足"
    
    return True, "适用"
```

### 9.3 不再假装"参数跨市场通用"

v1 声称参数跨市场通用，实际上：
- BTC/ETH 上 INNER_BAND_K=1.5 合理
- SOL 上可能需要 INNER_BAND_K=2.0
- 山寨币上可能需要完全重设

v2 的态度：
- **每个市场单独标定参数**
- 但参数语义保持一致（2×ATR 始终是"2 倍标准差"）
- ATR 自适应让默认值在多数主流币上工作




---

## 10. v1 → v2 修复对照表

| # | v1 问题 | 严重度 | v2 解决方案 | 是否完全解决 |
|---|---------|-------|------------|------------|
| 1 | HMA200 滞后导致中枢迁移期持续亏损 | 🔴🔴🔴 | 双中枢漂移检测，VWAP30 反应快 + HMA200 参考 | ✅ 完全解决 |
| 2 | WARN 状态"逆势加仓"陷阱 | 🔴🔴🔴 | 移除 WARN，简化为 TRADE/DEFEND/HALT，DEFEND 不开新仓 | ✅ 完全解决 |
| 3 | 状态切换 5 分钟空窗无保护 | 🔴🔴 | tick 级单笔止损（2×ATR）+ 进入 DEFEND 减为 2 根 K 线 | ✅ 解决 80%+ |
| 4 | 库存 vs HMA 硬阈值切换 | 🟡🟡 | sigmoid 平滑 | ✅ 完全解决 |
| 5 | 6 层挂单层数无依据 | 🟡 | 从间距和阈值推导（通常 2-3 层）| ✅ 完全解决 |
| 6 | 中心价 mid vs 判断指标 HMA 不同步 | 🟡🟡 | 报价偏置基于 VWAP30，与短期中枢同步 | ✅ 完全解决 |
| 7 | 跨市场一致性是幻觉 | 🟡 | 明确适用性边界 + 每日自检 | ✅ 透明化 |
| 8 | 方向偏置在状态边界跳变 | 🟢 | 用 tanh 连续函数 + 滞回带 | ✅ 完全解决 |
| 9 | 熔断阈值脱离 ATR | 🟢 | 改为 5×ATR | ✅ 完全解决 |
| 10 | 1.5/3.0 倍数无依据 | 🟢 | 仍是经验值，但通过自检验证 | ⚠️ 改善但不完美 |
| 11 | 极端事件 vs 状态机职责模糊 | 🟢 | L0-A/B/C 三层明确分工 + 优先级 | ✅ 完全解决 |

### 10.1 综合改进效果（主观评估）

| 维度 | v1 实盘成功概率 | v2 实盘成功概率 |
|------|---------------|----------------|
| 震荡市（理想环境） | 70% | 75% |
| 中枢迁移期 | 20%（亏损主因）| 60%（DEFEND 保护）|
| 真趋势期 | 40% | 70%（不再加仓逆势）|
| 黑天鹅日 | 50% | 80%（tick 级止损）|
| **加权平均** | **30-40%** | **55-65%** |

**注意**：这仍然是主观估计，必须通过严格回测验证。




---

## 11. 残留风险与诚实声明

### 11.1 v2 仍然不能解决的问题

**1. 完全没有趋势的极端震荡**

如果价格在 30 分钟内反复穿越 VWAP30（如某些 meme 币的"砸盘后反复底部"行情），VWAP30 会接近 mid，HMA200 也会逐渐对齐，**此时 v2 在 TRADE 状态做市可能持续被吃**。

应对：单笔止损（2×ATR）兜底，但仍会持续亏损直到日亏硬止触发。

**2. 真正的"无中枢"趋势**

价格连续 30 分钟单边推进 → VWAP30 也跟着推进 → HMA200 滞后 → 漂移检测触发 DEFEND。

但这中间至少有 5-10 分钟，**v2 在 TRADE 状态被吃单**，必然产生库存。然后 DEFEND 状态需要去库存——**通常在更不利的价位**。

**这是任何均值回归做市策略的固有问题，无法完全避免。**

**3. 假突破后真趋势**

价格冲出 STOP 阈值 → DEFEND → 价格短暂回归 → TRADE → 再次冲出 → DEFEND...

如果反复 3-4 次（每次 5-10 分钟），库存可能在每个 DEFEND 阶段被错误时机平仓+新建，累积成本。

应对：滞回带 + 单笔超时止损 + tick 级止损。但**完美应对不存在**。

### 11.2 v2 的"信仰前提"

| 假设 | 必要性 | 验证方式 |
|------|-------|---------|
| 主流币短期内有均值回归倾向 | 必要 | 历史数据统计：mid 离 VWAP30 > 1×ATR 后回归概率 > 60% |
| VWAP30 是有效的"短期中枢" | 必要 | 与 mid 的相关性 > 0.95 |
| ATR 是合理的波动率度量 | 必要 | 50年验证 |
| 状态机切换的滞回带能消除抖动 | 必要 | 回测中状态切换 < 30 次/天 |

**如果这些假设在某市场不成立，策略一定失效。**

### 11.3 v2 的设计禁忌（避免重蹈 v1 覆辙）

- ❌ **不要**为了"反应更快"缩短 SHORT_VWAP_PERIOD 到 10 分钟以下（VWAP 会变得太敏感）
- ❌ **不要**给 DEFEND 状态加"半仓做市"逻辑（这就是 v1 的 WARN，已被证明是陷阱）
- ❌ **不要**把 DRIFT_THRESHOLD_ATR 调到 1.0 以下（会频繁触发 DEFEND，损失做市机会）
- ❌ **不要**给 sigmoid 加"分段函数"
- ❌ **不要**因为某个特殊行情单独调参
- ❌ **不要**在状态机外加"机器学习预测层"




---

## 12. 回测验证要点

### 12.1 验收 Gate（在 v1 基础上加强）

| Gate | 检验项 | 合格标准 | 说明 |
|------|-------|---------|------|
| G1 | 净 PnL（扣费后） | > 0 | 基础门槛 |
| G2 | Sharpe（日频） | > 1.2 | 比 v1 的 1.0 更严 |
| G3 | 最大回撤 | < 4% | 比 v1 的 5% 更严 |
| G4 | TRADE 状态时间占比 | 50%~80% | 太低=策略不工作，太高=没保护 |
| G5 | DEFEND 状态时间占比 | 15%~40% | 检验保护是否合理 |
| G6 | HALT 触发频率 | < 3 次/天 | 熔断阈值是否合理 |
| G7 | tick 级单笔止损触发频率 | < 10 次/天 | 是否过度敏感 |
| G8 | **中枢迁移期 PnL** | **不显著负** | **专项验证 v1 主要失败模式** |
| G9 | 状态切换频率 | < 20 次/天 | 滞回带是否生效 |
| G10 | 单笔平均持仓 | < 15 分钟 | 库存周转是否健康 |

### 12.2 中枢迁移期专项测试（修复 v1 问题 1 的关键验证）

**测试方法**：
1. 在历史数据中标注所有"中枢迁移日"（VWAP60min 单日变化 > 1.5×日内ATR）
2. 单独跑回测，只看这些日子的表现
3. **必须达到的标准**：v2 在中枢迁移日 PnL 不应显著负（< -50bps）
4. 对比 v1 在同样日子的表现（应该明显改善）

```python
def identify_drift_days(klines_30d):
    """识别中枢迁移日"""
    drift_days = []
    for day in split_by_day(klines_30d):
        vwap_open = compute_vwap_first_60min(day)
        vwap_close = compute_vwap_last_60min(day)
        atr_day = atr(day, 14)
        
        if abs(vwap_close - vwap_open) > 1.5 * atr_day:
            drift_days.append(day)
    return drift_days
```

### 12.3 鲁棒性测试（与 v1 一致）

- 参数扰动 ±20%
- 跨主流币测试（BTC/ETH/SOL）
- Walk-forward 6+ 次
- 极端日测试（含 LUNA、FTX 等）

### 12.4 需要警惕的"v2 特有的假阳性"

| 假阳性来源 | 警示信号 | 处理 |
|-----------|---------|------|
| **VWAP30 计算窗口太小** | warmup 期 VWAP 异常波动 | 数据 < 30 根 K 线时 fallback 到 SMA20 |
| **滞回带过宽** | 状态长期卡在 DEFEND | 监控 DEFEND 持续时长 |
| **单笔止损过紧** | tick 止损 > 20 次/天 | 调高 SINGLE_STOP_ATR 至 2.5 |
| **熔断过频** | HALT > 5 次/天 | 调高 JUMP_THRESHOLD_ATR 至 6.0 |




---

## 13. 落地路线（v1 → v2 迁移）

### 13.1 已有 v1 代码的迁移路径

| 模块 | v1 状态 | v2 改动 | 工作量 |
|------|--------|---------|-------|
| HMA 计算 | 保留 | 不变 | 0 |
| ATR 计算 | 保留 | 不变 | 0 |
| 双层带宽 | 保留参考 | 改为单一 STOP 阈值 | 小 |
| 状态机 | 重写 | 3态→3态但语义不同，加滞回 | 中 |
| 报价生成 | 重写 | 中心价偏置改基于 VWAP30 | 中 |
| 极端事件保护 | 拆分 | 拆为 L0-A/B/C 三层 | 中 |
| **新增** | — | VWAP30 计算 | 小 |
| **新增** | — | 漂移检测 | 小 |
| **新增** | — | tick 级单笔保护 | 中 |
| **新增** | — | sigmoid 库存平滑 | 小 |

### 13.2 开发阶段

| 周 | 任务 | 验收标准 |
|----|------|---------|
| W1 | VWAP30 + 漂移检测 + 状态机重构 | 单元测试覆盖所有切换路径 |
| W2 | 报价生成器 + sigmoid 库存 | 集成测试，对比 v1 输出差异 |
| W3 | tick 级单笔保护 + 熔断重构 | 模拟极端场景验证 |
| W4 | 回测框架（含中枢迁移日识别）| 跑通 30 天历史 |
| W5-6 | 参数调优 + 鲁棒性测试 | 通过 G1-G10 全部 Gate |
| W7-8 | Paper Trading | 信号一致性 > 90% |
| W9+ | 小资金实盘（$200 起） | 2 周 PnL > 0 |

### 13.3 实盘启动检查清单（v2 加强版）

```
□ VWAP30 在 warmup 期间正确 fallback
□ 漂移检测在 30 天历史数据上识别正确率 > 90%（人工标注对比）
□ tick 级止损在历史极端日（如 2022-11 FTX）正确触发
□ 状态机滞回带消除了大部分抖动（< 20 次/天切换）
□ 参数扰动 ±20% PnL 变化 < 30%
□ Walk-forward 6 次以上，样本外 Sharpe > 1.0
□ 中枢迁移日 PnL 不显著负
□ Paper Trade 2 周
□ 紧急停机机制测试
□ Telegram/Discord 告警接入
□ 资金规模初始 < $300，验证后再放量
```




---

## 14. 总结

### 14.1 v2 核心创新

| 创新点 | 解决的 v1 问题 | 价值 |
|--------|---------------|------|
| 双中枢漂移检测 | #1 HMA 滞后 | 中枢迁移期不再亏钱 |
| 简化状态机（无 WARN）| #2 逆势加仓 | 消除最大亏损源 |
| tick 级单笔止损 | #3 状态切换空窗 | 单笔最大亏损可控 |
| sigmoid 库存平滑 | #4 硬阈值跳变 | 行为连续 |
| 层数自动推导 | #5 拍脑袋6层 | 减少冗余挂单 |
| 报价基于 VWAP30 | #6 信号执行不同步 | 内在一致 |
| 适用性自检 | #7 跨市场幻觉 | 透明化 |
| 滞回带 | #8 边界跳变 | 平滑切换 |
| ATR 倍数熔断 | #9 阈值脱离市场 | 自适应 |
| 三层风控分工 | #11 责任模糊 | 优先级清晰 |

### 14.2 一句话回顾

> **v1 用慢均线（HMA）当中枢，被中枢迁移打败。v2 用快慢均线的相对位置（VWAP30 vs HMA200）检测中枢迁移，并用简化状态机消除"逆势加仓"陷阱。**

### 14.3 演进总览

```
v1.0 (自创 trend_score)       — 精巧但虚
  ↓
v1.1 (六层风控 + 多周期)        — 复杂且乱
  ↓
v1.0 HMA-Band                  — 简单但有致命漏洞
  ↓
v2.0 双中枢 + 简化状态机       — 当前版本
```

### 14.4 v2 的诚实评估

**优点（确实改善的）**：
- 核心矛盾已解决（中枢漂移检测）
- 最大亏损源已消除（无 WARN 状态）
- 风控更明确（三层分工）
- 参数更少（10 个）
- 内在一致性更高（信号和执行同步）

**缺点（不能解决的）**：
- 极端震荡市仍可能亏损
- 真趋势启动期必有 5-10 分钟的库存累积
- 反复假突破时仍会损耗

**主观成功概率**：
- v1 实盘成功概率：30-40%
- v2 实盘成功概率：**55-65%**

**这个数字必须通过严格回测验证**。

### 14.5 不再继续叠加补丁的承诺

v1 → v1.1 → v1.0 HMA-Band → v2 的演进过程已经足够。**v2 不会再有 v3**，除非：

1. 回测发现某个**根本性**问题（不是参数调整能解决的）
2. 实盘出现了设计中**完全没有预料到**的失败模式
3. 市场结构发生**重大变化**（如永续合约规则变更）

否则，任何后续改进都应该是**参数微调**，不是新增模块。

**牢记：复杂度是策略的敌人。**

---

*文档版本: v2.0*  
*创建日期: 2026-05-21*  
*基于：双中枢（VWAP30 + HMA200）+ 简化状态机（TRADE/DEFEND/HALT）+ 三层风控（tick/分钟/日）*  
*核心参数: 10 个*  
*v1 → v2 修复 11 个问题中的 10 个*
