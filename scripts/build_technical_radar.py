import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_FILE = ROOT / "data" / "stock_universe.json"
OUTPUT_FILE = ROOT / "data" / "technical_radar.json"

DAILY = "1D"
WEEKLY = "W"
MONTHLY = "M"

# امتیازها فقط برای بررسی مقدماتی تکنیکال‌اند.
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

    window = values[-period:]
    return sum(window) / period


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


def trend_label(score, eligible, stale):
    if stale:
        return "داده قدیمی"
    if not eligible:
        return "تاریخچه ناکافی"
    if score >= 75:
        return "روند تکنیکال قوی"
    if score >= 60:
        return "روند نسبتاً مثبت"
    if score >= 40:
        return "روند مختلط"
    return "روند تکنیکال ضعیف"


def analyze_stock(stock):
    history = stock.get("history") or {}

    daily = read_candles(history.get(DAILY, {}).get("file", ""))
    weekly = read_candles(history.get(WEEKLY, {}).get("file", ""))
    monthly = read_candles(history.get(MONTHLY, {}).get("file", ""))

    d = [item["close"] for item in daily]
    w = [item["close"] for item in weekly]
    m = [item["close"] for item in monthly]

    daily_last = daily[-1] if daily else {}
    weekly_last = weekly[-1] if weekly else {}
    monthly_last = monthly[-1] if monthly else {}

    daily_sma50 = sma(d, 50)
    daily_sma200 = sma(d, 200)

    weekly_sma13 = sma(w, 13)
    weekly_sma26 = sma(w, 26)

    monthly_sma6 = sma(m, 6)
    monthly_sma12 = sma(m, 12)

    weekly_return13 = pct_change(w, 13)
    monthly_return6 = pct_change(m, 6)
    daily_return20 = pct_change(d, 20)
    daily_return60 = pct_change(d, 60)

    current_rsi = rsi(d, 14)

    checks = {}
    earned_points = 0
    available_points = 0

    def add_check(name, condition):
        nonlocal earned_points, available_points

        weight = WEIGHTS[name]
        if condition is None:
            checks[name] = None
            return

        checks[name] = bool(condition)
        available_points += weight

        if condition:
            earned_points += weight

    add_check(
        "daily_close_above_sma50",
        d[-1] > daily_sma50
        if d and daily_sma50 is not None else None,
    )
    add_check(
        "daily_sma50_above_sma200",
        daily_sma50 > daily_sma200
        if daily_sma50 is not None and daily_sma200 is not None
        else None,
    )
    add_check(
        "weekly_close_above_sma13",
        w[-1] > weekly_sma13
        if w and weekly_sma13 is not None else None,
    )
    add_check(
        "weekly_sma13_above_sma26",
        weekly_sma13 > weekly_sma26
        if weekly_sma13 is not None and weekly_sma26 is not None
        else None,
    )
    add_check(
        "monthly_close_above_sma6",
        m[-1] > monthly_sma6
        if m and monthly_sma6 is not None else None,
    )
    add_check(
        "monthly_sma6_above_sma12",
        monthly_sma6 > monthly_sma12
        if monthly_sma6 is not None and monthly_sma12 is not None
        else None,
    )
    add_check(
        "weekly_13_period_return_positive",
        weekly_return13 > 0
        if weekly_return13 is not None else None,
    )
    add_check(
        "monthly_6_period_return_positive",
        monthly_return6 > 0
        if monthly_return6 is not None else None,
    )

    score = (
        round(earned_points / available_points * 100, 2)
        if available_points else None
    )

    # تاریخچهٔ کافی برای ارزیابی افق بلندمدت
    mature = (
        len(d) >= 200
        and len(w) >= 26
        and len(m) >= 12
    )

    latest_date = daily_last.get("date")
    days_old = None

    if latest_date:
        try:
            last_day = datetime.strptime(
                latest_date, "%Y-%m-%d"
            ).date()
            days_old = (
                datetime.now(timezone.utc).date() - last_day
            ).days
        except ValueError:
            pass

    stale = days_old is not None and days_old > 15
    eligible = mature and not stale and score is not None

    volume = daily_last.get("volume")
    recent_volumes = [
        item["volume"]
        for item in daily[-21:-1]
        if isinstance(item.get("volume"), (int, float))
        and item["volume"] > 0
    ]

    average_volume20 = (
        sum(recent_volumes) / len(recent_volumes)
        if recent_volumes else None
    )

    volume_ratio = None
    if (
        isinstance(volume, (int, float))
        and volume > 0
        and average_volume20
    ):
        volume_ratio = round(volume / average_volume20, 3)

    if current_rsi is None:
        rsi_state = "داده ناکافی"
    elif current_rsi >= 70:
        rsi_state = "بالا؛ احتمال داغ‌شدن مومنتوم"
    elif current_rsi <= 30:
        rsi_state = "پایین؛ نیازمند بررسی"
    else:
        rsi_state = "میانه"

    return {
        "symbol": stock.get("symbol"),
        "company": stock.get("company"),
        "exchange": stock.get("exchange"),
        "chart_id": stock.get("chart_id"),
        "technical_score": score,
        "available_score_weight": available_points,
        "label": trend_label(score or 0, eligible, stale),
        "eligible_for_long_term_ranking": eligible,
        "history_mature": mature,
        "latest_daily_date": latest_date,
        "data_age_days": days_old,
        "history_bars": {
            "daily": len(d),
            "weekly": len(w),
            "monthly": len(m),
        },
        "price": {
            "daily_close": d[-1] if d else None,
            "weekly_close": w[-1] if w else None,
            "monthly_close": m[-1] if m else None,
        },
        "moving_averages": {
            "daily_sma50": daily_sma50,
            "daily_sma200": daily_sma200,
            "weekly_sma13": weekly_sma13,
            "weekly_sma26": weekly_sma26,
            "monthly_sma6": monthly_sma6,
            "monthly_sma12": monthly_sma12,
        },
        "momentum_pct": {
            "daily_20_period": daily_return20,
            "daily_60_period": daily_return60,
            "weekly_13_period": weekly_return13,
            "monthly_6_period": monthly_return6,
        },
        "rsi14_daily": round(current_rsi, 2)
        if current_rsi is not None else None,
        "rsi_state": rsi_state,
        "latest_daily_volume": volume,
        "average_volume20": round(average_volume20, 2)
        if average_volume20 is not None else None,
        "volume_vs_20_period_average": volume_ratio,
        "checks": checks,
    }


