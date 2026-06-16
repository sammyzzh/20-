#!/usr/bin/env python3
"""
强势板块选股系统 - 数据抓取脚本
每日收盘后（16:10）由 GitHub Actions 自动触发
数据来源：新浪财经（K线）+ 同花顺（板块指数/成分股）
"""

import requests
import re
import json
import numpy as np
from datetime import datetime, date, timedelta
from bs4 import BeautifulSoup
import time

SECTORS = [
    {"name": "小金属",    "ths_code": "300809", "ths_index": "小金属概念"},
    {"name": "算力",      "ths_code": "308828", "ths_index": "东数西算(算力)"},
    {"name": "芯片",      "ths_code": "301085", "ths_index": "芯片概念"},
    {"name": "AI应用",    "ths_code": "309264", "ths_index": "AI应用"},
    {"name": "人形机器人", "ths_code": "309119", "ths_index": "人形机器人"},
    {"name": "商业航天",  "ths_code": "309130", "ths_index": "商业航天"},
]

THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122",
    "Referer": "https://q.10jqka.com.cn/",
}
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}


def get_stock_market(code):
    """根据股票代码判断市场前缀"""
    if code.startswith('6'):
        return 'sh'
    elif code.startswith('9'):
        return 'bj'
    else:
        return 'sz'


def fetch_sector_stocks(ths_code):
    stocks = []
    for page in range(1, 10):
        url = f"https://q.10jqka.com.cn/gn/detail/code/{ths_code}/page/{page}/"
        try:
            r = requests.get(url, headers=THS_HEADERS, timeout=12)
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


def fetch_stock_kline(code, n=60):
    """从新浪财经拉取日线K线，数据及时准确"""
    market = get_stock_market(code)
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={market}{code}&scale=240&ma=no&datalen={n}"
    try:
        r = requests.get(url, headers=SINA_HEADERS, timeout=10)
        data = json.loads(r.text)
        rows = []
        for d in data:
            try:
                rows.append({
                    "date": d["day"].replace("-", ""),
                    "close": float(d["close"]),
                })
            except:
                pass
        return rows
    except Exception as e:
        return []


def classify_stock(kline_rows):
    if len(kline_rows) < 22:
        return None

    closes = [r["close"] for r in kline_rows]

    def day_state(c, ma):
        if c > ma * 1.03:   return "above"
        if c < ma * 0.97:   return "below"
        return "tangle"

    history = []
    for i in range(max(20, len(closes) - 30), len(closes)):
        ma = float(np.mean(closes[i-20:i]))
        history.append(day_state(closes[i], ma))

    if not history:
        return None

    ma20_today = float(np.mean(closes[-20:]))
    close_today = closes[-1]
    deviation = round((close_today - ma20_today) / ma20_today * 100, 2)

    below_streak = 0
    for s in reversed(history):
        if s == "below": below_streak += 1
        else: break

    above_streak = 0
    for s in reversed(history):
        if s == "above": above_streak += 1
        else: break

    today_state = history[-1]
    prev_state  = history[-2] if len(history) >= 2 else today_state

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


def deviation_alert(deviation):
    if abs(deviation) >= 50: return "strong"
    if abs(deviation) >= 30: return "warn"
    return None


def main():
    now_beijing = datetime.utcnow() + timedelta(hours=8)
    today_beijing = now_beijing.date()

    print(f"=== 强势选股系统 {now_beijing.strftime('%Y-%m-%d %H:%M')} (北京时间) ===")

    output = {
        "updated_at": now_beijing.strftime("%Y-%m-%d %H:%M"),
        "trade_date": today_beijing.strftime("%Y-%m-%d"),
        "sectors": [],
        "deviation_alerts": [],
    }

    for sector in SECTORS:
        print(f"\n── {sector['name']} ──")

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
            time.sleep(0.05)

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
