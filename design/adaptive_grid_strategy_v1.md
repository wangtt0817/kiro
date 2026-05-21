# 趋势自适应网格做市策略 (Trend-Adaptive Grid Market Making)

## 策略设计文档 v1.0

> **核心理念**：网格间距不是固定值，也不是"出了问题才调整"的被动参数——它是波动率和趋势强度的**实时连续函数**。策略在每个报价周期都根据市场状态重新计算最优间距，从而在趋势初期就减少逆向选择损失，而不是等库存堆积后才制动。

---

## 1. 策略哲学：与库存驱动策略的根本区别

| 维度 | 库存驱动（VAR-Grid-CV） | 本策略（趋势自适应） |
|------|------|------|
| 核心输入 | 库存状态（事后指标） | 波动率 + 趋势强度（事前指标） |
| 间距调整时机 | 库存超限后才调整 | 每个报价周期实时调整 |
| 对趋势的态度 | 异常状态，需要防御 | 市场信息，需要利用 |
| 反应链条 | 趋势→成交→库存堆积→触发制动→调整 | 趋势→检测→立即调整间距 |
| 延迟代价 | 10-20秒（致命） | 1-2秒（可接受） |

**一句话**：本策略的间距是市场的"体温计"，不是"止痛药"。

---

## 2. 核心架构：三引擎 + 一执行层


```
┌─────────────────────────────────────────────────────────────────────┐
│              趋势自适应网格做市策略 (Trend-Adaptive Grid)              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │  引擎 A：波动率引擎 (Volatility Engine)                     │     │
│  │  输入：1s mid-price 序列                                    │     │
│  │  输出：σ_fast (5s), σ_mid (60s), σ_slow (1h)              │     │
│  │  作用：决定网格间距的基础宽度                                │     │
│  └─────────────────────────┬─────────────────────────────────┘     │
│                            │                                        │
│  ┌─────────────────────────▼─────────────────────────────────┐     │
│  │  引擎 B：趋势引擎 (Trend Engine)                            │     │
│  │  输入：多时间尺度收益率 + OBI + 成交流                       │     │
│  │  输出：trend_score ∈ [-1, +1], trend_confidence             │     │
│  │  作用：决定网格的非对称偏移和层数分配                        │     │
│  └─────────────────────────┬─────────────────────────────────┘     │
│                            │                                        │
│  ┌─────────────────────────▼─────────────────────────────────┐     │
│  │  引擎 C：库存引擎 (Inventory Engine)                        │     │
│  │  输入：当前净仓位 + 持仓成本 + 权益                          │     │
│  │  输出：inventory_pressure ∈ [-1, +1]                        │     │
│  │  作用：叠加额外的非对称偏移（辅助，非主驱动）                │     │
│  └─────────────────────────┬─────────────────────────────────┘     │
│                            │                                        │
│  ┌─────────────────────────▼─────────────────────────────────┐     │
│  │  执行层：自适应报价生成器 (Adaptive Quote Generator)          │     │
│  │  合成三引擎输出 → 生成非对称网格挂单序列                     │     │
│  │  输出：[bid_1..bid_n] + [ask_1..ask_m]，n ≠ m 是常态       │     │
│  └───────────────────────────────────────────────────────────┘     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 引擎 A：波动率引擎（决定"间距多宽"）

### 3.1 设计原则

- 波动率高 → 间距宽（避免被趋势吃穿）
- 波动率低 → 间距窄（抓住小幅震荡的价差利润）
- 连续变化，没有跳跃式"模板切换"

### 3.2 三级 σ 计算

| 级别 | 半衰期 | 用途 | 更新频率 |
|------|--------|------|----------|
| σ_fast | 5 秒 | 跳跃检测 + 即时间距微调 | 每 tick |
| σ_mid | 60 秒 | 网格间距的主驱动 | 每秒 |
| σ_slow | 1 小时 | 归一化锚点（"正常"基线） | 每分钟 |

**计算方法**：EWMA 对 1s 对数收益率的平方


```python
# 伪代码
def update_sigma(new_return_bps):
    """每收到1s桶收益率时调用"""
    r2 = new_return_bps ** 2
    
    # EWMA 更新（α = 1 - exp(-1/halflife_seconds)）
    σ_fast_var = α_fast * r2 + (1 - α_fast) * σ_fast_var   # α_fast ≈ 0.13 (半衰期5s)
    σ_mid_var  = α_mid  * r2 + (1 - α_mid)  * σ_mid_var    # α_mid  ≈ 0.016 (半衰期60s)
    σ_slow_var = α_slow * r2 + (1 - α_slow) * σ_slow_var   # α_slow ≈ 0.00028 (半衰期1h)
    
    σ_fast = sqrt(σ_fast_var)  # 单位: bps/√s
    σ_mid  = sqrt(σ_mid_var)
    σ_slow = sqrt(σ_slow_var)
