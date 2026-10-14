import json
import math
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

    return [candles_by_time[t] for t in sorted(candles_by_time)]


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


def assess_heat(rsi_value, daily_20, daily_60, monthly_6, volume_ratio):
    """Experimental penalty for unusually strong recent price moves."""
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

    apply_tier(rsi_value, [(90, 18), (80, 12), (70, 5)], "rsi_high")
    apply_tier(daily_20, [(100, 12), (50, 8), (25, 4)], "daily_surge")
    apply_tier(daily_60, [(300, 10), (150, 6), (75, 3)], "medium_term_surge")
    apply_tier(monthly_6, [(400, 10), (200, 6), (100, 3)], "long_term_surge")

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
    elif (rsi_value is not None and rsi_value >= 70) or penalty >= 10:
        entry_timing = "فقط پس از تأیید مجدد"
    else:
        entry_timing = "قابل بررسی پس از تحلیل بنیادی"

    return penalty, flags, heat_label, entry_timing



def calculate_technical_targets(candles, data_age_days):
    """Emit conservative daily swing scenarios only from fresh, valid OHLC history.

    Targets are confirmed historical pivot zones, not price predictions. A side
    is withheld if its stop/target geometry cannot meet the minimum reward/risk.
    """
    blocked = {
        "status": "unavailable",
        "reason": "دادهٔ کافی/تازه برای تارگت معتبر نیست",
        "source": "Nahayatnegar historical daily OHLC",
        "latest_price_date": None,
        "data_age_days": None,
        "current_price": None,
        "atr14": None,
        "support_levels": [],
        "resistance_levels": [],
        "buy": None,
        "sell": None,
        "method": "confirmed 3-bar swing zones + ATR14; TP1 minimum reward/risk 1.3; daily close reference",
    }
    if (
        isinstance(data_age_days, bool)
        or not isinstance(data_age_days, (int, float))
        or not (0 <= data_age_days <= 5)
    ):
        blocked["reason"] = "سن دادهٔ ورودی نامعتبر است یا از ۵ روز بیشتر است"
        return blocked

    # De-duplicate timestamps and reject invalid/non-finite OHLC rows.
    candles_by_time = {}
    for candle in candles:
        try:
            timestamp = int(candle["time"])
            o, h, l, close = (
                float(candle[key]) for key in ("open", "high", "low", "close")
            )
            if timestamp <= 0 or not all(math.isfinite(v) for v in (o, h, l, close)):
                continue
            if min(o, h, l, close) <= 0:
                continue
            if h < max(o, close, l) or l > min(o, close, h):
                continue
            # Invalid extreme timestamps are not allowed to poison latest-date checks.
            datetime.fromtimestamp(timestamp, timezone.utc)
            candles_by_time[timestamp] = {
                "time": timestamp, "open": o, "high": h, "low": l, "close": close
            }
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue

    valid = [candles_by_time[t] for t in sorted(candles_by_time)]
    if len(valid) < 120:
        blocked["reason"] = "کمتر از ۱۲۰ کندل روزانهٔ معتبر"
        return blocked

    latest = valid[-1]
    latest_date = datetime.fromtimestamp(latest["time"], timezone.utc).date()
    actual_age = (datetime.now(timezone.utc).date() - latest_date).days
    if actual_age < 0 or actual_age > 5:
        blocked["reason"] = "آخرین کندل معتبر بیش از ۵ روز قدیمی یا دارای تاریخ آینده است"
        return blocked

    valid = valid[-250:]
    latest = valid[-1]
    close = latest["close"]
    true_ranges = []
    for i, candle in enumerate(valid):
        previous_close = valid[i - 1]["close"] if i else candle["close"]
        true_ranges.append(max(
            candle["high"] - candle["low"],
            abs(candle["high"] - previous_close),
            abs(candle["low"] - previous_close),
        ))
    atr = sum(true_ranges[-14:]) / 14
    if not math.isfinite(atr) or atr <= 0:
        blocked["reason"] = "محاسبهٔ ATR معتبر نیست"
        return blocked

    # A pivot is only usable after three bars have confirmed it on either side.
    window = valid[-120:]
    lows, highs = [], []
    for i in range(3, len(window) - 3):
        candle = window[i]
        left_right = window[i - 3:i] + window[i + 1:i + 4]
        if candle["low"] <= min(x["low"] for x in left_right) and candle["low"] < close:
            lows.append(candle["low"])
        if candle["high"] >= max(x["high"] for x in left_right) and candle["high"] > close:
            highs.append(candle["high"])

    def cluster(levels):
        # Merge nearby pivots into zones; sort first for deterministic clustering.
        zones = []
        tolerance = max(0.35 * atr, close * 0.002)
        for value in sorted(levels):
            if zones and abs(value - sum(zones[-1]) / len(zones[-1])) <= tolerance:
                zones[-1].append(value)
            else:
                zones.append([value])
        return [sum(zone) / len(zone) for zone in zones]

    # Only local levels are eligible for targets. Older prices beyond five ATR
    # can be distorted by corporate actions or regime changes and are not useful
    # as an immediate swing objective.
    max_target_distance = 5.0 * atr
    supports = sorted((v for v in cluster(lows) if 0 < close - v <= max_target_distance), reverse=True)
    resistances = sorted(v for v in cluster(highs) if 0 < v - close <= max_target_distance)
    minimum_risk = max(0.75 * atr, close * 0.002)
    maximum_risk = 3.0 * atr
    min_reward_multiple = 1.3

    def setup(direction):
        if direction == "buy":
            # Prefer the nearest structurally valid support, but avoid a stop that
            # is implausibly tight; only fall back to 1.5 ATR if no zone qualifies.
            stop = next((
                level - 0.15 * atr for level in supports
                if minimum_risk <= close - (level - 0.15 * atr) <= maximum_risk
            ), close - 1.5 * atr)
            risk = close - stop
            # Do not skip the nearest barrier to force a better-looking R/R.
            target1 = resistances[0] if resistances else None
            if risk <= 0 or target1 is None:
                return None
            reward = target1 - close
            if reward < min_reward_multiple * risk or reward > max_target_distance:
                return None
            target2 = next((
                level for level in resistances if target1 + 0.25 * atr <= level - close <= max_target_distance
            ), None)
            return {
                "entry_reference": round(close, 4),
                "target_1": round(target1, 4),
                "target_2": round(target2, 4) if target2 is not None else None,
                "stop_loss": round(stop, 4),
                "invalidation": round(stop, 4),
                "risk_reward_tp1": round((target1 - close) / risk, 2),
                "risk_reward_tp2": round((target2 - close) / risk, 2) if target2 is not None else None,
                "status": "scenario_only; confirm entry separately",
            }

        stop = next((
            level + 0.15 * atr for level in resistances
            if minimum_risk <= (level + 0.15 * atr) - close <= maximum_risk
        ), close + 1.5 * atr)
        risk = stop - close
        # Do not jump past a nearby support just because it offers poor R/R.
        target1 = supports[0] if supports else None
        if risk <= 0 or target1 is None:
            return None
        reward = close - target1
        if reward < min_reward_multiple * risk or reward > max_target_distance:
            return None
        target2 = next((
            level for level in supports if max_target_distance >= close - level >= close - target1 + 0.25 * atr
        ), None)
        return {
            "entry_reference": round(close, 4),
            "target_1": round(target1, 4),
            "target_2": round(target2, 4) if target2 is not None else None,
            "stop_loss": round(stop, 4),
            "invalidation": round(stop, 4),
            "risk_reward_tp1": round((close - target1) / risk, 2),
            "risk_reward_tp2": round((close - target2) / risk, 2) if target2 is not None else None,
            "status": "scenario_only; confirm entry separately",
        }

    if not supports and not resistances:
        blocked["reason"] = "نقاط نوسانی کافی برای سطوح معتبر یافت نشد"
        return blocked

    return {
        "status": "available",
        "reason": "",
        "source": "Nahayatnegar historical daily OHLC",
        "latest_price_date": latest_date.isoformat(),
        "data_age_days": actual_age,
        "current_price": round(close, 4),
        "atr14": round(atr, 4),
        "support_levels": [round(value, 4) for value in supports[:3]],
        "resistance_levels": [round(value, 4) for value in resistances[:3]],
        "buy": setup("buy"),
        "sell": setup("sell"),
        "method": "confirmed 3-bar swing zones + ATR14; stop risk 0.75–3.0 ATR; TP1 minimum reward/risk 1.3; daily close reference",
    }



