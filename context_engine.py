"""
Driver / environment context (CALCULATIONS.md §16).

Track temperature wear, marbles, incidents, steering fatigue, drafting, ODI,
and tactical offensive/defensive racing metrics.
"""

from __future__ import annotations

import math
from collections import deque
from enum import IntEnum
from typing import Any, Callable

from race_constants import (
    APEX_LOSS_DEFEND_PCT,
    APEX_STEER_DELTA_MIN,
    BRAKE_ZONE_LAP_DIST_END,
    BRAKE_ZONE_LAP_DIST_START,
    DEFENSIVE_GAP_BEHIND_MAX,
    DIVEBOMB_CLOSING_RATE,
    DIVEBOMB_GAP_MAX,
    DIVEBOMB_VOICE_COOLDOWN_S,
    DRAFT_GAP_SEC,
    DRAFT_STREAK_ODI_PENALTY,
    HIGH_LAT_SECTOR_END,
    HIGH_LAT_SECTOR_START,
    INCIDENT_OT_ALERT_COUNT,
    INCIDENT_WINDOW_LAPS,
    LAT_ACCEL_OFFLINE_THRESHOLD,
    MARBLE_LAPS_REMAINING,
    ODI_PACK_PENALTY,
    ODI_RIVAL_DEGRAD_MULT,
    RIVAL_DEGRAD_TREND_MIN,
    STEER_BASELINE_ALPHA,
    STEER_SAMPLE_LAT_G,
    STEER_STD_ELEVATED,
    STEER_STD_HIGH,
    TACTICAL_ALERT_COOLDOWN_LAPS,
    THERMAL_GREASY_C,
    THERMAL_WARM_C,
    TRACK_TEMP_COOL_STINT_BONUS,
    TRACK_TEMP_HOT_STINT_PENALTY,
    TRACK_TEMP_SHIFT_THRESHOLD_C,
    TRACK_TEMP_WEAR_COEFF,
    WEAR_STABLE_FALLOFF_MAX,
    first_lap_triangular_cost_exceeds,
)

_IR_GET = Callable[[str, Any], Any]


class ThermalStressState(IntEnum):
    CLEAN = 0
    WARM = 1
    GREASY = 2


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v: Any, default: int | None = None) -> int | None:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def calculate_rolling_trend(lap_history: list[float], *, window: int = 3) -> float:
    """Lap-over-lap pace trend (seconds/lap); positive = rival slowing (tire deg)."""
    if window < 2 or len(lap_history) < window:
        return 0.0
    recent = lap_history[-window:]
    deltas = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
    if not deltas:
        return 0.0
    return sum(deltas) / len(deltas)


def _max_front_tire_temp_c(ir_get: _IR_GET) -> float | None:
    vals: list[float] = []
    for corner in ("LF", "RF"):
        for suffix in ("CM", "CL", "CR"):
            v = ir_get(f"{corner}temp{suffix}", None)
            if v is None:
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
    return max(vals) if vals else None


def adjust_tire_stint_cap_for_track_temp(
    base_cap: int,
    *,
    track_temp_c: float | None,
    ref_temp_c: float | None,
) -> tuple[int, float | None]:
    """Return (adjusted_cap, delta_T) per §16.1.1."""
    if base_cap < 1 or track_temp_c is None or ref_temp_c is None:
        return max(1, base_cap), None
    delta_t = track_temp_c - ref_temp_c
    if abs(delta_t) < TRACK_TEMP_SHIFT_THRESHOLD_C:
        return max(1, base_cap), round(delta_t, 1)
    if delta_t <= -TRACK_TEMP_SHIFT_THRESHOLD_C:
        mult = 1.0 + TRACK_TEMP_COOL_STINT_BONUS * (abs(delta_t) / 10.0)
        return max(1, int(round(base_cap * mult))), round(delta_t, 1)
    mult = 1.0 - TRACK_TEMP_HOT_STINT_PENALTY * (delta_t / 10.0)
    return max(1, int(round(base_cap * max(0.5, mult)))), round(delta_t, 1)