```

### 3.3 波动率状态指标

```
z_vol = σ_fast / σ_slow    # 当前波动率相对于"正常"的倍数
```

| z_vol 区间 | 市场状态 | 间距策略 |
|------------|----------|----------|
| 0.3 ~ 0.8 | 极度安静 | 间距压缩到下限（Fee×2 + buffer） |
| 0.8 ~ 1.5 | 正常 | 间距 = 标准公式 |
| 1.5 ~ 3.0 | 波动抬升 | 间距线性放大 |
| 3.0 ~ 6.0 | 高波动 | 间距加速放大 + 减少层数 |
| > 6.0 | 极端/跳跃 | 仅保留远端防守单 |

---

## 4. 引擎 B：趋势引擎（决定"往哪偏"）

### 4.1 设计原则

- 输出连续的 `trend_score ∈ [-1, +1]`，不是离散的"震荡/趋势/跳跃"
- 负值 = 看空趋势，正值 = 看多趋势，0 = 无方向
- 用多指标融合，避免单一信号误判

### 4.2 趋势评分计算

```python
def compute_trend_score():
    """每个报价周期（1-2秒）调用"""
    
    # 信号1: 多时间尺度价格动量（权重 40%）
    mom_10s = (mid_now - mid_10s_ago) / (σ_mid * sqrt(10))   # 标准化
    mom_60s = (mid_now - mid_60s_ago) / (σ_mid * sqrt(60))
    mom_300s = (mid_now - mid_300s_ago) / (σ_mid * sqrt(300))
    
    # 三尺度同向 → 强趋势；不同向 → 削弱
    if sign(mom_10s) == sign(mom_60s) == sign(mom_300s):
        momentum_signal = 0.5 * mom_10s + 0.3 * mom_60s + 0.2 * mom_300s
    else:
        momentum_signal = 0.3 * mom_10s  # 冲突时只信最短尺度，且权重降低
    
    # 信号2: 订单簿不平衡 OBI（权重 30%）
    obi = (bid_volume_top10 - ask_volume_top10) / (bid_volume_top10 + ask_volume_top10)
    obi_signal = obi  # 已经在[-1,+1]范围
    
    # 信号3: 主动成交方向（权重 30%）
    buy_volume_30s = sum(trades where aggressor=buy in last 30s)
    sell_volume_30s = sum(trades where aggressor=sell in last 30s)
    trade_flow = (buy_volume_30s - sell_volume_30s) / (buy_volume_30s + sell_volume_30s + epsilon)
    
    # 融合
    raw_score = 0.40 * tanh(momentum_signal) + \
                0.30 * tanh(obi_signal * 2) + \
                0.30 * tanh(trade_flow * 2)
    
    # EWMA 平滑（防止单tick噪音，半衰期3秒）
    trend_score = α_trend * raw_score + (1 - α_trend) * trend_score_prev
    
    # 置信度 = 三个信号的一致程度
    signs = [sign(momentum_signal), sign(obi_signal), sign(trade_flow)]
    trend_confidence = count(s == sign(trend_score) for s in signs) / 3.0
    
    return trend_score, trend_confidence  # score∈[-1,+1], confidence∈[0.33,1.0]
