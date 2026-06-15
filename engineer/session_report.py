"""
Deterministic post-race analytics from recorded session JSONL packets.

Produces stint statistics, pit position deltas, strategy call timeline, and
optional comparison against a pinned pre-race baseline plan.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .session_recorder import _track_from_packet, load_session, replay_session
from .strategy_engine import advice_call_line, parse_call_line


@dataclass(frozen=True)
class LapSnapshot:
    lap: int
    position: int | None
    fuel_laps: float | None
    is_caution: bool
    mode: str | None
    thermal: str | None
    undercut: bool
    call_action: str | None
    call_line: str | None
    on_pit_road: bool
    std_clean_s: float | None


@dataclass(frozen=True)
class PitEvent:
    lap: int
    position_at_pit: int | None
    position_after: int | None
    positions_delta: int | None


@dataclass(frozen=True)
class StopComparison:
    """Planned vs actual pit stop with lap and fuel deltas."""

    stop_index: int
    planned_lap: int | None
    actual_lap: int | None
    lap_delta: int | None
    fuel_at_planned: float | None
    fuel_at_actual: float | None
    fuel_delta_laps: float | None


@dataclass
class SessionReport:
    track_name: str
    session_path: str | None
    packet_count: int
    start_position: int | None
    finish_position: int | None
    position_delta: int | None
    laps: list[LapSnapshot] = field(default_factory=list)
    pit_events: list[PitEvent] = field(default_factory=list)
    stint_stddevs: list[float] = field(default_factory=list)
    planned_stop_laps: list[int] = field(default_factory=list)
    actual_stop_laps: list[int] = field(default_factory=list)
    stop_lap_deltas: list[int] = field(default_factory=list)
    stop_comparisons: list[StopComparison] = field(default_factory=list)
    notable_calls: list[tuple[int, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_name": self.track_name,
            "session_path": self.session_path,
            "packet_count": self.packet_count,
            "start_position": self.start_position,
            "finish_position": self.finish_position,
            "position_delta": self.position_delta,
            "planned_stop_laps": self.planned_stop_laps,
            "actual_stop_laps": self.actual_stop_laps,
            "stop_lap_deltas": self.stop_lap_deltas,
            "stop_comparisons": [
                {
                    "stop_index": sc.stop_index,
                    "planned_lap": sc.planned_lap,
                    "actual_lap": sc.actual_lap,
                    "lap_delta": sc.lap_delta,
                    "fuel_at_planned": sc.fuel_at_planned,
                    "fuel_at_actual": sc.fuel_at_actual,
                    "fuel_delta_laps": sc.fuel_delta_laps,
                }
                for sc in self.stop_comparisons
            ],
            "stint_stddevs": self.stint_stddevs,
            "pit_events": [
                {
                    "lap": e.lap,
                    "position_at_pit": e.position_at_pit,
                    "position_after": e.position_after,
                    "positions_delta": e.positions_delta,
                }
                for e in self.pit_events
            ],
            "notable_calls": [{"lap": lap, "call": call} for lap, call in self.notable_calls],
            "laps": [
                {
                    "lap": r.lap,
                    "position": r.position,
                    "fuel_laps": r.fuel_laps,
                    "is_caution": r.is_caution,
                    "mode": r.mode,
                    "thermal": r.thermal,
                    "undercut": r.undercut,
                    "call_action": r.call_action,
                    "call_line": r.call_line,
                    "on_pit_road": r.on_pit_road,
                    "std_clean_s": r.std_clean_s,
                }
                for r in self.laps
            ],
        }


def load_session_meta(session_path: Path | str) -> dict[str, Any]:
    """Load optional sidecar metadata (baseline plan, etc.)."""
    p = Path(session_path)
    meta_path = p.with_suffix(".meta.json")
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_session_meta(session_path: Path | str, meta: dict[str, Any]) -> None:
    p = Path(session_path)
    meta_path = p.with_suffix(".meta.json")
    existing = load_session_meta(p)
    merged = {**existing, **meta}
    meta_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def parse_planned_stop_laps(baseline_plan: str | None) -> list[int]:
    """Extract projected pit laps (L12, lap 24, …) from a pre-race plan text."""
    if not baseline_plan or not str(baseline_plan).strip():
        return []
    laps: set[int] = set()
    for m in re.finditer(r"\bL(\d{1,3})\b", baseline_plan, re.IGNORECASE):
        laps.add(int(m.group(1)))
    for m in re.finditer(r"\blap\s+(\d{1,3})\b", baseline_plan, re.IGNORECASE):
        val = int(m.group(1))
        if "stop" in baseline_plan[max(0, m.start() - 40) : m.start()].lower() or "pit" in baseline_plan[
            max(0, m.start() - 40) : m.start()
        ].lower():
            laps.add(val)
    return sorted(laps)


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _packet_mode(packet: dict[str, Any]) -> str | None:
    fi = packet.get("fi") if isinstance(packet.get("fi"), dict) else {}
    sm = fi.get("sm") if isinstance(fi.get("sm"), dict) else {}
    name = sm.get("n")
    return str(name) if name else None


def _packet_thermal(packet: dict[str, Any]) -> str | None:
    m = packet.get("m") if isinstance(packet.get("m"), dict) else {}
    drv = m.get("drv") if isinstance(m.get("drv"), dict) else {}
    tsn = drv.get("tsn")
    return str(tsn) if tsn else None


def _packet_undercut(packet: dict[str, Any]) -> bool:
    fi = packet.get("fi") if isinstance(packet.get("fi"), dict) else {}
    odi = fi.get("odi") if isinstance(fi.get("odi"), dict) else {}
    return bool(odi.get("uc"))


def _is_caution_packet(packet: dict[str, Any]) -> bool:
    s = packet.get("s") if isinstance(packet.get("s"), dict) else {}
    flb = s.get("flb") if isinstance(s.get("flb"), dict) else {}
    return bool(flb.get("yel") or flb.get("cau"))


def _fuel_at_lap(rows: list[LapSnapshot], lap: int) -> float | None:
    for row in rows:
        if row.lap == lap:
            return row.fuel_laps
    return None


def build_stop_comparisons(
    planned: list[int],
    actual: list[int],
    rows: list[LapSnapshot],
) -> list[StopComparison]:
    """Pair planned and actual stops; compute lap and fuel deltas."""
    count = max(len(planned), len(actual))
    out: list[StopComparison] = []
    for i in range(count):
        planned_lap = planned[i] if i < len(planned) else None
        actual_lap = actual[i] if i < len(actual) else None
        lap_delta = None
        if planned_lap is not None and actual_lap is not None:
            lap_delta = actual_lap - planned_lap
        fuel_planned = _fuel_at_lap(rows, planned_lap) if planned_lap is not None else None
        fuel_actual = _fuel_at_lap(rows, actual_lap) if actual_lap is not None else None
        fuel_delta = None
        if fuel_planned is not None and fuel_actual is not None:
            fuel_delta = round(fuel_actual - fuel_planned, 2)
        out.append(
            StopComparison(
                stop_index=i + 1,
                planned_lap=planned_lap,
                actual_lap=actual_lap,
                lap_delta=lap_delta,
                fuel_at_planned=fuel_planned,
                fuel_at_actual=fuel_actual,
                fuel_delta_laps=fuel_delta,
            )
        )
    return out


def _notable_action(action: str | None) -> bool:
    if not action:
        return False
    a = action.upper()
    return a in ("PIT", "PIT NOW", "STAY OUT", "GUARD INSIDE", "COOL TIRES")


def build_session_report(
    packets: list[dict[str, Any]],
    *,
    session_path: Path | str | None = None,
    baseline_plan: str | None = None,
    replay_by_lap: dict[int, dict[str, Any]] | None = None,
) -> SessionReport:
    """Build analytics from lap snapshots and optional replay advice."""
    track = _track_from_packet(packets[0]) if packets else None
    track_name = track or "Unknown track"

    rows: list[LapSnapshot] = []
    prev_lp: int | None = None
    prev_on_pit = False

    for packet in packets:
        m = packet.get("m") if isinstance(packet.get("m"), dict) else {}
        lap = _safe_int(m.get("l"))
        if lap is None:
            continue
        replay = (replay_by_lap or {}).get(lap, {})
        advice = str(replay.get("advice") or "")
        action, _, _ = parse_call_line(advice) if advice else ("", "", "")
        call_line = str(replay.get("call_line") or "") or (
            advice_call_line(advice) if advice else ""
        )

        on_pit = bool(m.get("pr") or m.get("ps"))
        lp = _safe_int(m.get("lp"))
        pc = m.get("pc") if isinstance(m.get("pc"), dict) else {}

        rows.append(
            LapSnapshot(
                lap=lap,
                position=_safe_int(m.get("p")),
                fuel_laps=_safe_float(m.get("fl")),
                is_caution=_is_caution_packet(packet),
                mode=_packet_mode(packet),
                thermal=_packet_thermal(packet),
                undercut=_packet_undercut(packet),
                call_action=action or None,
                call_line=call_line or None,
                on_pit_road=on_pit,
                std_clean_s=_safe_float(pc.get("std_clean_s")),
            )
        )

        prev_lp = lp
        prev_on_pit = on_pit

    rows.sort(key=lambda r: r.lap)

    start_pos = rows[0].position if rows else None
    finish_pos = rows[-1].position if rows else None
    pos_delta = None
    if start_pos is not None and finish_pos is not None:
        pos_delta = start_pos - finish_pos

    pit_events: list[PitEvent] = []
    actual_stops: list[int] = []
    for i, row in enumerate(rows):
        if not row.on_pit_road:
            continue
        prev_row = rows[i - 1] if i > 0 else None
        if prev_row and prev_row.on_pit_road:
            continue
        actual_stops.append(row.lap)
        after_pos = rows[i + 1].position if i + 1 < len(rows) else row.position
        delta = None
        if row.position is not None and after_pos is not None:
            delta = after_pos - row.position
        pit_events.append(
            PitEvent(
                lap=row.lap,
                position_at_pit=row.position,
                position_after=after_pos,
                positions_delta=delta,
            )
        )

    stint_stddevs: list[float] = []
    stint_samples: list[float] = []
    for row in rows:
        if row.on_pit_road:
            if len(stint_samples) >= 3:
                stint_stddevs.append(round(statistics.pstdev(stint_samples), 3))
            stint_samples = []
            continue
        if row.std_clean_s is not None and row.std_clean_s > 0:
            stint_samples.append(row.std_clean_s)
    if len(stint_samples) >= 3:
        stint_stddevs.append(round(statistics.pstdev(stint_samples), 3))

    planned = parse_planned_stop_laps(baseline_plan)
    stop_deltas: list[int] = []
    for i, actual in enumerate(actual_stops):
        if i < len(planned):
            stop_deltas.append(actual - planned[i])
    stop_comparisons = build_stop_comparisons(planned, actual_stops, rows)

    notable: list[tuple[int, str]] = []
    last_call = ""
    for row in rows:
        if row.call_line and row.call_line != last_call and _notable_action(row.call_action):
            notable.append((row.lap, row.call_line))
            last_call = row.call_line

    return SessionReport(
        track_name=track_name,
        session_path=str(session_path) if session_path else None,
        packet_count=len(packets),
        start_position=start_pos,
        finish_position=finish_pos,
        position_delta=pos_delta,
        laps=rows,
        pit_events=pit_events,
        stint_stddevs=stint_stddevs,
        planned_stop_laps=planned,
        actual_stop_laps=actual_stops,
        stop_lap_deltas=stop_deltas,
        stop_comparisons=stop_comparisons,
        notable_calls=notable,
    )


def analyze_session_file(
    path: Path | str,
    *,
    baseline_plan: str | None = None,
    include_replay: bool = True,
) -> SessionReport:
    """Load a JSONL session, optionally replay strategy, and build the report."""
    p = Path(path)
    packets = load_session(p)
    meta = load_session_meta(p)
    plan = baseline_plan or str(meta.get("baseline_plan") or "") or None

    replay_by_lap: dict[int, dict[str, Any]] = {}
    if include_replay and packets:
        for row in replay_session(p):
            lap = row.get("lap")
            if isinstance(lap, int):
                replay_by_lap[lap] = row

    return build_session_report(
        packets,
        session_path=p,
        baseline_plan=plan,
        replay_by_lap=replay_by_lap,
    )


def format_report_markdown(report: SessionReport) -> str:
    """Human-readable markdown report."""
    lines: list[str] = [
        f"# Post-race report — {report.track_name}",
        "",
        "## Summary",
        f"- Packets: {report.packet_count}",
    ]
    if report.start_position is not None and report.finish_position is not None:
        delta_s = ""
        if report.position_delta is not None:
            sign = "+" if report.position_delta > 0 else ""
            delta_s = f" ({sign}{report.position_delta} positions)"
        lines.append(
            f"- Grid → finish: P{report.start_position} → P{report.finish_position}{delta_s}"
        )
    if report.stint_stddevs:
        avg_std = round(sum(report.stint_stddevs) / len(report.stint_stddevs), 3)
        lines.append(f"- Stint pace σ (avg): {avg_std}s ({len(report.stint_stddevs)} stint(s))")
    if report.planned_stop_laps:
        lines.append(f"- Planned stops: {', '.join(f'L{l}' for l in report.planned_stop_laps)}")
    if report.actual_stop_laps:
        lines.append(f"- Actual stops: {', '.join(f'L{l}' for l in report.actual_stop_laps)}")
    if report.stop_lap_deltas:
        lines.append(f"- Stop timing delta vs plan: {report.stop_lap_deltas}")
    if report.stop_comparisons:
        lines.extend(["", "## Stop plan vs actual"])
        for sc in report.stop_comparisons:
            plan_s = f"L{sc.planned_lap}" if sc.planned_lap is not None else "—"
            act_s = f"L{sc.actual_lap}" if sc.actual_lap is not None else "—"
            lap_d = f", Δlap {sc.lap_delta:+d}" if sc.lap_delta is not None else ""
            fuel_d = ""
            if sc.fuel_delta_laps is not None:
                fuel_d = f", Δfuel {sc.fuel_delta_laps:+.1f} laps"
            lines.append(f"- Stop {sc.stop_index}: plan {plan_s} → actual {act_s}{lap_d}{fuel_d}")

    if report.pit_events:
        lines.extend(["", "## Pit stops"])
        for pe in report.pit_events:
            delta = pe.positions_delta
            delta_s = f", ΔP {delta:+d}" if delta is not None else ""
            lines.append(
                f"- L{pe.lap}: P{pe.position_at_pit} → P{pe.position_after}{delta_s}"
            )

    if report.notable_calls:
        lines.extend(["", "## Strategy calls"])
        for lap, call in report.notable_calls[:20]:
            lines.append(f"- L{lap}: {call}")

    lines.extend(["", "## Lap timeline", "", "| Lap | Pos | Mode | Therm | Call |", "|-----|-----|------|-------|------|"])
    for row in report.laps:
        c = "Y" if row.is_caution else ""
        call = (row.call_action or "")[:12]
        lines.append(
            f"| {row.lap} | {row.position or ''} | {row.mode or ''} | {row.thermal or ''} | {call} {c} |"
        )

    lines.extend(["", "## Executive summary", "", generate_executive_summary(report)])
    return "\n".join(lines)


def generate_executive_summary(report: SessionReport) -> str:
    """
    Deterministic plain-language summary (no LLM).

    Suitable for D3 export; optional LLM can rewrite this text offline.
    """
    parts: list[str] = []

    if report.start_position is not None and report.finish_position is not None:
        if report.position_delta is not None and report.position_delta > 0:
            parts.append(
                f"You gained {report.position_delta} position(s), "
                f"finishing P{report.finish_position} from P{report.start_position}."
            )
        elif report.position_delta is not None and report.position_delta < 0:
            parts.append(
                f"You lost {-report.position_delta} position(s), "
                f"finishing P{report.finish_position} from P{report.start_position}."
            )
        else:
            parts.append(f"You held P{report.finish_position} for the recorded session.")

    if report.stint_stddevs:
        avg = sum(report.stint_stddevs) / len(report.stint_stddevs)
        if avg <= 0.25:
            parts.append("Pace consistency was strong across stints (low clean-lap variance).")
        elif avg >= 0.45:
            parts.append("Pace varied significantly between stints — review traffic and tire management.")
        else:
            parts.append("Pace consistency was moderate; no major instability flagged.")

    if report.pit_events:
        net = sum(pe.positions_delta or 0 for pe in report.pit_events)
        if net < 0:
            parts.append(f"Pit cycles netted {abs(net)} position(s) lost on pit road.")
        elif net > 0:
            parts.append(f"Pit strategy gained {net} position(s) versus pre-stop track position.")
        else:
            parts.append("Pit stops were neutral on track position.")

    if report.stop_lap_deltas:
        early = sum(1 for d in report.stop_lap_deltas if d < 0)
        late = sum(1 for d in report.stop_lap_deltas if d > 0)
        if early:
            parts.append(f"{early} stop(s) came earlier than the base plan.")
        if late:
            parts.append(f"{late} stop(s) came later than the base plan.")

    fuel_deltas = [sc.fuel_delta_laps for sc in report.stop_comparisons if sc.fuel_delta_laps is not None]
    if fuel_deltas:
        avg_fuel = sum(fuel_deltas) / len(fuel_deltas)
        if avg_fuel > 0.3:
            parts.append(
                f"Stops averaged {avg_fuel:.1f} lap(s) more fuel remaining than at planned stop laps."
            )
        elif avg_fuel < -0.3:
            parts.append(
                f"Stops averaged {abs(avg_fuel):.1f} lap(s) less fuel remaining than planned."
            )

    if report.notable_calls:
        pit_calls = sum(1 for _, c in report.notable_calls if "PIT" in c.upper())
        if pit_calls:
            parts.append(f"The engineer triggered {pit_calls} pit-related call(s) during the session.")

    if not parts:
        return "Insufficient session data for a summary."
    return " ".join(parts)
