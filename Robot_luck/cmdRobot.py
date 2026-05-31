import json
import threading
import time
from datetime import datetime, timedelta

import requests

session = requests.session()
cookies = ""
jwt_token = ""
api_lock = threading.RLock()

# relase_url = "https://okhds.com"
relase_url = "https://oksdh.com"


def login(username, password):
    try:
        print(f"执行 user {username} password {password}")
        url = relase_url + "/api/v1/user/login"
        with api_lock:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                "Content-Type": "application/json",
            }
            payload = {
                "username": username,
                "password": password,
            }
            response = session.post(url, headers=headers, data=json.dumps(payload))
            response_dict = json.loads(response.text)

            global cookies
            cookies = response.cookies
            global jwt_token
            jwt_token = response_dict.get("data", {}).get("token")
            cookies["crypto_quant_token"] = jwt_token
    except Exception:
        print("登录：error")
        time.sleep(1)


def getRobotParameter(username, max_attempts=5):
    try:
        url = relase_url + "/api/v1/robots?order=asc&order_by=id&user_name=" + username
        attempts = 0
        while attempts < max_attempts:
            with api_lock:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + jwt_token,
                }
                session.cookies = cookies
                response = session.get(url, headers=headers)
            if response.status_code == 200:
                data = json.loads(response.content)
                robotList = data.get("data", [])
                return [robot for robot in robotList if "UTA" not in robot["nick_name"]]

            print(f"获取机器人参数失败，尝试重新获取...（第 {attempts + 1} 次尝试）")
            attempts += 1
            time.sleep(1)

        print(f"尝试获取机器人参数失败 {max_attempts} 次，不再尝试")
        return None
    except Exception:
        print("!!!!!获取机器人参数：error!!!!!!")
        return None


def start(item, max_attempts=3):
    try:
        url = relase_url + f"/api/v1/robots/{item['id']}/start"
        attempts = 0
        while attempts < max_attempts:
            time.sleep(0.2)
            with api_lock:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + jwt_token,
                }
                session.cookies = cookies
                response = session.post(url, headers=headers)
            if response.status_code == 200:
                print(f"{item['account_nick_name']} 开启机器人执行成功")
                break

            print(f"{item['account_nick_name']} 开启机器人执行失败，尝试重新开启..")
            attempts += 1
            time.sleep(1)

        if attempts == max_attempts:
            print(f"{item['account_nick_name']} 尝试开启机器人执行失败 {max_attempts} 次，不再尝试")
    except Exception:
        print(f"{item['account_nick_name']} start error")


def stop(item, max_attempts=3):
    try:
        url = relase_url + f"/api/v1/robots/{item['id']}/stop"
        attempts = 0
        while attempts < max_attempts:
            with api_lock:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + jwt_token,
                }
                session.cookies = cookies
                response = session.post(url, headers=headers)
            if response.status_code == 200:
                print(f"{item['account_nick_name']} 停止机器人执行成功")
                break

            print(f"{item['account_nick_name']} 停止机器人执行失败，尝试重新停止...")
            attempts += 1
            time.sleep(0.1)

        if attempts == max_attempts:
            print(f"{item['account_nick_name']} 尝试停止机器人执行失败 {max_attempts} 次，不再尝试")
    except Exception:
        print(f"{item['account_nick_name']} 停止：error")


def modify_robot(item):
    try:
        url = relase_url + f"/api/v1/robots/{item['id']}"
        with api_lock:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + jwt_token,
            }
            session.cookies = cookies
            session.post(url, headers=headers, json=item)
        print(f"{item.get('account_nick_name')} 自动复制参数成功..")
    except Exception:
        print("modify_robot 失败，请检查")


def stopall(type, max_attempts=3):
    try:
        if type == 1:
            other_url = relase_url + "/api/v1/robots/stopall"
        elif type == 2:
            other_url = relase_url + "/api/v1/robots/startall"
        else:
            return

        attempts = 0
        while attempts < max_attempts:
            with api_lock:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + jwt_token,
                }
                session.cookies = cookies
                response = session.post(other_url, headers=headers)
            if response.status_code == 200:
                print("全部停机执行成功")
                break

            print("全部停机执行失败，尝试重新执行..")
            attempts += 1
            time.sleep(0.1)

        if attempts == max_attempts:
            print(f"尝试全部停机失败 {max_attempts} 次，不再尝试")
    except Exception:
        print("全部停机 error")


def resetbalance_robot(item):
    try:
        url = relase_url + "/api/v1/robots/resetbalance"
        with api_lock:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + jwt_token,
            }
            payload = [{"id": item["id"]}]
            session.cookies = cookies
            session.post(url, headers=headers, data=json.dumps(payload))
    except Exception:
        print(f"{item['account_nick_name']} 重置失败，请检查")


def sendfeishu(msg, webhook_url):
    try:
        message = {
            "msg_type": "text",
            "content": {"text": msg},
        }
        message_json = json.dumps(message)
        response = requests.post(webhook_url, data=message_json, headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            print("消息已成功发送到飞书")
        else:
            print("消息发送失败，HTTP状态码:", response.status_code)
    except Exception:
        print("飞书消息发送异常")


def sendDiscordMsg(msg, webhook_url):
    current_time = datetime.now()
    new_time = current_time + timedelta(hours=8)
    time_string = new_time.strftime("%Y-%m-%d %H:%M:%S")
    message_data = {
        "content": time_string + "  [" + msg + "]",
        "username": "推送助手",
    }
    requests.post(webhook_url, json=message_data)
