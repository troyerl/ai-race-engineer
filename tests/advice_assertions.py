"""Parse and assert engineer advice text (overlay / voice format)."""

from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from typing import Any

from strategy_engine import advice_call_line, parse_advice_why, parse_call_line, run_strategy


@dataclass(frozen=True)
class ParsedAdvice:
    call_line: str
    action: str
    timing: str
    service: str
    why: str
    forecast: str | None
    trigger: str | None
    conf: str | None
    raw: str
    lines: tuple[str, ...]
    is_dashboard: bool


def parse_advice(text: str) -> ParsedAdvice:
    """Split advice into call line, WHY, optional FORECAST, TRIGGER/CONF."""
    lines = tuple(line.strip() for line in (text or "").replace("\r\n", "\n").split("\n") if line.strip())
    is_dashboard = any("STRATEGY CALL" in ln.upper() for ln in lines)
    action, timing, service = parse_call_line(text)
    why = parse_advice_why(text)

    if is_dashboard:
        call_line = next((ln for ln in lines if "STRATEGY CALL" in ln.upper()), lines[0] if lines else "")
    else:
        call_line = lines[0] if lines else ""

    forecast: str | None = None
    trigger: str | None = None
    conf: str | None = None

    for line in lines:
        if line.startswith("FORECAST:"):
            forecast = line
        elif line.startswith("TRIGGER:"):
            m = re.search(r"TRIGGER:\s*(\S+)\s+CONF:\s*(\S+)", line)
            if m:
                trigger, conf = m.group(1), m.group(2)

    return ParsedAdvice(
        call_line=call_line,
        action=action,
        timing=timing or "THIS LAP",
        service=service,
        why=why,
        forecast=forecast,
        trigger=trigger,
        conf=conf,
        raw=text,
        lines=lines,
        is_dashboard=is_dashboard,
    )


def assert_advice(
    test: unittest.TestCase,
    text: str,
    *,
    action: str | None = None,
    timing: str = "THIS LAP",
    service: str | None = None,
    call_line: str | None = None,
    why_exact: str | None = None,
    why_contains: str | tuple[str, ...] | None = None,
    forecast_exact: str | None = None,
    forecast_contains: str | None = None,
    forecast_absent: bool = False,
    trigger: str | None = None,
    conf: str | None = None,
    line_count: int | None = None,
    dashboard: bool | None = True,
) -> ParsedAdvice:
    """Assert structured fields on formatted engineer advice."""
    parsed = parse_advice(text)
    test.assertTrue(parsed.call_line or parsed.action, "advice must have a call line or action")
    if dashboard is not None:
        test.assertEqual(parsed.is_dashboard, dashboard, "expected dashboard layout")

    if call_line is not None:
        test.assertEqual(parsed.call_line, call_line)
    if action is not None:
        test.assertEqual(parsed.action, action.upper())
    if service is not None:
        test.assertEqual(parsed.service, service.upper())
    test.assertEqual(parsed.timing, timing.upper())

    if why_exact is not None:
        test.assertEqual(parsed.why.upper(), why_exact.upper())
    if why_contains is not None:
        if isinstance(why_contains, str):
            why_contains = (why_contains,)
        why_u = parsed.why.upper()
        for fragment in why_contains:
            test.assertIn(fragment.upper(), why_u, f"WHY missing {fragment!r}: {parsed.why!r}")

    if forecast_absent:
        test.assertIsNone(parsed.forecast)
    if forecast_exact is not None:
        test.assertEqual(parsed.forecast, forecast_exact)
    if forecast_contains is not None:
        test.assertIsNotNone(parsed.forecast)
        assert parsed.forecast is not None
        test.assertIn(forecast_contains, parsed.forecast)

    if trigger is not None:
        test.assertEqual(parsed.trigger, trigger)
    if conf is not None:
        test.assertEqual(parsed.conf, conf)
    if line_count is not None:
        test.assertEqual(len(parsed.lines), line_count)

    return parsed


def advice_for_telemetry(telemetry: dict[str, Any], *, mode: str = "live") -> str:
    return run_strategy(telemetry, mode=mode)


def assert_telemetry_advice(
    test: unittest.TestCase,
    telemetry: dict[str, Any],
    **expectations: Any,
) -> ParsedAdvice:
    text = advice_for_telemetry(telemetry)
    return assert_advice(test, text, **expectations)