```


### 4.3 趋势评分的直觉解释

| trend_score | 含义 | 策略行为 |
|-------------|------|----------|
| -1.0 ~ -0.7 | 强看空 | 大幅压缩买单、拉远买单间距、增加卖单密度 |
| -0.7 ~ -0.3 | 中等看空 | 买单减少2-3层，间距适度拉宽 |
| -0.3 ~ +0.3 | 中性/震荡 | 对称双边正常做市 |
| +0.3 ~ +0.7 | 中等看多 | 卖单减少2-3层，间距适度拉宽 |
| +0.7 ~ +1.0 | 强看多 | 大幅压缩卖单、拉远卖单间距、增加买单密度 |

---

## 5. 引擎 C：库存引擎（辅助偏移）

### 5.1 定位

库存引擎**不是**主驱动，而是对趋势引擎输出的**叠加修正**。

- 趋势引擎告诉你"市场往哪走" → 决定报价中心的偏移
- 库存引擎告诉你"你现在需要往哪清" → 叠加额外的去库存压力

### 5.2 计算

```python
def compute_inventory_pressure():
    """
    inventory_ratio = 净仓位 / 账户权益  ∈ [-1, +1] 归一化
    正值 = 多头库存过重（需要卖出去库存）
    负值 = 空头库存过重（需要买入去库存）
    """
    inventory_ratio = net_position_value / account_equity
    normalized = inventory_ratio / INVENTORY_HARD_LIMIT  # 归一化到[-1,+1]
    
    # 非线性放大：库存越接近硬限，压力指数级增加
    inventory_pressure = sign(normalized) * (abs(normalized) ** 1.5)
    
    return clamp(inventory_pressure, -1.0, +1.0)
```

### 5.3 库存引擎与趋势引擎的关系

| 场景 | trend_score | inventory_pressure | 合成行为 |
|------|-------------|-------------------|----------|
| 趋势做多 + 多头库存轻 | +0.6 | +0.1 | 正常偏多，不需要特别去库存 |
| 趋势做多 + 多头库存重 | +0.6 | +0.7 | 偏多但叠加去库存，卖单更激进 |
| 趋势做空 + 多头库存重 | -0.5 | +0.7 | **两者同向**，强力去多头库存 |
| 震荡 + 库存中性 | 0.0 | 0.0 | 完美对称做市 |
| 震荡 + 多头库存重 | 0.0 | +0.7 | 中心价下移，激励卖出 |

---

## 6. 执行层：自适应报价生成器（核心公式）

### 6.1 报价周期

每 **1-2 秒** 重新生成一次完整的挂单序列。

### 6.2 中心价计算

```python
def compute_center_price():
    mid = (best_bid + best_ask) / 2
    
    # AS 偏移（趋势 + 库存联合驱动）
    combined_pressure = (
        W_TREND * trend_score +          # 权重建议 0.6
        W_INVENTORY * inventory_pressure  # 权重建议 0.4
    )
    
    # 偏移量 = 压力 × 最大偏移幅度
    max_shift_bps = K_SHIFT * σ_mid * sqrt(QUOTE_LIFETIME_SEC)
    # K_SHIFT 建议 1.5-2.5
    # QUOTE_LIFETIME_SEC = 报价的预期存活时间，建议 30-60s
    
    shift_bps = combined_pressure * max_shift_bps
    shift_bps = clamp(shift_bps, -MAX_SHIFT_CAP_BPS, +MAX_SHIFT_CAP_BPS)
    
    center_price = mid * (1 - shift_bps / 10000)
    # 正pressure → center下移 → 激励卖出（去多头库存）
    # 负pressure → center上移 → 激励买入（去空头库存）
    
    return center_price
