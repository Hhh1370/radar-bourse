#!/usr/bin/env python3
"""Iran equities: Codal fundamentals/projects + optional TSETMC money-flow layer.

Designed to run in GitHub Actions. Missing/blocked sources are recorded as missing,
never converted into a neutral or positive score. Codal report parsing is heuristic;
all extracted figures keep source links/snippets and should be reviewed for context.
"""

import csv
import html
import io
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
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_FILE = ROOT / "data" / "stock_universe.json"
MAP_FILE = ROOT / "data" / "tsetmc_instrument_map.json"
FUNDAI_RATIO_FILE = ROOT / "data" / "fundai_financial_ratios.json"
OUTPUT_FILE = ROOT / "data" / "fundamental_money_radar.json"

TSETMC_BASE = "https://cdn.tsetmc.com/api"
TSETMC_MIRROR_BASE = "https://cdn10.tsetmc.com/api"
TSETMC_MIRROR_UNAVAILABLE = False
TSETMC_MIRROR_UNAVAILABLE_REASON = None
TSETMC_LEGACY_UNAVAILABLE = False
TSETMC_LEGACY_UNAVAILABLE_REASON = None
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
MAX_FINANCIAL_DETAILS_PER_STOCK = 8
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


def tsetmc_mirror_get(path):
    """Try the documented public CDN mirror once; cache connection-level failures."""
    global TSETMC_MIRROR_UNAVAILABLE, TSETMC_MIRROR_UNAVAILABLE_REASON
    if TSETMC_MIRROR_UNAVAILABLE:
        raise RuntimeError(TSETMC_MIRROR_UNAVAILABLE_REASON or "TSETMC mirror unavailable")
    url = f"{TSETMC_MIRROR_BASE}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.tsetmc.com/",
            "Origin": "https://www.tsetmc.com",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = response.read(8 * 1024 * 1024 + 1)
        if not body:
            raise ValueError("empty response body from TSETMC mirror")
        if len(body) > 8 * 1024 * 1024:
            raise ValueError("TSETMC mirror response exceeded the 8 MiB safety limit")
        payload = json.loads(body.decode("utf-8-sig", errors="replace"))
        if not isinstance(payload, (dict, list)):
            raise ValueError("TSETMC mirror returned an unexpected JSON type")
        if TSETMC_DELAY:
            time.sleep(TSETMC_DELAY)
        return payload
    except urllib.error.HTTPError as exc:
        # 400/404 can be instrument/path-specific; do not conclude the whole host is down.
        if exc.code in (403, 429, 451, 500, 502, 503, 504):
            TSETMC_MIRROR_UNAVAILABLE = True
            TSETMC_MIRROR_UNAVAILABLE_REASON = f"HTTP {exc.code} from TSETMC mirror"
        raise RuntimeError(f"TSETMC mirror request failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        # Avoid retrying a blocked/timeout host once for each of 741 symbols.
        TSETMC_MIRROR_UNAVAILABLE = True
        TSETMC_MIRROR_UNAVAILABLE_REASON = f"{type(exc).__name__}: {str(exc)[:180]}"
        raise RuntimeError(f"TSETMC mirror request failed: {TSETMC_MIRROR_UNAVAILABLE_REASON}") from exc


def tsetmc_get(path):
    """Use the free CDN mirror first, then the main public endpoint if it remains usable."""
    errors = []
    if not TSETMC_MIRROR_UNAVAILABLE:
        try:
            return tsetmc_mirror_get(path)
        except Exception as exc:
            errors.append(str(exc))
    if not TSETMC_UNAVAILABLE:
        try:
            return request_json(f"{TSETMC_BASE}/{path.lstrip('/')}", delay=TSETMC_DELAY)
        except Exception as exc:
            errors.append(str(exc))
    reason = "; ".join(errors) or TSETMC_UNAVAILABLE_REASON or TSETMC_MIRROR_UNAVAILABLE_REASON or "no TSETMC host available"
    raise RuntimeError(f"all usable TSETMC hosts failed: {reason[:300]}")


def jalali_to_gregorian_date(jy: int, jm: int, jd: int):
    """Convert a Jalali date to Gregorian for a freshness check on client-type history."""
    if not (1 <= jm <= 12 and 1 <= jd <= 31):
        return None
    jy += 1595
    days = -355668 + (365 * jy) + (jy // 33) * 8 + ((jy % 33 + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    lengths = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > lengths[gm - 1]:
        gd -= lengths[gm - 1]
        gm += 1
    try:
        return date(gy, gm, gd)
    except ValueError:
        return None


def parse_tsetmc_date(value):
    digits = re.sub(r"\D", "", str(value or "").translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    ))
    if len(digits) != 8:
        return None
    year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
    try:
        if 2000 <= year <= 2200:
            return date(year, month, day)
        if 1300 <= year <= 1499:
            return jalali_to_gregorian_date(year, month, day)
    except ValueError:
        return None
    return None


def money_flow_history_coverage(history, now_date=None):
    """A 12-month claim requires 252 sessions, a long calendar span and fresh data."""
    if not history:
        return {
            "full_year_coverage": False, "latest_date_iso": None,
            "latest_age_days": None, "calendar_span_days": None,
        }
    latest = parse_tsetmc_date(history[-1].get("date_raw"))
    oldest = parse_tsetmc_date(history[0].get("date_raw"))
    if latest is None or oldest is None:
        return {
            "full_year_coverage": False, "latest_date_iso": None,
            "latest_age_days": None, "calendar_span_days": None,
        }
    today = now_date or datetime.now(timezone.utc).date()
    age = (today - latest).days
    span = (latest - oldest).days
    return {
        "full_year_coverage": len(history) >= 252 and span >= 300 and 0 <= age <= 10,
        "latest_date_iso": latest.isoformat(),
        "latest_age_days": age,
        "calendar_span_days": span,
    }


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
    url_path = f"Instrument/GetInstrumentSearch/{urllib.parse.quote(symbol, safe='')}"
    payload = tsetmc_get(url_path)
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


def parse_legacy_client_type_csv(text):
    """Parse the old public TSETMC clienttype.aspx export (13 comma fields, semicolon rows)."""
    output = []
    raw_text = str(text or "").replace("\\ufeff", "").strip()
    if not raw_text:
        return []
    # Historical endpoint usually emits comma-separated columns, with semicolon row
    # terminators. Support newline-separated rows too; never infer missing amounts as zero.
    chunks = []
    if ";" in raw_text:
        chunks = [part.strip() for part in raw_text.split(";") if part.strip()]
    else:
        chunks = [part.strip() for part in raw_text.splitlines() if part.strip()]
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    for chunk in chunks:
        try:
            fields = next(csv.reader([chunk], delimiter=",", skipinitialspace=True))
        except (csv.Error, StopIteration):
            continue
        fields = [str(v or "").strip().strip('"').translate(table) for v in fields]
        if len(fields) < 13:
            continue
        date_value = re.sub(r"\\D", "", fields[0])
        if len(date_value) != 8 or parse_tsetmc_date(date_value) is None:
            continue
        def num(index):
            value = fields[index].replace(",", "").strip()
            if not value or value in {"-", "—", "null", "None"}:
                return None
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) else None
            except ValueError:
                return None
        output.append({
            "recDate": date_value,
            "buy_I_Count": num(1),
            "buy_N_Count": num(2),
            "sell_I_Count": num(3),
            "sell_N_Count": num(4),
            "buy_I_Volume": num(5),
            "buy_N_Volume": num(6),
            "sell_I_Volume": num(7),
            "sell_N_Volume": num(8),
            "buy_I_Value": num(9),
            "buy_N_Value": num(10),
            "sell_I_Value": num(11),
            "sell_N_Value": num(12),
        })
    if not output:
        return []
    normalized = normalize_history({"clientType": output})
    return normalized


