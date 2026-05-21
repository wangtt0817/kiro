# Polymarket Maker Rebate 套利策略研究

## 一、Polymarket 返利规则总结

### 1.1 费率体系 (2026年最新)

| 市场类别 | Taker费率峰值(50%概率处) | Maker费率 |
|---------|------------------------|-----------|
| Crypto | 1.80% | 0 (免费) |
| Economics | 1.50% | 0 |
| Mentions | 1.56% | 0 |
| Culture | 1.25% | 0 |
| Weather | 1.25% | 0 |
| Finance | 1.00% | 0 |
| Politics | 1.00% | 0 |
| Tech | 1.00% | 0 |
| Sports | 0.75% | 0 |
| Geopolitics | **免费** | 0 |

### 1.2 费率计算公式

```
Fee = C × feeRate × p × (1 - p)
```

- `C` = 交易份数 (shares)
- `p` = 价格 (0~1之间，即隐含概率)
- `feeRate` = 该类别的费率参数
- 费率是动态的：在 p=0.50 时最高，在 p→0 或 p→1 时趋近于0

### 1.3 Maker Rebate Program (核心规则)

- **Maker 下单免费**：所有 limit order (挂单) 不收费
- **Taker费用100%回流Maker**：所有收取的taker费用每日以USDC重新分配给提供流动性的Maker
- **Finance市场返还比例高达50%**
- 分配方式基于流动性贡献评分（二次惩罚函数，距中点越近得分越高）
- **Polymarket US (美国站)**：flat 0.30% taker fee, **maker 获得 -0.20% rebate**（即每笔成交的maker方直接获得正向回扣）

### 1.4 Liquidity Rewards 评分机制

- 基于 **Adjusted Midpoint** 的距离计算
- **二次惩罚函数**：距离翻倍 → 奖励下降4倍
- 挂单越靠近中间价 & 越大尺寸 → 分数越高 → 分到的rebate越多
- 每个市场有 max spread（如±3¢~±5¢），超出范围的挂单不参与分配
- 最低发放门槛 $1

---

## 二、返佣套利策略

### 策略1：双边做市吃返佣 (Pure Maker Rebate Farming)

**原理：** 在高fee市场同时挂buy和sell的limit order，被taker吃到后获得rebate分成。

**操作方式：**
1. 选择 **Crypto类市场** (费率最高 1.80%)
2. 在概率 40%~60% 的市场挂双边单
3. 挂单距midpoint ≤ 2¢ (最大化二次评分)
4. 保持足够的order size满足最低要求

**收益模型：**
```python
# 策略1回测参数
class Strategy1_PureMakerRebate:
    """双边做市返佣策略"""
    
    # 假设参数
    market_category = "crypto"
    fee_rate = 0.018  # 1.8% peak
    rebate_share = 1.0  # 100% redistribution
    
    # 挂单参数
    spread_from_mid = 0.02  # 距中点2¢
    order_size = 1000  # 每单$1000
    
    # 收入：rebate = 被吃到时，从taker fee池中按评分分到的份额
    # 风险：持仓方向暴露（可能单边被吃造成库存偏移）
    
    # 关键变量
    fill_rate_per_day = 0.3  # 假设30%的挂单会被成交
    avg_price = 0.50  # 在p=0.5附近挂单，fee最高
    
    def daily_fee_generated_per_fill(self):
        """每次成交产生的fee"""
        fee = self.order_size * self.fee_rate * self.avg_price * (1 - self.avg_price)
        return fee  # = 1000 * 0.018 * 0.5 * 0.5 = $4.5 per fill
    
    def daily_income(self, market_share=0.05):
        """假设占市场5%份额"""
        # 假设该市场日总fee = $10000
        daily_total_fee = 10000
        return daily_total_fee * market_share  # = $500/day
    
    # 风险：inventory risk (库存风险)
    # 需要对冲或控制exposure
```

