"""Unit tests for auto-alert orchestration (display trigger logic)."""

from __future__ import annotations

import unittest
from typing import Any

from auto_alert_engine import (
    AutoMonitorState,
    evaluate_auto_alert_tick,
    reset_auto_monitor_state,
)
from race_constants import INCIDENT_OT_ALERT_COUNT, POST_PIT_ALERT_MIN_STINT_LAPS
from strategy_engine import advice_call_line, incident_push_advice, run_strategy
from tests.advice_assertions import assert_advice, parse_advice
from tests.fixtures import base_live_telemetry, inside_window_telemetry


def _tick(
    state: AutoMonitorState,
    *,
    lap: int,
    is_caution: bool,
    telemetry: dict[str, Any],
    last_delivered: str = "",
) -> tuple[AutoMonitorState, bool, str, str, str | None]:
    """Run one monitor tick; return (state, delivered?, call_line, reason, full_advice)."""
    state, decision = evaluate_auto_alert_tick(
        state,
        lap=lap,
        is_caution=is_caution,
        telemetry=telemetry,
        last_delivered_call_line=last_delivered,
    )
    if decision.deliver and decision.call_line:
        last_delivered = decision.call_line
    return state, decision.deliver, last_delivered, decision.reason, decision.advice


def _pit_window_telemetry(**overrides: Any) -> dict[str, Any]:
    """Inside fuel window, past post-pit quiet, clean reentry — should call PIT NOW."""
    default_m = {
        "l": 21,
        "lp": 1,
        "sl": 21,
        "fl": 1.8,
        "mk": False,
        "pb": 2,
        "pw": True,
    }
    m_override = overrides.pop("m", None)
    if isinstance(m_override, dict):
        default_m.update(m_override)
    return inside_window_telemetry(
        m=default_m,
        fi={"rej": {"v": "CLEAN"}},
        **overrides,
    )


class AutoAlertTransitionTests(unittest.TestCase):
    def test_no_transition_on_repeated_poll_same_lap(self) -> None:
        state = AutoMonitorState(last_lap=10, last_caution=False)
        tel = _pit_window_telemetry()
        state, delivered, _, reason, _ = _tick(state, lap=10, is_caution=False, telemetry=tel)
        self.assertFalse(delivered)
        self.assertEqual(reason, "no_transition")

    def test_no_lap_skips(self) -> None:
        state, decision = evaluate_auto_alert_tick(
            AutoMonitorState(),
            lap=None,
            is_caution=False,
            telemetry=base_live_telemetry(),
        )
        self.assertFalse(decision.deliver)
        self.assertEqual(decision.reason, "no_lap")


class AutoAlertPitWindowTests(unittest.TestCase):
    def test_lap_advance_in_pit_window_delivers_pit_now(self) -> None:
        state = reset_auto_monitor_state()
        tel = _pit_window_telemetry()
        state, delivered, call_line, reason, advice = _tick(state, lap=21, is_caution=False, telemetry=tel)
        self.assertTrue(delivered, reason)
        expected = run_strategy(tel, mode="live")
        self.assertEqual(advice, expected)
        assert_advice(
            self,
            advice or "",
            action="PIT NOW",
            service="4 TIRES",
            why_contains="CLEAN REENTRY",
            trigger="TRACK",
            conf="M",
        )

    def test_green_run_outside_window_does_not_deliver(self) -> None:
        state = reset_auto_monitor_state()
        tel = base_live_telemetry(m={"fl": 12.0, "mk": True, "pb": 2, "fcq": "hi", "sl": 15})
        state, delivered, _, reason, _ = _tick(state, lap=11, is_caution=False, telemetry=tel)
        self.assertFalse(delivered)
        self.assertEqual(reason, "should_not_alert")

    def test_post_pit_quiet_suppresses_pit_window_alert(self) -> None:
        state = reset_auto_monitor_state()
        tel = inside_window_telemetry(
            m={"l": 12, "lp": 11, "sl": 1, "fl": 1.5, "mk": False},
            fi={"rej": {"v": "CLEAN"}},
        )
        state, delivered, _, reason, _ = _tick(state, lap=12, is_caution=False, telemetry=tel)
        self.assertFalse(delivered)
        self.assertEqual(reason, "should_not_alert")

    def test_fuel_critical_in_window_delivers(self) -> None:
        state = reset_auto_monitor_state()
        tel = inside_window_telemetry(m={"fl": 0.7, "mk": False, "sl": 1, "lp": 11, "l": 12})
        state, delivered, call_line, _, advice = _tick(state, lap=12, is_caution=False, telemetry=tel)
        self.assertTrue(delivered)
        assert_advice(
            self,
            advice or "",
            action="PIT NOW",
            service="4 TIRES",
            why_contains="FUEL CRITICAL",
            trigger="FUEL",
            conf="H",
        )
        self.assertEqual(call_line, "PIT NOW — THIS LAP — 4 TIRES")


