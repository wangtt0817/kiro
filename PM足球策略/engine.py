"""PM足球策略 — 回测引擎核心"""

import json
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional

from .config import INITIAL_CAPITAL, FEE_RATE, KELLY_FRAC, MAX_BET_PCT, MAX_DAILY_LOSS_PCT, SLIPPAGE, LIQUIDITY_THRESHOLD


@dataclass
class Trade:
    date: str
    strategy: str
    team: str
    direction: str
    entry_price: float
    exit_price: float
    size: float
    edge: float
    pnl: float
    outcome: str


@dataclass
class StrategyResult:
    name: str
    trades: List[Trade] = field(default_factory=list)

    @property
    def total_trades(self): return len(self.trades)
    @property
    def wins(self): return sum(1 for t in self.trades if t.outcome == "WIN")
    @property
    def losses(self): return sum(1 for t in self.trades if t.outcome == "LOSS")
    @property
    def win_rate(self): return self.wins / self.total_trades if self.total_trades > 0 else 0
    @property
    def total_pnl(self): return sum(t.pnl for t in self.trades)
    @property
    def avg_edge(self):
        edges = [t.edge for t in self.trades]
        return np.mean(edges) if edges else 0
    @property
    def avg_win(self):
        w = [t.pnl for t in self.trades if t.pnl > 0]
        return np.mean(w) if w else 0
    @property
    def avg_loss(self):
        l = [t.pnl for t in self.trades if t.pnl <= 0]
        return np.mean(l) if l else 0
    @property
    def max_win(self):
        w = [t.pnl for t in self.trades if t.pnl > 0]
        return max(w) if w else 0
    @property
    def max_loss(self):
        l = [t.pnl for t in self.trades if t.pnl <= 0]
        return min(l) if l else 0


class BacktestEngine:
    """回测引擎 — Kelly 仓位管理 + 风控"""

    def __init__(self, initial_capital=INITIAL_CAPITAL, kelly_frac=KELLY_FRAC,
                 max_bet_pct=MAX_BET_PCT):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.peak_capital = initial_capital
        self.max_drawdown = 0.0
        self.equity_curve = [initial_capital]
        self.daily_pnl = {}
        self.kelly_frac = kelly_frac
        self.max_bet_pct = max_bet_pct
        self.max_daily_loss_pct = MAX_DAILY_LOSS_PCT

    def position_size(self, edge, price, win_rate=None, aggressive=False):
        """Kelly 仓位计算 — 使用 initial_capital 避免复利爆炸"""
        if edge <= 0 or price <= 0:
            return 0
        odds = (1 - price) / price if price < 1 else 0.01
        cap = 0.05 if aggressive else 0.03

        if win_rate and 0 < win_rate <= 1:
            capped_wr = min(win_rate, 0.70)
            kelly = (capped_wr * (1 + odds) - 1) / odds if odds > 0 else 0
        else:
            kelly = edge / odds if odds > 0 else 0

        kelly_pct = max(0, min(kelly * self.kelly_frac, cap))
        kelly_amount = self.initial_capital * kelly_pct
        hard_cap = self.initial_capital * self.max_bet_pct
        return max(0, min(kelly_amount, hard_cap))

    def check_daily_limit(self, date):
        daily = self.daily_pnl.get(date, 0)
        return abs(daily) < self.initial_capital * self.max_daily_loss_pct

    def execute_trade(self, strategy, team, direction, entry_price, exit_price,
                      edge, date, win_rate=None, aggressive=False):
        if not self.check_daily_limit(date):
            return None
        
        # 流动性检查：低概率市场流动性差，降低仓位
        liquidity_factor = 1.0
        if entry_price < LIQUIDITY_THRESHOLD:
            liquidity_factor = 0.5  # 低概率市场仓位减半
        
        size = self.position_size(edge, entry_price, win_rate, aggressive) * liquidity_factor
        if size < 1:
            return None

        # 滑点调整：实际成交价比预期差
        slippage_cost = size * SLIPPAGE
        
        fee = size * FEE_RATE
        if direction == "BUY_YES":
            raw_pnl = size * (exit_price - entry_price) / entry_price
        else:
            no_entry = 1 - entry_price
            no_exit = 1 - exit_price
            raw_pnl = size * (no_exit - no_entry) / no_entry if no_entry > 0 else 0

        pnl = raw_pnl - fee - slippage_cost
        outcome = "WIN" if pnl > 0 else "LOSS"
        self.capital += pnl
        self.peak_capital = max(self.peak_capital, self.capital)
        dd = (self.peak_capital - self.capital) / self.peak_capital
        self.max_drawdown = max(self.max_drawdown, dd)
        self.equity_curve.append(self.capital)
        self.daily_pnl[date] = self.daily_pnl.get(date, 0) + pnl

        return Trade(
            date=date, strategy=strategy, team=team, direction=direction,
            entry_price=round(entry_price, 4), exit_price=round(exit_price, 4),
            size=round(size, 2), edge=round(edge, 4), pnl=round(pnl, 2),
            outcome=outcome
        )


def compute_pf(result: StrategyResult) -> float:
    """计算盈利比 (Profit Factor)"""
    pnls = [t.pnl for t in result.trades]
    w = sum(p for p in pnls if p > 0)
    l = abs(sum(p for p in pnls if p <= 0))
    if l == 0:
        return 99.9
    return round(min(w / l, 99.9), 2)


def compute_metrics(result: StrategyResult, engine: BacktestEngine) -> dict:
    """计算策略完整指标"""
    if result.total_trades == 0:
        return {
            "name": result.name, "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "total_pnl": 0, "roi_pct": 0, "avg_edge": 0,
            "max_drawdown_pct": 0, "profit_factor": 0, "sharpe": 0,
            "avg_win": 0, "avg_loss": 0,
        }
    pnls = [t.pnl for t in result.trades]
    pf = compute_pf(result)
    sharpe = (np.mean(pnls) / np.std(pnls) * np.sqrt(len(pnls))
              if len(pnls) > 1 and np.std(pnls) > 0 else 0)
    return {
        "name": result.name,
        "total_trades": result.total_trades,
        "wins": result.wins,
        "losses": result.losses,
        "win_rate": round(result.win_rate * 100, 1),
        "total_pnl": round(result.total_pnl, 2),
        "roi_pct": round(result.total_pnl / engine.initial_capital * 100, 2),
        "avg_edge": round(result.avg_edge * 100, 2),
        "max_drawdown_pct": round(engine.max_drawdown * 100, 2),
        "profit_factor": pf,
        "sharpe": round(sharpe, 2),
        "avg_win": round(result.avg_win, 2),
        "avg_loss": round(result.avg_loss, 2),
    }
