"""Team strength ratings and rating -> expected-goals mapping.

We keep ratings on an ELO-like scale (base 1500). Two things are derived from a
pair of ratings:

* ``win_prob_elo`` -- the classic logistic win probability, used as a sanity
  check and for quick simulations.
* ``expected_goals`` -- a mapping from rating *difference* to the (lambda_a,
  lambda_b) goal-rate pair fed into the double-Poisson match model. The mapping
  is deliberately simple and documented so it can be recalibrated against real
  match data later.

None of this depends on numpy; everything is pure stdlib so the module can be
imported by both the dataset generator and the strategies without pulling in
heavy deps.
"""

from __future__ import annotations

from typing import Dict, Tuple


ELO_BASE = 1500.0

# Mapping constants (documented so they can be recalibrated):
#   ELO_PER_GOAL : how many ELO points correspond to ~1 goal of supremacy.
#   TOTAL_GOALS  : league-average total goals in a neutral match.
#   MIN_LAMBDA   : floor so a team always has *some* scoring chance.
ELO_PER_GOAL = 180.0
TOTAL_GOALS = 2.6
MIN_LAMBDA = 0.15


def win_prob_elo(rating_a: float, rating_b: float) -> float:
    """Logistic (ELO) probability that A beats B in a no-draw setting."""
    return 1.0 / (1.0 + 10.0 ** (-(rating_a - rating_b) / 400.0))


def expected_goals(rating_a: float, rating_b: float,
                   total_goals: float = TOTAL_GOALS) -> Tuple[float, float]:
    """Map a rating pair to expected goals (lambda_a, lambda_b).

    Supremacy (expected goal difference) scales linearly with rating gap; the
    sum of expected goals is held at ``total_goals``.
    """
    supremacy = (rating_a - rating_b) / ELO_PER_GOAL
    lam_a = max(MIN_LAMBDA, total_goals / 2.0 + supremacy / 2.0)
    lam_b = max(MIN_LAMBDA, total_goals / 2.0 - supremacy / 2.0)
    return lam_a, lam_b


def normalize_ratings(ratings: Dict[str, float]) -> Dict[str, float]:
    """Recenter ratings so the mean is ``ELO_BASE`` (keeps the goal mapping sane)."""
    if not ratings:
        return {}
    mean = sum(ratings.values()) / len(ratings)
    shift = ELO_BASE - mean
    return {team: r + shift for team, r in ratings.items()}
