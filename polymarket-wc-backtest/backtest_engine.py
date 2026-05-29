#!/usr/bin/env python3
"""
Polymarket World Cup 2026 — 10-Strategy Backtest Engine
Version: 002 (Real Data)
Date: 2026-05-21

Runs all 10 strategies on 29 days of REAL historical price data from Polymarket.
Uses Odds API data as cross-market reference.
"""

import json, os, sys
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple

# ============================================================
# Configuration
# ============================================================
BASE_DIR = os.path.expanduser("~/polymarket-wc-backtest")
DATA_DIR = f"{BASE_DIR}/data/clean"
OUTPUT_DIR = f"{BASE_DIR}/results/v002"
os.makedirs(OUTPUT_DIR, exist_ok=True)

INITIAL_CAPITAL = 10000.0
KELLY_FRACTION = 0.25
MAX_BET_PCT = 0.02  # 2% = $200 max per trade
FEE_RATE = 0.02  # 2% Polymarket taker fee

# ============================================================
# Data Structures
# ============================================================
@dataclass
class Trade:
    timestamp: str
    strategy: str
    team: str
    direction: str  # BUY_YES / BUY_NO / SELL
    entry_price: float
    exit_price: float
    size: float
    edge: float
    pnl: float
    outcome: str  # WIN / LOSS
    entry_date: str
    exit_date: str
    holding_days: int

@dataclass
class StrategyResult:
    name: str
    trades: List[Trade] = field(default_factory=list)
    
    @property
    def total_trades(self):
        return len(self.trades)
    
    @property
    def wins(self):
        return sum(1 for t in self.trades if t.outcome == "WIN")
    
    @property
    def losses(self):
        return sum(1 for t in self.trades if t.outcome == "LOSS")
    
    @property
    def win_rate(self):
        return self.wins / self.total_trades if self.total_trades > 0 else 0
    
    @property
    def total_pnl(self):
        return sum(t.pnl for t in self.trades)
    
    @property
    def avg_edge(self):
        edges = [t.edge for t in self.trades]
        return np.mean(edges) if edges else 0
    
    @property
    def avg_pnl_per_trade(self):
        return self.total_pnl / self.total_trades if self.total_trades > 0 else 0

class BacktestEngine:
    def __init__(self, initial_capital=INITIAL_CAPITAL):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.peak_capital = initial_capital
        self.max_drawdown = 0.0
        self.daily_returns = []
        self.equity_curve = []
    
    def kelly_size(self, model_prob, market_price):
        if model_prob <= market_price or market_price <= 0:
            return 0
        edge = model_prob - market_price
        odds = (1 - market_price) / market_price
        kelly = edge / odds if odds > 0 else 0
        return max(0, kelly * KELLY_FRACTION)
    
    def position_size(self, model_prob, market_price):
        kelly_pct = self.kelly_size(model_prob, market_price)
        kelly_amount = self.initial_capital * kelly_pct
        hard_cap = self.initial_capital * MAX_BET_PCT
        return max(0, min(kelly_amount, hard_cap))
    
    def execute_trade(self, strategy, team, direction, entry_price, exit_price, edge, 
                      entry_date, exit_date, holding_days):
        """Execute a trade with REAL price movement."""
        size = self.position_size(
            entry_price + edge if direction == "BUY_YES" else entry_price - edge,
            entry_price
        )
        
        if size < 1:  # Minimum $1 bet
            return None
        
        # Apply fee
        fee = size * FEE_RATE
        
        # Calculate PnL based on REAL price movement
        if direction == "BUY_YES":
            raw_pnl = size * (exit_price - entry_price) / entry_price
        elif direction == "BUY_NO":
            no_entry = 1 - entry_price
            no_exit = 1 - exit_price
            raw_pnl = size * (no_exit - no_entry) / no_entry if no_entry > 0 else 0
        else:
            raw_pnl = 0
        
        pnl = raw_pnl - fee
        outcome = "WIN" if pnl > 0 else "LOSS"
        
        self.capital += pnl
        self.peak_capital = max(self.peak_capital, self.capital)
        drawdown = (self.peak_capital - self.capital) / self.peak_capital
        self.max_drawdown = max(self.max_drawdown, drawdown)
        
        # Track equity curve
        self.equity_curve.append({
            'date': exit_date,
            'capital': self.capital,
            'drawdown': drawdown
        })
        
        return Trade(
            timestamp=entry_date,
            strategy=strategy,
            team=team,
            direction=direction,
            entry_price=round(entry_price, 4),
            exit_price=round(exit_price, 4),
            size=round(size, 2),
            edge=round(edge, 4),
            pnl=round(pnl, 2),
            outcome=outcome,
            entry_date=entry_date,
            exit_date=exit_date,
            holding_days=holding_days
        )

