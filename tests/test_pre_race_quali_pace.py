"""Quali pace spread lowers inflated climb when leaders are faster."""

from __future__ import annotations

import dataclasses
import unittest

from engineer.pre_race_sim import extract_pre_race_sim_context, run_caution_branch
from tests.test_pre_race_sim import _garage_packet


class QualiPaceSpreadSimTests(unittest.TestCase):
    def test_fast_leader_grid_limits_finish_climb(self) -> None:
        tel = _garage_packet()
        ctx_quali = extract_pre_race_sim_context(tel)
        self.assertLess(
            ctx_quali.field_pace_by_position[1],
            ctx_quali.field_pace_by_position[ctx_quali.hero_position],
        )

        ctx_uniform = dataclasses.replace(
            ctx_quali,
            field_pace_by_position={ctx_quali.hero_position: ctx_quali.hero_base_pace_s},
            quali_pace_source="synthetic",
        )

        br_uniform = run_caution_branch(ctx_uniform, label="uniform", caution_count=2, seed=42)
        br_quali = run_caution_branch(ctx_quali, label="quali", caution_count=2, seed=42)

        self.assertGreaterEqual(br_quali.finish_position, br_uniform.finish_position)


if __name__ == "__main__":
    unittest.main()
