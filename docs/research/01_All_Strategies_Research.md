# 同交易所 BTC/ETH 对冲策略开发思路（研究参考）

> ⚠️ **重要提示：本文档为早期研究记录，包含多种策略，其中大部分不适用于 MemeMax**
>
> MemeMax 是**纯永续合约 DEX**，不提供：
> - ❌ 现货市场（排除：现货+合约对冲、资金费率套利的现货端、期现基差）
> - ❌ 期权（排除：波动率套利）
> - ❌ 交割合约（排除：基差套利）
>
> **本文 5 个策略中，只有「策略二：配对交易」可以在 MemeMax 上落地。**
> 实际采用的方案请见 [`docs/01_MAIN_Strategy.md`](../01_MAIN_Strategy.md)。
>
> 本文保留是为了：
> 1. 记录决策过程，说明为什么最终选择配对交易
> 2. 如果未来 MemeMax 增加现货/期权，可以重新启用其他策略
> 3. 学习参考价值（部分思路可借鉴）

---

## 一、背景分析

BTC和ETH是加密市场中相关性最高的两大资产（历史相关性通常在0.85-0.95之间），但它们之间的价格比率（ETH/BTC ratio）会在不同时期出现显著偏离。利用这种偏离和回归特性，可以在**同一交易所内**构建多种对冲策略。

核心原则：**保本优先、对冲方向性风险、赚取结构性收益**。

---

## 二、GitHub优质开源项目参考

