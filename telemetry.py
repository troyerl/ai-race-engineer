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

        # Rough "stint" tracking so we can estimate tire wear even without a direct wear sensor.
        self._stint_start_lap: int | None = None
        self._last_on_pit_road: bool | None = None
        self._last_known_tire_wear: dict[str, Any] | None = None
        self._last_known_tire_wear_stint_laps: int | None = None
        self._last_pit_lap: int | None = None

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

    # ----------------------------
    # Small helpers (shared logic)
    # ----------------------------

    def _idx_by_class_pos(self, target_pos: int) -> int | None:
        positions = self._ir_get("CarIdxClassPosition", []) or []
        for idx, pos in enumerate(positions):
            if pos == target_pos:
                return idx
        return None

    def _avg_lap_s_for_idx(self, car_idx: int, fallback: float = 90.0) -> float:
        times = list(self.field_history.get(car_idx, []))
        if not times:
            return fallback
        last = times[-3:]
        return sum(last) / max(1, len(last))

    def _gap_est_s(self, a_idx: int | None, b_idx: int | None, *, lap_s_fallback: float) -> float | None:
        """
        Best-effort gap estimate between two car indices in seconds.

        Prefers iRacing's relative timing (`CarIdxF2Time`) when available.
        Falls back to lap distance percent delta scaled by a lap time estimate.
        """
        if a_idx is None or b_idx is None:
            return None

        car_idx_f2 = self._ir_get("CarIdxF2Time", None)
        try:
            if isinstance(car_idx_f2, (list, tuple)) and a_idx < len(car_idx_f2) and b_idx < len(car_idx_f2):
                a = float(car_idx_f2[a_idx])
                b = float(car_idx_f2[b_idx])
                if a >= 0 and b >= 0:
                    return round(abs(a - b), 2)
        except Exception:
            pass

        car_idx_dist = self._ir_get("CarIdxLapDistPct", None)
        try:
            if isinstance(car_idx_dist, (list, tuple)) and a_idx < len(car_idx_dist) and b_idx < len(car_idx_dist):
                da = float(car_idx_dist[a_idx])
                db = float(car_idx_dist[b_idx])
                if 0.0 <= da <= 1.0 and 0.0 <= db <= 1.0:
                    dd = (db - da) % 1.0
                    return round(abs(dd * float(lap_s_fallback)), 2)
        except Exception:
            pass

        return None

    def track_length_miles(self) -> float | None:
        """
        Best-effort track length in miles.

        iRacing can expose track length in different formats depending on build/API:
        - string like "2.50 mi" / "4.02 km"
        - numeric (often km-ish). We use heuristics if units are unknown.
        """
        v = self._ir_get("TrackLength", None)
        if v is None:
            return None
        try:
            if isinstance(v, str):
                s = v.strip().lower()
                if "mi" in s:
                    return float(s.replace("mi", "").strip())
                if "km" in s:
                    km = float(s.replace("km", "").strip())
                    return km * 0.621371
                # Try bare float string (assume km-ish if small).
                n = float(s)
                return n * 0.621371 if n <= 10 else n
            n = float(v)
            # Heuristic: if <=10 assume km, else already miles.
            return n * 0.621371 if n <= 10 else n
        except Exception:
            return None

    def track_name(self) -> str | None:
        for key in ("TrackDisplayName", "TrackName"):
            v = self._ir_get(key, None)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    def estimate_rejoin(self, pit_loss_sec: int) -> dict[str, Any]:
        """
        Best-effort rejoin prediction using current gaps + pit loss.
        Returns compact, UI-friendly values; does not affect the AI token budget.
        """
        player_idx = self._ir_get("PlayerCarIdx", 0)
        player_pos = self._ir_get("PlayerCarClassPosition", 0)

        lap_s = self._avg_lap_s_for_idx(player_idx, fallback=90.0)
        ahead_idx = self._idx_by_class_pos(player_pos - 1) if player_pos else None
        behind_idx = self._idx_by_class_pos(player_pos + 1) if player_pos else None

        ga = self._gap_est_s(player_idx, ahead_idx, lap_s_fallback=lap_s)
        gb = self._gap_est_s(player_idx, behind_idx, lap_s_fallback=lap_s)

        pl = float(pit_loss_sec)
        verdict = "UNKNOWN"
        # Very coarse: compare pit loss to nearest gaps.
        if gb is not None and pl > gb:
            verdict = "LIKELY_LOSE_POSITION"
        elif ga is not None and pl < ga:
            verdict = "UNDERCUT_POSSIBLE"
        elif ga is not None or gb is not None:
            verdict = "REJOIN_NEARBY"

        return {"ga": ga, "gb": gb, "pl": pit_loss_sec, "v": verdict}

    def predict_pit_position_loss(self, pit_loss_sec: int, pit_in_laps: int) -> dict[str, Any]:
        """
        Predict how many class positions you'll likely lose if you pit in N laps.

        This is intentionally coarse (race-safe, low compute):
        - Uses gap estimates + simple pace deltas to project gaps forward.
        - Evaluates only the nearby tracked slice (±2 by class position + top 3).
        """
        player_idx = self._ir_get("PlayerCarIdx", 0)
        player_pos = self._ir_get("PlayerCarClassPosition", 0)
        positions = self._ir_get("CarIdxClassPosition", []) or []

        you_avg = self._avg_lap_s_for_idx(player_idx, fallback=90.0)

        if not player_pos:
            return {"p": player_pos, "n": pit_in_laps, "pl": pit_loss_sec, "lost": None}

        # Consider cars behind within a few class positions that we likely track.
        behind = []
        for idx, pos in enumerate(positions):
            if isinstance(pos, int) and pos > player_pos and pos <= player_pos + 5:
                behind.append((idx, pos))

        lost = 0
        for idx, pos in behind:
            g = self._gap_est_s(player_idx, idx, lap_s_fallback=you_avg)
            if g is None:
                continue
            # Project gap forward N laps: gap + N * (their_avg - our_avg).
            their_avg = self._avg_lap_s_for_idx(idx, fallback=you_avg)
            projected_gap = float(g) + float(pit_in_laps) * (float(their_avg) - float(you_avg))
            if projected_gap < float(pit_loss_sec):
                lost += 1

        return {"p": player_pos, "n": int(pit_in_laps), "pl": int(pit_loss_sec), "lost": lost}

    def update_field_history(self) -> None:
        if not self.ensure_connected():
            return

        # Track pit road transitions to estimate current tire stint length.
        on_pit_road = bool(self._ir_get("OnPitRoad", False))
        lap_now = self._ir_get("Lap", None)
        if self._last_on_pit_road is None:
            self._last_on_pit_road = on_pit_road
            if isinstance(lap_now, int):
                self._stint_start_lap = lap_now
        else:
            # When we leave pit road, assume "fresh stint" (new tires likely / service).
            if self._last_on_pit_road and not on_pit_road:
                if isinstance(lap_now, int):
                    self._stint_start_lap = lap_now
                    self._last_pit_lap = lap_now
            self._last_on_pit_road = on_pit_road

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
        player_idx = self._ir_get("PlayerCarIdx", 0)
        player_pos = self._ir_get("PlayerCarClassPosition", 0)

        relevant_history: dict[str, Any] = {}
        positions = self._ir_get("CarIdxClassPosition", []) or []

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

        def flag_state(flags: Any) -> str:
            """
            Decode iRacing SessionFlags into a decision-friendly state.

            Important: SessionFlags is a bitmask and can be non-zero for many reasons
            besides caution/yellow (start lights, debris, etc.). We only return CAUTION
            when we can positively identify a caution/yellow bit.
            """
            try:
                flags_int = int(flags)
            except Exception:
                return "UNKNOWN"

            # Prefer the irsdk-provided flag definitions when available.
            Flags = getattr(irsdk, "Flags", None)
            if Flags is not None:
                try:
                    f = Flags(flags_int)
                    cautionish = []
                    for name in (
                        "caution",
                        "cautionWaving",
                        "yellow",
                        "yellowWaving",
                        "yellowWavingAtStart",
                        "fullCourseCaution",
                        "localYellow",
                    ):
                        bit = getattr(Flags, name, None)
                        if bit is not None:
                            cautionish.append(bit)
                    if cautionish and any((f & bit) for bit in cautionish):
                        return "CAUTION"
                except Exception:
                    pass

            # Fallback: without knowing exact bits, do NOT assume caution from non-zero.
            return "GREEN" if flags_int == 0 else "UNKNOWN"

        for idx, pos in enumerate(positions):
            if pos <= 3 or abs(pos - player_pos) <= 2:
                if self.field_history.get(idx):
                    label = f"P{pos}" if idx != player_idx else "YOU"
                    relevant_history[label] = list(self.field_history[idx])

        fuel_level = float(self._ir_get("FuelLevel", 0.0) or 0.0)
        fuel_use_per_hour_raw = float(self._ir_get("FuelUsePerHour", 0.0) or 0.0)
        fuel_use_per_hour = max(fuel_use_per_hour_raw, 0.1)
        laps_remain = self._ir_get("SessionLapsRemain", None)
        flags = self._ir_get("SessionFlags", 0)

        def read_tire_wear_snapshot() -> dict[str, Any] | None:
            """
            Best-effort read of tire wear/percent remaining from iRacing.
            Field names vary by car/build; if we can't find anything reliable,
            return None and let the AI use stint length as a proxy.
            """
            candidates = [
                ("LF", "LFwear"),
                ("RF", "RFwear"),
                ("LR", "LRwear"),
                ("RR", "RRwear"),
            ]
            out: dict[str, Any] = {}
            for corner, key in candidates:
                v = self._ir_get(key, None)
                if v is None:
                    continue
                try:
                    out[corner] = round(float(v), 3)
                except Exception:
                    out[corner] = v
            return out or None

        # Stint length estimate (laps since last pit-road exit).
        lap_int = self._ir_get("Lap", None)
        stint_laps = None
        if isinstance(lap_int, int) and isinstance(self._stint_start_lap, int):
            stint_laps = max(0, lap_int - self._stint_start_lap)
        last_pit_lap = self._last_pit_lap

        on_pit_road = bool(self._ir_get("OnPitRoad", False))
        tire_wear_snapshot = read_tire_wear_snapshot() if on_pit_road else None
        # Many cars/builds only update tire wear when pitting. Treat wear as "last known".
        if tire_wear_snapshot is not None:
            self._last_known_tire_wear = tire_wear_snapshot
            if isinstance(stint_laps, int):
                self._last_known_tire_wear_stint_laps = stint_laps
        tire_wear_last_known = self._last_known_tire_wear
        tire_wear_last_known_stint_laps = self._last_known_tire_wear_stint_laps

        # Derive a coarse per-lap wear rate from the last-known snapshot.
        # We do not assume units (could be % used, % remaining, etc.); the AI can
        # interpret the direction based on typical values and relative change.
        tire_wear_rate_est = None
        if tire_wear_last_known and isinstance(tire_wear_last_known_stint_laps, int) and tire_wear_last_known_stint_laps > 0:
            rates = {}
            for corner, val in tire_wear_last_known.items():
                try:
                    rates[corner] = round(float(val) / float(tire_wear_last_known_stint_laps), 4)
                except Exception:
                    continue
            tire_wear_rate_est = rates or None

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

        # --- Gap estimates (best-effort) ---
        gap_ahead_s = self._gap_est_s(player_idx, ahead_idx if player_pos else None, lap_s_fallback=float(avg_lap_s))
        gap_behind_s = self._gap_est_s(player_idx, behind_idx if player_pos else None, lap_s_fallback=float(avg_lap_s))

        # --- Degradation + pit payback ---
        best_lap_s = min(you_times) if you_times else None
        avg3_s = you_pace.get("avg_last3_s") if isinstance(you_pace, dict) else None
        falloff_s = None
        if isinstance(best_lap_s, (int, float)) and isinstance(avg3_s, (int, float)):
            falloff_s = round(float(avg3_s) - float(best_lap_s), 3)  # >0 => slower than best

        pit_payback_laps = None
        try:
            if falloff_s is not None and falloff_s > 0 and pit_loss_sec:
                pit_payback_laps = round(float(pit_loss_sec) / float(falloff_s), 1)
        except Exception:
            pass

        # --- Pit window helpers ---
        pit_window_open = can_make_to_end
        laps_until_window = None
        try:
            if laps_remain is not None:
                laps_until_window = round(max(0.0, float(laps_remain) - float(laps_of_fuel_left_est)), 2)
        except Exception:
            pass

        def drop_nones(x: Any) -> Any:
            if isinstance(x, dict):
                out = {}
                for k, v in x.items():
                    v2 = drop_nones(v)
                    if v2 is not None:
                        out[k] = v2
                return out
            if isinstance(x, list):
                return [drop_nones(v) for v in x]
            return x

        # Compact schema to reduce tokens (short keys, no nulls, rounded floats).
        packet = {
            "s": {  # session
                "st": self._ir_get("SessionState", None),
                "tr": self._ir_get("SessionTimeRemain", None),
                "lt": self._ir_get("SessionLapsTotal", None),
                "ot": self._ir_get("IsOnTrack", None),
                "ig": self._ir_get("IsInGarage", None),
            },
            "m": {  # me
                "l": self._ir_get("Lap", None),
                "lp": last_pit_lap,
                "lr": laps_remain,
                "p": player_pos,
                "fu": round(fuel_level, 2),
                "fph": round(fuel_use_per_hour_raw, 3),
                "fpl": round(fuel_use_per_lap_est, 4),
                "fl": round(laps_of_fuel_left_est, 2),
                "mk": can_make_to_end,
                "ls": round(laps_short_on_fuel, 2) if laps_short_on_fuel is not None else None,
                "t": you_times,
                "pc": you_pace,
                "fg": int(flags) if flags is not None else None,
                "fs": flag_state(flags),
                "pr": on_pit_road,
                "sl": stint_laps,
                "ga": gap_ahead_s,
                "gb": gap_behind_s,
                "bl": round(best_lap_s, 3) if isinstance(best_lap_s, (int, float)) else None,
                "fo": falloff_s,
                "pb": pit_payback_laps,
                "pw": pit_window_open,
                "pwu": laps_until_window,
                "tw": tire_wear_last_known,
                "tws": (not on_pit_road),
                "twsl": tire_wear_last_known_stint_laps,
                "twr": tire_wear_rate_est,
            },
            "r": {  # race_info
                "pl": int(pit_loss_sec),
                "ts": int(tire_sets_remaining),
                "fc": self._ir_get("FuelCapacity", None),
            },
            "rv": rivals,
            "f": relevant_history,
        }

        packet = drop_nones(packet)

        return json.dumps(packet, separators=(",", ":"))

