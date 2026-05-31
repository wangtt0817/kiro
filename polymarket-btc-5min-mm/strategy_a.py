#!/usr/bin/env python3
"""
Strategy A: Quote-and-Hedge MM Backtesting Framework

核心逻辑：
1. 围绕 Black-Scholes 数字期权公允价双边挂单
2. 赚取 bid-ask spread（目标每笔 1-4 cents）
3. 每次 fill 后立即在 Binance 永续做反向对冲，抹平 BTC delta
4. 临近到期时 gamma 风险大，需要主动收紧/撤单
"""

import math
import numpy as np
from scipy.stats import norm
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import json

@dataclass
class Quote:
    """报价"""
    side: str  # 'bid' or 'ask'
    price: float
    size: float
    timestamp: datetime

@dataclass
class Trade:
    """成交记录"""
    side: str  # 'buy' or 'sell'
    price: float
    size: float
    timestamp: datetime
    fair_value_at_fill: float

@dataclass
class HedgeTrade:
    """对冲交易"""
    side: str  # 'buy' or 'sell'
    price: float
    size: float
    timestamp: datetime
    slippage: float

@dataclass
class MarketState:
    """市场状态"""
    btc_price: float
    timestamp: datetime
    t_remaining: float  # 剩余时间（秒）
    sigma: float  # 年化波动率

@dataclass
class Inventory:
    """持仓"""
    up_tokens: float = 0.0
    down_tokens: float = 0.0
    btc_delta: float = 0.0  # BTC 对冲仓位