# ============================================================
# Data Loading
# ============================================================
def load_real_price_data():
    """Load REAL historical prices from v002 dataset."""
    with open(f"{DATA_DIR}/wc2026_full_dataset.json") as f:
        dataset = json.load(f)
    
    teams_data = dataset['teams']
    
    # Create price_history dict: team -> list of {date, price}
    price_history = {}
    # Create market_lookup: team -> metadata
    market_lookup = {}
    
    for team_data in teams_data:
        team = team_data['team']
        
        # Price series
        price_history[team] = team_data['price_series']
        
        # Market lookup with Odds API data
        odds_info = team_data.get('odds_api', {}) or {}
        market_lookup[team] = {
            'base_probability': team_data['last_price'],
            'first_price': team_data['first_price'],
            'last_price': team_data['last_price'],
            'price_change': team_data['price_change'],
            'price_change_pct': team_data['price_change_pct'],
            'odds_decimal': odds_info.get('decimal_odds', None),
            'odds_implied_prob': odds_info.get('implied_prob_pct', None) / 100 if odds_info.get('implied_prob_pct') else None,
            'odds_bookmaker': odds_info.get('bookmaker', None),
        }
    
    return price_history, market_lookup

def get_trading_dates(price_history):
    """Extract unique trading dates from price data."""
    all_dates = set()
    for team, series in price_history.items():
        for point in series:
            all_dates.add(point['date'])
    
    return sorted(list(all_dates))

def get_price_on_date(price_history, team, target_date):
    """Get price for a team on a specific date."""
    if team not in price_history:
        return None
    
    # Find closest price point to target date
    for point in price_history[team]:
        if point['date'] == target_date:
            return point['price']
    
    # If exact date not found, find nearest
    dates = [p['date'] for p in price_history[team]]
    if target_date < dates[0]:
        return price_history[team][0]['price']
    elif target_date > dates[-1]:
        return price_history[team][-1]['price']
    
    # Find closest
    for i, d in enumerate(dates):
        if d > target_date:
            return price_history[team][i-1]['price']
    
    return price_history[team][-1]['price']

def get_price_at_index(price_history, team, date_idx, dates):
    """Get price for a team at a specific date index."""
    if team not in price_history:
        return None
    
    target_date = dates[date_idx]
    return get_price_on_date(price_history, team, target_date)

# ============================================================
# Strategy Implementations (Real Data Versions)
# ============================================================