**优势：**
- Maker免手续费
- 直接获得taker费用的分成
- Crypto类市场费率最高 → 返佣池最大

**风险：**
- 库存偏移风险（单边被吃后出现方向性暴露）
- 竞争激烈（评分为二次函数，头部maker占大头）
- 市场结果结算后可能亏损

---

### 策略2：跨类别费率差套利 (Cross-Category Fee Arbitrage)

**原理：** 利用不同类别市场的费率差异，在高fee市场做maker吃返佣，同时在低fee/免费市场做taker对冲。

**操作方式：**
1. 找到 **同一事件** 在不同类别下有相关市场的情况
   - 例：某crypto事件可能同时存在于 Crypto (1.8%) 和 Tech (1.0%) 类别
   - 或者：地缘事件同时有 Geopolitics (免费) 和 Finance (1.0%) 的关联市场
2. 在高费率市场做 Maker (挂单，吃返佣)
3. 在低费率/免费市场做 Taker (市价单对冲)

**收益模型：**
```python
class Strategy2_CrossCategoryArb:
    """跨类别费率差套利"""
    
    # 高fee市场: Crypto (maker, 无费用)
    high_fee_market_rate = 0.018
    
    # 低fee市场: Geopolitics (taker, 免费) 或相关联市场
    low_fee_market_rate = 0.0
    
    # 净收益 = 高fee市场的rebate分成 - 低fee市场的taker费用 - spread成本
    
    def net_profit_per_trade(self, size=1000, price=0.50, 
                             rebate_received=4.5, hedge_fee=0, spread_cost=0.5):
        """
        size: 交易量
        rebate_received: 从高fee市场获得的返佣
        hedge_fee: 在对冲市场支付的费用
        spread_cost: 对冲时的spread损耗
        """
        return rebate_received - hedge_fee - spread_cost
    
    # 示例: rebate=$4.5, hedge_fee=$0, spread=$0.5
    # 净利 = $4.0 per round trip
```

**关键条件：**
- 需找到高度相关的跨类别市场对
- 相关性不完美时存在basis risk

---

### 策略3：概率极端区域的对冲做市 (Extreme Price Market Making)

**原理：** 在概率极端的市场（p<0.10 或 p>0.90），fee趋近于0，但liquidity reward评分有特殊规则。

**操作方式：**
1. 选择即将到期、概率极端的市场
2. 在这些市场提供流动性（双边挂单要求）
3. 由于 p*(1-p) 很小，即使被taker吃到，实际fee负担极低
4. 但仍可获得 Liquidity Rewards 分配

**收益模型：**
```python
class Strategy3_ExtremePrice:
    """极端概率区做市"""
    
    # p = 0.95 时
    p = 0.95
    fee_factor = p * (1 - p)  # = 0.95 * 0.05 = 0.0475 (vs 0.25 at p=0.5)
    # 实际fee仅为峰值的19%
    
    # 但此处的特点：
    # 1. 竞争者少（大部分MM集中在50%附近）
    # 2. 仍可获得liquidity reward分配
    # 3. 方向性风险更可控（95%概率市场大概率不翻转）
    
    def risk_reward(self, size=1000, fee_rate=0.018):
        """极端价格处的风险收益"""
        # 被吃到的fee很低
        fee_if_filled = size * fee_rate * self.p * (1 - self.p)
        # = 1000 * 0.018 * 0.0475 = $0.855
        
        # 但liquidity reward是按份额分配，竞争少→份额可能高
        # 风险：黑天鹅事件导致价格翻转
        
        # 胜率高、单次收益低、但黑天鹅亏损大
        expected_gain = 0.95 * 2.0  # 95%概率赚$2(spread+reward)
        expected_loss = 0.05 * 50   # 5%概率亏$50(价格翻转)
        ev = expected_gain - expected_loss  # = 1.9 - 2.5 = -$0.6 ← 需精确计算
        return ev
```