def main():
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(
            "Run Build Valid Stock Universe first."
        )

    universe = read_json(UNIVERSE_FILE)
    source_stocks = universe.get("stocks", [])

    results = []
    errors = []

    print("Validated universe:", len(source_stocks))

    for index, stock in enumerate(source_stocks, start=1):
        try:
            result = analyze_stock(stock)
            results.append(result)

            if index % 50 == 0 or index == len(source_stocks):
                print(f"Analyzed {index}/{len(source_stocks)}")
        except Exception as error:
            errors.append({
                "symbol": stock.get("symbol"),
                "error": repr(error),
            })

    eligible = [
        stock for stock in results
        if stock["eligible_for_long_term_ranking"]
    ]

    eligible.sort(
        key=lambda stock: (
            stock["technical_score"] if stock["technical_score"] is not None else -1,
            stock["momentum_pct"]["monthly_6_period"]
            if stock["momentum_pct"]["monthly_6_period"] is not None
            else -999,
        ),
        reverse=True,
    )

    ineligible = [
        stock for stock in results
        if not stock["eligible_for_long_term_ranking"]
    ]
    ineligible.sort(
        key=lambda stock: (
            stock["technical_score"]
            if stock["technical_score"] is not None else -1
        ),
        reverse=True,
    )

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Nahayatnegar historical candles",
        "input_universe_count": len(source_stocks),
        "analyzed_count": len(results),
        "eligible_for_long_term_ranking": len(eligible),
        "insufficient_or_stale_history": len(ineligible),
        "analysis_errors": len(errors),
        "method": {
            "daily": "SMA50/SMA200, 20/60-period returns, RSI14",
            "weekly": "SMA13/SMA26, 13-period return",
            "monthly": "SMA6/SMA12, 6-period return",
            "score": "Weighted technical rules, normalized for available checks",
            "eligibility": "At least 200 daily, 26 weekly and 12 monthly candles, with recent daily data",
        },
        "warning": (
            "Technical screening only; not a buy/sell recommendation. "
            "Scores are heuristic and have not yet been validated through "
            "historical strategy testing. Full TSE/IFB market coverage "
            "has not been independently verified."
        ),
        "top_50_for_review": eligible[:50],
        "all_eligible": eligible,
        "not_yet_ranked": ineligible,
        "errors": errors,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("\n===== TECHNICAL RADAR SUMMARY =====")
    print("Input stocks:", len(source_stocks))
    print("Analyzed:", len(results))
    print("Eligible:", len(eligible))
    print("Insufficient/stale:", len(ineligible))
    print("Analysis errors:", len(errors))
    print("Top candidates:", min(50, len(eligible)))
    print("Output:", OUTPUT_FILE.relative_to(ROOT))


if __name__ == "__main__":
    main()
