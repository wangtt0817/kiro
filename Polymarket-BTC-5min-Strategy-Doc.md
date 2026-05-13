# Polymarket BTC 5-Minute Trading Strategy
# 开发设计文档 v1.0

---

## 目录

1. [项目概述](#1-项目概述)
2. [市场机制分析](#2-市场机制分析)
3. [策略架构总览](#3-策略架构总览)
4. [Layer 1: 统计定价模型](#4-layer-1-统计定价模型)
5. [Layer 2: 实时确认信号](#5-layer-2-实时确认信号)
6. [Layer 3: 时机选择与执行](#6-layer-3-时机选择与执行)
7. [Layer 4: 风控体系](#7-layer-4-风控体系)
8. [数据需求与基建](#8-数据需求与基建)
9. [开发阶段规划](#9-开发阶段规划)
10. [关键风险与缓解](#10-关键风险与缓解)
11. [成功指标](#11-成功指标)
12. [参考资源](#12-参考资源)

---

## 1. 项目概述

### 1.1 目标

构建一个针对 Polymarket BTC 5 分钟 Up/Down 预测市场的自动化交易系统。

### 1.2 核心思路

融合三大策略流派的优势：

| 流派 | 借鉴内容 | 在本系统中的角色 |
|------|----------|------------------|
| **策略 E (校准偏差)** | 统计模型判断市场定价是否偏离真实概率 | Layer 1: 基本面判断 |
| **策略 B (多信号融合)** | OrderBook/Velocity/Divergence 等实时信号 | Layer 2: 方向确认 |
| **策略 D (末秒确定性)** | 利用时间衰减提高确定性，优先 maker order | Layer 3: 时机执行 |

### 1.3 设计哲学

- **不追求纯速度竞争**：不和 Oracle Lag bot 抢 100ms
- **不赌单一信号**：多维度交叉验证后才行动
- **时间是朋友**：越接近到期，方向越确定
- **小仓位高频率**：$1~5/单，依靠大数定律

---

## 2. 市场机制分析

### 2.1 Polymarket BTC 5-Min 市场规则

| 属性 | 详情 |
|------|------|
| 资产 | BTC/USD |
| 窗口 | 5 分钟（每 5 分钟开一个新 market） |
| 结算条件 | 5min 结束时 BTC 价格 >= 开始价 → "Up" 赢；< 开始价 → "Down" 赢 |
| Oracle | Chainlink (链上价格喂价，更新延迟 ~3-10s) |
| 交易标的 | Up token + Down token，价格 0~1 USDC |
| 订单簿 | CLOB (Central Limit Order Book)，非 AMM |
| 手续费 | Maker: 0%；Taker: 视活动而定（当前通常 0%） |
| 最小单位 | $0.01 (1 cent) |

### 2.2 关键时间节点

```
t=0          t=60s        t=180s       t=240s       t=270s    t=300s
|            |            |            |            |         |
市场开盘    早期信号     中期信号     信号成熟    末秒窗口   结算
(50/50)    (噪音大)    (方向显现)   (确定性60-70%) (85%+)
```

### 2.3 已知市场特征（来自 paucase4 经验研究）

- 中间概率区域（0.40~0.60）存在系统性定价偏差
- Brier Score 在到期前 2~3 分钟最差（=最有套利空间）
- 最后 30 秒市场效率极高（边际在 1~2%）
- 高波动率时段（美东开盘、CPI 发布等）偏差更大

---

## 3. 策略架构总览

### 3.1 四层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    POLYMARKET BTC 5-MIN BOT                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Layer 1: 统计定价模型 (Fair Value Engine)                │   │
│  │  输入: t_remaining, delta_price, sigma                   │   │
│  │  输出: P_fair(Up), 偏差大小 & 方向                       │   │
│  └───────────────────────┬─────────────────────────────────┘   │
│                          │ 偏差 > 阈值?                         │
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Layer 2: 实时确认信号 (Confirmation Signals)             │   │
│  │  信号A: OrderBook Imbalance                              │   │
│  │  信号B: Tick Velocity (Polymarket 自身动量)               │   │
│  │  信号C: CEX Spot Momentum (Binance/Coinbase)             │   │
│  │  输出: 综合确认分数 (0~100)                              │   │
│  └───────────────────────┬─────────────────────────────────┘   │
│                          │ 确认分数 > 阈值?                     │
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Layer 3: 时机选择与执行 (Timing & Execution)             │   │
│  │  决策: 现在下单 or 等到更晚时机？                         │   │
│  │  方式: Maker order (优先) vs Taker order (紧急)          │   │
│  │  价格: 基于 fair value 计算挂单价                        │   │
│  └───────────────────────┬─────────────────────────────────┘   │
│                          │                                      │
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Layer 4: 风控 (Risk Management)                          │   │
│  │  仓位控制 / 日亏损上限 / 连亏熔断 / 波动率自适应         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 决策流程（单个 5 分钟窗口内）

```
每秒循环:
  1. 更新 BTC 现货价格 (Binance WS)
  2. 更新 Polymarket 订单簿 & mid price
  3. 计算 t_remaining (距结算剩余秒数)
  4. 计算 delta_price = (BTC_now - BTC_open) / BTC_open
  5. 计算 realized_vol (最近 N 个窗口的 vol)
  
  6. Layer 1: P_fair = StatModel(t_remaining, delta_price, sigma)
  7. 偏差 = P_market - P_fair
  8. IF |偏差| < MIN_EDGE → 不交易，继续循环
  
  9. Layer 2: 确认信号
     - orderbook_score = check_orderbook_imbalance()
     - velocity_score = check_tick_velocity()
     - cex_score = check_cex_momentum()
     - confirmation = weighted_avg(scores)
  10. IF confirmation 方向 ≠ Layer1 方向 → 不交易
  11. IF confirmation < MIN_CONFIRMATION → 不交易
  
  12. Layer 3: 执行决策
     - IF t_remaining > 180s → 等待（太早，噪音大）
     - IF t_remaining < 120s AND signal_strong → Maker Order
     - IF t_remaining < 30s AND signal_very_strong → Taker Order
  
  13. Layer 4: 风控检查
     - 检查日亏损、连亏、仓位限制
     - 通过 → 下单
     - 不通过 → 跳过
```

---



## 4. Layer 1: 统计定价模型

### 4.1 核心任务

回答一个问题：**在当前条件下，BTC 最终收于 Up 的真实概率是多少？**

如果计算出 P_fair = 0.65，但市场报价 P_market = 0.55，则存在 10% 的错误定价 → 可以买 Up。

### 4.2 模型选择

#### 方案一：解析式（Black-Scholes 数字期权）

将 Polymarket Up token 视为一个数字看涨期权（Digital Call）：

```
P(Up) = N(d2)

其中:
  d2 = [ln(S/K) + (r - σ²/2) * τ] / (σ * √τ)
  
  S = 当前 BTC 价格
  K = 5分钟窗口的开盘价 (strike)
  τ = 剩余时间 (以年化计: 秒数/31536000)
  σ = BTC 年化波动率 (从 realized vol 估计)
  r = 0 (短周期忽略)
  N() = 标准正态分布 CDF
```

**优势**：解析解，计算速度快，可解释性强
**劣势**：假设对数正态，忽略跳跃和肥尾

#### 方案二：经验分布表（推荐与方案一结合）

用 3 个月历史数据建立条件查找表：

```
表结构:
  KEY = (t_remaining_bucket, delta_price_bucket, vol_regime)
  VALUE = 历史上该条件下 Up 的实际频率

示例:
  (120s~180s, +0.05%~+0.10%, medium_vol) → 历史 Up 概率 = 63.2% (N=847)
  (60s~120s, +0.10%~+0.20%, high_vol)   → 历史 Up 概率 = 71.5% (N=523)
```

**分桶建议**：
- t_remaining: [0-30s, 30-60s, 60-120s, 120-180s, 180-240s, 240-300s]
- delta_price: [-0.3%, -0.2%, -0.1%, -0.05%, 0, +0.05%, +0.1%, +0.2%, +0.3%]
- vol_regime: [low (<30% ann), medium (30-60%), high (>60%)]

#### 方案三（进阶）：GBDT/XGBoost 分类器

特征输入：
- t_remaining
- delta_price (当前偏离开盘的幅度)
- 1min realized vol
- 5min realized vol
- CEX orderbook imbalance
- funding rate
- 前一个 5min 窗口的结果（动量/反转统计）

输出：P(Up) ∈ [0, 1]

**注意**：留到阶段 3 再做，先用方案一+二验证 edge。

### 4.3 参数估计

#### σ (波动率) 估计方法

```
推荐: 指数加权已实现波动率 (EWMA RV)

σ_5min = std(5min returns, last 50 windows) * √(288)  # 年化
  
或更精确:
  σ_tick = 用最近 30 分钟的 tick-by-tick 数据计算
  σ_5min = σ_tick * √(5min / tick_interval)
```

**关键**：σ 必须用**当前 regime** 的数据，不能用全历史平均。BTC 波动率有很强的聚类效应。

#### 偏差计算

```
edge = P_fair - P_market   (当 edge > 0 时买 Up)
edge = P_market - P_fair   (当 edge < 0 时买 Down，即买 Down token)

最小交易阈值: |edge| >= 0.03 (3个百分点)
```

### 4.4 模型校准与监控

| 指标 | 目标 | 报警线 |
|------|------|--------|
| Brier Score | < 0.20 | > 0.25 (退化到抛硬币) |
| 预测校准误差 (ECE) | < 5% | > 10% |
| 样本覆盖率 | > 80% 时间有可计算的 P_fair | < 50% |
| 实际胜率 vs 预测概率 | 偏差 < 3% | 偏差 > 5% → 重新校准 |

---

## 5. Layer 2: 实时确认信号

### 5.1 设计原则

- Layer 1 说"市场定价错了"，Layer 2 验证"方向是否正确"
- 至少 2/3 信号同向才确认
- 信号权重可在线学习调整

### 5.2 信号 A: OrderBook Imbalance (权重 35%)

#### 数据源
Polymarket CLOB REST API:
```
GET https://clob.polymarket.com/book?token_id={YES_token_id}
```

#### 计算逻辑

```
bid_volume = Σ(price_i × size_i) for top 10 bids
ask_volume = Σ(price_i × size_i) for top 10 asks
imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)

范围: -1.0 (全是卖方) ～ +1.0 (全是买方)
```

#### 信号判定

| 条件 | 信号方向 | 强度 |
|------|----------|------|
| imbalance > +0.30 | BULLISH (买 Up) | MODERATE |
| imbalance > +0.50 | BULLISH | STRONG |
| imbalance > +0.70 | BULLISH | VERY STRONG |
| imbalance < -0.30 | BEARISH (买 Down) | MODERATE |
| imbalance < -0.50 | BEARISH | STRONG |
| imbalance < -0.70 | BEARISH | VERY STRONG |
| abs(imbalance) < 0.30 | 无信号 | — |

#### 额外检测: Wall Detection (大单)

```
IF 单笔订单 USD > 20% 总订单簿量:
  → 标记为 "Wall"
  → 该方向信号置信度 +5%
```

#### 注意事项
- 需要过滤低流动性订单簿 (total_volume < $50 时不产生信号)
- 不能单看快照——需要看变化量 (Δimbalance in last 30s)
- 挂单可以被撤：单一快照不代表真实意图

---

### 5.3 信号 B: Tick Velocity (权重 35%)

#### 数据源
Bot 自身维护的 tick 缓冲区（每个 quote tick 记录时间戳和中间价）

#### 计算逻辑

```
velocity_60s = (price_now - price_60s_ago) / price_60s_ago
velocity_30s = (price_now - price_30s_ago) / price_30s_ago
acceleration = velocity_30s - (velocity_60s - velocity_30s)
```

#### 信号判定

| 条件 | 信号方向 | 含义 |
|------|----------|------|
| velocity_30s > +1.0% | BULLISH | 概率在快速上涨 (买方在推) |
| velocity_30s < -1.0% | BEARISH | 概率在快速下跌 |
| acceleration 同向 > 0.5% | 加强信号 | 趋势在加速 |
| velocity_60s 与 velocity_30s 反向 | 减弱信号 | 可能在反转 |

#### 为什么有效

Polymarket 上的概率变动代表**真实资金的方向投票**。当概率从 0.50 快速移动到 0.57 时，这代表有信息的交易者已经在大量买入 Up token。他们可能看到了我们还没看到的 CEX 动量。跟随这个信号本质上是在跟随知情交易者。

---

### 5.4 信号 C: CEX Spot Momentum (权重 30%)

#### 数据源
Binance BTC/USDT WebSocket (实时 trade stream 或 1s K线)

#### 计算逻辑

```
btc_momentum_10s = (btc_now - btc_10s_ago) / btc_10s_ago
btc_momentum_30s = (btc_now - btc_30s_ago) / btc_30s_ago
btc_vs_open = (btc_now - btc_5min_open) / btc_5min_open
```

#### 信号判定

```
IF btc_momentum_10s > +0.03%  AND  btc_vs_open > 0:
  → BULLISH (BTC 在上涨且高于开盘)

IF btc_momentum_10s < -0.03%  AND  btc_vs_open < 0:
  → BEARISH (BTC 在下跌且低于开盘)

IF btc_momentum_10s 方向 ≠ btc_vs_open 方向:
  → 信号置信度降低 30% (方向矛盾)
```

#### 与 Oracle Lag 的区别

我们**不是**在利用 Chainlink 延迟做"确定性套利"。我们是在用 CEX 动量作为**一个信号维度**来确认 Polymarket 定价是否合理。区别在于：
- Oracle Lag bot: 看到 CEX 涨了 → 立即买 Up (纯速度)
- 我们: 看到 CEX 涨了 + Layer1 说市场低估 + OrderBook 也偏向买方 → 才行动

---

### 5.5 信号融合

#### 加权确认分数

```
confirmation_score = 
    w_A × score_A × direction_match_A +
    w_B × score_B × direction_match_B +
    w_C × score_C × direction_match_C

其中:
  w_A = 0.35, w_B = 0.35, w_C = 0.30
  score_X = 该信号的置信度 (0~1)
  direction_match_X = +1 (同向) 或 -1 (反向)
```

#### 最终决策矩阵

| Layer 1 偏差 | Layer 2 确认分数 | 动作 |
|---|---|---|
| >= 5% | >= 0.60 | **强信号** → 准备交易 |
| >= 3% | >= 0.50 | **标准信号** → 等待更好时机 |
| >= 3% | < 0.50 | **弱信号** → 不交易 |
| < 3% | 任何 | **无信号** → 不交易 |
| >= 5% | 2/3 信号反向 | **矛盾** → 不交易 (Layer2 否决) |

---



## 6. Layer 3: 时机选择与执行

### 6.1 核心理念

**时间 = 信息**。距离 5 分钟结算越近，BTC 的方向越确定（反转的概率越低）。但同时，越晚下单竞争越激烈、可买到的价格越差。

因此需要在"确定性"和"可执行性"之间找平衡。

### 6.2 时间窗口划分

| 阶段 | t_remaining | 策略行为 | 原因 |
|------|-------------|----------|------|
| **观察期** | 300s ~ 180s | 不交易，只收集数据 | 噪音太大，方向不明 |
| **准备期** | 180s ~ 120s | 计算 fair value，准备信号 | 开始有方向信号 |
| **主交易窗口** | 120s ~ 30s | **核心交易时段** | 确定性 60~75%，流动性尚可 |
| **末秒窗口** | 30s ~ 5s | 仅在强信号时追加 | 确定性 85%+ 但成交难 |
| **禁止交易** | < 5s | 绝不下单 | 链上确认可能来不及 |

### 6.3 订单类型选择

#### Maker Order（优先）

```
使用场景: t_remaining > 30s
优势: 零手续费，更好的价格
劣势: 可能不成交
实现: 
  - 计算目标价格 = P_fair - edge_buffer
  - 挂 limit order
  - 如果 15 秒未成交 → 评估是否 cancel 并以 taker 补入
```

#### Taker Order（备选）

```
使用场景: t_remaining < 30s 且信号极强
优势: 保证成交
劣势: 吃 spread (通常 1~3 cents)
实现:
  - 以当前 best ask/bid 直接吃单
  - 仅在 edge > spread + 2% 时使用
```

### 6.4 价格计算

```
买 Up Token:
  target_price = min(P_fair - 0.02, best_bid + 0.01)
  
  解释: 我们想比当前 best bid 高一点点（排队优先），
        但不超过 fair value - 2% (保证有 edge)

买 Down Token:
  target_price_down = min((1 - P_fair) - 0.02, best_bid_down + 0.01)
```

### 6.5 订单管理

```
下单后监控:
  - 每 5 秒检查是否成交
  - IF 成交 → 记录，等待结算
  - IF 未成交 AND t_remaining < 20s → Cancel (太晚了)
  - IF 未成交 AND 市场价格大幅反向移动 → Cancel (信号失效)
  - IF 未成交 AND 只剩 30s AND edge 仍存在 → 改 Taker 吃入
```

### 6.6 一个完整 5 分钟窗口的执行时间线示例

```
t=300s: 新市场开盘，BTC开盘价=$104,200，P(Up)=0.50
t=250s: 收集数据中...BTC涨到$104,220 (+0.019%)
t=200s: Layer1 计算 P_fair=0.54, 市场报价P=0.51, edge=3% → 还不够
t=150s: BTC到$104,280 (+0.077%), P_fair=0.61, P_market=0.55, edge=6% → 触发!
t=145s: Layer2 确认: OB_imbalance=+0.45, velocity=+1.2%, CEX=+0.05% → 全部BULLISH
t=140s: Layer3: t=140s在主交易窗口内 → 挂 Maker Order @ 0.57 买 Up
t=125s: 订单成交 @ 0.57
t=0s: 结算, BTC=$104,310 > $104,200 → Up wins! 收到 $1.00, 净利 $0.43
```

---

## 7. Layer 4: 风控体系

### 7.1 仓位控制

| 规则 | 参数 | 备注 |
|------|------|------|
| 单笔最大仓位 | $1 ~ $5 | 初期用 $1，验证后逐步放大 |
| 同时最大持仓数 | 1 | 每个 5min 窗口只做一笔 |
| 日最大交易次数 | 100 笔 | 防止过度交易 |
| 最大日总敞口 | $50 | 所有未结算仓位之和 |

### 7.2 亏损控制

| 规则 | 参数 | 触发动作 |
|------|------|----------|
| 单日最大亏损 | -$20 | 停止交易至次日 |
| 连续亏损上限 | 5 笔连亏 | 暂停 1 小时 |
| 周最大亏损 | -$80 | 停止交易，检查策略 |
| 最大回撤 | -15% 本金 | 完全停机，人工审核 |

### 7.3 信号质量控制

```
过去 20 笔交易的滚动监控:

IF win_rate_20 < 48%:
  → 将单笔仓位降至最小 ($1)
  → 将 edge 阈值从 3% 提高到 5% (更保守)

IF win_rate_20 < 42%:
  → 停止交易 2 小时
  → 触发模型重新校准流程

IF win_rate_20 > 60%:
  → 可考虑将仓位从 $1 提升到 $2 (渐进式)
```

### 7.4 市场环境过滤

**不交易的场景：**

| 条件 | 原因 |
|------|------|
| Polymarket 订单簿 total volume < $50 | 流动性不足，滑点大 |
| Bid-ask spread > 8 cents | 成本过高，吃掉 edge |
| BTC 5min realized vol > 150% 年化 | 极端行情，模型失效 |
| 距离 CPI/FOMC 发布 < 5 分钟 | 跳跃风险，非正常行情 |
| Binance WS 断连或数据延迟 > 5s | 数据不可靠 |
| Polymarket API 响应 > 2s | 执行延迟过大 |

### 7.5 Kelly 仓位公式（进阶）

```
最优仓位比例 f* = (p × b - q) / b

其中:
  p = 预估胜率 (来自 Layer1 的 P_fair)
  q = 1 - p
  b = 赔率 = (1 / entry_price) - 1

示例:
  P_fair = 0.62, entry_price = 0.55
  b = (1/0.55) - 1 = 0.818
  f* = (0.62 × 0.818 - 0.38) / 0.818 = 0.155

→ 用 15.5% 的可用资金下注
→ 但实际操作中用 half-Kelly (f*/2) 更安全
→ 并且受硬上限 $5 约束
```

---

## 8. 数据需求与基建

### 8.1 实时数据流

| 数据源 | 协议 | 用途 | 延迟要求 |
|--------|------|------|----------|
| Binance BTC/USDT | WebSocket (trade stream) | CEX momentum, 开盘价基准 | < 100ms |
| Polymarket CLOB | REST polling (每 2~5 秒) | OrderBook, mid price | < 1s |
| Polymarket Market Info | REST | 当前市场 token_id, 开盘时间 | 每 5min 一次 |
| Chainlink Price Feed (可选) | RPC/WS | 监控 oracle 更新时刻 | < 500ms |

### 8.2 历史数据（模型训练）

| 数据 | 来源 | 数量 | 用途 |
|------|------|------|------|
| Polymarket 5min 历史结算结果 | Polymarket API / Gamma Markets | 3+ 个月 | 建经验分布表 |
| 每个 market 的 tick-level 报价 | 自行采集 | 2+ 周 | 校准 Brier Score |
| Binance BTC/USDT 1s K线 | Binance API | 3+ 个月 | 计算 realized vol |
| Polymarket 订单簿快照 | 自行采集 (每 5s) | 2+ 周 | 回测 OrderBook 信号 |

### 8.3 技术栈建议

| 组件 | 推荐方案 | 替代方案 |
|------|----------|----------|
| 语言 | Python 3.11+ | — |
| 异步框架 | asyncio + aiohttp | — |
| Binance 连接 | python-binance (WS) | ccxt |
| Polymarket 连接 | py-clob-client (官方) | 自封装 httpx |
| 订单签名 | eth-account (EIP-712) | web3.py |
| 数据存储 (tick) | SQLite (本地) / TimescaleDB (生产) | Parquet 文件 |
| 调度 | asyncio event loop | APScheduler |
| 监控 | loguru + Prometheus + Grafana | 简化版: 纯日志 |
| 配置 | .env + pydantic Settings | — |
| 回测 | 自建事件驱动回测器 | vectorbt (有限制) |

### 8.4 部署架构

```
推荐: 单台 VPS (低延迟区域)

┌─────────────────────────────────────────┐
│  VPS: AWS us-east-1 / Hetzner US       │
│                                         │
│  ┌─────────────┐   ┌─────────────┐     │
│  │ Bot Process │   │ Data Logger │     │
│  │  (asyncio)  │   │  (SQLite)   │     │
│  └──────┬──────┘   └──────┬──────┘     │
│         │                  │             │
│         ├── Binance WS ────┤             │
│         ├── Polymarket REST─┤            │
│         └── Order Execution ┘            │
│                                         │
│  ┌─────────────┐                        │
│  │  Prometheus │ ← metrics              │
│  │  + Grafana  │                        │
│  └─────────────┘                        │
└─────────────────────────────────────────┘
```

---



## 9. 开发阶段规划

### 阶段 0: 数据采集与验证（第 1~2 周）

**目标**: 验证 edge 是否真实存在

| 任务 | 交付物 | 验证标准 |
|------|--------|----------|
| 接入 Polymarket REST API | 能拉取当前 5min market 列表和报价 | 成功获取 token_id 和 mid price |
| 接入 Binance WS | BTC/USDT 实时价格，延迟 < 100ms | 与 Binance 网页端对比无明显延迟 |
| 采集历史结算数据 | 至少 2000 个已结算的 5min market | 包含 open_price, close_price, result |
| 建立统计分布表 | 条件概率查找表 | 覆盖 80%+ 的 (t, Δp, σ) 组合 |
| 纸面计算 edge | Brier Score + 偏差分析报告 | Brier < 0.22 且偏差区域 > 3% |

**阶段 0 的关键验证**:
```
问题: "如果我在历史上每个 edge > 3% 的时刻都下注，净收益是多少？"
方法: 
  - 对每个历史 market 计算 P_fair(t=120s)
  - 比对当时的实际中间价 P_market
  - IF |P_fair - P_market| > 0.03 → 模拟下注
  - 统计: 胜率, 平均利润, 最大连亏, Sharpe
```

**通过标准**: 纸面年化 Sharpe > 1.5，胜率 > 54%
**失败标准**: Sharpe < 1.0 → 重新评估策略（可能 5min 市场已经太有效了）

---

### 阶段 1: 核心引擎开发（第 3~4 周）

**目标**: 实现四层架构的完整链路（本地模拟）

| 模块 | 任务 | 依赖 |
|------|------|------|
| Fair Value Engine | 实现 BS 定价 + 经验分布表双模型 | 阶段 0 数据 |
| Signal Processors | 实现 OrderBook / Velocity / CEX 三个信号 | Polymarket API + Binance WS |
| Signal Fusion | 加权投票融合，输出 confirmation_score | Signal Processors |
| Execution Engine | Maker/Taker 订单逻辑，时机选择 | py-clob-client |
| Risk Engine | 仓位控制、日亏损、连亏熔断 | — |
| Paper Trading Mode | 全链路模拟，不真实下单 | 以上全部 |

**阶段 1 交付物**:
- Bot 能每 5 分钟自动运行完整决策循环
- Paper trade 日志记录每笔"模拟交易"
- 所有参数可配置 (.env 文件)

---

### 阶段 2: Paper Trading 验证（第 5~6 周）

**目标**: 用真实市场数据运行 2 周，验证实际可行性

| 监控指标 | 目标 | 报警 |
|----------|------|------|
| Paper trade 胜率 | > 54% | < 50% |
| 平均 edge 大小 | > 3% | < 2% |
| 信号触发频率 | 每天 20~60 次 | < 10 次 (太少) 或 > 100 次 (太频繁) |
| OrderBook 信号准确率 | > 55% | < 48% |
| Tick Velocity 信号准确率 | > 55% | < 48% |
| Maker order 模拟成交率 | > 60% | < 30% (定价太保守) |
| 最大连亏 | < 8 笔 | > 12 笔 |

**关键验证**:
- 与纯 Layer1 (只用统计模型) 对比：Layer2 确认是否真的提高了胜率？
- 与"随机时间下单"对比：Layer3 时机选择是否真的提高了 ROI？

**阶段 2 输出**:
- 完整的 paper trade 统计报告
- 参数优化建议（哪些阈值需要调整）
- Go/No-go 决策：是否进入实盘

---

### 阶段 3: 小资金实盘（第 7~10 周）

**目标**: $50~$100 实盘验证

| 实盘新增关注点 | 详情 |
|----------------|------|
| 实际滑点 vs Paper trade 假设 | 实际成交价 vs 理论计算价 |
| Maker order 真实成交率 | 需要 > 50% 才有意义 |
| Gas 费 / 链上失败率 | Polygon 偶尔拥堵 |
| API 延迟与超时处理 | 5 秒内必须完成整个决策链路 |
| 结算正确性 | 手动验证前 20 笔的结算结果 |

**资金管理**:
```
初始资金: $100
单笔: $1 (固定)
日亏损上限: $10
目标: 2 周内验证策略是否实盘可行

里程碑:
  Week 1: 验证基础设施（成交率、延迟、结算）
  Week 2: 验证 PnL 是否为正
  
IF 2 周 PnL > 0 且 Sharpe > 1.0 → 进入阶段 4
IF 2 周 PnL < -$20 → 回到阶段 2 重新调参
```

---

### 阶段 4: 扩容与优化（第 11 周+）

| 优化方向 | 具体内容 |
|----------|----------|
| 增大仓位 | $1 → $2 → $5 (渐进式，每 1 周扩大一次) |
| 加入 Learning Engine | 自动调整信号权重 (根据过去 7 天表现) |
| 加入 ML 模型 | GBDT 替代查找表，特征工程 |
| 多市场覆盖 | ETH, SOL 的 5min market |
| 基础设施升级 | 专用 Polygon RPC 节点，降低延迟 |
| 备用 CEX 数据源 | Coinbase + OKX (防止 Binance 断连) |

---

## 10. 关键风险与缓解

### 10.1 策略风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Edge 不存在 / 太小 | 中 | 致命 | 阶段 0 彻底验证，不存在就不做 |
| 模型过拟合 | 高 | 严重 | 样本外验证、Walk-forward 回测 |
| 市场效率提升 → edge 衰减 | 高 | 中 | 持续监控 Brier Score，随时停机 |
| BTC 极端行情 (跳跃) | 中 | 中 | σ 实时更新、CPI/FOMC 时段不交易 |
| 信号冲突导致不交易 | 中 | 低 | 降低确认阈值，但保留最低标准 |

### 10.2 技术风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Polymarket API 变更 | 中 | 高 | 关注官方 changelog，做版本兼容 |
| Binance WS 断连 | 低 | 中 | 自动重连 + 备用数据源 (Coinbase) |
| Polygon 拥堵 → 订单超时 | 低 | 中 | Gas 价格监控，拥堵时不交易 |
| 私钥泄露 | 极低 | 致命 | .env 不入 git，金额控制 |
| 结算 oracle 争议 | 极低 | 中 | UMA dispute 监控 |

### 10.3 运营风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Polymarket 关闭 5min 市场 | 中 | 致命 | 策略可迁移至 15min / 1hr |
| Polymarket 修改手续费 | 中 | 高 | 费率纳入 edge 计算，自动停机 |
| 监管变化 | 低 | 高 | 关注 CFTC 动态 |
| 竞争加剧 → spread 收窄 | 高 | 中 | 切换为 maker 策略 or 降低频率 |

---

## 11. 成功指标

### 11.1 核心 KPI

| 指标 | 目标值 | 最低可接受 | 测量方式 |
|------|--------|------------|----------|
| **胜率** | > 56% | > 52% | 最近 100 笔滚动统计 |
| **Sharpe Ratio** (日频) | > 2.0 | > 1.2 | 日 PnL 标准差归一化 |
| **日均净利润** | > $5 | > $1 | 扣除所有成本后 |
| **最大回撤** | < 10% | < 15% | 峰值到谷值 |
| **最大连亏** | < 6 笔 | < 10 笔 | 连续亏损次数 |
| **Profit Factor** | > 1.5 | > 1.2 | 总利润 / 总亏损 |

### 11.2 运营 KPI

| 指标 | 目标值 | 测量方式 |
|------|--------|----------|
| 系统正常运行时间 | > 98% | 每日运行小时数 / 24 |
| 信号触发频率 | 30~60 次/天 | 日志统计 |
| Maker order 成交率 | > 60% | 成交数 / 挂单数 |
| API 平均响应时间 | < 500ms | 请求日志 |
| 数据缺失率 | < 1% | 缺失 tick 数 / 总 tick 数 |

### 11.3 停止条件

**任何一个满足则停机排查**:
- 滚动 50 笔胜率 < 45%
- 日亏损触及上限 (-$20)
- 连亏 > 8 笔
- API 错误率 > 10%
- 模型 Brier Score > 0.25（退化到抛硬币水平）

---

## 12. 参考资源

### 12.1 GitHub 项目

| 项目 | 价值 | 链接 |
|------|------|------|
| aulekator/Polymarket-BTC-15-Minute-Trading-Bot | 7阶段架构、6信号处理器参考 | https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot |
| manhashed/polymarket-maker | Log-normal 做市模型参考 | https://github.com/manhashed/polymarket-maker |
| oasisnoehub/PolymarketBtcBot | Oracle Lag 实现参考 | https://github.com/oasisnoehub/PolymarketBtcBot |
| Polymarket/poly-market-maker | 官方做市模板 | https://github.com/Polymarket/poly-market-maker |
| paucase4/polymarket5minBTCAnalysis | Brier Score 校准分析 | https://github.com/paucase4/polymarket5minBTCAnalysis |
| Polymarket/py-clob-client | 官方 Python CLOB 客户端 | https://github.com/Polymarket/py-clob-client |
| ThinkEnigmatic/polymarket-bot-arena | 多策略竞技场 | https://github.com/ThinkEnigmatic/polymarket-bot-arena |

### 12.2 文章与研究

| 标题 | 核心内容 | 链接 |
|------|----------|------|
| What I Learned Building a Trading Bot (optimizooor) | 10天252 commits 实盘52%胜率经验 | https://optimizooor.substack.com/p/10-days-252-commits-and-52-win-rate |
| Unlocking Edges in Polymarket 5-Min Markets | 末秒动态、bot策略、oracle lag分析 | https://medium.com/@benjamin.bigdev |
| Polymarket Calibration (LessWrong) | 7661个市场的Brier Score分析 | https://www.lesswrong.com/posts/Hruc6Gwo3vBZFGb6v |
| Polymarket New Rules (HTX) | 末秒maker策略、API新规 | https://www.htx.com/news/Trading-mkPMdTD8 |
| Oracle Hunter (Devpost) | Chainlink延迟套利实现 | https://devpost.com/software/oracle-hunter |

### 12.3 API 文档

| API | 用途 | 文档 |
|-----|------|------|
| Polymarket CLOB | 下单、查询订单簿、市场信息 | https://docs.polymarket.com |
| Binance Spot WS | BTC/USDT 实时价格 | https://binance-docs.github.io/apidocs |
| Chainlink Data Feeds | Oracle 价格监控 | https://docs.chain.link/data-feeds |
| Deribit Public API | BTC 期权 PCR (可选) | https://docs.deribit.com |

---

## 附录 A: 经济学验证清单

在写任何代码之前，必须能清楚回答以下问题：

```
□ 1. 结算规则确认
  - 5min 窗口的确切开始/结束时间戳是什么？
  - 使用哪个 Chainlink feed？更新频率是多少？
  - "等于开盘价" 算 Up 还是 Down？

□ 2. 成本结构
  - Maker fee = ?  Taker fee = ?
  - 平均 bid-ask spread = ? cents
  - Polygon gas per tx = ? MATIC (折合 $ 多少)
  - 资金进出 Polymarket 的摩擦 = ?

□ 3. 盈亏平衡计算
  - 假设平均 entry price = $0.55 (买 Up)
  - 赢: +$0.45
  - 输: -$0.55
  - 盈亏平衡胜率 = 0.55 / (0.45 + 0.55) = 55%
  - 加上成本 (spread + gas): 实际需要 ~56~57% 才能盈利

□ 4. 容量评估
  - 5min 市场单边平均流动性 = ? 
  - 我下 $5 会影响价格多少？
  - 每天最多能做几笔不影响市场的交易？

□ 5. Edge 衰减速度
  - 1个月前的偏差大小 vs 现在的偏差大小？
  - 趋势是在缩小吗？
```

---

## 附录 B: 信号处理器接口规范

所有信号处理器遵循统一接口：

```
接口定义:

输入:
  - current_price: Decimal        # 当前 Polymarket mid price (0~1)
  - t_remaining: float            # 距结算剩余秒数
  - metadata: Dict                # 额外上下文 (token_id, tick_buffer 等)

输出:
  - direction: BULLISH | BEARISH | NEUTRAL
  - confidence: float (0~1)       # 该信号的自信度
  - strength: WEAK | MODERATE | STRONG | VERY_STRONG
  - metadata: Dict                # 信号详细信息 (用于日志和学习)

规则:
  - 没有足够数据时 → 返回 NEUTRAL
  - confidence < 0.55 时 → 不产生信号
  - 异常/超时 → 返回 NEUTRAL (不报错)
```

---

## 附录 C: 配置参数参考表

| 参数 | 默认值 | 范围 | 含义 |
|------|--------|------|------|
| MIN_EDGE_THRESHOLD | 0.03 | 0.02~0.08 | Layer1 最低偏差才交易 |
| MIN_CONFIRMATION_SCORE | 0.50 | 0.40~0.70 | Layer2 最低确认分数 |
| TRADE_WINDOW_START | 120s | 60s~180s | 最早允许交易的时间 |
| TRADE_WINDOW_END | 5s | 3s~15s | 最晚允许交易的时间 |
| MAX_POSITION_USD | 1.0 | 1~10 | 单笔最大仓位 |
| DAILY_LOSS_LIMIT | 20.0 | 10~50 | 日亏损上限 |
| CONSECUTIVE_LOSS_LIMIT | 5 | 3~8 | 连亏熔断 |
| OB_IMBALANCE_THRESHOLD | 0.30 | 0.20~0.50 | 订单簿不平衡阈值 |
| VELOCITY_THRESHOLD_30S | 0.01 | 0.005~0.02 | 30s 速度阈值 |
| CEX_MOMENTUM_THRESHOLD | 0.0003 | 0.0001~0.0005 | CEX 10s 动量阈值 |
| SIGNAL_WEIGHT_OB | 0.35 | 0.20~0.50 | 订单簿信号权重 |
| SIGNAL_WEIGHT_VEL | 0.35 | 0.20~0.50 | 速度信号权重 |
| SIGNAL_WEIGHT_CEX | 0.30 | 0.15~0.40 | CEX 动量权重 |
| VOL_LOOKBACK_WINDOWS | 50 | 30~100 | σ 计算回溯窗口数 |
| MAKER_ORDER_TIMEOUT | 15s | 10s~30s | Maker 单未成交超时 |

---

*文档版本: v1.0*
*创建日期: 2026-05-13*
*基于 GitHub 开源项目分析和社区实战经验编写*
