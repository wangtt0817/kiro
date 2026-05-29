import threading
import requests
import json
import time
import hashlib
import hmac
import utils
from collections import deque
from websocket import create_connection

# 替换为你自己的 API Key 和 Secret Key
api_key = 'bg_140493e2cdc1effd697bafc8c9d4d8b1'
secret_key = '8af2f41a1ca42a2f7655a71895dc0d1d94d76247f7c51b0d76e3ead71604e1c3'


def generate_signature(api_key, secret_key, verb, endpoint, params):
    timestamp = str(int(time.time() * 1000))
    payload = f"{timestamp}{verb}{endpoint}"
    if params:
        payload += json.dumps(params, separators=(',', ':'))
    signature = hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return {
        'Apiid': api_key,
        'Timestamp': timestamp,
        'Sign': signature,
    }


def get_huobi_tickers(contract_code=None, pair=None, business_type=None):
    # 基础URL
    url = "https://api.hbdm.com/linear-swap-api/v1/swap_open_interest"

    # 默认将 contract_type 设置为 swap
    params = {
        'contract_type': 'swap'
    }

    # 根据参数设置其他字段
    if contract_code:
        params['contract_code'] = contract_code
    if pair:
        params['pair'] = pair
    if business_type:
        params['business_type'] = business_type

    try:
        # 发起GET请求
        response = requests.get(url, params=params)
        response.raise_for_status()  # 检查响应是否成功
        data = response.json()  # 获取响应的JSON数据

        # 判断数据是否有效
        if data['status'] == 'ok' and 'data' in data:
            # 生成一个列表，其中每个元素是一个字典，键是 'symbol'，值是处理后的 symbol
            symbol_list = [
                {'symbol': item['symbol'].split('-')[0].lower() + "_usdt"}
                for item in data['data']
            ]
            return symbol_list
        else:
            print("数据获取失败或格式不正确")
            return []

    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        return []


def get_bitget_tickers(maker, usdtVolume):
    # 替换为你的 API Key 和 Secret Key

    # 接口 URL
    if maker == "spot":
        url = "https://api.bitget.com//api/v2/spot/market/tickers"
        # 构造请求参数
        params = {}  # 根据实际需要添加参数

        # 生成签名
        signature_headers = generate_signature(api_key, secret_key, 'GET', '/api/spot/v1/market/tickers', params)
    else:
        url = "https://api.bitget.com/api/v2/mix/market/tickers"
        # 构造请求参数
        params = {"productType": "USDT-FUTURES"}  # 根据实际需要添加参数

        # 生成签名
        signature_headers = generate_signature(api_key, secret_key, 'GET', '/api/v2/mix/market/tickers', params)

    # 构造请求头部
    headers = {
        'Apiid': signature_headers['Apiid'],
        'Timestamp': signature_headers['Timestamp'],
        'Sign': signature_headers['Sign'],
    }

    # 发送 GET 请求
    response = requests.get(url, headers=headers, params=params)

    # 处理 API 响应
    if response.status_code == 200:
        data = response.json()
        # 遍历 data 中的每个元素
        # 取出 usdtVolume 大于 500000 的记录
        if maker == "spot":
            filtered_data = [item for item in data['data'] if float(item['usdtVolume']) > usdtVolume]
            # 按照 chgUtc 从大到小排序
            sorted_data = sorted(filtered_data, key=lambda x: float(x['change24h']), reverse=True)
        else:
            filtered_data = [item for item in data['data'] if float(item['usdtVolume']) > usdtVolume]
            # 按照 chgUtc 从大到小排序
            sorted_data = sorted(filtered_data, key=lambda x: float(x['change24h']), reverse=True)

        return sorted_data
    else:
        print(f"Error: {response.status_code}, {response.text}")


