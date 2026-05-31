"""方向 C — 趋势/震荡判别 (Regime Classifier)

来源: grafana_profit_loss_gate_state_summary.json
  震荡(range): amp_15m 低 + 量比正常 + 稳定 -> 均值回归最有效
  趋势/承压(trend): 高振幅 / 放量 / 强动量 -> 禁止重仓逆势

作用: 不是独立策略, 而是给方向 A/B 的仓位乘子开关。
"""

from __future__ import annotations

from config import (
    REGIME_TREND_AMP_15M, REGIME_TREND_VOL_RATIO, REGIME_TREND_RET_5M,
    REGIME_RANGE_AMP_15M, REGIME_RANGE_VOL_LOW, REGIME_RANGE_VOL_HIGH,
    REGIME_MULT,
)
from datafeed import EventRow


def classify_regime(ev: EventRow) -> str:
    """返回 'trend' / 'range' / 'neutral'。"""
    # 趋势/承压: 任一强信号
    if (ev.amp_15m >= REGIME_TREND_AMP_15M
            or ev.vol_ratio_5v30 >= REGIME_TREND_VOL_RATIO
            or abs(ev.ret_5m) >= REGIME_TREND_RET_5M):
        return "trend"
    # 震荡: 低振幅 + 量比正常
    if (ev.amp_15m < REGIME_RANGE_AMP_15M
            and REGIME_RANGE_VOL_LOW <= ev.vol_ratio_5v30 <= REGIME_RANGE_VOL_HIGH):
        return "range"
    return "neutral"


def regime_multiplier(regime: str, direction: str) -> float:
    """给定 regime 与方向 ('A'/'B'), 返回仓位乘子。"""
    return REGIME_MULT.get(regime, REGIME_MULT["neutral"]).get(direction, 0.5)