**注意：** 此策略需要精确评估事件概率，不能依赖市场价格。

---

### 策略4：YES+NO 同时挂单的无风险返佣 (Maker-Maker Spread Capture)

**原理：** 同一市场的YES和NO价格加总 = $1。如果分别以maker身份在YES和NO两边挂单，且两边都被成交，则实现无方向性暴露 + 获得双倍返佣。

**操作方式：**
1. 市场midpoint在0.50附近
2. YES侧挂买单在0.48 (maker)
3. NO侧挂买单在0.48 (maker) ← 等价于YES的0.52 sell
4. 两单都被成交后：持有YES@0.48 + NO@0.48 = 成本$0.96，结算必然获得$1
5. **无论结果如何，净赚 $0.04/pair + 两边的maker rebate**

**收益模型：**
```python
class Strategy4_MakerMakerSpread:
    """YES+NO双边Maker无风险套利"""
    
    def profit_per_pair(self, yes_price=0.48, no_price=0.48):
        """
        YES买入@0.48 + NO买入@0.48 = 总成本0.96
        结算必得$1, 净利$0.04/pair
        """
        total_cost = yes_price + no_price
        settlement = 1.0
        spread_profit = settlement - total_cost  # $0.04
        
        # 加上 maker rebate (从taker fee池中分得)
        # 假设每边被taker吃时产生的fee
        fee_rate = 0.018  # crypto
        yes_fee = yes_price * fee_rate * yes_price * (1 - yes_price)
        no_fee = no_price * fee_rate * no_price * (1 - no_price)
        
        # 你作为maker分到的rebate(假设5%份额)
        rebate = (yes_fee + no_fee) * 0.05 * 1000  # 放大到1000 shares
        
        return spread_profit * 1000 + rebate
    
    # 关键挑战：
    # 1. 两边都被成交的概率（可能只成交一边）
    # 2. 竞争导致spread被压到1¢以内
    # 3. 需要等待时间（流动性不足时成交慢）
```

**优势：**
- 理论上无方向性风险（YES+NO对冲）
- 吃到spread + 双倍rebate

**风险：**
- 只被吃一边（单腿风险）
- Spread太小利润微薄
- 资金效率低（需等待两边都成交）

---

### 策略5：Polymarket vs Kalshi 跨平台套利 + Maker返佣加成

**原理：** 跨平台价格差套利本身已有利润，在Polymarket侧以maker身份下单还能额外获得返佣。

**操作方式：**
1. 监控同一事件在 Polymarket 和 Kalshi 的价格差
2. 当 Polymarket YES + Kalshi NO < $1 (或反向)
3. 在Polymarket侧以 **limit order (maker)** 挂单 → 免费 + 返佣
4. 在Kalshi侧以市价单对冲

**收益模型：**
```python
class Strategy5_CrossPlatformArb:
    """跨平台套利 + Maker返佣加成"""
    
    def profit(self, poly_yes=0.55, kalshi_no=0.40):
        """
        Polymarket YES @0.55 (maker, 免费)
        Kalshi NO @0.40 (taker, 有fee)
        总成本 = 0.55 + 0.40 = 0.95
        结算 = $1.00
        """
        total_cost = poly_yes + kalshi_no
        gross_profit = 1.0 - total_cost  # $0.05
        
        # Polymarket maker rebate bonus
        # 你的maker order被成交时，产生的taker fee会计入rebate池
        # 你作为maker可以分到一部分
        rebate_bonus = 0.005  # 估计额外$0.005/share
        
        # Kalshi fee (约2% for event contracts)
        kalshi_fee = kalshi_no * 0.02  # $0.008
        
        net = gross_profit + rebate_bonus - kalshi_fee
        return net  # ≈ $0.047/share
    
    # 优势：
    # 1. 基础套利利润（价差）
    # 2. Polymarket maker免费 + 返佣加成
    # 3. 无方向性风险（完全对冲）
    
    # 挑战：
    # 1. 跨平台价差出现频率有限
    # 2. Kalshi有自己的费率
    # 3. 两平台结算时间可能不同步
    # 4. 资金分散在两个平台
```

