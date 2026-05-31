"""Accounting invariants for the v003 engine.

Run with::

    python test_engine.py
"""

from __future__ import annotations

import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from engine import (
    CostConfig,
    DIR_NO,
    DIR_YES,
    Engine,
    PortfolioConfig,
    kelly_fraction_no,
    kelly_fraction_yes,
)


def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
def test_kelly_yes_zero_when_no_edge():
    assert kelly_fraction_yes(0.5, 0.5) == 0.0
    assert kelly_fraction_yes(0.4, 0.5) == 0.0


def test_kelly_yes_positive_with_edge():
    f = kelly_fraction_yes(0.6, 0.5)
    assert _approx(f, 0.2)  # (0.6 - 0.5) / (1 - 0.5)


def test_kelly_no_zero_when_no_edge():
    assert kelly_fraction_no(0.5, 0.5) == 0.0
    assert kelly_fraction_no(0.6, 0.5) == 0.0  # YES edge, no NO edge


def test_kelly_no_positive_when_overpriced():
    # market p = 0.5, model p = 0.4 -> NO edge = 0.1 / 0.5 = 0.2
    f = kelly_fraction_no(0.4, 0.5)
    assert _approx(f, 0.2)


# ---------------------------------------------------------------------------
def test_cash_invariant_yes_no_change():
    """Open YES, close at same mid: PnL == -(round-trip costs), capital matches."""
    cost = CostConfig(taker_fee_rate=0.02, half_spread_default=0.005)
    eng = Engine(cost=cost, portfolio=PortfolioConfig(initial_capital=10_000))
    pos = eng.open_position("T", "Brazil", DIR_YES, 0.5, 0.6, "2026-04-01", 1)
    assert pos is not None
    eng.close_position(pos, 0.5, "2026-04-02")
    realized_pnl = sum(t.pnl for t in eng.trades)
    cash_change = eng.capital - eng.pcfg.initial_capital
    assert _approx(realized_pnl, cash_change), (realized_pnl, cash_change)
    # Negative because of fees + half-spread round-trip.
    assert realized_pnl < 0


def test_cash_invariant_no_leg_no_change():
    """Open NO, close at same mid: PnL == -(round-trip costs)."""
    cost = CostConfig(taker_fee_rate=0.0, half_spread_default=0.005)
    eng = Engine(cost=cost, portfolio=PortfolioConfig(initial_capital=10_000))
    # market p = 0.5, model = 0.4 -> NO has edge
    pos = eng.open_position("T", "Brazil", DIR_NO, 0.5, 0.4, "2026-04-01", 1)
    assert pos is not None, "NO trade must size and execute (legacy bug regression)"
    eng.close_position(pos, 0.5, "2026-04-02")
    realized_pnl = sum(t.pnl for t in eng.trades)
    cash_change = eng.capital - eng.pcfg.initial_capital
    assert _approx(realized_pnl, cash_change), (realized_pnl, cash_change)
    # With 0 fee, just the spread round-trip
    assert realized_pnl < 0
    assert eng.trades[0].direction == DIR_NO


def test_no_leg_profits_when_price_drops():
    """NO at p=0.6 closed at p=0.4 should be profitable (zero fee, zero spread)."""
    cost = CostConfig(taker_fee_rate=0.0, half_spread_default=0.0,
                      half_spread_longtail=0.0)
    eng = Engine(cost=cost)
    pos = eng.open_position("T", "Spain", DIR_NO, 0.6, 0.3, "2026-04-01", 1)
    assert pos is not None
    eng.close_position(pos, 0.4, "2026-04-02")
    pnl = eng.trades[0].pnl
    # Expected: bought NO at q=0.4, exit q=0.6 -> profit of 0.2 / 0.4 of stake
    expected = pos.stake * (0.6 - 0.4) / 0.4
    assert _approx(pnl, expected, tol=1e-3), (pnl, expected)
    assert pnl > 0