def get_gate_tickers(maker, usdtVolume):
    """获取 Gate.io 交易所可交易列表，按 24H USDT 成交额从高到低排序。"""
    # Gate.io API 接口
    if maker == "spot":
        url = "https://api.gateio.ws/api/v4/spot/tickers"
    else:
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"

    try:
        # 发送 GET 请求
        response = requests.get(url, timeout=8)

        # 处理 API 响应
        if response.status_code == 200:
            data = response.json()

            if maker == "spot":
                # 现货处理：只取 USDT 交易对
                filtered_data = [
                    {
                        'symbol': item['currency_pair'],
                        'change24h': float(item.get('change_percentage', 0)),
                        'usdtVolume': float(item.get('quote_volume', 0))
                    }
                    for item in data
                    if item['currency_pair'].endswith('_USDT') and float(item.get('quote_volume', 0)) > usdtVolume
                ]
            else:
                # 合约处理：contract 字段格式为 "BTC_USDT"，volume_24h_settle 是 USDT 交易量
                filtered_data = []
                for item in data:
                    try:
                        contract = item.get('contract', '')
                        # 只处理 USDT 结算的合约
                        if not contract.endswith('_USDT'):
                            continue

                        # 交易量字段
                        volume = float(
                            item.get('volume_24h_usd')
                            or item.get('volume_24h_quote')
                            or item.get('volume_24h_settle', 0)
                        )
                        if volume <= usdtVolume:
                            continue

                        # 涨跌幅字段
                        change = float(item.get('change_percentage', 0))

                        filtered_data.append({
                            'symbol': contract.lower(),  # 转换为小写，如 btc_usdt
                            'change24h': change,
                            'usdtVolume': volume
                        })
                    except (ValueError, KeyError) as e:
                        continue

            # 按 24H USDT 成交额从大到小排序
            sorted_data = sorted(filtered_data, key=lambda x: x['usdtVolume'], reverse=True)
            return sorted_data
        else:
            print(f"Gate.io API Error: {response.status_code}, {response.text}")
            return []
    except Exception as e:
        print(f"Gate.io API Exception: {e}")
        return []


def _gate_candle_usd_amount(candle):
    if isinstance(candle, dict):
        for key in ('sum', 'quote_volume', 'volume_quote', 'amount'):
            try:
                value = float(candle.get(key, 0))
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue
        try:
            return float(candle.get('v', 0)) * float(candle.get('c', 0))
        except (TypeError, ValueError):
            return 0.0

    if isinstance(candle, (list, tuple)):
        if len(candle) >= 7:
            try:
                value = float(candle[6])
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
        if len(candle) >= 3:
            try:
                return float(candle[1]) * float(candle[2])
            except (TypeError, ValueError):
                return 0.0

    return 0.0


def _normalize_gate_symbol(symbol):
    normalized = str(symbol or "").strip().lower()
    if normalized.endswith("usdt") and "_" not in normalized:
        normalized = normalized[:-4] + "_usdt"
    return normalized


def get_gate_futures_candlesticks(symbol, interval='1m', limit=10):
    url = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
    params = {
        "contract": symbol.upper(),
        "interval": interval,
        "limit": limit,
    }
    try:
        response = requests.get(url, params=params, timeout=8)
        if response.status_code != 200:
            print(f"Gate.io candlesticks API Error: {response.status_code}, {response.text}")
            return []
        return response.json()
    except Exception as e:
        print(f"Gate.io candlesticks API Exception: {symbol}, {e}")
        return []


def get_gate_volume_selection(min_24h_usdt=1000000, min_1m_usdt=8000, required_1m_count=7, limit=10,
                              blacklist=None, min_24h_change=0, hot_min_24h_usdt=10000000,
                              hot_min_1m_usdt=10000, hot_required_1m_count=7):
    blacklist = {_normalize_gate_symbol(item) for item in (blacklist or set())}
    candidates = []
    tickers = get_gate_tickers("swap", min_24h_usdt)
    for ticker in tickers:
        symbol = _normalize_gate_symbol(ticker.get('symbol', ''))
        if not symbol or symbol in blacklist:
            continue
        change24h = float(ticker.get('change24h', 0))
        usdt_volume = float(ticker.get('usdtVolume', 0))
        can_pass_normal = change24h >= min_24h_change
        can_pass_hot = usdt_volume >= hot_min_24h_usdt
        if not can_pass_normal and not can_pass_hot:
            continue
        candles = get_gate_futures_candlesticks(symbol, "1m", limit)
        qualified_1m_count = sum(1 for candle in candles if _gate_candle_usd_amount(candle) >= min_1m_usdt)
        hot_qualified_1m_count = sum(1 for candle in candles if _gate_candle_usd_amount(candle) >= hot_min_1m_usdt)
        passed_normal = can_pass_normal and qualified_1m_count >= required_1m_count
        passed_hot = can_pass_hot and hot_qualified_1m_count >= hot_required_1m_count
        if not passed_normal and not passed_hot:
            continue
        copied = ticker.copy()
        copied['symbol'] = symbol
        copied['cumulative_sum'] = ticker.get('usdtVolume', 0)
        copied['qualified_1m_count'] = qualified_1m_count
        copied['hot_qualified_1m_count'] = hot_qualified_1m_count
        if passed_normal and passed_hot:
            copied['selection_reason'] = 'normal+hot'
        elif passed_hot:
            copied['selection_reason'] = 'hot'
        else:
            copied['selection_reason'] = 'normal'
        candidates.append(copied)
    return candidates


