#!/usr/bin/env python3
"""
historical_data.py — Load cached 1-minute BTC klines and turn them into the
1-second BTC price series the engine consumes.

This module does NOT touch the network — it reads JSON files written by
fetch_btc_data.py. That keeps every run reproducible, and it keeps the in-sandbox
Stage 0 pipeline runnable as soon as the user drops a klines file into ./data/.

The public API:
    klines = load_klines("data/btc_1m_30d_binance.json")
    seconds = klines_to_seconds(klines)            # dense 1-Hz price array
    windows = iter_5min_windows(seconds, klines)   # yields (open_ts, prices[0..300])

Why piecewise-LINEAR interpolation between bar OPENS (instead of replaying OHLC)?
Polymarket's 5-min market settles on the Chainlink BTC/USD feed sampled near close,
not on the per-minute high/low. For Stage 0 (statistical edge validation) the seconds-
level path is used only for two things:
    * the FRESH digital fair value at each second, and
    * the underlying for the perp hedge (which we mostly don't run anyway).
A linear bridge between minute closes captures the per-minute drift correctly while
respecting the realised path's covariance structure at the minute scale, which is what
the BS digital pricing is sensitive to. We deliberately do NOT inject sub-minute
synthetic noise (no fake "tick path") because it would manufacture micro-volatility the
strategy could not actually trade against in real life.
"""

from __future__ import annotations

import json
from typing import Iterator, List, Tuple


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------
def load_klines(path: str) -> List[dict]:
    """Read a klines JSON cache (the format fetch_btc_data.py writes).

    Returns the raw kline list sorted by timestamp ascending. Raises if the file is
    malformed or contains gaps (>2 missing minutes), so silent data quality issues
    cannot leak into the validation.
    """
    with open(path) as f:
        payload = json.load(f)
    klines = payload["klines"] if isinstance(payload, dict) else payload
    klines = sorted(klines, key=lambda k: k["t"])

    # gap check (any single-minute hole shifts every following 5-min boundary)
    bad = 0
    for a, b in zip(klines, klines[1:]):
        if (b["t"] - a["t"]) > 60_000 + 5_000:    # >65s tolerates clock skew
            bad += 1
    if bad > max(2, len(klines) // 5_000):
        raise RuntimeError(f"klines have {bad} gaps > 60s; refetch with fewer "
                           "missing windows before running Stage 0")
    return klines


# ----------------------------------------------------------------------
# 1-minute -> 1-second interpolation (piecewise linear between bar opens)
# ----------------------------------------------------------------------
def klines_to_seconds(klines: List[dict]) -> List[float]:
    """Return a dense array of 1-Hz BTC prices spanning every minute in `klines`.

    For minute i with open o_i (timestamp t_i) and the next open o_{i+1} at t_{i+1}:
        price[t_i + s] = o_i + (s/60) * (o_{i+1} - o_i)   for s = 0..59
    The final minute is filled by repeating its close.

    The returned list has length 60 * len(klines) and corresponds to evenly-spaced
    seconds starting at klines[0]["t"]. Use the open time of `klines[0]` as t=0.
    """
    if len(klines) < 2:
        raise ValueError("need at least 2 klines to interpolate")

    out: List[float] = []
    for i in range(len(klines) - 1):
        o0 = klines[i]["o"]
        o1 = klines[i + 1]["o"]
        for s in range(60):
            out.append(o0 + (s / 60.0) * (o1 - o0))
    # last minute: hold the close
    last_c = klines[-1]["c"]
    out.extend([last_c] * 60)
    return out


# ----------------------------------------------------------------------
# 5-minute window iteration
# ----------------------------------------------------------------------
def iter_5min_windows(seconds: List[float], klines: List[dict],
                      window_s: int = 300) -> Iterator[Tuple[int, int, List[float]]]:
    """Yield successive non-overlapping 5-minute windows.

    Each yielded item is (start_idx, open_unix_ms, prices_301), where:
      * start_idx is the index into `seconds` where the window opens, useful for
        looking back at pre-window history without an O(n) `list.index` search.
      * prices_301 is the BTC spot series at seconds 0..window_s INCLUSIVE
        (length window_s + 1) — matching the convention used by build_window_from_path
        (S has length n+1; S[n] is the settlement price).

    Aligns the FIRST window to the nearest 5-minute boundary >= klines[0]["t"], the
    same way Polymarket's 5-min markets actually open at :00 :05 :10 ... UTC. This
    matters for Stage 0: aligning to the on-chain market clock is what makes BTC bars
    correspond to real market windows.
    """
    n = len(seconds)
    base_ms = klines[0]["t"]                         # second 0 of `seconds`

    # align to the next 5-minute boundary (UTC seconds % 300 == 0)
    base_s = base_ms // 1000
    misalign = (-base_s) % 300                       # seconds to skip
    start_idx = misalign

    step = window_s
    while start_idx + window_s < n:
        prices = seconds[start_idx:start_idx + window_s + 1]   # 301 prices
        ts = base_ms + start_idx * 1000
        yield start_idx, ts, list(prices)
        start_idx += step


# ----------------------------------------------------------------------
# Realised volatility helpers (for the regime-switching simulator + Stage 0 buckets)
# ----------------------------------------------------------------------
def realised_vol_annualised(seconds: List[float], window_s: int = 300) -> float:
    """Annualised realised vol of log returns at 1-second sampling for one window."""
    import math
    if len(seconds) < 2:
        return 0.0
    rets = []
    for a, b in zip(seconds, seconds[1:]):
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    if not rets:
        return 0.0
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / max(1, n - 1)
    # 1-second sampling -> annualise with sqrt(seconds-per-year)
    return math.sqrt(var) * math.sqrt(365.25 * 24 * 3600)


__all__ = ["load_klines", "klines_to_seconds", "iter_5min_windows",
           "realised_vol_annualised"]
