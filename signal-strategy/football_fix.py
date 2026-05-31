"""方向 F — 足球策略真实退出修复 (Football Fix)

问题 (前两轮分析已指出):
  PM足球策略 用 _simulate_price_change / _simulate_trend_exit 生成"有利的"退出价,
  并把 fair_value 设为 entry 之上, 导致回测出现近乎必然的高胜率 —— 这是循环论证。

本模块提供一个不依赖原包的、独立可运行的"诚实"做市回测:
  - 退出价 = 真实的下一根/未来第 k 根价格 (series[i+hold])
  - 成交率 = 真实低流动性水平 (config.FOOTBALL_FILL_RATE_REAL, 默认 10%)
  - 扣真实手续费 + 滑点
目的: 量化"模拟退出"相比"真实退出"夸大了多少胜率/盈亏。

数据输入: dataset = {"markets": [{"team":..., "filled_series":[{"date","price"}...]}, ...]}
与原 PM足球策略完全相同的结构, 因此可直接喂同一份数据对比。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional

from config import FOOTBALL_FEE_RATE, FOOTBALL_SLIPPAGE


def _half_spread(price: float, spread_frac: float = 0.03) -> float:
    """按价格比例的 half_spread (而非原框架的绝对值 0.02-0.05)。

    原框架在 p≈0.05 的市场用 0.03 绝对价差 = 60% 折价, 现实中根本不可能成交;
    这里改成 price × spread_frac (默认 3%), 并设一个很小的绝对下限。
    """
    return max(price * spread_frac, 0.001)


@dataclass
class FxTrade:
    date: str
    team: str
    entry: float
    exit: float
    size: float
    pnl: float
    win: bool


@dataclass
class FxResult:
    name: str
    trades: List[FxTrade] = field(default_factory=list)

    def metrics(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {"name": self.name, "trades": 0}
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        gw, gl = sum(wins), abs(sum(losses))
        return {
            "name": self.name,
            "trades": n,
            "win_rate_pct": round(len(wins) / n * 100, 1),
            "total_pnl": round(sum(t.pnl for t in self.trades), 2),
            "profit_factor": round(gw / gl, 2) if gl > 0 else 99.9,
            "avg_win": round(gw / len(wins), 2) if wins else 0,
            "avg_loss": round(-gl / len(losses), 2) if losses else 0,
        }


def market_make_real(
    dataset: dict,
    price_cap: float = 0.20,
    hold: int = 1,
    spread_frac: float = 0.03,
    size_per_trade: float = 100.0,
    model_adverse_selection: bool = True,
) -> FxResult:
    """诚实版做市回测: 退出价 = 真实未来价格, 且建模"挂单成交的逆向选择"。

    与原框架两处关键差异:
      1. 退出价 = series[i+hold]['price'] (真实), 不再用 _simulate_price_change
      2. 成交建模:
         - model_adverse_selection=True (默认): 限价买单只在价格真正下探到 bid 时成交
           (即 min(p, next_p) <= bid)。这会让成交集中在"下跌途中", 自然引入逆向选择,
           从而抵消价差收益 —— 这才是做市的真实风险。
         - False: 无条件按 fill 概率成交 (会高估做市收益, 仅作对照)。
    PnL = size*((exit-bid)/bid - 手续费 - 滑点)
    """
    res = FxResult(name=f"MM_real(cap={price_cap},hold={hold},spread={spread_frac},"
                        f"adverse={model_adverse_selection})")
    cost = FOOTBALL_FEE_RATE + FOOTBALL_SLIPPAGE
    for market in dataset.get("markets", []):
        series = market.get("filled_series") or []
        team = market.get("team", "?")
        if not series or series[-1]["price"] > price_cap:
            continue
        for i in range(len(series) - hold - 1):
            p = series[i]["price"]
            if p <= 0:
                continue
            hs = _half_spread(p, spread_frac)
            bid = max(0.003, p - hs)
            next_p = series[i + 1]["price"]
            if model_adverse_selection:
                # 限价买单是否成交, 取决于 bar 内最低价是否触及 bid。
                # 无 intrabar 数据时, 用 "min(开,收) 再向下伸出半个 bar 振幅" 近似下影线。
                bar_low = min(p, next_p) - 0.5 * abs(p - next_p)
                if bar_low > bid:
                    continue                         # 未触及, 不成交
            # 退出在成交那根 bar 之后的第 hold 根收盘 (与成交触发解耦, 避免自相关)
            exit_price = series[i + 1 + hold]["price"]   # ← 真实未来价格, 不模拟
            raw_ret = (exit_price - bid) / bid
            pnl = size_per_trade * (raw_ret - cost)
            res.trades.append(FxTrade(
                date=series[i]["date"], team=team, entry=round(bid, 4),
                exit=round(exit_price, 4), size=size_per_trade,
                pnl=round(pnl, 2), win=pnl > 0,
            ))
    return res


def trend_follow_real(
    dataset: dict,
    windows=(3, 5, 7),
    hold: int = 3,
    size_per_trade: float = 100.0,
) -> FxResult:
    """诚实版趋势回测: 全同向触发, 退出按真实未来价格 (不偏置 fair_value)。"""
    res = FxResult(name=f"Trend_real(win={windows},hold={hold})")
    for market in dataset.get("markets", []):
        series = market.get("filled_series") or []
        team = market.get("team", "?")
        n = len(series)
        start = max(windows)
        for i in range(start, n - hold):
            p = series[i]["price"]
            if p <= 0:
                continue
            trends = []
            ok = True
            for w in windows:
                pw = series[i - w]["price"]
                if pw <= 0:
                    ok = False
                    break
                trends.append((p - pw) / pw)
            if not ok:
                continue
            all_up = all(t > 0 for t in trends)
            all_down = all(t < 0 for t in trends)
            if not (all_up or all_down):
                continue
            side = 1 if all_up else -1                  # LONG / SHORT on YES
            exit_price = series[i + hold]["price"]      # ← 真实未来价格
            raw_ret = side * (exit_price - p) / p
            cost = FOOTBALL_FEE_RATE + FOOTBALL_SLIPPAGE
            pnl = size_per_trade * (raw_ret - cost)
            res.trades.append(FxTrade(
                date=series[i]["date"], team=team, entry=round(p, 4),
                exit=round(exit_price, 4), size=size_per_trade,
                pnl=round(pnl, 2), win=pnl > 0,
            ))
    return res


# ------------------------------------------------------------
# 演示: 合成一份足球价格序列, 对比"真实退出" vs "随机游走"基准
# ------------------------------------------------------------
def _demo_dataset(n_markets: int = 60, n_points: int = 120, seed: int = 1) -> dict:
    """生成低概率足球市场价格序列。

    用【可加性鞅】(driftless): price_next = price + N(0, vol_abs), 截断到 [0.005, 0.30]。
    真实低概率事件价格近似无漂移, 这样才能检验"策略是否凭空造出 edge"。
    vol_abs 设为价格的 ~4%, 与 spread (3%) 同量级, 使逆向选择效应可见。
    """
    rng = random.Random(seed)
    markets = []
    for m in range(n_markets):
        price = rng.uniform(0.03, 0.18)
        series = []
        for t in range(n_points):
            vol_abs = max(0.002, price * 0.04)
            price = max(0.005, min(0.30, price + rng.gauss(0, vol_abs)))  # 无漂移
            series.append({"date": f"2026-05-{(t % 28) + 1:02d}", "price": round(price, 4)})
        markets.append({"team": f"team_{m}", "filled_series": series})
    return {"markets": markets}


def demo():
    ds = _demo_dataset()
    mm_adverse = market_make_real(ds, model_adverse_selection=True)
    mm_naive = market_make_real(ds, model_adverse_selection=False)
    tr = trend_follow_real(ds)
    print("=" * 64)
    print("方向 F — 足球策略真实退出修复 (演示: 无漂移随机游走价格)")
    print("=" * 64)
    print("[做市·含逆向选择 (诚实)]   ", mm_adverse.metrics())
    print("[做市·无逆向选择 (高估对照)]", mm_naive.metrics())
    print("[趋势·真实退出]            ", tr.metrics())
    print("-" * 64)
    print("解读:")
    print("  1) 趋势策略在无漂移价格上扣费后必然亏损 (PF<1) —— 真实退出价戳破了")
    print("     原框架 _simulate_trend_exit 把 fair_value 偏向趋势方向的循环论证。")
    print("  2) 做市'无逆向选择'对照组看起来仍赚价差 —— 这正是原框架高估收益的来源:")
    print("     它假设挂单总能在好价位成交。")
    print("  3) 做市'含逆向选择'诚实组: 成交集中在下跌途中, 价差收益被逆向选择吃掉,")
    print("     胜率/盈亏比大幅回落 —— 这才是做市的真实风险面貌。")
    print("=" * 64)


if __name__ == "__main__":
    demo()
