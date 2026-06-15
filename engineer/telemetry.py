from __future__ import annotations

"""
iRacing telemetry collection + packet shaping.

Key goals:
- Keep telemetry memory bounded (no unbounded per-car accumulation).
- Provide the AI a decision-centric snapshot (you + top 3 + close rivals).
"""

import json
from collections import deque
from statistics import median
from typing import Any

import irsdk

from .race_constants import (
    CLEAN_LAP_BUFFER_DEPTH,
    CLEAN_LAP_DRAFT_FRAC_MAX,
    CLEAN_LAP_DRAFT_GAP_SEC,
    CLEAN_LAP_FALLOFF_MIN_SAMPLES,
    DEFAULT_AVG_LAP_S,
    FUEL_EMA_ALPHA,
    FUEL_LAPS_CLAMP_MULTIPLIER,
    FUEL_LAPS_CLAMP_OFFSET,
    HERD_POSITION_WINDOW,
    IRSDK_TIRE_SETS_UNLIMITED,
    LAP_HISTORY_DEPTH,
    REENTRY_WINDOW_PCT,
    TELEMETRY_POLL_INTERVAL_S,
    _L_TO_US_GAL,
    clamp_avg_lap_seconds,
    clamp_lap_distance_pct,
    clamp_nonneg_liters,
    clamp_positive_rate,
    resolve_combined_burn_rate,
    triangular_payback_lap,
    wrap_lap_distance_delta,
)
from .context_engine import DriverContextTracker
from .pre_race_strategy import find_race_session, race_lap_total_from_session

# AI-facing packet uses US customary fuel units (internal math stays liters + kg/h).
_KG_TO_LB = 2.204622621847693185

DEFAULT_TIRE_SETS_FALLBACK = 2


def parse_tire_sets_available(value: Any) -> int | None:
    """
    Parse iRacing TireSetsAvailable (and similar) for remaining full sets.

    Returns None when unlimited (255) or invalid — caller keeps manual UI default.
    """
    try:
        if value is None:
            return None
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v < 0 or v >= IRSDK_TIRE_SETS_UNLIMITED:
        return None
    return v


def _liters_to_us_gal(x: float) -> float:
    return float(x) * _L_TO_US_GAL


# iRacing uses large sentinels (commonly 32767) for unknown / unlimited lap counts.
_IRACING_LAPS_UNKNOWN_MIN = 32000


def _sanitize_lap_count(v: Any) -> int | None:
    if v is None:
        return None
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    if i < 0 or i >= _IRACING_LAPS_UNKNOWN_MIN:
        return None
    return i


# CarIdxTrackSurface / TrkLoc (irsdk)
_TRK_NOT_IN_WORLD = -1
_TRK_OFF_TRACK = 0
_TRK_IN_STALL = 1
_TRK_APPROACHING_PITS = 2
_TRK_ON_TRACK = 3

_SURFACE_CODES = {
    _TRK_NOT_IN_WORLD: "nw",
    _TRK_OFF_TRACK: "off",
    _TRK_IN_STALL: "stall",
    _TRK_APPROACHING_PITS: "ap",
    _TRK_ON_TRACK: "ot",
}

_PITTING_SURFACES = frozenset({_TRK_IN_STALL, _TRK_APPROACHING_PITS})


def _surface_code(v: Any) -> str | None:
    try:
        return _SURFACE_CODES.get(int(v))
    except (TypeError, ValueError):
        return None


def _is_pitting_surface(v: Any) -> bool:
    try:
        return int(v) in _PITTING_SURFACES
    except (TypeError, ValueError):
        return False


def _parse_session_flags_bools(flags: Any, pits_open: Any = None) -> dict[str, bool]:
    """Decode SessionFlags + PitsOpen into explicit booleans for strategy logic."""
    out = {"yel": False, "cau": False, "grn": False, "pcl": False}
    try:
        flags_int = int(flags)
    except (TypeError, ValueError):
        flags_int = 0

    Flags = getattr(irsdk, "Flags", None)
    if Flags is not None:
        for name, key in (
            ("yellow", "yel"),
            ("yellow_waving", "yel"),
            ("caution", "cau"),
            ("caution_waving", "cau"),
            ("green", "grn"),
        ):
            bit = getattr(Flags, name, None)
            if bit is not None:
                try:
                    if flags_int & int(bit):
                        out[key] = True
                except (TypeError, ValueError):
                    pass

    if pits_open is not None:
        try:
            out["pcl"] = not bool(pits_open)
        except Exception:
            pass
    return out


def _c_to_f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return round(float(v) * 9.0 / 5.0 + 32.0, 1)
    except (TypeError, ValueError):
        return None


def _read_corner_tire_wear(ir_get, corner: str) -> float | None:
    """Average L/M/R tread remaining % for a corner, or PitSv*offset when in the stall."""
    offset_key = f"PitSv{corner}Toffset"
    off = ir_get(offset_key, None)
    try:
        if off is not None:
            return round(float(off), 3)
    except (TypeError, ValueError):
        pass
    vals: list[float] = []
    for part in ("L", "M", "R"):
        v = ir_get(f"{corner}wear{part}", None)
        if v is None:
            v = ir_get(f"{corner}wear", None)
        try:
            if v is not None:
                vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    return round(sum(vals) / len(vals), 3)


def _read_tire_wear_corners(ir_get) -> dict[str, float] | None:
    out: dict[str, float] = {}
    for corner in ("LF", "RF", "LR", "RR"):
        w = _read_corner_tire_wear(ir_get, corner)
        if w is not None:
            out[corner] = w
    return out or None


def _leader_lap_from_car_laps(laps: list | None) -> int | None:
    best: int | None = None
    if not isinstance(laps, (list, tuple)):
        return None
    for x in laps:
        try:
            v = int(x)
            if v > 0:
                best = v if best is None else max(best, v)
        except (TypeError, ValueError):
            continue
    return best


def _find_leader_idx(
    laps: list | None,
    positions: list | None,
    surfaces: list | None,
) -> int | None:
    """Car index of the race leader (class P1 on the lead lap, on track)."""
    leader_lap = _leader_lap_from_car_laps(laps)
    if leader_lap is None or not isinstance(positions, (list, tuple)):
        return None
    for idx, pos in enumerate(positions):
        try:
            p = int(pos)
        except (TypeError, ValueError):
            continue
        if p != 1:
            continue
        lap_v = None
        if isinstance(laps, (list, tuple)) and idx < len(laps):
            try:
                lap_v = int(laps[idx])
            except (TypeError, ValueError):
                lap_v = None
        if lap_v is None or lap_v < leader_lap:
            continue
        if isinstance(surfaces, (list, tuple)) and idx < len(surfaces):
            try:
                if int(surfaces[idx]) not in (_TRK_ON_TRACK, _TRK_APPROACHING_PITS):
                    continue
            except (TypeError, ValueError):
                pass
        return idx
    return None


