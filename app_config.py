"""Shared ~/.ai_race_engineer.json load/save and defaults."""

from __future__ import annotations

import json
import os

from hotkey import DEFAULT_HOTKEY, normalize_hotkey
from race_link import DEFAULT_RACE_LINK_PORT

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ai_race_engineer.json")

FEATURE_REJOIN_ENV = "AIRACE_FEATURE_REJOIN"
FEATURE_VOICE_ENV = "AIRACE_FEATURE_VOICE"

REQUEST_TIMEOUT_MIN_SEC = 15
REQUEST_TIMEOUT_MAX_SEC = 60
DEFAULT_REQUEST_TIMEOUT_SEC = 30


def _migrate_token_counts(c: dict) -> None:
    if "bedrock_tokens_runtime_input" not in c:
        c["bedrock_tokens_runtime_input"] = int(c.get("bedrock_tokens_input_total", 0) or 0)
        c["bedrock_tokens_runtime_output"] = int(c.get("bedrock_tokens_output_total", 0) or 0)
    c.setdefault("bedrock_tokens_prior_input", 0)
    c.setdefault("bedrock_tokens_prior_output", 0)


def sync_legacy_total_keys(data: dict) -> None:
    pi = int(data.get("bedrock_tokens_prior_input", 0) or 0)
    po = int(data.get("bedrock_tokens_prior_output", 0) or 0)
    ri = int(data.get("bedrock_tokens_runtime_input", 0) or 0)
    ro = int(data.get("bedrock_tokens_runtime_output", 0) or 0)
    data["bedrock_tokens_input_total"] = pi + ri
    data["bedrock_tokens_output_total"] = po + ro


def merge_config_defaults(cfg: dict) -> dict:
    raw = dict(cfg) if isinstance(cfg, dict) else {}
    c = dict(raw)
    c.setdefault("clear_after_sec", 120)
    c.setdefault("voice_read_why", False)
    c.setdefault("voice_sim_enabled", True)
    c.setdefault("auto_apply_track_pit_loss", True)
    c["analyze_hotkey"] = normalize_hotkey(str(c.get("analyze_hotkey", DEFAULT_HOTKEY)))
    c.setdefault("analyze_hotkey_enabled", True)
    c.setdefault("race_link_host", "")
    c.setdefault("race_link_port", DEFAULT_RACE_LINK_PORT)
    c.setdefault("lan_display_name", "")

    if "voice_enabled" in raw:
        c["voice_enabled"] = bool(raw.get("voice_enabled"))
    else:
        c["voice_enabled"] = os.getenv(FEATURE_VOICE_ENV, "0") == "1"

    if "show_pit_impact" in raw:
        c["show_pit_impact"] = bool(raw.get("show_pit_impact"))
    else:
        c["show_pit_impact"] = os.getenv(FEATURE_REJOIN_ENV, "0") == "1"

    try:
        timeout = int(c.get("request_timeout_sec", DEFAULT_REQUEST_TIMEOUT_SEC))
    except (TypeError, ValueError):
        timeout = DEFAULT_REQUEST_TIMEOUT_SEC
    c["request_timeout_sec"] = max(REQUEST_TIMEOUT_MIN_SEC, min(REQUEST_TIMEOUT_MAX_SEC, timeout))

    _migrate_token_counts(c)
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
    sync_legacy_total_keys(merged)
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
                "voice_enabled": False,
                "voice_sim_enabled": True,
                "voice_read_why": False,
                "show_pit_impact": False,
                "request_timeout_sec": DEFAULT_REQUEST_TIMEOUT_SEC,
                "analyze_hotkey": DEFAULT_HOTKEY,
                "analyze_hotkey_enabled": True,
                "race_link_host": "",
                "race_link_port": DEFAULT_RACE_LINK_PORT,
                "lan_display_name": "",
                "bedrock_tokens_prior_input": 0,
                "bedrock_tokens_prior_output": 0,
                "bedrock_tokens_runtime_input": 0,
                "bedrock_tokens_runtime_output": 0,
                "bedrock_tokens_input_total": 0,
                "bedrock_tokens_output_total": 0,
            }
        )
    except Exception:
        pass