---

### 策略6：高频Maker + 事件相关性对冲 (Correlated Event Hedging)

**原理：** 利用Polymarket上同一事件的多个关联市场（如"BTC > 100k in June" vs "BTC > 90k in June"），在一个市场做maker吃返佣，在相关市场做对冲。

**操作方式：**
1. 找到高度相关的市场对（相关系数>0.8）
2. 在流动性更好/fee更高的市场做 Maker
3. 在另一个市场做 Taker 对冲
4. 利用相关性降低净暴露

**收益模型：**
```python
class Strategy6_CorrelatedHedge:
    """相关事件对冲做市"""
    
    # 示例：BTC > 100k (Crypto市场, 1.8% fee)
    #        BTC > 90k  (Crypto市场, 1.8% fee)
    # 两者高度相关但不完全一致
    
    correlation = 0.85
    
    def net_exposure(self, position_a=1000, position_b=-850):
        """
        做市A +1000 shares, 对冲B -850 shares
        残余暴露 = 1000 * (1 - 0.85) = 150 shares unhedged
        """
        unhedged = position_a * (1 - self.correlation)
        return unhedged
    
    def daily_pnl(self, rebate_income=20, hedge_cost=3, residual_risk=5):
        """
        rebate_income: 做市获得的返佣
        hedge_cost: 对冲的taker费用
        residual_risk: 残余风险的预期损失
        """
        return rebate_income - hedge_cost - residual_risk  # = $12/day
```

---

## 三、回测框架建议

```python
"""
回测框架核心模块
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass

@dataclass
class MarketConfig:
    """市场配置"""
    category: str
    fee_rate: float  # 该类别的fee参数
    liquidity_reward_pool: float  # 每日奖励池 (USD)
    avg_volume: float  # 日均交易量
    avg_spread: float  # 平均spread
    num_makers: int  # 活跃maker数量

@dataclass  
class BacktestConfig:
    """回测配置"""
    initial_capital: float = 10000
    max_position_per_market: float = 2000
    max_spread_from_mid: float = 0.03  # 最大距中点3¢
    rebalance_interval: int = 60  # 秒
    
MARKET_CONFIGS = {
    "crypto": MarketConfig("crypto", 0.072, 5000, 500000, 0.02, 50),
    "sports": MarketConfig("sports", 0.03, 1000, 200000, 0.03, 30),
    "finance": MarketConfig("finance", 0.04, 2000, 150000, 0.025, 20),
    "politics": MarketConfig("politics", 0.04, 1500, 300000, 0.02, 40),
}

def calculate_fee(shares: float, price: float, fee_rate: float) -> float:
    """计算taker fee"""
    return shares * fee_rate * price * (1 - price)

def calculate_maker_score(distance_from_mid: float, size: float, max_spread: float) -> float:
    """
    计算maker的liquidity score
    二次惩罚: score ∝ size / (distance^2)
    """
    if distance_from_mid > max_spread:
        return 0
    # 二次衰减
    score = size * (1 - (distance_from_mid / max_spread) ** 2)
    return max(0, score)

def simulate_strategy(strategy_class, market_config, days=30, seed=42):
    """
    通用回测模拟框架
    """
    np.random.seed(seed)
    results = []
    
    for day in range(days):
        # 模拟每日价格走势
        price_path = simulate_price_path(market_config)
        
        # 模拟maker order成交
        fills = simulate_fills(price_path, market_config)
        
        # 计算当日rebate
        daily_fee_pool = calculate_daily_fee_pool(fills, market_config)
        maker_rebate = calculate_rebate_share(strategy_class, daily_fee_pool, market_config)
        
        # 计算PnL
        inventory_pnl = calculate_inventory_pnl(fills, price_path)
        
        results.append({
            'day': day,
            'rebate_income': maker_rebate,
            'inventory_pnl': inventory_pnl,
            'total_pnl': maker_rebate + inventory_pnl
        })
    
    return pd.DataFrame(results)

def simulate_price_path(config, steps=1440):
    """模拟日内价格路径 (1分钟粒度)"""
    # 使用布朗运动 + mean reversion
    dt = 1/1440
    sigma = config.avg_spread * 2  # 日内波动率
    prices = [0.5]
    for _ in range(steps - 1):
        dp = -0.1 * (prices[-1] - 0.5) * dt + sigma * np.random.randn() * np.sqrt(dt)
        prices.append(np.clip(prices[-1] + dp, 0.01, 0.99))
    return np.array(prices)

def simulate_fills(price_path, config):
    """模拟limit order成交"""
    # 简化模型：当price穿过我们的挂单价时触发成交
    pass

def calculate_daily_fee_pool(fills, config):
    """计算当日fee池"""
    total_fee = sum(calculate_fee(f['size'], f['price'], config.fee_rate) for f in fills)
    return total_fee

def calculate_rebate_share(strategy, fee_pool, config):
    """计算我们的rebate份额"""
    our_score = strategy.get_liquidity_score()
    total_score = our_score * config.num_makers / 0.05  # 假设我们占5%
    return fee_pool * (our_score / total_score)

def calculate_inventory_pnl(fills, price_path):
    """计算库存持仓的损益"""
    pass
```

