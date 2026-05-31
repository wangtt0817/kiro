"""CLI runner for the v003 backtest (all 6 strategy directions + portfolio).

Examples
--------
All directions on synthetic data, with a 3-fold walk-forward and the portfolio
layer::

    python run.py --synthetic --walk-forward 3

Just the new directions (skip legacy), vol-targeted portfolio::

    python run.py --synthetic --group directions --portfolio vol_target

Real data (legacy v002 JSON layout, enriched fields optional)::

    python run.py --data ~/polymarket-wc-backtest/data/clean/wc2026_full_dataset.json

Add a fee to stress the strategies::

    python run.py --synthetic --fee 0.02
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from .engine import CostConfig, Engine, PortfolioConfig
    from .registry import ALL, DIRECTIONS, LEGACY, Spec, get
    from .synth_data import SynthConfig, generate_dataset, dataset_to_views, load_dataset
    from .walkforward import aggregate_folds, metrics_for, walk_forward
    from . import portfolio as pf
except ImportError:
    from engine import CostConfig, Engine, PortfolioConfig
    from registry import ALL, DIRECTIONS, LEGACY, Spec, get
    from synth_data import SynthConfig, generate_dataset, dataset_to_views, load_dataset
    from walkforward import aggregate_folds, metrics_for, walk_forward
    import portfolio as pf


# ---------------------------------------------------------------------------
def _print_table(rows: List[dict], cols: List[str]):
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def _row_from_metrics(m, direction: str):
    return {
        "Strategy": m.name,
        "Direction": direction,
        "Trades": m.total_trades,
        "YES": m.n_buy_yes,
        "NO": m.n_buy_no,
        "Win%": f"{m.win_rate * 100:.1f}",
        "PnL$": f"{m.total_pnl:.1f}",
        "ROI%": f"{m.roi_pct:.2f}",
        "MaxDD%": f"{m.max_drawdown_pct:.2f}",
        "Sharpe": f"{m.sharpe:.2f}",
        "PSR(0)": f"{m.psr_zero:.2f}",
        "DSR": f"{m.dsr:.2f}",
        "PF": f"{m.profit_factor:.2f}",
    }


def _dated_daily_pnl(engine: Engine) -> List[Tuple[str, float]]:
    eq = engine.equity_curve
    out: List[Tuple[str, float]] = []
    for i in range(1, len(eq)):
        out.append((eq[i][0], eq[i][1] - eq[i - 1][1]))
    return out


def _fmt_diag(diag: dict) -> str:
    d = dict(diag or {})
    top = d.pop("model_top5", None)
    parts = [f"{k}={v}" for k, v in d.items()]
    if top:
        parts.append("top=" + ",".join(f"{t}:{p:.3f}" for t, p in top[:3]))
    return "; ".join(parts)


# ---------------------------------------------------------------------------
def load_views(args):
    if args.synthetic:
        cfg = SynthConfig(seed=args.seed)
        dataset = generate_dataset(cfg)
        if args.save_dataset:
            os.makedirs(os.path.dirname(args.save_dataset) or ".", exist_ok=True)
            with open(args.save_dataset, "w") as fh:
                json.dump(dataset, fh, indent=2)
            print(f"[synthetic] wrote dataset to {args.save_dataset}")
        ph, ml, dates = dataset_to_views(dataset)
        print(f"[synthetic] seed={args.seed} teams={len(ph)} days={len(dates)} "
              f"events={len(dataset.get('events', []))}")
        return ph, ml, dates
    path = os.path.expanduser(args.data)
    if not os.path.exists(path):
        print(f"data file not found: {path}", file=sys.stderr)
        sys.exit(2)
    ph, ml, dates = load_dataset(path)
    print(f"[real] file={path} teams={len(ph)} days={len(dates)}")
    return ph, ml, dates


def select_specs(args) -> List[Spec]:
    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",") if s.strip()]
        return [get(n) for n in names]
    if args.group == "directions":
        return list(DIRECTIONS)
    if args.group == "legacy":
        return list(LEGACY)
    return list(ALL)


def main(argv=None):
    p = argparse.ArgumentParser(description="Polymarket WC backtest v003 -- all directions")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", action="store_true", help="generate synthetic data")
    src.add_argument("--data", type=str, help="path to wc2026_full_dataset.json")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--group", choices=["all", "directions", "legacy"], default="all")
    p.add_argument("--strategies", type=str, default="",
                   help="comma-separated subset (overrides --group)")
    p.add_argument("--walk-forward", type=int, default=1, metavar="N",
                   help="walk-forward folds (default 1 = full sample only)")
    p.add_argument("--portfolio", choices=["equal", "inverse_vol", "vol_target", "none"],
                   default="equal", help="portfolio combination method")
    p.add_argument("--target-vol", type=float, default=0.10,
                   help="annual vol target for --portfolio vol_target")
    p.add_argument("--fee", type=float, default=0.0,
                   help="taker fee rate (default 0.0 = current Polymarket reality)")
    p.add_argument("--half-spread", type=float, default=0.003,
                   help="mainstream half-spread in price units (default 0.003)")
    p.add_argument("--save-dataset", type=str, default="")
    p.add_argument("--output", type=str, default="")
    args = p.parse_args(argv)

    ph, ml, dates = load_views(args)
    specs = select_specs(args)
    n_trials = len(specs)
    cost = CostConfig(taker_fee_rate=args.fee, half_spread_default=args.half_spread,
                      half_spread_longtail=max(args.half_spread, 0.01))
    portfolio_cfg = PortfolioConfig()
    print(f"strategies={n_trials} fee={args.fee} half_spread={args.half_spread} "
          f"walk_forward={args.walk_forward} portfolio={args.portfolio}")

    rows_full: List[dict] = []
    rows_walk: List[dict] = []
    diag_lines: List[str] = []
    daily_by_strategy: Dict[str, List[Tuple[str, float]]] = {}
    raw_results: Dict[str, dict] = {}

    for spec in specs:
        engine = Engine(cost=cost, portfolio=portfolio_cfg)
        spec.fn(engine, ph, ml, dates)
        m = metrics_for(engine, spec.name, n_trials=n_trials)
        rows_full.append(_row_from_metrics(m, spec.direction))
        daily_by_strategy[spec.name] = _dated_daily_pnl(engine)

        diag = getattr(engine, "diagnostics", {})
        if diag:
            diag_lines.append(f"  {spec.name}: {_fmt_diag(diag)}")

        wf_summary = None
        if args.walk_forward > 1:
            try:
                folds = walk_forward(spec.fn, spec.name, ph, ml, dates,
                                     n_splits=args.walk_forward, n_trials=n_trials,
                                     cost=cost, portfolio=portfolio_cfg)
                agg = aggregate_folds(folds)
                wf_summary = agg
                rows_walk.append({
                    "Strategy": spec.name,
                    "Folds": agg["n_folds"],
                    "Trades": agg["total_trades"],
                    "TotPnL$": f"{agg['total_pnl']:.1f}",
                    "AvgROI%": f"{agg['avg_roi_pct']:.2f}",
                    "MinROI%": f"{agg['min_roi_pct']:.2f}",
                    "AvgSharpe": f"{agg['avg_sharpe']:.2f}",
                    "AvgDSR": f"{agg['avg_dsr']:.2f}",
                })
            except ValueError as e:
                rows_walk.append({"Strategy": spec.name, "Folds": "skip", "Trades": str(e)})

        raw_results[spec.name] = {"full_sample": asdict(m), "walk_forward": wf_summary,
                                  "diagnostics": diag}

    # ----- full sample table --------------------------------------------
    print()
    print("=" * 110)
    print("FULL-SAMPLE METRICS")
    print("=" * 110)
    _print_table(rows_full, ["Strategy", "Direction", "Trades", "YES", "NO", "Win%",
                             "PnL$", "ROI%", "MaxDD%", "Sharpe", "PSR(0)", "DSR", "PF"])

    if diag_lines:
        print("\nDiagnostics:")
        print("\n".join(diag_lines))

    total_yes = sum(int(r["YES"]) for r in rows_full)
    total_no = sum(int(r["NO"]) for r in rows_full)
    print(f"\nSanity: total BUY_YES={total_yes} BUY_NO={total_no} "
          f"(legacy v002 had BUY_NO=0 due to the sizing bug)")

    # ----- walk forward --------------------------------------------------
    if rows_walk:
        print()
        print("=" * 110)
        print(f"WALK-FORWARD ({args.walk_forward} folds)")
        print("=" * 110)
        _print_table(rows_walk, ["Strategy", "Folds", "Trades", "TotPnL$",
                                 "AvgROI%", "MinROI%", "AvgSharpe", "AvgDSR"])

    # ----- portfolio (deployable directions only; legacy shown as contrast) --
    portfolio_out = None
    portfolio_inputs = {s.name: daily_by_strategy[s.name]
                        for s in specs if s.direction != "Legacy" and s.name in daily_by_strategy}
    if len(portfolio_inputs) < 2:
        portfolio_inputs = daily_by_strategy  # fall back if only legacy selected
    if args.portfolio != "none" and len(portfolio_inputs) >= 2:
        print()
        print("=" * 110)
        print(f"PORTFOLIO (method={args.portfolio}, deployable directions only)")
        print("=" * 110)
        res = pf.combine(portfolio_inputs, method=args.portfolio,
                         initial_capital=portfolio_cfg.initial_capital,
                         target_annual_vol=args.target_vol)
        portfolio_out = asdict(res)
        portfolio_out.pop("combined_daily_returns", None)  # keep JSON small
        print(f"  members = {', '.join(portfolio_inputs.keys())}")
        print(f"  total_return = {res.total_return_pct:.2f}%   "
              f"annual_vol = {res.annual_vol_pct:.2f}%   "
              f"Sharpe = {res.sharpe:.2f}   PSR(0) = {res.psr_zero:.2f}   "
              f"maxDD = {res.max_drawdown_pct:.2f}%   leverage = {res.leverage}")
        print("  weights: " + ", ".join(f"{k}={v}" for k, v in res.weights.items()))
        print("\n  correlation matrix:")
        names = list(res.correlations.keys())
        short = {n: n.replace("S_", "").replace("_", "")[:8] for n in names}
        header = " " * 10 + "".join(f"{short[n]:>9}" for n in names)
        print(header)
        for a in names:
            row = "".join(f"{res.correlations[a][b]:>9.2f}" for b in names)
            print(f"  {short[a]:<8}{row}")

    # ----- output --------------------------------------------------------
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as fh:
            json.dump({"args": vars(args), "n_trials": n_trials,
                       "results": raw_results, "portfolio": portfolio_out},
                      fh, indent=2, default=str)
        print(f"\nwrote {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
