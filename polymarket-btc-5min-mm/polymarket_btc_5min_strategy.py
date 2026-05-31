#!/usr/bin/env python3
"""
Polymarket BTC 5-Min Strategy — All-in-One
==========================================
Merged single-file implementation of 6 strategy directions + Stage 0 validation.

Usage:
    python polymarket_btc_5min_strategy.py                    # run all 6 directions
    python polymarket_btc_5min_strategy.py --stage0           # Stage 0 validation
    python polymarket_btc_5min_strategy.py --direction 5      # run one direction only
    python polymarket_btc_5min_strategy.py --hedge-experiment # D1 hedge on vs off

No external dependencies — pure Python 3.10+ standard library.
"""
from __future__ import annotations
import argparse, json, math, os, random, sys, time, urllib.error, urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Tuple

SECONDS_PER_YEAR = 365.25 * 24 * 3600
WINDOW_SECONDS = 300
DT = 1.0



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 1: Digital Option Pricing
# ════════════════════════════════════════════════════════════════════════════════
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

class DigitalOptionEngine:
    @staticmethod
    def price_and_greeks(S, K, T_years, sigma, r=0.0):
        if T_years <= 0 or sigma <= 0:
            return {"p": 1.0 if S >= K else 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0}
        sqrtT = math.sqrt(T_years)
        d2 = (math.log(S / K) + (r - 0.5 * sigma**2) * T_years) / (sigma * sqrtT)
        d1 = d2 + sigma * sqrtT
        n_d2 = _norm_pdf(d2)
        return {
            "p": _norm_cdf(d2),
            "delta": n_d2 / (S * sigma * sqrtT),
            "gamma": -n_d2 * (d2 + sigma * sqrtT) / (S**2 * sigma**2 * T_years),
            "vega": -n_d2 * d1 / sigma,
        }
    @classmethod
    def fair(cls, S, K, T_years, sigma):
        return cls.price_and_greeks(S, K, T_years, sigma)["p"]
    @classmethod
    def delta(cls, S, K, T_years, sigma):
        return cls.price_and_greeks(S, K, T_years, sigma)["delta"]



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 2: Simulation Config & MarketWindow
# ════════════════════════════════════════════════════════════════════════════════
@dataclass
class SimConfig:
    sigma_annual: float = 0.60
    open_price: float = 100_000.0
    drift_annual: float = 0.0
    regime_sigmas: tuple = (0.30, 0.55, 1.20)
    regime_weights: tuple = (0.40, 0.45, 0.15)
    mkt_lag_s: int = 4
    mkt_noise: float = 0.012
    mkt_half_spread: float = 0.015
    mid_revert: float = 0.06
    taker_rate: float = 0.35
    taker_informed: float = 0.62
    taker_lookahead_s: int = 20
    taker_size_mean: float = 12.0

@dataclass
class MarketWindow:
    K: float; sigma: float; n: int
    S: List[float]; fair: List[float]; mkt_mid: List[float]
    best_bid: List[float]; best_ask: List[float]
    taker_buy: List[bool]; taker_sell: List[bool]; taker_size: List[float]
    settle_up: bool; settle_price: float
    def t_remaining(self, t): return max(0.0, self.n - t)
    def t_remaining_years(self, t): return self.t_remaining(t) / SECONDS_PER_YEAR