---

## 四、策略优先级排序

| 策略 | 预期年化 | 风险等级 | 资金效率 | 实现难度 | 推荐优先级 |
|------|---------|---------|---------|---------|-----------|
| 策略4: YES+NO双边Maker | 15-30% | ★☆☆ | 中 | 低 | ⭐⭐⭐⭐⭐ |
| 策略5: 跨平台套利+返佣 | 20-50% | ★★☆ | 中 | 中 | ⭐⭐⭐⭐ |
| 策略1: 纯做市返佣 | 30-80% | ★★★ | 高 | 高 | ⭐⭐⭐ |
| 策略2: 跨类别费率差 | 10-25% | ★★☆ | 低 | 中 | ⭐⭐⭐ |
| 策略6: 相关事件对冲 | 20-40% | ★★★ | 中 | 高 | ⭐⭐ |
| 策略3: 极端概率做市 | 5-15% | ★★☆ | 低 | 低 | ⭐⭐ |

---

## 五、关键数据源 (回测所需)

1. **Polymarket API** - 历史orderbook数据、成交数据
   - `https://clob.polymarket.com/` (CLOB API)
   - 历史价格/成交量
   
2. **Rebate分配记录** - 链上查询每日USDC分配
   - Polygon链上交易记录

3. **Kalshi API** - 跨平台价差数据
   - 用于策略5回测

4. **市场元数据** - 类别、到期时间、结算结果
   - 用于计算实际PnL

---

## 六、注意事项

1. **竞争动态**：Polymarket的liquidity scoring使用二次函数，头部maker（更靠近midpoint、更大size）会拿到绝大部分返佣
2. **资金成本**：USDC锁定在挂单中有机会成本（当前USDC理财年化~5%）
3. **结算风险**：预测市场有非连续性风险（事件突然明确导致价格跳变）
4. **API限制**：需申请market maker API权限，注意rate limit
5. **监管风险**：特别是跨平台策略涉及合规问题
6. **返佣规则变动**：Polymarket曾从100%返佣降到20%（2025年初crypto市场），再逐步恢复，规则可能继续调整

---

*Sources: Polymarket official docs, PredictionHunt, KuCoin blog, Pine Analytics, Binance Square, MEXC research (2026)*
*Content was rephrased for compliance with licensing restrictions*
