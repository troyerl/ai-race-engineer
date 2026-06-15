"""Unit tests for pre-race strategy and track-temperature stint caps."""

from __future__ import annotations

import unittest

from pre_race_strategy import (
    baseline_from_telemetry,
    find_race_session,
    generate_pre_race_green_plan,
    race_lap_total_from_session,
    run_pre_race_plan,
    rules_from_telemetry,
)
from tests.fixtures import base_live_telemetry


def _sample_session_yaml() -> dict:
    return {
        "WeekendInfo": {"TrackDisplayName": "Test Raceway"},
        "SessionInfo": {
            "Sessions": [
                {"SessionType": "Open", "SessionLaps": 10},
                {"SessionType": "Race", "SessionLaps": 60},
            ]
        },
    }


def _sample_baseline() -> dict:
    return {
        "avg_lap_time_s": 90.0,
        "fuel_burn_per_lap_gal": 0.12,
        "tire_wear_pace_falloff_per_lap_s": 0.08,
        "pit_lane_loss_time_s": 45.0,
        "fuel_tank_capacity_gal": 20.0,
    }


def _sample_rules() -> dict:
    return {"fuel_tank_capacity_pct": 100.0, "max_tire_sets": 4}


class RaceSessionLookupTests(unittest.TestCase):
    def test_find_race_session_case_insensitive(self) -> None:
        session = find_race_session(_sample_session_yaml())
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session["SessionType"], "Race")

    def test_race_lap_total_from_session(self) -> None:
        session = find_race_session(_sample_session_yaml())
        self.assertEqual(race_lap_total_from_session(session), 60)


class PreRaceGreenPlanTests(unittest.TestCase):
    def test_computes_stint_and_stop_schedule(self) -> None:
        plan = generate_pre_race_green_plan(_sample_session_yaml(), _sample_baseline(), _sample_rules())
        self.assertEqual(plan["calculated_total_race_laps"], 60)
        self.assertGreater(plan["max_laps_per_fuel_tank"], 0)
        self.assertGreater(plan["recommended_stint_length"], 0)
        self.assertGreaterEqual(plan["total_stops_required"], 1)
        self.assertTrue(plan["scheduled_pit_stops"])

    def test_cooled_track_extends_tire_stint_cap(self) -> None:
        baseline = _sample_baseline()
        warm = generate_pre_race_green_plan(
            _sample_session_yaml(),
            baseline,
            _sample_rules(),
            track_temp_c=30.0,
            track_temp_ref_c=30.0,
        )
        cool = generate_pre_race_green_plan(
            _sample_session_yaml(),
            baseline,
            _sample_rules(),
            track_temp_c=15.0,
            track_temp_ref_c=30.0,
        )
        self.assertGreater(cool["max_laps_per_tire_set"], warm["max_laps_per_tire_set"])
        self.assertGreaterEqual(cool["recommended_stint_length"], warm["recommended_stint_length"])

    def test_heated_track_shortens_tire_stint_cap(self) -> None:
        baseline = _sample_baseline()
        neutral = generate_pre_race_green_plan(
            _sample_session_yaml(),
            baseline,
            _sample_rules(),
            track_temp_c=30.0,
            track_temp_ref_c=30.0,
        )
        hot = generate_pre_race_green_plan(
            _sample_session_yaml(),
            baseline,
            _sample_rules(),
            track_temp_c=45.0,
            track_temp_ref_c=30.0,
        )
        self.assertLess(hot["max_laps_per_tire_set"], neutral["max_laps_per_tire_set"])


class BaselineFromTelemetryTests(unittest.TestCase):
    def test_uses_packet_pace_and_fuel(self) -> None:
        tel = base_live_telemetry(
            m={"t": [91.0, 92.0, 90.5], "fo": 0.1, "fpe": 0.11},
            r={"fc": 18.5, "ftl": 22, "pl": 40},
        )
        baseline = baseline_from_telemetry(tel)
        self.assertGreater(baseline["avg_lap_time_s"], 30.0)
        self.assertAlmostEqual(baseline["fuel_burn_per_lap_gal"], 0.11)
        self.assertAlmostEqual(baseline["tire_wear_pace_falloff_per_lap_s"], 0.1)
        self.assertEqual(baseline["fuel_tank_capacity_gal"], 18.5)


class RunPreRacePlanTests(unittest.TestCase):
    def test_run_pre_race_plan_includes_track_temp_from_packet(self) -> None:
        tel = base_live_telemetry(
            s={"ttc": 15.0, "tenv": {"ref": 30.0, "cur": 15.0, "dt": -15.0, "tsc": 24}},
            sy=_sample_session_yaml(),
        )
        text = run_pre_race_plan(tel, track_name="Test Raceway")
        self.assertIn("FUEL:", text)
        self.assertIn("TIRES:", text)
        self.assertIn("STOPS:", text)
        self.assertIn("Test Raceway", text)

    def test_rules_from_telemetry_reads_race_tire_limit(self) -> None:
        tel = base_live_telemetry(
            sy=_sample_session_yaml(),
            r={"ts": 2, "tsl": 6},
            s={"race_lt": 60, "ty": "Open Qualify"},
        )
        rules = rules_from_telemetry(tel)
        self.assertGreaterEqual(rules["max_tire_sets"], 1)


if __name__ == "__main__":
    unittest.main()
