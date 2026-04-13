#!/usr/bin/env python3
"""TradingView Health Check

Probes TradingView service endpoints, measures response latency, and
reports overall health status. Exits non-zero when unhealthy (use
--exit-code flag) so it can be wired into monitoring pipelines.

Usage:
    python health_check.py
    python health_check.py --format json
    python health_check.py --exit-code
"""

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    status: str          # "ok" | "warn" | "error"
    message: str
    latency_ms: Optional[float] = None


@dataclass
class HealthReport:
    timestamp: str
    overall: str         # "healthy" | "degraded" | "unhealthy"
    checks: List[CheckResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

#: Default set of TradingView endpoints to probe.
DEFAULT_ENDPOINTS: List[Tuple[str, str]] = [
    ("tradingview_main",   "https://www.tradingview.com"),
    ("tradingview_symbol", "https://symbol-search.tradingview.com/symbol_search/?text=AAPL&lang=en"),
    ("tradingview_data",   "https://data.tradingview.com"),
    ("tradingview_chart",  "https://www.tradingview.com/chart/"),
]


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def check_endpoint(
    name: str,
    url: str,
    timeout: int = 10,
) -> CheckResult:
    """Perform a single HTTP GET probe and return a CheckResult."""
    start = time.monotonic()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "tv-health-check/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            if resp.status == 200:
                return CheckResult(
                    name=name,
                    status="ok",
                    message=f"HTTP {resp.status}",
                    latency_ms=latency_ms,
                )
            return CheckResult(
                name=name,
                status="warn",
                message=f"HTTP {resp.status}",
                latency_ms=latency_ms,
            )
    except urllib.error.HTTPError as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return CheckResult(
            name=name,
            status="error",
            message=f"HTTP {exc.code}: {exc.reason}",
            latency_ms=latency_ms,
        )
    except urllib.error.URLError as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return CheckResult(
            name=name,
            status="error",
            message=f"Connection error: {exc.reason}",
            latency_ms=latency_ms,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        logger.debug("Unexpected error probing %s", url, exc_info=True)
        return CheckResult(
            name=name,
            status="error",
            message=f"Unexpected error: {exc}",
            latency_ms=latency_ms,
        )


def run_health_checks(
    endpoints: Optional[List[Tuple[str, str]]] = None,
    timeout: int = 10,
) -> HealthReport:
    """Run all endpoint probes and return a consolidated HealthReport."""
    if endpoints is None:
        endpoints = DEFAULT_ENDPOINTS

    checks = [check_endpoint(name, url, timeout) for name, url in endpoints]

    statuses = {c.status for c in checks}
    if statuses <= {"ok"}:
        overall = "healthy"
    elif "error" in statuses:
        overall = "unhealthy"
    else:
        overall = "degraded"

    return HealthReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        overall=overall,
        checks=checks,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_STATUS_ICON = {"ok": "[OK]   ", "warn": "[WARN] ", "error": "[ERROR]"}


def format_report(report: HealthReport, fmt: str = "text") -> str:
    """Render a HealthReport as text or JSON."""
    if fmt == "json":
        return json.dumps(
            {
                "timestamp": report.timestamp,
                "overall": report.overall,
                "checks": [asdict(c) for c in report.checks],
            },
            indent=2,
        )

    # --- plain text ---
    header_width = 80
    lines = [
        "TradingView Health Check",
        f"Timestamp : {report.timestamp}",
        f"Overall   : {report.overall.upper()}",
        "",
        f"{'Check':<35} {'Status':<9} {'Latency':>9}  Message",
        "-" * header_width,
    ]
    for c in report.checks:
        latency = f"{c.latency_ms:.0f} ms" if c.latency_ms is not None else "N/A"
        icon = _STATUS_ICON.get(c.status, c.status.upper())
        lines.append(f"{c.name:<35} {icon:<9} {latency:>9}  {c.message}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe TradingView endpoints and report health status."
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        metavar="SECONDS",
        help="Per-request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit with status 1 when overall health is 'unhealthy'",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    report = run_health_checks(timeout=args.timeout)
    print(format_report(report, args.format))

    if args.exit_code and report.overall == "unhealthy":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
