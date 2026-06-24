import os
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import requests

BOK_API_KEY = os.environ.get("BOK_API_KEY")
if not BOK_API_KEY:
    raise SystemExit("BOK_API_KEY environment variable is missing.")

OUT = Path("rates.json")
NAVER_URL = "https://finance.naver.com/marketindex/exchangeDetail.naver"


# -------------------------
# utils
# -------------------------
def to_float(value):
    try:
        return float(str(value).replace(",", "").strip())
    except:
        raise RuntimeError(f"Invalid numeric value: {value}")


# -------------------------
# 기존 데이터 로드 (네이버 누적용)
# -------------------------
def load_existing_rows():
    if not OUT.exists():
        return {}

    try:
        rows = json.loads(OUT.read_text(encoding="utf-8"))
        return {r["date"]: r for r in rows if "date" in r}
    except:
        return {}


# -------------------------
# BOK USD/KRW
# -------------------------
def fetch_bok_usd_krw(days_back=80):
    end = date.today()
    start = end - timedelta(days=days_back)

    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{BOK_API_KEY}/json/kr/1/5000/"
        f"731Y001/D/{start:%Y%m%d}/{end:%Y%m%d}"
    )

    r = requests.get(url, timeout=25)
    r.raise_for_status()
    payload = r.json()

    rows = payload["StatisticSearch"].get("row", [])

    result = {}
    for row in rows:
        if "미국달러" in row.get("ITEM_NAME1", ""):
            d = f"{row['TIME'][:4]}-{row['TIME'][4:6]}-{row['TIME'][6:]}"
            result[d] = to_float(row["DATA_VALUE"])

    if not result:
        raise RuntimeError("No BOK USD/KRW data found")

    return dict(sorted(result.items()))


# -------------------------
# CBR USD/RUB
# -------------------------
def fetch_cbr_usd_rub(iso_date):
    y, m, d = iso_date.split("-")
    date_req = f"{d}/{m}/{y}"

    url = "https://www.cbr.ru/scripts/XML_daily_eng.asp"
    r = requests.get(url, params={"date_req": date_req}, timeout=25)
    r.raise_for_status()

    root = ET.fromstring(r.content)

    for valute in root.findall("Valute"):
        if valute.findtext("CharCode") == "USD":
            nominal = to_float(valute.findtext("Nominal"))
            value = to_float(valute.findtext("Value"))
            return value / nominal

    raise RuntimeError(f"USD not found in CBR for {iso_date}")


# -------------------------
# NAVER RUB/KRW (핵심 수정)
# -------------------------
def fetch_naver_rub_krw():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.naver.com/marketindex/",
    }

    r = requests.get(
        NAVER_URL,
        params={"marketindexCd": "FX_RUBKRW"},
        headers=headers,
        timeout=25,
    )

    r.raise_for_status()
    r.encoding = "euc-kr"
    html = r.text

    # 가장 단순 + 안정적인 숫자 추출
    m = re.search(r'([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]+)', html)

    if not m:
        raise RuntimeError("NAVER parsing failed (no RUB/KRW found)")

    return to_float(m.group(1))


# -------------------------
# scoring
# -------------------------
def score_system(series, current):
    recent = series[-10:]
    avg = sum(recent) / len(recent)
    dev = (current - avg) / avg

    score = 60 + (-dev * 900)
    return max(0, min(100, round(score)))


def signal(score):
    if score >= 80:
        return "▲ BEST"
    if score >= 65:
        return "● GOOD"
    if score >= 50:
        return "■ NORMAL"
    return "▼ BAD"


# -------------------------
# main
# -------------------------
def main():
    today = date.today().isoformat()

    existing = load_existing_rows()

    bok = fetch_bok_usd_krw()
    naver_today = fetch_naver_rub_krw()

    dates = [(date.today() - timedelta(days=i)).isoformat() for i in range(9, -1, -1)]

    rows = []
    series = []

    for dt in dates:
        bok_val, bok_date = max((d for d in bok if d <= dt)), None
        bok_usd = bok[bok_val]

        cbr = fetch_cbr_usd_rub(dt)
        calc = bok_usd / cbr

        series.append(calc)

        row = {
            "date": dt,
            "bok_source_date": bok_val,
            "bok_usd_krw": round(bok_usd, 4),
            "cbr_usd_rub": round(cbr, 6),
            "calc_rub_krw": round(calc, 6),
            "krw_1_5m_to_rub": round(1500000 / calc),
            "usd_rub": round(cbr, 6),
            "krw_rub": round(calc, 6),
        }

        # 기존 네이버 값 유지
        if dt in existing and "naver_rub_krw" in existing[dt]:
            row["naver_rub_krw"] = existing[dt]["naver_rub_krw"]

        # 오늘만 새로 갱신
        if dt == today and naver_today is not None:
            row["naver_rub_krw"] = round(naver_today, 4)
            row["naver_calc_diff"] = round(naver_today - calc, 4)

        rows.append(row)

    # score
    for i, r in enumerate(rows):
        s = score_system(series[: i + 1], r["calc_rub_krw"])
        r["score"] = s
        r["signal"] = signal(s)

    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("OK:", len(rows), "rows")
    print("NAVER:", naver_today)


if __name__ == "__main__":
    main()
