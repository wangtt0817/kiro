"""Shared helpers for v003 strategies.

Kept tiny and dependency-free so every strategy module can import it without
worrying about circular imports.
"""

from __future__ import annotations

from typing import Dict, List, Optional


def price_at(price_history: Dict[str, list], team: str, date_idx: int) -> Optional[float]:
    series = price_history.get(team)
    if not series or date_idx < 0 or date_idx >= len(series):
        return None
    return series[date_idx]["price"]


def past_prices(price_history, team, date_idx, lookback) -> List[float]:
    """Strict past window [date_idx - lookback + 1 .. date_idx] (inclusive)."""
    out: List[float] = []
    for i in range(max(0, date_idx - lookback + 1), date_idx + 1):
        p = price_at(price_history, team, i)
        if p is not None:
            out.append(p)
    return out


def snapshot(price_history, teams, date_idx) -> Dict[str, float]:
    out = {}
    for team in teams:
        p = price_at(price_history, team, date_idx)
        if p is not None:
            out[team] = p
    return out


def rolling_std(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mu = sum(values) / n
    return (sum((v - mu) ** 2 for v in values) / (n - 1)) ** 0.5


def set_diag(engine, key: str, value) -> None:
    """Attach a diagnostic value to the engine without disturbing metrics."""
    if not hasattr(engine, "diagnostics"):
        engine.diagnostics = {}
    engine.diagnostics[key] = value


def hold_loop(engine, name, price_history, teams, dates, signal_fn,
              entry_threshold: float, exit_threshold: float = 0.0,
              horizon: int = 1, max_hold: Optional[int] = None):
    """Generic 'enter on a strong edge, hold until it closes' driver.

    ``signal_fn(team, t)`` returns ``(direction, model_prob, edge_magnitude)`` or
    ``None``, using only information available at day ``t`` (strictly past prices
    + constant metadata). Decisions:

    * **Open** when flat and ``edge_magnitude > entry_threshold``.
    * **Hold** while the position's direction still has ``edge_magnitude >
      exit_threshold``.
    * **Close** when the edge falls to/below ``exit_threshold``, the edge flips
      direction, or ``max_hold`` days elapse.

    The entry/exit hysteresis is essential: exiting the instant the gap dips
    below the *entry* threshold captures too little to clear the spread, which
    silently turns a real edge into a net loss. Holding until the edge actually
    closes (``exit_threshold`` near 0) is what makes small-edge mean-reversion
    profitable after costs.
    """
    open_pos: Dict[str, object] = {}
    entry_idx: Dict[str, int] = {}
    last_idx = len(dates) - 1

    for t in range(len(dates)):
        date_str = dates[t]

        # 1. manage existing positions
        for team in list(open_pos.keys()):
            mid = price_at(price_history, team, t)
            if mid is None:
                continue
            pos = open_pos[team]
            sig = signal_fn(team, t)
            want_close = False
            if sig is None:
                want_close = True
            else:
                direction, _, edge_mag = sig
                if direction != pos.direction or edge_mag <= exit_threshold:
                    want_close = True
            if not want_close and max_hold is not None and (t - entry_idx[team]) >= max_hold:
                want_close = True
            if want_close:
                engine.close_position(pos, mid, date_str)
                del open_pos[team]
                entry_idx.pop(team, None)

        # 2. open new positions (not on the final day -- need room to exit)
        if t < last_idx:
            for team in teams:
                if team in open_pos:
                    continue
                mid = price_at(price_history, team, t)
                if mid is None:
                    continue
                sig = signal_fn(team, t)
                if sig is None:
                    continue
                direction, model_prob, edge_mag = sig
                if edge_mag <= entry_threshold:
                    continue
                pos = engine.open_position(name, team, direction, mid, model_prob,
                                           date_str, horizon)
                if pos:
                    open_pos[team] = pos
                    entry_idx[team] = t

        # 3. mark to market
        engine.mark_to_market(snapshot(price_history, teams, t), date_str)

    # close everything at the last mid
    last_date = dates[last_idx]
    for team, pos in list(open_pos.items()):
        mid = price_at(price_history, team, last_idx)
        if mid is not None:
            engine.close_position(pos, mid, last_date)
