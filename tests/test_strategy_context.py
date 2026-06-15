"""Unit tests for §16 strategy overrides and live decision logic."""

from __future__ import annotations

import unittest
from typing import Any

from engineer.race_constants import (
    INCIDENT_OT_ALERT_COUNT,
    ODI_STAY_OUT_THRESHOLD,
    ODI_UNDERCUT_THRESHOLD,
    POST_PIT_ALERT_MIN_STINT_LAPS,
    STEER_STD_ELEVATED,
    STEER_STD_HIGH,
    TRACK_TEMP_SHIFT_THRESHOLD_C,
)
from engineer.strategy_engine import (
    _append_context_notes,
    _apply_context_directive_overrides,
    _can_run_to_finish,
    _fuel_context,
    _fuel_laps_for_pit_window,
    _post_pit_alert_quiet,
    _steer_forecast_penalty,
    _target_box_laps,
    _tire_pit_worth_it,
    advice_call_line,
    evaluate_and_forecast_strategy,
    format_strategy_dashboard,
    incident_push_advice,
    run_strategy,
    should_auto_alert,
)
from tests.advice_assertions import assert_telemetry_advice
from tests.fixtures import base_live_telemetry, inside_window_telemetry


class FuelContextTests(unittest.TestCase):
    def test_inside_window_when_cannot_make_to_end(self) -> None:
        tel = base_live_telemetry(m={"fl": 3.0, "mk": False, "pb": 2, "fcq": "hi"})
        laps, inside, critical = _fuel_context(tel)
        self.assertTrue(inside)
        self.assertFalse(critical)

    def test_inside_window_when_fuel_within_payback(self) -> None:
        tel = base_live_telemetry(m={"fl": 1.5, "mk": True, "pb": 2, "fcq": "hi"})
        _, inside, critical = _fuel_context(tel)
        self.assertTrue(inside)
        self.assertFalse(critical)

    def test_fuel_critical_at_one_lap(self) -> None:
        tel = base_live_telemetry(m={"fl": 0.9, "mk": False, "pb": 2, "fcq": "hi"})
        _, _, critical = _fuel_context(tel)
        self.assertTrue(critical)


class PostPitQuietTests(unittest.TestCase):
    def test_suppresses_alerts_early_in_stint(self) -> None:
        tel = base_live_telemetry(
            m={"l": 10, "lp": 9, "sl": 1, "fl": 5.0, "mk": True, "pb": 2, "fcq": "hi"},
            r={"ftl": 25, "pl": 45, "ts": 3, "fc": 20},
        )
        self.assertTrue(_post_pit_alert_quiet(tel))

    def test_allows_alerts_after_min_stint(self) -> None:
        tel = base_live_telemetry(
            m={
                "l": 30,
                "lp": 8,
                "sl": 22,
                "fl": 5.0,
                "mk": True,
                "pb": 2,
                "fcq": "hi",
            },
        )
        self.assertFalse(_post_pit_alert_quiet(tel))


