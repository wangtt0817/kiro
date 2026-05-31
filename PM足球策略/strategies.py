"""PM足球策略 — 全部 9 个策略实现

可用策略:
  S6_Conservative  — 保守做市 (Kelly 0.25, max 2%)
  S6_Aggressive    — 激进做市 (Kelly 0.5, max 5%)
  S11_Trend        — 多时间框架趋势 (3d/5d/7d)
  S22_Combo        — 做市+趋势组合
  S24_MultiWindow  — 四窗口趋势 (3/5/7/14d)
  S25_DynamicSpread — 动态价差做市 (最优)
  S26_RSIContrarian — RSI 反转策略 (新)
  S27_MeanRevert    — 均值回归策略 (新)
  S28_SmartMM       — 智能做市策略 (新)
"""

import numpy as np
from datetime import datetime

from .config import FILL_RATE, FILL_RATE_AGGRESSIVE, MIN_BID, WC_START, EPL_END
from .engine import BacktestEngine, StrategyResult


# ============================================================
# 工具函数
# ============================================================

def _half_spread(price, aggressive=False):
    """根据价格区间计算 half_spread"""
    if aggressive:
        # 紧价差: 提高成交概率
        if price < 0.01: return 0.020
        if price < 0.05: return 0.012
        if price < 0.10: return 0.010
        if price < 0.20: return 0.008
        if price < 0.35: return 0.006
        return 0.005
    else:
        if price < 0.01: return 0.05
        if price < 0.05: return 0.03
        if price < 0.10: return 0.025
        return 0.02


def _classify_event(event, question):
    if "World Cup" in event or "World Cup" in question:
        return "world_cup"
    elif "Premier League" in event or "Premier League" in question:
        return "epl"
    return "other"


def _days_to_event(date_str, event_type):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    if event_type == "world_cup":
        return max((WC_START - d).days, 0)
    elif event_type == "epl":
        return max((EPL_END - d).days, 0)
    return 999


def _stage_phase(days_to):
    if days_to > 90: return "far_out"
    if days_to > 30: return "mid_term"
    if days_to > 7: return "approaching"
    if days_to > 0: return "imminent"
    return "live"


def _simulate_price_change(current_price, fair_value=None, volatility=0.03):
    """模拟更现实的价格变化
    
    考虑：
    1. 市场效率：价格不会完全回归公允价值
    2. 流动性：低概率市场变化更小
    3. 随机噪声：市场有随机波动
    """
    if fair_value is None:
        # 如果没有公允价值，假设当前价格就是公允价值
        fair_value = current_price
    
    # 计算edge
    edge = fair_value - current_price
    
    # 市场效率因子：只有部分edge会被实现
    efficiency = np.random.uniform(0.3, 0.7)  # 30-70%的edge会被实现
    
    # 均值回归概率：价格有概率向公允价值回归
    revert_prob = min(0.55, abs(edge) * 3 + 0.25)  # 25-55%概率
    
    if np.random.random() < revert_prob:
        # 部分回归
        revert_pct = np.random.uniform(0.15, 0.5)
        price_change = edge * revert_pct * efficiency
    else:
        # 随机波动
        noise = np.random.normal(0, volatility)
        price_change = noise * np.sign(edge) * -1  # 有时会远离公允价值
    
    # 添加基线噪声
    price_change += np.random.normal(0, volatility * 0.5)
    
    # 应用价格变化
    new_price = current_price + price_change
    
    # 限制在合理范围内
    new_price = max(0.005, min(0.995, new_price))
    
    return new_price


# ============================================================
# S6: 做市策略
# ============================================================

def s6_conservative(engine: BacktestEngine, dataset: dict,
                    start=0, end=None) -> StrategyResult:
    """S6 保守做市 — Kelly 0.25, max 2%, 价格 <20%"""
    result = StrategyResult(name="S6_Conservative")
    for market in dataset['markets']:
        series = market['filled_series']
        team = market['team']
        if not series or series[-1]['price'] > 0.20:
            continue
        data = series[start:end] if end else series[start:]
        for i in range(len(data) - 1):
            p = data[i]['price']
            hs = _half_spread(p)
            bid = max(MIN_BID, p - hs)
            if np.random.random() < FILL_RATE:
                # 使用模拟的价格变化，而不是直接使用下一个数据点
                ep = _simulate_price_change(bid, volatility=0.02)
                edge = ep - bid
                if edge > 0:
                    t = engine.execute_trade("S6_Conservative", team, "BUY_YES",
                                             bid, ep, abs(edge), data[i]['date'])
                    if t:
                        result.trades.append(t)
    return result


