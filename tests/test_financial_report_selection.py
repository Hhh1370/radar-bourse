"""Tests to prevent quarterly statements being crowded out by annual report selection."""
import unittest

from scripts.build_fundamental_money_radar import choose_financial_reports, report_period_key


def report(title, date, key):
    return {
        "title": title,
        "date": date,
        "url": f"https://www.codal.ir/Reports/Decision.aspx?LetterSerial={key}",
        "has_html": True,
        "has_excel": False,
        "tracing_no": key,
    }


class FinancialReportSelectionTests(unittest.TestCase):
    def test_keeps_latest_three_six_nine_months_and_two_annual_statements(self):
        rows = [
            report("صورت مالی دوره ۳ ماهه منتهی به ۱۴۰۵/۰۳/۳۱", "2026-05-21", "q3"),
            report("صورت مالی دوره ۳ ماهه منتهی به ۱۴۰۴/۰۳/۳۱", "2025-05-22", "q3old"),
            report("صورت مالی دوره ۳ ماهه منتهی به ۱۴۰۳/۰۳/۳۱", "2024-05-20", "q3older"),
            report("صورت مالی دوره ۶ ماهه منتهی به ۱۴۰۵/۰۶/۳۱", "2026-08-25", "q6"),
            report("صورت مالی دوره ۹ ماهه منتهی به ۱۴۰۴/۰۹/۳۰", "2025-11-25", "q9"),
            report("صورت مالی سال مالی منتهی به ۱۴۰۴/۱۲/۲۹", "2026-04-12", "annual1"),
            report("صورت مالی سال مالی منتهی به ۱۴۰۳/۱۲/۲۹", "2025-04-15", "annual2"),
        ]
        selected = choose_financial_reports(rows)
        periods = {report_period_key(item) for item in selected}
        keys = {item["tracing_no"] for item in selected}

        self.assertEqual(len(selected), 6)
        self.assertEqual(periods, {"3", "6", "9", "annual"})
        self.assertIn("q3", keys)
        self.assertIn("q3old", keys)
        self.assertIn("q6", keys)
        self.assertIn("q9", keys)
        self.assertIn("annual1", keys)
        self.assertIn("annual2", keys)
        self.assertNotIn("q3older", keys)

    def test_quarterly_report_is_not_dropped_when_two_annuals_exist(self):
        rows = [
            report("صورت مالی سال مالی منتهی به ۱۴۰۴/۱۲/۲۹", "2026-04-12", "annual1"),
            report("صورت مالی سال مالی منتهی به ۱۴۰۳/۱۲/۲۹", "2025-04-15", "annual2"),
            report("صورت مالی دوره ۳ ماهه منتهی به ۱۴۰۵/۰۳/۳۱", "2026-05-21", "q3"),
        ]
        selected = choose_financial_reports(rows)
        self.assertIn("q3", {item["tracing_no"] for item in selected})


if __name__ == "__main__":
    unittest.main()
