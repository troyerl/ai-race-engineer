"""Unit tests for telemetry-gated clean lap tire falloff (m.fo)."""

from __future__ import annotations

import unittest

from engineer.race_constants import (
    CLEAN_LAP_DRAFT_FRAC_MAX,
    CLEAN_LAP_DRAFT_GAP_SEC,
    CLEAN_LAP_FALLOFF_MIN_SAMPLES,
    TELEMETRY_POLL_INTERVAL_S,
)
from engineer.telemetry import CleanLapGate, falloff_from_clean_paces


class FalloffFromCleanPacesTests(unittest.TestCase):
    def test_requires_min_samples(self) -> None:
        self.assertIsNone(
            falloff_from_clean_paces([30.0, 30.1, 30.2], stint_baseline_s=30.0, min_samples=4)
        )
        self.assertIsNone(falloff_from_clean_paces([30.0, 30.1], stint_baseline_s=30.0))

    def test_requires_stint_baseline(self) -> None:
        self.assertIsNone(falloff_from_clean_paces([30.0, 30.2, 30.4], stint_baseline_s=None))

    def test_median_minus_stint_baseline(self) -> None:
        times = [31.2, 31.3, 31.4, 31.5, 31.6]
        self.assertAlmostEqual(
            falloff_from_clean_paces(times, stint_baseline_s=30.0) or 0.0,
            1.4,
        )

    def test_zero_when_median_equals_baseline(self) -> None:
        self.assertEqual(
            falloff_from_clean_paces([30.0, 30.0, 30.0], stint_baseline_s=30.0),
            0.0,
        )


