"""signal_strategy.py — 多方向信号交易框架 (单文件版)

把 analysis_outputs/ 的真实交叉分析结论 (Grafana 盈亏信号 × Gate 30m K线,
2026-04-27 ~ 2026-05-27) 转化为可运行、可回测、可插入真实数据的策略代码。

本文件是以下模块合并而成的单文件版, 章节顺序即依赖顺序:
  [1] CONFIG     全部真实数据标定常量 (改这里即可调参)
  [2] DATAFEED   EventRow schema + 真实CSV加载 + 标定合成生成器
  [3] FEATURES   滞后分桶 / stress 标志 / capitulation 判定
  [4] SESSION    方向 D: 时段乘子
  [5] UNIVERSE   方向 E: 白/黑名单 + 动态评分
  [6] REGIME     方向 C: 趋势/震荡判别
  [7] SIGNALS    方向 A/B: 信号生成 (叠加全部过滤)
  [8] PORTFOLIO  仓位 + 账户级风控
  [9] BACKTEST   事件驱动回测 + 指标 + 多维归因
  [10] FOOTBALL  方向 F: 足球策略真实退出修复
  [11] RUN       CLI 入口 (含朴素基线 + 样本内外切分)

用法:
  python signal_strategy.py                  # 合成数据自检
  python signal_strategy.py --events 50000 --seed 11
  python signal_strategy.py --csv merged.csv # 接入真实合并数据
  python signal_strategy.py --football       # 方向 F: 足球真实退出演示

⚠️ 合成数据仅用于验证逻辑正确性; 真实 edge 必须用 --csv 接入真实合并数据复现。
免责声明: 仅用于策略研究与回测, 不构成投资建议。加密货币交易风险极高。
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional


# ============================================================
# [1] CONFIG — 全局配置与真实数据标定常量
# 来源: deep_cross_analysis.json / grafana_profit_loss_gate_state_summary.json / 盈利数据.txt
# ⚠️ 这些是历史样本统计, 非未来保证; 需滚动复核 + 样本外验证后才上线。
# ============================================================

# ---- 资金 / 成本 ----
INITIAL_CAPITAL = 10_000.0      # 账户初始权益 (U)
TAKER_FEE = 0.0005              # 单边吃单手续费 (Gate 永续 ~0.05%)
SLIPPAGE = 0.0003               # 单边滑点估计
ROUND_TRIP_COST = 2 * (TAKER_FEE + SLIPPAGE)   # 一进一出总成本 ≈ 0.16%

# ---- 仓位 / 风控 ----
BASE_RISK_FRAC = 0.010          # 单笔基础仓位 (占当前权益)
MAX_POSITION_FRAC = 0.05        # 单笔硬上限
MAX_DAILY_LOSS_FRAC = 0.04      # 单日亏损上限 (触发后当日停手)
MAX_CONCURRENT = 6              # 最大同时持仓数

# ---- K线口径 ----
BAR_MINUTES = 30                # 1 根 K线 = 30 分钟
DEFAULT_HOLD_BARS = 6           # 默认持仓 6 根 (=3 小时)

# ---- 方向 A: 反追涨均值回归 — STEP 7 滞后性分析 ----
# 按"信号出现时价格过去3h已涨幅"分桶:
#   <1%   : up3 50.3%, fwd6 -0.16%   -> 中性, 不交易
#   1-3%  : up3 50.8%, fwd6 +0.09%   -> 轻多
#   3-5%  : up3 51.0%, fwd6 +0.25%   -> 跟随做多 (甜区)
#   5-10% : up3 44.8%, fwd3 -0.65%   -> fade 做空
#   >10%  : up3 45.9%, fwd6 -1.88%   -> 强 fade 做空
LAG_BUCKETS = [
    # (past_ret_low, past_ret_high, side, raw_edge_pct, hold_bars)
    (-1e9,  1.0,  "FLAT",  0.00, 0),
    (1.0,   3.0,  "LONG",  0.09, 6),
    (3.0,   5.0,  "LONG",  0.25, 6),
    (5.0,   10.0, "SHORT", 0.65, 3),
    (10.0,  1e9,  "SHORT", 1.88, 6),
]
A_EDGE_COST_MULT = 2.0          # raw_edge_pct/100 >= ROUND_TRIP_COST * 2 才入场

# ---- 方向 B: 恐慌反转抄底 — STEP 6 超大盈利(>1000U) ----
# 过去3h跌幅中位 -11.24%, 未来1根 +3.04%, 上涨概率 54.1%
CAPITULATION_PAST_DROP = -8.0   # 过去3h跌幅 <= -8%
CAPITULATION_MIN_SCORE = 500.0  # gf_score_30m >= 500U (大额事件代理)
CAPITULATION_STOP_FALLING = 0.0 # ret_5m >= 0 (止跌确认)
CAPITULATION_EDGE_PCT = 3.04    # 历史未来1根收益中位
CAPITULATION_HOLD_BARS = 2      # 反弹快且短, 持 1-3 根
CAPITULATION_STOP_PCT = 3.0     # 再创新低 -3% 止损

# ---- 方向 C: 趋势/震荡判别 — state_summary stress/stable ----
REGIME_TREND_AMP_15M = 6.0      # amp_15m >= 6 -> 趋势/承压
REGIME_TREND_VOL_RATIO = 3.0    # vol_ratio_5v30 >= 3 -> 放量承压
REGIME_TREND_RET_5M = 3.0       # |ret_5m| >= 3% -> 单边动量
REGIME_RANGE_AMP_15M = 4.0      # amp_15m < 4 -> 偏震荡
REGIME_RANGE_VOL_LOW = 0.6
REGIME_RANGE_VOL_HIGH = 1.5
REGIME_MULT = {
    # regime:    A(reversion)  B(capitulation)
    "range":   {"A": 1.0, "B": 0.8},
    "trend":   {"A": 0.3, "B": 0.5},    # 趋势市禁止重仓 fade
    "neutral": {"A": 0.7, "B": 0.7},
}

# ---- 方向 D: 时段执行闸门 — STEP 9 UTC 时段热力 ----
# 乘子 0.0 = 禁止开新仓; 1.0 = 黄金时段全仓
SESSION_MULT = {
    0: 0.20,  1: 0.20,  2: 0.65,  3: 0.80,  4: 0.50,  5: 0.90,
    6: 0.85,  7: 0.00,  8: 0.85,  9: 0.85, 10: 1.00, 11: 0.85,
    12: 1.00, 13: 0.90, 14: 1.00, 15: 0.90, 16: 1.00, 17: 0.70,
    18: 0.50, 19: 0.00, 20: 0.65, 21: 0.00, 22: 0.50, 23: 0.00,
}
# 参考(净盈利U/胜率): 黄金 10(+202K/61%) 16(+255K/60%) 14(+238K/59%)
#                    禁止 23(-542K/44%) 07(-266K/50%) 19(-77K/52%) 21(-25K/48%)

# ---- 方向 E: 标的白/黑名单 — STEP 8 代币级别盈亏 ----
WHITELIST = {
    "lab_usdt", "skyai_usdt", "beat_usdt", "bsb_usdt", "wld_usdt",
    "near_usdt", "saga_usdt", "bio_usdt", "ub_usdt", "hype_usdt",
    "dogs_usdt", "vvv_usdt", "swarms_usdt", "zerebro_usdt", "grass_usdt",
}
BLACKLIST = {   # 结构性亏损; btc_usdt 单独 -390K
    "btc_usdt", "gua_usdt", "zkj_usdt", "siren_usdt", "esports_usdt",
    "dam_usdt", "genius_usdt", "fartcoin_usdt", "aia_usdt", "inx_usdt",
}
UNIVERSE_MULT = {"white": 1.0, "neutral": 0.6, "black": 0.0}

# ---- 全局 Stress 过滤 — signal_* 命中统计 (坏结果占比) ----
#   gate_5m_down_3 : 61.1% 坏 | amp_15m>=6 : 59.7% 坏 | vol_ratio>=3 : 48.3% 坏
STRESS_RET_5M_DOWN = -3.0       # ret_5m <= -3%
STRESS_AMP_15M = 6.0
STRESS_VOL_RATIO = 3.0

# ---- 方向 F: 足球策略真实退出修复 ----
FOOTBALL_FILL_RATE_REAL = 0.10  # 真实低流动性成交率 (替代 0.28-0.55)
FOOTBALL_FEE_RATE = 0.02
FOOTBALL_SLIPPAGE = 0.002


# ============================================================
# [2] DATAFEED — 数据接入层
# ============================================================
@dataclass
class EventRow:
    """一条已合并的 Grafana×Gate 事件 (对应 30m K线的某一根)。

    ts/contract/gf_score_30m/gf_delta_5m/gf_delta_15m/price/past_ret_3h/
    ret_5m/ret_15m/ret_30m/amp_15m/vol_ratio_5v30 与 analysis_outputs 对齐。
    fwd_ret_1/3/6 = 未来 1/3/6 根 K线收益(%); 仅回测可见, 实盘为 None。
    """
    ts: datetime
    contract: str
    gf_score_30m: float
    gf_delta_5m: float
    gf_delta_15m: float
    price: float
    past_ret_3h: float
    ret_5m: float
    ret_15m: float
    ret_30m: float
    amp_15m: float
    vol_ratio_5v30: float
    fwd_ret_1: Optional[float] = None
    fwd_ret_3: Optional[float] = None
    fwd_ret_6: Optional[float] = None

    @property
    def hour(self) -> int:
        return self.ts.hour

    def fwd_ret(self, bars: int) -> Optional[float]:
        return {1: self.fwd_ret_1, 3: self.fwd_ret_3, 6: self.fwd_ret_6}.get(bars)


def load_events_csv(path: str) -> List[EventRow]:
    """从真实合并 CSV 读取事件。

    期望表头 (缺失列将置 0/None):
      ts,contract,gf_score_30m,gf_delta_5m,gf_delta_15m,price,past_ret_3h,
      ret_5m,ret_15m,ret_30m,amp_15m,vol_ratio_5v30,fwd_ret_1,fwd_ret_3,fwd_ret_6
    ts 支持 ISO8601 (含 'Z')。回测需预计算 fwd_ret_*; 实盘流式接入可留空。
    """
    def _f(row, key, default=0.0):
        v = row.get(key, "")
        if v is None or v == "":
            return default
        try:
            return float(v)
        except ValueError:
            return default

    def _of(row, key):
        v = row.get(key, "")
        if v is None or v == "":
            return None
        try:
            return float(v)
        except ValueError:
            return None

    rows: List[EventRow] = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            ts_raw = r.get("ts", "").replace("Z", "+00:00")
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            rows.append(EventRow(
                ts=ts.astimezone(timezone.utc),
                contract=r.get("contract", "unknown"),
                gf_score_30m=_f(r, "gf_score_30m"),
                gf_delta_5m=_f(r, "gf_delta_5m"),
                gf_delta_15m=_f(r, "gf_delta_15m"),
                price=_f(r, "price", 1.0) or 1.0,
                past_ret_3h=_f(r, "past_ret_3h"),
                ret_5m=_f(r, "ret_5m"),
                ret_15m=_f(r, "ret_15m"),
                ret_30m=_f(r, "ret_30m"),
                amp_15m=_f(r, "amp_15m"),
                vol_ratio_5v30=_f(r, "vol_ratio_5v30", 1.0) or 1.0,
                fwd_ret_1=_of(r, "fwd_ret_1"),
                fwd_ret_3=_of(r, "fwd_ret_3"),
                fwd_ret_6=_of(r, "fwd_ret_6"),
            ))
    rows.sort(key=lambda e: e.ts)
    return rows


_NEUTRAL_CONTRACTS = [
    "ton_usdt", "sui_usdt", "play_usdt", "in_usdt", "naoris_usdt",
    "ordi_usdt", "wlfi_usdt", "dash_usdt", "fida_usdt", "q_usdt",
]


@dataclass
class SyntheticFeed:
    """生成标定到真实条件结构的事件流。

    标定目标: 滞后分桶占比/各桶未来收益(STEP7)、时段胜率(STEP9)、
    超大盈利急跌反弹(STEP6)、stress 惩罚(state_summary)、白黑名单偏向(STEP8)。
    它【不是】真实行情, 上线前必须用 load_events_csv 接入真实数据再验证。
    """
    n_events: int = 20_000
    seed: int = 7
    start: datetime = field(
        default_factory=lambda: datetime(2026, 4, 27, tzinfo=timezone.utc))

    def generate(self) -> List[EventRow]:
        rng = random.Random(self.seed)
        rows: List[EventRow] = []
        t = self.start
        contracts = (list(WHITELIST) + _NEUTRAL_CONTRACTS + list(BLACKLIST))
        weights = ([3.0] * len(WHITELIST)
                   + [2.0] * len(_NEUTRAL_CONTRACTS)
                   + [2.5] * len(BLACKLIST))

        for _ in range(self.n_events):
            t = t + timedelta(minutes=rng.randint(1, 4))
            hour = t.hour
            contract = rng.choices(contracts, weights=weights, k=1)[0]

            # 1) 滞后分桶 (past_ret_3h)
            bucket = rng.choices(
                ["none", "small", "mid", "big", "huge"],
                weights=[53.9, 10.2, 7.9, 13.2, 14.8], k=1)[0]
            past_ret_3h = {
                "none": rng.uniform(-1.0, 1.0),
                "small": rng.uniform(1.0, 3.0),
                "mid": rng.uniform(3.0, 5.0),
                "big": rng.uniform(5.0, 10.0),
                "huge": rng.uniform(10.0, 25.0),
            }[bucket]

            # 2) Grafana 分数
            gf_score_30m = max(0.0, rng.lognormvariate(4.2, 1.0))
            is_capit = rng.random() < 0.012
            if is_capit:
                past_ret_3h = rng.uniform(-18.0, CAPITULATION_PAST_DROP)
                gf_score_30m = rng.uniform(CAPITULATION_MIN_SCORE, 3000.0)
            gf_delta_5m = rng.gauss(gf_score_30m * 0.1, 30)
            gf_delta_15m = rng.gauss(gf_score_30m * 0.2, 50)

            # 3) 短周期收益 / 振幅 / 量比
            ret_5m = rng.gauss(0.0, 1.2)
            ret_15m = rng.gauss(0.0, 1.8)
            ret_30m = rng.gauss(past_ret_3h * 0.15, 2.0)
            amp_15m = abs(rng.gauss(2.6, 1.6)) + abs(ret_15m) * 0.3
            vol_ratio_5v30 = max(0.1, rng.lognormvariate(-0.05, 0.45))

            stress = rng.random() < 0.18
            if stress:
                amp_15m = rng.uniform(6.0, 20.0)
                vol_ratio_5v30 = rng.uniform(2.5, 6.0)
                ret_5m = rng.uniform(-6.0, -1.0)

            # 4) 未来收益 (核心: 条件化结构)
            mu = self._forward_mu(bucket, hour, contract, stress, is_capit, ret_5m)
            sigma = 1.8 + amp_15m * 0.15
            fwd1 = rng.gauss(mu, sigma)
            fwd3 = rng.gauss(mu * 1.6, sigma * 1.4)
            fwd6 = rng.gauss(mu * 2.1, sigma * 1.8)

            price = round(rng.uniform(0.2, 50.0), 4)
            rows.append(EventRow(
                ts=t, contract=contract,
                gf_score_30m=round(gf_score_30m, 2),
                gf_delta_5m=round(gf_delta_5m, 2),
                gf_delta_15m=round(gf_delta_15m, 2),
                price=price,
                past_ret_3h=round(past_ret_3h, 3),
                ret_5m=round(ret_5m, 3),
                ret_15m=round(ret_15m, 3),
                ret_30m=round(ret_30m, 3),
                amp_15m=round(amp_15m, 3),
                vol_ratio_5v30=round(vol_ratio_5v30, 3),
                fwd_ret_1=round(fwd1, 4),
                fwd_ret_3=round(fwd3, 4),
                fwd_ret_6=round(fwd6, 4),
            ))
        return rows

    def _forward_mu(self, bucket, hour, contract, stress, is_capit, ret_5m) -> float:
        """未来 1 根收益的漂移中枢(%). 把策略要捕捉的 edge 显式写入数据生成。"""
        base = {
            "none": -0.03, "small": 0.03, "mid": 0.08,
            "big": -0.22, "huge": -0.63,
        }[bucket]
        if is_capit and ret_5m >= -0.5:
            base = +1.0
        sess = SESSION_MULT.get(hour, 0.5)
        base += (sess - 0.6) * 0.25
        if stress:
            base -= 0.35
        if contract in WHITELIST:
            base += 0.06
        elif contract in BLACKLIST:
            base -= 0.20
        return base


def make_dataset(n_events: int = 20_000, seed: int = 7) -> List[EventRow]:
    """便捷入口: 生成一份合成数据集。"""
    return SyntheticFeed(n_events=n_events, seed=seed).generate()


# ============================================================
# [3] FEATURES — 特征派生层 (纯函数, 真实/合成数据通用)
# ============================================================
@dataclass
class LagBucket:
    side: str          # "LONG" / "SHORT" / "FLAT"
    raw_edge_pct: float
    hold_bars: int
    label: str


def classify_lag(past_ret_3h: float) -> LagBucket:
    """按过去3h涨幅落入 STEP 7 的滞后分桶。"""
    for lo, hi, side, edge, hold in LAG_BUCKETS:
        if lo < past_ret_3h <= hi:
            return LagBucket(side=side, raw_edge_pct=edge, hold_bars=hold,
                             label=f"({lo:g},{hi:g}]")
    lo, hi, side, edge, hold = LAG_BUCKETS[0]
    return LagBucket(side=side, raw_edge_pct=edge, hold_bars=hold, label=f"(-inf,{hi:g}]")


@dataclass
class StressFlags:
    down_5m: bool       # ret_5m <= -3%
    high_amp: bool      # amp_15m >= 6
    high_vol: bool      # vol_ratio >= 3

    @property
    def any(self) -> bool:
        return self.down_5m or self.high_amp or self.high_vol

    @property
    def count(self) -> int:
        return int(self.down_5m) + int(self.high_amp) + int(self.high_vol)


def stress_flags(ev: EventRow) -> StressFlags:
    """计算 state_summary 中的三个 stress 信号。"""
    return StressFlags(
        down_5m=ev.ret_5m <= STRESS_RET_5M_DOWN,
        high_amp=ev.amp_15m >= STRESS_AMP_15M,
        high_vol=ev.vol_ratio_5v30 >= STRESS_VOL_RATIO,
    )


def is_capitulation(ev: EventRow) -> bool:
    """急跌 + 大额盈利信号 + 止跌确认 (STEP 6 超大盈利)。"""
    return (
        ev.past_ret_3h <= CAPITULATION_PAST_DROP
        and ev.gf_score_30m >= CAPITULATION_MIN_SCORE
        and ev.ret_5m >= CAPITULATION_STOP_FALLING
    )


@dataclass
class Features:
    lag: LagBucket
    stress: StressFlags
    capitulation: bool


def derive(ev: EventRow) -> Features:
    return Features(
        lag=classify_lag(ev.past_ret_3h),
        stress=stress_flags(ev),
        capitulation=is_capitulation(ev),
    )


# ============================================================
# [4] SESSION — 方向 D: 时段执行闸门
# ============================================================
def session_multiplier(hour_utc: int) -> float:
    """返回该 UTC 小时的仓位乘子 [0.0, 1.0]。"""
    return SESSION_MULT.get(hour_utc % 24, 0.5)


def is_tradeable_hour(hour_utc: int) -> bool:
    return session_multiplier(hour_utc) > 0.0


def session_tier(hour_utc: int) -> str:
    m = session_multiplier(hour_utc)
    if m == 0.0:
        return "forbidden"
    if m >= 0.9:
        return "prime"
    if m >= 0.65:
        return "good"
    return "weak"


# ============================================================
# [5] UNIVERSE — 方向 E: 标的白/黑名单 + 动态评分
# ============================================================
def universe_class(contract: str) -> str:
    c = contract.lower()
    if c in BLACKLIST:
        return "black"
    if c in WHITELIST:
        return "white"
    return "neutral"


def universe_multiplier(contract: str) -> float:
    return UNIVERSE_MULT[universe_class(contract)]


def is_tradeable_contract(contract: str) -> bool:
    return universe_multiplier(contract) > 0.0


def rolling_score(
    realized_pnl_by_contract: Dict[str, float],
    top_frac: float = 0.25,
    bottom_frac: float = 0.25,
) -> Dict[str, str]:
    """滚动窗口已实现盈亏 -> 动态分 white/neutral/black (推荐生产用法)。"""
    if not realized_pnl_by_contract:
        return {}
    items = sorted(realized_pnl_by_contract.items(), key=lambda kv: kv[1], reverse=True)
    n = len(items)
    n_top = max(1, int(n * top_frac))
    n_bot = max(1, int(n * bottom_frac))
    result: Dict[str, str] = {}
    for i, (c, _) in enumerate(items):
        if i < n_top:
            result[c] = "white"
        elif i >= n - n_bot:
            result[c] = "black"
        else:
            result[c] = "neutral"
    return result


# ============================================================
# [6] REGIME — 方向 C: 趋势/震荡判别
# ============================================================
def classify_regime(ev: EventRow) -> str:
    """返回 'trend' / 'range' / 'neutral'。"""
    if (ev.amp_15m >= REGIME_TREND_AMP_15M
            or ev.vol_ratio_5v30 >= REGIME_TREND_VOL_RATIO
            or abs(ev.ret_5m) >= REGIME_TREND_RET_5M):
        return "trend"
    if (ev.amp_15m < REGIME_RANGE_AMP_15M
            and REGIME_RANGE_VOL_LOW <= ev.vol_ratio_5v30 <= REGIME_RANGE_VOL_HIGH):
        return "range"
    return "neutral"


def regime_multiplier(regime: str, direction: str) -> float:
    return REGIME_MULT.get(regime, REGIME_MULT["neutral"]).get(direction, 0.5)


# ============================================================
# [7] SIGNALS — 方向 A/B: 信号生成 (叠加全部过滤)
# ============================================================
@dataclass
class Signal:
    ts: object
    contract: str
    direction: str          # "A" reversion / "B" capitulation / "N" naive
    side: str               # "LONG" / "SHORT"
    entry_price: float
    hold_bars: int
    raw_edge_pct: float
    confidence: float       # 0..1 = session × universe × regime
    stop_pct: Optional[float]
    regime: str
    note: str = ""

    @property
    def expected_edge_pct(self) -> float:
        return self.raw_edge_pct * self.confidence


def signal_reversion(ev: EventRow, feats: Optional[Features] = None) -> Optional[Signal]:
    """方向 A: 涨3-5%跟随做多, 涨>5% fade做空 (STEP 7)。"""
    feats = feats or derive(ev)
    if feats.stress.any:                       # 1) 逆势, 承压态否决
        return None
    lag = feats.lag                            # 2) 方向
    if lag.side == "FLAT":
        return None
    if lag.raw_edge_pct / 100.0 < ROUND_TRIP_COST * A_EDGE_COST_MULT:   # 3) edge 门槛
        return None
    sess = session_multiplier(ev.hour)         # 4) 三层乘子
    if sess <= 0.0:
        return None
    uni = universe_multiplier(ev.contract)
    if uni <= 0.0:
        return None
    regime = classify_regime(ev)
    reg = regime_multiplier(regime, "A")
    confidence = sess * uni * reg
    if confidence <= 0.0:
        return None
    return Signal(
        ts=ev.ts, contract=ev.contract, direction="A", side=lag.side,
        entry_price=ev.price, hold_bars=lag.hold_bars,
        raw_edge_pct=lag.raw_edge_pct, confidence=round(confidence, 4),
        stop_pct=None, regime=regime,
        note=f"lag{lag.label} sess{sess:.2f} uni{uni:.2f} reg{reg:.2f}",
    )


def signal_capitulation(ev: EventRow, feats: Optional[Features] = None) -> Optional[Signal]:
    """方向 B: 急跌(-8%+) + 大额信号 + 止跌确认 -> 做多反弹 (STEP 6)。"""
    feats = feats or derive(ev)
    if not feats.capitulation:
        return None
    if feats.stress.high_vol:                  # 仍剧烈放量 -> 多半下跌中继, 否决
        return None
    sess = session_multiplier(ev.hour)
    if sess <= 0.0:
        return None
    uni = universe_multiplier(ev.contract)
    if uni <= 0.0:
        return None
    regime = classify_regime(ev)
    reg = regime_multiplier(regime, "B")
    confidence = sess * uni * reg
    if confidence <= 0.0:
        return None
    return Signal(
        ts=ev.ts, contract=ev.contract, direction="B", side="LONG",
        entry_price=ev.price, hold_bars=CAPITULATION_HOLD_BARS,
        raw_edge_pct=CAPITULATION_EDGE_PCT, confidence=round(confidence, 4),
        stop_pct=CAPITULATION_STOP_PCT, regime=regime,
        note=f"capit past{ev.past_ret_3h:.1f}% score{ev.gf_score_30m:.0f} sess{sess:.2f}",
    )


def generate_signals(ev: EventRow) -> List[Signal]:
    """对单根事件依次尝试各方向 (B 优先; A/B 互斥)。"""
    feats = derive(ev)
    b = signal_capitulation(ev, feats)
    if b:
        return [b]
    a = signal_reversion(ev, feats)
    return [a] if a else []


def naive_follow(ev: EventRow) -> List[Signal]:
    """基线: 看到盈利信号就追多, 无任何过滤 (证明裸信号无 edge)。"""
    if ev.gf_score_30m <= 50:
        return []
    return [Signal(
        ts=ev.ts, contract=ev.contract, direction="N", side="LONG",
        entry_price=ev.price, hold_bars=3, raw_edge_pct=0.3, confidence=1.0,
        stop_pct=None, regime="na", note="naive",
    )]


# ============================================================
# [8] PORTFOLIO — 仓位 + 账户级风控
# ============================================================
@dataclass
class Account:
    equity: float = INITIAL_CAPITAL
    peak_equity: float = INITIAL_CAPITAL
    max_drawdown: float = 0.0
    daily_pnl: Dict[str, float] = field(default_factory=dict)
    open_positions: int = 0

    def day_key(self, ts) -> str:
        return ts.strftime("%Y-%m-%d")

    def can_open(self, ts) -> bool:
        if self.open_positions >= MAX_CONCURRENT:
            return False
        d = self.daily_pnl.get(self.day_key(ts), 0.0)
        if d <= -abs(INITIAL_CAPITAL * MAX_DAILY_LOSS_FRAC):
            return False
        return True

    def register_pnl(self, ts, pnl: float):
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0
        self.max_drawdown = max(self.max_drawdown, dd)
        k = self.day_key(ts)
        self.daily_pnl[k] = self.daily_pnl.get(k, 0.0) + pnl


def position_notional(sig: Signal, equity: float) -> float:
    """仓位 = 权益 × 基础风险 × confidence × edge缩放, 受硬上限限制。"""
    edge_ratio = (sig.raw_edge_pct / 100.0) / ROUND_TRIP_COST if ROUND_TRIP_COST > 0 else 1.0
    edge_scale = max(0.5, min(edge_ratio / 2.0, 2.0))
    frac = BASE_RISK_FRAC * sig.confidence * edge_scale
    frac = max(0.0, min(frac, MAX_POSITION_FRAC))
    return equity * frac


# ============================================================
# [9] BACKTEST — 事件驱动回测 + 指标 + 多维归因
# 退出价用真实 fwd_ret_*, 而非足球策略那种"模拟出的有利价格"。
# ============================================================
@dataclass
class Trade:
    ts: object
    contract: str
    direction: str
    side: str
    regime: str
    hold_bars: int
    notional: float
    gross_ret: float
    net_ret: float
    pnl: float
    stopped: bool
    hour: int
    lag_label: str
    uni_class: str

    @property
    def win(self) -> bool:
        return self.pnl > 0


def _settle(sig: Signal, ev: EventRow) -> tuple[float, bool]:
    """用真实未来收益结算, 返回 (gross_ret, stopped)。
    stop 用 fwd_ret_1 作早盘触发代理。"""
    sign = 1.0 if sig.side == "LONG" else -1.0
    fwd = ev.fwd_ret(sig.hold_bars)
    if fwd is None:
        fwd = ev.fwd_ret_1 or 0.0
    gross = sign * (fwd / 100.0)
    stopped = False
    if sig.stop_pct is not None and ev.fwd_ret_1 is not None:
        early = sign * (ev.fwd_ret_1 / 100.0)
        if early <= -(sig.stop_pct / 100.0):
            gross = -(sig.stop_pct / 100.0)
            stopped = True
    return gross, stopped


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    account: Account = field(default_factory=Account)
    n_events: int = 0
    n_signals: int = 0

    def metrics(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {"trades": 0, "note": "no trades"}
        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        mean = sum(pnls) / n
        std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / n) if n > 1 else 0.0
        sharpe = (mean / std * math.sqrt(n)) if std > 0 else 0.0
        return {
            "trades": n,
            "signals": self.n_signals,
            "events": self.n_events,
            "signal_rate_pct": round(self.n_signals / self.n_events * 100, 2) if self.n_events else 0,
            "win_rate_pct": round(len(wins) / n * 100, 1),
            "total_pnl": round(sum(pnls), 2),
            "roi_pct": round(sum(pnls) / INITIAL_CAPITAL * 100, 2),
            "final_equity": round(self.account.equity, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.9,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "max_drawdown_pct": round(self.account.max_drawdown * 100, 2),
            "sharpe_per_trade": round(sharpe, 2),
            "stopped_count": sum(1 for t in self.trades if t.stopped),
        }

    def attribution(self, key: Callable[["Trade"], str]) -> Dict[str, dict]:
        groups: Dict[str, List[Trade]] = {}
        for t in self.trades:
            groups.setdefault(key(t), []).append(t)
        out = {}
        for g, ts in sorted(groups.items()):
            pnl = sum(t.pnl for t in ts)
            wr = sum(1 for t in ts if t.win) / len(ts) * 100
            out[g] = {"trades": len(ts), "pnl": round(pnl, 2), "win_rate_pct": round(wr, 1)}
        return out


def run_backtest(
    events: List[EventRow],
    signal_fn: Callable[[EventRow], List[Signal]] = generate_signals,
) -> BacktestResult:
    res = BacktestResult()
    res.n_events = len(events)
    for ev in events:
        sigs = signal_fn(ev)
        res.n_signals += len(sigs)
        for sig in sigs:
            if not res.account.can_open(ev.ts):
                continue
            notional = position_notional(sig, res.account.equity)
            if notional < 1.0:
                continue
            gross, stopped = _settle(sig, ev)
            net = gross - ROUND_TRIP_COST
            pnl = notional * net
            res.account.register_pnl(ev.ts, pnl)
            res.trades.append(Trade(
                ts=ev.ts, contract=ev.contract, direction=sig.direction,
                side=sig.side, regime=sig.regime, hold_bars=sig.hold_bars,
                notional=round(notional, 2), gross_ret=round(gross, 5),
                net_ret=round(net, 5), pnl=round(pnl, 2), stopped=stopped,
                hour=ev.ts.hour, lag_label=classify_lag(ev.past_ret_3h).label,
                uni_class=universe_class(ev.contract),
            ))
    return res


# 便捷归因 key
def by_direction(t: Trade) -> str: return f"{t.direction}_{t.side}"
def by_session_tier(t: Trade) -> str: return f"{t.hour:02d}_{session_tier(t.hour)}"
def by_regime(t: Trade) -> str: return t.regime
def by_lag(t: Trade) -> str: return t.lag_label
def by_universe(t: Trade) -> str: return t.uni_class


# ============================================================
# [10] FOOTBALL — 方向 F: 足球策略真实退出修复
# 退出价 = 真实未来价格 (非 _simulate_price_change), 并对做市建模逆向选择。
# ============================================================
def _fb_half_spread(price: float, spread_frac: float = 0.03) -> float:
    """按价格比例的 half_spread (替代原框架的绝对值 0.02-0.05)。"""
    return max(price * spread_frac, 0.001)


@dataclass
class FxTrade:
    date: str
    team: str
    entry: float
    exit: float
    size: float
    pnl: float
    win: bool


@dataclass
class FxResult:
    name: str
    trades: List[FxTrade] = field(default_factory=list)

    def metrics(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {"name": self.name, "trades": 0}
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        gw, gl = sum(wins), abs(sum(losses))
        return {
            "name": self.name,
            "trades": n,
            "win_rate_pct": round(len(wins) / n * 100, 1),
            "total_pnl": round(sum(t.pnl for t in self.trades), 2),
            "profit_factor": round(gw / gl, 2) if gl > 0 else 99.9,
            "avg_win": round(gw / len(wins), 2) if wins else 0,
            "avg_loss": round(-gl / len(losses), 2) if losses else 0,
        }


def market_make_real(
    dataset: dict,
    price_cap: float = 0.20,
    hold: int = 1,
    spread_frac: float = 0.03,
    size_per_trade: float = 100.0,
    model_adverse_selection: bool = True,
) -> FxResult:
    """诚实版做市: 退出价=真实未来价格 + 建模挂单逆向选择。

    model_adverse_selection=True: 限价买单只在价格下探触及 bid 时成交,
    成交集中在下跌途中 -> 价差收益被逆向选择抵消 (做市真实风险)。
    False: 无条件成交 (高估收益, 仅作对照, 即原框架的乐观假设)。
    """
    res = FxResult(name=f"MM_real(cap={price_cap},hold={hold},spread={spread_frac},"
                        f"adverse={model_adverse_selection})")
    cost = FOOTBALL_FEE_RATE + FOOTBALL_SLIPPAGE
    for market in dataset.get("markets", []):
        series = market.get("filled_series") or []
        team = market.get("team", "?")
        if not series or series[-1]["price"] > price_cap:
            continue
        for i in range(len(series) - hold - 1):
            p = series[i]["price"]
            if p <= 0:
                continue
            hs = _fb_half_spread(p, spread_frac)
            bid = max(0.003, p - hs)
            next_p = series[i + 1]["price"]
            if model_adverse_selection:
                # bar 内最低价近似 = min(开,收) 再向下伸出半个 bar 振幅 (下影线)
                bar_low = min(p, next_p) - 0.5 * abs(p - next_p)
                if bar_low > bid:
                    continue
            exit_price = series[i + 1 + hold]["price"]   # ← 真实未来价格
            raw_ret = (exit_price - bid) / bid
            pnl = size_per_trade * (raw_ret - cost)
            res.trades.append(FxTrade(
                date=series[i]["date"], team=team, entry=round(bid, 4),
                exit=round(exit_price, 4), size=size_per_trade,
                pnl=round(pnl, 2), win=pnl > 0,
            ))
    return res


def trend_follow_real(
    dataset: dict,
    windows=(3, 5, 7),
    hold: int = 3,
    size_per_trade: float = 100.0,
) -> FxResult:
    """诚实版趋势: 全同向触发, 退出按真实未来价格 (不偏置 fair_value)。"""
    res = FxResult(name=f"Trend_real(win={windows},hold={hold})")
    cost = FOOTBALL_FEE_RATE + FOOTBALL_SLIPPAGE
    for market in dataset.get("markets", []):
        series = market.get("filled_series") or []
        team = market.get("team", "?")
        n = len(series)
        start = max(windows)
        for i in range(start, n - hold):
            p = series[i]["price"]
            if p <= 0:
                continue
            trends = []
            ok = True
            for w in windows:
                pw = series[i - w]["price"]
                if pw <= 0:
                    ok = False
                    break
                trends.append((p - pw) / pw)
            if not ok:
                continue
            all_up = all(x > 0 for x in trends)
            all_down = all(x < 0 for x in trends)
            if not (all_up or all_down):
                continue
            side = 1 if all_up else -1
            exit_price = series[i + hold]["price"]       # ← 真实未来价格
            raw_ret = side * (exit_price - p) / p
            pnl = size_per_trade * (raw_ret - cost)
            res.trades.append(FxTrade(
                date=series[i]["date"], team=team, entry=round(p, 4),
                exit=round(exit_price, 4), size=size_per_trade,
                pnl=round(pnl, 2), win=pnl > 0,
            ))
    return res


def _fb_demo_dataset(n_markets: int = 60, n_points: int = 120, seed: int = 1) -> dict:
    """低概率足球市场价格 (可加性鞅, 无漂移): price_next = price + N(0, vol_abs)。"""
    rng = random.Random(seed)
    markets = []
    for m in range(n_markets):
        price = rng.uniform(0.03, 0.18)
        series = []
        for t in range(n_points):
            vol_abs = max(0.002, price * 0.04)
            price = max(0.005, min(0.30, price + rng.gauss(0, vol_abs)))
            series.append({"date": f"2026-05-{(t % 28) + 1:02d}", "price": round(price, 4)})
        markets.append({"team": f"team_{m}", "filled_series": series})
    return {"markets": markets}


def football_demo():
    ds = _fb_demo_dataset()
    mm_adverse = market_make_real(ds, model_adverse_selection=True)
    mm_naive = market_make_real(ds, model_adverse_selection=False)
    tr = trend_follow_real(ds)
    print("=" * 64)
    print("方向 F — 足球策略真实退出修复 (演示: 无漂移随机游走价格)")
    print("=" * 64)
    print("[做市·含逆向选择 (诚实)]   ", mm_adverse.metrics())
    print("[做市·无逆向选择 (高估对照)]", mm_naive.metrics())
    print("[趋势·真实退出]            ", tr.metrics())
    print("-" * 64)
    print("解读:")
    print("  1) 趋势在无漂移价格上扣费后必然亏损 (PF<1) — 真实退出价戳破了")
    print("     原框架 _simulate_trend_exit 把 fair_value 偏向趋势方向的循环论证。")
    print("  2) 做市'无逆向选择'仍赚价差 — 这正是原框架高估收益的来源。")
    print("  3) 做市'含逆向选择'诚实组胜率/盈亏比大幅回落 — 才是做市真实风险面貌。")
    print("=" * 64)


# ============================================================
# [11] RUN — CLI 入口
# ============================================================
def _print_metrics(title: str, res: BacktestResult):
    m = res.metrics()
    print(f"\n{'='*64}\n{title}\n{'='*64}")
    if m.get("trades", 0) == 0:
        print("  无交易")
        return
    order = [
        ("events", "事件数"), ("signals", "信号数"), ("signal_rate_pct", "信号率%"),
        ("trades", "成交数"), ("win_rate_pct", "胜率%"), ("total_pnl", "总盈亏U"),
        ("roi_pct", "ROI%"), ("final_equity", "期末权益U"), ("profit_factor", "盈亏比"),
        ("avg_win", "均盈U"), ("avg_loss", "均亏U"), ("max_drawdown_pct", "最大回撤%"),
        ("sharpe_per_trade", "夏普(每笔)"), ("stopped_count", "止损次数"),
    ]
    for k, label in order:
        if k in m:
            print(f"  {label:<12}: {m[k]}")


def _print_attr(title: str, attr: dict):
    print(f"\n  -- {title} --")
    print(f"  {'组':<18}{'笔数':>8}{'盈亏U':>12}{'胜率%':>8}")
    for g, v in attr.items():
        print(f"  {g:<18}{v['trades']:>8}{v['pnl']:>12.2f}{v['win_rate_pct']:>8.1f}")


def _top_hours(attr: dict, n: int = 8) -> dict:
    items = sorted(attr.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
    head = dict(items[:n // 2])
    head.update(dict(items[-n // 2:]))
    return head


def main():
    ap = argparse.ArgumentParser(description="多方向信号交易框架 (单文件版)")
    ap.add_argument("--events", type=int, default=20_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--csv", type=str, default=None, help="真实合并 CSV 路径")
    ap.add_argument("--oos-split", type=float, default=0.7, help="样本内占比")
    ap.add_argument("--football", action="store_true", help="只运行方向 F 足球演示")
    args = ap.parse_args()

    if args.football:
        football_demo()
        return

    if args.csv:
        print(f"加载真实数据: {args.csv}")
        events = load_events_csv(args.csv)
    else:
        print(f"生成合成数据 (标定到真实统计): {args.events} 事件, seed={args.seed}")
        print("⚠️ 合成数据仅用于验证逻辑, 上线前必须用 --csv 接入真实数据")
        events = make_dataset(n_events=args.events, seed=args.seed)
    if not events:
        print("无事件, 退出。")
        return
    print(f"事件总数: {len(events)}  时间范围: {events[0].ts.date()} ~ {events[-1].ts.date()}")

    res = run_backtest(events, generate_signals)
    _print_metrics("多方向策略 (A 反追涨 + B 恐慌反转 + 时段/标的/regime/stress 过滤)", res)

    base = run_backtest(events, naive_follow)
    _print_metrics("基线: 朴素跟随 (看到盈利信号就追多, 无过滤)", base)

    print(f"\n{'='*64}\n归因分析 (多方向策略)\n{'='*64}")
    _print_attr("按方向", res.attribution(by_direction))
    _print_attr("按 regime", res.attribution(by_regime))
    _print_attr("按滞后桶", res.attribution(by_lag))
    _print_attr("按标的类", res.attribution(by_universe))
    _print_attr("按时段(部分)", _top_hours(res.attribution(by_session_tier)))

    k = int(len(events) * args.oos_split)
    res_in = run_backtest(events[:k], generate_signals)
    res_out = run_backtest(events[k:], generate_signals)
    _print_metrics(f"样本内 (前 {int(args.oos_split*100)}%)", res_in)
    _print_metrics(f"样本外 (后 {100-int(args.oos_split*100)}%)", res_out)

    print(f"\n{'='*64}")
    print("解读:")
    print("  - 若'多方向'显著优于'朴素基线', 说明过滤层 (时段/反追涨/标的) 提供了 edge")
    print("  - 若'样本外'胜率与样本内接近, 说明规则较稳健, 过拟合风险低")
    print("  - 这是合成数据的自检; 真实 edge 必须用 --csv 接入真实合并数据复现")
    print("=" * 64)


if __name__ == "__main__":
    main()
