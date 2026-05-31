"""Double-Poisson match model with Dixon-Coles low-score correction.

Given expected goals ``(lambda_a, lambda_b)`` we build the joint score-line
probability grid. Independent Poisson over-states draws and 0-0/1-0/0-1/1-1
score lines; the Dixon-Coles ``tau`` correction (Dixon & Coles, 1997) damps or
boosts exactly those four cells with a single dependence parameter ``rho``.

From the grid we derive:

* ``match_outcome_probs`` -> (p_home_win, p_draw, p_away_win)
* ``simulate_knockout_winner`` -> 0 (A) or 1 (B), resolving draws by a penalty
  shootout whose win prob is tilted slightly toward the stronger side.
* ``simulate_league_points`` -> (points_a, points_b) for a group-stage match.

Pure stdlib (math + random); no numpy.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache
from typing import Tuple


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _dc_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles correction factor for the four low-score cells."""
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def score_grid(lam_a: float, lam_b: float, rho: float = -0.10,
               max_goals: int = 10):
    """Return a normalized (max_goals+1) x (max_goals+1) score-line grid."""
    grid = [[0.0] * (max_goals + 1) for _ in range(max_goals + 1)]
    total = 0.0
    for x in range(max_goals + 1):
        px = poisson_pmf(x, lam_a)
        for y in range(max_goals + 1):
            py = poisson_pmf(y, lam_b)
            p = px * py * _dc_tau(x, y, lam_a, lam_b, rho)
            p = max(0.0, p)  # tau can dip slightly negative for extreme rho
            grid[x][y] = p
            total += p
    if total > 0:
        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                grid[x][y] /= total
    return grid


_OUTCOME_CACHE: dict = {}


def match_outcome_probs(lam_a: float, lam_b: float, rho: float = -0.10,
                        max_goals: int = 10) -> Tuple[float, float, float]:
    """(p_home_win, p_draw, p_away_win) from the DC-corrected grid.

    Memoized on rounded lambdas so the tournament Monte Carlo (which evaluates
    the same handful of matchups millions of times) stays fast in pure Python.
    """
    key = (round(lam_a, 3), round(lam_b, 3), round(rho, 3), max_goals)
    cached = _OUTCOME_CACHE.get(key)
    if cached is not None:
        return cached
    grid = score_grid(lam_a, lam_b, rho, max_goals)
    p_home = p_draw = p_away = 0.0
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = grid[x][y]
            if x > y:
                p_home += p
            elif x == y:
                p_draw += p
            else:
                p_away += p
    out = (p_home, p_draw, p_away)
    _OUTCOME_CACHE[key] = out
    return out


def simulate_knockout_winner(rng: random.Random, lam_a: float, lam_b: float,
                             rho: float = -0.10) -> int:
    """Return 0 if A advances, 1 if B advances. Draws go to penalties.

    Penalty win prob is tilted toward the stronger side (by expected goals)
    but only mildly -- shootouts are close to a coin flip in practice.
    """
    p_home, p_draw, p_away = match_outcome_probs(lam_a, lam_b, rho)
    r = rng.random()
    if r < p_home:
        return 0
    if r < p_home + p_away:
        return 1
    # Draw -> penalties. Tilt slightly by relative strength.
    edge = (lam_a - lam_b)
    pen_a = max(0.35, min(0.65, 0.5 + 0.08 * edge))
    return 0 if rng.random() < pen_a else 1


def simulate_league_points(rng: random.Random, lam_a: float, lam_b: float,
                           rho: float = -0.10) -> Tuple[int, int]:
    """Group-stage points for one match: 3/0, 0/3, or 1/1."""
    p_home, p_draw, p_away = match_outcome_probs(lam_a, lam_b, rho)
    r = rng.random()
    if r < p_home:
        return 3, 0
    if r < p_home + p_draw:
        return 1, 1
    return 0, 3
