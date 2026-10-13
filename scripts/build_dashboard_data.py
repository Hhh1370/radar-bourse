#!/usr/bin/env python3
"""Build a compact, source-aware JSON snapshot consumed by the GitHub Pages dashboard.

The dashboard keeps provisional technical candidates separate from fundamental
and money-flow evidence. Missing/blocked sources remain unavailable, never zero.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TECH_FILE = DATA / "technical_radar.json"
FUND_FILE = DATA / "fundamental_money_radar.json"
CODAL_FILE = DATA / "codal_disclosures_latest.json"
OUTPUT_FILE = DATA / "radar_dashboard.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Required input is missing: {path.relative_to(ROOT)}")
    with path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path.relative_to(ROOT)}")
    return payload


def norm(value: Any) -> str:
    text = str(value or "").strip()
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    text = text.replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")
    return re.sub(r"[\s\-_.]+", "", text).casefold()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def number(value: Any, digits: int = 2):
    if not isinstance(value, (int, float)):
        return None
    return round(float(value), digits)


def shorten(value: Any, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def safe_link(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.startswith(("https://", "http://")) else ""


def index_by_symbol(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in items:
        key = norm(item.get("symbol"))
        if key and key not in result:
            result[key] = item
    return result


def simplify_financial_report(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": shorten(row.get("title"), 320),
        "date": shorten(row.get("date"), 30),
        "url": safe_link(row.get("url")),
        "detail_status": shorten(row.get("detail_status"), 120),
        "ratios": row.get("ratios") if isinstance(row.get("ratios"), dict) else None,
    }


def simplify_disclosure(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": shorten(row.get("title"), 320),
        "date": shorten(row.get("date"), 30),
        "url": safe_link(row.get("url")),
        "keywords": row.get("matched_keywords", []) if isinstance(row.get("matched_keywords"), list) else [],
    }


def simplify_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": shorten(row.get("symbol"), 70),
        "company": shorten(row.get("company"), 140),
        "report_type": shorten(row.get("report_type"), 100),
        "report_period": shorten(row.get("report_period"), 80),
        "published_at": shorten(row.get("published_at"), 50),
        "channel": shorten(row.get("source_name"), 100),
        "sales": shorten(row.get("sales_evidence"), 520),
        "profit": shorten(row.get("profit_evidence"), 520),
        "eps": shorten(row.get("eps_evidence"), 420),
        "dividend": shorten(row.get("cash_dividend_evidence"), 420),
        "growth": shorten(row.get("growth_evidence"), 520),
        "project": shorten(row.get("project_evidence"), 620),
        "project_strength": shorten(row.get("project_evidence_strength"), 40),
        "amounts_raw": shorten(row.get("raw_amount_mentions"), 220),
        "validation_status": shorten(row.get("validation_status"), 180),
        "telegram_url": safe_link(row.get("telegram_url")),
        "codal_url": safe_link(row.get("codal_url")),
        "source_text": shorten(row.get("source_text"), 1500),
    }


def main() -> None:
    tech = read_json(TECH_FILE)
    fund = read_json(FUND_FILE)
    codal = read_json(CODAL_FILE)

    tech_rows = tech.get("top_50_for_review", [])
    fund_rows = fund.get("stocks", [])
    evidence_rows = codal.get("channel_evidence", [])
    announcement_rows = codal.get("announcements", [])
    if not isinstance(tech_rows, list) or not tech_rows:
        raise ValueError("Technical source has no top_50_for_review candidates.")
    if not isinstance(fund_rows, list):
        fund_rows = []
    if not isinstance(evidence_rows, list):
        evidence_rows = []
    if not isinstance(announcement_rows, list):
        announcement_rows = []

    # The existing technical file stores up to 50 already-ranked, eligible
    # instruments. The dashboard shows the first five as technical candidates
    # and the next five as technical watchlist entries—not fundamental picks.
    ranked = tech_rows[:10]
    fund_map = index_by_symbol(fund_rows)
    evidence_map: dict[str, list[dict[str, Any]]] = {}
    for item in evidence_rows:
        key = norm(item.get("symbol"))
        if key:
            evidence_map.setdefault(key, []).append(item)
    for key, items in evidence_map.items():
        items.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)

    candidates = []
    for idx, item in enumerate(ranked, start=1):
        symbol = str(item.get("symbol") or "").strip()
        key = norm(symbol)
        fundamental = fund_map.get(key, {})
        technical_score = number(item.get("technical_score"), 1)
        adjusted_score = number(item.get("risk_adjusted_score"), 1)
        base = {
            "rank": idx,
            "bucket": "main" if idx <= 5 else "watch",
            "bucket_label": "نامزد تکنیکال برای بررسی" if idx <= 5 else "تحت نظر تکنیکال",
            "symbol": symbol,
            "company": str(item.get("company") or fundamental.get("company") or symbol),
            "exchange": str(item.get("exchange") or ""),
            "chart_id": str(item.get("chart_id") or ""),
            "price": number((item.get("price") or {}).get("daily_close"), 4),
            "price_date": str(item.get("latest_daily_date") or ""),
            "data_age_days": item.get("data_age_days"),
            "technical_score": technical_score,
            "risk_adjusted_score": adjusted_score,
            "relative_momentum_score": number(item.get("relative_momentum_score"), 2),
            "momentum_pct": {
                "daily_20": number((item.get("momentum_pct") or {}).get("daily_20_period"), 2),
                "daily_60": number((item.get("momentum_pct") or {}).get("daily_60_period"), 2),
                "weekly_13": number((item.get("momentum_pct") or {}).get("weekly_13_period"), 2),
                "monthly_6": number((item.get("momentum_pct") or {}).get("monthly_6_period"), 2),
            },
            "volume_vs_20_period_average": number(item.get("volume_vs_20_period_average"), 3),
            "rsi14_daily": number(item.get("rsi14_daily"), 2),
            "heat_risk": str(item.get("heat_risk") or "بررسی‌نشده"),
            "entry_timing": str(item.get("entry_timing") or "نیازمند بررسی"),
            "risk_flags": item.get("risk_flags", []) if isinstance(item.get("risk_flags"), list) else [],
            "fundamental_score": number(fundamental.get("fundamental_score"), 2),
            "fundamental_status": str(fundamental.get("fundamental_status") or "داده‌ای برای این نماد در منبع بنیادی موجود نیست"),
            "money_flow_score": number(fundamental.get("money_flow_score"), 2),
            "money_flow_status": str(fundamental.get("money_flow_status") or "داده‌ای برای این نماد در منبع پول حقیقی موجود نیست"),
            "money_flow_history_rows": fundamental.get("money_flow_history_rows"),
            "money_flow_full_year_coverage": fundamental.get("money_flow_full_year_coverage") is True,
            "money_flow_latest_date": str(fundamental.get("money_flow_latest_date") or ""),
            "money_flow_error": shorten(fundamental.get("money_flow_error"), 260),
            "financial_reports": [
                simplify_financial_report(x)
                for x in (fundamental.get("financial_reports") or [])[:3]
                if isinstance(x, dict)
            ],
            "important_disclosures": [
                simplify_disclosure(x)
                for x in (fundamental.get("important_disclosures") or [])[:4]
                if isinstance(x, dict)
            ],
            "channel_evidence": [
                simplify_evidence(x)
                for x in evidence_map.get(key, [])[:4]
            ],
        }
        base["data_quality"] = {
            "technical_available": technical_score is not None,
            "fundamental_scored": base["fundamental_score"] is not None,
            "money_flow_scored": base["money_flow_score"] is not None,
            "money_flow_12m_verified": base["money_flow_full_year_coverage"] and (base["money_flow_history_rows"] or 0) >= 200,
            "channel_evidence_count": len(evidence_map.get(key, [])),
        }
        candidates.append(base)

    top_scores_count = sum(1 for x in fund_rows if number(x.get("fundamental_score")) is not None)
    full_year_flow_count = sum(
        1 for x in fund_rows
        if x.get("money_flow_full_year_coverage") is True
        and isinstance(x.get("money_flow_history_rows"), (int, float))
        and x.get("money_flow_history_rows") >= 200
        and number(x.get("money_flow_score")) is not None
    )
    project_candidates = [
        simplify_evidence(x) for x in evidence_rows
        if x.get("project_evidence")
        and x.get("project_evidence_strength") in {"قوی", "متوسط"}
    ]
    project_candidates.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)

    source_warnings = []
    counts = fund.get("counts", {}) if isinstance(fund.get("counts"), dict) else {}
    if top_scores_count == 0:
        source_warnings.append("امتیاز بنیادی استانداردشده برای هیچ نمادی ساخته نشده است؛ دادهٔ ناقص به معنی بنیادی خنثی نیست.")
    if full_year_flow_count == 0:
        source_warnings.append("هیچ امتیاز پول حقیقی با پوشش ۱۲ماهه در خروجی بنیادی قابل تأیید نیست.")
    if not project_candidates:
        source_warnings.append("شواهد متنی کافی دربارهٔ طرح‌های توسعه در آرشیو فعلی کم است؛ نبود رکورد، به معنی نبود پروژه نیست.")
    if not any(
        x.get("http_status") == 200 and x.get("body_bytes", 0) > 0
        for x in codal.get("source_status", [])
        if isinstance(x, dict)
    ):
        source_warnings.append("دسترسی به منابع اطلاعیه در اجرای اخیر تأیید نشده است.")

    latest_technical = str(tech.get("generated_at_utc") or "")
    latest_fundamental = str(fund.get("generated_at_utc") or "")
    latest_codal = str(codal.get("generated_at_utc") or "")
    output = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "dashboard_status": "provisional",
        "title": "رادار بورس ایران",
        "subtitle": "نامزدهای تکنیکال و پوشش شواهد بنیادی؛ نه توصیهٔ خرید",
        "summary": {
            "universe_count": tech.get("input_universe_count"),
            "technical_analyzed": tech.get("analyzed_count"),
            "technical_eligible": tech.get("eligible_for_long_term_ranking"),
            "technical_errors": tech.get("analysis_errors"),
            "technical_candidates_shown": len(candidates),
            "technical_main_count": sum(1 for x in candidates if x["bucket"] == "main"),
            "technical_watch_count": sum(1 for x in candidates if x["bucket"] == "watch"),
            "fundamental_scored_count": top_scores_count,
            "money_flow_12m_scored_count": full_year_flow_count,
            "codal_disclosure_links": len(announcement_rows),
            "channel_evidence_rows": len(evidence_rows),
            "project_evidence_rows": len(project_candidates),
            "statement_reports_detailed": counts.get("statement_reports_detailed", 0),
        },
        "sources": {
            "technical": {
                "generated_at_utc": latest_technical,
                "label": "تاریخچهٔ قیمت نهایت‌نگر",
                "status": "موجود",
                "note": str(tech.get("warning") or ""),
            },
            "fundamental": {
                "generated_at_utc": latest_fundamental,
                "label": "BrsApi / دادهٔ بنیادی موجود",
                "status": "ناقص" if top_scores_count == 0 else "موجود",
                "note": str(fund.get("warning") or fund.get("codal_api_note") or ""),
            },
            "codal_channels": {
                "generated_at_utc": latest_codal,
                "label": "بورس‌تریدر و کدال۳۶۰",
                "status": "خلاصهٔ متنی",
                "note": str(codal.get("quality_note") or ""),
            },
        },
        "warnings": source_warnings,
        "methodology": {
            "main_and_watch": "۵ ردیف اول و ۵ ردیف بعدیِ خروجی تکنیکال فعلی؛ این تقسیم‌بندی، انتخاب نهایی بنیادی نیست.",
            "technical_score": "امتیاز تکنیکال و جریمهٔ گرمای روند از فایل رادار تکنیکال خوانده می‌شود. امتیاز نسبی برای شکستن تساوی استفاده می‌شود و هنوز بک‌تست نشده است.",
            "fundamental_score": "فقط در صورت وجود امتیاز محاسبه‌شده نشان داده می‌شود. دادهٔ گمشده به صفر یا وضعیت خنثی تبدیل نشده است.",
            "money_flow": "پوشش ۱۲ماهه فقط وقتی تأیید می‌شود که منبع هر دو پوشش و حداقل ۲۰۰ ردیف تاریخچه را گزارش کرده باشد؛ برای امتیاز معتبر، score هم باید موجود باشد.",
            "channel_evidence": "خلاصه‌های متنی تلگرام، شواهد خام هستند و جایگزین تطبیق با صورت مالی/اطلاعیهٔ رسمی نیستند.",
        },
        "candidates": candidates,
        "recent_evidence": [
            simplify_evidence(x)
            for x in sorted(
                [x for x in evidence_rows if isinstance(x, dict)],
                key=lambda x: str(x.get("published_at") or ""),
                reverse=True,
            )[:80]
        ],
        "recent_projects": project_candidates[:30],
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Re-read and validate what the app will consume.
    check = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    assert len(check.get("candidates", [])) == len(candidates)
    assert sum(1 for x in check["candidates"] if x["bucket"] == "main") <= 5
    assert sum(1 for x in check["candidates"] if x["bucket"] == "watch") <= 5
    print("RADAR_DASHBOARD_DATA_SUMMARY")
    print(f"TECHNICAL_UNIVERSE:{check['summary']['universe_count']}")
    print(f"TECHNICAL_ELIGIBLE:{check['summary']['technical_eligible']}")
    print(f"TECHNICAL_MAIN:{check['summary']['technical_main_count']}")
    print(f"TECHNICAL_WATCH:{check['summary']['technical_watch_count']}")
    print(f"FUNDAMENTAL_SCORED:{check['summary']['fundamental_scored_count']}")
    print(f"MONEY_FLOW_12M_SCORED:{check['summary']['money_flow_12m_scored_count']}")
    print(f"CHANNEL_EVIDENCE:{check['summary']['channel_evidence_rows']}")
    print(f"OUTPUT:{OUTPUT_FILE.relative_to(ROOT)}")
    print(f"STATUS:{check['dashboard_status']}")


if __name__ == "__main__":
    main()
