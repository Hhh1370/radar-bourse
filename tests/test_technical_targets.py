"""Regression tests for guarded daily technical target scenarios."""
import math
import unittest
from datetime import datetime, timedelta, timezone

from scripts.build_technical_radar import calculate_technical_targets


def synthetic_candles(count=180, days_ago=0):
    """Deterministic repeating swing pattern with known supports/resistances."""
    pattern = [100, 102, 105, 110, 115, 110, 105, 100, 95, 90, 95, 100, 105, 110, 115, 110, 105, 100]
    today = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    rows = []
    previous = pattern[0]
    for i in range(count):
        close = float(pattern[i % len(pattern)])
        day = today - timedelta(days=count - 1 - i)
        timestamp = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        opened = float(previous)
        rows.append({
            "time": timestamp,
            "open": opened,
            "high": max(opened, close) + 1.0,
            "low": min(opened, close) - 1.0,
            "close": close,
            "volume": 1000,
        })
        previous = close
    return rows


class TechnicalTargetTests(unittest.TestCase):
    def test_refuses_short_history(self):
        result = calculate_technical_targets(synthetic_candles(90), 0)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("۱۲۰", result["reason"])

    def test_refuses_stale_input_flag(self):
        result = calculate_technical_targets(synthetic_candles(), 6)
        self.assertEqual(result["status"], "unavailable")

    def test_checks_real_latest_valid_candle_date(self):
        # A falsely fresh caller-provided age must not override stale candle dates.
        result = calculate_technical_targets(synthetic_candles(days_ago=12), 0)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("۵ روز", result["reason"])

    def test_rejects_invalid_ohlc_and_nonfinite_values(self):
        rows = synthetic_candles()
        for row in rows:
            row["high"] = row["low"] - 1
        result = calculate_technical_targets(rows, 0)
        self.assertEqual(result["status"], "unavailable")

        rows = synthetic_candles()
        rows[-1]["close"] = math.nan
        result = calculate_technical_targets(rows, 0)
        self.assertEqual(result["status"], "available")
        # The invalid final candle must be skipped rather than used as the reference.
        self.assertNotEqual(result["latest_price_date"], datetime.now(timezone.utc).date().isoformat())

    def test_emitted_scenarios_obey_price_geometry_and_rr_floor(self):
        result = calculate_technical_targets(synthetic_candles(), 0)
        self.assertEqual(result["status"], "available")
        self.assertGreater(result["current_price"], 0)
        self.assertGreater(result["atr14"], 0)
        self.assertTrue(result["support_levels"])
        self.assertTrue(result["resistance_levels"])

        for direction in ("buy", "sell"):
            setup = result[direction]
            if setup is None:
                continue
            entry = setup["entry_reference"]
            stop = setup["stop_loss"]
            target1 = setup["target_1"]
            self.assertGreaterEqual(setup["risk_reward_tp1"], 1.3)
            if direction == "buy":
                self.assertLess(stop, entry)
                self.assertGreater(target1, entry)
                if setup["target_2"] is not None:\n                    self.assertGreater(setup["target_2"], target1)
            else:
                self.assertGreater(stop, entry)
                self.assertLess(target1, entry)
                if setup["target_2"] is not None:\n                    self.assertLess(setup["target_2"], target1)


if __name__ == "__main__":
    unittest.main()
