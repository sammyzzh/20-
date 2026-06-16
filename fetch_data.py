#!/usr/bin/env python3
"""
强势板块选股系统 - 数据抓取脚本
每日收盘后（15:30+）由 GitHub Actions 自动触发
数据来源：同花顺公开接口（无需账号）
"""

import requests
import re
import json
import numpy as np
from datetime import datetime, date
from bs4 import BeautifulSoup
import time
import sys

# ─────────────────────────────────────────────
# 六大板块配置（同花顺概念板块代码）
# ─────────────────────────────────────────────
SECTORS = [
    {"name": "小金属",   "ths_code": "300809"},
    {"name": "算力",     "ths_code": "308828"},
    {"name": "芯片",     "ths_code": "301085"},
    {"name": "AI应用",   "ths_code": "309264"},
    {"name": "人形机器人","ths_code": "309119"},
    {"name": "商业航天", "ths_code": "309130"},
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
            if found == 0:
                break
            # 检查是否有下一页
            next_btn = soup.find("a", string=re.compile("下一页|›|>"))
            if not next_btn:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"  抓取成分股失败 page={page}: {e}")
            break
    return stocks


# ─────────────────────────────────────────────
# 2. 抓取板块指数日线（用于判断板块多空）
# ─────────────────────────────────────────────
def fetch_sector_index(ths_code, n=60):
    """抓取同花顺概念板块指数，返回收盘价列表"""
    try:
        import akshare as ak
        today = date.today().strftime("%Y%m%d")
        # 取60个交易日前的日期（约3个月）
        from datetime import timedelta
        start = (date.today() - timedelta(days=100)).strftime("%Y%m%d")
        # 根据code找板块名
        name_map = {s["ths_code"]: s["name"] for s in SECTORS}
        sector_name = name_map.get(ths_code, "")
        df = ak.stock_board_concept_index_ths(
            symbol=sector_name, start_date=start, end_date=today
        )
        if df is None or len(df) < 22:
            return []
        closes = df["收盘价"].tolist()
        return closes
    except Exception as e:
        print(f"  板块指数抓取失败({ths_code}): {e}")
        return []


