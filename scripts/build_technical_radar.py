import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_FILE = ROOT / "data" / "stock_universe.json"
OUTPUT_FILE = ROOT / "data" / "technical_radar.json"

DAILY = "1D"
WEEKLY = "W"
MONTHLY = "M"

WEIGHTS = {
    "daily_close_above_sma50": 10,
    "daily_sma50_above_sma200": 10,
    "weekly_close_above_sma13": 15,
    "weekly_sma13_above_sma26": 20,
    "monthly_close_above_sma6": 15,
    "monthly_sma6_above_sma12": 20,
    "weekly_13_period_return_positive": 5,
    "monthly_6_period_return_positive": 5,
}


def read_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def read_candles(relative_path):
    if not relative_path:
        return []

    path = ROOT / relative_path

    if not path.is_file():
        return []

    candles_by_time = {}

    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue

                try:
                    candle = json.loads(line)
                    timestamp = int(candle["time"])
                    close = float(candle["close"])

                    if close <= 0:
                        continue

                    candle["time"] = timestamp
                    candle["close"] = close

                    for key in ("open", "high", "low", "volume"):
                        value = candle.get(key)

                        if value is not None:
                            try:
                                candle[key] = float(value)
                            except (ValueError, TypeError):
                                candle[key] = None

                    candles_by_time[timestamp] = candle

                except (KeyError, ValueError, TypeError):
                    continue

    except OSError:
        return []

    return [
        candles_by_time[t]
        for t in sorted(candles_by_time)
    ]


def sma(values, period):
    if len(values) < period:
        return None

    return sum(values[-period:]) / period


def pct_change(values, periods):
    if len(values) <= periods:
        return None

    base = values[-periods - 1]

    if base == 0:
        return None

    return (values[-1] / base - 1) * 100


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(len(values) - period, len(values))
    ]

    gains = [max(value, 0) for value in changes]
    losses = [max(-value, 0) for value in changes]

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0

    relative_strength = average_gain / average_loss

    return 100 - (100 / (1 + relative_strength))


