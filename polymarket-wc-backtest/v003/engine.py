"""Clean backtest engine for Polymarket-style binary markets.

Design notes
------------
* Prices are probabilities in (0, 1). YES contract pays $1 on resolution; NO
  contract pays $1 if the event does not occur. Equivalently, NO is "buying the
  YES of the complement", so we model it as buying at price q = 1 - p.
* Stakes are in dollars. We convert to shares using the **effective fill price**
  (mid + half-spread for buys, mid - half-spread for sells). PnL is then
  ``shares * (effective_exit - effective_entry)`` minus round-trip fees.
* Sizing uses fractional Kelly on whichever leg has a positive edge. The legacy
  v002 ``kelly_size`` accidentally returned 0 for every NO trade because it
  always compared the YES probabilities; that bug is fixed here by computing
  Kelly separately for each direction.
* The engine never sees future prices when sizing or accepting an order. The
  walk-forward harness enforces this by passing strategies a ``MarketView``
  that masks anything at or after the decision timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import math


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class CostConfig:
    """Round-trip cost model in absolute probability units."""

    taker_fee_rate: float = 0.02            # fraction of notional, charged at entry AND exit
    half_spread_default: float = 0.005      # 0.5c half spread for mainstream legs
    half_spread_longtail: float = 0.025     # 2.5c half spread when p < longtail_threshold
    longtail_threshold: float = 0.05
    min_price: float = 0.001
    max_price: float = 0.999

    def half_spread(self, mid: float) -> float:
        return self.half_spread_longtail if mid < self.longtail_threshold else self.half_spread_default

    def buy_price(self, mid: float) -> float:
        return min(self.max_price, max(self.min_price, mid + self.half_spread(mid)))

    def sell_price(self, mid: float) -> float:
        return min(self.max_price, max(self.min_price, mid - self.half_spread(mid)))


@dataclass
class PortfolioConfig:
    initial_capital: float = 10_000.0
    kelly_fraction: float = 0.25            # quarter Kelly
    max_bet_pct: float = 0.02               # 2% hard cap per trade
    max_team_exposure_pct: float = 0.05     # 5% gross exposure to any single team
    max_gross_exposure_pct: float = 0.40    # 40% of capital open at once
    min_stake_dollars: float = 1.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
DIR_YES = "BUY_YES"
DIR_NO = "BUY_NO"


@dataclass
class Position:
    strategy: str
    team: str
    direction: str
    entry_date: str
    entry_mid: float
    entry_fill: float          # effective price actually paid for one share
    shares: float              # number of shares held
    stake: float               # gross dollars deployed (including spread, before fees)
    fee_paid_entry: float
    model_prob: float
    horizon_days: int          # planned holding period
    open: bool = True


@dataclass
class Trade:
    strategy: str
    team: str
    direction: str
    entry_date: str
    exit_date: str
    entry_mid: float
    exit_mid: float
    entry_fill: float
    exit_fill: float
    shares: float
    stake: float
    edge: float
    fees: float
    pnl: float
    holding_days: int
    outcome: str               # WIN / LOSS / FLAT


# ---------------------------------------------------------------------------
# Kelly (per leg)
# ---------------------------------------------------------------------------
def kelly_fraction_yes(p_model: float, p_market: float) -> float:
    """Optimal Kelly fraction when buying YES at price ``p_market``.

    Returns 0 if there is no positive edge on the YES leg.
    """
    if p_market <= 0 or p_market >= 1:
        return 0.0
    if p_model <= p_market:
        return 0.0
    return (p_model - p_market) / (1.0 - p_market)


def kelly_fraction_no(p_model: float, p_market: float) -> float:
    """Optimal Kelly fraction when buying NO at price ``1 - p_market``."""
    if p_market <= 0 or p_market >= 1:
        return 0.0
    if p_model >= p_market:
        return 0.0
    return (p_market - p_model) / p_market


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class Engine:
    """Stateful, share-based backtest engine with portfolio constraints."""

    def __init__(
        self,
        cost: Optional[CostConfig] = None,
        portfolio: Optional[PortfolioConfig] = None,
    ):
        self.cost = cost or CostConfig()
        self.pcfg = portfolio or PortfolioConfig()

        self.capital = self.pcfg.initial_capital
        self.peak_capital = self.capital
        self.max_drawdown = 0.0

        # Open positions and completed trades
        self.positions: List[Position] = []
        self.trades: List[Trade] = []

        # exposure[(team)] = sum of stakes currently open on that team
        self._team_exposure: Dict[str, float] = {}
        self._gross_exposure: float = 0.0

        # Daily equity curve: list[(date, equity)]
        self.equity_curve: List[Tuple[str, float]] = []

    # -- sizing -----------------------------------------------------------
    def _kelly_stake(self, direction: str, p_model: float, p_market: float) -> float:
        if direction == DIR_YES:
            f = kelly_fraction_yes(p_model, p_market)
        elif direction == DIR_NO:
            f = kelly_fraction_no(p_model, p_market)
        else:
            return 0.0
        target = f * self.pcfg.kelly_fraction * self.pcfg.initial_capital
        hard_cap = self.pcfg.initial_capital * self.pcfg.max_bet_pct
        return max(0.0, min(target, hard_cap))

    def _allowed_stake(self, team: str, requested: float) -> float:
        cap_team = self.pcfg.initial_capital * self.pcfg.max_team_exposure_pct
        cap_gross = self.pcfg.initial_capital * self.pcfg.max_gross_exposure_pct
        team_remaining = max(0.0, cap_team - self._team_exposure.get(team, 0.0))
        gross_remaining = max(0.0, cap_gross - self._gross_exposure)
        return min(requested, team_remaining, gross_remaining)

    # -- raw primitives ---------------------------------------------------
    def enter(
        self,
        strategy: str,
        team: str,
        direction: str,
        fill_price: float,
        stake: float,
        ts: str,
        model_prob: float,
        mid_price: Optional[float] = None,
        horizon_days: int = 1,
        apply_caps: bool = True,
    ) -> Optional[Position]:
        """Open a position at an EXPLICIT leg-space fill price and stake.

        ``fill_price`` is the price actually paid for one share of the chosen
        leg (already in YES-space for YES, in NO-space ``q = 1 - p`` for NO).
        Used directly by market-making and structural-arb strategies that
        compute their own fills; ``open_position`` is a convenience wrapper that
        derives ``fill_price``/``stake`` from a mid + Kelly.
        """
        if direction not in (DIR_YES, DIR_NO):
            return None
        if not (0.0 < fill_price < 1.0):
            return None
        if apply_caps:
            stake = self._allowed_stake(team, stake)
        if stake < self.pcfg.min_stake_dollars:
            return None
        shares = stake / fill_price
        if shares <= 0:
            return None

        fee_entry = stake * self.cost.taker_fee_rate
        if mid_price is None:
            mid_price = fill_price if direction == DIR_YES else (1.0 - fill_price)

        pos = Position(
            strategy=strategy,
            team=team,
            direction=direction,
            entry_date=ts,
            entry_mid=mid_price,
            entry_fill=fill_price,
            shares=shares,
            stake=stake,
            fee_paid_entry=fee_entry,
            model_prob=model_prob,
            horizon_days=horizon_days,
        )
        self.positions.append(pos)
        self._team_exposure[team] = self._team_exposure.get(team, 0.0) + stake
        self._gross_exposure += stake
        # Stake leaves cash (tied up in shares); entry fee realized immediately.
        self.capital -= (stake + fee_entry)
        return pos

    def close_at_fill(self, pos: Position, exit_fill: float, exit_mid: float,
                      exit_date: str) -> Trade:
        """Close ``pos`` at an EXPLICIT leg-space exit fill price."""
        if not pos.open:
            raise RuntimeError("position already closed")
        exit_fill = min(self.cost.max_price, max(self.cost.min_price, exit_fill))

        gross_exit = pos.shares * exit_fill
        fee_exit = gross_exit * self.cost.taker_fee_rate
        realized_cash = gross_exit - fee_exit
        pnl = realized_cash - pos.stake - pos.fee_paid_entry
        self.capital += realized_cash

        self._team_exposure[pos.team] = max(0.0, self._team_exposure.get(pos.team, 0.0) - pos.stake)
        self._gross_exposure = max(0.0, self._gross_exposure - pos.stake)
        pos.open = False

        if pos.direction == DIR_YES:
            edge = pos.model_prob - pos.entry_mid
        else:
            edge = pos.entry_mid - pos.model_prob
        outcome = "WIN" if pnl > 0 else ("FLAT" if pnl == 0 else "LOSS")
        holding = max(1, _date_diff_days(pos.entry_date, exit_date))

        tr = Trade(
            strategy=pos.strategy,
            team=pos.team,
            direction=pos.direction,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            entry_mid=pos.entry_mid,
            exit_mid=exit_mid,
            entry_fill=pos.entry_fill,
            exit_fill=exit_fill,
            shares=pos.shares,
            stake=pos.stake,
            edge=edge,
            fees=pos.fee_paid_entry + fee_exit,
            pnl=pnl,
            holding_days=holding,
            outcome=outcome,
        )
        self.trades.append(tr)
        return tr

    # -- trade lifecycle (mid + Kelly convenience wrappers) ---------------
    def open_position(
        self,
        strategy: str,
        team: str,
        direction: str,
        mid_price: float,
        model_prob: float,
        ts: str,
        horizon_days: int = 1,
    ) -> Optional[Position]:
        if not (0.0 < mid_price < 1.0):
            return None

        # Effective fill price for buying the chosen leg.
        if direction == DIR_YES:
            fill = self.cost.buy_price(mid_price)
        elif direction == DIR_NO:
            fill = self.cost.buy_price(1.0 - mid_price)  # buying NO = buying at q = 1 - p
        else:
            return None

        requested = self._kelly_stake(direction, model_prob, mid_price)
        return self.enter(
            strategy=strategy,
            team=team,
            direction=direction,
            fill_price=fill,
            stake=requested,
            ts=ts,
            model_prob=model_prob,
            mid_price=mid_price,
            horizon_days=horizon_days,
            apply_caps=True,
        )

    def close_position(self, pos: Position, exit_mid: float, exit_date: str) -> Trade:
        if pos.direction == DIR_YES:
            exit_fill = self.cost.sell_price(exit_mid)
        else:  # DIR_NO -> selling NO = selling at q' = 1 - exit_mid
            exit_fill = self.cost.sell_price(1.0 - exit_mid)
        return self.close_at_fill(pos, exit_fill, exit_mid, exit_date)

    def mark_to_market(self, mid_prices: Dict[str, float], date: str) -> float:
        """Mark open positions to a snapshot of mid prices and record equity.

        Equity = free cash + Σ (shares × sell_fill_now) for open positions.
        Drawdown is tracked on equity, not on cash, because cash drops sharply
        whenever a position is opened (stake leaves cash but enters MTM).
        """
        mtm_value = 0.0
        for pos in self.positions:
            if not pos.open:
                continue
            mid = mid_prices.get(pos.team)
            if mid is None:
                continue
            if pos.direction == DIR_YES:
                sell = self.cost.sell_price(mid)
            else:
                sell = self.cost.sell_price(1.0 - mid)
            mtm_value += pos.shares * sell
        equity = self.capital + mtm_value
        # Drawdown on equity (not cash) so opening positions doesn't fake a DD.
        if equity > self.peak_capital:
            self.peak_capital = equity
        if self.peak_capital > 0:
            dd = (self.peak_capital - equity) / self.peak_capital
            if dd > self.max_drawdown:
                self.max_drawdown = dd
        self.equity_curve.append((date, equity))
        return equity

    # -- summary ----------------------------------------------------------
    def daily_pnl_series(self) -> List[float]:
        """Approximate daily PnL series from equity curve."""
        if not self.equity_curve:
            return []
        eq = [e for _, e in self.equity_curve]
        return [b - a for a, b in zip(eq[:-1], eq[1:])]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _date_diff_days(a: str, b: str) -> int:
    """Difference in days between two ISO date strings (b - a)."""
    from datetime import date

    fmt = "%Y-%m-%d"
    try:
        da = date.fromisoformat(a)
        db = date.fromisoformat(b)
    except Exception:
        return 1
    return (db - da).days
