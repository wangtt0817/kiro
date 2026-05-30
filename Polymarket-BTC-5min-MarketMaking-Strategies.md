# Polymarket BTC 5-Minute Market Making Strategies
# 做市策略开发设计文档 v1.0

> 本文档配套 `Polymarket-BTC-5min-Strategy-Doc.md`（方向性 alpha 策略）。
> 本文档专注于**做市 (Market Making)** 流派，给出两个互补的核心策略思路：
>
> 1. **策略 A：双边做市 + 数字期权动态对冲 (Quote-and-Hedge MM)** —— 不依赖返佣，靠 spread + 对冲赚钱。
> 2. **策略 B：Rebate-Optimized Market Making** —— 基于 Polymarket 官方 Liquidity Rewards / Maker Rebates 规则优化，把每日 USDC 返佣作为核心收益项。

---

## 目录

1. [项目概述](#1-项目概述)
2. [与方向性策略的关系](#2-与方向性策略的关系)
3. [市场机制与做市经济学](#3-市场机制与做市经济学)
4. [策略 A：Quote-and-Hedge MM](#4-策略-aquote-and-hedge-mm)
5. [策略 B：Rebate-Optimized MM](#5-策略-brebate-optimized-mm)
6. [共享基础设施](#6-共享基础设施)
7. [策略选型与组合矩阵](#7-策略选型与组合矩阵)
8. [开发阶段规划](#8-开发阶段规划)
9. [关键风险与缓解](#9-关键风险与缓解)
10. [成功指标](#10-成功指标)
11. [配置参数参考](#11-配置参数参考)
12. [参考资源](#12-参考资源)
13. [附录](#13-附录)

---

## 1. 项目概述

### 1.1 目标

构建针对 **Polymarket BTC 5-min Up/Down 市场** 的自动化**做市**系统。区别于方向性押注，做市以"提供两边流动性"为核心，通过**bid-ask 价差** + **平台返佣**赚取持续性收入，并把方向风险通过 **CEX 永续对冲** 抹平。

### 1.2 两个策略的定位

| 维度 | 策略 A: Quote-and-Hedge | 策略 B: Rebate-Optimized |
|------|------------------------|--------------------------|
| 主要收益来源 | bid-ask 价差 | Polymarket 每日 USDC 返佣 |
| 报价激进度 | 中等（必须留够 spread 覆盖风险） | 激进（返佣补贴 inventory 风险） |
| 报价位置 | 距 mid 1.5–4 cents | 距 mid 0.5–2 cents（由二次评分函数决定） |
| 对市场的要求 | 任何有流动性的 5-min 市场 | 必须在官方 rewards 列表中 |
| Fill rate 目标 | 高（成交即赚 spread） | 低（避免 fill 反而是好事，赚的是返佣） |
| 主要成本 | 对冲滑点 + gas | gas（频繁 cancel/replace） |
| 受市场效率影响 | 中 | 低（返佣收益独立于价格效率） |

### 1.3 设计哲学

- **不预测方向**：BTC 5min 涨跌 50/50，做市赚的是流动性补偿。
- **风险即时对冲**：每一笔 Polymarket 持仓都映射成 BTC delta，立即在 Binance 永续上抵消。
- **时间衰减是敌人也是朋友**：临近到期时 gamma 爆炸（敌人），但同时方向更确定（朋友），策略 A 主动降速、策略 B 退出 reward band 也主动撤单。
- **基础设施 = 护城河**：低延迟、稳定的 cancel/replace、精确的 fee/gas 会计。

---

## 2. 与方向性策略的关系

仓库里既有的 `Polymarket-BTC-5min-Strategy-Doc.md` 描述了**方向性 alpha**（统计偏差 + 多信号确认）。两类策略在工程上有**70% 共享代码**：

| 共享模块 | 方向性策略用途 | 做市策略用途 |
|---------|---------------|-------------|
| Polymarket CLOB 接入 | 读 mid，下单 | 读 book，挂双边单 |
| Binance WS | 计算 CEX momentum 信号 | 计算公允价 + 对冲 |
| Realized Vol 估计 (σ) | 输入统计模型 | 输入 BS digital + 决定 spread 宽度 |
| 风控引擎 | 仓位/亏损/连亏 | 同上 + inventory 上限 |

**实操建议**：先做策略 A（更通用），再做策略 B（更依赖平台政策稳定性）。两套可以同进程跑、共享数据流。

---

## 3. 市场机制与做市经济学

### 3.1 5-min 市场作为数字期权

Polymarket BTC 5-min 的 Up token 在数学上等价于一个**数字看涨期权 (digital call)**：

```
P(Up) = Pr[ S_T >= K ]   (T = 结算时刻)

数字 call 公允价 (Black-Scholes):
  P_fair = N(d2)
  d2 = [ln(S/K) + (r - σ²/2)·τ] / (σ·√τ)
  S = 当前 BTC 现货, K = 5min 窗口开盘价
  τ = 剩余时间(年化), σ = 年化波动率, r ≈ 0
```

关键性质：
- 时间衰减极快：剩 30s 时 gamma 比剩 4min 时大约 **8 倍**。
- delta 在 ATM 附近发散：mid ≈ 0.50 时小额 BTC 移动会导致 P_fair 大幅跳。
- 远 ITM/OTM 时几乎无 gamma：mid > 0.95 或 < 0.05 时风险极小。

### 3.2 做市者的盈亏来源

```
单笔做市 PnL =
    +  filled_size × (fill_price − fair_value_at_fill)        # 价差捕获
    −  hedge_slippage_on_binance                              # 对冲成本
    −  inventory_to_settlement_pnl                            # 没对冲完到结算的尾巴
    +  daily_rebate_share                                     # 仅策略 B
    −  gas + cancel/replace 成本                              # 基建摩擦
```

**盈亏平衡条件**：`half_spread + rebate_per_dollar > hedge_slippage + gas/volume`

### 3.3 Polymarket Liquidity Rewards 关键规则

> 来源：[docs.polymarket.com/market-makers/liquidity-rewards](https://docs.polymarket.com/market-makers/liquidity-rewards) 和 [maker-rebates](https://docs.polymarket.com/market-makers/maker-rebates)。以下为转述，已合规改写。

- 平台对 taker 收费，每天 ~UTC 0:00 把这部分钱以 USDC 形式按比例分配给做市者。
- 评分函数对**距 midpoint 的距离**做**二次惩罚**——越远得分掉得越快。
- 超出 `max_spread` 的单子直接 0 分。
- 单子的 size、在册时间、距离三者共同决定打分。
- 当 mid < 0.10 时，需要**双边都有挂单**才算合格。
- 每个市场单独维护 `max_spread`、`min_size`、`daily_rewards` 池子，需要每天爬。

> *上述规则按公开文档转述，避免逐字引用以满足合规要求。*

### 3.4 已知做市经济性数据点

| 指标 | 典型值 | 备注 |
|------|--------|------|
| BTC 5-min mid 平均 spread | 1–4 cents | 高波动时段更宽 |
| Maker fee | 0% | 当前政策 |
| Taker fee | 视市场而定 | 喂返佣池 |
| Polygon gas/tx | $0.001–$0.01 | 拥堵时翻数倍 |
| 5-min 市场单边平均订单簿深度 | $200–$2,000 | 越接近到期越深 |
| Rewards 池子（单 BTC 5min 市场） | 视活动而定 | 从公告/rewards 页面读 |

---

## 4. 策略 A：Quote-and-Hedge MM

### 4.1 核心理念

围绕模型公允价 P_fair **双边挂单**，赚取 spread；每被 fill 一次就在 Binance 上做反向对冲，把 BTC delta 抹平；在最后 30–60 秒主动收紧/撤单管理 gamma 风险。

### 4.2 模块 A1: Fair Value Engine

复用 `Polymarket-BTC-5min-Strategy-Doc.md` 第 4 节，但**在做市场景下职责升级**：

- 必须每秒至少更新一次（甚至每个 BTC tick 都更新）；
- 必须输出**delta 和 vega**（用于对冲量计算）；
- 在 vol regime 切换时必须迅速响应（用 EWMA short window）。

```python
# 伪代码接口
class FairValueEngine:
    def update(self, btc_price: float, t_remaining: float, sigma: float):
        self.p_fair = digital_call_bs(btc_price, self.k, t_remaining, sigma)
        self.delta  = digital_call_delta(btc_price, self.k, t_remaining, sigma)
        self.gamma  = digital_call_gamma(btc_price, self.k, t_remaining, sigma)
        self.vega   = digital_call_vega(btc_price, self.k, t_remaining, sigma)
```

### 4.3 模块 A2: Quote Generator

#### 报价公式

```
half_spread(τ, σ, inv) = max(
    min_tick,
    k_vol · σ · √τ        # 波动率 + 时间分量
  + k_inv · |inv|/inv_cap // inventory 越满越宽
  + buffer_fee_gas        // 覆盖 gas + 滑点的固定底
)

mid_skew = γ_inv · (inv / inv_cap)         # inventory 反向 skew
time_skew = γ_t · max(0, 1 − τ/τ_safe)     # 临近到期时整体偏保守

bid_price = clamp(P_fair − half_spread − mid_skew − time_skew, 0.01, 0.99)
ask_price = clamp(P_fair + half_spread − mid_skew + time_skew, 0.01, 0.99)

bid_size  = base_size · (1 − inv/inv_cap)   # 满仓时不再加多
ask_size  = base_size · (1 + inv/inv_cap)   # 满仓时鼓励减仓
```

#### 关键参数初值（待回测调整）

| 参数 | 初值 | 含义 |
|------|------|------|
| `min_tick` | 0.01 | Polymarket 最小价格档 |
| `k_vol` | 0.6 | 波动率项系数 |
| `k_inv` | 0.02 | inventory 项系数（满仓时加 2c） |
| `buffer_fee_gas` | 0.005 | 覆盖 gas/对冲滑点的底线 |
| `γ_inv` | 0.015 | inventory skew 强度 |
| `γ_t` | 0.01 | 时间 skew 强度 |
| `τ_safe` | 90s | 安全时间阈值 |
| `base_size` | $5–$20 | 单档基础挂单量 |
| `inv_cap` | $50 | 单一窗口最大持仓 |

### 4.4 模块 A3: Delta Hedge Engine

#### Delta 计算

每持有 1 个 Up token，等价于 `delta_BTC = ∂P_fair/∂S × 1` 单位的 BTC 多头风险（结果按 BTC 数量计）。

```
total_btc_delta = (n_up_held − n_down_held) · ∂P_fair/∂S · 1/S
                  (为保持单位为 BTC 数量)
```

#### 对冲触发逻辑

```
if abs(total_btc_delta) > delta_band:
    qty = total_btc_delta − target_delta(0)
    binance.market_order(side= "SELL" if qty>0 else "BUY",
                          symbol="BTCUSDT_PERP",
                          size=abs(qty))
```

#### 对冲参数

| 参数 | 初值 | 备注 |
|------|------|------|
| `delta_band` | 0.0005 BTC | ≈ $30 名义 |
| `hedge_venue` | Binance USDT-M Perp | 备选：Bybit |
| `hedge_order_type` | IOC market | 滑点 < 1bp 视为可接受 |
| `funding_check` | 每小时 | funding 极端时降低净敞口阈值 |

#### Gamma 自适应

剩余时间越短，gamma 越大，意味着同样的 BTC 移动会引起 P_fair 更大变化。对冲带 `delta_band` 应该 **τ 越小越严**：
```
delta_band(τ) = delta_band_base · max(0.2, τ/300)
```

### 4.5 模块 A4: 末秒风控（Gamma Cliff）

| 剩余时间 | 行为 |
|---------|------|
| > 90s | 双边正常报价 |
| 60–90s | half_spread × 1.3，inventory > 50% 时只挂减仓侧 |
| 30–60s | half_spread × 1.5，inventory > 30% 时只挂减仓侧；仅 maker |
| 10–30s | **停止新挂单**；仅在能减仓时用 taker 平仓；强制对冲 |
| 0–10s | 完全静止；接受残余 inventory 走结算（已 hedge 部分认结果） |

### 4.6 模块 A5: 订单生命周期管理

```
事件驱动:
  on_book_update(token):
    if abs(new_mid - last_quote_mid) > requote_threshold:  # 默认 0.5c
        cancel_existing_orders(token)
        place_new_orders(token)

  on_fill(side, size, price):
    update_inventory(side, size)
    fair_at_fill = fair_value.snapshot()
    record_pnl(side, size, price, fair_at_fill)
    trigger_hedge_check()

  on_btc_tick():
    fair_value.update(...)
    if drift_too_large():
        cancel_and_requote()
    hedge_engine.check_delta()
```

**重挂节流**：每秒 cancel/replace 不超过 N 次（默认 N=2），防止 gas 失控。

### 4.7 模块 A6: PnL 会计

每个 5-min 窗口结束后立即结算 PnL 报告：

```
窗口 PnL = Σ (filled_size × (fill_price − settlement_price))   # Polymarket 部分
        + Σ binance_hedge_pnl                                    # 对冲部分
        − total_gas_paid
        − total_cancel_replace_count × est_gas
```

理论上 Polymarket PnL 与 Binance PnL 应该高度负相关，**净值就是做市 spread**。如果两者总和波动太大，说明 hedge 不及时或 size mismatch。

### 4.8 KPI（策略 A）

| 指标 | 目标 | 报警 |
|------|------|------|
| 日成交笔数 | > 500 | < 100 → 报价太保守 |
| 日成交量 | > $5,000 | — |
| 平均净 spread (cents/$1 traded) | > 1.0c | < 0.5c → 不够覆盖成本 |
| Hedge 残余日 PnL 标准差 | < $5/天 | > $20 → 对冲失效 |
| Cancel/replace 比 | < 5×成交数 | > 20× → gas 黑洞 |
| 日净 PnL（去除一切成本后） | > $10 ($100 本金) | 连续 3 天为负 → 停机 |

---

## 5. 策略 B：Rebate-Optimized MM

### 5.1 核心理念

把 Polymarket Liquidity Rewards 当成一个**额外的负 maker fee**加进收益函数。由于评分对距 mid 的距离是**二次衰减**的，"挂得最紧"在 reward 主导的市场里是正 EV 的事——即使你被 fill 时是负 spread，每日返佣依然能把整体拉回正。

**关键认知差**：策略 A 怕 fill rate 太高，策略 B 怕 fill rate 太低（但更怕 fall out of reward band）。

### 5.2 模块 B1: Reward Score Simulator

#### 5.2.1 本地复刻评分函数

根据公开文档，评分核心是：

```
q(d) = max(0, 1 − (d / max_spread)²)        # 二次衰减
score(order) = size_eff · q(d) · t_in_book
  size_eff = min(order.size, size_cap_per_level)
  d = |order.price − adjusted_mid|

my_total_score   = Σ score(order)  for all my orders
total_score      = Σ score(order)  for all participants' orders
my_daily_reward  = (my_total_score / total_score) · daily_pool_USDC
```

> *这是基于公开文档的标准化转述；具体形式以 Polymarket 实时规则为准。*

#### 5.2.2 离线验证（关键步骤）

1. 拉 7–14 天 orderbook 快照（每 5 秒一帧）；
2. 对每个市场每天每分钟，重算所有挂单的 score；
3. 把"自己的预期 reward"和实际打款金额做回归；
4. 拟合校正系数 `α`，让 `α · simulated_reward ≈ actual_reward`，误差应 < 10%。

**没有这一步的话整个策略 B 是盲飞。**

### 5.3 模块 B2: 自适应报价位置

#### 5.3.1 最优距离 d\*

对每个挂单，期望 PnL 是：

```
E[PnL_per_order] = q(d) · size · (daily_pool / total_score) · time_in_book
                 + p_fill(d) · size · (fair_value − price)        # 被 fill 时的 spread/损益
                 − hedge_cost(size) · p_fill(d)
                 − gas/replace_count
```

求 d* = argmax 上式。一般解：
- 当 reward pool 大、竞争小 → d\* 趋近 0（贴 mid）；
- 当 reward pool 小、竞争大 → d\* 推到 ~½ max_spread（边际收益 = 边际 fill 风险）。

实操中用网格搜索 d ∈ {0.5c, 1c, 1.5c, 2c, ..., max_spread} 选最优。

#### 5.3.2 双边强制规则

```
if mid < 0.10 or mid > 0.90:
    require_two_sided = True       # 单边不合格
else:
    allow_one_sided = (inventory > inv_threshold)
```

#### 5.3.3 多档分布

把 size 切成多档（如 3 档），分别挂在 d ∈ {d\*, d\*+0.5c, d\*+1c}：
- 离 mid 最近的档主要赚 reward；
- 远档作为"防御档"，万一 mid 跑掉时还能维持 partial 评分。

### 5.4 模块 B3: 仓位与对冲

完全复用策略 A 的 Hedge Engine。差异：

| 项 | 策略 A | 策略 B |
|----|--------|--------|
| `inv_cap` | $50 | $80（返佣补贴允许更大仓） |
| `delta_band` | 0.0005 BTC | 0.0003 BTC（返佣边际成本低，对冲更勤） |
| 对冲触发 | 仓位事件 | 仓位事件 + 每个 BTC tick |

**关键约束**：`expected_daily_rebate / expected_filled_volume > hedge_slippage_per_dollar`，否则等于在补贴对手方。

### 5.5 模块 B4: 市场选择

#### 每日刷新流程（UTC 0:30 跑一次）

```
1. fetch_polymarket_rewards_list()           # 当天活跃 reward 市场
2. 过滤: category == BTC && window == 5min
3. 对每个候选市场计算:
     attractiveness = daily_pool / current_competition_score
4. 选 top-K 个市场（K=3~5）作为做市目标
5. 把剩余预算分配给这些市场（按 attractiveness 加权）
```

#### 进入/退出条件

```
ENTER:
  - 在 rewards 列表中
  - daily_pool >= $20
  - 当前我的预期 share >= 5%

EXIT:
  - 该市场被移出 rewards 列表
  - 流动性突然消失（spread > 2× max_spread）
  - 我的 share < 1%（卷不过别人）
```

### 5.6 模块 B5: 重挂节流（Gas-Aware）

策略 B 的最大成本是 cancel/replace gas。规则：

```
重挂触发条件 (满足任一):
  1. 我的最优档 fall out of reward band
  2. q(d_my) 因 mid 移动下降 > 30%
  3. inventory skew 触发再平衡
  4. 自上次重挂超过 60s（最低刷新频率，防止 stale）

不重挂条件:
  - mid 移动 < 0.5 cents 且我仍在 reward band 内
  - 上次 cancel/replace 在 5s 内（速率限制）
```

### 5.7 模块 B6: PnL 会计与归因

```
每日 PnL =
    + Σ filled_pnl_vs_fair               # 可能为负，可以接受
    + Σ binance_hedge_pnl
    + actual_received_rebate             # 核心
    − total_gas_paid

归因:
  rebate_pct  = rebate / total_pnl
  spread_pct  = filled_pnl / total_pnl
  hedge_pct   = abs(hedge_pnl) / total_pnl
```

健康的策略 B：`rebate_pct > 70%`。

### 5.8 KPI（策略 B）

| 指标 | 目标 | 报警 |
|------|------|------|
| 在 reward band 时间占比 | > 90% | < 70% → 报价 stale |
| 日 USDC 返佣（单市场） | > 池子的 5% | < 1% → 卷不过 |
| Rebate / Gas | > 5× | < 2× → gas 黑洞 |
| 实际 / 模拟 reward 误差 | < 10% | > 25% → simulator 失准 |
| 日 fill rate | 5–15% | > 30% → 挂得太紧；< 2% → 挂得太远 |
| 净 PnL（含返佣） | > 池子份额 × 0.6 | 持续 < 0.3 → 重新校准 |

---

## 6. 共享基础设施

### 6.1 数据流

```
┌────────────────────────────────────────────────────────────────┐
│                      DATA SOURCES                                │
├────────────────────────────────────────────────────────────────┤
│  Binance WS (BTC/USDT spot + perp)        ← 公允价、对冲价      │
│  Polymarket CLOB REST/WS                  ← orderbook、my orders│
│  Polymarket Rewards API/页面 (daily)      ← 策略 B 市场列表     │
│  Chainlink Feed (可选)                    ← 结算价监控          │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                       SHARED STATE                               │
│  - btc_price, sigma_ewma, vol_regime                           │
│  - per_market: { p_fair, mid, book_top, my_orders, inventory } │
│  - hedge_position_btc, total_delta                             │
└────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
       ┌──────────┐   ┌──────────────┐   ┌──────────┐
       │ Strat A  │   │   Strat B    │   │  Hedge   │
       │ Engine   │   │   Engine     │   │  Engine  │
       └──────────┘   └──────────────┘   └──────────┘
              │               │
              └───────┬───────┘
                      ▼
              ┌──────────────┐
              │ Order Router │ ← rate limiter, gas budget
              └──────────────┘
                      │
                      ▼
              ┌──────────────┐
              │ Risk Engine  │ ← 全局熔断
              └──────────────┘
```

### 6.2 订单管理子系统 (OMS)

需要支持：
- 多市场并发挂单（每个 5-min 市场都是独立 token_id）。
- **Order versioning**：每次 cancel/replace 给新的 client_order_id，避免乱序。
- **Idempotency**：一次 cancel 失败必须可重试且不重复 cancel。
- **Gas budget**：每窗口给定 gas 上限，超过就 freeze。

### 6.3 对冲引擎（共享）

```python
class HedgeEngine:
    def __init__(self, exchange, delta_band):
        self.target = 0.0
        self.delta_band = delta_band

    def on_polymarket_inventory_change(self, token, delta_change_btc):
        self.pending_delta += delta_change_btc
        self.maybe_hedge()

    def on_btc_tick(self):
        # gamma 大时即使 inventory 没变也要 reassess
        self.recompute_delta_from_positions()
        self.maybe_hedge()

    def maybe_hedge(self):
        net = self.compute_net_delta()
        if abs(net) > self.delta_band(t_remaining_min):
            qty = -net   # opposite direction
            self.exchange.execute_ioc(qty)
```

### 6.4 风控（双策略共用）

| 层级 | 规则 | 触发动作 |
|------|------|----------|
| 单市场 | inventory > inv_cap | 停止该方向挂单 |
| 单市场 | hedge 失败 > 2 次 | 取消所有挂单，强平 |
| 全局 | 日亏损 > -$50 (策略 A) / -$30 (策略 B) | 当日停机 |
| 全局 | Binance 断线 > 10s | 取消所有 Polymarket 挂单 |
| 全局 | 链上拥堵导致 cancel 延迟 > 5s | 进入 "drain mode"（只减仓） |
| 全局 | BTC 1min 涨跌 > 1% (跳跃) | 暂停 60s |
| 全局 | CPI/FOMC 发布前后 ±5min | 完全停机 |

---

## 7. 策略选型与组合矩阵

### 7.1 单策略选型

| 场景 | 推荐 |
|------|------|
| 该 5-min 市场不在 rewards 列表 | 策略 A |
| 该市场 daily_pool < $20 | 策略 A |
| 该市场 in rewards 且竞争稀疏 | 策略 B |
| 该市场 in rewards 但有 5+ 专业 MM 在卷 | 策略 A（避免被卷死）|
| 极端波动事件期 | 都不做 |

### 7.2 组合（同进程、不同 token 上各跑一种）

允许同一 BTC 5-min 市场上**只跑一种**策略（避免自己单子互相 fill 自损）。**不同 5-min 窗口**之间可以分配不同策略：

```
窗口分配示例（一天 288 个窗口）:
  - 70 个窗口：策略 B (in rewards)
  - 218 个窗口：策略 A 或不参与
```

### 7.3 渐进迁移

```
Stage 1: 只跑策略 A，验证 hedge + 基础设施
Stage 2: 引入 reward simulator（不实战，只观察）
Stage 3: 在 1 个 reward 市场上小资金跑策略 B
Stage 4: 扩展到多市场策略 B
Stage 5: AB 对照 / 自动切换
```

---

## 8. 开发阶段规划

### 阶段 0: 数据准备 + 经济验证（1 周）

**通用**:
- 跑通 Binance WS、Polymarket CLOB REST/WS 接入
- 采集 7+ 天 orderbook 快照 + 成交记录 + 我自己的小额测试单结算

**策略 A 专属**:
- 历史回测：如果在过去 7 天每个市场我都用 A 的报价规则双边挂 $5，预期 PnL 是多少？
- 验证 hedge 假设：模拟 Binance 反向操作的滑点是否真的 < 1bp

**策略 B 专属**:
- 实现 reward simulator，用 7 天数据回测
- 拿出实际 rebate 打款记录（即使是别人的，社区里有公开数据）做拟合
- 验证：simulator 误差是否 < 10%

**通过标准**:
- 策略 A：Paper trade 7 天净 PnL > 0
- 策略 B：Reward simulator 误差 < 10%

---

### 阶段 1: 核心引擎（2 周）

| 模块 | 优先级 | 估时 |
|------|--------|------|
| Fair Value Engine（含 delta/gamma） | P0 | 3 天 |
| Order Manager（多市场 + idempotent） | P0 | 4 天 |
| Hedge Engine（Binance perp） | P0 | 3 天 |
| 风控引擎 | P0 | 2 天 |
| 策略 A Quote Generator | P0 | 2 天 |
| 策略 B Reward Simulator + Position Optimizer | P1 | 4 天 |
| 监控 + 日志（Prometheus + Grafana） | P1 | 2 天 |

---

### 阶段 2: Paper Trading（2 周）

- 全链路用真实数据，但所有 Polymarket 订单调用都走 mock。
- 真实下 Binance 对冲单（数额极小，验证延迟和滑点真实性）。
- KPI 对照：策略 A、策略 B 分别看上面的指标表。

---

### 阶段 3: 小资金实盘（2–4 周）

| 阶段 | 资金 | 目标 |
|------|------|------|
| 3.1 策略 A only | $200 | 验证基础设施和 PnL 真实性 |
| 3.2 + 策略 B 单市场 | +$200 | 验证 reward 计算精度 |
| 3.3 多市场策略 B | +$500 | 验证扩展性 |

**升级阈值**：
- 每阶段连续 7 天净 PnL > 0 且最大回撤 < 8% → 进入下一阶段
- 不达标 → 回 Paper Trading 调参

---

### 阶段 4: 扩容与优化（持续）

- 把 inv_cap 从 $50 → $200 → $500（逐周翻倍）
- 加入第二个 CEX（Bybit）作为对冲冗余
- 学习引擎：自动调整 `k_vol`、`γ_inv`、`d*` 等参数（用过去 30 天数据）
- 扩展到 ETH/SOL 5-min（同一架构）

---

## 9. 关键风险与缓解

### 9.1 策略风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| Hedge 滑点 > 模型假设 | 中 | 实盘前用 $5 量级真实测试至少 100 笔 |
| Reward 规则变更（策略 B） | 高 | Daily fetch + 失配立即停机；不超过总资金 30% 投入 B |
| Reward 池子被卷死 | 中 | competition score 监控，share < 1% 时退出 |
| Inventory 来不及 hedge（gamma cliff） | 高 | 末秒强制收紧 + 不接受新 fill |
| BTC 跳跃（黑天鹅） | 高 | vol regime 切换时自动加宽 spread；event 黑名单 |

### 9.2 技术风险

| 风险 | 缓解 |
|------|------|
| Polygon 拥堵导致 cancel 卡住 | gas 上限 + drain mode |
| Polymarket WS 断 | fallback 到 REST polling，期间不挂新单 |
| Binance 断线 | 立即取消所有 Polymarket 挂单，等 hedge 恢复 |
| 私钥/API key 泄露 | 单独 deposit wallet + 上限设定（Polymarket 支持） |
| 时钟漂移（影响 t_remaining） | NTP 同步，每 5 分钟校验 |

### 9.3 运营风险

- Polymarket 可能调整 fee → 每月 review fee schedule
- 监管事件 → 准备 60s 内全清能力
- 单点 VPS → 备用机器冷待命

---

## 10. 成功指标

### 10.1 综合 KPI（双策略共用）

| 指标 | 目标 | 最低 |
|------|------|------|
| 月化 ROI | > 15% | > 5% |
| 月最大回撤 | < 10% | < 20% |
| Sharpe (日频) | > 2.5 | > 1.5 |
| 系统 uptime | > 98% | > 95% |
| Profit Factor | > 1.8 | > 1.3 |

### 10.2 停止条件

任一满足即停机：
- 滚动 7 天净 PnL < -10% 本金
- Hedge 残差日 std > $50（策略失效）
- Reward simulator 误差 > 25%（策略 B）
- 任何一笔单笔亏损 > $30（异常）

---

## 11. 配置参数参考

### 11.1 通用参数

| 参数 | 默认 | 范围 |
|------|------|------|
| `BTC_VOL_LOOKBACK` | 30min | 10min–60min |
| `REQUOTE_THRESHOLD` | 0.5c | 0.3c–1c |
| `MAX_REQUOTE_RATE` | 2/s | 1–5/s |
| `EVENT_BLACKLIST_WINDOW` | ±5min | ±3min–±10min |
| `JUMP_DETECTION_THRESHOLD` | 1%/min | 0.5%–2% |

### 11.2 策略 A 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `A_BASE_SIZE` | $5 | 单档基础挂单 |
| `A_INV_CAP` | $50 | 单一窗口最大持仓 |
| `A_K_VOL` | 0.6 | spread 中波动率系数 |
| `A_K_INV` | 0.02 | spread 中 inventory 系数 |
| `A_BUFFER_FEE_GAS` | 0.005 | spread 底 |
| `A_GAMMA_INV` | 0.015 | mid skew 强度 |
| `A_GAMMA_T` | 0.01 | time skew 强度 |
| `A_TAU_SAFE` | 90s | 安全时间阈值 |
| `A_DELTA_BAND` | 0.0005 BTC | hedge 触发带 |

### 11.3 策略 B 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `B_INV_CAP` | $80 | 返佣补贴下放宽的仓位 |
| `B_DELTA_BAND` | 0.0003 BTC | 更勤的 hedge |
| `B_MARKET_SHARE_FLOOR` | 0.01 | 低于则退出 |
| `B_REWARD_POOL_FLOOR` | $20 | 池子下限 |
| `B_DEPTH_LEVELS` | 3 | 多档分布数 |
| `B_DEPTH_STEPS` | [0, 0.5c, 1c] | 多档相对偏移 |
| `B_MIN_REQUOTE_INTERVAL` | 5s | 速率限制 |
| `B_MAX_REQUOTE_INTERVAL` | 60s | 防止 stale |

---

## 12. 参考资源

### 12.1 官方文档（按公开规则转述）

| 资源 | 链接 |
|------|------|
| Polymarket Liquidity Rewards | https://docs.polymarket.com/market-makers/liquidity-rewards |
| Polymarket Maker Rebates | https://docs.polymarket.com/market-makers/maker-rebates |
| Polymarket Fees | https://docs.polymarket.com/trading/fees |
| Polymarket CLOB Client (Python) | https://github.com/Polymarket/py-clob-client |
| Polymarket Rewards Page (daily) | https://polymarket.com/rewards |

### 12.2 工程参考

| 项目 | 价值 |
|------|------|
| Polymarket/poly-market-maker | 官方做市模板 |
| manhashed/polymarket-maker | log-normal 做市实现 |
| 仓库内 `Polymarket-BTC-5min-Strategy-Doc.md` | 共享 Layer 1 公允价 + 风控基础 |

### 12.3 文献

- Avellaneda & Stoikov, "High-frequency trading in a limit order book" (2008) — inventory skew 经典推导
- Cartea, Jaimungal, Penalva, "Algorithmic and High-Frequency Trading" (2015) — MM 框架
- 任何讲 digital option Greeks 的衍生品教材（Hull）

---

## 13. 附录

### 附录 A: 公允价 + Greeks 实现（Python 伪码）

```python
import math
from scipy.stats import norm

def digital_call(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> dict:
    """
    Returns P_fair, delta, gamma, vega for a digital call.
    T in years, sigma annualized.
    """
    if T <= 0:
        return dict(p=1.0 if S >= K else 0.0, delta=0, gamma=0, vega=0)
    sqrtT = math.sqrt(T)
    d2 = (math.log(S/K) + (r - 0.5 * sigma**2) * T) / (sigma * sqrtT)
    n_d2 = norm.pdf(d2)
    return dict(
        p     = norm.cdf(d2),
        delta = n_d2 / (S * sigma * sqrtT),
        gamma = -n_d2 * d2 / (S**2 * sigma**2 * T),
        vega  = -n_d2 * d2 / sigma,
    )
```

### 附录 B: Reward Score Simulator（Python 伪码）

```python
def order_score(order, adjusted_mid: float, max_spread: float,
                size_cap: float, t_in_book: float) -> float:
    d = abs(order.price - adjusted_mid)
    if d > max_spread:
        return 0.0
    q = max(0.0, 1.0 - (d / max_spread) ** 2)
    size_eff = min(order.size, size_cap)
    return size_eff * q * t_in_book

def expected_daily_reward(my_orders, all_orders, daily_pool, **ctx):
    my_score    = sum(order_score(o, **ctx) for o in my_orders)
    total_score = sum(order_score(o, **ctx) for o in all_orders)
    if total_score == 0:
        return 0.0
    return (my_score / total_score) * daily_pool
```

### 附录 C: 接口规范

```
QuoteGenerator:
  inputs:
    - market_state: { p_fair, sigma, t_remaining, mid, top_book }
    - inventory: { up: int, down: int }
    - constraints: { max_spread, min_size, etc. }
  output:
    - List[Quote]: { side, price, size, ttl }

HedgeEngine:
  inputs:
    - inventory_change_event | btc_tick_event
    - greeks: { delta, gamma }
  output:
    - List[ExchangeOrder]: { venue, symbol, side, size, type=IOC }

RiskGate:
  inputs:
    - proposed_order
    - global_state: { daily_pnl, inventory_total, gas_used_today, ... }
  output:
    - decision: ALLOW | DENY(reason)
```

---

*文档版本: v1.0*
*创建日期: 2026-05-23*
*作者: Kiro (基于 Polymarket 公开文档 + 既有方向性策略 doc 推演)*
