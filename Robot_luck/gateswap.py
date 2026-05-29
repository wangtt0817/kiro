import random
import threading
import json
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import schedule
import bgExchang
import cmdRobot
import grafanaRobot
import strategy_config
import utils
import xbtRobot
from main import taskUsername, taskPassword, DiscordWebhook_url, FeiShuWebhook_url, xbtUsername, xbtPassword, \
    grafanaUsername, grafanaPassword

# 基础配置
RunningExchange = 'gate_usdt_swap'  # 当前交易所机器人数量约 150

# 榜单配置
exchangeTopNum = 0  # Exchange 榜单前 N
grafanaTopNum = 3  # Grafana 榜单前 N
grafanahour = "15m"  # Grafana 榜单周期
XBTTopNum = 0  # XBT 榜单前 N
# XBT 和旧 Exchange 涨跌榜当前关闭，Gate 独立流动性选币由主循环触发
BLACKLIST_TOKENS_PATH = Path(__file__).with_name("blacklist_tokens.txt")
STANDBY_RECORDS_PATH = Path(__file__).with_name("standby_records.json")
GATE_SELECTION_MIN_24H_USDT = strategy_config.GATE_SELECTION_MIN_24H_USDT
GATE_SELECTION_MIN_24H_CHANGE = strategy_config.GATE_SELECTION_MIN_24H_CHANGE
GATE_HOT_SELECTION_MIN_24H_USDT = strategy_config.GATE_HOT_SELECTION_MIN_24H_USDT
GATE_HOT_SELECTION_MIN_1M_USDT = strategy_config.GATE_HOT_SELECTION_MIN_1M_USDT
GATE_HOT_SELECTION_REQUIRED_1M_COUNT = strategy_config.GATE_HOT_SELECTION_REQUIRED_1M_COUNT
GATE_SELECTION_MIN_1M_USDT = strategy_config.GATE_SELECTION_MIN_1M_USDT
GATE_SELECTION_REQUIRED_1M_COUNT = strategy_config.GATE_SELECTION_REQUIRED_1M_COUNT
GATE_SELECTION_KLINE_LIMIT = strategy_config.GATE_SELECTION_KLINE_LIMIT

# 策略阈值统一维护在 strategy_config.py。

# 等待加仓占位币
markSymbol = "1_usdt"
# 非策略占位币
XXXSymbol = "2_usdt"
# 待加仓币种列表
robot_add_List = []
# 用 Counter 统计待加仓数量
symbol_counts = Counter(robot_add_List)

stop_loss_List = {}
top_symbol_List = {}
# 机器人 ID 对照缓存
id_data_dict = {}

# 线程锁
lock = threading.Lock()

# 消息聚合发送
accumulated_messages = ""
last_send_time = time.time()
logger = utils.CustomLogger()

# 后台线程注册表
socket_thread_dict = {}

# 运行态缓存
temp_taskOptions_list = {}
MANUAL_START_COOLDOWN_MINUTES = strategy_config.MANUAL_START_COOLDOWN_MINUTES
AUTO_START_DETECTION_WINDOW_SECONDS = strategy_config.AUTO_START_DETECTION_WINDOW_SECONDS
LIMIT_SILENCE_WAIT_MINUTES = strategy_config.LIMIT_SILENCE_WAIT_MINUTES
LIMIT_SILENCE_MSG = "限制实在是太狠了(0/s)，没法跑了，静默半小时不开机，下次需提高成交率"
GRAFANA_RESTART_HOUR = strategy_config.GRAFANA_RESTART_HOUR
GRAFANA_RESTART_CACHE_SECONDS = strategy_config.GRAFANA_RESTART_CACHE_SECONDS
GRAFANA_MARKET_HOUR = strategy_config.GRAFANA_MARKET_HOUR
GRAFANA_MARKET_CACHE_SECONDS = strategy_config.GRAFANA_MARKET_CACHE_SECONDS
GRAFANA_MIN_30M_PROFIT = strategy_config.GRAFANA_MIN_30M_PROFIT
SYMBOL_SAMPLE_INTERVAL_SECONDS = strategy_config.SYMBOL_SAMPLE_INTERVAL_SECONDS
SYMBOL_DECISION_INTERVAL_SECONDS = strategy_config.SYMBOL_DECISION_INTERVAL_SECONDS
SYMBOL_HISTORY_KEEP_SECONDS = strategy_config.SYMBOL_HISTORY_KEEP_SECONDS
SYMBOL_HISTORY_READY_SECONDS = strategy_config.SYMBOL_HISTORY_READY_SECONDS
SYMBOL_WARMUP_READY_SECONDS = strategy_config.SYMBOL_WARMUP_READY_SECONDS
SYMBOL_HISTORY_MERGE_SECONDS = strategy_config.SYMBOL_HISTORY_MERGE_SECONDS
SYMBOL_SLOPE_SHORT_SECONDS = strategy_config.SYMBOL_SLOPE_SHORT_SECONDS
SYMBOL_SLOPE_LONG_SECONDS = strategy_config.SYMBOL_SLOPE_LONG_SECONDS
GRAFANA_TREND_1M_SECONDS = strategy_config.GRAFANA_TREND_1M_SECONDS
GRAFANA_TREND_3M_SECONDS = strategy_config.GRAFANA_TREND_3M_SECONDS
GRAFANA_TREND_5M_SECONDS = strategy_config.GRAFANA_TREND_5M_SECONDS
GRAFANA_TREND_15M_SECONDS = strategy_config.GRAFANA_TREND_15M_SECONDS
GRAFANA_TREND_30M_SECONDS = strategy_config.GRAFANA_TREND_30M_SECONDS
GRAFANA_TREND_60M_SECONDS = strategy_config.GRAFANA_TREND_60M_SECONDS
GRAFANA_EMERGENCY_D1M = strategy_config.GRAFANA_EMERGENCY_D1M
GRAFANA_FAST_ADD_D1M = strategy_config.GRAFANA_FAST_ADD_D1M
GRAFANA_EMERGENCY_D3M = strategy_config.GRAFANA_EMERGENCY_D3M
GRAFANA_EMERGENCY_WEAK_D1M = strategy_config.GRAFANA_EMERGENCY_WEAK_D1M
GRAFANA_EMERGENCY_ACCEL = strategy_config.GRAFANA_EMERGENCY_ACCEL
GRAFANA_BAN_SCORE = strategy_config.GRAFANA_BAN_SCORE
GRAFANA_REDUCE_D3M = strategy_config.GRAFANA_REDUCE_D3M
GRAFANA_REDUCE_ACCEL = strategy_config.GRAFANA_REDUCE_ACCEL
GRAFANA_TOP_ACCEL = strategy_config.GRAFANA_TOP_ACCEL
GRAFANA_OPEN_D3M = strategy_config.GRAFANA_OPEN_D3M
GRAFANA_FAKE_RECOVERY_D3M = strategy_config.GRAFANA_FAKE_RECOVERY_D3M
GRAFANA_FAKE_RECOVERY_ACCEL = strategy_config.GRAFANA_FAKE_RECOVERY_ACCEL
GRAFANA_FULL_ADD_D5M = strategy_config.GRAFANA_FULL_ADD_D5M
GRAFANA_FULL_ADD_D3M = strategy_config.GRAFANA_FULL_ADD_D3M
GRAFANA_FULL_ADD_ACCEL = strategy_config.GRAFANA_FULL_ADD_ACCEL
PRICE_STOP_THRESHOLD_PERCENT = strategy_config.PRICE_STOP_THRESHOLD_PERCENT
PRICE_STOP_SUPPLEMENT_CHECK_SECONDS = strategy_config.PRICE_STOP_SUPPLEMENT_CHECK_SECONDS
MAX_SYMBOL_ACTIVE_COUNT = strategy_config.MAX_SYMBOL_ACTIVE_COUNT
SYMBOL_OPEN_COOLDOWN_SECONDS = strategy_config.SYMBOL_OPEN_COOLDOWN_SECONDS
SYMBOL_ADD_COOLDOWN_SECONDS = strategy_config.SYMBOL_ADD_COOLDOWN_SECONDS
SYMBOL_STOP_COOLDOWN_SECONDS = strategy_config.SYMBOL_STOP_COOLDOWN_SECONDS
STANDBY_MAX_SECONDS = strategy_config.STANDBY_MAX_SECONDS
STANDBY_RESTART_PROTECT_SECONDS = strategy_config.STANDBY_RESTART_PROTECT_SECONDS
STANDBY_RUNNING_GRACE_SECONDS = strategy_config.STANDBY_RUNNING_GRACE_SECONDS
PROFIT_PROTECT_THRESHOLD = strategy_config.PROFIT_PROTECT_THRESHOLD
ALLOCATION_MAX_DRAWDOWN_15M = strategy_config.ALLOCATION_MAX_DRAWDOWN_15M
STOP_DRAWDOWN_15M = strategy_config.STOP_DRAWDOWN_15M
STOP_LOSS_DOWNGRADE_COUNT = strategy_config.STOP_LOSS_DOWNGRADE_COUNT
STOP_LOSS_DOWNGRADE_WINDOW_MINUTES = strategy_config.STOP_LOSS_DOWNGRADE_WINDOW_MINUTES
MANUAL_STOP_RECYCLE_MINUTES = strategy_config.MANUAL_STOP_RECYCLE_MINUTES
FAILED_STOP_LOSS_RECYCLE_PROFIT_PERCENT = strategy_config.FAILED_STOP_LOSS_RECYCLE_PROFIT_PERCENT
RUNTIME_RECYCLE_RULES = strategy_config.RUNTIME_RECYCLE_RULES
ALLOCATION_START_DELAY_SECONDS = strategy_config.ALLOCATION_START_DELAY_SECONDS
TOP_TEST_START_DELAY_SECONDS = strategy_config.TOP_TEST_START_DELAY_SECONDS
ELIMINATE_STRATEGY_INTERVAL_SECONDS = strategy_config.ELIMINATE_STRATEGY_INTERVAL_SECONDS
ELIMINATE_STRATEGY_COUNT = strategy_config.ELIMINATE_STRATEGY_COUNT
LOOP_SLEEP_SECONDS = strategy_config.LOOP_SLEEP_SECONDS
COPY_BIAS_WINDOW = strategy_config.COPY_BIAS_WINDOW
COPY_OPEN_MIN = strategy_config.COPY_OPEN_MIN
COPY_OPEN_MAX = strategy_config.COPY_OPEN_MAX
COPY_CLOSE_MIN = strategy_config.COPY_CLOSE_MIN
COPY_CLOSE_MAX = strategy_config.COPY_CLOSE_MAX
COPY_DEFAULT_LEVER = strategy_config.COPY_DEFAULT_LEVER
COPY_DEFAULT_STOP_LOSS = strategy_config.COPY_DEFAULT_STOP_LOSS
COPY_LEVER_BALANCE_TARGET = strategy_config.COPY_LEVER_BALANCE_TARGET
COPY_MIN_LEVER = strategy_config.COPY_MIN_LEVER
COPY_MAX_LEVER = strategy_config.COPY_MAX_LEVER
COPY_STOP_LOSS_BASE = strategy_config.COPY_STOP_LOSS_BASE
manual_start_cooldown_until = {}
auto_start_records = {}
grafana_restart_scores = {}
grafana_restart_scores_at = 0
grafana_market_scores = {}
grafana_market_scores_at = 0
symbol_history = {}
grafana_symbol_history = {}
symbol_health = {}
symbol_action_records = {}
symbol_standby_records = {}
symbol_restart_protect_until = {}
log_throttle_records = {}


