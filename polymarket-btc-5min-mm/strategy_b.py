#!/usr/bin/env python3
"""
Strategy B: Rebate-Optimized MM Backtesting Framework

核心逻辑：
1. 利用 Polymarket Liquidity Rewards 作为核心收益
2. 评分函数对距 mid 距离做二次惩罚 → 挂得紧得分高
3. 即使被 fill 时负 spread，返佣也能拉回正 EV
"""

import math
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import json

@dataclass
class RewardQuote:
    """返佣优化报价"""
    side: str  # 'bid' or 'ask'
    price: float
    size: float
    distance_from_mid: float  # 距离 mid 的距离
    expected_reward_score: float  # 预期返佣得分
    timestamp: datetime

@dataclass
class RewardTrade:
    """返佣交易记录"""
    side: str
    price: float
    size: float
    timestamp: datetime
    fair_value_at_fill: float
    expected_reward: float

@dataclass
class MarketRewardInfo:
    """市场返佣信息"""
    condition_id: str
    daily_pool: float  # 每日返佣池
    max_spread: float  # 最大有效 spread
    size_cap: float  # 每档 size 上限
    min_size: float  # 最小 size 要求

class RewardScoreSimulator:
    """返佣得分模拟器"""
    
    def __init__(self, config: dict):
        self.config = config
        self.size_cap = config.get('size_cap', 100.0)
    
    def calculate_order_score(self, price: float, size: float, 
                                adjusted_mid: float, max_spread: float,
                                t_in_book: float) -> float:
        """
        计算单个订单的返佣得分
        
        q(d) = max(0, 1 - (d / max_spread)^2)
        score = size_eff * q(d) * t_in_book
        """
        d = abs(price - adjusted_mid)
        
        if d > max_spread:
            return 0.0
        
        # 二次衰减函数
        q = max(0.0, 1.0 - (d / max_spread) ** 2)
        
        # 有效 size
        size_eff = min(size, self.size_cap)
        
        return size_eff * q * t_in_book
    
    def calculate_market_reward(self, my_orders: List[dict], 
                                  all_orders: List[dict],
                                  daily_pool: float,
                                  adjusted_mid: float,
                                  max_spread: float,
                                  t_in_book: float) -> float:
        """
        计算市场返佣收益
        
        my_daily_reward = (my_score / total_score) * daily_pool
        """
        my_score = sum(
            self.calculate_order_score(
                o['price'], o['size'], adjusted_mid, max_spread, t_in_book
            ) for o in my_orders
        )
        
        total_score = sum(
            self.calculate_order_score(
                o['price'], o['size'], adjusted_mid, max_spread, t_in_book
            ) for o in all_orders
        )
        
        if total_score == 0:
            return 0.0
        
        return (my_score / total_score) * daily_pool

