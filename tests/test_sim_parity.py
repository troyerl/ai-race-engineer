"""Tests for sim fuel EMA and session replay."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engineer.race_constants import green_flag_fuel_laps_from_telemetry
from engineer.session_recorder import SessionRecorder, load_session, replay_session
from sim.fuel_ema import SimFuelTracker
from sim.race_simulator import RaceSimulator, SCENARIOS, _load_scenarios


class SimFuelEmaTests(unittest.TestCase):
    def test_ema_builds_after_green_laps(self) -> None:
        tracker = SimFuelTracker(tank_laps=22.0, fuel_laps_left=22.0)
        for lap in range(1, 6):
            tracker.sync_from_fuel_laps(22.0 - lap)
            tracker.on_lap_boundary(lap)
        fields = tracker.packet_m_fields()
        self.assertIsNotNone(fields.get("ful"))
        self.assertIsNotNone(fields.get("fpe"))

    def test_yellow_lift_purges_ema(self) -> None:
        tracker = SimFuelTracker(tank_laps=22.0, fuel_laps_left=20.0)
        for lap in range(1, 4):
            tracker.sync_from_fuel_laps(20.0 - lap)
            tracker.on_lap_boundary(lap)
        self.assertIsNotNone(tracker.packet_m_fields().get("fpe"))
        tracker.reset_for_green_restart(4)
        tracker.sync_from_fuel_laps(16.0)
        tracker.on_lap_boundary(4)
        tracker.sync_from_fuel_laps(14.0)
        tracker.on_lap_boundary(5)
        fields = tracker.packet_m_fields()
        self.assertIsNotNone(fields.get("fpe"))

    @classmethod
    def setUpClass(cls) -> None:
        _load_scenarios()

    def test_sim_packets_include_fpe(self) -> None:
        sim = RaceSimulator(SCENARIOS["default"], seed=7)
        records = sim.run()
        green = [r for r in records if not r.is_caution and r.lap >= 4]
        self.assertTrue(green)
        for rec in green[:3]:
            m = rec.packet.get("m", {})
            self.assertIn("ful", m)
            self.assertIn("fpl", m)
            self.assertIsNotNone(m.get("fpe"))

    def test_green_flag_fuel_from_sim_packet(self) -> None:
        sim = RaceSimulator(SCENARIOS["default"], seed=3)
        records = sim.run()
        rec = next(r for r in records if r.lap == 10 and not r.is_caution)
        green = green_flag_fuel_laps_from_telemetry(rec.packet, fallback_l_per_lap=2.0)
        m = rec.packet["m"]
        self.assertGreater(green, 0.0)
        self.assertIsNotNone(m.get("fpe"))
        self.assertIsNotNone(m.get("ful"))


class SessionRecorderTests(unittest.TestCase):
    def test_jsonl_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rec = SessionRecorder.start(track_name="Test Track", save_dir=Path(tmp))
            p1 = {"m": {"l": 1, "p": 5}, "s": {"flb": {}}}
            p2 = {"m": {"l": 2, "p": 5}, "s": {"flb": {}}}
            rec.append_on_lap_change(p1)
            rec.append_on_lap_change(p1)
            rec.append_on_lap_change(p2)
            loaded = load_session(rec.path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["m"]["l"], 1)
            self.assertEqual(loaded[1]["m"]["l"], 2)

    def test_replay_runs_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mini.jsonl"
            packets = [
                {"m": {"l": 1, "p": 5, "lr": 19, "fl": 18.0, "pr": False, "ps": False}, "s": {"lt": 20, "flb": {}}, "r": {"pl": 46, "ftl": 22}},
                {"m": {"l": 2, "p": 5, "lr": 18, "fl": 17.0, "pr": False, "ps": False}, "s": {"lt": 20, "flb": {}}, "r": {"pl": 46, "ftl": 22}},
            ]
            path.write_text("\n".join(json.dumps(p) for p in packets) + "\n", encoding="utf-8")
            results = replay_session(path)
            self.assertEqual(len(results), 2)
            for row in results:
                self.assertTrue(str(row.get("advice", "")).strip())


if __name__ == "__main__":
    unittest.main()