def fetch_legacy_client_type_history(ins_code):
    """Try the public legacy CSV hosts once, then cache complete legacy-source failure."""
    global TSETMC_LEGACY_UNAVAILABLE, TSETMC_LEGACY_UNAVAILABLE_REASON
    if TSETMC_LEGACY_UNAVAILABLE:
        raise RuntimeError(TSETMC_LEGACY_UNAVAILABLE_REASON or "legacy TSETMC client-type hosts unavailable")
    code = str(ins_code or "").strip()
    if not re.fullmatch(r"\\d{10,20}", code):
        raise ValueError("legacy TSETMC history requires a numeric instrument code")
    urls = (
        f"https://old.tsetmc.com/tsev2/data/clienttype.aspx?i={urllib.parse.quote(code, safe='')}",
        f"http://old.tsetmc.com/tsev2/data/clienttype.aspx?i={urllib.parse.quote(code, safe='')}",
        f"http://www.tsetmc.com/tsev2/data/clienttype.aspx?i={urllib.parse.quote(code, safe='')}",
    )
    errors = []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/plain,text/csv,application/json,*/*",
        "Referer": "https://www.tsetmc.com/",
        "Connection": "close",
    }
    for url in urls:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read(8 * 1024 * 1024 + 1)
            if not body:
                raise ValueError("empty legacy TSETMC history response")
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("legacy TSETMC history response exceeded 8 MiB")
            text_body = body.decode("utf-8-sig", errors="replace")
            history = parse_legacy_client_type_csv(text_body)
            if len(history) < 20:
                raise ValueError(f"legacy TSETMC returned only {len(history)} parseable history rows")
            if TSETMC_DELAY:
                time.sleep(TSETMC_DELAY)
            return history
        except urllib.error.HTTPError as exc:
            errors.append(f"{urllib.parse.urlparse(url).hostname}: HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, csv.Error) as exc:
            errors.append(f"{urllib.parse.urlparse(url).hostname}: {type(exc).__name__}: {str(exc)[:120]}")
    TSETMC_LEGACY_UNAVAILABLE = True
    TSETMC_LEGACY_UNAVAILABLE_REASON = "; ".join(errors)[:360] or "legacy TSETMC history unavailable"
    raise RuntimeError(TSETMC_LEGACY_UNAVAILABLE_REASON)


def fetch_flow(ins_code=None, symbol=None):
    if BRS_API_KEY:
        if not symbol:
            raise ValueError("symbol is required for BrsApi client-type history")
        try:
            payload = brsapi_get_json("Tsetmc/History.php", {"type": "1", "l18": symbol})
            history = normalize_history(payload)
            if history:
                return history
            raise ValueError("BrsApi returned no usable client-type history")
        except Exception:
            # If the caller has a confirmed numeric insCode, continue through public paths.
            if not ins_code:
                raise
    if not ins_code:
        raise ValueError("ins_code is required for direct TSETMC history")
    errors = []
    try:
        payload = tsetmc_get(f"ClientType/GetClientTypeHistory/{urllib.parse.quote(str(ins_code), safe='')}")
        history = normalize_history(payload)
        if len(history) >= 20:
            return history
        errors.append(f"JSON history yielded only {len(history)} usable rows")
    except Exception as exc:
        errors.append(str(exc)[:240])
    try:
        return fetch_legacy_client_type_history(ins_code)
    except Exception as exc:
        errors.append(str(exc)[:260])
    raise RuntimeError("all public TSETMC client-type paths failed: " + "; ".join(errors)[:620])


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
        months = period_match.group(1)
        return "annual" if months == "12" else months
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
    """Keep a comparable two-year series of cumulative statements for every quarter.

    Companies publish at different dates, so selection is based primarily on the
    statement period end, not a market-wide assumed publication date. We retain up
    to two reports for each 3/6/9/12-month period, all from one consolidation mode,
    so Q2/Q3/Q4 can be derived from cumulative filings without mixing units/series.
    """
    eligible = [
        r for r in reports
        if (r.get("url") or r.get("excel_url"))
        and (r.get("has_html") or r.get("has_excel") or "صورت" in r.get("title", ""))
        and "صورت" in r.get("title", "")
        and report_period_key(r) in {"3", "6", "9", "annual"}
    ]

    # De-duplicate amended/repeated filings for the same period and statement series,
    # keeping the latest publication of each exact period end.
    unique_by_period = {}
    for report in eligible:
        period = report_period_key(report)
        end = report_year_end(report) or str(report.get("date") or "")
        mode = "consolidated" if "تلفیقی" in normalize_text(report.get("title", "")) else "standalone"
        key = (period, end, mode)
        prior = unique_by_period.get(key)
        if prior is None or str(report.get("date") or "") > str(prior.get("date") or ""):
            unique_by_period[key] = report
    eligible = list(unique_by_period.values())

    # Pick the statement series with the most period coverage. Ties favor standalone
    # statements, unless consolidated filings provide a materially longer coherent series.
    modes = ("standalone", "consolidated")
    def mode_quality(mode):
        rows = [r for r in eligible if (
            ("consolidated" if "تلفیقی" in normalize_text(r.get("title", "")) else "standalone") == mode
        )]
        covered_periods = len({report_period_key(r) for r in rows})
        two_year_periods = sum(
            1 for kind in ("3", "6", "9", "annual")
            if len({report_year_end(r) for r in rows if report_period_key(r) == kind and report_year_end(r)}) >= 2
        )
        return (two_year_periods, covered_periods, len(rows), mode == "standalone")

    selected_mode = max(modes, key=mode_quality)
    selected = []
    for kind in ("3", "6", "9", "annual"):
        rows = [
            r for r in eligible
            if report_period_key(r) == kind
            and ("consolidated" if "تلفیقی" in normalize_text(r.get("title", "")) else "standalone") == selected_mode
        ]
        rows.sort(
            key=lambda r: (str(report_year_end(r) or ""), str(r.get("date") or "")),
            reverse=True,
        )
        seen_period_ends = set()
        for report in rows:
            period_end = report_year_end(report) or str(report.get("date") or "")
            if period_end in seen_period_ends:
                continue
            selected.append(report)
            seen_period_ends.add(period_end)
            if len(seen_period_ends) >= 2:
                break

    # Keep outputs reproducible, while downstream scoring independently orders by
    # each issuer's period end for quarter-to-quarter/year-over-year comparisons.
    selected.sort(
        key=lambda r: (
            str(report_year_end(r) or ""),
            str(r.get("date") or ""),
        ),
        reverse=True,
    )
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
    report_url = str(report.get("url") or report.get("official_url") or report.get("codal_url") or "")
    excel_url = str(report.get("excel_url") or "").strip() or excel_service_url_from_report_url(report_url)
    if excel_url:
        report["excel_url"] = excel_url
        report["has_excel"] = True
        candidates.append(("excel_html", excel_url))
    # Try the report page as a fallback if the separate Excel host is unavailable
    # or its returned tables cannot be mapped. Per-host breakers stop repeated calls
    # if Codal's main domain is geo-blocked from this runner.
    if report_url:
        report["url"] = report_url
        if all(source_url != report_url for _, source_url in candidates):
            candidates.append(("html", report_url))
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


def _period_month_index(report):
    """Return Jalali year-month index and day for matching consecutive cumulative periods."""
    end = str(report.get("fiscal_year_end") or report_year_end(report) or "")
    match = re.fullmatch(r"((?:13|14)\d{2})-(\d{2})-(\d{2})", end)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return year * 12 + month, day


def derive_standalone_quarters(reports):
    """Derive separate Q2/Q3/Q4 flows from 6/9/12-month cumulative statements.

    Income-statement and operating-cash-flow values are cumulative, so subtract
    the prior cumulative report only when the period end is exactly three months
    earlier and unit/consolidation mode match. Balance-sheet stocks are not
    differenced. Missing or incompatible data is left unavailable.
    """
    parsed = [
        r for r in reports
        if isinstance(r.get("financial_metrics"), dict)
        and isinstance(r.get("ratios"), dict)
    ]
    quarters = []
    for report in parsed:
        if report.get("period_type") == "3":
            direct = dict(report)
            direct["quarter_number"] = 1
            direct["quarterly_derived_from_cumulative"] = False
            quarters.append(direct)

    flow_metrics = (
        "revenue", "gross_profit", "operating_profit", "net_profit",
        "operating_cash_flow", "finance_cost",
    )
    months_by_period = {"3": 3, "6": 6, "9": 9, "annual": 12}
    period_by_months = {3: "3", 6: "6", 9: "9", 12: "annual"}

    for current in parsed:
        months = months_by_period.get(str(current.get("period_type") or ""))
        if months not in (6, 9, 12):
            continue
        current_end = _period_month_index(current)
        if current_end is None:
            continue
        current_index, current_day = current_end
        previous_kind = period_by_months[months - 3]
        expected_index = current_index - 3
        current_unit = normalize_text(current.get("report_unit_hint") or "")
        current_mode = current.get("consolidation_mode")
        possible_previous = []
        for previous in parsed:
            if previous.get("period_type") != previous_kind:
                continue
            previous_end = _period_month_index(previous)
            if previous_end is None:
                continue
            previous_index, previous_day = previous_end
            if previous_index != expected_index or abs(previous_day - current_day) > 3:
                continue
            if previous.get("consolidation_mode") != current_mode:
                continue
            previous_unit = normalize_text(previous.get("report_unit_hint") or "")
            if not current_unit or not previous_unit or current_unit != previous_unit:
                continue
            possible_previous.append(previous)
        if not possible_previous:
            continue
        previous = max(possible_previous, key=lambda r: str(r.get("date") or ""))

        derived_metrics = {}
        for metric_name in flow_metrics:
            now_metric = (current.get("financial_metrics") or {}).get(metric_name)
            old_metric = (previous.get("financial_metrics") or {}).get(metric_name)
            now_value = now_metric.get("primary_value_candidate") if isinstance(now_metric, dict) else None
            old_value = old_metric.get("primary_value_candidate") if isinstance(old_metric, dict) else None
            if not isinstance(now_value, (int, float)) or not isinstance(old_value, (int, float)):
                continue
            if not math.isfinite(float(now_value)) or not math.isfinite(float(old_value)):
                continue
            difference = float(now_value) - float(old_value)
            derived_metrics[metric_name] = {
                "matched_label": f"مقدار مستقل فصل {months // 3} از تفاضل گزارش‌های تجمیعی",
                "row_text": f"{current.get('title', '')} منهای {previous.get('title', '')}",
                "numeric_values": [{"raw": difference, "value": difference}],
                "primary_value_candidate": difference,
            }
        if not any(key in derived_metrics for key in ("revenue", "net_profit")):
            continue
        derived = dict(current)
        derived["title"] = f"فصل {months // 3} استخراج‌شده از گزارش تجمیعی: {current.get('title', '')}"
        derived["period_type"] = "3"
        derived["quarter_number"] = months // 3
        derived["quarterly_derived_from_cumulative"] = True
        derived["quarterly_source_current_title"] = current.get("title", "")
        derived["quarterly_source_previous_title"] = previous.get("title", "")
        derived["quarterly_source_current_url"] = current.get("url") or current.get("excel_url") or ""
        derived["quarterly_source_previous_url"] = previous.get("url") or previous.get("excel_url") or ""
        derived["financial_metrics"] = derived_metrics
        derived["ratios"] = metrics_and_ratios(derived_metrics)
        quarters.append(derived)

    quarters.sort(
        key=lambda r: (
            str(r.get("fiscal_year_end") or ""),
            str(r.get("date") or ""),
            int(r.get("quarter_number") or 0),
        ),
        reverse=True,
    )
    # One best row per report period/mode/unit; direct and derived observations can
    # otherwise duplicate a quarter when a source publishes both variants.
    deduped = []
    seen = set()
    for row in quarters:
        key = (
            str(row.get("fiscal_year_end") or ""),
            str(row.get("consolidation_mode") or ""),
            normalize_text(row.get("report_unit_hint") or ""),
            int(row.get("quarter_number") or 0),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


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
    # Compare like-for-like three-month statements only: matching fiscal year-end
    # month/day, one Jalali year apart, identical consolidation mode and verified units.
    # The reports were selected independently of their publication dates, since companies
    # publish on different days.
    quarterly = derive_standalone_quarters(parsed)
    quarterly.sort(
        key=lambda r: (
            str(r.get("fiscal_year_end") or ""),
            str(r.get("date") or ""),
        ),
        reverse=True,
    )
    quarterly_growth = {}
    quarterly_growth_points = 0
    quarterly_comparison_status = "دو صورت مالی ۳ماههٔ قابل‌مقایسه هنوز موجود نیست"
    quarter_pair = None
    if quarterly:
        current_q = quarterly[0]
        current_end = str(current_q.get("fiscal_year_end") or "")
        current_match = re.fullmatch(r"(13|14)(\d{2})-(\d{2})-(\d{2})", current_end)
        if current_match:
            cy = int(current_match.group(1) + current_match.group(2))
            cm, cd = int(current_match.group(3)), int(current_match.group(4))
            for candidate in quarterly[1:]:
                prev_end = str(candidate.get("fiscal_year_end") or "")
                prev_match = re.fullmatch(r"(13|14)(\d{2})-(\d{2})-(\d{2})", prev_end)
                if not prev_match:
                    continue
                py = int(prev_match.group(1) + prev_match.group(2))
                pm, pd = int(prev_match.group(3)), int(prev_match.group(4))
                same_series = candidate.get("consolidation_mode") == current_q.get("consolidation_mode")
                units_match = (
                    current_q.get("report_unit_hint")
                    and candidate.get("report_unit_hint")
                    and normalize_text(current_q.get("report_unit_hint")) == normalize_text(candidate.get("report_unit_hint"))
                )
                if cy - py == 1 and (cm, cd) == (pm, pd) and same_series and units_match:
                    quarter_pair = (current_q, candidate)
                    break

    if quarter_pair:
        current_q, previous_q = quarter_pair
        for key in ("revenue", "net_profit", "operating_cash_flow"):
            now_value, old_value = metric_value(current_q, key), metric_value(previous_q, key)
            # Percentage growth is meaningful only with a positive prior-year baseline.
            if now_value is not None and old_value is not None and old_value > 0:
                quarterly_growth[key + "_yoy_pct"] = round((now_value / old_value - 1) * 100, 2)
        rev_q_growth = quarterly_growth.get("revenue_yoy_pct")
        profit_q_growth = quarterly_growth.get("net_profit_yoy_pct")
        cash_q_growth = quarterly_growth.get("operating_cash_flow_yoy_pct")
        if rev_q_growth is not None and rev_q_growth > 0:
            quarterly_growth_points += 4
            evidence.append("رشد درآمد در مقایسهٔ دو گزارش ۳ماههٔ هم‌دوره")
        if rev_q_growth is not None and rev_q_growth >= 15:
            quarterly_growth_points += 3
            evidence.append("رشد درآمد ۳ماهه دست‌کم ۱۵٪ نسبت به سال قبل")
        if profit_q_growth is not None and profit_q_growth > 0:
            quarterly_growth_points += 5
            evidence.append("رشد سود خالص در مقایسهٔ سال‌به‌سالِ ۳ماهه")
        elif profit_q_growth is not None and profit_q_growth < 0:
            evidence.append("هشدار: سود خالص ۳ماهه نسبت به دورهٔ مشابه سال قبل کاهش یافته")
        if cash_q_growth is not None and cash_q_growth > 0:
            quarterly_growth_points += 3
            evidence.append("رشد جریان نقد عملیاتی در مقایسهٔ سال‌به‌سالِ ۳ماهه")
        elif cash_q_growth is not None and cash_q_growth < 0:
            evidence.append("هشدار: جریان نقد عملیاتی ۳ماهه نسبت به دورهٔ مشابه سال قبل کاهش یافته")
        quarterly_comparison_status = (
            "مقایسهٔ سال‌به‌سال فصل مستقل استخراج‌شده از گزارش تجمیعی با واحد و سری یکسان"
            if current_q.get("quarterly_derived_from_cumulative")
            else "مقایسهٔ مقدماتی دو صورت مالی ۳ماهه با تاریخ پایان هم‌دوره و واحد یکسان"
        )
    elif len(quarterly) >= 2:
        quarterly_comparison_status = "گزارش‌های ۳ماهه موجودند، اما تاریخ پایان، واحد یا نوع تلفیقی برای مقایسهٔ معتبر یکسان نیست"

    score = min(100, score + growth_points + quarterly_growth_points)
    annual_count = len(annual_series)
    coverage = "قابل‌مقایسه‌تر" if (
        (annual_count >= 2 and isinstance(growth.get("revenue_yoy_pct"), (int, float)))
        or (quarter_pair is not None and isinstance(quarterly_growth.get("revenue_yoy_pct"), (int, float)))
    ) else "مقدماتی"
    return {
        "score": score,
        "status": "امتیاز مقدماتی؛ الگوی جداول و واحدهای گزارش باید بازبینی شوند",
        "evidence": evidence,
        "latest_report_title": latest.get("title"),
        "latest_report_date": latest.get("date"),
        "annual_reports_parsed": annual_count,
        "annual_growth_candidates_pct": growth,
        "quarterly_growth_candidates_pct": quarterly_growth,
        "quarterly_comparison_status": quarterly_comparison_status,
        "quarterly_growth_points": quarterly_growth_points,
        "coverage_grade": coverage,
        "caveat": "این امتیاز غربالگری اولیه است، نه ارزش‌گذاری نهایی. ارقام از استخراج اکتشافی جدول به‌دست می‌آیند؛ پیش از رتبه‌بندی نهایی باید واحد، تلفیقی/جداگانه و برچسب سطرها با گزارش رسمی بازبینی شوند. بانک‌ها و شرکت‌های مالی معیارهای متفاوت دارند.",
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




def excel_service_url_from_report_url(url):
    """Build Codal's public Excel/HTML endpoint from a report LetterSerial."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(str(url))
        query = urllib.parse.parse_qs(parsed.query)
        serial = next(
            (values[0] for key, values in query.items()
             if key.casefold() == "letterserial" and values and values[0]),
            "",
        )
        if serial:
            return "https://excel.codal.ir/service/Excel/GetAll/" + urllib.parse.quote(serial, safe="") + "/0"
    except (TypeError, ValueError):
        return ""
    return ""


def public_statement_report(row, symbol="", company=""):
    """Normalize a public discovery row into the common financial-report schema."""
    if not isinstance(row, dict):
        return None
    original_title = str(
        row.get("title") or row.get("report_title") or row.get("link_title")
        or row.get("subject") or ""
    ).strip()
    classification_raw = " ".join(str(row.get(k) or "") for k in (
        "classification", "report_type", "primary_category", "financial_keywords"
    ))
    classification = normalize_text(classification_raw)
    if any(term in classification for term in ("فعالیتماهانه", "تولیدوفروش", "عملکردماهانه")):
        return None

    period_hint = str(row.get("report_period") or row.get("period") or "").strip()
    context = str(row.get("source_text") or row.get("source_context") or "").strip()
    candidates = [original_title, context]
    title = next((value for value in candidates if value and "صورت" in normalize_text(value)), "")
    # Secondary snapshots frequently label the link "مشاهده گزارش" while the period
    # and statement class are stored in neighbouring metadata fields.
    if not title and any(term in classification for term in ("صورتهایمالی", "financialstatement", "صورتهایمالیحسابرسی")):
        if period_hint:
            title = "صورت‌های مالی دوره " + period_hint
        elif "صورت" in normalize_text(context):
            title = context[:500]
    if not title or "صورت" not in normalize_text(title):
        return None
    if period_hint and report_period_key({"title": title}) not in {"3", "6", "9", "annual"}:
        title = title + " · دوره " + period_hint
    period_type = report_period_key({"title": title})
    if period_type not in {"3", "6", "9", "annual"}:
        return None

    url = str(
        row.get("official_url") or row.get("codal_url") or row.get("url")
        or row.get("letter_url") or ""
    ).strip()
    if url and url.startswith("/"):
        url = urllib.parse.urljoin("https://www.codal.ir/", url)
    if not url or "LetterSerial=" not in url:
        return None
    excel_url = str(row.get("excel_url") or row.get("ExcelUrl") or "").strip()
    if excel_url and excel_url.startswith("/"):
        excel_url = urllib.parse.urljoin("https://excel.codal.ir/", excel_url)
    if not excel_url:
        excel_url = excel_service_url_from_report_url(url)
    published = str(
        row.get("date") or row.get("published_at") or row.get("publication_date")
        or row.get("publish_date") or ""
    ).strip()
    combined_title = title + (" " + period_hint if period_hint else "")
    normalized = {
        "symbol": symbol or str(row.get("symbol") or row.get("ticker") or "").strip(),
        "company": company or str(row.get("company") or row.get("company_name") or "").strip(),
        "title": title,
        "date": published,
        "url": url,
        "excel_url": excel_url or None,
        "has_html": True,
        "has_excel": bool(excel_url),
        "is_financial_report_candidate": True,
        "period_type": period_type,
        "fiscal_year_end": report_year_end({"title": combined_title}),
        "report_period_hint": period_hint,
        "discovery_source": str(row.get("source_name") or row.get("source") or "public Codal-linked snapshot"),
    }
    return normalized


def load_public_statement_reports(stocks):
    """Discover quarterly statements from cached public filing links without paid APIs."""
    disclosure_path = ROOT / "data" / "codal_disclosures_latest.json"
    ledger_path = ROOT / "data" / "codal_filing_ledger.json"
    disclosure = {}
    ledger = {}
    for path, target in ((disclosure_path, "disclosure"), (ledger_path, "ledger")):
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            if isinstance(data, dict):
                if target == "disclosure":
                    disclosure = data
                else:
                    ledger = data
        except (OSError, json.JSONDecodeError):
            continue

    by_symbol = {}
    by_company = {}
    for stock in stocks:
        ticker = str(stock.get("symbol") or "").strip()
        company = str(stock.get("company") or "").strip()
        if ticker:
            by_symbol.setdefault(normalize_text(ticker), stock)
        if company:
            by_company.setdefault(normalize_text(company), stock)

    source_rows = []
    for key in ("announcements", "channel_evidence"):
        values = disclosure.get(key, [])
        if isinstance(values, list):
            source_rows.extend(values)
    ledger_rows = ledger.get("reports", [])
    if isinstance(ledger_rows, list):
        source_rows.extend(ledger_rows)

    reports_by_symbol = {}
    unique = {}
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("symbol") or row.get("ticker") or "").strip()
        company = str(row.get("company") or row.get("company_name") or "").strip()
        stock = by_symbol.get(normalize_text(ticker)) if ticker else None
        if stock is None and company:
            stock = by_company.get(normalize_text(company))
        if stock is None:
            continue
        report = public_statement_report(row, str(stock.get("symbol") or ticker), str(stock.get("company") or company))
        if not report:
            continue
        key = (normalize_text(report["symbol"]), report["url"])
        prior = unique.get(key)
        if prior is None or len(str(row.get("source_text") or "")) > len(str(prior.get("source_text") or "")):
            report["source_text"] = str(row.get("source_text") or row.get("source_context") or "")[:1500]
            unique[key] = report

    for report in unique.values():
        reports_by_symbol.setdefault(normalize_text(report["symbol"]), []).append(report)
    for reports in reports_by_symbol.values():
        reports.sort(key=lambda r: (str(r.get("fiscal_year_end") or ""), str(r.get("date") or "")), reverse=True)
    return reports_by_symbol, disclosure, ledger


def run_public_codal_only():
    """Refresh only from free public snapshots + Codal Excel HTML; no paid BrsApi calls."""
    if not UNIVERSE_FILE.is_file():
        raise FileNotFoundError(f"Universe file not found: {UNIVERSE_FILE}")
    universe = read_json(UNIVERSE_FILE)
    stocks = universe.get("stocks", [])
    if not isinstance(stocks, list):
        raise ValueError("Invalid universe format: 'stocks' must be a list")

    prior = {}
    if OUTPUT_FILE.is_file():
        try:
            prior_payload = read_json(OUTPUT_FILE)
            for row in prior_payload.get("stocks", []) or []:
                if isinstance(row, dict) and row.get("symbol"):
                    prior[normalize_text(row["symbol"])] = row
        except (OSError, ValueError, json.JSONDecodeError):
            prior_payload = {}
    else:
        prior_payload = {}

    # Fundai's public snapshot includes numeric tsetmc_id candidates. Use these only
    # as lookup candidates; a money-flow score is retained only after the TSETMC endpoint
    # returns usable history for that exact numeric instrument code.
    ratio_ids_by_symbol = {}
    ratio_rows_available = 0
    ratio_snapshot_age = ""
    try:
        ratio_snapshot = read_json(FUNDAI_RATIO_FILE)
        ratio_rows = ratio_snapshot.get("rows", [])
        if isinstance(ratio_rows, list):
            ratio_rows_available = len(ratio_rows)
            ratio_snapshot_age = str(ratio_snapshot.get("generated_at_utc") or "")
            for ratio_row in ratio_rows:
                if not isinstance(ratio_row, dict):
                    continue
                ticker = normalize_text(ratio_row.get("company"))
                code = str(ratio_row.get("tsetmc_id") or "").strip()
                if ticker and re.fullmatch(r"\d{10,20}", code):
                    ratio_ids_by_symbol[ticker] = code
    except (OSError, ValueError, json.JSONDecodeError):
        ratio_ids_by_symbol = {}

    public_reports, disclosure, ledger = load_public_statement_reports(stocks)
    ledger_summary = ledger.get("last_run", {}) if isinstance(ledger.get("last_run"), dict) else {}
    print(
        f"PUBLIC_CODAL_INPUTS: source_announcements={len(disclosure.get('announcements', []) or [])} "
        f"source_evidence={len(disclosure.get('channel_evidence', []) or [])} "
        f"ledger_reports={len(ledger.get('reports', []) or [])} "
        f"symbols_with_statement_links={len(public_reports)}"
    )

    output_rows = []
    financial_watchlist = []
    parsed_new_count = 0
    cache_reused_count = 0
    failures = 0
    flow_requests_attempted = 0
    flow_histories_returned = 0
    flow_12m_verified = 0
    flow_first_error = ""
    verified_flow_mappings = {}
    now = utc_now()

    for index, stock in enumerate(stocks, start=1):
        symbol = str(stock.get("symbol") or "").strip()
        if not symbol:
            continue
        company = str(stock.get("company") or symbol)
        key = normalize_text(symbol)
        old = dict(prior.get(key) or {})
        cached_reports = [
            r for r in (old.get("financial_reports") or [])
            if isinstance(r, dict) and (r.get("url") or r.get("excel_url"))
        ]
        cached_by_key = {
            report_cache_key(symbol, r): r for r in cached_reports
        }
        selected = choose_financial_reports(public_reports.get(key, []))
        current_details = []
        for report in selected:
            report["symbol"] = symbol
            report["company"] = company
            cache_key = report_cache_key(symbol, report)
            cached = cached_by_key.get(cache_key)
            if cached and cached.get("detail_status") == "parsed_with_heuristic_cell_mapping":
                detail = merge_cached_detail(cached, report, symbol, company)
                cache_reused_count += 1
            else:
                detail = detail_financial_report(report)
                detail["symbol"] = symbol
                detail["company"] = company
                if detail.get("detail_status") == "parsed_with_heuristic_cell_mapping":
                    parsed_new_count += 1
                else:
                    failures += 1
                    if failures <= 10:
                        print(
                            "PUBLIC_CODAL_PARSE_FAILURE:"
                            + symbol + "|" + str(report.get("title") or "")[:90]
                            + "|" + str(detail.get("detail_status") or "")
                            + "|" + str(detail.get("detail_error") or "no detail error captured")[:220]
                        )
            current_details.append(detail)

        # Preserve prior statements not in the current source window, so year-over-year
        # comparisons survive the source feed's finite Telegram/archive window.
        details_by_identity = {}
        for report in cached_reports + current_details:
            identity = report_cache_key(symbol, report)
            previous = details_by_identity.get(identity)
            if previous is None or (
                report.get("detail_status") == "parsed_with_heuristic_cell_mapping"
                and previous.get("detail_status") != "parsed_with_heuristic_cell_mapping"
            ):
                details_by_identity[identity] = report
        all_details = sorted(
            details_by_identity.values(),
            key=lambda r: (str(r.get("fiscal_year_end") or report_year_end(r) or ""), str(r.get("date") or "")),
            reverse=True,
        )[:20]
        fundamentals = score_fundamentals(all_details)

        # Direct public TSETMC money flow is attempted only when a valid-looking
        # source ID exists and the endpoint host has not been marked unavailable.
        flow_info = None
        flow_history = []
        flow_coverage = {
            "full_year_coverage": False, "latest_date_iso": None,
            "latest_age_days": None, "calendar_span_days": None,
        }
        flow_error = "شناسه یا تاریخچهٔ معتبر پول حقیقی در منبع عمومی در دسترس نیست"
        ratio_ins_code = ratio_ids_by_symbol.get(key)
        chart_id = str(stock.get("chart_id") or old.get("chart_id") or "").strip()
        is_common_equity = chart_id.startswith(COMMON_EQUITY_ISIN_PREFIXES)
        if is_common_equity and ratio_ins_code and not TSETMC_LEGACY_UNAVAILABLE and not (TSETMC_UNAVAILABLE and TSETMC_MIRROR_UNAVAILABLE):
            flow_requests_attempted += 1
            try:
                flow_history = fetch_flow(ins_code=ratio_ins_code)
                flow_coverage = money_flow_history_coverage(flow_history)
                flow_info = score_money_flow(flow_history)
                if flow_history:
                    flow_histories_returned += 1
                has_valid_year = (
                    flow_coverage.get("full_year_coverage") is True
                    and isinstance(flow_info.get("score"), (int, float))
                    and isinstance(flow_info.get("windows"), dict)
                    and (flow_info.get("windows", {}).get("252") or {}).get("real_value_coverage_ratio", 0) >= 0.80
                )
                flow_coverage["full_year_coverage"] = bool(has_valid_year)
                if has_valid_year:
                    flow_12m_verified += 1
                    flow_error = ""
                    verified_flow_mappings[symbol] = {
                        "ins_code": ratio_ins_code,
                        "isin": chart_id,
                        "ticker_tsetmc": symbol,
                        "company_tsetmc": company,
                        "instrument_kind": "common_equity",
                        "mapping_source": "Fundai tsetmc_id; confirmed by successful TSETMC client-type history",
                        "mapped_at_utc": now,
                    }
                elif flow_history:
                    flow_error = (
                        "تاریخچه دریافت شده، اما برای تأیید پول حقیقی ۱۲ماهه باید حداقل ۲۵۲ جلسه، "
                        "پوشش زمانی کافی، دادهٔ تازه و مقادیر حقیقی معتبر داشته باشد"
                    )
                else:
                    flow_error = "پاسخ عمومی TSETMC تاریخچهٔ قابل‌استفاده‌ای برای این شناسه نداشت"
            except Exception as exc:
                flow_error = f"دریافت تاریخچهٔ پول حقیقی ناموفق: {str(exc)[:180]}"
                if not flow_first_error:
                    flow_first_error = flow_error
                if TSETMC_UNAVAILABLE and TSETMC_MIRROR_UNAVAILABLE:
                    print("TSETMC_FLOW_SOURCE_UNAVAILABLE:" + flow_error[:220])

        row = old
        row.update({
            "symbol": symbol,
            "company": company,
            "exchange": str(stock.get("exchange") or old.get("exchange") or ""),
            "chart_id": str(stock.get("chart_id") or old.get("chart_id") or ""),
            "instrument_kind": (
                "common_equity" if str(stock.get("chart_id") or old.get("chart_id") or "").startswith(COMMON_EQUITY_ISIN_PREFIXES)
                else str(old.get("instrument_kind") or "unconfirmed")
            ),
            "financial_reports": all_details,
            "fundamental_score": fundamentals.get("score"),
            "fundamental_status": (
                fundamentals.get("status") or "گزارش ساختاریافته قابل امتیازدهی در دادهٔ فعلی موجود نیست"
            ),
            "fundamental_evidence": fundamentals.get("evidence", []),
            "fundamental_caveat": fundamentals.get("caveat"),
            "fundamental_assessment": fundamentals,
            "recent_codal_reports": [
                {
                    "title": r.get("title"), "date": r.get("date"), "url": r.get("url"),
                    "period_type": r.get("period_type"), "discovery_source": r.get("discovery_source"),
                }
                for r in public_reports.get(key, [])[:20]
            ] or (old.get("recent_codal_reports") or []),
            "codal_search_error": (
                "به‌روزرسانی این اجرا از مسیر رایگانِ پیوندهای عمومی و Excel/HTML کدال انجام شد؛ "
                "جست‌وجوی کامل نمادها و پول حقیقی از API پولی دریافت نشد."
            ),
            "public_fundamental_refresh_at_utc": now,
            "public_statement_links_discovered": len(public_reports.get(key, [])),
            "data_status": (
                "public_report_parsed" if any(
                    r.get("detail_status") == "parsed_with_heuristic_cell_mapping" for r in all_details
                ) else "public_statement_links_only_or_source_unavailable"
            ),
        })
        # Never leave an old money-flow score looking current when this run could
        # not verify it. Incomplete histories may be shown as candidates, but fail the
        # annual-coverage gate in the dashboard.
        row["money_flow_score"] = flow_info.get("score") if isinstance(flow_info, dict) else None
        row["money_flow_status"] = (
            str(flow_info.get("status") or "دادهٔ پول حقیقی ناکافی")
            if isinstance(flow_info, dict) else "تاریخچهٔ پول حقیقی تازه‌سازی/اعتبارسنجی نشده"
        )
        row["money_flow_evidence"] = flow_info.get("evidence", []) if isinstance(flow_info, dict) else []
        row["money_flow_windows"] = flow_info.get("windows", {}) if isinstance(flow_info, dict) else {}
        row["money_flow_history_rows"] = len(flow_history)
        row["money_flow_full_year_coverage"] = bool(flow_coverage.get("full_year_coverage"))
        row["money_flow_latest_date"] = (
            flow_coverage.get("latest_date_iso")
            or (flow_history[-1].get("date_raw") if flow_history else None)
        )
        row["money_flow_source"] = (
            "TSETMC public client-type history via Fundai tsetmc_id"
            if flow_history else "TSETMC public history unavailable"
        )
        row["money_flow_error"] = flow_error
        output_rows.append(row)
        for report in all_details:
            financial_watchlist.append({
                key_name: report.get(key_name)
                for key_name in ("symbol", "company", "title", "date", "url", "detail_status", "ratios")
            })
        if index % 100 == 0 or index == len(stocks):
            print(f"PUBLIC_CODAL_PROGRESS:{index}/{len(stocks)} parsed_new={parsed_new_count} cache={cache_reused_count} failures={failures}")

    money_candidates = [
        r for r in output_rows
        if isinstance(r.get("money_flow_score"), (int, float))
        and r.get("money_flow_full_year_coverage") is True
        and r.get("instrument_kind") == "common_equity"
        and not str(r.get("money_flow_error") or "").strip()
    ]
    fundamental_candidates = [
        r for r in output_rows
        if isinstance(r.get("fundamental_score"), (int, float))
        and r.get("instrument_kind") == "common_equity"
    ]
    fundamental_candidates.sort(key=lambda r: (r.get("fundamental_score", -1), r.get("fundamental_assessment", {}).get("latest_report_date") or ""), reverse=True)
    if verified_flow_mappings:
        try:
            cached_map_payload = read_json(MAP_FILE) if MAP_FILE.is_file() else {}
        except (OSError, ValueError, json.JSONDecodeError):
            cached_map_payload = {}
        cached_symbols = cached_map_payload.get("symbols", {}) if isinstance(cached_map_payload, dict) else {}
        if not isinstance(cached_symbols, dict):
            cached_symbols = {}
        cached_symbols.update(verified_flow_mappings)
        write_json(MAP_FILE, {"updated_at_utc": now, "symbols": cached_symbols})

    output = {
        "generated_at_utc": now,
        "source": "Public Codal/Telegram filing-link ledger + Fundai ratios + direct public TSETMC history when reachable",
        "source_access_mode": "free_public_codal_and_tsetmc_flow_if_reachable",
        "brsapi_configured": False,
        "input_universe_count": len(stocks),
        "coverage_verified_for_all_active_market_symbols": False,
        "codal_api_status": (
            "public linked statement metadata; detailed values only if official Excel/HTML download succeeds"
        ),
        "codal_api_note": (
            "Discovery uses accessible public Telegram/Codal-linked snapshots; this is not guaranteed to enumerate every active issuer. "
            "A report is scored only when its official attachment is parsed into structured rows."
        ),
        "tsetmc_api_status": (
            "verified_12m_history_available" if flow_12m_verified
            else "direct_history_attempted_but_no_12m_coverage"
            if flow_requests_attempted
            else "no_numeric_tsetmc_id_or_tsetmc_hosts_unavailable"
        ),
        "tsetmc_api_note": (
            f"Fundai snapshot tickers with numeric IDs: {len(ratio_ids_by_symbol)}; "
            f"flow requests attempted: {flow_requests_attempted}; history responses: {flow_histories_returned}; "
            f"12-month histories verified: {flow_12m_verified}. "
            + (flow_first_error[:240] if flow_first_error else "")
        ),
        "counts": {
            "universe_records_processed": len(output_rows),
            "symbols_with_public_statement_links": len(public_reports),
            "public_statement_links_discovered": sum(len(v) for v in public_reports.values()),
            "statement_reports_detailed": parsed_new_count,
            "financial_detail_cache_hits": cache_reused_count,
            "public_statement_parse_failures": failures,
            "fundai_ratio_rows_available_for_flow_map": ratio_rows_available,
            "fundai_ratio_symbols_with_numeric_ids": len(ratio_ids_by_symbol),
            "tsetmc_flow_history_requests_attempted": flow_requests_attempted,
            "tsetmc_flow_history_responses": flow_histories_returned,
            "money_flow_12m_verified_symbols": flow_12m_verified,
            "tsetmc_mirror_unavailable": TSETMC_MIRROR_UNAVAILABLE,
            "tsetmc_legacy_unavailable": TSETMC_LEGACY_UNAVAILABLE,
            "tsetmc_primary_unavailable": TSETMC_UNAVAILABLE,
            "fundamental_candidates_scored": len(fundamental_candidates),
            "money_flow_candidates_scored": len(money_candidates),
            "financial_report_watchlist_items": len(financial_watchlist),
            "project_watchlist_items": len(prior_payload.get("project_disclosure_watchlist", []) or []) if isinstance(prior_payload, dict) else 0,
            "important_disclosure_watchlist_items": len(prior_payload.get("important_disclosure_watchlist", []) or []) if isinstance(prior_payload, dict) else 0,
            "codal_ledger_records": int(ledger_summary.get("total_records_in_ledger") or 0),
            "codal_ledger_new_records": int(ledger_summary.get("new_records_count") or 0),
        },
        "method": {
            "fundamental": (
                "Minimal quarterly screen: verified structured report rows when available; same-period YoY comparison; "
                "standalone Q2/Q3/Q4 derived from cumulative 6/9/12-month report differences only when period, units, and consolidation basis match."
            ),
            "update_schedule": "Public report discovery weekly; per-issuer update when its own filing link appears. No daily fundamental recalculation.",
            "ranking": "Fundamental outputs are a light quality overlay; the dashboard's separate shortlist rank is technical-first.",
            "limitations": (
                "Public channel snapshots are incomplete discovery aids. Direct table parsing is attempted via Excel/HTML. "
                "Unparsed or absent reports are missing data, not evidence that the issuer did not publish."
            ),
        },
        "warning": (
            "This mode uses public Codal/Telegram links and attempts direct TSETMC client-type history through the CDN mirror and primary host. "
            "A 12-month money-flow score is admitted only when at least 252 sessions, adequate calendar span, fresh dates, and valid real-person values are present. "
            "Official financial tables are scored only after they are parsed; otherwise their scores remain unavailable. "
            "If direct hosts are blocked from the runner, configure BRS_API_KEY under repository Actions secrets to enable the provider-based full scan."
        ),
        "top_50_fundamental_for_review": fundamental_candidates[:50],
        "top_50_money_flow_for_review": money_candidates[:50],
        "financial_report_watchlist": sorted(financial_watchlist, key=lambda r: str(r.get("date") or ""), reverse=True)[:5000],
        "project_disclosure_watchlist": prior_payload.get("project_disclosure_watchlist", []) if isinstance(prior_payload, dict) else [],
        "important_disclosure_watchlist": prior_payload.get("important_disclosure_watchlist", []) if isinstance(prior_payload, dict) else [],
        "stocks": output_rows,
    }
    write_json(OUTPUT_FILE, output)
    print("PUBLIC_CODAL_ONLY_SUMMARY")
    for name, value in output["counts"].items():
        print(f"{name.upper()}:{value}")
    print(f"OUTPUT:{OUTPUT_FILE.relative_to(ROOT)}")


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

def provider_preflight_or_public_fallback():
    """Verify the paid provider once; a paywall or broken key must not create a fake full scan."""
    global BRS_API_KEY, BRS_API_UNAVAILABLE, BRS_API_UNAVAILABLE_REASON, BRS_API_CONSECUTIVE_FAILURES
    if not BRS_API_KEY:
        return False
    try:
        probe_sources()
        return True
    except Exception as exc:
        error_text = str(exc).replace(BRS_API_KEY, "[REDACTED]")[:240]
        print("BRSAPI_PREFLIGHT_FAILED_FALLING_BACK_TO_PUBLIC_SOURCES:" + error_text)
        BRS_API_KEY = ""
        BRS_API_UNAVAILABLE = False
        BRS_API_UNAVAILABLE_REASON = None
        BRS_API_CONSECUTIVE_FAILURES = 0
        return False


def main():
    global BRS_API_KEY
    if "--probe" in sys.argv:
        probe_sources()
        return
    if "--public-codal-only" in sys.argv or not BRS_API_KEY:
        run_public_codal_only()
        return
    if not provider_preflight_or_public_fallback():
        run_public_codal_only()
        return
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
                try:
                    mapping = find_instrument(symbol)
                    if mapping:
                        symbol_map[symbol] = mapping
                except Exception as exc:
                    counts["tsetmc_errors"] += 1
                    if not (TSETMC_UNAVAILABLE and TSETMC_MIRROR_UNAVAILABLE):
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
        elif mapping and mapping.get("ins_code") and not (TSETMC_UNAVAILABLE and TSETMC_MIRROR_UNAVAILABLE):
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
