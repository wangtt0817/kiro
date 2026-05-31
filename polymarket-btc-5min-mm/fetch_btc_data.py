#!/usr/bin/env python3
"""
fetch_btc_data.py — Pull real BTC 1-minute klines and cache to JSON for replay.

WHY THIS IS A STANDALONE SCRIPT
-------------------------------
The Kiro sandbox runs in INTEGRATIONS_ONLY network mode — every external HTTP host
(Binance, Coinbase, GitHub raw, CoinGecko, ...) returns 403 / connection refused.
So this fetch step CANNOT run inside the sandbox. Run it on your laptop / VPS where
you have normal internet, then copy the resulting `data/btc_1m_*.json` into the repo
and re-run `stage0_validation.py` (which reads the cache, no network required).

USAGE (anywhere with internet)
------------------------------
    # 30 days of 1-minute BTC bars, default Binance source
    python fetch_btc_data.py --days 30

    # 90 days, prefer Coinbase
    python fetch_btc_data.py --days 90 --source coinbase

    # write somewhere else
    python fetch_btc_data.py --days 30 --out data/btc_1m_30d.json

OUTPUT FORMAT (JSON)
--------------------
    {
        "source": "binance" | "coinbase",
        "symbol": "BTCUSDT" | "BTC-USD",
        "interval": "1m",
        "fetched_utc": "2026-05-31T12:34:56Z",
        "klines": [
            {"t": <unix_ms>, "o": <open>, "h": <high>, "l": <low>,
             "c": <close>, "v": <volume>},
            ...
        ]
    }

This is the format historical_data.load_klines() expects.

NOTE: only the standard library is used — no pip install needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BINANCE_URL = "https://api.binance.com/api/v3/klines"
COINBASE_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"

UA = "polymarket-btc-5min-mm/1.0 (research)"


# ----------------------------------------------------------------------
# HTTP helper
# ----------------------------------------------------------------------
def _http_get_json(url: str, params: dict, timeout: float = 15.0):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ----------------------------------------------------------------------
# Binance: 1m klines, max 1000 bars per call, paginated by startTime
# ----------------------------------------------------------------------
def fetch_binance_1m(days: int) -> list[dict]:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        rows = _http_get_json(BINANCE_URL, {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "startTime": cursor,
            "limit": 1000,
        })
        if not rows:
            break
        for r in rows:
            out.append({"t": r[0], "o": float(r[1]), "h": float(r[2]),
                        "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
        cursor = rows[-1][0] + 60_000     # next minute after last bar
        time.sleep(0.25)                  # be polite
        print(f"  binance: {len(out):>6} bars", file=sys.stderr)
    return out


# ----------------------------------------------------------------------
# Coinbase: granularity=60 (1m), max 300 bars per call, paginated by start/end
# ----------------------------------------------------------------------
def fetch_coinbase_1m(days: int) -> list[dict]:
    end_s = int(time.time())
    start_s = end_s - days * 24 * 3600
    out: list[dict] = []
    chunk = 300 * 60                      # 300 minutes = 18,000 s
    cursor = start_s
    while cursor < end_s:
        seg_end = min(end_s, cursor + chunk)
        rows = _http_get_json(COINBASE_URL, {
            "granularity": 60,
            "start": datetime.fromtimestamp(cursor, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(seg_end, tz=timezone.utc).isoformat(),
        })
        # Coinbase returns [time, low, high, open, close, volume], newest first
        for r in sorted(rows):
            out.append({"t": int(r[0]) * 1000, "o": float(r[3]), "h": float(r[2]),
                        "l": float(r[1]), "c": float(r[4]), "v": float(r[5])})
        cursor = seg_end
        time.sleep(0.25)
        print(f"  coinbase: {len(out):>6} bars", file=sys.stderr)
    return out


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30,
                    help="how many days of 1-minute BTC bars to fetch (default 30)")
    ap.add_argument("--source", choices=("binance", "coinbase"), default="binance")
    ap.add_argument("--out", default=None,
                    help="output JSON path (default data/btc_1m_<days>d_<source>.json)")
    args = ap.parse_args()

    out_path = args.out or os.path.join("data", f"btc_1m_{args.days}d_{args.source}.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    print(f"Fetching {args.days} days of 1-minute BTC bars from {args.source} -> {out_path}",
          file=sys.stderr)
    fetcher = {"binance": fetch_binance_1m, "coinbase": fetch_coinbase_1m}[args.source]
    try:
        klines = fetcher(args.days)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"FETCH FAILED ({args.source}): {e}", file=sys.stderr)
        if args.source == "binance":
            print("Tip: --source coinbase  (Binance is geo-blocked in some regions)",
                  file=sys.stderr)
        return 2

    if not klines:
        print("No bars returned — aborting.", file=sys.stderr)
        return 3

    payload = {
        "source": args.source,
        "symbol": {"binance": "BTCUSDT", "coinbase": "BTC-USD"}[args.source],
        "interval": "1m",
        "fetched_utc": datetime.now(timezone.utc).isoformat(),
        "klines": klines,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f)

    first = datetime.fromtimestamp(klines[0]["t"] / 1000, tz=timezone.utc)
    last = datetime.fromtimestamp(klines[-1]["t"] / 1000, tz=timezone.utc)
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"OK: {len(klines)} bars  {first.isoformat()}  ->  {last.isoformat()}  "
          f"({size_mb:.2f} MB)", file=sys.stderr)
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
