"""signal-strategy — 基于真实数据 (Grafana 盈亏信号 x Gate K线) 的多方向交易策略框架

本包将 analysis_outputs/ 中的真实交叉分析结论，转化为可运行、可回测、
可插入真实数据的策略代码。

六个方向 (Directions):
  A  Anti-Chase Reversion   反追涨均值回归   signals.py
  B  Capitulation Bounce    恐慌反转抄底     signals.py
  C  Regime Classifier      趋势/震荡判别     regime.py
  D  Session Gate           时段执行闸门     session.py
  E  Universe Selection     标的白/黑名单     universe.py
  F  Football Fix           足球策略真实退出  football_fix.py

数据口径: Gate 30m K线 (1 根 = 30 分钟), Grafana 30m 累计盈亏信号。
"""

__version__ = "0.1.0"
