"""Offensive undercut runway guards (§16.4.2 / §10.2)."""

from __future__ import annotations

import unittest

from strategy_engine import (
    OFFENSIVE_UNDERCUT_WHY,
    _undercut_opportunity,
    _undercut_runway_ok,
    evaluate_and_forecast_strategy,
)
from tests.fixtures import inside_window_telemetry


def _long_run_telemetry(**overrides):
    return inside_window_telemetry(
        m={
            "fl": 1.8,
            "sl": 10,
            "lr": 30,
            "lp": 1,
            "l": 20,
            "ga": 0.7,
            **(overrides.pop("m", None) or {}),
        },
        r={"ftl": 25, "pl": 45, "ts": 3, **(overrides.pop("r", None) or {})},
        fi={
            "rej": {"v": "CLEAN", "n": 0},
            "odi": {"pa": 0.35, "pb": 0.0, "score": 0.35},
            **(overrides.pop("fi", None) or {}),
        },
        **overrides,
    )


class TestUndercutRunwayGuard(unittest.TestCase):
    def test_runway_ok_on_extended_stint(self) -> None:
        tel = _long_run_telemetry()
        self.assertTrue(_undercut_runway_ok(tel, laps_remain=30))

    def test_runway_blocked_short_stint(self) -> None:
        tel = _long_run_telemetry(m={"sl": 3})
        self.assertFalse(_undercut_runway_ok(tel, laps_remain=30))

    def test_runway_blocked_near_race_end(self) -> None:
        tel = _long_run_telemetry(m={"lr": 5})
        self.assertFalse(_undercut_runway_ok(tel, laps_remain=5))

    def test_runway_blocked_fuel_stint_tail(self) -> None:
        tel = _long_run_telemetry(m={"sl": 20}, r={"ftl": 25})
        self.assertFalse(_undercut_runway_ok(tel, laps_remain=30))


class TestOffensiveUndercutOpportunity(unittest.TestCase):
    def test_opportunity_when_all_gates_pass(self) -> None:
        tel = _long_run_telemetry()
        self.assertTrue(
            _undercut_opportunity(
                tel,
                inside_window=True,
                rej_v="CLEAN",
                laps_remain=30,
                is_fuel_critical=False,
            )
        )

    def test_opportunity_blocked_without_gap_ahead(self) -> None:
        tel = _long_run_telemetry(m={"ga": 2.5})
        self.assertFalse(
            _undercut_opportunity(
                tel,
                inside_window=True,
                rej_v="CLEAN",
                laps_remain=30,
                is_fuel_critical=False,
            )
        )

    def test_immediate_decision_pit_now_offensive_undercut(self) -> None:
        tel = _long_run_telemetry()
        result = evaluate_and_forecast_strategy(tel)
        imm = result["immediate_directive"]
        self.assertEqual(imm["ACTION"], "PIT NOW")
        self.assertEqual(imm["WHY"], OFFENSIVE_UNDERCUT_WHY)

    def test_no_offensive_undercut_on_short_stint(self) -> None:
        tel = _long_run_telemetry(m={"sl": 3})
        result = evaluate_and_forecast_strategy(tel)
        imm = result["immediate_directive"]
        self.assertNotEqual(imm["WHY"], OFFENSIVE_UNDERCUT_WHY)


if __name__ == "__main__":
    unittest.main()