def valid_ohlc_rows(candles):
    """Return finite OHLCV rows; reject malformed daily-limit/corporate-action glitches."""
    rows = []
    for candle in candles or []:
        try:
            o, h, l, close = (
                float(candle[key]) for key in ("open", "high", "low", "close")
            )
            if not all(math.isfinite(v) and v > 0 for v in (o, h, l, close)):
                continue
            if h < max(o, close, l) or l > min(o, close, h):
                continue
            raw_volume = candle.get("volume")
            volume = float(raw_volume) if raw_volume is not None else None
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                volume = None
            rows.append({"open": o, "high": h, "low": l, "close": close, "volume": volume})
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return rows


def market_structure_assessment(candles, lookback=120, pivot_width=2):
    """Score confirmed swing structure (higher highs/lows vs lower highs/lows).

    Pivots require bars on both sides, so the latest two pivots are confirmed rather
    than inferred from the current unfinished swing.
    """
    rows = valid_ohlc_rows(candles)[-lookback:]
    blocked = {
        "score": None, "label": "دادهٔ ناکافی", "last_swing_high": None,
        "previous_swing_high": None, "last_swing_low": None,
        "previous_swing_low": None, "high_structure": None, "low_structure": None,
    }
    width = max(1, int(pivot_width))
    if len(rows) < 2 * width + 8:
        return blocked

    swing_highs, swing_lows = [], []
    for i in range(width, len(rows) - width):
        candle = rows[i]
        neighbors = rows[i - width:i] + rows[i + 1:i + width + 1]
        neighbor_highs = [x["high"] for x in neighbors]
        neighbor_lows = [x["low"] for x in neighbors]
        if candle["high"] >= max(neighbor_highs) and candle["high"] > min(neighbor_highs):
            swing_highs.append((i, candle["high"]))
        if candle["low"] <= min(neighbor_lows) and candle["low"] < max(neighbor_lows):
            swing_lows.append((i, candle["low"]))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return blocked

    prev_high, last_high = swing_highs[-2][1], swing_highs[-1][1]
    prev_low, last_low = swing_lows[-2][1], swing_lows[-1][1]
    tolerance = max(rows[-1]["close"] * 0.002, 1e-9)
    high_state = (
        "HH" if last_high > prev_high + tolerance
        else "LH" if last_high < prev_high - tolerance else "EQ"
    )
    low_state = (
        "HL" if last_low > prev_low + tolerance
        else "LL" if last_low < prev_low - tolerance else "EQ"
    )
    score = 50
    score += 22 if high_state == "HH" else -22 if high_state == "LH" else 0
    score += 22 if low_state == "HL" else -22 if low_state == "LL" else 0
    score = max(0, min(100, score))
    if high_state == "HH" and low_state == "HL":
        label = "ساختار صعودی؛ سقف و کف بالاتر"
    elif high_state == "LH" and low_state == "LL":
        label = "ساختار نزولی؛ سقف و کف پایین‌تر"
    else:
        label = "ساختار مختلط یا در حال تغییر"

    return {
        "score": round(float(score), 1),
        "label": label,
        "last_swing_high": round(last_high, 4),
        "previous_swing_high": round(prev_high, 4),
        "last_swing_low": round(last_low, 4),
        "previous_swing_low": round(prev_low, 4),
        "high_structure": high_state,
        "low_structure": low_state,
    }


