"""Synthetic dataset generator for the v003 backtest.

The legacy v002 code expected ``~/polymarket-wc-backtest/data/clean/wc2026_full_dataset.json``
which does not ship with the repo. This module produces a structurally richer
dataset entirely from a seeded RNG so every strategy direction can be exercised
without external data.

Generative process (internally consistent with the model layer)
---------------------------------------------------------------
1. **Ratings.** Each team gets an ELO rating drawn from a normal centred on
   1500 (a few favourites, a long tail), then recentred.
2. **True championship probabilities.** ``p_true`` is produced by running the
   *same* tournament Monte Carlo the strategies use (``tournament`` module). So
   the "fundamental" probability a model can recover is exactly the data
   generator's ground truth -- the market price is what wobbles around it.
3. **Polymarket mid path.** For each team the daily mid mean-reverts (OU) toward
   ``p_true`` from a random initial gap, plus daily observation noise. This is
   what creates value for momentum / mean-reversion / model strategies.
4. **Sharp odds series.** A daily "sharp" implied probability = ``p_true`` +
   small daily noise, with a bookmaker overround folded in. PM lags this, so the
   cross-market / CLV strategy has something to converge toward.
5. **Events.** A handful of (team, day) shocks inject a jump into the PM path
   plus a partial follow-through over the next two days (under-reaction), giving
   the event-driven strategy a real momentum signal.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

try:
    from .ratings import normalize_ratings
    from .tournament import championship_probabilities
except ImportError:
    from ratings import normalize_ratings
    from tournament import championship_probabilities


WC2026_TEAMS = [
    "Argentina", "Brazil", "France", "England", "Spain", "Germany", "Portugal",
    "Netherlands", "Italy", "Belgium", "Croatia", "Uruguay", "USA", "Mexico",
    "Canada", "Japan", "South Korea", "Senegal", "Morocco", "Colombia",
    "Switzerland", "Denmark", "Poland", "Ecuador", "Australia", "Iran",
    "Serbia", "Wales", "Tunisia", "Costa Rica", "Ghana", "Saudi Arabia",
]


@dataclass
class SynthConfig:
    n_days: int = 60
    start_date: str = "2026-04-01"
    seed: int = 42
    rating_spread: float = 90.0    # std of ELO ratings around 1500
    n_sims_true: int = 6000        # MC sims to compute ground-truth champ probs
    drift_kappa: float = 0.10      # OU mean-reversion speed toward p_true
    drift_sigma: float = 0.006     # OU innovation std (daily)
    init_gap_sigma: float = 0.022  # initial mispricing vs p_true
    obs_noise: float = 0.0025      # per-day observation noise on mid
    overround: float = 0.06        # bookmaker margin on the sharp odds (~6%)
    odds_noise: float = 0.007      # daily noise on the sharp estimate of p_true
    n_events: int = 24             # number of injected news shocks
    event_jump: float = 0.04       # ABSOLUTE jump magnitude (probability units)
    event_followthrough: float = 0.45  # fraction of jump that continues next day


def _gauss(rng: random.Random, mu: float = 0.0, sigma: float = 1.0) -> float:
    return rng.gauss(mu, sigma)


def _clip(x: float, lo: float = 0.001, hi: float = 0.999) -> float:
    return max(lo, min(hi, x))


def generate_dataset(cfg: SynthConfig = SynthConfig()) -> dict:
    rng = random.Random(cfg.seed)
    teams = list(WC2026_TEAMS)
    n = len(teams)

    # 1. ratings -----------------------------------------------------------
    raw_ratings = {t: 1500.0 + _gauss(rng, 0.0, cfg.rating_spread) for t in teams}
    ratings = normalize_ratings(raw_ratings)

    # 2. ground-truth championship probabilities (same model the strategy uses)
    p_true = championship_probabilities(
        teams, ratings, n_sims=cfg.n_sims_true, seed=cfg.seed + 1
    )

    # 3. build event schedule ---------------------------------------------
    #    events[(day_index, team)] = signed jump magnitude
    events: List[dict] = []
    event_map: Dict[tuple, float] = {}
    for _ in range(cfg.n_events):
        di = rng.randint(2, cfg.n_days - 4)  # leave room for follow-through
        team = rng.choice(teams)
        sign = 1.0 if rng.random() < 0.5 else -1.0
        mag = sign * cfg.event_jump * (0.5 + rng.random())  # absolute, 0.5x..1.5x base
        event_map[(di, team)] = event_map.get((di, team), 0.0) + mag
        events.append({"day_index": di, "team": team, "jump_abs": round(mag, 4)})

    start = date.fromisoformat(cfg.start_date)
    dates = [(start + timedelta(days=t)).isoformat() for t in range(cfg.n_days)]

    # 4. simulate PM mid paths + sharp odds series ------------------------
    price_history: Dict[str, List[dict]] = {t: [] for t in teams}
    odds_history: Dict[str, List[dict]] = {t: [] for t in teams}

    for team in teams:
        pt = p_true[team]
        # initial level mispriced relative to p_true
        level = _clip(pt + _gauss(rng, 0.0, cfg.init_gap_sigma))
        for t in range(cfg.n_days):
            # OU reversion toward p_true
            level = level + cfg.drift_kappa * (pt - level) + _gauss(rng, 0.0, cfg.drift_sigma)
            # event jump + follow-through (under-reaction), ABSOLUTE in prob units
            if (t, team) in event_map:
                level = _clip(level + event_map[(t, team)])
            for lag, frac in ((1, cfg.event_followthrough), (2, cfg.event_followthrough * 0.4)):
                if (t - lag, team) in event_map:
                    level = _clip(level + event_map[(t - lag, team)] * frac)
            mid = _clip(level + _gauss(rng, 0.0, cfg.obs_noise))
            price_history[team].append({"date": dates[t], "price": round(mid, 4)})

            # sharp odds: noisy estimate of p_true with overround
            sharp = _clip(pt + _gauss(rng, 0.0, cfg.odds_noise))
            implied = _clip(sharp * (1.0 + cfg.overround))
            odds_history[team].append({
                "date": dates[t],
                "implied_prob": round(implied, 4),
                "decimal_odds": round(1.0 / implied, 3),
            })

    # 5. assemble payload (v002-compatible + new fields) ------------------
    teams_payload = []
    for team in teams:
        series = price_history[team]
        first = series[0]["price"]
        last = series[-1]["price"]
        last_odds = odds_history[team][-1]
        teams_payload.append({
            "team": team,
            "elo": round(ratings[team], 1),
            "p_true": round(p_true[team], 5),
            "price_series": series,
            "odds_series": odds_history[team],
            "first_price": first,
            "last_price": last,
            "price_change": round(last - first, 4),
            "price_change_pct": round((last - first) / first * 100, 2) if first > 0 else 0,
            # static snapshot kept for backward-compat with legacy S2/S9/S10
            "odds_api": {
                "decimal_odds": last_odds["decimal_odds"],
                "implied_prob_pct": round(last_odds["implied_prob"] * 100, 2),
                "bookmaker": "synthetic_consensus",
            },
        })

    return {
        "schema": "wc2026_full_dataset.v003.synthetic",
        "generated_with_seed": cfg.seed,
        "n_days": cfg.n_days,
        "n_teams": n,
        "events": events,
        "teams": teams_payload,
    }


def dataset_to_views(ds: dict):
    """Reshape a dataset dict into ``(price_history, market_lookup, dates)``.

    ``market_lookup[team]`` carries: first/last price, static ``odds_implied_prob``
    (backward-compat), ``elo``, ``p_true`` (for diagnostics only -- strategies
    must not read it), and ``odds_series`` as a ``{date: implied}`` dict for the
    cross-market strategy.
    """
    price_history: Dict[str, List[dict]] = {}
    market_lookup: Dict[str, dict] = {}
    all_dates = set()
    for tdata in ds["teams"]:
        team = tdata["team"]
        series = tdata["price_series"]
        price_history[team] = series
        for pt in series:
            all_dates.add(pt["date"])
        odds = tdata.get("odds_api") or {}
        implied_pct = odds.get("implied_prob_pct")
        odds_series = {pt["date"]: pt["implied_prob"] for pt in tdata.get("odds_series", [])}
        market_lookup[team] = {
            "first_price": tdata["first_price"],
            "last_price": tdata["last_price"],
            "odds_implied_prob": (implied_pct / 100.0) if implied_pct is not None else None,
            "odds_decimal": odds.get("decimal_odds"),
            "odds_bookmaker": odds.get("bookmaker"),
            "elo": tdata.get("elo"),
            "p_true": tdata.get("p_true"),  # diagnostics only
            "odds_series": odds_series,
        }
    dates = sorted(all_dates)
    return price_history, market_lookup, dates


def save_dataset(path: str, cfg: SynthConfig = SynthConfig()) -> str:
    data = generate_dataset(cfg)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
    return path


def load_dataset(path: str):
    """Load ``wc2026_full_dataset.json`` and reshape into engine views."""
    with open(path) as fh:
        ds = json.load(fh)
    return dataset_to_views(ds)
