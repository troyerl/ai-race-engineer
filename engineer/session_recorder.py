"""
Record and replay multi-lap telemetry sessions for offline strategy regression.

Sessions are stored as JSONL (one packet per line) under ``sim_logs/sessions/``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SESSION_DIR = Path("sim_logs/sessions")


def default_session_dir() -> Path:
    return DEFAULT_SESSION_DIR


def latest_session_file(save_dir: Path | str | None = None) -> Path | None:
    """Return the most recently modified session JSONL, if any."""
    out_dir = Path(save_dir) if save_dir is not None else default_session_dir()
    if not out_dir.is_dir():
        return None
    files = sorted(out_dir.glob("session_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _slug_track(name: str | None) -> str:
    if not name:
        return "session"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return slug[:40] or "session"


def _track_from_packet(packet: dict[str, Any]) -> str | None:
    sy = packet.get("sy")
    if isinstance(sy, dict):
        wi = sy.get("WeekendInfo")
        if isinstance(wi, dict):
            return str(wi.get("TrackDisplayName") or wi.get("TrackName") or "") or None
    s = packet.get("s")
    if isinstance(s, dict):
        tn = s.get("tn")
        if tn:
            return str(tn)
    return None


def _lap_from_packet(packet: dict[str, Any]) -> int | None:
    m = packet.get("m")
    if isinstance(m, dict):
        lap = m.get("l")
        if isinstance(lap, int):
            return lap
        try:
            return int(lap)
        except (TypeError, ValueError):
            pass
    return None


class SessionRecorder:
    """Append telemetry packets to a JSONL session file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._last_lap: int | None = None

    @classmethod
    def start(
        cls,
        *,
        track_name: str | None = None,
        save_dir: Path | str | None = None,
    ) -> SessionRecorder:
        out_dir = Path(save_dir) if save_dir is not None else default_session_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = _slug_track(track_name)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"session_{slug}_{ts}.jsonl"
        path.write_text("", encoding="utf-8")
        return cls(path)

    @property
    def packet_count(self) -> int:
        if not self.path.exists():
            return 0
        count = 0
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    def append(self, packet: dict[str, Any]) -> None:
        """Write one packet as a JSON line."""
        if not isinstance(packet, dict):
            raise ValueError("packet must be a dict")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(packet, separators=(",", ":")))
            f.write("\n")

    def append_on_lap_change(self, packet: dict[str, Any]) -> bool:
        """
        Append only when ``m.l`` advances (one snapshot per completed lap boundary).

        Returns True when a new line was written.
        """
        lap = _lap_from_packet(packet)
        if lap is None:
            return False
        if self._last_lap is not None and lap == self._last_lap:
            return False
        self._last_lap = lap
        self.append(packet)
        return True


def load_session(path: Path | str) -> list[dict[str, Any]]:
    """Load packets from a JSONL session file or a single JSON array/object."""
    p = Path(path)
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > 1:
        packets: list[dict[str, Any]] = []
        for line in lines:
            row = json.loads(line)
            if isinstance(row, dict):
                packets.append(row)
        return packets

    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("session JSON array must contain packet objects")
        return [x for x in data if isinstance(x, dict)]

    if text.startswith("{"):
        obj = json.loads(text)
        if isinstance(obj, dict):
            return [obj]

    packets: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            packets.append(row)
    return packets


def replay_session(
    path: Path | str,
    *,
    include_auto_alerts: bool = True,
) -> list[dict[str, Any]]:
    """
    Replay each packet through the production strategy + auto-alert stack.

    Returns one result dict per packet with advice, call line, and alert decision.
    """
    from .auto_alert_engine import AutoMonitorState, evaluate_auto_alert_tick, reset_auto_monitor_state
    from .strategy_engine import advice_call_line, resolve_live_advice

    packets = load_session(path)
    state = reset_auto_monitor_state()
    last_call = ""
    results: list[dict[str, Any]] = []

    for packet in packets:
        m = packet.get("m") if isinstance(packet.get("m"), dict) else {}
        s = packet.get("s") if isinstance(packet.get("s"), dict) else {}
        flb = s.get("flb") if isinstance(s.get("flb"), dict) else {}
        lap = m.get("l")
        try:
            lap_i = int(lap) if lap is not None else None
        except (TypeError, ValueError):
            lap_i = None
        is_caution = bool(flb.get("yel") or flb.get("cau"))

        advice = resolve_live_advice(packet, mode="live")
        alert_decision = None
        if include_auto_alerts and lap_i is not None:
            state, alert_decision = evaluate_auto_alert_tick(
                state,
                lap=lap_i,
                is_caution=is_caution,
                telemetry=packet,
                last_delivered_call_line=last_call,
            )
            if alert_decision.deliver and alert_decision.call_line:
                last_call = alert_decision.call_line

        results.append(
            {
                "lap": lap_i,
                "is_caution": is_caution,
                "advice": advice,
                "call_line": advice_call_line(advice),
                "auto_alert": alert_decision,
            }
        )
    return results