def volume_price_assessment(candles, lookback=20):
    """Measure accumulation/distribution pressure from candle close location and volume."""
    rows = [
        row for row in valid_ohlc_rows(candles)
        if isinstance(row.get("volume"), (int, float)) and row["volume"] > 0
    ][-lookback:]
    blocked = {
        "score": None, "weighted_close_location": None,
        "up_volume": None, "down_volume": None,
        "up_down_volume_ratio": None, "sample_bars": len(rows),
    }
    if len(rows) < 8:
        return blocked

    total_volume = sum(row["volume"] for row in rows)
    if total_volume <= 0:
        return blocked
    weighted_clv = sum(
        (((2 * row["close"] - row["high"] - row["low"]) /
          (row["high"] - row["low"])) if row["high"] > row["low"] else 0.0)
        * row["volume"] for row in rows
    ) / total_volume
    up_volume = sum(row["volume"] for row in rows if row["close"] > row["open"])
    down_volume = sum(row["volume"] for row in rows if row["close"] < row["open"])
    directional_imbalance = (
        (up_volume - down_volume) / (up_volume + down_volume)
        if up_volume + down_volume > 0 else 0.0
    )
    score = max(0.0, min(100.0, 50 + 32 * weighted_clv + 18 * directional_imbalance))
    return {
        "score": round(score, 1),
        "weighted_close_location": round(weighted_clv, 3),
        "up_volume": round(up_volume, 2),
        "down_volume": round(down_volume, 2),
        "up_down_volume_ratio": round(up_volume / down_volume, 3) if down_volume > 0 else None,
        "sample_bars": len(rows),
        "interpretation": (
            "فشار انباشت/خرید در کندل‌ها" if score >= 62
            else "فشار توزیع/فروش در کندل‌ها" if score <= 38
            else "فشار حجمی خنثی یا مختلط"
        ),
    }


