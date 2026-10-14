"""Tests for safe TSETMC host fallback and history freshness checks."""
import unittest
from datetime import date
from unittest.mock import patch

from scripts import build_fundamental_money_radar as radar


class TsetmcFallbackTests(unittest.TestCase):
    def setUp(self):
        self.old_primary_unavailable = radar.TSETMC_UNAVAILABLE
        self.old_primary_reason = radar.TSETMC_UNAVAILABLE_REASON
        self.old_mirror_unavailable = radar.TSETMC_MIRROR_UNAVAILABLE
        self.old_mirror_reason = radar.TSETMC_MIRROR_UNAVAILABLE_REASON
        self.old_legacy_unavailable = radar.TSETMC_LEGACY_UNAVAILABLE
        self.old_legacy_reason = radar.TSETMC_LEGACY_UNAVAILABLE_REASON
        radar.TSETMC_UNAVAILABLE = False
        radar.TSETMC_UNAVAILABLE_REASON = None
        radar.TSETMC_MIRROR_UNAVAILABLE = False
        radar.TSETMC_MIRROR_UNAVAILABLE_REASON = None
        radar.TSETMC_LEGACY_UNAVAILABLE = False
        radar.TSETMC_LEGACY_UNAVAILABLE_REASON = None

    def tearDown(self):
        radar.TSETMC_UNAVAILABLE = self.old_primary_unavailable
        radar.TSETMC_UNAVAILABLE_REASON = self.old_primary_reason
        radar.TSETMC_MIRROR_UNAVAILABLE = self.old_mirror_unavailable
        radar.TSETMC_MIRROR_UNAVAILABLE_REASON = self.old_mirror_reason
        radar.TSETMC_LEGACY_UNAVAILABLE = self.old_legacy_unavailable
        radar.TSETMC_LEGACY_UNAVAILABLE_REASON = self.old_legacy_reason

    def test_parse_legacy_csv_maps_real_and_legal_fields_without_inventing_missing_values(self):
        row = "14050721,10,2,5,1,100,20,50,10,1000,200,500,100;"
        result = radar.parse_legacy_client_type_csv(row)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date_raw"], "14050721")
        self.assertEqual(result[0]["buy_real_count"], 10.0)
        self.assertEqual(result[0]["sell_real_count"], 5.0)
        self.assertEqual(result[0]["buy_real_value"], 1000.0)
        self.assertEqual(result[0]["sell_real_value"], 500.0)
        self.assertEqual(result[0]["buy_legal_value"], 200.0)
        self.assertEqual(result[0]["sell_legal_value"], 100.0)

    def test_fetch_flow_uses_legacy_csv_when_both_json_hosts_fail(self):
        history = [
            {
                "date_raw": f"202610{day:02d}", "date_sort": f"202610{day:02d}",
                "buy_real_value": 1000.0, "sell_real_value": 700.0,
                "buy_legal_value": 500.0, "sell_legal_value": 600.0,
                "buy_real_volume": 10.0, "sell_real_volume": 8.0,
                "buy_legal_volume": 5.0, "sell_legal_volume": 6.0,
                "buy_real_count": 10.0, "sell_real_count": 8.0,
                "buy_legal_count": 2.0, "sell_legal_count": 3.0,
            }
            for day in range(1, 31)
        ]
        with patch.object(radar, "BRS_API_KEY", ""), \
             patch.object(radar, "tsetmc_get", side_effect=RuntimeError("DNS/timeout")), \
             patch.object(radar, "fetch_legacy_client_type_history", return_value=history) as legacy:
            result = radar.fetch_flow(ins_code="7503669593172728")
        self.assertEqual(len(result), 30)
        self.assertEqual(result[-1]["buy_real_value"], 1000.0)
        legacy.assert_called_once_with("7503669593172728")

    def test_mirror_is_used_before_primary_host(self):
        with patch.object(radar, "tsetmc_mirror_get", return_value={"source": "mirror"}) as mirror, \
             patch.object(radar, "request_json", side_effect=AssertionError("primary should not be called")):
            result = radar.tsetmc_get("ClientType/GetClientTypeHistory/12345678901234567")
        self.assertEqual(result, {"source": "mirror"})
        mirror.assert_called_once()

    def test_primary_host_is_fallback_if_mirror_fails(self):
        with patch.object(radar, "tsetmc_mirror_get", side_effect=RuntimeError("mirror timeout")), \
             patch.object(radar, "request_json", return_value={"source": "primary"}) as primary:
            result = radar.tsetmc_get("ClientType/GetClientTypeHistory/12345678901234567")
        self.assertEqual(result, {"source": "primary"})
        primary.assert_called_once()

    def test_paid_provider_failure_switches_to_free_mode_without_leaking_key(self):
        with patch.object(radar, "BRS_API_KEY", "test-secret"), \
             patch.object(radar, "probe_sources", side_effect=RuntimeError("HTTP 402 test-secret")):
            self.assertFalse(radar.provider_preflight_or_public_fallback())
            self.assertEqual(radar.BRS_API_KEY, "")
            self.assertFalse(radar.BRS_API_UNAVAILABLE)
            self.assertIsNone(radar.BRS_API_UNAVAILABLE_REASON)

    def test_successful_paid_provider_preflight_keeps_provider_enabled(self):
        with patch.object(radar, "BRS_API_KEY", "test-secret"), \
             patch.object(radar, "probe_sources", return_value=None):
            self.assertTrue(radar.provider_preflight_or_public_fallback())
            self.assertEqual(radar.BRS_API_KEY, "test-secret")

    def test_date_parser_supports_gregorian_and_jalali_client_dates(self):
        self.assertEqual(radar.parse_tsetmc_date("20261013"), date(2026, 10, 13))
        self.assertEqual(radar.parse_tsetmc_date("1405/07/21"), date(2026, 10, 13))

    def test_12_month_coverage_requires_long_span_and_recent_data(self):
        history = [{"date_raw": "20250901"}] + [{"date_raw": "20250901"} for _ in range(250)] + [{"date_raw": "20261013"}]
        coverage = radar.money_flow_history_coverage(history, now_date=date(2026, 10, 14))
        self.assertTrue(coverage["full_year_coverage"])
        self.assertEqual(coverage["latest_date_iso"], "2026-10-13")
        stale_history = list(history)
        stale_history[-1] = {"date_raw": "20260901"}
        stale = radar.money_flow_history_coverage(stale_history, now_date=date(2026, 10, 14))
        self.assertFalse(stale["full_year_coverage"])


if __name__ == "__main__":
    unittest.main()