class AdaptiveQuotePositioner:
    """自适应报价位置器"""
    
    def __init__(self, config: dict):
        self.config = config
        self.reward_simulator = RewardScoreSimulator(config)
        
        # 参数
        self.depth_levels = config.get('depth_levels', 3)
        self.depth_steps = config.get('depth_steps', [0, 0.5, 1.0])  # cents
        self.min_requote_interval = config.get('min_requote_interval', 5)  # seconds
        self.max_requote_interval = config.get('max_requote_interval', 60)  # seconds
    
    def find_optimal_distance(self, fair_value: float, 
                                daily_pool: float,
                                max_spread: float,
                                competition_score: float,
                                fill_probability_func) -> float:
        """
        寻找最优报价距离 d*
        
        E[PnL_per_order] = q(d) * size * (daily_pool / total_score) * time_in_book
                         + p_fill(d) * size * (fair_value - price)
                         - hedge_cost(size) * p_fill(d)
                         - gas/replace_count
        """
        # 网格搜索
        best_d = 0.5  # 默认 0.5 cents
        best_ev = -float('inf')
        
        for d in np.arange(0.5, max_spread + 0.5, 0.5):  # 0.5c 步长
            # 计算返佣收益
            q = max(0.0, 1.0 - (d / max_spread) ** 2)
            reward_score = q * self.config.get('base_size', 10.0)
            expected_reward = reward_score * daily_pool / max(1, competition_score)
            
            # 计算 fill 概率
            p_fill = fill_probability_func(d)
            
            # 计算 spread 损益
            spread_pnl = p_fill * self.config.get('base_size', 10.0) * (fair_value - (fair_value - d))
            
            # 计算对冲成本
            hedge_cost = p_fill * self.config.get('hedge_slippage', 0.0001) * self.config.get('base_size', 10.0)
            
            # 计算总 EV
            ev = expected_reward + spread_pnl - hedge_cost
            
            if ev > best_ev:
                best_ev = ev
                best_d = d
        
        return best_d
    
    def generate_quotes(self, fair_value: float, 
                          daily_pool: float,
                          max_spread: float,
                          competition_score: float,
                          inventory: float,
                          inv_cap: float) -> List[RewardQuote]:
        """生成返佣优化报价"""
        # 计算最优距离
        def fill_probability(d):
            # 简化的 fill 概率模型
            return max(0.0, 1.0 - d / max_spread)
        
        optimal_d = self.find_optimal_distance(
            fair_value, daily_pool, max_spread, competition_score, fill_probability
        )
        
        # 生成多档报价
        quotes = []
        for i in range(self.depth_levels):
            d = optimal_d + self.depth_steps[i] * 0.01  # 转换为价格单位
            
            # 双边报价
            bid_price = max(0.01, min(0.99, fair_value - d))
            ask_price = max(0.01, min(0.99, fair_value + d))
            
            # size 根据 inventory 调整
            inv_ratio = inventory / inv_cap
            base_size = self.config.get('base_size', 10.0)
            
            bid_size = base_size * (1 - inv_ratio)
            ask_size = base_size * (1 + inv_ratio)
            
            # 计算预期返佣得分
            q = max(0.0, 1.0 - (d / max_spread) ** 2)
            reward_score = q * base_size
            
            timestamp = datetime.now()
            
            if bid_size > 0:
                quotes.append(RewardQuote('bid', bid_price, bid_size, d, reward_score, timestamp))
            if ask_size > 0:
                quotes.append(RewardQuote('ask', ask_price, ask_size, d, reward_score, timestamp))
        
        return quotes

class StrategyBBacktester:
    """策略 B 回测引擎"""
    
    def __init__(self, config: dict):
        self.config = config
        self.initial_capital = config.get('initial_capital', 1000.0)
        self.reward_simulator = RewardScoreSimulator(config)
        self.quote_positioner = AdaptiveQuotePositioner(config)
        
        # 状态
        self.capital = self.initial_capital
        self.up_tokens = 0.0
        self.down_tokens = 0.0
        self.btc_delta = 0.0
        
        # PnL 跟踪
        self.spread_pnl = 0.0
        self.rebate_pnl = 0.0
        self.hedge_pnl = 0.0
        self.gas_cost = 0.0
        self.total_pnl = 0.0
        
        # 交易记录
        self.trades: List[RewardTrade] = []
        self.quotes: List[RewardQuote] = []
        
        # 市场信息
        self.market_reward_info: Optional[MarketRewardInfo] = None
    
    def set_market_reward_info(self, info: MarketRewardInfo):
        """设置市场返佣信息"""
        self.market_reward_info = info
    
    def calculate_fair_value(self, btc_price: float, K: float, 
                              t_remaining: float, sigma: float) -> float:
        """计算公允价 (简化版)"""
        # 使用 Black-Scholes 数字期权公式
        import math
        from scipy.stats import norm
        
        if t_remaining <= 0:
            return 1.0 if btc_price >= K else 0.0
        
        T = t_remaining / (365.25 * 24 * 3600)  # 年化
        sqrtT = math.sqrt(T)
        d2 = (math.log(btc_price / K) + (0 - 0.5 * sigma**2) * T) / (sigma * sqrtT)
        
        return norm.cdf(d2)
    
    def simulate_fill(self, quote: RewardQuote, market_trades: List[dict]) -> Optional[RewardTrade]:
        """模拟成交"""
        for t in market_trades:
            trade_price = float(t.get('price', 0))
            trade_side = t.get('side', '')
            
            if quote.side == 'bid' and trade_side == 'SELL' and trade_price <= quote.price:
                fill_size = min(quote.size, float(t.get('size', 0)))
                return RewardTrade('buy', trade_price, fill_size, datetime.now(), 0.0, 0.0)
            
            elif quote.side == 'ask' and trade_side == 'BUY' and trade_price >= quote.price:
                fill_size = min(quote.size, float(t.get('size', 0)))
                return RewardTrade('sell', trade_price, fill_size, datetime.now(), 0.0, 0.0)
        
        return None
    
    def update_inventory(self, trade: RewardTrade):
        """更新持仓"""
        if trade.side == 'buy':
            self.up_tokens += trade.size
        else:
            self.down_tokens += trade.size
    
    def calculate_rebate(self, quotes: List[RewardQuote], 
                           adjusted_mid: float, max_spread: float,
                           t_in_book: float) -> float:
        """计算返佣收益"""
        if not self.market_reward_info:
            return 0.0
        
        # 计算我的得分
        my_score = sum(
            self.reward_simulator.calculate_order_score(
                q.price, q.size, adjusted_mid, max_spread, t_in_book
            ) for q in quotes
        )
        
        # 假设总得分是我的得分的 N 倍（竞争）
        competition_factor = self.config.get('competition_factor', 10.0)
        total_score = my_score * competition_factor
        
        if total_score == 0:
            return 0.0
        
        return (my_score / total_score) * self.market_reward_info.daily_pool
    
    def settle_window(self, settlement_price: float, K: float):
        """结算5分钟窗口"""
        # 判断 Up/Down
        is_up = settlement_price >= K
        
        # 计算 Polymarket PnL
        for trade in self.trades:
            if trade.side == 'buy':
                if is_up:
                    self.spread_pnl += trade.size * (1.0 - trade.price)
                else:
                    self.spread_pnl -= trade.size * trade.price
            else:
                if is_up:
                    self.spread_pnl -= trade.size * (1.0 - trade.price)
                else:
                    self.spread_pnl += trade.size * trade.price
        
        # 计算总 PnL
        self.total_pnl = self.spread_pnl + self.rebate_pnl + self.hedge_pnl - self.gas_cost
        
        # 更新资金
        self.capital += self.total_pnl
        
        return {
            'spread_pnl': self.spread_pnl,
            'rebate_pnl': self.rebate_pnl,
            'hedge_pnl': self.hedge_pnl,
            'gas_cost': self.gas_cost,
            'total_pnl': self.total_pnl,
            'capital': self.capital,
            'trades': len(self.trades),
            'rebate_pct': self.rebate_pnl / max(0.01, abs(self.total_pnl)) * 100
        }

