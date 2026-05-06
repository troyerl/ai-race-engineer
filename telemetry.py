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

    def is_connected(self) -> bool:
        # Lightweight check used by the UI status indicator.
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

        def pace_stats(times: list[float]) -> dict[str, Any]:
            # Decision features: averages + simple trend (improving/slowing).
            if not times:
                return {"n": 0}
            last3 = times[-3:]
            last5 = times[-5:]
            avg3 = sum(last3) / len(last3)
            avg5 = sum(last5) / len(last5)
            return {
                "n": len(times),
                "avg_last3_s": round(avg3, 3),
                "avg_last5_s": round(avg5, 3),
                # Positive => slowing down (worse), negative => improving.
                "trend_s": round(avg3 - avg5, 3),
            }

        def flag_state_hint(flags: Any) -> str:
            # SessionFlags is a bitmask; exact decoding is non-trivial. This hint is
            # intentionally conservative. The raw bitmask is also included.
            try:
                f = int(flags)
            except Exception:
                return "UNKNOWN"
            return "GREEN" if f == 0 else "NON_GREEN"

        for idx, pos in enumerate(positions):
            if pos <= 3 or abs(pos - player_pos) <= 2:
                if self.field_history.get(idx):
                    label = f"P{pos}" if idx != player_idx else "YOU"
                    relevant_history[label] = list(self.field_history[idx])

        fuel_level = self.ir["FuelLevel"]
        fuel_use_per_hour = max(self.ir["FuelUsePerHour"], 0.1)
        laps_remain = self.ir["SessionLapsRemain"]
        flags = self.ir["SessionFlags"]

        you_times = list(self.field_history.get(player_idx, []))
        you_pace = pace_stats(you_times)

        # Estimate fuel-per-lap using current pace (FuelUsePerHour * lap_time_hours).
        avg_lap_s = None
        if you_pace.get("n", 0) >= 1:
            avg_lap_s = you_pace.get("avg_last3_s") or you_pace.get("avg_last5_s")
        if not avg_lap_s:
            avg_lap_s = 90.0  # fallback; avoids divide-by-zero / nonsense

        fuel_use_per_lap_est = fuel_use_per_hour * (float(avg_lap_s) / 3600.0)
        fuel_use_per_lap_est = max(fuel_use_per_lap_est, 1e-6)
        laps_of_fuel_left_est = float(fuel_level) / fuel_use_per_lap_est

        can_make_to_end = None
        laps_short_on_fuel = None
        try:
            lr = int(laps_remain)
            can_make_to_end = laps_of_fuel_left_est >= lr
            laps_short_on_fuel = max(0.0, float(lr) - laps_of_fuel_left_est)
        except Exception:
            pass

        # Rival pace summary for the car directly ahead/behind (when available).
        def idx_by_pos(target_pos: int) -> int | None:
            for idx, pos in enumerate(positions):
                if pos == target_pos:
                    return idx
            return None

        rivals: dict[str, Any] = {}
        if player_pos:
            ahead_idx = idx_by_pos(player_pos - 1)
            behind_idx = idx_by_pos(player_pos + 1)
            if ahead_idx is not None and self.field_history.get(ahead_idx):
                rivals["ahead"] = {
                    "pos": player_pos - 1,
                    "pace": pace_stats(list(self.field_history[ahead_idx])),
                }
            if behind_idx is not None and self.field_history.get(behind_idx):
                rivals["behind"] = {
                    "pos": player_pos + 1,
                    "pace": pace_stats(list(self.field_history[behind_idx])),
                }

        packet = {
            "me": {
                "lap": self.ir["Lap"],
                "laps_remain": laps_remain,
                "pos": player_pos,
                "fuel": round(fuel_level, 2),
                # Raw iRacing field plus derived estimates to reduce ambiguity.
                "fuel_use_per_hour": round(self.ir["FuelUsePerHour"], 3),
                "fuel_use_per_lap_est": round(fuel_use_per_lap_est, 4),
                "laps_of_fuel_left_est": round(laps_of_fuel_left_est, 2),
                "can_make_to_end_on_fuel": can_make_to_end,
                "laps_short_on_fuel": round(laps_short_on_fuel, 2) if laps_short_on_fuel is not None else None,
                "times": you_times,
                "pace": you_pace,
                "flags": flags,
                "flag_state_hint": flag_state_hint(flags),
            },
            "race_info": {
                "pit_loss_sec": int(pit_loss_sec),
                "tire_sets_remaining": int(tire_sets_remaining),
            },
            "rivals": rivals,
            "field": relevant_history,
        }

        return json.dumps(packet, separators=(",", ":"))

