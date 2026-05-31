"""Reference strategies for v003.

These are *deliberately small* clean reimplementations of four legacy
strategies. They share three guarantees:

1. **No look-ahead.** The decision at index ``t`` only reads ``prices[..., :t+1]``
   and metadata that is constant over the backtest. Exits at ``t + horizon`` use
   the actual mid at that index, but only for settlement, never for filtering.
2. **No random gating.** Every strategy is fully deterministic given its inputs;
   if you want to model "% of signals that are tradeable", do it explicitly via
   a confidence threshold, not ``np.random``.
3. **NO leg works.** Strategies emit ``BUY_NO`` whenever the model probability
   is below the market and the engine sizes/settles them correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

try:
    from .engine import Engine, Position, DIR_NO, DIR_YES
except ImportError:  # allow `python run.py` from inside the v003/ folder
    from engine import Engine, Position, DIR_NO, DIR_YES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _price_at(price_history: Dict[str, list], team: str, date_idx: int) -> Optional[float]:
    series = price_history.get(team)
    if not series or date_idx < 0 or date_idx >= len(series):
        return None
    return series[date_idx]["price"]


def _past_prices(price_history, team, date_idx, lookback) -> List[float]:
    """Strict past window [date_idx - lookback + 1 .. date_idx]."""
    out: List[float] = []
    for i in range(max(0, date_idx - lookback + 1), date_idx + 1):
        p = _price_at(price_history, team, i)
        if p is not None:
            out.append(p)
    return out


def _signal(direction: str, model_prob: float, mid: float, threshold: float) -> bool:
    if direction == DIR_YES:
        return (model_prob - mid) > threshold
    return (mid - model_prob) > threshold


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------
@dataclass
class StrategySpec:
    name: str
    fn: Callable
    description: str


def s2_cross_platform(engine: Engine, price_history, market_lookup, dates,
                      threshold: float = 0.015):
    """PM mid vs (de-vigged) Odds API implied probability.

    The legacy version used the raw implied probability which is inflated by the
    bookmaker overround. We strip that out per-team using the assumption that
    the overround scales the true prob uniformly (rough but better than raw).
    """
    name = "S2_CrossPlatform"
    teams = list(price_history.keys())

    # De-vig: rescale all bookmaker implied probs so they sum to 1.
    raw = {t: market_lookup[t].get("odds_implied_prob") for t in teams
           if market_lookup[t].get("odds_implied_prob") is not None}
    s = sum(raw.values()) or 1.0
    devig = {t: v / s for t, v in raw.items()}

    horizon = 1
    open_positions: List[Position] = []

    for t in range(len(dates) - horizon):
        date_str = dates[t]
        # 1. close any positions whose horizon is up.
        still_open: List[Position] = []
        for pos in open_positions:
            if pos.entry_date <= date_str and (t > 0 and dates[t - 1] >= pos.entry_date):
                # entered before today; close at today's mid
                mid = _price_at(price_history, pos.team, t)
                if mid is not None:
                    engine.close_position(pos, mid, date_str)
                    continue
            still_open.append(pos)
        open_positions = still_open

        # 2. emit new entries based on past info only.
        for team in teams:
            mid = _price_at(price_history, team, t)
            sharp = devig.get(team)
            if mid is None or sharp is None:
                continue
            if sharp - mid > threshold:
                pos = engine.open_position(name, team, DIR_YES, mid, sharp, date_str, horizon)
                if pos:
                    open_positions.append(pos)
            elif mid - sharp > threshold:
                pos = engine.open_position(name, team, DIR_NO, mid, sharp, date_str, horizon)
                if pos:
                    open_positions.append(pos)

        # 3. mark to market
        snapshot = {team: _price_at(price_history, team, t) for team in teams}
        snapshot = {k: v for k, v in snapshot.items() if v is not None}
        engine.mark_to_market(snapshot, date_str)

    # Close any leftovers at the last available price.
    last_idx = len(dates) - 1
    last_date = dates[last_idx]
    for pos in open_positions:
        mid = _price_at(price_history, pos.team, last_idx)
        if mid is not None:
            engine.close_position(pos, mid, last_date)


def s3_momentum(engine: Engine, price_history, market_lookup, dates,
                lookback: int = 5, threshold: float = 0.01):
    """Trend continuation. ``model_prob = clip(mid * (1 + slope * 0.5))``.

    The legacy version called this "Dixon-Coles" but it is just a momentum
    extrapolator. Renamed honestly. Decision uses only past prices.
    """
    name = "S3_Momentum"
    teams = list(price_history.keys())
    horizon = 1
    open_positions: List[Position] = []

    for t in range(lookback, len(dates) - horizon):
        date_str = dates[t]

        # close positions opened the previous day
        still_open: List[Position] = []
        for pos in open_positions:
            mid = _price_at(price_history, pos.team, t)
            if mid is not None:
                engine.close_position(pos, mid, date_str)
            else:
                still_open.append(pos)
        open_positions = still_open

        for team in teams:
            past = _past_prices(price_history, team, t, lookback)
            if len(past) < 2 or past[0] <= 0:
                continue
            slope = (past[-1] - past[0]) / past[0]
            mid = past[-1]
            model_prob = max(0.001, min(0.999, mid * (1 + slope * 0.5)))
            if model_prob - mid > threshold:
                pos = engine.open_position(name, team, DIR_YES, mid, model_prob, date_str, horizon)
                if pos:
                    open_positions.append(pos)
            elif mid - model_prob > threshold:
                pos = engine.open_position(name, team, DIR_NO, mid, model_prob, date_str, horizon)
                if pos:
                    open_positions.append(pos)

        snapshot = {team: _price_at(price_history, team, t) for team in teams}
        snapshot = {k: v for k, v in snapshot.items() if v is not None}
        engine.mark_to_market(snapshot, date_str)

    last_idx = len(dates) - 1
    last_date = dates[last_idx]
    for pos in open_positions:
        mid = _price_at(price_history, pos.team, last_idx)
        if mid is not None:
            engine.close_position(pos, mid, last_date)


def s5_mean_reversion(engine: Engine, price_history, market_lookup, dates,
                      lookback: int = 7, z_threshold: float = 1.0):
    """Buy oversold (>= z below MA), sell overbought (>= z above MA).

    Uses a rolling Z-score over the *past* lookback window; the model prob is
    the rolling mean. Look-ahead is structurally impossible because the decision
    only references past prices.
    """
    name = "S5_MeanReversion"
    teams = list(price_history.keys())
    horizon = 1
    open_positions: List[Position] = []

    for t in range(lookback, len(dates) - horizon):
        date_str = dates[t]

        still_open: List[Position] = []
        for pos in open_positions:
            mid = _price_at(price_history, pos.team, t)
            if mid is not None:
                engine.close_position(pos, mid, date_str)
            else:
                still_open.append(pos)
        open_positions = still_open

        for team in teams:
            past = _past_prices(price_history, team, t, lookback)
            if len(past) < 3:
                continue
            mu = sum(past) / len(past)
            var = sum((p - mu) ** 2 for p in past) / max(1, len(past) - 1)
            sd = var ** 0.5
            if sd <= 0:
                continue
            mid = past[-1]
            z = (mid - mu) / sd
            if z <= -z_threshold:
                pos = engine.open_position(name, team, DIR_YES, mid, mu, date_str, horizon)
            elif z >= z_threshold:
                pos = engine.open_position(name, team, DIR_NO, mid, mu, date_str, horizon)
            else:
                pos = None
            if pos:
                open_positions.append(pos)

        snapshot = {team: _price_at(price_history, team, t) for team in teams}
        snapshot = {k: v for k, v in snapshot.items() if v is not None}
        engine.mark_to_market(snapshot, date_str)

    last_idx = len(dates) - 1
    last_date = dates[last_idx]
    for pos in open_positions:
        mid = _price_at(price_history, pos.team, last_idx)
        if mid is not None:
            engine.close_position(pos, mid, last_date)


def s6_longtail(engine: Engine, price_history, market_lookup, dates,
                p_max: float = 0.05, threshold: float = 0.005):
    """Long-tail single-leg strategy with NO bias.

    Long-tail teams are systematically over-priced because of lottery-ticket
    demand, so we should usually be SHORT them. This strategy enters NO when
    the current mid is more than ``threshold`` above its rolling 5-day mean.

    Long-tail status is determined **per-day from the rolling mean** to avoid
    the look-ahead trap of using ``market_lookup['last_price']`` (which is the
    final day of the dataset).
    """
    name = "S6_LongTail"
    teams = list(price_history.keys())
    horizon = 1
    open_positions: List[Position] = []

    for t in range(3, len(dates) - horizon):
        date_str = dates[t]
        still_open: List[Position] = []
        for pos in open_positions:
            mid = _price_at(price_history, pos.team, t)
            if mid is not None:
                engine.close_position(pos, mid, date_str)
            else:
                still_open.append(pos)
        open_positions = still_open

        for team in teams:
            past = _past_prices(price_history, team, t, 5)
            if len(past) < 3:
                continue
            mu = sum(past) / len(past)
            mid = past[-1]
            # Determine long-tail status from the rolling mean (no look-ahead).
            if mu >= p_max:
                continue
            # Long-tail teams typically mean-revert downward; bias toward NO.
            if mid - mu > threshold:
                pos = engine.open_position(name, team, DIR_NO, mid, mu, date_str, horizon)
                if pos:
                    open_positions.append(pos)

        snapshot = {team: _price_at(price_history, team, t) for team in teams}
        snapshot = {k: v for k, v in snapshot.items() if v is not None}
        engine.mark_to_market(snapshot, date_str)

    last_idx = len(dates) - 1
    last_date = dates[last_idx]
    for pos in open_positions:
        mid = _price_at(price_history, pos.team, last_idx)
        if mid is not None:
            engine.close_position(pos, mid, last_date)


REGISTRY: List[StrategySpec] = [
    StrategySpec("S2_CrossPlatform", s2_cross_platform,
                 "Mid vs de-vigged Odds API consensus"),
    StrategySpec("S3_Momentum", s3_momentum,
                 "Trend continuation on past lookback window"),
    StrategySpec("S5_MeanReversion", s5_mean_reversion,
                 "Z-score mean reversion vs rolling mean"),
    StrategySpec("S6_LongTail", s6_longtail,
                 "Long-tail short bias (NO leg) when mid > rolling mean"),
]


def get_strategy(name: str) -> StrategySpec:
    for spec in REGISTRY:
        if spec.name == name:
            return spec
    raise KeyError(f"strategy {name!r} not registered. Known: {[s.name for s in REGISTRY]}")