def build_window_from_path(S, sigma, cfg, rng):
    n = WINDOW_SECONDS
    K, settle_price = S[0], S[n]
    settle_up = settle_price >= K
    fair, mkt_mid, best_bid, best_ask = [], [], [], []
    taker_buy, taker_sell, taker_size = [], [], []
    for t in range(n):
        T_rem_yr = max(1e-9, (n - t) / SECONDS_PER_YEAR)
        f = DigitalOptionEngine.fair(S[t], K, T_rem_yr, sigma)
        fair.append(f)
        lag_idx = max(0, t - cfg.mkt_lag_s)
        f_stale = DigitalOptionEngine.fair(S[lag_idx], K, T_rem_yr, sigma)
        m = f_stale + cfg.mid_revert * (0.5 - f_stale) + rng.gauss(0.0, cfg.mkt_noise)
        m = min(0.99, max(0.01, m))
        mkt_mid.append(m)
        best_bid.append(min(0.99, max(0.01, m - cfg.mkt_half_spread)))
        best_ask.append(min(0.99, max(0.01, m + cfg.mkt_half_spread)))
        look = min(n, t + cfg.taker_lookahead_s)
        fwd = S[look] - S[t]
        p_buy = cfg.taker_informed if fwd >= 0 else (1.0 - cfg.taker_informed)
        arrived = rng.random() < cfg.taker_rate
        is_buy = rng.random() < p_buy
        taker_buy.append(arrived and is_buy)
        taker_sell.append(arrived and not is_buy)
        taker_size.append(max(1.0, rng.gauss(cfg.taker_size_mean, cfg.taker_size_mean*0.4)))
    return MarketWindow(K=K, sigma=sigma, n=n, S=S, fair=fair, mkt_mid=mkt_mid,
        best_bid=best_bid, best_ask=best_ask, taker_buy=taker_buy,
        taker_sell=taker_sell, taker_size=taker_size,
        settle_up=settle_up, settle_price=settle_price)

def simulate_window(cfg, rng, sigma_override=None):
    n = WINDOW_SECONDS
    sigma = sigma_override if sigma_override is not None else cfg.sigma_annual
    dt_yr = DT / SECONDS_PER_YEAR
    K = cfg.open_price
    S = [K]
    for _ in range(n):
        z = rng.gauss(0.0, 1.0)
        S.append(S[-1] * math.exp((cfg.drift_annual - 0.5*sigma**2)*dt_yr + sigma*math.sqrt(dt_yr)*z))
    return build_window_from_path(S, sigma, cfg, rng)

def simulate_window_regime(cfg, rng):
    u = rng.random(); acc = 0.0; chosen = cfg.regime_sigmas[-1]
    for s, w in zip(cfg.regime_sigmas, cfg.regime_weights):
        acc += w
        if u <= acc: chosen = s; break
    return simulate_window(cfg, rng, sigma_override=chosen)



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 3: TokenBook, HedgeBook, Metrics
# ════════════════════════════════════════════════════════════════════════════════
@dataclass
class TokenBook:
    tokens: float = 0.0; cash: float = 0.0
    def trade(self, side, price, size):
        if side == "buy": self.tokens += size; self.cash -= size * price
        elif side == "sell": self.tokens -= size; self.cash += size * price
    def settle(self, is_up): return self.cash + self.tokens * (1.0 if is_up else 0.0)

@dataclass
class HedgeBook:
    slippage_bps: float = 1.0; qty: float = 0.0; cash: float = 0.0
    slippage_paid: float = 0.0; n_hedges: int = 0
    def rehedge_to(self, target_qty, spot):
        dq = target_qty - self.qty
        if abs(dq) < 1e-12: return
        self.cash -= dq * spot
        slip = abs(dq) * spot * (self.slippage_bps * 1e-4)
        self.cash -= slip; self.slippage_paid += slip
        self.qty = target_qty; self.n_hedges += 1
    def close(self, spot):
        self.cash += self.qty * spot; self.qty = 0.0; return self.cash

def hedge_target_qty(net_up_tokens, delta_digital):
    return -net_up_tokens * delta_digital

@dataclass
class WindowResult:
    pnl: float; spread_pnl: float = 0.0; rebate_pnl: float = 0.0
    hedge_pnl: float = 0.0; fee_gas: float = 0.0; traded: bool = False
    won: Optional[bool] = None; edge: float = 0.0
    n_fills: int = 0; n_hedges: int = 0
    extra: Dict[str, float] = field(default_factory=dict)



