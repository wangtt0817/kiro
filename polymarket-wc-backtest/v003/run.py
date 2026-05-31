"""CLI runner for the v003 backtest.

Examples
--------
Run all strategies on synthetic data with 3 walk-forward folds::

    python -m v003.run --synthetic --walk-forward 3

Run on the legacy real dataset (path to v002 JSON)::

    python -m v003.run --data ~/polymarket-wc-backtest/data/clean/wc2026_full_dataset.json

Subset of strategies::

    python -m v003.run --synthetic --strategies S2_CrossPlatform,S5_MeanReversion
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import List

import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

try:
    from .engine import CostConfig, Engine, PortfolioConfig
    from .strategies import REGISTRY, get_strategy
    from .synth_data import SynthConfig, generate_dataset, load_dataset, save_dataset
    from .walkforward import aggregate_folds, metrics_for, walk_forward
except ImportError:
    from engine import CostConfig, Engine, PortfolioConfig
    from strategies import REGISTRY, get_strategy
    from synth_data import SynthConfig, generate_dataset, load_dataset, save_dataset
    from walkforward import aggregate_folds, metrics_for, walk_forward


def _print_table(rows: List[dict], cols: List[str]):
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in cols}
    sep = "  ".join("-" * widths[c] for c in cols)
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print(sep)
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def _row_from_metrics(m, extra=None):
    base = {
        "Strategy": m.name,
        "Trades": m.total_trades,
        "YES": m.n_buy_yes,
        "NO": m.n_buy_no,
        "Win%": f"{m.win_rate * 100:.1f}",
        "PnL$": f"{m.total_pnl:.2f}",
        "ROI%": f"{m.roi_pct:.2f}",
        "MaxDD%": f"{m.max_drawdown_pct:.2f}",
        "Sharpe": f"{m.sharpe:.2f}",
        "PSR(0)": f"{m.psr_zero:.2f}",
        "DSR": f"{m.dsr:.2f}",
        "PF": f"{m.profit_factor:.2f}",
        "Hold(d)": f"{m.avg_holding_days:.1f}",
    }
    if extra:
        base.update(extra)
    return base


def main(argv=None):
    p = argparse.ArgumentParser(description="Polymarket WC backtest v003 runner")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", action="store_true",
                     help="generate a synthetic dataset on the fly")
    src.add_argument("--data", type=str,
                     help="path to a wc2026_full_dataset.json file")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--strategies", type=str, default="",
                   help="comma-separated subset of strategy names; default = all")
    p.add_argument("--walk-forward", type=int, default=1, metavar="N",
                   help="number of walk-forward folds (default 1 = full sample)")
    p.add_argument("--save-dataset", type=str, default="",
                   help="if set with --synthetic, write the dataset to this path")
    p.add_argument("--output", type=str, default="",
                   help="if set, write JSON results to this path")
    p.add_argument("--fee", type=float, default=0.02, help="taker fee rate (default 0.02)")
    args = p.parse_args(argv)

    # Load / generate data
    if args.synthetic:
        cfg = SynthConfig(seed=args.seed)
        dataset = generate_dataset(cfg)
        if args.save_dataset:
            os.makedirs(os.path.dirname(args.save_dataset) or ".", exist_ok=True)
            with open(args.save_dataset, "w") as fh:
                json.dump(dataset, fh, indent=2)
        # Inline reshape so we don't have to write+reread.
        price_history = {}
        market_lookup = {}
        all_dates = set()
        for tdata in dataset["teams"]:
            team = tdata["team"]
            price_history[team] = tdata["price_series"]
            for pt in tdata["price_series"]:
                all_dates.add(pt["date"])
            odds = tdata.get("odds_api") or {}
            implied_pct = odds.get("implied_prob_pct")
            market_lookup[team] = {
                "first_price": tdata["first_price"],
                "last_price": tdata["last_price"],
                "odds_implied_prob": (implied_pct / 100.0) if implied_pct is not None else None,
                "odds_decimal": odds.get("decimal_odds"),
                "odds_bookmaker": odds.get("bookmaker"),
            }
        dates = sorted(all_dates)
        print(f"[synthetic] seed={args.seed} teams={len(price_history)} days={len(dates)}")
    else:
        path = os.path.expanduser(args.data)
        if not os.path.exists(path):
            print(f"data file not found: {path}", file=sys.stderr)
            return 2
        price_history, market_lookup, dates = load_dataset(path)
        print(f"[real] file={path} teams={len(price_history)} days={len(dates)}")

    # Pick strategies
    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",") if s.strip()]
        specs = [get_strategy(n) for n in names]
    else:
        specs = list(REGISTRY)
    n_trials = len(specs)
    print(f"running {n_trials} strategies; walk-forward folds = {args.walk_forward}")

    cost = CostConfig(taker_fee_rate=args.fee)
    portfolio = PortfolioConfig()

    rows_full = []
    rows_walk = []
    raw_results = {}

    for spec in specs:
        # Full-sample run
        engine = Engine(cost=cost, portfolio=portfolio)
        spec.fn(engine, price_history, market_lookup, dates)
        m_full = metrics_for(engine, spec.name, n_trials=n_trials)
        rows_full.append(_row_from_metrics(m_full))

        wf_summary = None
        if args.walk_forward > 1:
            folds = walk_forward(
                spec.fn, spec.name, price_history, market_lookup, dates,
                n_splits=args.walk_forward, n_trials=n_trials,
                cost=cost, portfolio=portfolio,
            )
            agg = aggregate_folds(folds)
            wf_summary = agg
            rows_walk.append({
                "Strategy": spec.name,
                "Folds": agg["n_folds"],
                "Trades": agg["total_trades"],
                "TotalPnL$": f"{agg['total_pnl']:.2f}",
                "AvgROI%": f"{agg['avg_roi_pct']:.2f}",
                "MinROI%": f"{agg['min_roi_pct']:.2f}",
                "AvgSharpe": f"{agg['avg_sharpe']:.2f}",
                "AvgPSR(0)": f"{agg['avg_psr_zero']:.2f}",
                "AvgDSR": f"{agg['avg_dsr']:.2f}",
                "MaxDD%": f"{agg['max_dd_pct']:.2f}",
            })

        raw_results[spec.name] = {
            "full_sample": asdict(m_full),
            "walk_forward": wf_summary,
            "trades_sample": [
                {k: v for k, v in t.__dict__.items()}
                for t in engine.trades[:5]
            ],
        }

    print()
    print("=" * 100)
    print("FULL-SAMPLE METRICS")
    print("=" * 100)
    _print_table(rows_full, ["Strategy", "Trades", "YES", "NO", "Win%", "PnL$",
                              "ROI%", "MaxDD%", "Sharpe", "PSR(0)", "DSR", "PF", "Hold(d)"])

    if rows_walk:
        print()
        print("=" * 100)
        print(f"WALK-FORWARD ({args.walk_forward} folds)")
        print("=" * 100)
        _print_table(rows_walk, ["Strategy", "Folds", "Trades", "TotalPnL$",
                                  "AvgROI%", "MinROI%", "AvgSharpe",
                                  "AvgPSR(0)", "AvgDSR", "MaxDD%"])

    # Sanity counters that the legacy bug is gone.
    total_no = sum(int(r["NO"]) for r in rows_full)
    total_yes = sum(int(r["YES"]) for r in rows_full)
    print()
    print(f"Sanity: total BUY_YES = {total_yes}, total BUY_NO = {total_no}")
    if total_no == 0:
        print("WARNING: no BUY_NO trades were taken -- check strategy thresholds.")

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as fh:
            json.dump({
                "args": vars(args),
                "n_trials": n_trials,
                "results": raw_results,
            }, fh, indent=2, default=str)
        print(f"\nwrote {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
