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




RATING_WEIGHTS = {
    "fundamental": 30,
    "earnings_quality": 15,
    "technical": 25,
    "money_flow": 20,
    "projects": 10,
}


def metric_number(report: dict[str, Any], key: str):
    value = (report.get("financial_metrics") or {}).get(key)
    if not isinstance(value, dict):
        return None
    primary = value.get("primary_value_candidate")
    return float(primary) if isinstance(primary, (int, float)) else None


def calculate_earnings_quality(fundamental: dict[str, Any]) -> dict[str, Any]:
    """Score earnings quality only from parsed structured report fields—not post keywords."""
    reports = [
        row for row in (fundamental.get("financial_reports") or [])
        if isinstance(row, dict)
        and isinstance(row.get("financial_metrics"), dict)
        and isinstance(row.get("ratios"), dict)
    ]
    reports.sort(key=lambda row: str(row.get("date") or ""), reverse=True)
    if not reports:
        return {
            "score": None,
            "status": "دادهٔ ساختاریافتهٔ قابل‌خواندن از صورت مالی موجود نیست",
            "fields_observed": 0,
            "report_date": "",
            "evidence": [],
        }

    report = reports[0]
    ratios = report.get("ratios") or {}
    required_keys = (
        "revenue", "net_profit", "operating_cash_flow", "total_assets",
        "total_liabilities", "current_assets", "current_liabilities",
    )
    observed = [key for key in required_keys if metric_number(report, key) is not None]
    net_profit = metric_number(report, "net_profit")
    revenue = metric_number(report, "revenue")
    operating_cash = metric_number(report, "operating_cash_flow")
    margin = ratios.get("net_margin_pct")
    cash_conversion = ratios.get("operating_cash_flow_to_net_profit")
    leverage = ratios.get("liabilities_to_assets_pct")
    current_ratio = ratios.get("current_ratio")

    if len(observed) < 4 or net_profit is None or revenue in (None, 0):
        return {
            "score": None,
            "status": "پوشش اقلام صورت مالی برای سنجش کیفیت سود ناکافی است",
            "fields_observed": len(observed),
            "report_date": str(report.get("date") or ""),
            "evidence": [],
        }

    score = 50.0
    evidence = []
    if net_profit > 0:
        score += 10; evidence.append("سود خالص مثبت در دادهٔ ساختاریافته")
    else:
        score -= 20; evidence.append("هشدار: سود خالص صفر یا منفی")
    if operating_cash is not None:
        if operating_cash > 0:
            score += 15; evidence.append("جریان نقد عملیاتی مثبت")
        else:
            score -= 15; evidence.append("هشدار: جریان نقد عملیاتی غیرمثبت")
    if isinstance(cash_conversion, (int, float)):
        if cash_conversion >= 0.8:
            score += 15; evidence.append("تبدیل سود به نقد مناسب (حداقل ۰٫۸)")
        elif cash_conversion >= 0:
            score += 5; evidence.append("تبدیل سود به نقد مثبت اما محدود")
        else:
            score -= 10; evidence.append("هشدار: جریان نقد با سود خالص هم‌جهت نیست")
    if isinstance(margin, (int, float)):
        if margin >= 15:
            score += 10; evidence.append("حاشیه سود خالص حداقل ۱۵٪")
        elif margin > 0:
            score += 5; evidence.append("حاشیه سود خالص مثبت")
        else:
            score -= 10; evidence.append("حاشیه سود خالص منفی")
    if isinstance(leverage, (int, float)):
        if leverage <= 60:
            score += 10; evidence.append("نسبت بدهی به دارایی حداکثر ۶۰٪")
        elif leverage >= 85:
            score -= 10; evidence.append("هشدار: نسبت بدهی به دارایی بالا")
    if isinstance(current_ratio, (int, float)):
        if current_ratio >= 1.2:
            score += 10; evidence.append("نسبت جاری حداقل ۱٫۲")
        elif current_ratio < 1:
            score -= 10; evidence.append("هشدار: نسبت جاری کمتر از ۱")

    return {
        "score": round(max(0, min(100, score)), 1),
        "status": "کیفیت سود مقدماتی از اقلام ساختاریافته؛ نیازمند کنترل دوره/واحد/حسابرسی",
        "fields_observed": len(observed),
        "report_date": str(report.get("date") or ""),
        "report_title": shorten(report.get("title"), 220),
        "evidence": evidence,
        "coverage": round(len(observed) / len(required_keys), 2),
        "audit_hint": "حسابرسی‌شده" if "حسابرسی شده" in norm(report.get("title", "")) else "وضعیت حسابرسی نیازمند بررسی",
    }