def compute_metrics(name, results, initial_capital=1000.0):
    pnls = [r.pnl for r in results]; n = len(pnls); total = sum(pnls)
    equity = peak = initial_capital; max_dd = 0.0
    for p in pnls:
        equity += p; peak = max(peak, equity)
        if peak > 0: max_dd = max(max_dd, (peak - equity) / peak)
    wins_pnl = [p for p in pnls if p > 0]; loss_pnl = [p for p in pnls if p <= 0]
    settled = [r for r in results if r.won is not None]
    if settled: wins = sum(1 for r in settled if r.won); denom = len(settled)
    else: wins = len(wins_pnl); denom = n
    mean = total / n if n else 0.0
    if n > 1:
        var = sum((p - mean)**2 for p in pnls) / (n - 1)
        std = math.sqrt(var); sharpe = (mean / std) if std > 0 else 0.0
    else: std = sharpe = 0.0
    pf = (sum(wins_pnl) / abs(sum(loss_pnl))) if loss_pnl and sum(loss_pnl) != 0 else 99.9
    return {"name": name, "windows": n, "traded_windows": sum(1 for r in results if r.traded),
        "win_rate": round(100.0 * wins / denom, 2) if denom else 0.0,
        "total_pnl": round(total, 2), "avg_pnl_per_window": round(mean, 4),
        "spread_pnl": round(sum(r.spread_pnl for r in results), 2),
        "rebate_pnl": round(sum(r.rebate_pnl for r in results), 2),
        "hedge_pnl": round(sum(r.hedge_pnl for r in results), 2),
        "fee_gas": round(sum(r.fee_gas for r in results), 2),
        "max_drawdown_pct": round(100*max_dd, 2), "profit_factor": round(min(pf, 99.9), 2),
        "sharpe": round(sharpe, 2), "total_fills": sum(r.n_fills for r in results),
        "total_hedges": sum(r.n_hedges for r in results),
        "final_capital": round(initial_capital + total, 2)}

def format_metrics_table(rows):
    cols = [("name","Direction",26,"<"),("win_rate","Win%",7,">"),("total_pnl","PnL$",11,">"),
            ("avg_pnl_per_window","PnL/win",9,">"),("sharpe","Sharpe",8,">"),
            ("max_drawdown_pct","MaxDD%",8,">"),("profit_factor","PF",7,">"),
            ("traded_windows","Trades",7,">")]
    head = "".join(f"{h:{al}{w}}" for _,h,w,al in cols)
    line = "-" * len(head)
    out = [line, head, line]
    for r in rows:
        out.append("".join(f"{str(r.get(k,'')):{al}{w}}" for k,_,w,al in cols))
    out.append(line); return "\n".join(out)



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 4: Market Making Engine (shared by D1, D2, D5, D6)
# ════════════════════════════════════════════════════════════════════════════════
@dataclass
class MarketMakerConfig:
    min_tick: float = 0.01; k_vol: float = 0.6; k_inv: float = 0.02
    buffer_fee_gas: float = 0.005; gamma_inv: float = 0.015
    base_size: float = 10.0; inv_cap: float = 50.0; gas_per_hedge: float = 0.002
    hedge: bool = False; delta_band_btc: float = 0.0005
    hedge_slippage_bps: float = 1.0; max_delta_per_token: float = 0.02
    tau_safe_s: float = 60.0; unwind_near_expiry: bool = True; unwind_cost: float = 0.005
    rebate: bool = False; reward_pool_per_window: float = 2.0
    reward_max_spread_c: float = 3.0; size_cap: float = 100.0; competitor_score: float = 4000.0
    signal_skew: bool = False; skew_gain: float = 0.6; skew_clip: float = 0.03

def _mm_half_spread(cfg, t_rem_s, sigma, inventory):
    tau = t_rem_s / SECONDS_PER_YEAR
    vol_c = cfg.k_vol * sigma * math.sqrt(max(tau, 0.0))
    inv_c = cfg.k_inv * abs(inventory) / cfg.inv_cap
    return max(cfg.min_tick, vol_c + inv_c + cfg.buffer_fee_gas)

def _reward_score(price, size, adj_mid, max_spread_c, size_cap, dt):
    d_c = abs(price - adj_mid) * 100.0
    if d_c > max_spread_c: return 0.0
    q = max(0.0, 1.0 - (d_c / max_spread_c)**2)
    return min(size, size_cap) * q * dt



