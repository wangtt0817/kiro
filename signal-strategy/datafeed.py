"""signal-strategy — 数据接入层

提供三件事：
  1. EventRow         统一事件行 schema (Grafana 信号 + Gate K线 合并后的一行)
  2. load_events_csv  真实数据加载器 (把你的合并 CSV 读成 EventRow 列表)
  3. SyntheticFeed    标定到真实统计的合成数据生成器

⚠️ 关于合成数据：
   仓库里只有 analysis_outputs/*.json 汇总，没有原始 K线/信号明细，
   因此本框架自带一个"按真实条件结构生成"的合成数据器，用于：
     - 让整套回测/风控/归因代码可以立即跑起来
     - 验证策略逻辑能否"还原"出真实分析里记录的 edge
   合成器把【未来收益】显式地条件化在 (滞后分桶, UTC时段, stress, 标的, 急跌反转)
   上，分布参数来自 deep_cross_analysis.json / 盈利数据.txt。
   它【不是】真实行情，上线前必须用 load_events_csv 接入真实合并数据再验证。
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from config import (
    SESSION_MULT, WHITELIST, BLACKLIST,
    CAPITULATION_PAST_DROP, CAPITULATION_MIN_SCORE,
)


# ============================================================
# 统一事件行 schema
# ============================================================
@dataclass
class EventRow:
    """一条已合并的 Grafana×Gate 事件 (对应 30m K线的某一根)。

    字段含义与 analysis_outputs 完全对齐：
      ts            UTC 时间戳
      contract      合约名, 如 'lab_usdt'
      gf_score_30m  Grafana 30m 累计盈亏 (U), >0 盈利信号
      gf_delta_5m   分数 5 分钟变化
      gf_delta_15m  分数 15 分钟变化
      price         Gate 当根收盘价
      past_ret_3h   过去 3 小时价格涨跌幅 (%)  -> 滞后分桶用
      ret_5m/15m/30m  Gate 近 5/15/30 分钟收益 (%)
      amp_15m       15 分钟振幅 (%)
      vol_ratio_5v30  近5min/近30min 成交量比
      fwd_ret_1/3/6   未来 1/3/6 根 K线收益 (%)  ← 仅回测可见, 实盘为 None
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
    # 回测专用前视字段 (实盘为 None)
    fwd_ret_1: Optional[float] = None
    fwd_ret_3: Optional[float] = None
    fwd_ret_6: Optional[float] = None

    @property
    def hour(self) -> int:
        return self.ts.hour

    def fwd_ret(self, bars: int) -> Optional[float]:
        return {1: self.fwd_ret_1, 3: self.fwd_ret_3, 6: self.fwd_ret_6}.get(bars)


