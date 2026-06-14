"""Telemetry source fed by race_link snapshots (receiver / AI machine)."""

from __future__ import annotations

import copy
import json
import time
from typing import Any

from race_memory import RaceMemory
from telemetry import _is_caution_flags


class RemoteTelemetrySource:
    STALE_SEC = 5.0

    def __init__(self):
        self._packet: dict[str, Any] | None = None
        self._last_mono = 0.0
        self._iracing = False
        self._mode = "live"
        self._track_name: str | None = None
        self._track_mi: float | None = None
        self._link_up = False
        self._memory = RaceMemory()

    def set_link_up(self, up: bool) -> None:
        self._link_up = bool(up)
        if not up:
            self._iracing = False

    def ingest_snapshot(self, msg: dict) -> None:
        pkt = msg.get("packet")
        if not isinstance(pkt, dict):
            return
        self._memory.record_snapshot(msg)
        self._packet = pkt
        self._last_mono = time.monotonic()
        self._iracing = bool(msg.get("iracing"))
        self._mode = str(msg.get("mode") or "live")
        track = msg.get("track")
        self._track_name = track if isinstance(track, str) and track.strip() else None
        tm = msg.get("track_mi")
        try:
            self._track_mi = float(tm) if tm is not None else None
        except (TypeError, ValueError):
            self._track_mi = None

    def record_advice(self, text: str) -> None:
        self._memory.record_advice(text, mode=self.ui_mode())

    def _fresh(self) -> bool:
        if not self._link_up or self._packet is None:
            return False
        return (time.monotonic() - self._last_mono) <= self.STALE_SEC

    def ensure_connected(self) -> bool:
        return self._fresh() and self._iracing

    def is_connected(self) -> bool:
        return self.ensure_connected()

    def ui_mode(self) -> str:
        if not self._fresh():
            return "live"
        return self._mode if self._mode in ("live", "strategy") else "live"

    def get_monitor_state(self) -> tuple[int | None, bool]:
        if not self._fresh() or not isinstance(self._packet, dict):
            return None, False
        m = self._packet.get("m") if isinstance(self._packet.get("m"), dict) else {}
        s = self._packet.get("s") if isinstance(self._packet.get("s"), dict) else {}
        lap = m.get("l")
        try:
            lap_i = int(lap) if lap is not None else None
        except (TypeError, ValueError):
            lap_i = None
        flb = s.get("flb") if isinstance(s.get("flb"), dict) else {}
        is_caution = bool(flb.get("yel") or flb.get("cau"))
        return lap_i, is_caution

    def update_field_history(self) -> None:
        pass

    def track_name(self) -> str | None:
        return self._track_name

    def track_length_miles(self) -> float | None:
        return self._track_mi

    def build_packet(self, tire_sets_remaining: int, pit_loss_sec: int) -> str:
        if not self._packet:
            return json.dumps({"x": {"md": "live", "u": "us", "fe": 0, "ll": 0}}, separators=(",", ":"))
        pkt = copy.deepcopy(self._packet)
        r = pkt.get("r")
        if not isinstance(r, dict):
            r = {}
            pkt["r"] = r
        r["ts"] = int(tire_sets_remaining)
        r["pl"] = int(pit_loss_sec)
        x = pkt.get("x")
        if not isinstance(x, dict):
            x = {}
            pkt["x"] = x
        x["md"] = self.ui_mode()
        pkt = self._memory.enrich_packet(pkt)
        return json.dumps(pkt, separators=(",", ":"))

    def predict_pit_position_loss(self, pit_loss_sec: int, pit_in_laps: int) -> dict[str, Any]:
        pkt = self._packet if isinstance(self._packet, dict) else {}
        m = pkt.get("m") if isinstance(pkt.get("m"), dict) else {}
        s = pkt.get("s") if isinstance(pkt.get("s"), dict) else {}
        fi = pkt.get("fi") if isinstance(pkt.get("fi"), dict) else {}
        p = m.get("p")
        flags_bools = s.get("flb") if isinstance(s.get("flb"), dict) else {}
        flag_state = str(m.get("fs") or "")
        cpi = fi.get("cpi") if isinstance(fi.get("cpi"), dict) else None

        if _is_caution_flags(flags_bools, flag_state) and cpi:
            try:
                lost_lead = int(cpi["ll"]) if cpi.get("ll") is not None else None
            except (TypeError, ValueError):
                lost_lead = None
            try:
                total_lost = int(cpi["tl"]) if cpi.get("tl") is not None else lost_lead
            except (TypeError, ValueError):
                total_lost = lost_lead
            exit_p = cpi.get("xp")
            try:
                exit_p = int(exit_p) if exit_p is not None else None
            except (TypeError, ValueError):
                exit_p = None
            if pit_in_laps > 0:
                bump = min(2, int(pit_in_laps))
                if lost_lead is not None:
                    lost_lead += bump
                if total_lost is not None:
                    total_lost += bump
                if exit_p is not None:
                    exit_p += bump
            return {
                "p": p,
                "n": int(pit_in_laps),
                "pl": int(pit_loss_sec),
                "lost": total_lost,
                "caution": True,
                "exit_p": exit_p,
                "lost_lead": lost_lead,
                "lda": cpi.get("lda"),
                "grid_rows": cpi.get("gr"),
                "lrk": cpi.get("lrk"),
            }

        gb = m.get("gb")
        lost = None
        try:
            if gb is not None and float(gb) < float(pit_loss_sec):
                lost = 1
            elif gb is not None:
                lost = 0
        except (TypeError, ValueError):
            pass
        return {"p": p, "n": int(pit_in_laps), "pl": int(pit_loss_sec), "lost": lost, "caution": False}