| 项目 | 核心思路 | 链接 |
|------|---------|------|
| **QuantConnect/Research** | 基于协整的配对交易策略研究 | [GitHub](https://github.com/QuantConnect/Research) |
| **AndyTKH/Cointegration-Crypto** | 加密货币协整配对交易的均值回归策略 | [GitHub](https://github.com/AndyTKH/Cointegration-Crypto) |
| **anthonyli01/Statistical-Arbitrage-Pairs-Trading-Strategy** | 协整+Kalman滤波+Copula+ML多方法配对交易 | [GitHub](https://github.com/anthonyli01/Statistical-Arbitrage-Pairs-Trading-Strategy) |
| **CryptoWizardsNet/dydx-trading-bot** (53⭐) | dYdX上的协整统计套利实盘Bot | [GitHub](https://github.com/CryptoWizardsNet/dydx-trading-bot) |
| **aoki-h-jp/funding-rate-arbitrage** | 资金费率套利框架（支持多CEX） | [GitHub](https://github.com/aoki-h-jp/funding-rate-arbitrage) |
| **hummingbot/hummingbot** (18k⭐) | 高频交易Bot框架，内置资金费率套利+跨交易所做市策略 | [GitHub](https://github.com/hummingbot/hummingbot) |
| **IrakliXYZ/ARBOT** | 现货-期货资金费率套利Bot（15-30% APY） | [GitHub](https://github.com/IrakliXYZ/ARBOT) |
| **Go1vf/Statistical-Arbitrage-Strategy** | Avellaneda-Lee统计套利在40种加密货币中的实现 | [GitHub](https://github.com/Go1vf/Statistical-Arbitrage-Strategy) |
| **nialeksandrov/hse-crypto-statistical-arbitrage** | Kalman Filter更新策略的加密货币统计套利 | [GitHub](https://github.com/nialeksandrov/hse-crypto-statistical-arbitrage) |
| **freqtrade/freqtrade** (高星) | 开源加密货币交易框架，支持自定义策略 | [GitHub](https://github.com/freqtrade/freqtrade) |

---

## 三、核心策略思路（由低风险到高收益排序）

---

### 策略一：资金费率套利（Funding Rate Arbitrage）⭐ 最稳健

**原理：**
永续合约每隔8小时收取/支付一次资金费率(Funding Rate)。当多头情绪过热时，资金费率为正，做空方可以收取费用。

**同交易所操作：**
```
BTC策略：买入1 BTC现货 + 做空1 BTC永续合约（Delta中性）
ETH策略：买入X ETH现货 + 做空X ETH永续合约（Delta中性）
```

**增强版本 - BTC/ETH双币轮动：**
1. 实时监控BTC和ETH的资金费率
2. 将资金分配给**资金费率更高**的那个币种
3. 当BTC funding rate > ETH时 → 资金侧重BTC的现货-合约对冲
4. 当ETH funding rate > BTC时 → 资金侧重ETH的现货-合约对冲
5. 动态再平衡

**预期收益：** 年化8-30%（取决于市场情绪）
**风险点：** 资金费率可能转负、穿仓风险（需控制杠杆）、现货和合约价格偏离（基差风险）

**参考实现：** [aoki-h-jp/funding-rate-arbitrage](https://github.com/aoki-h-jp/funding-rate-arbitrage)

---

### 策略二：BTC/ETH Ratio 均值回归配对交易 ⭐⭐ 核心推荐

**原理：**
ETH/BTC的价格比率（ratio）具有均值回归特性。当ratio偏离均值达到一定阈值时，做多低估方+做空高估方。

**核心步骤：**

#### Step 1: 协整性检验
```python
# Engle-Granger两步法检验BTC和ETH是否协整
from statsmodels.tsa.stattools import coint

score, p_value, _ = coint(btc_prices, eth_prices)
# p_value < 0.05 → 存在协整关系
```

#### Step 2: 计算对冲比率（Hedge Ratio）

**方法A - OLS回归（静态）：**
```python
# ETH_price = beta * BTC_price + alpha + epsilon
# beta就是对冲比率
import numpy as np
beta = np.polyfit(btc_prices, eth_prices, 1)[0]
```

**方法B - Kalman滤波（动态，推荐）：**
```python
# Kalman Filter动态更新hedge ratio
# 优势：不需要窗口参数，自动适应市场状态变化
delta = 0.0001
Vw = delta / (1 - delta) * np.eye(2)  # 状态转移噪声
Ve = 0.001  # 观测噪声

# 状态向量：[alpha, beta]
# 观测方程：ETH_price = alpha + beta * BTC_price + noise
```

#### Step 3: 构建价差（Spread）
```python
spread = eth_prices - beta * btc_prices
# 或使用Z-Score标准化
z_score = (spread - spread.mean()) / spread.std()
```

#### Step 4: 交易信号
```
开仓做多spread：z_score < -2.0（ETH相对BTC被低估）
    → 做多ETH永续合约 + 做空BTC永续合约（按beta调整仓位）

开仓做空spread：z_score > 2.0（ETH相对BTC被高估）
    → 做空ETH永续合约 + 做多BTC永续合约

平仓：z_score回归到[-0.5, 0.5]区间
止损：z_score > 3.5 或 < -3.5（结构性突破）
```

#### Step 5: 仓位管理
```
1 BTC名义价值 = BTC_price × 1
对应ETH名义价值 = BTC_price × beta
ETH数量 = (BTC_price × beta) / ETH_price
```

**预期收益：** 年化15-50%（取决于波动率和参数优化）
**风险点：** 协整关系可能断裂（ETH独立行情）、极端行情下spread不回归

**参考实现：** 
- [AndyTKH/Cointegration-Crypto](https://github.com/AndyTKH/Cointegration-Crypto)
- [QuantConnect Research](https://github.com/QuantConnect/Research)
- [Kalman Filter Pairs Trading](https://bookdown.org/palomar/portfoliooptimizationbook/15.6-kalman-pairs-trading.html)

---

### 策略三：复合策略 - 配对交易 + 资金费率增强 ⭐⭐⭐ 最优方案

**核心思路：在配对交易的基础上，叠加资金费率收益**

```
场景：z_score > 2.0（做空ETH/BTC spread）

操作拆解：
├── 做空ETH：
│   ├── 方案A（纯合约）：开空ETH永续 → 若ETH funding > 0，额外获得资金费
│   └── 方案B（现货+合约）：借入ETH卖出（若有借贷功能）
│
└── 做多BTC：
    ├── 方案A（纯合约）：开多BTC永续 → 若BTC funding > 0，需支付资金费
    └── 方案B（现货+合约）：买入BTC现货 + 避免支付资金费

最优组合：
若 ETH_funding > 0 且 BTC_funding > 0：
    → 空ETH合约（收资金费）+ 多BTC现货（不付资金费）
    → 获得spread回归收益 + ETH资金费 + 节省BTC资金费
```

**策略增强逻辑：**
1. 计算spread信号（配对交易主信号）
2. 检查资金费率方向（辅助决策）
3. 如果资金费率与spread方向一致 → 加大仓位（顺风局）
4. 如果资金费率与spread方向相反 → 减小仓位或观望

---

### 策略四：波动率差异套利

**原理：**
BTC和ETH的隐含波动率/实际波动率之间存在溢价差异。

**操作思路：**
```
1. 计算BTC和ETH近期实际波动率（Realized Volatility）
2. 计算两者的波动率比 vol_ratio = vol_ETH / vol_BTC
3. 当vol_ratio显著偏高时 → ETH波动率将回落相对于BTC
   做空ETH永续（赚波动率溢价）+ 做多BTC永续（对冲方向性风险）
4. 仓位按照beta-neutral原则配比
```

---

### 策略五：期现基差套利（Basis Trading）

**同交易所内操作：**
```
BTC方面：
- 买入BTC现货 + 卖出BTC季度交割合约
- 当基差 > 阈值时建仓，等待交割日收敛

ETH方面：
- 监控ETH基差
- 在BTC和ETH之间选择基差更大的进行套利

动态切换：
- 每日比较BTC_basis vs ETH_basis
- 资金always分配给基差更大（年化收益更高）的品种
```

---

## 四、技术实现框架建议

```
project/
├── config/
│   ├── exchange_config.yaml     # 交易所API配置
│   └── strategy_params.yaml     # 策略参数
├── data/
│   ├── market_data.py           # 行情数据获取（WebSocket）
│   ├── funding_rate.py          # 资金费率数据
│   └── historical_data.py       # 历史数据下载
├── strategy/
│   ├── cointegration.py         # 协整检验模块
│   ├── kalman_filter.py         # Kalman滤波器
│   ├── spread_calculator.py     # 价差计算 & Z-Score
│   ├── signal_generator.py      # 信号生成
│   ├── funding_optimizer.py     # 资金费率优化
│   └── position_sizer.py        # 仓位管理
├── execution/
│   ├── order_manager.py         # 下单管理
│   ├── risk_manager.py          # 风险控制
│   └── portfolio_tracker.py     # 组合跟踪
├── backtest/
│   ├── backtester.py            # 回测引擎
│   └── performance_metrics.py   # 绩效分析
├── monitor/
│   ├── dashboard.py             # 实时监控面板
│   └── alert.py                 # 异常告警
└── main.py                      # 主程序入口
```

---

## 五、风险控制要点（保本核心）

### 1. 仓位管理
| 规则 | 说明 |
|------|------|
| 单策略最大敞口 | ≤ 总资金的30% |
| 单方向最大净敞口 | ≤ 总资金的10%（beta不完美对冲部分） |
| 最大杠杆 | ≤ 3x |
| 总持仓保证金 | ≤ 总资金的50% |

### 2. 止损规则
```
- Spread止损：z_score超过3.5个标准差 → 强制平仓
- 单笔亏损限制：≤ 总资金的2%
- 日亏损限制：≤ 总资金的5%
- 协整断裂检测：滚动窗口p_value > 0.1 → 暂停策略
```

### 3. 特殊风险处理
```
- 极端行情：ETH独立暴跌（如FTX事件），需设置绝对止损
- 流动性风险：大单拆分执行，避免滑点
- 交易所风险：保留20%资金作为保证金缓冲
- Funding异常：资金费率极端异常时（>0.3%）减仓
- API风控：订单频率控制，异常断连自动平仓
```

---

## 六、策略选择建议

| 风险偏好 | 推荐策略 | 预期年化 | 最大回撤 |
|----------|---------|---------|---------|
| 极低风险 | 纯资金费率套利 | 8-15% | <2% |
| 低风险 | 资金费率+基差轮动 | 12-25% | <5% |
| 中等风险 | BTC/ETH配对交易（Kalman） | 15-40% | <10% |
| 中高风险 | 配对交易+资金费率增强 | 20-50% | <15% |

---

## 七、推荐开发路径

```
Phase 1（1-2周）：数据基础
  → 接入交易所API，获取BTC/ETH的K线、深度、资金费率
  → 构建历史数据库

Phase 2（1-2周）：策略回测
  → 实现协整检验 + Kalman Filter
  → 回测BTC/ETH配对交易策略
  → 优化参数（窗口、阈值、止损）

Phase 3（1周）：资金费率模块
  → 实现资金费率监控和轮动逻辑
  → 与配对交易策略融合

Phase 4（1周）：风控 & 执行
  → 实现风险管理模块
  → 实现下单和仓位管理

Phase 5（持续）：实盘 & 优化
  → 小资金实盘测试
  → 监控和参数调优
```

---

## 八、关键参考资料

- [Pairs Trading Crypto Perpetuals (Substack)](https://delphicalpha.substack.com/p/pairs-trading-crypto-perpetuals-the) - 数学推导完整
- [QuantConnect Kalman Filter Stat Arb](https://www.quantconnect.com/docs/v2/research-environment/applying-research/kalman-filters-and-stat-arb) - 官方教程
- [Kalman Filter Pairs Trading Book](https://bookdown.org/palomar/portfoliooptimizationbook/15.6-kalman-pairs-trading.html) - 理论教材
- [PANews: 资金费率套利揭秘](https://www.panewslab.com/en/articles/arx014s2htg1) - 资金费率套利深度分析
- [Binance Funding Rate Arbitrage Bot](https://www.binance.com/en/blog/tech/3611863022773164727) - 币安官方资金费率套利Bot

---

*Content was rephrased for compliance with licensing restrictions. Sources linked inline.*
