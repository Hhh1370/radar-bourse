#!/usr/bin/env python3
"""Conservative fundamental screening from public Codal360/Telegram report summaries.

These scores are explicitly provisional: the source is a public summary, not a
parsed/reconciled official statement table. Monthly activity is tracked separately.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def norm(value: Any) -> str:
    text = str(value or "").translate(DIGIT_MAP)
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = text.replace("\u200c", "").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"[\s\-_.]+", "", text).casefold()


def as_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(float(str(value).translate(DIGIT_MAP)))
    except (ValueError, TypeError):
        return None


def period_end(row: dict[str, Any]) -> str:
    value = str(row.get("period_end_jalali") or "").replace("-", "/")
    match = re.search(r"((?:13|14)\d{2})/(\d{1,2})/(\d{1,2})", value)
    if not match:
        return ""
    year, month, day = map(int, match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return ""
    return f"{year:04d}/{month:02d}/{day:02d}"


def _observations(row: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = row.get("financial_metrics")
    values = metrics.get("observations") if isinstance(metrics, dict) else None
    return [x for x in values if isinstance(x, dict)] if isinstance(values, list) else []


def _metric_value(row: dict[str, Any], metric: str) -> float | None:
    rows = [x for x in _observations(row) if x.get("metric") == metric]
    if not rows:
        return None
    months = as_int(row.get("period_months"))
    same_period = [x for x in rows if as_int(x.get("period_months")) in (None, months)]
    chosen = same_period[-1] if same_period else rows[-1]
    try:
        value = float(chosen.get("value"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    context = str(chosen.get("context") or "")
    # Some summary parsers capture a loss amount as a positive numeral.
    if metric == "net_profit" and ("زیان" in context or "منفی" in context):
        value = -abs(value)
    if metric == "eps" and ("زیان" in context or "منفی" in context):
        value = -abs(value)
    return value


def _reported_growth(row: dict[str, Any], preferred_metric: str) -> float | None:
    for metric in (preferred_metric, "yoy_growth"):
        for item in _observations(row):
            if item.get("metric") != metric:
                continue
            value = item.get("growth_pct_candidate")
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
            # A standalone yoy_growth observation stores its percentage in value.
            if metric == "yoy_growth" and isinstance(item.get("value"), (int, float)):
                candidate = float(item["value"])
                if math.isfinite(candidate):
                    return candidate
    return None


def _growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior <= 0:
        return None
    value = (current / prior - 1.0) * 100.0
    return round(value, 2) if math.isfinite(value) else None


def _matching_prior(current: dict[str, Any], statements: list[dict[str, Any]]) -> dict[str, Any] | None:
    current_end = period_end(current)
    months = as_int(current.get("period_months"))
    if not current_end or months not in (3, 6, 9, 12):
        return None
    y, md = current_end.split("/", 1)
    wanted = f"{int(y) - 1:04d}/{md}"
    candidates = [
        row for row in statements
        if row is not current
        and as_int(row.get("period_months")) == months
        and period_end(row) == wanted
        and _observations(row)
    ]
    candidates.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
    return candidates[0] if candidates else None


def _score(report: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    profit = _metric_value(report, "net_profit")
    eps = _metric_value(report, "eps")
    revenue = _metric_value(report, "revenue_or_sales")
    prior_profit = _metric_value(prior, "net_profit") if prior else None
    prior_eps = _metric_value(prior, "eps") if prior else None
    prior_revenue = _metric_value(prior, "revenue_or_sales") if prior else None

    profit_growth = _growth(profit, prior_profit)
    eps_growth = _growth(eps, prior_eps)
    revenue_growth = _growth(revenue, prior_revenue)
    # Prefer same-period calculations; a growth rate in the summary is the fallback.
    if profit_growth is None:
        profit_growth = _reported_growth(report, "net_profit")
    if eps_growth is None:
        eps_growth = _reported_growth(report, "eps")
    if revenue_growth is None:
        revenue_growth = _reported_growth(report, "revenue_or_sales")

    score = 50.0
    evidence = []
    observed = 0
    if profit is not None:
        observed += 1
        if profit > 0:
            score += 12
            evidence.append("سود خالص مثبت طبق خلاصه گزارش")
        elif profit < 0:
            score -= 25
            evidence.append("زیان خالص طبق خلاصه گزارش")
        else:
            score -= 10
            evidence.append("سود خالص صفر طبق خلاصه گزارش")
    if eps is not None:
        observed += 1
        if eps > 0:
            score += 4
            evidence.append("EPS مثبت")
        elif eps < 0:
            score -= 12
            evidence.append("EPS منفی")
    if revenue is not None:
        observed += 1
    if profit is not None and revenue is not None and revenue > 0:
        margin = profit / revenue * 100.0
        if margin >= 15:
            score += 6
            evidence.append("حاشیه سود خالص بالاتر از ۱۵٪ (محاسبه مقدماتی)")
        elif margin > 0:
            score += 3
            evidence.append("حاشیه سود خالص مثبت (محاسبه مقدماتی)")
        else:
            score -= 8
            evidence.append("حاشیه سود خالص منفی (محاسبه مقدماتی)")

    def growth_points(value: float | None, positive_mid: float, positive_high: float,
                      negative_mid: float, negative_high: float) -> int:
        if value is None:
            return 0
        if value >= 25:
            return positive_high
        if value > 0:
            return positive_mid
        if value <= -25:
            return negative_high
        if value < 0:
            return negative_mid
        return 0

    profit_growth_points = growth_points(profit_growth, 7, 14, -8, -15)
    revenue_growth_points = growth_points(revenue_growth, 4, 8, -5, -10)
    eps_growth_points = growth_points(eps_growth, 2, 4, -3, -5)
    score += profit_growth_points + revenue_growth_points + eps_growth_points
    if profit_growth is not None:
        evidence.append(f"رشد/افت سود خالص گزارش‌شده یا مقایسه‌ای: {profit_growth:.1f}٪")
    if revenue_growth is not None:
        evidence.append(f"رشد/افت فروش گزارش‌شده یا مقایسه‌ای: {revenue_growth:.1f}٪")
    if eps_growth is not None:
        evidence.append(f"رشد/افت EPS گزارش‌شده یا مقایسه‌ای: {eps_growth:.1f}٪")

    score = round(max(0.0, min(100.0, score)), 1)
    official_url = str(report.get("official_url") or "")
    telegram_url = str(report.get("telegram_url") or "")
    quality = min(0.60, 0.25 + 0.08 * observed + (0.03 if official_url else 0.0))
    if "حسابرسیشده" in norm(report.get("title") or "") or "حسابرسی شده" in str(report.get("audit_status") or ""):
        quality = min(0.62, quality + 0.02)

    period_months = as_int(report.get("period_months"))
    period_label = f"{period_months}ماهه" if period_months else "نامشخص"
    status = (
        "امتیاز بنیادی مقدماتی از خلاصهٔ عمومی کدال۳۶۰/تلگرام؛ "
        "ارقام جدول رسمی هنوز ماشینی با PDF/Excel تطبیق داده نشده‌اند"
    )
    comparison_status = "برای این گزارش، صورت مالی هم‌دوره سال قبل در دفتر گزارش‌ها پیدا نشد"
    growth_candidates = {}
    if prior:
        growth_candidates = {
            key: value for key, value in (
                ("net_profit_yoy_pct", _growth(profit, prior_profit)),
                ("revenue_yoy_pct", _growth(revenue, prior_revenue)),
                ("eps_yoy_pct", _growth(eps, prior_eps)),
            ) if value is not None
        }
        if growth_candidates:
            comparison_status = f"مقایسه مقدماتی {period_label} با دوره مشابه سال قبل از خلاصه‌های عمومی"
        else:
            comparison_status = f"گزارش هم‌دوره سال قبل موجود است، اما مقایسه عددی سازگار برای {period_label} کامل نیست"

    return {
        "score": score if observed >= 1 else None,
        "status": status if observed else "خلاصهٔ صورت مالی شناسایی شده، اما عدد سود/EPS/فروش قابل استخراج نیست",
        "source_confidence_factor": round(quality, 3) if observed else 0.0,
        "source_quality_label": "خلاصهٔ ثانویه؛ اطمینان محدود، برای غربالگری اولیه",
        "latest_report_title": str(report.get("title") or ""),
        "latest_report_period": str(report.get("report_period") or period_label),
        "latest_report_period_months": period_months,
        "latest_report_end_jalali": period_end(report),
        "latest_report_published_at": str(report.get("published_at") or ""),
        "audit_status": str(report.get("audit_status") or ""),
        "is_correction": report.get("is_correction") is True,
        "company": str(report.get("company") or ""),
        "net_profit_million_rial_candidate": profit,
        "eps_rial_candidate": eps,
        "revenue_million_rial_candidate": revenue,
        "net_profit_yoy_pct_candidate": profit_growth,
        "eps_yoy_pct_candidate": eps_growth,
        "revenue_yoy_pct_candidate": revenue_growth,
        "quarterly_growth_candidates_pct": growth_candidates,
        "quarterly_comparison_status": comparison_status,
        "evidence": evidence,
        "official_url": official_url,
        "telegram_url": telegram_url,
        "source_status": "secondary_summary_not_reconciled_to_official_tables",
    }


def _latest_monthly(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        row for row in rows
        if row.get("classification") == "monthly_activity" and _observations(row)
    ]
    candidates.sort(
        key=lambda row: (period_end(row), str(row.get("published_at") or "")),
        reverse=True,
    )
    if not candidates:
        return None
    row = candidates[0]
    sales = [
        {
            "value": item.get("value"),
            "unit": item.get("unit"),
            "period_months": item.get("period_months"),
            "growth_pct_candidate": item.get("growth_pct_candidate"),
            "context": item.get("context"),
        }
        for item in _observations(row)
        if item.get("metric") == "revenue_or_sales"
    ]
    return {
        "report_title": str(row.get("title") or ""),
        "report_period": str(row.get("report_period") or ""),
        "period_end_jalali": period_end(row),
        "published_at": str(row.get("published_at") or ""),
        "company": str(row.get("company") or ""),
        "sales_candidates": sales[:4],
        "official_url": str(row.get("official_url") or ""),
        "telegram_url": str(row.get("telegram_url") or ""),
        "status": "گزارش فعالیت ماهانه؛ فروش برای روند عملیاتی، نه جایگزین صورت مالی",
    }


def build_summary_map(ledger_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index the latest usable statement-summary score and monthly report by ticker."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ledger_rows:
        if not isinstance(row, dict):
            continue
        key = norm(row.get("normalized_symbol") or row.get("symbol"))
        if key:
            grouped[key].append(row)

    result = {}
    for key, rows in grouped.items():
        statements = [
            row for row in rows
            if row.get("classification") == "financial_statement"
            and _observations(row)
            and as_int(row.get("period_months")) in (3, 6, 9, 12)
        ]
        statements.sort(
            key=lambda row: (period_end(row), str(row.get("published_at") or "")),
            reverse=True,
        )
        latest = statements[0] if statements else None
        monthly = _latest_monthly(rows)
        item: dict[str, Any] = {"monthly_activity": monthly}
        if latest is not None:
            prior = _matching_prior(latest, statements)
            item.update(_score(latest, prior))
            item["prior_report_title"] = str(prior.get("title") or "") if prior else ""
            item["prior_report_period_end_jalali"] = period_end(prior) if prior else ""
            item["prior_net_profit_million_rial_candidate"] = _metric_value(prior, "net_profit") if prior else None
            item["prior_eps_rial_candidate"] = _metric_value(prior, "eps") if prior else None
            item["prior_revenue_million_rial_candidate"] = _metric_value(prior, "revenue_or_sales") if prior else None
            item["has_comparable_prior_year_report"] = prior is not None
        else:
            item.update({
                "score": None,
                "status": "پیام گزارش ماهانه/متنی موجود است، اما صورت مالی میان‌دوره‌ای/سالانه دارای عدد قابل‌استخراج نیست",
                "source_confidence_factor": 0.0,
                "source_quality_label": "دادهٔ بنیادی عددی ناکافی",
                "latest_report_title": "",
                "latest_report_period": "",
                "latest_report_period_months": None,
                "latest_report_end_jalali": "",
                "latest_report_published_at": "",
                "audit_status": "",
                "is_correction": False,
                "company": str((monthly or {}).get("company") or ""),
                "net_profit_million_rial_candidate": None,
                "eps_rial_candidate": None,
                "revenue_million_rial_candidate": None,
                "net_profit_yoy_pct_candidate": None,
                "eps_yoy_pct_candidate": None,
                "revenue_yoy_pct_candidate": None,
                "quarterly_growth_candidates_pct": {},
                "quarterly_comparison_status": "صورت مالی عددی قابل‌مقایسه در خلاصه‌های فعلی پیدا نشد",
                "evidence": [],
                "official_url": "",
                "telegram_url": "",
                "source_status": "metadata_or_monthly_summary_only",
                "has_comparable_prior_year_report": False,
            })
        result[key] = item
    return result