def get_tickers(exchange, maker, usdtVolume):
    """
    通用的获取交易所行情列表函数；Gate 返回按成交额排序的数据。

    参数:
        exchange: 交易所标识，如 'gate_usdt_swap', 'bitget_usdt_swap', 'huobi_usdt_swap' 等
        maker: 市场类型，'spot' 或 'swap'
        usdtVolume: USDT交易量过滤阈值

    返回:
        交易对列表
    """
    # 从 exchange 中提取交易所名称
    exchange_name = exchange.split('_')[0].lower()

    if exchange_name == 'gate':
        return get_gate_tickers(maker, usdtVolume)
    elif exchange_name == 'bitget':
        return get_bitget_tickers(maker, usdtVolume)
    elif exchange_name == 'huobi':
        # Huobi 使用 get_huobi_tickers，但它返回的是符号列表，不是涨幅排行
        # 这里暂时返回空列表或调用 get_bitget_tickers 作为备选
        print(f"Warning: Huobi exchange does not support tickers ranking, using Bitget as fallback")
        return get_bitget_tickers(maker, usdtVolume)
    else:
        # 默认使用 Bitget 作为备选
        print(f"Warning: Unknown exchange '{exchange_name}', using Bitget as fallback")
        return get_bitget_tickers(maker, usdtVolume)


def getRefParmentOfOkSdh(RunningExchange, symbol):
    try:
        base_url = "https://oksdh.com/api/v1/symbol/"

        # 截取 symbol 中 _ 前面的部分
        symbol = re.sub(r'^\d+', '', symbol)
        symbol_part = symbol.split('_')[0]

        # 拼接完整的 URL
        full_url = f"{base_url}{symbol_part}"
        # 进行 GET 请求
        response = requests.get(full_url)

        data = json.loads(response.content)  # 解析 JSON 数据
    except Exception:
        print(f"Error: Failed to decode JSON. Response content: {response.content}")
        return getRefParment(RunningExchange,symbol)
    # 初始化变量用于存储所有符合条件的记录
    matching_pairs = []

    # 提取pair，并进行比较
    for item in data['data']:
        pair = item['pair']
        quote_volume_24h = item.get('quote_volume_24h', 0)

        # 检查pair是否匹配symbol（去掉数字部分的匹配）
        if re.sub(r'^\d+', '', pair) == re.sub(r'^\d+', '', symbol):
            matching_pairs.append(item)

    # 按照 quote_volume_24h 进行排序
    matching_pairs.sort(key=lambda x: x.get('quote_volume_24h', 0), reverse=True)

    # 找到最高和次高的exchange_name和对应的pair
    defaultRefExchange = None
    defaultRefSymbol = None

    if matching_pairs:
        best_exchange_name = matching_pairs[0]['exchange_name']
        best_pair = matching_pairs[0]['pair']
        if best_exchange_name == RunningExchange and len(matching_pairs) > 1:
            defaultRefExchange = matching_pairs[1]['exchange_name']
            defaultRefSymbol = matching_pairs[1]['pair']
        elif best_exchange_name != RunningExchange:
            defaultRefExchange = best_exchange_name
            defaultRefSymbol = best_pair

    if defaultRefExchange is None or defaultRefSymbol is None:
        return getRefParment(RunningExchange, symbol)

    return defaultRefExchange, defaultRefSymbol


