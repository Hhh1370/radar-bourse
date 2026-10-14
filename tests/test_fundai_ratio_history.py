"""Tests for strict period matching and scoring of public Fundai ratio history."""
import unittest
from datetime import date

from scripts.fundai_ratio_history import (
    build_history_trend_scores,
    normalize_history_payload,
    parse_jalali_date,
)


class FundaiHistoryTests(unittest.TestCase):
    def test_normalize_payload_deduplicates_report_date_and_duration(self):
        payload = {
            "company_slug": "sample",
            "duration": "ttm",
            "durations": ["s-1"],
            "results": [
                {"company": "نماد", "report_date": "۱۴۰۵-۰۳-۳۱", "duration_code": "s-1", "roe_ttm": 0.2},
                {"company": "نماد", "report_date": "1405-03-31", "duration_code": "s-1", "roe_ttm": 0.25},
                {"company": "نماد", "report_date": "1405-03-31", "duration_code": "s-2", "roe_ttm": 0.3},
            ],
        }
        normalized = normalize_history_payload(
            payload, company_slug="sample", symbol="نماد",
            industry="صنعت نمونه", tsetmc_id="123", fetched_at_utc="2026-10-14T00:00:00+00:00",
        )
        self.assertEqual(normalized["history_row_count"], 2)
        self.assertEqual(normalized["latest_report_date"], "1405-03-31")
        self.assertEqual(normalized["company_slug"], "sample")

    def test_rejects_company_slug_mismatch(self):
        with self.assertRaises(ValueError):
            normalize_history_payload(
                {"company_slug": "wrong", "results": [{"report_date":"1405-03-31","duration_code":"s-1"}]},
                company_slug="expected", symbol="نماد",
            )

    def test_trend_uses_same_duration_and_same_month_day_prior_year(self):
        snapshot = {
            "histories": [{
                "symbol": "نماد",
                "company_slug": "sample",
                "industry": "صنعت نمونه",
                "history_rows": [
                    {"report_date":"1405-03-31","duration_code":"s-1","roe_ttm":0.30,"roa_ttm":0.12,"net_profit_margin_ttm":0.20,"debt_to_assets":0.30},
                    {"report_date":"1404-03-31","duration_code":"s-1","roe_ttm":0.20,"roa_ttm":0.08,"net_profit_margin_ttm":0.10,"debt_to_assets":0.40},
                    {"report_date":"1405-06-30","duration_code":"s-2","roe_ttm":0.35,"roa_ttm":0.14,"net_profit_margin_ttm":0.22,"debt_to_assets":0.25},
                    {"report_date":"1404-06-30","duration_code":"s-2","roe_ttm":0.30,"roa_ttm":0.12,"net_profit_margin_ttm":0.18,"debt_to_assets":0.30},
                ],
            }]
        }
        result = build_history_trend_scores(snapshot, today=date(2026, 10, 14))["نماد"]
        self.assertIsNotNone(result["score"])
        self.assertGreater(result["score"], 50)
        self.assertEqual(result["matched_period_pair_count"], 2)
        self.assertGreaterEqual(result["metric_comparison_count"], 6)

    def test_mismatched_period_and_duration_are_not_compared(self):
        snapshot = {
            "histories": [{
                "symbol": "نماد",
                "history_rows": [
                    {"report_date":"1405-03-31","duration_code":"s-1","roe_ttm":0.30,"roa_ttm":0.12,"net_profit_margin_ttm":0.20},
                    {"report_date":"1404-06-30","duration_code":"s-1","roe_ttm":0.20,"roa_ttm":0.08,"net_profit_margin_ttm":0.10},
                    {"report_date":"1405-06-30","duration_code":"s-2","roe_ttm":0.35,"roa_ttm":0.14,"net_profit_margin_ttm":0.22},
                    {"report_date":"1404-06-30","duration_code":"s-3","roe_ttm":0.30,"roa_ttm":0.12,"net_profit_margin_ttm":0.18},
                ],
            }]
        }
        result = build_history_trend_scores(snapshot, today=date(2026, 10, 14))["نماد"]
        self.assertIsNone(result["score"])
        self.assertEqual(result["matched_period_pair_count"], 0)

    def test_invalid_date_is_rejected(self):
        self.assertIsNone(parse_jalali_date("۱۴۰۵/۱۳/۳۱"))


if __name__ == "__main__":
    unittest.main()
