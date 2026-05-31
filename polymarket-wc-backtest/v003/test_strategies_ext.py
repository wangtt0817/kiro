"""Tests for the new strategy directions + portfolio layer.

Run with::

    python test_strategies_ext.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from engine import CostConfig, Engine, DIR_NO, DIR_YES
from synth_data import SynthConfig, generate_dataset, dataset_to_views
from strategies_arb import s_structural_arb
from strategies_model import s_model_value
from strategies_crossmarket import s_cross_market_clv
from strategies_mm import s_market_making
from strategies_event import s_event_driven
from strat_utils import hold_loop
import portfolio as pf


# Build one shared synthetic dataset (small + fast).
_DS = generate_dataset(SynthConfig(seed=42, n_days=40, n_sims_true=1500))
_PH, _ML, _DATES = dataset_to_views(_DS)
_COST = CostConfig(taker_fee_rate=0.0, half_spread_default=0.003, half_spread_longtail=0.01)


def _run(fn, **kw):
    e = Engine(cost=_COST)
    fn(e, _PH, _ML, _DATES, **kw)
    return e


def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --- each direction runs and trades both legs where expected ---------------
def test_structural_arb_runs_and_reports_dutch_days():
    e = _run(s_structural_arb)
    assert hasattr(e, "diagnostics")
    assert "dutch_arb_days" in e.diagnostics
    # accounting invariant
    assert _approx(sum(t.pnl for t in e.trades), e.capital - e.pcfg.initial_capital, tol=1e-3)


def test_model_value_uses_both_legs():
    e = _run(s_model_value, threshold=0.02)
    dirs = {t.direction for t in e.trades}
    assert e.trades, "model strategy produced no trades"
    assert DIR_NO in dirs, "model strategy must short overpriced teams"
    assert "model_top5" in e.diagnostics


def test_model_value_profitable_frictionless():
    cost = CostConfig(taker_fee_rate=0.0, half_spread_default=0.0, half_spread_longtail=0.0)
    e = Engine(cost=cost)
    s_model_value(e, _PH, _ML, _DATES, threshold=0.02)
    assert sum(t.pnl for t in e.trades) > 0, "model edge should be positive frictionless"


def test_cross_market_reports_clv():
    e = _run(s_cross_market_clv, threshold=0.02)
    assert "avg_clv" in e.diagnostics
    assert "clv_positive_pct" in e.diagnostics
    # in synthetic data the sharp line leads price, so CLV should be mostly positive
    assert e.diagnostics["clv_positive_pct"] > 50.0


def test_market_making_only_buys_yes_and_books_round_trips():
    e = _run(s_market_making)
    assert all(t.direction == DIR_YES for t in e.trades)
    assert _approx(sum(t.pnl for t in e.trades), e.capital - e.pcfg.initial_capital, tol=1e-3)


def test_event_driven_runs():
    e = _run(s_event_driven, abs_threshold=0.02)
    assert "event_signals" in e.diagnostics
    # signals should be on the order of injected events, not hundreds of noise hits
    assert e.diagnostics["event_signals"] < 100


# --- hold_loop hysteresis --------------------------------------------------
def test_hold_loop_reduces_turnover_vs_naive():
    """Holding should produce far fewer trades than re-deciding every day."""
    e = _run(s_model_value, threshold=0.02)
    n_hold = len(e.trades)
    # A daily-churn version would trade ~ (#signal-days). Compare to team-days.
    team_days = len(_PH) * len(_DATES)
    assert n_hold < team_days * 0.25, "hold_loop should not churn every day"


# --- portfolio -------------------------------------------------------------
def _series(fn, **kw):
    e = _run(fn, **kw)
    eq = e.equity_curve
    return [(eq[i][0], eq[i][1] - eq[i - 1][1]) for i in range(1, len(eq))]


def test_portfolio_equal_weights():
    series = {
        "A": _series(s_model_value),
        "B": _series(s_cross_market_clv),
        "C": _series(s_market_making),
    }
    res = pf.combine(series, method="equal", initial_capital=10_000)
    assert _approx(sum(res.weights.values()), 1.0, tol=2e-3)  # weights are display-rounded
    for w in res.weights.values():
        assert _approx(w, 1.0 / 3, tol=1e-3)


def test_portfolio_weights_sum_to_one_inverse_vol():
    series = {
        "A": _series(s_model_value),
        "B": _series(s_cross_market_clv),
        "C": _series(s_market_making),
    }
    res = pf.combine(series, method="inverse_vol", initial_capital=10_000)
    assert _approx(sum(res.weights.values()), 1.0, tol=1e-3)


def test_portfolio_correlation_diagonal_is_one():
    series = {"A": _series(s_model_value), "B": _series(s_market_making)}
    res = pf.combine(series, method="equal", initial_capital=10_000)
    assert _approx(res.correlations["A"]["A"], 1.0)
    assert _approx(res.correlations["B"]["B"], 1.0)
    # symmetry
    assert _approx(res.correlations["A"]["B"], res.correlations["B"]["A"])


def test_portfolio_vol_target_hits_target():
    series = {
        "A": _series(s_model_value),
        "B": _series(s_cross_market_clv),
        "C": _series(s_market_making),
    }
    res = pf.combine(series, method="vol_target", initial_capital=10_000,
                     target_annual_vol=0.10)
    # annual vol should be close to the 10% target (within rounding)
    assert abs(res.annual_vol_pct - 10.0) < 0.5


# --- runner ----------------------------------------------------------------
def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    print(f"running {len(tests)} strategy/portfolio tests")
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