class ContextDirectiveOverrideTests(unittest.TestCase):
    def _apply(
        self,
        tel: dict[str, Any],
        *,
        action: str = "PIT NOW",
        service: str = "4 TIRES",
        why: str = "Pit window open; clean reentry air window confirmed.",
        inside_window: bool = True,
        is_fuel_critical: bool = False,
        rej_v: str = "CLEAN",
        tire_sets: int = 3,
        laps_remain: int = 30,
    ) -> tuple[str, str, str]:
        return _apply_context_directive_overrides(
            tel,
            action,
            service,
            why,
            inside_window=inside_window,
            is_fuel_critical=is_fuel_critical,
            rej_v=rej_v,
            tire_sets=tire_sets,
            laps_remain=laps_remain,
        )

    def test_odi_pack_with_pace_stays_out(self) -> None:
        tel = inside_window_telemetry(
            fi={"rej": {"v": "PACK"}, "odi": {"score": ODI_STAY_OUT_THRESHOLD, "pa": 0.6, "pb": 0.0}},
        )
        action, service, why = self._apply(tel, rej_v="PACK")
        self.assertEqual(action, "STAY OUT")
        self.assertEqual(service, "NONE")
        self.assertIn("pass on track", why)

    def test_odi_high_pace_defers_clean_window_pit(self) -> None:
        tel = inside_window_telemetry(
            fi={"rej": {"v": "CLEAN"}, "odi": {"score": 0.7, "pa": 0.7, "pb": 0.0}},
        )
        action, _, why = self._apply(tel, rej_v="CLEAN")
        self.assertEqual(action, "STAY OUT")
        self.assertIn("pace advantage", why)

    def test_undercut_threat_triggers_pit(self) -> None:
        tel = inside_window_telemetry(
            m={"sl": 10, "lr": 30, "lp": 1, "l": 20},
            r={"ftl": 25},
            fi={
                "rej": {"v": "PACK"},
                "odi": {"score": ODI_UNDERCUT_THRESHOLD - 0.1, "pa": -0.5, "pb": 0.4},
            },
        )
        action, service, why = self._apply(
            tel,
            action="STAY OUT",
            service="NONE",
            why="Pit window open but reentry projects heavy pack traffic; delay one lap.",
            rej_v="PACK",
            laps_remain=30,
        )
        self.assertEqual(action, "PIT NOW")
        self.assertEqual(service, "4 TIRES")
        self.assertIn("Undercut", why)

    def test_undercut_threat_blocked_short_stint(self) -> None:
        tel = inside_window_telemetry(
            m={"sl": 3, "lr": 30, "lp": 1, "l": 20},
            r={"ftl": 25},
            fi={
                "rej": {"v": "PACK"},
                "odi": {"score": ODI_UNDERCUT_THRESHOLD - 0.1, "pa": -0.5, "pb": 0.4},
            },
        )
        action, _, _ = self._apply(
            tel,
            action="STAY OUT",
            service="NONE",
            why="Pit window open but reentry projects heavy pack traffic; delay one lap.",
            rej_v="PACK",
            laps_remain=30,
        )
        self.assertEqual(action, "STAY OUT")

    def test_incident_headroom_suppresses_pit(self) -> None:
        tel = inside_window_telemetry(m={"inc": {"ot": 1, "hr": 1, "tot": 16}})
        action, _, why = self._apply(tel)
        self.assertEqual(action, "STAY OUT")
        self.assertIn("Incident limit", why)

    def test_low_headroom_and_close_car_behind_stays_out(self) -> None:
        tel = inside_window_telemetry(m={"inc": {"ot": 0, "hr": 2, "tot": 15}, "gb": 0.5})
        action, _, why = self._apply(tel)
        self.assertEqual(action, "STAY OUT")
        self.assertIn("incident license", why.lower())

    def test_fuel_critical_not_overridden_by_incidents(self) -> None:
        tel = inside_window_telemetry(m={"fl": 0.5, "inc": {"ot": 0, "hr": 0, "tot": 17}})
        action, _, _ = self._apply(tel, is_fuel_critical=True)
        self.assertEqual(action, "PIT NOW")


class ContextNotesTests(unittest.TestCase):
    def test_marble_note_appended(self) -> None:
        tel = base_live_telemetry(m={"mar": {"lr": 2}})
        why = _append_context_notes("Maintaining stint.", tel)
        self.assertIn("Pickup on tires", why)

    def test_steering_note_when_loose(self) -> None:
        tel = base_live_telemetry(m={"drv": {"ss": 0.2, "ssr": STEER_STD_HIGH}})
        why = _append_context_notes("Maintaining stint.", tel)
        self.assertIn("Car is loose", why)

    def test_track_cooled_note(self) -> None:
        tel = base_live_telemetry(s={"tenv": {"dt": -12.0, "tsc": 24}})
        why = _append_context_notes("Maintaining stint.", tel)
        self.assertIn("Track cooled", why)

    def test_track_heated_note(self) -> None:
        tel = base_live_telemetry(s={"tenv": {"dt": TRACK_TEMP_SHIFT_THRESHOLD_C + 2, "tsc": 16}})
        why = _append_context_notes("Maintaining stint.", tel)
        self.assertIn("Track heated", why)


class SteerForecastPenaltyTests(unittest.TestCase):
    def test_penalty_tiers(self) -> None:
        low = base_live_telemetry(m={"drv": {"ssr": STEER_STD_ELEVATED}})
        high = base_live_telemetry(m={"drv": {"ssr": STEER_STD_HIGH}})
        none = base_live_telemetry()
        self.assertEqual(_steer_forecast_penalty(none), 0)
        self.assertEqual(_steer_forecast_penalty(low), 1)
        self.assertEqual(_steer_forecast_penalty(high), 2)


class IncidentPushAdviceTests(unittest.TestCase):
    def test_returns_advice_at_threshold(self) -> None:
        tel = base_live_telemetry(m={"inc": {"ot": INCIDENT_OT_ALERT_COUNT, "hr": 10}})
        advice = incident_push_advice(tel)
        self.assertIsNotNone(advice)
        assert advice is not None
        self.assertIn("BACK IT DOWN", advice)
        self.assertIn("TRIGGER: TRACK", advice)

    def test_none_below_threshold(self) -> None:
        tel = base_live_telemetry(m={"inc": {"ot": INCIDENT_OT_ALERT_COUNT - 1}})
        self.assertIsNone(incident_push_advice(tel))