class AutoAlertCautionTests(unittest.TestCase):
    def test_caution_start_delivers_even_when_stay_out(self) -> None:
        state = AutoMonitorState(last_lap=8, last_caution=False)
        tel = base_live_telemetry(
            m={"fl": 10.0, "mk": True, "fcq": "hi", "sl": 8},
            s={"flb": {"yel": True, "cau": True}},
            fi={"hd": {"pra": 0.2}, "cpi": {"ll": 8}},
        )
        state, delivered, call_line, reason, advice = _tick(state, lap=8, is_caution=True, telemetry=tel)
        self.assertTrue(delivered, reason)
        assert_advice(
            self,
            advice or "",
            action="STAY OUT",
            service="NONE",
            why_contains="STAY OUT FOR TRACK POSITION",
            forecast_exact="FORECAST: Paused under caution — re-run after green",
            trigger="FLAGS",
            conf="M",
        )
        self.assertEqual(call_line, "STAY OUT — THIS LAP — NONE")
        self.assertTrue(state.caution_announced)

    def test_caution_start_skips_duplicate_call_already_on_screen(self) -> None:
        state = AutoMonitorState(last_lap=8, last_caution=False)
        tel = base_live_telemetry(
            m={"fl": 10.0, "mk": True, "fcq": "hi"},
            s={"flb": {"yel": True, "cau": True}},
            fi={"hd": {"pra": 0.2}, "cpi": {"ll": 8}},
        )
        advice = run_strategy(tel, mode="live")
        prior_call = advice_call_line(advice)

        state, delivered, last, reason, _ = _tick(
            state,
            lap=8,
            is_caution=True,
            telemetry=tel,
            last_delivered=prior_call,
        )
        self.assertFalse(delivered)
        self.assertEqual(reason, "duplicate_caution_call")
        self.assertEqual(last, prior_call)
        self.assertTrue(state.caution_announced)

    def test_second_caution_after_green_can_deliver_again(self) -> None:
        state = AutoMonitorState(last_lap=10, last_caution=False)
        tel_yellow = base_live_telemetry(
            s={"flb": {"yel": True, "cau": True}},
            fi={"hd": {"pra": 0.2}, "cpi": {"ll": 8}},
        )
        state, delivered1, last, _, _ = _tick(state, lap=10, is_caution=True, telemetry=tel_yellow)
        self.assertTrue(delivered1)

        tel_green = base_live_telemetry(s={"flb": {"grn": True}})
        state, delivered_green, last, _, _ = _tick(state, lap=11, is_caution=False, telemetry=tel_green)
        self.assertFalse(delivered_green)
        self.assertFalse(state.caution_announced)

        state, delivered2, _, _, _ = _tick(state, lap=12, is_caution=True, telemetry=tel_yellow)
        self.assertTrue(delivered2)

    def test_caution_end_suppresses_forecast_only_alert(self) -> None:
        state = AutoMonitorState(last_lap=9, last_caution=True)
        tel = base_live_telemetry(
            m={"fl": 8.0, "mk": True, "fcq": "hi", "sl": 9},
            s={"flb": {"grn": True}},
        )
        state, delivered, _, reason, _ = _tick(state, lap=10, is_caution=False, telemetry=tel)
        self.assertFalse(delivered)
        self.assertEqual(reason, "should_not_alert")

    def test_lap_advance_under_sustained_caution_delivers_when_window_open(self) -> None:
        state = AutoMonitorState(last_lap=20, last_caution=True, caution_announced=True)
        tel = _pit_window_telemetry(s={"flb": {"yel": True, "cau": True}})
        state, delivered, _, reason, advice = _tick(state, lap=21, is_caution=True, telemetry=tel)
        self.assertTrue(delivered, reason)
        assert_advice(
            self,
            advice or "",
            action="STAY OUT",
            service="NONE",
            why_contains=("CAUTION", "TRACK POSITION"),
            forecast_exact="FORECAST: Paused under caution — re-run after green",
            trigger="FLAGS",
            conf="M",
        )


