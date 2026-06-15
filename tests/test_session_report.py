"""Tests for post-race session analytics (Phase D)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engineer.session_report import (
    analyze_session_file,
    build_session_report,
    build_stop_comparisons,
    generate_executive_summary,
    load_session_meta,
    parse_planned_stop_laps,
    save_session_meta,
)


def _sample_packets() -> list[dict]:
    return [
        {
            "m": {
                "l": 1,
                "p": 8,
                "fl": 20.0,
                "pr": False,
                "ps": False,
                "pc": {"std_clean_s": 0.22},
            },
            "s": {"flb": {}, "tn": "Test Speedway"},
            "fi": {"sm": {"n": "GREEN"}},
        },
        {
            "m": {
                "l": 2,
                "p": 7,
                "fl": 19.0,
                "pr": False,
                "ps": False,
                "pc": {"std_clean_s": 0.24},
            },
            "s": {"flb": {}},
            "fi": {"sm": {"n": "GREEN"}},
        },
        {
            "m": {
                "l": 3,
                "p": 6,
                "fl": 18.0,
                "pr": True,
                "ps": False,
                "pc": {"std_clean_s": 0.21},
            },
            "s": {"flb": {}},
            "fi": {"sm": {"n": "GREEN"}},
        },
        {
            "m": {
                "l": 4,
                "p": 9,
                "fl": 17.0,
                "pr": False,
                "ps": False,
                "pc": {"std_clean_s": 0.26},
            },
            "s": {"flb": {}},
            "fi": {"sm": {"n": "GREEN"}},
        },
        {
            "m": {
                "l": 5,
                "p": 8,
                "fl": 16.0,
                "pr": False,
                "ps": False,
                "pc": {"std_clean_s": 0.25},
            },
            "s": {"flb": {}},
            "fi": {"sm": {"n": "GREEN"}},
        },
        {
            "m": {
                "l": 6,
                "p": 8,
                "fl": 15.0,
                "pr": False,
                "ps": False,
                "pc": {"std_clean_s": 0.27},
            },
            "s": {"flb": {}},
            "fi": {"sm": {"n": "GREEN"}},
        },
    ]


class SessionReportTests(unittest.TestCase):
    def test_parse_planned_stop_laps(self) -> None:
        plan = "Green stint to L12, then fuel only. Second stop L28 four tires."
        self.assertEqual(parse_planned_stop_laps(plan), [12, 28])

    def test_build_report_positions_and_pit(self) -> None:
        report = build_session_report(_sample_packets())
        self.assertEqual(report.start_position, 8)
        self.assertEqual(report.finish_position, 8)
        self.assertEqual(report.position_delta, 0)
        self.assertEqual(report.actual_stop_laps, [3])
        self.assertEqual(len(report.pit_events), 1)
        pe = report.pit_events[0]
        self.assertEqual(pe.lap, 3)
        self.assertEqual(pe.position_at_pit, 6)
        self.assertEqual(pe.position_after, 9)
        self.assertEqual(pe.positions_delta, 3)

    def test_stint_stddev_computed(self) -> None:
        report = build_session_report(_sample_packets())
        self.assertTrue(report.stint_stddevs)

    def test_stop_delta_vs_baseline(self) -> None:
        plan = "Pit L2 fuel only"
        report = build_session_report(_sample_packets(), baseline_plan=plan)
        self.assertEqual(report.planned_stop_laps, [2])
        self.assertEqual(report.actual_stop_laps, [3])
        self.assertEqual(report.stop_lap_deltas, [1])
        self.assertEqual(len(report.stop_comparisons), 1)
        sc = report.stop_comparisons[0]
        self.assertEqual(sc.planned_lap, 2)
        self.assertEqual(sc.actual_lap, 3)
        self.assertEqual(sc.lap_delta, 1)
        self.assertIsNotNone(sc.fuel_delta_laps)

    def test_build_stop_comparisons_fuel_delta(self) -> None:
        from engineer.session_report import LapSnapshot

        rows = [
            LapSnapshot(1, 8, 20.0, False, None, None, False, None, None, False, None),
            LapSnapshot(2, 7, 19.0, False, None, None, False, None, None, False, None),
            LapSnapshot(3, 6, 17.5, False, None, None, False, None, None, True, None),
        ]
        comps = build_stop_comparisons([2], [3], rows)
        self.assertEqual(len(comps), 1)
        self.assertAlmostEqual(comps[0].fuel_delta_laps or 0, -1.5)

    def test_executive_summary_mentions_gain_or_loss(self) -> None:
        report = build_session_report(_sample_packets())
        summary = generate_executive_summary(report)
        self.assertIn("position", summary.lower())

    def test_session_meta_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_test.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            save_session_meta(path, {"baseline_plan": "Stop L10"})
            meta = load_session_meta(path)
            self.assertEqual(meta.get("baseline_plan"), "Stop L10")

    def test_analyze_session_file_no_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mini.jsonl"
            path.write_text(
                "\n".join(json.dumps(p) for p in _sample_packets()) + "\n",
                encoding="utf-8",
            )
            report = analyze_session_file(path, include_replay=False)
            self.assertEqual(len(report.laps), 6)
            self.assertEqual(report.track_name, "Test Speedway")


if __name__ == "__main__":
    unittest.main()
