import threading
import time
import traceback
import bgExchang
import cmdRobot
import utils
#import sys  # 确保这行代码在你尝试导入 websocket 模块之前执行。
#sys.path.append('/home/ubuntu/.local/lib/python3.8/site-packages')  #这将手动将 websocket-client 包的路径添加到 Python 解释器的搜索路径中，使其能够找到正确安装的包。添加路径后，重新运行你的 Python 脚本，看看问题是否得到解决。


# 用户名和密码
taskUsername = ""
taskPassword = ""
# xbtusdt.com的用户名和密码
xbtUsername = ""
xbtPassword = ""
# grafana用户名和密码
grafanaUsername = ""
grafanaPassword = ""

# $飞书机器人推送的Webhook URL
FeiShuWebhook_url = ""
# $Discord机器人推送的Webhook URL
DiscordWebhook_url = ""
logger = utils.CustomLogger()

# 在方法外部定义一个变量，用于存储累积的消息
accumulated_messages = ""
last_send_time = time.time()
timer_thread = None  # 定义一个全局变量用于存储定时器对象
filtered_data = {}
RunningExchange = 'main'
filtered_data = {}


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


def safe_get_robot_parameter():
    return normalize_robot_list(cmdRobot.getRobotParameter(taskUsername))


def log_exception(context):
    msg = utils.getCurrenTime(RunningExchange) + f"{context} 异常:\n{traceback.format_exc()}"
    print(msg)
    logger.log_info(msg)


def guarded_gateswap_run_strategy():
    try:
        gateswap.run_strategy()
    except Exception:
        log_exception("gateswap.run_strategy")


def guarded_kucoinswap_run_strategy():
    try:
        kucoinswap.run_strategy()
    except Exception:
        log_exception("kucoinswap.run_strategy")


def price_change_callback(symbol):
    global timer_thread
    global filtered_data
    msg = utils.getCurrenTime(
        RunningExchange) + f"【警告!警告!警告!】当前BTC价格波动大，执行账号【!!!全部停机!!!】操作.修改对应参数/重置收益/停止成功\n"
    print(msg)
    logger.log_info(msg)
    SendSmg(msg)
    utils.set_running_status(False)

    cmdRobot.login(taskUsername, taskPassword)
    taskOptions_list = safe_get_robot_parameter()
    if not filtered_data:
        if taskOptions_list is None:
            msg = utils.getCurrenTime(
                RunningExchange) + "[---警告---] 全局停机前获取运行中机器人列表失败，本次无法缓存恢复列表。"
            print(msg)
            logger.log_info(msg)
            SendSmg(msg)
        else:
            filtered_data = [item for item in taskOptions_list if item['status'] == 2]
    cmdRobot.stopall(1)
    # 创建一个线程，在回调后30分钟开启之前停止波动的机器人
    # 取消之前已经创建的定时器
    if timer_thread and timer_thread.is_alive():
        timer_thread.cancel()

    # 创建一个新的定时器，在回调后60分钟启动 callbackStartRobot
    timer_thread = threading.Timer(97 * 60, callbackStartRobot, args=(filtered_data,))  # $等待 59 * 60秒 == 59分钟，后全部开机。
    timer_thread.daemon = True
    timer_thread.start()


def callbackStartRobot(filtered_dataTemp):
    global filtered_data
    msg = utils.getCurrenTime(RunningExchange) + f"等待97分钟后，执行恢复【///全部开机///】操作."
    logger.log_info(msg)
    print(msg)
    SendSmg(msg)
    for robot in normalize_robot_list(filtered_dataTemp):
        gateswap.record_auto_start(robot, "main_resume_all")
        kucoinswap.record_auto_start(robot, "main_resume_all")
        cmdRobot.start(robot)
    utils.set_running_status(True)
    filtered_data = {}


def SendSmg(msg):
    global accumulated_messages, last_send_time

    # 将新的消息追加到累积的消息中
    accumulated_messages += msg

    # 检查是否满足发送条件（消息不为空且距离上次发送超过10秒）
    if accumulated_messages and time.time() - last_send_time >= 10:
        # 发送消息的操作，这里使用 print 代替实际发送消息的操作
        if DiscordWebhook_url != "":
            cmdRobot.sendDiscordMsg(msg, DiscordWebhook_url)

        # 重置累积的消息和发送时间
        accumulated_messages = ""
        last_send_time = time.time()


import bitgetswap
import bitgetspot
import kucoinswap
import huobiswap
import gateswap
#import kucoinspot
#import gatespot

if __name__ == '__main__':
    # $监控Binance BTC实时行情数据，价格波动检测提醒-0.05，可修改BTC的波动率幅度，以进行全体关机。-0.5代表跌0.5% （一定要小写）。
    symbols_and_thresholds = {'btcusdt': '-0.79'}
    monitor = bgExchang.BinancePriceMonitor(symbols_and_thresholds, logger, callback=price_change_callback)
    # 启动监控线程
    monitor_thread = threading.Thread(target=monitor.start_monitoring, args=('spot', RunningExchange,))
    monitor_thread.start()

    cmdRobot.login(taskUsername, taskPassword)
    # 启动添加某一个交易所
    # threading.Thread(target=bitgetswap.run_strategy, daemon=True).start()
    # 启动添加某一个交易所
    # threading.Thread(target=bitgetspot.run_strategy, daemon=True).start()
    # 启动添加某一个交易所
    threading.Thread(target=guarded_kucoinswap_run_strategy, daemon=True).start()
    # 启动添加某一个交易所
    # threading.Thread(target=huobiswap.run_strategy, daemon=True).start()
    # 启动添加某一个交易所
    # threading.Thread(target=gatespot.run_strategy, daemon=True).start()
    threading.Thread(target=guarded_gateswap_run_strategy, daemon=True).start()

    while True:
        # 等待10秒
        time.sleep(10)