def run_mm_window(w, cfg):
    book = TokenBook(); hedge = HedgeBook(slippage_bps=cfg.hedge_slippage_bps)
    my_reward_score = 0.0; n_fills = 0; gas = 0.0; unwound = False
    for t in range(w.n):
        t_rem = w.t_remaining(t); T_rem_yr = w.t_remaining_years(t)
        observed_mid = w.mkt_mid[t]
        if t_rem < cfg.tau_safe_s:
            if cfg.unwind_near_expiry and not unwound:
                if abs(book.tokens) > 1e-9:
                    if book.tokens > 0:
                        book.trade("sell", max(0.01, observed_mid - cfg.unwind_cost), book.tokens)
                    else:
                        book.trade("buy", min(0.99, observed_mid + cfg.unwind_cost), -book.tokens)
                if cfg.hedge: hedge.rehedge_to(0.0, w.S[t]); gas += cfg.gas_per_hedge
                unwound = True
            continue
        quote_center = observed_mid
        if cfg.signal_skew:
            lean = cfg.skew_gain * (w.fair[t] - observed_mid)
            lean = max(-cfg.skew_clip, min(cfg.skew_clip, lean))
            quote_center = observed_mid + lean
        hs = _mm_half_spread(cfg, t_rem, w.sigma, book.tokens)
        inv_skew = cfg.gamma_inv * (book.tokens / cfg.inv_cap)
        bid_price = max(0.01, min(0.99, quote_center - hs - inv_skew))
        ask_price = max(0.01, min(0.99, quote_center + hs - inv_skew))
        inv_ratio = book.tokens / cfg.inv_cap
        bid_size = max(0.0, cfg.base_size * (1.0 - inv_ratio))
        ask_size = max(0.0, cfg.base_size * (1.0 + inv_ratio))
        if w.taker_sell[t] and bid_size > 0 and bid_price >= w.best_bid[t] - 1e-9:
            book.trade("buy", bid_price, min(bid_size, w.taker_size[t])); n_fills += 1
        if w.taker_buy[t] and ask_size > 0 and ask_price <= w.best_ask[t] + 1e-9:
            book.trade("sell", ask_price, min(ask_size, w.taker_size[t])); n_fills += 1
        if cfg.hedge:
            delta = DigitalOptionEngine.delta(w.S[t], w.K, T_rem_yr, w.sigma)
            delta = max(-cfg.max_delta_per_token, min(cfg.max_delta_per_token, delta))
            target = hedge_target_qty(book.tokens, delta)
            band = cfg.delta_band_btc * max(0.2, t_rem / WINDOW_SECONDS)
            if abs(target - hedge.qty) > band: hedge.rehedge_to(target, w.S[t]); gas += cfg.gas_per_hedge
        if cfg.rebate:
            adj_mid = quote_center
            if bid_size > 0: my_reward_score += _reward_score(bid_price, bid_size, adj_mid, cfg.reward_max_spread_c, cfg.size_cap, 1.0)
            if ask_size > 0: my_reward_score += _reward_score(ask_price, ask_size, adj_mid, cfg.reward_max_spread_c, cfg.size_cap, 1.0)
    token_pnl = book.settle(w.settle_up)
    hedge_pnl = hedge.close(w.settle_price) if cfg.hedge else 0.0
    rebate_pnl = 0.0
    if cfg.rebate and my_reward_score > 0:
        rebate_pnl = (my_reward_score / (my_reward_score + cfg.competitor_score)) * cfg.reward_pool_per_window
    total = token_pnl + hedge_pnl + rebate_pnl - gas
    return WindowResult(pnl=total, spread_pnl=token_pnl, rebate_pnl=rebate_pnl,
        hedge_pnl=hedge_pnl, fee_gas=gas, traded=(n_fills > 0), n_fills=n_fills, n_hedges=hedge.n_hedges)



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 5: Signals (Direction 4 — CEX momentum / confirmation)
# ════════════════════════════════════════════════════════════════════════════════
W_OB, W_VEL, W_CEX = 0.35, 0.35, 0.30