def breakout_position_assessment(candles, volume_ratio=None):
    """Check 20/55-bar range breaks and location in the available 52-week range."""
    rows = valid_ohlc_rows(candles)
    blocked = {
        "score": None, "breakout_20": None, "breakout_55": None,
        "breakdown_20": None, "breakdown_55": None, "range_position_52w": None,
    }
    if len(rows) < 25:
        return blocked
    latest = rows[-1]
    prior20 = rows[-21:-1]
    prior55 = rows[-56:-1] if len(rows) >= 56 else []
    reference = rows[-252:]
    range_high = max(x["high"] for x in reference)
    range_low = min(x["low"] for x in reference)
    range_position = (
        (latest["close"] - range_low) / (range_high - range_low)
        if range_high > range_low else 0.5
    )
    score = 30 + 40 * max(0.0, min(1.0, range_position))
    breakout20 = latest["close"] > max(x["high"] for x in prior20)
    breakdown20 = latest["close"] < min(x["low"] for x in prior20)
    breakout55 = latest["close"] > max(x["high"] for x in prior55) if prior55 else None
    breakdown55 = latest["close"] < min(x["low"] for x in prior55) if prior55 else None
    if breakout20:
        score += 10
    if breakout55 is True:
        score += 12
    if breakdown20:
        score -= 10
    if breakdown55 is True:
        score -= 12
    volume_confirmed = isinstance(volume_ratio, (int, float)) and volume_ratio >= 1.5
    if volume_confirmed and (breakout20 or breakout55 is True):
        score += 8
    elif volume_confirmed and (breakdown20 or breakdown55 is True):
        score -= 8
    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "breakout_20": bool(breakout20),
        "breakout_55": breakout55,
        "breakdown_20": bool(breakdown20),
        "breakdown_55": breakdown55,
        "range_position_52w": round(range_position, 3),
        "volume_confirmed": volume_confirmed,
        "prior_20_high": round(max(x["high"] for x in prior20), 4),
        "prior_20_low": round(min(x["low"] for x in prior20), 4),
        "prior_55_high": round(max(x["high"] for x in prior55), 4) if prior55 else None,
        "prior_55_low": round(min(x["low"] for x in prior55), 4) if prior55 else None,
    }


