# 波动率套利需要的完整工具链（不适用于 MemeMax）

> 🚫 **本策略在 MemeMax 上无法实施**
>
> 波动率套利的核心工具是**期权**（Options），用于直接交易波动率。
> MemeMax 是纯永续合约（PERP）DEX，**不提供期权产品**，因此本策略不可用。
>
> 如果未来想做波动率套利，需要使用 [Deribit](https://www.deribit.com/)（占加密期权市场 85% 份额）等期权交易所。
>
> 本文保留作为知识库，介绍：
> - 波动率套利的核心原理（IV vs RV、跨资产 vol spread）
> - 需要的工具链（DVOL 指数、Greeks 计算、Delta hedging bot）
> - 为什么 MemeMax 上做不了（关键缺失：Vega/Gamma/Theta 没有载体）
>
> 当前 MemeMax 上**有效**的策略请见 [`docs/01_MAIN_Strategy.md`](../01_MAIN_Strategy.md)。

---

## 概述

波动率套利与方向性策略有本质区别——它交易的是**波动率本身**，而非价格方向。这需要一套专门的工具和基础设施。

---

## 一、交易所选择（必须有期权）

| 交易所 | 期权市场份额 | BTC/ETH 期权流动性 | 适合程度 |
|--------|------------|------------------|---------|
| **Deribit** | **85% 市场份额**（绝对霸主）| BTC OI $30B+, ETH 深度好 | ⭐⭐⭐⭐⭐ 首选 |
| OKX | ~5% | BTC/ETH 期权有，流动性一般 | ⭐⭐⭐ |
| Binance | ~3% | 欧式 USDT 期权，品种有限 | ⭐⭐ |
| CME | ~7% | BTC 日均 > $1B，机构级 | ⭐⭐⭐⭐（限美国/合规机构） |
| Bybit | 少量 | 新兴，流动性有限 | ⭐⭐ |

> **关键结论**：Deribit 是唯一能做 BTC/ETH 跨资产波动率套利的现实选择，它同时提供两者的深度期权市场。

来源：[TradeAlgo](https://www.tradealgo.com/trading-guides/crypto/crypto-options-trading)、[FXEmpire](https://www.fxempire.com/exchanges/best/options)

---

## 二、核心工具层级

### 🔧 Level 1：数据工具（监控波动率）

| 工具 | 用途 | 链接 |
|------|------|------|
| **Deribit DVOL 指数** | 30 天隐含波动率指数（类似 VIX），BTC 和 ETH 各一个 | [Deribit API](https://docs.deribit.com/subscriptions/market-data/deribit_volatility_indexindex_name) |
| **Glassnode 期权套件** | 40+ 期权指标，覆盖 Deribit/OKX/Bybit | [Glassnode](https://blockchain.news/news/glassnode-quadruples-options-metrics-40-tool-derivatives-suite) |
| **CryptoGamma.io** | 实时 Gamma Exposure、Skew 分析、Put/Call 比率 | [cryptogamma.io](https://cryptogamma.io/) |
| **HyBlock DVOL API** | Deribit 隐含波动率情绪数据 | [HyBlock](https://docs.hyblockcapital.com/dvol) |
| **nostoz/deribit_volatility** | 下载和可视化 Deribit 波动率数据（Python） | [GitHub](https://github.com/nostoz/deribit_volatility_download_and_visualize) |

### 🔧 Level 2：定价与分析工具

| 工具 | 用途 |
|------|------|
| **Black-Scholes / Black-76 模型** | 计算理论期权价格、提取隐含波动率（IV） |
| **GARCH(1,1) 模型** | 预测未来实际波动率（RV），用于判断 IV vs RV 偏离 |
| **Greeks 计算器** | Delta、Gamma、Vega、Theta — 仓位风险管理核心 |
| **波动率曲面（Vol Surface）构建** | 不同行权价和到期日的 IV 分布 → 寻找定价异常 |
| **SABR 模型** | 更精确的波动率微笑拟合 |

### 🔧 Level 3：执行与对冲工具

| 工具 | 用途 | 参考 |
|------|------|------|
| **Delta Hedge Bot** | 自动再平衡 Delta 至中性 | [schepal/delta_hedge](https://github.com/schepal/delta_hedge) |
| **Deribit API (Python)** | 下单、查询持仓、WebSocket 行情 | [deribit-api-python](https://github.com/kramer65/deribit-api-python)、[AntonioVentilii/deribit-wrapper](https://github.com/AntonioVentilii/deribit-wrapper) |
| **CCXT 库** | 统一多交易所 API 访问（含 Deribit） | [ccxt/ccxt](https://github.com/ccxt/ccxt) |
| **NautilusTrader** | 专业量化框架，内置 Deribit 集成 | [nautilustrader.io](https://nautilustrader.io/docs/latest/integrations/deribit) |

---

## 三、BTC vs ETH 波动率套利的具体操作方式

### 方式 A：跨资产 Vega 中性交易（核心方法）

```
原理：
- ETH IV 历史上约比 BTC IV 高 15-25 个点（结构性溢价）
- 当这个溢价偏离均值时 → 交易均值回归

操作：
┌──────────────────────────────────────────────┐
│ ETH_IV - BTC_IV 的 spread（"Vol Spread"）     │
│                                              │
│ 正常区间：15-25 vol points                    │
│ 偏高（>30）→ 卖 ETH 波动率 + 买 BTC 波动率    │
│ 偏低（<10）→ 买 ETH 波动率 + 卖 BTC 波动率    │
└──────────────────────────────────────────────┘

具体头寸：
卖 ETH 波动率 = 卖出 ETH Straddle（卖 Call + 卖 Put，同行权价）
买 BTC 波动率 = 买入 BTC Straddle（买 Call + 买 Put，同行权价）

仓位匹配规则：Vega-neutral
  → ETH Vega = BTC Vega（等量 Vega 敞口）
  → 确保只赚 vol spread 收敛，不暴露单腿风险
```

### 方式 B：单资产 IV vs RV 套利

```
原理：
- 当 IV > RV（隐含波动率高于实际波动率）→ 卖期权（收取时间价值）
- 当 IV < RV（隐含波动率低于实际波动率）→ 买期权（低价买入波动率）

操作（以 BTC 为例）：
┌────────────────────────────────────────────┐
│ GARCH 预测 30 天 RV = 45%                   │
│ 当前 BTC DVOL（30 天 IV）= 60%              │
│ 差值 = 15% → IV 显著高估                    │
│                                            │
│ → 卖出 BTC ATM Straddle                     │
│ → 同时 Delta 对冲（买/卖 BTC 期货保持 Delta=0）│
│ → 赚取 Theta（时间衰减）- Gamma 损失         │
│ → 如果 RV 确实 < IV，策略盈利                 │
└────────────────────────────────────────────┘
```

### 方式 C：波动率曲面套利（进阶）

```
原理：不同到期日/行权价的 IV 之间存在定价不一致

示例：
- BTC 1 周到期 ATM IV = 55%
- BTC 1 月到期 ATM IV = 50%
- 正常期限结构应该是短期 < 长期（远期不确定性更大）
- 当短期 IV 异常高于长期 → 卖短期 + 买长期（Calendar Spread）

操作：
  → 卖出 BTC 近月 Straddle + 买入 BTC 远月 Straddle
  → 赚取短期高 IV 回落 + 长期低 IV 上升
```

---

## 四、完整开发技术栈

```python
# 所需 Python 库
pip install ccxt           # 交易所统一 API
pip install py_vollib      # Black-Scholes 定价、Greeks 计算
pip install arch           # GARCH 模型
pip install numpy scipy    # 数值计算
pip install pandas         # 数据处理
pip install websockets     # Deribit WebSocket 实时行情
pip install deribit-api    # Deribit 专用 SDK（或用 ccxt）

# 关键模块
1. vol_surface_builder.py    # 构建波动率曲面
2. iv_rv_analyzer.py         # IV vs RV 偏离检测
3. cross_asset_vol_spread.py # BTC-ETH vol spread 监控
4. greeks_calculator.py      # Greeks 实时计算
5. delta_hedger.py           # 自动 Delta 对冲
6. position_manager.py       # 多腿期权仓位管理
7. risk_monitor.py           # Vega/Gamma/Theta 敞口监控
```

---

## 五、为什么说"没有期权工具基本不可行"

| 没有期权时的替代方案 | 问题 |
|---|---|
| 用期货模拟"卖波动率" | 无法锁定收益，频繁 rebalance 成本极高 |
| 用涨跌幅度判断波动率方向 | 只能做方向性赌博，不是真正的 vol arb |
| 用 BTC/ETH ratio 波动率做交易 | 退化为配对交易，不是波动率套利 |

**期权提供的不可替代性：**
- **Vega**：直接交易波动率的"量"
- **Gamma**：从价格波动中获利/亏损
- **Theta**：时间价值衰减（卖方赚钱来源）
- **非线性收益**：期权提供凸性保护，期货无法实现

---

## 六、落地可行性评估

| 条件 | 是否满足 |
|------|---------|
| 需要期权交易所 | ✅ Deribit（85% 市场份额） |
| 需要 BTC + ETH 期权 | ✅ Deribit 同时有 |
| 需要足够流动性 | ⚠️ BTC 期权 OK，ETH 期权中等偏弱 |
| 需要 DVOL 指数数据 | ✅ Deribit 提供 API |
| 需要 Greeks 计算能力 | ✅ py_vollib 等库 |
| 需要自动 Delta 对冲 | ✅ GitHub 有开源 Bot |
| 需要高频再平衡 | ⚠️ Deribit API 频率限制需注意 |
| 最低资金量 | $20k+（期权最小面值 + 保证金需求） |
| 开发难度 | ⭐⭐⭐⭐⭐（最高） |

---

## 总结

**波动率套利需要的核心工具 = 期权交易所（Deribit）+ Greeks 计算 + Delta 对冲 Bot + 波动率预测模型**

推荐落地路径：
1. 先在 Deribit 开通测试网账户（testnet）
2. 用 API 下载 BTC/ETH 期权链数据
3. 构建 IV vs RV 分析框架
4. 先做单资产（BTC）的 vol arb 练手
5. 再扩展到 BTC-ETH 跨资产 vol spread

> **对于 MemeMax 这类纯永续合约 DEX，波动率套利策略不适用。**

---

*Content was rephrased for compliance with licensing restrictions.*