def assess_heat(
    rsi_value,
    daily_20,
    daily_60,
    monthly_6,
    volume_ratio,
):
    """
    جریمهٔ مقدماتی برای شناسایی جهش‌های تند قیمت.
    این آستانه‌ها قواعد آزمایشی‌اند، نه قانون قطعی بازار.
    """
    penalty = 0
    flags = []

    def apply_tier(value, levels, flag):
        nonlocal penalty

        if value is None:
            return

        for threshold, points in levels:
            if value >= threshold:
                penalty += points
                flags.append(flag)
                return

    apply_tier(
        rsi_value,
        [(90, 18), (80, 12), (70, 5)],
        "rsi_high",
    )

    apply_tier(
        daily_20,
        [(100, 12), (50, 8), (25, 4)],
        "daily_surge",
    )

    apply_tier(
        daily_60,
        [(300, 10), (150, 6), (75, 3)],
        "medium_term_surge",
    )

    apply_tier(
        monthly_6,
        [(400, 10), (200, 6), (100, 3)],
        "long_term_surge",
    )

    if volume_ratio is not None and volume_ratio >= 4:
        penalty += 2
        flags.append("unusual_volume")

    penalty = min(penalty, 40)

    if penalty >= 25:
        heat_label = "ریسک داغ‌شدن بالا"
    elif penalty >= 12:
        heat_label = "ریسک داغ‌شدن متوسط"
    else:
        heat_label = "ریسک داغ‌شدن پایین"

    if (
        (rsi_value is not None and rsi_value >= 85)
        or (daily_20 is not None and daily_20 >= 60)
        or (
            monthly_6 is not None
            and monthly_6 >= 250
            and daily_20 is not None
            and daily_20 >= 20
        )
    ):
        entry_timing = "انتظار برای اصلاح یا تثبیت"
    elif (
        (rsi_value is not None and rsi_value >= 70)
        or penalty >= 10
    ):
        entry_timing = "فقط پس از تأیید مجدد"
    else:
        entry_timing = "قابل بررسی پس از تحلیل بنیادی"

    return penalty, flags, heat_label, entry_timing
    def analyze_stock(stock):
    history = stock.get("history", {})
    daily = read_candles((history.get(DAILY) or {}).get("file"))
    weekly = read_candles((history.get(WEEKLY) or {}).get("file"))
    monthly = read_candles((history.get(MONTHLY) or {}).get("file"))

    daily_closes = [c["close"] for c in daily]
    weekly_closes = [c["close"] for c in weekly]
    monthly_closes = [c["close"] for c in monthly]

    d50, d200 = sma(daily_closes, 50), sma(daily_closes, 200)
    w13, w26 = sma(weekly_closes, 13), sma(weekly_closes, 26)
    m6, m12 = sma(monthly_closes, 6), sma(monthly_closes, 12)

    rsi_value = rsi(daily_closes)
    d20_return = pct_change(daily_closes, 20)
    d60_return = pct_change(daily_closes, 60)
    w13_return = pct_change(weekly_closes, 13)
    m6_return = pct_change(monthly_closes, 6)

    last_close = daily_closes[-1] if daily_closes else None

    def above(left, right):
        return None if left is None or right is None else left > right

    checks = {
        "daily_close_above_sma50": above(last_close, d50),
        "daily_sma50_above_sma200": above(d50, d200),
        "weekly_close_above_sma13": above(
            weekly_closes[-1] if weekly_closes else None, w13
        ),
        "weekly_sma13_above_sma26": above(w13, w26),
        "monthly_close_above_sma6": above(
            monthly_closes[-1] if monthly_closes else None, m6
        ),
        "monthly_sma6_above_sma12": above(m6, m12),
        "weekly_13_period_return_positive":
            None if w13_return is None else w13_return > 0,
        "monthly_6_period_return_positive":
            None if m6_return is None else m6_return > 0,
    }

    available_weight = sum(
        WEIGHTS[key] for key, value in checks.items()
        if value is not None
    )
    earned_weight = sum(
        WEIGHTS[key] for key, value in checks.items()
        if value is True
    )
    score = round(earned_weight * 100 / available_weight, 1) \
        if available_weight else 0.0

    volume_values = [
        c.get("volume") for c in daily[-20:]
        if isinstance(c.get("volume"), (int, float))
        and c.get("volume") > 0
    ]
    average_volume20 = (
        sum(volume_values) / len(volume_values)
        if volume_values else None
    )
    latest_volume = daily[-1].get("volume") if daily else None
    volume_ratio = (
        latest_volume / average_volume20
        if isinstance(latest_volume, (int, float))
        and average_volume20 else None
    )

    latest_date = (
        datetime.fromtimestamp(
            daily[-1]["time"], timezone.utc
        ).date()
        if daily else None
    )
    data_age = (
        (datetime.now(timezone.utc).date() - latest_date).days
        if latest_date else None
    )

    mature = (
        len(daily) >= 200
        and len(weekly) >= 26
        and len(monthly) >= 12
    )
    eligible = (
        mature
        and data_age is not None
        and 0 <= data_age <= 10
    )

    penalty, flags, heat_label, entry_timing = assess_heat(
        rsi_value, d20_return, d60_return, m6_return, volume_ratio
    )
    adjusted_score = round(max(0.0, score - penalty), 1)

    label = (
        "روند تکنیکال قوی" if score >= 75
        else "روند تکنیکال متوسط" if score >= 55
        else "روند تکنیکال ضعیف"
    )
    rsi_state = (
        "بالا؛ احتمال داغ‌شدن مومنتوم"
        if rsi_value is not None and rsi_value >= 70
        else "عادی یا پایین‌تر" if rsi_value is not None
        else "داده ناکافی"
    )

    return {
        "symbol": stock.get("symbol", ""),
        "company": stock.get("company", ""),
        "exchange": stock.get("exchange", ""),
        "chart_id": stock.get("chart_id", ""),
        "technical_score": score,
        "risk_penalty": penalty,
        "risk_adjusted_score": adjusted_score,
        "available_score_weight": available_weight,
        "label": label,
        "eligible_for_long_term_ranking": eligible,
        "history_mature": mature,
        "latest_daily_date": latest_date.isoformat() if latest_date else None,
        "data_age_days": data_age,
        "history_bars": {
            "daily": len(daily),
            "weekly": len(weekly),
            "monthly": len(monthly),
        },
        "price": {
            "daily_close": last_close,
            "weekly_close": weekly_closes[-1] if weekly_closes else None,
            "monthly_close": monthly_closes[-1] if monthly_closes else None,
        },
        "moving_averages": {
            "daily_sma50": d50,
            "daily_sma200": d200,
            "weekly_sma13": w13,
            "weekly_sma26": w26,
            "monthly_sma6": m6,
            "monthly_sma12": m12,
        },
        "momentum_pct": {
            "daily_20_period": d20_return,
            "daily_60_period": d60_return,
            "weekly_13_period": w13_return,
            "monthly_6_period": m6_return,
        },
        "rsi14_daily": round(rsi_value, 2) if rsi_value is not None else None,
        "rsi_state": rsi_state,
        "heat_risk": heat_label,
        "risk_flags": flags,
        "entry_timing": entry_timing,
        "latest_daily_volume": latest_volume,
        "average_volume20": (
            round(average_volume20, 2)
            if average_volume20 is not None else None
        ),
        "volume_vs_20_period_average": (
            round(volume_ratio, 3)
            if volume_ratio is not None else None
        ),
        "checks": checks,
    }