def cex_momentum_signal(w, t, win_s=10, thresh=0.0003):
    if t < win_s: return (0, 0.0)
    mom = (w.S[t] - w.S[t - win_s]) / w.S[t - win_s]
    vs_open = (w.S[t] - w.K) / w.K
    if abs(mom) < thresh: return (0, 0.0)
    d = 1 if mom > 0 else -1
    conf = min(1.0, abs(mom) / (thresh * 6))
    if (mom > 0) != (vs_open > 0): conf *= 0.7
    return (d, conf)

def tick_velocity_signal(w, t, win_s=30, thresh=0.01):
    if t < win_s: return (0, 0.0)
    vel = w.mkt_mid[t] - w.mkt_mid[t - win_s]
    if abs(vel) < thresh: return (0, 0.0)
    return (1 if vel > 0 else -1, min(1.0, abs(vel) / (thresh * 5)))

def orderbook_imbalance_signal(w, t, rng=None):
    gap = w.fair[t] - w.mkt_mid[t]
    noise = rng.gauss(0.0, 0.15) if rng else 0.0
    imb = max(-1.0, min(1.0, gap * 8.0 + noise))
    if abs(imb) < 0.30: return (0, 0.0)
    return (1 if imb > 0 else -1, min(1.0, abs(imb)))

def confirmation_score(w, t, rng=None):
    ob_d, ob_c = orderbook_imbalance_signal(w, t, rng)
    vel_d, vel_c = tick_velocity_signal(w, t)
    cex_d, cex_c = cex_momentum_signal(w, t)
    return W_OB*ob_d*ob_c + W_VEL*vel_d*vel_c + W_CEX*cex_d*cex_c



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 6: Six Direction Configs & Runners
# ════════════════════════════════════════════════════════════════════════════════
def d1_config():
    return MarketMakerConfig(inv_cap=50.0, buffer_fee_gas=0.005, hedge=False, rebate=False, signal_skew=False)

def d2_config():
    return MarketMakerConfig(inv_cap=80.0, buffer_fee_gas=0.002, hedge=False, rebate=True, signal_skew=False, delta_band_btc=0.0003)

def d5_config():
    return MarketMakerConfig(inv_cap=50.0, buffer_fee_gas=0.005, hedge=False, rebate=False, signal_skew=True)

def d6_config():
    return MarketMakerConfig(inv_cap=80.0, buffer_fee_gas=0.003, hedge=False, rebate=True, signal_skew=True)

def run_mm_direction(name, cfg_fn, n_windows, seed, sim_cfg):
    rng = random.Random(seed); cfg = cfg_fn()
    return name, [run_mm_window(simulate_window(sim_cfg, rng), cfg) for _ in range(n_windows)]

def _kelly_tokens(capital, model_p, entry, kelly_frac=0.5, max_bet_frac=0.02):
    if entry <= 0 or entry >= 1: return 0.0
    b = (1.0 - entry) / entry
    f = max(0.0, (model_p * b - (1.0 - model_p)) / b) * kelly_frac
    return min(f * capital, max_bet_frac * capital) / entry



