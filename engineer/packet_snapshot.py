"""Save live telemetry packets to JSON for offline replay and debugging."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PACKET_DIR = Path("sim_logs/packets")


def default_packet_save_dir() -> Path:
    return DEFAULT_PACKET_DIR


def _slug_track(name: str | None) -> str:
    if not name:
        return "session"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return slug[:40] or "session"


def save_telemetry_packet(
    telemetry: dict[str, Any] | str,
    *,
    track_name: str | None = None,
    save_dir: Path | str | None = None,
) -> Path:
    """
    Write a telemetry packet to ``sim_logs/packets/<track>_<UTC>.json``.

    Returns the path written. Pretty-printed JSON for human inspection / CLI replay.
    """
    if isinstance(telemetry, str):
        data = json.loads(telemetry)
    else:
        data = telemetry
    if not isinstance(data, dict):
        raise ValueError("telemetry must be a dict or JSON string")

    out_dir = Path(save_dir) if save_dir is not None else default_packet_save_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    tn = track_name
    if not tn:
        sy = data.get("sy")
        if isinstance(sy, dict):
            wi = sy.get("WeekendInfo")
            if isinstance(wi, dict):
                tn = str(wi.get("TrackDisplayName") or wi.get("TrackName") or "") or None
    slug = _slug_track(tn)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"packet_{slug}_{ts}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
