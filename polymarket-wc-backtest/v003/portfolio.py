"""Direction 6 -- Portfolio meta-allocation.

Takes the per-strategy daily PnL streams and combines them into a single
portfolio. This adds no new alpha; its job is diversification -- lowering
combined volatility and drawdown by spreading capital across weakly-correlated
sub-strategies.

Weighting schemes
-----------------
* ``equal``       -- 1/N.
* ``inverse_vol`` -- w_i proportional to 1 / vol_i (a.k.a. naive risk parity;
  exact risk parity needs the covariance matrix but inverse-vol is the standard
  first-order approximation and is robust on short samples).
* ``vol_target``  -- inverse-vol weights, then the whole book is scaled so the
  combined series hits ``target_annual_vol``.

We also report the correlation matrix between strategies, because the
diversification benefit is entirely a function of those correlations.

All inputs are *dated* PnL series so strategies with different trade calendars
align correctly (missing days contribute zero PnL).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

try:
    from .walkforward import probabilistic_sharpe, sharpe
except ImportError:
    from walkforward import probabilistic_sharpe, sharpe


PERIODS_PER_YEAR = 252


def align_series(series_by_strategy: Dict[str, List[Tuple[str, float]]]):
    """Align dated PnL series onto the union of dates (0 fill for gaps).

    Returns ``(dates, matrix)`` where ``matrix[strategy]`` is a list aligned to
    ``dates``.
    """
    all_dates = set()
    for series in series_by_strategy.values():
        for d, _ in series:
            all_dates.add(d)
    dates = sorted(all_dates)
    index = {d: i for i, d in enumerate(dates)}

    matrix: Dict[str, List[float]] = {}
    for strat, series in series_by_strategy.items():
        row = [0.0] * len(dates)
        for d, pnl in series:
            row[index[d]] += pnl
        matrix[strat] = row
    return dates, matrix


def _std(xs: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))


def _corr(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    ma = sum(a[:n]) / n
    mb = sum(b[:n]) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((a[i] - ma) ** 2 for i in range(n))
    vb = sum((b[i] - mb) ** 2 for i in range(n))
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def correlation_matrix(matrix: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    names = list(matrix.keys())
    for a in names:
        out[a] = {}
        for b in names:
            out[a][b] = round(_corr(matrix[a], matrix[b]), 3)
    return out


def compute_weights(matrix: Dict[str, List[float]], method: str = "inverse_vol") -> Dict[str, float]:
    names = list(matrix.keys())
    if not names:
        return {}
    if method == "equal":
        w = 1.0 / len(names)
        return {n: w for n in names}

    # inverse-vol (also the base for vol_target)
    inv = {}
    for n in names:
        v = _std(matrix[n])
        inv[n] = (1.0 / v) if v > 0 else 0.0
    s = sum(inv.values())
    if s <= 0:
        w = 1.0 / len(names)
        return {n: w for n in names}
    return {n: inv[n] / s for n in names}


@dataclass
class PortfolioResult:
    method: str
    weights: Dict[str, float]
    leverage: float
    total_return_pct: float
    annual_vol_pct: float
    sharpe: float
    psr_zero: float
    max_drawdown_pct: float
    combined_daily_returns: List[float]
    correlations: Dict[str, Dict[str, float]]


def combine(series_by_strategy: Dict[str, List[Tuple[str, float]]],
            method: str = "inverse_vol",
            initial_capital: float = 10_000.0,
            target_annual_vol: float = 0.10) -> PortfolioResult:
    dates, matrix = align_series(series_by_strategy)
    names = list(matrix.keys())

    # convert PnL ($) to per-strategy returns (fraction of capital)
    ret_matrix = {n: [p / initial_capital for p in matrix[n]] for n in names}
    weights = compute_weights(ret_matrix, method if method != "vol_target" else "inverse_vol")

    horizon = len(dates)
    combined = [0.0] * horizon
    for n in names:
        w = weights[n]
        row = ret_matrix[n]
        for i in range(horizon):
            combined[i] += w * row[i]

    leverage = 1.0
    if method == "vol_target":
        realized_vol = _std(combined) * math.sqrt(PERIODS_PER_YEAR)
        if realized_vol > 0:
            leverage = target_annual_vol / realized_vol
            combined = [c * leverage for c in combined]

    # metrics on combined return series
    sr = sharpe(combined, PERIODS_PER_YEAR)
    psr0 = probabilistic_sharpe(combined, 0.0, PERIODS_PER_YEAR)
    ann_vol = _std(combined) * math.sqrt(PERIODS_PER_YEAR)

    # equity curve (additive returns) + drawdown
    equity = []
    cum = 1.0
    for r in combined:
        cum += r
        equity.append(cum)
    peak = equity[0] if equity else 1.0
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)
    total_return = (equity[-1] - 1.0) if equity else 0.0

    return PortfolioResult(
        method=method,
        weights={n: round(w, 4) for n, w in weights.items()},
        leverage=round(leverage, 3),
        total_return_pct=round(total_return * 100, 2),
        annual_vol_pct=round(ann_vol * 100, 2),
        sharpe=round(sr, 2),
        psr_zero=round(psr0, 3),
        max_drawdown_pct=round(max_dd * 100, 2),
        combined_daily_returns=combined,
        correlations=correlation_matrix(ret_matrix),
    )
