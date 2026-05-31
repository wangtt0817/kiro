import logging
import os
import threading
import configparser
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

# config.py

Running = True


def get_running_status():
    return Running


def set_running_status(value):
    global Running
    Running = value


class CustomLogger:
    _instance = None

    def __new__(cls, log_file_path="logs_", log_level=logging.DEBUG):
        if cls._instance is None:
            cls._instance = super(CustomLogger, cls).__new__(cls)
            cls._instance.log_file_path = log_file_path
            cls._instance.log_level = log_level
            cls._instance.logger = cls._instance.setup_logger()
        return cls._instance

    def setup_logger(self):
        if not os.path.exists(self.log_file_path):
            os.makedirs(self.log_file_path)

        yesterday = datetime.now() - timedelta(days=1)
        yesterday_log_file = yesterday.strftime("%Y-%m-%d") + "_app.log"
        yesterday_log_file_path = os.path.join(self.log_file_path, yesterday_log_file)
        if os.path.exists(yesterday_log_file_path):
            os.remove(yesterday_log_file_path)

        log_format = "%(asctime)s [%(levelname)s]: %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"

        log_file_name = datetime.now().strftime("%Y-%m-%d") + "_app.log"
        log_file_path = os.path.join(self.log_file_path, log_file_name)

        handler = TimedRotatingFileHandler(log_file_path, when="midnight", interval=1, backupCount=1)
        handler.setFormatter(logging.Formatter(fmt=log_format, datefmt=date_format))

        logger = logging.getLogger(__name__)
        logger.addHandler(handler)
        logger.setLevel(self.log_level)

        return logger

    def log_info(self, message):
        self.logger.info(message)

    def log_warning(self, message):
        self.logger.warning(message)

    def log_error(self, message):
        self.logger.error(message)

    def log_exception(self, message):
        self.logger.exception(message)


def delete_data_by_robotid(file_path, robotid):
    """
    鏍规嵁robotid鍒犻櫎瀵瑰簲鏁版嵁
    """
    config = configparser.ConfigParser()
    config.read(file_path)
    if robotid in config:
        del config[robotid]
        with open(file_path, 'w') as f:
            config.write(f)


def calculate_profit_rate(now_balance, init_balance):
    if init_balance != 0:
        return round(((now_balance - init_balance) / init_balance) * 100, 2)


def group_and_count(options_list2, target_exchange):
    # 使用字典跟踪每个 symbol 的累计值
    symbol_totals = {}

    for item in options_list2:
        symbol = item['options'].get('symbol')
        exchange = item['options'].get('exchange')
        init_balance = item['init_balance']
        now_balance = item['now_balance']
        options_raw = item['options_raw']

        if exchange == target_exchange:
            if symbol not in symbol_totals:
                symbol_totals[symbol] = {
                    'symbol': symbol,
                    'number': 0,
                    'exchange': exchange,
                    'options_raw': options_raw,
                    'total_profit': 0,
                    'total_init_balance': 0,
                    'total_now_balance': 0
                }

            symbol_totals[symbol]['total_profit'] += now_balance - init_balance
            symbol_totals[symbol]['total_init_balance'] += init_balance
            symbol_totals[symbol]['total_now_balance'] += now_balance
            symbol_totals[symbol]['number'] += 1

    # 计算每个 symbol 的收益率并生成最终结果列表
    grouped_data = [{
        'symbol': symbol,
        'number': data['number'],
        'exchange': data['exchange'],
        'options_raw': data['options_raw'],
        'total_profit': data['total_profit'],
        'total_init_balance': data['total_init_balance'],
        'total_now_balance': data['total_now_balance'],
        'profit_rate': calculate_profit_rate(data['total_now_balance'], data['total_init_balance'])
    } for symbol, data in symbol_totals.items()]

    # sorted_grouped_data = sorted(grouped_data, key=lambda x: x['profit_rate'], reverse=True)
    sorted_grouped_data = sorted(grouped_data, key=lambda x: (x['profit_rate'] is not None, x['profit_rate'] or 0),
                                 reverse=True)

    return sorted_grouped_data