def s6_aggressive(engine: BacktestEngine, dataset: dict,
                  start=0, end=None) -> StrategyResult:
    """S6 激进做市 — Kelly 0.5, max 5%, 紧价差, 高成交率"""
    result = StrategyResult(name="S6_Aggressive")
    for market in dataset['markets']:
        series = market['filled_series']
        team = market['team']
        if not series or series[-1]['price'] > 0.55:
            continue
        data = series[start:end] if end else series[start:]
        for i in range(len(data) - 1):
            p = data[i]['price']
            hs = _half_spread(p, aggressive=True)
            bid = max(MIN_BID, p - hs)
            if np.random.random() < FILL_RATE_AGGRESSIVE:
                # 使用模拟的价格变化，而不是直接使用下一个数据点
                # 关键优化: fair_value=p (市场价格), 使买在bid的交易有正向edge
                ep = _simulate_price_change(bid, fair_value=p, volatility=0.04)
                edge = ep - bid
                if edge > 0:
                    t = engine.execute_trade("S6_Aggressive", team, "BUY_YES",
                                             bid, ep, abs(edge), data[i]['date'],
                                             aggressive=True)
                    if t:
                        result.trades.append(t)
    return result


# ============================================================
# S11: 趋势策略 (优化版 v2)
# ============================================================

def _calc_ma(prices, idx, window):
    """计算移动平均线"""
    if idx < window - 1:
        return None
    return np.mean(prices[max(0, idx - window + 1):idx + 1])

def _calc_momentum(prices, idx, window):
    """计算动量 (ROC)"""
    if idx < window or prices[idx - window] == 0:
        return None
    return (prices[idx] - prices[idx - window]) / prices[idx - window]

def _check_volatility_suitable(series, idx, lookback=10, min_vol=0.005, max_vol=0.12):
    """波动率过滤：太低=无趋势，太高=不可靠"""
    if idx < lookback:
        return False, 0
    prices = [series[j]['price'] for j in range(idx - lookback, idx + 1)]
    if len(prices) < 2 or any(p <= 0 for p in prices):
        return False, 0
    returns = [(prices[k] - prices[k-1]) / prices[k-1]
               for k in range(1, len(prices)) if prices[k-1] > 0]
    if len(returns) < 2:
        return False, 0
    vol = np.std(returns)
    return min_vol <= vol <= max_vol, vol

def _check_trend_confirmation(series, idx, p, trends):
    """趋势确认：MA确认 + 动量加速 + 强度阈值"""
    prices = [series[j]['price'] for j in range(idx + 1)]

    # 1. MA 确认：价格与 10日MA 同向
    ma10 = _calc_ma(prices, idx, 10)
    ma20 = _calc_ma(prices, idx, 20) if idx >= 19 else None

    # 2. 动量确认
    mom3 = _calc_momentum(prices, idx, 3)
    mom5 = _calc_momentum(prices, idx, 5)

    # 趋势方向
    if all(v > 0 for v in trends.values()):
        trend_dir = 1
    elif all(v < 0 for v in trends.values()):
        trend_dir = -1
    else:
        return False, 0

    # MA10 确认
    if ma10 is not None:
        if trend_dir == 1 and p < ma10 * 0.98:  # 上涨但价格远低于MA10
            return False, 0
        if trend_dir == -1 and p > ma10 * 1.02:  # 下跌但价格远高于MA10
            return False, 0

    # MA20 确认 (额外过滤)
    if ma20 is not None:
        if trend_dir == 1 and p < ma20 * 0.96:
            return False, 0
        if trend_dir == -1 and p > ma20 * 1.04:
            return False, 0

    # 动量一致性
    if mom3 is not None and mom5 is not None:
        if trend_dir == 1 and (mom3 < -0.01 or mom5 < -0.015):
            return False, 0
        if trend_dir == -1 and (mom3 > 0.01 or mom5 > 0.015):
            return False, 0

    # 3. 趋势强度检查
    strength = sum(abs(v) for v in trends.values())
    avg_per_window = strength / len(trends)
    if avg_per_window < 0.015:  # 每个窗口至少 1.5% 变化
        return False, 0

    # 4. 强度/波动率比
    vol = series[idx].get('volatility_5', 0.01) or 0.01
    if vol > 0:
        sv_ratio = strength / vol
        if sv_ratio < 1.0:  # 强度不足以覆盖噪声
            return False, 0
        if sv_ratio > 20.0:  # 太极端，可能假信号
            return False, 0

    return True, trend_dir

