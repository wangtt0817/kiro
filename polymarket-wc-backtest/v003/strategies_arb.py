"""Direction 1 -- Structural arbitrage.

A champion market is a single multi-outcome book: the YES prices of all teams
should sum to ~$1. Two things can be exploited:

* **Strict Dutch book (riskless).** If the sum of *ask* prices across all teams
  is below $1, you can buy one share of every team's YES for less than the $1
  guaranteed payout. This is rare on real venues (market makers close it fast)
  and -- honestly -- almost never fires on the synthetic data either. The
  strategy still *detects and counts* these days (and trades them when present)
  so the logic is there for live data.

* **Book normalization (statistical).** Day to day the sum of mids drifts away
  from 1. We treat the normalized price ``mid_i / sum(mid)`` as the fair value
  and trade each leg toward it: buy underpriced legs (sum < 1), sell (BUY_NO)
  overpriced legs (sum > 1). This converges as the book re-normalizes.

Diagnostics written to ``engine.diagnostics``:
  ``dutch_arb_days`` -- count of days a strict riskless arb existed.
"""

from __future__ import annotations

from typing import List

try:
    from .engine import Engine, DIR_NO, DIR_YES
    from .strat_utils import hold_loop, price_at, snapshot, set_diag
except ImportError:
    from engine import Engine, DIR_NO, DIR_YES
    from strat_utils import hold_loop, price_at, snapshot, set_diag


def s_structural_arb(engine: Engine, price_history, market_lookup, dates,
                     threshold: float = 0.01, min_teams: int = 10,
                     min_price: float = 0.02):
    name = "S_StructArb"
    teams = list(price_history.keys())

    # --- pre-pass: count strict riskless Dutch-book days -----------------
    dutch_days = 0
    totals_by_t = {}
    for t in range(len(dates)):
        mids = snapshot(price_history, teams, t)
        totals_by_t[t] = sum(mids.values()) if mids else 0.0
        if len(mids) >= min_teams:
            asks = sum(engine.cost.buy_price(m) for m in mids.values())
            if asks < 1.0 - 1e-9:
                dutch_days += 1
    set_diag(engine, "dutch_arb_days", dutch_days)

    # --- statistical book-normalization (held until the book re-normalizes)
    def signal(team, t):
        total = totals_by_t.get(t, 0.0)
        mid = price_at(price_history, team, t)
        if mid is None or total <= 0 or mid < min_price:
            return None
        fair = mid / total  # normalized so the book sums to 1
        edge = fair - mid
        direction = DIR_YES if edge > 0 else DIR_NO
        return direction, fair, abs(edge)

    hold_loop(engine, name, price_history, teams, dates, signal,
              entry_threshold=threshold, exit_threshold=max(0.0, threshold * 0.3),
              horizon=1, max_hold=None)
