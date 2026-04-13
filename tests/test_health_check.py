"""Unit tests for health_check.py"""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from health_check import (
    CheckResult,
    HealthReport,
    check_endpoint,
    format_report,
    run_health_checks,
)


# ---------------------------------------------------------------------------
# check_endpoint
# ---------------------------------------------------------------------------


class TestCheckEndpoint(unittest.TestCase):
    def _make_response(self, status: int):
        mock = MagicMock()
        mock.status = status
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    @patch("urllib.request.urlopen")
    def test_ok_on_200(self, mock_open):
        mock_open.return_value = self._make_response(200)
        result = check_endpoint("test", "https://example.com")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.name, "test")
        self.assertIsNotNone(result.latency_ms)
        self.assertGreaterEqual(result.latency_ms, 0)

    @patch("urllib.request.urlopen")
    def test_warn_on_non_200(self, mock_open):
        mock_open.return_value = self._make_response(503)
        result = check_endpoint("test", "https://example.com")
        self.assertEqual(result.status, "warn")
        self.assertIn("503", result.message)

    @patch("urllib.request.urlopen")
    def test_error_on_http_error(self, mock_open):
        mock_open.side_effect = urllib.error.HTTPError(
            url="https://example.com",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        result = check_endpoint("test", "https://example.com")
        self.assertEqual(result.status, "error")
        self.assertIn("500", result.message)

    @patch("urllib.request.urlopen")
    def test_error_on_url_error(self, mock_open):
        mock_open.side_effect = urllib.error.URLError(reason="Connection refused")
        result = check_endpoint("test", "https://example.com")
        self.assertEqual(result.status, "error")
        self.assertIn("Connection", result.message)

    @patch("urllib.request.urlopen")
    def test_error_on_unexpected_exception(self, mock_open):
        mock_open.side_effect = RuntimeError("boom")
        result = check_endpoint("test", "https://example.com")
        self.assertEqual(result.status, "error")
        self.assertIn("boom", result.message)

    @patch("urllib.request.urlopen")
    def test_latency_recorded(self, mock_open):
        mock_open.return_value = self._make_response(200)
        result = check_endpoint("test", "https://example.com")
        self.assertIsInstance(result.latency_ms, float)


# ---------------------------------------------------------------------------
# run_health_checks
# ---------------------------------------------------------------------------


class TestRunHealthChecks(unittest.TestCase):
    def _patch_check(self, statuses):
        """Return a side_effect function mapping endpoint index -> status."""
        status_iter = iter(statuses)

        def _side_effect(name, url, timeout=10):
            st = next(status_iter)
            return CheckResult(name=name, status=st, message="mock", latency_ms=1.0)

        return _side_effect

    @patch("health_check.check_endpoint")
    def test_healthy_when_all_ok(self, mock_check):
        mock_check.side_effect = self._patch_check(["ok", "ok"])
        report = run_health_checks(
            endpoints=[("a", "https://a.com"), ("b", "https://b.com")]
        )
        self.assertEqual(report.overall, "healthy")
        self.assertEqual(len(report.checks), 2)

    @patch("health_check.check_endpoint")
    def test_unhealthy_when_any_error(self, mock_check):
        mock_check.side_effect = self._patch_check(["ok", "error"])
        report = run_health_checks(
            endpoints=[("a", "https://a.com"), ("b", "https://b.com")]
        )
        self.assertEqual(report.overall, "unhealthy")

    @patch("health_check.check_endpoint")
    def test_degraded_when_warn_only(self, mock_check):
        mock_check.side_effect = self._patch_check(["ok", "warn"])
        report = run_health_checks(
            endpoints=[("a", "https://a.com"), ("b", "https://b.com")]
        )
        self.assertEqual(report.overall, "degraded")

    @patch("health_check.check_endpoint")
    def test_uses_default_endpoints_when_none_given(self, mock_check):
        mock_check.return_value = CheckResult(
            name="x", status="ok", message="HTTP 200", latency_ms=5.0
        )
        report = run_health_checks()
        self.assertGreater(len(report.checks), 0)

    @patch("health_check.check_endpoint")
    def test_timestamp_is_iso8601(self, mock_check):
        mock_check.return_value = CheckResult(
            name="x", status="ok", message="HTTP 200", latency_ms=5.0
        )
        report = run_health_checks(endpoints=[("x", "https://x.com")])
        # Should not raise
        from datetime import datetime
        datetime.fromisoformat(report.timestamp)


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport(unittest.TestCase):
    def _sample_report(self, overall="healthy"):
        return HealthReport(
            timestamp="2026-04-13T00:00:00+00:00",
            overall=overall,
            checks=[
                CheckResult(
                    name="tradingview_main",
                    status="ok",
                    message="HTTP 200",
                    latency_ms=42.0,
                ),
                CheckResult(
                    name="tradingview_data",
                    status="warn",
                    message="HTTP 302",
                    latency_ms=None,
                ),
            ],
        )

    # --- JSON ---

    def test_json_is_valid(self):
        output = format_report(self._sample_report(), "json")
        data = json.loads(output)  # must not raise
        self.assertIn("overall", data)
        self.assertIn("checks", data)
        self.assertIn("timestamp", data)

    def test_json_overall_value(self):
        data = json.loads(format_report(self._sample_report("unhealthy"), "json"))
        self.assertEqual(data["overall"], "unhealthy")

    def test_json_checks_count(self):
        data = json.loads(format_report(self._sample_report(), "json"))
        self.assertEqual(len(data["checks"]), 2)

    def test_json_null_latency_preserved(self):
        data = json.loads(format_report(self._sample_report(), "json"))
        latencies = [c["latency_ms"] for c in data["checks"]]
        self.assertIn(None, latencies)

    # --- text ---

    def test_text_contains_header(self):
        output = format_report(self._sample_report(), "text")
        self.assertIn("TradingView Health Check", output)

    def test_text_contains_overall_status(self):
        output = format_report(self._sample_report("degraded"), "text")
        self.assertIn("DEGRADED", output)

    def test_text_contains_check_names(self):
        output = format_report(self._sample_report(), "text")
        self.assertIn("tradingview_main", output)
        self.assertIn("tradingview_data", output)

    def test_text_ok_icon(self):
        output = format_report(self._sample_report(), "text")
        self.assertIn("[OK]", output)

    def test_text_warn_icon(self):
        output = format_report(self._sample_report(), "text")
        self.assertIn("[WARN]", output)

    def test_text_error_icon(self):
        report = HealthReport(
            timestamp="2026-04-13T00:00:00+00:00",
            overall="unhealthy",
            checks=[
                CheckResult(name="x", status="error", message="failed", latency_ms=1.0)
            ],
        )
        output = format_report(report, "text")
        self.assertIn("[ERROR]", output)


# ---------------------------------------------------------------------------
# main() / CLI
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    @patch("health_check.run_health_checks")
    def test_exits_zero_when_healthy(self, mock_run):
        from health_check import main

        mock_run.return_value = HealthReport(
            timestamp="2026-04-13T00:00:00+00:00",
            overall="healthy",
            checks=[CheckResult("x", "ok", "HTTP 200", 10.0)],
        )
        exit_code = main(["--exit-code"])
        self.assertEqual(exit_code, 0)

    @patch("health_check.run_health_checks")
    def test_exits_one_when_unhealthy_with_exit_code_flag(self, mock_run):
        from health_check import main

        mock_run.return_value = HealthReport(
            timestamp="2026-04-13T00:00:00+00:00",
            overall="unhealthy",
            checks=[CheckResult("x", "error", "failed", 10.0)],
        )
        exit_code = main(["--exit-code"])
        self.assertEqual(exit_code, 1)

    @patch("health_check.run_health_checks")
    def test_exits_zero_when_unhealthy_without_exit_code_flag(self, mock_run):
        from health_check import main

        mock_run.return_value = HealthReport(
            timestamp="2026-04-13T00:00:00+00:00",
            overall="unhealthy",
            checks=[CheckResult("x", "error", "failed", 10.0)],
        )
        exit_code = main([])
        self.assertEqual(exit_code, 0)

    @patch("health_check.run_health_checks")
    def test_json_format_flag(self, mock_run):
        """--format json should produce parseable JSON output."""
        import io
        import sys

        from health_check import main

        mock_run.return_value = HealthReport(
            timestamp="2026-04-13T00:00:00+00:00",
            overall="healthy",
            checks=[CheckResult("x", "ok", "HTTP 200", 5.0)],
        )
        captured = io.StringIO()
        sys_stdout = sys.stdout
        sys.stdout = captured
        try:
            main(["--format", "json"])
        finally:
            sys.stdout = sys_stdout

        data = json.loads(captured.getvalue())
        self.assertIn("overall", data)


if __name__ == "__main__":
    unittest.main()
