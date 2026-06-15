#!/usr/bin/env python3
"""Replay a recorded telemetry session through the production strategy engine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root without install.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engineer.session_recorder import load_session, replay_session
from engineer.strategy_engine import advice_call_line


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay sim_logs session JSONL through resolve_live_advice + auto-alerts.",
    )
    parser.add_argument(
        "session",
        type=Path,
        help="Path to session .jsonl (or single JSON packet)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print full advice text for each lap",
    )
    parser.add_argument(
        "--no-auto-alerts",
        action="store_true",
        help="Skip auto-alert evaluation",
    )
    args = parser.parse_args()

    if not args.session.is_file():
        print(f"ERROR: session file not found: {args.session}", file=sys.stderr)
        return 1

    packets = load_session(args.session)
    if not packets:
        print(f"ERROR: no packets in {args.session}", file=sys.stderr)
        return 1

    results = replay_session(
        args.session,
        include_auto_alerts=not args.no_auto_alerts,
    )
    print(f"Replayed {len(results)} packet(s) from {args.session}")
    print("-" * 72)

    for row in results:
        lap = row.get("lap")
        caution = "CAUTION" if row.get("is_caution") else "GREEN"
        call = row.get("call_line") or advice_call_line(str(row.get("advice", "")))
        alert = row.get("auto_alert")
        alert_s = ""
        if alert is not None:
            alert_s = f" | auto={alert.deliver}"
        print(f"L{lap:>3} {caution} | {call}{alert_s}")
        if args.verbose:
            print(row.get("advice", ""))
            print("-" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