class DigitalOptionEngine:
    """数字期权定价引擎"""
    
    @staticmethod
    def digital_call(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> dict:
        """
        计算数字看涨期权的价格和 Greeks
        
        Args:
            S: 当前 BTC 价格
            K: 行权价（5分钟窗口开盘价）
            T: 剩余时间（年化）
            sigma: 年化波动率
            r: 无风险利率
        
        Returns:
            dict with p, delta, gamma, vega
        """
        if T <= 0:
            return {
                'p': 1.0 if S >= K else 0.0,
                'delta': 0.0,
                'gamma': 0.0,
                'vega': 0.0
            }
        
        sqrtT = math.sqrt(T)
        d2 = (math.log(S / K) + (r - 0.5 * sigma**2) * T) / (sigma * sqrtT)
        n_d2 = norm.pdf(d2)
        
        return {
            'p': norm.cdf(d2),
            'delta': n_d2 / (S * sigma * sqrtT),
            'gamma': -n_d2 * d2 / (S**2 * sigma**2 * T),
            'vega': -n_d2 * d2 / sigma
        }

class QuoteGenerator:
    """报价生成器"""
    
    def __init__(self, config: dict):
        self.config = config
        self.min_tick = config.get('min_tick', 0.01)
        self.k_vol = config.get('k_vol', 0.6)
        self.k_inv = config.get('k_inv', 0.02)
        self.buffer_fee_gas = config.get('buffer_fee_gas', 0.005)
        self.gamma_inv = config.get('gamma_inv', 0.015)
        self.gamma_t = config.get('gamma_t', 0.01)
        self.tau_safe = config.get('tau_safe', 90)  # 秒
        self.base_size = config.get('base_size', 10.0)
        self.inv_cap = config.get('inv_cap', 50.0)
    
    def calculate_half_spread(self, t_remaining: float, sigma: float, 
                               inventory: float) -> float:
        """计算 half spread"""
        # 波动率 + 时间分量
        tau = t_remaining / (365.25 * 24 * 3600)  # 转换为年化
        vol_component = self.k_vol * sigma * math.sqrt(tau)
        
        # inventory 分量
        inv_component = self.k_inv * abs(inventory) / self.inv_cap
        
        # 总 half spread
        half_spread = max(self.min_tick, vol_component + inv_component + self.buffer_fee_gas)
        
        return half_spread
    
    def calculate_skew(self, inventory: float, t_remaining: float) -> Tuple[float, float]:
        """计算 mid skew 和 time skew"""
        # inventory skew
        mid_skew = self.gamma_inv * (inventory / self.inv_cap)
        
        # time skew (临近到期时整体偏保守)
        time_skew = self.gamma_t * max(0, 1 - t_remaining / self.tau_safe)
        
        return mid_skew, time_skew
    
    def generate_quotes(self, fair_value: float, t_remaining: float, 
                         sigma: float, inventory: float) -> List[Quote]:
        """生成双边报价"""
        # 计算 half spread
        half_spread = self.calculate_half_spread(t_remaining, sigma, inventory)
        
        # 计算 skew
        mid_skew, time_skew = self.calculate_skew(inventory, t_remaining)
        
        # 计算 bid/ask 价格
        bid_price = max(0.01, min(0.99, fair_value - half_spread - mid_skew - time_skew))
        ask_price = max(0.01, min(0.99, fair_value + half_spread - mid_skew + time_skew))
        
        # 计算 size
        inv_ratio = inventory / self.inv_cap
        bid_size = self.base_size * (1 - inv_ratio)  # 满仓时不再加多
        ask_size = self.base_size * (1 + inv_ratio)  # 满仓时鼓励减仓
        
        # 确保 size 为正
        bid_size = max(0, bid_size)
        ask_size = max(0, ask_size)
        
        timestamp = datetime.now()
        
        quotes = []
        if bid_size > 0:
            quotes.append(Quote('bid', bid_price, bid_size, timestamp))
        if ask_size > 0:
            quotes.append(Quote('ask', ask_price, ask_size, timestamp))
        
        return quotes

class DeltaHedgeEngine:
    """Delta 对冲引擎"""
    
    def __init__(self, config: dict):
        self.config = config
        self.delta_band = config.get('delta_band', 0.0005)  # BTC
        self.hedge_slippage = config.get('hedge_slippage', 0.0001)  # 1bp
    
    def should_hedge(self, total_delta: float, t_remaining: float) -> bool:
        """判断是否需要对冲"""
        # gamma 自适应：剩余时间越短，delta_band 越严
        tau_ratio = t_remaining / 300  # 5分钟 = 300秒
        adjusted_band = self.delta_band * max(0.2, tau_ratio)
        
        return abs(total_delta) > adjusted_band
    
    def calculate_hedge_size(self, total_delta: float) -> float:
        """计算对冲数量"""
        return -total_delta  # 反向对冲
    
    def simulate_hedge(self, side: str, size: float, btc_price: float) -> HedgeTrade:
        """模拟对冲交易"""
        # 计算滑点
        slippage = abs(size) * self.hedge_slippage * btc_price
        
        # 对冲价格（含滑点）
        if side == 'sell':
            price = btc_price * (1 - self.hedge_slippage)
        else:
            price = btc_price * (1 + self.hedge_slippage)
        
        return HedgeTrade(side, price, abs(size), datetime.now(), slippage)

class StrategyABacktester:
    """策略 A 回测引擎"""
    
    def __init__(self, config: dict):
        self.config = config
        self.initial_capital = config.get('initial_capital', 1000.0)
        self.digital_engine = DigitalOptionEngine()
        self.quote_generator = QuoteGenerator(config)
        self.hedge_engine = DeltaHedgeEngine(config)
        
        # 状态
        self.capital = self.initial_capital
        self.inventory = Inventory()
        self.trades: List[Trade] = []
        self.hedge_trades: List[HedgeTrade] = []
        self.quotes: List[Quote] = []
        
        # PnL 跟踪
        self.polymarket_pnl = 0.0
        self.hedge_pnl = 0.0
        self.gas_cost = 0.0
        self.total_pnl = 0.0
        
        # 5分钟窗口参数
        self.K = 0.0  # 行权价（窗口开盘价）
        self.window_start_time = None
        self.window_end_time = None
    
    def start_window(self, open_price: float, start_time: datetime, end_time: datetime):
        """开始新的5分钟窗口"""
        self.K = open_price
        self.window_start_time = start_time
        self.window_end_time = end_time
        self.inventory = Inventory()
        self.trades = []
        self.hedge_trades = []
        self.quotes = []
    
    def calculate_fair_value(self, btc_price: float, t_remaining: float, 
                              sigma: float) -> float:
        """计算公允价"""
        T = t_remaining / (365.25 * 24 * 3600)  # 转换为年化
        result = self.digital_engine.digital_call(btc_price, self.K, T, sigma)
        return result['p']
    
    def calculate_delta(self, btc_price: float, t_remaining: float, 
                         sigma: float) -> float:
        """计算 delta"""
        T = t_remaining / (365.25 * 24 * 3600)
        result = self.digital_engine.digital_call(btc_price, self.K, T, sigma)
        return result['delta']
    
    def simulate_fill(self, quote: Quote, market_trades: List[dict]) -> Optional[Trade]:
        """模拟成交"""
        # 检查是否有匹配的交易
        for t in market_trades:
            trade_price = float(t.get('price', 0))
            trade_side = t.get('side', '')
            
            # 买单匹配 ask，卖单匹配 bid
            if quote.side == 'bid' and trade_side == 'SELL' and trade_price <= quote.price:
                # 成交
                fill_size = min(quote.size, float(t.get('size', 0)))
                fair_value = self.calculate_fair_value(
                    self.config.get('btc_price', 100000),
                    self.config.get('t_remaining', 300),
                    self.config.get('sigma', 0.5)
                )
                return Trade('buy', trade_price, fill_size, datetime.now(), fair_value)
            
            elif quote.side == 'ask' and trade_side == 'BUY' and trade_price >= quote.price:
                # 成交
                fill_size = min(quote.size, float(t.get('size', 0)))
                fair_value = self.calculate_fair_value(
                    self.config.get('btc_price', 100000),
                    self.config.get('t_remaining', 300),
                    self.config.get('sigma', 0.5)
                )
                return Trade('sell', trade_price, fill_size, datetime.now(), fair_value)
        
        return None
    
    def update_inventory(self, trade: Trade):
        """更新持仓"""
        if trade.side == 'buy':
            self.inventory.up_tokens += trade.size
        else:
            self.inventory.down_tokens += trade.size
        
        # 计算 delta
        delta = self.calculate_delta(
            self.config.get('btc_price', 100000),
            self.config.get('t_remaining', 300),
            self.config.get('sigma', 0.5)
        )
        
        # 更新 BTC delta
        self.inventory.btc_delta = (self.inventory.up_tokens - self.inventory.down_tokens) * delta
    
    def check_and_hedge(self, btc_price: float, t_remaining: float):
        """检查并执行对冲"""
        if self.hedge_engine.should_hedge(self.inventory.btc_delta, t_remaining):
            hedge_size = self.hedge_engine.calculate_hedge_size(self.inventory.btc_delta)
            
            if hedge_size > 0:
                side = 'buy'
            else:
                side = 'sell'
            
            hedge_trade = self.hedge_engine.simulate_hedge(side, abs(hedge_size), btc_price)
            self.hedge_trades.append(hedge_trade)
            
            # 更新持仓
            self.inventory.btc_delta = 0.0
            
            # 计算对冲成本
            self.hedge_pnl -= hedge_trade.slippage
    
    def settle_window(self, settlement_price: float):
        """结算5分钟窗口"""
        # 判断 Up/Down
        is_up = settlement_price >= self.K
        
        # 计算 Polymarket PnL
        for trade in self.trades:
            if trade.side == 'buy':
                # 买入 Up token
                if is_up:
                    # Up token 结算为 1
                    self.polymarket_pnl += trade.size * (1.0 - trade.price)
                else:
                    # Up token 结算为 0
                    self.polymarket_pnl -= trade.size * trade.price
            else:
                # 卖出 Up token
                if is_up:
                    # Up token 结算为 1
                    self.polymarket_pnl -= trade.size * (1.0 - trade.price)
                else:
                    # Up token 结算为 0
                    self.polymarket_pnl += trade.size * trade.price
        
        # 计算总 PnL
        self.total_pnl = self.polymarket_pnl + self.hedge_pnl - self.gas_cost
        
        # 更新资金
        self.capital += self.total_pnl
        
        return {
            'polymarket_pnl': self.polymarket_pnl,
            'hedge_pnl': self.hedge_pnl,
            'gas_cost': self.gas_cost,
            'total_pnl': self.total_pnl,
            'capital': self.capital,
            'trades': len(self.trades),
            'hedge_trades': len(self.hedge_trades)
        }

def load_binance_data(filepath: str) -> List[dict]:
    """加载 Binance 数据"""
    with open(filepath) as f:
        data = json.load(f)
    return data.get('klines', [])

def load_polymarket_data(filepath: str) -> List[dict]:
    """加载 Polymarket 数据"""
    with open(filepath) as f:
        data = json.load(f)
    return data.get('trades', [])

def run_backtest(config: dict, binance_data: List[dict], 
                  polymarket_data: List[dict]) -> dict:
    """运行回测"""
    backtester = StrategyABacktester(config)
    
    # 初始化
    backtester.capital = config.get('initial_capital', 1000.0)
    
    # 模拟5分钟窗口
    window_results = []
    
    # 这里简化处理，实际需要根据时间对齐数据
    # 暂时返回配置信息
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
        'min_tick': 0.01,
        'k_vol': 0.6,
        'k_inv': 0.02,
        'buffer_fee_gas': 0.005,
        'gamma_inv': 0.015,
        'gamma_t': 0.01,
        'tau_safe': 90,
        'base_size': 10.0,
        'inv_cap': 50.0,
        'delta_band': 0.0005,
        'hedge_slippage': 0.0001,
        'btc_price': 100000.0,  # 会被动态更新
        't_remaining': 300.0,   # 会被动态更新
        'sigma': 0.5            # 会被动态更新
    }
    
    # 加载数据
    binance_path = '/home/ubuntu/polymarket-btc-5min-mm/data/binance/BTCUSDT_1m.json'
    polymarket_path = '/home/ubuntu/polymarket-btc-5min-mm/data/polymarket/test_50_markets.json'
    
    try:
        binance_data = load_binance_data(binance_path)
        print(f"Loaded {len(binance_data)} Binance klines")
    except Exception as e:
        print(f"Error loading Binance data: {e}")
        binance_data = []
    
    try:
        polymarket_data = load_polymarket_data(polymarket_path)
        print(f"Loaded {len(polymarket_data)} Polymarket markets")
    except Exception as e:
        print(f"Error loading Polymarket data: {e}")
        polymarket_data = []
    
    # 运行回测
    result = run_backtest(config, binance_data, polymarket_data)
    
    print("\n" + "="*60)
    print("STRATEGY A BACKTEST FRAMEWORK READY")
    print("="*60)
    print(f"Initial Capital: ${config['initial_capital']:,.2f}")
    print(f"Binance Data Points: {len(binance_data)}")
    print(f"Polymarket Markets: {len(polymarket_data)}")
    print("\nConfiguration:")
    for key, value in config.items():
        print(f"  {key}: {value}")

if __name__ == '__main__':
    main()