def s1_internal_arb(engine, price_history, market_lookup, dates):
    """S1: Polymarket internal combination arbitrage.
    
    Checks if sum of all YES prices < $1 in multi-leg markets.
    In a proper multi-leg market, sum should be ~$1.
    We look for moments where sum drifts below 0.96.
    """
    result = StrategyResult(name="S1_InternalArb")
    teams = list(price_history.keys())
    
    for date_idx in range(len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        # Calculate sum of all YES prices for this day
        day_prices = {}
        for team in teams:
            price = get_price_at_index(price_history, team, date_idx, dates)
            if price is not None:
                day_prices[team] = price
        
        if len(day_prices) < 10:  # Need enough teams
            continue
        
        total_yes = sum(day_prices.values())
        
        # If sum < 0.96, there's an arb opportunity
        if total_yes < 0.96:
            # Buy all YES contracts
            for team, entry in day_prices.items():
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                
                edge = exit_price - entry
                
                if edge > 0.005:
                    trade = engine.execute_trade("S1_InternalArb", team, "BUY_YES",
                                                entry, exit_price, edge, 
                                                date_str, exit_date, 1)
                    if trade:
                        result.trades.append(trade)
    
    return result

def s2_cross_platform(engine, price_history, market_lookup, dates):
    """S2: Cross-platform arbitrage (PM vs Odds API).
    
    Uses Odds API implied probability as "sharp" price.
    Buys when PM deviates > 3% from bookmaker odds.
    """
    result = StrategyResult(name="S2_CrossPlatform")
    teams = list(price_history.keys())
    
    for date_idx in range(len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        for team in teams:
            entry = get_price_at_index(price_history, team, date_idx, dates)
            if entry is None:
                continue
            
            # Get Odds API implied probability
            odds_prob = market_lookup.get(team, {}).get('odds_implied_prob')
            if odds_prob is None:
                continue
            
            edge = odds_prob - entry
            
            if abs(edge) > 0.03:  # 3% threshold
                direction = "BUY_YES" if edge > 0 else "BUY_NO"
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                
                trade = engine.execute_trade("S2_CrossPlatform", team, direction,
                                            entry, exit_price, abs(edge),
                                            date_str, exit_date, 1)
                if trade:
                    result.trades.append(trade)
    
    return result

def s3_dixon_coles(engine, price_history, market_lookup, dates):
    """S3: Dixon-Coles pre-match value betting.
    
    Uses historical price momentum as proxy for team strength.
    Compares with current price to find value.
    """
    result = StrategyResult(name="S3_DixonColes")
    teams = list(price_history.keys())
    
    # Lookback period for momentum
    lookback = 7
    
    for date_idx in range(lookback, len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        for team in teams:
            # Only 25% of signals are tradeable
            if np.random.random() > 0.25:
                continue
            
            entry = get_price_at_index(price_history, team, date_idx, dates)
            if entry is None:
                continue
            
            # Calculate 7-day momentum
            price_7d_ago = get_price_at_index(price_history, team, date_idx - lookback, dates)
            if price_7d_ago is None:
                continue
            
            momentum = (entry - price_7d_ago) / price_7d_ago if price_7d_ago > 0 else 0
            
            # Fair value based on momentum extrapolation
            fair_value = entry * (1 + momentum * 0.5)  # Dampened momentum
            fair_value = max(0.005, min(0.995, fair_value))
            
            edge = fair_value - entry
            
            if abs(edge) > 0.02:
                direction = "BUY_YES" if edge > 0 else "BUY_NO"
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                
                trade = engine.execute_trade("S3_DixonColes", team, direction,
                                            entry, exit_price, abs(edge),
                                            date_str, exit_date, 1)
                if trade:
                    result.trades.append(trade)
    
    return result

def s4_elo_momentum(engine, price_history, market_lookup, dates):
    """S4: Elo/FIFA ranking momentum.
    
    Uses price change % as proxy for Elo momentum.
    Signals when price deviates from recent trend.
    """
    result = StrategyResult(name="S4_EloMomentum")
    teams = list(price_history.keys())
    
    for date_idx in range(5, len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        for team in teams:
            # Only 15% of signals are actually tradeable
            if np.random.random() > 0.15:
                continue
            
            entry = get_price_at_index(price_history, team, date_idx, dates)
            if entry is None:
                continue
            
            # Get historical prices
            prices = []
            for i in range(max(0, date_idx - 5), date_idx + 1):
                p = get_price_at_index(price_history, team, i, dates)
                if p is not None:
                    prices.append(p)
            
            if len(prices) < 3:
                continue
            
            # Calculate moving average
            ma = np.mean(prices)
            
            # Fair value: mean reversion toward MA
            fair_value = ma * 1.02  # Slight premium for momentum
            fair_value = max(0.005, min(0.995, fair_value))
            
            edge = fair_value - entry
            
            if abs(edge) > 0.015:
                direction = "BUY_YES" if edge > 0 else "BUY_NO"
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                
                trade = engine.execute_trade("S4_EloMomentum", team, direction,
                                            entry, exit_price, abs(edge),
                                            date_str, exit_date, 1)
                if trade:
                    result.trades.append(trade)
    
    return result

def s5_inplay(engine, price_history, market_lookup, dates):
    """S5: In-play goal reaction (overshoot trading).
    
    Uses daily price volatility to detect overshoot opportunities.
    Buys on dips, sells on spikes (mean reversion).
    """
    result = StrategyResult(name="S5_Inplay")
    teams = list(price_history.keys())
    
    for date_idx in range(3, len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        # Only 33% of days have good opportunities
        if np.random.random() > 0.33:
            continue
        
        for team in teams:
            entry = get_price_at_index(price_history, team, date_idx, dates)
            if entry is None:
                continue
            
            # Calculate recent volatility
            prices = []
            for i in range(max(0, date_idx - 3), date_idx + 1):
                p = get_price_at_index(price_history, team, i, dates)
                if p is not None:
                    prices.append(p)
            
            if len(prices) < 2:
                continue
            
            volatility = np.std(prices) / np.mean(prices) if np.mean(prices) > 0 else 0
            
            # Detect overshoot: price > 2 std devs from mean
            mean_price = np.mean(prices)
            std_price = np.std(prices)
            
            if entry > mean_price + 2 * std_price:
                # Overbought - sell
                direction = "BUY_NO"
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                edge = entry - exit_price  # Expecting price to drop
            elif entry < mean_price - 2 * std_price:
                # Oversold - buy
                direction = "BUY_YES"
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                edge = exit_price - entry  # Expecting price to rise
            else:
                continue
            
            if abs(edge) > 0.01:
                trade = engine.execute_trade("S5_Inplay", team, direction,
                                            entry, exit_price, abs(edge),
                                            date_str, exit_date, 1)
                if trade:
                    result.trades.append(trade)
    
    return result

def s6_market_making(engine, price_history, market_lookup, dates):
    """S6: Long-tail market making.
    
    Focuses on low-probability teams (< 5%).
    Provides liquidity and earns spread.
    """
    result = StrategyResult(name="S6_MarketMaking")
    teams = list(price_history.keys())
    
    # Focus on teams with < 5% probability
    long_tail = [t for t in teams if market_lookup.get(t, {}).get('last_price', 1) < 0.05]
    
    for date_idx in range(len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        for team in long_tail[:5]:  # Max 5 positions
            entry = get_price_at_index(price_history, team, date_idx, dates)
            if entry is None:
                continue
            
            # Calculate spread
            half_spread = 0.025 if entry > 0.01 else 0.04
            
            # Buy at bid
            bid_price = max(0.005, entry - half_spread)
            
            # 60% fill rate
            if np.random.random() < 0.6:
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                
                edge = exit_price - bid_price
                
                trade = engine.execute_trade("S6_MarketMaking", team, "BUY_YES",
                                            bid_price, exit_price, abs(edge),
                                            date_str, exit_date, 1)
                if trade:
                    result.trades.append(trade)
    
    return result

def s7_news(engine, price_history, market_lookup, dates):
    """S7: News/injury event-driven.
    
    Detects significant price movements (>5% daily change) as news proxy.
    Trades on continuation or reversal patterns.
    """
    result = StrategyResult(name="S7_News")
    teams = list(price_history.keys())
    
    for date_idx in range(1, len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        # 25% chance of major news per day
        if np.random.random() > 0.25:
            continue
        
        for team in teams:
            entry = get_price_at_index(price_history, team, date_idx, dates)
            prev_price = get_price_at_index(price_history, team, date_idx - 1, dates)
            
            if entry is None or prev_price is None:
                continue
            
            # Detect news: >5% daily change
            daily_change = (entry - prev_price) / prev_price if prev_price > 0 else 0
            
            if abs(daily_change) > 0.05:
                # News detected - trade on continuation
                if daily_change > 0:
                    direction = "BUY_YES"
                    edge = daily_change * 0.5  # Expect 50% continuation
                else:
                    direction = "BUY_NO"
                    edge = abs(daily_change) * 0.5
                
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                
                trade = engine.execute_trade("S7_News", team, direction,
                                            entry, exit_price, edge,
                                            date_str, exit_date, 1)
                if trade:
                    result.trades.append(trade)
    
    return result

def s8_monte_carlo(engine, price_history, market_lookup, dates):
    """S8: Monte Carlo group qualification simulation.
    
    Uses price volatility to simulate outcomes.
    Compares simulated fair value with market price.
    """
    result = StrategyResult(name="S8_MonteCarlo")
    teams = list(price_history.keys())
    
    # Only run simulation every 3 days
    for date_idx in range(7, len(dates) - 1, 3):
        date_str = dates[date_idx]
        exit_date = dates[min(date_idx + 3, len(dates) - 1)]
        
        for team in teams:
            entry = get_price_at_index(price_history, team, date_idx, dates)
            if entry is None:
                continue
            
            # Get historical prices for simulation
            prices = []
            for i in range(max(0, date_idx - 7), date_idx + 1):
                p = get_price_at_index(price_history, team, i, dates)
                if p is not None:
                    prices.append(p)
            
            if len(prices) < 3:
                continue
            
            # Monte Carlo simulation
            n_sims = 1000
            returns = np.diff(prices) / prices[:-1] if len(prices) > 1 else [0]
            mean_return = np.mean(returns)
            std_return = np.std(returns) if len(returns) > 1 else 0.01
            
            sim_prices = []
            for _ in range(n_sims):
                sim_return = np.random.normal(mean_return, std_return)
                sim_price = entry * (1 + sim_return)
                sim_price = max(0.005, min(0.995, sim_price))
                sim_prices.append(sim_price)
            
            fair_value = np.mean(sim_prices)
            
            edge = fair_value - entry
            
            if abs(edge) > 0.02:
                direction = "BUY_YES" if edge > 0 else "BUY_NO"
                
                # Use price at exit date
                exit_price = get_price_at_index(price_history, team, 
                                               min(date_idx + 3, len(dates) - 1), dates)
                if exit_price is None:
                    continue
                
                holding_days = min(3, len(dates) - 1 - date_idx)
                
                trade = engine.execute_trade("S8_MonteCarlo", team, direction,
                                            entry, exit_price, abs(edge),
                                            date_str, exit_date, holding_days)
                if trade:
                    result.trades.append(trade)
    
    return result

def s9_closing_line(engine, price_history, market_lookup, dates):
    """S9: Closing line reverse engineering.
    
    Uses Odds API implied probability as closing line proxy.
    Compares with PM price to find value.
    """
    result = StrategyResult(name="S9_ClosingLine")
    teams = list(price_history.keys())
    
    for date_idx in range(len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        for team in teams:
            # Only 15% of signals are actually tradeable
            if np.random.random() > 0.15:
                continue
            
            entry = get_price_at_index(price_history, team, date_idx, dates)
            if entry is None:
                continue
            
            # Get Odds API closing line
            closing_prob = market_lookup.get(team, {}).get('odds_implied_prob')
            if closing_prob is None:
                continue
            
            edge = closing_prob - entry
            
            if abs(edge) > 0.02:
                direction = "BUY_YES" if edge > 0 else "BUY_NO"
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                
                trade = engine.execute_trade("S9_ClosingLine", team, direction,
                                            entry, exit_price, abs(edge),
                                            date_str, exit_date, 1)
                if trade:
                    result.trades.append(trade)
    
    return result

def s10_betfair(engine, price_history, market_lookup, dates):
    """S10: Betfair Exchange cross-market arbitrage.
    
    Uses Odds API multi-bookmaker spread as Betfair proxy.
    Buys when PM price is below bookmaker consensus.
    """
    result = StrategyResult(name="S10_Betfair")
    teams = list(price_history.keys())
    
    for date_idx in range(len(dates) - 1):
        date_str = dates[date_idx]
        exit_date = dates[date_idx + 1]
        
        for team in teams:
            entry = get_price_at_index(price_history, team, date_idx, dates)
            if entry is None:
                continue
            
            # Use Odds API implied probability as Betfair proxy
            betfair_prob = market_lookup.get(team, {}).get('odds_implied_prob')
            if betfair_prob is None:
                continue
            
            edge = betfair_prob - entry
            
            if abs(edge) > 0.02:
                direction = "BUY_YES" if edge > 0 else "BUY_NO"
                exit_price = get_price_at_index(price_history, team, date_idx + 1, dates)
                if exit_price is None:
                    continue
                
                trade = engine.execute_trade("S10_Betfair", team, direction,
                                            entry, exit_price, abs(edge),
                                            date_str, exit_date, 1)
                if trade:
                    result.trades.append(trade)
    
    return result

# ============================================================
# Main Runner
# ============================================================
def compute_metrics(result: StrategyResult, engine: BacktestEngine) -> dict:
    """Compute comprehensive metrics for a strategy."""
    if result.total_trades == 0:
        return {
            "name": result.name,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "roi_pct": 0,
            "avg_edge": 0,
            "avg_pnl_per_trade": 0,
            "max_drawdown_pct": 0,
            "profit_factor": 0,
            "sharpe_approx": 0,
            "final_capital": engine.capital,
            "max_consecutive_losses": 0,
            "avg_holding_days": 0,
        }
    
    pnls = [t.pnl for t in result.trades]
    wins_pnl = [p for p in pnls if p > 0]
    losses_pnl = [p for p in pnls if p <= 0]
    holding_days = [t.holding_days for t in result.trades]
    
    # Max consecutive losses
    max_consec = 0
    current_consec = 0
    for p in pnls:
        if p <= 0:
            current_consec += 1
            max_consec = max(max_consec, current_consec)
        else:
            current_consec = 0
    
    # Sharpe approximation
    if len(pnls) > 1:
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(252) if np.std(pnls) > 0 else 0
    else:
        sharpe = 0
    
    profit_factor = sum(wins_pnl) / abs(sum(losses_pnl)) if losses_pnl and sum(losses_pnl) != 0 else float('inf')
    
    return {
        "name": result.name,
        "total_trades": result.total_trades,
        "wins": result.wins,
        "losses": result.losses,
        "win_rate": round(result.win_rate * 100, 1),
        "total_pnl": round(result.total_pnl, 2),
        "roi_pct": round(result.total_pnl / INITIAL_CAPITAL * 100, 2),
        "avg_edge": round(result.avg_edge * 100, 2),
        "avg_pnl_per_trade": round(result.avg_pnl_per_trade, 2),
        "max_drawdown_pct": round(engine.max_drawdown * 100, 2),
        "profit_factor": round(min(profit_factor, 99.9), 2),
        "sharpe_approx": round(sharpe, 2),
        "final_capital": round(engine.capital, 2),
        "max_consecutive_losses": max_consec,
        "avg_holding_days": round(np.mean(holding_days), 1) if holding_days else 0,
    }


def run_all_strategies():
    """Run all 10 strategies on real data."""
    print("Loading real price data...")
    price_history, market_lookup = load_real_price_data()
    dates = get_trading_dates(price_history)
    
    print(f"Loaded {len(price_history)} teams, {len(dates)} trading days")
    print(f"Date range: {dates[0]} to {dates[-1]}")
    print()
    
    strategies = [
        ("S1_InternalArb", s1_internal_arb),
        ("S2_CrossPlatform", s2_cross_platform),
        ("S3_DixonColes", s3_dixon_coles),
        ("S4_EloMomentum", s4_elo_momentum),
        ("S5_Inplay", s5_inplay),
        ("S6_MarketMaking", s6_market_making),
        ("S7_News", s7_news),
        ("S8_MonteCarlo", s8_monte_carlo),
        ("S9_ClosingLine", s9_closing_line),
        ("S10_Betfair", s10_betfair),
    ]
    
    all_results = {}
    
    print("=" * 100)
    print(f"{'Strategy':<22} {'Trades':>6} {'Win%':>6} {'PnL':>10} {'ROI%':>8} {'MaxDD%':>8} {'Sharpe':>8} {'PF':>6} {'AvgHold':>8}")
    print("=" * 100)
    
    for name, func in strategies:
        engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
        result = func(engine, price_history, market_lookup, dates)
        metrics = compute_metrics(result, engine)
        all_results[name] = metrics
        
        print(f"{name:<22} {metrics['total_trades']:>6} {metrics['win_rate']:>5.1f}% "
              f"${metrics['total_pnl']:>8.2f} {metrics['roi_pct']:>7.2f}% "
              f"{metrics['max_drawdown_pct']:>7.2f}% {metrics['sharpe_approx']:>7.2f} "
              f"{metrics['profit_factor']:>5.2f} {metrics['avg_holding_days']:>6.1f}d")
    
    print("=" * 100)
    
    # Summary
    total_pnl = sum(r['total_pnl'] for r in all_results.values())
    avg_roi = np.mean([r['roi_pct'] for r in all_results.values()])
    best = max(all_results.items(), key=lambda x: x[1]['total_pnl'])
    worst = min(all_results.items(), key=lambda x: x[1]['total_pnl'])
    
    print(f"\n📊 SUMMARY")
    print(f"  Combined PnL:    ${total_pnl:,.2f}")
    print(f"  Average ROI:     {avg_roi:.2f}%")
    print(f"  Best strategy:   {best[0]} (${best[1]['total_pnl']:,.2f})")
    print(f"  Worst strategy:  {worst[0]} (${worst[1]['total_pnl']:,.2f})")
    
    return all_results


if __name__ == "__main__":
    np.random.seed(42)  # Reproducibility
    
    results = run_all_strategies()
    
    # Save results
    output_path = f"{OUTPUT_DIR}/backtest_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n💾 Results saved to: {output_path}")
