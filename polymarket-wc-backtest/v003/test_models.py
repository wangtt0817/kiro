"""Tests for the probability-model layer (ratings, poisson, tournament).

Run with::

    python test_models.py
"""

from __future__ import annotations

import os
import sys
import random

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ratings import expected_goals, win_prob_elo, normalize_ratings, ELO_BASE
from poisson import (
    match_outcome_probs,
    poisson_pmf,
    score_grid,
    simulate_knockout_winner,
    simulate_league_points,
)
from tournament import championship_probabilities, make_groups


def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --- ratings ---------------------------------------------------------------
def test_win_prob_symmetry():
    assert _approx(win_prob_elo(1500, 1500), 0.5)
    assert win_prob_elo(1700, 1500) > 0.5
    assert _approx(win_prob_elo(1700, 1500) + win_prob_elo(1500, 1700), 1.0)


def test_expected_goals_monotonic():
    la_strong, lb_weak = expected_goals(1800, 1400)
    assert la_strong > lb_weak
    la_eq, lb_eq = expected_goals(1500, 1500)
    assert _approx(la_eq, lb_eq)


def test_normalize_ratings_recenters():
    r = normalize_ratings({"a": 1600, "b": 1400, "c": 1500})
    assert _approx(sum(r.values()) / 3, ELO_BASE)


# --- poisson ---------------------------------------------------------------
def test_poisson_pmf_sums_to_one():
    s = sum(poisson_pmf(k, 1.5) for k in range(40))
    assert _approx(s, 1.0, tol=1e-6)


def test_score_grid_normalized():
    grid = score_grid(1.6, 1.1, rho=-0.1, max_goals=10)
    total = sum(sum(row) for row in grid)
    assert _approx(total, 1.0, tol=1e-9)


def test_outcome_probs_sum_to_one_and_favour_strong():
    ph, pd, pa = match_outcome_probs(2.0, 0.8)
    assert _approx(ph + pd + pa, 1.0, tol=1e-9)
    assert ph > pa  # stronger expected goals -> more home wins


def test_outcome_probs_symmetric_for_equal():
    ph, pd, pa = match_outcome_probs(1.3, 1.3)
    assert _approx(ph, pa, tol=1e-9)


def test_knockout_winner_favours_strong():
    rng = random.Random(0)
    wins_a = sum(simulate_knockout_winner(rng, 2.2, 0.7) == 0 for _ in range(3000))
    assert wins_a > 2000  # strong side wins clearly more than half


def test_league_points_valid():
    rng = random.Random(1)
    pa, pb = simulate_league_points(rng, 1.5, 1.2)
    assert (pa, pb) in [(3, 0), (1, 1), (0, 3)]


# --- tournament ------------------------------------------------------------
def _ratings(n=32):
    return {f"T{i:02d}": 1750 - i * 11 for i in range(n)}


def test_make_groups_partitions_all_teams():
    r = _ratings(32)
    groups = make_groups(list(r.keys()), r, group_size=4)
    assert len(groups) == 8
    flat = [t for g in groups for t in g]
    assert sorted(flat) == sorted(r.keys())


def test_championship_probs_sum_to_one():
    r = _ratings(32)
    probs = championship_probabilities(list(r.keys()), r, n_sims=2000, seed=3)
    assert _approx(sum(probs.values()), 1.0, tol=1e-9)


def test_championship_probs_strong_beats_weak():
    r = _ratings(32)
    probs = championship_probabilities(list(r.keys()), r, n_sims=4000, seed=4)
    strongest = max(r, key=r.get)
    weakest = min(r, key=r.get)
    assert probs[strongest] > probs[weakest]
    assert probs[strongest] > 0.03


def test_championship_probs_reproducible():
    r = _ratings(16)
    p1 = championship_probabilities(list(r.keys()), r, n_sims=1500, seed=9)
    p2 = championship_probabilities(list(r.keys()), r, n_sims=1500, seed=9)
    assert p1 == p2


# --- runner ----------------------------------------------------------------
def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    print(f"running {len(tests)} model tests")
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {fn.__name__}: {e!r}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