def run_d3(n_windows, seed, sim_cfg, initial_capital=1000.0, min_edge=0.03, min_conf=0.15):
    """Direction 3 — Directional edge (4-layer architecture)."""
    rng_w = random.Random(seed); rng_s = random.Random(seed * 31 + 1)
    capital = initial_capital; results = []; consec = 0; pause = 0
    for i in range(n_windows):
        w = simulate_window_regime(sim_cfg, rng_w)
        if pause > 0: pause -= 1; results.append(WindowResult(pnl=0.0)); continue
        chosen = None
        for t in range(w.n - 120, w.n - 30):
            edge = w.fair[t] - w.mkt_mid[t]
            if abs(edge) < min_edge: continue
            conf = confirmation_score(w, t, rng_s)
            if abs(conf) < min_conf: continue
            if (edge > 0) != (conf > 0): continue
            chosen = (t, edge); break
        if chosen is None: results.append(WindowResult(pnl=0.0)); continue
        t, edge = chosen; bullish = edge > 0
        if bullish: model_p = w.fair[t]; entry = w.mkt_mid[t]
        else: model_p = 1.0 - w.fair[t]; entry = 1.0 - w.mkt_mid[t]
        if rng_s.random() > 0.6:  # maker didn't fill -> cross as taker
            entry = min(0.99, entry + 0.01)
        tokens = _kelly_tokens(initial_capital, model_p, entry)
        if tokens <= 0: results.append(WindowResult(pnl=0.0)); continue
        won = w.settle_up if bullish else (not w.settle_up)
        pnl = tokens * ((1.0 if won else 0.0) - entry)
        capital += pnl
        if pnl <= 0: consec += 1
        else: consec = 0
        if consec >= 6: pause = 20; consec = 0
        results.append(WindowResult(pnl=pnl, spread_pnl=pnl, traded=True, won=won, edge=abs(edge), n_fills=1, extra={"t_used": float(t)}))
    return "D3 Directional edge", results

def run_d4(n_windows, seed, sim_cfg, latency_s=2, taker_cost=0.01):
    """Direction 4 — Naive oracle-lag taker (demonstrates edge collapse)."""
    rng = random.Random(seed); results = []; decision_t = 150; size = 10.0
    for _ in range(n_windows):
        w = simulate_window(sim_cfg, rng)
        t_info = max(0, decision_t - latency_s)
        sig_d, sig_c = cex_momentum_signal(w, t_info)
        if sig_d == 0: results.append(WindowResult(pnl=0.0)); continue
        if sig_d > 0:
            entry = min(0.99, w.best_ask[decision_t] + taker_cost)
            pnl = size * ((1.0 if w.settle_up else 0.0) - entry); won = w.settle_up
        else:
            entry = max(0.01, w.best_bid[decision_t] - taker_cost)
            pnl = size * (entry - (1.0 if w.settle_up else 0.0)); won = not w.settle_up
        results.append(WindowResult(pnl=pnl, spread_pnl=pnl, traded=True, won=won,
            edge=abs(w.fair[decision_t] - w.mkt_mid[decision_t])))
    return "D4 CEX-lag taker [+2s,1c]", results



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 7: Historical Data & Replay (for real-data Stage 0)
# ════════════════════════════════════════════════════════════════════════════════
def load_klines(path):
    with open(path) as f: payload = json.load(f)
    klines = payload["klines"] if isinstance(payload, dict) else payload
    return sorted(klines, key=lambda k: k["t"])

def klines_to_seconds(klines):
    out = []
    for i in range(len(klines) - 1):
        o0, o1 = klines[i]["o"], klines[i+1]["o"]
        for s in range(60): out.append(o0 + (s / 60.0) * (o1 - o0))
    out.extend([klines[-1]["c"]] * 60)
    return out

def iter_5min_windows(seconds, klines, window_s=300):
    n = len(seconds); base_ms = klines[0]["t"]
    base_s = base_ms // 1000; start_idx = (-base_s) % 300
    while start_idx + window_s < n:
        prices = seconds[start_idx:start_idx + window_s + 1]
        yield start_idx, base_ms + start_idx * 1000, list(prices)
        start_idx += window_s

def realised_vol_annualised(seconds):
    if len(seconds) < 2: return 0.0
    rets = [math.log(b/a) for a,b in zip(seconds, seconds[1:]) if a > 0 and b > 0]
    if not rets: return 0.0
    n = len(rets); mean = sum(rets)/n
    var = sum((r-mean)**2 for r in rets) / max(1, n-1)
    return math.sqrt(var) * math.sqrt(365.25*24*3600)

