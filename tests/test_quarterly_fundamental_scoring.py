"""Tests for conservative same-period year-over-year quarterly fundamental scoring."""
import unittest

from scripts.build_fundamental_money_radar import derive_standalone_quarters, score_fundamentals


def statement(end_date, published, revenue, profit, operating_cash, unit="میلیون ریال", mode="standalone"):
    metrics = {
        "revenue": {"primary_value_candidate": revenue},
        "gross_profit": {"primary_value_candidate": profit * 2},
        "operating_profit": {"primary_value_candidate": profit * 1.5},
        "net_profit": {"primary_value_candidate": profit},
        "operating_cash_flow": {"primary_value_candidate": operating_cash},
        "total_assets": {"primary_value_candidate": 1000},
        "total_liabilities": {"primary_value_candidate": 400},
        "current_assets": {"primary_value_candidate": 300},
        "current_liabilities": {"primary_value_candidate": 200},
        "finance_cost": {"primary_value_candidate": 5},
    }
    return {
        "title": f"صورت مالی دوره ۳ ماهه منتهی به {end_date}",
        "date": published,
        "period_type": "3",
        "fiscal_year_end": end_date.replace("/", "-"),
        "report_unit_hint": unit,
        "consolidation_mode": mode,
        "financial_metrics": metrics,
        "ratios": {
            "net_margin_pct": profit / revenue * 100,
            "operating_margin_pct": profit * 1.5 / revenue * 100,
            "current_ratio": 1.5,
            "liabilities_to_assets_pct": 40,
            "operating_cash_flow_to_net_profit": operating_cash / profit if profit else None,
        },
    }

def cumulative_statement(end_date, published, revenue, profit, operating_cash, unit="میلیون ریال", mode="standalone", period_type="3"):
    row = statement(end_date, published, revenue, profit, operating_cash, unit, mode)
    row["period_type"] = period_type
    row["fiscal_year_end"] = end_date.replace("/", "-")
    row["financial_metrics"]["revenue"]["primary_value_candidate"] = revenue
    row["financial_metrics"]["net_profit"]["primary_value_candidate"] = profit
    row["financial_metrics"]["operating_cash_flow"]["primary_value_candidate"] = operating_cash
    return row



class QuarterlyFundamentalScoringTests(unittest.TestCase):
    def test_compares_matching_three_month_periods_and_units(self):
        current = statement("1405/03/31", "2026-06-01", 150, 24, 27)
        previous = statement("1404/03/31", "2025-06-02", 100, 20, 15)
        result = score_fundamentals([previous, current])
        growth = result["quarterly_growth_candidates_pct"]

        self.assertEqual(result["quarterly_growth_points"], 15)
        self.assertEqual(growth["revenue_yoy_pct"], 50.0)
        self.assertEqual(growth["net_profit_yoy_pct"], 20.0)
        self.assertEqual(growth["operating_cash_flow_yoy_pct"], 80.0)
        self.assertIn("مقایسهٔ مقدماتی دو صورت مالی ۳ماهه", result["quarterly_comparison_status"])

    def test_refuses_growth_comparison_when_units_differ(self):
        current = statement("1405/03/31", "2026-06-01", 150, 24, 27, unit="میلیون ریال")
        previous = statement("1404/03/31", "2025-06-02", 100, 20, 15, unit="هزار ریال")
        result = score_fundamentals([previous, current])

        self.assertEqual(result["quarterly_growth_points"], 0)
        self.assertEqual(result["quarterly_growth_candidates_pct"], {})
        self.assertIn("واحد", result["quarterly_comparison_status"])

    def test_refuses_growth_comparison_for_different_fiscal_period_end(self):
        current = statement("1405/03/31", "2026-06-01", 150, 24, 27)
        previous = statement("1404/06/31", "2025-09-02", 100, 20, 15)
        result = score_fundamentals([previous, current])

        self.assertEqual(result["quarterly_growth_points"], 0)
        self.assertEqual(result["quarterly_growth_candidates_pct"], {})
    def test_derives_standalone_second_quarter_from_matching_cumulative_reports(self):
        rows = [
            cumulative_statement("1405/03/31", "2026-06-02", 100, 20, 10, period_type="3"),
            cumulative_statement("1405/06/31", "2026-09-01", 230, 35, 18, period_type="6"),
            cumulative_statement("1404/03/31", "2025-06-03", 90, 18, 9, period_type="3"),
            cumulative_statement("1404/06/31", "2025-09-02", 190, 27, 15, period_type="6"),
        ]
        quarters = derive_standalone_quarters(rows)
        current_q2 = next(row for row in quarters if row.get("fiscal_year_end") == "1405-06-31")
        self.assertTrue(current_q2["quarterly_derived_from_cumulative"])
        self.assertEqual(current_q2["quarter_number"], 2)
        self.assertEqual(current_q2["financial_metrics"]["revenue"]["primary_value_candidate"], 130)
        self.assertEqual(current_q2["financial_metrics"]["net_profit"]["primary_value_candidate"], 15)
        self.assertEqual(current_q2["financial_metrics"]["operating_cash_flow"]["primary_value_candidate"], 8)
        result = score_fundamentals(rows)
        growth = result["quarterly_growth_candidates_pct"]
        self.assertEqual(growth["revenue_yoy_pct"], 30.0)
        self.assertAlmostEqual(growth["net_profit_yoy_pct"], 66.67, places=2)
        self.assertAlmostEqual(growth["operating_cash_flow_yoy_pct"], 33.33, places=2)
        self.assertIn("فصل مستقل", result["quarterly_comparison_status"])

    def test_does_not_derive_quarter_when_units_or_consolidation_mode_do_not_match(self):
        rows = [
            cumulative_statement("1405/03/31", "2026-06-02", 100, 20, 10, unit="میلیون ریال", period_type="3"),
            cumulative_statement("1405/06/31", "2026-09-01", 230, 35, 18, unit="میلیون ریال", period_type="6"),
            cumulative_statement("1404/03/31", "2025-06-03", 90, 18, 9, unit="هزار ریال", period_type="3"),
            cumulative_statement("1404/06/31", "2025-09-02", 190, 27, 15, unit="هزار ریال", period_type="6"),
        ]
        quarters = derive_standalone_quarters(rows)
        previous_q2 = next(row for row in quarters if row.get("fiscal_year_end") == "1404-06-31")
        self.assertNotIn("net_profit", previous_q2["financial_metrics"])
        mismatch = [
            cumulative_statement("1405/03/31", "2026-06-02", 100, 20, 10, mode="standalone", period_type="3"),
            cumulative_statement("1405/06/31", "2026-09-01", 230, 35, 18, mode="consolidated", period_type="6"),
        ]
        mismatch_quarters = derive_standalone_quarters(mismatch)
        self.assertFalse(any(row.get("quarterly_derived_from_cumulative") for row in mismatch_quarters if row.get("fiscal_year_end") == "1405-06-31"))



if __name__ == "__main__":
    unittest.main()
