"""Direction 3 -- Cross-market value & Closing Line Value (CLV).

Unlike the legacy S2/S9/S10 (which compared PM to a single static odds
snapshot), this strategy consumes a **daily** sharp odds series. Each day:

1. Read the sharp implied probability for every team.
2. De-vig across the book (divide by the day's sum) so we compare like with
   like and strip the bookmaker overround.
3. Trade PM toward the de-vigged sharp line when the gap exceeds a threshold.

We also track **CLV**: for every entry, the closing-line value is the signed
move of the sharp line from entry to the final day. Positive average CLV is the
real test of whether a betting edge exists, independent of the realised PnL.
``engine.diagnostics['avg_clv']`` reports it.
"""

from __future__ import annotations

from typing import Dict, List

try:
    from .engine import Engine, DIR_NO, DIR_YES
    from .strat_utils import hold_loop, price_at, set_diag
except ImportError:
    from engine import Engine, DIR_NO, DIR_YES
    from strat_utils import hold_loop, price_at, set_diag


def _devig_for_date(market_lookup, teams, date_str) -> Dict[str, float]:
    raw = {}
    for team in teams:
        series = market_lookup.get(team, {}).get("odds_series") or {}
        v = series.get(date_str)
        if v is not None:
            raw[team] = v
    s = sum(raw.values())
    if s <= 0:
        return {}
    return {t: v / s for t, v in raw.items()}


def s_cross_market_clv(engine: Engine, price_history, market_lookup, dates,
                       threshold: float = 0.025, exit_threshold: float = 0.005,
                       min_price: float = 0.02):
    name = "S_CrossMarketCLV"
    teams = list(price_history.keys())

    # Pre-compute de-vigged sharp lines per date (constant given the dataset).
    devig_by_date = {d: _devig_for_date(market_lookup, teams, d) for d in dates}
    final_sharp = devig_by_date.get(dates[-1], {})
    clv_samples: List[float] = []
    seen_entries = set()

    def signal(team, t):
        mid = price_at(price_history, team, t)
        sp = devig_by_date.get(dates[t], {}).get(team)
        if mid is None or sp is None or mid < min_price:
            return None
        edge = sp - mid
        direction = DIR_YES if edge > 0 else DIR_NO
        # Record a CLV sample once per (team, day) crossing the entry threshold.
        if abs(edge) > threshold and (team, t) not in seen_entries:
            seen_entries.add((team, t))
            fs = final_sharp.get(team)
            if fs is not None:
                clv_samples.append((fs - mid) if direction == DIR_YES else (mid - fs))
        return direction, sp, abs(edge)

    hold_loop(engine, name, price_history, teams, dates, signal,
              entry_threshold=threshold, exit_threshold=exit_threshold,
              horizon=1, max_hold=None)

    avg_clv = sum(clv_samples) / len(clv_samples) if clv_samples else 0.0
    set_diag(engine, "avg_clv", round(avg_clv, 5))
    set_diag(engine, "clv_positive_pct",
             round(100.0 * sum(1 for c in clv_samples if c > 0) / len(clv_samples), 1)
             if clv_samples else 0.0)
