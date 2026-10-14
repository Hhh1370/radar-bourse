#!/usr/bin/env python3
"""Iran equities: Codal fundamentals/projects + optional TSETMC money-flow layer.

Designed to run in GitHub Actions. Missing/blocked sources are recorded as missing,
never converted into a neutral or positive score. Codal report parsing is heuristic;
all extracted figures keep source links/snippets and should be reviewed for context.
"""

import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_FILE = ROOT / "data" / "stock_universe.json"
MAP_FILE = ROOT / "data" / "tsetmc_instrument_map.json"
OUTPUT_FILE = ROOT / "data" / "fundamental_money_radar.json"

TSETMC_BASE = "https://cdn.tsetmc.com/api"
CODAL_SEARCH_BASE = "https://search.codal.ir/api/search/v2/q"
BRS_API_BASE = "https://api.brsapi.ir"
BRS_API_KEY = os.environ.get("BRS_API_KEY", "").strip()
BRS_API_DELAY = 0.22
BRS_API_UNAVAILABLE = False
BRS_API_UNAVAILABLE_REASON = None
BRS_API_CONSECUTIVE_FAILURES = 0
BRS_ANNOUNCEMENT_CACHE = {}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
)
REQUEST_TIMEOUT = 10
RETRIES = 1
REPORT_TIMEOUT = 7
REPORT_RETRIES = 0
CODAL_REPORT_FAILURES = {}
CODAL_REPORT_UNAVAILABLE = set()
TSETMC_DELAY = 0.08
CODAL_DELAY = 0.08
MAX_GENERAL_SEARCH_PAGES = 2
MAX_FINANCIAL_SEARCH_PAGES = 2
MAX_PROJECT_DETAILS_PER_STOCK = 1
MAX_FINANCIAL_DETAILS_PER_STOCK = 6
FLOW_WINDOWS = (5, 20, 60, 120, 252)
COMMON_EQUITY_ISIN_PREFIXES = ("IRO1", "IRO3")

TSETMC_UNAVAILABLE = False
TSETMC_UNAVAILABLE_REASON = None
TSETMC_CONSECUTIVE_FAILURES = 0
CODAL_UNAVAILABLE = False
CODAL_UNAVAILABLE_REASON = None
CODAL_CONSECUTIVE_FAILURES = 0

PROJECT_KEYWORDS = (
    "طرح توسعه", "طرح احداث", "پروژه", "پیشرفت فیزیکی", "پیشرفت پروژه",
    "افزایش ظرفیت", "ظرفیت تولید", "بهره برداری", "بهره‌برداری",
    "راه اندازی", "راه‌اندازی", "تولید آزمایشی", "تکمیل طرح", "توسعه خطوط",
    "عملیات اجرایی", "سرمایه گذاری در طرح", "سرمایه‌گذاری در طرح", "فاز توسعه",
    "ماشین آلات طرح", "ماشین‌آلات طرح", "ظرفیت اسمی", "گزارش توجیهی طرح",
)
FINANCIAL_KEYWORDS = (
    "صورت های مالی", "صورت‌های مالی", "اطلاعات و صورت مالی", "گزارش تفسیری مدیریت",
    "گزارش فعالیت ماهانه", "تولید و فروش", "صورت مالی میاندوره", "صورت مالی میان دوره",
)
IMPORTANT_DISCLOSURE_KEYWORDS = (
    "افشای اطلاعات بااهمیت", "اطلاعات بااهمیت", "ماده 13", "ماده ۱۳",
    "ماده 129", "ماده ۱۲۹", "توقف نماد", "تعلیق نماد", "دریافت مجوز",
    "افزایش سرمایه", "انعقاد قرارداد", "مزایده", "مناقصه",
)

# Text labels used to extract comparable lines from Codal statement tables.
METRIC_LABELS = {
    "revenue": ["درآمدهای عملیاتی", "درآمد عملیاتی", "درآمد حاصل از فروش", "فروش خالص"],
    "gross_profit": ["سود (زیان) ناخالص", "سود ناخالص"],
    "operating_profit": ["سود (زیان) عملیاتی", "سود عملیاتی"],
    "net_profit": ["سود (زیان) خالص", "سود خالص دوره", "سود خالص"],
    "total_assets": ["جمع کل دارایی ها", "جمع دارایی ها", "جمع داراییها"],
    "total_liabilities": ["جمع کل بدهی ها", "جمع بدهی ها", "جمع بدهیها"],
    "current_assets": ["جمع دارایی های جاری", "جمع داراییهای جاری"],
    "current_liabilities": ["جمع بدهی های جاری", "جمع بدهیهای جاری"],
    "operating_cash_flow": [
        "خالص جریان ورود (خروج) وجه نقد ناشی از فعالیت های عملیاتی",
        "جریان خالص وجه نقد حاصل از فعالیت های عملیاتی",
        "وجه نقد حاصل از فعالیت های عملیاتی",
        "خالص وجه نقد حاصل از فعالیت های عملیاتی",
    ],
    "finance_cost": ["هزینه های مالی", "هزینه مالی"],
    "cash_and_equivalents": ["وجه نقد و معادل های نقد", "موجودی نقد و بانک", "وجه نقد و معادل نقد"],
    "inventory": ["موجودی مواد و کالا", "موجودی کالا"],
    "trade_receivables": ["دریافتنی های تجاری", "حساب های دریافتنی تجاری"],
    "retained_earnings": ["سود (زیان) انباشته", "سود انباشته"],
    "basic_eps": ["سود (زیان) پایه هر سهم", "سود پایه هر سهم", "سود هر سهم"],
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(value):
    text = str(value or "").strip()
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    text = text.replace("ۀ", "ه").replace("ة", "ه").replace("أ", "ا")
    text = text.replace("‌", "").replace("\u200c", "").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"[\s\-_/]+", "", text).casefold()


