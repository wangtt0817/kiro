"""Direction 4 -- Market making with inventory skew.

A simplified Avellaneda-Stoikov style maker. Each day, for every team in the
chosen liquidity band, we post a two-sided quote around an inventory-adjusted
reservation price:

    reservation = mid - inventory_units * inventory_skew
    bid = reservation - half_spread
    ask = reservation + half_spread

``half_spread`` widens with recent volatility (we want to be paid more to
provide liquidity in choppy names). Inventory skew pushes both quotes down when
we are long, encouraging mean-reverting flow to lift our ask.

**Fill model (deterministic, no look-ahead in the decision).** Quotes are set at
day ``t`` using information up to ``t``. The fill is resolved by day ``t+1``'s
mid: if it falls to/through our bid we buy a unit; if it rises to/through our
ask and we hold inventory, we sell the oldest unit (FIFO). At most one fill per
team per day. Any inventory left at the end is liquidated at the last mid.

Each round-trip is recorded as a normal engine trade, so the maker's economics
flow through the same cash accounting and metrics as every other strategy.
"""

from __future__ import annotations

from typing import Dict, List

try:
    from .engine import Engine, Position, DIR_YES
    from .strat_utils import price_at, past_prices, rolling_std, snapshot
except ImportError:
    from engine import Engine, Position, DIR_YES
    from strat_utils import price_at, past_prices, rolling_std, snapshot


def s_market_making(engine: Engine, price_history, market_lookup, dates,
                    p_low: float = 0.03, p_high: float = 0.40,
                    base_half_spread: float = 0.012, vol_mult: float = 0.5,
                    inventory_skew: float = 0.004, max_inventory: int = 3,
                    unit_stake: float = 40.0):
    name = "S_MarketMaking"
    # Liquidity band chosen from the first observed price (no look-ahead).
    teams = []
    for team in price_history.keys():
        p0 = price_at(price_history, team, 0)
        if p0 is not None and p_low <= p0 <= p_high:
            teams.append(team)

    inventory: Dict[str, List[Position]] = {t: [] for t in teams}

    for t in range(len(dates) - 1):
        date_str = dates[t]
        for team in teams:
            mid = price_at(price_history, team, t)
            nxt = price_at(price_history, team, t + 1)
            if mid is None or nxt is None:
                continue

            recent = past_prices(price_history, team, t, 5)
            hs = base_half_spread + vol_mult * rolling_std(recent)
            inv_units = len(inventory[team])
            reservation = mid - inv_units * inventory_skew
            bid = reservation - hs
            ask = reservation + hs

            # Ask fill first (reduce inventory) -- FIFO.
            if inventory[team] and nxt >= ask:
                pos = inventory[team].pop(0)
                engine.close_at_fill(pos, exit_fill=min(0.999, ask),
                                     exit_mid=nxt, exit_date=dates[t + 1])
            # Bid fill (add inventory) if we have capacity.
            elif inv_units < max_inventory and nxt <= bid and bid > 0:
                pos = engine.enter(name, team, DIR_YES, fill_price=max(0.001, bid),
                                   stake=unit_stake, ts=date_str, model_prob=mid,
                                   mid_price=mid, horizon_days=1, apply_caps=True)
                if pos:
                    inventory[team].append(pos)

        engine.mark_to_market(snapshot(price_history, teams, t), date_str)

    # Liquidate remaining inventory at the last mid.
    last_idx = len(dates) - 1
    last_date = dates[last_idx]
    for team, lots in inventory.items():
        mid = price_at(price_history, team, last_idx)
        if mid is None:
            continue
        sell = engine.cost.sell_price(mid)
        for pos in lots:
            engine.close_at_fill(pos, exit_fill=sell, exit_mid=mid, exit_date=last_date)
    engine.mark_to_market(snapshot(price_history, teams, last_idx), last_date)