def iter_replay_windows(klines_path, cfg, seed=7, max_windows=None, lookback_s=1800):
    klines = load_klines(klines_path); seconds = klines_to_seconds(klines)
    rng = random.Random(seed); n_yielded = 0
    for start_idx, ts_ms, prices in iter_5min_windows(seconds, klines):
        if start_idx >= lookback_s:
            sigma = max(0.05, realised_vol_annualised(seconds[start_idx-lookback_s:start_idx+1]))
        else: sigma = cfg.sigma_annual
        yield build_window_from_path(prices, sigma, cfg, rng)
        n_yielded += 1
        if max_windows and n_yielded >= max_windows: return



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 8: Stage 0 Validation
# ════════════════════════════════════════════════════════════════════════════════
def stage0_report(mode="regime", n_windows=4000, seed=7, klines_path=None):
    sim_cfg = SimConfig()
    if mode == "real":
        if not klines_path or not os.path.exists(klines_path):
            print("ERROR: --stage0 --mode real requires --klines <path>")
            print("Run on a machine with internet:")
            print("  curl -o data.json 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000'")
            return None
        windows = list(iter_replay_windows(klines_path, sim_cfg, seed, n_windows))
    else:
        rng = random.Random(seed)
        fn = simulate_window_regime if mode == "regime" else simulate_window
        windows = [fn(sim_cfg, rng) for _ in range(n_windows)]

    # Calibration
    calib = [(w.fair[180], w.settle_up) for w in windows]
    brier = sum((p - (1.0 if up else 0.0))**2 for p, up in calib) / len(calib)

    # Run D3
    _, results = run_d3(n_windows, seed, sim_cfg)

    metrics = compute_metrics("D3 directional", results)
    traded = [r for r in results if r.traded and r.won is not None]

    # Edge buckets
    edge_bins = [(0.03,0.05),(0.05,0.07),(0.07,0.10),(0.10,0.15),(0.15,1.0)]
    edge_rows = []
    for lo, hi in edge_bins:
        b = [r for r in traded if lo <= r.edge < hi]
        if b: edge_rows.append((lo, hi, len(b), round(100*sum(1 for r in b if r.won)/len(b),1)))
        else: edge_rows.append((lo, hi, 0, 0))

    # t_remaining buckets
    t_bins = [(120,150),(90,120),(60,90),(30,60)]
    t_rows = []
    for lo_r, hi_r in t_bins:
        b = [r for r in traded if "t_used" in r.extra and lo_r <= (300 - r.extra["t_used"]) < hi_r]
        if b: t_rows.append((lo_r, hi_r, len(b), round(100*sum(1 for r in b if r.won)/len(b),1)))
        else: t_rows.append((lo_r, hi_r, 0, 0))

    passed = metrics["win_rate"] > 54 and metrics["profit_factor"] > 1.2 and metrics["total_pnl"] > 0 and brier < 0.22

    # Print report
    print(f"=== Stage 0 Validation ({mode}) ===")
    print(f"windows: {len(windows)}  trades: {metrics['traded_windows']}")
    print(f"\n-- Metrics --")
    print(f"  win rate     : {metrics['win_rate']:.1f}%  (gate: >54%)")
    print(f"  profit factor: {metrics['profit_factor']:.2f}   (gate: >1.2)")
    print(f"  total PnL    : {metrics['total_pnl']:.2f}   (gate: >0)")
    print(f"  Brier score  : {brier:.3f}   (gate: <0.22)")
    print(f"\n-- Edge buckets --")
    for lo, hi, n, wr in edge_rows:
        print(f"  {lo:.2f}..{hi:.2f}  n={n:5d}  win%={wr:5.1f}")
    print(f"\n-- t_remaining buckets --")
    for lo, hi, n, wr in t_rows:
        print(f"  {lo:3d}..{hi:3d}s  n={n:5d}  win%={wr:5.1f}")
    verdict = "PASS" if passed else "FAIL"
    print(f"\nVERDICT: {verdict}")
    if mode != "real":
        print("  NOTE: synthetic data. Re-run with --mode real for a meaningful verdict.")
    return passed



