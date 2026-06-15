"""Tactical offensive/defensive context and strategy advice tests."""

from __future__ import annotations

import unittest

from engineer.context_engine import (
    DriverContextTracker,
    StrategyMode,
    ThermalStressState,
    calculate_rolling_trend,
    compute_overtake_difficulty_index,
    evaluate_strategy_mode,
)
from engineer.race_constants import (
    BRAKE_ZONE_LAP_DIST_START,
    HIGH_LAT_SECTOR_END,
    HIGH_LAT_SECTOR_START,
    MODE_DEFENSIVE_GAP_BEHIND_MAX,
    MODE_OFFENSIVE_GAP_BEHIND_MIN,
    ODI_RIVAL_DEGRAD_MULT,
    RIVAL_DEGRAD_TREND_MIN,
    STEER_SAMPLE_LAT_G,
    THERMAL_GREASY_C,
)
from engineer.strategy_engine import (
    tactical_defensive_advice,
    tactical_undercut_advice,
)
from tests.fixtures import inside_window_telemetry


class MockIRacing:
    def __init__(self, state: dict | None = None) -> None:
        self.state = dict(state or {})

    def set(self, **kwargs) -> None:
        self.state.update(kwargs)

    def __call__(self, key: str, default=None):
        return self.state.get(key, default)


class StrategyModeTests(unittest.TestCase):
    def test_defensive_priority_when_pressure_close(self) -> None:
        self.assertEqual(evaluate_strategy_mode(0.6, 0.4), StrategyMode.DEFENSIVE)

    def test_offensive_when_strike_window_open(self) -> None:
        self.assertEqual(evaluate_strategy_mode(0.5, 1.0), StrategyMode.OFFENSIVE)

    def test_balanced_in_clear_air(self) -> None:
        self.assertEqual(evaluate_strategy_mode(2.0, 2.0), StrategyMode.BALANCED)

    def test_defensive_overrides_offensive_geometry(self) -> None:
        self.assertEqual(evaluate_strategy_mode(0.5, 0.3), StrategyMode.DEFENSIVE)


class RollingTrendTests(unittest.TestCase):
    def test_positive_trend_when_rival_slowing(self) -> None:
        trend = calculate_rolling_trend([90.0, 90.3, 90.6], window=3)
        self.assertAlmostEqual(trend, 0.3, places=2)

    def test_insufficient_history_returns_zero(self) -> None:
        self.assertEqual(calculate_rolling_trend([90.0], window=3), 0.0)


class OdiRivalDegradTests(unittest.TestCase):
    def test_rival_degrad_lowers_odi_when_wear_stable(self) -> None:
        base = compute_overtake_difficulty_index(
            pace_delta_ahead=0.5,
            pace_delta_behind=0.0,
            reentry_verdict="CLEAN",
            draft_streak=0,
        )
        adj = compute_overtake_difficulty_index(
            pace_delta_ahead=0.5,
            pace_delta_behind=0.0,
            reentry_verdict="CLEAN",
            draft_streak=0,
            rival_degrad=RIVAL_DEGRAD_TREND_MIN + 0.05,
            wear_stable=True,
        )
        self.assertAlmostEqual(adj["score"], base["score"] * ODI_RIVAL_DEGRAD_MULT)


