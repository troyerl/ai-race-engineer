"""Tests for driver/engineer call split and race history chart data."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engineer.pre_race_sim import run_caution_branch, extract_pre_race_sim_context
from engineer.race_history import branches_to_meta, entry_from_session, finalize_session_trajectory
from engineer.race_memory_strategy import race_memory_context_lines
from engineer.strategy_engine import driver_call_line, format_engineer_advice
from tests.fixtures import base_live_telemetry


class DriverCallTests(unittest.TestCase):
    def test_driver_call_line_short(self) -> None:
        advice = format_engineer_advice(
            "PIT NOW",
            "THIS LAP",
            "FUEL ONLY",
            "Fuel window open.",
            trigger="FUEL",
            conf="H",
        )
        line = driver_call_line(advice)
        self.assertIn("PIT NOW", line)
        self.assertIn("FUEL ONLY", line)
        self.assertNotIn("WHY", line)


class RaceMemoryStrategyTests(unittest.TestCase):
    def test_race_memory_context_lines(self) -> None:
        tel = base_live_telemetry()
        tel["h"] = {"ss": {"cc": 5, "pd5": 2, "pc": 1}, "tr": {"pc": "degrading"}}
        lines = race_memory_context_lines(tel)
        self.assertIsNotNone(lines)
        assert lines is not None
        self.assertIn("MODERATE", lines)
        self.assertIn("degrading", lines)


class RaceHistoryChartTests(unittest.TestCase):
    def test_branch_positions_in_meta(self) -> None:
        tel = base_live_telemetry()
        ctx = extract_pre_race_sim_context(tel)
        br = run_caution_branch(ctx, label="LIGHT (0–3 cautions)", caution_count=2, seed=3)
        self.assertGreaterEqual(len(br.positions_by_lap), 2)
        meta_list = branches_to_meta([br])
        self.assertEqual(len(meta_list), 1)
        self.assertIn("positions", meta_list[0])

    def test_entry_from_session_with_branches_and_actual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_test.jsonl"
            path.write_text(
                json.dumps({"m": {"l": 1, "p": 8}, "s": {"tn": "Test Track"}}) + "\n"
                + json.dumps({"m": {"l": 2, "p": 7}, "s": {}}) + "\n",
                encoding="utf-8",
            )
            meta_path = path.with_suffix(".meta.json")
            meta_path.write_text(
                json.dumps(
                    {
                        "pre_race_branches": [
                            {
                                "label": "LIGHT (0–3 cautions)",
                                "start_position": 8,
                                "finish_position": 7,
                                "positions": [[1, 8], [2, 7]],
                            }
                        ],
                        "actual": {
                            "label": "ACTUAL",
                            "start_position": 8,
                            "finish_position": 7,
                            "positions": [[1, 8], [2, 7]],
                        },
                    }
                ),
                encoding="utf-8",
            )
            entry = entry_from_session(path)
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(len(entry.series), 2)
            self.assertTrue(entry.has_chart_data)

    def test_finalize_session_trajectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_run.jsonl"
            packets = [
                {"m": {"l": 1, "p": 10, "pr": False, "ps": False}, "s": {"flb": {}, "tn": "Track A"}},
                {"m": {"l": 2, "p": 9, "pr": False, "ps": False}, "s": {"flb": {}}},
            ]
            path.write_text("\n".join(json.dumps(p) for p in packets) + "\n", encoding="utf-8")
            finalize_session_trajectory(path)
            entry = entry_from_session(path)
            self.assertIsNone(entry)  # needs branches + actual — only actual written
            meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
            self.assertIn("actual", meta)
            self.assertEqual(meta["actual"]["start_position"], 10)


if __name__ == "__main__":
    unittest.main()