```


### 6.3 网格间距计算（核心自适应公式）

```python
def compute_grid_interval():
    """
    间距 = 保证盈利的最小值 与 波动率驱动值 取较大者
    """
    # 下限：保证每笔成交覆盖成本
    min_interval = FEE_BPS * 2 + SLIPPAGE_BUFFER_BPS + TICK_FLOOR_BPS
    
    # 波动率驱动间距
    # 物理含义：在报价存活期内，价格有68%概率移动的距离
    vol_interval = K_GRID * σ_mid * sqrt(QUOTE_LIFETIME_SEC)
    # K_GRID 建议 1.0-1.5
    
    # 趋势放大因子：趋势越强，间距越宽
    trend_multiplier = 1.0 + TREND_WIDEN_COEFF * abs(trend_score) ** 2
    # TREND_WIDEN_COEFF 建议 1.0-2.0
    # 平方关系：弱趋势几乎不影响，强趋势才大幅拉宽
    
    # 波动率放大因子：高波动时额外加宽
    vol_multiplier = max(1.0, (z_vol / 1.5) ** 1.5)
    # z_vol < 1.5 时不加宽，> 1.5 时加速加宽
    
    base_interval = max(min_interval, vol_interval)
    final_interval = base_interval * trend_multiplier * vol_multiplier
    
    return final_interval
```

### 6.4 非对称层数分配（关键创新）

```python
def compute_layer_allocation():
    """
    趋势方向的逆势侧（容易被吃的那侧）减少层数
    趋势方向的顺势侧（需要去库存的那侧）增加层数
    """
    total_layers = BASE_GRID_ORDERS  # 比如 8 层总预算
    
    # trend_score > 0 → 看多 → 卖单是逆势侧（容易被吃） → 卖单减少
    # trend_score < 0 → 看空 → 买单是逆势侧（容易被吃） → 买单减少
    
    # 非对称分配系数
    asymmetry = trend_score * ASYMMETRY_COEFF  # ASYMMETRY_COEFF 建议 0.4-0.6
    # asymmetry ∈ [-0.6, +0.6]
    
    bid_layers = round(total_layers * (0.5 - asymmetry * 0.5))
    ask_layers = round(total_layers * (0.5 + asymmetry * 0.5))
    
    # 确保每侧至少1层
    bid_layers = max(1, min(total_layers - 1, bid_layers))
    ask_layers = max(1, min(total_layers - 1, ask_layers))
    
    return bid_layers, ask_layers

# 示例：
# trend_score = +0.8 (强看多), ASYMMETRY_COEFF = 0.5
# asymmetry = 0.4
# bid_layers = round(8 * 0.3) = 2  (买单只留2层——不怕被吃)
# ask_layers = round(8 * 0.7) = 6  (卖单6层——积极去多头库存)
```

### 6.5 非对称间距（逆势侧加宽）

```python
def compute_asymmetric_intervals(base_interval, trend_score):
    """
    逆势侧间距更宽（被吃了也亏得少）
    顺势侧间距更窄（更容易成交去库存）
    """
    # 逆势侧加宽因子
    adverse_widen = 1.0 + ADVERSE_WIDEN_COEFF * abs(trend_score)
    # ADVERSE_WIDEN_COEFF 建议 0.5-1.5
    
    # 顺势侧收窄因子（但不能低于成本下限）
    favorable_narrow = max(0.6, 1.0 - FAVORABLE_NARROW_COEFF * abs(trend_score))
    # FAVORABLE_NARROW_COEFF 建议 0.2-0.4
    
    if trend_score > 0:  # 看多
        bid_interval = base_interval * adverse_widen    # 买单宽（逆势）
        ask_interval = base_interval * favorable_narrow # 卖单窄（顺势去库存）
    else:  # 看空
        bid_interval = base_interval * favorable_narrow # 买单窄（顺势去库存）
        ask_interval = base_interval * adverse_widen    # 卖单宽（逆势）
    
    # 硬下限保护
    bid_interval = max(bid_interval, MIN_INTERVAL_BPS)
    ask_interval = max(ask_interval, MIN_INTERVAL_BPS)
    
    return bid_interval, ask_interval
