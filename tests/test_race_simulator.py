"""Smoke tests for offline race simulation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sim.race_simulator import (
    MAX_FIELD_SIZE,
    MIN_FIELD_SIZE,
    RaceScenario,
    RaceSimulator,
    SCENARIOS,
    CautionWindow,
    clamp_field_size,
    generate_simulated_packet_extras,
    resolve_field_size,
    _load_scenarios,
    write_sim_log,
)
from engineer.context_engine import StrategyMode
from engineer.race_constants import THERMAL_GREASY_C
from tests.advice_assertions import parse_advice


class RaceSimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _load_scenarios()

    def test_default_scenario_runs_all_laps(self) -> None:
        scenario = SCENARIOS["default"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        self.assertEqual(len(records), scenario.total_laps)
        for rec in records:
            self.assertTrue(rec.advice.strip())
            parsed = parse_advice(rec.advice)
            self.assertTrue(parsed.action)

    def test_caution_scenario_flags_yellow(self) -> None:
        scenario = SCENARIOS["caution_lap8"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        caution_laps = [r for r in records if r.is_caution]
        self.assertGreaterEqual(len(caution_laps), 3)
        lap8 = next(r for r in records if r.lap == 8)
        self.assertTrue(lap8.is_caution)
        parsed = parse_advice(lap8.advice)
        self.assertIn(parsed.action, ("PIT", "PIT NOW", "STAY OUT"))

        lap9 = next(r for r in records if r.lap == 9)
        self.assertTrue(lap9.on_pit_road or lap9.in_stall)
        self.assertEqual(lap9.immediate_directive.get("ACTION"), "STAY OUT")

        lap11 = next(r for r in records if r.lap == 11)
        self.assertFalse(lap11.is_caution)
        self.assertIsNotNone(lap11.gap_ahead)

    def test_writes_log_file(self) -> None:
        scenario = SCENARIOS["default"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.log"
            write_sim_log(records, scenario=scenario, log_path=path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("IMMEDIATE DIRECTIVE", text)
            self.assertIn("ENGINEER ADVICE", text)
            self.assertIn("LAP 01", text)
            self.assertIn("END OF SIMULATION", text)

    def test_undercut_scenario_offensive_mode(self) -> None:
        scenario = SCENARIOS["undercut"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        lap12 = next(r for r in records if r.lap == 12)
        self.assertIn(lap12.mode, ("OFFENSIVE", "BALANCED", "DEFENSIVE"))

    def test_mid_race_join_starts_at_configured_lap(self) -> None:
        scenario = SCENARIOS["join_traffic_p5_lap22"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        self.assertEqual(records[0].lap, 22)
        self.assertEqual(records[-1].lap, scenario.total_laps)
        self.assertEqual(len(records), scenario.total_laps - 21)
        self.assertEqual(records[0].position, 5)
        self.assertEqual(len(sim.field), 38)

    def test_field_size_clamped_to_22_40(self) -> None:
        self.assertEqual(clamp_field_size(10), MIN_FIELD_SIZE)
        self.assertEqual(clamp_field_size(50), MAX_FIELD_SIZE)
        self.assertEqual(clamp_field_size(32), 32)

    def test_large_field_reentry_packet(self) -> None:
        scenario = SCENARIOS["join_traffic_p8_lap30"]
        sim = RaceSimulator(scenario, seed=1)
        self.assertEqual(len(sim.field), 40)
        records = sim.run()
        self.assertEqual(records[0].position, 18)
        rej = records[0].packet.get("fi", {}).get("rej", {})
        self.assertIn(rej.get("v"), ("CLEAN", "TRAFFIC", "PACK", "UNKNOWN"))

    def test_join_leader_starts_p1(self) -> None:
        scenario = SCENARIOS["join_leader_lap12"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        self.assertEqual(records[0].lap, 12)
        self.assertEqual(records[0].position, 1)
        self.assertIsNone(records[0].gap_ahead)

    def test_join_last_starts_p10(self) -> None:
        scenario = SCENARIOS["join_last_lap18"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        self.assertEqual(records[0].position, 35)
        self.assertEqual(records[0].lap, 18)
        self.assertEqual(len(sim.field), 35)

    def test_lapped_danger_reentry_on_late_laps(self) -> None:
        scenario = SCENARIOS["lapped_danger"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        late = [r for r in records if r.lap >= 15]
        verdicts = {r.reentry_verdict for r in late}
        self.assertTrue(
            "LAPPED_DANGER" in verdicts or "PACK" in verdicts or "CLEAN" in verdicts,
            f"unexpected verdicts only: {verdicts}",
        )

    def test_tactical_state_transitions_under_caution(self) -> None:
        """Caution compresses gaps to DEFENSIVE mode and suppresses offensive undercut."""
        scenario = SCENARIOS["tactical_caution_gate"]
        runner = RaceSimulator(scenario=scenario, seed=1)

        runner.run_lap(1)
        self.assertNotEqual(runner.current_strategy_mode, StrategyMode.OFFENSIVE)
        self.assertFalse(runner.context.fi_odi_undercut)

        rec2 = runner.run_lap(2)
        self.assertTrue(rec2.is_caution)
        self.assertEqual(runner.current_strategy_mode, StrategyMode.DEFENSIVE)
        self.assertFalse(runner.context.fi_odi_undercut)
        self.assertFalse(rec2.tactical_summary.get("odi_uc"))
        self.assertNotIn("fi_odi_undercut", rec2.tactical_alerts)

    def test_caution_suppresses_offensive_undercut(self) -> None:
        """Yellow flag on an undercut lap blocks fi.odi.uc even with draft overrides."""
        undercut = SCENARIOS["undercut"]
        scenario = RaceScenario(
            name="undercut_caution_l12",
            description="Undercut window under yellow for regression gate.",
            total_laps=15,
            hero_position=undercut.hero_position,
            field_size=undercut.field_size,
            cautions=[CautionWindow(start_lap=12, duration_laps=1, herd_pit_ratio=0.75)],
            on_lap_start=undercut.on_lap_start,
            irsdk_overrides=dict(undercut.irsdk_overrides),
        )
        sim = RaceSimulator(scenario, seed=1)
        rec = sim.run_lap(12)
        self.assertTrue(rec.is_caution)
        self.assertFalse(rec.tactical_summary.get("odi_uc"))
        self.assertNotIn("fi_odi_undercut", rec.tactical_alerts)

    def test_defensive_mode_under_synthetic_pressure(self) -> None:
        scenario = SCENARIOS["defensive_pressure"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        lap11 = next(r for r in records if r.lap == 11)
        self.assertEqual(lap11.mode, "DEFENSIVE")
        self.assertEqual(lap11.tactical_summary.get("context_mode"), "DEFENSIVE")
        self.assertFalse(lap11.tactical_summary.get("odi_uc"))
        self.assertGreaterEqual(lap11.sim_extras.get("max_tire_temp", 0), 107.0)
        self.assertGreaterEqual(float(lap11.sim_extras.get("apex_loss", 0)), 5.0)

    def test_generate_simulated_packet_extras_defaults(self) -> None:
        scenario = SCENARIOS["default"]
        sim = RaceSimulator(scenario, seed=1)
        hero_car = sim.hero
        hero_car.gap_to_behind_s = 0.35
        extras = generate_simulated_packet_extras(hero_car, scenario, 5, is_caution=False)
        self.assertGreater(extras["tactical"]["apex_loss"], 5.0)
        self.assertGreater(extras["irsdk"]["LFtempCM"], THERMAL_GREASY_C - 1)

    def test_log_includes_tactical_block(self) -> None:
        scenario = SCENARIOS["tactical_caution_gate"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tactical.log"
            write_sim_log(records, scenario=scenario, log_path=path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("TACTICAL:", text)
            self.assertIn("Mode: DEFENSIVE", text)

    def test_green_restart_target_box_does_not_drift_outward(self) -> None:
        """Lap 4 green after caution should not push target box past lap 3 caution box."""
        scenario = SCENARIOS["tactical_caution_gate"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        lap3 = next(r for r in records if r.lap == 3)
        lap4 = next(r for r in records if r.lap == 4)
        self.assertIn("Target Box: L21 - L23", lap3.advice)
        self.assertIn("Target Box: L21 - L23", lap4.advice)


if __name__ == "__main__":
    unittest.main()