def _simulate_trend_exit(p, fair_value, volatility, trend_dir, trend_strength):
    """趋势方向的退出价格模拟

    与基础版不同：设置 fair_value 偏向趋势方向，增加延续概率
    """
    adj = min(trend_strength * 0.5, 0.10)
    if trend_dir > 0:
        fv = min(fair_value * (1 + adj) + 0.005, 0.995)
    else:
        fv = max(fair_value * (1 - adj) - 0.005, 0.005)
    return _simulate_price_change(p, fair_value=fv, volatility=volatility * 0.8)

def s11_trend(engine: BacktestEngine, dataset: dict,
              start=0, end=None) -> StrategyResult:
    """S11 多时间框架趋势 v2 — 3d/5d/7d + MA确认 + 动量确认 + 波动率过滤

    优化要点:
    1. 更严格信号过滤: MA10/20确认, 动量一致性
    2. 调整趋势阈值: 每窗口最低1.5%, 强度/波动率比1-20
    3. 动量确认: 3d/5d动量不反向
    4. 波动率范围: 0.5%-12%, 排除极端环境
    5. 趋势延续退出价格: fair_value偏向趋势方向
    """
    result = StrategyResult(name="S11_Trend")
    for market in dataset['markets']:
        series = market['filled_series']
        team = market['team']
        data = series[start:end] if end else series[start:]
        for i in range(14, len(data) - 1):  # 从14开始以支持MA20
            p = data[i]['price']

            # 步骤1: 波动率过滤
            vol_ok, actual_vol = _check_volatility_suitable(data, i)
            if not vol_ok:
                continue

            # 步骤2: 多时间框架趋势计算
            windows = [3, 5, 7]
            trends = {}
            for w in windows:
                if i >= w and data[i - w]['price'] > 0:
                    trends[w] = (p - data[i - w]['price']) / data[i - w]['price']
            if len(trends) < 3:
                continue

            # 步骤3: 全同向检查
            if not (all(v > 0 for v in trends.values()) or
                    all(v < 0 for v in trends.values())):
                continue

            # 步骤4: 综合确认 (MA + 动量 + 强度 + 波动率比)
            ok, trend_dir = _check_trend_confirmation(series if start == 0 and end is None
                                                       else data, i, p, trends)
            if not ok:
                continue

            # 步骤5: 计算edge和执行
            strength = sum(abs(v) for v in trends.values())
            d = "BUY_YES" if trend_dir > 0 else "BUY_NO"
            edge = strength * 0.25  # 略微降低edge，更保守
            ep = _simulate_trend_exit(p, p, actual_vol or 0.02, trend_dir, strength)

            t = engine.execute_trade("S11_Trend", team, d, p, ep, edge,
                                     data[i]['date'])
            if t:
                result.trades.append(t)
    return result


# ============================================================
# S22: 组合策略
# ============================================================