def as_number(value):
    """Parse a numeric field without accidentally reading years from dates."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip()
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    text = text.replace("٬", ",").replace("،", ",").replace("٫", ".")
    if not text or text in {"-", "—", "–", "...", "نا مشخص", "ندارد"}:
        return None
    # Only accept numeric cells (optionally with units), not free-text snippets/dates.
    pattern = r"^\s*\(?\s*[-+]?\s*\d[\d, .]*\s*\)?\s*(?:ریال|هزار\s*ریال|میلیون\s*ریال|درصد|%)?\s*$"
    if not re.fullmatch(pattern, text):
        return None
    cleaned = re.sub(r"[^0-9+\-.]", "", text)
    try:
        number = float(cleaned)
        if "(" in text and "-" not in cleaned:
            number = -number
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if value is None:
        return False
    return str(value).strip().casefold() in {"true", "1", "yes", "بله"}


def first_value(obj, aliases):
    if not isinstance(obj, dict):
        return None
    lower_map = {str(k).casefold(): v for k, v in obj.items()}
    for alias in aliases:
        if alias.casefold() in lower_map:
            return lower_map[alias.casefold()]
    return None


def find_array(payload, keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        lower = {str(k).casefold(): v for k, v in payload.items()}
        for key in keys:
            value = lower.get(key.casefold())
            if isinstance(value, list):
                return value
        for value in payload.values():
            found = find_array(value, keys) if isinstance(value, (dict, list)) else None
            if found is not None:
                return found
    return []


def request_bytes(url, delay=0.0):
    """Fetch a URL with bounded retries and a source-level circuit breaker."""
    global TSETMC_UNAVAILABLE, TSETMC_UNAVAILABLE_REASON, TSETMC_CONSECUTIVE_FAILURES
    global CODAL_UNAVAILABLE, CODAL_UNAVAILABLE_REASON, CODAL_CONSECUTIVE_FAILURES

    is_tsetmc = "cdn.tsetmc.com" in url
    is_codal = "codal.ir" in url
    if is_tsetmc and TSETMC_UNAVAILABLE:
        raise RuntimeError(TSETMC_UNAVAILABLE_REASON or "TSETMC API unavailable")
    if is_codal and CODAL_UNAVAILABLE:
        raise RuntimeError(CODAL_UNAVAILABLE_REASON or "Codal API unavailable")

    last_error = None
    for attempt in range(RETRIES + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json" if "search.codal.ir" in url else "text/html,application/json,*/*",
                "Referer": "https://www.codal.ir/" if is_codal else "https://www.tsetmc.com/",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                body = response.read()
            if not body:
                raise ValueError("empty response body")
            if delay:
                time.sleep(delay)
            if is_tsetmc:
                TSETMC_CONSECUTIVE_FAILURES = 0
            if is_codal:
                CODAL_CONSECUTIVE_FAILURES = 0
            return body
        except urllib.error.HTTPError as exc:
            last_error = exc
            # Clear service-level blocks should immediately stop expensive retries.
            if is_tsetmc and exc.code in (403, 451):
                TSETMC_UNAVAILABLE = True
                TSETMC_UNAVAILABLE_REASON = f"HTTP {exc.code}; TSETMC CDN may be blocked from the runner"
                raise RuntimeError(TSETMC_UNAVAILABLE_REASON) from exc
            if is_codal and exc.code in (403, 429, 451, 500, 502, 503, 504):
                CODAL_UNAVAILABLE = True
                CODAL_UNAVAILABLE_REASON = f"Codal endpoint returned HTTP {exc.code}; stopped to avoid repeated timeouts"
                raise RuntimeError(CODAL_UNAVAILABLE_REASON) from exc
            if exc.code in (400, 401, 403, 404):
                break
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = exc
        if attempt < RETRIES:
            time.sleep(0.35)

    # If the same provider fails twice in a row, open the circuit for this run.
    if is_tsetmc:
        TSETMC_CONSECUTIVE_FAILURES += 1
        if TSETMC_CONSECUTIVE_FAILURES >= 2:
            TSETMC_UNAVAILABLE = True
            TSETMC_UNAVAILABLE_REASON = f"TSETMC failed on consecutive requests: {last_error}"
    elif is_codal:
        CODAL_CONSECUTIVE_FAILURES += 1
        if CODAL_CONSECUTIVE_FAILURES >= 2:
            CODAL_UNAVAILABLE = True
            CODAL_UNAVAILABLE_REASON = f"Codal failed on consecutive requests: {last_error}"

    raise RuntimeError(f"request failed: {last_error}")


def request_json(url, delay=0.0):
    body = request_bytes(url, delay=delay)
    decoded = body.decode("utf-8-sig", errors="replace")
    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        if "search.codal.ir" not in url:
            raise
        # Codal may return XML if its content negotiation changes. Parse it as a fallback.
        root = ET.fromstring(decoded)
        letters = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "CodalLetterHeaderDto":
                continue
            item = {}
            for child in list(element):
                key = child.tag.rsplit("}", 1)[-1]
                item[key] = child.text or ""
            if item:
                letters.append(item)
        if not letters:
            raise ValueError("Codal response was neither JSON nor a recognized XML report list")
        return {"Letters": letters, "Total": len(letters), "Page": 1}


def tsetmc_get(path):
    global TSETMC_UNAVAILABLE
    if TSETMC_UNAVAILABLE:
        raise RuntimeError(TSETMC_UNAVAILABLE_REASON or "TSETMC API unavailable")
    return request_json(f"{TSETMC_BASE}/{path.lstrip('/')}", delay=TSETMC_DELAY)


def brsapi_get_json(path, params):
    """Call BrsApi; used because Codal/TSETMC direct hosts are geo-restricted."""
    global BRS_API_UNAVAILABLE, BRS_API_UNAVAILABLE_REASON, BRS_API_CONSECUTIVE_FAILURES
    if not BRS_API_KEY:
        raise RuntimeError("BRS_API_KEY is missing; add it as a GitHub Actions repository secret.")
    if BRS_API_UNAVAILABLE:
        raise RuntimeError(BRS_API_UNAVAILABLE_REASON or "BrsApi is unavailable")

    query = dict(params)
    query["key"] = BRS_API_KEY
    url = f"{BRS_API_BASE}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"
    try:
        body = request_bytes(url, delay=BRS_API_DELAY)
        payload = json.loads(body.decode("utf-8-sig", errors="replace"))
        # Detect common provider-side API-key / quota errors instead of treating them as empty data.
        if isinstance(payload, dict):
            status_text = " ".join(
                str(first_value(payload, (name,)) or "")
                for name in ("error", "message", "detail", "status", "success")
            ).casefold()
            if any(term in status_text for term in (
                "invalid key", "invalid api", "unauthorized", "quota exceeded",
                "api key required", "کلید نامعتبر", "کلید api", "محدودیت درخواست",
            )):
                raise RuntimeError(status_text[:180])
        BRS_API_CONSECUTIVE_FAILURES = 0
        return payload
    except Exception as exc:
        safe_error = str(exc).replace(BRS_API_KEY, "[REDACTED]")
        BRS_API_CONSECUTIVE_FAILURES += 1
        if BRS_API_CONSECUTIVE_FAILURES >= 2:
            BRS_API_UNAVAILABLE = True
            BRS_API_UNAVAILABLE_REASON = f"BrsApi failed twice consecutively: {safe_error[:180]}"
        raise RuntimeError(safe_error[:220]) from exc


def _brsapi_announcement_rows(payload):
    rows = find_array(payload, ("announcements", "announcement", "items", "results", "result", "data", "letters", "records", "list"))
    if rows:
        return rows
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if first_value(payload, ("count_announcement", "count_page", "total", "totalCount")) is not None:
            return []
        # A structured error should not be misreported as an empty filing history.
        message = first_value(payload, ("error", "message", "detail", "status"))
        if message:
            raise RuntimeError(f"BrsApi announcement response: {str(message)[:180]}")
    raise ValueError("Unrecognized BrsApi announcement response schema")


def brsapi_codal_search(symbol):
    """Fetch the latest announcement page once per symbol and cache it for both filters."""
    if symbol in BRS_ANNOUNCEMENT_CACHE:
        return BRS_ANNOUNCEMENT_CACHE[symbol]

    payload = brsapi_get_json("Codal/Announcement.php", {"l18": symbol, "page": "1"})
    rows = _brsapi_announcement_rows(payload)
    reports = []
    seen = set()

    for raw in rows:
        if not isinstance(raw, dict):
            continue
        title = first_value(raw, ("title", "Title", "subject", "letterTitle", "name"))
        if not title:
            continue
        title = str(title).strip()
        date = first_value(raw, ("date_publish", "date_send", "date_title", "date", "publishDate", "sentDate"))
        report_url = first_value(raw, ("link", "url", "reportUrl", "report_url"))
        excel_url = first_value(raw, ("link_excel", "excelUrl", "excel_url"))
        pdf_url = first_value(raw, ("link_pdf", "pdfUrl", "pdf_url"))
        attachment_url = first_value(raw, ("link_attachment", "attachmentUrl", "attachment_url"))
        if report_url:
            report_url = urllib.parse.urljoin("https://www.codal.ir/", html.unescape(str(report_url)))
        if excel_url:
            excel_url = urllib.parse.urljoin("https://excel.codal.ir/", html.unescape(str(excel_url)))
        if pdf_url:
            pdf_url = urllib.parse.urljoin("https://www.codal.ir/", html.unescape(str(pdf_url)))
        code = first_value(raw, ("code", "tracingNo", "TracingNo", "id"))
        key = (str(code or ""), title, str(date or ""))
        if key in seen:
            continue
        seen.add(key)
        norm_title = normalize_text(title)
        project_keywords = [kw for kw in PROJECT_KEYWORDS if normalize_text(kw) in norm_title]
        financial_keywords = [kw for kw in FINANCIAL_KEYWORDS if normalize_text(kw) in norm_title]
        # A few Codal titles do not use the exact canonical wording in FINANCIAL_KEYWORDS.
        is_financial = bool(financial_keywords) or "صورت" in norm_title or "عملکردماهانه" in norm_title or "تولیدوفروش" in norm_title
        material_keywords = [kw for kw in IMPORTANT_DISCLOSURE_KEYWORDS if normalize_text(kw) in norm_title]
        reports.append({
            "title": title,
            "date": str(date).strip() if date else None,
            "url": str(report_url) if report_url else None,
            "excel_url": str(excel_url) if excel_url else None,
            "pdf_url": str(pdf_url) if pdf_url else None,
            "attachment_url": str(attachment_url) if attachment_url else None,
            "tracing_no": str(code) if code is not None else None,
            "letter_code": str(first_value(raw, ("category", "letterCode", "code")) or ""),
            "has_html": bool(report_url),
            "has_excel": bool(excel_url),
            "is_project_candidate": bool(project_keywords),
            "project_keywords": project_keywords,
            "is_financial_report_candidate": is_financial,
            "financial_keywords": financial_keywords,
            "is_important_disclosure": bool(material_keywords),
            "important_disclosure_keywords": material_keywords,
            "source": "BrsApi Codal announcement proxy",
        })

    reports.sort(key=lambda r: normalize_text(r.get("date") or ""), reverse=True)
    BRS_ANNOUNCEMENT_CACHE[symbol] = reports
    return reports


def codal_search(symbol, financial_only=False):
    """Use BrsApi rather than calling geo-restricted Codal directly from GitHub Actions."""
    reports = brsapi_codal_search(symbol)
    if financial_only:
        return [r for r in reports if r.get("is_financial_report_candidate")]
    return list(reports)


def normalize_history(payload):
    rows = find_array(payload, ("clientType", "clientTypeHistory", "clienttype", "clientTypeHistoryDto", "history", "data", "items", "records", "result", "results"))
    cleaned, seen = [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        date_value = first_value(raw, ("recDate", "dEven", "date", "dateTime", "dateString"))
        if date_value is None:
            continue
        date_key = re.sub(r"\D", "", normalize_text(date_value))
        if not date_key or date_key in seen:
            continue
        seen.add(date_key)

        def n(*keys):
            return as_number(first_value(raw, keys))

        cleaned.append({
            "date_raw": str(date_value),
            "date_sort": date_key,
            "buy_real_value": n("buy_I_Value", "buyIValue", "buy_i_value"),
            "sell_real_value": n("sell_I_Value", "sellIValue", "sell_i_value"),
            "buy_legal_value": n("buy_N_Value", "buyNValue", "buy_n_value"),
            "sell_legal_value": n("sell_N_Value", "sellNValue", "sell_n_value"),
            "buy_real_volume": n("buy_I_Volume", "buyIVolume", "buy_i_volume"),
            "sell_real_volume": n("sell_I_Volume", "sellIVolume", "sell_i_volume"),
            "buy_legal_volume": n("buy_N_Volume", "buyNVolume", "buy_n_volume"),
            "sell_legal_volume": n("sell_N_Volume", "sellNVolume", "sell_n_volume"),
            "buy_real_count": n("buy_I_Count", "buyICount", "buy_i_count", "Buy_CountI", "buy_count_i"),
            "sell_real_count": n("sell_I_Count", "sellICount", "sell_i_count", "Sell_CountI", "sell_count_i"),
            "buy_legal_count": n("buy_N_Count", "buyNCount", "buy_n_count", "Buy_CountN", "buy_count_n"),
            "sell_legal_count": n("sell_N_Count", "sellNCount", "sell_n_count", "Sell_CountN", "sell_count_n"),
        })
    cleaned.sort(key=lambda row: row["date_sort"])
    return cleaned[-252:]


def sum_field(rows, field):
    vals = [row.get(field) for row in rows if row.get(field) is not None]
    return sum(vals) if vals else None


def ratio_pct(num, den):
    if num is None or den is None or den <= 0:
        return None
    return round(num / den * 100, 3)


def flow_window(rows):
    brv = sum_field(rows, "buy_real_value")
    srv = sum_field(rows, "sell_real_value")
    blv = sum_field(rows, "buy_legal_value")
    slv = sum_field(rows, "sell_legal_value")
    brvol = sum_field(rows, "buy_real_volume")
    srvol = sum_field(rows, "sell_real_volume")
    real_net = brv - srv if brv is not None and srv is not None else None
    legal_net = blv - slv if blv is not None and slv is not None else None
    net_volume = brvol - srvol if brvol is not None and srvol is not None else None
    bcount = sum_field(rows, "buy_real_count")
    scount = sum_field(rows, "sell_real_count")
    buyer_value = brv / bcount if brv is not None and bcount and bcount > 0 else None
    seller_value = srv / scount if srv is not None and scount and scount > 0 else None
    per_capita = buyer_value / seller_value if buyer_value is not None and seller_value and seller_value > 0 else None
    observed = [r for r in rows if r.get("buy_real_value") is not None and r.get("sell_real_value") is not None]
    positive_days = sum(r["buy_real_value"] > r["sell_real_value"] for r in observed)
    return {
        "sessions_available": len(rows),
        "sessions_with_real_values": len(observed),
        "real_value_coverage_ratio": round(len(observed) / len(rows), 3) if rows else None,
        "latest_date_raw": rows[-1]["date_raw"] if rows else None,
        "real_net_value": real_net,
        "real_net_value_pct_of_real_turnover": ratio_pct(real_net, brv + srv if brv is not None and srv is not None else None),
        "real_net_volume": net_volume,
        "legal_net_value": legal_net,
        "positive_real_net_days_ratio": round(positive_days / len(observed), 3) if observed else None,
        "real_buyer_value_per_buyer": round(buyer_value, 2) if buyer_value is not None else None,
        "real_seller_value_per_seller": round(seller_value, 2) if seller_value is not None else None,
        "real_buyer_to_seller_average_value_ratio": round(per_capita, 3) if per_capita is not None else None,
    }


def score_money_flow(history):
    if len(history) < 20:
        return {"score": None, "status": "داده ناکافی برای امتیازدهی", "evidence": ["کمتر از ۲۰ جلسهٔ معتبر"], "windows": {}}
    windows = {str(n): flow_window(history[-n:]) for n in FLOW_WINDOWS if len(history) >= n}
    w20 = windows.get("20", {})
    if not w20 or w20.get("real_net_value") is None or (w20.get("real_value_coverage_ratio") or 0) < 0.80:
        return {"score": None, "status": "دادهٔ ارزش خرید و فروش حقیقی ناکافی یا ناقص است", "evidence": ["پوشش داده کمتر از ۸۰٪ است"], "windows": windows}

    score, evidence = 0, []
    def positive(window, key, pts, label):
        nonlocal score
        value = window.get(key)
        if value is not None and value > 0:
            score += pts
            evidence.append(label)

    positive(windows.get("5", {}), "real_net_value", 5, "خرید خالص حقیقی در ۵ جلسه")
    positive(w20, "real_net_value", 15, "خرید خالص حقیقی در ۲۰ جلسه")
    ratio20 = w20.get("real_net_value_pct_of_real_turnover")
    if ratio20 is not None and ratio20 >= 3:
        score += 5
        evidence.append("شدت ورود پول حقیقی در بازه ۲۰ جلسه بالا است")
    positive(windows.get("60", {}), "real_net_value", 15, "خرید خالص حقیقی در ۶۰ جلسه")
    positive(windows.get("120", {}), "real_net_value", 10, "خرید خالص حقیقی در ۱۲۰ جلسه")
    positive(windows.get("252", {}), "real_net_value", 10, "خرید خالص حقیقی در حدود یک سال معاملاتی")
    d60 = windows.get("60", {}).get("positive_real_net_days_ratio")
    if d60 is not None and d60 >= 0.55:
        score += 10
        evidence.append("بیش از ۵۵٪ جلسات ۶۰روزه خرید خالص حقیقی دارند")
    power = w20.get("real_buyer_to_seller_average_value_ratio")
    if power is not None and power >= 1.2:
        score += 15
        evidence.append("قدرت خرید سرانه حقیقی بیش از ۱٫۲ برابر فروش سرانه است")
    elif power is not None and power > 1.0:
        score += 5
        evidence.append("برتری خفیف خرید سرانه حقیقی")
    net60 = windows.get("60", {}).get("real_net_value")
    net20 = w20.get("real_net_value")
    if net20 is not None and net60 is not None and net20 > 0 and net60 > 0 and net20 / 20 > net60 / 60 * 1.1:
        score += 10
        evidence.append("سرعت ورود پول در ۲۰ جلسه اخیر شتاب گرفته")
    score = min(100, score)
    if len(history) < 252:
        status = "تاریخچه کمتر از حدود یک سال؛ نتیجه موقت"
    elif score >= 70:
        status = "نشانه‌های قوی‌تر ورود پول حقیقی؛ نیازمند تأیید"
    elif score >= 55:
        status = "نشانه‌های متوسط ورود پول حقیقی"
    elif score >= 40:
        status = "جریان پول مختلط یا ضعیف"
    else:
        status = "شواهد کافی برای ورود پول حقیقی دیده نمی‌شود"
    if not evidence:
        evidence = ["شواهد مثبت کافی در معیارهای ثبت‌شده نیست"]
    return {
        "score": score, "status": status, "evidence": evidence, "windows": windows,
        "interpretation_note": "جریان حقیقی به‌تنهایی اثبات‌کننده پول هوشمند نیست؛ باید با حجم، قیمت، تمرکز معاملات و اطلاعات بنیادی تأیید شود.",
    }


def find_instrument(symbol):
    if TSETMC_UNAVAILABLE:
        return None
    url = f"{TSETMC_BASE}/Instrument/GetInstrumentSearch/{urllib.parse.quote(symbol, safe='')}"
    payload = request_json(url, delay=TSETMC_DELAY)
    items = find_array(payload, ("instrumentSearch",))
    target = normalize_text(symbol)
    exact = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ticker = item.get("lVal18AFC") or item.get("lVal18") or ""
        # TSETMC lVal18AFC is the actual ticker; do NOT append the chart suffix '1'.
        if normalize_text(ticker) != target:
            continue
        ins_code = item.get("insCode") or item.get("inscode")
        isin = str(item.get("cIsin") or item.get("cisin") or "").strip()
        if not ins_code:
            continue
        exact.append({
            "ins_code": str(ins_code), "isin": isin, "ticker_tsetmc": ticker,
            "company_tsetmc": item.get("lVal30", ""),
            "instrument_kind": "common_equity" if isin.startswith(COMMON_EQUITY_ISIN_PREFIXES) else "other_or_unconfirmed",
            "mapped_at_utc": utc_now(),
        })
    exact.sort(key=lambda item: (item["instrument_kind"] != "common_equity", item.get("isin", "")))
    return exact[0] if exact else None


def get_tsetmc_snapshot(ins_code):
    payload = tsetmc_get(f"Instrument/GetInstrumentInfo/{urllib.parse.quote(ins_code, safe='')}")
    info = first_value(payload, ("instrumentInfo", "instrumentinfo"))
    if not isinstance(info, dict):
        info = payload if isinstance(payload, dict) else {}
    sector_node = first_value(info, ("sector",))
    sub_node = first_value(info, ("subSector", "subSectorInfo"))
    return {
        "eps": as_number(first_value(info, ("eps", "EPS"))),
        "pe": as_number(first_value(info, ("pe", "pE", "peRatio"))),
        "closing_price": as_number(first_value(info, ("pClosing", "closingPrice"))),
        "last_trade_price": as_number(first_value(info, ("pDrCotVal", "lastPrice"))),
        "capital_or_share_count": as_number(first_value(info, ("zTitad", "shareCount", "capital"))),
        "sector": first_value(info, ("lSecVal", "sectorName")) or first_value(sector_node, ("lSecVal", "name", "title")) if isinstance(sector_node, dict) else first_value(info, ("lSecVal", "sectorName")),
        "sub_sector": first_value(info, ("lSoSecVal", "subSectorName")) or first_value(sub_node, ("lSoSecVal", "name", "title")) if isinstance(sub_node, dict) else first_value(info, ("lSoSecVal", "subSectorName")),
    }


def fetch_flow(ins_code=None, symbol=None):
    if BRS_API_KEY:
        if not symbol:
            raise ValueError("symbol is required for BrsApi client-type history")
        payload = brsapi_get_json("Tsetmc/History.php", {"type": "1", "l18": symbol})
        return normalize_history(payload)
    if not ins_code:
        raise ValueError("ins_code is required for direct TSETMC history")
    payload = tsetmc_get(f"ClientType/GetClientTypeHistory/{urllib.parse.quote(ins_code, safe='')}")
    return normalize_history(payload)


class ScriptCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_script = False
        self.parts = []
        self.scripts = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self.in_script = True
            self.parts = []
    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_script:
            self.scripts.append("".join(self.parts))
            self.in_script = False
    def handle_data(self, data):
        if self.in_script:
            self.parts.append(data)


class HTMLTableRows(HTMLParser):
    """Extract visible HTML table rows, including Codal Excel-as-HTML exports."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.current_row = []
        self.current_cell = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self.table_depth += 1
        elif self.table_depth and tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.table_depth and self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []
        elif self.table_depth and self.in_cell and tag == "br":
            self.current_cell.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self.in_cell:
            value = re.sub(r"\s+", " ", " ".join(self.current_cell)).strip()
            self.current_row.append(value)
            self.current_cell = []
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if any(self.current_row):
                self.rows.append(self.current_row)
            self.current_row = []
            self.in_row = False
        elif tag == "table" and self.table_depth:
            self.table_depth -= 1

    def handle_data(self, data):
        if self.in_cell and data:
            self.current_cell.append(data)


