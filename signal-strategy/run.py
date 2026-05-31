"""signal-strategy — 回测运行入口 (CLI)

用法:
  python run.py                 # 合成数据, 默认 20000 事件
  python run.py --events 50000 --seed 11
  python run.py --csv merged.csv    # 接入真实合并数据

输出:
  1) 多方向策略 (A+B + 全部过滤) 的指标
  2) "朴素跟随" 基线 (无脑追涨) 作对照, 证明过滤的价值
  3) 样本内/样本外切分对比 (检测过拟合)
  4) 多维度归因 (方向 / 时段 / regime / 滞后桶 / 标的类)
"""

from __future__ import annotations

import argparse
import os
import sys

# 允许直接 `python run.py` (模块间用裸名导入)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datafeed import make_dataset, load_events_csv, EventRow      # noqa: E402
from signals import generate_signals, Signal                       # noqa: E402
from backtest import (                                             # noqa: E402
    run_backtest, by_direction, by_session_tier, by_regime, by_lag, by_universe,
)


# ------------------------------------------------------------
# 基线策略: 朴素跟随 (看到盈利信号就追多, 不加任何过滤)
# 用来证明"真实数据里裸信号无 edge" + 过滤层的增量价值
# ------------------------------------------------------------
def naive_follow(ev: EventRow):
    if ev.gf_score_30m <= 50:
        return []
    return [Signal(
        ts=ev.ts, contract=ev.contract, direction="N", side="LONG",
        entry_price=ev.price, hold_bars=3, raw_edge_pct=0.3, confidence=1.0,
        stop_pct=None, regime="na", note="naive",
    )]


def _print_metrics(title: str, res):
    m = res.metrics()
    print(f"\n{'='*64}\n{title}\n{'='*64}")
    if m.get("trades", 0) == 0:
        print("  无交易")
        return
    order = [
        ("events", "事件数"), ("signals", "信号数"), ("signal_rate_pct", "信号率%"),
        ("trades", "成交数"), ("win_rate_pct", "胜率%"), ("total_pnl", "总盈亏U"),
        ("roi_pct", "ROI%"), ("final_equity", "期末权益U"), ("profit_factor", "盈亏比"),
        ("avg_win", "均盈U"), ("avg_loss", "均亏U"), ("max_drawdown_pct", "最大回撤%"),
        ("sharpe_per_trade", "夏普(每笔)"), ("stopped_count", "止损次数"),
    ]
    for k, label in order:
        if k in m:
            print(f"  {label:<12}: {m[k]}")


def _print_attr(title: str, attr: dict):
    print(f"\n  -- {title} --")
    print(f"  {'组':<18}{'笔数':>8}{'盈亏U':>12}{'胜率%':>8}")
    for g, v in attr.items():
        print(f"  {g:<18}{v['trades']:>8}{v['pnl']:>12.2f}{v['win_rate_pct']:>8.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=20_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--csv", type=str, default=None, help="真实合并 CSV 路径")
    ap.add_argument("--oos-split", type=float, default=0.7, help="样本内占比")
    args = ap.parse_args()

    if args.csv:
        print(f"加载真实数据: {args.csv}")
        events = load_events_csv(args.csv)
    else:
        print(f"生成合成数据 (标定到真实统计): {args.events} 事件, seed={args.seed}")
        print("⚠️ 合成数据仅用于验证逻辑, 上线前必须用 --csv 接入真实数据")
        events = make_dataset(n_events=args.events, seed=args.seed)
    print(f"事件总数: {len(events)}  时间范围: {events[0].ts.date()} ~ {events[-1].ts.date()}")

    # ---- 1) 多方向策略 (全量) ----
    res = run_backtest(events, generate_signals)
    _print_metrics("多方向策略 (A 反追涨 + B 恐慌反转 + 时段/标的/regime/stress 过滤)", res)

    # ---- 2) 朴素基线 ----
    base = run_backtest(events, naive_follow)
    _print_metrics("基线: 朴素跟随 (看到盈利信号就追多, 无过滤)", base)

    # ---- 3) 归因 ----
    print(f"\n{'='*64}\n归因分析 (多方向策略)\n{'='*64}")
    _print_attr("按方向", res.attribution(by_direction))
    _print_attr("按 regime", res.attribution(by_regime))
    _print_attr("按滞后桶", res.attribution(by_lag))
    _print_attr("按标的类", res.attribution(by_universe))
    _print_attr("按时段(部分)", _top_hours(res.attribution(by_session_tier)))

    # ---- 4) 样本内/外切分 ----
    k = int(len(events) * args.oos_split)
    res_in = run_backtest(events[:k], generate_signals)
    res_out = run_backtest(events[k:], generate_signals)
    _print_metrics(f"样本内 (前 {int(args.oos_split*100)}%)", res_in)
    _print_metrics(f"样本外 (后 {100-int(args.oos_split*100)}%)", res_out)

    print(f"\n{'='*64}")
    print("解读:")
    print("  - 若'多方向'显著优于'朴素基线', 说明过滤层 (时段/反追涨/标的) 提供了 edge")
    print("  - 若'样本外'胜率与样本内接近, 说明规则较稳健, 过拟合风险低")
    print("  - 这是合成数据的自检; 真实 edge 必须用 --csv 接入真实合并数据复现")
    print("=" * 64)


def _top_hours(attr: dict, n: int = 8) -> dict:
    items = sorted(attr.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
    head = dict(items[:n // 2])
    tail = dict(items[-n // 2:])
    head.update(tail)
    return head


if __name__ == "__main__":
    main()
