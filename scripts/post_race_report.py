#!/usr/bin/env python3
"""Generate a deterministic post-race report from a recorded session JSONL file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engineer.session_recorder import default_session_dir, latest_session_file
from engineer.session_report import analyze_session_file, format_report_markdown


def _resolve_session(path: str | None) -> Path:
    if path:
        p = Path(path)
        if not p.is_file():
            raise SystemExit(f"Session file not found: {p}")
        return p
    latest = latest_session_file()
    if latest is None:
        raise SystemExit(
            "No session file given and none found under sim_logs/sessions/. "
            "Enable session_recording in ~/.ai_race_engineer.json or pass a path."
        )
    return latest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic post-race report from a session JSONL recording.",
    )
    parser.add_argument(
        "session",
        nargs="?",
        help="Path to session_*.jsonl (default: newest in sim_logs/sessions/)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write markdown report to this file (default: stdout)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON analytics instead of markdown",
    )
    parser.add_argument(
        "--baseline",
        help="Optional pre-race plan text (overrides .meta.json baseline)",
    )
    parser.add_argument(
        "--no-replay",
        action="store_true",
        help="Skip strategy replay (faster; call timeline may be empty)",
    )
    args = parser.parse_args(argv)

    session_path = _resolve_session(args.session)
    report = analyze_session_file(
        session_path,
        baseline_plan=args.baseline,
        include_replay=not args.no_replay,
    )

    if args.json:
        payload = json.dumps(report.to_dict(), indent=2)
        if args.output:
            Path(args.output).write_text(payload + "\n", encoding="utf-8")
            print(f"Wrote JSON report to {args.output}", file=sys.stderr)
        else:
            print(payload)
    else:
        md = format_report_markdown(report)
        if args.output:
            Path(args.output).write_text(md + "\n", encoding="utf-8")
            print(f"Wrote markdown report to {args.output}", file=sys.stderr)
        else:
            print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
