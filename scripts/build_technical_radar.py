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
    جریمهٔ آزمایشی برای شناسایی رشدهای بسیار تند.
    این جریمه به معنی پیش‌بینی قطعی اصلاح قیمت نیست.
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

    daily = read_candles(
        (history.get(DAILY) or {}).get("file")
    )
    weekly = read_candles(
        (history.get(WEEKLY) or {}).get("file")
    )
    monthly = read_candles(
        (history.get(MONTHLY) or {}).get("file")
    )

    daily_closes = [c["close"] for c in daily]
    weekly_closes = [c["close"] for c in weekly]
    monthly_closes = [c["close"] for c in monthly]

    daily_sma50 = sma(daily_closes, 50)
    daily_sma200 = sma(daily_closes, 200)

    weekly_sma13 = sma(weekly_closes, 13)
    weekly_sma26 = sma(weekly_closes, 26)

    monthly_sma6 = sma(monthly_closes, 6)
    monthly_sma12 = sma(monthly_closes, 12)

    rsi_value = rsi(daily_closes)

    daily_20_return = pct_change(daily_closes, 20)
    daily_60_return = pct_change(daily_closes, 60)
    weekly_13_return = pct_change(weekly_closes, 13)
    monthly_6_return = pct_change(monthly_closes, 6)

    daily_close = daily_closes[-1] if daily_closes else None
    weekly_close = weekly_closes[-1] if weekly_closes else None
    monthly_close = monthly_closes[-1] if monthly_closes else None

    def above(left, right):
        if left is None or right is None:
            return None
        return left > right

    checks = {
        "daily_close_above_sma50": above(
            daily_close, daily_sma50
        ),
        "daily_sma50_above_sma200": above(
            daily_sma50, daily_sma200
        ),
        "weekly_close_above_sma13": above(
            weekly_close, weekly_sma13
        ),
        "weekly_sma13_above_sma26": above(
            weekly_sma13, weekly_sma26
        ),
        "monthly_close_above_sma6": above(
            monthly_close, monthly_sma6
        ),
        "monthly_sma6_above_sma12": above(
            monthly_sma6, monthly_sma12
        ),
        "weekly_13_period_return_positive": (
            None if weekly_13_return is None
            else weekly_13_return > 0
        ),
        "monthly_6_period_return_positive": (
            None if monthly_6_return is None
            else monthly_6_return > 0
        ),
    }

    available_weight = sum(
        WEIGHTS[key]
        for key, value in checks.items()
        if value is not None
    )

    earned_weight = sum(
        WEIGHTS[key]
        for key, value in checks.items()
        if value is True
    )

    technical_score = (
        round(earned_weight * 100 / available_weight, 1)
        if available_weight
        else 0.0
    )

    recent_volumes = [
        candle.get("volume")
        for candle in daily[-20:]
        if isinstance(candle.get("volume"), (int, float))
        and candle.get("volume") > 0
    ]

    average_volume20 = (
        sum(recent_volumes) / len(recent_volumes)
        if recent_volumes
        else None
    )

    latest_volume = daily[-1].get("volume") if daily else None

    volume_ratio = (
        latest_volume / average_volume20
        if isinstance(latest_volume, (int, float))
        and average_volume20
        else None
    )

    latest_date = (
        datetime.fromtimestamp(
            daily[-1]["time"],
            timezone.utc,
        ).date()
        if daily
        else None
    )

    today_utc = datetime.now(timezone.utc).date()

    data_age = (
        (today_utc - latest_date).days
        if latest_date is not None
        else None
    )

    history_mature = (
        len(daily) >= 200
        and len(weekly) >= 26
        and len(monthly) >= 12
    )

    eligible = (
        history_mature
        and data_age is not None
        and 0 <= data_age <= 10
    )

    penalty, flags, heat_label, entry_timing = assess_heat(
        rsi_value,
        daily_20_return,
        daily_60_return,
        monthly_6_return,
        volume_ratio,
    )

    risk_adjusted_score = round(
        max(0.0, technical_score - penalty),
        1,
    )

    if technical_score >= 75:
        label = "روند