def get_symbol_funds(options_list, symbol, target_exchange):
    """
    获取指定交易所中指定品种的全部资金信息。

    Args:
        options_list: 机器人参数列表。
        symbol: 要查询的品种符号。
        target_exchange: 目标交易所。

    Returns:
        包含该品种资金信息的字典；如果未找到则返回 None。
    """
    # 使用字典跟踪指定 symbol 的累计值
    symbol_data = None

    for item in options_list:
        item_symbol = item['options'].get('symbol')
        exchange = item['options'].get('exchange')
        init_balance = item['init_balance']
        now_balance = item['now_balance']
        options_raw = item['options_raw']

        # 只处理指定交易所和指定品种的数据
        if exchange == target_exchange and item_symbol == symbol:
            if symbol_data is None:
                symbol_data = {
                    'symbol': symbol,
                    'number': 0,
                    'exchange': exchange,
                    'options_raw': options_raw,
                    'total_profit': 0,
                    'total_init_balance': 0,
                    'total_now_balance': 0
                }

            symbol_data['total_profit'] += now_balance - init_balance
            symbol_data['total_init_balance'] += init_balance
            symbol_data['total_now_balance'] += now_balance
            symbol_data['number'] += 1

    if symbol_data:
        symbol_data['profit_rate'] = calculate_profit_rate(symbol_data['total_now_balance'],
                                                           symbol_data['total_init_balance'])

    return symbol_data


def get_all_symbols_funds_comparison(current_options_list, temp_options_list, target_exchange):
    if current_options_list is None or temp_options_list is None:
        return []

    current_symbols = {
        item['options'].get('symbol')
        for item in current_options_list
        if item['options'].get('exchange') == target_exchange
    }
    temp_symbols = {
        item['options'].get('symbol')
        for item in temp_options_list
        if item['options'].get('exchange') == target_exchange
    }
    all_symbols = current_symbols & temp_symbols
    symbols_comparison = []

    for symbol in all_symbols:
        current_robots = [
            item for item in current_options_list
            if item['options'].get('symbol') == symbol
            and item['options'].get('exchange') == target_exchange
        ]
        temp_robots = [
            item for item in temp_options_list
            if item['options'].get('symbol') == symbol
            and item['options'].get('exchange') == target_exchange
        ]

        if not current_robots or not temp_robots:
            continue

        current_data = {
            'symbol': symbol,
            'exchange': target_exchange,
            'number': len(current_robots),
            'total_init_balance': 0,
            'total_now_balance': 0,
            'total_profit': 0,
            'robot_ids': [item['id'] for item in current_robots],
            'options_raw': current_robots[0]['options_raw'] if current_robots else None,
        }
        temp_data = {
            'symbol': symbol,
            'exchange': target_exchange,
            'number': len(temp_robots),
            'total_init_balance': 0,
            'total_now_balance': 0,
            'total_profit': 0,
            'robot_ids': [item['id'] for item in temp_robots],
        }

        for current_robot in current_robots:
            current_data['total_init_balance'] += current_robot['init_balance']
            current_data['total_now_balance'] += current_robot['now_balance']
            current_data['total_profit'] += current_robot['now_balance'] - current_robot['init_balance']

        for temp_robot in temp_robots:
            temp_data['total_init_balance'] += temp_robot['init_balance']
            temp_data['total_now_balance'] += temp_robot['now_balance']
            temp_data['total_profit'] += temp_robot['now_balance'] - temp_robot['init_balance']

        current_data['profit_rate'] = calculate_profit_rate(
            current_data['total_now_balance'],
            current_data['total_init_balance'],
        )

        if temp_data['total_now_balance'] > 0:
            change_percentage = (
                (current_data['total_now_balance'] - temp_data['total_now_balance'])
                / temp_data['total_now_balance']
                * 100
            )
        else:
            change_percentage = 0

        symbols_comparison.append({
            'symbol': symbol,
            'current': current_data,
            'temp': temp_data,
            'change_percentage': round(change_percentage, 2),
        })

    return sorted(symbols_comparison, key=lambda x: x['change_percentage'], reverse=True)


def xbt_group_and_count(options_list2, target_exchange, XBTRobotNum):
    # 使用字典跟踪每个 symbol 的累计值
    symbol_totals = {}

    for item in options_list2:
        symbol = item['symbol']
        exchange = item['exchange']
        margin_equity = item['margin_equity']
        pnl = item['pnl']
        daily_return_1h = item['daily_return_1h']

        if exchange == target_exchange:
            if symbol not in symbol_totals:
                symbol_totals[symbol] = {
                    'symbol': symbol,
                    'number': 0,
                    'exchange': exchange,
                    'total_profit': 0,
                    'total_margin_equity': 0,
                    'total_pnl': 0,
                    'daily_return_1h': 0
                }

            # symbol_totals[symbol]['total_profit'] += now_balance - init_balance
            symbol_totals[symbol]['total_margin_equity'] += margin_equity
            symbol_totals[symbol]['total_pnl'] += pnl
            symbol_totals[symbol]['daily_return_1h'] += daily_return_1h
            symbol_totals[symbol]['number'] += 1

    # 计算每个 symbol 的收益率并生成最终结果列表
    grouped_data = [{
        'symbol': symbol,
        'number': data['number'],
        'exchange': data['exchange'],
        'total_margin_equity': data['total_margin_equity'],
        'total_pnl': data['total_pnl'],
        'total_pnl_rate': xbt_calculate_rate(xbt_calculate_rate(data['total_pnl'], data['total_margin_equity']),
                                             data['number']),
        'total_daily_1h_rate': xbt_calculate_rate(data['daily_return_1h'], data['number'])
    } for symbol, data in symbol_totals.items()]

    filtered_data = [
        {
            'symbol': item['symbol'],
            'number': item['number'],
            'exchange': item['exchange'],
            'total_margin_equity': item['total_margin_equity'],
            'total_pnl': item['total_pnl'],
            'total_pnl_rate': item['total_pnl_rate'],
            'total_daily_1h_rate': item['total_daily_1h_rate']
        }
        for item in grouped_data
        if item['number'] >= XBTRobotNum and item['total_daily_1h_rate'] > 0  # XBTRobotNum 为机器人数量阈值，可调整
    ]

    return filtered_data


