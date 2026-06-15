"""Assert exact engineer advice text for every live strategy scenario (dashboard layout)."""

from __future__ import annotations

import unittest

from engineer.race_constants import (
    INCIDENT_OT_ALERT_COUNT,
    ODI_STAY_OUT_THRESHOLD,
    STEER_STD_HIGH,
    TRACK_TEMP_SHIFT_THRESHOLD_C,
)
from engineer.strategy_engine import advice_call_line, incident_push_advice, parse_call_line, run_strategy
from tests.advice_assertions import assert_advice, assert_telemetry_advice, parse_advice
from tests.fixtures import base_live_telemetry, inside_window_telemetry


class GreenFlagAdviceTextTests(unittest.TestCase):
    def test_maintaining_stint(self) -> None:
        assert_telemetry_advice(
            self,
            base_live_telemetry(),
            action="STAY OUT",
            service="NONE",
            why_contains="MAINTAIN STINT PACE",
            forecast_contains="FORECAST:",
            trigger="FUEL",
            conf="M",
        )

    def test_pit_window_clean_reentry(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(m={"l": 21, "lp": 1, "sl": 21}, fi={"rej": {"v": "CLEAN"}}),
            action="PIT NOW",
            service="4 TIRES",
            why_contains="CLEAN REENTRY",
            forecast_contains="green-flag stop",
            trigger="TRACK",
            conf="M",
        )

    def test_pack_traffic_delay(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"sl": 10},
                fi={"rej": {"v": "PACK"}, "odi": {"score": 0.0, "pa": -0.2, "pb": 0.0}},
            ),
            action="STAY OUT",
            service="NONE",
            why_contains="AVOIDING HEAVY TRAFFIC PACK",
            trigger="TRACK",
            conf="M",
        )

    def test_post_pit_quiet_holds(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"l": 12, "lp": 11, "sl": 1, "fl": 1.5, "mk": False},
                fi={"rej": {"v": "CLEAN"}},
            ),
            action="STAY OUT",
            service="NONE",
            why_contains="FRESH STINT",
            trigger="FUEL",
            conf="M",
        )

    def test_fuel_critical(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(m={"fl": 0.7, "mk": False, "sl": 10, "l": 20}),
            action="PIT NOW",
            service="4 TIRES",
            why_contains="FUEL CRITICAL",
            trigger="FUEL",
            conf="H",
        )


class CautionAdviceTextTests(unittest.TestCase):
    def test_caution_stay_out_track_position(self) -> None:
        assert_telemetry_advice(
            self,
            base_live_telemetry(
                s={"flb": {"yel": True, "cau": True}},
                fi={"hd": {"pra": 0.2}, "cpi": {"ll": 8}},
            ),
            action="STAY OUT",
            service="NONE",
            why_contains="STAY OUT FOR TRACK POSITION",
            forecast_exact="FORECAST: Paused under caution — re-run after green",
            trigger="FLAGS",
            conf="M",
        )

    def test_caution_pit_field_boxing(self) -> None:
        assert_telemetry_advice(
            self,
            base_live_telemetry(
                s={"flb": {"yel": True, "cau": True}},
                fi={"hd": {"pra": 0.7}, "cpi": {"ll": 2}},
            ),
            action="PIT",
            service="4 TIRES",
            why_contains="FIELD BOXING",
            forecast_exact="FORECAST: Paused under caution — re-run after green",
            trigger="FLAGS",
            conf="M",
        )

    def test_caution_fuel_critical(self) -> None:
        assert_telemetry_advice(
            self,
            base_live_telemetry(
                m={"fl": 0.5, "mk": False, "fcq": "hi"},
                s={"flb": {"yel": True, "cau": True}},
            ),
            action="PIT NOW",
            service="4 TIRES",
            why_contains="FUEL CRITICAL UNDER CAUTION",
            forecast_exact="FORECAST: Paused under caution — re-run after green",
            trigger="FLAGS",
            conf="M",
        )


class RaceControlAdviceTextTests(unittest.TestCase):
    def test_pit_lane_closed(self) -> None:
        assert_telemetry_advice(
            self,
            base_live_telemetry(s={"flb": {"pcl": True}}),
            action="STAY OUT",
            service="NONE",
            why_contains="PIT LANE CLOSED",
            trigger="FLAGS",
            conf="H",
        )

    def test_on_pit_road(self) -> None:
        assert_telemetry_advice(
            self,
            base_live_telemetry(m={"pr": True}),
            action="STAY OUT",
            service="NONE",
            why_contains="ON PIT ROAD",
            trigger="FUEL",
            conf="M",
        )

    def test_mandatory_repair_in_stall(self) -> None:
        assert_telemetry_advice(
            self,
            base_live_telemetry(m={"pr": False, "ps": True, "rr": 12.0}),
            action="STAY OUT",
            service="REPAIR",
            why_contains="MANDATORY REPAIRS",
            trigger="REPAIR",
            conf="H",
        )


