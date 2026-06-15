"""Integration tests: advice priority chain, auto-alerts, fi.sm packet, divebomb speech."""

from __future__ import annotations

import json
import unittest
from typing import Any

from engineer.auto_alert_engine import AutoMonitorState, evaluate_auto_alert_tick
from engineer.context_engine import DriverContextTracker, StrategyMode
from engineer.race_constants import INCIDENT_OT_ALERT_COUNT, MODE_OFFENSIVE_GAP_BEHIND_MIN
from engineer.speech import _speech_lines
from engineer.strategy_engine import (
    format_engineer_advice,
    incident_push_advice,
    resolve_live_advice,
    run_strategy,
    should_auto_alert,
    tactical_defensive_advice,
    tactical_undercut_advice,
)
from tests.advice_assertions import parse_advice
from tests.fixtures import base_live_telemetry, inside_window_telemetry


class MockIRacing:
    def __init__(self, state: dict | None = None) -> None:
        self.state = dict(state or {})

    def __call__(self, key: str, default=None):
        return self.state.get(key, default)


def _offensive_undercut_telemetry(**overrides: Any) -> dict[str, Any]:
    default_m = {
        "fl": 1.8,
        "sl": 10,
        "lr": 30,
        "lp": 1,
        "l": 20,
        "ga": 0.5,
        "gb": 1.0,
    }
    m_override = overrides.pop("m", None)
    if isinstance(m_override, dict):
        default_m.update(m_override)
    return inside_window_telemetry(
        m=default_m,
        r={"ftl": 25, "pl": 45, "ts": 3},
        rv={"ahead": {"pos": 4}},
        fi={
            "sm": {"m": 1, "n": "OFFENSIVE"},
            "rej": {"v": "CLEAN"},
            "odi": {"uc": True, "pa": 0.3, "pb": 0.0, "score": 0.3},
        },
        **overrides,
    )


def _defensive_telemetry(**overrides: Any) -> dict[str, Any]:
    return base_live_telemetry(
        m={"gb": 0.4, "ga": 2.0},
        fi={
            "sm": {"m": 2, "n": "DEFENSIVE"},
            "tac": {"cool": True},
        },
        **overrides,
    )


class ResolveLiveAdvicePriorityTests(unittest.TestCase):
    def test_incident_beats_tactical_undercut(self) -> None:
        tel = _offensive_undercut_telemetry(
            m={"inc": {"ot": INCIDENT_OT_ALERT_COUNT, "hr": 5, "tot": 10}},
        )
        advice = resolve_live_advice(tel)
        self.assertIsNotNone(incident_push_advice(tel))
        self.assertIn("back it down", parse_advice(advice).why.lower())

    def test_tactical_undercut_beats_main_strategy(self) -> None:
        tel = _offensive_undercut_telemetry()
        advice = resolve_live_advice(tel)
        parsed = parse_advice(advice)
        self.assertEqual(parsed.action, "PIT NOW")
        self.assertIn("UNDERCUT ACTIVE", parsed.why.upper())

    def test_tactical_defensive_beats_maintain_stint(self) -> None:
        tel = _defensive_telemetry()
        advice = resolve_live_advice(tel)
        parsed = parse_advice(advice)
        self.assertEqual(parsed.action, "COOL TIRES")
        self.assertIn("OVERHEATING", parsed.why.upper())
        self.assertNotEqual(
            parse_advice(run_strategy(tel)).why,
            parsed.why,
        )

    def test_balanced_falls_through_to_strategy(self) -> None:
        tel = inside_window_telemetry(
            m={"fl": 8.0, "mk": True, "sl": 8, "lp": 5, "l": 13},
            fi={"sm": {"m": 0, "n": "BALANCED"}, "rej": {"v": "CLEAN"}},
        )
        advice = resolve_live_advice(tel)
        self.assertIsNone(tactical_undercut_advice(tel))
        self.assertIsNone(tactical_defensive_advice(tel))
        self.assertEqual(parse_advice(advice).action, "STAY OUT")


class ShouldAutoAlertTacticalTests(unittest.TestCase):
    def test_tactical_undercut_triggers_alert(self) -> None:
        tel = _offensive_undercut_telemetry()
        strategy_advice = run_strategy(tel)
        self.assertTrue(should_auto_alert(tel, strategy_advice))

    def test_tactical_defensive_triggers_alert_even_when_strategy_stays_out(self) -> None:
        tel = _defensive_telemetry()
        strategy_advice = run_strategy(tel)
        self.assertEqual(parse_advice(strategy_advice).action, "STAY OUT")
        self.assertTrue(should_auto_alert(tel, strategy_advice))

    def test_no_tactical_alert_when_mode_suppresses_advice(self) -> None:
        tel = inside_window_telemetry(
            m={"fl": 8.0, "mk": True, "sl": 10, "lp": 1, "l": 20, "lr": 30},
            fi={
                "sm": {"m": 0, "n": "BALANCED"},
                "rej": {"v": "CLEAN"},
                "odi": {"uc": True},
            },
        )
        self.assertIsNone(tactical_undercut_advice(tel))
        advice = run_strategy(tel)
        self.assertFalse(should_auto_alert(tel, advice))


