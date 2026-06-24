"""
Load recorded sessions and chart-ready position trajectories (plan vs actual).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .session_recorder import default_session_dir, load_session
from .session_report import analyze_session_file, load_session_meta, save_session_meta


@dataclass
class PositionSeries:
    label: str
    start_position: int | None
    finish_position: int | None
    positions: list[list[int]] = field(default_factory=list)
    color_key: str = "actual"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "start_position": self.start_position,
            "finish_position": self.finish_position,
            "positions": self.positions,
            "color_key": self.color_key,
        }


@dataclass
class RaceHistoryEntry:
    session_path: Path
    track_name: str
    recorded_at: str | None
    series: list[PositionSeries] = field(default_factory=list)

    @property
    def has_chart_data(self) -> bool:
        return len(self.series) >= 2


def branch_color_key(label: str) -> str:
    low = label.lower()
    if "light" in low:
        return "light"
    if "moderate" in low:
        return "moderate"
    if "heavy" in low:
        return "heavy"
    return "plan"


def branches_meta_to_series(branches: list[dict[str, Any]]) -> list[PositionSeries]:
    out: list[PositionSeries] = []
    for br in branches:
        if not isinstance(br, dict):
            continue
        label = str(br.get("label") or "Plan")
        positions = br.get("positions") or []
        if not positions:
            continue
        out.append(
            PositionSeries(
                label=label,
                start_position=_safe_int(br.get("start_position")),
                finish_position=_safe_int(br.get("finish_position")),
                positions=_normalize_positions(positions),
                color_key=branch_color_key(label),
            )
        )
    return out


def actual_from_report_meta(
    report_laps: list[Any],
    *,
    start_position: int | None,
    finish_position: int | None,
) -> PositionSeries:
    positions: list[list[int]] = []
    for row in report_laps:
        lap = getattr(row, "lap", None)
        pos = getattr(row, "position", None)
        if lap is not None and pos is not None:
            positions.append([int(lap), int(pos)])
    return PositionSeries(
        label="ACTUAL",
        start_position=start_position,
        finish_position=finish_position,
        positions=positions,
        color_key="actual",
    )


def finalize_session_trajectory(session_path: Path | str) -> None:
    """Write actual lap-by-lap positions into session .meta.json after a race."""
    p = Path(session_path)
    if not p.is_file():
        return
    try:
        report = analyze_session_file(p, include_replay=False)
    except Exception:
        return
    if not report.laps:
        return
    actual = actual_from_report_meta(
        report.laps,
        start_position=report.start_position,
        finish_position=report.finish_position,
    )
    save_session_meta(
        p,
        {
            "actual": actual.to_dict(),
            "track_name": report.track_name,
        },
    )


def finalize_receiver_session_meta(
    session_path: Path | str,
    positions: list[list[int]],
    *,
    track_name: str | None = None,
) -> None:
    """Write actual trajectory from RaceMemory when no local JSONL laps exist."""
    if len(positions) < 2:
        return
    p = Path(session_path)
    start_pos = positions[0][1] if positions else None
    finish_pos = positions[-1][1] if positions else None
    actual = PositionSeries(
        label="ACTUAL",
        start_position=start_pos,
        finish_position=finish_pos,
        positions=positions,
        color_key="actual",
    )
    meta: dict[str, Any] = {"actual": actual.to_dict()}
    if track_name:
        meta["track_name"] = track_name
    if p.is_file():
        save_session_meta(p, meta)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        save_session_meta(p, meta)


def entry_from_session(path: Path) -> RaceHistoryEntry | None:
    if not path.is_file():
        return None
    meta = load_session_meta(path)
    track = str(meta.get("track_name") or "")
    if not track:
        try:
            packets = load_session(p)
            if packets:
                from .session_recorder import _track_from_packet

                track = _track_from_packet(packets[0]) or "Unknown track"
        except Exception:
            track = "Unknown track"

    series: list[PositionSeries] = []
    series.extend(branches_meta_to_series(meta.get("pre_race_branches") or []))

    actual_raw = meta.get("actual")
    if isinstance(actual_raw, dict) and actual_raw.get("positions"):
        series.append(
            PositionSeries(
                label="ACTUAL",
                start_position=_safe_int(actual_raw.get("start_position")),
                finish_position=_safe_int(actual_raw.get("finish_position")),
                positions=_normalize_positions(actual_raw.get("positions") or []),
                color_key="actual",
            )
        )

    if len(series) < 2:
        return None

    mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    return RaceHistoryEntry(
        session_path=path,
        track_name=track,
        recorded_at=mtime,
        series=series,
    )


def list_race_history(
    save_dir: Path | str | None = None,
    *,
    limit: int = 40,
) -> list[RaceHistoryEntry]:
    out_dir = Path(save_dir) if save_dir is not None else default_session_dir()
    if not out_dir.is_dir():
        return []
    paths = sorted(out_dir.glob("session_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    entries: list[RaceHistoryEntry] = []
    for path in paths:
        entry = entry_from_session(path)
        if entry is not None:
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def branches_to_meta(branches: list[Any]) -> list[dict[str, Any]]:
    """Serialize PreRaceBranchResult list for session meta."""
    out: list[dict[str, Any]] = []
    for br in branches:
        positions = getattr(br, "positions_by_lap", None) or []
        out.append(
            {
                "label": getattr(br, "label", ""),
                "start_position": getattr(br, "start_position", None),
                "finish_position": getattr(br, "finish_position", None),
                "caution_count": getattr(br, "caution_count", None),
                "pit_stops": getattr(br, "pit_stops", None),
                "positions": positions,
            }
        )
    return out


def _normalize_positions(raw: Any) -> list[list[int]]:
    out: list[list[int]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            lap = _safe_int(item[0])
            pos = _safe_int(item[1])
            if lap is not None and pos is not None:
                out.append([lap, pos])
        elif isinstance(item, dict):
            lap = _safe_int(item.get("lap") or item.get("l"))
            pos = _safe_int(item.get("position") or item.get("p"))
            if lap is not None and pos is not None:
                out.append([lap, pos])
    out.sort(key=lambda pair: pair[0])
    return out


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None