def trend_efficiency_assessment(candles, period=20):
    """Signed Kaufman-style efficiency ratio: direction and smoothness, not RSI."""
    rows = valid_ohlc_rows(candles)
    if len(rows) <= period:
        return {"score": None, "efficiency_ratio": None, "direction": "دادهٔ ناکافی"}
    closes = [row["close"] for row in rows[-period - 1:]]
    net = closes[-1] - closes[0]
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path <= 0:
        ratio = 0.0
    else:
        ratio = min(1.0, abs(net) / path)
    signed = ratio if net > 0 else -ratio if net < 0 else 0.0
    score = max(0.0, min(100.0, 50 + 50 * signed))
    direction = "صعودی" if net > 0 else "نزولی" if net < 0 else "خنثی"
    return {
        "score": round(score, 1),
        "efficiency_ratio": round(ratio, 3),
        "direction": direction,
        "net_change_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
        "period": period,
    }


def volatility_quality_assessment(candles):
    """Measure true-range volatility relative to price; missing OHLC stays unavailable."""
    rows = valid_ohlc_rows(candles)
    if len(rows) < 15:
        return {"score": None, "atr14": None, "atr_pct": None}
    ranges = []
    for i in range(len(rows) - 14, len(rows)):
        row = rows[i]
        previous_close = rows[i - 1]["close"] if i else row["close"]
        ranges.append(max(
            row["high"] - row["low"],
            abs(row["high"] - previous_close),
            abs(row["low"] - previous_close),
        ))
    atr = sum(ranges) / len(ranges)
    close = rows[-1]["close"]
    atr_pct = atr / close * 100 if close > 0 else None
    if atr_pct is None or not math.isfinite(atr_pct):
        score = None
    elif atr_pct <= 2:
        score = 92
    elif atr_pct <= 3.5:
        score = 85
    elif atr_pct <= 5:
        score = 75
    elif atr_pct <= 7.5:
        score = 60
    elif atr_pct <= 10:
        score = 45
    elif atr_pct <= 14:
        score = 30
    else:
        score = 20
    return {
        "score": float(score) if score is not None else None,
        "atr14": round(atr, 4),
        "atr_pct": round(atr_pct, 3) if atr_pct is not None else None,
    }


def weighted_technical_score(factors):
    """Combine independent technical dimensions while renormalizing missing evidence."""
    weights = {
        "multi_timeframe_trend": 25,
        "daily_market_structure": 15,
        "weekly_market_structure": 15,
        "monthly_market_structure": 10,
        "volume_price_pressure": 15,
        "breakout_structure": 10,
        "trend_efficiency": 5,
        "volatility_quality": 5,
    }
    available = [
        (weights[key], value) for key, value in factors.items()
        if key in weights and isinstance(value, (int, float)) and math.isfinite(value)
    ]
    total_weight = sum(weight for weight, _ in available)
    score = (
        sum(weight * max(0.0, min(100.0, float(value))) for weight, value in available) / total_weight
        if total_weight else 0.0
    )
    return round(score, 1), total_weight