class EvaluateStrategyIntegrationTests(unittest.TestCase):
    def test_clean_window_pits_by_default(self) -> None:
        tel = inside_window_telemetry(
            m={"l": 21, "lp": 1, "sl": 21},
            fi={"rej": {"v": "CLEAN"}},
        )
        assert_telemetry_advice(
            self,
            tel,
            action="PIT NOW",
            service="4 TIRES",
            why_contains="CLEAN REENTRY",
            trigger="TRACK",
            conf="M",
        )

    def test_pack_window_stays_out_without_odi_pace(self) -> None:
        tel = inside_window_telemetry(
            m={"sl": POST_PIT_ALERT_MIN_STINT_LAPS + 2},
            fi={"rej": {"v": "PACK"}, "odi": {"score": 0.0, "pa": -0.2, "pb": 0.0}},
        )
        assert_telemetry_advice(
            self,
            tel,
            action="STAY OUT",
            service="NONE",
            why_contains="AVOIDING HEAVY TRAFFIC PACK",
            trigger="TRACK",
            conf="M",
        )

    def test_odi_overrides_pack_to_stay_out_with_pace_message(self) -> None:
        tel = inside_window_telemetry(
            m={"sl": POST_PIT_ALERT_MIN_STINT_LAPS + 2},
            fi={
                "rej": {"v": "PACK"},
                "odi": {"score": ODI_STAY_OUT_THRESHOLD + 0.1, "pa": 0.6, "pb": 0.0},
            },
        )
        assert_telemetry_advice(
            self,
            tel,
            action="STAY OUT",
            service="NONE",
            why_contains="PACE ADVANTAGE",
            trigger="TRACK",
            conf="M",
        )

    def test_post_pit_quiet_holds_inside_window(self) -> None:
        tel = inside_window_telemetry(
            m={"l": 12, "lp": 11, "sl": 1, "fl": 1.5, "mk": False},
            fi={"rej": {"v": "CLEAN"}},
        )
        assert_telemetry_advice(
            self,
            tel,
            action="STAY OUT",
            service="NONE",
            why_contains="FRESH STINT",
            trigger="FUEL",
            conf="M",
        )

    def test_steering_penalty_advances_tire_forecast_stop(self) -> None:
        tel = inside_window_telemetry(
            m={
                "l": 5,
                "lr": 30,
                "sl": 14,
                "fl": 20.0,
                "mk": False,
                "fo": 4.0,
                "drv": {"ssr": STEER_STD_HIGH},
            },
            s={"tenv": {"tsc": 15, "dt": 0.0, "ref": 28.0, "cur": 28.0}, "flb": {}},
            fi={"rej": {"v": "CLEAN"}},
        )
        result = evaluate_and_forecast_strategy(tel)
        stops = result["green_flag_rest_of_race_forecast"]["projected_pit_schedule"]
        self.assertTrue(stops)
        first = stops[0]
        self.assertLessEqual(first["laps_from_now"], 3)

    def test_caution_pauses_forecast(self) -> None:
        tel = base_live_telemetry(s={"flb": {"yel": True, "cau": True}})
        assert_telemetry_advice(
            self,
            tel,
            forecast_exact="FORECAST: Paused under caution — re-run after green",
            trigger="FLAGS",
        )
        result = evaluate_and_forecast_strategy(tel)
        self.assertTrue(result["green_flag_rest_of_race_forecast"]["paused_for_caution"])


class ShouldAutoAlertTests(unittest.TestCase):
    def test_incident_push_triggers_alert(self) -> None:
        tel = base_live_telemetry(m={"inc": {"ot": INCIDENT_OT_ALERT_COUNT}})
        self.assertTrue(should_auto_alert(tel, ""))

    def test_post_pit_quiet_suppresses_fuel_window_alert(self) -> None:
        tel = inside_window_telemetry(
            m={"l": 12, "lp": 11, "sl": 1, "fl": 1.5, "mk": False},
        )
        advice = "PIT NOW — THIS LAP — 4 TIRES"
        self.assertFalse(should_auto_alert(tel, advice))

    def test_fuel_critical_always_alerts(self) -> None:
        tel = inside_window_telemetry(m={"fl": 0.8, "mk": False})
        advice = "PIT NOW — THIS LAP — 4 TIRES"
        self.assertTrue(should_auto_alert(tel, advice))

    def test_caution_started_alerts_under_yellow(self) -> None:
        tel = base_live_telemetry(s={"flb": {"yel": True, "cau": True}})
        self.assertTrue(
            should_auto_alert(tel, "STAY OUT — THIS LAP — NONE", caution_started=True)
        )

    def test_caution_ended_suppresses_non_critical(self) -> None:
        tel = base_live_telemetry(m={"fl": 5.0, "mk": True, "fcq": "hi"})
        self.assertFalse(
            should_auto_alert(tel, "STAY OUT — THIS LAP — NONE", caution_ended=True)
        )