def compute_overtake_difficulty_index(
    *,
    pace_delta_ahead: float | None,
    pace_delta_behind: float | None,
    reentry_verdict: str | None,
    draft_streak: int,
    rival_degrad: float | None = None,
    wear_stable: bool = True,
) -> dict[str, Any]:
    """§16.4.2 ODI with optional rival-degradation stay-out bias."""
    pa = pace_delta_ahead if pace_delta_ahead is not None else 0.0
    pb = pace_delta_behind if pace_delta_behind is not None else 0.0
    score = pa
    if (reentry_verdict or "").upper() == "PACK":
        score -= ODI_PACK_PENALTY
    if draft_streak >= 3:
        score -= DRAFT_STREAK_ODI_PENALTY
    if (
        wear_stable
        and rival_degrad is not None
        and float(rival_degrad) > RIVAL_DEGRAD_TREND_MIN
    ):
        score *= ODI_RIVAL_DEGRAD_MULT
    out: dict[str, Any] = {"pa": round(pa, 3), "pb": round(pb, 3), "score": round(score, 3)}
    return out


class DriverContextTracker:
    """Stateful §16 metrics; one instance per TelemetryTracker."""

    def __init__(self) -> None:
        self._track_temp_ref_c: float | None = None
        self._marble_laps_remaining = 0
        self._draft_streak = 0
        self._draft_laps_excluded_ema = 0
        self._last_lap_drafted = False
        self._skip_next_fuel_ema = False

        self._prev_incident_components: list[int] | None = None
        self._off_track_events: deque[tuple[int, int]] = deque(maxlen=INCIDENT_WINDOW_LAPS)
        self._session_incidents = 0
        self._incident_limit: int | None = None

        self._steer_samples: list[float] = []
        self._steer_std_last: float | None = None
        self._steer_baseline: float | None = None
        self._last_lap_for_steer: int | None = None
        self._prev_steer: float | None = None

        self._last_recorded_lap: int | None = None
        self._last_lat_spike_lap: int | None = None

        # Tactical offensive / defensive state
        self.fi_odi_undercut = False
        self.fi_odi_rival_degrad = 0.0
        self.m_drv_apex_loss = 0.0
        self.m_drv_therm_stress = ThermalStressState.CLEAN
        self._apex_speed_baseline: float | None = None
        self._prev_gap_behind: float | None = None
        self._prev_tick_time: float | None = None
        self._divebomb_active = False
        self._last_defensive_line_lap: int | None = None
        self._last_cool_tires_lap: int | None = None
        self._last_divebomb_voice_at: float | None = None

    def set_incident_limit(self, limit: int | None) -> None:
        if limit is not None and limit > 0:
            self._incident_limit = int(limit)

    def reset_stint(self, *, track_temp_c: float | None = None) -> None:
        """New tire stint (pit exit)."""
        if track_temp_c is not None:
            self._track_temp_ref_c = float(track_temp_c)
        self._steer_baseline = self._steer_std_last
        self._draft_streak = 0
        self._marble_laps_remaining = 0
        self._apex_speed_baseline = None
        self.fi_odi_undercut = False

    def consume_fuel_ema_skip(self) -> bool:
        skip = self._skip_next_fuel_ema
        self._skip_next_fuel_ema = False
        return skip

    def poll(
        self,
        ir_get: _IR_GET,
        *,
        player_idx: int,
        lap: int | None,
        on_track: bool,
        gap_ahead_s: float | None = None,
        gap_behind_s: float | None = None,
        is_caution: bool,
        session_time_elapsed: float | None = None,
        ahead_lap_times: list[float] | None = None,
        pit_loss_sec: float = 45.0,
        best_lap_s: float | None = None,
        pace_delta_ahead: float | None = None,
        tire_falloff_s: float | None = None,
    ) -> None:
        if not on_track or lap is None:
            return

        lap_dist = _as_float(ir_get("LapDistPct", 0.0))
        lat_g = abs(_as_float(ir_get("LatAccel", 0.0))) / 9.81
        steer = ir_get("SteeringWheelAngle", None)
        if steer is None:
            steer = ir_get("Steering", None)
        steer_f: float | None = None
        if steer is not None:
            try:
                steer_f = float(steer)
            except (TypeError, ValueError):
                steer_f = None

        speed_mps = _as_float(ir_get("Speed", 0.0), 0.0)

        in_corner = HIGH_LAT_SECTOR_START <= lap_dist <= HIGH_LAT_SECTOR_END and lat_g >= STEER_SAMPLE_LAT_G
        if in_corner and steer_f is not None:
            self._steer_samples.append(steer_f)
            if speed_mps > 0:
                self._tick_apex_speed(speed_mps)

        in_draft = (
            not is_caution
            and gap_ahead_s is not None
            and 0 < gap_ahead_s < DRAFT_GAP_SEC
        )
        self._last_lap_drafted = in_draft

        self._tick_thermal_stress(ir_get)
        self._tick_divebomb(
            gap_behind_s,
            lap_dist=lap_dist,
            session_time_elapsed=session_time_elapsed,
        )
        self._tick_undercut_predictor(
            in_draft=in_draft,
            pace_delta_ahead=pace_delta_ahead,
            gap_ahead_s=gap_ahead_s,
            ahead_lap_times=ahead_lap_times,
            best_lap_s=best_lap_s,
            pit_loss_sec=pit_loss_sec,
        )

        if (
            in_corner
            and steer_f is not None
            and self._prev_steer is not None
            and self.m_drv_apex_loss > APEX_LOSS_DEFEND_PCT
            and abs(steer_f - self._prev_steer) > APEX_STEER_DELTA_MIN
            and gap_behind_s is not None
            and 0 < gap_behind_s < DEFENSIVE_GAP_BEHIND_MAX
        ):
            self._flag_defensive_line(lap)

        if steer_f is not None:
            self._prev_steer = steer_f

        self._tick_incidents(ir_get, lap)
        self._tick_offline(ir_get, lap, lat_g)

        if ahead_lap_times and len(ahead_lap_times) >= 3:
            self.fi_odi_rival_degrad = round(calculate_rolling_trend(ahead_lap_times, window=3), 4)

        if self._last_recorded_lap is not None and lap > self._last_recorded_lap:
            self._on_lap_complete(lap, is_caution=is_caution, tire_falloff_s=tire_falloff_s)
        self._last_recorded_lap = lap

    def _tick_apex_speed(self, speed_mps: float) -> None:
        if speed_mps <= 0:
            return
        if self._apex_speed_baseline is None or speed_mps > self._apex_speed_baseline:
            self._apex_speed_baseline = speed_mps
        baseline = self._apex_speed_baseline
        if baseline and baseline > 0:
            loss_pct = max(0.0, ((baseline - speed_mps) / baseline) * 100.0)
            self.m_drv_apex_loss = round(loss_pct, 2)

    def _tick_thermal_stress(self, ir_get: _IR_GET) -> None:
        max_temp = _max_front_tire_temp_c(ir_get)
        if max_temp is None:
            return
        if max_temp > THERMAL_GREASY_C:
            self.m_drv_therm_stress = ThermalStressState.GREASY
        elif max_temp > THERMAL_WARM_C:
            self.m_drv_therm_stress = ThermalStressState.WARM
        else:
            self.m_drv_therm_stress = ThermalStressState.CLEAN

    def _tick_divebomb(
        self,
        gap_behind_s: float | None,
        *,
        lap_dist: float,
        session_time_elapsed: float | None,
    ) -> None:
        self._divebomb_active = False
        if gap_behind_s is None:
            self._prev_gap_behind = None
            return

        in_brake_zone = BRAKE_ZONE_LAP_DIST_START <= lap_dist <= BRAKE_ZONE_LAP_DIST_END
        dt = 0.0
        if session_time_elapsed is not None and self._prev_tick_time is not None:
            dt = max(0.0, float(session_time_elapsed) - self._prev_tick_time)
        if session_time_elapsed is not None:
            self._prev_tick_time = float(session_time_elapsed)

        closing_rate = 0.0
        if dt > 0 and self._prev_gap_behind is not None:
            closing_rate = (float(gap_behind_s) - self._prev_gap_behind) / dt

        if (
            in_brake_zone
            and 0 < gap_behind_s < DIVEBOMB_GAP_MAX
            and closing_rate < DIVEBOMB_CLOSING_RATE
        ):
            self._divebomb_active = True
            if session_time_elapsed is not None:
                last = self._last_divebomb_voice_at
                if last is None or (session_time_elapsed - last) >= DIVEBOMB_VOICE_COOLDOWN_S:
                    self._last_divebomb_voice_at = float(session_time_elapsed)

        self._prev_gap_behind = float(gap_behind_s)

    def _tick_undercut_predictor(
        self,
        *,
        in_draft: bool,
        pace_delta_ahead: float | None,
        gap_ahead_s: float | None,
        ahead_lap_times: list[float] | None,
        best_lap_s: float | None,
        pit_loss_sec: float,
    ) -> None:
        self.fi_odi_undercut = False
        if not in_draft or pace_delta_ahead is None or pace_delta_ahead <= 0:
            return
        if gap_ahead_s is None or gap_ahead_s <= 0:
            return

        opponent_pace = None
        if ahead_lap_times:
            opponent_pace = ahead_lap_times[-1]
        if opponent_pace is None or opponent_pace <= 0:
            return

        fresh_out = best_lap_s if best_lap_s and best_lap_s > 0 else opponent_pace
        try:
            pit_penalty = max(0.0, float(pit_loss_sec) / max(fresh_out, 1.0)) * 0.15
        except (TypeError, ValueError):
            pit_penalty = 0.0
        projected_fresh_out = fresh_out + pit_penalty

        undercut_margin = (float(opponent_pace) - projected_fresh_out) - float(gap_ahead_s)
        if undercut_margin > 0:
            self.fi_odi_undercut = True

    def _flag_defensive_line(self, lap: int) -> None:
        self._last_defensive_line_lap = lap

    def _flag_cool_tires(self, lap: int) -> None:
        self._last_cool_tires_lap = lap

    def _tick_incidents(self, ir_get: _IR_GET, lap: int) -> None:
        comps = ir_get("PlayerCarInComponentIncidentCount", None)
        if not isinstance(comps, (list, tuple)):
            return
        ints = []
        for c in comps:
            try:
                ints.append(int(c))
            except (TypeError, ValueError):
                ints.append(0)
        if self._prev_incident_components is not None and len(ints) == len(self._prev_incident_components):
            increased = sum(max(0, ints[i] - self._prev_incident_components[i]) for i in range(len(ints)))
            if increased > 0:
                self._off_track_events.append((lap, increased))
                self._session_incidents += increased
        self._prev_incident_components = ints

    def _tick_offline(self, ir_get: _IR_GET, lap: int, lat_g: float) -> None:
        if lat_g >= LAT_ACCEL_OFFLINE_THRESHOLD and lap != self._last_lat_spike_lap:
            self._marble_laps_remaining = max(self._marble_laps_remaining, MARBLE_LAPS_REMAINING)
            self._last_lat_spike_lap = lap
        surface = ir_get("PlayerTrackSurface", None)
        try:
            if int(surface) == 0:
                self._marble_laps_remaining = max(self._marble_laps_remaining, MARBLE_LAPS_REMAINING)
        except (TypeError, ValueError):
            pass

    def _on_lap_complete(self, lap: int, *, is_caution: bool, tire_falloff_s: float | None = None) -> None:
        if self._last_lap_drafted:
            self._draft_streak += 1
            self._skip_next_fuel_ema = True
            self._draft_laps_excluded_ema += 1
        else:
            self._draft_streak = 0

        if self._marble_laps_remaining > 0 and not is_caution:
            self._marble_laps_remaining = max(0, self._marble_laps_remaining - 1)

        if self._steer_samples:
            std = _std_dev(self._steer_samples)
            self._steer_std_last = std
            if self._steer_baseline is None:
                self._steer_baseline = std
            else:
                self._steer_baseline = (
                    STEER_BASELINE_ALPHA * std + (1.0 - STEER_BASELINE_ALPHA) * self._steer_baseline
                )
            self._steer_samples.clear()

        if (
            self.m_drv_therm_stress == ThermalStressState.GREASY
            and self._prev_gap_behind is not None
            and 0 < self._prev_gap_behind < DEFENSIVE_GAP_BEHIND_MAX
        ):
            self._flag_cool_tires(lap)

    def _off_track_count_window(self, current_lap: int) -> int:
        return sum(n for lap_i, n in self._off_track_events if current_lap - lap_i < INCIDENT_WINDOW_LAPS)

    def _license_headroom(self) -> int | None:
        if self._incident_limit is None:
            return None
        return max(0, self._incident_limit - self._session_incidents)

    def _tactical_alert_ready(self, last_lap: int | None, current_lap: int | None) -> bool:
        if last_lap is None or current_lap is None:
            return True
        return (current_lap - last_lap) >= TACTICAL_ALERT_COOLDOWN_LAPS

    def build_packet_extras(
        self,
        *,
        track_temp_c: float | None,
        tire_wear_rate_est: dict[str, float] | None,
        pit_loss_sec: float,
        tire_falloff_s: float,
        fuel_stint_cap: int,
        rivals: dict[str, Any],
        you_pace: dict[str, Any],
        reentry_verdict: str | None,
        current_lap: int | None,
        gap_behind_s: float | None = None,
    ) -> dict[str, Any]:
        """Return fragments to merge into m, s, fi."""
        m_extra: dict[str, Any] = {}
        s_extra: dict[str, Any] = {}
        fi_extra: dict[str, Any] = {}

        ot_events = self._off_track_count_window(current_lap or 0)
        hr = self._license_headroom()
        inc: dict[str, Any] = {"ot": ot_events}
        if hr is not None:
            inc["hr"] = hr
            inc["tot"] = self._session_incidents
        if ot_events > 0:
            m_extra["inc"] = inc

        drv: dict[str, Any] = {}
        if self._steer_std_last is not None:
            base = max(self._steer_baseline or self._steer_std_last, 0.01)
            ssr = round(self._steer_std_last / base, 3)
            drv["ss"] = round(self._steer_std_last, 4)
            drv["ssr"] = ssr
        if self.m_drv_apex_loss > 0:
            drv["al"] = self.m_drv_apex_loss
        drv["ts"] = int(self.m_drv_therm_stress)
        drv["tsn"] = self.m_drv_therm_stress.name
        if drv:
            m_extra["drv"] = drv

        if self._marble_laps_remaining > 0:
            m_extra["mar"] = {"lr": self._marble_laps_remaining}

        if track_temp_c is not None:
            if self._track_temp_ref_c is None:
                self._track_temp_ref_c = track_temp_c
            adj_cap, delta_t = adjust_tire_stint_cap_for_track_temp(
                fuel_stint_cap,
                track_temp_c=track_temp_c,
                ref_temp_c=self._track_temp_ref_c,
            )
            tenv: dict[str, Any] = {"ref": round(self._track_temp_ref_c, 1), "cur": round(track_temp_c, 1)}
            if delta_t is not None:
                tenv["dt"] = delta_t
                tenv["tsc"] = adj_cap
            if tire_wear_rate_est:
                twr_adj = {}
                for corner, rate in tire_wear_rate_est.items():
                    try:
                        twr_adj[corner] = round(
                            float(rate) * (1.0 + TRACK_TEMP_WEAR_COEFF * (track_temp_c - self._track_temp_ref_c)),
                            4,
                        )
                    except (TypeError, ValueError):
                        continue
                if twr_adj:
                    tenv["twr_adj"] = twr_adj
            s_extra["tenv"] = tenv

        fi_extra["draft"] = {
            "on": self._last_lap_drafted,
            "streak": self._draft_streak,
            "ex": self._draft_laps_excluded_ema,
        }

        pa = pb = None
        ahead = rivals.get("ahead") if isinstance(rivals.get("ahead"), dict) else None
        behind = rivals.get("behind") if isinstance(rivals.get("behind"), dict) else None
        you_avg = _as_float(you_pace.get("avg_last3_s"), 0.0)
        if ahead and you_avg > 0:
            ah = _as_float((ahead.get("pace") or {}).get("avg_last3_s"), 0.0)
            if ah > 0:
                pa = you_avg - ah
        if behind and you_avg > 0:
            bh = _as_float((behind.get("pace") or {}).get("avg_last3_s"), 0.0)
            if bh > 0:
                pb = bh - you_avg

        wear_stable = abs(float(tire_falloff_s)) <= WEAR_STABLE_FALLOFF_MAX
        odi = compute_overtake_difficulty_index(
            pace_delta_ahead=pa,
            pace_delta_behind=pb,
            reentry_verdict=reentry_verdict,
            draft_streak=self._draft_streak,
            rival_degrad=self.fi_odi_rival_degrad,
            wear_stable=wear_stable,
        )
        odi["uc"] = self.fi_odi_undercut
        odi["rd"] = self.fi_odi_rival_degrad
        fi_extra["odi"] = odi

        tac: dict[str, Any] = {}
        if self._tactical_alert_ready(self._last_defensive_line_lap, current_lap):
            if self.m_drv_apex_loss > APEX_LOSS_DEFEND_PCT and gap_behind_s is not None and 0 < gap_behind_s < DEFENSIVE_GAP_BEHIND_MAX:
                tac["def_line"] = True
        if self._tactical_alert_ready(self._last_cool_tires_lap, current_lap):
            if (
                self.m_drv_therm_stress == ThermalStressState.GREASY
                and gap_behind_s is not None
                and 0 < gap_behind_s < DEFENSIVE_GAP_BEHIND_MAX
            ):
                tac["cool"] = True
        if self._divebomb_active:
            tac["db"] = True
        if tac:
            fi_extra["tac"] = tac

        return {"m": m_extra, "s": s_extra, "fi": fi_extra}

    def incident_push_alert(self, current_lap: int) -> bool:
        return self._off_track_count_window(current_lap) >= INCIDENT_OT_ALERT_COUNT

    def steer_fatigue_forecast_penalty(self) -> int:
        """Extra laps to pull forward tire-limited forecast when steering is rough."""
        if self._steer_std_last is None or self._steer_baseline is None:
            return 0
        ratio = self._steer_std_last / max(self._steer_baseline, 0.01)
        if ratio >= STEER_STD_HIGH:
            return 2
        if ratio >= STEER_STD_ELEVATED:
            return 1
        return 0

    def divebomb_voice_due(self, session_time_elapsed: float | None) -> bool:
        if not self._divebomb_active or session_time_elapsed is None:
            return False
        last = self._last_divebomb_voice_at
        return last is not None and abs(float(session_time_elapsed) - last) < 0.5


def _std_dev(samples: list[float]) -> float:
    if len(samples) < 2:
        return abs(samples[0]) if samples else 0.0
    mean = sum(samples) / len(samples)
    var = sum((x - mean) ** 2 for x in samples) / len(samples)
    return math.sqrt(var)


def adjusted_live_tire_stint_cap(
    *,
    pit_loss_sec: float,
    tire_falloff_s: float,
    track_temp_c: float | None,
    ref_temp_c: float | None,
    max_lap: int = 80,
) -> int | None:
    """Triangular tire cap with §16.1.1 temperature adjustment."""
    from race_constants import TIRE_COST_THRESHOLD_BUMP

    exceed = first_lap_triangular_cost_exceeds(
        pit_loss_sec + TIRE_COST_THRESHOLD_BUMP,
        max(0.01, tire_falloff_s),
        max_lap=max_lap,
    )
    if exceed is None:
        return None
    adj, _ = adjust_tire_stint_cap_for_track_temp(
        exceed,
        track_temp_c=track_temp_c,
        ref_temp_c=ref_temp_c,
    )
    return adj
