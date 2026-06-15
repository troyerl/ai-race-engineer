"""Shared telemetry packet fixtures for strategy tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def base_live_telemetry(**overrides: Any) -> dict[str, Any]:
    """Minimal trustworthy green-flag packet for live strategy evaluation."""
    packet: dict[str, Any] = {
        "x": {"md": "live", "u": "us", "fe": 1, "ll": 1},
        "s": {
            "st": "Racing",
            "ty": "Race",
            "lt": 50,
            "flb": {},
            "ttc": 28.0,
        },
        "m": {
            "l": 10,
            "lr": 40,
            "sl": 8,
            "lp": 5,
            "fl": 8.0,
            "fcq": "hi",
            "mk": True,
            "pb": 2,
            "pw": True,
            "fo": 0.08,
            "gb": 3.0,
            "pc": {"avg_last3_s": 90.0, "n": 5},
        },
        "r": {"pl": 45, "ts": 3, "ftl": 25, "fc": 20.0},
        "fi": {"rej": {"v": "CLEAN", "n": 0}},
        "rv": {},
    }
    _deep_update(packet, overrides)
    return packet


def inside_window_telemetry(**overrides: Any) -> dict[str, Any]:
    """Fuel inside pit payback window with clean reentry."""
    default_m = {
        "l": 20,
        "lr": 30,
        "sl": 12,
        "lp": 8,
        "fl": 1.8,
        "fcq": "hi",
        "mk": False,
        "pb": 2,
        "pw": True,
        "fo": 0.08,
        "gb": 2.5,
        "pc": {"avg_last3_s": 90.0, "n": 8},
    }
    m_override = overrides.pop("m", None)
    if isinstance(m_override, dict):
        default_m.update(m_override)
    return base_live_telemetry(m=default_m, **overrides)


def _deep_update(base: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = deepcopy(value)
