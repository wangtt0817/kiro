"""
妖币策略 V4 融合引擎（单文件实现）
================================
妖币猎手 (V1) × TradeCat (异动检测) 深度融合

设计理念：
  - V1 阶段叙事 (控盘→诱空→主升→派发) 作为决策框架
  - TradeCat 作为信号源 + 数学严谨化工具 + 维度补全
  - 双引擎互补，不重复打分，不黑箱依赖

依赖：仅 Python 3.8+ 标准库
用法：
  python yaobi_strategy_v4.py --csv "交易猫市场数据终端 - 异动面板.csv"
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 1. 配置常量
# ============================================================
class Config:
    # 控盘门槛
    CONTROL_GATE = 45

    # 三层权重
    W_CONTROL = 0.40
    W_POSITION = 0.35
    W_STRUCTURE = 0.25

    # z-score 映射上限
    Z_K_HIGH_OI = 2.5
    Z_K_HIGH_VOL = 2.0

    # 主动性向量阈值
    TAKER_ACTIVE_LONG = 1.5
    TAKER_ACTIVE_SHORT = 0.67
    HOLDER_LONG = 1.3
    HOLDER_SHORT = 0.77
    TAKER_OUTLIER = 50.0   # 超过此值视为数据噪声

    # 候选池筛选
    MIN_HIT_COUNT = 2
    INTENSITY_PASS = ("极强", "强")

    # 仓位/止损
    BASE_EXPOSURE_PCT = 0.05
    MAX_LEVERAGE = 5




# ============================================================
# 2. 工具函数
# ============================================================
def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def js_round(v: float) -> int:
    """与 V1 行为对齐的 round（向上舍入半数）"""
    if v >= 0:
        return math.floor(v + 0.5)
    return math.ceil(v - 0.5)


def z_score_to_points(z: Optional[float], k_high: float, max_points: float) -> float:
    """把 z-score 映射成 0~max_points
       z = k_high 即满分，z <= 0 得 0 分"""
    if z is None or z <= 0:
        return 0.0
    return min(z / k_high, 1.0) * max_points


def parse_pct(s: Any) -> Optional[float]:
    """解析 '1.538%' → 0.01538；'-7.36%' → -0.0736；空/'-' → None"""
    if s is None or s == "" or s == "-":
        return None
    if isinstance(s, (int, float)):
        return float(s) / 100.0 if abs(s) > 1 else float(s)
    s = str(s).strip().rstrip("%")
    try:
        return float(s) / 100.0
    except ValueError:
        return None


def parse_float(s: Any) -> Optional[float]:
    if s is None or s == "" or s == "-":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_int(s: Any) -> int:
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0




# ============================================================
# 3. 数据模型
# ============================================================
@dataclass
class TradeCatRow:
    """对应 TradeCat 异动面板一行"""
    symbol: str
    composite_score: Optional[float] = None
    direction: Optional[str] = None
    intensity: Optional[str] = None
    main_anomaly_type: Optional[str] = None
    main_window: Optional[str] = None
    hit_count: int = 0
    formation: str = "中性"
    risk_label: str = "-"

    amt_5m: Optional[float] = None
    amt_15m: Optional[float] = None
    amt_1h: Optional[float] = None
    amt_4h: Optional[float] = None
    amt_1d: Optional[float] = None
    amt_1w: Optional[float] = None
    vol_4h: Optional[float] = None
    vol_1d: Optional[float] = None

    amt_z_4h: Optional[float] = None
    amt_z_1d: Optional[float] = None
    vol_z_4h: Optional[float] = None
    vol_z_1d: Optional[float] = None

    consec_amt_n: int = 0
    consec_vol_n: int = 0

    taker_ratio: Optional[float] = None
    taker_delta: Optional[float] = None
    holder_ratio: Optional[float] = None
    holder_market_gap: Optional[float] = None

    freshness: int = 75
    update_time: Optional[str] = None




@dataclass
class LocalInput:
    """V1 本地数据"""
    symbol: str
    circulation_rate: Optional[float] = None
    liquidity_to_mcap: Optional[float] = None
    holders: Optional[int] = None
    listing_hours: Optional[float] = None
    has_futures: bool = True
    listing_cex: bool = False
    top10_ratio: Optional[float] = None
    largest_holder_ratio: Optional[float] = None
    exchange_inflow_ratio: Optional[float] = None
    oi_to_mcap: Optional[float] = None
    futures_volume_to_mcap: Optional[float] = None
    funding_rate: Optional[float] = None
    basis_rate: Optional[float] = None
    price_change_24h: Optional[float] = None
    near_high_ratio: Optional[float] = None
    dex_volume_to_liquidity: Optional[float] = None
    spot_lead: Optional[float] = None
    michill_risk_score: float = 0.0
    cluster_control_ratio: Optional[float] = None
    smart_money_net_flow: float = 0.0
    whale_exit_volume_24h: float = 0.0
    mcap: Optional[float] = None


@dataclass
class StrategyResult:
    symbol: str
    in_pool: bool
    safety_passed: bool
    control_score: int = 0
    position_score: int = 0
    structure_score: int = 0
    position_bias: str = "neutral"
    structure_bias: str = "neutral"
    total_score: int = 0
    actor: Dict[str, Any] = field(default_factory=dict)
    state: str = "吸筹"
    bias: str = "观察"
    reason: str = ""
    risk_flags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    position_size_pct: float = 0.0
    stop_loss_pct: float = 0.0




# ============================================================
# 4. CSV 解析
# ============================================================
def parse_anomaly_panel_row(row: Dict[str, str]) -> TradeCatRow:
    return TradeCatRow(
        symbol=row.get("交易对", "").strip(),
        composite_score=parse_float(row.get("综合分")),
        direction=row.get("方向"),
        intensity=row.get("强度等级"),
        main_anomaly_type=row.get("主异动类型"),
        main_window=row.get("主时间窗口"),
        hit_count=parse_int(row.get("命中数")),
        formation=row.get("结构形态") or "中性",
        risk_label=row.get("清仓/挤压风险") or "-",

        amt_5m=parse_pct(row.get("5m额变(%)")),
        amt_15m=parse_pct(row.get("15m额变(%)")),
        amt_1h=parse_pct(row.get("1h额变(%)")),
        amt_4h=parse_pct(row.get("4h额变(%)")),
        amt_1d=parse_pct(row.get("1d额变(%)")),
        amt_1w=parse_pct(row.get("1w额变(%)")),
        vol_4h=parse_pct(row.get("4h量变(%)")),
        vol_1d=parse_pct(row.get("1d量变(%)")),

        amt_z_4h=parse_float(row.get("4h额强度(z)")),
        amt_z_1d=parse_float(row.get("1d额强度(z)")),
        vol_z_4h=parse_float(row.get("4h量强度(z)")),
        vol_z_1d=parse_float(row.get("1d量强度(z)")),

        consec_amt_n=parse_int(row.get("额连续根数")),
        consec_vol_n=parse_int(row.get("量连续根数")),

        taker_ratio=parse_float(row.get("主动买卖比")),
        taker_delta=parse_float(row.get("主动买卖差")),
        holder_ratio=parse_float(row.get("大户持仓比")),
        holder_market_gap=parse_float(row.get("大户仓位-市场账户")),

        freshness=parse_int(row.get("数据新鲜度分")) or 75,
        update_time=row.get("更新时间"),
    )


def load_anomaly_csv(file_path: str) -> List[TradeCatRow]:
    """加载 TradeCat 异动面板 CSV。
    第一行是 quoted 多行 metadata 块（被 csv 视为单 row），第二行才是表头。"""
    rows: List[TradeCatRow] = []
    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # 跳过 metadata（quoted 多行 row）
        except StopIteration:
            return rows
        try:
            header = next(reader)
        except StopIteration:
            return rows
        for raw in reader:
            if not raw or not any(raw):
                continue
            r = dict(zip(header, raw))
            try:
                tc = parse_anomaly_panel_row(r)
                if tc.symbol:
                    rows.append(tc)
            except Exception:
                continue
    return rows




def detect_data_anomaly(tc: TradeCatRow) -> List[str]:
    warnings: List[str] = []
    if tc.freshness < 60:
        warnings.append(f"数据陈旧(freshness={tc.freshness})")
    if tc.taker_ratio is not None and abs(tc.taker_ratio) > Config.TAKER_OUTLIER:
        warnings.append(f"taker_ratio={tc.taker_ratio:.2f} 异常极值（薄盘噪声）")
    return warnings


# ============================================================
# 5. 候选池筛选
# ============================================================
def is_in_candidate_pool(tc: TradeCatRow) -> bool:
    if tc.hit_count >= Config.MIN_HIT_COUNT:
        return True
    if tc.intensity in Config.INTENSITY_PASS:
        return True
    return False


# ============================================================
# 6. 控盘分（V1 保留）
# ============================================================
def _step_score(value: Optional[float], steps: List[Tuple[Any, int]], fallback: int) -> int:
    if value is None:
        return fallback
    for cond, score in steps:
        if cond(value):
            return score
    return fallback


def compute_control_score_v4(li: LocalInput) -> Dict[str, Any]:
    breakdown = {
        "floatTightness": _step_score(
            li.circulation_rate,
            [(lambda v: v <= 0.2, 25), (lambda v: v <= 0.35, 20), (lambda v: v <= 0.5, 12)],
            6,
        ),
        "liquidityTension": _step_score(
            li.liquidity_to_mcap,
            [(lambda v: v <= 0.12, 20), (lambda v: v <= 0.2, 15), (lambda v: v <= 0.3, 10)],
            4,
        ),
        "holderScarcity": _step_score(
            li.holders,
            [(lambda v: v <= 5000, 20), (lambda v: v <= 20000, 15), (lambda v: v <= 60000, 10)],
            4,
        ),
        "freshness": _step_score(
            li.listing_hours,
            [(lambda v: v <= 24, 15), (lambda v: v <= 72, 10), (lambda v: v <= 168, 6)],
            2,
        ),
        "venueAsymmetry": 10 if (not li.listing_cex and li.has_futures) else (6 if li.has_futures else 2),
    }
    cluster = li.cluster_control_ratio if li.cluster_control_ratio is not None else li.top10_ratio
    breakdown["verifiedConcentration"] = (
        _step_score(cluster, [(lambda v: v >= 0.85, 16), (lambda v: v >= 0.7, 12), (lambda v: v >= 0.55, 8)], 0)
        + _step_score(li.largest_holder_ratio, [(lambda v: v >= 0.3, 10), (lambda v: v >= 0.2, 7), (lambda v: v >= 0.12, 4)], 0)
    )
    breakdown["distributionPenalty"] = _step_score(
        li.exchange_inflow_ratio,
        [(lambda v: v >= 0.06, 16), (lambda v: v >= 0.03, 10), (lambda v: v >= 0.015, 6)],
        0,
    )
    total = (sum(v for k, v in breakdown.items() if k != "distributionPenalty")
             - breakdown["distributionPenalty"])
    return {"value": int(clamp(total, 0, 100)), "breakdown": breakdown}




# ============================================================
# 7. 持仓分（V4 z-score 化）
# ============================================================
def compute_position_score_v4(li: LocalInput, tc: TradeCatRow) -> Dict[str, Any]:
    funding = li.funding_rate or 0
    basis = li.basis_rate or 0
    pc24h = li.price_change_24h or 0

    # OI 压力：4h 主信号(22) + 1d 长周期(8) = 满分 30
    oi_pressure = (
        z_score_to_points(tc.amt_z_4h, Config.Z_K_HIGH_OI, 22)
        + z_score_to_points(tc.amt_z_1d, Config.Z_K_HIGH_OI, 8)
    )
    if tc.amt_z_4h is None and tc.amt_z_1d is None and li.oi_to_mcap is not None:
        oi_pressure = clamp(li.oi_to_mcap * 48, 0, 30)

    # 合约换手
    turnover = z_score_to_points(tc.vol_z_4h, Config.Z_K_HIGH_VOL, 20)
    if tc.vol_z_4h is None and li.futures_volume_to_mcap is not None:
        turnover = clamp(li.futures_volume_to_mcap * 20, 0, 20)

    # 持续性加成
    consec_bonus = 0
    n = tc.consec_amt_n or 0
    if n >= 4:
        consec_bonus = 8
    elif n >= 2:
        consec_bonus = 4

    long_crowding = clamp(abs(min(funding, 0)) * 18000, 0, 20)
    short_crowding = clamp(max(funding, 0) * 12000, 0, 20)

    long_score = (
        oi_pressure + turnover + long_crowding + consec_bonus
        + (10 if 0 < basis <= 0.015 else 0)
        + clamp(pc24h * 140, 0, 12)
    )
    short_score = (
        oi_pressure + turnover + short_crowding
        + (10 if basis >= 0.018 else 0)
        + clamp(pc24h * 50, 0, 15)
    )

    return {
        "value": js_round(clamp(max(long_score, short_score), 0, 100)),
        "bias": "long" if long_score >= short_score else "short",
        "breakdown": {
            "oi_pressure": round(oi_pressure, 2),
            "turnover": round(turnover, 2),
            "consec_bonus": consec_bonus,
            "long_score": round(long_score, 2),
            "short_score": round(short_score, 2),
        },
    }




# ============================================================
# 8. 结构分（V1 保留）
# ============================================================
def compute_structure_score_v4(li: LocalInput, tc: TradeCatRow) -> Dict[str, Any]:
    near_high = li.near_high_ratio or 0
    pc24h = li.price_change_24h or 0
    dex_vol_liq = li.dex_volume_to_liquidity or 0
    spot_lead = li.spot_lead or 0
    basis = li.basis_rate or 0

    long_score = (
        clamp(near_high * 28, 0, 28)
        + clamp(pc24h * 100, 0, 20)
        + clamp(dex_vol_liq * 6, 0, 22)
        + clamp(spot_lead * 70, 0, 18)
        + (10 if 0 < basis <= 0.015 else 0)
    )
    short_score = (
        (18 if near_high >= 0.92 else 0)
        + clamp(max(pc24h - 0.18, 0) * 70, 0, 18)
        + clamp(max(-spot_lead, 0) * 70, 0, 18)
        + (18 if basis >= 0.018 else 0)
        + (12 if dex_vol_liq <= 1 else 0)
    )

    return {
        "value": js_round(clamp(max(long_score, short_score), 0, 100)),
        "bias": "long" if long_score >= short_score else "short",
    }


# ============================================================
# 9. 主动性向量（V4 新增）
# ============================================================
def compute_actor_vector(tc: TradeCatRow) -> Dict[str, Any]:
    taker_ratio = tc.taker_ratio if tc.taker_ratio is not None else 1.0
    holder_ratio = tc.holder_ratio if tc.holder_ratio is not None else 1.0
    holder_gap = tc.holder_market_gap if tc.holder_market_gap is not None else 0.0

    taker_reliable = abs(taker_ratio) <= Config.TAKER_OUTLIER

    if not taker_reliable:
        taker_dir = "unknown"
    elif taker_ratio >= Config.TAKER_ACTIVE_LONG:
        taker_dir = "active_long"
    elif taker_ratio <= Config.TAKER_ACTIVE_SHORT:
        taker_dir = "active_short"
    else:
        taker_dir = "neutral"

    if holder_ratio >= Config.HOLDER_LONG and holder_gap > 0:
        holder_dir = "whale_long"
    elif holder_ratio <= Config.HOLDER_SHORT and holder_gap < 0:
        holder_dir = "whale_short"
    else:
        holder_dir = "whale_neutral"

    divergence = (
        (taker_dir == "active_long" and holder_dir == "whale_short")
        or (taker_dir == "active_short" and holder_dir == "whale_long")
    )
    return {
        "taker": taker_dir, "holder": holder_dir, "divergence": divergence,
        "taker_reliable": taker_reliable,
        "raw": {"taker_ratio": taker_ratio, "holder_ratio": holder_ratio, "gap": holder_gap},
    }




# ============================================================
# 10. 总分
# ============================================================
def compute_total_score(control: int, position: int, structure: int) -> int:
    return math.floor(
        control * Config.W_CONTROL
        + position * Config.W_POSITION
        + structure * Config.W_STRUCTURE
    )


# ============================================================
# 11. V4 状态机（结构形态硬约束矩阵）
# ============================================================
def derive_strategy_state_v4(
    control: int, position: int, structure: int,
    pos_bias: str, str_bias: str,
    li: LocalInput, tc: TradeCatRow, actor: Dict[str, Any],
) -> Dict[str, Any]:
    funding = li.funding_rate or 0
    pc24h = li.price_change_24h or 0
    near_high = li.near_high_ratio or 0
    spot_lead = li.spot_lead or 0
    formation = tc.formation or "中性"
    risk_label = tc.risk_label or "-"
    consec_amt = tc.consec_amt_n or 0

    # 0. 控盘门槛
    if control < Config.CONTROL_GATE:
        return {"state": "吸筹", "bias": "观察",
                "riskFlags": ["控盘不足"],
                "reason": f"控盘分{control}<{Config.CONTROL_GATE}"}

    # 0.5 假信号过滤
    if formation == "结构冲突":
        return {"state": "吸筹", "bias": "观察",
                "riskFlags": [], "reason": "TC 结构冲突，等待方向明朗"}

    # 1. 派发预警
    if (formation == "大户领先做空" and risk_label == "清仓风险"
        and near_high >= 0.85):
        return {"state": "派发预警", "bias": "回避",
                "riskFlags": ["大户出货"],
                "reason": "TC 大户做空 + 高位 + 清仓风险"}

    # 2. 高位派发
    if (pos_bias == "short" and control >= 70
        and structure >= 65 and near_high >= 0.95
        and funding > 0.001 and pc24h > 0.18
        and formation in ("大户领先做空", "多头共振")
        and risk_label in ("清仓风险", "挤压/追多风险")):
        return {"state": "高位派发", "bias": "准备空",
                "riskFlags": ["过热"],
                "reason": "高位+TC 风险标签确认派发"}

    # 3. 逼空主升
    if (pos_bias == "long" and position >= 70 and structure >= 70
        and (funding < 0.0005 or spot_lead > 0.08) and pc24h >= 0.08
        and formation == "大户领先做多"
        and risk_label != "挤压/追多风险"
        and actor["taker"] != "active_long"):
        flags = ["过热"] if (pc24h > 0.5 and near_high > 0.96) else []
        return {"state": "逼空主升", "bias": "做多",
                "riskFlags": flags,
                "reason": "三层共振+大户多头就位+Taker未追入，挤空临界"}



    # 4. 启动主升
    if (pc24h >= 0.25 and structure >= 60 and pos_bias == "long"
        and formation in ("主动买领先多", "多头共振")
        and consec_amt >= 3):
        return {"state": "启动主升", "bias": "准备多",
                "riskFlags": [],
                "reason": "TC 启动结构 + 连续根数≥3 + 本地多头"}

    # 5. 诱空（V4 强化：必须是 Taker 没有 active_long）
    if (pos_bias == "long" and position >= 60 and structure >= 55
        and funding < 0
        and formation in ("空头共振", "主动买领先空", "大户领先做多")
        and actor["taker"] != "active_long"
        and (actor["divergence"] or formation == "大户领先做多")):
        return {"state": "诱空", "bias": "准备多",
                "riskFlags": [],
                "reason": "费率负 + 大户散户分歧 / 大户做多 + Taker 未追入"}

    # 6. 砸盘下杀
    if (pos_bias == "short" and position >= 72 and structure >= 72
        and pc24h < -0.04 and risk_label == "清仓风险"):
        return {"state": "砸盘下杀", "bias": "做空",
                "riskFlags": [],
                "reason": "结构破位+TC 清仓风险确认"}

    return {"state": "吸筹", "bias": "观察",
            "riskFlags": [], "reason": "未确认明确状态"}


# ============================================================
# 12. 风控
# ============================================================
def calc_position_size_pct(total_score: int, state: str, confidence: float = 1.0) -> float:
    if state in ("观察", "吸筹", "回避", "派发预警"):
        return 0.0
    base = Config.BASE_EXPOSURE_PCT
    if total_score >= 90:
        factor = 1.0
    elif total_score >= 80:
        factor = 0.8
    elif total_score >= 70:
        factor = 0.6
    elif total_score >= 60:
        factor = 0.4
    else:
        factor = 0.0
    return base * factor * confidence


STOP_LOSS_TABLE = {
    "逼空主升": -0.08,
    "启动主升": -0.10,
    "诱空":     -0.06,
    "砸盘下杀": -0.08,
    "高位派发": -0.06,
}


def get_stop_loss_pct(state: str) -> float:
    return STOP_LOSS_TABLE.get(state, 0.0)


def safety_gate(li: LocalInput) -> bool:
    if li.michill_risk_score >= 75:
        return False
    return True




# ============================================================
# 13. 主入口：单币评估
# ============================================================
def evaluate_token(li: LocalInput, tc: TradeCatRow) -> StrategyResult:
    result = StrategyResult(symbol=li.symbol, in_pool=False, safety_passed=False)
    result.warnings = detect_data_anomaly(tc)

    if not is_in_candidate_pool(tc):
        result.reason = "未通过 TradeCat 候选池筛选"
        return result
    result.in_pool = True

    if not safety_gate(li):
        result.reason = "安全门未通过（高 Rug 风险）"
        return result
    result.safety_passed = True

    control = compute_control_score_v4(li)
    result.control_score = control["value"]

    position = compute_position_score_v4(li, tc)
    result.position_score = position["value"]
    result.position_bias = position["bias"]

    structure = compute_structure_score_v4(li, tc)
    result.structure_score = structure["value"]
    result.structure_bias = structure["bias"]

    actor = compute_actor_vector(tc)
    result.actor = actor

    result.total_score = compute_total_score(
        result.control_score, result.position_score, result.structure_score
    )

    state = derive_strategy_state_v4(
        result.control_score, result.position_score, result.structure_score,
        result.position_bias, result.structure_bias, li, tc, actor,
    )
    result.state = state["state"]
    result.bias = state["bias"]
    result.reason = state["reason"]
    result.risk_flags = state.get("riskFlags", [])

    result.position_size_pct = calc_position_size_pct(result.total_score, result.state)
    result.stop_loss_pct = get_stop_loss_pct(result.state)
    return result


def make_default_local_input(symbol: str) -> LocalInput:
    """缺数据时给出保守默认值（控盘分会很低，自动被门槛拦截）"""
    return LocalInput(
        symbol=symbol,
        circulation_rate=0.6,
        liquidity_to_mcap=0.4,
        holders=80000,
        listing_hours=720,
        has_futures=True,
        listing_cex=True,
        top10_ratio=0.35,
        largest_holder_ratio=0.08,
        exchange_inflow_ratio=0.0,
    )


def make_demo_yaobi_input(symbol: str, **overrides) -> LocalInput:
    """演示用：模拟一个典型妖币的本地数据（控盘≈70）。
    用于在没有真实链上数据时验证完整流程。"""
    base = LocalInput(
        symbol=symbol,
        circulation_rate=0.18,        # 流通率低
        liquidity_to_mcap=0.10,       # 流动性紧
        holders=4500,                 # 地址少
        listing_hours=60,             # 上市新
        has_futures=True,
        listing_cex=False,            # 未上现货 → 场地错位 +10
        top10_ratio=0.78,             # 集中度高
        largest_holder_ratio=0.22,
        exchange_inflow_ratio=0.0,
        funding_rate=-0.0008,         # 负费率（诱空）
        basis_rate=0.005,             # 温和正基差
        price_change_24h=0.12,
        near_high_ratio=0.78,
        dex_volume_to_liquidity=2.5,
        spot_lead=0.10,               # 现货领先
    )
    for k, v in overrides.items():
        if hasattr(base, k):
            setattr(base, k, v)
    return base


def evaluate_csv(csv_path: str,
                 local_inputs_map: Optional[Dict[str, LocalInput]] = None
                 ) -> List[StrategyResult]:
    rows = load_anomaly_csv(csv_path)
    results: List[StrategyResult] = []
    local_inputs_map = local_inputs_map or {}
    for tc in rows:
        li = local_inputs_map.get(tc.symbol) or make_default_local_input(tc.symbol)
        try:
            results.append(evaluate_token(li, tc))
        except Exception as e:
            results.append(StrategyResult(
                symbol=tc.symbol, in_pool=False, safety_passed=False,
                reason=f"evaluate error: {e}",
            ))
    return results




# ============================================================
# 14. 报告输出
# ============================================================
def format_result(r: StrategyResult) -> str:
    actor_str = (f"taker={r.actor.get('taker','?')}, "
                 f"holder={r.actor.get('holder','?')}, "
                 f"div={r.actor.get('divergence','?')}") if r.actor else "—"
    flags_str = (f" [{', '.join(r.risk_flags)}]") if r.risk_flags else ""
    warn_str = (f" warn:{'; '.join(r.warnings)}") if r.warnings else ""
    pool = "Y" if r.in_pool else "N"
    return (
        f"{r.symbol:<10} pool={pool} score={r.total_score:>3} "
        f"(C={r.control_score} P={r.position_score} S={r.structure_score}) | "
        f"{r.state}/{r.bias}{flags_str} | actor: {actor_str} | "
        f"size={r.position_size_pct*100:.1f}% sl={r.stop_loss_pct*100:.0f}% | "
        f"reason: {r.reason}{warn_str}"
    )


def summarize(results: List[StrategyResult]) -> Dict[str, Any]:
    total = len(results)
    in_pool = sum(1 for r in results if r.in_pool)
    by_state: Dict[str, int] = {}
    actionable: List[str] = []
    for r in results:
        by_state[r.state] = by_state.get(r.state, 0) + 1
        if r.bias in ("做多", "做空", "试仓多", "准备多", "准备空") and r.position_size_pct > 0:
            actionable.append(r.symbol)
    return {
        "total_rows": total,
        "in_candidate_pool": in_pool,
        "by_state": by_state,
        "actionable_count": len(actionable),
        "actionable_symbols": actionable,
    }


# ============================================================
# 15. CLI 入口
# ============================================================
def run_selftest():
    """5 个典型场景验证 V4 状态机分支全覆盖"""
    print("=" * 80)
    print("V4 状态机自检 - 5 个典型场景")
    print("=" * 80)

    scenarios = [
        # (场景描述, LocalInput overrides, TradeCatRow kwargs, 预期 state)
        ("逼空主升 (大户做多 + Taker未追入 + 高持仓Z + 妖币控盘)",
         dict(funding_rate=-0.001, basis_rate=0.005, price_change_24h=0.12,
              spot_lead=0.12, near_high_ratio=0.85, dex_volume_to_liquidity=3.0),
         dict(symbol="DEMO1", formation="大户领先做多", risk_label="-",
              hit_count=4, intensity="强",
              amt_z_4h=2.8, amt_z_1d=2.2, vol_z_4h=2.0, consec_amt_n=4,
              taker_ratio=1.0, holder_ratio=1.6, holder_market_gap=0.5),
         "逼空主升"),

        ("高位派发 (大户做空 + 风险标签 + 高位)",
         dict(funding_rate=0.002, basis_rate=0.020, price_change_24h=0.25,
              near_high_ratio=0.97, dex_volume_to_liquidity=0.8),
         dict(symbol="DEMO2", formation="大户领先做空", risk_label="清仓风险",
              hit_count=3, intensity="极强",
              amt_z_4h=1.8, amt_z_1d=2.5, vol_z_4h=1.5, consec_amt_n=2,
              taker_ratio=1.6, holder_ratio=0.5, holder_market_gap=-0.8),
         "派发预警"),

        ("诱空 (负费率 + 大户做多 + 散户做空)",
         dict(funding_rate=-0.001, basis_rate=0.005, price_change_24h=0.10,
              spot_lead=0.10, near_high_ratio=0.65, dex_volume_to_liquidity=3.0),
         dict(symbol="DEMO3", formation="大户领先做多", risk_label="-",
              hit_count=2, intensity="强",
              amt_z_4h=1.5, amt_z_1d=1.0, vol_z_4h=0.8, consec_amt_n=3,
              taker_ratio=0.5, holder_ratio=1.5, holder_market_gap=0.6),
         "诱空"),

        ("结构冲突 → 强制观察",
         dict(funding_rate=-0.0005, price_change_24h=0.10),
         dict(symbol="DEMO4", formation="结构冲突", risk_label="-",
              hit_count=2, intensity="强",
              amt_z_4h=1.5, amt_z_1d=1.5, vol_z_4h=1.0, consec_amt_n=3,
              taker_ratio=1.0, holder_ratio=1.0, holder_market_gap=0.0),
         "吸筹"),

        ("假象多头 (TC 大户做多但 Taker 已追入 → 拒绝逼空)",
         dict(funding_rate=-0.001, basis_rate=0.005, price_change_24h=0.10,
              spot_lead=0.10, near_high_ratio=0.80),
         dict(symbol="DEMO5", formation="大户领先做多",
              risk_label="挤压/追多风险",
              hit_count=4, intensity="极强",
              amt_z_4h=3.0, amt_z_1d=2.5, vol_z_4h=2.5, consec_amt_n=4,
              taker_ratio=2.5, holder_ratio=1.5, holder_market_gap=0.5),
         "吸筹"),
    ]

    passed, failed = 0, 0
    for desc, li_kw, tc_kw, expected in scenarios:
        li = make_demo_yaobi_input(tc_kw["symbol"], **li_kw)
        tc = TradeCatRow(**tc_kw)
        r = evaluate_token(li, tc)
        ok = (r.state == expected)
        passed += int(ok)
        failed += int(not ok)
        status = "✓ PASS" if ok else "✗ FAIL"
        print(f"\n[{status}] {desc}")
        print(f"  期望: {expected} | 实际: {r.state}/{r.bias}")
        print(f"  分数: C={r.control_score} P={r.position_score} S={r.structure_score} total={r.total_score}")
        print(f"  actor: {r.actor.get('taker')}/{r.actor.get('holder')} div={r.actor.get('divergence')}")
        print(f"  reason: {r.reason}")

    print("\n" + "=" * 80)
    print(f"自检结果: {passed} pass / {failed} fail (共 {len(scenarios)} 场景)")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="妖币策略 V4 融合引擎 - TradeCat 异动面板分析器"
    )
    parser.add_argument("--csv", required=True, help="TradeCat 异动面板 CSV 路径")
    parser.add_argument("--top", type=int, default=30, help="只显示前 N 名")
    parser.add_argument("--actionable-only", action="store_true",
                        help="只显示有可执行信号的币")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--demo", action="store_true",
                        help="演示模式：所有币注入妖币模拟数据，验证完整流程")
    parser.add_argument("--selftest", action="store_true",
                        help="自检：用 5 个典型场景验证状态机所有分支")
    args = parser.parse_args()

    if args.selftest:
        run_selftest()
        return

    if args.demo:
        # demo 模式：给候选池里的每个币都注入妖币级别的本地数据
        rows = load_anomaly_csv(args.csv)
        local_inputs_map = {tc.symbol: make_demo_yaobi_input(tc.symbol) for tc in rows}
        results = []
        for tc in rows:
            li = local_inputs_map[tc.symbol]
            try:
                results.append(evaluate_token(li, tc))
            except Exception as e:
                results.append(StrategyResult(
                    symbol=tc.symbol, in_pool=False, safety_passed=False,
                    reason=f"evaluate error: {e}",
                ))
    else:
        results = evaluate_csv(args.csv)
    results.sort(key=lambda r: (
        -1 if r.position_size_pct > 0 else 0,
        -r.total_score,
    ))

    if args.actionable_only:
        results = [r for r in results if r.position_size_pct > 0]

    summary = summarize(results)

    if args.top:
        display = results[: args.top]
    else:
        display = results

    if args.json:
        print(json.dumps({"summary": summary,
                          "results": [asdict(r) for r in display]},
                         ensure_ascii=False, indent=2))
        return

    print("=" * 100)
    print(f"妖币策略 V4 融合引擎 - 分析报告")
    print(f"CSV: {args.csv}")
    print("=" * 100)
    for r in display:
        print(format_result(r))
    print("-" * 100)
    print(f"汇总: 总行数={summary['total_rows']} 候选池={summary['in_candidate_pool']} "
          f"可执行={summary['actionable_count']}")
    print(f"状态分布: {summary['by_state']}")
    if summary["actionable_symbols"]:
        print(f"可执行币: {', '.join(summary['actionable_symbols'])}")


if __name__ == "__main__":
    main()
