"""Direction 2 -- Model value betting.

Build championship probabilities from *fundamentals* (ELO ratings) using the
double-Poisson + tournament Monte Carlo model, then trade the gap against the
Polymarket mid. This is the only strategy that injects information the price
series does not already contain, so it is the most likely source of genuine
alpha.

The model probabilities are computed **once** from the ratings (which are
constant fundamentals) and cached on the engine; they are not re-derived from
the price path, so there is no look-ahead.
"""

from __future__ import annotations

from typing import Dict, List, Optional

try:
    from .engine import Engine, Position, DIR_NO, DIR_YES
    from .strat_utils import hold_loop, price_at, set_diag
    from .tournament import championship_probabilities
except ImportError:
    from engine import Engine, Position, DIR_NO, DIR_YES
    from strat_utils import hold_loop, price_at, set_diag
    from tournament import championship_probabilities


def _ratings_from_lookup(market_lookup) -> Dict[str, float]:
    return {t: (m.get("elo") or 1500.0) for t, m in market_lookup.items()}


def s_model_value(engine: Engine, price_history, market_lookup, dates,
                  threshold: float = 0.025, exit_threshold: float = 0.005,
                  min_price: float = 0.02, n_sims: int = 3000, seed: int = 7,
                  champ_probs: Optional[Dict[str, float]] = None):
    name = "S_ModelValue"
    teams = list(price_history.keys())

    if champ_probs is None:
        ratings = _ratings_from_lookup(market_lookup)
        champ_probs = championship_probabilities(teams, ratings, n_sims=n_sims, seed=seed)
    set_diag(engine, "model_top5",
             sorted(champ_probs.items(), key=lambda x: -x[1])[:5])

    def signal(team, t):
        mid = price_at(price_history, team, t)
        mp = champ_probs.get(team)
        if mid is None or mp is None or mid < min_price:
            return None
        edge = mp - mid
        direction = DIR_YES if edge > 0 else DIR_NO
        return direction, mp, abs(edge)

    hold_loop(engine, name, price_history, teams, dates, signal,
              entry_threshold=threshold, exit_threshold=exit_threshold,
              horizon=1, max_hold=None)
