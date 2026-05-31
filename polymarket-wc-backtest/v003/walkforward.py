"""Walk-forward harness + Probabilistic / Deflated Sharpe ratio.

Why this matters
----------------
The legacy v002 backtest reported a single in-sample Sharpe per strategy and
selected the best of 10. With selection bias and short series this number is
**meaningless**: a random strategy out of 10 candidates can easily clear
Sharpe ~1 on 30 daily observations.

We compute:

* **Probabilistic Sharpe Ratio (PSR)** -- the probability that the *true* Sharpe
  exceeds a threshold, given the observed Sharpe and the (skew, kurtosis) of
  daily returns. Bailey & Lopez de Prado, 2012.
* **Deflated Sharpe Ratio (DSR)** -- PSR with the threshold set to the expected
  maximum Sharpe under the null hypothesis when ``N`` candidate strategies were
  tried. Bailey & Lopez de Prado, 2014.

Walk-forward
------------
The harness splits the trading dates into ``n_splits`` consecutive folds. Each
fold is run independently (fresh engine, no parameter optimization across folds
for now -- this is a baseline). Out-of-sample metrics are reported per fold.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional, Tuple

try:
    from .engine import CostConfig, Engine, PortfolioConfig
except ImportError:
    from engine import CostConfig, Engine, PortfolioConfig


_EULER_MASCHERONI = 0.5772156649


# ---------------------------------------------------------------------------
# Standard normal CDF / quantile
# ---------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Beasley-Springer-Moro inverse normal."""
    p = min(max(p, 1e-12), 1 - 1e-12)
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# ---------------------------------------------------------------------------
# Statistics over PnL series
# ---------------------------------------------------------------------------
def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _skew_kurt(xs):
    if len(xs) < 4:
        return 0.0, 3.0
    m = _mean(xs)
    s = _std(xs)
    if s == 0:
        return 0.0, 3.0
    n = len(xs)
    g3 = sum(((x - m) / s) ** 3 for x in xs) / n
    g4 = sum(((x - m) / s) ** 4 for x in xs) / n
    return g3, g4


def sharpe(pnl_series: List[float], periods_per_year: int = 252) -> float:
    if len(pnl_series) < 2:
        return 0.0
    s = _std(pnl_series)
    if s == 0:
        return 0.0
    return (_mean(pnl_series) / s) * math.sqrt(periods_per_year)


def probabilistic_sharpe(pnl_series: List[float], sr_benchmark: float = 0.0,
                         periods_per_year: int = 252) -> float:
    """PSR(SR*) = P(true SR > sr_benchmark | observed)."""
    n = len(pnl_series)
    if n < 4:
        return 0.0
    sr = sharpe(pnl_series, periods_per_year)
    g3, g4 = _skew_kurt(pnl_series)
    # Convert annualized SR back to per-period for the formula.
    sr_per = sr / math.sqrt(periods_per_year)
    sr_bench_per = sr_benchmark / math.sqrt(periods_per_year)
    denom = math.sqrt(max(1e-12, 1 - g3 * sr_per + (g4 - 1) / 4 * sr_per ** 2))
    z = (sr_per - sr_bench_per) * math.sqrt(n - 1) / denom
    return _norm_cdf(z)


def expected_max_sharpe_null(n_trials: int) -> float:
    """E[max SR_i | SR_i ~ N(0, 1)] for ``n_trials`` independent strategies."""
    if n_trials <= 1:
        return 0.0
    g = _EULER_MASCHERONI
    return (1 - g) * _norm_ppf(1 - 1.0 / n_trials) + g * _norm_ppf(1 - 1.0 / (n_trials * math.e))


def deflated_sharpe(pnl_series: List[float], n_trials: int,
                    periods_per_year: int = 252) -> float:
    """DSR = PSR with benchmark = annualized E[max SR] under null."""
    if len(pnl_series) < 4 or n_trials <= 0:
        return 0.0
    sr_star_per = expected_max_sharpe_null(n_trials)  # this is per-period
    sr_star_annual = sr_star_per * math.sqrt(periods_per_year)
    return probabilistic_sharpe(pnl_series, sr_star_annual, periods_per_year)