def _gap_to_leader_seconds(
    player_idx: int,
    leader_idx: int | None,
    *,
    laps: list | None,
    lap_dist: list | None,
    f2_times: list | None,
    lap_s: float,
) -> float | None:
    """Seconds behind the race leader (best-effort; None when unknown)."""
    if leader_idx is None or player_idx is None:
        return None
    lap_s = clamp_avg_lap_seconds(lap_s)
    if lap_s <= 0:
        return None

    if isinstance(f2_times, (list, tuple)) and player_idx < len(f2_times):
        try:
            g = float(f2_times[player_idx])
            if g >= 0:
                return round(g, 2)
        except (TypeError, ValueError):
            pass

    leader_lap = None
    player_lap = None
    if isinstance(laps, (list, tuple)):
        if leader_idx < len(laps):
            try:
                leader_lap = int(laps[leader_idx])
            except (TypeError, ValueError):
                leader_lap = None
        if player_idx < len(laps):
            try:
                player_lap = int(laps[player_idx])
            except (TypeError, ValueError):
                player_lap = None

    if leader_lap is None or player_lap is None or leader_lap <= 0 or player_lap <= 0:
        return None

    lap_delta = max(0, leader_lap - player_lap)
    if lap_delta == 0 and isinstance(lap_dist, (list, tuple)):
        if player_idx < len(lap_dist) and leader_idx < len(lap_dist):
            ld_p = clamp_lap_distance_pct(lap_dist[player_idx])
            ld_l = clamp_lap_distance_pct(lap_dist[leader_idx])
            if ld_p is not None and ld_l is not None:
                dd = wrap_lap_distance_delta(ld_p, ld_l)
                if dd >= 0:
                    return round(dd * lap_s, 2)
                return round((1.0 + dd) * lap_s, 2)

    if lap_delta > 0:
        on_lap = 0.0
        if isinstance(lap_dist, (list, tuple)) and player_idx < len(lap_dist) and leader_idx < len(lap_dist):
            ld_p = clamp_lap_distance_pct(lap_dist[player_idx])
            ld_l = clamp_lap_distance_pct(lap_dist[leader_idx])
            if ld_p is not None and ld_l is not None:
                on_lap = max(0.0, (1.0 - ld_p + ld_l) % 1.0) * lap_s
        if lap_delta == 1 and on_lap > 0:
            return round(on_lap, 2)
        return round(lap_delta * lap_s + on_lap, 2)

    return None