def getRefParment(RunningExchange, symbol):
    # 'binance_usdt_swap','kucoin_usdt_swap','huobi_usdt_swap','gate_usdt_swap','coinex_usdt_swap','bybit_usdt_swap','okx_usdt_swap','bitget_usdt_swap'
    # 'binance_spot','bybit_spot','kucoin_spot','gate_spot','huobi_spot','okx_spot','bitget_spot'

    # 默认参考合约，和当前交易品种
    defaultRefExchange = "binance_usdt_swap"
    defaultRefSymbol = symbol
    # 合约参考盘以及参考币种更换
    if RunningExchange == "bitget_usdt_swap":  # 交易盘
        if symbol == "pepe_usdt":  # 交易币种
            return "binance_usdt_swap", "1000pepe_usdt"  # 参考盘，参考币种
        elif symbol == "sats_usdt":
            return "binance_usdt_swap", "1000sats_usdt"
        elif symbol == "10000sats_usdt":
            return "binance_usdt_swap", "1000sats_usdt"
        elif symbol == "1000rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "bonk_usdt":
            return "binance_usdt_swap", "1000bonk_usdt"
        elif symbol == "luna_usdt":
            return "binance_usdt_swap", "luna2_usdt"
        elif symbol == "dodo_usdt":
            return "binance_usdt_swap", "dodox_usdt"
        elif symbol == "omni1_usdt":
            return "binance_usdt_swap", "omni_usdt"
        elif symbol == "beam_usdt":
            return "binance_usdt_swap", "beamx_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        elif symbol == "10000why_usdt":
            return "binance_usdt_swap", "1000why_usdt"
        else:
            return defaultRefExchange, defaultRefSymbol

    elif RunningExchange == "gate_usdt_swap":  # 交易盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "10000sats_usdt":
            return "binance_usdt_swap", "1000sats_usdt"
        elif symbol == "1000rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "bonk_usdt":
            return "binance_usdt_swap", "1000bonk_usdt"
        elif symbol == "luna_usdt":
            return "binance_usdt_swap", "luna2_usdt"
        elif symbol == "dodo_usdt":
            return "binance_usdt_swap", "dodox_usdt"
        elif symbol == "omni1_usdt":
            return "binance_usdt_swap", "omni_usdt"
        elif symbol == "beam_usdt":
            return "binance_usdt_swap", "beamx_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        return defaultRefExchange, defaultRefSymbol

    elif RunningExchange == "kucoin_usdt_swap":  # 交易盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "10000sats_usdt":
            return "binance_usdt_swap", "1000sats_usdt"
        elif symbol == "1000rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "bonk_usdt":
            return "binance_usdt_swap", "1000bonk_usdt"
        elif symbol == "luna_usdt":
            return "binance_usdt_swap", "luna2_usdt"
        elif symbol == "dodo_usdt":
            return "binance_usdt_swap", "dodox_usdt"
        elif symbol == "omni1_usdt":
            return "binance_usdt_swap", "omni_usdt"
        elif symbol == "beam_usdt":
            return "binance_usdt_swap", "beamx_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        return defaultRefExchange, defaultRefSymbol

    elif RunningExchange == "huobi_usdt_swap":  # 交易盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "10000sats_usdt":
            return "binance_usdt_swap", "1000sats_usdt"
        elif symbol == "1000rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "bonk_usdt":
            return "binance_usdt_swap", "1000bonk_usdt"
        elif symbol == "luna_usdt":
            return "binance_usdt_swap", "luna2_usdt"
        elif symbol == "dodo_usdt":
            return "binance_usdt_swap", "dodox_usdt"
        elif symbol == "omni1_usdt":
            return "binance_usdt_swap", "omni_usdt"
        elif symbol == "beam_usdt":
            return "binance_usdt_swap", "beamx_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        return defaultRefExchange, defaultRefSymbol

    elif RunningExchange == "bybit_usdt_swap":  # 交易盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "10000sats_usdt":
            return "binance_usdt_swap", "1000sats_usdt"
        elif symbol == "1000rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "bonk_usdt":
            return "binance_usdt_swap", "1000bonk_usdt"
        elif symbol == "luna_usdt":
            return "binance_usdt_swap", "luna2_usdt"
        elif symbol == "dodo_usdt":
            return "binance_usdt_swap", "dodox_usdt"
        elif symbol == "omni1_usdt":
            return "binance_usdt_swap", "omni_usdt"
        elif symbol == "beam_usdt":
            return "binance_usdt_swap", "beamx_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        return defaultRefExchange, defaultRefSymbol

    # 现货参考盘以及参考币种更换
    elif RunningExchange == "bitget_spot":  # 交易盘
        defaultRefExchange = "binance_usdt_swap"  # 默认参考盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        elif symbol == "neiro_usdt":
            return "binance_usdt_swap", "neiroeth_usdt"
        elif symbol == "why_usdt":
            return "binance_usdt_swap", "1000why_usdt"
        return defaultRefExchange, defaultRefSymbol

    elif RunningExchange == "gate_spot":  # 交易盘
        defaultRefExchange = "binance_usdt_swap"  # 默认参考盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        elif symbol == "neiro_usdt":
            return "binance_usdt_swap", "neiroeth_usdt"
        elif symbol == "why_usdt":
            return "binance_usdt_swap", "1000why_usdt"
        return defaultRefExchange, defaultRefSymbol

    elif RunningExchange == "kucoin_spot":  # 交易盘
        defaultRefExchange = "binance_usdt_swap"  # 默认参考盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        elif symbol == "neiro_usdt":
            return "binance_usdt_swap", "neiroeth_usdt"
        elif symbol == "why_usdt":
            return "binance_usdt_swap", "1000why_usdt"
        return defaultRefExchange, defaultRefSymbol

    elif RunningExchange == "bybit_spot":  # 交易盘
        defaultRefExchange = "binance_usdt_swap"  # 默认参考盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        elif symbol == "neiro_usdt":
            return "binance_usdt_swap", "neiroeth_usdt"
        elif symbol == "why_usdt":
            return "binance_usdt_swap", "1000why_usdt"
        return defaultRefExchange, defaultRefSymbol

    elif RunningExchange == "huobi_spot":  # 交易盘
        defaultRefExchange = "binance_usdt_swap"  # 默认参考盘
        if symbol == "sats_usdt":  # 交易币种
            return "binance_usdt_swap", "1000sats_usdt"  # 参考盘，参考币种
        elif symbol == "rats_usdt":
            return "binance_usdt_swap", "1000rats_usdt"
        elif symbol == "toncoin_usdt":
            return "binance_usdt_swap", "ton_usdt"
        elif symbol == "neiro_usdt":
            return "binance_usdt_swap", "neiroeth_usdt"
        elif symbol == "why_usdt":
            return "binance_usdt_swap", "1000why_usdt"
        return defaultRefExchange, defaultRefSymbol

    return defaultRefExchange, defaultRefSymbol