def extract_html_table_rows(page_text):
    parser = HTMLTableRows()
    try:
        parser.feed(page_text)
        parser.close()
    except Exception:
        return []
    # Avoid huge mostly-empty tables and repeated header/layout fragments.
    return [row for row in parser.rows if len(row) >= 2 and any(cell.strip() for cell in row)]


class VisibleTextCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.text_parts = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript"}:
            self.hidden_depth += 1
    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript"} and self.hidden_depth:
            self.hidden_depth -= 1
    def handle_data(self, data):
        if not self.hidden_depth and data.strip():
            self.text_parts.append(re.sub(r"\s+", " ", data).strip())


def fetch_report_page(url):
    """Fetch a report with a short, per-host breaker so one blocked host does not block another."""
    if not url:
        raise ValueError("report URL missing")
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in CODAL_REPORT_UNAVAILABLE:
        raise RuntimeError(f"report host temporarily disabled after repeated failures: {host}")

    last_error = None
    for attempt in range(REPORT_RETRIES + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json,*/*",
                "Referer": "https://www.codal.ir/",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REPORT_TIMEOUT) as response:
                body = response.read()
            if not body:
                raise ValueError("empty report response")
            CODAL_REPORT_FAILURES[host] = 0
            time.sleep(CODAL_DELAY)
            return body.decode("utf-8-sig", errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (403, 404, 429, 451):
                CODAL_REPORT_UNAVAILABLE.add(host)
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
        if attempt < REPORT_RETRIES:
            time.sleep(0.25)

    CODAL_REPORT_FAILURES[host] = CODAL_REPORT_FAILURES.get(host, 0) + 1
    if CODAL_REPORT_FAILURES[host] >= 2:
        CODAL_REPORT_UNAVAILABLE.add(host)
    raise RuntimeError(f"report fetch failed for {host}: {last_error}")


def extract_datasource(page_text):
    parser = ScriptCollector()
    parser.feed(page_text)
    decoder = json.JSONDecoder()
    for script in parser.scripts:
        for match in re.finditer(r"\bdatasource\s*=\s*", script, flags=re.I):
            start = script.find("{", match.end())
            if start < 0:
                continue
            try:
                obj, _ = decoder.raw_decode(script[start:])
                if isinstance(obj, dict) and ("sheets" in obj or "tables" in obj):
                    return obj
            except json.JSONDecodeError:
                continue
    # A few report templates embed datasource outside script tags.
    for match in re.finditer(r"\bdatasource\s*=\s*", page_text, flags=re.I):
        start = page_text.find("{", match.end())
        if start < 0:
            continue
        try:
            obj, _ = decoder.raw_decode(page_text[start:])
            if isinstance(obj, dict) and ("sheets" in obj or "tables" in obj):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def cell_field(cell, names):
    return first_value(cell, names) if isinstance(cell, dict) else None


def cells_to_rows(datasource):
    """Build ordered rows from common Codal datasource cell schemas."""
    found_rows = []
    seen_tables = set()

    def walk(node):
        if isinstance(node, dict):
            cells = first_value(node, ("cells", "Cells"))
            if isinstance(cells, list) and cells:
                signature = id(cells)
                if signature not in seen_tables:
                    seen_tables.add(signature)
                    grouped = {}
                    implicit = []
                    for position, cell in enumerate(cells):
                        if isinstance(cell, list):
                            implicit.append([str(v) for v in cell if v is not None])
                            continue
                        if not isinstance(cell, dict):
                            implicit.append([str(cell)])
                            continue
                        value = cell_field(cell, ("value", "v", "text", "cellValue", "displayValue", "valueText", "formattedValue"))
                        if value is None:
                            value = cell_field(cell, ("value2", "rawValue", "Value"))
                        if isinstance(value, (dict, list)):
                            value = json.dumps(value, ensure_ascii=False)
                        if value is None:
                            continue
                        r = cell_field(cell, ("rowIndex", "row", "r", "rowId", "y"))
                        c = cell_field(cell, ("columnIndex", "col", "column", "c", "x"))
                        if r is None or c is None:
                            name = cell_field(cell, ("cellName", "address", "name"))
                            if name:
                                m = re.fullmatch(r"([A-Z]+)(\d+)", str(name).strip(), flags=re.I)
                                if m:
                                    letters, digits = m.group(1).upper(), m.group(2)
                                    col_number = 0
                                    for letter in letters:
                                        col_number = col_number * 26 + (ord(letter) - ord("A") + 1)
                                    if c is None:
                                        c = col_number - 1
                                    if r is None:
                                        r = int(digits) - 1
                        if r is None:
                            implicit.append([str(value)])
                        else:
                            try:
                                ri = int(r)
                                ci = int(c) if c is not None else position
                            except (TypeError, ValueError):
                                implicit.append([str(value)])
                                continue
                            grouped.setdefault(ri, []).append((ci, str(value)))
                    for ri in sorted(grouped):
                        found_rows.append([v for _, v in sorted(grouped[ri], key=lambda pair: pair[0])])
                    found_rows.extend(row for row in implicit if row)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                if isinstance(value, (dict, list)):
                    walk(value)
    walk(datasource)
    # Fallback for row-based arrays without cell coordinates.
    if not found_rows:
        def find_matrix(node):
            if isinstance(node, list) and node and all(isinstance(x, list) for x in node):
                return [[str(c) for c in row if c is not None] for row in node]
            if isinstance(node, dict):
                for value in node.values():
                    matrix = find_matrix(value)
                    if matrix:
                        return matrix
            if isinstance(node, list):
                for value in node:
                    matrix = find_matrix(value)
                    if matrix:
                        return matrix
            return []
        found_rows = find_matrix(datasource)
    return found_rows


def extract_metric_rows(datasource):
    rows = cells_to_rows(datasource)
    extracted = {}
    for metric, labels in METRIC_LABELS.items():
        candidates = []
        normalized_labels = sorted((normalize_text(label) for label in labels), key=len, reverse=True)
        for row in rows:
            joined = " | ".join(str(c) for c in row)
            norm_row = normalize_text(joined)
            if metric == "total_assets" and any(x in norm_row for x in ("داراییهایجاری", "داراییهایغیرجاری")) and "جمعکلداراییها" not in norm_row:
                continue
            if metric == "total_liabilities" and any(x in norm_row for x in ("بدهیهایجاری", "بدهیهایغیرجاری")) and "جمعکلبدهیها" not in norm_row:
                continue
            matched_label = next((label for label in normalized_labels if label in norm_row), None)
            if not matched_label:
                continue
            # Prefer numeric cells after the label cell, but retain all column values for review.
            label_position = next((i for i, c in enumerate(row) if matched_label in normalize_text(c)), 0)
            numeric_cells = []
            for cell in row[label_position + 1:] + row[:label_position]:
                number = as_number(cell)
                if number is not None:
                    numeric_cells.append({"raw": str(cell), "value": number})
            candidates.append({
                "matched_label": matched_label,
                "row_text": joined[:400],
                "numeric_values": numeric_cells[:6],
                "primary_value_candidate": numeric_cells[0]["value"] if numeric_cells else None,
            })
        if candidates:
            extracted[metric] = candidates[0]
    return extracted, rows


def metrics_and_ratios(extracted):
    def val(key):
        item = extracted.get(key) or {}
        return item.get("primary_value_candidate")
    revenue = val("revenue")
    gross_profit = val("gross_profit")
    operating_profit = val("operating_profit")
    net_profit = val("net_profit")
    assets = val("total_assets")
    liabilities = val("total_liabilities")
    op_cash = val("operating_cash_flow")
    current_assets = val("current_assets")
    current_liabilities = val("current_liabilities")
    finance_cost = val("finance_cost")
    ratios = {
        "gross_margin_pct": round(gross_profit / revenue * 100, 2) if revenue and gross_profit is not None else None,
        "operating_margin_pct": round(operating_profit / revenue * 100, 2) if revenue and operating_profit is not None else None,
        "net_margin_pct": round(net_profit / revenue * 100, 2) if revenue and net_profit is not None else None,
        "liabilities_to_assets_pct": round(liabilities / assets * 100, 2) if assets and liabilities is not None else None,
        "current_ratio": round(current_assets / current_liabilities, 3) if current_liabilities and current_assets is not None else None,
        "operating_cash_flow_to_net_profit": round(op_cash / net_profit, 3) if net_profit and op_cash is not None else None,
        "finance_cost_to_operating_profit": round(finance_cost / operating_profit, 3) if operating_profit and finance_cost is not None else None,
    }
    return ratios


def report_period_key(report):
    title = normalize_text(report.get("title", ""))
    # Annual statements may include the fiscal year end, while interim titles specify 3/6/9 months.
    period_match = re.search(r"دوره(3|6|9|12)ماهه", title)
    if period_match:
        return period_match.group(1)
    if "سالمالیمنتهی" in title:
        return "annual"
    return "other"


def report_year_end(report):
    title = str(report.get("title", "")).translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    )
    match = re.search(r"((?:13|14)\d{2})[\-/](0[1-9]|1[0-2])[\-/](0[1-9]|[12]\d|3[01])", title)
    return "-".join(match.groups()) if match else None


def choose_financial_reports(reports):
    """Select current interim statements first, then annual comparators.

    A previous ordering bug placed two annual reports before the interim report while
    enforcing a two-report cap, which could silently exclude every 3/6/9-month statement.
    Keep the latest available statement for each interim period plus two annual statements.
    """
    reports = [
        r for r in reports
        if (r.get("url") or r.get("excel_url"))
        and (r.get("has_html") or r.get("has_excel") or "صورت" in r.get("title", ""))
        and "صورت" in r.get("title", "")
    ]
    reports.sort(key=lambda r: str(r.get("date") or ""), reverse=True)

    annuals = [r for r in reports if report_period_key(r) == "annual"]
    # Keep one consistent parent-company/consolidated series and avoid duplicate filings for the same year.
    standalone = [r for r in annuals if "تلفیقی" not in normalize_text(r.get("title", ""))]
    if len({report_year_end(r) for r in standalone if report_year_end(r)}) >= 2:
        annuals = standalone
    unique_annuals, annual_keys = [], set()
    for report in annuals:
        key = report_year_end(report) or normalize_text(report.get("title", ""))[:60]
        if key not in annual_keys:
            annual_keys.add(key)
            unique_annuals.append(report)

    interim = [r for r in reports if report_period_key(r) in ("3", "6", "9")]
    selected, seen = [], set()

    # Prioritize the latest two 3-month reports so year-over-year comparisons
    # remain possible, plus the latest 6- and 9-month statements and two annual
    # comparators. All six records are cached after a successful parse.
    candidates = [r for r in interim if report_period_key(r) == "3"][:2]
    candidates.extend([r for r in interim if report_period_key(r) == "6"][:1])
    candidates.extend([r for r in interim if report_period_key(r) == "9"][:1])
    candidates.extend(unique_annuals[:2])

    for report in candidates:
        key = report.get("url") or report.get("excel_url") or report.get("tracing_no") or report.get("title")
        if key in seen:
            continue
        selected.append(report)
        seen.add(key)
        if len(selected) >= MAX_FINANCIAL_DETAILS_PER_STOCK:
            break

    # Order selected rows by publication date for downstream latest-report scoring,
    # while the result still contains a quarter report if one was available.
    selected.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return selected


def find_contexts(strings, keyword_groups, max_each=8):
    normalized = [(normalize_text(k), k) for k in keyword_groups]
    results = []
    seen = set()
    for text in strings:
        original = re.sub(r"\s+", " ", str(text or "")).strip()
        norm = normalize_text(original)
        if not norm:
            continue
        matched = [label for label, original_label in normalized if label and label in norm]
        if matched:
            key = original[:300]
            if key not in seen:
                seen.add(key)
                results.append({"text": original[:700], "matched_keywords": matched[:5]})
                if len(results) >= max_each:
                    break
    return results


def build_project_assessment(report, page_text, datasource):
    extracted_text = []
    visible = VisibleTextCollector()
    visible.feed(page_text)
    for visible_part in visible.text_parts:
        extracted_text.extend(re.split(r"[\r\n؛;]+", visible_part))
    if datasource:
        extracted_rows = cells_to_rows(datasource)
        extracted_text.extend(" | ".join(row) for row in extracted_rows if row)
        def collect_values(node):
            if isinstance(node, dict):
                for key, val in node.items():
                    if isinstance(val, (str, int, float)) and key.casefold() in {"value", "v", "text", "title", "name", "cellvalue", "displayvalue"}:
                        extracted_text.append(str(val))
                    elif isinstance(val, (dict, list)):
                        collect_values(val)
            elif isinstance(node, list):
                for val in node:
                    collect_values(val)
        collect_values(datasource)
    # Avoid very long JavaScript payloads and repeated near-identical table strings.
    extracted_text = list(dict.fromkeys(x[:1500] for x in extracted_text if x and len(x) < 2000))

    development_terms = (
        "طرح", "پروژه", "پیشرفت فیزیکی", "بهره برداری", "راه اندازی", "ظرفیت اسمی",
        "سرمایه گذاری", "هزینه تکمیل", "بازده داخلی", "دوره بازگشت سرمایه",
    )
    contexts = find_contexts(extracted_text, development_terms, max_each=25)
    # Keep raw evidence; do not promote inferred numbers to verified facts.
    all_text = "\n".join(extracted_text)
    translated = all_text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    progress = []
    for match in re.finditer(r"(?:پیشرفت(?:\s|‌)*(?:فیزیکی)?[^\n%]{0,80}?)(\d{1,3}(?:[.,]\d+)?)\s*(?:درصد|%)|(?:(\d{1,3}(?:[.,]\d+)?)\s*(?:درصد|%).{0,80}?پیشرفت(?:\s|‌)*(?:فیزیکی)?)", translated, flags=re.I):
        value = as_number(match.group(1) or match.group(2))
        if value is not None and 0 <= value <= 100:
            progress.append({"value_pct_candidate": value, "context": translated[max(0, match.start()-100):min(len(translated), match.end()+100)].replace("\n", " ")})
            if len(progress) >= 10:
                break
    capex_terms = ("مبلغ سرمایه گذاری", "برآورد هزینه", "هزینه تکمیل", "کل سرمایه گذاری", "سرمایه گذاری مورد نیاز", "مانده مخارج طرح")
    commissioning_terms = ("زمان بهره برداری", "تاریخ بهره برداری", "برنامه بهره برداری", "راه اندازی", "بهره برداری آزمایشی")
    capacity_terms = ("ظرفیت اسمی", "افزایش ظرفیت", "ظرفیت تولید", "تن در سال", "متر مکعب در روز")
    return_terms = ("نرخ بازده داخلی", "دوره بازگشت سرمایه", "بازده سرمایه گذاری", "نرخ بازده")
    capex_contexts = find_contexts(extracted_text, capex_terms, max_each=8)
    commissioning = find_contexts(extracted_text, commissioning_terms, max_each=8)
    capacity = find_contexts(extracted_text, capacity_terms, max_each=8)
    returns = find_contexts(extracted_text, return_terms, max_each=8)

    def numeric_evidence(items, terms, cap=10):
        output = []
        seen = set()
        norm_terms = [normalize_text(t) for t in terms]
        for item in items:
            raw = re.sub(r"\s+", " ", str(item or "")).strip()
            norm = normalize_text(raw)
            if not raw or not any(term in norm for term in norm_terms):
                continue
            translated_raw = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
            # Remove Jalali dates before collecting numeric candidates so a date such as 1406/06/30
            # is never mistaken for project capex, capacity or ROI.
            numeric_source = re.sub(
                r"(?<!\d)(?:13|14)\d{2}[/-]\d{1,2}[/-]\d{1,2}(?!\d)",
                " ",
                translated_raw,
            )
            tokens = re.findall(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?", numeric_source)
            for token in tokens:
                number = as_number(token)
                if number is None:
                    continue
                unique = (normalize_text(raw), token)
                if unique in seen:
                    continue
                seen.add(unique)
                unit = None
                for label in ("میلیارد ریال", "میلیون ریال", "هزار ریال", "ریال", "تن در سال", "تن", "متر مکعب در روز", "درصد", "%", "ماه", "روز"):
                    if normalize_text(label) in norm:
                        unit = label
                        break
                output.append({"value_candidate": number, "raw_token": token, "unit_candidate": unit, "context": raw[:500]})
                if len(output) >= cap:
                    return output
        return output

    capex_numbers = numeric_evidence(extracted_text, capex_terms)
    remaining_cost_terms = (
        "هزینه تکمیل", "مانده مخارج طرح", "مخارج باقی مانده",
        "هزینه باقی مانده", "هزینه باقیمانده", "مخارج باقیمانده",
    )
    remaining_cost_numbers = numeric_evidence(extracted_text, remaining_cost_terms)
    capacity_numbers = numeric_evidence(extracted_text, capacity_terms)
    return_numbers = numeric_evidence(extracted_text, return_terms)
    commissioning_dates = []
    date_pattern = r"(?<!\d)((?:13|14)\d{2})[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])(?!\d)"
    for item in extracted_text:
        raw = re.sub(r"\s+", " ", str(item or "")).strip()
        norm = normalize_text(raw)
        if any(normalize_text(term) in norm for term in commissioning_terms):
            translated_raw = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
            for match in re.finditer(date_pattern, translated_raw):
                commissioning_dates.append({"date_candidate": match.group(0), "context": raw[:500]})
                if len(commissioning_dates) >= 10:
                    break
        if len(commissioning_dates) >= 10:
            break
    all_norm = normalize_text(all_text)
    title_norm = normalize_text(report.get("title", ""))
    operating_phrases = (
        "به بهره برداری رسید", "بهره برداری شد", "بهره برداری از طرح آغاز شد",
        "تولید تجاری آغاز شد", "آغاز تولید محصول",
    )
    trial_phrases = ("تولید آزمایشی", "بهره برداری آزمایشی", "راه اندازی آزمایشی")
    commissioning_phrases = ("شروع راه اندازی", "آغاز راه اندازی", "در مرحله راه اندازی", "نصب و راه اندازی")
    execution_phrases = ("عملیات اجرایی آغاز", "در حال احداث", "در حال ساخت", "پیشرفت فیزیکی")
    contract_phrases = ("قرارداد اجرای طرح", "انعقاد قرارداد", "دریافت مجوز", "مجوز احداث")
    if any(normalize_text(phrase) in all_norm for phrase in operating_phrases):
        project_stage = "بهره‌برداری/تولید در متن گزارش صریحاً ذکر شده"
        readiness = "شواهد آمادگی بهره‌برداری: بالا؛ تداوم تولید باید بررسی شود"
    elif any(normalize_text(phrase) in all_norm for phrase in trial_phrases):
        project_stage = "تولید یا بهره‌برداری آزمایشی گزارش شده"
        readiness = "شواهد آمادگی بهره‌برداری: نسبتاً بالا؛ تأیید تولید تجاری لازم است"
    elif any(normalize_text(phrase) in all_norm for phrase in commissioning_phrases):
        project_stage = "مرحلهٔ نصب/راه‌اندازی طبق متن گزارش"
        readiness = "شواهد آمادگی بهره‌برداری: متوسط رو به بالا؛ موانع راه‌اندازی بررسی شود"
    elif progress:
        max_progress = max(item["value_pct_candidate"] for item in progress)
        project_stage = "پیشرفت فیزیکی گزارش شده؛ وضعیت اجرایی نیازمند تطبیق با آخرین گزارش"
        readiness = (
            "شواهد آمادگی بهره‌برداری: نسبتاً بالا، اما قطعی نیست"
            if max_progress >= 80 else
            "شواهد آمادگی بهره‌برداری: متوسط/نامشخص بر اساس درصد پیشرفت گزارش‌شده"
        )
    elif any(normalize_text(phrase) in all_norm for phrase in execution_phrases):
        project_stage = "اجرای پروژه ذکر شده؛ درصد پیشرفت استخراج نشده"
        readiness = "شواهد آمادگی بهره‌برداری: نامشخص؛ درصد پیشرفت و هزینهٔ باقی‌مانده لازم است"
    elif any(normalize_text(phrase) in all_norm or normalize_text(phrase) in title_norm for phrase in contract_phrases):
        project_stage = "قرارداد/مجوز/مرحلهٔ مقدماتی در شواهد دیده می‌شود"
        readiness = "شواهد آمادگی بهره‌برداری: پایین تا نامشخص؛ شروع/پیشرفت اجرا باید تأیید شود"
    else:
        project_stage = "مرحلهٔ پروژه از متن بازیابی‌شده قابل تعیین نیست"
        readiness = "شواهد آمادگی بهره‌برداری: نامشخص"

    return {
        "symbol": report.get("symbol"),
        "company": report.get("company"),
        "title": report.get("title"),
        "date": report.get("date"),
        "url": report.get("url"),
        "matched_keywords": report.get("project_keywords", []),
        "full_report_fetched": True,
        "project_stage_assessment": project_stage,
        "qualitative_operation_readiness": readiness,
        "operation_readiness_is_statistical_probability": False,
        "physical_progress_pct_candidates": progress,
        "capex_evidence": capex_contexts,
        "capex_numeric_candidates": capex_numbers,
        "remaining_cost_evidence": find_contexts(extracted_text, remaining_cost_terms, max_each=8),
        "remaining_cost_numeric_candidates": remaining_cost_numbers,
        "commissioning_evidence": commissioning,
        "commissioning_date_candidates": commissioning_dates,
        "capacity_evidence": capacity,
        "capacity_numeric_candidates": capacity_numbers,
        "return_evidence": returns,
        "return_numeric_candidates": return_numbers,
        "project_context_snippets": contexts,
        "assessment_status": "full report inspected; extracted values are candidates and require source-level review",
        "cost_unit": "not standardized; check source report unit",
        "roi_status": "not scored unless explicit evidence is verified",
    }


def detail_financial_report(report):
    result = dict(report)
    candidates = []
    if report.get("excel_url"):
        candidates.append(("excel", report["excel_url"]))
    if report.get("url"):
        candidates.append(("html", report["url"]))
    if not candidates:
        result["detail_status"] = "report URL unavailable"
        return result

    errors = []
    for content_kind, content_url in candidates:
        try:
            page = fetch_report_page(content_url)
            datasource = extract_datasource(page)
            if not datasource:
                table_rows = extract_html_table_rows(page)
                if table_rows:
                    datasource = {"tables": table_rows}
            if not datasource:
                errors.append(f"{content_kind}: no statement table found")
                continue
            metrics, rows = extract_metric_rows(datasource)
            if not metrics:
                errors.append(f"{content_kind}: table parsed but financial labels not mapped")
                continue
            result["detail_status"] = "parsed_with_heuristic_cell_mapping"
            result["detail_source_kind"] = content_kind
            result["detail_source_url"] = content_url
            result["financial_metrics"] = metrics
            result["ratios"] = metrics_and_ratios(metrics)
            result["parsed_row_count"] = len(rows)
            result["period_type"] = report_period_key(report)
            result["fiscal_year_end"] = report_year_end(report)
            result["consolidation_mode"] = "consolidated" if "تلفیقی" in normalize_text(report.get("title", "")) else "standalone"
            unit_match = re.search(r"(میلیون\s*ریال|هزار\s*ریال|مبالغ?\s*(?:به|برحسب)\s*ریال|ریال)", page)
            result["report_unit_hint"] = unit_match.group(1) if unit_match else None
            result["source_units_note"] = "Raw units vary by report; ratios are computed from within-report values only. Check units and row labels before use."
            return result
        except Exception as exc:
            errors.append(f"{content_kind}: {str(exc)[:160]}")

    result["detail_status"] = "detail_fetch_failed_or_unparsed"
    result["detail_error"] = "; ".join(errors)[:600]
    return result


def score_fundamentals(reports):
    parsed = [r for r in reports if isinstance(r.get("ratios"), dict) and r.get("financial_metrics")]
    if not parsed:
        return {"score": None, "status": "دادهٔ استانداردشدهٔ کافی از صورت‌های مالی استخراج نشد", "evidence": []}
    parsed.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    latest = parsed[0]
    ratios = latest.get("ratios", {})
    score, evidence = 0, []
    latest_metrics = latest.get("financial_metrics") or {}

    def metric_value(report, key):
        return ((report.get("financial_metrics") or {}).get(key) or {}).get("primary_value_candidate")

    net_profit = metric_value(latest, "net_profit")
    operating_cash = metric_value(latest, "operating_cash_flow")
    margin = ratios.get("net_margin_pct")
    cash_conversion = ratios.get("operating_cash_flow_to_net_profit")
    current_ratio = ratios.get("current_ratio")
    leverage = ratios.get("liabilities_to_assets_pct")
    if net_profit is not None and net_profit > 0:
        score += 15; evidence.append("سود خالص مثبت در صورت مالی استخراج‌شده")
    if margin is not None and margin > 0:
        score += 10; evidence.append("حاشیه سود خالص مثبت")
    if margin is not None and margin >= 15:
        score += 10; evidence.append("حاشیه سود خالص دست‌کم ۱۵٪")
    op_margin = ratios.get("operating_margin_pct")
    if op_margin is not None and op_margin > 0:
        score += 10; evidence.append("حاشیه سود عملیاتی مثبت")
    if operating_cash is not None and operating_cash > 0:
        score += 15; evidence.append("جریان نقد عملیاتی مثبت")
    if cash_conversion is not None and cash_conversion >= 0.8:
        score += 20; evidence.append("پوشش سود خالص با جریان نقد عملیاتی قابل قبول")
    elif cash_conversion is not None and cash_conversion < 0:
        evidence.append("هشدار: جریان نقد عملیاتی و سود خالص هم‌جهت نیستند")
    if current_ratio is not None and current_ratio >= 1.2:
        score += 10; evidence.append("نسبت جاری بیش از ۱٫۲")
    if leverage is not None and leverage <= 60:
        score += 10; evidence.append("نسبت بدهی به دارایی حداکثر ۶۰٪")
    elif leverage is not None and leverage > 85:
        evidence.append("هشدار نسبت بالای بدهی به دارایی؛ نیازمند مقایسه بخشی")

    annuals = [r for r in parsed if r.get("period_type") == "annual"]
    annuals.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    # Only compare reports from the same consolidation series and known matching units.
    annual_series = []
    if annuals:
        preferred_mode = annuals[0].get("consolidation_mode")
        annual_series = [r for r in annuals if r.get("consolidation_mode") == preferred_mode]
        unique_periods, unique_series = set(), []
        for report in annual_series:
            key = report.get("fiscal_year_end") or report.get("date")
            if key not in unique_periods:
                unique_periods.add(key)
                unique_series.append(report)
        annual_series = unique_series
    growth = {}
    growth_points = 0
    if len(annual_series) >= 2:
        current, previous = annual_series[0], annual_series[1]
        current_unit, previous_unit = current.get("report_unit_hint"), previous.get("report_unit_hint")
        if current_unit and previous_unit and normalize_text(current_unit) == normalize_text(previous_unit):
            for key in ("revenue", "net_profit", "operating_cash_flow"):
                now_value, old_value = metric_value(current, key), metric_value(previous, key)
                if now_value is not None and old_value not in (None, 0):
                    growth[key + "_yoy_pct"] = round((now_value / old_value - 1) * 100, 2)
            rev_growth = growth.get("revenue_yoy_pct")
            profit_growth = growth.get("net_profit_yoy_pct")
            cash_growth = growth.get("operating_cash_flow_yoy_pct")
            if rev_growth is not None and rev_growth > 0:
                growth_points += 5; evidence.append("رشد سال‌به‌سال درآمد مثبت در دو گزارش سالانه")
            if rev_growth is not None and rev_growth >= 15:
                growth_points += 5; evidence.append("رشد سال‌به‌سال درآمد حداقل ۱۵٪")
            if profit_growth is not None and profit_growth > 0:
                growth_points += 10; evidence.append("رشد سال‌به‌سال سود خالص مثبت")
            if cash_growth is not None and cash_growth > 0:
                growth_points += 5; evidence.append("رشد سال‌به‌سال جریان نقد عملیاتی مثبت")
        else:
            growth["status"] = "واحد گزارش سالانه با اطمینان تطبیق داده نشد؛ رشد امتیازدهی نشد"
    else:
        growth["status"] = "دو صورت مالی سالانهٔ هم‌سری قابل تحلیل موجود نیست؛ رشد سال‌به‌سال امتیازدهی نشد"
    score = min(100, score + growth_points)
    annual_count = len(annual_series)
    coverage = "قابل‌مقایسه‌تر" if annual_count >= 2 and isinstance(growth.get("revenue_yoy_pct"), (int, float)) else "مقدماتی"
    return {
        "score": score,
        "status": "امتیاز مقدماتی؛ الگوی جداول و واحدهای گزارش باید بازبینی شوند",
        "evidence": evidence,
        "latest_report_title": latest.get("title"),
        "latest_report_date": latest.get("date"),
        "annual_reports_parsed": annual_count,
        "annual_growth_candidates_pct": growth,
        "coverage_grade": coverage,
        "caveat": "این امتیاز غربالگری اولیه است، نه ارزش‌گذاری نهایی. مقایسه دوره‌ها/واحدها باید درست باشد؛ بانک‌ها و شرکت‌های مالی معیارهای متفاوت دارند.",
    }


def report_cache_key(symbol, report):
    # URL is retained in both financial and project detail records; prefer it so cache keys match.
    identity = report.get("url") or report.get("tracing_no") or (
        str(report.get("title") or "") + "|" + str(report.get("date") or "")
    )
    return str(symbol), str(identity)


def merge_cached_detail(cached, latest_report, symbol, company):
    merged = dict(cached)
    for key, value in latest_report.items():
        if value is not None and value != "":
            merged[key] = value
    merged["symbol"] = symbol
    merged["company"] = company
    merged["detail_cache_reused"] = True
    return merged



def probe_sources():
    """Small preflight: validate both API endpoints before the 741-symbol run."""
    if not BRS_API_KEY:
        raise RuntimeError(
            "BRS_API_KEY is missing. Request a key from https://api.brsapi.ir/Panel/register.html "
            "and save it in GitHub repository Actions secrets as BRS_API_KEY."
        )
    sample_symbol = "فملی"
    print(f"Preflight: checking BrsApi announcement endpoint for {sample_symbol}...")
    reports = brsapi_codal_search(sample_symbol)
    print(f"Announcement records returned: {len(reports)}")
    if not reports:
        raise RuntimeError(
            "BrsApi returned no announcement records for فملی. Check the key, provider response schema, and quota; full scan stopped."
        )

    print(f"Preflight: checking BrsApi حقیقی/حقوقی history for {sample_symbol}...")
    history = fetch_flow(symbol=sample_symbol)
    print(f"Money-flow sessions returned: {len(history)}")
    if len(history) < 20:
        raise RuntimeError(
            f"BrsApi returned only {len(history)} usable money-flow rows for فملی; full scan stopped."
        )

    print(f"Latest recognized money-flow date: {history[-1]['date_raw'] if history else 'N/A'}")
    print(f"At least 252 sessions: {len(history) >= 252}")
    print("BrsApi preflight passed; full-universe scan may begin.")

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        probe_sources()
        return
    if not BRS_API_KEY:
        raise RuntimeError(
            "BRS_API_KEY is not configured. GitHub-hosted runners are being blocked by direct Codal/TSETMC endpoints. "
            "Request a BrsApi key, add it under Settings > Secrets and variables > Actions as BRS_API_KEY, then rerun."
        )
    if not UNIVERSE_FILE.is_file():
        raise FileNotFoundError(f"Universe file not found: {UNIVERSE_FILE}")
    universe = read_json(UNIVERSE_FILE)
    stocks = universe.get("stocks", [])
    if not isinstance(stocks, list):
        raise ValueError("Invalid stock_universe.json: 'stocks' must be a list")

    cached_map = {}
    if MAP_FILE.is_file():
        try:
            cached_map = read_json(MAP_FILE).get("symbols", {})
        except (OSError, json.JSONDecodeError, AttributeError):
            cached_map = {}
    symbol_map = dict(cached_map)

    # Reuse downloaded report details across scheduled runs; published filings are immutable.
    old_stocks = []
    if OUTPUT_FILE.is_file():
        try:
            previous_output = read_json(OUTPUT_FILE)
            old_stocks = previous_output.get("stocks", []) if isinstance(previous_output, dict) else []
        except (OSError, json.JSONDecodeError, AttributeError):
            old_stocks = []
    financial_detail_cache = {}
    project_detail_cache = {}
    for old_stock in old_stocks:
        old_symbol = str(old_stock.get("symbol", "")).strip()
        for detail in old_stock.get("financial_reports", []) or []:
            if isinstance(detail, dict) and detail.get("detail_status") == "parsed_with_heuristic_cell_mapping":
                financial_detail_cache[report_cache_key(old_symbol, detail)] = detail
        for detail in old_stock.get("project_disclosure_candidates", []) or []:
            if isinstance(detail, dict) and detail.get("full_report_fetched") is True:
                project_detail_cache[report_cache_key(old_symbol, detail)] = detail

    results = []
    project_watchlist, financial_watchlist, important_watchlist = [], [], []
    counts = {
        "codal_general_search_success": 0,
        "codal_financial_search_success": 0,
        "statement_reports_detailed": 0,
        "project_reports_detailed": 0,
        "tsetmc_mapping_success": 0,
        "confirmed_common_equities": 0,
        "flow_history_success": 0,
        "snapshot_success": 0,
        "codal_errors": 0,
        "tsetmc_errors": 0,
        "brsapi_announcement_symbols": 0,
        "brsapi_flow_symbols": 0,
        "project_title_matches": 0,
        "financial_detail_cache_hits": 0,
        "project_detail_cache_hits": 0,
    }

    print(f"Input universe records: {len(stocks)}")
    print("Starting BrsApi-backed report discovery and money-flow history; direct Codal/TSETMC hosts are bypassed.")

    for index, stock in enumerate(stocks, start=1):
        symbol = str(stock.get("symbol", "")).strip()
        if not symbol:
            continue
        company = stock.get("company", "")
        all_reports, statement_reports = [], []
        codal_error = None
        try:
            all_reports = codal_search(symbol, financial_only=False)
            counts["codal_general_search_success"] += 1
            counts["brsapi_announcement_symbols"] += 1
        except Exception as exc:
            codal_error = str(exc).replace(BRS_API_KEY, "[REDACTED]")[:220]
            counts["codal_errors"] += 1
        try:
            # Filter the same per-symbol response; this deliberately avoids spending a second API call.
            statement_reports = codal_search(symbol, financial_only=True)
            counts["codal_financial_search_success"] += 1
        except Exception as exc:
            if not codal_error:
                codal_error = str(exc).replace(BRS_API_KEY, "[REDACTED]")[:220]
            counts["codal_errors"] += 1

        for report in all_reports:
            report["symbol"] = symbol
            report["company"] = company
        for report in statement_reports:
            report["symbol"] = symbol
            report["company"] = company

        # Use direct Codal search regardless of whether TSETMC can map the symbol.
        # This prevents a TSETMC failure from silently skipping fundamentals/projects.
        chart_id = str(stock.get("chart_id") or "")
        if BRS_API_KEY:
            kind = "common_equity" if chart_id.startswith(COMMON_EQUITY_ISIN_PREFIXES) else "other_or_unconfirmed"
            mapping = {
                "ins_code": None,
                "isin": None,
                "ticker_tsetmc": symbol,
                "company_tsetmc": company,
                "instrument_kind": kind,
                "mapping_source": "stock_universe chart_id prefix; no numeric TSETMC code needed for BrsApi",
            }
            counts["brsapi_symbol_mapping"] = counts.get("brsapi_symbol_mapping", 0) + (1 if chart_id else 0)
        else:
            mapping = symbol_map.get(symbol)
            if not isinstance(mapping, dict) or not mapping.get("ins_code"):
                if not TSETMC_UNAVAILABLE:
                    try:
                        mapping = find_instrument(symbol)
                        if mapping:
                            symbol_map[symbol] = mapping
                    except Exception as exc:
                        counts["tsetmc_errors"] += 1
                        if not TSETMC_UNAVAILABLE:
                            print(f"TSETMC mapping error [{symbol}]: {str(exc)[:180]}")
                        mapping = None
        if mapping and mapping.get("ins_code"):
            counts["tsetmc_mapping_success"] += 1
            if mapping.get("instrument_kind") == "common_equity":
                counts["confirmed_common_equities"] += 1

        snapshot = None
        marketmap_price = as_number(stock.get("marketmap_close_price"))
        if marketmap_price is not None:
            snapshot = {"closing_price": marketmap_price, "source": "stock_universe marketmap cache; not a TSETMC live snapshot"}
        flow = {"score": None, "status": "دادهٔ پول حقیقی/حقوقی دریافت نشده", "evidence": [], "windows": {}}
        if BRS_API_KEY:
            try:
                history = fetch_flow(symbol=symbol)
                flow = score_money_flow(history)
                flow["history_rows"] = len(history)
                flow["latest_date_raw"] = history[-1]["date_raw"] if history else None
                flow["full_year_coverage"] = len(history) >= 252
                if len(history) >= 252:
                    counts["flow_history_success"] += 1
                if history:
                    counts["brsapi_flow_symbols"] += 1
            except Exception as exc:
                safe_error = str(exc).replace(BRS_API_KEY, "[REDACTED]")
                counts["tsetmc_errors"] += 1
                flow = {"score": None, "status": "دریافت تاریخچهٔ حقیقی/حقوقی ناموفق", "evidence": [], "windows": {}, "error": safe_error[:220]}
        elif mapping and mapping.get("ins_code") and not TSETMC_UNAVAILABLE:
            try:
                snapshot = get_tsetmc_snapshot(mapping["ins_code"])
                counts["snapshot_success"] += 1
            except Exception as exc:
                counts["tsetmc_errors"] += 1
                if not TSETMC_UNAVAILABLE:
                    print(f"TSETMC snapshot error [{symbol}]: {str(exc)[:180]}")
            try:
                history = fetch_flow(mapping["ins_code"])
                flow = score_money_flow(history)
                flow["history_rows"] = len(history)
                flow["latest_date_raw"] = history[-1]["date_raw"] if history else None
                flow["full_year_coverage"] = len(history) >= 252
                if len(history) >= 252:
                    counts["flow_history_success"] += 1
            except Exception as exc:
                counts["tsetmc_errors"] += 1
                flow = {"score": None, "status": "دریافت تاریخچهٔ حقیقی/حقوقی ناموفق", "evidence": [], "windows": {}, "error": str(exc)[:220]}

        project_reports = [r for r in all_reports if r.get("is_project_candidate")]
        important_reports = [r for r in all_reports if r.get("is_important_disclosure")]
        financial_titles = [r for r in statement_reports if r.get("is_financial_report_candidate") or "صورت" in normalize_text(r.get("title", ""))]
        selected_statements = choose_financial_reports(financial_titles)
        parsed_statements = []
        for report in selected_statements:
            cache_key = report_cache_key(symbol, report)
            cached_detail = financial_detail_cache.get(cache_key)
            if cached_detail is not None:
                detail = merge_cached_detail(cached_detail, report, symbol, company)
                counts["financial_detail_cache_hits"] += 1
            else:
                detail = detail_financial_report(report)
            parsed_statements.append(detail)
            if detail.get("detail_status") == "parsed_with_heuristic_cell_mapping":
                counts["statement_reports_detailed"] += 1
            financial_watchlist.append({k: detail.get(k) for k in ("symbol", "company", "title", "date", "url", "detail_status", "ratios")})

        selected_projects = project_reports[:MAX_PROJECT_DETAILS_PER_STOCK]
        project_details = []
        for report in selected_projects:
            counts["project_title_matches"] += 1
            project_cache_key = report_cache_key(symbol, report)
            cached_project = project_detail_cache.get(project_cache_key)
            if isinstance(cached_project, dict) and cached_project.get("full_report_fetched"):
                detail_item = merge_cached_detail(cached_project, report, symbol, company)
                counts["project_detail_cache_hits"] += 1
                project_details.append(detail_item)
                project_watchlist.append(detail_item)
                continue
            detail_item = {
                "symbol": symbol,
                "company": company,
                "title": report.get("title"),
                "date": report.get("date"),
                "url": report.get("url"),
                "matched_keywords": report.get("project_keywords", []),
                "full_report_fetched": False,
                "assessment_status": "title match only; detail not fetched",
                "physical_progress_pct_candidates": [],
                "capex_evidence": [],
                "capex_numeric_candidates": [],
                "commissioning_evidence": [],
                "commissioning_date_candidates": [],
                "capacity_evidence": [],
                "capacity_numeric_candidates": [],
                "return_evidence": [],
                "return_numeric_candidates": [],
            }
            project_sources = []
            if report.get("url") and report.get("has_html"):
                project_sources.append(("html", report["url"]))
            if report.get("excel_url"):
                project_sources.append(("excel", report["excel_url"]))
            fetch_errors = []
            for source_kind, source_url in project_sources:
                try:
                    page = fetch_report_page(source_url)
                    datasource = extract_datasource(page)
                    if not datasource:
                        table_rows = extract_html_table_rows(page)
                        if table_rows:
                            datasource = {"tables": table_rows}
                    detail_item = build_project_assessment(
                        {**report, "symbol": symbol, "company": company}, page, datasource
                    )
                    detail_item["detail_source_kind"] = source_kind
                    detail_item["detail_source_url"] = source_url
                    counts["project_reports_detailed"] += 1
                    break
                except Exception as exc:
                    fetch_errors.append(f"{source_kind}: {str(exc)[:140]}")
            if project_sources and not detail_item.get("full_report_fetched"):
                detail_item["assessment_status"] = "detail fetch failed or table/text not found"
                if fetch_errors:
                    detail_item["detail_error"] = "; ".join(fetch_errors)[:500]
            project_details.append(detail_item)
            project_watchlist.append(detail_item)
        for report in important_reports[:5]:
            important_watchlist.append({"symbol": symbol, "company": company, "title": report.get("title"), "date": report.get("date"), "url": report.get("url"), "matched_keywords": report.get("important_disclosure_keywords", [])})

        fundamentals = score_fundamentals(parsed_statements)
        record = {
            "symbol": symbol,
            "company": company,
            "exchange": stock.get("exchange", ""),
            "chart_id": stock.get("chart_id", ""),
            "marketmap_close_price": stock.get("marketmap_close_price"),
            "tsetmc_ins_code": mapping.get("ins_code") if mapping else None,
            "tsetmc_isin": mapping.get("isin") if mapping else None,
            "instrument_kind": mapping.get("instrument_kind", "unconfirmed") if mapping else "unmapped",
            "mapping_status": "matched" if mapping else "unavailable",
            "snapshot": snapshot,
            "money_flow_score": flow.get("score"),
            "money_flow_status": flow.get("status"),
            "money_flow_evidence": flow.get("evidence", []),
            "money_flow_windows": flow.get("windows", {}),
            "money_flow_history_rows": flow.get("history_rows"),
            "money_flow_full_year_coverage": flow.get("full_year_coverage", False),
            "money_flow_latest_date": flow.get("latest_date_raw"),
            "money_flow_error": flow.get("error"),
            "fundamental_score": fundamentals.get("score"),
            "fundamental_status": fundamentals.get("status"),
            "fundamental_evidence": fundamentals.get("evidence", []),
            "fundamental_caveat": fundamentals.get("caveat"),
            "financial_reports": parsed_statements,
            "fundamental_assessment": fundamentals,
            "project_disclosure_candidates": project_details,
            "recent_codal_reports": all_reports[:20],
            "important_disclosures": important_reports[:5],
            "codal_search_error": codal_error,
            "project_assessment_status": "گزارش‌های دارای کلیدواژه پایش شدند؛ مقدارها/پیشرفت/زمان اجرا فقط در صورت استخراج و بازبینی صریح معتبرند" if project_details else "در اطلاعیه‌های بازیابی‌شده عنوان طرح توسعه پیدا نشد؛ به معنی نبود پروژه نیست",
            "data_status": "complete_for_available_sources" if (all_reports or parsed_statements or snapshot or flow.get("windows")) else "no_source_data",
        }
        results.append(record)

        if index % 10 == 0 or index == len(stocks):
            print(
                f"Processed {index}/{len(stocks)} | Codal={counts['codal_general_search_success']} "
                f"FS-search={counts['codal_financial_search_success']} statements={counts['statement_reports_detailed']} "
                f"projects={counts['project_reports_detailed']} flow={counts['flow_history_success']} "
                f"TSETMC-blocked={TSETMC_UNAVAILABLE}"
            )

    if counts["brsapi_announcement_symbols"] == 0 and counts["brsapi_flow_symbols"] == 0:
        raise RuntimeError(
            "No usable data was returned by BrsApi for any symbol. Previous output is left untouched; inspect endpoint/key/quota errors above."
        )

    # Separate ranking layers; do not fill missing scores with zero for interpretation.
    money_candidates = [r for r in results if isinstance(r.get("money_flow_score"), (int, float)) and r.get("money_flow_full_year_coverage") is True and r.get("instrument_kind") == "common_equity"]
    money_candidates.sort(key=lambda r: (r.get("money_flow_score", -1), (r.get("money_flow_windows", {}).get("60", {}) or {}).get("real_net_value_pct_of_real_turnover") if (r.get("money_flow_windows", {}).get("60", {}) or {}).get("real_net_value_pct_of_real_turnover") is not None else -999), reverse=True)
    fundamental_candidates = [r for r in results if isinstance(r.get("fundamental_score"), (int, float)) and r.get("instrument_kind") == "common_equity"]
    fundamental_candidates.sort(key=lambda r: (r.get("fundamental_score", -1), r.get("fundamental_assessment", {}).get("latest_report_date") or ""), reverse=True)

    output = {
        "generated_at_utc": utc_now(),
        "source": "BrsApi Codal announcements and TSETMC client-type history; marketmap price cache",
        "source_access_mode": "BrsApi authenticated proxy; direct Codal/TSETMC endpoints are not used for search/history",
        "brsapi_configured": bool(BRS_API_KEY),
        "input_universe_count": len(stocks),
        "coverage_verified_for_all_active_market_symbols": False,
        "tsetmc_api_status": "proxied_via_brsapi" if BRS_API_KEY else ("blocked_or_unavailable" if TSETMC_UNAVAILABLE else "not_tested"),
        "tsetmc_api_note": "Client-type history retrieved through BrsApi when configured; direct GitHub-runner TSETMC calls are bypassed.",
        "codal_api_status": "announcements_via_brsapi; direct report details may be geo-blocked" if BRS_API_KEY else ("blocked_or_unavailable" if CODAL_UNAVAILABLE else "not_tested"),
        "codal_api_note": "BrsApi provides announcement metadata; report-page detail extraction still depends on availability of report links from the runner.",
        "counts": {
            **counts,
            "universe_records_processed": len(results),
            "fundamental_candidates_scored": len(fundamental_candidates),
            "money_flow_candidates_scored": len(money_candidates),
            "project_watchlist_items": len(project_watchlist),
            "financial_report_watchlist_items": len(financial_watchlist),
            "important_disclosure_watchlist_items": len(important_watchlist),
        },
        "method": {
            "money_flow": "5/20/60/120/252 trading-session natural-person net flow, positivity consistency and per-capita value comparison when TSETMC history is available; 252-session coverage is separately noted.",
            "fundamental": "First-pass extraction of statement labels from Codal embedded datasource; ratios include margins, liabilities/assets, current ratio, operating cash flow/net profit and finance cost/operating profit. Annual growth is not scored until equal periods are explicitly matched.",
            "development_projects": "Searches recent Codal report titles for project/development terms, opens available report pages, preserves textual evidence on capex/progress/commissioning/capacity/return. Candidate values require report-level review; no ROI estimate is fabricated.",
            "request_budget": "Two recent Codal search pages per search type, up to two financial report details and one project detail per symbol; request timeout is 10 seconds with one retry. Consecutive provider failures trigger a circuit breaker.",
            "cache": "Reuses previously fetched detail for unchanged published reports; search metadata and money-flow history are refreshed each scheduled run.",
            "limitations": "BrsApi announcement metadata is only one recent page per ticker; financial statement/project details are scored only if the linked report content can actually be fetched and parsed. Cell layouts vary; all values are candidate extractions pending source review.",
        },
        "warning": "Research screener only, not investment advice. TSETMC CDN may geo-block GitHub runners. Do not interpret an unscored/missing layer as neutral. Coverage of every active TSE/IFB ordinary share is not independently verified.",
        "top_50_money_flow_for_review": money_candidates[:50],
        "top_50_fundamental_for_review": fundamental_candidates[:50],
        "project_disclosure_watchlist": sorted(project_watchlist, key=lambda r: str(r.get("date") or ""), reverse=True)[:5000],
        "financial_report_watchlist": sorted(financial_watchlist, key=lambda r: str(r.get("date") or ""), reverse=True)[:5000],
        "important_disclosure_watchlist": sorted(important_watchlist, key=lambda r: str(r.get("date") or ""), reverse=True)[:5000],
        "stocks": results,
    }
    write_json(OUTPUT_FILE, output)
    write_json(MAP_FILE, {"updated_at_utc": utc_now(), "symbols": symbol_map})

    print("\n===== FUNDAMENTAL / PROJECT / MONEY-FLOW SUMMARY =====")
    print(f"Input universe: {len(stocks)}")
    print(f"Universe records processed: {len(results)}")
    print(f"Codal searches succeeded: {counts['codal_general_search_success']}")
    print(f"Financial searches succeeded: {counts['codal_financial_search_success']}")
    print(f"Statement reports parsed: {counts['statement_reports_detailed']}")
    print(f"Fundamental stocks scored: {len(fundamental_candidates)}")
    print(f"Project title matches: {counts['project_title_matches']}")
    print(f"Project report details fetched: {counts['project_reports_detailed']}")
    print(f"Financial detail cache hits: {counts['financial_detail_cache_hits']}")
    print(f"Project detail cache hits: {counts['project_detail_cache_hits']}")
    print(f"TSETMC mappings: {counts['tsetmc_mapping_success']}")
    print(f"BrsApi announcement symbols: {counts['brsapi_announcement_symbols']}")
    print(f"BrsApi flow symbols with rows: {counts['brsapi_flow_symbols']}")
    print(f"BrsApi symbols classified from chart IDs: {counts.get('brsapi_symbol_mapping', 0)}")
    print(f"TSETMC snapshots received: {counts['snapshot_success']}")
    print(f"Money-flow histories with >=252 sessions: {counts['flow_history_success']}")
    print(f"TSETMC errors: {counts['tsetmc_errors']}")
    print(f"Codal errors: {counts['codal_errors']}")
    print(f"BrsApi key configured: {bool(BRS_API_KEY)}")
    print(f"BrsApi API unavailable: {BRS_API_UNAVAILABLE}")
    print(f"BrsApi note: {BRS_API_UNAVAILABLE_REASON}")
    print(f"Direct TSETMC unavailable: {TSETMC_UNAVAILABLE}")
    print(f"Direct Codal detail host unavailable: {CODAL_UNAVAILABLE}")
    print(f"Direct Codal note: {CODAL_UNAVAILABLE_REASON}")
    print(f"Output: {OUTPUT_FILE.relative_to(ROOT)}")
    print(f"Instrument map: {MAP_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
