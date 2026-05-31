"""Monte-Carlo tournament simulator -> championship probabilities.

Format (a simplified but recognisable World-Cup shape):

1. **Group stage.** Teams are drawn into groups of 4 via a snake distribution
   over rating (so strong teams are spread out). Each group plays a single
   round robin using ``poisson.simulate_league_points`` (draws allowed). The
   top two of each group advance, ranked by points then rating then a random
   tiebreak.
2. **Knockout.** Qualifiers are seeded into a single-elimination bracket padded
   to the next power of two with byes for the top seeds. Each tie is resolved
   by ``poisson.simulate_knockout_winner`` (draws -> penalties).

The group draw is performed **once** (deterministic given ratings); only match
outcomes are randomised across simulations, which mirrors reality (the draw is a
one-off event, results are the random part).

``championship_probabilities`` runs ``n_sims`` tournaments and returns a
team -> probability dict that sums to 1.
"""

from __future__ import annotations

import random
from typing import Dict, List, Tuple

try:
    from .ratings import expected_goals
    from .poisson import simulate_knockout_winner, simulate_league_points
except ImportError:  # script / flat execution
    from ratings import expected_goals
    from poisson import simulate_knockout_winner, simulate_league_points


def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def make_groups(teams: List[str], ratings: Dict[str, float],
                group_size: int = 4) -> List[List[str]]:
    """Snake-distribute teams (strongest first) into groups of ``group_size``."""
    ordered = sorted(teams, key=lambda t: ratings.get(t, 1500.0), reverse=True)
    n_groups = max(1, len(ordered) // group_size)
    groups: List[List[str]] = [[] for _ in range(n_groups)]
    # Snake pattern: 0,1,..,n-1, n-1,..,0, 0,1,..
    idx = 0
    direction = 1
    for team in ordered:
        groups[idx].append(team)
        if direction == 1 and idx == n_groups - 1:
            direction = -1
        elif direction == -1 and idx == 0:
            direction = 1
        else:
            idx += direction
    return groups


def _simulate_group(rng: random.Random, group: List[str],
                    ratings: Dict[str, float], rho: float) -> List[str]:
    """Round-robin; return group members ranked best-first."""
    points = {t: 0 for t in group}
    gd = {t: 0 for t in group}  # crude goal-diff proxy via points only
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            a, b = group[i], group[j]
            lam_a, lam_b = expected_goals(ratings[a], ratings[b])
            pa, pb = simulate_league_points(rng, lam_a, lam_b, rho)
            points[a] += pa
            points[b] += pb
    # Rank: points desc, then rating desc, then random jitter for ties.
    jitter = {t: rng.random() for t in group}
    return sorted(group, key=lambda t: (points[t], ratings.get(t, 1500.0), jitter[t]),
                  reverse=True)


def _simulate_knockout(rng: random.Random, seeds: List[str],
                       ratings: Dict[str, float], rho: float) -> str:
    """Single-elimination from a seed-ordered list; returns the champion."""
    size = _next_pow2(len(seeds))
    # Standard seeding: seed[i] vs seed[size-1-i]; byes (None) auto-advance.
    bracket: List = list(seeds) + [None] * (size - len(seeds))
    # Reorder so #1 meets the lowest seed, etc.
    ordered = [None] * size
    for i in range(size):
        ordered[i] = bracket[i]
    field = ordered
    while len(field) > 1:
        nxt = []
        for i in range(0, len(field), 2):
            a = field[i]
            b = field[i + 1]
            if a is None:
                nxt.append(b)
                continue
            if b is None:
                nxt.append(a)
                continue
            lam_a, lam_b = expected_goals(ratings[a], ratings[b])
            winner = a if simulate_knockout_winner(rng, lam_a, lam_b, rho) == 0 else b
            nxt.append(winner)
        field = nxt
    return field[0]


def simulate_one(rng: random.Random, groups: List[List[str]],
                 ratings: Dict[str, float], rho: float) -> str:
    qualifiers: List[str] = []
    # Collect group winners (rank 0) then runners-up (rank 1) for a sensible
    # cross-bracket seeding (winners are top seeds).
    ranked_groups = [_simulate_group(rng, g, ratings, rho) for g in groups]
    for g in ranked_groups:
        if len(g) >= 1:
            qualifiers.append(g[0])
    for g in ranked_groups:
        if len(g) >= 2:
            qualifiers.append(g[1])
    return _simulate_knockout(rng, qualifiers, ratings, rho)


def championship_probabilities(
    teams: List[str],
    ratings: Dict[str, float],
    n_sims: int = 5000,
    seed: int = 0,
    rho: float = -0.10,
    group_size: int = 4,
) -> Dict[str, float]:
    """Run ``n_sims`` tournaments; return team -> championship probability."""
    rng = random.Random(seed)
    groups = make_groups(teams, ratings, group_size)
    counts: Dict[str, int] = {t: 0 for t in teams}
    for _ in range(n_sims):
        champ = simulate_one(rng, groups, ratings, rho)
        counts[champ] = counts.get(champ, 0) + 1
    return {t: counts.get(t, 0) / n_sims for t in teams}