def xbt_calculate_rate(total, number):
    if total is not None and number is not None and total != 0 and number != 0:
        return round((total / number), 2)
    else:
        return 0.0  # or any default value you prefer


def xbt_data_merge(daily_sort_data, pnl_sort_data):
    merged_data_dict = {item['symbol']: item for item in daily_sort_data}

    for item in pnl_sort_data:
        symbol = item['symbol']
        if symbol in merged_data_dict:
            merged_data_dict[symbol].update(item)
        else:
            merged_data_dict[symbol] = item

    merged_data = list(merged_data_dict.values())
    return merged_data


def update_stop_loss_list(stop_loss_List, id_to_update):
    # Check if id_to_update exists in stop_loss_List
    for item in stop_loss_List:
        if item["id"] == id_to_update:
            # If id exists, increment stop_loss_count by 1
            item["stop_loss_count"] += 1
            break
    else:
        if not stop_loss_List:
            stop_loss_List = []
        # If id doesn't exist, add a new entry
        stop_loss_List.append({"id": id_to_update, "stop_loss_count": 1, "stop_time": datetime.now()})

    return stop_loss_List


def get_stop_loss_count(stop_loss_List, target_id):
    for item in stop_loss_List:
        if item["id"] == target_id:
            return item["stop_loss_count"]
    else:
        # 未找到目标 id 时返回默认值
        return 0

def get_stop_time(stop_loss_List, target_id):
    for item in stop_loss_List:
        if item["id"] == target_id:
            return item["stop_time"]
    else:
        # 未找到目标 id 时返回默认值
        return ""


def update_symbol_list(top_symbol_List, symbol):
    # 如果 symbol 已存在则更新时间，否则追加新记录
    for item in top_symbol_List:
        if item["symbol"] == symbol:
            item["add_time"] = datetime.now()
            break
    else:
        if not top_symbol_List:
            top_symbol_List = []
        # symbol 不存在时追加新记录
        top_symbol_List.append({"symbol": symbol, "add_time": datetime.now()})

    return top_symbol_List


def get_addSymbol_time(top_symbol_List, symbol):
    for item in top_symbol_List:
        if item["symbol"] == symbol:
            return item["add_time"]
    else:
        # 未找到目标 symbol 时返回默认值
        return ""


def normalize_symbol(symbol):
    # 1. 移除 _UMCBL
    s = symbol.split('_')[0].lower().replace('$', '')

    if s.endswith('usdt'):
        if len(s) > 4 and not s.endswith('_usdt'):
            s = s[:-4] + '_usdt'
    else:
        s = s + '_usdt'

    return s


def remove_addSymbol_by_id(top_symbol_List, symbol):
    for item in top_symbol_List:
        if item["symbol"] == symbol:
            top_symbol_List.remove(item)
            break
    return top_symbol_List


def is_time_difference_less_than_one_hour_fifteen_minutes(s_minutes, time1, time2):
    # 计算时间差
    time_difference = abs(time1 - time2)

    # 定义时间阈值
    threshold = timedelta(minutes=s_minutes)

    # 检查时间差是否小于阈值
    return time_difference < threshold


def remove_entry_by_id(stop_loss_List, target_id):
    for item in stop_loss_List:
        if item["id"] == target_id:
            stop_loss_List.remove(item)
            break
    return stop_loss_List


def count_threads_by_name(thread_name):
    return sum(1 for thread in threading.enumerate() if thread.name == thread_name)


def cancel_thread_by_name(thread_name):
    # 获取当前所有活跃线程
    for thread in threading.enumerate():
        if thread.getName() == thread_name:
            if hasattr(thread, "cancel"):
                thread.cancel()


def getCurrenTime(RunningExchange="main"):
    current_time = datetime.now()
    formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
    return formatted_time + "：【" + RunningExchange + "】 "



