#!/usr/bin/env python3
"""PM足球策略 — 回测运行器"""

import json
import os
import sys
import numpy as np
from collections import defaultdict

# 确保能导入包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PM足球策略.config import INITIAL_CAPITAL, DATA_DIR, RESULTS_DIR, STRATEGY_STATUS
from PM足球策略.engine import BacktestEngine, compute_metrics
from PM足球策略.strategies import STRATEGIES, ACTIVE_STRATEGIES

os.makedirs(RESULTS_DIR, exist_ok=True)


def load_data():
    with open(f"{DATA_DIR}/football_dataset.json") as f:
        return json.load(f)


def correlation_matrix(all_trades):
    """策略间逐日收益相关性"""
    daily = defaultdict(dict)
    for name, trades in all_trades.items():
        for t in trades:
            daily[t.date].setdefault(name, 0)
            daily[t.date][name] += t.pnl

    names = list(all_trades.keys())
    dates = sorted(daily.keys())
    if len(dates) < 5:
        return

    returns = {n: [daily[d].get(n, 0) for d in dates] for n in names}
    profitable = [n for n in names if sum(returns[n]) > 0]
    if len(profitable) < 2:
        return

    print(f"\n{'=' * 80}")
    print("📊 策略逐日收益相关性矩阵")
    print("=" * 80)

    header = f"{'':>22}"
    for n in profitable:
        header += f"{n[:8]:>10}"
    print(header)

    for n1 in profitable:
        row = f"{n1[:22]:>22}"
        for n2 in profitable:
            r1, r2 = np.array(returns[n1]), np.array(returns[n2])
            if np.std(r1) > 0 and np.std(r2) > 0:
                corr = np.corrcoef(r1, r2)[0, 1]
                row += f"{corr:>10.2f}"
            else:
                row += f"{'N/A':>10}"
        print(row)


def run_backtest(strategies=None, verbose=True):
    """运行回测
    
    Args:
        strategies: 策略字典 {name: func}，默认全部 ACTIVE_STRATEGIES
        verbose: 是否打印详细结果
    Returns:
        dict: {strategy_name: metrics_dict}
    """
    if strategies is None:
        strategies = ACTIVE_STRATEGIES

    dataset = load_data()
    if verbose:
        print("=" * 100)
        print("PM足球策略 — 回测")
        print("=" * 100)
        print(f"\n数据: {dataset['metadata']['total_markets']} 市场, "
              f"{dataset['metadata']['total_data_points']} 点")
        print(f"\n{'=' * 100}")
        print(f"{'策略':<24} {'交易':>5} {'胜率':>6} {'PnL':>10} {'ROI%':>7} "
              f"{'MaxDD%':>7} {'PF':>7} {'Sharpe':>7} {'均赢':>7} {'均亏':>7}")
        print(f"{'=' * 100}")

    all_results = {}
    all_trades = {}

    for name, func in strategies.items():
        engine = BacktestEngine()
        result = func(engine, dataset)
        metrics = compute_metrics(result, engine)
        all_results[name] = metrics
        all_trades[name] = result.trades

        if verbose:
            status = STRATEGY_STATUS.get(name, "")
            marker = "✅" if metrics['total_pnl'] > 0 else "❌"
            print(f"{marker} {name:<22} {metrics['total_trades']:>5} "
                  f"{metrics['win_rate']:>5.1f}% ${metrics['total_pnl']:>8.2f} "
                  f"{metrics['roi_pct']:>6.2f}% {metrics['max_drawdown_pct']:>6.2f}% "
                  f"{metrics['profit_factor']:>6.2f} {metrics['sharpe']:>6.2f} "
                  f"${metrics['avg_win']:>5.2f} ${metrics['avg_loss']:>5.2f}")

    if verbose:
        correlation_matrix(all_trades)

        profitable = [(n, m) for n, m in all_results.items() if m['total_pnl'] > 0]
        profitable.sort(key=lambda x: x[1]['total_pnl'], reverse=True)
        total_pnl = sum(r['total_pnl'] for r in all_results.values())

        print(f"\n{'=' * 100}")
        print("📊 回测总结")
        print("=" * 100)
        print(f"  策略总数:    {len(all_results)}")
        print(f"  盈利策略:    {len(profitable)}")
        print(f"  Combined:    ${total_pnl:,.2f}")
        print(f"\n  🏆 排名:")
        for n, m in profitable:
            print(f"    {n:<24} PnL ${m['total_pnl']:>8.2f}  "
                  f"ROI {m['roi_pct']:>6.2f}%  PF {m['profit_factor']:>6.2f}")

    return all_results


def save_results(results, filename="backtest_results.json"):
    path = f"{RESULTS_DIR}/{filename}"
    with open(path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n💾 保存: {path}")


if __name__ == "__main__":
    np.random.seed(42)
    results = run_backtest()
    save_results(results, "backtest_results_v5.json")
