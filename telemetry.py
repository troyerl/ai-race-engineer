from __future__ import annotations

"""
iRacing telemetry collection + packet shaping.

Key goals:
- Keep telemetry memory bounded (no unbounded per-car accumulation).
- Provide the AI a decision-centric snapshot (you + top 3 + close rivals).
"""

import json
from collections import deque
from typing import Any

import irsdk


class TelemetryTracker:
    def __init__(self):
        self.ir = irsdk.IRSDK()
        self.ir.startup()

        # Bounded recent lap history per relevant car_idx.
        self.field_history: dict[int, deque] = {}
        self.last_recorded_lap: dict[int, int] = {}

    def ensure_connected(self) -> bool:
        if not self.ir.is_connected:
            self.ir.startup()
        return bool(self.ir.is_connected)

    def _ir_get(self, key: str, default=None):
        try:
            v = self.ir[key]
        except Exception:
            return default
        return default if v is None else v

    def update_field_history(self) -> None:
        if not self.ensure_connected():
            return

        laps = self.ir["CarIdxLap"] or []
        last_lap_times = self.ir["CarIdxLastLapTime"] or []
        positions = self.ir["CarIdxClassPosition"] or []
        player_idx = self.ir["PlayerCarIdx"]
        player_pos = self.ir["PlayerCarClassPosition"]

        # Track only the drivers we care about so memory doesn't grow with the full field.
        tracked = set()
        for idx, pos in enumerate(positions):
            if pos <= 3 or abs(pos - player_pos) <= 2:
                tracked.add(idx)
        tracked.add(player_idx)

        # Prune any cars that are no longer in the relevant slice.
        for idx in list(self.field_history.keys()):
            if idx not in tracked:
                self.field_history.pop(idx, None)
                self.last_recorded_lap.pop(idx, None)

        for i in tracked:
            if i >= len(laps) or i >= len(last_lap_times):
                continue
            curr_lap = laps[i]
            if i not in self.last_recorded_lap:
                self.last_recorded_lap[i] = curr_lap
                self.field_history[i] = deque(maxlen=5)

            # Only append a lap time when the lap counter increments.
            if curr_lap > self.last_recorded_lap[i]:
                t = last_lap_times[i]
                if t > 0:
                    self.field_history[i].append(round(t, 3))
                self.last_recorded_lap[i] = curr_lap

    def build_packet(self, tire_sets_remaining: int, pit_loss_sec: int) -> str:
        # The AI prompt expects a compact JSON payload; keep only what matters for
        # strategy decisions (position, fuel, flags, and recent pace of key rivals).
        player_idx = self.ir["PlayerCarIdx"]
        player_pos = self.ir["PlayerCarClassPosition"]

        relevant_history: dict[str, Any] = {}
        positions = self.ir["CarIdxClassPosition"] or []

        for idx, pos in enumerate(positions):
            if pos <= 3 or abs(pos - player_pos) <= 2:
                if self.field_history.get(idx):
                    label = f"P{pos}" if idx != player_idx else "YOU"
                    relevant_history[label] = list(self.field_history[idx])

        fuel_level = self.ir["FuelLevel"]
        fuel_use_per_hour = max(self.ir["FuelUsePerHour"], 0.1)

        packet = {
            "me": {
                "lap": self.ir["Lap"],
                "laps_remain": self.ir["SessionLapsRemain"],
                "pos": player_pos,
                "fuel": round(fuel_level, 2),
                "fuel_per_lap": round(self.ir["FuelUsePerHour"], 2),
                "times": list(self.field_history.get(player_idx, [])),
                "flags": self.ir["SessionFlags"],
            },
            "race_info": {
                "pit_loss_sec": int(pit_loss_sec),
                "est_laps_on_fuel": round(fuel_level / fuel_use_per_hour, 1),
                "tire_sets_remaining": int(tire_sets_remaining),
            },
            "field": relevant_history,
        }

        return json.dumps(packet, separators=(",", ":"))

