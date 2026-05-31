"""signal-strategy — 信号生成层 (方向 A & B)

把方向 A (反追涨均值回归) 与方向 B (恐慌反转抄底) 实现为可组合的信号生成器。
每个生成器消费一根 EventRow, 经过 全局stress过滤 → 方向逻辑 → 时段/标的/regime 乘子,
产出一个 Signal (或 None)。

设计原则:
  - 信号的"方向"和"基础 edge"来自真实分析 (config.LAG_BUCKETS / CAPITULATION_*)
  - 信号的"置信乘子"= session × universe × regime 三层叠加
  - stress 命中直接否决 (方向 B 的止跌反弹例外)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from config import (
    ROUND_TRIP_COST, A_EDGE_COST_MULT,
    CAPITULATION_EDGE_PCT, CAPITULATION_HOLD_BARS, CAPITULATION_STOP_PCT,
)
from datafeed import EventRow
from features import derive, Features
from session import session_multiplier
from universe import universe_multiplier
from regime import classify_regime, regime_multiplier


@dataclass
class Signal:
    ts: object
    contract: str
    direction: str          # "A" (reversion) / "B" (capitulation)
    side: str               # "LONG" / "SHORT"
    entry_price: float
    hold_bars: int
    raw_edge_pct: float     # 历史基础 edge (%)
    confidence: float       # 0..1 综合乘子 (session×universe×regime)
    stop_pct: Optional[float]  # 止损 (%)，None 表示按持仓周期到点平
    regime: str
    note: str = ""

    @property
    def expected_edge_pct(self) -> float:
        """置信度加权后的期望 edge (%)，扣往返成本前。"""
        return self.raw_edge_pct * self.confidence


# ============================================================
# 方向 A — 反追涨均值回归
# ============================================================
def signal_reversion(ev: EventRow, feats: Optional[Features] = None) -> Optional[Signal]:
    """STEP 7 滞后分桶: 涨3-5%跟随做多, 涨>5% fade做空。

    流程:
      1) 全局 stress 过滤 (急跌/高振幅/放量 → 否决)
      2) 取滞后分桶方向; FLAT 不交易
      3) edge 必须覆盖往返成本的 A_EDGE_COST_MULT 倍
      4) 叠加 session × universe × regime 乘子
    """
    feats = feats or derive(ev)

    # 1) stress 否决 (方向 A 是逆势, 承压态风险极高)
    if feats.stress.any:
        return None

    # 2) 方向
    lag = feats.lag
    if lag.side == "FLAT":
        return None

    # 3) edge 门槛: 历史 edge 要够覆盖成本
    if lag.raw_edge_pct / 100.0 < ROUND_TRIP_COST * A_EDGE_COST_MULT:
        return None

    # 4) 乘子
    sess = session_multiplier(ev.hour)
    if sess <= 0.0:
        return None
    uni = universe_multiplier(ev.contract)
    if uni <= 0.0:
        return None
    regime = classify_regime(ev)
    reg = regime_multiplier(regime, "A")

    confidence = sess * uni * reg
    if confidence <= 0.0:
        return None

    return Signal(
        ts=ev.ts, contract=ev.contract, direction="A", side=lag.side,
        entry_price=ev.price, hold_bars=lag.hold_bars,
        raw_edge_pct=lag.raw_edge_pct, confidence=round(confidence, 4),
        stop_pct=None, regime=regime,
        note=f"lag{lag.label} sess{sess:.2f} uni{uni:.2f} reg{reg:.2f}",
    )


# ============================================================
# 方向 B — 恐慌反转抄底
# ============================================================
def signal_capitulation(ev: EventRow, feats: Optional[Features] = None) -> Optional[Signal]:
    """STEP 6 超大盈利: 急跌(-8%+) + 大额信号 + 止跌确认 → 做多反弹。

    注意: 方向 B 允许在"曾经急跌"的环境下进场, 但要求 ret_5m>=0 (止跌),
    所以不会被 down_5m 这一 stress 项否决; 但仍否决"仍在放量恶化"的情形。
    """
    feats = feats or derive(ev)

    if not feats.capitulation:
        return None

    # 止跌后若仍在剧烈放量 (高 vol_ratio) → 多半是下跌中继, 否决
    if feats.stress.high_vol:
        return None

    sess = session_multiplier(ev.hour)
    if sess <= 0.0:
        return None
    uni = universe_multiplier(ev.contract)
    if uni <= 0.0:
        return None
    regime = classify_regime(ev)
    reg = regime_multiplier(regime, "B")

    confidence = sess * uni * reg
    if confidence <= 0.0:
        return None

    return Signal(
        ts=ev.ts, contract=ev.contract, direction="B", side="LONG",
        entry_price=ev.price, hold_bars=CAPITULATION_HOLD_BARS,
        raw_edge_pct=CAPITULATION_EDGE_PCT, confidence=round(confidence, 4),
        stop_pct=CAPITULATION_STOP_PCT, regime=regime,
        note=f"capit past{ev.past_ret_3h:.1f}% score{ev.gf_score_30m:.0f} sess{sess:.2f}",
    )


# ============================================================
# 组合: 对单根事件依次尝试各方向 (B 优先, 因 edge 更大)
# ============================================================
def generate_signals(ev: EventRow) -> List[Signal]:
    feats = derive(ev)
    out: List[Signal] = []
    b = signal_capitulation(ev, feats)
    if b:
        out.append(b)
        return out          # capitulation 与 reversion 互斥 (同一根只取 B)
    a = signal_reversion(ev, feats)
    if a:
        out.append(a)
    return out
