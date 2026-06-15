"""Lapped-danger pit deferral (§6.3 / §8 / §10.2 / §15)."""

from __future__ import annotations

import unittest

from strategy_engine import (
    LAPPED_DANGER_WHY,
    evaluate_and_forecast_strategy,
    format_engineer_advice,
    lapped_danger_voice_for_advice,
    run_strategy,
)
from telemetry import compute_reentry_verdict
from tests.advice_assertions import assert_advice
from tests.fixtures import inside_window_telemetry


_ON_TRACK = 3


class TestComputeReentryVerdict(unittest.TestCase):
    def test_lapped_danger_when_pit_loss_exceeds_gap_by_full_lap(self) -> None:
        lap_dist = [0.5, 0.52, 0.9, 0.1]
        surfaces = [_ON_TRACK, _ON_TRACK, _ON_TRACK, _ON_TRACK]
        laps = [20, 20, 19, 20]
        positions = [3, 1, 5, 2]
        f2 = [95.0, 0.0, 120.0, 40.0]

        rej = compute_reentry_verdict(
            0,
            lap_dist=lap_dist,
            surfaces=surfaces,
            laps=laps,
            positions=positions,
            f2_times=f2,
            pit_loss_sec=190.0,
            lap_s=90.0,
        )
        self.assertEqual(rej["v"], "LAPPED_DANGER")
        self.assertGreaterEqual(rej.get("pll", 0), 1)
        self.assertAlmostEqual(rej.get("gtl", 0), 95.0)

    def test_clean_when_gap_covers_pit_loss(self) -> None:
        lap_dist = [0.5, 0.52]
        surfaces = [_ON_TRACK, _ON_TRACK]
        laps = [20, 20]
        positions = [2, 1]
        f2 = [200.0, 0.0]

        rej = compute_reentry_verdict(
            0,
            lap_dist=lap_dist,
            surfaces=surfaces,
            laps=laps,
            positions=positions,
            f2_times=f2,
            pit_loss_sec=45.0,
            lap_s=90.0,
        )
        self.assertEqual(rej["v"], "CLEAN")
        self.assertNotIn("pll", rej)

    def test_ldw_counts_lap_down_cars_in_window(self) -> None:
        lap_dist = [0.48, 0.97, 0.9]
        surfaces = [_ON_TRACK, _ON_TRACK, _ON_TRACK]
        laps = [20, 19, 20]
        positions = [4, 6, 1]
        f2 = [300.0, 310.0, 0.0]

        rej = compute_reentry_verdict(
            0,
            lap_dist=lap_dist,
            surfaces=surfaces,
            laps=laps,
            positions=positions,
            f2_times=f2,
            pit_loss_sec=45.0,
            lap_s=90.0,
        )
        self.assertGreaterEqual(rej.get("ldw", 0), 1)


class TestLappedDangerStrategy(unittest.TestCase):
    def test_stay_out_inside_window_with_lapped_danger(self) -> None:
        tel = inside_window_telemetry(
            m={"fl": 2.0, "mk": False},
            fi={
                "rej": {
                    "v": "LAPPED_DANGER",
                    "bv": "CLEAN",
                    "pll": 1,
                    "gtl": 30.0,
                    "n": 0,
                }
            },
        )
        result = evaluate_and_forecast_strategy(tel)
        imm = result["immediate_directive"]
        self.assertEqual(imm["ACTION"], "STAY OUT")
        self.assertEqual(imm["WHY"], LAPPED_DANGER_WHY)

    def test_fuel_critical_overrides_lapped_danger(self) -> None:
        tel = inside_window_telemetry(
            m={"fl": 0.8, "mk": False},
            fi={"rej": {"v": "LAPPED_DANGER", "pll": 2}},
        )
        result = evaluate_and_forecast_strategy(tel)
        self.assertEqual(result["immediate_directive"]["ACTION"], "PIT NOW")

    def test_untrusted_fuel_ignores_lapped_danger_uses_base_density(self) -> None:
        tel = inside_window_telemetry(
            x={"md": "live", "u": "us", "fe": 0, "ll": 1},
            m={
                "fl": 2.0,
                "mk": False,
                "fpl": 0.2,
                "ful": 0.4,
                "fcq": "low",
                "l": 20,
                "lp": 1,
                "sl": 19,
            },
            fi={"rej": {"v": "LAPPED_DANGER", "bv": "CLEAN", "pll": 1}},
        )
        result = evaluate_and_forecast_strategy(tel)
        self.assertEqual(result["immediate_directive"]["ACTION"], "PIT NOW")

    def test_dashboard_and_voice_for_lapped_danger(self) -> None:
        tel = inside_window_telemetry(
            m={"fl": 2.0, "mk": False},
            fi={"rej": {"v": "LAPPED_DANGER", "pll": 1, "gtl": 20.0}},
        )
        advice = run_strategy(tel)
        assert_advice(
            self,
            advice,
            action="STAY OUT",
            why_contains="LAP DOWN",
            trigger="TRACK",
            conf="M",
        )
        voice = lapped_danger_voice_for_advice(advice)
        self.assertIsNotNone(voice)
        self.assertIn("lap down", voice.lower())

    def test_lapped_danger_beats_clean_window_pit(self) -> None:
        tel = inside_window_telemetry(
            m={"fl": 1.8, "sl": 12, "lp": 8, "l": 20},
            fi={"rej": {"v": "LAPPED_DANGER", "bv": "CLEAN", "pll": 1}},
        )
        advice = format_engineer_advice(
            "STAY OUT",
            "THIS LAP",
            "NONE",
            LAPPED_DANGER_WHY,
            trigger="TRACK",
            conf="M",
            telemetry=tel,
        )
        self.assertIn("DELAYING PIT STOP", advice)
        self.assertIn("LAP DOWN", advice)


if __name__ == "__main__":
    unittest.main()