def compute_reentry_verdict(
    player_idx: int,
    *,
    lap_dist: list | None,
    surfaces: list | None,
    laps: list | None,
    positions: list | None,
    f2_times: list | None,
    pit_loss_sec: float,
    lap_s: float,
    traffic_window: float = REENTRY_WINDOW_PCT,
) -> dict[str, Any]:
    """
    Project pit reentry traffic (§6.3) and green-flag lap-down risk (§8 extension).

    When pit loss exceeds gap to the leader, sets ``v=LAPPED_DANGER`` and ``pll`` ≥ 1.
    """
    if not isinstance(lap_dist, (list, tuple)) or player_idx >= len(lap_dist):
        return {"v": "UNKNOWN"}

    player_dist = clamp_lap_distance_pct(lap_dist[player_idx])
    lap_s = clamp_avg_lap_seconds(lap_s)
    if player_dist is None or lap_s <= 0:
        return {"v": "UNKNOWN"}

    leader_lap = _leader_lap_from_car_laps(laps)
    pit_frac = (float(pit_loss_sec) / lap_s) % 1.0
    reentry_dist = (player_dist + pit_frac) % 1.0
    pack = 0
    lap_down_in_window = 0

    for idx, dist in enumerate(lap_dist):
        if idx == player_idx:
            continue
        d = clamp_lap_distance_pct(dist)
        if d is None:
            continue
        if isinstance(surfaces, (list, tuple)) and idx < len(surfaces):
            try:
                if int(surfaces[idx]) not in (_TRK_ON_TRACK, _TRK_APPROACHING_PITS):
                    continue
            except (TypeError, ValueError):
                pass
        dd = abs(wrap_lap_distance_delta(reentry_dist, d))
        if dd <= traffic_window:
            pack += 1
            if leader_lap is not None and isinstance(laps, (list, tuple)) and idx < len(laps):
                try:
                    car_lap = int(laps[idx])
                    if 0 < car_lap < leader_lap:
                        lap_down_in_window += 1
                except (TypeError, ValueError):
                    pass

    if pack == 0:
        base_verdict = "CLEAN"
    elif pack >= 2:
        base_verdict = "PACK"
    else:
        base_verdict = "TRAFFIC"

    leader_idx = _find_leader_idx(laps, positions, surfaces)
    gap_to_leader = _gap_to_leader_seconds(
        player_idx,
        leader_idx,
        laps=laps,
        lap_dist=lap_dist,
        f2_times=f2_times,
        lap_s=lap_s,
    )

    projected_laps_lost = 0
    if gap_to_leader is not None and float(pit_loss_sec) > gap_to_leader:
        projected_laps_lost = int((float(pit_loss_sec) - gap_to_leader) // lap_s)

    verdict = base_verdict
    if projected_laps_lost >= 1:
        verdict = "LAPPED_DANGER"

    out: dict[str, Any] = {
        "v": verdict,
        "bv": base_verdict,
        "n": pack,
        "dp": round(reentry_dist, 4),
    }
    if gap_to_leader is not None:
        out["gtl"] = gap_to_leader
    if projected_laps_lost > 0:
        out["pll"] = projected_laps_lost
    if lap_down_in_window > 0:
        out["ldw"] = lap_down_in_window
    if leader_lap is not None:
        out["ll"] = leader_lap
    return out


def _car_is_pitting(idx: int, on_pit_road: list | None, surfaces: list | None) -> bool:
    if isinstance(on_pit_road, (list, tuple)) and idx < len(on_pit_road):
        try:
            if bool(on_pit_road[idx]):
                return True
        except (TypeError, ValueError):
            pass
    if isinstance(surfaces, (list, tuple)) and idx < len(surfaces):
        return _is_pitting_surface(surfaces[idx])
    return False


def _is_caution_flags(flags_bools: dict[str, bool] | None, flag_state: str | None = None) -> bool:
    if isinstance(flags_bools, dict) and (flags_bools.get("cau") or flags_bools.get("yel")):
        return True
    return str(flag_state or "").upper() == "CAUTION"


def compute_caution_pit_impact(
    player_idx: int,
    player_pos: int,
    positions: list,
    laps: list | None,
    *,
    pit_loss_sec: int,
    pit_in_laps: int,
    hd: dict | None = None,
    on_pit_road: list | None = None,
    surfaces: list | None = None,
) -> dict[str, Any]:
    """
    Estimate caution-pit cost: lead-lap positions lost, on-track class position, restart rows.

    Under yellow, lap-down cars that stay out can inflate class position loss vs lead-lap
    competitors — restart grid is usually lead-lap order first.
    """
    base = {"p": player_pos, "n": int(pit_in_laps), "pl": int(pit_loss_sec), "caution": True}
    leader_lap = _leader_lap_from_car_laps(laps)
    if leader_lap is None or player_pos <= 0 or not isinstance(positions, (list, tuple)):
        return {**base, "lost": None}

    def _lap_at(idx: int) -> int | None:
        if not isinstance(laps, (list, tuple)) or idx >= len(laps):
            return None
        try:
            v = int(laps[idx])
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    def _pos_at(idx: int) -> int | None:
        if idx >= len(positions):
            return None
        try:
            v = int(positions[idx])
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    lead_positions: list[int] = []
    for idx in range(len(positions)):
        if idx == player_idx:
            continue
        lap_v = _lap_at(idx)
        pos_v = _pos_at(idx)
        if lap_v is not None and pos_v is not None and lap_v >= leader_lap:
            lead_positions.append(pos_v)

    player_lap = _lap_at(player_idx)
    if player_lap is not None and player_lap >= leader_lap:
        lead_positions.append(player_pos)
    lead_positions = sorted(set(lead_positions))

    try:
        player_rank_lead = lead_positions.index(player_pos) + 1
    except ValueError:
        player_rank_lead = player_pos

    lead_ahead = [idx for idx in range(len(positions)) if _pos_at(idx) is not None and _pos_at(idx) < player_pos and (_lap_at(idx) or 0) >= leader_lap]
    lap_down_ahead = [
        idx
        for idx in range(len(positions))
        if idx != player_idx and _pos_at(idx) is not None and _pos_at(idx) < player_pos and (_lap_at(idx) or 0) < leader_lap
    ]
    behind_stay_out = [
        idx
        for idx in range(len(positions))
        if idx != player_idx and _pos_at(idx) is not None and _pos_at(idx) > player_pos
        and not _car_is_pitting(idx, on_pit_road, surfaces)
    ]

    herd = hd if isinstance(hd, dict) else {}
    try:
        pra = float(herd.get("pra", 0.35))
    except (TypeError, ValueError):
        pra = 0.35
    try:
        prb = float(herd.get("prb", 0.25))
    except (TypeError, ValueError):
        prb = 0.25
    pra = max(0.0, min(1.0, pra))
    prb = max(0.0, min(1.0, prb))

    stay_out_lead_ahead = sum(1 for idx in lead_ahead if not _car_is_pitting(idx, on_pit_road, surfaces))
    if lead_ahead:
        stay_out_lead_ahead = max(stay_out_lead_ahead, int(round(len(lead_ahead) * (1.0 - pra))))

    pitting_lead_ahead = sum(1 for idx in lead_ahead if _car_is_pitting(idx, on_pit_road, surfaces))
    if lead_ahead and pitting_lead_ahead == 0:
        pitting_lead_ahead = int(round(len(lead_ahead) * pra))

    lap_down_stay_out_ahead = sum(
        1 for idx in lap_down_ahead if not _car_is_pitting(idx, on_pit_road, surfaces)
    )
    behind_pass = len(behind_stay_out)
    lap_down_on_track = sum(
        1 for idx in behind_stay_out if (_lap_at(idx) or 0) < leader_lap
    ) + lap_down_stay_out_ahead

    exit_rank_lead = min(len(lead_positions) if lead_positions else player_rank_lead, stay_out_lead_ahead + pitting_lead_ahead + 1)
    lost_lead = max(0, exit_rank_lead - player_rank_lead)

    if pit_in_laps > 0:
        # Under caution, laps until pit rarely change the herd much — small bump.
        lost_lead += min(2, int(pit_in_laps))

    exit_class = min(len(positions), player_pos + lost_lead + behind_pass)
    total_lost = max(0, exit_class - player_pos)
    grid_rows = max(0, int(round(lost_lead * 0.75)))

    return {
        **base,
        "lost": total_lost,
        "exit_p": exit_class,
        "lost_lead": lost_lead,
        "lda": lap_down_on_track,
        "grid_rows": grid_rows,
        "lrk": player_rank_lead,
        "pra": round(pra, 2),
        "prb": round(prb, 2),
    }


def _compact_caution_pit_intel(impact: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for src, dst in (
        ("lrk", "lrk"),
        ("lda", "lda"),
        ("lost_lead", "ll"),
        ("lost", "tl"),
        ("exit_p", "xp"),
        ("grid_rows", "gr"),
        ("pra", "pra"),
        ("prb", "prb"),
    ):
        v = impact.get(src)
        if v is not None:
            out[dst] = v
    return out


def _player_incident_count(ir_get) -> int:
    """Best-effort total incident count for the player car."""
    raw = ir_get("PlayerCarMyIncidentCount", None)
    if raw is not None:
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    comps = ir_get("PlayerCarInComponentIncidentCount", None) or []
    total = 0
    for x in comps:
        try:
            total += max(0, int(x))
        except (TypeError, ValueError):
            continue
    return total


def falloff_from_clean_paces(
    clean_times: list[float],
    *,
    stint_baseline_s: float | None,
    min_samples: int = CLEAN_LAP_FALLOFF_MIN_SAMPLES,
) -> float | None:
    """
    Median-of-clean-laps tire falloff proxy vs stint anchor (§3.4 telemetry gate).

    Uses rolling median for noise rejection and subtracts the fastest clean lap
    of the entire stint (not min of the rolling window) so macro wear accumulates.

    Returns None when fewer than min_samples clean laps are buffered or baseline
    is unset.
    """
    if len(clean_times) < min_samples or stint_baseline_s is None:
        return None
    rep = median(clean_times)
    raw = float(rep) - float(stint_baseline_s)
    if raw > 0:
        return round(raw, 3)
    return 0.0


class CleanLapGate:
    """
    Telemetry-gated filter: only clean green-flag laps in clean air update m.fo.

    Mid-lap polls track incidents, caution exposure, and draft time; on lap
    complete the lap time is admitted to a rolling buffer when all gates pass.
    """

    def __init__(self) -> None:
        self.buffer: deque[float] = deque(maxlen=CLEAN_LAP_BUFFER_DEPTH)
        self._stint_clean_baseline_s: float | None = None
        self._lap_dirty = False
        self._lap_had_caution = False
        self._draft_time_s = 0.0
        self._last_incident_count = 0
        self._last_session_t: float | None = None
        self._falloff_s: float | None = None
        self._incident_baseline_set = False

    def reset_stint(self) -> None:
        self.buffer.clear()
        self._stint_clean_baseline_s = None
        self._falloff_s = None
        self._reset_mid_lap()
        self._last_session_t = None

    def sync_incident_baseline(self, incident_count: int) -> None:
        self._last_incident_count = max(0, int(incident_count))
        self._incident_baseline_set = True

    def poll(
        self,
        *,
        incident_count: int,
        gap_ahead_s: float | None,
        is_caution: bool,
        on_track: bool,
        session_time_s: float | None,
    ) -> None:
        if not on_track:
            return
        if not self._incident_baseline_set:
            self.sync_incident_baseline(incident_count)
        if incident_count > self._last_incident_count:
            self._lap_dirty = True
        if is_caution:
            self._lap_had_caution = True
        if gap_ahead_s is not None and 0 < float(gap_ahead_s) < CLEAN_LAP_DRAFT_GAP_SEC:
            dt = TELEMETRY_POLL_INTERVAL_S
            if session_time_s is not None and self._last_session_t is not None:
                dt = max(0.0, float(session_time_s) - self._last_session_t)
                if dt > 2.0:
                    dt = TELEMETRY_POLL_INTERVAL_S
            self._draft_time_s += dt
        if session_time_s is not None:
            self._last_session_t = float(session_time_s)

    def on_lap_complete(
        self,
        lap_time_s: float,
        *,
        is_caution: bool,
        reference_lap_s: float,
        incident_count: int,
    ) -> bool:
        """Commit lap time when clean; refresh cached falloff. Returns admission."""
        draft_limit = max(4.0, CLEAN_LAP_DRAFT_FRAC_MAX * max(reference_lap_s, 1.0))
        is_draft_skewed = self._draft_time_s > draft_limit
        lap_caution = bool(is_caution or self._lap_had_caution)
        admitted = (
            not self._lap_dirty
            and not lap_caution
            and not is_draft_skewed
            and lap_time_s > 0
        )
        if admitted:
            lap_t = round(float(lap_time_s), 3)
            self.buffer.append(lap_t)
            if self._stint_clean_baseline_s is None or lap_t < self._stint_clean_baseline_s:
                self._stint_clean_baseline_s = lap_t
        fo = falloff_from_clean_paces(
            list(self.buffer),
            stint_baseline_s=self._stint_clean_baseline_s,
        )
        self._falloff_s = fo if fo is not None else 0.0
        self._last_incident_count = max(0, int(incident_count))
        self._reset_mid_lap()
        return admitted

    def _reset_mid_lap(self) -> None:
        self._lap_dirty = False
        self._lap_had_caution = False
        self._draft_time_s = 0.0

    @property
    def falloff_s(self) -> float | None:
        return self._falloff_s


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

        # Lap-boundary fuel deltas → liters/lap EMA (robust vs idle/pit-road instantaneous kg/h).
        self._fuel_prev_lap: int | None = None
        self._fuel_prev_level_L: float | None = None
        self._fuel_per_lap_ema_L: float | None = None
        self._fuel_last_lap_burn_L: float | None = None
        self._fuel_burn_samples_L: deque = deque(maxlen=12)
        self._last_is_caution: bool | None = None

        # Session pit-lane time (PitiExtTime proxy when SDK omits it).
        self._cumulative_pit_time_s: float = 0.0
        self._last_pit_tick_session_t: float | None = None
        self._on_pit_road_for_time: bool = False

        # Tire wear snapshots logged in the pit box (wear vs track temp trends).
        self._pit_tire_wear_log: deque = deque(maxlen=8)
        self._logged_stall_this_stop: bool = False

        # Opponent surface transitions (herd dynamics).
        self._last_car_surface: dict[int, int] = {}

        self._ctx = DriverContextTracker()
        self._incident_limit_loaded = False
        self._clean_lap_gate = CleanLapGate()

    def ensure_connected(self) -> bool:
        if not self.ir.is_connected:
            self.ir.startup()
        return bool(self.ir.is_connected)

    def is_connected(self) -> bool:
        # Lightweight check used by the UI status indicator.
        return bool(self.ir.is_connected)

    def read_tire_set_limit(self) -> int | None:
        """Race-weekend dry tire set allocation (PlayerCarDryTireSetLimit)."""
        if not self.is_connected():
            return None
        return parse_tire_sets_available(self._ir_get("PlayerCarDryTireSetLimit", None))

    def read_tire_sets_remaining(self) -> int | None:
        """Remaining tire sets — uses race allocation in practice/qualifying."""
        if not self.is_connected():
            return None
        limit = self.read_tire_set_limit()
        session_type = str(self._ir_get("SessionType", "") or "").strip().lower()
        session_name = str(self._ir_get("SessionName", "") or "").strip().upper()
        in_race = session_type == "race" or session_name == "RACE"
        if not in_race and limit is not None:
            return limit
        return parse_tire_sets_available(self._ir_get("TireSetsAvailable", None))

    def ui_mode(self) -> str:
        """
        Live in-car calls vs pre-race garage / off-track planning.

        Heuristic: connected and not physically on track → pre-race strategy.
        """
        if not self.is_connected():
            return "live"
        if self._ir_get("IsOnTrack", False):
            return "live"
        return "strategy"

    def get_monitor_state(self) -> tuple[int | None, bool]:
        """Current player lap and whether yellow/caution is active (for auto alerts)."""
        if not self.is_connected():
            return None, False
        lap = _sanitize_lap_count(self._ir_get("Lap", None))
        flags = _parse_session_flags_bools(self._ir_get("SessionFlags", 0), self._ir_get("PitsOpen", None))
        is_caution = bool(flags.get("yel") or flags.get("cau"))
        return lap, is_caution

    def _ir_get(self, key: str, default=None):
        try:
            v = self.ir[key]
        except Exception:
            return default
        return default if v is None else v

    def _tick_fuel_per_lap_ema(
        self,
        lap_int: Any,
        fuel_level: float,
        fuel_capacity: Any,
        *,
        exclude_sample: bool = False,
    ) -> None:
        """Track liters consumed per lap from lap-boundary deltas (more reliable than idle kg/h)."""
        if not isinstance(lap_int, int):
            return
        pl = self._fuel_prev_lap
        pf = self._fuel_prev_level_L
        fc_f = None
        try:
            if fuel_capacity is not None:
                fc_f = float(fuel_capacity)
        except Exception:
            fc_f = None

        if pl is not None and pf is not None:
            if lap_int < pl:
                self._fuel_per_lap_ema_L = None
                self._fuel_last_lap_burn_L = None
            elif lap_int > pl and not exclude_sample:
                dl = lap_int - pl
                df = pf - fuel_level
                if dl >= 1 and df > 0.02:
                    sample = df / float(dl)
                    if fc_f is None or sample <= fc_f * 0.98:
                        alpha = FUEL_EMA_ALPHA
                        ema = self._fuel_per_lap_ema_L
                        self._fuel_per_lap_ema_L = sample if ema is None else (alpha * sample + (1 - alpha) * ema)
                        if dl == 1:
                            self._fuel_last_lap_burn_L = round(df, 4)
                            self._fuel_burn_samples_L.append(self._fuel_last_lap_burn_L)

        self._fuel_prev_lap = lap_int
        self._fuel_prev_level_L = fuel_level

    def _reset_fuel_ema_for_green_restart(self, lap_now: Any, fuel_level: float) -> None:
        """Purge caution-skewed burn history when yellow lifts (§2.3)."""
        self._fuel_per_lap_ema_L = None
        self._fuel_last_lap_burn_L = None
        self._fuel_burn_samples_L.clear()
        if isinstance(lap_now, int):
            self._fuel_prev_lap = lap_now
            self._fuel_prev_level_L = fuel_level

    def _tracked_indices(self, player_idx: int, player_pos: int, positions: list) -> set[int]:
        tracked = {player_idx}
        for idx, pos in enumerate(positions):
            try:
                p = int(pos)
            except (TypeError, ValueError):
                continue
            if p <= 0:
                continue
            if p <= 3 or abs(p - player_pos) <= 2:
                tracked.add(idx)
        return tracked

    def _projected_reentry_gap(
        self,
        player_idx: int,
        *,
        lap_dist: list | None,
        surfaces: list | None,
        pit_loss_sec: float,
        lap_s: float,
        laps: list | None = None,
        positions: list | None = None,
        f2_times: list | None = None,
        traffic_window: float = REENTRY_WINDOW_PCT,
    ) -> dict[str, Any]:
        """Estimate whether a pit now merges into clean air or lap-down danger."""
        return compute_reentry_verdict(
            player_idx,
            lap_dist=lap_dist,
            surfaces=surfaces,
            laps=laps,
            positions=positions,
            f2_times=f2_times,
            pit_loss_sec=float(pit_loss_sec),
            lap_s=float(lap_s),
            traffic_window=traffic_window,
        )

    def _pitting_ratios(
        self,
        player_idx: int,
        player_pos: int,
        positions: list,
        surfaces: list | None,
        on_pit_road: list | None,
        *,
        window: int = HERD_POSITION_WINDOW,
        caution: bool,
    ) -> dict[str, Any]:
        if not caution or player_pos <= 0:
            return {}
        ahead_total = ahead_pit = behind_total = behind_pit = 0
        for idx, pos in enumerate(positions):
            try:
                p = int(pos)
            except (TypeError, ValueError):
                continue
            if p <= 0 or idx == player_idx:
                continue
            pitting = False
            if isinstance(on_pit_road, (list, tuple)) and idx < len(on_pit_road):
                try:
                    pitting = bool(on_pit_road[idx])
                except (TypeError, ValueError):
                    pitting = False
            if not pitting and isinstance(surfaces, (list, tuple)) and idx < len(surfaces):
                pitting = _is_pitting_surface(surfaces[idx])
            if p < player_pos and p >= player_pos - window:
                ahead_total += 1
                if pitting:
                    ahead_pit += 1
            elif p > player_pos and p <= player_pos + window:
                behind_total += 1
                if pitting:
                    behind_pit += 1

        out: dict[str, Any] = {}
        if ahead_total:
            out["pra"] = round(ahead_pit / ahead_total, 2)
        if behind_total:
            out["prb"] = round(behind_pit / behind_total, 2)
        return out

    def _herd_counts(
        self,
        player_idx: int,
        player_pos: int,
        positions: list,
        surfaces: list | None,
        *,
        window: int = HERD_POSITION_WINDOW,
    ) -> dict[str, int]:
        counts = {"apa": 0, "apb": 0, "isa": 0, "isb": 0}
        if player_pos <= 0 or not isinstance(surfaces, (list, tuple)):
            return counts
        for idx, pos in enumerate(positions):
            try:
                p = int(pos)
            except (TypeError, ValueError):
                continue
            if p <= 0 or idx == player_idx or idx >= len(surfaces):
                continue
            try:
                surf = int(surfaces[idx])
            except (TypeError, ValueError):
                continue
            if p < player_pos and p >= player_pos - window:
                if surf == _TRK_APPROACHING_PITS:
                    counts["apa"] += 1
                elif surf == _TRK_IN_STALL:
                    counts["isa"] += 1
            elif p > player_pos and p <= player_pos + window:
                if surf == _TRK_APPROACHING_PITS:
                    counts["apb"] += 1
                elif surf == _TRK_IN_STALL:
                    counts["isb"] += 1
        return counts

    def _build_field_intel(
        self,
        player_idx: int,
        player_pos: int,
        positions: list,
        *,
        pit_loss_sec: int,
        lap_s: float,
        flags_bools: dict[str, bool],
    ) -> dict[str, Any]:
        tracked = self._tracked_indices(player_idx, player_pos, positions)
        lap_dist = self._ir_get("CarIdxLapDistPct", []) or []
        f2 = self._ir_get("CarIdxF2Time", []) or []
        surfaces = self._ir_get("CarIdxTrackSurface", []) or []
        on_pit = self._ir_get("CarIdxOnPitRoad", []) or []

        dist_map: dict[str, float] = {}
        f2_map: dict[str, float] = {}
        surf_map: dict[str, str] = {}
        for idx in sorted(tracked):
            pos = positions[idx] if idx < len(positions) else None
            try:
                p = int(pos)
            except (TypeError, ValueError):
                continue
            if p <= 0:
                continue
            label = "YOU" if idx == player_idx else f"P{p}"
            if idx < len(lap_dist):
                try:
                    dist_map[label] = round(float(lap_dist[idx]), 4)
                except (TypeError, ValueError):
                    pass
            if idx < len(f2):
                try:
                    f2_map[label] = round(float(f2[idx]), 3)
                except (TypeError, ValueError):
                    pass
            if idx < len(surfaces):
                sc = _surface_code(surfaces[idx])
                if sc:
                    surf_map[label] = sc

        fi: dict[str, Any] = {}
        if dist_map:
            fi["dist"] = dist_map
        if f2_map:
            fi["f2"] = f2_map
        if surf_map:
            fi["surf"] = surf_map

        hd = self._herd_counts(player_idx, player_pos, positions, surfaces)
        pr = self._pitting_ratios(
            player_idx,
            player_pos,
            positions,
            surfaces,
            on_pit,
            caution=bool(flags_bools.get("cau") or flags_bools.get("yel")),
        )
        hd.update(pr)
        if any(hd.values()):
            fi["hd"] = hd

        rej = self._projected_reentry_gap(
            player_idx,
            lap_dist=lap_dist,
            surfaces=surfaces,
            pit_loss_sec=float(pit_loss_sec),
            lap_s=float(lap_s),
            laps=self._ir_get("CarIdxLap", []) or [],
            positions=positions,
            f2_times=f2,
        )
        if rej.get("v") != "UNKNOWN":
            fi["rej"] = rej
        if _is_caution_flags(flags_bools):
            cpi = compute_caution_pit_impact(
                player_idx,
                int(player_pos),
                positions,
                self._ir_get("CarIdxLap", []) or [],
                pit_loss_sec=int(pit_loss_sec),
                pit_in_laps=0,
                hd=fi.get("hd") if isinstance(fi, dict) else None,
                on_pit_road=on_pit,
                surfaces=surfaces,
            )
            compact = _compact_caution_pit_intel(cpi)
            if compact:
                fi["cpi"] = compact
        return fi

    # ----------------------------
    # Small helpers (shared logic)
    # ----------------------------

    def _maybe_load_incident_limit(self) -> None:
        if self._incident_limit_loaded:
            return
        self._incident_limit_loaded = True
        sy = self.get_session_yaml_dict()
        if not isinstance(sy, dict):
            return
        wi = sy.get("WeekendInfo")
        if not isinstance(wi, dict):
            return
        opts = wi.get("WeekendOptions")
        if not isinstance(opts, dict):
            return
        for key in ("IncidentLimit", "IncidentLimitPerRace"):
            try:
                lim = int(opts.get(key))
                if lim > 0:
                    self._ctx.set_incident_limit(lim)
                    return
            except (TypeError, ValueError):
                continue

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
                da = clamp_lap_distance_pct(car_idx_dist[a_idx])
                db = clamp_lap_distance_pct(car_idx_dist[b_idx])
                if da is not None and db is not None:
                    dd = wrap_lap_distance_delta(da, db)
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

    def get_session_yaml_dict(self) -> dict[str, Any] | None:
        """Parse iRacing SessionInfo YAML for pre-race strategy."""
        if not self.is_connected():
            return None
        try:
            import yaml

            raw = self.ir["SessionInfo"]
            if not raw:
                return None
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                return None
            slim: dict[str, Any] = {}
            for key in ("WeekendInfo", "SessionInfo", "DriverInfo"):
                if key in data:
                    slim[key] = data[key]
            return slim or data
        except Exception:
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
        Predict class positions lost if you pit in N laps.

        Under caution, separates lead-lap loss from lap-down traffic and estimates
        restart-grid rows.
        """
        player_idx = self._ir_get("PlayerCarIdx", 0)
        player_pos = self._ir_get("PlayerCarClassPosition", 0)
        positions = self._ir_get("CarIdxClassPosition", []) or []
        laps = self._ir_get("CarIdxLap", []) or []
        flags = self._ir_get("SessionFlags", 0)
        pits_open = self._ir_get("PitsOpen", None)
        flags_bools = _parse_session_flags_bools(flags, pits_open)
        flag_state = "CAUTION" if _is_caution_flags(flags_bools) else "GREEN"

        if not player_pos:
            return {"p": player_pos, "n": pit_in_laps, "pl": pit_loss_sec, "lost": None}

        if _is_caution_flags(flags_bools, flag_state):
            you_avg = self._avg_lap_s_for_idx(player_idx, fallback=90.0)
            fi = self._build_field_intel(
                player_idx,
                player_pos,
                positions,
                pit_loss_sec=int(pit_loss_sec),
                lap_s=float(you_avg),
                flags_bools=flags_bools,
            )
            return compute_caution_pit_impact(
                player_idx,
                int(player_pos),
                positions,
                laps,
                pit_loss_sec=int(pit_loss_sec),
                pit_in_laps=int(pit_in_laps),
                hd=fi.get("hd") if isinstance(fi, dict) else None,
                on_pit_road=self._ir_get("CarIdxOnPitRoad", []) or [],
                surfaces=self._ir_get("CarIdxTrackSurface", []) or [],
            )

        you_avg = self._avg_lap_s_for_idx(player_idx, fallback=90.0)

        behind = []
        for idx, pos in enumerate(positions):
            if isinstance(pos, int) and pos > player_pos and pos <= player_pos + 5:
                behind.append((idx, pos))

        lost = 0
        for idx, pos in behind:
            g = self._gap_est_s(player_idx, idx, lap_s_fallback=you_avg)
            if g is None:
                continue
            their_avg = self._avg_lap_s_for_idx(idx, fallback=you_avg)
            projected_gap = float(g) + float(pit_in_laps) * (float(their_avg) - float(you_avg))
            if projected_gap < float(pit_loss_sec):
                lost += 1

        return {"p": player_pos, "n": int(pit_in_laps), "pl": int(pit_loss_sec), "lost": lost, "caution": False}

    def update_field_history(self) -> None:
        if not self.ensure_connected():
            return

        # Track pit road transitions to estimate current tire stint length.
        on_pit_road = bool(self._ir_get("OnPitRoad", False))
        in_stall = bool(self._ir_get("PlayerCarInPitStall", False))
        lap_now = self._ir_get("Lap", None)
        session_t = self._ir_get("SessionTime", None)
        try:
            session_t_f = float(session_t) if session_t is not None else None
        except (TypeError, ValueError):
            session_t_f = None

        if on_pit_road and session_t_f is not None:
            if self._on_pit_road_for_time and self._last_pit_tick_session_t is not None:
                self._cumulative_pit_time_s += max(0.0, session_t_f - self._last_pit_tick_session_t)
            self._on_pit_road_for_time = True
            self._last_pit_tick_session_t = session_t_f
        else:
            self._on_pit_road_for_time = False
            self._last_pit_tick_session_t = None
            self._logged_stall_this_stop = False

        if in_stall and not self._logged_stall_this_stop:
            wear = _read_tire_wear_corners(self._ir_get)
            if wear:
                entry: dict[str, Any] = {"l": lap_now}
                try:
                    tt = self._ir_get("TrackTempCrew", None) or self._ir_get("TrackTemp", None)
                    at = self._ir_get("AirTemp", None)
                    if tt is not None:
                        entry["tt"] = round(float(tt), 1)
                    if at is not None:
                        entry["at"] = round(float(at), 1)
                except (TypeError, ValueError):
                    pass
                entry.update(wear)
                self._pit_tire_wear_log.append(entry)
                self._logged_stall_this_stop = True

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
                fuel_level = clamp_nonneg_liters(self._ir_get("FuelLevel", 0.0))
                fuel_cap = self._ir_get("FuelCapacity", None)
                try:
                    cap_f = float(fuel_cap) if fuel_cap is not None else None
                except (TypeError, ValueError):
                    cap_f = None
                if cap_f and cap_f > 0 and fuel_level >= cap_f * 0.88:
                    self._fuel_per_lap_ema_L = None
                    self._fuel_last_lap_burn_L = None
                    if isinstance(lap_now, int):
                        self._fuel_prev_lap = lap_now
                        self._fuel_prev_level_L = fuel_level
                tt = self._ir_get("TrackTempCrew", None) or self._ir_get("TrackTemp", None)
                try:
                    tt_f = float(tt) if tt is not None else None
                except (TypeError, ValueError):
                    tt_f = None
                self._ctx.reset_stint(track_temp_c=tt_f)
                self._clean_lap_gate.reset_stint()
            self._last_on_pit_road = on_pit_road

        laps = self.ir["CarIdxLap"] or []
        last_lap_times = self.ir["CarIdxLastLapTime"] or []
        positions = self.ir["CarIdxClassPosition"] or []
        player_idx = self.ir["PlayerCarIdx"]
        player_pos = self.ir["PlayerCarClassPosition"]
        surfaces = self._ir_get("CarIdxTrackSurface", []) or []

        # Track only the drivers we care about so memory doesn't grow with the full field.
        tracked = self._tracked_indices(player_idx, player_pos, positions)

        # Opponent pit-approach / stall surface transitions.
        for idx in tracked:
            if idx >= len(surfaces):
                continue
            try:
                surf = int(surfaces[idx])
            except (TypeError, ValueError):
                continue
            prev = self._last_car_surface.get(idx)
            if prev is not None and surf != prev and surf in _PITTING_SURFACES:
                pass  # herd state captured in build_packet fi.hd
            self._last_car_surface[idx] = surf

        # Prune any cars that are no longer in the relevant slice.
        for idx in list(self.field_history.keys()):
            if idx not in tracked:
                self.field_history.pop(idx, None)
                self.last_recorded_lap.pop(idx, None)
                self._last_car_surface.pop(idx, None)

        fuel_level = clamp_nonneg_liters(self._ir_get("FuelLevel", 0.0))
        fuel_capacity = self._ir_get("FuelCapacity", None)
        self._maybe_load_incident_limit()

        flags = _parse_session_flags_bools(
            self._ir_get("SessionFlags", 0),
            self._ir_get("PitsOpen", None),
        )
        is_caution = bool(flags.get("cau") or flags.get("yel"))
        if self._last_is_caution and not is_caution:
            self._reset_fuel_ema_for_green_restart(lap_now, fuel_level)
        self._last_is_caution = is_caution
        on_track = bool(self._ir_get("IsOnTrack", False))
        avg_lap_s = self._avg_lap_s_for_idx(player_idx, fallback=DEFAULT_AVG_LAP_S)
        ahead_idx = self._idx_by_class_pos(player_pos - 1) if player_pos else None
        behind_idx = self._idx_by_class_pos(player_pos + 1) if player_pos else None
        gap_ahead_s = self._gap_est_s(player_idx, ahead_idx, lap_s_fallback=avg_lap_s)
        gap_behind_s = self._gap_est_s(player_idx, behind_idx, lap_s_fallback=avg_lap_s)
        lap_i = lap_now if isinstance(lap_now, int) else None
        ahead_times = list(self.field_history.get(ahead_idx, [])) if ahead_idx is not None else None
        best_lap = None
        you_times_early = list(self.field_history.get(player_idx, []))
        if you_times_early:
            best_lap = min(you_times_early)
        pa_early = None
        if ahead_times and you_times_early:
            you_avg = sum(you_times_early[-3:]) / min(3, len(you_times_early))
            ah_avg = sum(ahead_times[-3:]) / min(3, len(ahead_times))
            if ah_avg > 0:
                pa_early = you_avg - ah_avg
        session_te = self._ir_get("SessionTime", None)
        if session_te is None:
            session_te = self._ir_get("SessionTimeElapsed", None)
        try:
            session_te_f = float(session_te) if session_te is not None else None
        except (TypeError, ValueError):
            session_te_f = None
        self._ctx.poll(
            self._ir_get,
            player_idx=player_idx,
            lap=lap_i,
            on_track=on_track,
            gap_ahead_s=gap_ahead_s,
            gap_behind_s=gap_behind_s,
            is_caution=is_caution,
            session_time_elapsed=session_te_f,
            ahead_lap_times=ahead_times,
            pit_loss_sec=float(self._ir_get("PitLaneTime", 45) or 45),
            best_lap_s=best_lap,
            pace_delta_ahead=pa_early,
        )
        incident_count = _player_incident_count(self._ir_get)
        self._clean_lap_gate.poll(
            incident_count=incident_count,
            gap_ahead_s=gap_ahead_s,
            is_caution=is_caution,
            on_track=on_track,
            session_time_s=session_te_f,
        )

        if isinstance(lap_now, int):
            self._tick_fuel_per_lap_ema(
                lap_now,
                fuel_level,
                fuel_capacity,
                exclude_sample=self._ctx.consume_fuel_ema_skip(),
            )

        for i in tracked:
            if i >= len(laps) or i >= len(last_lap_times):
                continue
            curr_lap = laps[i]
            if i not in self.last_recorded_lap:
                self.last_recorded_lap[i] = curr_lap
                self.field_history[i] = deque(maxlen=LAP_HISTORY_DEPTH)

            # Only append a lap time when the lap counter increments.
            if curr_lap > self.last_recorded_lap[i]:
                t = last_lap_times[i]
                if t > 0:
                    self.field_history[i].append(round(t, 3))
                    if i == player_idx:
                        self._clean_lap_gate.on_lap_complete(
                            float(t),
                            is_caution=is_caution,
                            reference_lap_s=avg_lap_s,
                            incident_count=incident_count,
                        )
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
            fb = _parse_session_flags_bools(flags)
            if fb.get("cau") or fb.get("yel"):
                return "CAUTION"
            if fb.get("grn"):
                return "GREEN"
            try:
                flags_int = int(flags)
            except Exception:
                return "UNKNOWN"
            return "GREEN" if flags_int == 0 else "UNKNOWN"

        for idx, pos in enumerate(positions):
            if pos <= 3 or abs(pos - player_pos) <= 2:
                if self.field_history.get(idx):
                    label = f"P{pos}" if idx != player_idx else "YOU"
                    relevant_history[label] = list(self.field_history[idx])

        fuel_level = clamp_nonneg_liters(self._ir_get("FuelLevel", 0.0))
        fuel_level_pct = self._ir_get("FuelLevelPct", None)
        try:
            if fuel_level_pct is not None:
                fuel_level_pct = round(float(fuel_level_pct), 2)
        except (TypeError, ValueError):
            fuel_level_pct = None
        fuel_use_per_hour_raw = float(self._ir_get("FuelUsePerHour", 0.0) or 0.0)
        laps_remain = _sanitize_lap_count(self._ir_get("SessionLapsRemain", None))
        laps_total = _sanitize_lap_count(self._ir_get("SessionLapsTotal", None))
        flags = self._ir_get("SessionFlags", 0)
        pits_open = self._ir_get("PitsOpen", None)
        flags_bools = _parse_session_flags_bools(flags, pits_open)
        is_on_track = bool(self._ir_get("IsOnTrack", False))
        fuel_capacity_raw = self._ir_get("FuelCapacity", None)
        pit_sv_fuel_L = self._ir_get("PitSvFuel", None)
        pit_time_div10 = self._ir_get("PitTimeDivideBy10", None)
        piti_ext_time = self._ir_get("PitiExtTime", None)

        def read_tire_wear_snapshot() -> dict[str, Any] | None:
            return _read_tire_wear_corners(self._ir_get)

        # Stint length estimate (laps since last pit-road exit).
        lap_int = self._ir_get("Lap", None)
        self._tick_fuel_per_lap_ema(lap_int, fuel_level, fuel_capacity_raw)
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

        # Estimate fuel-per-lap: FuelUsePerHour is kg/h (SDK); FuelLevel is liters — convert via nominal density.
        avg_lap_s = None
        if you_pace.get("n", 0) >= 1:
            avg_lap_s = you_pace.get("avg_last3_s") or you_pace.get("avg_last5_s")
        if not avg_lap_s:
            avg_lap_s = DEFAULT_AVG_LAP_S

        is_caution = bool(flags_bools.get("cau") or flags_bools.get("yel"))
        fpl_ema = self._fuel_per_lap_ema_L
        fuel_use_per_lap_est = resolve_combined_burn_rate(
            fuel_use_per_hour_raw,
            clamp_avg_lap_seconds(avg_lap_s),
            fpl_ema,
            is_caution=is_caution,
        )

        laps_of_fuel_left_est = float(fuel_level) / fuel_use_per_lap_est

        # Instantaneous kg/h is often nonsense on pit road / slow out-laps — clamp absurd range fuel.
        try:
            if laps_remain is not None:
                lr_d = float(max(int(laps_remain), 1))
                if laps_of_fuel_left_est > max(
                    lr_d * FUEL_LAPS_CLAMP_MULTIPLIER,
                    lr_d + FUEL_LAPS_CLAMP_OFFSET,
                ):
                    fuel_use_per_lap_est = max(fuel_use_per_lap_est, fuel_level / lr_d)
                    laps_of_fuel_left_est = float(fuel_level) / fuel_use_per_lap_est
        except Exception:
            pass

        if self._fuel_per_lap_ema_L is not None and isinstance(lap_int, int) and lap_int >= 3:
            fuel_calc_quality = "hi"
        elif on_pit_road and self._fuel_per_lap_ema_L is None:
            fuel_calc_quality = "low"
        else:
            fuel_calc_quality = "med"

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

        # --- Degradation + pit payback (telemetry-gated m.fo) ---
        best_lap_s = min(you_times) if you_times else None
        avg3_s = you_pace.get("avg_last3_s") if isinstance(you_pace, dict) else None
        gated_fo = self._clean_lap_gate.falloff_s
        if gated_fo is not None:
            falloff_s = gated_fo
        elif isinstance(best_lap_s, (int, float)) and isinstance(avg3_s, (int, float)):
            raw_falloff = float(avg3_s) - float(best_lap_s)
            if raw_falloff > 0:
                falloff_s = round(raw_falloff, 3)
            else:
                falloff_s = None
        else:
            falloff_s = None

        pit_payback_laps = None
        try:
            if falloff_s is not None and float(falloff_s) > 0 and pit_loss_sec:
                payback_lap = triangular_payback_lap(
                    float(pit_loss_sec),
                    clamp_positive_rate(falloff_s, minimum=0.01, default=0.01),
                )
                if payback_lap is not None:
                    pit_payback_laps = round(float(payback_lap), 1)
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

        # Full-tank stint uses green-flag EMA when available (not caution-instant burn).
        ftl = None
        try:
            fc = fuel_capacity_raw
            ftl_burn_L = float(fpl_ema) if fpl_ema is not None else fuel_use_per_lap_est
            if fc is not None and ftl_burn_L > 0:
                ftl = round(float(fc) / ftl_burn_L, 1)
        except Exception:
            pass
        if ftl is not None and (ftl > 320 or ftl < 0.8):
            ftl = None

        fpl_us = _liters_to_us_gal(fuel_use_per_lap_est)
        fuel_est_ok = True
        if fpl_us < 0.012 and fuel_calc_quality != "hi":
            fuel_est_ok = False
        if laps_of_fuel_left_est > 420:
            fuel_est_ok = False
        if not is_on_track and int(you_pace.get("n", 0) or 0) < 1 and fuel_calc_quality != "hi":
            fuel_est_ok = False

        lap_data_ok = laps_remain is not None or laps_total is not None or race_laps_total is not None

        session_time_remain = self._ir_get("SessionTimeRemain", None)
        if isinstance(session_time_remain, (int, float)) and float(session_time_remain) >= 86400.0:
            # Hosted / test sessions often expose ~604800s placeholder — omit so the model does not treat it as real stint clock.
            session_time_remain = None

        session_time_elapsed = self._ir_get("SessionTime", None)
        if isinstance(session_time_elapsed, (int, float)) and float(session_time_elapsed) >= 86400.0:
            session_time_elapsed = None

        session_type = self._ir_get("SessionType", None)
        if session_type is not None:
            session_type = str(session_type).strip() or None

        air_temp_c = self._ir_get("AirTemp", None)
        track_temp_c = self._ir_get("TrackTempCrew", None) or self._ir_get("TrackTemp", None)
        try:
            if air_temp_c is not None:
                air_temp_c = round(float(air_temp_c), 1)
        except (TypeError, ValueError):
            air_temp_c = None
        try:
            if track_temp_c is not None:
                track_temp_c = round(float(track_temp_c), 1)
        except (TypeError, ValueError):
            track_temp_c = None

        air_temp_f = _c_to_f(air_temp_c)
        track_temp_f = _c_to_f(track_temp_c)
        track_wetness = self._ir_get("TrackWetness", None)
        try:
            if track_wetness is not None:
                track_wetness = round(float(track_wetness), 3)
        except (TypeError, ValueError):
            track_wetness = None

        player_class = self._ir_get("PlayerCarClass", None)
        if player_class is not None:
            player_class = str(player_class).strip() or None

        tire_compound = None
        for key in ("PlayerCarDryTireType", "PlayerTireCompound", "PlayerCarLeftFrontTireType"):
            v = self._ir_get(key, None)
            if isinstance(v, str) and v.strip():
                tire_compound = v.strip()
                break

        speed_mph = None
        try:
            spd = self._ir_get("Speed", None)
            if spd is not None:
                speed_mph = round(float(spd) * 2.23694, 1)
        except (TypeError, ValueError):
            speed_mph = None

        pit_lane_time_s = None
        try:
            if piti_ext_time is not None:
                pit_lane_time_s = round(float(piti_ext_time), 1)
        except (TypeError, ValueError):
            pit_lane_time_s = None
        if pit_lane_time_s is None and self._cumulative_pit_time_s > 0:
            pit_lane_time_s = round(self._cumulative_pit_time_s, 1)

        pit_lane_penalty_s = None
        try:
            if pit_time_div10 is not None:
                pit_lane_penalty_s = round(float(pit_time_div10) * 10.0, 2)
        except (TypeError, ValueError):
            pit_lane_penalty_s = None

        pit_sv_fuel_us = None
        try:
            if pit_sv_fuel_L is not None:
                pit_sv_fuel_us = round(_liters_to_us_gal(float(pit_sv_fuel_L)), 3)
        except (TypeError, ValueError):
            pit_sv_fuel_us = None

        fuel_burn_last_us = None
        if self._fuel_last_lap_burn_L is not None:
            fuel_burn_last_us = round(_liters_to_us_gal(self._fuel_last_lap_burn_L), 5)
        fuel_burn_hist_us = None
        if self._fuel_burn_samples_L:
            fuel_burn_hist_us = [round(_liters_to_us_gal(x), 5) for x in list(self._fuel_burn_samples_L)[-6:]]

        field_intel = self._build_field_intel(
            player_idx,
            player_pos,
            positions,
            pit_loss_sec=int(pit_loss_sec),
            lap_s=float(avg_lap_s),
            flags_bools=flags_bools,
        )

        twl = list(self._pit_tire_wear_log) if self._pit_tire_wear_log else None

        mode = self.ui_mode()

        session_yaml = self.get_session_yaml_dict()
        race_session = find_race_session(session_yaml or {})
        race_laps_total = race_lap_total_from_session(race_session)
        tire_set_limit = self.read_tire_set_limit()

        # Compact schema to reduce tokens (short keys, no nulls, rounded floats).
        fc_us_gal = None
        try:
            if fuel_capacity_raw is not None:
                fc_us_gal = round(_liters_to_us_gal(float(fuel_capacity_raw)), 2)
        except Exception:
            fc_us_gal = None

        packet = {
            "x": {"md": mode, "u": "us", "fe": (1 if fuel_est_ok else 0), "ll": (1 if lap_data_ok else 0)},
            "s": {  # session
                "st": self._ir_get("SessionState", None),
                "ty": session_type,
                "ses": self._ir_get("SessionNum", None),
                "tr": session_time_remain,
                "te": session_time_elapsed,
                "lt": laps_total,
                "race_lt": race_laps_total,
                "ot": is_on_track,
                "ig": self._ir_get("IsInGarage", None),
                "at": air_temp_f,
                "tt": track_temp_f,
                "atc": air_temp_c,
                "ttc": track_temp_c,
                "wn": track_wetness,
                "cls": player_class,
                "flb": flags_bools,
                "pto": pit_lane_penalty_s,
            },
            "m": {  # me
                "l": self._ir_get("Lap", None),
                "lp": last_pit_lap,
                "lr": laps_remain,
                "p": player_pos,
                "ful": round(fuel_level, 4),
                "fu": round(_liters_to_us_gal(fuel_level), 3),
                "fup": fuel_level_pct,
                "fbl": fuel_burn_last_us,
                "fbh": fuel_burn_hist_us,
                "psf": pit_sv_fuel_us,
                "pte": pit_lane_time_s,
                "fph": round(float(fuel_use_per_hour_raw) * _KG_TO_LB, 3),
                "fpl": round(_liters_to_us_gal(fuel_use_per_lap_est), 5),
                "fpe": round(_liters_to_us_gal(float(fpl_ema)), 5) if fpl_ema is not None else None,
                "fcq": fuel_calc_quality,
                "fl": round(laps_of_fuel_left_est, 2),
                "mk": can_make_to_end,
                "ls": round(laps_short_on_fuel, 2) if laps_short_on_fuel is not None else None,
                "t": you_times,
                "pc": you_pace,
                "fg": int(flags) if flags is not None else None,
                "fs": flag_state(flags),
                "pr": on_pit_road,
                "ps": bool(self._ir_get("PlayerCarInPitStall", False)),
                # Repairs are only exposed while in the stall (per SDK docs); keep in the packet anyway.
                "rr": self._ir_get("PitRepairLeft", None),
                "or": self._ir_get("PitOptRepairLeft", None),
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
                "cmp": tire_compound,
                "sp": speed_mph,
            },
            "r": {  # race_info
                "pl": int(pit_loss_sec),
                "ts": int(tire_sets_remaining),
                "tsl": tire_set_limit,
                "fc": fc_us_gal,
                "ftl": ftl,
            },
            "rv": rivals,
            "f": relevant_history,
        }
        if field_intel:
            packet["fi"] = field_intel
        if twl:
            packet["twl"] = twl

        reentry_verdict = None
        if isinstance(field_intel, dict):
            rej_obj = field_intel.get("rej")
            if isinstance(rej_obj, dict):
                reentry_verdict = rej_obj.get("v")
        stint_cap_laps = max(1, int(ftl)) if ftl is not None else 25
        ctx_extras = self._ctx.build_packet_extras(
            track_temp_c=track_temp_c,
            tire_wear_rate_est=tire_wear_rate_est,
            pit_loss_sec=float(pit_loss_sec),
            tire_falloff_s=float(falloff_s) if isinstance(falloff_s, (int, float)) else 0.08,
            fuel_stint_cap=stint_cap_laps,
            rivals=rivals,
            you_pace=you_pace,
            reentry_verdict=reentry_verdict,
            current_lap=lap_int if isinstance(lap_int, int) else None,
            gap_behind_s=gap_behind_s,
        )
        if isinstance(ctx_extras.get("m"), dict) and ctx_extras["m"]:
            packet["m"].update(ctx_extras["m"])
        if isinstance(ctx_extras.get("s"), dict) and ctx_extras["s"]:
            packet["s"].update(ctx_extras["s"])
        fi_extra = ctx_extras.get("fi")
        if isinstance(fi_extra, dict) and fi_extra:
            packet.setdefault("fi", {}).update(fi_extra)

        if session_yaml:
            packet["sy"] = session_yaml

        if not fuel_est_ok:
            mm = packet.get("m")
            if isinstance(mm, dict):
                for k in ("fpl", "fpe", "fl", "mk", "ls", "pw", "pwu"):
                    mm.pop(k, None)
            rr = packet.get("r")
            if isinstance(rr, dict):
                rr.pop("ftl", None)

        packet = drop_nones(packet)

        return json.dumps(packet, separators=(",", ":"))

