"""signal-strategy — 组合 / 仓位 / 风控层

把 Signal 转成具体下单名义金额, 并执行账户级风控:
  - 单笔仓位 = 权益 × BASE_RISK_FRAC × confidence × edge缩放, 受 MAX_POSITION_FRAC 限制
  - 单日亏损达 MAX_DAILY_LOSS_FRAC 后当日停手
  - 最大同时持仓数 MAX_CONCURRENT
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from config import (
    INITIAL_CAPITAL, BASE_RISK_FRAC, MAX_POSITION_FRAC,
    MAX_DAILY_LOSS_FRAC, MAX_CONCURRENT, ROUND_TRIP_COST,
)
from signals import Signal


@dataclass
class Account:
    equity: float = INITIAL_CAPITAL
    peak_equity: float = INITIAL_CAPITAL
    max_drawdown: float = 0.0
    daily_pnl: Dict[str, float] = field(default_factory=dict)
    open_positions: int = 0

    def day_key(self, ts) -> str:
        return ts.strftime("%Y-%m-%d")

    def can_open(self, ts) -> bool:
        """日内亏损限额 + 并发限额检查。"""
        if self.open_positions >= MAX_CONCURRENT:
            return False
        d = self.daily_pnl.get(self.day_key(ts), 0.0)
        if d <= -abs(INITIAL_CAPITAL * MAX_DAILY_LOSS_FRAC):
            return False
        return True

    def register_pnl(self, ts, pnl: float):
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0
        self.max_drawdown = max(self.max_drawdown, dd)
        k = self.day_key(ts)
        self.daily_pnl[k] = self.daily_pnl.get(k, 0.0) + pnl


def position_notional(sig: Signal, equity: float) -> float:
    """计算下单名义金额 (U)。

    仓位 = 权益 × 基础风险 × confidence × edge缩放
    edge缩放: 历史 edge 越大相对成本, 仓位越大 (上限 2x)。
    """
    edge_ratio = (sig.raw_edge_pct / 100.0) / ROUND_TRIP_COST if ROUND_TRIP_COST > 0 else 1.0
    edge_scale = max(0.5, min(edge_ratio / 2.0, 2.0))
    frac = BASE_RISK_FRAC * sig.confidence * edge_scale
    frac = max(0.0, min(frac, MAX_POSITION_FRAC))
    return equity * frac
