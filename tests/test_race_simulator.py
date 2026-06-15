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
from engineer.strategy_engine import OFFENSIVE_UNDERCUT_WHY
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
        """Lap 4 green after caution: run-to-finish shows CHECKERED, not an out-of-range box."""
        scenario = SCENARIOS["tactical_caution_gate"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        lap3 = next(r for r in records if r.lap == 3)
        lap4 = next(r for r in records if r.lap == 4)
        self.assertIn("Target Box:", lap3.advice)
        self.assertIn("TARGET PIT: CHECKERED", lap4.advice)
        self.assertNotIn("Target Box:", lap4.advice)
        self.assertIn("No further stops", lap4.advice)

    def test_default_scenario_run_to_finish_no_phantom_stops(self) -> None:
        scenario = SCENARIOS["default"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        for rec in records:
            self.assertIn("TARGET PIT: CHECKERED", rec.advice)
            self.assertNotIn("Target Box:", rec.advice)
            self.assertIn("No further stops", rec.advice)
            self.assertNotIn("L22", rec.advice)


class LongRaceScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _load_scenarios()

    def test_long_race_scenario_configs(self) -> None:
        short = SCENARIOS["long_short_track"]
        medium = SCENARIOS["long_medium_track"]
        large = SCENARIOS["long_large_track"]
        self.assertEqual(short.total_laps, 120)
        self.assertEqual(medium.total_laps, 90)
        self.assertEqual(large.total_laps, 60)
        self.assertAlmostEqual(short.pit_loss_sec, 42.0)
        self.assertAlmostEqual(medium.pit_loss_sec, 46.0)
        self.assertAlmostEqual(large.pit_loss_sec, 58.0)
        self.assertEqual(short.track_name, "Bristol Motor Speedway")
        self.assertEqual(medium.track_name, "Charlotte Motor Speedway")
        self.assertEqual(large.track_name, "Daytona International Speedway")

    def test_long_medium_track_completes_all_laps(self) -> None:
        scenario = SCENARIOS["long_medium_track"]
        sim = RaceSimulator(scenario, seed=7)
        records = sim.run()
        self.assertEqual(len(records), 90)
        self.assertEqual(records[-1].lap, 90)
        self.assertTrue(all(rec.advice.strip() for rec in records))

    def test_long_short_track_log_includes_track(self) -> None:
        scenario = SCENARIOS["long_short_track"]
        sim = RaceSimulator(scenario, seed=1)
        records = sim.run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.log"
            write_sim_log(records, scenario=scenario, log_path=path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("Bristol Motor Speedway", text)
            self.assertIn("LAP 120", text)

    def test_long_medium_no_pit_now_early_lap(self) -> None:
        scenario = SCENARIOS["long_medium_track"]
        sim = RaceSimulator(scenario, seed=7)
        records = sim.run()
        lap10 = next(r for r in records if r.lap == 10)
        self.assertNotIn("PIT NOW", lap10.advice)
        self.assertNotIn(OFFENSIVE_UNDERCUT_WHY, lap10.advice)

    def test_follow_strategy_executes_pit_stop(self) -> None:
        scenario = SCENARIOS["race_full"]
        sim = RaceSimulator(scenario, seed=1, follow_strategy=True)
        records = sim.run()
        refueled = [
            r
            for r in records
            if r.lap > 10 and r.fuel_laps_left >= scenario.fuel_tank_laps - 1.0
        ]
        self.assertGreater(len(refueled), 0)

    def test_passing_can_improve_position(self) -> None:
        scenario = SCENARIOS["default"]
        sim = RaceSimulator(scenario, seed=99)
        sim.hero.base_pace_s -= 1.5
        records = sim.run()
        self.assertLess(records[-1].position, scenario.hero_position)

    def test_opponents_pit_during_race(self) -> None:
        scenario = SCENARIOS["long_medium_track"]
        sim = RaceSimulator(scenario, seed=3)
        records = sim.run()
        opponent_pitted = any(
            car.name != "HERO" and car.last_pit_lap is not None for car in sim.field
        )
        self.assertTrue(opponent_pitted)
        final_pos = records[-1].position
        self.assertGreater(final_pos, 1)

    def test_long_large_final_lap_no_pit_now_when_fuel_ok(self) -> None:
        scenario = SCENARIOS["long_large_track"]
        sim = RaceSimulator(scenario, seed=7, follow_strategy=True)
        records = sim.run()
        final = records[-1]
        self.assertNotIn("PIT NOW", final.advice)
        self.assertEqual(final.immediate_directive.get("ACTION"), "STAY OUT")

    def test_undercut_pace_stable_triggers_offensive_undercut(self) -> None:
        scenario = SCENARIOS["undercut_pace_stable"]
        sim = RaceSimulator(scenario, seed=42)
        records = sim.run()
        lap14 = next(r for r in records if r.lap == 14)
        pc = lap14.packet.get("m", {}).get("pc", {})
        self.assertTrue(lap14.tactical_summary.get("pace_stable_for_offense"))
        self.assertLess(float(pc.get("std_clean_s", 99)), 0.4)
        self.assertGreaterEqual(int(pc.get("n_clean", 0)), 5)
        self.assertEqual(lap14.immediate_directive.get("ACTION"), "PIT NOW")
        self.assertIn(OFFENSIVE_UNDERCUT_WHY, lap14.immediate_directive.get("WHY", ""))

    def test_undercut_pace_unstable_suppresses_offensive_undercut(self) -> None:
        scenario = SCENARIOS["undercut_pace_unstable"]
        sim = RaceSimulator(scenario, seed=42)
        records = sim.run()
        lap14 = next(r for r in records if r.lap == 14)
        pc = lap14.packet.get("m", {}).get("pc", {})
        self.assertFalse(lap14.tactical_summary.get("pace_stable_for_offense"))
        self.assertGreaterEqual(float(pc.get("std_clean_s", 0)), 0.4)
        self.assertNotIn(OFFENSIVE_UNDERCUT_WHY, lap14.immediate_directive.get("WHY", ""))
        self.assertNotIn("fi_odi_undercut", lap14.tactical_alerts)


class PaceStabilitySimLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _load_scenarios()

    def test_log_includes_pace_stability_block(self) -> None:
        scenario = SCENARIOS["undercut_pace_stable"]
        sim = RaceSimulator(scenario, seed=42)
        records = sim.run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pace_stable.log"
            write_sim_log(records, scenario=scenario, log_path=path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("PACE STABILITY:", text)
            self.assertIn("offense_ok=True", text)


if __name__ == "__main__":
    unittest.main()
