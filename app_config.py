"""Shared ~/.ai_race_engineer.json load/save and defaults."""

from __future__ import annotations

import json
import os

from hotkey import DEFAULT_HOTKEY, normalize_hotkey
from race_link import DEFAULT_RACE_LINK_PORT

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ai_race_engineer.json")

FEATURE_DEFAULTS_KEY = "feature_defaults_all_on"


def _apply_feature_toggle_defaults(c: dict, *, force_all_on: bool = False) -> None:
    toggles = (
        "voice_enabled",
        "voice_read_why",
        "show_pit_impact",
        "voice_sim_enabled",
        "auto_apply_track_pit_loss",
        "analyze_hotkey_enabled",
        "auto_pit_alerts",
    )
    if force_all_on:
        for key in toggles:
            c[key] = True
        if int(c.get("clear_after_sec", 0) or 0) <= 0:
            c["clear_after_sec"] = 120
        return
    for key in toggles:
        c.setdefault(key, True)
    c.setdefault("clear_after_sec", 120)


def merge_config_defaults(cfg: dict) -> dict:
    raw = dict(cfg) if isinstance(cfg, dict) else {}
    c = dict(raw)
    c["analyze_hotkey"] = normalize_hotkey(str(c.get("analyze_hotkey", DEFAULT_HOTKEY)))
    c.setdefault("race_link_host", "")
    c.setdefault("race_link_port", DEFAULT_RACE_LINK_PORT)
    c.setdefault("lan_display_name", "")

    if not raw.get(FEATURE_DEFAULTS_KEY):
        _apply_feature_toggle_defaults(c, force_all_on=True)
        c[FEATURE_DEFAULTS_KEY] = True
    else:
        _apply_feature_toggle_defaults(c, force_all_on=False)

    return c


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return merge_config_defaults(data if isinstance(data, dict) else {})
    except Exception:
        return merge_config_defaults({})


def write_config(data: dict) -> None:
    merged = merge_config_defaults(data)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f)
    os.replace(tmp, CONFIG_PATH)


def ensure_default_config_file() -> None:
    if os.path.exists(CONFIG_PATH):
        return
    try:
        write_config(
            {
                "clear_after_sec": 120,
                "auto_apply_track_pit_loss": True,
                "voice_enabled": True,
                "voice_sim_enabled": True,
                "voice_read_why": True,
                "show_pit_impact": True,
                "analyze_hotkey": DEFAULT_HOTKEY,
                "analyze_hotkey_enabled": True,
                "race_link_host": "",
                "race_link_port": DEFAULT_RACE_LINK_PORT,
                "lan_display_name": "",
                "auto_pit_alerts": True,
                FEATURE_DEFAULTS_KEY: True,
            }
        )
    except Exception:
        pass
