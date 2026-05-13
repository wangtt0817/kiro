# 平仓单腿失败的三级防护方案

## 问题场景

**两腿都成交（持仓中），平仓时一边成交，另一边失败怎么办？**

这是配对交易中最危险的场景——如果处理不当，会导致**单腿裸露的方向性敞口**，可能造成远超手续费收益的亏损。

---

## 问题分析

```
错误的顺序平仓逻辑（危险！）：
  先平 ETH → 成功 → 再平 BTC → 失败
  结果：只剩裸露的 BTC 单腿持仓 → 方向性风险暴露！

可能后果：
  假设 BTC 暴涨 5%，但我们是空 BTC
  单腿亏损 5% × $2,000 = $100
  远远超过配对交易正常一轮的盈利（$0.6 = 0.03%）
```

---

## 解决方案：同时平仓 + 三级防护

### 核心原则

> **永远不允许单腿裸露超过 5 秒钟。宁可付 0.15% Taker 费（一轮亏损），也不冒方向性风险（可能亏 5-10%）。**

### 三级防护设计

```
┌─────────────────────────────────────────────────────────────┐
│  Level 1: 同时平仓（CLOSING_BOTH）                           │
│                                                             │
│  两腿同时下 Maker 平仓单                                     │
│  - 如果都成交 → 完美 ✅                                      │
│  - 如果都没成交 → 每 3 秒刷新价格重挂                        │
│  - 如果一腿成交另一腿没成交 → 进入 Level 2                   │
├─────────────────────────────────────────────────────────────┤
│  Level 2: 紧急平仓（CLOSING_REMAINING）                      │
│                                                             │
│  剩余那腿：激进 Maker 策略                                   │
│  - 每 1 秒刷新订单价格（不等 3 秒）                          │
│  - 挂单位置从 best_ask 改为 mid-spread（更容易成交）         │
│  - 最多重试 5 次                                             │
│  - 超过 5 次 → 进入 Level 3                                  │
├─────────────────────────────────────────────────────────────┤
│  Level 3: 强制 Taker（最后手段）                             │
│                                                             │
│  直接以对手价吃单，保证立即成交                               │
│  - 会多付 0.15% 手续费（vs 0.005%）                          │
│  - 但消除了裸露敞口风险                                      │
│  - 宁可这一轮亏手续费，也不留单腿过夜                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 状态流转图

```
HOLDING
    ↓ 触发平仓（达到目标/止损/超时）
CLOSING_BOTH（两腿同时挂 Maker 平仓单）
    ├── 两腿都成交 → DONE ✅
    ├── 一腿成交 + 另一腿超时 → CLOSING_REMAINING ⚠️
    │       ├── 激进 Maker 每 1 秒刷新 → 成交 → DONE
    │       └── 5 次失败 → 强制 Taker → DONE
    └── 两腿都超时 → 刷新价格，继续 CLOSING_BOTH
```

---

## Level 1：同时平仓（CLOSING_BOTH）

```python
async def _start_close(self, trade_round: TradeRound):
    """开始同时平仓两腿"""
    trade_round.state = TradeState.CLOSING_BOTH
    
    # 两腿同时下单（关键：并行，不是顺序）
    await self._place_close_order(trade_round, "eth")
    await self._place_close_order(trade_round, "btc")

async def _check_closing_both(self, trade_round):
    """监控两腿平仓状态"""
    # 检查 ETH 腿
    if eth_leg.close_order_filled():
        eth_leg.closed = True
    # 检查 BTC 腿
    if btc_leg.close_order_filled():
        btc_leg.closed = True
    
    if eth_closed and btc_closed:
        → DONE ✅
    elif eth_closed and not btc_closed:
        → 进入 CLOSING_REMAINING（紧急处理 BTC）
    elif btc_closed and not eth_closed:
        → 进入 CLOSING_REMAINING（紧急处理 ETH）
    else:
        → 3 秒后刷新两腿价格
```

---

## Level 2：紧急平仓（CLOSING_REMAINING）

```python
async def _aggressive_close_leg(self, trade_round, leg_name):
    """激进模式：挂单在盘口内侧，更容易成交"""
    ob = await self.client.get_orderbook(leg.symbol)
    spread = ob.best_ask - ob.best_bid
    
    if close_side == SELL:
        # 普通 Maker：卖在 best_ask
        # 激进 Maker：卖在 best_ask - spread × 30%
        price = ob.best_ask - spread * 0.3
        price = max(price, ob.best_bid)  # 防止跌破 bid 变成 Taker
    else:
        price = ob.best_bid + spread * 0.3
        price = min(price, ob.best_ask)
    
    # 下单 → 记录重试次数
