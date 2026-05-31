"""方向 E — 标的白/黑名单 (Universe Selection)

来源: 盈利数据.txt STEP 8 (代币级别盈亏)。
  白名单: 结构性盈利 (lab/skyai/beat/bsb/wld...)
  黑名单: 结构性亏损 (btc 单独 -390K, gua/zkj/siren...)
作用: 乘子 — 白名单全仓, 中性减半, 黑名单禁止信号跟随。

⚠️ 白/黑名单是历史拟合, 易过拟合。生产环境应改为"滚动30天净盈利动态评分",
   见 rolling_score() 接口。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, Tuple

from config import WHITELIST, BLACKLIST, UNIVERSE_MULT


def universe_class(contract: str) -> str:
    c = contract.lower()
    if c in BLACKLIST:
        return "black"
    if c in WHITELIST:
        return "white"
    return "neutral"


def universe_multiplier(contract: str) -> float:
    return UNIVERSE_MULT[universe_class(contract)]


def is_tradeable_contract(contract: str) -> bool:
    return universe_multiplier(contract) > 0.0


# ------------------------------------------------------------
# 动态评分 (推荐生产用法, 替代写死名单)
# ------------------------------------------------------------
def rolling_score(
    realized_pnl_by_contract: Dict[str, float],
    top_frac: float = 0.25,
    bottom_frac: float = 0.25,
) -> Dict[str, str]:
    """根据滚动窗口已实现盈亏, 把标的动态分为 white/neutral/black。

    realized_pnl_by_contract: {contract: 滚动窗口净盈亏}
    返回: {contract: "white"/"neutral"/"black"}
    """
    if not realized_pnl_by_contract:
        return {}
    items = sorted(realized_pnl_by_contract.items(), key=lambda kv: kv[1], reverse=True)
    n = len(items)
    n_top = max(1, int(n * top_frac))
    n_bot = max(1, int(n * bottom_frac))
    result: Dict[str, str] = {}
    for i, (c, _) in enumerate(items):
        if i < n_top:
            result[c] = "white"
        elif i >= n - n_bot:
            result[c] = "black"
        else:
            result[c] = "neutral"
    return result
