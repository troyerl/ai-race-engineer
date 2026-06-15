"""Tests for Phase C: GWC fuel reserve, oval stagger, predictive tire falloff."""

from __future__ import annotations

import unittest

from engineer.strategy_engine import (
    _coast_to_checkered_ok,
    _fuel_critical_threshold,
    _gwc_overtime_prep,
    _tire_pit_worth_it,
    resolve_live_advice,
)
from engineer.tire_model import compute_stagger_from_corners, effective_tire_falloff_s
from engineer.track_db import is_oval_track
from tests.advice_assertions import parse_advice
from tests.fixtures import base_live_telemetry, inside_window_telemetry


def _oval_gwc_telemetry(**overrides):
    base = inside_window_telemetry(
        m={"fl": 1.85, "lr": 3, "l": 58, "sl": 20, "mk": False},
        s={"ty": "Race", "lt": 60, "ov": 1, "tn": "Bristol Motor Speedway"},
        r={"ts": 2, "pl": 42, "ftl": 22},
    )
    if overrides:
        for key, val in overrides.items():
            if isinstance(val, dict) and isinstance(base.get(key), dict):
                base[key].update(val)
            else:
                base[key] = val
    return base


class GwcFuelTests(unittest.TestCase):
    def test_gwc_prep_active_on_oval_late_race(self) -> None:
        tel = _oval_gwc_telemetry()
        self.assertTrue(_gwc_overtime_prep(tel))
        self.assertAlmostEqual(_fuel_critical_threshold(tel), 2.0)

    def test_gwc_not_active_on_road_course(self) -> None:
        tel = inside_window_telemetry(m={"lr": 3, "fl": 1.8}, s={"ov": 0, "tn": "Spa"})
        self.assertFalse(_gwc_overtime_prep(tel))
        self.assertAlmostEqual(_fuel_critical_threshold(tel), 1.0)

    def test_gwc_fuel_critical_at_one_point_eight_laps(self) -> None:
        tel = _oval_gwc_telemetry(m={"fl": 1.85, "lr": 2})
        parsed = parse_advice(resolve_live_advice(tel))
        self.assertEqual(parsed.action, "PIT NOW")
        self.assertIn("GWC FUEL RESERVE", parsed.why.upper())

    def test_gwc_coast_needs_extra_lap_on_white_flag(self) -> None:
        tel = _oval_gwc_telemetry(m={"l": 60, "lr": 1, "fl": 1.5, "mk": False})
        self.assertFalse(
            _coast_to_checkered_ok(tel, fuel_laps_left=1.5)
        )
        self.assertTrue(
            _coast_to_checkered_ok(tel, fuel_laps_left=2.1)
        )


class StaggerAndTireModelTests(unittest.TestCase):
    def test_compute_stagger(self) -> None:
        stg = compute_stagger_from_corners({"LF": 92.0, "LR": 90.0, "RF": 88.0, "RR": 86.0})
        self.assertIsNotNone(stg)
        assert stg is not None
        self.assertAlmostEqual(stg["la"], 91.0)
        self.assertAlmostEqual(stg["ra"], 87.0)
        self.assertAlmostEqual(stg["dg"], 4.0)

    def test_effective_falloff_increases_with_stagger(self) -> None:
        base = base_live_telemetry(m={"fo": 0.08})
        boosted = base_live_telemetry(m={"fo": 0.08, "stg": {"dg": 6.0}})
        self.assertGreater(effective_tire_falloff_s(boosted), effective_tire_falloff_s(base))

    def test_stagger_makes_tire_pit_worth_it_sooner(self) -> None:
        tel = base_live_telemetry(
            m={"fo": 0.55, "lr": 20},
            r={"pl": 8},
        )
        tel_stg = base_live_telemetry(
            m={"fo": 0.55, "lr": 20, "stg": {"dg": 7.0}},
            r={"pl": 8},
        )
        self.assertFalse(_tire_pit_worth_it(tel, laps_remaining=12))
        self.assertTrue(_tire_pit_worth_it(tel_stg, laps_remaining=12))

    def test_is_oval_track_heuristic(self) -> None:
        self.assertTrue(is_oval_track("Bristol Motor Speedway", track_length_miles=0.533))
        self.assertFalse(is_oval_track("Circuit de Spa-Francorchamps", track_length_miles=4.3))


if __name__ == "__main__":
    unittest.main()
