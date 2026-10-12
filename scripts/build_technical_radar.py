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