class PitWindowFuelTests(unittest.TestCase):
    def test_green_after_caution_floors_inflated_fl(self) -> None:
        """floor(19.5)=19 keeps target box stable vs caution round(20.5)=20 when a stop is required."""
        tel = base_live_telemetry(
            m={"l": 4, "lp": 4, "fl": 8.5, "fcq": "hi", "mk": False, "pb": 2, "lr": 10},
            r={"ftl": 22, "pl": 46, "ts": 3, "fc": 20.0},
            s={"flb": {}, "lt": 14},
        )
        window_fuel = _fuel_laps_for_pit_window(tel, fuel_laps_left=8.5)
        self.assertEqual(window_fuel, 8.0)
        box = _target_box_laps(tel, fuel_laps_left=8.5, payback_laps=2.0)
        self.assertIsNotNone(box)
        assert box is not None
        self.assertEqual(box, (10, 12))

    def test_under_caution_keeps_live_fuel_reading(self) -> None:
        tel = base_live_telemetry(
            m={"l": 3, "lp": 3, "fl": 20.5, "fcq": "hi", "pb": 2},
            r={"ftl": 22},
            s={"flb": {"yel": True, "cau": True}},
        )
        window_fuel = _fuel_laps_for_pit_window(tel, fuel_laps_left=20.5)
        self.assertEqual(window_fuel, 20.5)

    def test_green_ema_path_when_ful_present(self) -> None:
        tel = base_live_telemetry(
            m={
                "l": 12,
                "lp": 12,
                "fl": 8.0,
                "ful": 8.0,
                "fpe": 0.264,
                "fcq": "hi",
                "pb": 2,
                "mk": False,
                "lr": 12,
            },
            r={"ftl": 22},
            s={"lt": 24},
        )
        window_fuel = _fuel_laps_for_pit_window(tel, fuel_laps_left=8.0)
        self.assertAlmostEqual(window_fuel, 8.0, delta=1.0)


class RunToFinishTests(unittest.TestCase):
    def test_can_run_to_finish_suppresses_pit_window(self) -> None:
        tel = base_live_telemetry(
            m={"l": 4, "lr": 16, "fl": 18.0, "mk": True, "fcq": "hi", "pb": 2, "fo": 0.3},
            r={"ftl": 22, "pl": 46},
            s={"lt": 20},
        )
        self.assertTrue(_can_run_to_finish(tel))
        self.assertEqual(_fuel_laps_for_pit_window(tel, fuel_laps_left=18.0), 0.0)
        self.assertIsNone(_target_box_laps(tel, fuel_laps_left=18.0, payback_laps=2.0))
        result = evaluate_and_forecast_strategy(tel)
        self.assertEqual(result["green_flag_rest_of_race_forecast"]["projected_pit_schedule"], [])

    def test_forecast_skips_tire_stop_when_pit_loss_exceeds_wear(self) -> None:
        tel = base_live_telemetry(
            m={"l": 4, "lr": 16, "fl": 18.0, "mk": True, "fcq": "hi", "sl": 4, "fo": 0.2},
            r={"ftl": 22, "pl": 46},
            s={"lt": 20, "tenv": {"tsc": 8}},
        )
        self.assertFalse(_tire_pit_worth_it(tel, laps_remaining=16))
        result = evaluate_and_forecast_strategy(tel)
        self.assertEqual(result["green_flag_rest_of_race_forecast"]["projected_pit_schedule"], [])

    def test_dashboard_shows_checkered_when_run_to_finish(self) -> None:
        tel = base_live_telemetry(
            m={"l": 4, "lr": 16, "fl": 18.0, "mk": True, "fcq": "hi", "pb": 2},
            s={"lt": 20},
        )
        result = evaluate_and_forecast_strategy(tel)
        dash = format_strategy_dashboard(result, tel)
        self.assertIn("TARGET PIT: CHECKERED", dash)
        self.assertNotIn("Target Box:", dash)


if __name__ == "__main__":
    unittest.main()