class AutoAlertTacticalIntegrationTests(unittest.TestCase):
    def test_lap_change_delivers_tactical_undercut(self) -> None:
        tel = _offensive_undercut_telemetry()
        state = AutoMonitorState(last_lap=19, last_caution=False)
        state, decision = evaluate_auto_alert_tick(
            state,
            lap=20,
            is_caution=False,
            telemetry=tel,
            last_delivered_call_line="",
        )
        self.assertTrue(decision.deliver, decision.reason)
        self.assertIsNotNone(decision.advice)
        assert decision.advice is not None
        self.assertIn("UNDERCUT ACTIVE", decision.advice.upper())

    def test_lap_change_delivers_tactical_defensive(self) -> None:
        tel = _defensive_telemetry()
        state = AutoMonitorState(last_lap=9, last_caution=False)
        state, decision = evaluate_auto_alert_tick(
            state,
            lap=10,
            is_caution=False,
            telemetry=tel,
            last_delivered_call_line="",
        )
        self.assertTrue(decision.deliver, decision.reason)
        self.assertIsNotNone(decision.advice)
        assert decision.advice is not None
        self.assertIn("OVERHEATING", decision.advice.upper())


class PacketStrategyModeIntegrationTests(unittest.TestCase):
    def _build_extras(self, tracker: DriverContextTracker, *, gap_behind: float, gap_ahead: float = 2.0) -> dict[str, Any]:
        return tracker.build_packet_extras(
            track_temp_c=28.0,
            tire_wear_rate_est=None,
            pit_loss_sec=45.0,
            tire_falloff_s=0.08,
            fuel_stint_cap=25,
            rivals={},
            you_pace={"avg_last3_s": 90.0},
            reentry_verdict="CLEAN",
            current_lap=10,
            gap_behind_s=gap_behind,
        )

    def test_poll_and_packet_merge_offensive_mode(self) -> None:
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
        extras = self._build_extras(tracker, gap_behind=MODE_OFFENSIVE_GAP_BEHIND_MIN, gap_ahead=0.5)

        packet: dict[str, Any] = {"m": {}, "fi": {"rej": {"v": "CLEAN"}}}
        packet["fi"].update(extras["fi"])

        self.assertEqual(packet["fi"]["sm"]["n"], "OFFENSIVE")
        self.assertEqual(tracker.current_mode, StrategyMode.OFFENSIVE)
        self.assertNotIn("tac", packet["fi"])

    def test_poll_and_packet_merge_defensive_mode(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing(
            {
                "LapDistPct": 0.12,
                "LatAccel": 0.0,
                "LFtempCM": 110.0,
                "RFtempCM": 90.0,
            }
        )
        tracker.poll(
            ir,
            player_idx=0,
            lap=10,
            on_track=True,
            gap_ahead_s=2.0,
            gap_behind_s=0.35,
            is_caution=False,
            session_time_elapsed=10.0,
        )
        tracker.poll(
            ir,
            player_idx=0,
            lap=10,
            on_track=True,
            gap_ahead_s=2.0,
            gap_behind_s=0.15,
            is_caution=False,
            session_time_elapsed=10.5,
        )
        extras = self._build_extras(tracker, gap_behind=0.15)

        packet: dict[str, Any] = {"m": {}, "fi": {}}
        packet["fi"].update(extras["fi"])
        if extras.get("m"):
            packet["m"].update(extras["m"])

        self.assertEqual(packet["fi"]["sm"]["n"], "DEFENSIVE")
        odi = packet["fi"]["odi"]
        self.assertFalse(odi.get("uc"))
        self.assertEqual(odi.get("rd"), 0.0)

    def test_balanced_mode_suppresses_tactical_packet_fields(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing({"LapDistPct": 0.5, "LatAccel": 0.0})
        tracker.poll(
            ir,
            player_idx=0,
            lap=10,
            on_track=True,
            gap_ahead_s=2.0,
            gap_behind_s=2.0,
            is_caution=False,
        )
        extras = self._build_extras(tracker, gap_behind=2.0, gap_ahead=2.0)
        packet = {"fi": dict(extras["fi"])}

        self.assertEqual(packet["fi"]["sm"]["n"], "BALANCED")
        self.assertNotIn("tac", packet["fi"])
        self.assertFalse(packet["fi"]["odi"]["uc"])
        self.assertEqual(packet["fi"]["odi"]["rd"], 0.0)

    def test_packet_json_roundtrip_includes_sm(self) -> None:
        tracker = DriverContextTracker()
        ir = MockIRacing({"LapDistPct": 0.5, "LatAccel": 0.0})
        tracker.poll(
            ir,
            player_idx=0,
            lap=5,
            on_track=True,
            gap_ahead_s=0.6,
            gap_behind_s=1.0,
            is_caution=False,
        )
        extras = self._build_extras(tracker, gap_behind=1.0, gap_ahead=0.6)
        packet = {"fi": extras["fi"]}
        blob = json.dumps(packet, separators=(",", ":"))
        loaded = json.loads(blob)
        self.assertEqual(loaded["fi"]["sm"]["n"], "OFFENSIVE")


class DivebombSpeechIntegrationTests(unittest.TestCase):
    def test_speech_lines_divebomb_override(self) -> None:
        advice = format_engineer_advice(
            "GUARD INSIDE",
            "THIS LAP",
            "NONE",
            "Divebomb threat inside — protect the entry.",
            trigger="TRACK",
            conf="M",
            telemetry=_defensive_telemetry(),
        )
        lines = _speech_lines(advice, include_why=True)
        self.assertEqual(lines, ["Divebomb threat inside, guard the entry."])

    def test_speech_lines_lapped_danger_still_wins_over_divebomb(self) -> None:
        from engineer.strategy_engine import LAPPED_DANGER_WHY

        advice = format_engineer_advice(
            "STAY OUT",
            "THIS LAP",
            "NONE",
            LAPPED_DANGER_WHY,
            trigger="TRACK",
            conf="M",
            telemetry=inside_window_telemetry(),
        )
        lines = _speech_lines(advice, include_why=True)
        self.assertEqual(len(lines), 1)
        self.assertIn("lap down", lines[0].lower())


if __name__ == "__main__":
    unittest.main()