def score_project_evidence(fundamental: dict[str, Any], evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Do not award a project score until a report-level numeric score is explicitly validated."""
    assessment = fundamental.get("project_assessment")
    if isinstance(assessment, dict):
        score = assessment.get("score")
        validated = assessment.get("report_level_validated") is True or assessment.get("validated") is True
        if isinstance(score, (int, float)) and validated:
            return {
                "score": round(max(0, min(100, float(score))), 1),
                "status": "امتیاز طرح با دادهٔ عددی گزارش‌سطحی و پرچم اعتبارسنجی",
                "validated": True,
                "evidence_count": len(evidence_rows),
            }
    candidates = [
        row for row in evidence_rows
        if row.get("project")
        and row.get("project_strength") in {"قوی", "متوسط"}
        and row.get("codal_url")
    ]
    return {
        "score": None,
        "status": "شاهد متنی هست، اما هزینه/زمان بهره‌برداری/بازده طرح ساختاریافته تأیید نشده",
        "validated": False,
        "evidence_count": len(candidates),
    }


def technical_quality(item: dict[str, Any]) -> float:
    bars = item.get("history_bars") or {}
    daily, weekly, monthly = bars.get("daily") or 0, bars.get("weekly") or 0, bars.get("monthly") or 0
    age = item.get("data_age_days")
    if not isinstance(age, (int, float)) or age < 0 or age > 10:
        return 0.0
    if daily < 200 or weekly < 26 or monthly < 12:
        return 0.0
    history_factor = min(1.0, daily / 500.0, weekly / 52.0, monthly / 24.0)
    age_factor = 1.0 if age <= 3 else (0.85 if age <= 7 else 0.65)
    return round(max(0, min(1, history_factor * age_factor)), 3)


def valid_money_flow(fundamental: dict[str, Any]) -> bool:
    rows = fundamental.get("money_flow_history_rows")
    return (
        fundamental.get("instrument_kind") == "common_equity"
        and isinstance(fundamental.get("money_flow_score"), (int, float))
        and fundamental.get("money_flow_full_year_coverage") is True
        and isinstance(rows, (int, float)) and rows >= 200
        and bool(fundamental.get("money_flow_latest_date"))
        and not str(fundamental.get("money_flow_error") or "").strip()
    )


def credit_assessment(
    tech_item: dict[str, Any],
    fundamental: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    project_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Return a provisional blended score and data-completeness confidence, not profit probability."""
    technical_score = tech_item.get("technical_rank_score")
    if not isinstance(technical_score, (int, float)):
        technical_score = tech_item.get("risk_adjusted_score")
    technical_score = float(technical_score) if isinstance(technical_score, (int, float)) else None
    fundamental_score = fundamental.get("fundamental_score")
    fundamental_score = float(fundamental_score) if isinstance(fundamental_score, (int, float)) else None
    earnings = calculate_earnings_quality(fundamental)
    earnings_score = earnings.get("score")
    flow_ok = valid_money_flow(fundamental)
    flow_score = float(fundamental["money_flow_score"]) if flow_ok else None
    project_score = project_evidence.get("score")
    project_score = float(project_score) if isinstance(project_score, (int, float)) and project_evidence.get("validated") is True else None

    components = {
        "fundamental": fundamental_score,
        "earnings_quality": earnings_score,
        "technical": technical_score,
        "money_flow": flow_score,
        "projects": project_score,
    }
    weights = {"fundamental": 30, "earnings_quality": 15, "technical": 25, "money_flow": 20, "projects": 10}
    available_weight = sum(weights[key] for key, value in components.items() if isinstance(value, (int, float)))
    provisional_score = (
        round(sum(weights[key] * float(value) for key, value in components.items() if isinstance(value, (int, float))) / available_weight, 1)
        if available_weight else None
    )

    flow_rows = fundamental.get("money_flow_history_rows")
    flow_quality = 1.0 if flow_ok else (
        0.5 if isinstance(fundamental.get("money_flow_score"), (int, float))
        and isinstance(flow_rows, (int, float)) and flow_rows >= 60 else 0.0
    )
    t_quality = technical_quality(tech_item)
    parsed_report = any(
        isinstance(row, dict) and isinstance(row.get("financial_metrics"), dict)
        and isinstance(row.get("ratios"), dict)
        for row in (fundamental.get("financial_reports") or [])
    )
    fund_quality = 1.0 if fundamental_score is not None and parsed_report else 0.0
    earnings_quality = min(1.0, float(earnings.get("coverage") or 0)) if earnings_score is not None else 0.0
    project_quality = 1.0 if project_score is not None else (
        0.35 if project_evidence.get("evidence_count", 0) > 0 else 0.0
    )
    confidence_factors = {
        "fundamental": fund_quality,
        "earnings_quality": earnings_quality,
        "technical": t_quality,
        "money_flow": flow_quality,
        "projects": project_quality,
    }
    confidence_score = round(sum(weights[key] * confidence_factors[key] for key in weights), 1)

    final_ready = (
        fundamental_score is not None and earnings_score is not None
        and technical_score is not None and flow_score is not None
        and project_score is not None and confidence_score >= 75
    )
    if final_ready:
        reliability_label = "پوشش مناسب برای بازبینی نهایی"
        rating_status = "امتیاز تلفیقی قابل محاسبه؛ نیازمند بازبینی انسانی و آزمون تاریخی"
    elif confidence_score >= 60:
        reliability_label = "متوسط؛ داده‌های مهم ناقص"
        rating_status = "موقت؛ رتبهٔ اعتباری نهایی صادر نشده"
    elif confidence_score >= 35:
        reliability_label = "پایین؛ اتکا محدود"
        rating_status = "موقت؛ برای رتبهٔ نهایی بنیادی/پروژه‌ای کافی نیست"
    else:
        reliability_label = "بسیار پایین؛ داده ناکافی"
        rating_status = "تعلیق رتبهٔ نهایی؛ فقط غربالگری تکنیکال/دادهٔ محدود"

    return {
        "provisional_score": provisional_score,
        "provisional_score_available_weight_pct": round(available_weight, 1),
        "component_scores": components,
        "component_weights_pct": weights,
        "earnings_quality_status": earnings.get("status"),
        "earnings_quality_evidence": earnings.get("evidence", []),
        "earnings_quality_fields_observed": earnings.get("fields_observed", 0),
        "earnings_quality_report_date": earnings.get("report_date", ""),
        "project_score": project_score,
        "project_score_status": project_evidence.get("status"),
        "rating_confidence_score": confidence_score,
        "rating_confidence_label": reliability_label,
        "rating_confidence_is_data_coverage_not_profit_probability": True,
        "confidence_factors_pct": {key: round(value * 100, 1) for key, value in confidence_factors.items()},
        "final_rating_eligible": final_ready,
        "final_rating_status": rating_status,
        "final_credit_grade": None if not final_ready else (
            "A" if provisional_score >= 80 else
            "B" if provisional_score >= 70 else
            "C" if provisional_score >= 60 else "D"
        ),
        "provisional_input_components": [key for key, value in components.items() if isinstance(value, (int, float))],
    }


def main() -> None:
    tech = read_json(TECH_FILE)
    fund = read_json(FUND_FILE)
    codal = read_json(CODAL_FILE)

    tech_rows = tech.get("all_eligible_ranked")
    if not isinstance(tech_rows, list) or not tech_rows:
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

    # Score every eligible instrument before forming the provisional 5+5.
    # Final investment/credit grades remain withheld when material data layers are missing.
    ranked = tech_rows
    fund_map = index_by_symbol(fund_rows)
    evidence_map: dict[str, list[dict[str, Any]]] = {}
    for item in evidence_rows:
        key = norm(item.get("symbol"))
        if key:
            evidence_map.setdefault(key, []).append(item)
    for key, items in evidence_map.items():
        items.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)

    candidates = []
    for item in ranked:
        symbol = str(item.get("symbol") or "").strip()
        key = norm(symbol)
        fundamental = fund_map.get(key, {})
        technical_score = number(item.get("technical_score"), 1)
        adjusted_score = number(item.get("risk_adjusted_score"), 1)
        symbol_evidence = evidence_map.get(key, [])
        project_assessment = score_project_evidence(fundamental, symbol_evidence)
        rating = credit_assessment(item, fundamental, symbol_evidence, project_assessment)
        base = {
            "rank": None,
            "bucket": "watch",
            "bucket_label": "نامزد موقت رادار",
            "symbol": symbol,
            "company": str(item.get("company") or fundamental.get("company") or symbol),
            "exchange": str(item.get("exchange") or ""),
            "chart_id": str(item.get("chart_id") or ""),
            "price": number((item.get("price") or {}).get("daily_close"), 4),
            "price_date": str(item.get("latest_daily_date") or ""),
            "data_age_days": item.get("data_age_days"),
            "technical_score": technical_score,
            "risk_adjusted_score": adjusted_score,
            "technical_rank_score": number(item.get("technical_rank_score"), 2),
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
            "money_flow_score": number(fundamental.get("money_flow_score"), 2) if valid_money_flow(fundamental) else None,
            "money_flow_status": str(fundamental.get("money_flow_status") or "داده‌ای برای این نماد در منبع پول حقیقی موجود نیست"),
            "instrument_kind": str(fundamental.get("instrument_kind") or "unconfirmed"),
            "credit_assessment": rating,
            "credit_score": rating.get("provisional_score"),
            "credit_confidence_score": rating.get("rating_confidence_score"),
            "credit_confidence_label": rating.get("rating_confidence_label"),
            "final_credit_grade": rating.get("final_credit_grade"),
            "final_rating_status": rating.get("final_rating_status"),
            "earnings_quality_score": rating.get("component_scores", {}).get("earnings_quality"),
            "project_score": rating.get("project_score"),
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
            "money_flow_12m_verified": valid_money_flow(fundamental),
            "channel_evidence_count": len(symbol_evidence),
            "parsed_financial_report_count": sum(
                1 for report in (fundamental.get("financial_reports") or [])
                if isinstance(report, dict)
                and isinstance(report.get("financial_metrics"), dict)
                and isinstance(report.get("ratios"), dict)
            ),
            "final_credit_grade_available": rating.get("final_rating_eligible") is True,
        }
        candidates.append(base)

    candidates.sort(
        key=lambda row: (
            -(float(row.get("credit_score")) if isinstance(row.get("credit_score"), (int, float)) else -1.0),
            -float(row.get("technical_rank_score") or row.get("risk_adjusted_score") or 0),
            -float(row.get("credit_confidence_score") or 0),
            str(row.get("symbol") or ""),
        )
    )
    for index, row in enumerate(candidates, start=1):
        row["rank"] = index
        row["bucket"] = "main" if index <= 5 else "watch"
        row["bucket_label"] = "۵ نامزد موقت رادار" if index <= 5 else "۵ نماد تحت‌نظر موقت"

    final_grade_count = sum(1 for x in candidates if x.get("final_credit_grade") is not None)
    reliable_confidence_count = sum(1 for x in candidates if (x.get("credit_confidence_score") or 0) >= 75)
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
            "technical_candidates_shown": min(10, len(candidates)),
            "technical_main_count": min(5, len(candidates)),
            "technical_watch_count": max(0, min(5, len(candidates) - 5)),
            "candidate_pool_ranked": len(candidates),
            "fundamental_scored_count": top_scores_count,
            "final_credit_grade_count": final_grade_count,
            "high_confidence_count": reliable_confidence_count,
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
            "credit_model": "وزن‌های امتیاز تلفیقی: بنیادی ۳۰٪، کیفیت سود ۱۵٪، تکنیکال ۲۵٪، پول حقیقی ۲۰٪ و طرح توسعه ۱۰٪. فقط اجزای عددی معتبر در امتیاز موقت وارد می‌شوند؛ وزن پوشش موجود جداگانه اعلام می‌شود.",
            "final_rating_gate": "رتبهٔ نهایی فقط در صورت موجود بودن امتیاز بنیادی، کیفیت سود ساختاریافته، تکنیکال، پول حقیقی ۱۲ماهه و امتیاز کمی اعتبارسنجی‌شده طرح‌ها و اطمینان پوشش حداقل ۷۵ صادر می‌شود. در غیر این صورت رتبه معلق می‌ماند.",
            "confidence": "امتیاز اطمینان، سنجهٔ پوشش و کیفیت داده است؛ احتمال سود یا احتمال درست بودن پیش‌بینی نیست.",
            "earnings_quality": "فقط از اقلام عددی خوانده‌شده از صورت مالی ساختاریافته محاسبه می‌شود؛ کلیدواژه‌های کانال در امتیاز کیفیت سود وارد نمی‌شوند.",
            "money_flow": "فقط وقتی در امتیاز تلفیقی وارد می‌شود که نماد سهام عادی، تاریخچه حداقل ۲۰۰ جلسه، پرچم پوشش سالانه، تاریخ آخرین داده و امتیاز معتبر داشته باشد.",
            "channel_evidence": "خلاصه‌های متنی تلگرام، شواهد خام هستند و جایگزین تطبیق با صورت مالی/اطلاعیهٔ رسمی نیستند.",
        },
        "candidates": candidates[:10],
        "all_candidate_count": len(candidates),
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
    assert len(check.get("candidates", [])) == min(10, len(candidates))
    assert sum(1 for x in check["candidates"] if x["bucket"] == "main") == min(5, len(candidates))
    assert sum(1 for x in check["candidates"] if x["bucket"] == "watch") == max(0, min(5, len(candidates) - 5))
    assert all("credit_confidence_score" in x for x in check["candidates"])
    assert all("final_credit_grade" in x for x in check["candidates"])
    if check["summary"]["fundamental_scored_count"] == 0:
        assert all(x["final_credit_grade"] is None for x in check["candidates"]), "Cannot issue final rating without fundamental scores"
    print("RADAR_DASHBOARD_DATA_SUMMARY")
    print(f"TECHNICAL_UNIVERSE:{check['summary']['universe_count']}")
    print(f"TECHNICAL_ELIGIBLE:{check['summary']['technical_eligible']}")
    print(f"TECHNICAL_MAIN:{check['summary']['technical_main_count']}")
    print(f"TECHNICAL_WATCH:{check['summary']['technical_watch_count']}")
    print(f"CANDIDATE_POOL_RANKED:{check['summary']['candidate_pool_ranked']}")
    print(f"FUNDAMENTAL_SCORED:{check['summary']['fundamental_scored_count']}")
    print(f"MONEY_FLOW_12M_SCORED:{check['summary']['money_flow_12m_scored_count']}")
    print(f"FINAL_CREDIT_GRADES:{check['summary']['final_credit_grade_count']}")
    print(f"HIGH_CONFIDENCE_COUNT:{check['summary']['high_confidence_count']}")
    for row in check["candidates"]:
        print(f"RANK:{row['rank']} SYMBOL:{row['symbol']} SCORE:{row.get('credit_score')} CONFIDENCE:{row.get('credit_confidence_score')} FINAL_GRADE:{row.get('final_credit_grade')} STATUS:{row.get('final_rating_status')}")
    print(f"CHANNEL_EVIDENCE:{check['summary']['channel_evidence_rows']}")
    print(f"OUTPUT:{OUTPUT_FILE.relative_to(ROOT)}")
    print(f"STATUS:{check['dashboard_status']}")


if __name__ == "__main__":
    main()
