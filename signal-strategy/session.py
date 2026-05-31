"""方向 D — 时段执行闸门 (Session Gate)

来源: 盈利数据.txt STEP 9 (UTC 时段净盈利 + 胜率)。
作用: 全局乘子, 叠加在所有方向之上。乘子 0.0 = 禁止开新仓。
这是确定性最高、成本最低的收益放大器, 建议第一个上线。
"""

from __future__ import annotations

from config import SESSION_MULT


def session_multiplier(hour_utc: int) -> float:
    """返回该 UTC 小时的仓位乘子 [0.0, 1.0]。"""
    return SESSION_MULT.get(hour_utc % 24, 0.5)


def is_tradeable_hour(hour_utc: int) -> bool:
    """该时段是否允许开新仓 (乘子 > 0)。"""
    return session_multiplier(hour_utc) > 0.0


# 便于报表展示的分级
def session_tier(hour_utc: int) -> str:
    m = session_multiplier(hour_utc)
    if m == 0.0:
        return "forbidden"
    if m >= 0.9:
        return "prime"
    if m >= 0.65:
        return "good"
    return "weak"
