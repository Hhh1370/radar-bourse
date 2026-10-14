import unittest

from scripts.codal_summary_fundamentals import build_summary_map


def report(symbol, months, end, published, profit, eps, revenue, growth=None, kind="financial_statement"):
    observations = []
    if profit is not None:
        observations.append({
            "metric": "net_profit", "value": abs(profit), "unit": "میلیون ریال",
            "period_months": months, "context": ("زیان خالص" if profit < 0 else "سود خالص"),
            "growth_pct_candidate": growth,
        })
    if eps is not None:
        observations.append({
            "metric": "eps", "value": eps, "unit": "ریال به‌ازای هر سهم",
            "period_months": months, "context": ("زیان هر سهم" if eps < 0 else "سود هر سهم"),
            "growth_pct_candidate": None,
        })
    if revenue is not None:
        observations.append({
            "metric": "revenue_or_sales", "value": revenue, "unit": "میلیون ریال",
            "period_months": months, "context": "درآمد فروش",
            "growth_pct_candidate": None,
        })
    return {
        "symbol": symbol, "normalized_symbol": symbol, "company": "شرکت نمونه",
        "classification": kind, "period_months": months, "period_end_jalali": end,
        "published_at": published, "title": f"صورت مالی {months} ماهه منتهی به {end}",
        "financial_metrics": {"observations": observations, "numeric_field_count": len(observations)},
        "official_url": "https://www.codal.ir/Reports/Decision.aspx?LetterSerial=123",
        "telegram_url": "https://t.me/Codal360_ir/123",
        "audit_status": "حسابرسی‌نشده",
    }