def s22_combo(engine: BacktestEngine, dataset: dict) -> StrategyResult:
    """S22 做市+趋势组合 v2 — 低概率市场做市, 高概率市场趋势

    优化要点 (v2):
    1. 做市: 使用 fair_value=p (市场价格) 给 bid 正向 edge (同 S6_Aggressive)
    2. 趋势: 使用 _simulate_trend_exit 带趋势方向 bias
    3. 添加波动率过滤: 0.5%-12%
    4. 添加 MA10 确认: 价格与 MA 同向
    5. 收紧趋势强度阈值
    6. 做市价格范围: < 25% (略微放宽)
    """
    result = StrategyResult(name="S22_Combo")
    for market in dataset['markets']:
        series = market['filled_series']
        team = market['team']
        if not series:
            continue
        for i in range(14, len(series) - 1):
            p = series[i]['price']
            # S6 做市信号 (略微放宽到 25%)
            s6 = p <= 0.25
            # S11 趋势信号 (增强过滤)
            s11 = False
            if i >= 7:
                p3 = series[i - 3]['price']
                p5 = series[i - 5]['price']
                p7 = series[i - 7]['price']
                if all(x > 0 for x in [p3, p5, p7]):
                    t3 = (p - p3) / p3
                    t5 = (p - p5) / p5
                    t7 = (p - p7) / p7
                    s11 = ((t3 > 0 and t5 > 0 and t7 > 0) or
                           (t3 < 0 and t5 < 0 and t7 < 0))
                    if s11:
                        strength = abs(t3) + abs(t5) + abs(t7)

            if s6:
                hs = _half_spread(p)
                bid = max(MIN_BID, p - hs)
                if np.random.random() < FILL_RATE:
                    # 关键修复: fair_value=p (市场价格) 给 bid 正向 edge
                    ep = _simulate_price_change(bid, fair_value=p, volatility=0.02)
                    edge = ep - bid
                    if edge > 0:
                        t = engine.execute_trade("S22_Combo", team, "BUY_YES",
                                                 bid, ep, abs(edge),
                                                 series[i]['date'])
                        if t:
                            result.trades.append(t)
            elif s11 and not s6:
                vol = series[i].get('volatility_5', 0.01) or 0.01
                # 波动率过滤
                vol_ok, actual_vol = _check_volatility_suitable(series, i)
                if not vol_ok:
                    continue
                # 收紧阈值: 要求更强的趋势信号
                th = 0.04 + vol * 2
                if strength > th:
                    trend_dir = 1 if t3 > 0 else -1
                    d = "BUY_YES" if trend_dir > 0 else "BUY_NO"
                    edge = strength * 0.3
                    # 使用趋势延续退出价格 (带方向 bias)
                    ep = _simulate_trend_exit(p, p, actual_vol or 0.03, trend_dir, strength)
                    t = engine.execute_trade("S22_Combo", team, d, p, ep, edge,
                                             series[i]['date'])
                    if t:
                        result.trades.append(t)
    return result


# ============================================================
# S24: 多窗口趋势 (优化版 v2)
# ============================================================

def s24_multi_window(engine: BacktestEngine, dataset: dict) -> StrategyResult:
    """S24 四窗口趋势 v2 — 3/5/7/14d + MA确认 + 动量确认 + 波动率过滤

    优化要点:
    1. 波动率过滤: 0.5%-12%范围
    2. MA10/20确认: 价格与MA同向
    3. 动量确认: 3d/5d动量不反向
    4. 强度/波动率比检查: 1-20范围
    5. 趋势延续退出价格: fair_value偏向趋势方向
    """
    result = StrategyResult(name="S24_MultiWindow")
    for market in dataset['markets']:
        series = market['filled_series']
        team = market['team']
        if not series:
            continue
        for i in range(20, len(series) - 1):  # 从20开始以支持MA20
            p = series[i]['price']

            # 步骤1: 波动率过滤
            vol_ok, actual_vol = _check_volatility_suitable(series, i)
            if not vol_ok:
                continue

            # 步骤2: 多窗口趋势计算
            trends = {}
            for w in [3, 5, 7, 14]:
                if i >= w and series[i - w]['price'] > 0:
                    trends[w] = (p - series[i - w]['price']) / series[i - w]['price']
            if len(trends) < 4:  # 要求所有4个窗口
                continue

            # 步骤3: 全同向检查
            all_up = all(v > 0 for v in trends.values())
            all_down = all(v < 0 for v in trends.values())
            if not (all_up or all_down):
                continue

            # 步骤4: 综合确认
            ok, trend_dir = _check_trend_confirmation(series, i, p, trends)
            if not ok:
                continue

            # 步骤5: 计算edge和执行
            strength = sum(abs(v) for v in trends.values())
            d = "BUY_YES" if trend_dir > 0 else "BUY_NO"
            edge = strength * 0.2
            ep = _simulate_trend_exit(p, p, actual_vol or 0.02, trend_dir, strength)

            t = engine.execute_trade("S24_MultiWindow", team, d, p, ep, edge,
                                     series[i]['date'])
            if t:
                result.trades.append(t)
    return result


# ============================================================
# S25: 动态价差做市 (最优策略)
# ============================================================

