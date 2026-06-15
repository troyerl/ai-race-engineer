"""Pre-race simulation branches from live session packets."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engineer.packet_snapshot import save_telemetry_packet
from engineer.pre_race_sim import (
    CAUTION_BUCKETS,
    build_field_pace_map,
    distribute_caution_windows,
    extract_pre_race_sim_context,
    field_size_from_telemetry,
    format_pre_race_sim_plan,
    grid_position_from_telemetry,
    quali_pace_by_position_from_session,
    run_pre_race_simulation,
)
from tests.fixtures import base_live_telemetry


def _quali_results(field_size: int = 28, *, hero_pos: int = 8, hero_lap: float = 91.0) -> list[dict]:
    rows = []
    for pos in range(1, field_size + 1):
        # Cars ahead on the grid are faster (negative delta when pos < hero_pos).
        delta = (pos - hero_pos) * 0.3
        rows.append(
            {
                "Position": pos,
                "CarIdx": pos - 1,
                "FastestLap": round(hero_lap + delta, 3),
            }
        )
    return rows


def _garage_packet() -> dict:
    return base_live_telemetry(
        m={
            "p": 8,
            "t": [91.2, 90.8, 91.0, 90.5],
            "fo": 0.09,
            "fpe": 0.11,
            "bl": 90.4,
        },
        r={"fc": 18.0, "ftl": 22.0, "pl": 46, "ts": 4, "tsl": 4},
        s={
            "ttc": 28.0,
            "race_lt": 60,
            "tenv": {"ref": 30.0, "cur": 28.0, "dt": -2.0, "tsc": 24},
        },
        sy={
            "WeekendInfo": {
                "TrackDisplayName": "Test Speedway",
                "TrackLength": "1.5",
            },
            "SessionInfo": {
                "Sessions": [
                    {
                        "SessionType": "Open Qualify",
                        "ResultsPositions": _quali_results(),
                    },
                    {"SessionType": "Race", "SessionLaps": 60},
                ]
            },
            "DriverInfo": {
                "Drivers": [{"CarIdx": 0, "CarIsPlayer": 1, "UserName": "Hero"}]
                + [{"CarIdx": i, "CarIsPlayer": 0} for i in range(1, 28)]
            },
        },
    )


class CautionDistributionTests(unittest.TestCase):
    def test_spreads_cautions_across_race(self) -> None:
        windows = distribute_caution_windows(60, 5)
        self.assertEqual(len(windows), 5)
        starts = [w.start_lap for w in windows]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(3 <= lap <= 57 for lap in starts))

    def test_zero_cautions_empty(self) -> None:
        self.assertEqual(distribute_caution_windows(40, 0), [])


class SessionExtractTests(unittest.TestCase):
    def test_extracts_grid_pace_and_laps(self) -> None:
        tel = _garage_packet()
        ctx = extract_pre_race_sim_context(tel)
        self.assertEqual(ctx.track_name, "Test Speedway")
        self.assertEqual(ctx.total_laps, 60)
        self.assertEqual(ctx.hero_position, 8)
        self.assertEqual(ctx.field_size, 28)
        self.assertGreater(ctx.fuel_tank_laps, 10)
        self.assertAlmostEqual(ctx.hero_base_pace_s, 90.875, places=2)
        self.assertEqual(len(ctx.hero_lap_times), 4)
        self.assertIn(1, ctx.field_pace_by_position)
        self.assertLess(ctx.field_pace_by_position[1], ctx.field_pace_by_position[8])

    def test_quali_pace_parser(self) -> None:
        sy = _garage_packet()["sy"]
        pace, label = quali_pace_by_position_from_session(sy)
        self.assertIn("Qualify", label)
        self.assertAlmostEqual(pace[1], 88.9, places=2)
        self.assertAlmostEqual(pace[8], 91.0, places=2)

    def test_field_size_from_driver_info(self) -> None:
        tel = _garage_packet()
        self.assertEqual(field_size_from_telemetry(tel), 28)

    def test_grid_from_position(self) -> None:
        tel = _garage_packet()
        self.assertEqual(grid_position_from_telemetry(tel, field_size=28), 8)


class PreRaceSimRunTests(unittest.TestCase):
    def test_three_branches_complete(self) -> None:
        ctx, green, branches = run_pre_race_simulation(_garage_packet(), seed=1)
        self.assertEqual(len(branches), len(CAUTION_BUCKETS))
        self.assertGreater(green.get("total_stops_required", 0), 0)
        for br in branches:
            self.assertGreater(br.finish_position, 0)
            self.assertLessEqual(br.finish_position, ctx.field_size)

    def test_formatted_plan_mentions_branches(self) -> None:
        ctx, green, branches = run_pre_race_simulation(_garage_packet(), seed=1)
        text = format_pre_race_sim_plan(ctx, green, branches)
        self.assertIn("GREEN BASELINE", text)
        self.assertIn("LIGHT (0–3 cautions)", text)
        self.assertIn("GRID PACE:", text)
        self.assertIn("SESSION:", text)


class PacketSnapshotTests(unittest.TestCase):
    def test_save_telemetry_packet_writes_json(self) -> None:
        tel = _garage_packet()
        with tempfile.TemporaryDirectory() as tmp:
            path = save_telemetry_packet(tel, track_name="Test Speedway", save_dir=Path(tmp))
            self.assertTrue(path.exists())
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(loaded.get("m"), dict)


if __name__ == "__main__":
    unittest.main()