def log_message(msg, write_log=True, console=True):
    if console:
        print(msg)
    if write_log:
        logger.log_info(msg)


def log_throttled(key, msg, interval_seconds=300, write_log=True, console=True):
    now = time.time()
    if now - log_throttle_records.get(key, 0) < interval_seconds:
        return False
    log_throttle_records[key] = now
    log_message(msg, write_log=write_log, console=console)
    return True


def standby_robot_key(robot_or_id):
    if isinstance(robot_or_id, dict):
        robot_or_id = robot_or_id.get('id')
    return str(robot_or_id)


def load_standby_records():
    if not STANDBY_RECORDS_PATH.exists():
        return {}
    try:
        data = json.loads(STANDBY_RECORDS_PATH.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def save_standby_records():
    try:
        STANDBY_RECORDS_PATH.write_text(
            json.dumps(symbol_standby_records, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except OSError as error:
        msg = utils.getCurrenTime(RunningExchange) + f"保存停机观察状态失败: {error}"
        print(msg)
        logger.log_info(msg)


def get_robot_name(robot):
    if not isinstance(robot, dict):
        return ""
    account_nick_names = robot.get('account_nick_names')
    if isinstance(account_nick_names, list) and account_nick_names:
        return str(account_nick_names[0])
    if account_nick_names:
        return str(account_nick_names)
    account_nick_name = robot.get('account_nick_name')
    if account_nick_name:
        return str(account_nick_name)
    nick_name = robot.get('nick_name')
    if nick_name:
        return str(nick_name)
    return ""


def normalize_robot(robot):
    if isinstance(robot, dict) and not robot.get('account_nick_name'):
        robot['account_nick_name'] = get_robot_name(robot)
    return robot


def normalize_robot_list(robot_list):
    if robot_list is None:
        return None
    return [normalize_robot(robot) for robot in robot_list]


def normalize_blacklist_symbol(symbol):
    normalized = str(symbol or "").strip().lower()
    if normalized.endswith("usdt") and "_" not in normalized:
        normalized = normalized[:-4] + "_usdt"
    return normalized


def load_blacklist_tokens():
    if not BLACKLIST_TOKENS_PATH.exists():
        return set()
    tokens = set()
    for line in BLACKLIST_TOKENS_PATH.read_text(encoding='utf-8-sig').splitlines():
        token = line.strip()
        if not token or token.startswith("#"):
            continue
        tokens.add(normalize_blacklist_symbol(token))
    return tokens


def safe_get_robot_parameter():
    return normalize_robot_list(cmdRobot.getRobotParameter(taskUsername))


def fetch_robot_parameter_with_relogin():
    taskOptions_list = safe_get_robot_parameter()
    if taskOptions_list is None:
        cmdRobot.login(taskUsername, taskPassword)
        taskOptions_list = safe_get_robot_parameter()
    return taskOptions_list


def record_auto_start(robot, reason):
    if not isinstance(robot, dict) or robot.get('id') is None:
        return
    auto_start_records[robot['id']] = {
        'at': datetime.now(),
        'reason': reason,
    }


def start_robot(robot, reason):
    normalize_robot(robot)
    record_auto_start(robot, reason)
    cmdRobot.start(robot)


def schedule_robot_start(delay_seconds, robot, reason):
    normalize_robot(robot)
    record_auto_start(robot, reason)
    timer = threading.Timer(delay_seconds, cmdRobot.start, (robot,))
    timer.daemon = True
    timer.start()


def is_manual_start_cooling(robot_id):
    cooldown_until = manual_start_cooldown_until.get(robot_id)
    if cooldown_until is None:
        return False
    if cooldown_until <= datetime.now():
        del manual_start_cooldown_until[robot_id]
        return False
    return True


def get_grafana_restart_scores():
    global grafana_restart_scores, grafana_restart_scores_at
    now = time.time()
    if now - grafana_restart_scores_at >= GRAFANA_RESTART_CACHE_SECONDS:
        grafanaRobot.GrafanaLogin(grafanaUsername, grafanaPassword)
        scores = grafanaRobot.getGrafanaScores("swap", GRAFANA_RESTART_HOUR)
        grafana_restart_scores = scores or {}
        grafana_restart_scores_at = now
    return grafana_restart_scores


def get_grafana_market_scores():
    global grafana_market_scores, grafana_market_scores_at
    now = time.time()
    if now - grafana_market_scores_at >= GRAFANA_MARKET_CACHE_SECONDS:
        grafanaRobot.GrafanaLogin(grafanaUsername, grafanaPassword)
        scores = grafanaRobot.getGrafanaScores("swap", GRAFANA_MARKET_HOUR)
        grafana_market_scores = scores or {}
        grafana_market_scores_at = now
    return grafana_market_scores


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def calc_drawdown_percent(peak_value, current_value):
    peak_value = _safe_float(peak_value)
    current_value = _safe_float(current_value)
    drawdown_value = peak_value - current_value
    if drawdown_value <= 0:
        return 0.0
    base_value = abs(peak_value) if abs(peak_value) > 0 else abs(current_value)
    if base_value <= 0:
        return 0.0
    return drawdown_value / base_value * 100


def calc_change_percent(baseline_value, current_value):
    baseline_value = _safe_float(baseline_value)
    current_value = _safe_float(current_value)
    change_value = current_value - baseline_value
    base_value = abs(baseline_value) if abs(baseline_value) > 0 else abs(current_value)
    if base_value <= 0:
        return 0.0
    return change_value / base_value * 100


def update_symbol_history(symbol, current_data, now_ts=None):
    now_ts = int(time.time() if now_ts is None else now_ts)
    profit_rate = _safe_float(current_data.get('profit_rate'))
    row = {
        'ts': now_ts,
        'balance': _safe_float(current_data.get('total_now_balance')),
        'profit_rate': profit_rate,
        'active_count': int(current_data.get('number') or 0),
    }
    rows = symbol_history.setdefault(symbol, [])
    if rows and now_ts - rows[-1]['ts'] < SYMBOL_HISTORY_MERGE_SECONDS:
        rows[-1] = row
    else:
        rows.append(row)
    cutoff = now_ts - SYMBOL_HISTORY_KEEP_SECONDS
    symbol_history[symbol] = [item for item in rows if item['ts'] >= cutoff]


def update_grafana_symbol_history(symbol, grafana_profit, now_ts=None):
    now_ts = int(time.time() if now_ts is None else now_ts)
    row = {
        'ts': now_ts,
        'profit_rate': _safe_float(grafana_profit),
    }
    rows = grafana_symbol_history.setdefault(symbol, [])
    if rows and now_ts - rows[-1]['ts'] < SYMBOL_HISTORY_MERGE_SECONDS:
        rows[-1] = row
    else:
        rows.append(row)
    cutoff = now_ts - SYMBOL_HISTORY_KEEP_SECONDS
    grafana_symbol_history[symbol] = [item for item in rows if item['ts'] >= cutoff]


def _baseline_row(rows, now_ts, window_seconds):
    cutoff = now_ts - window_seconds
    baseline = None
    for row in rows:
        if row['ts'] <= cutoff:
            baseline = row
        else:
            break
    return baseline


def calc_delta_value(baseline_row, current_value):
    if baseline_row is None:
        return 0.0
    return _safe_float(current_value) - _safe_float(baseline_row.get('profit_rate'))


def classify_trend_state(d5m, d30m):
    if d30m > 0 and d5m > 0:
        return 'STRONG_TREND'
    if d30m < 0 and d5m > 0:
        return 'FAKE_RECOVERY'
    if d30m > 0 and d5m < 0:
        return 'TOP_RISK'
    if d30m < 0 and d5m < 0:
        return 'DOWN_TREND'
    return 'MIXED'


def get_hold_level(active_count):
    if active_count <= 0:
        return 'NONE'
    if active_count == 1:
        return 'LIGHT'
    if active_count >= MAX_SYMBOL_ACTIVE_COUNT:
        return 'OVERWEIGHT'
    return 'FULL'


def decide_grafana_action(metrics, hold_level='NONE', gate_risk='NONE'):
    if not metrics.get('data_fresh'):
        return 'WAIT_DATA'
    if gate_risk == 'CRASH':
        return 'EMERGENCY_STOP'

    score_30m = metrics.get('current_return', 0.0)
    d1m = metrics.get('d1m', 0.0)
    d3m = metrics.get('d3m', 0.0)
    d5m = metrics.get('d5m', 0.0)
    d15m = metrics.get('d15m', 0.0)
    d30m = metrics.get('d30m', 0.0)
    d60m = metrics.get('d60m', 0.0)
    accel = metrics.get('accel', 0.0)
    drawdown_15m = metrics.get('drawdown_15m', 0.0)
    trend_state = metrics.get('trend_state', 'NO_DATA')

    if (
        d1m <= GRAFANA_EMERGENCY_D1M
        or d3m <= GRAFANA_EMERGENCY_D3M
        or (d1m <= GRAFANA_EMERGENCY_WEAK_D1M and accel <= GRAFANA_EMERGENCY_ACCEL)
    ):
        return 'EMERGENCY_STOP'
    if score_30m <= GRAFANA_BAN_SCORE and d15m < 0:
        return 'BAN_24H'
    if (
        d1m >= GRAFANA_FAST_ADD_D1M
        and d60m > 0
        and trend_state == 'STRONG_TREND'
        and hold_level not in ('OVERWEIGHT',)
        and gate_risk != 'RISK'
    ):
        if hold_level == 'NONE':
            return 'OPEN_1'
        return 'FAST_ADD_2'

    if trend_state == 'DOWN_TREND':
        return 'STOP_OBSERVE' if hold_level != 'NONE' else 'SKIP'
    if trend_state == 'TOP_RISK':
        return 'REDUCE_1' if hold_level in ('FULL', 'OVERWEIGHT') else 'HOLD'

    if trend_state == 'FAKE_RECOVERY':
        if (
            hold_level == 'NONE'
            and metrics.get('trend_ready')
            and d3m > GRAFANA_FAKE_RECOVERY_D3M
            and accel > GRAFANA_FAKE_RECOVERY_ACCEL
            and drawdown_15m < ALLOCATION_MAX_DRAWDOWN_15M
            and gate_risk != 'RISK'
        ):
            return 'OPEN_1_PROBE'
        return 'HOLD'

    if d5m < 0 and accel < 0 and drawdown_15m >= STOP_DRAWDOWN_15M:
        return 'STOP_OBSERVE' if hold_level != 'NONE' else 'SKIP'
    if d3m <= GRAFANA_REDUCE_D3M and accel <= GRAFANA_REDUCE_ACCEL:
        return 'REDUCE_1' if hold_level != 'NONE' else 'SKIP'
    if d5m > 0 and accel <= GRAFANA_TOP_ACCEL:
        return 'REDUCE_1' if hold_level in ('FULL', 'OVERWEIGHT') else 'HOLD'

    if trend_state == 'STRONG_TREND':
        if hold_level == 'NONE':
            if (
                d15m > 0
                and d30m > 0
                and d3m > GRAFANA_OPEN_D3M
                and accel >= 0
                and gate_risk != 'RISK'
            ):
                return 'OPEN_1'
            return 'SKIP'
        if hold_level == 'LIGHT':
            if (
                d3m > 0
                and accel > 0
                and drawdown_15m < ALLOCATION_MAX_DRAWDOWN_15M
                and gate_risk != 'RISK'
            ):
                return 'ADD_1'
            return 'HOLD'
        if hold_level == 'FULL':
            if (
                d5m >= GRAFANA_FULL_ADD_D5M
                and d3m > GRAFANA_FULL_ADD_D3M
                and accel > GRAFANA_FULL_ADD_ACCEL
                and drawdown_15m < ALLOCATION_MAX_DRAWDOWN_15M
                and gate_risk != 'RISK'
            ):
                return 'ADD_1'
            return 'HOLD'
    return 'HOLD'


def default_grafana_metrics():
    return {
        'data_ready': False,
        'warmup_ready': False,
        'trend_ready': False,
        'data_fresh': False,
        'slope_5m': 0.0,
        'slope_15m': 0.0,
        'drawdown_15m': 0.0,
        'peak_15m': 0.0,
        'current_return': 0.0,
        'd1m': 0.0,
        'd3m': 0.0,
        'd5m': 0.0,
        'd15m': 0.0,
        'd30m': 0.0,
        'd60m': 0.0,
        'accel': 0.0,
        'prev_d5m': 0.0,
        'trend_state': 'NO_DATA',
        'source': 'grafana',
    }


def compute_symbol_metrics(symbol, now_ts=None):
    rows = sorted(symbol_history.get(symbol, []), key=lambda item: item['ts'])
    if not rows:
        return {
            'data_ready': False,
            'warmup_ready': False,
            'data_fresh': False,
            'slope_5m': 0.0,
            'slope_15m': 0.0,
            'drawdown_15m': 0.0,
            'peak_15m': 0.0,
            'current_return': 0.0,
        }

    latest = rows[-1]
    now_ts = int(latest['ts'] if now_ts is None else now_ts)
    baseline_5m = _baseline_row(rows, now_ts, SYMBOL_SLOPE_SHORT_SECONDS)
    baseline_15m = _baseline_row(rows, now_ts, SYMBOL_SLOPE_LONG_SECONDS)
    window_15m = [row for row in rows if row['ts'] >= now_ts - SYMBOL_SLOPE_LONG_SECONDS]
    peak_15m = max((_safe_float(row.get('profit_rate')) for row in window_15m), default=_safe_float(latest.get('profit_rate')))
    current_return = _safe_float(latest.get('profit_rate'))
    return {
        'data_ready': baseline_15m is not None and latest['ts'] - rows[0]['ts'] >= SYMBOL_HISTORY_READY_SECONDS,
        'data_fresh': now_ts - latest['ts'] <= SYMBOL_SAMPLE_INTERVAL_SECONDS * 2,
        'slope_5m': calc_change_percent(baseline_5m.get('profit_rate'), current_return) if baseline_5m else 0.0,
        'slope_15m': calc_change_percent(baseline_15m.get('profit_rate'), current_return) if baseline_15m else 0.0,
        'drawdown_15m': calc_drawdown_percent(peak_15m, current_return),
        'peak_15m': peak_15m,
        'current_return': current_return,
    }


def compute_grafana_symbol_metrics(symbol, now_ts=None):
    rows = sorted(grafana_symbol_history.get(symbol, []), key=lambda item: item['ts'])
    if not rows:
        return default_grafana_metrics()

    latest = rows[-1]
    now_ts = int(latest['ts'] if now_ts is None else now_ts)
    baseline_1m = _baseline_row(rows, now_ts, GRAFANA_TREND_1M_SECONDS)
    baseline_3m = _baseline_row(rows, now_ts, GRAFANA_TREND_3M_SECONDS)
    baseline_5m = _baseline_row(rows, now_ts, SYMBOL_SLOPE_SHORT_SECONDS)
    baseline_10m = _baseline_row(rows, now_ts, GRAFANA_TREND_5M_SECONDS * 2)
    baseline_15m = _baseline_row(rows, now_ts, SYMBOL_SLOPE_LONG_SECONDS)
    baseline_30m = _baseline_row(rows, now_ts, GRAFANA_TREND_30M_SECONDS)
    baseline_60m = _baseline_row(rows, now_ts, GRAFANA_TREND_60M_SECONDS)
    window_15m = [row for row in rows if row['ts'] >= now_ts - SYMBOL_SLOPE_LONG_SECONDS]
    peak_15m = max((_safe_float(row.get('profit_rate')) for row in window_15m), default=_safe_float(latest.get('profit_rate')))
    current_return = _safe_float(latest.get('profit_rate'))
    d1m = calc_delta_value(baseline_1m, current_return)
    d3m = calc_delta_value(baseline_3m, current_return)
    d5m = calc_delta_value(baseline_5m, current_return)
    d15m = calc_delta_value(baseline_15m, current_return)
    d30m = calc_delta_value(baseline_30m, current_return)
    d60m = calc_delta_value(baseline_60m, current_return)
    prev_d5m = calc_delta_value(baseline_10m, _safe_float(baseline_5m.get('profit_rate'))) if baseline_5m and baseline_10m else 0.0
    accel = d5m - prev_d5m
    return {
        'data_ready': baseline_15m is not None and latest['ts'] - rows[0]['ts'] >= SYMBOL_HISTORY_READY_SECONDS,
        'warmup_ready': baseline_5m is not None and latest['ts'] - rows[0]['ts'] >= SYMBOL_WARMUP_READY_SECONDS,
        'trend_ready': baseline_30m is not None,
        'data_fresh': now_ts - latest['ts'] <= SYMBOL_SAMPLE_INTERVAL_SECONDS * 2,
        'slope_5m': calc_change_percent(baseline_5m.get('profit_rate'), current_return) if baseline_5m else 0.0,
        'slope_15m': calc_change_percent(baseline_15m.get('profit_rate'), current_return) if baseline_15m else 0.0,
        'drawdown_15m': calc_drawdown_percent(peak_15m, current_return),
        'peak_15m': peak_15m,
        'current_return': current_return,
        'd1m': d1m,
        'd3m': d3m,
        'd5m': d5m,
        'd15m': d15m,
        'd30m': d30m,
        'd60m': d60m,
        'accel': accel,
        'prev_d5m': prev_d5m,
        'trend_state': classify_trend_state(d5m, d30m) if baseline_30m else 'WARMUP',
        'source': 'grafana',
    }


def count_symbol_stop_loss_status(taskOptions_list, symbol):
    if taskOptions_list is None:
        return 0
    return sum(
        1 for item in taskOptions_list
        if item['options'].get('exchange') == RunningExchange
        and item['options'].get('symbol') == symbol
        and item.get('status') == 4
    )


def classify_symbol_health(metrics, stop_loss_count):
    if not metrics.get('data_fresh'):
        return 'watch', '等待Grafana新鲜数据'

    current_return = metrics.get('current_return', 0.0)
    d5m = metrics.get('d5m', 0.0)
    d15m = metrics.get('d15m', 0.0)
    drawdown_15m = metrics.get('drawdown_15m', 0.0)
    trend_state = metrics.get('trend_state', 'NO_DATA')
    action = decide_grafana_action(metrics, 'LIGHT')

    if action in ('EMERGENCY_STOP', 'BAN_24H'):
        return 'frozen', f'Grafana动作={action}'

    if not metrics.get('data_ready'):
        if (
            metrics.get('warmup_ready')
            and d5m > 0
        ):
            return 'recovering', 'Grafana预热期5m收益分数修复'
        return 'watch', '等待Grafana 15分钟采样数据'

    if stop_loss_count >= STOP_LOSS_DOWNGRADE_COUNT and d5m <= 0:
        return 'frozen', '本账号止损偏多且Grafana 5m分数未修复'
    if trend_state == 'DOWN_TREND':
        return 'frozen', 'Grafana 30m/5m同向下跌'
    if d5m < 0 and d15m <= 0 and drawdown_15m >= STOP_DRAWDOWN_15M:
        return 'frozen', 'Grafana短中周期转弱且15m回撤过大'
    if trend_state == 'TOP_RISK':
        return 'watch', 'Grafana 30m仍强但5m转弱，顶部风险'
    if trend_state == 'FAKE_RECOVERY':
        return 'recovering', 'Grafana 30m仍弱但5m反弹，等待真伪确认'
    if trend_state == 'STRONG_TREND' and d15m > 0 and drawdown_15m <= ALLOCATION_MAX_DRAWDOWN_15M:
        return 'healthy', 'Grafana 30m/5m同向上涨且回撤可控'
    if d5m > 0 and drawdown_15m <= STOP_DRAWDOWN_15M:
        return 'recovering', 'Grafana 5m收益分数修复'
    return 'watch', '收益分数趋势或回撤未确认'


def update_symbol_health(taskOptions_list, symbol, metrics):
    stop_loss_count = count_symbol_stop_loss_status(taskOptions_list, symbol)
    state, reason = classify_symbol_health(metrics, stop_loss_count)
    symbol_health[symbol] = {
        'state': state,
        'reason': reason,
        'action': decide_grafana_action(metrics, 'LIGHT'),
        'trend_state': metrics.get('trend_state', 'NO_DATA'),
        'updated_at': time.time(),
        'stop_loss_count': stop_loss_count,
    }
    return symbol_health[symbol]


def build_account_symbol_map(taskOptions_list):
    symbols = {}
    if taskOptions_list is None:
        return symbols
    for robot in taskOptions_list:
        if robot['options'].get('exchange') != RunningExchange:
            continue
        symbol = robot['options'].get('symbol')
        if symbol == markSymbol or symbol == XXXSymbol:
            continue
        symbols.setdefault(symbol, []).append(robot)
    return symbols


def is_symbol_action_cooling(symbol, action, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    record = symbol_action_records.get((symbol, action))
    if record is None:
        return False
    cooldown = {
        'open': SYMBOL_OPEN_COOLDOWN_SECONDS,
        'add': SYMBOL_ADD_COOLDOWN_SECONDS,
        'stop': SYMBOL_STOP_COOLDOWN_SECONDS,
        'standby': SYMBOL_STOP_COOLDOWN_SECONDS,
    }.get(action, 0)
    return now_ts - record < cooldown


def record_symbol_action(symbol, action, now_ts=None):
    symbol_action_records[(symbol, action)] = time.time() if now_ts is None else now_ts


def is_standby_robot(robot):
    return standby_robot_key(robot) in symbol_standby_records


def record_standby_robot(robot, metrics, reason):
    symbol = robot['options'].get('symbol')
    symbol_standby_records[standby_robot_key(robot)] = {
        'symbol': symbol,
        'standby_since': time.time(),
        'standby_peak': metrics.get('peak_15m', metrics.get('current_return', 0.0)),
        'reason': reason,
    }
    save_standby_records()


def record_price_stop_standby(robot, symbol):
    if is_standby_robot(robot):
        return
    metrics = compute_grafana_symbol_metrics(symbol)
    record_standby_robot(robot, metrics, f"价格下跌触发{PRICE_STOP_THRESHOLD_PERCENT}%止损检测")


def clear_standby_robot(robot_id):
    key = standby_robot_key(robot_id)
    if key in symbol_standby_records:
        del symbol_standby_records[key]
        save_standby_records()


def is_standby_restart_protected(symbol, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    protect_until = symbol_restart_protect_until.get(symbol, 0)
    return now_ts < protect_until


def record_standby_restart(symbol, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    symbol_restart_protect_until[symbol] = now_ts + STANDBY_RESTART_PROTECT_SECONDS


def check_standby_recycle_ready(robot, metrics, health, now_ts=None):
    record = symbol_standby_records.get(standby_robot_key(robot))
    if not record:
        return False
    now_ts = time.time() if now_ts is None else now_ts
    if now_ts - record['standby_since'] < STANDBY_MAX_SECONDS:
        return False
    if metrics.get('current_return', 0.0) < GRAFANA_MIN_30M_PROFIT:
        return True
    if health.get('state') == 'frozen':
        return True
    if decide_grafana_action(metrics, 'LIGHT') in ('EMERGENCY_STOP', 'BAN_24H', 'STOP_OBSERVE'):
        return True
    return (
        metrics.get('d5m', 0.0) < 0
        and metrics.get('d15m', 0.0) < 0
        and metrics.get('drawdown_15m', 0.0) >= STOP_DRAWDOWN_15M
    )


def allocate_open_candidates(open_candidates, waiting_count):
    if waiting_count <= 0:
        return []
    allocated = []
    remaining = waiting_count
    for item in sorted(open_candidates, key=lambda row: row['cumulative_sum'], reverse=True):
        if remaining <= 0:
            break
        plan_count = max(1, int(item.get('plan_count', 1)))
        planned = min(plan_count, remaining)
        copied = item.copy()
        copied['plan_count'] = planned
        allocated.append(copied)
        remaining -= planned
    return allocated


def decide_allocation_count(active_count, metrics, health, waiting_count):
    if waiting_count <= 0 or active_count >= MAX_SYMBOL_ACTIVE_COUNT:
        return 0
    hold_level = get_hold_level(active_count)
    action = decide_grafana_action(metrics, hold_level)
    if action not in ('OPEN_1', 'OPEN_1_PROBE', 'ADD_1', 'FAST_ADD_2'):
        return 0
    if active_count > 0 and health.get('state') not in ('healthy', 'recovering'):
        return 0
    capacity = min(waiting_count, MAX_SYMBOL_ACTIVE_COUNT - active_count)
    if action == 'FAST_ADD_2':
        return min(2, capacity)
    return min(1, capacity)


def decide_stop_count(metrics, health, active_count=1):
    action = decide_grafana_action(metrics, get_hold_level(active_count))
    if action in ('EMERGENCY_STOP', 'BAN_24H'):
        return 2
    if not metrics.get('data_ready'):
        return 0
    if health.get('state') == 'frozen':
        return 2
    if action == 'REDUCE_1':
        return 1
    return 0


def decide_standby_count(metrics, health, active_count=1):
    action = decide_grafana_action(metrics, get_hold_level(active_count))
    if action in ('EMERGENCY_STOP', 'BAN_24H'):
        return 2
    if not metrics.get('data_ready'):
        return 0
    if action == 'STOP_OBSERVE':
        return 1
    if health.get('state') == 'frozen':
        return 2
    return 0


def can_restart_robot(robot, taskOptions_list, metrics=None, action=None):
    symbol = robot['options'].get('symbol')
    if (
        action in ('OPEN_1', 'OPEN_1_PROBE')
        and metrics
        and metrics.get('data_fresh')
        and metrics.get('d1m', 0.0) >= 0
    ):
        return True

    msg = utils.getCurrenTime(
        RunningExchange) + f"{robot['account_nick_name']} 【{symbol}】状态：[---拦截---] 动作={action}，未重新满足开仓动作，跳过本次重启"
    log_throttled(f"restart_blocked:{robot['id']}", msg, 10 * 60)
    return False


def log_exception(context):
    msg = utils.getCurrenTime(RunningExchange) + f"{context} 寮傚父:\n{traceback.format_exc()}"
    print(msg)
    logger.log_info(msg)


def guarded_check_stopTime_rules():
    try:
        check_stopTime_rules()
    except Exception:
        log_exception("check_stopTime_rules")


def run_guarded(context, func):
    try:
        return func()
    except Exception:
        log_exception(context)
        return None


def price_change_callback(symbol):
    symbol = symbol.replace('usdt', '_usdt')
    taskOptions_list = fetch_robot_parameter_with_relogin()
    if taskOptions_list is None:
        return
    running_robots = [
        item for item in taskOptions_list
        if item['options'].get('symbol') == symbol
        and item['options'].get('exchange') == RunningExchange
        and item.get('status') == 2
    ]
    if not running_robots:
        return
    msg = utils.getCurrenTime(RunningExchange) + f"[警告] 当前{symbol}价格下跌触发{PRICE_STOP_THRESHOLD_PERCENT}%止损检测，执行账号[!!停机!!]操作\n"
    print(msg)
    logger.log_info(msg)
    SendSmg(msg)

    for item in running_robots:
        record_price_stop_standby(item, symbol)
        cmdRobot.stop(item)
    schedule_price_stop_supplement(symbol)


def schedule_price_stop_supplement(symbol):
    timer_thread = threading.Timer(
        PRICE_STOP_SUPPLEMENT_CHECK_SECONDS,
        supplement_price_stop,
        args=(symbol,),
    )
    timer_thread.daemon = True
    timer_thread.start()


def supplement_price_stop(symbol):
    taskOptions_list = fetch_robot_parameter_with_relogin()
    if taskOptions_list is None:
        return
    running_robots = [
        item for item in taskOptions_list
        if item['options'].get('symbol') == symbol
        and item['options'].get('exchange') == RunningExchange
        and item.get('status') == 2
    ]
    if not running_robots:
        return
    msg = utils.getCurrenTime(
        RunningExchange) + f"[补停] {symbol} 下跌止损后仍有{len(running_robots)}个机器人运行，执行二次停机。"
    print(msg)
    logger.log_info(msg)
    for robot in running_robots:
        record_price_stop_standby(robot, symbol)
        cmdRobot.stop(robot)


def check_profit_rate(decide_actions=True):
    if utils.get_running_status():
        taskOptions_list = fetch_robot_parameter_with_relogin()
        if taskOptions_list is None or temp_taskOptions_list is None:
            return

        if decide_actions:
            with ThreadPoolExecutor(max_workers=2) as executor:
                scan_future = executor.submit(run_grafana_market_decision, taskOptions_list, decide_actions)
                gate_selection_future = executor.submit(getGateSelectionTop)
                grafana_scores = scan_future.result() or {}
                gate_selection_data = gate_selection_future.result()
        else:
            gate_selection_data = None
            run_grafana_market_decision(taskOptions_list, decide_actions)

        if decide_actions:
            if gate_selection_data:
                gate_selection_data = filter_gate_selection_by_grafana(gate_selection_data, grafana_scores)
            if gate_selection_data:
                topSetTest(RunningExchange, gate_selection_data)


def run_grafana_market_decision(taskOptions_list, decide_actions=True):
    grafana_scores = get_grafana_market_scores()
    for grafana_symbol, grafana_profit in grafana_scores.items():
        update_grafana_symbol_history(grafana_symbol, grafana_profit)

    scan_grafana_market_actions(taskOptions_list, grafana_scores, decide_actions)
    return grafana_scores


def filter_gate_selection_by_grafana(gate_selection_data, grafana_scores):
    grafana_symbols = {normalize_blacklist_symbol(symbol) for symbol in (grafana_scores or {}).keys()}
    filtered_data = []
    skipped_symbols = []
    for item in gate_selection_data or []:
        symbol = normalize_blacklist_symbol(item.get('symbol'))
        if symbol in grafana_symbols:
            skipped_symbols.append(symbol)
            continue
        filtered_data.append(item)
    if skipped_symbols:
        preview = ", ".join(skipped_symbols[:10])
        if len(skipped_symbols) > 10:
            preview += f" ... +{len(skipped_symbols) - 10}"
        log_throttled(
            "gate_skip_grafana_existing",
            utils.getCurrenTime(RunningExchange) + f"Gate选币命中Grafana榜单，按Grafana选币为准跳过复制：{preview}",
            5 * 60,
        )
    return filtered_data


def log_market_metric(symbol, metrics, health, account_metrics, active_count):
    if active_count <= 0 and health.get('state') == 'watch':
        return
    action = decide_grafana_action(metrics, get_hold_level(active_count))
    msg = utils.getCurrenTime(RunningExchange) + (
        f"{symbol} Grafana指标: "
        f"30m={metrics['current_return']:.2f}U, "
        f"d1m={metrics.get('d1m', 0.0):.2f}U, "
        f"d5m={metrics.get('d5m', 0.0):.2f}U, "
        f"d30m={metrics.get('d30m', 0.0):.2f}U, "
        f"accel={metrics.get('accel', 0.0):.2f}U, "
        f"趋势={metrics.get('trend_state', 'NO_DATA')}, "
        f"动作={action}, "
        f"账号参考收益={account_metrics['current_return']:.2f}%, "
        f"健康度={health['state']}({health['reason']}), "
        f"账号持仓数={active_count}"
    )
    log_throttled(f"market_metric:{symbol}", msg, interval_seconds=15 * 60)


def scan_grafana_market_actions(taskOptions_list, grafana_scores, decide_actions=True):
    if taskOptions_list is None or temp_taskOptions_list is None:
        return

    blacklist_tokens = load_blacklist_tokens()
    account_symbol_map = build_account_symbol_map(taskOptions_list)

    # 按币种汇总当前资金变化，账号数据只用于日志、模板选择和执行参考。
    all_symbols_comparison = utils.get_all_symbols_funds_comparison(
        taskOptions_list,
        temp_taskOptions_list,
        RunningExchange
    )
    account_comparison_map = {
        item['symbol']: item for item in all_symbols_comparison
        if item['symbol'] != markSymbol and item['symbol'] != XXXSymbol
    }

    for symbol, robots in account_symbol_map.items():
        current_robot = robots[0]
        running_robots = [robot for robot in robots if robot.get('status') == 2]
        current_data = {
            'symbol': symbol,
            'number': len(running_robots),
            'total_now_balance': sum(robot.get('now_balance', 0) for robot in robots),
            'total_init_balance': sum(robot.get('init_balance', 0) for robot in robots),
            'options_raw': current_robot.get('options_raw'),
        }
        if current_data['total_init_balance'] > 0:
            current_data['profit_rate'] = (
                (current_data['total_now_balance'] - current_data['total_init_balance'])
                / current_data['total_init_balance']
                * 100
            )
        else:
            current_data['profit_rate'] = 0
        update_symbol_history(symbol, current_data)
        maintain_symbol_monitor(symbol, len(running_robots), current_robot.get('options_raw'))

    open_candidates = []
    scan_symbols = sorted(set(grafana_scores.keys()) | set(account_symbol_map.keys()))
    for symbol in scan_symbols:
        if symbol == markSymbol or symbol == XXXSymbol:
            continue
        symbol_blacklisted = normalize_blacklist_symbol(symbol) in blacklist_tokens
        metrics = compute_grafana_symbol_metrics(symbol)
        health = update_symbol_health(taskOptions_list, symbol, metrics)
        robots = account_symbol_map.get(symbol, [])
        active_count = sum(1 for robot in robots if robot.get('status') == 2)
        account_metrics = compute_symbol_metrics(symbol)

        if decide_actions:
            log_market_metric(symbol, metrics, health, account_metrics, active_count)

        if not decide_actions:
            continue

        if not robots:
            if symbol_blacklisted:
                continue
            waiting_count = count_waiting_robots(taskOptions_list)
            add_count = decide_allocation_count(0, metrics, health, waiting_count)
            if add_count > 0 and not is_symbol_action_cooling(symbol, 'open'):
                open_candidates.append({
                    'symbol': symbol,
                    'cumulative_sum': metrics['current_return'],
                    'plan_count': add_count,
                })
            continue

        comparison = account_comparison_map.get(symbol)
        current_data = comparison['current'] if comparison else {
            'symbol': symbol,
            'number': active_count,
            'options_raw': robots[0].get('options_raw') if robots else None,
        }
        standby_exists = any(is_standby_robot(robot) for robot in robots)
        standby_count = decide_standby_count(metrics, health, active_count)
        if (
            standby_count > 0
            and (not standby_exists or health.get('state') == 'frozen')
            and not is_symbol_action_cooling(symbol, 'standby')
            and not is_standby_restart_protected(symbol)
        ):
            if check_standby_rule(symbol, standby_count, metrics, health):
                record_symbol_action(symbol, 'standby')
                continue

        stop_count = decide_stop_count(metrics, health, active_count)
        if stop_count > 0 and not is_symbol_action_cooling(symbol, 'stop'):
            if check_stop_rule(symbol, 1, 0, stop_count, protect_profit=False):
                record_symbol_action(symbol, 'stop')
                continue

        if standby_exists:
            continue

        waiting_count = count_waiting_robots(taskOptions_list)
        add_count = decide_allocation_count(active_count, metrics, health, waiting_count)
        if add_count > 0 and not is_symbol_action_cooling(symbol, 'add'):
            if check_allocation_rules(taskOptions_list, current_data, 1, metrics, health):
                record_symbol_action(symbol, 'add')

    if open_candidates:
        open_candidates = allocate_open_candidates(open_candidates, count_waiting_robots(taskOptions_list))
        topSetTest(RunningExchange, open_candidates)


def count_waiting_robots(taskOptions_list):
    if taskOptions_list is None:
        return 0
    return sum(
        1 for item in taskOptions_list
        if item['options'].get('exchange') == RunningExchange
        and item['options'].get('symbol') == markSymbol
    )


def maintain_symbol_monitor(symbol, active_count, options_raw):
    if active_count >= 1 and symbol != markSymbol and symbol != XXXSymbol:
        monitor_thread_name = RunningExchange + "_" + symbol + "_" + "monitor_thread"
        if monitor_thread_name not in socket_thread_dict:
            symbol_without_dash_underscore = symbol.replace('_', '').replace('-', '')
            symbols_and_thresholds = {symbol_without_dash_underscore: str(PRICE_STOP_THRESHOLD_PERCENT)}
            monitor = bgExchang.BinancePriceMonitor(symbols_and_thresholds, logger,
                                                    callback=price_change_callback)
            msg = utils.getCurrenTime(
                RunningExchange) + f"[++新增监控进程+++] 添加 {RunningExchange} 中 {symbol} 的监控行情进程."
            print(msg)
            ref_exchange_value = None
            if options_raw:
                ref_exchange_value = next(
                    (item['value'] for item in options_raw if item['name'] == 'ref_exchange'), None
                )
            if ref_exchange_value is None:
                ref_exchange_value = "binance_usdt_swap"
            thread = threading.Timer(0, monitor.start_monitoring,
                                     args=(ref_exchange_value, RunningExchange,))
            thread.setName(monitor_thread_name)
            thread.daemon = True
            thread.start()
            socket_thread_dict[monitor_thread_name] = monitor
        return

    monitor_thread_name = RunningExchange + "_" + symbol + "_" + "monitor_thread"
    if monitor_thread_name in socket_thread_dict:
        msg = utils.getCurrenTime(
            RunningExchange) + f"[--删除监控进程---] 取消 {RunningExchange} 中 {symbol} 的监控行情进程."
        print(msg)
        thread = socket_thread_dict[monitor_thread_name]
        thread.stop_monitoring()
        del socket_thread_dict[monitor_thread_name]

    return


def check_allocation_rules(taskOptions_list, item, change_percentage, metrics=None, health=None):
    A = item['number']
    symbol = item.get('symbol')
    # 先检查是否还有可用等待位
    filtered_data = [item for item in taskOptions_list if
                     item['options'].get('symbol') == markSymbol and item['options'].get(
                         'exchange') == RunningExchange]
    waiting_count = len(filtered_data)
    if waiting_count <= 0:
        return False

    metrics = metrics or compute_grafana_symbol_metrics(symbol)
    health = health or symbol_health.get(symbol, {'state': 'watch', 'reason': '无健康度数据'})
    add_robots_num = decide_allocation_count(A, metrics, health, waiting_count)
    if add_robots_num <= 0:
        return False

    action = decide_grafana_action(metrics, get_hold_level(A))
    msg = utils.getCurrenTime(
        RunningExchange) + f"{symbol} 满足加仓：动作={action}，趋势={metrics.get('trend_state', 'NO_DATA')}，健康度={health['state']}，Grafana30m={metrics['current_return']:.2f}U，d3m={metrics.get('d3m', 0.0):.2f}U，d5m={metrics.get('d5m', 0.0):.2f}U，d30m={metrics.get('d30m', 0.0):.2f}U，accel={metrics.get('accel', 0.0):.2f}U，计划加仓={add_robots_num}"
    print(msg)
    logger.log_info(msg)
    return check_allocation_rule(symbol, 1, 0, add_robots_num)


def check_allocation_rule(symbol, change_percentage, threshold, add_robots_num):
    allocation_done = False
    if change_percentage > threshold:
        # 按次数执行加仓
        for index in range(add_robots_num):
            taskOptions_list = safe_get_robot_parameter()
            if taskOptions_list is None or temp_taskOptions_list is None:
                msg = utils.getCurrenTime(
                    RunningExchange) + f"[---跳过加仓---] 币种:{symbol}，取参失败，放弃本轮加仓。"
                log_throttled(f"allocation_skip:param:{symbol}", msg, 5 * 60)
                return allocation_done
            filtered_data = [item for item in taskOptions_list if
                             item['options'].get('symbol') == markSymbol and item['options'].get(
                                 'exchange') == RunningExchange]
            if len(filtered_data) == 0:
                log_throttled(
                    f"allocation_skip:no_waiting:{symbol}",
                    utils.getCurrenTime(RunningExchange) + f"加仓币种:{symbol},无仓位可加仓",
                    10 * 60,
                )
                return allocation_done

            # 取出上一轮和当前收益最高的机器人
            filtered = [item for item in taskOptions_list if
                        item['options'].get('symbol') == symbol and item['options'].get('exchange') == RunningExchange]
            filtered_dataTemp = [item for item in temp_taskOptions_list if
                                 item['options'].get('symbol') == symbol and item['options'].get(
                                     'exchange') == RunningExchange]

            # 以上一轮数据为基准，按 ID 配对比较资金变化
            robot_pairs = []
            for current in filtered_dataTemp:
                matching_temp = next((temp for temp in filtered if temp['id'] == current['id']), None)
                if matching_temp:
                    # 计算余额变化百分比
                    current_balance = matching_temp['now_balance']
                    previous_balance = current['now_balance']
                    if previous_balance > 0:
                        change_percent = ((current_balance - previous_balance) / previous_balance) * 100
                        robot_pairs.append((current, change_percent))

            # 按收益变化从高到低排序
            sorted_pairs = sorted(robot_pairs, key=lambda x: x[1], reverse=True)

            # 选择收益变化最好的机器人作为加仓模板
            if sorted_pairs:
                best_robot, best_change = sorted_pairs[0]
                best_options_raw = best_robot['options_raw']
                msg = utils.getCurrenTime(
                    RunningExchange) + f"执行加仓：当前币种:{symbol}，模板机器人:{best_robot['account_nick_name']}，收益变化:{best_change:.2f}%，触发阈值:{threshold}%"
                logger.log_info(msg)

                options_list = [{"id": item["id"], "status": item["status"], **item} for item in taskOptions_list]
                filtered_data = [
                    item for item in options_list
                    if item['options'].get('exchange') == RunningExchange
                    and item['options'].get('symbol') == markSymbol
                ]
                shuffled_data = filtered_data.copy()
                random.shuffle(shuffled_data)

                for robot in shuffled_data:
                    if best_options_raw == "":
                        continue

                    robot['options_raw'] = best_options_raw
                    robot['options'] = best_options_raw
                    cmdRobot.stop(robot)
                    cmdRobot.modify_robot(robot)
                    schedule_robot_start(ALLOCATION_START_DELAY_SECONDS, robot, "allocation")
                    cmdRobot.resetbalance_robot(robot)
                    msg = utils.getCurrenTime(
                        RunningExchange) + f"{index}, 当前币种:{symbol}, [+++加仓成功+++] 当前加仓机器人:{robot['account_nick_name']}"
                    print(msg)
                    logger.log_info(msg)
                    allocation_done = True
                    break
    return allocation_done


def check_standby_rule(symbol, standby_robots_num, metrics=None, health=None):
    standby_done = False
    metrics = metrics or compute_grafana_symbol_metrics(symbol)
    health = health or symbol_health.get(symbol, {'state': 'watch', 'reason': '无健康度数据'})
    action = decide_grafana_action(metrics, 'LIGHT')
    ignore_profit_protect = action in ('EMERGENCY_STOP', 'BAN_24H', 'STOP_OBSERVE', 'REDUCE_1')
    for index in range(standby_robots_num):
        taskOptions_list = safe_get_robot_parameter()
        if taskOptions_list is None:
            msg = utils.getCurrenTime(
                RunningExchange) + f"[---跳过停机观察---] 币种:{symbol}，取参失败，放弃本轮停机观察。"
            log_throttled(f"standby_skip:param:{symbol}", msg, 5 * 60)
            return standby_done
        candidates = [
            item for item in taskOptions_list
            if item['options'].get('symbol') == symbol
            and item['options'].get('exchange') == RunningExchange
            and item.get('status') == 2
            and not is_standby_robot(item)
            and (
                ignore_profit_protect
                or health.get('state') == 'frozen'
                or item.get('profit', 0) * 100 < PROFIT_PROTECT_THRESHOLD
            )
        ]
        if not candidates:
            return standby_done
        standby_robot = sorted(candidates, key=lambda item: item.get('profit', 0))[0]
        cmdRobot.stop(standby_robot)
        record_standby_robot(standby_robot, metrics, health.get('reason', 'Grafana短线转弱'))
        standby_done = True
        msg = utils.getCurrenTime(
            RunningExchange) + f"{index}, {standby_robot['account_nick_name']} 【{symbol}】进入停机观察，保留原币种参数，观察窗口={int(STANDBY_MAX_SECONDS / 60)}分钟，动作={decide_grafana_action(metrics, 'LIGHT')}，趋势={metrics.get('trend_state', 'NO_DATA')}，健康度={health['state']}，d5m={metrics.get('d5m', 0.0):.2f}U，d30m={metrics.get('d30m', 0.0):.2f}U，accel={metrics.get('accel', 0.0):.2f}U"
        print(msg)
        logger.log_info(msg)
    return standby_done


def check_stop_rules(taskOptions_list, item, change_percentage, metrics=None, health=None):
    symbol = item.get('symbol')
    metrics = metrics or compute_grafana_symbol_metrics(symbol)
    health = health or symbol_health.get(symbol, {'state': 'watch', 'reason': '无健康度数据'})
    sub_robots_num = decide_stop_count(metrics, health, int(item.get('number') or 1))
    if sub_robots_num <= 0:
        return False

    action = decide_grafana_action(metrics, get_hold_level(int(item.get('number') or 1)))
    msg = utils.getCurrenTime(
        RunningExchange) + f"{symbol} 满足减仓：动作={action}，趋势={metrics.get('trend_state', 'NO_DATA')}，健康度={health['state']}，Grafana30m={metrics['current_return']:.2f}U，d3m={metrics.get('d3m', 0.0):.2f}U，d5m={metrics.get('d5m', 0.0):.2f}U，d30m={metrics.get('d30m', 0.0):.2f}U，accel={metrics.get('accel', 0.0):.2f}U，计划减仓={sub_robots_num}"
    print(msg)
    logger.log_info(msg)
    return check_stop_rule(symbol, 1, 0, sub_robots_num, protect_profit=False)


def check_stop_rule(symbol, change_percentage, threshold, sub_robots_num, protect_profit=True):
    stop_done = False
    if abs(change_percentage) > threshold:
        # 按次数执行减仓
        for index in range(sub_robots_num):
            taskOptions_list = safe_get_robot_parameter()
            if taskOptions_list is None or temp_taskOptions_list is None:
                msg = utils.getCurrenTime(
                    RunningExchange) + f"[---跳过减仓---] 币种:{symbol}，取参失败，放弃本轮减仓。"
                log_throttled(f"stop_skip:param:{symbol}", msg, 5 * 60)
                return stop_done
            filtered_data = [item for item in taskOptions_list if
                             item['options'].get('symbol') == symbol and item['options'].get(
                                 'exchange') == RunningExchange]
            filtered_data = [
                item for item in filtered_data
                if not is_standby_robot(item) or check_standby_recycle_ready(
                    item,
                    compute_grafana_symbol_metrics(symbol),
                    symbol_health.get(symbol, {'state': 'watch', 'reason': '无健康度数据'}),
                )
            ]
            filtered_dataTemp = [item for item in temp_taskOptions_list if
                                 item['options'].get('symbol') == symbol and item['options'].get(
                                     'exchange') == RunningExchange]

            robot_pairs = []
            for current in filtered_data:
                matching_temp = next((temp for temp in filtered_dataTemp if temp['id'] == current['id']), None)
                if matching_temp:
                    if protect_profit and current.get('profit', 0) * 100 >= PROFIT_PROTECT_THRESHOLD:
                        continue
                    current_balance = current['now_balance']
                    previous_balance = matching_temp['now_balance']
                    if previous_balance > 0:
                        change_percent = ((current_balance - previous_balance) / previous_balance) * 100
                        robot_pairs.append((current, change_percent))

            if not robot_pairs:
                fallback_robots = [
                    current for current in filtered_data
                    if current['status'] != 0
                    and current['options'].get('symbol') not in (markSymbol, XXXSymbol)
                    and (not protect_profit or current.get('profit', 0) * 100 < PROFIT_PROTECT_THRESHOLD)
                ]
                robot_pairs = [(current, current.get('profit', 0) * 100) for current in fallback_robots]

            # 按亏损变化从低到高排序
            sorted_pairs = sorted(robot_pairs, key=lambda x: x[1])

            # 选择亏损最大的机器人执行减仓
            if sorted_pairs:
                worst_robot, worst_change = sorted_pairs[0]

                if worst_robot['status'] == 0 or worst_robot['options'].get('symbol') == markSymbol or worst_robot[
                    'options'].get('symbol') == XXXSymbol:
                    return stop_done

                msg = utils.getCurrenTime(
                    RunningExchange) + f"执行减仓：当前币种:{symbol}，机器人:{worst_robot['account_nick_name']}，亏损变化:{worst_change:.2f}%，触发阈值:-{threshold}"
                print(msg)
                logger.log_info(msg)

                options_raw = worst_robot['options_raw']
                for item in options_raw:
                    if item['name'] == 'ref_symbol':
                        item['value'] = markSymbol
                    elif item['name'] == 'symbol':
                        item['value'] = markSymbol

                worst_robot['options_raw'] = options_raw
                worst_robot['options'] = options_raw

                cmdRobot.stop(worst_robot)
                cmdRobot.modify_robot(worst_robot)
                cmdRobot.resetbalance_robot(worst_robot)
                clear_standby_robot(worst_robot['id'])

                msg = utils.getCurrenTime(
                    RunningExchange) + f"{index}, {worst_robot['account_nick_name']} 减仓完成，已修改参数/重置收益/停止成功，亏损比例: {worst_change:.2f}%"
                print(msg)
                logger.log_info(msg)
                stop_done = True
                continue
    return stop_done


def add_TH_data(symbol):
    robot_add_List.append(symbol)
    update_TH_counts()


# 待加仓计数
def update_TH_counts():
    global symbol_counts
    symbol_counts = Counter(robot_add_List)
# 统计当前待加仓数量

# 查询指定币种的待加仓数量
def query_TH_count(symbol):
    update_TH_counts()
    return symbol_counts.get(symbol, 0)


def remove_TH_symbol(symbol):
    update_TH_counts()
    if symbol in robot_add_List:
        robot_add_List.remove(symbol)


# 定时检查止损、手动停机和等待位回收
def check_stopTime_rules():
    while True:
        if utils.get_running_status():
            taskOptions_list = fetch_robot_parameter_with_relogin()
            if taskOptions_list is None:
                time.sleep(LOOP_SLEEP_SECONDS)
                continue
            global stop_loss_List  # 使用全局止损列表
            check_symbol_rule(taskOptions_list)
            options_list = [{"id": item["id"], "status": item["status"], **item} for item in taskOptions_list]

            filtered_data = [item for item in options_list if item['options'].get('exchange') == RunningExchange]
            for robot in filtered_data:
                if robot['options'].get('symbol') == XXXSymbol:
                    continue
                if robot['options'].get('symbol') == markSymbol:
                    continue

                # 使用系统返回的更新时间作为当前这轮运行时长基准
                timestamp_in_minutes = (time.time() * 1000 - robot['updated_at']) / (1000 * 60)

                profit_rate = robot['profit'] * 100
                waitingFlag = False
                symbol = robot['options'].get('symbol')
                health_state = symbol_health.get(symbol, {}).get('state', 'watch')
                limit_silence_hit = robot['status'] == 4 and LIMIT_SILENCE_MSG in str(robot.get('msg') or "")
                standby_record = symbol_standby_records.get(standby_robot_key(robot))
                if standby_record and robot['status'] == 2:
                    if time.time() - standby_record.get('standby_since', 0) < STANDBY_RUNNING_GRACE_SECONDS:
                        continue
                    clear_standby_robot(robot['id'])
                    record_standby_restart(symbol)
                    standby_record = None

                if standby_record and robot['status'] in (0, 4):
                    metrics = compute_grafana_symbol_metrics(symbol)
                    health = symbol_health.get(symbol, {'state': 'watch', 'reason': '无健康度数据'})
                    if check_standby_recycle_ready(robot, metrics, health):
                        print(
                            utils.getCurrenTime(
                                RunningExchange) + f"{robot['account_nick_name']} 【{symbol}】停机观察满{int(STANDBY_MAX_SECONDS / 60)}分钟仍未恢复，加入待加仓列表"
                        )
                        waitingFlag = True
                    else:
                        checkRobotOpenStatus(robot, taskOptions_list)
                        continue
                elif robot['status'] == 0 and timestamp_in_minutes > MANUAL_STOP_RECYCLE_MINUTES:
                    print(
                        utils.getCurrenTime(
                            RunningExchange) + f"{robot['account_nick_name']} 【{robot['options'].get('symbol')}】[---减仓---]手动停机超过5分钟，加入待加仓列表"
                    )
                    waitingFlag = True
                elif limit_silence_hit:
                    if timestamp_in_minutes >= LIMIT_SILENCE_WAIT_MINUTES:
                        print(
                            utils.getCurrenTime(
                                RunningExchange) + f"{robot['account_nick_name']} 【{robot['options'].get('symbol')}】[---减仓---] 命中限制实在是太狠了日志，停机满30分钟后加入待加仓列表"
                        )
                        waitingFlag = True
                    else:
                        continue
                elif profit_rate <= FAILED_STOP_LOSS_RECYCLE_PROFIT_PERCENT and robot['status'] == 4:
                    print(
                        utils.getCurrenTime(
                            RunningExchange) + f"{robot['account_nick_name']} 【{robot['options'].get('symbol')}】[---减仓---]止损出错且收益<-4%，收益={profit_rate}%，加入待加仓列表"
                    )
                    waitingFlag = True
                elif checkRobotOpenStatus(robot, taskOptions_list):
                    waitingFlag = True
                elif profit_rate >= PROFIT_PROTECT_THRESHOLD:
                    continue
                elif health_state in ('healthy', 'recovering'):
                    continue
                else:
                    matched_runtime_rule = next(
                        (
                            (minutes, profit_threshold)
                            for minutes, profit_threshold in RUNTIME_RECYCLE_RULES
                            if timestamp_in_minutes > minutes and profit_rate <= profit_threshold
                        ),
                        None,
                    )
                    if matched_runtime_rule:
                        minutes, profit_threshold = matched_runtime_rule
                        print(
                            utils.getCurrenTime(
                                RunningExchange) + f"{robot['account_nick_name']} 【{robot['options'].get('symbol')}】[---减仓---]运行时间>{minutes / 60:g}h 且收益<={profit_threshold}%，收益={profit_rate}%，加入待加仓列表"
                        )
                        waitingFlag = True
                    else:
                        waitingFlag = False

                if waitingFlag:
                    options_raw = robot['options_raw']
                    for item in options_raw:
                        if item['name'] == 'ref_symbol':
                            item['value'] = markSymbol
                        elif item['name'] == 'symbol':
                            item['value'] = markSymbol

                    robot['options_raw'] = options_raw
                    robot['options'] = options_raw
                    cmdRobot.stop(robot)
                    cmdRobot.modify_robot(robot)
                    cmdRobot.resetbalance_robot(robot)
                    clear_standby_robot(robot['id'])
                    msg = utils.getCurrenTime(
                        RunningExchange) + f"{robot['account_nick_name']} 开始加入到等待加仓队列中，修改参数/重置收益/停止成功."
                    print(msg)
                    logger.log_info(msg)
        # 每轮巡检间隔10秒
        time.sleep(LOOP_SLEEP_SECONDS)


def checkRobotOpenStatus(robot, taskOptions_list):
    global stop_loss_List  # 使用全局止损列表
    timestamp_in_minutes = (time.time() * 1000 - robot['updated_at']) / (1000 * 60)
    elapsed_seconds = timestamp_in_minutes * 60
    profit_rate = robot['profit'] * 100
    stop_loss_count = utils.get_stop_loss_count(stop_loss_List, robot['id'])
    stop_time = utils.get_stop_time(stop_loss_List, robot['id'])
    symbol = robot['options'].get('symbol')
    if robot['status'] != 4 and not is_standby_robot(robot):
        return False
    if is_standby_robot(robot) and robot['status'] not in (0, 4):
        return False

    if stop_loss_count >= STOP_LOSS_DOWNGRADE_COUNT:
        if stop_time != "" and datetime.now() - stop_time < timedelta(minutes=STOP_LOSS_DOWNGRADE_WINDOW_MINUTES):
            msg = utils.getCurrenTime(
                RunningExchange) + f"{robot['account_nick_name']} 【{robot['options'].get('symbol')}】状态：[---减仓---] 6小时内触发止损2次，加入到等待加仓队列中."
            print(msg)
            logger.log_info(msg)
            stop_loss_List = utils.remove_entry_by_id(stop_loss_List, robot['id'])
            return True
        stop_loss_List = utils.remove_entry_by_id(stop_loss_List, robot['id'])
        stop_loss_count = 0

    metrics = compute_grafana_symbol_metrics(symbol)
    health = symbol_health.get(symbol, {'state': 'watch', 'reason': '无健康度数据'})
    restart_action = decide_grafana_action(metrics, 'NONE')
    restart_ready = restart_action in ('OPEN_1', 'OPEN_1_PROBE')

    if stop_loss_count < 2 and profit_rate >= -4 and robot['options'].get('symbol') != markSymbol and not is_manual_start_cooling(robot['id']) and restart_ready:
        if not can_restart_robot(robot, taskOptions_list, metrics, restart_action):
            return False
        start_robot(robot, "auto_recovery")  # 满足条件后自动恢复开机
        stop_loss_List = utils.update_stop_loss_list(stop_loss_List, robot['id'])
        if is_standby_robot(robot):
            clear_standby_robot(robot['id'])
            record_standby_restart(symbol)
        msg = utils.getCurrenTime(
            RunningExchange) + f"{robot['account_nick_name']} 【{robot['options'].get('symbol')}】状态：[///开机///] 动作={restart_action}，趋势={metrics.get('trend_state', 'NO_DATA')}，健康度={health['state']}，Grafana30m={metrics['current_return']:.2f}U，d5m={metrics.get('d5m', 0.0):.2f}U，d15m={metrics.get('d15m', 0.0):.2f}U，d30m={metrics.get('d30m', 0.0):.2f}U，accel={metrics.get('accel', 0.0):.2f}U，执行重新开机成功"
        print(msg)
        logger.log_info(msg)
    return False


def check_symbol_rule(taskOptions_list):
    # 閬嶅巻 taskOptions_list
    filtered_data = [item for item in taskOptions_list if item['options'].get('exchange') == RunningExchange]
    for item in filtered_data:
        robot_id = item["id"]
        symbol = item['options'].get('symbol')
        status = item["status"]
        current_time = datetime.now()
        previous_item = id_data_dict.get(robot_id)
        previous_status = previous_item["status"] if previous_item else None
        auto_start_record = auto_start_records.get(robot_id)

        if previous_status != 2 and status == 2:
            if auto_start_record and current_time - auto_start_record['at'] <= timedelta(
                    seconds=AUTO_START_DETECTION_WINDOW_SECONDS):
                del auto_start_records[robot_id]
            else:
                manual_start_cooldown_until[robot_id] = current_time + timedelta(
                    minutes=MANUAL_START_COOLDOWN_MINUTES)
                msg = utils.getCurrenTime(
                    RunningExchange) + f"{item['account_nick_name']} 【{symbol}】检测到手动开机，进入{MANUAL_START_COOLDOWN_MINUTES}分钟冷却期"
                print(msg)
                logger.log_info(msg)
        elif auto_start_record and current_time - auto_start_record['at'] > timedelta(
                seconds=AUTO_START_DETECTION_WINDOW_SECONDS):
            del auto_start_records[robot_id]

        # 更新字典中的数据
        id_data_dict[robot_id] = item


def topSetTest(exchange, top_symbol_data=None):
    global top_symbol_List
    log_throttled("top_set_start", utils.getCurrenTime(RunningExchange) + "开始进行排行榜top复制币种测试!!!", 5 * 60)
    taskOptions_list = fetch_robot_parameter_with_relogin()
    if taskOptions_list is None:
        return
    if count_waiting_robots(taskOptions_list) <= 0:
        log_throttled(
            "top_set_no_waiting",
            utils.getCurrenTime(RunningExchange) + "当前无1_usdt等待位，跳过本轮复制候选。",
            5 * 60,
        )
        return
    contract_data = bgExchang.get_tickers(exchange, "swap", 0)
    available_symbols = set()
    if contract_data:
        available_symbols = set(utils.normalize_symbol(item['symbol']) for item in contract_data)
    blacklist_tokens = load_blacklist_tokens()
    options_list = [{"id": item["id"], "status": item["status"], **item} for item in taskOptions_list]
    filtered_data = [item for item in options_list if item['options'].get('exchange') == exchange]
    active_or_standby_symbols = set(
        item['options']['symbol']
        for item in filtered_data
        if item.get('status') == 2 or is_standby_robot(item)
    )
    for topItem in top_symbol_data:
        bTopSetSymbolFlag = False
        top_symbol = normalize_blacklist_symbol(topItem.get('symbol'))
        topItem['symbol'] = top_symbol
        if top_symbol in blacklist_tokens:
            msg = utils.getCurrenTime(RunningExchange) + f"{top_symbol} 命中黑名单，跳过本次换币。"
            log_throttled(f"top_skip:blacklist:{top_symbol}", msg, 15 * 60)
            continue
        if available_symbols and topItem['symbol'] not in available_symbols:
            msg = utils.getCurrenTime(
                RunningExchange) + f"{topItem['symbol']} 不在 {exchange} 可交易合约列表中，跳过本次换币。"
            log_throttled(f"top_skip:unavailable:{topItem['symbol']}", msg, 15 * 60)
            continue
        if topItem['symbol'] in active_or_standby_symbols:
            msg = utils.getCurrenTime(RunningExchange) + f"{topItem['symbol']} 当前运行中或停机观察中已存在该交易对，不进行加仓测试."
            log_throttled(f"top_skip:exists:{topItem['symbol']}", msg, 15 * 60)
            continue
        if utils.get_addSymbol_time(top_symbol_List,
                                    topItem['symbol']) != "" and datetime.now() - utils.get_addSymbol_time(
            top_symbol_List, topItem['symbol']) < timedelta(seconds=SYMBOL_OPEN_COOLDOWN_SECONDS):
            msg = utils.getCurrenTime(RunningExchange) + f"{int(SYMBOL_OPEN_COOLDOWN_SECONDS / 60)}分钟内该品种加入过测试，已跳过：" + topItem['symbol']
            log_throttled(f"top_skip:cooling:{topItem['symbol']}", msg, 15 * 60)
            continue
        msg = utils.getCurrenTime(RunningExchange) + "开始复制进行测试：" + topItem['symbol']
        print(msg)
        logger.log_info(msg)

        iteration_count = 0
        max_iterations = max(1, int(topItem.get('plan_count', 1)))
        backup_data = filtered_data.copy()
        for robot in backup_data:
            if robot['options']['symbol'] != markSymbol:
                continue
            if iteration_count == max_iterations:
                break
            iteration_count += 1
            update_item = [item for item in filtered_data if item["id"] == robot["id"]]
            if update_item[0]['options'].get('symbol') == topItem['symbol']:
                print(utils.getCurrenTime(
                    RunningExchange) + f"{update_item[0]['account_nick_name']} 交易对相同，不进行复制.")
                filtered_data.remove(robot)
                continue

            options_raw = update_item[0]['options_raw']
            ref_exchange, ref_symbol = bgExchang.getRefParment(RunningExchange, topItem['symbol'])
            for item in options_raw:
                ref_lever, ref_lose = setItemLever(update_item[0]['init_balance'])
                if item['name'] == 'opening_mode':
                    item['value'] = '仅Maker'
                if item['name'] == 'closing_mode':
                    item['value'] = '仅Maker'
                if item['name'] == 'ref_exchange':
                    item['value'] = ref_exchange
                elif item['name'] == 'ref_symbol':
                    item['value'] = ref_symbol
                elif item['name'] == 'symbol':
                    item['value'] = topItem['symbol']
                elif item['name'] == 'open':
                    item['value'] = ref_open2()
                elif item['name'] == 'close':
                    item['value'] = ref_close2()
                elif item['name'] == 'lever':
                    item['value'] = ref_lever
                elif item['name'] == 'stop_lose_percent':
                    item['value'] = ref_lose
                elif item['name'] == 'bias_type':
                    item['value'] = 'EMA'
                elif item['name'] == 'window':
                    item['value'] = COPY_BIAS_WINDOW

            filtered_data.remove(robot)
            update_item[0]['options_raw'] = options_raw
            update_item[0]['options'] = options_raw
            cmdRobot.stop(update_item[0])
            cmdRobot.modify_robot(update_item[0])
            schedule_robot_start(TOP_TEST_START_DELAY_SECONDS, update_item[0], "top_test")
            cmdRobot.resetbalance_robot(update_item[0])
            bTopSetSymbolFlag = True
            top_symbol_List = utils.update_symbol_list(top_symbol_List, topItem['symbol'])
            record_symbol_action(topItem['symbol'], 'open')
            continue

        if not bTopSetSymbolFlag:
            utils.remove_addSymbol_by_id(top_symbol_List, topItem['symbol'])
            msg = utils.getCurrenTime(
                RunningExchange) + f"{topItem['symbol']} 当前交易对[***测试失败***]，原因：无待加仓列表仓位."
            if log_throttled(f"top_failed:no_waiting:{topItem['symbol']}", msg, 15 * 60):
                SendSmg(msg)


def ref_open():
    random_number_A = random.uniform(0, 0.1)
    random_number_A = round(random_number_A, 2)
    result = 15 + random_number_A
    result = round(result, 2)
    return result


def ref_open2():
    result = round(random.uniform(COPY_OPEN_MIN, COPY_OPEN_MAX), 2)
    return result


def ref_close():
    random_number_B = random.uniform(0, 0.1)
    random_number_B = round(random_number_B, 2)
    result = 1.3 + random_number_B
    result = round(result, 2)
    return result


def ref_close2():
    result = round(random.uniform(COPY_CLOSE_MIN, COPY_CLOSE_MAX), 2)
    return result


def setItemLever(init_balance):
    if init_balance <= 0:
        return COPY_DEFAULT_LEVER, COPY_DEFAULT_STOP_LOSS

    setLeverBalance = COPY_LEVER_BALANCE_TARGET
    lever = setLeverBalance / init_balance
    lever = round(lever, 2)
    lever = max(COPY_MIN_LEVER, min(lever, COPY_MAX_LEVER))

    stop_loss = COPY_STOP_LOSS_BASE + (lever / 100)
    stop_loss = round(stop_loss, 4)
    return lever, stop_loss


# 历史手动杠杆/止损方案已废弃，保留方案1


def getGateSelectionTop():
    blacklist = load_blacklist_tokens()
    selected_data = bgExchang.get_gate_volume_selection(
        min_24h_usdt=GATE_SELECTION_MIN_24H_USDT,
        min_1m_usdt=GATE_SELECTION_MIN_1M_USDT,
        required_1m_count=GATE_SELECTION_REQUIRED_1M_COUNT,
        limit=GATE_SELECTION_KLINE_LIMIT,
        blacklist=blacklist,
        min_24h_change=GATE_SELECTION_MIN_24H_CHANGE,
        hot_min_24h_usdt=GATE_HOT_SELECTION_MIN_24H_USDT,
        hot_min_1m_usdt=GATE_HOT_SELECTION_MIN_1M_USDT,
        hot_required_1m_count=GATE_HOT_SELECTION_REQUIRED_1M_COUNT,
    )
    log_gate_selection(selected_data, len(blacklist))
    return selected_data


def log_gate_selection(selected_data, blacklist_count):
    preview = ", ".join(
        f"{item['symbol']}(通道{item.get('selection_reason', '-')}, 涨幅{_safe_float(item.get('change24h')):.2f}%, 24H量{_safe_float(item.get('usdtVolume')):.0f}U, 普通{item.get('qualified_1m_count', 0)}/{GATE_SELECTION_KLINE_LIMIT}, 热门{item.get('hot_qualified_1m_count', 0)}/{GATE_SELECTION_KLINE_LIMIT})"
        for item in selected_data[:10]
    )
    if len(selected_data) > 10:
        preview += f" ... +{len(selected_data) - 10}"
    msg = utils.getCurrenTime(
        RunningExchange) + f"Gate流动性选币：普通通道=24H成交额>={GATE_SELECTION_MIN_24H_USDT}U且24H涨幅>={GATE_SELECTION_MIN_24H_CHANGE}%且1mK线{GATE_SELECTION_REQUIRED_1M_COUNT}/{GATE_SELECTION_KLINE_LIMIT}根>={GATE_SELECTION_MIN_1M_USDT}U；热门通道=24H成交额>={GATE_HOT_SELECTION_MIN_24H_USDT}U且1mK线{GATE_HOT_SELECTION_REQUIRED_1M_COUNT}/{GATE_SELECTION_KLINE_LIMIT}根>={GATE_HOT_SELECTION_MIN_1M_USDT}U；黑名单={blacklist_count}，入选={len(selected_data)}"
    if preview:
        msg += f"，候选={preview}"
    log_throttled("gate_selection_summary", msg, 5 * 60)


def getXbtTop():
    result = xbtRobot.XbtLogin(xbtUsername, xbtPassword)
    if result == 0:
        xbtdata = xbtRobot.getXBTRobotParameter()
        xbtGroupData = utils.xbt_group_and_count(xbtdata, RunningExchange, XBTRobotNum)
        daily_sort_data = sorted(xbtGroupData, key=lambda x: x['total_daily_1h_rate'], reverse=True)[
                          :XBTTopNum]  # 1. 按小时日化排序，并取前 N
        pnl_sort_data = sorted(xbtGroupData, key=lambda x: x['total_pnl_rate'], reverse=True)[
                        :XBTTopNum]  # 2. 按收益率排序，并取前 N
        merge_data = utils.xbt_data_merge(daily_sort_data, pnl_sort_data)  # 3. 融合榜单数据
        merge_sort_data = sorted(merge_data, key=lambda x: x['total_daily_1h_rate'], reverse=True)[
                          :XBTTopNum]  # 4. 融合后再次按小时日化排序，并取前 N
        preview = ", ".join(
            f"{topItem['symbol']}({topItem['total_daily_1h_rate']}%)"
            for topItem in merge_sort_data[:10]
        )
        msg = utils.getCurrenTime(
            RunningExchange) + f"XBT融合榜单：设置数量={XBTTopNum}，入选={len(merge_sort_data)}"
        if preview:
            msg += f"，候选={preview}"
        log_throttled("xbt_selection_summary", msg, 10 * 60)
        return merge_sort_data
    return None


def run_Test():
    if utils.get_running_status():
        # 1. 先进行 XBT 测试
        if XBTTopNum > 0:
            msg = utils.getCurrenTime(RunningExchange) + "——————————运行xbt测试——————————"
            log_throttled("run_test_xbt_start", msg, 10 * 60)
            top_xbt_data = getXbtTop()
            if top_xbt_data is not None:
                topSetTest(RunningExchange, top_xbt_data)

        # 2. Grafana 不再按 top3 复制，统一走全量监控和方案2+3动作决策。
        msg = utils.getCurrenTime(RunningExchange) + "——————————运行Grafana全量策略检查——————————"
        log_throttled("run_test_grafana_start", msg, 10 * 60)
        taskOptions_list = fetch_robot_parameter_with_relogin()
        if taskOptions_list is not None:
            run_grafana_market_decision(taskOptions_list, True)

        # 3. 再进行交易所榜单测试
        if exchangeTopNum > 0:
            top_symbol_data = getGateSelectionTop()
            top_symbol_data = filter_gate_selection_by_grafana(top_symbol_data, get_grafana_market_scores())
            msg = utils.getCurrenTime(RunningExchange) + "——————————运行Gate流动性选币成功——————————"
            log_throttled("run_test_gate_selection_done", msg, 10 * 60)
            topSetTest(RunningExchange, top_symbol_data)

        if FeiShuWebhook_url != "":
            cmdRobot.sendfeishu("消息推送：AutoRobot 当前 top 复制成功", FeiShuWebhook_url)
        if DiscordWebhook_url != "":
            cmdRobot.sendDiscordMsg("消息推送：AutoRobot 当前 top 复制成功", DiscordWebhook_url)


def SendSmg(msg):
    global accumulated_messages, last_send_time

    # 把新消息追加到累计消息中
    accumulated_messages += msg

    # 满足发送条件时推送消息
    if accumulated_messages and time.time() - last_send_time >= 10:
        # 实际发送消息
        if DiscordWebhook_url != "":
            cmdRobot.sendDiscordMsg(msg, DiscordWebhook_url)

        # 重置累计消息和发送时间
        accumulated_messages = ""
        last_send_time = time.time()

def eliminate_strategy():
    if utils.get_running_status():
        taskOptions_list = fetch_robot_parameter_with_relogin()
        if taskOptions_list is None:
            return
        options_list = [{"id": item["id"], "status": item["status"], **item} for item in taskOptions_list]
        filtered_data = [item for item in options_list if (
                item['options'].get('exchange') == RunningExchange and item['options'].get(
            "symbol") != XXXSymbol and item['options'].get('symbol') != markSymbol and (
                        item['status'] == 2 or item['status'] == 4))]
        # 按 profit 从小到大排序
        filtered_data = [
            item for item in filtered_data
            if symbol_health.get(item['options'].get('symbol'), {}).get('state') == 'frozen'
        ]
        sorted_data = sorted(filtered_data, key=lambda x: float(x['profit']), reverse=False)
        top_5_data = sorted_data[:ELIMINATE_STRATEGY_COUNT]
        for robot in top_5_data:
            options_raw = robot['options_raw']
            for item in options_raw:
                if item['name'] == 'ref_symbol':
                    item['value'] = markSymbol
                elif item['name'] == 'symbol':
                    item['value'] = markSymbol

            robot['options_raw'] = options_raw
            robot['options'] = options_raw
            cmdRobot.stop(robot)
            cmdRobot.modify_robot(robot)
            cmdRobot.resetbalance_robot(robot)
            msg = utils.getCurrenTime(
                RunningExchange) + f"{robot['account_nick_name']} [---减仓---] 收益排名靠后，加入等待列表，已修改参数/重置收益/停止成功."
            print(msg)
            logger.log_info(msg)


def run_strategy():
    global symbol_standby_records
    symbol_standby_records = load_standby_records()

    check_stop_rules_thread = threading.Thread(target=guarded_check_stopTime_rules, daemon=True)
    check_stop_rules_thread.start()

    global temp_taskOptions_list
    temp_taskOptions_list = fetch_robot_parameter_with_relogin()

    last_eliminate_strategy_time = time.time()
    last_symbol_sample_time = 0
    last_symbol_decision_time = 0

    while True:
        current_time = time.time()
        if current_time - last_symbol_sample_time >= SYMBOL_SAMPLE_INTERVAL_SECONDS:
            should_decide = current_time - last_symbol_decision_time >= SYMBOL_DECISION_INTERVAL_SECONDS
            run_guarded("check_profit_rate", lambda: check_profit_rate(should_decide))
            last_symbol_sample_time = current_time
            if should_decide:
                last_symbol_decision_time = current_time
                refreshed_task_options = fetch_robot_parameter_with_relogin()
                if refreshed_task_options is not None:
                    temp_taskOptions_list = refreshed_task_options
                log_throttled(
                    "strategy_loop_decision_done",
                    "++++++++++++++++++++++++++++++++++++++++【Bitget合约】检查加减仓完成++++++++++++++++++++",
                    10 * 60,
                    write_log=False,
                )

        if current_time - last_eliminate_strategy_time >= ELIMINATE_STRATEGY_INTERVAL_SECONDS:
            run_guarded("eliminate_strategy", eliminate_strategy)
            last_eliminate_strategy_time = current_time
            log_throttled(
                "strategy_loop_eliminate_done",
                "----------------------------------------【Bitget合约】收益排行最低的2个机器人进行减仓--------------------",
                10 * 60,
                write_log=False,
            )

        time.sleep(LOOP_SLEEP_SECONDS)
