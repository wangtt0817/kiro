#!/usr/bin/env python3
"""
mm_core.py — Shared core library for the Polymarket BTC 5-min strategy directions.

This module fixes the structural problems found in strategy_a.py / strategy_b.py and
provides a single, self-contained, event-driven backtest harness that ALL six
strategy directions consume, so they can be compared apples-to-apples.

What lives here
---------------
1. DigitalOptionEngine  — Black-Scholes digital (cash-or-nothing) call with CORRECT
                          delta/gamma/vega. Pricing of the Polymarket Up token.
2. MarketWindow / simulate_window
                        — a self-contained synthetic 5-minute market:
                            * BTC spot path via GBM (strike K = open price)
                            * true fair value P_fair[t] = N(d2) using the FRESH spot
                            * a STALE + NOISY market mid (models the CEX->Polymarket lag)
                              which is what creates a directional edge to detect
                            * order-book best bid/ask and INFORMED taker flow which
                              creates realistic adverse selection for market makers
3. HedgeBook            — perpetual-future delta hedge with proper
                          MARK-TO-SETTLEMENT accounting (the single biggest bug in
                          strategy_a.py was that hedging only ever subtracted slippage
                          and never booked the offsetting directional PnL).
4. Metrics / equity-curve helpers — same shape as the WC backtest engine.

IMPORTANT: results produced on this module are on SYNTHETIC data. The point is to
validate the *logic* of each direction (sign of PnL, sensitivity to spread / rebate /
edge / adverse selection), NOT to claim real-world profit. Swap simulate_window() for a
real historical replay to get tradeable numbers.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

SECONDS_PER_YEAR = 365.25 * 24 * 3600  # 31,557,600
WINDOW_SECONDS = 300                   # 5-minute markets
DT = 1.0                               # 1-second simulation step
SECONDS_PER_DAY = 24 * 3600


# ============================================================
# 1. Pricing — Black-Scholes digital (cash-or-nothing) call
# ============================================================
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


class DigitalOptionEngine:
    """Prices the Up token as a digital call: it pays $1 if S_T >= K else $0."""

    @staticmethod
    def price_and_greeks(S: float, K: float, T_years: float, sigma: float,
                         r: float = 0.0) -> Dict[str, float]:
        """Return p (=P(Up)), delta=dp/dS, gamma=d2p/dS2, vega=dp/dsigma.

        These are the CORRECT digital-call greeks (strategy_a.py used d2 where it
        should have used d1 in vega, and dropped a term in gamma).
        """
        if T_years <= 0 or sigma <= 0:
            return {"p": 1.0 if S >= K else 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0}

        sqrtT = math.sqrt(T_years)
        d2 = (math.log(S / K) + (r - 0.5 * sigma ** 2) * T_years) / (sigma * sqrtT)
        d1 = d2 + sigma * sqrtT
        n_d2 = _norm_pdf(d2)

        return {
            "p": _norm_cdf(d2),
            "delta": n_d2 / (S * sigma * sqrtT),
            # d/dS [ n(d2)/(S sigma sqrtT) ] = -n(d2)(d2 + sigma sqrtT)/(S^2 sigma^2 T)
            "gamma": -n_d2 * (d2 + sigma * sqrtT) / (S ** 2 * sigma ** 2 * T_years),
            # vega of a digital call = -n(d2) * d1 / sigma
            "vega": -n_d2 * d1 / sigma,
        }

    @classmethod
    def fair(cls, S: float, K: float, T_years: float, sigma: float) -> float:
        return cls.price_and_greeks(S, K, T_years, sigma)["p"]

    @classmethod
    def delta(cls, S: float, K: float, T_years: float, sigma: float) -> float:
        return cls.price_and_greeks(S, K, T_years, sigma)["delta"]


# ============================================================
# 2. Synthetic 5-minute market window
# ============================================================
@dataclass
class SimConfig:
    """Knobs for the synthetic world. Tune these to make edges appear/disappear."""
    sigma_annual: float = 0.60          # BTC annualised realised vol
    open_price: float = 100_000.0       # strike K = window open price
    drift_annual: float = 0.0           # no drift over 5 min

    # Market microstructure
    mkt_lag_s: int = 4                  # how stale the Polymarket mid is vs CEX spot
    mkt_noise: float = 0.012            # gaussian noise on the market mid (prob units)
    mkt_half_spread: float = 0.015      # half of the visible market bid-ask (prob)
    mid_revert: float = 0.06            # market under-reaction toward 0.5 (mispricing)

    # Taker flow (drives MM fills + adverse selection)
    taker_rate: float = 0.35            # P(a taker arrives) per second
    taker_informed: float = 0.62        # 0.5 = uninformed; >0.5 = flow predicts the move
    taker_lookahead_s: int = 20         # horizon the informed flow "knows" about
    taker_size_mean: float = 12.0       # average taker clip size (tokens)


@dataclass
class MarketWindow:
    """One fully-simulated 5-minute window, shared by every direction."""
    K: float
    sigma: float
    n: int                              # number of seconds (== WINDOW_SECONDS)
    S: List[float]                      # BTC spot path (fresh)
    fair: List[float]                   # true fair P(Up) using fresh spot
    mkt_mid: List[float]                # stale + noisy Polymarket mid
    best_bid: List[float]
    best_ask: List[float]
    taker_buy: List[bool]               # per second: did a buy-taker arrive?
    taker_sell: List[bool]              # per second: did a sell-taker arrive?
    taker_size: List[float]
    settle_up: bool                     # did the window settle Up?
    settle_price: float

    def t_remaining(self, t: int) -> float:
        return max(0.0, (self.n - t))

    def t_remaining_years(self, t: int) -> float:
        return self.t_remaining(t) / SECONDS_PER_YEAR


def simulate_window(cfg: SimConfig, rng: random.Random) -> MarketWindow:
    """Generate one 5-minute window.

    The two effects that matter:
      * The market mid is priced off a *stale* spot (mkt_lag_s seconds old) plus noise
        and a mild pull toward 0.5 (documented mid-probability mispricing). Because the
        FRESH spot is the best predictor of settlement, trading toward the fresh fair
        value is +EV -> this is the synthetic 'edge' Direction 3 is meant to detect.
      * Taker flow is informed: a taker is more likely to trade in the direction of the
        *next* spot move. A maker resting at the stale mid therefore gets adversely
        selected -> this is the cost Directions 1/2/5/6 must overcome.
    """
    n = WINDOW_SECONDS
    K = cfg.open_price
    sigma = cfg.sigma_annual
    dt_yr = DT / SECONDS_PER_YEAR
    mu = cfg.drift_annual

    # --- BTC spot path (GBM) ---
    S = [K]
    for _ in range(n):
        z = rng.gauss(0.0, 1.0)
        s_prev = S[-1]
        s_next = s_prev * math.exp((mu - 0.5 * sigma ** 2) * dt_yr + sigma * math.sqrt(dt_yr) * z)
        S.append(s_next)
    # S has length n+1 (S[0]..S[n]); settlement uses S[n]

    settle_price = S[n]
    settle_up = settle_price >= K

    fair: List[float] = []
    mkt_mid: List[float] = []
    best_bid: List[float] = []
    best_ask: List[float] = []
    taker_buy: List[bool] = []
    taker_sell: List[bool] = []
    taker_size: List[float] = []

    for t in range(n):
        T_rem_yr = max(1e-9, (n - t) / SECONDS_PER_YEAR)

        # true fair using fresh spot
        f = DigitalOptionEngine.fair(S[t], K, T_rem_yr, sigma)
        fair.append(f)

        # stale market mid (lagged spot) + under-reaction + noise
        lag_idx = max(0, t - cfg.mkt_lag_s)
        f_stale = DigitalOptionEngine.fair(S[lag_idx], K, T_rem_yr, sigma)
        m = f_stale + cfg.mid_revert * (0.5 - f_stale) + rng.gauss(0.0, cfg.mkt_noise)
        m = min(0.99, max(0.01, m))
        mkt_mid.append(m)
        best_bid.append(min(0.99, max(0.01, m - cfg.mkt_half_spread)))
        best_ask.append(min(0.99, max(0.01, m + cfg.mkt_half_spread)))

        # informed taker flow: biased by the move over the next `taker_lookahead_s`
        # seconds (not just the next tick) -> makers who get filled are disproportionately
        # on the wrong side, which is what real adverse selection feels like.
        look = min(n, t + cfg.taker_lookahead_s)
        fwd = S[look] - S[t]
        up_move = fwd >= 0
        p_buy = cfg.taker_informed if up_move else (1.0 - cfg.taker_informed)
        arrived = rng.random() < cfg.taker_rate
        is_buy = rng.random() < p_buy
        taker_buy.append(arrived and is_buy)
        taker_sell.append(arrived and not is_buy)
        taker_size.append(max(1.0, rng.gauss(cfg.taker_size_mean, cfg.taker_size_mean * 0.4)))

    return MarketWindow(
        K=K, sigma=sigma, n=n, S=S, fair=fair, mkt_mid=mkt_mid,
        best_bid=best_bid, best_ask=best_ask,
        taker_buy=taker_buy, taker_sell=taker_sell, taker_size=taker_size,
        settle_up=settle_up, settle_price=settle_price,
    )


# ============================================================
# 3. Inventory + Hedge book (mark-to-settlement)
# ============================================================
@dataclass
class TokenBook:
    """Net Up-token inventory with cash accounting.

    Buying an Up token: +tokens, cash -= size*price.
    Selling an Up token: -tokens, cash += size*price (short Up == long Down).
    At settlement each Up token pays $1 if Up else $0.
    """
    tokens: float = 0.0     # net Up-token position (signed)
    cash: float = 0.0       # USDC cash from token trades

    def trade(self, side: str, price: float, size: float) -> None:
        if side == "buy":
            self.tokens += size
            self.cash -= size * price
        elif side == "sell":
            self.tokens -= size
            self.cash += size * price
        else:
            raise ValueError(side)

    def settle(self, is_up: bool) -> float:
        payoff = 1.0 if is_up else 0.0
        return self.cash + self.tokens * payoff


@dataclass
class HedgeBook:
    """Binance-perp delta hedge with PROPER mark-to-settlement PnL.

    This is the key fix vs strategy_a.py. We track a signed BTC quantity and a cash
    balance. Rehedging trades BTC at the current spot (paying slippage). At settlement
    we close the position at the final spot. realized PnL = resulting cash, which
    correctly captures the directional offset *and* the slippage cost.
    """
    slippage_bps: float = 1.0           # 1 bp per hedge trade
    qty: float = 0.0                    # signed BTC held (long perp > 0)
    cash: float = 0.0                   # USD cash from hedging
    slippage_paid: float = 0.0
    n_hedges: int = 0

    def rehedge_to(self, target_qty: float, spot: float) -> None:
        dq = target_qty - self.qty
        if abs(dq) < 1e-12:
            return
        # buying dq BTC costs dq*spot; selling adds; slippage always a cost
        self.cash -= dq * spot
        slip = abs(dq) * spot * (self.slippage_bps * 1e-4)
        self.cash -= slip
        self.slippage_paid += slip
        self.qty = target_qty
        self.n_hedges += 1

    def close(self, spot: float) -> float:
        """Close the hedge at settlement spot; return realized hedge PnL."""
        self.cash += self.qty * spot
        self.qty = 0.0
        return self.cash


def hedge_target_qty(net_up_tokens: float, delta_digital: float) -> float:
    """BTC quantity that neutralises the token book's BTC delta.

    Token book value V = net_up_tokens * P. dV/dS = net_up_tokens * delta_digital.
    A perp position of q BTC has PnL q*dS. To offset: q = -net_up_tokens*delta_digital.
    (delta_digital has units 1/$, net_up_tokens in tokens -> q in BTC.)
    """
    return -net_up_tokens * delta_digital


# ============================================================
# 4. Metrics + equity curve (same shape as the WC engine)
# ============================================================
@dataclass
class WindowResult:
    pnl: float
    spread_pnl: float = 0.0
    rebate_pnl: float = 0.0
    hedge_pnl: float = 0.0
    fee_gas: float = 0.0
    traded: bool = False
    won: Optional[bool] = None          # for directional bets
    edge: float = 0.0
    n_fills: int = 0
    n_hedges: int = 0
    extra: Dict[str, float] = field(default_factory=dict)


def compute_metrics(name: str, results: List[WindowResult],
                    initial_capital: float = 1000.0) -> Dict[str, float]:
    """Summarise a list of per-window results into the standard metric set."""
    pnls = [r.pnl for r in results]
    n = len(pnls)
    total = sum(pnls)

    # equity curve + max drawdown
    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    wins_pnl = [p for p in pnls if p > 0]
    loss_pnl = [p for p in pnls if p <= 0]

    # win-rate: for directional bets use settled win flag; else use positive-PnL windows
    settled = [r for r in results if r.won is not None]
    if settled:
        wins = sum(1 for r in settled if r.won)
        denom = len(settled)
    else:
        wins = len(wins_pnl)
        denom = n

    # max consecutive losing windows
    max_consec = consec = 0
    for p in pnls:
        if p <= 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0

    mean = total / n if n else 0.0
    if n > 1:
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var)
        # Per-window Sharpe (mean / std). We deliberately do NOT annualise: scaling a
        # 5-minute-window Sharpe by sqrt(~105k windows/yr) produces meaningless 3-digit
        # numbers. Compare these per-window figures across directions directly.
        sharpe = (mean / std) if std > 0 else 0.0
    else:
        std = 0.0
        sharpe = 0.0

    pf = (sum(wins_pnl) / abs(sum(loss_pnl))) if loss_pnl and sum(loss_pnl) != 0 else float("inf")

    return {
        "name": name,
        "windows": n,
        "traded_windows": sum(1 for r in results if r.traded),
        "win_rate": round(100.0 * wins / denom, 2) if denom else 0.0,
        "total_pnl": round(total, 2),
        "avg_pnl_per_window": round(mean, 4),
        "spread_pnl": round(sum(r.spread_pnl for r in results), 2),
        "rebate_pnl": round(sum(r.rebate_pnl for r in results), 2),
        "hedge_pnl": round(sum(r.hedge_pnl for r in results), 2),
        "fee_gas": round(sum(r.fee_gas for r in results), 2),
        "avg_edge_pct": round(100.0 * (sum(r.edge for r in results) / n), 3) if n else 0.0,
        "max_drawdown_pct": round(100.0 * max_dd, 2),
        "profit_factor": round(min(pf, 99.9), 2),
        "sharpe": round(sharpe, 2),
        "total_fills": sum(r.n_fills for r in results),
        "total_hedges": sum(r.n_hedges for r in results),
        "final_capital": round(initial_capital + total, 2),
    }


def format_metrics_table(rows: List[Dict[str, float]]) -> str:
    """Pretty fixed-width comparison table."""
    cols = [
        ("name", "Direction", 26, "<"),
        ("win_rate", "Win%", 7, ">"),
        ("total_pnl", "PnL$", 11, ">"),
        ("avg_pnl_per_window", "PnL/win", 9, ">"),
        ("sharpe", "Sharpe", 8, ">"),
        ("max_drawdown_pct", "MaxDD%", 8, ">"),
        ("profit_factor", "PF", 7, ">"),
        ("traded_windows", "Trades", 7, ">"),
    ]
    head = "".join(f"{h:{al}{w}}" for _, h, w, al in cols)
    line = "-" * len(head)
    out = [line, head, line]
    for r in rows:
        out.append("".join(f"{str(r.get(k, '')):{al}{w}}" for k, _, w, al in cols))
    out.append(line)
    return "\n".join(out)


# ============================================================
# 5. Quote model shared by the market-making directions
# ============================================================
@dataclass
class MMConfig:
    min_tick: float = 0.01
    k_vol: float = 0.6
    k_inv: float = 0.02
    buffer_fee_gas: float = 0.005       # fee + gas buffer baked into half-spread
    gamma_inv: float = 0.015            # inventory skew
    base_size: float = 10.0
    inv_cap: float = 50.0
    gas_per_hedge: float = 0.002        # $ per hedge tx (Polygon)


def mm_half_spread(cfg: MMConfig, t_remaining_s: float, sigma: float, inventory: float) -> float:
    tau = t_remaining_s / SECONDS_PER_YEAR
    vol_component = cfg.k_vol * sigma * math.sqrt(max(tau, 0.0))
    inv_component = cfg.k_inv * abs(inventory) / cfg.inv_cap
    return max(cfg.min_tick, vol_component + inv_component + cfg.buffer_fee_gas)


__all__ = [
    "SECONDS_PER_YEAR", "WINDOW_SECONDS", "DT", "SECONDS_PER_DAY",
    "DigitalOptionEngine", "SimConfig", "MarketWindow", "simulate_window",
    "TokenBook", "HedgeBook", "hedge_target_qty",
    "WindowResult", "compute_metrics", "format_metrics_table",
    "MMConfig", "mm_half_spread",
]