```


### 6.6 最终报价序列生成

```python
def generate_quotes():
    """每1-2秒调用一次，生成完整的挂单序列"""
    
    # Step 1: 计算三引擎输出
    σ_fast, σ_mid, σ_slow = volatility_engine.update()
    trend_score, trend_confidence = trend_engine.compute()
    inventory_pressure = inventory_engine.compute()
    
    # Step 2: 置信度门控 —— 低置信度时趋势信号衰减
    effective_trend = trend_score * trend_confidence
    
    # Step 3: 计算报价参数
    center = compute_center_price(effective_trend, inventory_pressure)
    base_interval = compute_grid_interval()
    bid_layers, ask_layers = compute_layer_allocation()
    bid_interval, ask_interval = compute_asymmetric_intervals(base_interval, effective_trend)
    
    # Step 4: 生成买单序列
    bids = []
    for i in range(bid_layers):
        price = center * (1 - (i + 1) * bid_interval / 10000)
        size = compute_order_size(i, 'bid', inventory_pressure)
        bids.append(Order(side='buy', price=price, size=size))
    
    # Step 5: 生成卖单序列
    asks = []
    for i in range(ask_layers):
        price = center * (1 + (i + 1) * ask_interval / 10000)
        size = compute_order_size(i, 'ask', inventory_pressure)
        asks.append(Order(side='sell', price=price, size=size))
    
    # Step 6: 极端保护 —— z_vol > 6 时只保留最远2层
    if z_vol > 6.0:
        bids = bids[-1:]  # 只保留最远一层
        asks = asks[-1:]
    
    return bids, asks


def compute_order_size(layer_index, side, inventory_pressure):
    """
    近端小单，远端大单（被吃了亏少，捡到了赚多）
    库存压力方向的去库存侧，size 放大
    """
    base_size = BASE_ORDER_SIZE
    
    # 远端递增（第i层 size = base × (1 + 0.2*i)）
    layer_factor = 1.0 + 0.2 * layer_index
    
    # 去库存侧放大
    if (side == 'sell' and inventory_pressure > 0) or \
       (side == 'buy' and inventory_pressure < 0):
        inventory_factor = 1.0 + 0.5 * abs(inventory_pressure)
    else:
        inventory_factor = 1.0
    
    return base_size * layer_factor * inventory_factor
```

---

## 7. 撤补单策略：惰性更新（减少链上成本）

### 7.1 问题

如果每1-2秒重新生成报价，是否每次都要全部撤单再挂？在链上环境下这太贵了。

### 7.2 解决方案：阈值触发的惰性更新

```python
def should_requote(old_orders, new_orders):
    """只有当新旧报价差异超过阈值时才执行撤补"""
    
    for old, new in zip(old_orders, new_orders):
        price_drift = abs(old.price - new.price) / old.price * 10000  # bps
        
        # 价格偏移超过半个间距 → 必须更新
        if price_drift > current_interval * 0.5:
            return True
    
    # 层数变化 → 必须更新
    if len(old_orders) != len(new_orders):
        return True
    
    # 中心价大幅偏移 → 必须更新
    if abs(new_center - old_center) / mid * 10000 > current_interval * 0.3:
        return True
    
    return False  # 差异小，保持不动
```

### 7.3 分层更新优先级

| 优先级 | 条件 | 动作 |
|--------|------|------|
| P0 | z_vol > 6（极端波动） | 立即撤所有近端，只留远端防守 |
| P1 | trend_score 方向翻转 | 撤逆势侧近端，补顺势侧 |
| P2 | 价格偏移 > 0.5×间距 | 更新偏移最大的那一侧 |
| P3 | 间距变化 > 20% | 全量重挂 |
| P4 | 正常微调 | 不动（等自然成交） |

---

## 8. 跳跃保护（快速熔断）

### 8.1 跳跃检测

```python
def detect_jump(latest_return_bps):
    """每tick检测"""
    z_tick = abs(latest_return_bps) / (σ_fast * sqrt(dt_seconds))
    
    if z_tick > JUMP_THRESHOLD:  # 建议 5-8
        return True
    return False
```


### 8.2 跳跃应对

```python
def on_jump_detected():
    """跳跃检测触发后立即执行"""
    
    # 1. 立即撤所有近端挂单（不等确认就标记为"待撤"）
    cancel_all_within(distance_bps=GRID_INTERVAL * 3)
    
    # 2. 进入冷却期
    cooldown_until = now + JUMP_COOLDOWN_SEC  # 建议 10-30s
    
    # 3. 冷却期内只允许：
    #    - 远端防守单（距中心 > 5×间距）
    #    - 去库存方向的中远端单
    
    # 4. 冷却期结束后：
    #    - 重新评估 σ_fast, σ_mid
    #    - 如果 z_vol 仍然 > 3 → 继续宽间距模式
    #    - 如果 z_vol 恢复 < 2 → 正常恢复