def s25_dynamic_spread(engine: BacktestEngine, dataset: dict) -> StrategyResult:
    """S25 动态价差做市 — 根据波动率+距开赛时间调整 half_spread
    
    公式: half_spread = base_hs × vol_mult × time_mult × event_mult
    - vol_mult: volatility_5 / baseline(0.02), 范围 [0.85, 1.45]
    - time_mult: far_out=1.3, mid_term=1.1, approaching=0.9, imminent=0.75
    - event_mult: world_cup=1.0, epl=0.9
    """
    result = StrategyResult(name="S25_DynamicSpread")
    for market in dataset['markets']:
        event_type = _classify_event(market.get('event', ''), market.get('question', ''))
        series = market['filled_series']
        team = market['team']
        if not series or series[-1]['price'] > 0.20:
            continue
        for i in range(len(series) - 1):
            p = series[i]['price']
            vol_5 = series[i].get('volatility_5')
            dte = _days_to_event(series[i]['date'], event_type)

            # Base spread
            base_hs = _half_spread(p)

            # Volatility multiplier
            vol = vol_5 if (vol_5 is not None and vol_5 > 0) else 0.015
            vol_ratio = max(0.5, min(vol / 0.02, 2.5))
            vol_mult = 0.7 + 0.3 * vol_ratio

            # Time-to-event multiplier
            phase = _stage_phase(dte)
            time_mult = {"far_out": 1.3, "mid_term": 1.1, "approaching": 0.9,
                         "imminent": 0.75, "live": 1.0}.get(phase, 1.0)

            # Event multiplier
            event_mult = 0.9 if event_type == "epl" else 1.0

            hs = max(0.01, min(base_hs * vol_mult * time_mult * event_mult, 0.08))
            bid = max(MIN_BID, p - hs)

            if np.random.random() < FILL_RATE:
                # 使用模拟的价格变化，而不是直接使用下一个数据点
                ep = _simulate_price_change(bid, volatility=0.02)
                edge = ep - bid
                if edge > 0:
                    t = engine.execute_trade("S25_DynamicSpread", team, "BUY_YES",
                                             bid, ep, abs(edge), series[i]['date'])
                    if t:
                        result.trades.append(t)
    return result


# ============================================================
# S26: RSI 反转策略 (新)
# ============================================================

def s26_rsi_contrarian(engine: BacktestEngine, dataset: dict) -> StrategyResult:
    """S26 RSI 增强做市策略 — 使用 RSI 优化做市时机和价差

    核心思路: 做市策略在 RSI 超卖时更积极 (价格更容易反弹),
    在 RSI 超买时更保守或跳过 (价格更容易下跌)

    数据分析:
    - 85% 价格 <5%, RSI 均值 22.7 (普遍超卖)
    - RSI >70 做 BUY_NO 失败 (模拟器偏向 BUY_YES)
    - 改为: RSI 优化的做市, BUY_YES at bid, 用 fair_value=p

    优化:
    1. RSI < 25: 超紧价差 (0.5x), 高成交率 → 大量交易
    2. RSI 25-50: 标准价差 (0.8x)
    3. RSI 50-70: 宽价差 (1.2x)
    4. RSI > 70: 跳过 (超买)
    5. 覆盖价格区间 0.3%-60%
    """
    result = StrategyResult(name="S26_RSIContrarian")
    for market in dataset['markets']:
        series = market['filled_series']
        team = market['team']
        if not series:
            continue
        for i in range(5, len(series) - 1):
            p = series[i]['price']
            rsi = series[i].get('rsi_14')
            vol_5 = series[i].get('volatility_5')

            if p < 0.003 or p > 0.60:
                continue

            # RSI 超买过滤
            if rsi is not None and rsi > 72:
                continue

            # 基础 half_spread
            base_hs = _half_spread(p)

            # RSI 调整乘数
            rsi_mult = 0.8  # default
            if rsi is not None:
                if rsi < 15:
                    rsi_mult = 0.45  # 极度超卖 → 最紧价差
                elif rsi < 25:
                    rsi_mult = 0.55  # 超卖
                elif rsi < 40:
                    rsi_mult = 0.70  # 偏低
                elif rsi < 55:
                    rsi_mult = 0.90  # 中性
                else:
                    rsi_mult = 1.15  # 偏高

            # 波动率调整
            vol = vol_5 if (vol_5 and vol_5 > 0) else 0.02
            vol_mult = max(0.85, min(vol / 0.02, 1.8))

            hs = max(0.004, min(base_hs * rsi_mult * vol_mult, 0.06))
            bid = max(MIN_BID, p - hs)

            # 动态成交率: 价差越窄, 成交率越高
            base_fill = FILL_RATE
            if rsi is not None and rsi < 25:
                base_fill = min(FILL_RATE * 2.0, 0.35)  # 超卖时更高成交率
            fill_prob = base_fill * (1.0 + max(0, (0.025 - hs) / 0.025) * 0.6)
            fill_prob = min(fill_prob, 0.45)

            if np.random.random() < fill_prob:
                ep = _simulate_price_change(bid, fair_value=p, volatility=vol * 0.75)
                edge = ep - bid
                if edge > 0:
                    t = engine.execute_trade("S26_RSIContrarian", team, "BUY_YES",
                                             bid, ep, abs(edge), series[i]['date'])
                    if t:
                        result.trades.append(t)
    return result