# ============================================================
# 真实数据加载器
# ============================================================
def load_events_csv(path: str) -> List[EventRow]:
    """从真实合并 CSV 读取事件。

    期望表头 (缺失列将置 0 / None)：
      ts,contract,gf_score_30m,gf_delta_5m,gf_delta_15m,price,
      past_ret_3h,ret_5m,ret_15m,ret_30m,amp_15m,vol_ratio_5v30,
      fwd_ret_1,fwd_ret_3,fwd_ret_6

    ts 支持 ISO8601 (含 'Z')。回测时请保证 fwd_ret_* 已预计算；
    实盘流式接入时 fwd_ret_* 留空即可。
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


# ============================================================
# 标定到真实统计的合成数据生成器
# ============================================================
# 标的池: 白/黑名单 + 一批中性, 采样权重模拟事件数量
_NEUTRAL_CONTRACTS = [
    "ton_usdt", "sui_usdt", "play_usdt", "in_usdt", "naoris_usdt",
    "ordi_usdt", "wlfi_usdt", "dash_usdt", "fida_usdt", "q_usdt",
]


@dataclass
class SyntheticFeed:
    """生成标定到真实条件结构的事件流。

    标定目标 (来自真实分析):
      - 滞后分桶占比:  <1% 53.9%, 1-3% 10.2%, 3-5% 7.9%, 5-10% 13.2%, >10% 14.8%
      - 各桶未来收益符号/幅度 (STEP 7)
      - 时段胜率随 SESSION_MULT 同向 (STEP 9)
      - 超大盈利(>1000U)且急跌 -> 未来反弹 (STEP 6)
      - stress(高振幅/放量/急跌) -> 未来更差 (state_summary)
      - 白名单偏正 / 黑名单偏负 (STEP 8)
    """
    n_events: int = 20_000
    seed: int = 7
    start: datetime = field(
        default_factory=lambda: datetime(2026, 4, 27, tzinfo=timezone.utc))

    def generate(self) -> List[EventRow]:
        rng = random.Random(self.seed)
        rows: List[EventRow] = []
        t = self.start

        # 标的采样权重: 白名单/中性/黑名单
        contracts = (list(WHITELIST) + _NEUTRAL_CONTRACTS + list(BLACKLIST))
        weights = ([3.0] * len(WHITELIST)
                   + [2.0] * len(_NEUTRAL_CONTRACTS)
                   + [2.5] * len(BLACKLIST))

        for _ in range(self.n_events):
            # 时间推进 (平均 ~2 分钟一个事件, 覆盖全天各时段)
            t = t + timedelta(minutes=rng.randint(1, 4))
            hour = t.hour
            contract = rng.choices(contracts, weights=weights, k=1)[0]

            # ---- 1) 采样滞后分桶 (past_ret_3h) ----
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

            # ---- 2) Grafana 分数 (盈利信号规模) ----
            gf_score_30m = max(0.0, rng.lognormvariate(4.2, 1.0))  # 中位~67U
            # 偶发"超大盈利+急跌"的 capitulation 场景
            is_capit = rng.random() < 0.012
            if is_capit:
                past_ret_3h = rng.uniform(-18.0, CAPITULATION_PAST_DROP)
                gf_score_30m = rng.uniform(CAPITULATION_MIN_SCORE, 3000.0)
            gf_delta_5m = rng.gauss(gf_score_30m * 0.1, 30)
            gf_delta_15m = rng.gauss(gf_score_30m * 0.2, 50)

            # ---- 3) 短周期收益 / 振幅 / 量比 ----
            ret_5m = rng.gauss(0.0, 1.2)
            ret_15m = rng.gauss(0.0, 1.8)
            ret_30m = rng.gauss(past_ret_3h * 0.15, 2.0)
            amp_15m = abs(rng.gauss(2.6, 1.6)) + abs(ret_15m) * 0.3
            vol_ratio_5v30 = max(0.1, rng.lognormvariate(-0.05, 0.45))

            # stress 注入: 一部分事件处于承压态
            stress = rng.random() < 0.18
            if stress:
                amp_15m = rng.uniform(6.0, 20.0)
                vol_ratio_5v30 = rng.uniform(2.5, 6.0)
                ret_5m = rng.uniform(-6.0, -1.0)

            # ---- 4) 生成未来收益 (核心: 条件化结构) ----
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

    # --- 未来收益均值: 把真实条件结构编码进来 ---
    def _forward_mu(self, bucket, hour, contract, stress, is_capit, ret_5m) -> float:
        """返回未来 1 根收益的"漂移"中枢 (%)。

        说明: 这里把策略要捕捉的 edge 显式写入数据生成。
        若策略逻辑正确, 回测应能在扣费后还原出正向收益; 若策略写错(如追涨),
        则会亏损。这正是用合成数据自检逻辑的目的。
        """
        # 滞后分桶基准漂移 (与 STEP 7 同号)
        base = {
            "none": -0.03,   # 中性偏弱
            "small": 0.03,
            "mid": 0.08,     # 跟随甜区: 正
            "big": -0.22,    # fade 区: 负 (做空才赚)
            "huge": -0.63,   # 强 fade 区: 更负
        }[bucket]

        # capitulation 反转: 急跌后的大额盈利 -> 未来正
        if is_capit and ret_5m >= -0.5:
            base = +1.0

        # 时段效应: 用 SESSION_MULT 映射到 [-0.15, +0.15] 的漂移叠加
        sess = SESSION_MULT.get(hour, 0.5)
        base += (sess - 0.6) * 0.25

        # stress 惩罚: 承压态未来更差
        if stress:
            base -= 0.35

        # 标的结构性偏向
        if contract in WHITELIST:
            base += 0.06
        elif contract in BLACKLIST:
            base -= 0.20

        return base


def make_dataset(n_events: int = 20_000, seed: int = 7) -> List[EventRow]:
    """便捷入口: 生成一份合成数据集。"""
    return SyntheticFeed(n_events=n_events, seed=seed).generate()