class BinancePriceMonitor:
    def __init__(self, symbols_and_thresholds, logger, callback):
        self.symbols_to_monitor = list(symbols_and_thresholds.keys())
        self.thresholds = symbols_and_thresholds
        self.callback = callback
        self.ws = None
        # 使用复制方式初始化，确保每个symbol有独立的数据结构
        self.price_changes_windows = {symbol.lower(): {'data': [], 'volume_data': [], 'last_timestamp': 0}.copy() for
                                      symbol in
                                      self.symbols_to_monitor}
        self.start_time = time.time()
        self.last_timestamp = time.time()
        self.last_print_time = time.time()  # 上次打印时间
        self.logger = logger
        self.running = True  # 用于停止监视线程的事件标志

    def start_monitoring(self, ref_marker, running_marker):
        if 'swap' in ref_marker:
            symbols_str = '/'.join(symbol.lower() + '@aggTrade' for symbol in self.symbols_to_monitor)
            url = f"wss://fstream.binance.com/stream?streams={symbols_str}"  # 合约订阅地址
            print(f" 开始监控合约 {', '.join(map(str, self.symbols_to_monitor))} 价格及交易量变化...{url}")
        elif 'spot' in ref_marker:
            symbols_str = '/'.join(symbol.lower() + '@aggTrade' for symbol in self.symbols_to_monitor)
            url = f"wss://stream.binance.com:9443/stream?streams={symbols_str}"  # 现货订阅地址
            print(f" 开始监控现货 {', '.join(map(str, self.symbols_to_monitor))} 价格及交易量变化...{url}")
        else:
            # 处理未知的 marker
            print(f" 未知订阅错误")
            return

        self.ws = create_connection(url)

        # 启动一个线程负责定时打印和存储波动率
        print_thread = threading.Thread(target=self.print_and_log_fluctuations, daemon=True,
                                        args=(ref_marker, running_marker,))
        print_thread.start()

        while self.running:
            try:
                data = json.loads(self.ws.recv())
                if data is None:
                    continue
                current_price = float(data['data']['p'])  # 获取当前最新价格
                symbol = data['data']['s'].lower()  # 获取交易对
                current_volume = float(data['data']['q'])  # 获取当前最新交易量

                # 将当前价格变化加入时间窗口
                timestamp = int(data['data']['T'] / 1000)  # 将毫秒级时间戳转换为秒

                # 判断累计1秒内所有符号的价格下跌是否都超过阈值
                all_symbols_below_threshold = all(
                    (len(window['data']) > 1) and (
                            (window['data'][-1] - window['data'][0]) / window['data'][0] * 100 <= float(
                        self.thresholds[symbol])
                    )
                    for symbol, window in self.price_changes_windows.items()
                )

                # 历史逐项调试输出已废弃
                #                        content += f"【{symbol}】 涨跌率: {price_change_percent}%  交易量: {total_volume}"
                #                msg = utils.getCurrenTime() + f">>>>>>当前两条数据之间:{content}<<<<<<"
                #                self.logger.log_info(msg)

                if all_symbols_below_threshold:
                    self.callback(symbol)
                    # 重置每个符号的价格和交易量数据
                    for symbol in self.symbols_to_monitor:
                        self.price_changes_windows[symbol.lower()]['data'] = []
                        self.price_changes_windows[symbol.lower()]['volume_data'] = []

                # 如果时间戳是当前秒，则将数据添加到对应的列表
                if timestamp == self.price_changes_windows[symbol]['last_timestamp']:
                    self.price_changes_windows[symbol]['data'].append(current_price)
                    self.price_changes_windows[symbol]['volume_data'].append(current_volume)
                else:
                    # 如果不是当前秒，使用新的数据替换对应的列表
                    self.price_changes_windows[symbol]['data'] = [current_price]
                    self.price_changes_windows[symbol]['volume_data'] = [current_volume]
                    # 更新最后收到数据的时间戳
                    self.price_changes_windows[symbol]['last_timestamp'] = timestamp

            except Exception as e:
                print(f"!!!!!!!!!!websocket 连接 Error: {e}")
                # 如果出现异常，等待一段时间后重新连接
                time.sleep(1)
                self.ws = create_connection(url)

    def print_and_log_fluctuations(self, ref_marker, running_marker):
        while self.running:
            try:
                content = ""
                for symbol, window in self.price_changes_windows.items():
                    data_list = window['data']
                    volume_list = window['volume_data']

                    if data_list:  # Check if the list is not empty
                        price_change_percent = (data_list[-1] - data_list[0]) / data_list[0] * 100
                        price_change_percent = "{:.4f}".format(price_change_percent)
                        total_volume = "{:.2f}".format(sum(volume_list))
                        content += f"【{symbol}】 涨跌率: {price_change_percent}%  交易量: {total_volume}"
                    else:
                        content += f"【{symbol}】 涨跌率: {0}%  交易量: 0 "
                msg = utils.getCurrenTime(running_marker) + f">>>>>>当前{content}<<<<<<"

                if time.time() - self.last_print_time >= 5 * 60:  # $ X * 60秒 = X 分钟，打印一次波动率。
                    self.last_print_time = time.time()
                    print("【实时监控】" + msg)
                    self.logger.log_info(msg)

                time.sleep(1)  # 休眠1秒钟，避免频繁检查

            except Exception as e:
                print(f"Error in print_and_log_fluctuations: {e}")

    def stop_monitoring(self):
        self.running = False  # 设置停止事件标志
        if self.ws:
            self.ws.close()