def main():
    universe = read_json(UNIVERSE_FILE)
    stocks = universe.get("stocks", [])
    results = []
    errors = 0

    print(f"Input stocks: {len(stocks)}")

    for index, stock in enumerate(stocks, start=1):
        try:
            results.append(analyze_stock(stock))
        except Exception as exc:
            errors += 1
            print(
                f"Analysis error for {stock.get('symbol', '?')}: {exc}"
            )

        if index % 50 == 0 or index == len(stocks):
            print(f"Analyzed {index}/{len(stocks)}")

    eligible = [
        item for item in results
        if item["eligible_for_long_term_ranking"]
    ]
    insufficient = len(results) - len(eligible)

    eligible.sort(
        key=lambda item: (
            item["risk_adjusted_score"],
            item["technical_score"],
        ),
        reverse=True,
    )

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Nahayatnegar historical candles",
        "input_universe_count": len(stocks),
        "analyzed_count": len(results),
        "eligible_for_long_term_ranking": len(eligible),
        "insufficient_or_stale_history": insufficient,
        "analysis_errors": errors,
        "method": {
            "daily": "SMA50/SMA200, 20/60-period returns, RSI14",
            "weekly": "SMA13/SMA26, 13-period return",
            "monthly": "SMA6/SMA12, 6-period return",
            "score": "Weighted technical rules, normalized for available checks",
            "risk_adjustment": (
                "Subtracts a heuristic heat penalty from the technical "
                "score; ranks by adjusted score first"
            ),
            "eligibility": (
                "At least 200 daily, 26 weekly and 12 monthly candles, "
                "with daily data no more than 10 days old"
            ),
        },
        "warning": (
            "Technical screening only; not a buy/sell recommendation. "
            "Scores and heat penalties are heuristic and not yet "
            "validated by historical strategy testing. Full TSE/IFB "
            "market coverage has not been independently verified."
        ),
        "top_50_for_review": eligible[:50],
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print("===== TECHNICAL RADAR SUMMARY =====")
    print(f"Input stocks: {len(stocks)}")
    print(f"Analyzed: {len(results)}")
    print(f"Eligible: {len(eligible)}")
    print(f"Insufficient/stale: {insufficient}")
    print(f"Analysis errors: {errors}")
    print(f"Top candidates: {min(50, len(eligible))}")
    print(f"Output: {OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
