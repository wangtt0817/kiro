"""Synthetic dataset generator for the v003 backtest.

The legacy v002 code expected ``~/polymarket-wc-backtest/data/clean/wc2026_full_dataset.json``
which does not ship with the repo. This module produces a structurally identical
dataset (teams + price_series + odds_api) entirely from a seeded RNG so the
engine can be smoke-tested without any external data.

Generative process
------------------
* 32 teams. Each team's "true" championship probability ``p_true`` is drawn from
  a Dirichlet so they sum to 1, with concentration tuned to give a power-law-ish
  shape (a few favourites, a long tail).
* The Polymarket mid for team i on day t is
    ``mid[i, t] = clip(p_true[i] + drift[i, t] + noise[i, t], 0.001, 0.999)``
  where ``drift`` is a slow OU process and ``noise`` is daily Gaussian.
* The Odds API "implied probability" snapshot is a noisy estimate of ``p_true``
  with bookmaker overround so ``sum(odds_implied) > 1``. It is intentionally
  *different* from the live PM series — that is what creates exploitable edge
  for the cross-platform strategy.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List


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
    dirichlet_alpha: float = 0.6   # smaller -> more skewed toward favourites
    drift_kappa: float = 0.15      # mean-reversion speed of OU drift
    drift_sigma: float = 0.012     # OU noise (daily)
    obs_noise: float = 0.004       # per-day observation noise on mid
    overround: float = 0.07        # bookmaker margin on Odds API snapshot (~7%)
    odds_noise: float = 0.015      # noise on Odds API estimate of p_true


def _dirichlet(alphas: List[float], rng: random.Random) -> List[float]:
    # Sample via independent Gammas; pure-stdlib (no numpy dependency).
    samples = [rng.gammavariate(a, 1.0) for a in alphas]
    s = sum(samples)
    return [x / s for x in samples]


def _gauss(rng: random.Random, mu: float = 0.0, sigma: float = 1.0) -> float:
    return rng.gauss(mu, sigma)


def generate_dataset(cfg: SynthConfig = SynthConfig()) -> dict:
    rng = random.Random(cfg.seed)
    n = len(WC2026_TEAMS)

    # 1. true championship probabilities ---------------------------------
    alphas = [cfg.dirichlet_alpha] * n
    p_true = _dirichlet(alphas, rng)
    # Guarantee a head-shape by ordering descending and remapping:
    p_true.sort(reverse=True)

    # 2. simulate 60 days of Polymarket mids ------------------------------
    drift = [0.0] * n
    price_history: Dict[str, List[dict]] = {team: [] for team in WC2026_TEAMS}

    start = date.fromisoformat(cfg.start_date)
    for t in range(cfg.n_days):
        day = (start + timedelta(days=t)).isoformat()
        for i, team in enumerate(WC2026_TEAMS):
            # Ornstein-Uhlenbeck drift around 0.
            drift[i] = (1 - cfg.drift_kappa) * drift[i] + _gauss(rng, 0.0, cfg.drift_sigma)
            mid = p_true[i] + drift[i] + _gauss(rng, 0.0, cfg.obs_noise)
            mid = max(0.001, min(0.999, mid))
            price_history[team].append({"date": day, "price": round(mid, 4)})

    # 3. odds_api snapshot ------------------------------------------------
    odds_block: Dict[str, dict] = {}
    for i, team in enumerate(WC2026_TEAMS):
        # Noisy estimate of p_true with bookmaker overround folded in.
        est = p_true[i] * (1 + cfg.overround) + _gauss(rng, 0.0, cfg.odds_noise)
        est = max(0.001, min(0.999, est))
        decimal_odds = 1.0 / est
        odds_block[team] = {
            "decimal_odds": round(decimal_odds, 3),
            "implied_prob_pct": round(est * 100, 2),
            "bookmaker": "synthetic_consensus",
        }

    # 4. assemble in the v002 layout -------------------------------------
    teams_payload = []
    for team in WC2026_TEAMS:
        series = price_history[team]
        first = series[0]["price"]
        last = series[-1]["price"]
        teams_payload.append({
            "team": team,
            "price_series": series,
            "first_price": first,
            "last_price": last,
            "price_change": round(last - first, 4),
            "price_change_pct": round((last - first) / first * 100, 2) if first > 0 else 0,
            "odds_api": odds_block[team],
        })

    return {
        "schema": "wc2026_full_dataset.v003.synthetic",
        "generated_with_seed": cfg.seed,
        "n_days": cfg.n_days,
        "n_teams": n,
        "teams": teams_payload,
    }


def save_dataset(path: str, cfg: SynthConfig = SynthConfig()) -> str:
    data = generate_dataset(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
    return path


def load_dataset(path: str):
    """Reshape ``wc2026_full_dataset.json`` into ``(price_history, market_lookup, dates)``."""
    with open(path) as fh:
        ds = json.load(fh)

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
        market_lookup[team] = {
            "first_price": tdata["first_price"],
            "last_price": tdata["last_price"],
            "odds_implied_prob": (implied_pct / 100.0) if implied_pct is not None else None,
            "odds_decimal": odds.get("decimal_odds"),
            "odds_bookmaker": odds.get("bookmaker"),
        }
    dates = sorted(all_dates)
    return price_history, market_lookup, dates
