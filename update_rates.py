import html
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOK_API_KEY = os.environ.get("BOK_API_KEY")
if not BOK_API_KEY:
    raise SystemExit("BOK_API_KEY environment variable is missing.")

OUT = Path("rates.json")
NAVER_URL = "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_RUBKRW"


def make_session():
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = make_session()


def to_float(value):
    if value is None:
        return None
    text = html.unescape(str(value)).strip()
    text = re.sub(r"[^0-9,\.\-]", "", text)
    if not text:
        return None
    return float(text.replace(",", ""))


def fetch_bok_usd_krw(days_back=80):
    end = date.today()
    start = end - timedelta(days=days_back)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{BOK_API_KEY}/json/kr/1/5000/731Y001/D/"
        f"{start:%Y%m%d}/{end:%Y%m%d}"
    )
    r = SESSION.get(url, timeout=(5, 20))
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
    r = SESSION.get(url, params={"date_req": date_req}, timeout=(5, 15))
    r.raise_for_status()
    root = ET.fromstring(r.content)
    for valute in root.findall("Valute"):
        if valute.findtext("CharCode") == "USD":
            nominal = to_float(valute.findtext("Nominal"))
            value = to_float(valute.findtext("Value"))
            return value / nominal
    raise RuntimeError(f"USD not found in CBR response for {iso_date}")


def _clean_text(raw):
    raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def _flex_label_pattern(label):
    # 네이버 표기는 "사실때" / "사실 때"처럼 띄어쓰기가 바뀔 수 있어 글자 사이 공백을 허용합니다.
    compact = re.sub(r"\s+", "", label)
    return r"\s*".join(map(re.escape, compact))


def _extract_after_label(text, label):
    # 예: "현찰 사실 때 22.56" / "송금 보내실 때 21.33" / "T/C 사실 때 N/A"
    pattern = _flex_label_pattern(label) + r"\s*([0-9][0-9,\.]*|N/A)"
    m = re.search(pattern, text)
    if not m:
        return None
    return m.group(1) if m.group(1) == "N/A" else to_float(m.group(1))


def _decode_response(response):
    # 네이버 금융은 euc-kr/utf-8이 섞여 보일 수 있어 여러 방식으로 안전하게 해석합니다.
    candidates = []
    if response.encoding:
        candidates.append(response.encoding)
    candidates += ["euc-kr", "cp949", "utf-8"]
    raw_bytes = response.content
    for enc in candidates:
        try:
            return raw_bytes.decode(enc)
        except Exception:
            pass
    return response.text


def fetch_naver_rub_krw():
    """네이버 금융 RUB/KRW 상세 페이지에서 주요 고시환율을 가져온다.

    개선점:
    - GitHub Actions에서 차단 가능성을 줄이기 위해 브라우저에 가까운 header 사용
    - connect/read timeout 분리
    - 429/5xx는 짧게 재시도
    - 네이버의 띄어쓰기/인코딩 변화에 좀 더 강하게 파싱
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://finance.naver.com/marketindex/",
        "Connection": "close",
    }
    r = SESSION.get(NAVER_URL, headers=headers, timeout=(5, 10))
    r.raise_for_status()

    raw = _decode_response(r)
    text = _clean_text(raw)

    rate = None

    # 1) 상세 페이지의 대표 환율 영역(no_today)을 우선 사용
    m = re.search(r'class=["\']no_today["\'][\s\S]{0,800}?<em[^>]*>([0-9][0-9,\.]+)</em>', raw, flags=re.I)
    if m:
        rate = to_float(m.group(1))

    # 2) 태그 제거 텍스트에서 보조 추출
    if rate is None:
        m = re.search(r"러시아\s*RUB(?:KRW)?\s*(?:환율)?\s*([0-9][0-9,\.]+)\s*원", text)
        if m:
            rate = to_float(m.group(1))

    # 3) 그래도 안 되면 '전일대비' 앞쪽의 대표 숫자를 보조 추출
    if rate is None:
        m = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+\.[0-9]+)\s*원\s*전일대비", text)
        if m:
            rate = to_float(m.group(1))

    change = None
    change_pct = None
    m = re.search(r"전일대비\s*([▲▼+\-]?)\s*([0-9][0-9,\.]+)\s*([+\-]?[0-9][0-9,\.]*%)", text)
    if m:
        sign = -1 if m.group(1) in ("▼", "-") else 1
        change = sign * to_float(m.group(2))
        change_pct = m.group(3)

    time_text = None
    m = re.search(r"(20\d{2}\.\d{2}\.\d{2}\s+\d{2}:\d{2})", text)
    if m:
        time_text = m.group(1)

    result = {
        "naver_rub_krw": rate,
        "naver_change": change,
        "naver_change_pct": change_pct,
        "naver_time": time_text,
        "naver_cash_buy": _extract_after_label(text, "현찰 사실 때"),
        "naver_cash_sell": _extract_after_label(text, "현찰 파실 때"),
        "naver_send": _extract_after_label(text, "송금 보내실 때"),
        "naver_receive": _extract_after_label(text, "송금 받으실 때"),
        "naver_tc_buy": _extract_after_label(text, "T/C 사실 때"),
        "naver_check_sell": _extract_after_label(text, "외화수표 파실 때"),
    }

    # 대표 환율조차 없으면 HTML 구조 변경/차단 가능성이 높으므로 명확히 실패 처리
    if result["naver_rub_krw"] is None:
        snippet = text[:300]
        raise RuntimeError(f"Naver RUB/KRW rate was not found. Snippet: {snippet}")

    return result


def empty_naver_data():
    return {
        "naver_rub_krw": None,
        "naver_change": None,
        "naver_change_pct": None,
        "naver_time": None,
        "naver_cash_buy": None,
        "naver_cash_sell": None,
        "naver_send": None,
        "naver_receive": None,
        "naver_tc_buy": None,
        "naver_check_sell": None,
    }


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


def main():
    today = date.today()
    bok = fetch_bok_usd_krw(days_back=80)
    recent_dates = [(today - timedelta(days=i)).isoformat() for i in range(9, -1, -1)]

    try:
        naver = fetch_naver_rub_krw()
        print("Fetched Naver RUB/KRW data.")
    except Exception as exc:
        print(f"Warning: failed to fetch Naver RUB/KRW data: {exc}")
        naver = empty_naver_data()

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
        # 네이버는 현재 상세 고시값이므로 최신 행에만 표시하고, 그래프는 최신점만 표시합니다.
        row.update(naver if dt == recent_dates[-1] else empty_naver_data())
        rows.append(row)

    for i, row in enumerate(rows):
        s = score_system(calc_series[:i + 1], row["calc_rub_krw"])
        row["score"] = s
        row["signal"] = signal(s)

    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} with {len(rows)} rows.")
    print(f"Latest date: {rows[-1]['date']}")
    print(f"Latest BOK source date: {rows[-1]['bok_source_date']}")


if __name__ == "__main__":
    main()