def analyze_stock(stock):
    history = stock.get("history", {})
    daily = read_candles((history.get(DAILY) or {}).get("file"))
    weekly = read_candles((history.get(WEEKLY) or {}).get("file"))
    monthly = read_candles((history.get(MONTHLY) or {}).get("file"))

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
        "daily_close_above_sma50": above(daily_close, daily_sma50),
        "daily_sma50_above_sma200": above(daily_sma50, daily_sma200),
        "weekly_close_above_sma13": above(weekly_close, weekly_sma13),
        "weekly_sma13_above_sma26": above(weekly_sma13, weekly_sma26),
        "monthly_close_above_sma6": above(monthly_close, monthly_sma6),
        "monthly_sma6_above_sma12": above(monthly_sma6, monthly_sma12),
        "weekly_13_period_return_positive": (
            None if weekly_13_return is None else weekly_13_return > 0
        ),
        "monthly_6_period_return_positive": (
            None if monthly_6_return is None else monthly_6_return > 0
        ),
    }

    available_weight = sum(
        WEIGHTS[key] for key, value in checks.items() if value is not None
    )
    earned_weight = sum(
        WEIGHTS[key] for key, value in checks.items() if value is True
    )
    trend_check_score = (
        round(earned_weight * 100 / available_weight, 1)
        if available_weight
        else None
    )

    recent_volumes = [
        candle.get("volume")
        for candle in daily[-20:]
        if isinstance(candle.get("volume"), (int, float))
        and candle.get("volume") > 0
    ]
    average_volume20 = (
        sum(recent_volumes) / len(recent_volumes) if recent_volumes else None
    )
    latest_volume = daily[-1].get("volume") if daily else None
    volume_ratio = (
        latest_volume / average_volume20
        if isinstance(latest_volume, (int, float)) and average_volume20
        else None
    )

    # Strong technical ranking: price structure and traded-volume behavior sit
    # alongside, rather than underneath, conventional trend indicators.
    daily_structure = market_structure_assessment(daily)
    weekly_structure = market_structure_assessment(weekly, lookback=52)
    monthly_structure = market_structure_assessment(monthly, lookback=36)
    volume_price = volume_price_assessment(daily)
    breakout = breakout_position_assessment(daily, volume_ratio)
    efficiency = trend_efficiency_assessment(daily, period=20)
    volatility = volatility_quality_assessment(daily)
    factor_scores = {
        "multi_timeframe_trend": trend_check_score,
        "daily_market_structure": daily_structure.get("score"),
        "weekly_market_structure": weekly_structure.get("score"),
        "monthly_market_structure": monthly_structure.get("score"),
        "volume_price_pressure": volume_price.get("score"),
        "breakout_structure": breakout.get("score"),
        "trend_efficiency": efficiency.get("score"),
        "volatility_quality": volatility.get("score"),
    }
    technical_score, technical_factor_available_weight = weighted_technical_score(factor_scores)

    latest_date = (
        datetime.fromtimestamp(daily[-1]["time"], timezone.utc).date()
        if daily
        else None
    )
    data_age = (
        (datetime.now(timezone.utc).date() - latest_date).days
        if latest_date is not None
        else None
    )
    history_mature = (
        len(daily) >= 200 and len(weekly) >= 26 and len(monthly) >= 12
    )
    eligible = (
        history_mature and data_age is not None and 0 <= data_age <= 10
    )

    penalty, flags, heat_label, entry_timing = assess_heat(
        rsi_value, daily_20_return, daily_60_return, monthly_6_return, volume_ratio
    )
    risk_adjusted_score = round(max(0.0, technical_score - penalty), 1)

    if technical_score >= 75:
        label = "روند تکنیکال قوی"
    elif technical_score >= 55:
        label = "روند تکنیکال متوسط"
    else:
        label = "روند تکنیکال ضعیف"

    if rsi_value is None:
        rsi_state = "داده ناکافی"
    elif rsi_value >= 70:
        rsi_state = "بالا؛ احتمال داغ‌شدن مومنتوم"
    elif rsi_value <= 30:
        rsi_state = "پایین؛ احتمال ضعف مومنتوم"
    else:
        rsi_state = "محدودهٔ میانی"

    return {
        "symbol": stock.get("symbol", ""),
        "company": stock.get("company", ""),
        "exchange": stock.get("exchange", ""),
        "chart_id": stock.get("chart_id", ""),
        "technical_score": technical_score,
        "technical_factor_scores": factor_scores,
        "technical_factor_available_weight": technical_factor_available_weight,
        "market_structure": {
            "daily": daily_structure,
            "weekly": weekly_structure,
            "monthly": monthly_structure,
        },
        "volume_price_analysis": volume_price,
        "breakout_analysis": breakout,
        "trend_efficiency": efficiency,
        "volatility_quality": volatility,
        "risk_penalty": penalty,
        "risk_adjusted_score": risk_adjusted_score,
        "available_score_weight": available_weight,
        "trend_check_available_weight": available_weight,
        "label": label,
        "eligible_for_long_term_ranking": eligible,
        "history_mature": history_mature,
        "latest_daily_date": latest_date.isoformat() if latest_date else None,
        "data_age_days": data_age,
        "history_bars": {
            "daily": len(daily),
            "weekly": len(weekly),
            "monthly": len(monthly),
        },
        "price": {
            "daily_close": daily_close,
            "weekly_close": weekly_close,
            "monthly_close": monthly_close,
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
            "daily_20_period": daily_20_return,
            "daily_60_period": daily_60_return,
            "weekly_13_period": weekly_13_return,
            "monthly_6_period": monthly_6_return,
        },
        "rsi14_daily": round(rsi_value, 2) if rsi_value is not None else None,
        "rsi_state": rsi_state,
        "heat_risk": heat_label,
        "risk_flags": flags,
        "entry_timing": entry_timing,
        "latest_daily_volume": latest_volume,
        "average_volume20": (
            round(average_volume20, 2) if average_volume20 is not None else None
        ),
        "volume_vs_20_period_average": (
            round(volume_ratio, 3) if volume_ratio is not None else None
        ),
        "checks": checks,
        "technical_targets": calculate_technical_targets(daily, data_age),
    }


