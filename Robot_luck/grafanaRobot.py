import json
import time

import requests

grafana_session = requests.session()
grafana_cookies = ""
jwt_token = ""


def GrafanaLogin(username, password):
    try:
        print(f"执行 user {username} password {password}")
        url = "https://oksdh.com/grafana/login"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
        }
        payload = {
            "user": username,
            "password": password,
        }
        response = grafana_session.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code == 200:
            response_dict = json.loads(response.text)
            global grafana_cookies
            grafana_cookies = response.cookies.items()[0][1]
            global jwt_token
            jwt_token = response_dict.get("data", {}).get("token")
            print("登录成功，cookies:", grafana_cookies)
            return 0

        print("登录失败，状态码:", response.status_code)
        return -1
    except Exception:
        print("error")
        return -1


def getGrafanaRobotPair():
    try:
        url = "https://oksdh.com/grafana/api/datasources/proxy/1/query?db=accounts&epoch=ms"
        post_header = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Cookie": "grafana_session=" + grafana_cookies,
        }
        post_parameters = {
            "db": "accounts",
            "epoch": "ms",
            "q": 'SHOW TAG VALUES WITH KEY="pair"',
        }

        response = grafana_session.post(url, headers=post_header, params=post_parameters)
        if response.status_code != 200:
            print("执行失败")
            return []

        data = json.loads(response.content)
        for result in data.get("results", []):
            for series in result.get("series", []):
                return series.get("values", [])
        return []
    except Exception:
        print("error")
        return []


def getGrafanaRobotParameter(grafanahour, pair):
    try:
        url = "https://oksdh.com/grafana/api/datasources/proxy/1/query?db=accounts&epoch=ms"
        post_header = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Cookie": "grafana_session=" + grafana_cookies,
        }
        result_string = ""
        for value in pair:
            if value[1].count("_") == 2 and "@" not in value[1] and "$" not in value[1]:
                result_string += value[1] + "|"

        result_string = "\\" + result_string[:-1]
        if result_string == "\\":
            return None

        post_parameters = {
            "db": "accounts",
            "epoch": "ms",
            "q": 'SELECT cumulative_sum("profit_1min") FROM "User" WHERE  ("pair" =~ /^('
            + result_string
            + ')$/) AND time >= now() - '
            + grafanahour
            + ' and time <= now() GROUP BY "pair"',
        }

        response = grafana_session.post(url, headers=post_header, params=post_parameters)
        if response.status_code != 200:
            print(response.content)
            return None

        data = json.loads(response.content)
        for result in data.get("results", []):
            return result.get("series")
        return None
    except Exception as error:
        print(error)
        return None


def getGrafanaScores(maker, grafanahour):
    pair_list = getGrafanaRobotPair()
    if not pair_list:
        return {}

    segment_count = min(12, len(pair_list))
    segment_size = len(pair_list) // segment_count
    all_data = []

    for i in range(segment_count):
        start_idx = i * segment_size
        end_idx = (i + 1) * segment_size if i < segment_count - 1 else len(pair_list)
        segment = pair_list[start_idx:end_idx]
        data = getGrafanaRobotParameter(grafanahour, segment)
        if data is not None:
            all_data.extend(data)
        time.sleep(0.5)

    pair_scores = {}
    for value in all_data:
        pair = value["tags"]["pair"]
        if maker.lower() not in pair:
            continue

        values = value.get("values") or []
        if not values:
            continue

        pair_without_prefix = pair.split("_", 1)[-1]
        cumulative_sum = max(values, key=lambda x: x[0])[1]
        pair_scores[pair_without_prefix] = cumulative_sum

    return pair_scores


def getGrafanaTop(maker, grafanahour, topNum):
    pair_list = getGrafanaRobotPair()
    if not pair_list:
        return []

    segment_count = min(12, len(pair_list))
    segment_size = len(pair_list) // segment_count
    all_data = []

    for i in range(segment_count):
        start_idx = i * segment_size
        end_idx = (i + 1) * segment_size if i < segment_count - 1 else len(pair_list)
        segment = pair_list[start_idx:end_idx]
        data = getGrafanaRobotParameter(grafanahour, segment)
        if data is not None:
            all_data.extend(data)
        time.sleep(0.5)

    pair_cumulative_sum = []
    for value in all_data:
        pair = value["tags"]["pair"]
        if maker.lower() not in pair:
            continue

        values = value.get("values") or []
        if not values:
            continue

        pair_without_prefix = pair.split("_", 1)[-1]
        cumulative_sum = max(values, key=lambda x: x[0])[1]
        if cumulative_sum > 50:
            pair_cumulative_sum.append({
                "symbol": pair_without_prefix,
                "cumulative_sum": cumulative_sum,
            })

    sorted_data = sorted(pair_cumulative_sum, key=lambda x: x["cumulative_sum"], reverse=True)[:topNum]
    print(sorted_data)
    return sorted_data

