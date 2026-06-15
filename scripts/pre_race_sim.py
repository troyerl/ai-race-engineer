#!/usr/bin/env python3
"""
Run pre-race caution-branch simulations from a telemetry JSON packet.

Usage (from repo root):
    python3 scripts/pre_race_sim.py --packet path/to/packet.json
    python3 race_simulator.py --list-scenarios   # offline scenarios (separate tool)

Garage / strategy mode in the overlay uses the same engine via GET RACE STRATEGY.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engineer.pre_race_sim import format_pre_race_sim_plan, run_pre_race_simulation  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-race multi-caution strategy simulation.")
    parser.add_argument(
        "--packet",
        type=Path,
        help="Telemetry JSON packet (from overlay --packet-json or build_packet).",
    )
    parser.add_argument("--track-name", default=None, help="Override track display name.")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for sim branches.")
    args = parser.parse_args(argv)

    if args.packet is None:
        parser.error("--packet is required (save telemetry JSON from a connected session).")

    raw = args.packet.read_text(encoding="utf-8")
    telemetry = json.loads(raw)
    if not isinstance(telemetry, dict):
        print("Packet must be a JSON object.", file=sys.stderr)
        return 1

    from engineer.pre_race_sim import format_pre_race_sim_plan, run_pre_race_simulation

    ctx, green, branches = run_pre_race_simulation(
        telemetry,
        track_name=args.track_name,
        seed=args.seed,
    )
    print(format_pre_race_sim_plan(ctx, green, branches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
