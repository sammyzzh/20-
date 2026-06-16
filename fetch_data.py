#!/usr/bin/env python3
"""
强势板块选股系统 - 数据抓取脚本
每日收盘后（15:40）由 GitHub Actions 自动触发
数据来源：同花顺公开接口（无需账号）
"""

import requests
import re
import json
import numpy as np
from datetime import datetime, date, timedelta
from bs4 import BeautifulSoup
import time

# ─────────────────────────────────────────────
# 六大板块配置
# name:       展示名称
# ths_code:   同花顺概念板块代码（用于抓成分股）
# ths_index:  同花顺板块指数名称（用于拉板块指数K线，必须完全匹配）
# ─────────────────────────────────────────────
SECTORS = [
    {"name": "小金属",    "ths_code": "300809", "ths_index": "小金属概念"},
    {"name": "算力",      "ths_code": "308828", "ths_index": "东数西算(算力)"},
    {"name": "芯片",      "ths_code": "301085", "ths_index": "芯片概念"},
    {"name": "AI应用",    "ths_code": "309264", "ths_index": "AI应用"},
    {"name": "人形机器人", "ths_code": "309119", "ths_index": "人形机器人"},
    {"name": "商业航天",  "ths_code": "309130", "ths_index": "商业航天"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122",
    "Referer": "https://q.10jqka.com.cn/",
}


# ─────────────────────────────────────────────
# 1. 抓取板块成分股
# ─────────────────────────────────────────────
def fetch_sector_stocks(ths_code):
    stocks = []
    for page in range(1, 10):
        url = f"https://q.10jqka.com.cn/gn/detail/code/{ths_code}/page/{page}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            soup = BeautifulSoup(r.content, "html.parser", from_encoding="gbk")
            table = soup.find("table")
            if not table:
                break
            rows = table.find_all("tr")
            found = 0
            for row in rows[1:]:
                cols = row.find_all("td")
                if len(cols) >= 3:
                    code = cols[1].get_text(strip=True)
                    name = cols[2].get_text(strip=True)
                    if re.match(r"^\d{6}$", code):
                        stocks.append({"code": code, "name": name})
                        found += 1
            next_btn = soup.find("a", string=re.compile("下一页|›|>"))
            if not next_btn or found == 0:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"  抓取成分股失败 page={page}: {e}")
            break
    return stocks


# ─────────────────────────────────────────────
# 2. 抓取板块指数日线
# ─────────────────────────────────────────────
def fetch_sector_index(ths_index_name):
    try:
        import akshare as ak
        today = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=120)).strftime("%Y%m%d")
        df = ak.stock_board_concept_index_ths(
            symbol=ths_index_name, start_date=start, end_date=today
        )
        if df is None or len(df) < 22:
            return []
        return df["收盘价"].tolist()
    except Exception as e:
        print(f"  板块指数抓取失败({ths_index_name}): {e}")
        return []