def test_yes_leg_profits_when_price_rises():
    cost = CostConfig(taker_fee_rate=0.0, half_spread_default=0.0,
                      half_spread_longtail=0.0)
    eng = Engine(cost=cost)
    pos = eng.open_position("T", "Argentina", DIR_YES, 0.4, 0.7, "2026-04-01", 1)
    assert pos is not None
    eng.close_position(pos, 0.6, "2026-04-02")
    pnl = eng.trades[0].pnl
    expected = pos.stake * (0.6 - 0.4) / 0.4
    assert _approx(pnl, expected, tol=1e-3)
    assert pnl > 0


# ---------------------------------------------------------------------------
def test_max_loss_bounded_by_stake():
    """Even worst-case (NO at p=0.4 -> exit p=0.999), loss can't exceed stake + fee_entry."""
    cost = CostConfig(taker_fee_rate=0.02, half_spread_default=0.005)
    eng = Engine(cost=cost)
    pos = eng.open_position("T", "Saudi Arabia", DIR_NO, 0.4, 0.1, "2026-04-01", 1)
    assert pos is not None
    eng.close_position(pos, 0.999, "2026-04-02")
    pnl = eng.trades[0].pnl
    max_possible_loss = -(pos.stake + pos.fee_paid_entry)
    assert pnl >= max_possible_loss - 1e-3, (pnl, max_possible_loss)


# ---------------------------------------------------------------------------
def test_portfolio_caps_enforced():
    cost = CostConfig(taker_fee_rate=0.0, half_spread_default=0.0,
                      half_spread_longtail=0.0)
    pcfg = PortfolioConfig(
        initial_capital=10_000,
        kelly_fraction=1.0,
        max_bet_pct=1.0,
        max_team_exposure_pct=0.05,   # 5% per team
        max_gross_exposure_pct=0.10,  # 10% gross
    )
    eng = Engine(cost=cost, portfolio=pcfg)
    p1 = eng.open_position("T", "Brazil", DIR_YES, 0.5, 0.99, "2026-04-01", 1)
    p2 = eng.open_position("T", "Brazil", DIR_YES, 0.5, 0.99, "2026-04-01", 1)
    # Second Brazil entry should be capped or rejected because team cap = 5% = $500
    assert p1 is not None
    if p2 is not None:
        assert p1.stake + p2.stake <= 10_000 * 0.05 + 1e-6
    # Try to add a 3rd team to exceed gross cap
    p3 = eng.open_position("T", "Argentina", DIR_YES, 0.5, 0.99, "2026-04-01", 1)
    p4 = eng.open_position("T", "France", DIR_YES, 0.5, 0.99, "2026-04-01", 1)
    total = sum(p.stake for p in (p1, p2, p3, p4) if p is not None)
    assert total <= 10_000 * 0.10 + 1e-6, total


# ---------------------------------------------------------------------------
def test_mark_to_market_equity_consistency():
    """Equity = cash + Σ MTM_value should equal initial_capital when nothing has happened."""
    eng = Engine()
    eng.mark_to_market({}, "2026-04-01")
    eq = eng.equity_curve[-1][1]
    assert _approx(eq, eng.pcfg.initial_capital)


def test_mark_to_market_drops_on_open():
    """Opening a position with cost > 0 should reduce equity by round-trip cost."""
    cost = CostConfig(taker_fee_rate=0.02, half_spread_default=0.005)
    eng = Engine(cost=cost)
    pos = eng.open_position("T", "Brazil", DIR_YES, 0.5, 0.7, "2026-04-01", 1)
    assert pos is not None
    eng.mark_to_market({"Brazil": 0.5}, "2026-04-01")
    eq = eng.equity_curve[-1][1]
    # Should be initial - (entry fee + half_spread loss + exit half_spread haircut on MTM)
    assert eq < eng.pcfg.initial_capital
    # And should be at least initial - 2*stake (sanity)
    assert eq > eng.pcfg.initial_capital - 2 * pos.stake


# ---------------------------------------------------------------------------
def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    print(f"running {len(tests)} tests")
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