class CleanLapGateTests(unittest.TestCase):
    def _gate(self) -> CleanLapGate:
        gate = CleanLapGate()
        gate.sync_incident_baseline(0)
        return gate

    def _complete_clean(self, gate: CleanLapGate, lap_time: float) -> bool:
        return gate.on_lap_complete(
            lap_time,
            is_caution=False,
            reference_lap_s=lap_time,
            incident_count=0,
        )

    def _draft_skewed_polls(self, gate: CleanLapGate, *, ref_lap: float = 40.0) -> None:
        """Simulate enough draft exposure to exceed the 30% / 4s threshold."""
        limit = max(4.0, CLEAN_LAP_DRAFT_FRAC_MAX * ref_lap)
        polls = int(limit / TELEMETRY_POLL_INTERVAL_S) + 2
        t = 0.0
        for _ in range(polls):
            gate.poll(
                incident_count=0,
                gap_ahead_s=CLEAN_LAP_DRAFT_GAP_SEC - 0.1,
                is_caution=False,
                on_track=True,
                session_time_s=t,
            )
            t += TELEMETRY_POLL_INTERVAL_S

    def test_clean_laps_build_falloff(self) -> None:
        gate = self._gate()
        for lt in (30.0, 30.2, 30.4):
            self.assertTrue(self._complete_clean(gate, lt))
        self.assertAlmostEqual(gate.falloff_s or 0.0, 0.2)

    def test_cumulative_macro_wear_uses_stint_baseline(self) -> None:
        gate = self._gate()
        for lt in (30.0, 30.1, 30.2, 30.3, 30.4):
            self._complete_clean(gate, lt)
        for lt in (30.8, 30.9, 31.0, 31.1):
            self._complete_clean(gate, lt)
        for lt in (31.2, 31.3, 31.4, 31.5, 31.6):
            self._complete_clean(gate, lt)
        self.assertAlmostEqual(gate.falloff_s or 0.0, 1.4)
        self.assertEqual(gate._stint_clean_baseline_s, 30.0)

    def test_incident_lap_excluded(self) -> None:
        gate = self._gate()
        gate.poll(
            incident_count=1,
            gap_ahead_s=None,
            is_caution=False,
            on_track=True,
            session_time_s=1.0,
        )
        admitted = gate.on_lap_complete(
            29.5,
            is_caution=False,
            reference_lap_s=30.0,
            incident_count=1,
        )
        self.assertFalse(admitted)
        self.assertEqual(gate.falloff_s, 0.0)

    def test_caution_lap_excluded(self) -> None:
        gate = self._gate()
        gate.poll(
            incident_count=0,
            gap_ahead_s=None,
            is_caution=True,
            on_track=True,
            session_time_s=1.0,
        )
        admitted = gate.on_lap_complete(
            29.5,
            is_caution=True,
            reference_lap_s=30.0,
            incident_count=0,
        )
        self.assertFalse(admitted)

    def test_draft_skewed_lap_excluded(self) -> None:
        gate = self._gate()
        self._draft_skewed_polls(gate)
        admitted = gate.on_lap_complete(
            29.0,
            is_caution=False,
            reference_lap_s=40.0,
            incident_count=0,
        )
        self.assertFalse(admitted)

    def test_reset_stint_clears_buffer_and_baseline(self) -> None:
        gate = self._gate()
        for lt in (30.0, 30.2, 30.4):
            self._complete_clean(gate, lt)
        gate.reset_stint()
        self.assertEqual(len(gate.buffer), 0)
        self.assertIsNone(gate._stint_clean_baseline_s)
        self.assertIsNone(gate.falloff_s)

    def test_spike_lap_does_not_distort_after_clean_window(self) -> None:
        gate = self._gate()
        clean = [30.0, 30.2, 30.4, 30.6, 30.8]
        for lt in clean:
            self._complete_clean(gate, lt)
        before = gate.falloff_s
        self.assertAlmostEqual(before or 0.0, 0.4)
        gate.poll(
            incident_count=1,
            gap_ahead_s=None,
            is_caution=False,
            on_track=True,
            session_time_s=100.0,
        )
        gate.on_lap_complete(
            35.0,
            is_caution=False,
            reference_lap_s=30.0,
            incident_count=1,
        )
        self.assertEqual(gate.falloff_s, before)
        self.assertEqual(len(gate.buffer), CLEAN_LAP_FALLOFF_MIN_SAMPLES + 2)

    def test_dirty_fast_lap_cannot_lower_stint_baseline(self) -> None:
        gate = self._gate()
        for lt in (30.2, 30.4, 30.6):
            self._complete_clean(gate, lt)
        gate.poll(
            incident_count=1,
            gap_ahead_s=0.5,
            is_caution=False,
            on_track=True,
            session_time_s=50.0,
        )
        gate.on_lap_complete(
            29.0,
            is_caution=False,
            reference_lap_s=30.0,
            incident_count=1,
        )
        self.assertEqual(gate._stint_clean_baseline_s, 30.2)

    def test_pace_stddev_stable_clean_laps(self) -> None:
        gate = self._gate()
        for lt in (30.0, 30.05, 30.1, 30.08, 30.12):
            self._complete_clean(gate, lt)
        self.assertAlmostEqual(gate.pace_stddev_s() or 0.0, 0.05, places=1)

    def test_pace_stddev_unstable_clean_laps(self) -> None:
        gate = self._gate()
        for lt in (30.0, 30.8, 29.5, 31.0, 29.7):
            self._complete_clean(gate, lt)
        self.assertGreaterEqual(gate.pace_stddev_s() or 0.0, 0.4)

    def test_dirty_lap_does_not_affect_stability_buffer(self) -> None:
        gate = self._gate()
        for lt in (30.0, 30.1, 30.0, 30.1, 30.0):
            self._complete_clean(gate, lt)
        gate.poll(
            incident_count=1,
            gap_ahead_s=0.5,
            is_caution=False,
            on_track=True,
            session_time_s=10.0,
        )
        gate.on_lap_complete(35.0, is_caution=False, reference_lap_s=30.0, incident_count=1)
        self.assertEqual(gate.clean_lap_count, 5)
        self.assertLess(gate.pace_stddev_s() or 1.0, 0.15)


class CleanLapPaceStddevTests(unittest.TestCase):
    def test_requires_min_samples(self) -> None:
        from engineer.telemetry import clean_lap_pace_stddev

        self.assertIsNone(clean_lap_pace_stddev([30.0, 30.1, 30.2, 30.3]))


if __name__ == "__main__":
    unittest.main()
