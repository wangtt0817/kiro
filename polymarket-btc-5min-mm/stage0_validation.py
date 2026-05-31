#!/usr/bin/env python3
"""
stage0_validation.py — The "does the directional edge actually exist?" gate.

This is the experiment the design doc demands BEFORE any further engineering effort:
    "If I bet every time |edge| > 3% with confirmation, what is my hit-rate / Sharpe?"

It runs Direction 3 (the 4-layer directional engine) across many 5-minute windows and
produces the four diagnostics that decide whether the directional line is alive:

    1. Brier score & calibration  — is P_fair from BS digital actually well-calibrated
       against the real settlement distribution? (Doc target: Brier < 0.22.)
    2. Hit-rate vs |edge| buckets — does the strategy ACTUALLY win more often when the
       Layer 1 edge is bigger? If not, "edge" is noise.
    3. Hit-rate vs t_remaining   — the doc asserts the 120s..30s window is the
       sweet spot (60–75% certainty). We test that empirically.
    4. The official PASS/FAIL gate — win% > 54% AND profit-factor > 1.2 AND PnL > 0.

Three data sources are supported, in priority order:

    (A) REAL klines:  if any data/btc_1m_*.json exists, replay it. This is the
        only mode where a PASS verdict is meaningful for going to Stage 1.
    (B) Regime-switching synthetic: in-sandbox stand-in. Useful for validating that
        the PIPELINE behaves the way it should under heteroskedastic vol; CANNOT
        decide whether real-world edge exists.
    (C) Constant-vol synthetic: the simplest sanity baseline.

USAGE
-----
    # in the sandbox (no internet) -> regime-switching synthetic Stage 0 demo
    python stage0_validation.py
    python stage0_validation.py --mode regime --windows 5000

    # on a machine with internet, after fetching real klines:
    python fetch_btc_data.py --days 30
    python stage0_validation.py --mode real --klines data/btc_1m_30d_binance.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from typing import Iterable, List, Optional

from mm_core import (
    SimConfig, MarketWindow, simulate_window, simulate_window_regime,
    compute_metrics, WindowResult,
)
from direction3_directional_edge import DirectionalConfig, run as run_d3_on_iter

# We need to drive Direction 3 against an arbitrary stream of windows (synthetic OR
# real-replay), so we re-implement its inner loop here and parameterise it on the
# window iterator. The logic is byte-for-byte the same as direction3_directional_edge.
from direction4_cex_signal import confirmation_score


# ----------------------------------------------------------------------
# Window iterators for the three data sources
# ----------------------------------------------------------------------
def _iter_synthetic(n: int, seed: int, sim_cfg: SimConfig,
                    regime: bool) -> Iterable[MarketWindow]:
    rng = random.Random(seed)
    fn = simulate_window_regime if regime else simulate_window
    for _ in range(n):
        yield fn(sim_cfg, rng)


def _iter_real(klines_path: str, n: Optional[int], seed: int,
               sim_cfg: SimConfig) -> Iterable[MarketWindow]:
    # imported lazily so the module is importable without a klines file present
    from historical_replay import iter_replay_windows
    yield from iter_replay_windows(klines_path, sim_cfg, seed=seed, max_windows=n)


# ----------------------------------------------------------------------
# Direction-3 inner loop driven by an external window iterator
# ----------------------------------------------------------------------
def _kelly_tokens(capital: float, model_p: float, entry: float,
                  cfg: DirectionalConfig) -> float:
    if entry <= 0 or entry >= 1:
        return 0.0
    b = (1.0 - entry) / entry
    q = 1.0 - model_p
    f = (model_p * b - q) / b
    f = max(0.0, f) * cfg.kelly_fraction
    stake = min(f * capital, cfg.max_bet_frac * capital)
    return stake / entry


def run_direction3_on(windows: Iterable[MarketWindow], cfg: DirectionalConfig,
                      seed: int = 7,
                      initial_capital: float = 1000.0) -> List[WindowResult]:
    """Same logic as direction3_directional_edge.run but takes an external iterator
    so we can swap in real-replay windows. Kept in one place to avoid drift."""
    rng = random.Random(seed * 31 + 1)
    capital = initial_capital
    consec_losses = 0
    pause_left = 0
    day_start_capital = capital
    results: List[WindowResult] = []

    for i, w in enumerate(windows):
        if i % cfg.windows_per_day == 0:
            day_start_capital = capital

        day_paused = (capital - day_start_capital) <= -cfg.daily_loss_frac * day_start_capital
        if pause_left > 0 or day_paused:
            pause_left = max(0, pause_left - 1)
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        t_lo = w.n - cfg.trade_start_s
        t_hi = w.n - cfg.trade_end_s
        chosen = None
        for t in range(t_lo, t_hi):
            edge = w.fair[t] - w.mkt_mid[t]
            if abs(edge) < cfg.min_edge:
                continue
            conf = confirmation_score(w, t, rng)
            if abs(conf) < cfg.min_confirmation:
                continue
            if (edge > 0) != (conf > 0):
                continue
            chosen = (t, edge)
            break

        if chosen is None:
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        t, edge = chosen
        t_rem = w.n - t
        bullish = edge > 0
        if bullish:
            model_p = w.fair[t]
            maker_entry = w.mkt_mid[t]
            taker_entry = min(0.99, w.best_ask[t] + cfg.taker_cost)
        else:
            model_p = 1.0 - w.fair[t]
            maker_entry = 1.0 - w.mkt_mid[t]
            taker_entry = min(0.99, (1.0 - w.best_bid[t]) + cfg.taker_cost)

        filled_maker = rng.random() < cfg.maker_fill_prob
        if filled_maker:
            entry = maker_entry
        elif t_rem <= cfg.trade_end_s + 15:
            entry = taker_entry
        else:
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        tokens = _kelly_tokens(initial_capital, model_p, entry, cfg)
        if tokens <= 0:
            results.append(WindowResult(pnl=0.0, traded=False))
            continue

        won = w.settle_up if bullish else (not w.settle_up)
        payoff = 1.0 if won else 0.0
        pnl = tokens * (payoff - entry)
        capital += pnl

        if pnl <= 0:
            consec_losses += 1
            if consec_losses >= cfg.consecutive_loss_limit:
                pause_left = cfg.pause_windows
                consec_losses = 0
        else:
            consec_losses = 0

        results.append(WindowResult(
            pnl=pnl, spread_pnl=pnl, traded=True, won=won, edge=abs(edge),
            n_fills=1,
            extra={"entry": entry, "tokens": tokens, "maker": float(filled_maker),
                   "t_used": float(t), "model_p": model_p,
                   "bullish": float(bullish)},
        ))
    return results


# ----------------------------------------------------------------------
# Diagnostics 1–3: Brier score, calibration buckets, edge buckets
# ----------------------------------------------------------------------
def brier_calibration(samples: List[tuple]) -> dict:
    """samples: list of (predicted_p_up, settled_up_bool)."""
    if not samples:
        return {"n": 0, "brier": float("nan"), "buckets": []}
    n = len(samples)
    brier = sum((p - (1.0 if up else 0.0)) ** 2 for p, up in samples) / n

    edges = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.001]
    buckets = []
    for lo, hi in zip(edges, edges[1:]):
        bucket = [(p, up) for p, up in samples if lo <= p < hi]
        if not bucket:
            buckets.append({"range": [lo, hi], "n": 0, "predicted": None, "realised": None})
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        rate = sum(1 for _, up in bucket if up) / len(bucket)
        buckets.append({"range": [round(lo, 2), round(hi, 2)], "n": len(bucket),
                        "predicted": round(avg_p, 3), "realised": round(rate, 3)})
    return {"n": n, "brier": round(brier, 4), "buckets": buckets}


def edge_bucket_hit_rates(results: List[WindowResult]) -> List[dict]:
    traded = [r for r in results if r.traded and r.won is not None]
    if not traded:
        return []
    edges = [0.03, 0.05, 0.07, 0.10, 0.15, 1.0]
    rows = []
    for lo, hi in zip(edges, edges[1:]):
        bucket = [r for r in traded if lo <= r.edge < hi]
        if not bucket:
            rows.append({"edge_range": [lo, hi], "n": 0, "win_rate_pct": None,
                         "avg_pnl": None})
            continue
        wins = sum(1 for r in bucket if r.won)
        avg_pnl = sum(r.pnl for r in bucket) / len(bucket)
        rows.append({"edge_range": [lo, hi], "n": len(bucket),
                     "win_rate_pct": round(100.0 * wins / len(bucket), 1),
                     "avg_pnl": round(avg_pnl, 4)})
    return rows


def t_remaining_hit_rates(results: List[WindowResult]) -> List[dict]:
    traded = [r for r in results if r.traded and r.won is not None and "t_used" in r.extra]
    if not traded:
        return []
    bins = [(180, 150), (150, 120), (120, 90), (90, 60), (60, 30)]
    rows = []
    for hi_rem, lo_rem in bins:
        # t_used is "second of window when we entered"; t_remaining = w.n - t_used = 300 - t_used
        bucket = [r for r in traded
                  if lo_rem <= (300 - r.extra["t_used"]) < hi_rem]
        if not bucket:
            rows.append({"t_rem_range": [lo_rem, hi_rem], "n": 0,
                         "win_rate_pct": None, "avg_pnl": None})
            continue
        wins = sum(1 for r in bucket if r.won)
        avg_pnl = sum(r.pnl for r in bucket) / len(bucket)
        rows.append({"t_rem_range": [lo_rem, hi_rem], "n": len(bucket),
                     "win_rate_pct": round(100.0 * wins / len(bucket), 1),
                     "avg_pnl": round(avg_pnl, 4)})
    return rows


# ----------------------------------------------------------------------
# Putting it together
# ----------------------------------------------------------------------
def run_stage0(mode: str, n_windows: int, seed: int, klines_path: Optional[str],
               sim_cfg: Optional[SimConfig] = None,
               d3_cfg: Optional[DirectionalConfig] = None) -> dict:
    sim_cfg = sim_cfg or SimConfig()
    d3_cfg = d3_cfg or DirectionalConfig()

    if mode == "real":
        if not klines_path or not os.path.exists(klines_path):
            raise FileNotFoundError(
                f"--mode real requires --klines pointing to an existing JSON cache. "
                f"Run fetch_btc_data.py on a machine with internet first.")
        windows_iter = list(_iter_real(klines_path, n_windows, seed, sim_cfg))
    elif mode == "regime":
        windows_iter = list(_iter_synthetic(n_windows, seed, sim_cfg, regime=True))
    elif mode == "synthetic":
        windows_iter = list(_iter_synthetic(n_windows, seed, sim_cfg, regime=False))
    else:
        raise ValueError(f"unknown mode: {mode}")

    # 1. calibration: sample fair vs settlement at a fixed t (120s) across all windows
    calib_samples = [(w.fair[180], w.settle_up) for w in windows_iter]   # t=180 -> 120s left
    calib = brier_calibration(calib_samples)

    # 2. run Direction 3
    results = run_direction3_on(windows_iter, d3_cfg, seed=seed)

    # 3. metrics + buckets
    metrics = compute_metrics("D3 directional", results)
    edge_rows = edge_bucket_hit_rates(results)
    t_rows = t_remaining_hit_rates(results)

    # Stage-0 gate
    passed = (metrics["win_rate"] > 54.0
              and metrics["profit_factor"] > 1.2
              and metrics["total_pnl"] > 0
              and calib["brier"] < 0.22)

    return {
        "mode": mode,
        "klines_path": klines_path,
        "windows": len(windows_iter),
        "metrics": metrics,
        "calibration": calib,
        "edge_buckets": edge_rows,
        "t_remaining_buckets": t_rows,
        "stage0_pass": passed,
    }


def _print_report(report: dict) -> None:
    m = report["metrics"]
    c = report["calibration"]
    print(f"=== Stage 0 validation ({report['mode']}) ===")
    print(f"windows                : {report['windows']}")
    print(f"")
    print(f"-- Direction 3 metrics --")
    print(f"  trades             : {m['traded_windows']}")
    print(f"  win rate           : {m['win_rate']:.1f}%   (gate: > 54%)")
    print(f"  profit factor      : {m['profit_factor']:.2f}    (gate: > 1.2)")
    print(f"  total PnL          : {m['total_pnl']:.2f}    (gate: > 0)")
    print(f"  per-window Sharpe  : {m['sharpe']:.3f}")
    print(f"  max drawdown       : {m['max_drawdown_pct']:.1f}%")
    print(f"")
    print(f"-- Calibration of P_fair @ t_remaining=120s --")
    print(f"  Brier score        : {c['brier']:.3f}    (gate: < 0.22)")
    print(f"  bucket  predicted  realised   n")
    for b in c["buckets"]:
        if b["n"] == 0:
            continue
        print(f"  {b['range'][0]:.2f}-{b['range'][1]:.2f}    "
              f"{b['predicted']:.3f}     {b['realised']:.3f}    {b['n']}")
    print(f"")
    print(f"-- Win rate by Layer-1 edge bucket --")
    print(f"  edge_range          n   win%    avg_pnl")
    for r in report["edge_buckets"]:
        if r["n"] == 0:
            continue
        print(f"  {r['edge_range'][0]:.2f}..{r['edge_range'][1]:.2f}      "
              f"{r['n']:5d}  {r['win_rate_pct'] or 0:5.1f}    {r['avg_pnl']:.3f}")
    print(f"")
    print(f"-- Win rate by t_remaining bucket --")
    print(f"  t_rem_range          n   win%    avg_pnl")
    for r in report["t_remaining_buckets"]:
        if r["n"] == 0:
            continue
        print(f"  {r['t_rem_range'][0]:3d}..{r['t_rem_range'][1]:3d}s         "
              f"{r['n']:5d}  {r['win_rate_pct'] or 0:5.1f}    {r['avg_pnl']:.3f}")
    print(f"")
    verdict = "PASS — proceed to Stage 1" if report["stage0_pass"] \
        else "FAIL — directional edge not demonstrated; do not build further"
    print(f"VERDICT: {verdict}")
    if report["mode"] != "real":
        print("\n  NOTE: this run used SYNTHETIC data. A PASS here only means the "
              "pipeline\n        works correctly under a model that has a "
              "by-construction edge.\n        Re-run with --mode real on a real klines "
              "cache before drawing\n        any conclusion about whether the edge "
              "exists in the live market.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("real", "regime", "synthetic"), default="regime")
    ap.add_argument("--windows", type=int, default=4000,
                    help="how many 5-min windows to evaluate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--klines", default=None,
                    help="path to a klines JSON cache (required for --mode real)")
    ap.add_argument("--out", default=None,
                    help="if set, write the full report as JSON to this path")
    args = ap.parse_args()

    # auto-pick the most recent cached file if --mode real --klines wasn't given
    if args.mode == "real" and not args.klines and os.path.isdir("data"):
        cands = sorted(os.path.join("data", f) for f in os.listdir("data")
                       if f.startswith("btc_1m_") and f.endswith(".json"))
        if cands:
            args.klines = cands[-1]
            print(f"(auto-picked klines cache: {args.klines})", file=sys.stderr)

    report = run_stage0(args.mode, args.windows, args.seed, args.klines)
    _print_report(report)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nWrote {args.out}")
    return 0 if report["stage0_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
