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


def to_float(value):
    return float(str(value).replace(",", ""))


def load_existing_rows():
    """Load existing rates.json so manually/previously collected Naver values survive."""
    if not OUT.exists():
        return {}

    try:
        rows = json.loads(OUT.read_text(encoding="utf-8"))
        return {row.get("date"): row for row in rows if row.get("date")}
    except Exception as e:
        print(f"Existing rates.json could not be read. Starting fresh. Reason: {e}")
        return {}


def fetch_bok_usd_krw(days_back=80):
    end = date.today()
    start = end - timedelta(days=days_back)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{BOK_API_KEY}/json/kr/1/5000/731Y001/D/"
        f"{start:%Y%m%d}/{end:%Y%m%d}"
    )
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    payload = r.json()

    if "StatisticSearch" not in payload:
        raise RuntimeError(f"Unexpected BOK response: {payload}")

    rows = payload["StatisticSearch"].get("row", [])
    result = {}

    for row in rows:
        name = row.get("ITEM_NAME1", "")
        if "미국달러" in name:
            t = row["TIME"]
            d = f"{t[:4]}-{t[4:6]}-{t[6:]}"
            result[d] = to_float(row["DATA_VALUE"])

    if not result:
        raise RuntimeError("No USD/KRW rows found from BOK.")

    return dict(sorted(result.items()))


def fetch_cbr_usd_rub(iso_date):
    y, m, d = iso_date.split("-")
    date_req = f"{d}/{m}/{y}"
    url = "https://www.cbr.ru/scripts/XML_daily_eng.asp"
    r = requests.get(url, params={"date_req": date_req}, timeout=25)
    r.raise_for_status()
    root = ET.fromstring(r.content)

    for valute in root.findall("Valute"):
        code = valute.findtext("CharCode")
        if code == "USD":
            nominal = to_float(valute.findtext("Nominal"))
            value = to_float(valute.findtext("Value"))
            return value / nominal

    raise RuntimeError(f"USD not found in CBR response for {iso_date}")


def fetch_naver_rub_krw():
    """Fetch today's RUB/KRW from Naver Finance.

    Naver's public detail page does not provide a reliable 10-day historical feed.
    We fetch only today's visible quote, then preserve previous Naver values already
    stored in rates.json so the 10-day table gradually fills over time.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/marketindex/",
    }

    try:
        r = requests.get(
            NAVER_URL,
            params={"marketindexCd": "FX_RUBKRW"},
            headers=headers,
            timeout=25,
        )
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "euc-kr"
        html = r.text

        # Typical Naver markup:
        # <p class="no_today"> ... <span class="blind">18.12</span>
        m = re.search(
            r'class=["\']no_today["\'][\s\S]*?<span class=["\']blind["\']>([0-9,.]+)</span>',
            html,
        )

        if not m:
            # Fallback for small HTML changes: find a numeric value near RUB/KRW.
            m = re.search(r'([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*</span>[\s\S]{0,500}?원', html)

        if not m:
            print("Naver RUB/KRW value not found in page HTML.")
            return None

        return to_float(m.group(1))
    except Exception as e:
        print(f"Naver RUB/KRW fetch failed: {e}")
        return None


def score_system(calc_series, current):
    recent = calc_series[-10:]
    avg = sum(recent) / len(recent)
    variance = sum((x - avg) ** 2 for x in recent) / len(recent)
    vol = math.sqrt(variance)
    dev = (current - avg) / avg
    trend = 0 if len(recent) < 2 else (recent[-1] - recent[0]) / recent[0]

    score = 60 + (-dev * 900) + (-trend * 250) - (vol * 1.2)
    return max(0, min(100, round(score)))


def signal(score):
    if score >= 80:
        return "▲ BEST"
    if score >= 65:
        return "● GOOD"
    if score >= 50:
        return "■ NORMAL"
    return "▼ BAD"


def last_bok_value_on_or_before(bok_map, iso_date):
    available_dates = [d for d in bok_map.keys() if d <= iso_date]
    if not available_dates:
        raise RuntimeError(f"No BOK USD/KRW value available on or before {iso_date}")
    last_date = max(available_dates)
    return bok_map[last_date], last_date


def copy_existing_naver_fields(row, existing_row):
    """Preserve Naver values collected on previous Action runs."""
    if not existing_row:
        return

    for key in ("naver_rub_krw", "naver_calc_diff"):
        if key in existing_row:
            row[key] = existing_row[key]


def main():
    today = date.today()
    today_iso = today.isoformat()

    existing_by_date = load_existing_rows()
    bok = fetch_bok_usd_krw(days_back=80)
    naver_today = fetch_naver_rub_krw()

    recent_dates = [
        (today - timedelta(days=i)).isoformat()
        for i in range(9, -1, -1)
    ]

    rows = []
    calc_series = []

    for dt in recent_dates:
        bok_usd_krw, bok_source_date = last_bok_value_on_or_before(bok, dt)
        cbr_usd_rub = fetch_cbr_usd_rub(dt)
        calc_rub_krw = bok_usd_krw / cbr_usd_rub
        calc_series.append(calc_rub_krw)

        row = {
            "date": dt,
            "bok_source_date": bok_source_date,
            "bok_usd_krw": round(bok_usd_krw, 4),
            "cbr_usd_rub": round(cbr_usd_rub, 6),
            "calc_rub_krw": round(calc_rub_krw, 6),
            "krw_1_5m_to_rub": round(1500000 / calc_rub_krw),
            "usd_rub": round(cbr_usd_rub, 6),
            "krw_rub": round(calc_rub_krw, 6),
        }

        copy_existing_naver_fields(row, existing_by_date.get(dt))

        if dt == today_iso and naver_today is not None:
            row["naver_rub_krw"] = round(naver_today, 4)
            row["naver_calc_diff"] = round(naver_today - calc_rub_krw, 4)

        rows.append(row)

    for i, row in enumerate(rows):
        s = score_system(calc_series[: i + 1], row["calc_rub_krw"])
        row["score"] = s
        row["signal"] = signal(s)

    OUT.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    naver_count = sum(1 for row in rows if row.get("naver_rub_krw") is not None)
    print(f"Wrote {OUT} with {len(rows)} rows.")
    print(f"Latest date: {rows[-1]['date']}")
    print(f"Latest BOK source date: {rows[-1]['bok_source_date']}")
    print(f"Naver RUB/KRW rows preserved/collected: {naver_count}")


if __name__ == "__main__":
    main()
