#!/usr/bin/env python3
"""Build a persistent, source-aware ledger of newly discovered Codal report summaries.

The hosted GitHub runner currently cannot retrieve Codal's official report/Excel hosts.
This processor therefore turns publicly accessible report metadata and channel summaries
into structured *candidate evidence*. It deliberately does not emit a fundamental score:
secondary summary text is not a substitute for validating the official filing tables.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / "data" / "codal_disclosures_latest.json"
LEDGER_FILE = ROOT / "data" / "codal_filing_ledger.json"
LEDGER_VERSION = 1
MAX_REPORTS = 15000
MAX_TEXT = 2800

DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(value: object) -> str:
    text = str(value or "").translate(DIGIT_MAP)
    text = (
        text.replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
        .replace("\u200f", "")
        .replace("\u200e", "")
        .replace("\ufeff", "")
    )
    return re.sub(r"[\s\u00a0\-_]+", "", text).casefold()


def normalized_symbol(value: object) -> str:
    return normalize(value)


def text_digits(value: object) -> str:
    return str(value or "").translate(DIGIT_MAP).replace("٬", ",").replace("،", ",")


def jalali_date(value: object) -> str:
    text = text_digits(value)
    match = re.search(r"(?<!\d)((?:13|14)\d{2})[/-](\d{1,2})[/-](\d{1,2})(?!\d)", text)
    if not match:
        return ""
    year, month, day = map(int, match.groups())
    return f"{year:04d}/{month:02d}/{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else ""


def extract_period_months(text: object) -> int | None:
    value = text_digits(text)
    matches = re.findall(r"(?<!\d)(1[0-2]|[1-9])[\s_\-‌]*ماهه", value)
    if matches:
        return int(matches[0])
    compact = normalize(value)
    if "سالمالیمنتهی" in compact and "ماه" not in compact:
        return 12
    return None


def classification(row: dict) -> str:
    text = " ".join(
        str(row.get(k) or "")
        for k in (
            "report_type", "primary_category", "title", "link_title",
            "source_text", "source_context", "financial_keywords",
        )
    )
    n = normalize(text)
    if any(term in n for term in ("گزارشفعالیتماهانه", "تولیدوفروش", "عملکردماهانه")):
        return "monthly_activity"
    if any(term in n for term in ("صورتهایمالی", "صورت‌مالی", "گزارشحسابرسی", "گزارشتفسیریمدیریت")):
        return "financial_statement"
    if any(term in n for term in ("طرح‌توسعه", "طرحهای‌توسعه", "طرحاحداث", "پیشرفتفیزیکی", "پروژه")):
        return "development_project"
    if any(term in n for term in ("افشایاطلاعاتبااهمیت", "اطلاعاتبااهمیت", "شفافسازی")):
        return "material_disclosure"
    # Current channel classifier already assigns the canonical labels for most records.
    category = normalize(row.get("primary_category") or row.get("report_type") or "")
    if "صورتهایمالی" in category:
        return "financial_statement"
    if "ماهانه" in category or "تولیدوفروش" in category:
        return "monthly_activity"
    return "other_disclosure"


def report_period(row: dict, text: str) -> tuple[str, int | None, str]:
    period = str(row.get("report_period") or "").strip()
    joined = period + " " + str(row.get("title") or "") + " " + text
    months = extract_period_months(period) or extract_period_months(joined)
    period_end = jalali_date(period) or jalali_date(joined)
    if not period and months is not None:
        period = f"{months} ماهه"
        if period_end:
            period += f" منتهی به {period_end}"
    elif not period and period_end:
        period = f"منتهی به {period_end}"
    return period[:100], months, period_end


def serial_from_url(url: object) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        query = parse_qs(urlsplit(text).query)
        for key, values in query.items():
            if key.casefold() == "letterserial" and values:
                return unquote(str(values[0])).strip().casefold()
    except Exception:
        pass
    # Some links use alternate routes; still preserve an URL-derived stable identity.
    return ""


def stable_id(row: dict, symbol: str, url: str, period: str, kind: str, text: str) -> str:
    serial = serial_from_url(url)
    if serial:
        seed = f"{symbol}|serial|{serial}"
    elif url:
        # Ignore display-only query arguments where possible; preserve all other URL content.
        try:
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            query = {k: v for k, v in query.items() if k.casefold() not in {"rt", "ct", "ft", "sheetid"}}
            from urllib.parse import urlencode
            canonical = parts._replace(query=urlencode(query, doseq=True)).geturl()
        except Exception:
            canonical = url
        seed = f"{symbol}|url|{canonical}"
    else:
        seed = "|".join((
            symbol,
            normalize(row.get("company")),
            kind,
            normalize(period),
            jalali_date(row.get("date") or row.get("published_at")),
            normalize(row.get("title") or row.get("link_title")),
            normalize(text)[:1200],
        ))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def split_evidence(value: object) -> list[str]:
    parts = [re.sub(r"\s+", " ", part).strip() for part in str(value or "").split("||")]
    return [part for part in parts if part]


def first_amount(text: str, unit_pattern: str) -> float | None:
    value = text_digits(text)
    match = re.search(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)\s*" + unit_pattern, value, flags=re.I)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def sentence_period(text: str, fallback_months: int | None) -> int | None:
    return extract_period_months(text) or fallback_months


def extract_growth(text: str) -> tuple[float | None, str | None]:
    value = text_digits(text)
    patterns = (
        (r"(\d+(?:\.\d+)?)\s*درصد\s*(?:رشد|افزایش|افزایش داشته|بالا رفته)", 1.0, "افزایش"),
        (r"(\d+(?:\.\d+)?)\s*درصد\s*(?:افت|کاهش|کاهش داشته|پایین آمده)", -1.0, "کاهش"),
        (r"(?:افزایش|رشد)\s*(?:به میزان)?\s*(\d+(?:\.\d+)?)\s*درصد", 1.0, "افزایش"),
        (r"(?:کاهش|افت)\s*(?:به میزان)?\s*(\d+(?:\.\d+)?)\s*درصد", -1.0, "کاهش"),
    )
    for pattern, sign, label in patterns:
        match = re.search(pattern, value)
        if match:
            try:
                return round(float(match.group(1)) * sign, 4), label
            except ValueError:
                continue
    # Preserve the sign as unknown when only "تغییری نکرده" is stated.
    if "تغییری نکرده" in value or "تغییر نکرده" in value:
        return 0.0, "بدون تغییر"
    return None, None


def ocr_telegram_images(images: object, limit: int = 3) -> dict:
    """Best-effort local Tesseract OCR; image text remains unverified candidate evidence."""
    result = {"texts": [], "status": "no_images", "images_seen": 0, "images_ocr_ok": 0}
    if not isinstance(images, list) or not images:
        return result
    result["images_seen"] = len(images)
    executable = os.environ.get("TESSERACT_CMD", "tesseract")
    try:
        probe = subprocess.run([executable, "--list-langs"], capture_output=True, text=True, timeout=5)
        languages = set((probe.stdout or "").splitlines()[1:])
    except (OSError, subprocess.SubprocessError):
        result["status"] = "tesseract_unavailable"
        return result
    lang = "+".join(x for x in ("fas", "eng") if x in languages)
    if not lang:
        result["status"] = "tesseract_language_unavailable"
        return result
    headers = {"User-Agent": "Mozilla/5.0 (compatible; RadarBourse/1.0)", "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*"}
    for item in images[:limit]:
        if not isinstance(item, dict) or not str(item.get("url") or "").startswith("https://"):
            continue
        req = urllib.request.Request(str(item["url"]), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024 or not content_type.startswith("image/"):
                continue
            with tempfile.NamedTemporaryFile(suffix=".img") as image_file:
                image_file.write(data)
                image_file.flush()
                run = subprocess.run([executable, image_file.name, "stdout", "-l", lang, "--psm", "11"],
                                     capture_output=True, text=True, timeout=18)
            text = re.sub(r"[ \\t]+", " ", (run.stdout or "")).strip()
            if run.returncode == 0 and len(text) >= 12:
                result["texts"].append(text[:5000])
                result["images_ocr_ok"] += 1
        except (OSError, urllib.error.URLError, subprocess.SubprocessError, TimeoutError):
            continue
    result["status"] = "ocr_candidate_text" if result["texts"] else "ocr_failed_or_empty"
    return result


def extract_metric_observations(row: dict, fallback_months: int | None) -> dict:
    """Parse numbers only where the surrounding text identifies the metric.

    Values remain secondary-summary candidates. Nothing here is promoted to an audited figure.
    """
    sales_chunks = split_evidence(row.get("sales_evidence"))
    profit_chunks = split_evidence(row.get("profit_evidence"))
    eps_chunks = split_evidence(row.get("eps_evidence"))
    growth_chunks = split_evidence(row.get("growth_evidence"))

    if not sales_chunks and not profit_chunks and not eps_chunks and not growth_chunks:
        # Announcement text sometimes contains a usable summary even if the evidence split
        # did not recognize the exact keyword. Only parse obvious financial sentences.
        source = str(row.get("source_text") or row.get("source_context") or "")
        for segment in re.split(r"(?<=[؛.!؟])\s+", source):
            sn = normalize(segment)
            if any(k in sn for k in ("درآمدفروش", "بهفروشرسانده", "سودشناسایی", "سودمحقق", "به‌ازایهرسهم")):
                if "فروش" in sn or "درآمدفروش" in sn:
                    sales_chunks.append(segment)
                elif "به‌ازایهرسهم" in sn or "به ازای هر سهم" in segment:
                    eps_chunks.append(segment)
                else:
                    profit_chunks.append(segment)

    observations: list[dict] = []

    for sentence in sales_chunks:
        amount = first_amount(sentence, r"میلیون\s*ریال")
        if amount is None:
            continue
        growth, growth_label = extract_growth(sentence)
        observations.append({
            "metric": "revenue_or_sales",
            "value": amount,
            "unit": "میلیون ریال",
            "period_months": sentence_period(sentence, fallback_months),
            "growth_pct_candidate": growth,
            "growth_direction_candidate": growth_label,
            "context": sentence[:700],
        })

    for sentence in profit_chunks:
        # The amount must be tied to a profit phrase. Avoid taking registered capital
        # from a sentence that happens to mention it alongside net profit.
        value = text_digits(sentence)
        match = re.search(
            r"مبلغ\s+([\d,]+(?:\.\d+)?)\s*میلیون\s*ریال\s*(?:سود|زیان)\s*(?:خالص|شناسایی|محقق|عملکرد|کرده|کرده است|ثبت)?",
            value,
        )
        if not match:
            match = re.search(
                r"([\d,]+(?:\.\d+)?)\s*میلیون\s*ریال[^.]{0,80}?(?:سود\s+شناسایی|سود\s+محقق|زیان\s+شناسایی)",
                value,
            )
        if not match:
            continue
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        growth, growth_label = extract_growth(sentence)
        observations.append({
            "metric": "net_profit",
            "value": amount,
            "unit": "میلیون ریال",
            "period_months": sentence_period(sentence, fallback_months),
            "growth_pct_candidate": growth,
            "growth_direction_candidate": growth_label,
            "context": sentence[:700],
        })

    for sentence in eps_chunks:
        value = text_digits(sentence)
        match = re.search(
            r"به\s*ازای\s*هر\s*سهم\s*(?:مبلغ\s*)?([\d,]+(?:\.\d+)?)\s*ریال",
            value,
        )
        if not match:
            # A few summaries put the number before the "سود به ازای هر سهم" phrase.
            match = re.search(
                r"([\d,]+(?:\.\d+)?)\s*ریال[^.]{0,100}?(?:به\s*ازای\s*هر\s*سهم|سود\s*هر\s*سهم)",
                value,
            )
        if not match:
            continue
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        sign = -1.0 if "زیان" in value or "منفی" in value else 1.0
        growth, growth_label = extract_growth(sentence)
        observations.append({
            "metric": "eps",
            "value": amount * sign,
            "unit": "ریال به‌ازای هر سهم",
            "period_months": sentence_period(sentence, fallback_months),
            "growth_pct_candidate": growth,
            "growth_direction_candidate": growth_label,
            "context": sentence[:700],
        })

    # Some channel posts report a growth line without repeating a numeric sales/profit value.
    for sentence in growth_chunks:
        growth, growth_label = extract_growth(sentence)
        if growth is not None:
            observations.append({
                "metric": "yoy_growth",
                "value": growth,
                "unit": "درصد",
                "period_months": sentence_period(sentence, fallback_months),
                "growth_pct_candidate": growth,
                "growth_direction_candidate": growth_label,
                "context": sentence[:700],
            })

    # Deduplicate identical metric/period/number observations from repeated captions.
    unique: dict[tuple, dict] = {}
    for item in observations:
        key = (item["metric"], item["period_months"], item["value"], item["unit"])
        prior = unique.get(key)
        if prior is None or len(item.get("context", "")) > len(prior.get("context", "")):
            unique[key] = item
    return {
        "observations": list(unique.values())[:12],
        "numeric_field_count": len({item["metric"] for item in unique.values()}),
    }


def build_record(row: dict, evidence: dict | None, seen_at: str) -> dict | None:
    evidence = evidence or {}
    image_ocr = ocr_telegram_images(evidence.get("image_attachments"))
    ocr_text = "\\n".join(image_ocr.get("texts", []))
    merged_text = " ".join(
        str(value or "")
        for value in (
            evidence.get("source_text"),
            evidence.get("source_context"),
            row.get("source_context"),
            row.get("link_title"),
            row.get("title"),
            ocr_text,
        )
    )
    link = str(
        evidence.get("codal_url")
        or row.get("url")
        or evidence.get("url")
        or ""
    ).strip()
    symbol = str(evidence.get("symbol") or row.get("symbol") or "").strip()
    company = str(evidence.get("company") or row.get("company") or "").strip()
    kind = classification({
        **row,
        **evidence,
        "title": row.get("title") or row.get("link_title") or "",
        "source_text": merged_text,
        "source_context": merged_text,
    })
    if kind == "other_disclosure":
        return None

    report_type = str(evidence.get("report_type") or row.get("primary_category") or row.get("link_title") or kind)
    period, months, period_end = report_period(
        evidence,
        merged_text + " " + str(row.get("link_title") or "") + " " + str(row.get("title") or ""),
    )
    published = str(
        evidence.get("published_at")
        or row.get("published_at")
        or row.get("date")
        or ""
    ).strip()
    title = str(row.get("title") or row.get("link_title") or "").strip()
    if not title or title in {"📘 Excel", "📥 دانلود فایل Excel", "📥 دانلود فایل PDF"}:
        title = report_type
        if period:
            title += " · " + period
    audit_status = str(evidence.get("audit_status") or "").strip()
    normalized_all = normalize(merged_text + " " + title + " " + audit_status)
    if "اصلاحیه" in normalized_all:
        audit_status = "؛ ".join(x for x in (audit_status, "اصلاحیه") if x and "اصلاحیه" not in x)
        is_correction = True
    else:
        is_correction = False

    official_url = link if "codal.ir" in urlsplit(link).netloc.lower() else ""
    summary_source = (
        str(evidence.get("source_name") or evidence.get("source") or row.get("source") or "")
        .strip()
    )
    text_source = str(evidence.get("source_text") or evidence.get("source_context") or row.get("source_context") or merged_text).strip()[:MAX_TEXT]
    metric_input = dict(evidence)
    if ocr_text:
        metric_input["source_text"] = (str(metric_input.get("source_text") or "") + "\\n" + ocr_text)[:MAX_TEXT * 2]
        # Keep OCR-derived numbers distinguishable from plain Telegram text.
        metric_input["sales_evidence"] = str(metric_input.get("sales_evidence") or "") + " || " + ocr_text
        metric_input["profit_evidence"] = str(metric_input.get("profit_evidence") or "") + " || " + ocr_text
        metric_input["eps_evidence"] = str(metric_input.get("eps_evidence") or "") + " || " + ocr_text
        metric_input["growth_evidence"] = str(metric_input.get("growth_evidence") or "") + " || " + ocr_text
    metric_data = extract_metric_observations(metric_input, months)
    if ocr_text:
        metric_data["source_confidence"] = "low_ocr_candidate"
    else:
        metric_data["source_confidence"] = "secondary_text_candidate" if metric_data["numeric_field_count"] else "metadata_only"
    metric_data["image_ocr"] = {k: v for k, v in image_ocr.items() if k != "texts"}
    metric_data["image_ocr_text"] = ocr_text[:5000]
    rid = stable_id(
        {**row, **evidence, "title": title, "date": published},
        normalized_symbol(symbol),
        official_url or link,
        period,
        kind,
        text_source,
    )
    return {
        "record_id": rid,
        "symbol": symbol,
        "normalized_symbol": normalized_symbol(symbol),
        "company": company,
        "title": title[:260],
        "classification": kind,
        "report_type": report_type[:120],
        "report_period": period,
        "period_months": months,
        "period_end_jalali": period_end,
        "published_at": published[:60],
        "jalali_publish_date": jalali_date(published) or jalali_date(text_source),
        "audit_status": audit_status[:100],
        "is_correction": is_correction,
        "official_url": official_url,
        "telegram_url": str(evidence.get("telegram_url") or evidence.get("telegram_urls") or row.get("telegram_url") or "").strip()[:400],
        "source_name": summary_source[:180],
        "source_text": text_source,
        "image_attachments": (evidence.get("image_attachments") or [])[:12] if isinstance(evidence.get("image_attachments"), list) else [],
        "financial_metrics": metric_data,
        "source_validation_status": (
            str(evidence.get("validation_status") or "")
            or "خلاصهٔ متن عمومی؛ با جدول رسمی کدال تطبیق نشده است"
        )[:260],
        "detail_status": (
            "summary_numbers_extracted"
            if metric_data["numeric_field_count"] > 0
            else "metadata_only"
        ),
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
        "last_seen_in_snapshot": True,
        "seen_count": 1,
        "newly_detected": True,
        "sources": [summary_source] if summary_source else [],
    }


def official_identity(url: object) -> str:
    return serial_from_url(url)


def merge_records(records: list[dict]) -> list[dict]:
    """Merge metadata-only announcement rows into richer channel evidence by serial."""
    by_id: dict[str, dict] = {}
    serial_to_id: dict[str, str] = {}
    for record in records:
        url = record.get("official_url") or ""
        serial = official_identity(url)
        current_id = serial_to_id.get(serial) if serial else None
        rid = current_id or record["record_id"]
        if rid not in by_id:
            record["record_id"] = rid
            by_id[rid] = record
            if serial:
                serial_to_id[serial] = rid
            continue

        prior = by_id[rid]
        # Prefer the richer source text and financial observations.
        if record.get("financial_metrics", {}).get("numeric_field_count", 0) > prior.get("financial_metrics", {}).get("numeric_field_count", 0):
            prior["financial_metrics"] = record["financial_metrics"]
            prior["detail_status"] = record["detail_status"]
        for key in ("symbol", "company", "title", "report_type", "report_period", "period_months",
                    "period_end_jalali", "published_at", "audit_status", "source_validation_status"):
            incoming = record.get(key)
            if incoming not in (None, "") and (prior.get(key) in (None, "") or key in {"title", "source_validation_status"}):
                prior[key] = incoming
        if not prior.get("official_url") and record.get("official_url"):
            prior["official_url"] = record["official_url"]
        if len(record.get("source_text") or "") > len(prior.get("source_text") or "") and record.get("financial_metrics", {}).get("numeric_field_count", 0) >= prior.get("financial_metrics", {}).get("numeric_field_count", 0):
            prior["source_text"] = record["source_text"]
        for src in record.get("sources") or []:
            if src and src not in prior.setdefault("sources", []):
                prior["sources"].append(src)

    return list(by_id.values())


def load_json(path: Path, default: dict | None = None) -> dict:
    if not path.is_file():
        return default.copy() if default else {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else (default.copy() if default else {})
    except (OSError, json.JSONDecodeError):
        return default.copy() if default else {}


def main() -> None:
    if not INPUT_FILE.is_file():
        raise SystemExit(f"Missing disclosure snapshot: {INPUT_FILE}")
    snapshot = load_json(INPUT_FILE)
    previous = load_json(LEDGER_FILE, {"schema_version": LEDGER_VERSION, "reports": []})
    prior_rows = previous.get("reports") if isinstance(previous.get("reports"), list) else []
    previous_by_id = {
        str(row.get("record_id")): row
        for row in prior_rows if isinstance(row, dict) and row.get("record_id")
    }
    seen_at = now_utc()

    announcements = snapshot.get("announcements") if isinstance(snapshot.get("announcements"), list) else []
    evidence_rows = snapshot.get("channel_evidence") if isinstance(snapshot.get("channel_evidence"), list) else []
    built = []
    evidence_by_serial: dict[str, dict] = {}
    evidence_by_symbol_period: dict[tuple, dict] = {}
    for evidence in evidence_rows:
        if not isinstance(evidence, dict):
            continue
        serial = serial_from_url(evidence.get("codal_url"))
        if serial:
            evidence_by_serial[serial] = evidence
        key = (normalized_symbol(evidence.get("symbol")), normalize(evidence.get("report_period")))
        prior_e = evidence_by_symbol_period.get(key)
        if prior_e is None or len(str(evidence.get("source_text") or "")) > len(str(prior_e.get("source_text") or "")):
            evidence_by_symbol_period[key] = evidence

    for row in announcements:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        serial = serial_from_url(url)
        evidence = evidence_by_serial.get(serial) if serial else None
        if evidence is None:
            key = (normalized_symbol(row.get("symbol")), normalize(row.get("report_period") or row.get("source_context")))
            evidence = evidence_by_symbol_period.get(key)
        record = build_record(row, evidence, seen_at)
        if record:
            built.append(record)

    # Some source summaries do not expose a separate link record; retain them on their own.
    linked_serials = {serial_from_url(row.get("official_url")) for row in built if serial_from_url(row.get("official_url"))}
    for evidence in evidence_rows:
        if not isinstance(evidence, dict):
            continue
        serial = serial_from_url(evidence.get("codal_url"))
        if serial and serial in linked_serials:
            continue
        record = build_record(evidence, evidence, seen_at)
        if record:
            built.append(record)

    current = merge_records(built)
    new_ids: set[str] = set()
    new_statement_count = 0
    new_monthly_count = 0
    new_project_count = 0
    new_by_symbol: dict[str, int] = {}

    for row in current:
        rid = str(row.get("record_id") or "")
        prior = previous_by_id.get(rid)
        if prior:
            row["first_seen_at"] = prior.get("first_seen_at") or seen_at
            row["seen_count"] = int(prior.get("seen_count") or 0) + 1
            row["newly_detected"] = False
            # A record remains in the current source window only when seen on this run.
            row["last_seen_at"] = seen_at
            row["last_seen_in_snapshot"] = True
            source_union = list(prior.get("sources") or [])
            for source in row.get("sources") or []:
                if source and source not in source_union:
                    source_union.append(source)
            row["sources"] = source_union
            # Never downgrade an existing numeric extraction because one feed omitted the detail.
            if row.get("financial_metrics", {}).get("numeric_field_count", 0) < prior.get("financial_metrics", {}).get("numeric_field_count", 0):
                row["financial_metrics"] = prior["financial_metrics"]
                row["detail_status"] = prior.get("detail_status", row.get("detail_status"))
        else:
            new_ids.add(rid)
            row["first_seen_at"] = seen_at
            row["seen_count"] = 1
            row["newly_detected"] = True
            row["last_seen_at"] = seen_at
            row["last_seen_in_snapshot"] = True
            kind = row.get("classification")
            if kind == "financial_statement":
                new_statement_count += 1
            elif kind == "monthly_activity":
                new_monthly_count += 1
            elif kind == "development_project":
                new_project_count += 1
        symbol_key = row.get("normalized_symbol") or normalized_symbol(row.get("symbol"))
        if row["newly_detected"] and symbol_key:
            new_by_symbol[symbol_key] = new_by_symbol.get(symbol_key, 0) + 1

    # Preserve old records outside the current Telegram/archive window; don't pretend
    # they have been re-checked just because the ledger was rebuilt.
    merged_by_id = {str(row.get("record_id")): row for row in current if row.get("record_id")}
    for rid, old in previous_by_id.items():
        if rid not in merged_by_id:
            preserved = dict(old)
            preserved["newly_detected"] = False
            preserved["last_seen_in_snapshot"] = False
            merged_by_id[rid] = preserved

    reports = list(merged_by_id.values())
    reports.sort(key=lambda row: (
        str(row.get("published_at") or ""),
        str(row.get("symbol") or ""),
        str(row.get("record_id") or ""),
    ), reverse=True)
    reports = reports[:MAX_REPORTS]

    # Per-symbol per-run summary used by the dashboard, normalized for robust ticker matching.
    tracked_by_symbol: dict[str, int] = {}
    numeric_by_symbol: dict[str, int] = {}
    for row in reports:
        key = str(row.get("normalized_symbol") or normalized_symbol(row.get("symbol")))
        if key:
            tracked_by_symbol[key] = tracked_by_symbol.get(key, 0) + 1
            if (row.get("financial_metrics") or {}).get("numeric_field_count", 0):
                numeric_by_symbol[key] = numeric_by_symbol.get(key, 0) + 1

    parsed_statement_rows = sum(
        1 for row in reports
        if row.get("classification") == "financial_statement"
        and (row.get("financial_metrics") or {}).get("numeric_field_count", 0) > 0
    )
    parsed_monthly_rows = sum(
        1 for row in reports
        if row.get("classification") == "monthly_activity"
        and (row.get("financial_metrics") or {}).get("numeric_field_count", 0) > 0
    )
    summary = {
        "run_generated_at_utc": seen_at,
        "source_snapshot_generated_at_utc": str(snapshot.get("generated_at_utc") or ""),
        "source_snapshot_announcements": len(announcements),
        "source_snapshot_channel_evidence": len(evidence_rows),
        "records_seen_in_snapshot": len(current),
        "total_records_in_ledger": len(reports),
        "new_records_count": len(new_ids),
        "new_financial_statement_records_count": new_statement_count,
        "new_monthly_activity_records_count": new_monthly_count,
        "new_project_records_count": new_project_count,
        "financial_statement_records_with_numeric_summary": parsed_statement_rows,
        "monthly_activity_records_with_numeric_summary": parsed_monthly_rows,
        "records_with_any_numeric_summary": sum(
            1 for row in reports if (row.get("financial_metrics") or {}).get("numeric_field_count", 0) > 0
        ),
        "source_access_mode": "secondary_public_text_summaries",
        "official_statement_tables_verified": False,
        "limitations": [
            "This ledger tracks accessible public announcement/Telegram summary text, not verified official statement-table values.",
            "Direct official Codal/Excel/PDF hosts timed out from the current GitHub Actions runner during source probes.",
            "Numeric values are candidates with source snippets and units; they are excluded from final fundamental/earnings-quality scores until reconciled to the official report.",
            "Monthly activity reports are kept separate from interim and annual financial statements.",
        ],
        "new_records_by_symbol": new_by_symbol,
    }
    output = {
        "schema_version": LEDGER_VERSION,
        "generated_at_utc": seen_at,
        "source_snapshot_generated_at_utc": str(snapshot.get("generated_at_utc") or ""),
        "source": "Bourse-Trader and Codal360 public text summaries plus official report URLs where referenced",
        "source_status": "summary_text_only_official_attachments_not_verified",
        "reports": reports,
        "last_run": summary,
    }
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("CODAL_REPORT_LEDGER_SUMMARY")
    for key in (
        "records_seen_in_snapshot", "total_records_in_ledger", "new_records_count",
        "new_financial_statement_records_count", "new_monthly_activity_records_count",
        "new_project_records_count", "financial_statement_records_with_numeric_summary",
        "monthly_activity_records_with_numeric_summary", "records_with_any_numeric_summary",
    ):
        print(f"{key.upper()}:{summary[key]}")
    print(f"OUTPUT:{LEDGER_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
