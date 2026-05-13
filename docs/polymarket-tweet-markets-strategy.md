# Polymarket Tweet Markets 交易策略开发文档

> **项目代号**: Tweet Oracle  
> **版本**: v1.0  
> **日期**: 2026-05-13  
> **目标市场**: Polymarket "Elon Musk # tweets" 系列合约  

---

## 目录

1. [市场概述与规则](#1-市场概述与规则)
2. [策略核心思想](#2-策略核心思想)
3. [数学框架](#3-数学框架)
4. [系统架构](#4-系统架构)
5. [数据层规范](#5-数据层规范)
6. [模型层规范](#6-模型层规范)
7. [信号与执行层](#7-信号与执行层)
8. [仓位管理与风控](#8-仓位管理与风控)
9. [回测框架](#9-回测框架)
10. [部署与运维](#10-部署与运维)
11. [开发路线图](#11-开发路线图)
12. [附录](#附录)

---

## 1. 市场概述与规则

### 1.1 市场定义

Polymarket Tweet Markets 是一类预测市场，交易者押注 Elon Musk 在特定时间窗口内的发推（帖子）总数将落入哪个数量区间（bucket）。

### 1.2 结算规则

| 要素 | 规则 |
|------|------|
| **计数口径** | main-feed posts + quote posts + reposts（主帖 + 引用帖 + 转发） |
| **时间窗口** | 通常为 7 天（周一 12:00 PM ET → 下周一 12:00 PM ET），也有 2-3 天的短周期合约 |
| **数据源** | Polymarket 官方 XTracker（xtracker.polymarket.com） |
| **Bucket 划分** | 通常 6-8 个互斥区间，如 <80, 80-99, 100-119, 120-139, 140-159, 160-179, 180-199, 200+ |
| **结算方式** | 最终计数落入的 bucket 的 YES 合约结算为 $1.00，其余为 $0.00 |
| **交易费用** | Polymarket CLOB 手续费（maker 0%, taker ~2%） |
| **底层链** | Polygon（Gas 费用极低，约 $0.001-0.01/笔） |

### 1.3 市场特征

| 指标 | 当前数据（2026年5月） |
|------|------|
| **周交易量** | $3M - $6M |
| **流动性** | $600K - $1M |
| **参与者数** | 数千个活跃钱包 |
| **Bucket 数量** | 6-8 个 |
| **价格精度** | $0.01（1 cent） |

### 1.4 关键约束

- 所有 bucket 的 YES 价格之和 ≈ $1.00（套利者会维持）
- 订单簿深度有限，大单会产生显著滑点
- 结算后资金才释放，期间存在资金占用成本



---

## 2. 策略核心思想

### 2.1 一句话总结

> **在一周的过程中，利用泊松过程的分裂性质，将"已观察到的推文数量"作为证据，对"最终总量落入各 bucket"的概率做贝叶斯实时更新，当模型概率与市场定价出现显著偏差时，以 Kelly 准则确定仓位执行交易。**

### 2.2 策略哲学

本策略不是多条分支的拼凑，而是**一个统一的数学引擎**：

```
先验分布（历史统计）
    × 似然函数（实时观测）
    ÷ 归一化常数
    = 后验分布（实时预测）
    → 与市场价对比
    → Kelly 仓位
    → 执行
```

### 2.3 Edge 来源

本策略的信息优势来自以下几个层面：

1. **计算优势**：精确的贝叶斯更新 vs 散户的"感觉"定价
2. **时间优势**：每小时更新 vs 市场价格的滞后反应
3. **结构认知**：利用日内强度曲线的非均匀性，更精确地估计 ρ(t)
4. **纪律优势**：数学化的仓位管理 vs 情绪化的加减仓

### 2.4 策略不做什么

- **不做高频交易**：更新频率 1-4 小时，不需要毫秒级反应
- **不依赖 LLM**：纯数学模型，确定性输出，无幻觉风险
- **不需要主观判断**：公式输出交易信号，无拍脑袋环节
- **不做多市场套利**：专注 Tweet Markets 单一赛道



---

## 3. 数学框架

### 3.1 符号表

| 符号 | 含义 |
|------|------|
| $N$ | 一周内推文总数（随机变量） |
| $T$ | 一周总时长（= 168 小时） |
| $t$ | 当前已过去的时间 |
| $n_t$ | 截至时刻 $t$ 已观察到的推文数 |
| $r, p$ | 负二项分布参数 |
| $\mu$ | 周推文数均值 $= r(1-p)/p$ |
| $\sigma^2$ | 方差 $= \mu + \mu^2/r$ |
| $\lambda(h)$ | 第 $h$ 小时的发推强度（posts/hour） |
| $\Lambda(t)$ | 累积强度函数 $= \sum_{h=0}^{t} \lambda(h)$ |
| $\rho(t)$ | 强度比例 $= \Lambda(t) / \Lambda(T)$ |
| $B_i$ | 第 $i$ 个 bucket，定义为区间 $[a_i, b_i]$ |
| $P_m(B_i)$ | 模型计算的 bucket $i$ 后验概率 |
| $P_{mkt}(B_i)$ | 市场隐含的 bucket $i$ 概率（= YES 价格） |

### 3.2 先验分布：负二项模型

#### 为什么选负二项而不是泊松

Musk 的发推数据呈现**过度离散**（overdispersion）：
- 泊松分布要求 $\text{Var}(N) = \text{Mean}(N)$
- 实际数据中 $\text{Var}(N) >> \text{Mean}(N)$
- 原因：Musk 的发推行为具有"聚集性"——活跃日远超均值，静默日远低于均值

负二项分布允许 $\text{Var} > \text{Mean}$，是处理过度离散计数数据的标准选择。

#### 参数化

$$f_{NB}(k; r, p) = \binom{k + r - 1}{k} (1-p)^r p^k, \quad k = 0, 1, 2, ...$$

其中：
- 均值：$\mu = rp / (1-p)$
- 方差：$\sigma^2 = rp / (1-p)^2 = \mu / (1-p)$
- 离散度参数：$r$ 越小，离散度越大

> **注**：这里使用的是"成功次数"参数化（scipy.stats 中 `nbinom` 的约定）。不同文献参数化方式不同，实现时需统一。

#### 参数估计

使用历史数据 $\{N_1, N_2, ..., N_W\}$（过去 $W$ 周的周推文数），通过**最大似然估计（MLE）**求解 $r, p$：

$$\hat{r}, \hat{p} = \arg\max_{r,p} \sum_{w=1}^{W} \ln f_{NB}(N_w; r, p)$$

实践中使用 `scipy.optimize.minimize` 求数值解。

#### 马尔可夫增强（可选）

借鉴 Polymarket 官方 "Tweet Quant" 分析的发现：

> "Low" 周有 61% 概率被另一个 "Low" 周跟随

将历史数据分为 K 个状态（如 Low/Medium/High），根据上周实际结果选择本周的**条件先验**：

$$f_{prior}(k) = f_{NB}(k; r_{state}, p_{state})$$

其中 $state \in \{Low, Medium, High\}$ 由上周结果决定。

### 3.3 似然函数：泊松分裂性质

#### 核心定理

对于非齐次泊松过程，给定时间区间 $[0, T]$ 内总共发生了 $k$ 个事件，则在子区间 $[0, t]$ 内观察到 $n_t$ 个事件的条件概率为：

$$P(N(t) = n_t \mid N(T) = k) = \binom{k}{n_t} \rho(t)^{n_t} (1 - \rho(t))^{k - n_t}$$

这是一个二项分布，其中 $\rho(t)$ 是时刻 $t$ 之前的累积强度占总强度的比例。

#### 直觉解释

如果一周总共会发 $k$ 条推文，到周三中午已经过了"40% 的总发推强度时间"（$\rho = 0.4$），那么你期望在周三中午之前看到约 $0.4k$ 条推文。实际观测到的数量提供了关于总量 $k$ 的信息。

### 3.4 后验分布：贝叶斯更新

应用贝叶斯定理：

$$P(N = k \mid n_t) = \frac{P(N(t) = n_t \mid N = k) \cdot P(N = k)}{\sum_{j=n_t}^{K_{max}} P(N(t) = n_t \mid N = j) \cdot P(N = j)}$$

展开：

$$\boxed{P(N = k \mid n_t) = \frac{\binom{k}{n_t} \rho^{n_t} (1-\rho)^{k-n_t} \cdot f_{NB}(k; r, p)}{\sum_{j=n_t}^{K_{max}} \binom{j}{n_t} \rho^{n_t} (1-\rho)^{j-n_t} \cdot f_{NB}(j; r, p)}}$$

其中：
- 求和下限为 $n_t$（总量不可能小于已观察量）
- $K_{max}$ 为合理截断值（如 500），超过该值概率密度可忽略
- 分母是归一化常数，确保后验概率之和为 1

### 3.5 Bucket 概率计算

对于 bucket $B_i = [a_i, b_i]$：

$$P_m(B_i) = \sum_{k=a_i}^{b_i} P(N = k \mid n_t)$$

### 3.6 强度比例 ρ(t) 的估计

#### 方法 A：均匀假设（Baseline）

$$\rho(t) = t / T$$

简单但不精确。

#### 方法 B：经验强度曲线（推荐）

从历史数据中统计每小时的平均发推数 $\hat{\lambda}(h)$，$h = 0, 1, ..., 167$：

$$\rho(t) = \frac{\sum_{h=0}^{\lfloor t \rfloor} \hat{\lambda}(h)}{\sum_{h=0}^{167} \hat{\lambda}(h)}$$

#### 方法 C：含周内节奏修正

考虑到周一到周日的发推模式不同：

$$\hat{\lambda}(h) = \hat{\lambda}_{weekday}(h \bmod 24) \cdot \gamma_{day\_of\_week}$$

其中 $\gamma_{day\_of\_week}$ 是每天的强度修正因子（如周六 = 0.7，周三 = 1.2）。

### 3.7 事件因子叠加（可选增强）

当存在已知事件（如产品发布、政治事件）时，对先验均值做乘性调整：

$$\mu_{adjusted} = \mu_{base} \cdot (1 + \alpha_{event})$$

其中 $\alpha_{event}$ 为事件影响系数：
- SpaceX 发射：$\alpha \approx +0.10$ 至 $+0.20$
- 重大政治争议：$\alpha \approx +0.20$ 至 $+0.50$
- 已知出行/休假：$\alpha \approx -0.20$ 至 $-0.40$

调整后重新计算 $r_{adj}, p_{adj}$ 使得新均值 = $\mu_{adjusted}$，同时保持离散度不变。

### 3.8 仓位管理：多互斥结果 Kelly 准则

#### 标准 Kelly（单 Bucket）

对于单个 bucket $B_i$，其 YES 合约定价为 $q_i$，你的模型概率为 $p_i$：

$$f_i^{Kelly} = \frac{p_i - q_i}{1 - q_i}$$

#### 多 Bucket 联合优化

当同时在多个 bucket 下注时，最优仓位通过以下优化问题求解：

$$\max_{f_1, ..., f_M} \sum_{i=1}^{M} p_i \cdot \ln\left(1 + f_i \cdot \frac{1 - q_i}{q_i}\right) + \left(1 - \sum_{i} p_i\right) \cdot \ln\left(1 - \sum_{i} f_i\right)$$

约束条件：
- $f_i \geq 0, \quad \forall i$（不做空）
- $\sum_i f_i \leq f_{max}$（总仓位上限）

#### 实践简化：Quarter-Kelly

为降低破产风险和模型误差的影响，使用 1/4 Kelly：

$$f_i^* = \frac{1}{4} \cdot \max\left(0, \frac{p_i - q_i}{1 - q_i}\right)$$

仅对 $p_i > q_i + \epsilon$ 的 bucket 建仓（$\epsilon$ 为最小 edge 阈值，建议 $\epsilon = 0.03$）。



---

## 4. 系统架构

### 4.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Tweet Oracle System                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐   │
│  │   Data Layer  │────▶│  Model Layer │────▶│  Signal & Exec   │   │
│  │              │     │              │     │     Layer         │   │
│  │ • XTracker   │     │ • NegBin Prior│     │ • Edge Calculator│   │
│  │ • Polymarket │     │ • Bayes Update│     │ • Kelly Sizer    │   │
│  │ • Historical │     │ • ρ(t) Curve │     │ • Order Manager  │   │
│  └──────┬───────┘     └──────┬───────┘     └────────┬─────────┘   │
│         │                    │                       │             │
│         ▼                    ▼                       ▼             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                      Storage Layer                            │  │
│  │  • PostgreSQL (历史数据)  • Redis (实时状态)  • Logs          │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Monitoring & Alerting                       │  │
│  │  • Scheduler (Cron)  • Health Check  • Telegram Alerts       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 模块职责

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| **DataFetcher** | 定时拉取 XTracker 和 Polymarket 数据 | API endpoints | raw data → DB |
| **PriorEstimator** | 从历史数据估计 NegBin 参数 | historical_counts | (r, p) |
| **IntensityCurve** | 构建/更新日内强度曲线 | tweet timestamps | λ(h), ρ(t) |
| **BayesUpdater** | 核心贝叶斯更新计算 | n_t, ρ(t), prior | posterior P(N=k|n_t) |
| **EdgeCalculator** | 对比模型概率与市场价 | P_m(Bi), P_mkt(Bi) | edge_vector |
| **PositionSizer** | Quarter-Kelly 仓位计算 | edge_vector, bankroll | position_vector |
| **OrderManager** | 下单/撤单/调仓 | position_vector | orders |
| **RiskGuard** | 风控检查，异常熔断 | all state | allow/block signal |
| **Scheduler** | 定时触发主循环 | cron config | trigger events |

### 4.3 技术栈选择

| 层级 | 技术选型 | 理由 |
|------|----------|------|
| **语言** | Python 3.11+ | 科学计算生态最好（scipy, numpy） |
| **执行层补充** | TypeScript (可选) | Polymarket SDK 对 JS 生态更好 |
| **数据库** | PostgreSQL 15 | 时序数据查询、窗口函数 |
| **缓存** | Redis 7 | 实时状态、当前计数、位置信息 |
| **调度** | APScheduler / Cron | 定时任务驱动 |
| **HTTP** | httpx (async) | 异步 HTTP 请求 |
| **数学** | scipy.stats, numpy | 负二项分布、优化器 |
| **告警** | Telegram Bot API | 实时通知交易信号和异常 |
| **部署** | Docker + VPS | 低延迟、7×24 运行 |

### 4.4 数据流

```
每 1 小时触发一次主循环：

1. DataFetcher.fetch_xtracker()     → n_t (当前推文计数)
2. DataFetcher.fetch_market_prices() → q_vector (各 bucket 市场价)
3. IntensityCurve.compute_rho(t)     → ρ (强度比例)
4. BayesUpdater.update(n_t, ρ)       → posterior (后验分布)
5. EdgeCalculator.compute(posterior, q_vector) → edges
6. RiskGuard.check(edges, state)     → approved_edges
7. PositionSizer.size(approved_edges) → target_positions
8. OrderManager.reconcile(current_positions, target_positions) → orders
9. OrderManager.execute(orders)      → fills
10. Logger.record(all_state)         → DB + Telegram
```



---

## 5. 数据层规范

### 5.1 数据源清单

| 数据源 | 用途 | API 端点 | 频率 |
|--------|------|----------|------|
| **XTracker** | 实时推文计数（结算依据） | `https://xtracker.polymarket.com/api/...` | 每 30-60 分钟 |
| **Polymarket CLOB API** | 订单簿、市场价格 | `https://clob.polymarket.com/` | 每 30-60 分钟 |
| **Polymarket Gamma API** | 市场元数据（bucket 定义、结算时间） | `https://gamma-api.polymarket.com/` | 每天 |
| **历史数据** | 参数估计、回测 | 自建数据库 | 每周更新 |

### 5.2 XTracker API 规范

XTracker 是 Polymarket 官方提供的社交媒体帖子追踪工具。

**文档地址**：https://xtracker.polymarket.com/docs

**关键端点**：
- 获取用户当前计数
- 获取指定时间窗口内的帖子数

**数据结构**（预期）：
```json
{
  "user": "elonmusk",
  "platform": "x",
  "window_start": "2026-05-12T12:00:00-04:00",
  "window_end": "2026-05-19T12:00:00-04:00",
  "current_count": 87,
  "last_updated": "2026-05-14T15:30:00Z"
}
```

### 5.3 Polymarket CLOB API 规范

**文档地址**：https://docs.polymarket.com/

**关键端点**：
- `GET /markets` — 获取市场列表
- `GET /book` — 获取订单簿
- `GET /prices` — 获取最新价格
- `POST /order` — 下单
- `DELETE /order` — 撤单

**价格数据结构**：
```json
{
  "market_id": "0x...",
  "token_id": "bucket_100_119_yes",
  "best_bid": 0.30,
  "best_ask": 0.32,
  "mid_price": 0.31,
  "last_trade": 0.31,
  "volume_24h": 125000.00
}
```

### 5.4 历史数据需求

需要收集并存储的历史数据：

| 数据类型 | 字段 | 时间范围 | 来源 |
|----------|------|----------|------|
| **周推文总数** | week_start, week_end, total_count | 最近 52 周 | XTracker 或手动回溯 |
| **每小时推文数** | timestamp, hourly_count | 最近 12 周 | XTracker 或 X API |
| **市场历史价格** | market_id, bucket, timestamp, price | 所有历史合约 | Polymarket API |
| **结算结果** | market_id, final_count, winning_bucket | 所有历史合约 | Polymarket |

### 5.5 数据库 Schema

```sql
-- 周度推文统计
CREATE TABLE weekly_tweet_counts (
    id SERIAL PRIMARY KEY,
    week_start TIMESTAMPTZ NOT NULL,
    week_end TIMESTAMPTZ NOT NULL,
    total_count INTEGER NOT NULL,
    account VARCHAR(50) DEFAULT 'elonmusk',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 小时级推文数据
CREATE TABLE hourly_tweet_counts (
    id SERIAL PRIMARY KEY,
    hour_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL,
    account VARCHAR(50) DEFAULT 'elonmusk',
    day_of_week INTEGER,  -- 0=Monday, 6=Sunday
    hour_of_day INTEGER   -- 0-23
);

-- 市场快照
CREATE TABLE market_snapshots (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR(100) NOT NULL,
    snapshot_time TIMESTAMPTZ NOT NULL,
    bucket_label VARCHAR(50) NOT NULL,  -- e.g. "100-119"
    bucket_low INTEGER NOT NULL,
    bucket_high INTEGER NOT NULL,
    yes_bid DECIMAL(6,4),
    yes_ask DECIMAL(6,4),
    yes_mid DECIMAL(6,4),
    volume DECIMAL(12,2)
);

-- 模型预测记录
CREATE TABLE model_predictions (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR(100) NOT NULL,
    prediction_time TIMESTAMPTZ NOT NULL,
    observed_count INTEGER NOT NULL,
    rho DECIMAL(6,4) NOT NULL,
    bucket_label VARCHAR(50) NOT NULL,
    model_prob DECIMAL(6,4) NOT NULL,
    market_price DECIMAL(6,4) NOT NULL,
    edge DECIMAL(6,4) NOT NULL,
    position_size DECIMAL(8,4)
);

-- 交易记录
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR(100) NOT NULL,
    bucket_label VARCHAR(50) NOT NULL,
    side VARCHAR(10) NOT NULL,  -- 'BUY' or 'SELL'
    price DECIMAL(6,4) NOT NULL,
    size DECIMAL(12,2) NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL,
    order_id VARCHAR(100),
    pnl DECIMAL(12,2)  -- 结算后回填
);
```

### 5.6 Redis 状态结构

```
# 当前周期实时状态
tweet_oracle:current_market_id        → "0x..."
tweet_oracle:current_count            → 87
tweet_oracle:current_rho              → 0.412
tweet_oracle:window_start             → "2026-05-12T16:00:00Z"
tweet_oracle:window_end               → "2026-05-19T16:00:00Z"
tweet_oracle:last_update              → "2026-05-14T15:30:00Z"

# 当前持仓
tweet_oracle:positions:100-119        → {"size": 150, "avg_price": 0.28}
tweet_oracle:positions:120-139        → {"size": 200, "avg_price": 0.25}

# 模型参数
tweet_oracle:params:r                 → 8.5
tweet_oracle:params:p                 → 0.94
tweet_oracle:params:state             → "medium"
```



---

## 6. 模型层规范

### 6.1 参数估计模块

#### 输入
- `weekly_counts: List[int]` — 过去 W 周的周推文总数

#### 输出
- `r: float` — 负二项分布形状参数
- `p: float` — 负二项分布成功概率参数
- `mu: float` — 均值
- `var: float` — 方差

#### 算法

```python
# 伪代码
def estimate_negbin_params(weekly_counts, method='mle'):
    """
    MLE 估计负二项参数
    
    负二项 PMF: P(X=k) = C(k+r-1, k) * (1-p)^r * p^k
    均值 mu = r*p/(1-p)
    方差 sigma^2 = r*p/(1-p)^2
    """
    # 矩估计作为初始值
    mu = mean(weekly_counts)
    var = variance(weekly_counts)
    r_init = mu^2 / (var - mu)  # 若 var > mu
    p_init = mu / var
    
    # MLE 精确求解
    result = scipy.optimize.minimize(
        neg_log_likelihood,
        x0=[r_init, p_init],
        bounds=[(0.01, 100), (0.001, 0.999)]
    )
    return result.r, result.p
```

#### 滑动窗口

- **短期窗口**（12 周）：捕捉近期行为模式变化
- **长期窗口**（52 周）：提供稳定的基准
- **加权组合**：`r_final = 0.7 * r_short + 0.3 * r_long`

#### 马尔可夫状态先验（增强）

根据上周结果选择条件参数：

```python
# 状态定义
STATES = {
    'low':    (0, 119),
    'medium': (120, 179),
    'high':   (180, float('inf'))
}

# 转移矩阵（从历史数据估计）
TRANSITION = {
    'low':    {'low': 0.61, 'medium': 0.28, 'high': 0.11},
    'medium': {'low': 0.25, 'medium': 0.50, 'high': 0.25},
    'high':   {'low': 0.10, 'medium': 0.30, 'high': 0.60}
}

# 根据上周状态选择条件参数
def get_conditional_prior(last_week_count, params_by_state):
    state = classify_state(last_week_count)
    return params_by_state[state]  # (r, p) for this state
```

### 6.2 强度曲线模块

#### 输入
- `hourly_data: DataFrame` — 历史每小时推文数（含 day_of_week, hour_of_day）

#### 输出
- `intensity_curve: Array[168]` — 一周 168 小时的归一化强度

#### 算法

```python
def build_intensity_curve(hourly_data):
    """
    构建 168 小时的强度曲线
    """
    # 按 (day_of_week, hour_of_day) 分组取均值
    curve = np.zeros(168)
    for dow in range(7):  # 0=Mon, 6=Sun
        for hod in range(24):
            idx = dow * 24 + hod
            subset = hourly_data[
                (hourly_data.day_of_week == dow) & 
                (hourly_data.hour_of_day == hod)
            ]
            curve[idx] = subset['count'].mean()
    
    # 平滑（避免噪声）
    curve = gaussian_smooth(curve, sigma=2)
    
    # 归一化为累积比例函数
    cumulative = np.cumsum(curve)
    rho_curve = cumulative / cumulative[-1]
    
    return curve, rho_curve
```

#### ρ(t) 的实时计算

```python
def compute_rho(current_time, window_start, rho_curve):
    """
    计算当前时刻对应的强度比例 ρ
    """
    hours_elapsed = (current_time - window_start).total_seconds() / 3600
    hour_index = int(hours_elapsed)
    
    if hour_index >= 168:
        return 1.0
    if hour_index < 0:
        return 0.0
    
    # 线性插值
    frac = hours_elapsed - hour_index
    if hour_index < 167:
        rho = rho_curve[hour_index] * (1 - frac) + rho_curve[hour_index + 1] * frac
    else:
        rho = rho_curve[hour_index]
    
    return rho
```

### 6.3 贝叶斯更新模块

#### 输入
- `n_t: int` — 已观察推文数
- `rho: float` — 强度比例
- `r, p: float` — 负二项参数
- `k_max: int` — 截断上限（默认 500）

#### 输出
- `posterior: Array[k_max+1]` — P(N=k | n_t) 对 k=0,...,k_max

#### 算法

```python
def bayesian_update(n_t, rho, r, p, k_max=500):
    """
    核心贝叶斯更新
    
    P(N=k|n_t) ∝ Binom(n_t; k, ρ) × NegBin(k; r, p)
    """
    log_posterior = np.full(k_max + 1, -np.inf)
    
    for k in range(n_t, k_max + 1):
        # log-likelihood: log Binom(n_t; k, ρ)
        log_lik = (
            log_comb(k, n_t) 
            + n_t * np.log(rho) 
            + (k - n_t) * np.log(1 - rho)
        )
        
        # log-prior: log NegBin(k; r, p)
        log_prior = negbin_logpmf(k, r, p)
        
        log_posterior[k] = log_lik + log_prior
    
    # 归一化 (log-sum-exp trick)
    log_norm = logsumexp(log_posterior[n_t:])
    posterior = np.exp(log_posterior - log_norm)
    posterior[:n_t] = 0  # k < n_t 不可能
    
    return posterior
```

#### 数值稳定性注意事项

1. **使用 log 空间**：避免组合数导致的浮点溢出
2. **log-sum-exp trick**：归一化时使用
3. **截断上限**：$k_{max} = \mu + 5\sigma$ 足够，通常 500 以内
4. **边界检查**：当 $\rho$ 接近 0 或 1 时需要特殊处理

### 6.4 Bucket 概率聚合

```python
def compute_bucket_probs(posterior, buckets):
    """
    将后验分布聚合到各 bucket
    
    buckets: List[Tuple[int, int]]  例如 [(0,79), (80,99), (100,119), ...]
    """
    probs = {}
    for (lo, hi) in buckets:
        hi_capped = min(hi, len(posterior) - 1)
        probs[(lo, hi)] = posterior[lo:hi_capped+1].sum()
    
    return probs
```



---

## 7. 信号与执行层

### 7.1 Edge 计算

```python
def compute_edges(model_probs, market_prices, min_edge=0.03):
    """
    计算每个 bucket 的 edge
    
    model_probs: Dict[bucket_label, float]  — 模型概率
    market_prices: Dict[bucket_label, float] — 市场 YES 价格（ask）
    min_edge: float — 最小 edge 阈值
    
    返回：仅包含有交易价值的 bucket
    """
    signals = {}
    for bucket, p_model in model_probs.items():
        p_market = market_prices[bucket]
        edge = p_model - p_market
        
        if edge > min_edge:
            signals[bucket] = {
                'edge': edge,
                'p_model': p_model,
                'p_market': p_market,
                'direction': 'BUY_YES',
                'confidence': edge / p_model  # edge 占模型概率的比例
            }
        elif edge < -min_edge:
            # 考虑买 NO（等价于卖 YES）
            signals[bucket] = {
                'edge': abs(edge),
                'p_model': 1 - p_model,  # NO 的模型概率
                'p_market': 1 - p_market,  # NO 的市场价
                'direction': 'BUY_NO',
                'confidence': abs(edge) / (1 - p_model)
            }
    
    return signals
```

### 7.2 仓位计算

```python
def compute_positions(signals, bankroll, max_total_exposure=0.30):
    """
    Quarter-Kelly 仓位计算
    
    signals: 来自 compute_edges 的输出
    bankroll: 当前总资金
    max_total_exposure: 总仓位上限占 bankroll 比例
    """
    positions = {}
    total_allocation = 0
    
    # 按 edge 大小排序，优先分配给 edge 最大的 bucket
    sorted_signals = sorted(
        signals.items(), 
        key=lambda x: x[1]['edge'], 
        reverse=True
    )
    
    for bucket, signal in sorted_signals:
        p = signal['p_model']
        q = signal['p_market']
        
        # Quarter-Kelly 公式
        kelly_fraction = 0.25 * (p - q) / (1 - q)
        
        # 单 bucket 上限：10% bankroll
        kelly_fraction = min(kelly_fraction, 0.10)
        
        # 检查总仓位上限
        if total_allocation + kelly_fraction > max_total_exposure:
            kelly_fraction = max_total_exposure - total_allocation
            if kelly_fraction <= 0:
                break
        
        dollar_size = kelly_fraction * bankroll
        
        positions[bucket] = {
            'direction': signal['direction'],
            'dollar_size': dollar_size,
            'kelly_fraction': kelly_fraction,
            'target_price': q  # 以市场价挂单
        }
        
        total_allocation += kelly_fraction
    
    return positions
```

### 7.3 订单执行规范

#### 下单策略

| 参数 | 规范 |
|------|------|
| **订单类型** | Limit Order（限价单） |
| **挂单价格** | `best_ask - $0.01`（作为 maker 避免 taker 费） |
| **有效期** | Good Till Cancel（GTC） |
| **最大滑点** | 2 cents（超过则放弃） |
| **分批下单** | 单笔 > $500 时分 3-5 批下入 |
| **间隔** | 分批间隔 30-60 秒 |

#### 调仓逻辑

```python
def reconcile_positions(current_positions, target_positions):
    """
    比较当前持仓与目标持仓，生成调仓指令
    """
    orders = []
    
    for bucket, target in target_positions.items():
        current = current_positions.get(bucket, {'dollar_size': 0})
        delta = target['dollar_size'] - current['dollar_size']
        
        if abs(delta) < 50:  # 小于 $50 不调仓（避免频繁小单）
            continue
        
        if delta > 0:
            orders.append({
                'bucket': bucket,
                'side': 'BUY',
                'token': 'YES' if target['direction'] == 'BUY_YES' else 'NO',
                'amount': delta,
                'max_price': target['target_price'] + 0.02  # 最大滑点
            })
        else:
            orders.append({
                'bucket': bucket,
                'side': 'SELL',
                'token': 'YES' if target['direction'] == 'BUY_YES' else 'NO',
                'amount': abs(delta),
                'min_price': target['target_price'] - 0.02
            })
    
    return orders
```

### 7.4 执行时间表

```
周一 12:00 ET   — 新合约开始，建初始仓位（基于纯先验）
周一-周日       — 每 1-2 小时更新一次（非活跃时段可延长到 4 小时）
周五起          — 加密更新频率到每 30 分钟（临近截止信息量最大）
截止前 6 小时    — 每 15 分钟更新（此时后验非常集中，edge 最大）
截止前 1 小时    — 停止新建仓位（流动性枯竭风险）
```



---

## 8. 仓位管理与风控

### 8.1 风控参数表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MAX_SINGLE_BUCKET` | 10% bankroll | 单 bucket 最大仓位 |
| `MAX_TOTAL_EXPOSURE` | 30% bankroll | 所有 bucket 总仓位上限 |
| `MIN_EDGE_THRESHOLD` | 0.03 (3%) | 最小 edge 才触发交易 |
| `MAX_SLIPPAGE` | $0.02 | 超过此滑点放弃执行 |
| `MIN_ORDER_SIZE` | $50 | 最小下单金额 |
| `KELLY_FRACTION` | 0.25 | Kelly 缩放因子 |
| `MAX_DAILY_LOSS` | 5% bankroll | 日亏损熔断线 |
| `MAX_WEEKLY_LOSS` | 10% bankroll | 周亏损熔断线 |
| `CONFIDENCE_FLOOR` | 0.15 | edge/p_model 的最低比例 |

### 8.2 熔断机制

```python
class RiskGuard:
    def check_all(self, state) -> Tuple[bool, str]:
        """
        返回 (is_allowed, reason)
        """
        checks = [
            self.check_daily_loss(state),
            self.check_weekly_loss(state),
            self.check_data_freshness(state),
            self.check_model_sanity(state),
            self.check_market_anomaly(state),
        ]
        
        for passed, reason in checks:
            if not passed:
                return False, reason
        
        return True, "all checks passed"
    
    def check_daily_loss(self, state):
        """日亏损超过 5% 停止交易"""
        daily_pnl = state.get_daily_pnl()
        if daily_pnl < -state.bankroll * 0.05:
            return False, f"Daily loss limit hit: {daily_pnl}"
        return True, ""
    
    def check_data_freshness(self, state):
        """XTracker 数据超过 2 小时未更新则暂停"""
        last_update = state.get_last_xtracker_update()
        if (now() - last_update) > timedelta(hours=2):
            return False, "XTracker data stale"
        return True, ""
    
    def check_model_sanity(self, state):
        """模型输出的概率分布总和必须接近 1"""
        total_prob = sum(state.model_probs.values())
        if abs(total_prob - 1.0) > 0.05:
            return False, f"Model probs sum to {total_prob}"
        return True, ""
    
    def check_market_anomaly(self, state):
        """市场价格总和偏离 1 超过 5% 为异常"""
        total_market = sum(state.market_prices.values())
        if abs(total_market - 1.0) > 0.05:
            return False, f"Market prices sum to {total_market}"
        return True, ""
```

### 8.3 极端场景处理

| 场景 | 触发条件 | 处理方式 |
|------|----------|----------|
| **Musk 账号异常** | 12h 无推文 or 突然删除大量帖子 | 暂停交易，保持当前仓位 |
| **XTracker 宕机** | API 返回错误超过 3 次 | 切换到备用数据源或暂停 |
| **市场流动性枯竭** | 最佳买卖价差 > $0.10 | 暂停该 bucket 交易 |
| **模型概率极端集中** | 单 bucket > 90% | 限制该 bucket 仓位为 15% |
| **闪崩 / 闪涨** | 价格 5 分钟内变动 > 20% | 等待 15 分钟后重新评估 |
| **结算争议** | UMA 预言机被挑战 | 不新建仓位，等待争议解决 |

### 8.4 资金管理

#### 建议资金配置

| 资金规模 | 每周可投入 | 单 Bucket 上限 | 预期周收益 |
|----------|-----------|---------------|-----------|
| $5,000 | $1,500 | $500 | $50-150 (1-3%) |
| $20,000 | $6,000 | $2,000 | $200-600 |
| $50,000 | $15,000 | $5,000 | $500-1,500 |

#### 盈利再投资

- 首 8 周：固定 bankroll，不加仓（验证期）
- 验证通过后：每月将利润的 50% 加入 bankroll
- bankroll 翻倍后：考虑分散到其他 Tweet Markets（Trump 等）



---

## 9. 回测框架

### 9.1 回测目标

在历史数据上模拟策略运行，验证：
1. 模型预测的校准度（calibration）
2. 策略的盈利能力和风险特征
3. 参数敏感性

### 9.2 回测方法论

#### Walk-Forward 回测（非 In-Sample）

```
对于每一个历史合约 c = 1, 2, ..., C:
    1. 用 c 之前的数据估计模型参数（out-of-sample）
    2. 模拟 c 的交易周期：
        - 每 1 小时步进一次
        - 用当时的 n_t 和 ρ(t) 做贝叶斯更新
        - 用当时的历史市场价做 edge 计算
        - 模拟 Quarter-Kelly 建仓/调仓
    3. 结算后计算 PnL
```

#### 关键简化假设

| 假设 | 合理性 |
|------|--------|
| 以 mid-price 成交 | 略乐观，实际会有价差 |
| 无滑点 | 小资金下可接受 |
| 忽略 Gas 费 | Polygon 上极低 |
| Taker 费 2% | 保守估计 |

### 9.3 评估指标

| 指标 | 公式 / 说明 | 目标 |
|------|-------------|------|
| **总收益率** | Σ PnL / 初始资金 | > 0 |
| **周胜率** | 盈利周数 / 总周数 | > 55% |
| **Sharpe Ratio** | mean(weekly_return) / std(weekly_return) × √52 | > 1.5 |
| **最大回撤** | max peak-to-trough | < 15% |
| **Profit Factor** | 总盈利 / 总亏损 | > 1.5 |
| **平均 Edge** | mean(model_prob - market_price) on winning trades | > 0.05 |
| **Brier Score** | mean((forecast - outcome)^2) across all buckets | < 市场 Brier |
| **Calibration** | 对于模型说 30% 的事件，实际发生率 ≈ 30% | 斜率 ≈ 1.0 |

### 9.4 Brier Score 对比

这是评估概率预测质量的黄金标准：

$$\text{Brier Score} = \frac{1}{N} \sum_{t=1}^{N} \sum_{i=1}^{M} (p_{t,i} - o_{t,i})^2$$

其中 $o_{t,i} = 1$ 如果 bucket $i$ 在第 $t$ 周是获胜 bucket，否则 = 0。

**对比**：
- 计算你的模型的 Brier Score
- 计算市场价格的 Brier Score（市场也在做预测）
- 如果 `Brier_model < Brier_market`，说明你的模型比市场更准确

### 9.5 敏感性分析

需要扫描的参数空间：

| 参数 | 扫描范围 | 含义 |
|------|----------|------|
| 历史窗口 W | 8, 12, 20, 30, 52 周 | 先验估计的数据量 |
| Kelly 缩放 | 0.10, 0.15, 0.20, 0.25, 0.33 | 风险偏好 |
| Min Edge | 0.02, 0.03, 0.05, 0.08 | 交易频率 vs 质量 |
| ρ 方法 | uniform, empirical, weekday-adjusted | 强度曲线精度 |
| Markov 状态 | 无, 2态, 3态, 5态 | 先验的条件化程度 |
| 更新频率 | 30min, 1h, 2h, 4h | 交易频率 |

### 9.6 过拟合防范

- **小样本警告**：Tweet Markets 从 2024.6 开始，最多 ~100 个历史合约
- **简单模型优先**：负二项 + 贝叶斯更新 只有 2-3 个自由参数
- **交叉验证**：使用 Walk-Forward，绝不 look-ahead
- **参数稳定性**：如果最优参数在相邻区间差异巨大 → 过拟合信号
- **外样本验证**：保留最近 8 周不参与参数选择



---

## 10. 部署与运维

### 10.1 基础设施

| 组件 | 选型 | 规格 | 费用/月 |
|------|------|------|---------|
| **VPS** | Hetzner / DigitalOcean | 2 vCPU, 4GB RAM | $15-20 |
| **数据库** | PostgreSQL (同 VPS) | 20GB SSD | 含在 VPS |
| **Redis** | 同 VPS | 512MB | 含在 VPS |
| **域名** | 可选（用于监控面板） | — | $10/年 |
| **Telegram Bot** | 告警通知 | 免费 | $0 |
| **总计** | — | — | ~$20/月 |

### 10.2 部署方式

```yaml
# docker-compose.yml
version: '3.8'
services:
  app:
    build: .
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/tweet_oracle
      - REDIS_URL=redis://redis:6379
      - POLYMARKET_API_KEY=${POLYMARKET_API_KEY}
      - PRIVATE_KEY=${PRIVATE_KEY}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
    depends_on:
      - db
      - redis
    restart: always
  
  db:
    image: postgres:15
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=tweet_oracle
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
  
  redis:
    image: redis:7-alpine
    
volumes:
  pgdata:
```

### 10.3 安全规范

| 类别 | 措施 |
|------|------|
| **私钥管理** | 环境变量注入，不硬编码。使用独立交易钱包，不放大额资金 |
| **API 密钥** | 环境变量，`.env` 文件 gitignore |
| **钱包隔离** | 交易钱包与存储钱包分离，交易钱包仅保留 2 周资金 |
| **权限最小化** | 交易 API key 只赋予 trade 权限，不给 withdraw |
| **异常登录告警** | VPS 设置 fail2ban + SSH key only |

### 10.4 监控告警

#### Telegram 通知触发条件

| 级别 | 条件 | 示例消息 |
|------|------|----------|
| 🟢 INFO | 正常交易执行 | "Bought YES 120-139 @ $0.28, size $200" |
| 🟡 WARN | 数据延迟、滑点偏高 | "XTracker data 90min stale" |
| 🔴 ALERT | 熔断触发、API 错误 | "DAILY LOSS LIMIT HIT. Trading halted." |
| 🔵 REPORT | 每日/每周汇总 | "Weekly PnL: +$340, Sharpe: 2.1" |

#### 每日报告模板

```
📊 Tweet Oracle Daily Report
━━━━━━━━━━━━━━━━━━━━━━━━
📅 Date: 2026-05-14 (Day 3/7)
📝 Current Count: 87 tweets
📈 ρ(t): 0.412
━━━━━━━━━━━━━━━━━━━━━━━━
📊 Model vs Market:
  80-99:   Model 5%  | Mkt 4%   | Edge +1%
  100-119: Model 28% | Mkt 32%  | Edge -4%
  120-139: Model 35% | Mkt 28%  | Edge +7% ✅
  140-159: Model 22% | Mkt 24%  | Edge -2%
  160-179: Model 8%  | Mkt 9%   | Edge -1%
  180+:    Model 2%  | Mkt 3%   | Edge -1%
━━━━━━━━━━━━━━━━━━━━━━━━
💰 Positions:
  120-139 YES: $400 @ avg $0.26
  140-159 YES: $150 @ avg $0.23
━━━━━━━━━━━━━━━━━━━━━━━━
📈 Unrealized PnL: +$52
🏦 Bankroll: $5,052 / $5,000
```

### 10.5 日常运维清单

| 频率 | 任务 |
|------|------|
| **每小时（自动）** | 主循环执行：数据拉取 → 更新 → 交易 |
| **每日（人工 5min）** | 查看 Telegram 日报，确认无异常 |
| **每周一 12:00 ET** | 新合约开始，确认市场 ID 更新正确 |
| **每周日（自动）** | 结算后记录 PnL，更新参数 |
| **每月** | 回顾月度表现，决定是否调整参数 |
| **每季度** | 检查策略 edge 是否衰减（Brier Score 趋势） |



---

## 11. 开发路线图

### Phase 0：调研验证（1 周）

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 手动收集 Musk 过去 52 周的周推文数 | CSV 数据文件 | 数据完整无缺失 |
| 拟合负二项分布，画图对比 | 分布拟合报告 | KS 检验 p > 0.05 |
| 对当前活跃市场手动做一次贝叶斯更新 | 计算笔记 | 模型概率 vs 市场价有可见偏差 |
| 确认 XTracker API 可访问性和数据格式 | API 测试脚本 | 能稳定获取数据 |
| 确认 Polymarket CLOB API 鉴权和下单流程 | 测试交易（$1） | 成功下单并撤单 |

**Phase 0 的目的：在写任何正式代码之前，验证核心假设是否成立。**

---

### Phase 1：数据层（1-2 周）

| 任务 | 详情 |
|------|------|
| 实现 XTracker 数据拉取模块 | 定时获取当前推文计数 |
| 实现 Polymarket 价格拉取模块 | 获取所有 bucket 的 bid/ask/mid |
| 搭建 PostgreSQL 数据库 | 建表、索引、初始数据导入 |
| 历史数据回填 | 从 XTracker/第三方获取历史周数据 |
| 构建小时级数据管道 | 持续记录每小时推文数 |

**验收**：能稳定跑 3 天不中断地收集数据。

---

### Phase 2：模型层（2 周）

| 任务 | 详情 |
|------|------|
| 实现 NegBin 参数估计 | MLE + 矩估计初始化 |
| 实现强度曲线构建 | 168 小时归一化强度 |
| 实现贝叶斯更新核心函数 | 含 log 空间计算、数值稳定性 |
| 实现 Bucket 概率聚合 | 后验 → 各 bucket 概率 |
| 单元测试 | 已知输入的期望输出对比 |
| 手动验证 | 对 2-3 个历史合约做事后"回放" |

**验收**：模型在已知历史数据上产出合理的概率预测（Brier Score < 市场）。

---

### Phase 3：回测（1-2 周）

| 任务 | 详情 |
|------|------|
| 实现 Walk-Forward 回测引擎 | 按小时步进，模拟交易 |
| 费用模型 | 加入 taker fee、滑点模拟 |
| 参数扫描 | 历史窗口、Kelly 缩放、min_edge |
| 结果分析 | Sharpe、回撤、胜率、Brier |
| 决策：是否继续 | 如果 Sharpe < 1.0 → 复盘改进或放弃 |

**验收**：回测 Sharpe > 1.5，最大回撤 < 15%，且参数对结果不过度敏感。

---

### Phase 4：执行层（1-2 周）

| 任务 | 详情 |
|------|------|
| 实现 Edge 计算和 Kelly 仓位 | 信号 → 目标持仓 |
| 实现订单管理 | 下单、撤单、调仓、状态追踪 |
| 实现风控模块 | 熔断、限额、异常检测 |
| Polymarket SDK 集成 | 签名、认证、WebSocket |
| 纸上交易（Paper Trading）| 跑完整流程但不真实下单 |

**验收**：Paper Trading 跑 2 周，信号合理，订单逻辑正确。

---

### Phase 5：小资金实盘（2-4 周）

| 任务 | 详情 |
|------|------|
| 实盘部署 | Docker + VPS + 监控 |
| 初始资金 | $500-$1,000（可承受亏损） |
| 密切监控 | 每日检查 Telegram 报告 |
| 记录实际 vs 预期偏差 | 滑点、费用、延迟 |
| 逐步增加资金 | 盈利 4 周后翻倍 |

**验收**：实盘 4 周累计盈利，且 PnL 与回测预期在 1 个标准差以内。

---

### Phase 6：优化与扩展（持续）

| 任务 | 详情 |
|------|------|
| 马尔可夫状态先验 | 加入上周状态条件化 |
| 事件因子数据库 | 构建事件 → 影响系数映射 |
| 多账号扩展 | Trump tweets, 其他热门账号 |
| Adjacent Bucket 扫描器 | 作为附加免费策略 |
| 自适应参数 | 检测模式漂移，自动调参 |

---

### 时间线总结

```
Week 1:      Phase 0 — 调研验证
Week 2-3:    Phase 1 — 数据层
Week 4-5:    Phase 2 — 模型层
Week 6-7:    Phase 3 — 回测
Week 8-9:    Phase 4 — 执行层 + Paper Trading
Week 10-13:  Phase 5 — 小资金实盘
Week 14+:    Phase 6 — 优化迭代

总计首次上线时间：约 9-10 周
```



---

## 附录

### A. 核心公式速查卡

```
┌─────────────────────────────────────────────────────────────────┐
│                    TWEET ORACLE FORMULA CARD                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. 先验分布                                                     │
│     N ~ NegBin(r, p)                                             │
│     μ = rp/(1-p),  σ² = rp/(1-p)²                               │
│                                                                  │
│  2. 强度比例                                                     │
│     ρ(t) = Λ(t) / Λ(T) = Σ λ(h) [0→t] / Σ λ(h) [0→T]         │
│                                                                  │
│  3. 后验更新 ★                                                   │
│     P(N=k|n_t) ∝ C(k,n_t) · ρ^n_t · (1-ρ)^(k-n_t) · f_NB(k)  │
│     (k ≥ n_t)                                                    │
│                                                                  │
│  4. Bucket 概率                                                  │
│     P(Bi) = Σ P(N=k|n_t)  for k in [a_i, b_i]                  │
│                                                                  │
│  5. Edge                                                         │
│     edge_i = P_model(Bi) - P_market(Bi)                         │
│                                                                  │
│  6. Quarter-Kelly 仓位                                           │
│     f_i = (1/4) · max(0, (p_i - q_i) / (1 - q_i))              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### B. 关键数据源 URL

| 资源 | URL | 用途 |
|------|-----|------|
| XTracker 首页 | https://xtracker.polymarket.com/ | 实时推文计数 |
| XTracker API 文档 | https://xtracker.polymarket.com/docs | API 接入 |
| Polymarket CLOB 文档 | https://docs.polymarket.com/ | 交易 API |
| Polymarket WebSocket | docs.polymarket.com/market-data/websocket | 实时价格 |
| Gamma API | https://gamma-api.polymarket.com/ | 市场元数据 |
| PredictPM | https://www.predictpm.com/ | 第三方免费监控 |
| Tweet Markets 页面 | https://polymarket.com/predictions/tweets-markets | 当前活跃合约 |

### C. 参考资料

1. **Polymarket "Tweet Quant"** — Polymarket 官方分析文章，揭示 Musk 发推的马尔可夫转移性质  
   https://news.polymarket.com/p/tweet-quant

2. **noovd 交易记录** — 验证了 Tweet Markets 存在可持续 edge 的实盘案例（$345K 利润）  
   https://www.binance.com/en/square/post/293258794826450

3. **OpenClaw polymarket-elon-tweets** — 开源的 Adjacent Bucket 策略实现  
   https://playbooks.com/skills/openclaw/skills/polymarket-elon-tweets

4. **PolySwarm (arxiv 2604.03888)** — 多智能体 LLM 预测市场交易框架  
   https://arxiv.org/abs/2604.03888

5. **Polymarket/agents** — 官方 AI Agent 框架（2.1K Stars）  
   https://github.com/Polymarket/agents

6. **Kelly Criterion for Multiple Mutually Exclusive Outcomes**  
   https://vegapit.com/article/kelly-criterion-multiple-mutually-exclusive-outcomes/

7. **Application of the Kelly Criterion to Prediction Markets**  
   https://arxiv.org/html/2412.14144v1

8. **On Optimal Betting Strategies with Multiple Mutually Exclusive Outcomes** (Wiley, 2024)  
   https://onlinelibrary.wiley.com/doi/full/10.1111/boer.12474

### D. 术语表

| 术语 | 解释 |
|------|------|
| **Bucket** | 数量区间合约，如 "100-119 tweets" |
| **YES/NO** | 二元合约的两面，YES + NO = $1.00 |
| **CLOB** | Central Limit Order Book，中央限价订单簿 |
| **XTracker** | Polymarket 官方社交媒体帖子追踪工具 |
| **Edge** | 模型概率 - 市场价格，正值为交易机会 |
| **Kelly Criterion** | 最大化对数增长率的最优下注比例 |
| **Quarter-Kelly** | Kelly 公式的 1/4，更保守的仓位管理 |
| **Brier Score** | 概率预测准确度的评估指标，越低越好 |
| **Walk-Forward** | 向前滚动的样本外回测方法 |
| **NegBin** | 负二项分布，处理过度离散计数数据的标准分布 |
| **ρ(t)** | 强度比例，已过去时间占总强度的累积比例 |
| **UMA Oracle** | Polymarket 使用的乐观预言机结算系统 |
| **Thinning Property** | 泊松过程分裂性质：条件分布为二项分布 |

### E. 风险声明

1. **本文档仅供研究和教育目的**，不构成投资建议
2. 预测市场交易存在本金全损风险
3. 模型可能因 Musk 行为模式突变而失效
4. Polymarket 运营在监管灰色地带，存在平台风险
5. 智能合约风险：虽然极低但不为零
6. 过去的回测结果不代表未来表现

---

*文档结束*
