"""Helpers for validating and scoring same-period public Fundai ratio history.

These series are secondary-source financial-ratio observations, not audited
financial statements. A trend score is emitted only from matched same-duration,
same Jalali month/day year-over-year pairs; missing comparisons stay unavailable.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any


PROFITABILITY_FIELDS = {
    "roe_ttm": 0.10,
    "roa_ttm": 0.05,
    "net_profit_margin_ttm": 0.10,
    "gross_profit_margin_ttm": 0.10,
}
LEVERAGE_FIELDS = {
    "debt_to_assets": 0.10,
    "debt_to_equity": 0.50,
}
ALL_TREND_FIELDS = {**PROFITABILITY_FIELDS, **LEVERAGE_FIELDS}


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip()
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    text = text.replace("\u200c", "").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"[\s\-_.]+", "", text).casefold()


def finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def parse_jalali_date(value: Any) -> tuple[int, int, int] | None:
    match = re.match(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\D|$)", str(value or ""))
    if not match:
        translated = str(value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
        match = re.match(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\D|$)", translated)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    if not (1300 <= year <= 1499 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date | None:
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
    month_lengths = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > month_lengths[gm - 1]:
        gd -= month_lengths[gm - 1]
        gm += 1
    try:
        return date(gy, gm, gd)
    except ValueError:
        return None


def jalali_date_age_days(value: Any, today: date | None = None) -> int | None:
    parts = parse_jalali_date(value)
    if not parts:
        return None
    gregorian = jalali_to_gregorian(*parts)
    if gregorian is None:
        return None
    age = ((today or datetime.now(timezone.utc).date()) - gregorian).days
    return age if age >= 0 else None


def normalize_history_payload(
    payload: Any,
    *,
    company_slug: str,
    symbol: str,
    industry: str = "",
    tsetmc_id: str = "",
    fetched_at_utc: str = "",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Fundai history payload must be an object.")
    returned_slug = str(payload.get("company_slug") or "").strip()
    if returned_slug and returned_slug != company_slug:
        raise ValueError(f"Fundai history slug mismatch: expected {company_slug!r}, got {returned_slug!r}.")
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Fundai history endpoint returned no valid result rows.")
    normalized_rows = []
    seen = set()
    for raw in rows:
        parsed = parse_jalali_date(raw.get("report_date"))
        if not parsed:
            continue
        duration_code = str(raw.get("duration_code") or "").strip()
        if not duration_code:
            continue
        identity = (parsed, duration_code)
        if identity in seen:
            continue
        seen.add(identity)
        entry = dict(raw)
        entry["report_date"] = f"{parsed[0]:04d}-{parsed[1]:02d}-{parsed[2]:02d}"
        normalized_rows.append(entry)
    normalized_rows.sort(
        key=lambda row: (str(row.get("report_date") or ""), str(row.get("duration_code") or "")),
        reverse=True,
    )
    if not normalized_rows:
        raise ValueError("Fundai history contained no rows with a valid Jalali date and duration code.")
    return {
        "symbol": symbol,
        "company_slug": company_slug,
        "industry": industry,
        "tsetmc_id": str(tsetmc_id or ""),
        "duration": payload.get("duration"),
        "durations": payload.get("durations") if isinstance(payload.get("durations"), list) else [],
        "history_rows": normalized_rows,
        "history_row_count": len(normalized_rows),
        "first_report_date": min(row["report_date"] for row in normalized_rows),
        "latest_report_date": max(row["report_date"] for row in normalized_rows),
        "fetched_at_utc": fetched_at_utc,
    }


def _metric_score(metric: str, delta: float) -> float:
    scale = ALL_TREND_FIELDS[metric]
    # Rising profitability is positive; falling leverage is positive.
    direction = -1.0 if metric in LEVERAGE_FIELDS else 1.0
    return max(0.0, min(100.0, 50.0 + 50.0 * math.tanh(direction * delta / scale)))


def build_history_trend_scores(
    snapshot: dict[str, Any],
    *,
    max_current_age_days: int = 450,
    today: date | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a 0–100 directional trend proxy from matched same-period annual pairs.

    Only exact duration-code, Jalali month/day and prior-year matches are compared.
    At least three metric comparisons are required. Valuation multiples are omitted:
    a lower multiple alone is not necessarily better and may reflect distress.
    """
    histories = snapshot.get("histories", []) if isinstance(snapshot, dict) else []
    if not isinstance(histories, list):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for stock in histories:
        if not isinstance(stock, dict):
            continue
        symbol = str(stock.get("symbol") or "").strip()
        key = normalize_symbol(symbol)
        rows = stock.get("history_rows")
        if not key or not isinstance(rows, list):
            continue
        by_duration: dict[str, dict[tuple[int, int, int], dict[str, Any]]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            parts = parse_jalali_date(row.get("report_date"))
            duration = str(row.get("duration_code") or "").strip()
            if not parts or not duration:
                continue
            by_duration.setdefault(duration, {})[parts] = row

        measurements: list[dict[str, Any]] = []
        matched_pairs = []
        latest_dates = []
        for duration, period_rows in by_duration.items():
            candidates = []
            for (year, month, day), current in period_rows.items():
                age = jalali_date_age_days(current.get("report_date"), today=today)
                if age is None or age > max_current_age_days:
                    continue
                prior = period_rows.get((year - 1, month, day))
                if prior is None:
                    continue
                current_date = f"{year:04d}-{month:02d}-{day:02d}"
                candidates.append((current_date, age, current, prior))
            if not candidates:
                continue
            current_date, age, current, prior = max(candidates, key=lambda x: x[0])
            pair_metrics = []
            for metric in ALL_TREND_FIELDS:
                cur_value, prev_value = current.get(metric), prior.get(metric)
                if not finite_number(cur_value) or not finite_number(prev_value):
                    continue
                cur_value, prev_value = float(cur_value), float(prev_value)
                if metric == "debt_to_assets" and not (0 <= cur_value <= 3 and 0 <= prev_value <= 3):
                    continue
                if metric == "debt_to_equity" and not (0 <= cur_value <= 200 and 0 <= prev_value <= 200):
                    continue
                delta = cur_value - prev_value
                if abs(delta) < 1e-12:
                    score = 50.0
                else:
                    score = _metric_score(metric, delta)
                record = {
                    "metric": metric,
                    "current_value": cur_value,
                    "prior_year_value": prev_value,
                    "delta": round(delta, 6),
                    "directional_score": round(score, 1),
                }
                pair_metrics.append(record)
                measurements.append({**record, "duration_code": duration, "report_date": current_date, "prior_report_date": prior.get("report_date")})
            if pair_metrics:
                matched_pairs.append({
                    "duration_code": duration,
                    "report_date": current_date,
                    "prior_report_date": str(prior.get("report_date") or ""),
                    "current_age_days": age,
                    "metric_count": len(pair_metrics),
                })
                latest_dates.append((current_date, age))

        metric_count = len(measurements)
        if metric_count < 3 or not matched_pairs:
            output[key] = {
                "symbol": symbol,
                "company_slug": str(stock.get("company_slug") or ""),
                "industry": str(stock.get("industry") or ""),
                "score": None,
                "status": "insufficient_same_period_history",
                "matched_period_pair_count": len(matched_pairs),
                "metric_comparison_count": metric_count,
                "latest_comparison_date": max((x[0] for x in latest_dates), default=""),
                "source": "Fundai public historical ratios; secondary-source trend proxy",
            }
            continue

        # Avoid over-weighting any one issuer-period if the API repeats durations.
        # Equal weighting across observed, correctly matched metric comparisons is transparent.
        trend_score = round(sum(item["directional_score"] for item in measurements) / metric_count, 1)
        latest_comparison_date = max((x[0] for x in latest_dates), default="")
        newest_age = min((x[1] for x in latest_dates), default=None)
        recency_factor = 1.0 if newest_age is not None and newest_age <= 210 else (
            0.8 if newest_age is not None and newest_age <= 300 else 0.55
        )
        pair_factor = min(1.0, len(matched_pairs) / 3.0)
        metric_factor = min(1.0, metric_count / 12.0)
        quality = round(recency_factor * pair_factor * metric_factor, 3)
        output[key] = {
            "symbol": symbol,
            "company_slug": str(stock.get("company_slug") or ""),
            "industry": str(stock.get("industry") or ""),
            "score": trend_score,
            "status": "available_same_duration_same_period_yoy_candidate",
            "matched_period_pair_count": len(matched_pairs),
            "metric_comparison_count": metric_count,
            "latest_comparison_date": latest_comparison_date,
            "latest_comparison_age_days": newest_age,
            "quality": quality,
            "comparisons": measurements[:24],
            "matched_periods": matched_pairs[:12],
            "source": "Fundai public historical ratios; secondary-source trend proxy",
        }
    return output
