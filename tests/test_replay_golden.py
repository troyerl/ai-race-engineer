"""Golden replay regression — strategy calls must stay stable for fixture sessions."""

from __future__ import annotations

import unittest
from pathlib import Path

from engineer.session_recorder import replay_session
from engineer.strategy_engine import parse_call_line

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Captured from production engine; update intentionally when strategy rules change.
GOLDEN_ACTIONS: dict[int, str] = {
    1: "PIT NOW",
    2: "PIT NOW",
    3: "STAY OUT",
    4: "PIT NOW",
}


class ReplayGoldenTests(unittest.TestCase):
    def test_session_golden_replay_actions(self) -> None:
        path = FIXTURES / "session_golden.jsonl"
        self.assertTrue(path.is_file(), f"missing fixture {path}")
        by_lap: dict[int, str] = {}
        for row in replay_session(path):
            lap = row.get("lap")
            if not isinstance(lap, int):
                continue
            advice = str(row.get("advice") or "")
            action, _, _ = parse_call_line(advice)
            by_lap[lap] = action
        self.assertEqual(by_lap, GOLDEN_ACTIONS)


if __name__ == "__main__":
    unittest.main()