```

---

## 9. 完整参数表

### 9.1 锚参数（需要人工设定，共6个）

| 参数 | 含义 | 建议值 | 来源 |
|------|------|--------|------|
| `FEE_BPS` | 单边手续费 | 2-5 | 交易所费率表 |
| `SLIPPAGE_BUFFER_BPS` | 滑点缓冲 | 1-3 | 历史成交分析 |
| `TICK_FLOOR_BPS` | 最小tick精度 | 1-3 | 合约规格 |
| `BASE_GRID_ORDERS` | 总层数预算 | 6-10 | 资金规模 |
| `BASE_ORDER_SIZE` | 单层基础下单量 | 按权益1-3% | 风险偏好 |
| `INVENTORY_HARD_LIMIT` | 库存硬限（占权益比） | 20-30% | 风控底线 |

### 9.2 衍生参数（从 σ 自动计算）

| 参数 | 公式 | 说明 |
|------|------|------|
| `GRID_INTERVAL` | `max(Fee×2+Slip, K_GRID×σ_mid×√T_quote)` | 基础间距 |
| `MAX_SHIFT_CAP_BPS` | `2.5 × σ_mid × √T_quote` | 中心价最大偏移 |
| `JUMP_THRESHOLD` | `6.0`（固定） | z_tick 跳跃阈值 |
| `QUOTE_LIFETIME_SEC` | `30-60` | 报价预期存活时间 |

### 9.3 行为系数（可通过回测优化）

| 参数 | 建议值 | 作用 |
|------|--------|------|
| `K_GRID` | 1.0-1.5 | 间距对σ的敏感度 |
| `K_SHIFT` | 1.5-2.5 | 中心价偏移对σ的敏感度 |
| `W_TREND` | 0.6 | 趋势引擎在中心价偏移中的权重 |
| `W_INVENTORY` | 0.4 | 库存引擎在中心价偏移中的权重 |
| `TREND_WIDEN_COEFF` | 1.0-2.0 | 趋势对间距的放大系数 |
| `ASYMMETRY_COEFF` | 0.4-0.6 | 层数非对称分配的激进度 |
| `ADVERSE_WIDEN_COEFF` | 0.5-1.5 | 逆势侧额外加宽系数 |
| `FAVORABLE_NARROW_COEFF` | 0.2-0.4 | 顺势侧收窄系数 |
| `JUMP_COOLDOWN_SEC` | 10-30 | 跳跃后冷却秒数 |

---

## 10. 盈利模型与盈亏边界

### 10.1 单笔期望利润

```
E[profit_per_fill] = interval_bps - 2×Fee - Slippage - Adverse_Selection_Cost

其中 Adverse_Selection_Cost ≈ σ_mid × √(time_to_fill) × adverse_ratio
adverse_ratio ∈ [0.3, 0.7]  取决于趋势强度
```

### 10.2 本策略如何降低 Adverse Selection Cost

| 机制 | 如何降低逆向选择 |
|------|-----------------|
| 间距随σ自动加宽 | 被吃时价格已经充分反映了波动 |
| 逆势侧额外加宽 | 趋势方向被吃的那侧间距更宽 |
| 逆势侧减少层数 | 减少了被逆向选择"咬到"的概率 |
| 中心价顺势偏移 | 逆势侧的第一层已经离当前价较远 |
| 跳跃熔断 | 极端行情下直接撤近端 |

### 10.3 盈亏边界估算

```
策略盈利条件（简化）：

成交频率 × 每笔净利润 > 固定成本（gas、维护）

等价于：
  interval > 2×Fee + Slip + σ_mid × √T_fill × adverse_ratio

当 σ_mid 过高（z_vol > 4-5）时：
  即使加宽间距，成交频率也会极低 → 无法覆盖固定成本
  此时策略应该停止做市
  
当趋势过强（|trend_score| > 0.85 持续 > 5分钟）时：
  逆向选择成本超过间距利润 → 策略应大幅减少逆势侧或暂停
