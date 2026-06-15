#!/usr/bin/env python3
"""
Run every built-in race simulation scenario and write logs to sim_logs/.

Usage (from repo root):
    python3 scripts/run_all_simulations.py
    python3 scripts/run_all_simulations.py --seed 1 --verbose
    python3 scripts/run_all_simulations.py --scenario default --scenario undercut
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.race_simulator import (  # noqa: E402
    RaceSimulator,
    SCENARIOS,
    _default_log_path,
    _load_scenarios,
    write_sim_log,
)


@dataclass
class RunResult:
    name: str
    laps: int
    log_path: Path
    elapsed_s: float
    ok: bool
    error: str | None = None


def _expected_lap_count(scenario_name: str) -> int:
    scenario = SCENARIOS[scenario_name]
    start = 1
    if scenario.start is not None:
        start = max(1, scenario.start.start_lap)
    return scenario.total_laps - start + 1


def _run_one(
    scenario_name: str,
    *,
    seed: int,
    log_dir: Path | None,
    include_packet: bool,
    verbose: bool,
    follow_strategy: bool,
) -> RunResult:
    scenario = SCENARIOS[scenario_name]
    t0 = time.perf_counter()
    try:
        sim = RaceSimulator(scenario, seed=seed, follow_strategy=follow_strategy)
        records = sim.run()
        expected = _expected_lap_count(scenario_name)
        if len(records) != expected:
            raise RuntimeError(f"expected {expected} lap records, got {len(records)}")
        if not all(rec.advice.strip() for rec in records):
            raise RuntimeError("empty advice on one or more laps")

        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{scenario_name}.log"
        else:
            log_path = _default_log_path(scenario_name)

        write_sim_log(
            records,
            scenario=scenario,
            log_path=log_path,
            include_packet=include_packet,
        )
        elapsed = time.perf_counter() - t0
        print(f"  OK  {scenario_name:24} {len(records):4d} laps  {elapsed:5.2f}s  -> {log_path.resolve()}")
        if verbose:
            print(log_path.read_text(encoding="utf-8"))
        return RunResult(scenario_name, len(records), log_path, elapsed, ok=True)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        print(f"  FAIL {scenario_name:24} {elapsed:5.2f}s  ({exc})")
        return RunResult(scenario_name, 0, Path(), elapsed, ok=False, error=str(exc))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    _load_scenarios()
    parser = argparse.ArgumentParser(
        description="Run all built-in race simulation scenarios.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        metavar="NAME",
        choices=sorted(SCENARIOS.keys()),
        help="Run only these scenarios (repeatable; default: all)",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default: 42)")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Write logs as <dir>/<scenario>.log (default: sim_logs/<scenario>_<UTC>.log)",
    )
    parser.add_argument("--packet-json", action="store_true", help="Include full packet JSON per lap")
    parser.add_argument("-v", "--verbose", action="store_true", help="Echo each log to stdout after the run")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on the first failed scenario",
    )
    parser.add_argument(
        "--follow-strategy",
        action="store_true",
        help="Execute PIT/PIT NOW directives during each scenario run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    names = args.scenarios if args.scenarios else sorted(SCENARIOS.keys())

    print(f"Running {len(names)} simulation(s) (seed={args.seed})...")
    print()

    results: list[RunResult] = []
    for name in names:
        result = _run_one(
            name,
            seed=args.seed,
            log_dir=args.log_dir,
            include_packet=args.packet_json,
            verbose=args.verbose,
            follow_strategy=args.follow_strategy,
        )
        results.append(result)
        if not result.ok and args.fail_fast:
            break

    print()
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    total_s = sum(r.elapsed_s for r in results)
    print(
        f"Finished: {len(ok)}/{len(results)} passed in {total_s:.2f}s"
        + (f" ({len(failed)} failed)" if failed else "")
    )
    if failed:
        for r in failed:
            print(f"  - {r.name}: {r.error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
