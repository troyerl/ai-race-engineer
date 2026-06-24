"""
Strategy hints derived from RaceMemory history block (packet key ``h``).

Read-only — does not alter pit/stay-out rules; enriches engineer dashboard context.
"""

from __future__ import annotations

from typing import Any


def race_memory_context_lines(telemetry: dict[str, Any]) -> str | None:
    """Compact race-context summary for the engineer detail panel."""
    h = telemetry.get("h")
    if not isinstance(h, dict):
        return None

    parts: list[str] = []
    ss = h.get("ss") if isinstance(h.get("ss"), dict) else {}

    cc = _safe_int(ss.get("cc"))
    if cc is not None and cc >= 8:
        parts.append(f"{cc} cautions — trending HEAVY pre-race branch")
    elif cc is not None and cc >= 4:
        parts.append(f"{cc} cautions — trending MODERATE pre-race branch")
    elif cc is not None and cc > 0:
        parts.append(f"{cc} caution(s) — LIGHT branch territory")

    pd5 = _safe_int(ss.get("pd5"))
    if pd5 is not None and pd5 >= 2:
        parts.append(f"+{pd5} positions last 5 laps")
    elif pd5 is not None and pd5 <= -2:
        parts.append(f"{pd5} positions last 5 laps")

    gc = _safe_int(ss.get("gc"))
    if gc is not None and gc >= 12:
        parts.append(f"{gc}-lap green run")

    pc = _safe_int(ss.get("pc"))
    if pc is not None and pc >= 2:
        parts.append(f"{pc} pit stop(s) this session")

    tr = h.get("tr") if isinstance(h.get("tr"), dict) else {}
    pace = str(tr.get("pc") or "")
    if pace == "degrading":
        parts.append("pace trend degrading")
    elif pace == "improving":
        parts.append("pace trend improving")

    ga = str(tr.get("ga") or "")
    if ga == "closing":
        parts.append("closing on car ahead")
    elif ga == "opening":
        parts.append("gap ahead opening")

    if not parts:
        return None
    return " · ".join(parts)


def race_memory_caution_bucket_hint(telemetry: dict[str, Any]) -> str | None:
    """Label matching pre-race LIGHT / MODERATE / HEAVY branch for chart comparison."""
    h = telemetry.get("h")
    if not isinstance(h, dict):
        return None
    ss = h.get("ss") if isinstance(h.get("ss"), dict) else {}
    cc = _safe_int(ss.get("cc"))
    if cc is None:
        return None
    if cc >= 8:
        return "HEAVY"
    if cc >= 4:
        return "MODERATE"
    return "LIGHT"


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None
