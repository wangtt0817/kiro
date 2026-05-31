#!/usr/bin/env python3
"""
historical_replay.py — Drop-in replacement for mm_core.simulate_window that drives
the engine with a REAL BTC price path loaded from a klines cache.

Design contract (this is what makes "real-data Stage 0" trivial)
----------------------------------------------------------------
Every direction in this repo consumes a `MarketWindow`. `simulate_window` builds one
from a synthetic GBM path; `replay_window` builds one from an interpolated real BTC
path. NOTHING ELSE CHANGES — the same fair-value / mid-lag / taker-flow / settlement
machinery in mm_core.build_window_from_path produces the window in both cases.

What is REAL when you run this with a real klines cache
-------------------------------------------------------
* BTC spot path                 — real (1-minute klines, linearly interpolated to 1 Hz)
* Per-window realised vol       — real (computed from the real path)
* Settlement outcome (Up/Down)  — real (real BTC at +5min vs real BTC at window open)
* Vol regime distribution       — real (no normality assumption, captures fat tails)
* Intraday seasonality          — real (CPI/FOMC days, US-open spikes, weekends, ...)

What is STILL MODELLED (no Polymarket order-book history is available here)
---------------------------------------------------------------------------
* Polymarket market mid         — modelled as `lagged_fair + mid_revert + gauss(noise)`
* Best bid / best ask           — modelled as mid +/- mkt_half_spread
* Taker arrival + size + side   — modelled with informed bias on the real forward move

If you later acquire Polymarket CLOB historical snapshots (e.g. by scraping
clob.polymarket.com/book on a 5s cadence into a SQLite/Parquet store), swap the
microstructure block in `mm_core.build_window_from_path` for a "play back the real
order book" version. The directions themselves do not need to change.
"""

from __future__ import annotations

import random
from typing import Iterator, List, Optional

from mm_core import (
    SimConfig, MarketWindow, build_window_from_path, WINDOW_SECONDS,
)
from historical_data import (
    load_klines, klines_to_seconds, iter_5min_windows, realised_vol_annualised,
)


def replay_window(prices_301: List[float], cfg: SimConfig, rng: random.Random,
                  sigma: Optional[float] = None) -> MarketWindow:
    """Build one MarketWindow from a real 301-second BTC price path.

    `sigma` is the annualised volatility used by the digital option pricer for both
    the fair value and the modelled market mid. If None, it is estimated from the
    realised vol of THIS window's seconds returns — i.e. the bot only uses information
    available at decision time. This avoids the lookahead-bias trap of plugging in
    forward-looking volatility.
    """
    if sigma is None:
        # use a tiny floor; with strict zero-vol the BS digital collapses
        sigma = max(0.05, realised_vol_annualised(prices_301))
    return build_window_from_path(prices_301, sigma, cfg, rng)


def iter_replay_windows(klines_path: str, cfg: SimConfig, seed: int = 7,
                         max_windows: Optional[int] = None,
                         sigma_lookback_s: int = 1800) -> Iterator[MarketWindow]:
    """Stream `MarketWindow`s built from a klines JSON cache.

    `sigma_lookback_s` (default 30 minutes) controls the EWMA lookback used to estimate
    sigma for each window — using ONLY data before the window opens, no lookahead. This
    matches the design doc's "use the current vol regime" guidance.
    """
    klines = load_klines(klines_path)
    seconds = klines_to_seconds(klines)
    rng = random.Random(seed)

    n_yielded = 0
    for start_idx, ts_ms, prices in iter_5min_windows(seconds, klines, WINDOW_SECONDS):
        # estimate sigma from the lookback ending RIGHT BEFORE this window opens
        # (so we never use future data in the model the strategy sees)
        if start_idx >= sigma_lookback_s:
            history = seconds[start_idx - sigma_lookback_s:start_idx + 1]
            sigma = max(0.05, realised_vol_annualised(history))
        else:
            sigma = cfg.sigma_annual            # fall back to default before warmup

        yield replay_window(prices, cfg, rng, sigma=sigma)

        n_yielded += 1
        if max_windows is not None and n_yielded >= max_windows:
            return


# ----------------------------------------------------------------------
# self-check (only runs if a klines file is present)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys

    # find any cached klines file in ./data
    candidates = []
    if os.path.isdir("data"):
        candidates = sorted(
            os.path.join("data", f) for f in os.listdir("data")
            if f.startswith("btc_1m_") and f.endswith(".json")
        )
    if not candidates:
        print("No klines cache found in ./data/. To populate it, run on a machine with "
              "internet access:")
        print("    python fetch_btc_data.py --days 30")
        print("    python fetch_btc_data.py --days 30 --source coinbase")
        print("then copy the data/btc_1m_*.json file back into this directory.")
        sys.exit(0)

    path = candidates[-1]
    print(f"Replaying {path}")
    cfg = SimConfig()
    n = 0
    settle_up = 0
    sigmas = []
    for w in iter_replay_windows(path, cfg, max_windows=200):
        n += 1
        if w.settle_up:
            settle_up += 1
        sigmas.append(w.sigma)
    print(f"replayed windows={n}  Up settlements={settle_up} ({100*settle_up/max(1,n):.1f}%)")
    if sigmas:
        sigmas.sort()
        print(f"sigma quartiles: "
              f"q25={sigmas[len(sigmas)//4]:.2f}  "
              f"med={sigmas[len(sigmas)//2]:.2f}  "
              f"q75={sigmas[3*len(sigmas)//4]:.2f}")