```

### 10.4 自动暂停条件

```python
def should_pause_market_making():
    """每分钟检查"""
    # 条件1: 波动率过高持续太久
    if z_vol > 5.0 and z_vol_duration > 120:  # 2分钟
        return True, "极端波动持续过久"
    
    # 条件2: 趋势过强持续太久
    if abs(trend_score) > 0.85 and trend_duration > 300:  # 5分钟
        return True, "强趋势持续过久，逆向选择成本不可控"
    
    # 条件3: 库存硬限持续太久
    if abs(inventory_pressure) > 0.9 and inventory_duration > 600:  # 10分钟
        return True, "库存无法去化，市场不适合做市"
    
    # 条件4: 过去1小时盈亏
    if pnl_last_hour_bps < -RISK_BUDGET_HOURLY:
        return True, "小时亏损超限"
    
    return False, ""
```

---

## 11. 完整执行循环（伪代码）

```python
class TrendAdaptiveGridStrategy:
    
    def __init__(self, config):
        self.vol_engine = VolatilityEngine(config)
        self.trend_engine = TrendEngine(config)
        self.inventory_engine = InventoryEngine(config)
        self.quote_generator = AdaptiveQuoteGenerator(config)
        self.order_manager = OrderManager(config)
        
        self.state = 'WARMUP'  # WARMUP → ACTIVE → PAUSED
        self.warmup_ticks = 0
    
    async def on_tick(self, market_data):
        """每收到一次market data更新时调用（约每秒1-2次）"""
        
        # === Phase 0: 更新波动率（每tick都做） ===
        self.vol_engine.update(market_data.mid_price)
        
        # === Phase 1: 冷启动检查 ===
        self.warmup_ticks += 1
        if self.state == 'WARMUP':
            if self.warmup_ticks < 120:  # 至少2分钟
                return
            if not self.vol_engine.is_converged():
                return
            self.state = 'ACTIVE'
        
        # === Phase 2: 跳跃检测（最高优先级） ===
        if self.vol_engine.detect_jump(market_data):
            self.on_jump()
            return
        
        # === Phase 3: 暂停检查 ===
        should_pause, reason = self.should_pause_market_making()
        if should_pause:
            self.state = 'PAUSED'
            self.order_manager.cancel_all()
            log.warning(f"策略暂停: {reason}")
            return
        
        # === Phase 4: 计算三引擎输出 ===
        σ_fast, σ_mid, σ_slow, z_vol = self.vol_engine.get_state()
        trend_score, trend_confidence = self.trend_engine.compute(market_data)
        inventory_pressure = self.inventory_engine.compute()
        
        # === Phase 5: 生成新报价 ===
        new_bids, new_asks = self.quote_generator.generate(
            mid=market_data.mid_price,
            σ_mid=σ_mid,
            z_vol=z_vol,
            trend_score=trend_score * trend_confidence,
            inventory_pressure=inventory_pressure
        )
        
        # === Phase 6: 惰性更新（只有变化超阈值才撤补） ===
        if self.order_manager.should_requote(new_bids, new_asks):
            await self.order_manager.update_orders(new_bids, new_asks)
    
    def on_jump(self):
        """跳跃事件处理"""
        self.order_manager.cancel_near_orders()
        self.cooldown_until = time.now() + JUMP_COOLDOWN_SEC
    
    async def on_fill(self, fill_event):
        """成交回调"""
        # 更新库存
        self.inventory_engine.on_fill(fill_event)
        
        # 成交后立即重新评估（不等下一个tick）
        # 因为成交本身改变了库存状态
        self.force_requote_next_tick = True
