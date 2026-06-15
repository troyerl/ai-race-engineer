"""Unit tests for context_engine.py (CALCULATIONS.md §16)."""

from __future__ import annotations

import unittest
from typing import Any

from context_engine import (
    DriverContextTracker,
    adjust_tire_stint_cap_for_track_temp,
    adjusted_live_tire_stint_cap,
    compute_overtake_difficulty_index,
)
from race_constants import (
    DRAFT_STREAK_ODI_PENALTY,
    HIGH_LAT_SECTOR_END,
    HIGH_LAT_SECTOR_START,
    INCIDENT_OT_ALERT_COUNT,
    INCIDENT_WINDOW_LAPS,
    LAT_ACCEL_OFFLINE_THRESHOLD,
    MARBLE_LAPS_REMAINING,
    ODI_PACK_PENALTY,
    STEER_SAMPLE_LAT_G,
    TRACK_TEMP_SHIFT_THRESHOLD_C,
)


class MockIRacing:
    """Minimal ir_get callable for DriverContextTracker.poll."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})

    def set(self, **kwargs: Any) -> None:
        self.state.update(kwargs)

    def __call__(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)


class AdjustTrackTempCapTests(unittest.TestCase):
    def test_missing_temp_returns_base_cap(self) -> None:
        cap, delta = adjust_tire_stint_cap_for_track_temp(20, track_temp_c=None, ref_temp_c=30.0)
        self.assertEqual(cap, 20)
        self.assertIsNone(delta)

    def test_small_delta_no_cap_change(self) -> None:
        cap, delta = adjust_tire_stint_cap_for_track_temp(20, track_temp_c=25.0, ref_temp_c=30.0)
        self.assertEqual(cap, 20)
        self.assertEqual(delta, -5.0)

    def test_cooled_track_extends_cap(self) -> None:
        cap, delta = adjust_tire_stint_cap_for_track_temp(
            20,
            track_temp_c=15.0,
            ref_temp_c=30.0,
        )
        self.assertGreater(cap, 20)
        self.assertLessEqual(delta, -TRACK_TEMP_SHIFT_THRESHOLD_C)

    def test_heated_track_shortens_cap(self) -> None:
        cap, delta = adjust_tire_stint_cap_for_track_temp(
            20,
            track_temp_c=45.0,
            ref_temp_c=30.0,
        )
        self.assertLess(cap, 20)
        self.assertGreaterEqual(delta, TRACK_TEMP_SHIFT_THRESHOLD_C)

    def test_heated_cap_never_below_one(self) -> None:
        cap, _ = adjust_tire_stint_cap_for_track_temp(3, track_temp_c=80.0, ref_temp_c=20.0)
        self.assertGreaterEqual(cap, 1)


class OvertakeDifficultyIndexTests(unittest.TestCase):
    def test_pace_ahead_only(self) -> None:
        odi = compute_overtake_difficulty_index(
            pace_delta_ahead=0.4,
            pace_delta_behind=0.0,
            reentry_verdict="CLEAN",
            draft_streak=0,
        )
        self.assertEqual(odi["score"], 0.4)

    def test_pack_penalty_applied(self) -> None:
        odi = compute_overtake_difficulty_index(
            pace_delta_ahead=0.6,
            pace_delta_behind=0.0,
            reentry_verdict="PACK",
            draft_streak=0,
        )
        self.assertAlmostEqual(odi["score"], 0.6 - ODI_PACK_PENALTY)

    def test_draft_streak_penalty_from_three_laps(self) -> None:
        odi = compute_overtake_difficulty_index(
            pace_delta_ahead=0.6,
            pace_delta_behind=0.1,
            reentry_verdict="CLEAN",
            draft_streak=3,
        )
        self.assertAlmostEqual(odi["score"], 0.6 - DRAFT_STREAK_ODI_PENALTY)
        self.assertEqual(odi["pb"], 0.1)


class DriverContextTrackerDraftTests(unittest.TestCase):
    def test_draft_lap_sets_fuel_ema_skip(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing({"LapDistPct": 0.5, "LatAccel": 0.0})

        tracker.poll(ir, player_idx=0, lap=1, on_track=True, gap_ahead_s=1.0, is_caution=False)
        tracker.poll(ir, player_idx=0, lap=2, on_track=True, gap_ahead_s=1.0, is_caution=False)

        self.assertTrue(tracker.consume_fuel_ema_skip())
        self.assertFalse(tracker.consume_fuel_ema_skip())

    def test_draft_streak_increments_on_consecutive_draft_laps(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing()

        for lap in range(1, 5):
            tracker.poll(ir, player_idx=0, lap=lap, on_track=True, gap_ahead_s=1.0, is_caution=False)

        extras = tracker.build_packet_extras(
            track_temp_c=None,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=20,
            rivals={},
            you_pace={},
            reentry_verdict=None,
            current_lap=4,
        )
        self.assertGreaterEqual(extras["fi"]["draft"]["streak"], 2)
        self.assertGreaterEqual(extras["fi"]["draft"]["ex"], 2)

    def test_no_draft_under_caution(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing()

        tracker.poll(ir, player_idx=0, lap=1, on_track=True, gap_ahead_s=1.0, is_caution=True)
        tracker.poll(ir, player_idx=0, lap=2, on_track=True, gap_ahead_s=1.0, is_caution=True)

        self.assertFalse(tracker.consume_fuel_ema_skip())


class DriverContextTrackerIncidentTests(unittest.TestCase):
    def test_off_track_increments_session_and_window(self) -> None:
        tracker = DriverContextTracker()
        tracker.set_incident_limit(17)
        ir = MockIRacing({"PlayerCarInComponentIncidentCount": [0, 0, 0]})

        tracker.poll(ir, player_idx=0, lap=5, on_track=True, gap_ahead_s=None, is_caution=False)
        ir.set(PlayerCarInComponentIncidentCount=[1, 0, 0])
        tracker.poll(ir, player_idx=0, lap=5, on_track=True, gap_ahead_s=None, is_caution=False)
        ir.set(PlayerCarInComponentIncidentCount=[2, 0, 0])
        tracker.poll(ir, player_idx=0, lap=6, on_track=True, gap_ahead_s=None, is_caution=False)

        extras = tracker.build_packet_extras(
            track_temp_c=None,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=20,
            rivals={},
            you_pace={},
            reentry_verdict=None,
            current_lap=6,
        )
        self.assertEqual(extras["m"]["inc"]["ot"], 2)
        self.assertEqual(extras["m"]["inc"]["tot"], 2)
        self.assertEqual(extras["m"]["inc"]["hr"], 15)
        self.assertTrue(tracker.incident_push_alert(6))

    def test_old_incidents_fall_out_of_window(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing({"PlayerCarInComponentIncidentCount": [0]})

        tracker.poll(ir, player_idx=0, lap=1, on_track=True, gap_ahead_s=None, is_caution=False)
        ir.set(PlayerCarInComponentIncidentCount=[1])
        tracker.poll(ir, player_idx=0, lap=1, on_track=True, gap_ahead_s=None, is_caution=False)

        tracker.poll(ir, player_idx=0, lap=1 + INCIDENT_WINDOW_LAPS, on_track=True, gap_ahead_s=None, is_caution=False)

        extras = tracker.build_packet_extras(
            track_temp_c=None,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=20,
            rivals={},
            you_pace={},
            reentry_verdict=None,
            current_lap=1 + INCIDENT_WINDOW_LAPS,
        )
        self.assertNotIn("inc", extras["m"])
        self.assertFalse(tracker.incident_push_alert(1 + INCIDENT_WINDOW_LAPS))


class DriverContextTrackerMarbleTests(unittest.TestCase):
    def test_lat_spike_sets_marble_counter(self) -> None:
        tracker = DriverContextTracker()
        lat = LAT_ACCEL_OFFLINE_THRESHOLD * 9.81
        ir = MockIRacing({"LapDistPct": 0.5, "LatAccel": lat, "PlayerTrackSurface": 1})

        tracker.poll(ir, player_idx=0, lap=3, on_track=True, gap_ahead_s=None, is_caution=False)
        tracker.poll(ir, player_idx=0, lap=4, on_track=True, gap_ahead_s=None, is_caution=False)

        extras = tracker.build_packet_extras(
            track_temp_c=None,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=20,
            rivals={},
            you_pace={},
            reentry_verdict=None,
            current_lap=4,
        )
        self.assertEqual(extras["m"]["mar"]["lr"], MARBLE_LAPS_REMAINING - 1)

    def test_off_track_surface_sets_marbles(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing({"LapDistPct": 0.1, "LatAccel": 0.0, "PlayerTrackSurface": 0})

        tracker.poll(ir, player_idx=0, lap=2, on_track=True, gap_ahead_s=None, is_caution=False)

        extras = tracker.build_packet_extras(
            track_temp_c=None,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=20,
            rivals={},
            you_pace={},
            reentry_verdict=None,
            current_lap=2,
        )
        self.assertEqual(extras["m"]["mar"]["lr"], MARBLE_LAPS_REMAINING)

    def test_reset_stint_clears_marbles(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing({"PlayerTrackSurface": 0, "LapDistPct": 0.1, "LatAccel": 0.0})
        tracker.poll(ir, player_idx=0, lap=2, on_track=True, gap_ahead_s=None, is_caution=False)
        tracker.reset_stint(track_temp_c=25.0)

        extras = tracker.build_packet_extras(
            track_temp_c=25.0,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=20,
            rivals={},
            you_pace={},
            reentry_verdict=None,
            current_lap=2,
        )
        self.assertNotIn("mar", extras["m"])


class DriverContextTrackerSteeringTests(unittest.TestCase):
    def _corner_ir(self) -> MockIRacing:
        lat = STEER_SAMPLE_LAT_G * 9.81
        return MockIRacing(
            {
                "LapDistPct": (HIGH_LAT_SECTOR_START + HIGH_LAT_SECTOR_END) / 2,
                "LatAccel": lat,
                "SteeringWheelAngle": 0.1,
            }
        )

    def test_steering_samples_produce_drv_packet(self) -> None:
        tracker = DriverContextTracker()
        ir = self._corner_ir()

        for lap in (1, 2):
            for offset in (0.0, 0.05, -0.03, 0.08):
                ir.set(SteeringWheelAngle=0.1 + offset)
                tracker.poll(ir, player_idx=0, lap=lap, on_track=True, gap_ahead_s=None, is_caution=False)

        extras = tracker.build_packet_extras(
            track_temp_c=None,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=20,
            rivals={},
            you_pace={},
            reentry_verdict=None,
            current_lap=2,
        )
        self.assertIn("drv", extras["m"])
        self.assertGreater(extras["m"]["drv"]["ssr"], 0.0)

    def test_steer_fatigue_forecast_penalty_scales_with_ratio(self) -> None:
        tracker = DriverContextTracker()
        tracker._steer_std_last = 0.20
        tracker._steer_baseline = 0.10
        self.assertEqual(tracker.steer_fatigue_forecast_penalty(), 2)

        tracker._steer_std_last = 0.15
        tracker._steer_baseline = 0.10
        self.assertEqual(tracker.steer_fatigue_forecast_penalty(), 1)


class DriverContextTrackerPacketExtrasTests(unittest.TestCase):
    def test_track_temp_env_and_wear_adjustment(self) -> None:
        tracker = DriverContextTracker()
        tracker.reset_stint(track_temp_c=30.0)

        extras = tracker.build_packet_extras(
            track_temp_c=18.0,
            tire_wear_rate_est={"lf": 0.01, "rf": 0.01},
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=20,
            rivals={
                "ahead": {"pace": {"avg_last3_s": 92.0}},
                "behind": {"pace": {"avg_last3_s": 89.0}},
            },
            you_pace={"avg_last3_s": 90.0},
            reentry_verdict="PACK",
            current_lap=10,
        )

        tenv = extras["s"]["tenv"]
        self.assertEqual(tenv["ref"], 30.0)
        self.assertEqual(tenv["cur"], 18.0)
        self.assertLess(tenv["dt"], -TRACK_TEMP_SHIFT_THRESHOLD_C)
        self.assertGreater(tenv["tsc"], 20)
        self.assertIn("twr_adj", tenv)

        odi = extras["fi"]["odi"]
        self.assertAlmostEqual(odi["pa"], -2.0)
        self.assertAlmostEqual(odi["pb"], -1.0)
        self.assertLess(odi["score"], odi["pa"])


class AdjustedLiveTireStintCapTests(unittest.TestCase):
    def test_returns_temp_adjusted_cap(self) -> None:
        base = adjusted_live_tire_stint_cap(
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            track_temp_c=15.0,
            ref_temp_c=30.0,
            max_lap=80,
        )
        hot = adjusted_live_tire_stint_cap(
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            track_temp_c=45.0,
            ref_temp_c=30.0,
            max_lap=80,
        )
        self.assertIsNotNone(base)
        self.assertIsNotNone(hot)
        assert base is not None and hot is not None
        self.assertGreater(base, hot)


if __name__ == "__main__":
    unittest.main()