class CodalSummaryFundamentalsTests(unittest.TestCase):
    def test_scores_latest_report_and_compares_same_period_prior_year(self):
        old = report("نماد", 3, "1404/03/31", "2025-06-01", 100, 50, 500)
        current = report("نماد", 3, "1405/03/31", "2026-06-01", 150, 60, 700)
        result = build_summary_map([old, current])["نماد"]
        self.assertEqual(result["latest_report_end_jalali"], "1405/03/31")
        self.assertAlmostEqual(result["quarterly_growth_candidates_pct"]["net_profit_yoy_pct"], 50.0)
        self.assertAlmostEqual(result["quarterly_growth_candidates_pct"]["revenue_yoy_pct"], 40.0)
        self.assertIsInstance(result["score"], float)
        self.assertEqual(result["source_status"], "secondary_summary_not_reconciled_to_official_tables")
        self.assertFalse(result["has_comparable_prior_year_report"] is False)

    def test_loss_is_not_misread_as_positive_profit(self):
        loss = report("زیان‌دار", 3, "1405/03/31", "2026-06-01", -30, -15, 200)
        result = build_summary_map([loss])["زیاندار"]
        self.assertEqual(result["net_profit_million_rial_candidate"], -30.0)
        self.assertEqual(result["eps_rial_candidate"], -15.0)
        self.assertLess(result["score"], 50.0)

    def test_monthly_report_is_exposed_but_does_not_create_statement_score(self):
        monthly = report("ماهانه", 1, "1405/04/31", "2026-08-01", None, None, 120, kind="monthly_activity")
        result = build_summary_map([monthly])["ماهانه"]
        self.assertIsNone(result["score"])
        self.assertIsNotNone(result["monthly_activity"])
        self.assertIn("گزارش فعالیت ماهانه", result["monthly_activity"]["status"])

    def test_monthly_sales_growth_adjusts_the_provisional_score(self):
        old = report("نماد", 3, "1404/03/31", "2025-06-01", 100, 50, 500)
        current = report("نماد", 3, "1405/03/31", "2026-06-01", 150, 60, 700)
        monthly = report("نماد", 1, "1405/04/31", "2026-08-01", None, None, 120, kind="monthly_activity")
        monthly["financial_metrics"]["observations"][0]["growth_pct_candidate"] = 30.0

        without_monthly = build_summary_map([old, current])["نماد"]
        with_monthly = build_summary_map([old, current, monthly])["نماد"]

        self.assertEqual(with_monthly["monthly_sales_growth_pct_candidate"], 30.0)
        self.assertGreater(with_monthly["score"], without_monthly["score"])
        self.assertTrue(any("رشد فروش گزارش فعالیت ماهانه" in item for item in with_monthly["evidence"]))

    def test_monthly_sales_growth_compares_same_month_prior_year_when_units_match(self):
        old = report("نماد", 1, "1404/04/31", "2025-08-01", None, None, 500, kind="monthly_activity")
        current = report("نماد", 1, "1405/04/31", "2026-08-01", None, None, 650, kind="monthly_activity")
        old["financial_metrics"]["observations"][0]["unit"] = "میلیون ریال"
        current["financial_metrics"]["observations"][0]["unit"] = "میلیون ریال"
        result = build_summary_map([old, current])["نماد"]["monthly_activity"]
        self.assertEqual(result["sales_growth_pct_candidate"], 30.0)
        self.assertEqual(result["prior_year_same_month_sales_candidate"], 500.0)
        self.assertIn("همان ماه سال قبل", result["comparison_status"])

    def test_monthly_yoy_comparison_refuses_different_units(self):
        old = report("نماد", 1, "1404/04/31", "2025-08-01", None, None, 500, kind="monthly_activity")
        current = report("نماد", 1, "1405/04/31", "2026-08-01", None, None, 650, kind="monthly_activity")
        old["financial_metrics"]["observations"][0]["unit"] = "میلیون ریال"
        current["financial_metrics"]["observations"][0]["unit"] = "هزار ریال"
        result = build_summary_map([old, current])["نماد"]["monthly_activity"]
        self.assertIsNone(result["sales_growth_pct_candidate"])
        self.assertIsNone(result["prior_year_same_month_sales_candidate"])

    def test_ocr_only_numbers_remain_evidence_but_do_not_create_score(self):
        row = report("پوستر", 3, "1405/03/31", "2026-06-01", 900, 120, 5000)
        metrics = row["financial_metrics"]
        metrics["image_ocr_text"] = "سود خالص 900 میلیون ریال درآمد فروش 5000 میلیون ریال"
        for observation in metrics["observations"]:
            observation["context"] = metrics["image_ocr_text"]
            observation["evidence_confidence"] = "low_ocr_candidate"
        result = build_summary_map([row])["پوستر"]
        self.assertIsNone(result["score"])
        self.assertEqual(len(metrics["observations"]), 3)  # preserved for review

    def test_verified_text_candidate_survives_when_other_observation_is_ocr_only(self):
        row = report("ترکیبی", 3, "1405/03/31", "2026-06-01", 150, 60, 700)
        metrics = row["financial_metrics"]
        metrics["image_ocr_text"] = "سود خالص 999999 میلیون ریال"
        metrics["observations"].append({
            "metric": "net_profit", "value": 999999, "unit": "میلیون ریال",
            "period_months": 3, "context": metrics["image_ocr_text"],
            "evidence_confidence": "low_ocr_candidate",
        })
        result = build_summary_map([row])["ترکیبی"]
        self.assertEqual(result["net_profit_million_rial_candidate"], 150.0)
        self.assertGreaterEqual(result["score"], 0.0)

    def test_report_summary_without_numeric_values_has_no_score(self):
        row = report("نامشخص", 3, "1405/03/31", "2026-06-01", None, None, None)
        row["financial_metrics"]["observations"] = []
        row["financial_metrics"]["numeric_field_count"] = 0
        result = build_summary_map([row])["نامشخص"]
        self.assertIsNone(result["score"])
        self.assertEqual(result["source_confidence_factor"], 0.0)


if __name__ == "__main__":
    unittest.main()