```


---

## 12. 与 VAR-Grid-CV 的关键对比总结

| 维度 | VAR-Grid-CV (库存驱动) | 本策略 (趋势自适应) |
|------|------|------|
| **间距是什么** | 固定值，出问题后按模板切换 | σ × √T 的实时连续函数 |
| **趋势怎么影响间距** | 间接：趋势→库存堆积→触发制动→切模板 | 直接：trend_score→间距公式→即时生效 |
| **反应延迟** | 10-20秒（等库存确认） | 1-2秒（下一个报价周期） |
| **状态机** | 4个离散状态（震荡/弱趋势/强趋势/跳跃） | 连续输出（trend_score + z_vol） |
| **模板切换抖动** | 容易在边界反复切换 | 没有切换——连续变化，天然平滑 |
| **逆向选择应对** | 事后（库存堆积后才制动） | 事前（趋势信号直接拉宽逆势侧） |
| **中心价偏移** | 纯库存驱动（AS模型） | 趋势×0.6 + 库存×0.4 联合驱动 |
| **极端保护** | 跳跃态模板（离散切换） | 连续退化：z_vol从1→6渐进减少层数 |
| **复杂度** | 高（四层×多模板×状态机×规则表） | 中（三引擎×一组公式×惰性更新） |
| **可回测性** | 差（大量离散规则难以穷举） | 好（全部连续函数，参数可网格搜索） |

---

## 13. 为什么这个策略更容易赚钱

### 13.1 解决了负收益的三个根本原因

| 负收益原因 | 本策略如何解决 |
|-----------|--------------|
| 趋势初期大量逆向成交 | trend_score 在1-2秒内就拉宽逆势侧+减少逆势层数 |
| 间距太窄覆盖不了成本 | 间距下限 = Fee×2 + Slippage，数学保证每笔正期望 |
| 状态切换延迟太大 | 没有离散状态切换，连续函数无延迟 |

### 13.2 保留了做市的核心优势

| 优势 | 如何保留 |
|------|---------|
| 震荡期高频赚价差 | z_vol低+trend中性时 → 窄间距对称做市 |
| 库存自然对冲 | 双边挂单，震荡市中买卖自然配对 |
| 低风险稳定收入 | 间距保底+层数限制+硬限保护 |

---

## 14. 回测验证建议

### 14.1 数据需求

| 数据 | 最低要求 | 理想要求 |
|------|----------|----------|
| L2 订单簿快照 | 每5秒，Top5 | 每1秒，Top20 |
| 成交流 | aggressor标记 | aggressor+size+时间戳 |
| BBO | 每秒 | 每tick |
| 时间跨度 | 30天 | 90天+ |

### 14.2 成交建模（避免回测幻觉）

```python
def simulate_fill(order, market_data):
    """保守成交模型"""
    
    # 规则1: 价格必须穿越挂单价（不是触碰）
    if order.side == 'buy':
        filled = market_data.low < order.price  # 必须低于（不是等于）
    else:
        filled = market_data.high > order.price
    
    # 规则2: 成交延迟
    fill_delay = sample_from_batch_distribution()  # 从历史batch间隔采样
    
    # 规则3: 部分成交概率
    if filled:
        # 离当前价越远，成交概率越低
        distance_bps = abs(order.price - market_data.mid) / market_data.mid * 10000
        fill_probability = exp(-distance_bps / (2 * current_interval))
        filled = random() < fill_probability
    
    return filled, fill_delay
```

### 14.3 关键指标

| 指标 | 合格标准 |
|------|----------|
| 净 PnL（扣费后） | > 0（首先不亏！） |
| Sharpe（日频） | > 1.5 |
| 最大回撤 | < 日均利润 × 10 |
| 双边对冲率 | > 60% |
| 逆向选择比率 | < 40%（被趋势吃的成交占比） |
| 平均持仓时间 | < 5分钟 |

---

## 15. 落地开发路线

| 阶段 | 内容 | 周期 |
|------|------|------|
| **Week 1** | 波动率引擎 + 趋势引擎（纯信号计算，无交易） | 验证信号质量 |
| **Week 2** | 报价生成器 + 历史回测框架 | 验证参数 |
| **Week 3** | 惰性更新 + 链上延迟建模 | 验证执行可行性 |
| **Week 4** | Paper Trading（接真实行情，模拟下单） | 验证实盘信号 |
| **Week 5+** | 小资金实盘 | 逐步放量 |

---

*本文档定义了趋势自适应网格做市策略的完整核心逻辑。与库存驱动策略的根本区别在于：所有参数都是市场状态的连续函数，不存在离散的"模板切换"，从而消除了状态切换延迟和边界抖动问题。*