def add_relative_momentum_tiebreaker(items):
    """Assign a cross-sectional momentum/volume score used only to break trend-score ties.

    The original weighted technical checklist and heat penalty stay unchanged.
    Each input metric is ranked across currently eligible instruments; missing
    metrics are omitted and the remaining weights are renormalized per symbol.
    This relative score is a ranking aid, not a validated return forecast.
    """
    metrics = [
        ("monthly_6_period", 0.30, lambda item: item["momentum_pct"].get("monthly_6_period")),
        ("weekly_13_period", 0.25, lambda item: item["momentum_pct"].get("weekly_13_period")),
        ("daily_60_period", 0.25, lambda item: item["momentum_pct"].get("daily_60_period")),
        ("daily_20_period", 0.10, lambda item: item["momentum_pct"].get("daily_20_period")),
        ("volume_ratio", 0.10, lambda item: item.get("volume_vs_20_period_average")),
    ]
    accum = {
        id(item): {"weighted_sum": 0.0, "weight_sum": 0.0, "components": {}}
        for item in items
    }

    for metric_name, weight, getter in metrics:
        values = []
        for item in items:
            value = getter(item)
            if isinstance(value, (int, float)):
                values.append((float(value), id(item)))
        if not values:
            continue
        values.sort(key=lambda pair: pair[0])
        n = len(values)
        start = 0
        while start < n:
            end = start
            while end + 1 < n and values[end + 1][0] == values[start][0]:
                end += 1
            # Midrank percentile preserves ties and keeps the scale in [0, 100].
            percentile = 50.0 if n == 1 else ((start + end) / 2) * 100.0 / (n - 1)
            for pos in range(start, end + 1):
                item_id = values[pos][1]
                accum[item_id]["weighted_sum"] += percentile * weight
                accum[item_id]["weight_sum"] += weight
                accum[item_id]["components"][metric_name] = round(percentile, 1)
            start = end + 1

    for item in items:
        data = accum[id(item)]
        item["relative_momentum_score"] = (
            round(data["weighted_sum"] / data["weight_sum"], 2)
            if data["weight_sum"] else None
        )
        item["relative_momentum_components"] = data["components"]
        # Checklist scores saturate at 100 for many names; use a continuous
        # cross-sectional component so the displayed technical score can rank them.
        checklist = float(item.get("risk_adjusted_score") or 0)
        relative = item.get("relative_momentum_score")
        item["technical_rank_score"] = (
            round(0.70 * checklist + 0.30 * float(relative), 2)
            if isinstance(relative, (int, float))
            else round(checklist, 2)
        )


