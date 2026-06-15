"""Tests for track database and sector learning."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engineer.context_engine import DriverContextTracker
from engineer.race_constants import HIGH_LAT_SECTOR_END, HIGH_LAT_SECTOR_START
from engineer.track_db import (
    SectorLearner,
    TrackDatabase,
    normalize_track_key,
    resolve_high_lat_sector,
    resolve_track_pit_loss_detail,
    resolve_track_pit_loss_seconds,
)


class TrackDbTests(unittest.TestCase):
    def test_normalize_track_key(self) -> None:
        self.assertEqual(
            normalize_track_key("Watkins Glen International"),
            "watkins_glen_international",
        )

    def test_seed_pit_loss_overrides_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = TrackDatabase(Path(tmp) / "tracks.db")
            loss = resolve_track_pit_loss_seconds(
                "Watkins Glen International", 1.5, db=db
            )
            self.assertAlmostEqual(loss, 46.0)
            profile = db.get_profile("watkins_glen_international")
            self.assertIsNotNone(profile)
            assert profile is not None
            self.assertEqual(profile.pit_loss_source, "seed")

    def test_measured_pit_loss_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = TrackDatabase(Path(tmp) / "tracks.db")
            db.record_measured_pit_loss(
                "custom_test_track",
                display_name="Custom Test Track",
                length_mi=2.0,
                pit_loss_sec=51.5,
            )
            detail = resolve_track_pit_loss_detail("Custom Test Track", 2.0, db=db)
            self.assertAlmostEqual(detail.pit_loss_sec, 51.5)
            self.assertEqual(detail.source, "measured")
            self.assertIn("measured", detail.label())

    def test_heuristic_when_no_db_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = TrackDatabase(Path(tmp) / "tracks.db")
            detail = resolve_track_pit_loss_detail("Unknown Circuit XYZ", 1.5, db=db)
            self.assertEqual(detail.source, "heuristic")
            self.assertAlmostEqual(detail.pit_loss_sec, detail.heuristic_sec)

    def test_sector_learner_finds_peak(self) -> None:
        learner = SectorLearner()
        for _ in range(25):
            learner.observe(0.10, 0.8)
        for _ in range(25):
            learner.observe(0.42, 2.5)
        for _ in range(25):
            learner.observe(0.80, 0.9)
        result = learner.compute_sector()
        self.assertIsNotNone(result)
        assert result is not None
        start, end, peak = result
        self.assertGreater(peak, 2.0)
        self.assertLess(start, 0.42)
        self.assertGreater(end, 0.42)

    def test_learned_sector_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = TrackDatabase(Path(tmp) / "tracks.db")
            db.record_learned_sector(
                "learn_track",
                display_name="Learn Track",
                sector_start=0.38,
                sector_end=0.52,
                peak_lat_g=2.1,
                sample_count=120,
            )
            start, end = resolve_high_lat_sector("Learn Track", db=db)
            self.assertAlmostEqual(start, 0.38)
            self.assertAlmostEqual(end, 0.52)

    def test_context_tracker_uses_custom_sector(self) -> None:
        ctx = DriverContextTracker()
        ctx.set_high_lat_sector(0.38, 0.52)

        class FakeIR:
            def __init__(self, state):
                self.state = state

            def __call__(self, key, default=None):
                return self.state.get(key, default)

        ir = FakeIR(
            {
                "LapDistPct": 0.45,
                "LatAccel": 14.0,
                "Speed": 55.0,
                "SteeringWheelAngle": 0.1,
                "LFtempCM": 90,
                "RFtempCM": 90,
                "PlayerCarInComponentIncidentCount": [0, 0, 0, 0],
            }
        )
        ctx.poll(
            ir,
            player_idx=0,
            lap=5,
            on_track=True,
            gap_ahead_s=2.0,
            gap_behind_s=2.0,
            is_caution=False,
        )
        self.assertEqual(ctx._high_lat_sector_start, 0.38)
        self.assertEqual(ctx._high_lat_sector_end, 0.52)

    def test_context_tracker_resets_invalid_sector(self) -> None:
        ctx = DriverContextTracker()
        ctx.set_high_lat_sector(0.9, 0.2)
        self.assertEqual(ctx._high_lat_sector_start, HIGH_LAT_SECTOR_START)
        self.assertEqual(ctx._high_lat_sector_end, HIGH_LAT_SECTOR_END)


if __name__ == "__main__":
    unittest.main()
