"""Regression tests for multi-factor technical-radar components."""
import unittest

from scripts.build_technical_radar import (
    breakout_position_assessment,
    market_structure_assessment,
    trend_efficiency_assessment,
    volume_price_assessment,
    volatility_quality_assessment,
    weighted_technical_score,
)


def candles_from_closes(closes, volumes=None):
    rows = []
    previous = float(closes[0])
    for i, raw in enumerate(closes):
        close = float(raw)
        rows.append({
            "time": i + 1,
            "open": previous,
            "high": max(previous, close) * 1.01,
            "low": min(previous, close) * 0.99,
            "close": close,
            "volume": float(volumes[i]) if volumes else 1000.0,
        })
        previous = close
    return rows


class TechnicalFactorTests(unittest.TestCase):
    def test_weighted_score_renormalizes_missing_dimensions(self):
        score, weight = weighted_technical_score({
            "multi_timeframe_trend": 80,
            "daily_market_structure": 60,
            "weekly_market_structure": None,
            "monthly_market_structure": None,
            "volume_price_pressure": None,
            "breakout_structure": None,
            "trend_efficiency": None,
            "volatility_quality": None,
        })
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        self.assertEqual(weight, 40)
        self.assertAlmostEqual(score, 72.5)

    def test_weighted_score_ignores_invalid_values(self):
        score, weight = weighted_technical_score({
            "multi_timeframe_trend": 110,
            "daily_market_structure": -5,
            "volume_price_pressure": float("nan"),
            "unknown_dimension": 100,
        })
        self.assertEqual(weight, 40)
        self.assertAlmostEqual(score, 50.0)

    def test_structure_finds_confirmed_higher_highs_and_lows(self):
        # Repeated stair-step movement gives confirmed rising local swings.
        closes = []
        for base in (100, 104, 108, 112, 116, 120, 124, 128):
            closes.extend([base, base + 1, base + 2, base + 1, base - 1])
        result = market_structure_assessment(candles_from_closes(closes), lookback=100, pivot_width=1)
        self.assertIsNotNone(result["score"])
        self.assertGreaterEqual(result["score"], 50)
        self.assertIn(result["high_structure"], {"HH", "LH", "EQ"})
        self.assertIn(result["low_structure"], {"HH", "LH", "EQ", "HL", "LL"})

    def test_structure_withholds_score_when_history_is_too_short(self):
        result = market_structure_assessment(candles_from_closes([10, 11, 12, 11, 10]), pivot_width=2)
        self.assertIsNone(result["score"])

    def test_volume_price_pressure_uses_volume_and_close_location(self):
        # Strong closes near highs with positive-volume bars should create positive pressure.
        rows = []
        for i in range(20):
            close = 100 + i
            rows.append({
                "open": close - 1, "high": close + 0.1,
                "low": close - 1.2, "close": close,
                "volume": 1000 + i * 10,
            })
        result = volume_price_assessment(rows)
        self.assertIsNotNone(result["score"])
        self.assertGreater(result["score"], 50)
        self.assertEqual(result["sample_bars"], 20)

    def test_breakout_requires_close_outside_prior_range(self):
        closes = [100 + ((i % 5) - 2) for i in range(40)]
        rows = candles_from_closes(closes)
        rows[-1].update({"open": 101, "low": 101, "high": 120, "close": 119, "volume": 5000})
        result = breakout_position_assessment(rows, volume_ratio=2.0)
        self.assertTrue(result["breakout_20"])
        self.assertTrue(result["volume_confirmed"])
        self.assertGreater(result["score"], 50)

    def test_efficiency_ratio_distinguishes_directional_trend(self):
        rising = candles_from_closes([100 + i for i in range(30)])
        falling = candles_from_closes([130 - i for i in range(30)])
        up = trend_efficiency_assessment(rising, period=20)
        down = trend_efficiency_assessment(falling, period=20)
        self.assertEqual(up["direction"], "صعودی")
        self.assertEqual(down["direction"], "نزولی")
        self.assertGreater(up["score"], 50)
        self.assertLess(down["score"], 50)

    def test_volatility_quality_returns_normalized_score(self):
        rows = []
        for i in range(30):
            close = 100 + i * 0.5
            rows.append({
                "open": close - 0.2, "high": close + 0.4,
                "low": close - 0.4, "close": close,
                "volume": 1000,
            })
        result = volatility_quality_assessment(rows)
        self.assertIsNotNone(result["atr14"])
        self.assertGreater(result["atr_pct"], 0)
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)


if __name__ == "__main__":
    unittest.main()
