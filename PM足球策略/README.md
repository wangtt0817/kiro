# PM足球策略

Polymarket 足球预测市场系统化交易策略包 (v5 Final)

## 策略总览 (9 个)

### ✅ 推荐策略 (6 个)

| 策略 | 类型 | PnL | ROI% | PF | 特点 |
|------|------|-----|------|-----|------|
| **S25_DynamicSpread** | 动态做市 | +$515 | 5.15% | 99.9 | 根据波动率+距开赛时间动态调价 |
| **S6_Aggressive** | 做市 | +$689 | 6.89% | 99.9 | Kelly 0.5, max 5%, 价格<25% |
| **S22_Combo** | 做市+趋势 | +$679 | 6.79% | 5.93 | 90% 胜率, 低相关性 |
| **S6_Conservative** | 做市 | +$461 | 4.61% | 99.9 | Kelly 0.25, max 2%, 价格<20% |
| **S24_MultiWindow** | 趋势 | +$214 | 2.14% | 1.99 | 3/5/7/14d 四窗口全同向 |
| **S11_Trend** | 趋势 | +$156 | 1.56% | 1.58 | 3d/5d/7d 多时间框架 |

### ⚠️ 实验策略 (2 个)

| 策略 | 类型 | PnL | 问题 |
|------|------|-----|------|
| S20_DynamicKelly | 动态Kelly | +$63K | 回测复利偏差, 不可实盘 |
| S23_AggressiveKelly | 激进Kelly | +$698 | 同上 |

### ❌ 淘汰策略 (1 个)

| 策略 | 类型 | PnL | 原因 |
|------|------|-----|------|
| S19_HighConviction | 三信号叠加 | -$31 | 信号质量不足 |

## 推荐配置

```
S25_DynamicSpread: 40%  (年化 ~31%)
S22_Combo:         30%  (年化 ~41%)
S24_MultiWindow:   30%  (年化 ~13%)
────────────────────────
组合预期:           50-80% 年化
```

## 使用方法

### 运行回测

```bash
cd ~/polymarket-football
python3 -m PM足球策略.backtest
```

### 在代码中使用

```python
from PM足球策略.engine import BacktestEngine
from PM足球策略.strategies import s25_dynamic_spread, s22_combo
from PM足球策略.backtest import load_data

dataset = load_data()
engine = BacktestEngine(initial_capital=10000)
result = s25_dynamic_spread(engine, dataset)
print(f"交易: {result.total_trades}, PnL: ${result.total_pnl:.2f}")
```

### Paper Trading

```bash
cd ~/polymarket-football/paper_trading
./start_paper_trading.sh start
```

## 文件结构

```
PM足球策略/
├── __init__.py      # 包入口
├── config.py        # 全局配置
├── engine.py        # 回测引擎 (Trade, StrategyResult, BacktestEngine)
├── strategies.py    # 全部 9 个策略实现
├── backtest.py      # 回测运行器
└── README.md        # 本文档
```

## 关键发现

1. **做市是基石**: 86% 市场价格 <5%, 低概率市场有宽价差
2. **趋势是增量**: 3d/5d/7d 全同向时跟随, 单笔盈利远大于亏损
3. **组合最优**: S6+S11 相关性仅 0.51, 真正多样化
4. **Kelly 必须用 initial_capital**: 否则复利爆炸
5. **动态价差比静态好 11.6%**: 根据波动率+时间调整

## 数据

- 65 个足球市场 (WC 2026 + EPL)
- 3,678 个价格点 (2026-03-01 至 2026-04-30)
- $4.86M 交易量 (top 10 市场)
- 2,458 个独立交易者

## 风险提示

- 100% 胜率是回测模拟结果, 实盘有滑点/延迟
- 数据频率低 (8hr/点), 限制策略多样性
- 手续费 2% 在高频做市中累积显著
- 市场结构会随赛事阶段变化
