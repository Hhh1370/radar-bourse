"""Tests for the historical, as-of-date radar validation."""
import math
import unittest
from datetime import date, datetime, timedelta, timezone

from scripts.validate_radar_scores import (
    clean_candles,
    forward_return_pct,
    score_as_of,
    spearman_correlation,
    classify_validation_status,
)
from scripts import build_technical_radar as tech


def candle(day, close, open_price=None, volume=1000):
    op = float(close if open_price is None else open_price)
    value = float(close)
    ts = int(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).timestamp())
    return {
        "time": ts,
        "open": op,
        "high": max(op, value) * 1.01,
        "low": min(op, value) * 0.99,
        "close": value,
        "volume": volume,
    }


def series(start, end, step_days, base=100.0, slope=0.25):
    result = []
    day = start
    i = 0
    while day <= end:
        close = base + slope * i + math.sin(i / 5.0) * 1.8
        result.append(candle(day, close, close - 0.15, 1000 + (i % 9) * 25))
        i += 1
        day += timedelta(days=step_days)
    return result


class RadarValidationTests(unittest.TestCase):
    def test_clean_candles_rejects_bad_geometry_and_deduplicates_time(self):
        good = candle(date(2026, 1, 2), 105, 100)
        duplicate = dict(good, close=106, high=107, low=99)
        bad = dict(candle(date(2026, 1, 3), 100), low=120)
        result = clean_candles([good, duplicate, bad])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["close"], 106.0)

    def test_forward_return_uses_next_session_open_and_nth_future_close(self):
        rows = [
            candle(date(2026, 1, 1), 100),
            candle(date(2026, 1, 2), 102, 101),
            candle(date(2026, 1, 3), 106, 103),
            candle(date(2026, 1, 4), 110, 105),
        ]
        dates = [datetime.fromtimestamp(x["time"], timezone.utc).date() for x in rows]
        result = forward_return_pct(rows, dates, date(2026, 1, 1), 2)
        self.assertAlmostEqual(result, (106 / 101 - 1) * 100, places=4)

    def test_forward_return_is_unavailable_without_full_horizon(self):
        rows = [candle(date(2026, 1, 1) + timedelta(days=i), 100 + i) for i in range(4)]
        dates = [datetime.fromtimestamp(x["time"], timezone.utc).date() for x in rows]
        self.assertIsNone(forward_return_pct(rows, dates, date(2026, 1, 2), 3))

    def test_spearman_correlation_handles_monotonic_and_tied_values(self):
        self.assertAlmostEqual(spearman_correlation([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman_correlation([1, 1, 3, 4], [20, 10, 40, 30]), 0.7379, places=4)
        self.assertIsNone(spearman_correlation([1, 2], [1, 2]))

    def test_validation_status_rejects_relative_edge_without_absolute_short_horizon_profitability(self):
        h20 = {
            "median_spearman_rank_ic": 0.10,
            "median_top_quintile_excess_vs_universe_pct": 2.0,
            "median_top_quintile_mean_return_pct": -1.2,
            "median_top_quintile_win_rate_pct": 41.0,
        }
        h60 = {
            "median_spearman_rank_ic": 0.04,
            "median_top_quintile_excess_vs_universe_pct": 1.5,
            "median_top_quintile_mean_return_pct": 2.3,
            "median_top_quintile_win_rate_pct": 53.0,
        }
        self.assertEqual(
            classify_validation_status(True, h20, h60),
            "relative_edge_but_mixed_absolute_returns",
        )

    def test_validation_status_requires_absolute_returns_and_win_rate_for_positive_label(self):
        h20 = {
            "median_spearman_rank_ic": 0.10,
            "median_top_quintile_excess_vs_universe_pct": 2.0,
            "median_top_quintile_mean_return_pct": 1.2,
            "median_top_quintile_win_rate_pct": 52.0,
        }
        h60 = {
            "median_spearman_rank_ic": 0.04,
            "median_top_quintile_excess_vs_universe_pct": 1.5,
            "median_top_quintile_mean_return_pct": 2.3,
            "median_top_quintile_win_rate_pct": 53.0,
        }
        self.assertEqual(classify_validation_status(True, h20, h60), "positive_out_of_sample_evidence")
        self.assertEqual(classify_validation_status(False, h20, h60), "insufficient_history_for_validation")

    def test_as_of_score_does_not_change_when_future_prices_change(self):
        cutoff = date(2025, 6, 30)
        start_daily = cutoff - timedelta(days=520)
        end_daily = cutoff + timedelta(days=150)
        daily = series(start_daily, end_daily, 1, base=100, slope=0.1)
        weekly = series(date(2023, 1, 2), cutoff + timedelta(days=150), 7, base=100, slope=0.8)
        monthly = []
        day = date(2023, 1, 1)
        while day <= cutoff + timedelta(days=150):
            close = 100 + (day.year - 2023) * 9 + day.month * 1.4
            monthly.append(candle(day, close, close - 0.3, 50000))
            if day.month == 12:
                day = date(day.year + 1, 1, 1)
            else:
                day = date(day.year, day.month + 1, 1)

        stock = {"symbol": "نماد"}
        baseline = score_as_of(stock, daily, weekly, monthly, cutoff, tech)
        self.assertIsNotNone(baseline)
        mutated_future = []
        for row in daily:
            row_date = datetime.fromtimestamp(row["time"], timezone.utc).date()
            if row_date > cutoff:
                close = row["close"] * 0.35
                mutated_future.append(dict(row, open=close * 1.01, high=close * 1.02, low=close * 0.98, close=close))
            else:
                mutated_future.append(dict(row))
        modified = score_as_of(stock, mutated_future, weekly, monthly, cutoff, tech)
        self.assertIsNotNone(modified)
        self.assertEqual(baseline["technical_score"], modified["technical_score"])
        self.assertEqual(baseline["risk_adjusted_score"], modified["risk_adjusted_score"])
        self.assertEqual(baseline["momentum_pct"], modified["momentum_pct"])
        self.assertNotEqual(
            baseline["forward_returns_pct"]["20"],
            modified["forward_returns_pct"]["20"],
        )


if __name__ == "__main__":
    unittest.main()
