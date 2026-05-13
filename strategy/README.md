# MemeMax BTC/ETH High-Frequency Pair Trading Strategy

## Overview

A high-frequency pair trading strategy for MemeMax Perp DEX that exploits
mean-reversion in the ETH/BTC price ratio. Designed to maximize trading
volume while maintaining small but consistent profits per round.

## Key Numbers

| Metric | Value |
|--------|-------|
| Maker Fee | 0.005% |
| Taker Fee | 0.15% |
| Round-trip Cost (4× Maker) | 0.02% |
| Target Gross Profit | 0.05% |
| Net Profit per Round | ~0.03% |
| Open Threshold | 0.05% deviation |
| Max Hold Time | 30 minutes |
| Max Leverage | 2x |
| Daily Loss Limit | 1% |

## Architecture

```
main.py              → Entry point, logging, lifecycle
config.py            → All configurable parameters
exchange_client.py   → Exchange API abstraction (implement with MemeMax SDK)
signal.py            → EMA-based ratio signal generator
risk_manager.py      → P&L tracking, loss limits, pause logic
pair_trader.py       → Core execution engine, state machine
```

## How It Works

```
1. Every 2 seconds: fetch BTC & ETH mark prices
2. Calculate ratio = ETH/BTC, update 20-period EMA
3. If ratio deviates > 0.05% from EMA → open signal
4. Place Maker limit order on ETH (less liquid leg first)
5. On ETH fill → immediately place Maker limit on BTC
6. If BTC fills within 3s → position is open (HOLDING)
7. If BTC times out → cancel + unwind ETH (ABORT)
8. Monitor: when ratio reverts → close both legs (Maker)
9. Repeat
```

## State Machine

```
IDLE → OPENING_LEG1 → OPENING_LEG2 → HOLDING → CLOSING → DONE
                  ↓                       ↑
              timeout                  timeout/stoploss
                  ↓                       ↓
               (cancel)              (force close)
```

## Setup

1. Fill in `API_KEY` and `API_SECRET` in `config.py`
2. Update `BASE_URL` to MemeMax API endpoint
3. Implement `exchange_client.py` methods with actual MemeMax API calls
4. Run: `python main.py`

## Implementation TODO

The `exchange_client.py` contains interface stubs that need to be implemented
with the actual MemeMax API. Each method has a docstring explaining:
- What it should do
- What parameters it accepts
- What it should return

You can use:
- MemeMax official SDK (if available)
- ccxt library (if MemeMax is supported)
- Raw HTTP requests to MemeMax REST API + WebSocket

## Risk Controls

- **Daily loss > 1%**: Stop trading for the day
- **5 consecutive losses**: Pause 30 minutes
- **Hourly loss > 0.5%**: Pause 1 hour
- **Margin usage > 60%**: No new positions
- **Hold time > 30min**: Force close
- **Single round loss > 0.15%**: Stop loss

## Tuning Guide

### If too few trades (volume too low):
- Decrease `OPEN_THRESHOLD` (e.g., 0.03%)
- Decrease `EMA_PERIOD` (e.g., 10)
- Decrease `SCAN_INTERVAL` (e.g., 1s)

### If too many losses:
- Increase `OPEN_THRESHOLD` (e.g., 0.08%)
- Increase `EMA_PERIOD` (e.g., 40)
- Decrease `TARGET_PROFIT` to close earlier
- Decrease `MAX_HOLD_SECONDS`

### If fills are too slow:
- Adjust `MAKER_OFFSET_TICKS`
- Consider using post-only order type (if supported)