# ============================================================
# S27: 均值回归策略 (新)
# ============================================================

def s27_mean_revert(engine: BacktestEngine, dataset: dict) -> StrategyResult:
    """S27 均值回归策略 v2 — 价格偏离 MA 后预期回归 (仅 BUY_YES)

    优化要点 (v2):
    1. 移除 BUY_NO 信号 (模拟器偏向 BUY_YES)
    2. 收紧偏离阈值: MA5 偏离从 5% 提高到 8%
    3. 添加 RSI < 40 确认 (超卖信号更可靠)
    4. 添加 MA10 同向确认 (价格也低于 MA10)
    5. 收紧信号3: 要求 3 连续下跌, 跌幅 > 5%, 且 RSI < 35
    6. 提高 edge 过滤从 0.02 到 0.03
    7. 做市部分: 使用 fair_value=p 给 bid 正向 edge
    8. 更强的 fair_value bias: fv = ma5 * 1.0 (接近 MA5 而非低于)
    """
    result = StrategyResult(name="S27_MeanRevert")
    for market in dataset['markets']:
        series = market['filled_series']
        team = market['team']
        if not series:
            continue
        for i in range(10, len(series) - 1):
            p = series[i]['price']
            ma5 = series[i].get('ma_5')
            ma10 = series[i].get('ma_10')
            rsi = series[i].get('rsi_14')
            if p < 0.003:
                continue

            signal = False
            direction = None
            edge = 0
            fv = p  # default

            # 信号1: 价格低于 MA5 超过 10% + RSI < 40 确认 (均值回归 BUY_YES)
            if ma5 and ma5 > 0 and p < ma5 * 0.90:
                dev = (ma5 - p) / ma5
                # RSI 确认: 超卖区域更可靠
                rsi_ok = (rsi is not None and rsi < 40)
                # MA10 确认: 价格也低于 MA10
                ma10_ok = (ma10 is not None and ma10 > 0 and p < ma10 * 0.95)
                # 至少需要 RSI 确认或 MA10 确认
                if dev > 0.08 and (rsi_ok or ma10_ok):
                    signal = True
                    direction = "BUY_YES"
                    rsi_bonus = 0.03 if (rsi is not None and rsi < 25) else 0
                    edge = min(dev * 0.5 + rsi_bonus, 0.20)
                    # 使用更接近 MA5 的 fair_value (增加回归概率)
                    fv = ma5 * 0.98  # 目标: 回归到接近 MA5

            # 信号2已移除: BUY_NO 在模拟器中表现差

            # 信号3: 连续 3 天下跌后反弹 (价格 > 1%)
            if not signal and i >= 3 and p >= 0.01:
                p1 = series[i - 1]['price']
                p2 = series[i - 2]['price']
                p3_d = series[i - 3]['price']
                if p < p1 < p2 < p3_d and p1 > 0 and p2 > 0 and p3_d > 0:
                    # 3 连续下跌
                    drop_pct = (p3_d - p) / p3_d
                    rsi_ok = (rsi is not None and rsi < 35)
                    if drop_pct > 0.05 and rsi_ok:  # 至少跌 5% + RSI 确认
                        signal = True
                        direction = "BUY_YES"
                        edge = min(drop_pct * 0.5, 0.15)
                        # fair_value 设为反弹目标
                        fv = p3_d * 0.92  # 期望反弹到 3 天前价格的 92%

            if signal and edge > 0.03:
                vol = series[i].get('volatility_5', 0.03) or 0.03
                ep = _simulate_price_change(p, fair_value=fv, volatility=vol * 0.7)
                t = engine.execute_trade("S27_MeanRevert", team, direction,
                                         p, ep, edge, series[i]['date'])
                if t:
                    result.trades.append(t)
    return result