# ─────────────────────────────────────────────
# 3. 抓取个股日线K线
# ─────────────────────────────────────────────
def fetch_stock_kline(code, n=60):
    url = f"https://d.10jqka.com.cn/v6/line/hs_{code}/01/last{n}.js"
    try:
        r = requests.get(url, headers={
            "Referer": "https://stockpage.10jqka.com.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }, timeout=10)
        data_match = re.search(r'"data":"([^"]+)"', r.text)
        if not data_match:
            return []
        rows = []
        for item in data_match.group(1).split(";"):
            parts = item.split(",")
            if len(parts) >= 5:
                try:
                    rows.append({"date": parts[0], "close": float(parts[4])})
                except:
                    pass
        return rows
    except:
        return []


# ─────────────────────────────────────────────
# 4. 计算20日均线状态
# ─────────────────────────────────────────────
def classify_stock(kline_rows):
    if len(kline_rows) < 22:
        return None

    closes = [r["close"] for r in kline_rows]

    def day_state(c, ma):
        if c > ma * 1.03:   return "above"
        if c < ma * 0.97:   return "below"
        return "tangle"

    # 计算最近30天每天状态
    history = []
    for i in range(max(20, len(closes) - 30), len(closes)):
        ma = float(np.mean(closes[i-20:i]))
        history.append(day_state(closes[i], ma))

    if not history:
        return None

    ma20_today = float(np.mean(closes[-20:]))
    close_today = closes[-1]
    deviation = round((close_today - ma20_today) / ma20_today * 100, 2)

    # 连续跌破/站上天数
    below_streak = sum(1 for _ in iter(lambda: history and history.pop() == "below" or False, False))
    history2 = []
    for i in range(max(20, len(closes) - 30), len(closes)):
        ma = float(np.mean(closes[i-20:i]))
        history2.append(day_state(closes[i], ma))

    below_streak = 0
    for s in reversed(history2):
        if s == "below": below_streak += 1
        else: break

    above_streak = 0
    for s in reversed(history2):
        if s == "above": above_streak += 1
        else: break

    today_state = history2[-1]
    prev_state  = history2[-2] if len(history2) >= 2 else today_state

    if below_streak >= 3:
        status = "excluded"
    elif today_state == "below":
        status = f"below_d{min(below_streak, 2)}"
    elif today_state == "tangle":
        status = "tangle"
    elif today_state == "above":
        if prev_state in ("below", "tangle") and above_streak == 1:
            status = "breakout_up"
        else:
            status = "above"
    else:
        status = "unknown"

    return {
        "status": status,
        "ma20": round(ma20_today, 3),
        "close": round(close_today, 3),
        "deviation": deviation,
        "above_streak": above_streak,
        "below_streak": below_streak,
    }


# ─────────────────────────────────────────────
# 5. 乖离率警报
# ─────────────────────────────────────────────
def deviation_alert(deviation):
    if abs(deviation) >= 50: return "strong"
    if abs(deviation) >= 30: return "warn"
    return None


# ─────────────────────────────────────────────
# 6. 主流程
# ─────────────────────────────────────────────
def main():
    print(f"=== 强势选股系统 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "trade_date": date.today().strftime("%Y-%m-%d"),
        "sectors": [],
        "deviation_alerts": [],
    }

    for sector in SECTORS:
        print(f"\n── {sector['name']} ──")

        # 板块指数
        sector_closes = fetch_sector_index(sector["ths_index"])
        if len(sector_closes) >= 20:
            ma20 = float(np.mean(sector_closes[-20:]))
            close = sector_closes[-1]
            dev = (close - ma20) / ma20 * 100
            sector_status = "bullish" if close > ma20 * 1.03 else ("bearish" if close < ma20 * 0.97 else "tangle")
            sector_ma20, sector_close, sector_dev = round(ma20, 2), round(close, 2), round(dev, 2)
        else:
            sector_status, sector_ma20, sector_close, sector_dev = "unknown", None, None, None
        print(f"  板块状态: {sector_status}  收盘={sector_close}  MA20={sector_ma20}")

        # 成分股
        stocks_raw = fetch_sector_stocks(sector["ths_code"])
        print(f"  成分股 {len(stocks_raw)} 只，计算均线...")

        stocks_result = []
        for i, s in enumerate(stocks_raw):
            kline = fetch_stock_kline(s["code"])
            result = classify_stock(kline)
            if result is None:
                continue
            alert = deviation_alert(result["deviation"])
            stock_data = {"code": s["code"], "name": s["name"], **result, "deviation_alert": alert}
            stocks_result.append(stock_data)
            if alert:
                output["deviation_alerts"].append({
                    "sector": sector["name"], "code": s["code"], "name": s["name"],
                    "deviation": result["deviation"], "alert": alert,
                    "close": result["close"], "ma20": result["ma20"],
                })
            if (i + 1) % 10 == 0:
                print(f"    {i+1}/{len(stocks_raw)}")
            time.sleep(0.08)

        sc = {}
        for s in stocks_result:
            sc[s["status"]] = sc.get(s["status"], 0) + 1

        output["sectors"].append({
            "name": sector["name"], "ths_code": sector["ths_code"],
            "status": sector_status, "ma20": sector_ma20,
            "close": sector_close, "deviation": sector_dev,
            "stock_count": len(stocks_result), "status_counts": sc,
            "stocks": stocks_result,
        })
        print(f"  状态分布: {sc}")

    output["deviation_alerts"].sort(key=lambda x: abs(x["deviation"]), reverse=True)

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成  板块:{len(output['sectors'])}  乖离预警:{len(output['deviation_alerts'])}")


if __name__ == "__main__":
    main()