class AutoAlertDedupTests(unittest.TestCase):
    def test_same_call_not_repeated_on_next_lap(self) -> None:
        state = reset_auto_monitor_state()
        tel = _pit_window_telemetry()
        state, d1, last, _, _ = _tick(state, lap=21, is_caution=False, telemetry=tel)
        self.assertTrue(d1)

        tel_next = _pit_window_telemetry(m={"l": 22, "sl": 22})
        state, d2, _, reason, _ = _tick(state, lap=22, is_caution=False, telemetry=tel_next, last_delivered=last)
        self.assertFalse(d2)
        self.assertEqual(reason, "duplicate_call")

    def test_new_call_delivers_after_prior_different_call(self) -> None:
        state = reset_auto_monitor_state()
        tel_window = _pit_window_telemetry()
        state, _, last, _, _ = _tick(state, lap=21, is_caution=False, telemetry=tel_window)

        tel_incident = base_live_telemetry(m={"inc": {"ot": INCIDENT_OT_ALERT_COUNT, "hr": 8}})
        state, delivered, _, _, advice = _tick(
            state,
            lap=22,
            is_caution=False,
            telemetry=tel_incident,
            last_delivered=last,
        )
        self.assertTrue(delivered)
        expected = incident_push_advice(tel_incident)
        self.assertEqual(advice, expected)
        assert_advice(
            self,
            advice or "",
            action="STAY OUT",
            service="NONE",
            why_contains="BACK IT DOWN",
            trigger="TRACK",
            conf="M",
        )


class AutoAlertIncidentTests(unittest.TestCase):
    def test_incident_push_delivers_on_lap_change(self) -> None:
        state = reset_auto_monitor_state()
        tel = base_live_telemetry(
            m={"inc": {"ot": INCIDENT_OT_ALERT_COUNT, "hr": 10}, "fl": 15.0, "mk": True, "fcq": "hi"},
        )
        state, delivered, _, _, advice = _tick(state, lap=5, is_caution=False, telemetry=tel)
        self.assertTrue(delivered)
        expected = incident_push_advice(tel)
        self.assertEqual(advice, expected)
        assert_advice(
            self,
            advice or "",
            action="STAY OUT",
            service="NONE",
            why_contains="BACK IT DOWN",
            trigger="TRACK",
            conf="M",
        )


class AutoAlertForecastTests(unittest.TestCase):
    def test_forecast_stop_within_horizon_delivers(self) -> None:
        state = reset_auto_monitor_state()
        tel = base_live_telemetry(
            m={
                "l": 5,
                "lr": 25,
                "sl": 22,
                "lp": 1,
                "fl": 4.0,
                "mk": True,
                "pb": 2,
                "fcq": "hi",
                "fo": 0.15,
            },
            s={"tenv": {"tsc": 8, "dt": 0.0, "ref": 28.0, "cur": 28.0}},
            r={"ftl": 12, "pl": 45, "ts": 3, "fc": 20},
        )
        state, delivered, _, reason, _ = _tick(state, lap=5, is_caution=False, telemetry=tel)
        self.assertTrue(delivered, reason)


class AutoAlertStateMachineTests(unittest.TestCase):
    def test_reset_clears_monitor(self) -> None:
        state = AutoMonitorState(last_lap=99, last_caution=True, caution_announced=True)
        fresh = reset_auto_monitor_state()
        self.assertIsNone(fresh.last_lap)
        self.assertFalse(fresh.last_caution)
        self.assertFalse(fresh.caution_announced)

    def test_full_race_sequence_pit_then_quiet_then_window(self) -> None:
        """Simulate: fresh pit (quiet) → advance laps → pit window opens → alert fires once."""
        state = reset_auto_monitor_state()
        last = ""

        quiet_tel = inside_window_telemetry(
            m={"l": 12, "lp": 11, "sl": 1, "fl": 1.5, "mk": False},
            fi={"rej": {"v": "CLEAN"}},
        )
        state, d, last, _, _ = _tick(state, lap=12, is_caution=False, telemetry=quiet_tel, last_delivered=last)
        self.assertFalse(d)

        mid_tel = base_live_telemetry(
            m={"l": 15, "lp": 11, "sl": 4, "fl": 6.0, "mk": True, "pb": 2, "fcq": "hi"},
        )
        state, d, last, _, _ = _tick(state, lap=15, is_caution=False, telemetry=mid_tel, last_delivered=last)
        self.assertFalse(d)

        window_tel = inside_window_telemetry(
            m={"l": 21, "lp": 1, "sl": 21, "fl": 1.8, "mk": False},
            fi={"rej": {"v": "CLEAN"}},
        )
        state, d, last, _, advice = _tick(state, lap=21, is_caution=False, telemetry=window_tel, last_delivered=last)
        self.assertTrue(d)
        assert_advice(
            self,
            advice or "",
            action="PIT NOW",
            service="4 TIRES",
            why_contains="CLEAN REENTRY",
            trigger="TRACK",
            conf="M",
        )

        window_tel2 = inside_window_telemetry(
            m={"l": 22, "lp": 1, "sl": 22, "fl": 1.5, "mk": False},
            fi={"rej": {"v": "CLEAN"}},
        )
        state, d, _, reason, _ = _tick(state, lap=22, is_caution=False, telemetry=window_tel2, last_delivered=last)
        self.assertFalse(d)
        self.assertEqual(reason, "duplicate_call")


if __name__ == "__main__":
    unittest.main()