# ─────────────────────────────────────────────
# 3. 抓取个股日线K线（同花顺公开接口）
# ─────────────────────────────────────────────
def fetch_stock_kline(code, n=60):
    url = f"https://d.10jqka.com.cn/v6/line/hs_{code}/01/last{n}.js"
    try:
        r = requests.get(url, headers={
            "Referer": "https://stockpage.10jqka.com.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }, timeout=10)
        text = r.text
        data_match = re.search(r'"data":"([^"]+)"', text)
        if not data_match:
            return []
        raw = data_match.group(1)
        rows = []
        for item in raw.split(";"):
            parts = item.split(",")
            if len(parts) >= 5:
                try:
                    rows.append({
                        "date": parts[0],
                        "close": float(parts[4]),
                    })
                except:
                    pass
        return rows
    except Exception as e:
        return []


# ─────────────────────────────────────────────
# 4. 计算20日均线状态
# ─────────────────────────────────────────────
def classify_stock(kline_rows):
    """
    返回 dict:
      status: above / breakout_up / tangle / breakout_down / below_d1 / below_d2 / excluded
      ma20: 今日20日均线
      close: 今日收盘
      deviation: 今日乖离率(%)
      breakout_day: 突破/跌破第几天
    """
    if len(kline_rows) < 22:
        return None

    closes = [r["close"] for r in kline_rows]

    # 历史状态序列（用于判断连续跌破天数 & 突破第N天）
    def day_state(close_val, ma):
        top = ma * 1.03
        bot = ma * 0.97
        if close_val > top:
            return "above"
        elif close_val < bot:
            return "below"
        else:
            return "tangle"

    # 计算每天的ma20和状态（只用最近30天判断）
    history = []
    for i in range(max(0, len(closes) - 30), len(closes)):
        if i < 20:
            continue
        ma = float(np.mean(closes[i-20:i]))
        s = day_state(closes[i], ma)
        history.append(s)

    if not history:
        return None

    today_state = history[-1]
    ma20_today = float(np.mean(closes[-20:]))
    close_today = closes[-1]
    deviation = round((close_today - ma20_today) / ma20_today * 100, 2)

    # 连续跌破天数
    below_streak = 0
    for s in reversed(history):
        if s == "below":
            below_streak += 1
        else:
            break

    # 连续站上天数（用于突破第N天）
    above_streak = 0
    for s in reversed(history):
        if s == "above":
            above_streak += 1
        else:
            break

    # 前一天状态
    prev_state = history[-2] if len(history) >= 2 else today_state

    # 判断最终status
    if below_streak >= 3:
        status = "excluded"
    elif today_state == "below":
        status = f"below_d{below_streak}"
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
# 5. 乖离率警报（独立于板块状态）
# ─────────────────────────────────────────────
def deviation_alert(deviation):
    """返回 None / 'warn'(±30%) / 'strong'(±50%)"""
    abs_dev = abs(deviation)
    if abs_dev >= 50:
        return "strong"
    elif abs_dev >= 30:
        return "warn"
    return None


# ─────────────────────────────────────────────
# 6. 主流程
# ─────────────────────────────────────────────
def main():
    print(f"=== 强势选股系统 开始运行 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "trade_date": date.today().strftime("%Y-%m-%d"),
        "sectors": [],
        "deviation_alerts": [],   # 全市场乖离率预警（独立模块）
    }

    for sector in SECTORS:
        print(f"\n── 处理板块：{sector['name']} ──")

        # 板块指数
        sector_closes = fetch_sector_index(sector["ths_code"])
        if len(sector_closes) >= 20:
            ma20 = float(np.mean(sector_closes[-20:]))
            close = sector_closes[-1]
            dev = (close - ma20) / ma20 * 100
            if close > ma20 * 1.03:
                sector_status = "bullish"
            elif close < ma20 * 0.97:
                sector_status = "bearish"
            else:
                sector_status = "tangle"
            sector_ma20 = round(ma20, 2)
            sector_close = round(close, 2)
            sector_dev = round(dev, 2)
        else:
            sector_status = "unknown"
            sector_ma20 = None
            sector_close = None
            sector_dev = None

        print(f"  板块状态: {sector_status}  收盘={sector_close}  MA20={sector_ma20}")

        # 成分股
        print(f"  抓取成分股列表...")
        stocks_raw = fetch_sector_stocks(sector["ths_code"])
        print(f"  共 {len(stocks_raw)} 只成分股，开始计算均线...")

        stocks_result = []
        for i, s in enumerate(stocks_raw):
            kline = fetch_stock_kline(s["code"])
            result = classify_stock(kline)
            if result is None:
                continue

            alert = deviation_alert(result["deviation"])
            stock_data = {
                "code": s["code"],
                "name": s["name"],
                **result,
                "deviation_alert": alert,
            }
            stocks_result.append(stock_data)

            # 乖离率预警加入全局列表
            if alert:
                output["deviation_alerts"].append({
                    "sector": sector["name"],
                    "code": s["code"],
                    "name": s["name"],
                    "deviation": result["deviation"],
                    "alert": alert,
                    "close": result["close"],
                    "ma20": result["ma20"],
                })

            if (i + 1) % 10 == 0:
                print(f"    已处理 {i+1}/{len(stocks_raw)}")
            time.sleep(0.08)  # 限流

        # 按状态分组统计
        status_counts = {}
        for s in stocks_result:
            st = s["status"]
            status_counts[st] = status_counts.get(st, 0) + 1

        output["sectors"].append({
            "name": sector["name"],
            "ths_code": sector["ths_code"],
            "status": sector_status,
            "ma20": sector_ma20,
            "close": sector_close,
            "deviation": sector_dev,
            "stock_count": len(stocks_result),
            "status_counts": status_counts,
            "stocks": stocks_result,
        })

        print(f"  完成，状态分布: {status_counts}")

    # 乖离率预警按绝对值排序
    output["deviation_alerts"].sort(key=lambda x: abs(x["deviation"]), reverse=True)

    # 写出 JSON
    out_path = "docs/data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 数据已写入 {out_path}")
    print(f"   板块数: {len(output['sectors'])}")
    print(f"   乖离率预警: {len(output['deviation_alerts'])} 条")


if __name__ == "__main__":
    main()