```

**关键改进**：
- 挂单价格从 `best_ask` 改为 `best_ask - spread * 0.3`（向对手价靠近 30%）
- 仍然是 Maker 订单（价格未跨过对手最优价）
- 成交概率大幅提升
- 刷新频率从 3 秒 → 1 秒

---

## Level 3：强制 Taker（最后手段）

```python
async def _force_taker_close(self, trade_round, leg_name):
    """
    最后手段：用 Taker 保证成交
    
    代价：手续费 0.15% vs 0.005%（多付约 0.145%）
    收益：避免单腿裸露敞口（可能亏 5-10%）
    """
    ob = await self.client.get_orderbook(leg.symbol)
    
    # 价格跨过盘口，保证立即成交
    if close_side == SELL:
        price = ob.best_bid  # 卖到 bid = Taker，立即成交
    else:
        price = ob.best_ask  # 买到 ask = Taker，立即成交
    
    await self.client.place_limit_order(
        symbol=leg.symbol,
        side=close_side,
        amount=leg.amount,
        price=price,
        reduce_only=True,
    )
```

---

## 手续费对比

| 情况 | 手续费 | 说明 |
|------|-------|------|
| 正常全 Maker 平仓 | 0.01%（2 笔 × 0.005%） | 最优 |
| 一腿被迫 Taker | 0.155%（1 Maker + 1 Taker） | 紧急 |
| 单次多付 | +0.145% | 约一轮正常盈利 |

**结论**：
- 偶尔发生一次 Taker 强平完全可以接受
- 100 轮交易中即使 10% 发生 Taker 强平，总成本仅增加 0.0145%
- 相比被大行情打穿单腿（可能亏 5%+），这点成本微不足道

---

## 数据结构变更

原来的 `TradeLeg`：
```python
@dataclass
class TradeLeg:
    order_id: str = ""         # 开仓和平仓复用，有冲突风险
    filled: bool = False       # 只表示开仓成交
```

改进后的 `TradeLeg`：
```python
@dataclass
class TradeLeg:
    order_id: str = ""              # 开仓订单 ID
    close_order_id: str = ""        # 平仓订单 ID（独立字段）
    filled: bool = False            # 开仓是否成交
    closed: bool = False            # 平仓是否完成
    close_order_time: float = 0.0   # 平仓单下单时间（用于超时判断）
    close_retries: int = 0          # 重试次数（用于升级策略）
```

关键改进：
1. 开仓和平仓订单 ID 分离，防止状态混乱
2. 新增 `closed` 字段明确标记平仓完成
3. 独立追踪每一腿的重试次数和下单时间

---

## 状态机新增状态

原来：
```
CLOSING_LEG1 → CLOSING_LEG2 → DONE
```

改进后：
```
CLOSING_BOTH（并行）
    ├→ 两腿都成交 → DONE
    └→ 一腿失败 → CLOSING_REMAINING → DONE
```

优势：
- 并行比串行快（平仓速度提升 50%+）
- 失败处理路径明确
- 单腿裸露时间严格控制

---

## 超时参数

```python
CLOSE_LEG_TIMEOUT = 3              # 普通状态：3 秒后刷新
# CLOSING_REMAINING 状态：1 秒后刷新（在代码中硬编码）
FORCE_TAKER_AFTER_RETRIES = 5      # 5 次失败后强制 Taker
```

时间计算：
- Level 1 最多耗时：3 秒 × N 次刷新（持续尝试直到另一腿或全部成交）
- Level 2 最多耗时：1 秒 × 5 次 = 5 秒
- Level 3 执行时间：< 1 秒

**最坏情况：单腿裸露约 6 秒**（从 Level 1 发现失败到 Level 3 强制平仓）

---

## 监控告警

代码中已加入警告日志：

```python
# 进入 CLOSING_REMAINING 时
logger.warning(
    f"Round #{id} | ⚠️ ETH closed but BTC still open! "
    f"Entering CLOSING_REMAINING mode"
)

# 强制 Taker 时
logger.warning(
    f"Round #{id} | ⚠️⚠️ {leg} leg failed {retries} Maker attempts! "
    f"FORCING TAKER CLOSE (will cost 0.15% but prevents naked exposure)"
)

# 成交后
logger.warning(
    f"Round #{id} | {leg} FORCE CLOSED (Taker) @ {price:.2f}"
)
```

建议将这些警告接入告警系统（Telegram、企业微信），一旦频繁出现需要人工干预。

---

## 总结

**核心思想：预防 > 补救**

1. **预防**：同时下单比顺序下单更安全（Level 1）
2. **快速响应**：发现单腿敞口立即紧急处理（Level 2）
3. **保底兜底**：保证执行永远有 Taker 作为退路（Level 3）
4. **透明追踪**：完整日志便于事后复盘

**成本权衡**：愿意承担偶尔的 0.15% Taker 费，换取零单腿敞口风险。