# ---------------------------------------------------------------------------
# Trade-level metrics
# ---------------------------------------------------------------------------
@dataclass
class StrategyMetrics:
    name: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    roi_pct: float
    avg_pnl: float
    max_drawdown_pct: float
    profit_factor: float
    sharpe: float
    psr_zero: float          # P(true SR > 0)
    dsr: float               # deflated against n_trials
    final_capital: float
    avg_holding_days: float
    n_buy_yes: int
    n_buy_no: int


def metrics_for(engine: Engine, strategy_name: str, n_trials: int) -> StrategyMetrics:
    trades = [t for t in engine.trades if t.strategy == strategy_name]
    pnls = [t.pnl for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    n = len(trades)
    daily = engine.daily_pnl_series()
    sr = sharpe(daily)
    psr0 = probabilistic_sharpe(daily, 0.0)
    dsr = deflated_sharpe(daily, n_trials)
    win_pnl = sum(p for p in pnls if p > 0)
    loss_pnl = -sum(p for p in pnls if p < 0)
    pf = (win_pnl / loss_pnl) if loss_pnl > 0 else float("inf")

    return StrategyMetrics(
        name=strategy_name,
        total_trades=n,
        wins=wins,
        losses=losses,
        win_rate=(wins / n) if n else 0.0,
        total_pnl=sum(pnls),
        roi_pct=(sum(pnls) / engine.pcfg.initial_capital * 100.0) if n else 0.0,
        avg_pnl=(sum(pnls) / n) if n else 0.0,
        max_drawdown_pct=engine.max_drawdown * 100.0,
        profit_factor=min(pf, 99.9),
        sharpe=sr,
        psr_zero=psr0,
        dsr=dsr,
        final_capital=engine.capital,
        avg_holding_days=(sum(t.holding_days for t in trades) / n) if n else 0.0,
        n_buy_yes=sum(1 for t in trades if t.direction == "BUY_YES"),
        n_buy_no=sum(1 for t in trades if t.direction == "BUY_NO"),
    )


# ---------------------------------------------------------------------------
# Walk-forward harness
# ---------------------------------------------------------------------------
@dataclass
class FoldResult:
    fold: int
    train_dates: Tuple[str, str]
    test_dates: Tuple[str, str]
    metrics: StrategyMetrics


def _slice_history(price_history, dates_subset):
    keep = set(dates_subset)
    out = {}
    for team, series in price_history.items():
        out[team] = [pt for pt in series if pt["date"] in keep]
    return out


def walk_forward(
    strategy_fn: Callable,
    strategy_name: str,
    price_history,
    market_lookup,
    dates,
    n_splits: int = 3,
    n_trials: int = 1,
    cost: Optional[CostConfig] = None,
    portfolio: Optional[PortfolioConfig] = None,
) -> List[FoldResult]:
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")
    fold_size = len(dates) // n_splits
    if fold_size < 5:
        raise ValueError(f"fold size {fold_size} too small; reduce n_splits")
    results: List[FoldResult] = []
    for k in range(n_splits):
        start = k * fold_size
        end = (k + 1) * fold_size if k < n_splits - 1 else len(dates)
        fold_dates = dates[start:end]
        fold_history = _slice_history(price_history, fold_dates)
        engine = Engine(cost=cost, portfolio=portfolio)
        strategy_fn(engine, fold_history, market_lookup, fold_dates)
        m = metrics_for(engine, strategy_name, n_trials=n_trials)
        results.append(FoldResult(
            fold=k,
            train_dates=(fold_dates[0], fold_dates[-1]),
            test_dates=(fold_dates[0], fold_dates[-1]),
            metrics=m,
        ))
    return results


def aggregate_folds(folds: List[FoldResult]) -> Dict[str, float]:
    if not folds:
        return {}
    agg = {
        "n_folds": len(folds),
        "total_trades": sum(f.metrics.total_trades for f in folds),
        "total_pnl": sum(f.metrics.total_pnl for f in folds),
        "avg_roi_pct": _mean([f.metrics.roi_pct for f in folds]),
        "avg_sharpe": _mean([f.metrics.sharpe for f in folds]),
        "avg_psr_zero": _mean([f.metrics.psr_zero for f in folds]),
        "avg_dsr": _mean([f.metrics.dsr for f in folds]),
        "avg_win_rate": _mean([f.metrics.win_rate for f in folds]),
        "min_roi_pct": min(f.metrics.roi_pct for f in folds),
        "max_dd_pct": max(f.metrics.max_drawdown_pct for f in folds),
    }
    return agg