class UndercutPredictorTests(unittest.TestCase):
    def test_undercut_flag_in_draft_with_margin(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing({"LapDistPct": 0.5, "LatAccel": 0.0, "Speed": 50.0})
        tracker.poll(
            ir,
            player_idx=0,
            lap=10,
            on_track=True,
            gap_ahead_s=0.5,
            gap_behind_s=MODE_OFFENSIVE_GAP_BEHIND_MIN,
            is_caution=False,
            ahead_lap_times=[92.0, 92.1, 92.2],
            best_lap_s=90.0,
            pace_delta_ahead=0.3,
            pit_loss_sec=45.0,
        )
        self.assertEqual(tracker.current_mode, StrategyMode.OFFENSIVE)
        self.assertTrue(tracker.fi_odi_undercut)

    def test_no_undercut_without_draft(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing({"LapDistPct": 0.5, "LatAccel": 0.0})
        tracker.poll(
            ir,
            player_idx=0,
            lap=10,
            on_track=True,
            gap_ahead_s=3.0,
            gap_behind_s=2.0,
            is_caution=False,
            ahead_lap_times=[92.0, 92.1, 92.2],
            best_lap_s=90.0,
            pace_delta_ahead=0.3,
        )
        self.assertEqual(tracker.current_mode, StrategyMode.BALANCED)
        self.assertFalse(tracker.fi_odi_undercut)

    def test_offensive_metrics_suppressed_in_balanced(self) -> None:
        tracker = DriverContextTracker()
        tracker.fi_odi_rival_degrad = 0.25
        ir = MockIRacing({"LapDistPct": 0.5, "LatAccel": 0.0})
        tracker.poll(
            ir,
            player_idx=0,
            lap=10,
            on_track=True,
            gap_ahead_s=2.0,
            gap_behind_s=2.0,
            is_caution=False,
            ahead_lap_times=[92.0, 92.1, 92.3],
        )
        self.assertEqual(tracker.fi_odi_rival_degrad, 0.0)


class ThermalStressTests(unittest.TestCase):
    def test_greasy_state_above_threshold(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing(
            {
                "LapDistPct": 0.5,
                "LatAccel": 0.0,
                "LFtempCM": THERMAL_GREASY_C + 1.0,
                "RFtempCM": 90.0,
            }
        )
        tracker.poll(
            ir,
            player_idx=0,
            lap=5,
            on_track=True,
            gap_ahead_s=2.0,
            gap_behind_s=MODE_DEFENSIVE_GAP_BEHIND_MAX - 0.1,
            is_caution=False,
        )
        self.assertEqual(tracker.current_mode, StrategyMode.DEFENSIVE)
        self.assertEqual(tracker.m_drv_therm_stress, ThermalStressState.GREASY)


class DivebombTests(unittest.TestCase):
    def test_divebomb_flag_in_brake_zone(self) -> None:
        tracker = DriverContextTracker()
        lap_dist = (BRAKE_ZONE_LAP_DIST_START + 0.02)
        ir = MockIRacing({"LapDistPct": lap_dist, "LatAccel": 0.0})
        tracker.poll(
            ir,
            player_idx=0,
            lap=8,
            on_track=True,
            gap_ahead_s=2.0,
            gap_behind_s=0.35,
            is_caution=False,
            session_time_elapsed=10.0,
        )
        tracker.poll(
            ir,
            player_idx=0,
            lap=8,
            on_track=True,
            gap_ahead_s=2.0,
            gap_behind_s=0.15,
            is_caution=False,
            session_time_elapsed=10.5,
        )
        self.assertEqual(tracker.current_mode, StrategyMode.DEFENSIVE)
        extras = tracker.build_packet_extras(
            track_temp_c=None,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=25,
            rivals={},
            you_pace={},
            reentry_verdict="CLEAN",
            current_lap=8,
            gap_behind_s=0.35,
        )
        self.assertTrue(extras["fi"].get("tac", {}).get("db"))


class TacticalAdviceTests(unittest.TestCase):
    def test_tactical_undercut_advice_from_packet(self) -> None:
        tel = inside_window_telemetry(
            m={"fl": 1.8, "sl": 10, "lr": 30, "lp": 1, "l": 20},
            r={"ftl": 25},
            rv={"ahead": {"pos": 4}},
            fi={
                "sm": {"m": 1, "n": "OFFENSIVE"},
                "rej": {"v": "CLEAN"},
                "odi": {"uc": True, "pa": 0.3, "pb": 0.0, "score": 0.3},
            },
        )
        advice = tactical_undercut_advice(tel)
        self.assertIsNotNone(advice)
        assert advice is not None
        self.assertIn("UNDERCUT ACTIVE", advice.upper())
        self.assertIn("OVERTAKE", advice)

    def test_tactical_undercut_suppressed_in_balanced_mode(self) -> None:
        tel = inside_window_telemetry(
            fi={
                "sm": {"m": 0, "n": "BALANCED"},
                "odi": {"uc": True},
            },
        )
        self.assertIsNone(tactical_undercut_advice(tel))

    def test_tactical_defensive_cool_tires(self) -> None:
        tel = inside_window_telemetry(
            m={"gb": 0.4, "drv": {"ts": 2, "tsn": "GREASY"}},
            fi={"sm": {"m": 2, "n": "DEFENSIVE"}, "tac": {"cool": True}},
        )
        advice = tactical_defensive_advice(tel)
        self.assertIsNotNone(advice)
        assert advice is not None
        self.assertIn("OVERHEATING", advice.upper())

    def test_tactical_defensive_suppressed_in_offensive_mode(self) -> None:
        tel = inside_window_telemetry(
            fi={"sm": {"m": 1, "n": "OFFENSIVE"}, "tac": {"cool": True}},
        )
        self.assertIsNone(tactical_defensive_advice(tel))


if __name__ == "__main__":
    unittest.main()