def run_strategy_b_backtest(config: dict, binance_data: List[dict],
                              polymarket_data: List[dict]) -> dict:
    """运行策略 B 回测"""
    backtester = StrategyBBacktester(config)
    
    # 初始化
    backtester.capital = config.get('initial_capital', 1000.0)
    
    # 这里简化处理，实际需要根据时间对齐数据
    return {
        'config': config,
        'initial_capital': config.get('initial_capital', 1000.0),
        'status': 'backtest_framework_ready'
    }

def main():
    """主函数"""
    # 配置参数
    config = {
        'initial_capital': 1000.0,
        'base_size': 10.0,
        'inv_cap': 80.0,  # 返佣补贴允许更大仓
        'delta_band': 0.0003,  # 更勤的对冲
        'hedge_slippage': 0.0001,
        'size_cap': 100.0,
        'depth_levels': 3,
        'depth_steps': [0, 0.5, 1.0],
        'min_requote_interval': 5,
        'max_requote_interval': 60,
        'competition_factor': 10.0,
        'market_share_floor': 0.01,
        'reward_pool_floor': 20.0
    }
    
    # 加载数据
    binance_path = '/home/ubuntu/polymarket-btc-5min-mm/data/binance/BTCUSDT_1m.json'
    polymarket_path = '/home/ubuntu/polymarket-btc-5min-mm/data/polymarket/test_50_markets.json'
    
    try:
        with open(binance_path) as f:
            binance_data = json.load(f).get('klines', [])
        print(f"Loaded {len(binance_data)} Binance klines")
    except Exception as e:
        print(f"Error loading Binance data: {e}")
        binance_data = []
    
    try:
        with open(polymarket_path) as f:
            polymarket_data = json.load(f)
        print(f"Loaded {len(polymarket_data)} Polymarket markets")
    except Exception as e:
        print(f"Error loading Polymarket data: {e}")
        polymarket_data = []
    
    # 运行回测
    result = run_strategy_b_backtest(config, binance_data, polymarket_data)
    
    print("\n" + "="*60)
    print("STRATEGY B BACKTEST FRAMEWORK READY")
    print("="*60)
    print(f"Initial Capital: ${config['initial_capital']:,.2f}")
    print(f"Binance Data Points: {len(binance_data)}")
    print(f"Polymarket Markets: {len(polymarket_data)}")
    print("\nConfiguration:")
    for key, value in config.items():
        print(f"  {key}: {value}")

if __name__ == '__main__':
    main()
