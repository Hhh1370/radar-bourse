def assess_heat(rsi_value, daily_20, daily_60, monthly_6, volume_ratio):
    penalty = 0
    flags = []

    def tier(value, levels, flag):
        nonlocal penalty
        if value is None:
            return
        for threshold, points in levels:
            if value >= threshold:
                penalty += points
                flags.append(flag)
                return

    tier(rsi_value, [(90, 18), (80, 12), (70, 5)], "rsi_high")
    tier(daily_20, [(100, 12), (50, 8), (25, 4)], "daily_surge")
    tier(daily_60, [(300, 10), (150, 6), (75, 3)], "medium_term_surge")
    tier(monthly_6, [(400, 10), (200, 6), (100, 3)], "long_term_surge")

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