def technical_rank_key(item):
    """Rank by the continuous technical composite, then resolve ties deterministically."""
    momentum = item.get("momentum_pct", {})
    monthly = momentum.get("monthly_6_period")
    weekly = momentum.get("weekly_13_period")
    daily60 = momentum.get("daily_60_period")
    return (
        -float(item.get("technical_rank_score") or 0),
        -float(item.get("risk_adjusted_score") or 0),
        -float(item.get("relative_momentum_score") or 0),
        -(float(monthly) if monthly is not None else -1e12),
        -(float(weekly) if weekly is not None else -1e12),
        -(float(daily60) if daily60 is not None else -1e12),
        str(item.get("symbol") or ""),
    )


def main():
    if not UNIVERSE_FILE.is_file():
        raise FileNotFoundError(f"Universe file not found: {UNIVERSE_FILE}")

    universe = read_json(UNIVERSE_FILE)
    stocks = universe.get("stocks", [])
    if not isinstance(stocks, list):
        raise ValueError("Invalid universe format: 'stocks' must be a list")

    results = []
    errors = 0
    print(f"Input stocks: {len(stocks)}")

    for index, stock in enumerate(stocks, start=1):
        try:
            results.append(analyze_stock(stock))
        except Exception as exc:
            errors += 1
            print(f"Analysis error for {stock.get('symbol', '?')}: {exc}")
        if index % 50 == 0 or index == len(stocks):
            print(f"Analyzed {index}/{len(stocks)}")

    eligible = [
        item for item in results
        if item["eligible_for_long_term_ranking"]
    ]
    insufficient = len(results) - len(eligible)
    add_relative_momentum_tiebreaker(eligible)
    eligible.sort(key=technical_rank_key)

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
            "score": (
                "Weighted multi-factor score: multi-timeframe trend alignment (25%), "
                "confirmed daily/weekly/monthly market structure (40%), volume-price pressure "
                "(15%), 20/55-bar breakout structure (10%), trend efficiency (5%) and "
                "ATR/price volatility quality (5%). Missing dimensions are excluded and "
                "remaining weights renormalized. This is a screening heuristic, not a "
                "validated forecast."
            ),
            "ranking_score": (
                "Provisional technical rank = 70% risk-adjusted multi-factor score + "
                "30% cross-sectional relative momentum score. The relative score "
                "uses percentiles: 30% 6-period monthly return, 25% 13-period weekly "
                "return, 25% 60-period daily return, 10% 20-period daily return and "
                "10% latest volume / 20-session average; missing measures are "
                "excluded and available weights are renormalized. The composite prioritizes "
                "confirmed structure and volume-price behavior over indicator count."
            ),
            "sort_order": (
                "Technical rank score descending; then risk-adjusted checklist, "
                "relative momentum and longer-period momentum."
            ),
            "risk_adjustment": (
                "Experimental heat penalty subtracted from technical score; "
                "ranked by adjusted score"
            ),
            "eligibility": (
                "At least 200 daily, 26 weekly and 12 monthly candles, "
                "with latest daily data no more than 10 days old"
            ),
        },
        "warning": (
            "Technical screening only, not a buy/sell recommendation. The composite "
            "technical rank is provisional and has not been validated by walk-forward "
            "or out-of-sample backtesting. Fundamental health, earnings quality, "
            "project ROI and cash-flow quality are not included. Full TSE/IFB coverage "
            "has not been independently verified."
        ),
        "top_50_for_review": eligible[:50],
        "all_eligible_ranked": eligible,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print()
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
