"""signal-strategy — 事件驱动回测引擎 + 指标 + 归因

对事件流逐根处理:
  generate_signals → 风控闸门 → 仓位 → 用真实/合成 fwd_ret 结算 → 记录 Trade
退出价格使用 EventRow.fwd_ret_*(真实未来 K线收益)，
而非足球策略里那种"模拟出来的有利价格" —— 这是与旧框架最本质的区别。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from config import ROUND_TRIP_COST, INITIAL_CAPITAL
from datafeed import EventRow
from features import classify_lag
from session import session_tier
from portfolio import Account, position_notional
from signals import Signal, generate_signals


@dataclass
class Trade:
    ts: object
    contract: str
    direction: str       # A / B
    side: str            # LONG / SHORT
    regime: str
    hold_bars: int
    notional: float
    gross_ret: float     # 已折算方向, 扣费前 (小数)
    net_ret: float       # 扣往返成本后 (小数)
    pnl: float
    stopped: bool
    hour: int
    lag_label: str
    uni_class: str

    @property
    def win(self) -> bool:
        return self.pnl > 0


def _settle(sig: Signal, ev: EventRow) -> tuple[float, bool]:
    """用真实未来收益结算一笔交易, 返回 (gross_ret, stopped)。

    stop 处理: 仅有离散 fwd_ret_*，用 fwd_ret_1 作为早盘触发代理。
    若早盘已击穿止损, 视为按 -stop 出场。
    """
    sign = 1.0 if sig.side == "LONG" else -1.0
    fwd = ev.fwd_ret(sig.hold_bars)
    if fwd is None:
        fwd = ev.fwd_ret_1 or 0.0
    gross = sign * (fwd / 100.0)

    stopped = False
    if sig.stop_pct is not None and ev.fwd_ret_1 is not None:
        early = sign * (ev.fwd_ret_1 / 100.0)
        if early <= -(sig.stop_pct / 100.0):
            gross = -(sig.stop_pct / 100.0)
            stopped = True
    return gross, stopped


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    account: Account = field(default_factory=Account)
    n_events: int = 0
    n_signals: int = 0

    # ---- 汇总指标 ----
    def metrics(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {"trades": 0, "note": "no trades"}
        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        mean = sum(pnls) / n
        std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / n) if n > 1 else 0.0
        sharpe = (mean / std * math.sqrt(n)) if std > 0 else 0.0
        return {
            "trades": n,
            "signals": self.n_signals,
            "events": self.n_events,
            "signal_rate_pct": round(self.n_signals / self.n_events * 100, 2) if self.n_events else 0,
            "win_rate_pct": round(len(wins) / n * 100, 1),
            "total_pnl": round(sum(pnls), 2),
            "roi_pct": round(sum(pnls) / INITIAL_CAPITAL * 100, 2),
            "final_equity": round(self.account.equity, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.9,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "max_drawdown_pct": round(self.account.max_drawdown * 100, 2),
            "sharpe_per_trade": round(sharpe, 2),
            "stopped_count": sum(1 for t in self.trades if t.stopped),
        }

    # ---- 分组归因 ----
    def attribution(self, key: Callable[[Trade], str]) -> Dict[str, dict]:
        groups: Dict[str, List[Trade]] = {}
        for t in self.trades:
            groups.setdefault(key(t), []).append(t)
        out = {}
        for g, ts in sorted(groups.items()):
            pnl = sum(t.pnl for t in ts)
            wr = sum(1 for t in ts if t.win) / len(ts) * 100
            out[g] = {"trades": len(ts), "pnl": round(pnl, 2), "win_rate_pct": round(wr, 1)}
        return out


def run_backtest(
    events: List[EventRow],
    signal_fn: Callable[[EventRow], List[Signal]] = generate_signals,
) -> BacktestResult:
    """主回测循环。"""
    res = BacktestResult()
    res.n_events = len(events)

    for ev in events:
        sigs = signal_fn(ev)
        res.n_signals += len(sigs)
        for sig in sigs:
            if not res.account.can_open(ev.ts):
                continue
            notional = position_notional(sig, res.account.equity)
            if notional < 1.0:
                continue

            gross, stopped = _settle(sig, ev)
            net = gross - ROUND_TRIP_COST
            pnl = notional * net

            res.account.register_pnl(ev.ts, pnl)
            res.trades.append(Trade(
                ts=ev.ts, contract=ev.contract, direction=sig.direction,
                side=sig.side, regime=sig.regime, hold_bars=sig.hold_bars,
                notional=round(notional, 2), gross_ret=round(gross, 5),
                net_ret=round(net, 5), pnl=round(pnl, 2), stopped=stopped,
                hour=ev.ts.hour, lag_label=classify_lag(ev.past_ret_3h).label,
                uni_class=_uni_class(ev.contract),
            ))
    return res


def _uni_class(contract: str) -> str:
    from universe import universe_class
    return universe_class(contract)


# 便捷归因 key
def by_direction(t: Trade) -> str: return f"{t.direction}_{t.side}"
def by_session_tier(t: Trade) -> str: return f"{t.hour:02d}_{session_tier(t.hour)}"
def by_regime(t: Trade) -> str: return t.regime
def by_lag(t: Trade) -> str: return t.lag_label
def by_universe(t: Trade) -> str: return t.uni_class