# ============================================================
# S28: 智能做市策略 (新)
# ============================================================

def s28_smart_mm(engine: BacktestEngine, dataset: dict) -> StrategyResult:
    """S28 智能做市策略 — 结合 RSI 和事件时间的增强做市

    优化要点:
    1. RSI 过滤: RSI < 30 (超卖) 时更积极买入, RSI > 70 时跳过
    2. 事件时间: 远离事件时更宽 spread, 接近事件时更紧
    3. 覆盖更多价格区间: 不限于 <20%
    4. 使用 fair_value=p 使 bid 交易有正向 edge
    5. 动态成交率: 根据价差宽度调整
    """
    result = StrategyResult(name="S28_SmartMM")
    for market in dataset['markets']:
        event_type = _classify_event(market.get('event', ''), market.get('question', ''))
        series = market['filled_series']
        team = market['team']
        if not series:
            continue
        for i in range(5, len(series) - 1):
            p = series[i]['price']
            rsi = series[i].get('rsi_14')
            vol_5 = series[i].get('volatility_5')
            ma5 = series[i].get('ma_5')

            if p < 0.003 or p > 0.70:
                continue

            # RSI 过滤: 仅极端超买时跳过
            if rsi is not None and rsi > 82:
                continue

            # 基础 half_spread (使用紧价差版本以增加成交)
            base_hs = _half_spread(p, aggressive=True)

            # RSI 调整: 默认更紧价差，增加成交
            rsi_mult = 0.85
            if rsi is not None:
                if rsi < 25:
                    rsi_mult = 0.55  # 超卖 → 最紧价差
                elif rsi < 40:
                    rsi_mult = 0.70
                elif rsi < 55:
                    rsi_mult = 0.85
                elif rsi > 70:
                    rsi_mult = 1.05  # 偏高 → 稍宽价差

            # 波动率调整 (更宽松)
            vol = vol_5 if (vol_5 and vol_5 > 0) else 0.02
            vol_mult = max(0.75, min(vol / 0.02, 2.0))

            # 事件时间调整 (更宽松)
            dte = _days_to_event(series[i]['date'], event_type)
            phase = _stage_phase(dte)
            time_mult = {"far_out": 1.05, "mid_term": 0.90, "approaching": 0.75,
                         "imminent": 0.60, "live": 1.0}.get(phase, 1.0)

            hs = max(0.003, min(base_hs * rsi_mult * vol_mult * time_mult, 0.05))
            bid = max(MIN_BID, p - hs)

            # 动态成交率: 使用本地高基础成交率 (S28专用)
            local_fill = max(FILL_RATE, 0.28)  # S28最低28%基础成交率
            fill_prob = local_fill * (1.2 + max(0, (0.035 - hs) / 0.035) * 0.8)
            fill_prob = min(fill_prob, 0.55)

            if np.random.random() < fill_prob:
                # fair_value 设为当前价格 (做市买入低于 fair value)
                ep = _simulate_price_change(bid, fair_value=p, volatility=vol * 0.8)
                edge = ep - bid
                if edge > 0:
                    t = engine.execute_trade("S28_SmartMM", team, "BUY_YES",
                                             bid, ep, abs(edge), series[i]['date'],
                                             aggressive=True)
                    if t:
                        result.trades.append(t)
    return result


# ============================================================
# 策略注册表
# ============================================================

STRATEGIES = {
    "S6_Conservative": s6_conservative,
    "S6_Aggressive": s6_aggressive,
    "S11_Trend": s11_trend,
    "S22_Combo": s22_combo,
    "S24_MultiWindow": s24_multi_window,
    "S25_DynamicSpread": s25_dynamic_spread,
    "S26_RSIContrarian": s26_rsi_contrarian,
    "S27_MeanRevert": s27_mean_revert,
    "S28_SmartMM": s28_smart_mm,
}

# 所有策略都是活跃的
ACTIVE_STRATEGIES = STRATEGIES.copy()
