"""Unit tests for shared race math in race_constants.py."""

from __future__ import annotations

import unittest

from engineer.race_constants import (
    TIRE_COST_THRESHOLD_BUMP,
    clamp_nonneg_liters,
    first_lap_triangular_cost_exceeds,
    green_flag_fuel_laps_from_telemetry,
    triangular_payback_lap,
    wrap_lap_distance_delta,
)
from tests.fixtures import base_live_telemetry


class TriangularCostTests(unittest.TestCase):
    def test_first_lap_exceeds_threshold(self) -> None:
        lap = first_lap_triangular_cost_exceeds(75.0, 0.08, max_lap=80)
        self.assertIsNotNone(lap)
        assert lap is not None
        self.assertGreater(lap, 0)

    def test_payback_lap_matches_falloff(self) -> None:
        falloff = 0.1
        pit_loss = 40.0
        payback = triangular_payback_lap(pit_loss, falloff)
        self.assertIsNotNone(payback)
        assert payback is not None
        exceed = first_lap_triangular_cost_exceeds(pit_loss, falloff, max_lap=100)
        self.assertEqual(payback, exceed)

    def test_pre_race_bump_raises_tire_cap_threshold(self) -> None:
        base = first_lap_triangular_cost_exceeds(45.0, 0.08, max_lap=80)
        bumped = first_lap_triangular_cost_exceeds(
            45.0 + TIRE_COST_THRESHOLD_BUMP,
            0.08,
            max_lap=80,
        )
        self.assertIsNotNone(base)
        self.assertIsNotNone(bumped)
        assert base is not None and bumped is not None
        self.assertLess(base, bumped)


class ClampTests(unittest.TestCase):
    def test_clamp_nonneg_liters(self) -> None:
        self.assertEqual(clamp_nonneg_liters(-3.0), 0.0)
        self.assertEqual(clamp_nonneg_liters(2.5), 2.5)


class LapDistanceTests(unittest.TestCase):
    def test_wrap_delta_across_start_finish(self) -> None:
        # dist_b ahead of dist_a by 0.04 lap when crossing the line
        self.assertAlmostEqual(wrap_lap_distance_delta(0.02, 0.98), -0.04, places=3)


class GreenFlagFuelLapsTests(unittest.TestCase):
    def test_uses_ema_and_fuel_liters(self) -> None:
        tel = base_live_telemetry(m={"ful": 13.0, "fpe": 0.11})
        laps = green_flag_fuel_laps_from_telemetry(tel, fallback_l_per_lap=0.2)
        self.assertGreater(laps, 10.0)

    def test_falls_back_to_fpl_estimate(self) -> None:
        tel = base_live_telemetry(
            x={"fe": 0, "ll": 1},
            m={"ful": 3.0, "fpl": 0.264172},
        )
        laps = green_flag_fuel_laps_from_telemetry(tel, fallback_l_per_lap=0.2)
        self.assertAlmostEqual(laps, 3.0, places=1)


if __name__ == "__main__":
    unittest.main()
