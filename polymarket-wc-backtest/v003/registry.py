"""Single source of truth for every strategy the runner knows about.

Each entry's ``name`` MUST match the ``name`` the strategy function assigns to
its trades (``Trade.strategy``), because metrics are computed by filtering
``engine.trades`` on that name.

Strategies are grouped by "direction" so the runner / docs can present them by
theme rather than as a flat list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

try:
    from .strategies import s2_cross_platform, s3_momentum, s5_mean_reversion, s6_longtail
    from .strategies_arb import s_structural_arb
    from .strategies_model import s_model_value
    from .strategies_crossmarket import s_cross_market_clv
    from .strategies_mm import s_market_making
    from .strategies_event import s_event_driven
except ImportError:
    from strategies import s2_cross_platform, s3_momentum, s5_mean_reversion, s6_longtail
    from strategies_arb import s_structural_arb
    from strategies_model import s_model_value
    from strategies_crossmarket import s_cross_market_clv
    from strategies_mm import s_market_making
    from strategies_event import s_event_driven


@dataclass
class Spec:
    name: str
    direction: str
    fn: Callable
    description: str


# New, themed directions (the point of this PR).
DIRECTIONS: List[Spec] = [
    Spec("S_StructArb", "D1 Structural Arb", s_structural_arb,
         "Dutch-book detector + book-normalization mean reversion"),
    Spec("S_ModelValue", "D2 Model Value", s_model_value,
         "Tournament Monte-Carlo champion prob vs PM mid"),
    Spec("S_CrossMarketCLV", "D3 Cross-market/CLV", s_cross_market_clv,
         "Trade PM toward daily de-vigged sharp line; track CLV"),
    Spec("S_MarketMaking", "D4 Market Making", s_market_making,
         "Two-sided quoting with inventory skew"),
    Spec("S_EventDriven", "D5 Event-driven", s_event_driven,
         "Absolute price-shock continuation"),
]

# Legacy-but-cleaned reference strategies (no look-ahead, no random).
LEGACY: List[Spec] = [
    Spec("S2_CrossPlatform", "Legacy", s2_cross_platform, "Mid vs de-vigged static odds"),
    Spec("S3_Momentum", "Legacy", s3_momentum, "Trend continuation"),
    Spec("S5_MeanReversion", "Legacy", s5_mean_reversion, "Z-score mean reversion"),
    Spec("S6_LongTail", "Legacy", s6_longtail, "Long-tail short bias"),
]

ALL: List[Spec] = DIRECTIONS + LEGACY


def get(name: str) -> Spec:
    for s in ALL:
        if s.name == name:
            return s
    raise KeyError(f"unknown strategy {name!r}; known: {[s.name for s in ALL]}")