# ════════════════════════════════════════════════════════════════════════════════
# SECTION 9: Main — CLI entry point
# ════════════════════════════════════════════════════════════════════════════════
def run_all_directions(n_windows=3000, seed=7):
    sim_cfg = SimConfig()
    print(f"Polymarket BTC 5-min — 6-direction backtest")
    print(f"windows={n_windows}  seed={seed}  sigma={sim_cfg.sigma_annual}  "
          f"mkt_lag={sim_cfg.mkt_lag_s}s  taker_informed={sim_cfg.taker_informed}\n")
    runs = [
        run_mm_direction("D1 Pure MM (spread)", d1_config, n_windows, seed, sim_cfg),
        run_mm_direction("D2 Rebate farm", d2_config, n_windows, seed, sim_cfg),
        run_d3(n_windows, seed, sim_cfg),
        run_d4(n_windows, seed, sim_cfg),
        run_mm_direction("D5 MM + skew", d5_config, n_windows, seed, sim_cfg),
        run_mm_direction("D6 Combined book", d6_config, n_windows, seed, sim_cfg),
    ]
    rows = [compute_metrics(name, res) for name, res in runs]
    print(format_metrics_table(rows))
    print("\nPer-leg PnL ($):")
    print(f"  {'Direction':<26}{'spread':>10}{'rebate':>10}{'hedge':>10}{'fee/gas':>10}")
    for r in rows:
        print(f"  {r['name']:<26}{r['spread_pnl']:>10.1f}{r['rebate_pnl']:>10.1f}"
              f"{r['hedge_pnl']:>10.1f}{r['fee_gas']:>10.1f}")

def hedge_experiment(n_windows=3000, seed=7):
    sim_cfg = SimConfig()
    print("=== Hedge Experiment: continuous perp delta-hedge ON vs OFF ===\n")
    for label, hedge in [("OFF", False), ("ON", True)]:
        cfg = d1_config(); cfg.hedge = hedge
        rng = random.Random(seed)
        res = [run_mm_window(simulate_window(sim_cfg, rng), cfg) for _ in range(n_windows)]
        m = compute_metrics(f"D1 hedge={label}", res)
        print(f"  hedge={label:3s}  PnL={m['total_pnl']:9.1f}  Sharpe={m['sharpe']:.3f}  "
              f"win%={m['win_rate']:5.1f}  hedge_leg={m['hedge_pnl']:9.1f}")
    print("\nFinding: continuous hedging of 5-min digitals is counterproductive (short gamma).")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--windows", "-n", type=int, default=3000)
    ap.add_argument("--seed", "-s", type=int, default=7)
    ap.add_argument("--direction", "-d", type=int, default=None, help="run only one direction (1-6)")
    ap.add_argument("--stage0", action="store_true", help="run Stage 0 validation")
    ap.add_argument("--mode", choices=("real","regime","synthetic"), default="regime")
    ap.add_argument("--klines", default=None)
    ap.add_argument("--hedge-experiment", action="store_true")
    args = ap.parse_args()

    if args.hedge_experiment:
        hedge_experiment(args.windows, args.seed)
    elif args.stage0:
        stage0_report(args.mode, args.windows, args.seed, args.klines)
    elif args.direction:
        sim_cfg = SimConfig()
        d = args.direction
        if d == 1: name, res = run_mm_direction("D1 Pure MM", d1_config, args.windows, args.seed, sim_cfg)
        elif d == 2: name, res = run_mm_direction("D2 Rebate farm", d2_config, args.windows, args.seed, sim_cfg)
        elif d == 3: name, res = run_d3(args.windows, args.seed, sim_cfg)
        elif d == 4: name, res = run_d4(args.windows, args.seed, sim_cfg)
        elif d == 5: name, res = run_mm_direction("D5 MM+skew", d5_config, args.windows, args.seed, sim_cfg)
        elif d == 6: name, res = run_mm_direction("D6 Combined", d6_config, args.windows, args.seed, sim_cfg)
        else: print(f"Unknown direction {d}"); return
        print(json.dumps(compute_metrics(name, res), indent=2))
    else:
        run_all_directions(args.windows, args.seed)

if __name__ == "__main__":
    main()
