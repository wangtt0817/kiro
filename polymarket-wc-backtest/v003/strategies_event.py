"""Direction 5 -- Event-driven (under-reaction continuation).

Real news (injuries, suspensions, lineup leaks) shows up first as a sharp price
jump. Markets often *under-react* on the day of the move, so the price keeps
drifting the same way for a day or two. We detect the jump purely from the price
path (so no event feed is required at decision time) and trade the continuation.

Detection: a daily move ``|mid_t / mid_{t-1} - 1| > jump_threshold`` flags an
event at day ``t``. We then enter in the *same* direction for the ``t -> t+1``
window, sizing on an estimated follow-through fraction of the jump.

The synthetic generator injects exactly this kind of jump + partial
follow-through, so a correctly-built continuation strategy should be profitable
there; on real data the sign/size of the drift is an empirical question.
"""

from __future__ import annotations

from typing import List

try:
    from .engine import Engine, Position, DIR_NO, DIR_YES
    from .strat_utils import price_at, snapshot, set_diag
except ImportError:
    from engine import Engine, Position, DIR_NO, DIR_YES
    from strat_utils import price_at, snapshot, set_diag


def s_event_driven(engine: Engine, price_history, market_lookup, dates,
                   abs_threshold: float = 0.02, followthrough: float = 0.45,
                   min_price: float = 0.03):
    """Detect an absolute one-day price shock and trade its continuation.

    Prediction-market news is far better detected by *absolute* moves than
    relative ones: on a low-priced contract a 6% relative move is just noise,
    whereas a 1.5c absolute move is several standard deviations above the daily
    noise floor. We require ``|mid_t - mid_{t-1}| > abs_threshold`` and trade in
    the direction of the move for the ``t -> t+1`` window.
    """
    name = "S_EventDriven"
    teams = list(price_history.keys())
    horizon = 1
    open_positions: List[Position] = []
    n_signals = 0

    for t in range(1, len(dates) - horizon):
        date_str = dates[t]

        still_open: List[Position] = []
        for pos in open_positions:
            mid = price_at(price_history, pos.team, t)
            if mid is not None:
                engine.close_position(pos, mid, date_str)
            else:
                still_open.append(pos)
        open_positions = still_open

        for team in teams:
            mid = price_at(price_history, team, t)
            prev = price_at(price_history, team, t - 1)
            if mid is None or prev is None or prev <= 0 or mid < min_price:
                continue
            change = mid - prev  # ABSOLUTE move in probability units
            if abs(change) <= abs_threshold:
                continue
            n_signals += 1
            if change > 0:
                model_prob = min(0.999, mid + change * followthrough)
                direction = DIR_YES
            else:
                model_prob = max(0.001, mid + change * followthrough)
                direction = DIR_NO
            pos = engine.open_position(name, team, direction, mid, model_prob,
                                       date_str, horizon)
            if pos:
                open_positions.append(pos)

        engine.mark_to_market(snapshot(price_history, teams, t), date_str)

    last_idx = len(dates) - 1
    last_date = dates[last_idx]
    for pos in open_positions:
        mid = price_at(price_history, pos.team, last_idx)
        if mid is not None:
            engine.close_position(pos, mid, last_date)
    set_diag(engine, "event_signals", n_signals)
