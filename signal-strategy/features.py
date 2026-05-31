"""signal-strategy — 特征派生层

把 EventRow 的原始字段转成策略可直接消费的派生特征：
  - 滞后分桶 (lag bucket) + 该桶的方向/edge/持仓
  - stress 标志 (急跌 / 高振幅 / 放量)
  - 是否 capitulation 反转场景
这些函数是纯函数, 不含随机性, 真实数据与合成数据通用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config import (
    LAG_BUCKETS,
    STRESS_RET_5M_DOWN, STRESS_AMP_15M, STRESS_VOL_RATIO,
    CAPITULATION_PAST_DROP, CAPITULATION_MIN_SCORE, CAPITULATION_STOP_FALLING,
)
from datafeed import EventRow


@dataclass
class LagBucket:
    side: str          # "LONG" / "SHORT" / "FLAT"
    raw_edge_pct: float
    hold_bars: int
    label: str


def classify_lag(past_ret_3h: float) -> LagBucket:
    """按过去3h涨幅落入 STEP 7 的滞后分桶。"""
    for lo, hi, side, edge, hold in LAG_BUCKETS:
        if lo < past_ret_3h <= hi:
            label = f"({lo:g},{hi:g}]"
            return LagBucket(side=side, raw_edge_pct=edge, hold_bars=hold, label=label)
    # 落到最低桶 (past_ret <= 第一个区间上界)
    lo, hi, side, edge, hold = LAG_BUCKETS[0]
    return LagBucket(side=side, raw_edge_pct=edge, hold_bars=hold, label=f"(-inf,{hi:g}]")


@dataclass
class StressFlags:
    down_5m: bool       # ret_5m <= -3%
    high_amp: bool      # amp_15m >= 6
    high_vol: bool      # vol_ratio >= 3

    @property
    def any(self) -> bool:
        return self.down_5m or self.high_amp or self.high_vol

    @property
    def count(self) -> int:
        return int(self.down_5m) + int(self.high_amp) + int(self.high_vol)


def stress_flags(ev: EventRow) -> StressFlags:
    """计算 state_summary 中的三个 stress 信号。"""
    return StressFlags(
        down_5m=ev.ret_5m <= STRESS_RET_5M_DOWN,
        high_amp=ev.amp_15m >= STRESS_AMP_15M,
        high_vol=ev.vol_ratio_5v30 >= STRESS_VOL_RATIO,
    )


def is_capitulation(ev: EventRow) -> bool:
    """判断是否"急跌 + 大额盈利信号 + 止跌确认"的反转场景 (STEP 6 超大盈利)。"""
    return (
        ev.past_ret_3h <= CAPITULATION_PAST_DROP
        and ev.gf_score_30m >= CAPITULATION_MIN_SCORE
        and ev.ret_5m >= CAPITULATION_STOP_FALLING   # 不再创新低
    )


@dataclass
class Features:
    """单根事件的完整派生特征包。"""
    lag: LagBucket
    stress: StressFlags
    capitulation: bool


def derive(ev: EventRow) -> Features:
    return Features(
        lag=classify_lag(ev.past_ret_3h),
        stress=stress_flags(ev),
        capitulation=is_capitulation(ev),
    )