class Section16AdviceTextTests(unittest.TestCase):
    def test_odi_pack_pace_stay_out(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"sl": 10},
                fi={
                    "rej": {"v": "PACK"},
                    "odi": {"score": ODI_STAY_OUT_THRESHOLD + 0.1, "pa": 0.6, "pb": 0.0},
                },
            ),
            action="STAY OUT",
            service="NONE",
            why_contains="PACE ADVANTAGE",
            trigger="TRACK",
            conf="M",
        )

    def test_undercut_threat_pit_now(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"sl": 10, "lr": 30, "lp": 1, "l": 20},
                r={"ftl": 25},
                fi={"rej": {"v": "PACK"}, "odi": {"score": -0.5, "pa": -0.5, "pb": 0.4}},
            ),
            action="PIT NOW",
            service="4 TIRES",
            why_contains="UNDERCUT THREAT",
            trigger="TRACK",
            conf="M",
        )

    def test_incident_headroom_tight(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"l": 21, "lp": 1, "sl": 21, "inc": {"ot": 0, "hr": 1, "tot": 16}},
                fi={"rej": {"v": "CLEAN"}},
            ),
            action="STAY OUT",
            service="NONE",
            why_contains="INCIDENT LIMIT TIGHT",
            trigger="FUEL",
            conf="M",
        )

    def test_incident_headroom_protect_license(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"l": 21, "lp": 1, "sl": 21, "inc": {"ot": 0, "hr": 2, "tot": 15}, "gb": 0.5},
                fi={"rej": {"v": "CLEAN"}},
            ),
            action="STAY OUT",
            service="NONE",
            why_contains="PROTECT LICENSE",
            trigger="FUEL",
            conf="M",
        )

    def test_marble_pickup_appends_tire_note(self) -> None:
        assert_telemetry_advice(
            self,
            base_live_telemetry(m={"mar": {"lr": 2}}),
            action="STAY OUT",
            service="NONE",
            why_contains=("MAINTAIN STINT PACE", "Pickup on tires"),
            trigger="TIRES",
            conf="M",
        )

    def test_steering_loose_appends_note_on_pit_call(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"l": 21, "lp": 1, "sl": 21, "drv": {"ssr": STEER_STD_HIGH}},
                fi={"rej": {"v": "CLEAN"}},
            ),
            action="PIT NOW",
            service="4 TIRES",
            why_contains=("CLEAN REENTRY", "Car is loose"),
            trigger="TRACK",
            conf="M",
        )

    def test_track_cooled_appends_stint_note(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"l": 21, "lp": 1, "sl": 21},
                s={"tenv": {"dt": -12.0, "tsc": 24}},
                fi={"rej": {"v": "CLEAN"}},
            ),
            action="PIT NOW",
            service="4 TIRES",
            why_contains=("CLEAN REENTRY", "Track cooled"),
            trigger="TIRES",
            conf="M",
        )

    def test_track_heated_appends_stint_note(self) -> None:
        assert_telemetry_advice(
            self,
            inside_window_telemetry(
                m={"l": 21, "lp": 1, "sl": 21},
                s={"tenv": {"dt": TRACK_TEMP_SHIFT_THRESHOLD_C + 2, "tsc": 16}},
                fi={"rej": {"v": "CLEAN"}},
            ),
            action="PIT NOW",
            service="4 TIRES",
            why_contains=("CLEAN REENTRY", "Track heated"),
            trigger="TIRES",
            conf="M",
        )


class IncidentPushAdviceTextTests(unittest.TestCase):
    def test_incident_push_dashboard_format(self) -> None:
        tel = base_live_telemetry(m={"inc": {"ot": INCIDENT_OT_ALERT_COUNT, "hr": 8}})
        text = incident_push_advice(tel)
        self.assertIsNotNone(text)
        assert text is not None
        assert_advice(
            self,
            text,
            action="STAY OUT",
            service="NONE",
            why_contains="BACK IT DOWN",
            trigger="TRACK",
            conf="M",
        )
        self.assertIn("MACHINE STATE", text)
        self.assertIn("STRATEGY CALL", text)


class AdviceFormatConsistencyTests(unittest.TestCase):
    def test_run_strategy_matches_parse_roundtrip(self) -> None:
        cases = [
            base_live_telemetry(),
            inside_window_telemetry(m={"l": 21, "lp": 1, "sl": 21}, fi={"rej": {"v": "CLEAN"}}),
            base_live_telemetry(s={"flb": {"yel": True, "cau": True}}),
        ]
        for tel in cases:
            text = run_strategy(tel, mode="live")
            parsed = parse_advice(text)
            self.assertTrue(parsed.is_dashboard)
            action, timing, service = parse_call_line(text)
            self.assertEqual(advice_call_line(text), f"{action} — {timing} — {service}")
            self.assertIn("CONF:", text)
            self.assertIn("STRATEGY CALL", text)


if __name__ == "__main__":
    unittest.main()
