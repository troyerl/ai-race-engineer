"""
Race Engineer Agent - Strategy Engine
================================================================
Deterministic decision algorithm and telemetry schema reference for the
local race strategy engine (no cloud AI).
"""

from __future__ import annotations

import math
import re
from typing import Any

from .race_constants import (
    ALERT_LAP_HORIZON,
    CAUTION_GREEN_RUN_BUFFER_LAPS,
    CAUTION_HERD_PIT_RATIO_MIN,
    CAUTION_PODIUM_FUEL_BUFFER_LAPS,
    CAUTION_PODIUM_MAX_POSITION,
    CAUTION_STAY_OUT_MIN_SPOTS_LOST,
    CAUTION_STAY_OUT_TOP_POSITION,
    CAUTION_TOP10_FUEL_ABUNDANCE_LAPS,
    CAUTION_TOP10_MAX_SPOTS_LOST,
    DEFAULT_CAUTION_BURN_L,
    INCIDENT_OT_ALERT_COUNT,
    LAPPED_DANGER_FUEL_MIN_LAPS,
    LEADER_FORCED_PIT_GAP_BEHIND,
    LEADER_STRETCH_FUEL_FLOOR,
    LEADER_STRETCH_FUEL_FLOOR_NO_HERD,
    LEADER_STRETCH_HERD_PIT_BELOW,
    LEADER_STRETCH_MAX_POSITION,
    LEADER_STRETCH_MIN_LAPS_REMAIN,
    LEADER_UNDERCUT_SELF_FUEL_MAX,
    LEADER_UNDERCUT_SELF_MAX_POSITION,
    LEADER_UNDERCUT_SELF_MIN_LAPS_REMAIN,
    LEADER_UNDERCUT_SELF_MIN_POSITION,
    MIN_UNDERCUT_RUNWAY_LAPS,
    MIN_UNDERCUT_STINT_LAPS,
    ODI_STAY_OUT_THRESHOLD,
    ODI_UNDERCUT_THRESHOLD,
    PACE_STABILITY_MIN_SAMPLES,
    PACE_STABILITY_STD_THRESHOLD_S,
    POST_PIT_ALERT_MIN_STINT_LAPS,
    FUEL_CRITICAL_LAPS_DEFAULT,
    GWC_FUEL_RESERVE_LAPS,
    GWC_OVAL_LAPS_REMAIN_MAX,
    STEER_STD_ELEVATED,
    STEER_STD_HIGH,
    TRACK_TEMP_SHIFT_THRESHOLD_C,
    UNDERCUT_GAP_AHEAD_MAX_SEC,
    UNDERCUT_RIVAL_PACE_DELTA_MIN,
    WHITE_FLAG_LAPS_REMAINING,
    clamp_nonneg_liters,
    green_flag_fuel_laps_from_telemetry,
    triangular_payback_lap,
)
from .pre_race_strategy import run_pre_race_plan
from .tire_model import OVAL_STAGGER_WARN_DELTA_PCT, effective_tire_falloff_s

# ================================================================
# 1. TELEMETRY DATA DICTIONARY SCHEMA
# ================================================================
SCHEMA_DOCUMENTATION: dict[str, Any] = {
    "x": {
        "md": "Mode (live | strategy)",
        "u": "Units configuration (us_fuel_gal_and_lb_hr)",
        "fe": "Fuel telemetry fidelity check (1 = trustworthy, 0 = untrusted)",
        "ll": "Lap tracking fidelity check (1 = trustworthy, 0 = untrusted)",
    },
    "s": {
        "st": "Session state",
        "ty": "Session type",
        "ses": "Session number",
        "tr": "Time remaining in seconds",
        "te": "Elapsed time in seconds",
        "lt": "Total laps in session",
        "ot": "Is on track (bool)",
        "ig": "Is in garage (bool)",
        "at": "Air temp F",
        "tt": "Track temp F",
        "atc": "Air temp C",
        "ttc": "Track temp C",
        "wn": "Wetness / Precipitation level",
        "cls": "Car class identifier",
        "flb": "Flags dict {yel: yellow, cau: caution, grn: green, pcl: pit_lane_closed}",
        "pto": "Pit lane penalty in seconds",
    },
    "m": {
        "l": "Current lap",
        "lr": "Laps remaining",
        "p": "Position",
        "ful": "Fuel liters",
        "fu": "Fuel US gal",
        "fup": "Fuel tank percentage",
        "fbl": "Fuel burn last lap US gal",
        "fbh": "Fuel burn lap history trend",
        "psf": "Scheduled fuel to add at next pit stop",
        "pte": "Pit lane total elapsed session time",
        "fph": "Fuel burn lb/hr scale",
        "fpe": "Fuel US gal/lap exponential moving average",
        "fpl": "Fuel US gal/lap estimation",
        "fcq": "Fuel estimate quality (hi|med|low)",
        "fl": "Calculated fuel laps remaining",
        "mk": "Can make it to end without pitting (bool)",
        "ls": "Laps short of finishing",
        "lp": "Last pit lap",
        "t": "Lap times history (seconds)",
        "pc": "Current delta pace {avg_last3_s, trend_s, std_clean_s, n_clean}",
        "fg": "Flags",
        "fs": "Flag state string",
        "pr": "Is on pit road (bool)",
        "ps": "Is in pit stall (bool)",
        "rr": "Required repair time left",
        "or": "Optional repair time left",
        "sl": "Stint laps completed",
        "ga": "Gap ahead in seconds",
        "gb": "Gap behind in seconds",
        "bl": "Best lap time",
        "fo": "Pace falloff delta from tire wear",
        "pb": "Pit payback laps metric",
        "pw": "Pit window open (bool)",
        "pwu": "Laps until pit window opens",
        "tw": "Tire wear array",
        "tws": "Tire wear stale check",
        "twsl": "Tire wear slant",
        "twr": "Tire wear remaining percentage",
        "cmp": "Current tire compound string",
        "sp": "Speed mph",
    },
    "r": {
        "pl": "Pit loss time penalty in seconds",
        "ts": "Tire sets remaining available",
        "fc": "Fuel tank capacity in US gallons",
        "ftl": "Estimated laps achievable on a completely full tank",
    },
    "fi": {
        "dist": "Lap distance percentage tracked for field",
        "f2": "CarIdxF2Time tracking index",
        "surf": "Track surface code tracking",
        "hd": "Herd dynamics dict {apa, apb, isa, isb, pra: pit ratio ahead, prb: pit ratio behind}",
        "rej": "Projected pit reentry verdict (CLEAN | TRAFFIC | PACK | LAPPED_DANGER | UNKNOWN); bv=base density; gtl=gap to leader s; pll=projected laps lost; ldw=lap-down cars in window",
        "cpi": "Caution pit analytics {tl, ll, lda, xp, gr, ...}",
        "draft": "Draft state {on, streak, ex}",
        "odi": "Overtake difficulty {pa, pb, score, uc, rd}",
        "tac": "Tactical alerts {def_line, cool, db} — DEFENSIVE mode only",
        "sm": "Strategy mode {m: enum, n: BALANCED|OFFENSIVE|DEFENSIVE}",
    },
    "twl": "Pit box tire wear log array",
    "m_inc": "m.inc off-track window {ot, hr, tot}",
    "m_drv": "m.drv steering fatigue {ss, ssr}",
    "m_mar": "m.mar marble pickup {lr}",
    "s_tenv": "s.tenv track environment {ref, cur, dt, tsc, twr_adj}",
}


# ================================================================
# 2. LIVE STRATEGY + REST-OF-RACE FORECAST
# ================================================================
def _safe_float(v: Any, default: float) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _resolve_pit_payback_laps(telemetry: dict[str, Any]) -> float:
    """Packet pit payback laps, or triangular recompute from falloff + pit loss (Section 3.5)."""
    m = telemetry.get("m", {})
    r = telemetry.get("r", {})
    pb = _safe_float(m.get("pb"), 0.0)
    if pb > 0:
        return pb
    falloff_s = _safe_float(m.get("fo"), 0.0)
    pit_loss = clamp_nonneg_liters(_safe_float(r.get("pl"), 0.0))
    if falloff_s > 0 and pit_loss > 0:
        lap = triangular_payback_lap(pit_loss, falloff_s)
        if lap is not None:
            return float(lap)
    return 0.0


def _laps_since_pit_stop(telemetry: dict[str, Any]) -> int | None:
    """Laps completed on the current stint (since last pit exit)."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    sl = m.get("sl")
    if isinstance(sl, int) and sl >= 0:
        return sl
    lap = m.get("l")
    last_pit = m.get("lp")
    if isinstance(lap, int) and isinstance(last_pit, int):
        return max(0, lap - last_pit)
    return None


def _post_pit_alert_quiet(telemetry: dict[str, Any], *, alert_horizon: int = ALERT_LAP_HORIZON) -> bool:
    """Defer forecast pit nags early in a fresh stint after a stop."""
    since = _laps_since_pit_stop(telemetry)
    if since is None:
        return False
    r = telemetry.get("r", {}) if isinstance(telemetry.get("r"), dict) else {}
    ftl = _safe_float(r.get("ftl"), 12.0)
    min_stint = max(
        POST_PIT_ALERT_MIN_STINT_LAPS,
        int(max(1.0, ftl - float(alert_horizon) - 1.0)),
    )
    return since < min_stint


def _forecast_fuel_laps_seed(telemetry: dict[str, Any], *, max_tank_stint: float) -> float:
    """Green-flag forecast fuel seed; full tank after a recent pit stop."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    live_fl = _safe_float(m.get("fl"), 0.0)
    green = green_flag_fuel_laps_from_telemetry(
        telemetry,
        fallback_l_per_lap=DEFAULT_CAUTION_BURN_L,
    )
    if green <= 0 and live_fl > 0:
        green = live_fl
    elif live_fl > 0 and m.get("mk") is True:
        green = max(green, live_fl)
    since = _laps_since_pit_stop(telemetry)
    if since is not None and since <= 3:
        return max(0.0, max(max_tank_stint, green) - 1.0)
    return max(0.0, green - 1.0)


def _fuel_laps_for_pit_window(telemetry: dict[str, Any], *, fuel_laps_left: float) -> float:
    """
    Laps-left for target box / target pit lap (§10.3).

    Under caution, use the live tank reading. Under green, prefer green-flag EMA when
    liters are available; otherwise floor the live reading so caution savings and
    bankers-rounding do not push the window outward.
    """
    if _under_caution(telemetry):
        return fuel_laps_left

    if _can_run_to_finish(telemetry) and not _under_caution(telemetry):
        return 0.0

    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    if m.get("ful") is not None:
        green = green_flag_fuel_laps_from_telemetry(
            telemetry,
            fallback_l_per_lap=DEFAULT_CAUTION_BURN_L,
        )
        if green > 0:
            return green

    return math.floor(fuel_laps_left)


LAPPED_DANGER_WHY = (
    "Delaying pit stop — green stop will put us a lap down. Extending to find cleaner window."
)
LAPPED_DANGER_VOICE = (
    "Stay out, stay out. Pitting now puts us a lap down. Extend this stint."
)
OFFENSIVE_UNDERCUT_WHY = (
    "Offensive undercut — car ahead on degrading tires; box this lap for clean merge."
)
LEADER_UNDERCUT_SELF_WHY = (
    "Undercut yourself — box from mid-front before inheriting lead on worn fuel."
)
LEADER_CLEAN_AIR_STRETCH_WHY = (
    "Leader clean-air stretch — defer pit while fuel carries; protect track position."
)


def _session_laps_remain(telemetry: dict[str, Any]) -> int:
    """Laps left in session (m.lr with s.lt fallback)."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    s = telemetry.get("s", {}) if isinstance(telemetry.get("s"), dict) else {}
    laps_remain = m.get("lr")
    if laps_remain is None:
        current_lap = _safe_int(m.get("l"), 1)
        s_lt = s.get("lt")
        if isinstance(s_lt, int):
            laps_remain = max(0, int(s_lt) - current_lap)
        else:
            laps_remain = 0
    return max(0, _safe_int(laps_remain, 0))


def _session_total_laps(telemetry: dict[str, Any]) -> int:
    """Scheduled race distance (s.lt, else current + laps remain)."""
    s = telemetry.get("s", {}) if isinstance(telemetry.get("s"), dict) else {}
    s_lt = s.get("lt")
    if isinstance(s_lt, int) and s_lt > 0:
        return int(s_lt)
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    return _safe_int(m.get("l"), 1) + _session_laps_remain(telemetry)


def _can_run_to_finish(telemetry: dict[str, Any]) -> bool:
    """True when fuel range covers the remaining race distance (no mandatory fuel stop)."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    if m.get("mk") is True:
        return True
    fuel_laps_left = _safe_float(m.get("fl"), 0.0)
    if fuel_laps_left <= 1.0:
        return False
    return fuel_laps_left >= float(_session_laps_remain(telemetry)) - 0.01


def _coast_to_checkered_ok(telemetry: dict[str, Any], *, fuel_laps_left: float) -> bool:
    """Suppress green-flag pit calls on the final lap when fuel reaches the line."""
    if _under_caution(telemetry):
        return False
    laps_remain = _session_laps_remain(telemetry)
    if laps_remain > WHITE_FLAG_LAPS_REMAINING:
        return False
    reserve = GWC_FUEL_RESERVE_LAPS if _gwc_overtime_prep(telemetry) else 0.0
    return fuel_laps_left >= float(laps_remain) + reserve - 0.01


def _caution_stay_out_for_track_position(
    telemetry: dict[str, Any],
    *,
    fuel_laps_left: float,
) -> bool:
    """Stay out under yellow when top-ten position loss outweighs a pit cycle."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    position = _safe_int(m.get("p"), 0)
    if position < 1 or position > CAUTION_STAY_OUT_TOP_POSITION:
        return False
    cpi = fi.get("cpi", {}) if isinstance(fi.get("cpi"), dict) else {}
    spots_lost = _safe_int(cpi.get("ll"), 0)
    if spots_lost <= CAUTION_STAY_OUT_MIN_SPOTS_LOST:
        return False
    r = telemetry.get("r", {}) if isinstance(telemetry.get("r"), dict) else {}
    ftl = max(1.0, _safe_float(r.get("ftl"), 25.0))
    stint = _safe_int(m.get("sl"), 0)
    min_fuel = float(CAUTION_GREEN_RUN_BUFFER_LAPS) + 1.0
    if fuel_laps_left < min_fuel:
        return False
    tire_runway = ftl - float(stint)
    if tire_runway < float(CAUTION_GREEN_RUN_BUFFER_LAPS):
        return False
    return True


def _leader_in_pit_decision_zone(
    telemetry: dict[str, Any],
    *,
    fuel_laps_left: float,
    inside_window: bool,
) -> bool:
    """True when the leader is in the fuel-box decision band (not only inside_window)."""
    if inside_window:
        return True
    if fuel_laps_left <= LEADER_UNDERCUT_SELF_FUEL_MAX:
        m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
        current_lap = _safe_int(m.get("l"), 1)
        pb_laps = _resolve_pit_payback_laps(telemetry)
        box = _target_box_laps(telemetry, fuel_laps_left=fuel_laps_left, payback_laps=pb_laps)
        if box is not None:
            low, high = box
            if (low - 1) <= current_lap <= high:
                return True
    return False


def _leader_stretch_fuel_floor(telemetry: dict[str, Any]) -> float:
    """
    Hard floor when herd behind is pitting; softer floor when no cycling cars behind.

    Hybrid: never stretch below LEADER_STRETCH_FUEL_FLOOR (1.5). When fi.hd.prb is
    low, require more fuel reserve before deferring a window pit from the lead.
    """
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    hd = fi.get("hd", {}) if isinstance(fi.get("hd"), dict) else {}
    prb = _safe_float(hd.get("prb"), 0.0)
    if prb >= LEADER_STRETCH_HERD_PIT_BELOW:
        return LEADER_STRETCH_FUEL_FLOOR
    return max(LEADER_STRETCH_FUEL_FLOOR, LEADER_STRETCH_FUEL_FLOOR_NO_HERD)


def _leader_forced_pit_by_behind(
    telemetry: dict[str, Any],
    *,
    inside_window: bool,
    laps_remain: int,
) -> bool:
    """Trailing undercut threat while leading — stretch ends when pressure is real."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    gap_behind = _safe_float(m.get("gb"), 99.0)
    if gap_behind > LEADER_FORCED_PIT_GAP_BEHIND:
        return False
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    hd = fi.get("hd", {}) if isinstance(fi.get("hd"), dict) else {}
    prb = _safe_float(hd.get("prb"), 0.0)
    if prb >= LEADER_STRETCH_HERD_PIT_BELOW:
        return False
    odi = fi.get("odi", {}) if isinstance(fi.get("odi"), dict) else {}
    odi_score = _safe_float(odi.get("score"), 0.0)
    pace_behind = _safe_float(odi.get("pb"), 0.0)
    return _defensive_undercut_threat(
        telemetry,
        inside_window=inside_window,
        is_fuel_critical=False,
        action="STAY OUT",
        odi_score=odi_score,
        pace_behind=pace_behind,
        laps_remain=laps_remain,
    )


def _leader_clean_air_stretch_ok(
    telemetry: dict[str, Any],
    *,
    fuel_laps_left: float,
    inside_window: bool,
    is_fuel_critical: bool,
    laps_remain: int,
) -> bool:
    """Defer a window pit from P1–P3 while clean air and fuel/herd allow."""
    if _under_caution(telemetry) or is_fuel_critical:
        return False
    if not _leader_in_pit_decision_zone(
        telemetry,
        fuel_laps_left=fuel_laps_left,
        inside_window=inside_window,
    ):
        return False
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    position = _safe_int(m.get("p"), 0)
    if position < 1 or position > LEADER_STRETCH_MAX_POSITION:
        return False
    if laps_remain <= LEADER_STRETCH_MIN_LAPS_REMAIN:
        return False
    fuel_floor = _leader_stretch_fuel_floor(telemetry)
    if fuel_laps_left <= fuel_floor:
        return False
    if _leader_forced_pit_by_behind(
        telemetry,
        inside_window=inside_window,
        laps_remain=laps_remain,
    ):
        return False
    return True


def _undercut_yourself_opportunity(
    telemetry: dict[str, Any],
    *,
    inside_window: bool,
    fuel_laps_left: float,
    rej_v: str,
    laps_remain: int,
    is_fuel_critical: bool,
) -> bool:
    """Box from P3–P5 before inheriting the lead on a binding fuel stop."""
    if not _pace_stable_for_offense(telemetry):
        return False
    if _under_caution(telemetry) or is_fuel_critical:
        return False
    if not inside_window and fuel_laps_left > LEADER_UNDERCUT_SELF_FUEL_MAX:
        return False
    if laps_remain < LEADER_UNDERCUT_SELF_MIN_LAPS_REMAIN:
        return False
    if rej_v != "CLEAN":
        return False
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    position = _safe_int(m.get("p"), 0)
    if position < LEADER_UNDERCUT_SELF_MIN_POSITION or position > LEADER_UNDERCUT_SELF_MAX_POSITION:
        return False
    if fuel_laps_left > LEADER_UNDERCUT_SELF_FUEL_MAX:
        return False
    return True


def _pit_loss_seconds(telemetry: dict[str, Any]) -> float:
    r = telemetry.get("r", {}) if isinstance(telemetry.get("r"), dict) else {}
    return max(0.0, _safe_float(r.get("pl"), 46.0))


def _tire_pit_worth_it(telemetry: dict[str, Any], *, laps_remaining: int) -> bool:
    """
    Schedule a tire stop only when projected pace loss from wear exceeds pit-road cost.

    projected_loss ≈ tire_falloff_s × laps_remaining (§3 wear proxy in m.fo).
    """
    if laps_remaining <= 0:
        return False
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    falloff_s = effective_tire_falloff_s(telemetry)
    if falloff_s <= 0:
        falloff_s = _safe_float(m.get("fo"), 0.0)
    if falloff_s <= 0:
        return False
    return falloff_s * float(laps_remaining) >= _pit_loss_seconds(telemetry)


def _undercut_runway_ok(telemetry: dict[str, Any], *, laps_remain: int | None = None) -> bool:
    """§16.4.2 — long green stint + session/fuel runway before any undercut call."""
    if laps_remain is None:
        laps_remain = _session_laps_remain(telemetry)
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    r = telemetry.get("r", {}) if isinstance(telemetry.get("r"), dict) else {}
    stint_laps = _safe_int(m.get("sl"), 0)
    ftl = _safe_float(r.get("ftl"), 25.0)
    fuel_runway = ftl - float(stint_laps)
    if laps_remain < MIN_UNDERCUT_RUNWAY_LAPS:
        return False
    if fuel_runway < MIN_UNDERCUT_RUNWAY_LAPS:
        return False
    if stint_laps < MIN_UNDERCUT_STINT_LAPS:
        return False
    return True


def _projected_merge_position(telemetry: dict[str, Any]) -> int | None:
    """Best-effort on-track class position after a green-flag pit (fi.cpi.xp)."""
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    cpi = fi.get("cpi") if isinstance(fi.get("cpi"), dict) else {}
    xp = _safe_int(cpi.get("xp"), 0)
    return xp if xp > 0 else None


def _undercut_net_gain_ok(telemetry: dict[str, Any]) -> bool:
    """
    §16.4.2 — offensive pit only when merge gains track position or rival blocks pace.

    Blocks P12→P12 'undercuts' that only burn pit-road time.
    """
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    current = _safe_int(m.get("p"), 0)
    projected = _projected_merge_position(telemetry)
    if projected is not None and current > 0:
        return projected < current

    rv = telemetry.get("rv", {}) if isinstance(telemetry.get("rv"), dict) else {}
    ahead = rv.get("ahead") if isinstance(rv.get("ahead"), dict) else {}
    ahead_pos = _safe_int(ahead.get("pos"), 0)
    if current <= 0 and ahead_pos > 0:
        current = ahead_pos + 1

    gap_ahead = m.get("ga")
    if (
        isinstance(gap_ahead, (int, float))
        and 0 < float(gap_ahead) < UNDERCUT_GAP_AHEAD_MAX_SEC
        and _rival_tires_decaying(telemetry)
        and ahead_pos > 0
        and ahead_pos < current
    ):
        return True
    return False


def _rival_tires_decaying(telemetry: dict[str, Any]) -> bool:
    """True when car ahead is slower — proxy for rival tire deg on long runs."""
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    odi = fi.get("odi") if isinstance(fi.get("odi"), dict) else {}
    pa = odi.get("pa")
    if isinstance(pa, (int, float)) and float(pa) >= UNDERCUT_RIVAL_PACE_DELTA_MIN:
        return True

    rv = telemetry.get("rv", {}) if isinstance(telemetry.get("rv"), dict) else {}
    ahead = rv.get("ahead") if isinstance(rv.get("ahead"), dict) else {}
    pace = ahead.get("pace") if isinstance(ahead.get("pace"), dict) else {}
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    pc = m.get("pc") if isinstance(m.get("pc"), dict) else {}
    you_avg = pc.get("avg_last3_s")
    ahead_avg = pace.get("avg_last3_s")
    if isinstance(you_avg, (int, float)) and isinstance(ahead_avg, (int, float)):
        return float(ahead_avg) - float(you_avg) >= UNDERCUT_RIVAL_PACE_DELTA_MIN
    return False


def _pace_stable_for_offense(telemetry: dict[str, Any]) -> bool:
    """
    True when clean-lap pace is consistent enough for offensive undercut gambles.

    Insufficient clean-lap history does not block — only suppress when variance
    is measured and exceeds PACE_STABILITY_STD_THRESHOLD_S.
    """
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    pc = m.get("pc") if isinstance(m.get("pc"), dict) else {}
    std = pc.get("std_clean_s")
    n_clean = _safe_int(pc.get("n_clean"), 0)
    if std is None or n_clean < PACE_STABILITY_MIN_SAMPLES:
        return True
    return float(std) < PACE_STABILITY_STD_THRESHOLD_S


def _undercut_opportunity(
    telemetry: dict[str, Any],
    *,
    inside_window: bool,
    rej_v: str,
    laps_remain: int,
    is_fuel_critical: bool,
) -> bool:
    """§16.4.2 offensive undercut matrix — all pacing, traffic, and runway gates."""
    if not _pace_stable_for_offense(telemetry):
        return False
    if not inside_window or is_fuel_critical:
        return False
    if rej_v != "CLEAN":
        return False
    if not _undercut_runway_ok(telemetry, laps_remain=laps_remain):
        return False
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    gap_ahead = m.get("ga")
    if not isinstance(gap_ahead, (int, float)):
        return False
    ga = float(gap_ahead)
    if ga <= 0 or ga >= UNDERCUT_GAP_AHEAD_MAX_SEC:
        return False
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    odi = fi.get("odi") if isinstance(fi.get("odi"), dict) else {}
    if odi.get("uc") and _strategy_mode(telemetry) == "OFFENSIVE":
        return _undercut_net_gain_ok(telemetry)
    if _rival_tires_decaying(telemetry):
        return _undercut_net_gain_ok(telemetry)
    return False


def _defensive_undercut_threat(
    telemetry: dict[str, Any],
    *,
    inside_window: bool,
    is_fuel_critical: bool,
    action: str,
    odi_score: float,
    pace_behind: float,
    laps_remain: int,
) -> bool:
    """Pressure from behind — gated by the same long-run runway guard."""
    if not inside_window or is_fuel_critical or action != "STAY OUT":
        return False
    if not _undercut_runway_ok(telemetry, laps_remain=laps_remain):
        return False
    return odi_score < ODI_UNDERCUT_THRESHOLD and pace_behind > 0.2


def _reentry_verdict(rej: dict[str, Any] | None) -> str:
    if not isinstance(rej, dict):
        return "CLEAN"
    return str(rej.get("v", "CLEAN") or "CLEAN").upper()


def _effective_reentry_verdict(rej: dict[str, Any] | None, telemetry: dict[str, Any]) -> str:
    """Use density-only verdict when fuel telemetry is untrusted (x.fe != 1)."""
    verdict = _reentry_verdict(rej)
    x = telemetry.get("x", {}) if isinstance(telemetry.get("x"), dict) else {}
    if x.get("fe", 0) != 1 and verdict == "LAPPED_DANGER" and isinstance(rej, dict):
        return str(rej.get("bv", "CLEAN") or "CLEAN").upper()
    return verdict


def _lapped_danger_should_stay_out(
    telemetry: dict[str, Any],
    *,
    inside_window: bool,
    fuel_laps_left: float,
    rej: dict[str, Any] | None,
) -> bool:
    """§10.2 — defer green pit when reentry projects a lap-down merge."""
    x = telemetry.get("x", {}) if isinstance(telemetry.get("x"), dict) else {}
    if x.get("fe", 0) != 1:
        return False
    if not inside_window:
        return False
    if fuel_laps_left <= LAPPED_DANGER_FUEL_MIN_LAPS:
        return False
    return _reentry_verdict(rej) == "LAPPED_DANGER"


def lapped_danger_voice_for_advice(advice: str) -> str | None:
    """§15 — fixed utterance when the lap-down pit deferral fires."""
    why = parse_advice_why(advice)
    if not why:
        return None
    wl = why.lower()
    if "lap down" in wl and ("delaying pit" in wl or "green stop" in wl):
        return LAPPED_DANGER_VOICE
    return None


def _is_race_session(telemetry: dict[str, Any]) -> bool:
    s = telemetry.get("s") if isinstance(telemetry.get("s"), dict) else {}
    ty = str(s.get("ty") or "").strip().lower()
    st = str(s.get("st") or "").strip().lower()
    return ty == "race" or st == "racing"


def _is_oval_track(telemetry: dict[str, Any]) -> bool:
    s = telemetry.get("s") if isinstance(telemetry.get("s"), dict) else {}
    if s.get("ov") in (1, True, "1"):
        return True
    from .track_db import is_oval_track

    return is_oval_track(
        str(s.get("tn") or ""),
        track_length_miles=s.get("tlmi"),
        track_type=str(s.get("tcat") or "") or None,
    )


def _gwc_overtime_prep(telemetry: dict[str, Any]) -> bool:
    """Late-race oval window where an extra lap of fuel should be reserved."""
    if not _is_race_session(telemetry) or not _is_oval_track(telemetry):
        return False
    m = telemetry.get("m") if isinstance(telemetry.get("m"), dict) else {}
    laps_remain = _safe_int(m.get("lr"), 999)
    return 0 < laps_remain <= GWC_OVAL_LAPS_REMAIN_MAX


def _fuel_critical_threshold(telemetry: dict[str, Any]) -> float:
    threshold = FUEL_CRITICAL_LAPS_DEFAULT
    if _gwc_overtime_prep(telemetry):
        threshold += GWC_FUEL_RESERVE_LAPS
    return threshold


def _fuel_context(telemetry: dict[str, Any]) -> tuple[float, bool, bool]:
    """Return (fuel_laps_left, inside_window, is_fuel_critical)."""
    crit = _fuel_critical_threshold(telemetry)
    x = telemetry.get("x", {})
    m = telemetry.get("m", {})
    if x.get("fe", 0) == 1 and m.get("fcq") in ("hi", "med"):
        fuel_laps_left = _safe_float(m.get("fl"), 0.0)
        pb_laps = _resolve_pit_payback_laps(telemetry)
        is_fuel_critical = fuel_laps_left <= crit

        if _can_run_to_finish(telemetry) and not _under_caution(telemetry):
            inside_window = fuel_laps_left <= pb_laps
        else:
            current_lap = _safe_int(m.get("l"), 1)
            window_fuel = _fuel_laps_for_pit_window(telemetry, fuel_laps_left=fuel_laps_left)
            if window_fuel <= 0:
                inside_window = fuel_laps_left <= pb_laps
            else:
                target_stop_lap = current_lap + max(0, int(math.floor(window_fuel)))
                window_margin = max(1, int(round(pb_laps)))
                inside_window = current_lap >= (target_stop_lap - window_margin)
                if fuel_laps_left <= pb_laps:
                    inside_window = True
    else:
        fpl = _safe_float(m.get("fpl"), 0.2)
        ful = _safe_float(m.get("ful"), 0.0)
        fuel_laps_left = ful / fpl if fpl > 0 else 3.0
        inside_window = fuel_laps_left <= 2.0
        is_fuel_critical = fuel_laps_left <= crit
    return fuel_laps_left, inside_window, is_fuel_critical


def _steer_forecast_penalty(telemetry: dict[str, Any]) -> int:
    drv = telemetry.get("m", {}).get("drv") if isinstance(telemetry.get("m"), dict) else None
    if not isinstance(drv, dict):
        return 0
    ssr = _safe_float(drv.get("ssr"), 0.0)
    if ssr >= STEER_STD_HIGH:
        return 2
    if ssr >= STEER_STD_ELEVATED:
        return 1
    return 0


def _append_context_notes(why: str, telemetry: dict[str, Any]) -> str:
    notes: list[str] = []
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    s = telemetry.get("s", {}) if isinstance(telemetry.get("s"), dict) else {}

    mar = m.get("mar")
    if isinstance(mar, dict) and _safe_int(mar.get("lr"), 0) > 0:
        notes.append("Pickup on tires — expect understeer for a lap or two; avoid offline passes.")

    stg = m.get("stg") if isinstance(m.get("stg"), dict) else {}
    if _is_oval_track(telemetry) and abs(_safe_float(stg.get("dg"), 0.0)) >= OVAL_STAGGER_WARN_DELTA_PCT:
        notes.append("Oval stagger split — left/right wear mismatch; handling may push tire stop earlier.")

    drv = m.get("drv")
    if isinstance(drv, dict) and _safe_float(drv.get("ssr"), 0.0) >= STEER_STD_HIGH:
        notes.append("Car is loose — consider short-shifting or pit when window opens.")

    tenv = s.get("tenv")
    if isinstance(tenv, dict):
        delta_t = _safe_float(tenv.get("dt"), 0.0)
        base_tsc = _safe_int(tenv.get("tsc"), 0)
        if abs(delta_t) >= TRACK_TEMP_SHIFT_THRESHOLD_C and base_tsc > 0:
            if delta_t <= -TRACK_TEMP_SHIFT_THRESHOLD_C:
                notes.append(f"Track cooled — extend tire stint ~{base_tsc} laps vs baseline.")
            elif delta_t >= TRACK_TEMP_SHIFT_THRESHOLD_C:
                notes.append(f"Track heated — shorten tire stint ~{base_tsc} laps vs baseline.")

    if not notes:
        return why
    extra = " ".join(notes)
    return f"{why} {extra}".strip()


def _apply_context_directive_overrides(
    telemetry: dict[str, Any],
    action: str,
    service: str,
    why: str,
    *,
    inside_window: bool,
    is_fuel_critical: bool,
    rej_v: str,
    tire_sets: int,
    laps_remain: int,
) -> tuple[str, str, str]:
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    fuel_laps_left = _safe_float(m.get("fl"), 0.0)
    if _coast_to_checkered_ok(telemetry, fuel_laps_left=fuel_laps_left):
        return (
            "STAY OUT",
            "NONE",
            _append_context_notes(
                "Final lap — fuel covers checkered; pitting forfeits track position.",
                telemetry,
            ),
        )
    odi = fi.get("odi") if isinstance(fi.get("odi"), dict) else {}
    odi_score = _safe_float(odi.get("score"), 0.0)
    pace_behind = _safe_float(odi.get("pb"), 0.0)

    if inside_window and not is_fuel_critical:
        if odi_score >= ODI_STAY_OUT_THRESHOLD and rej_v == "PACK":
            action = "STAY OUT"
            service = "NONE"
            why = "You have pace to pass on track; don't pit into traffic."
        elif odi_score >= ODI_STAY_OUT_THRESHOLD and action in ("PIT", "PIT NOW"):
            action = "STAY OUT"
            service = "NONE"
            why = "Pit window open; defer one lap — pace advantage supports staying out."
        elif _defensive_undercut_threat(
            telemetry,
            inside_window=inside_window,
            is_fuel_critical=is_fuel_critical,
            action=action,
            odi_score=odi_score,
            pace_behind=pace_behind,
            laps_remain=laps_remain,
        ):
            action = "PIT NOW"
            service = "4 TIRES" if tire_sets > 0 else "FUEL ONLY"
            why = "Undercut threat from behind; pit while window is open."

    inc = m.get("inc")
    if isinstance(inc, dict):
        headroom = inc.get("hr")
        if isinstance(headroom, int):
            if headroom <= 1 and action in ("PIT", "PIT NOW") and not is_fuel_critical:
                action = "STAY OUT"
                service = "NONE"
                why = "Incident limit tight; avoid risky pit entry unless fuel critical."
            elif headroom <= 2 and action in ("PIT", "PIT NOW") and not is_fuel_critical:
                gap_behind = _safe_float(m.get("gb"), 99.0)
                if gap_behind < 1.0:
                    action = "STAY OUT"
                    service = "NONE"
                    why = "Protect incident license; let pressure car through before pitting."

    return action, service, _append_context_notes(why, telemetry)


def incident_push_advice(telemetry: dict[str, Any]) -> str | None:
    """§16.2.1 voice/UI when off-track events pile up in the rolling window."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    inc = m.get("inc")
    if not isinstance(inc, dict):
        return None
    if _safe_int(inc.get("ot"), 0) < INCIDENT_OT_ALERT_COUNT:
        return None
    return format_engineer_advice(
        "STAY OUT",
        "THIS LAP",
        "NONE",
        "Pushing too hard — back it down 2%.",
        trigger="TRACK",
        conf="M",
        telemetry=telemetry,
    )


def _strategy_mode(telemetry: dict[str, Any]) -> str:
    """Read orchestration mode from fi.sm (defaults BALANCED)."""
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    sm = fi.get("sm")
    if isinstance(sm, dict):
        name = str(sm.get("n", "") or "").upper()
        if name in ("BALANCED", "OFFENSIVE", "DEFENSIVE"):
            return name
    return "BALANCED"


def tactical_undercut_advice(telemetry: dict[str, Any]) -> str | None:
    """§16.4 offensive — draft undercut predictor (fi.odi.uc) with runway guard."""
    if _under_caution(telemetry):
        return None
    if not _pace_stable_for_offense(telemetry):
        return None
    if _strategy_mode(telemetry) != "OFFENSIVE":
        return None
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    odi = fi.get("odi") if isinstance(fi.get("odi"), dict) else {}
    if not odi.get("uc"):
        return None
    if not _undercut_net_gain_ok(telemetry):
        return None
    fuel_laps_left, inside_window, is_fuel_critical = _fuel_context(telemetry)
    if not inside_window or is_fuel_critical:
        return None
    rej = fi.get("rej") if isinstance(fi.get("rej"), dict) else {}
    if _effective_reentry_verdict(rej, telemetry) != "CLEAN":
        return None
    laps_remain = _session_laps_remain(telemetry)
    if not _undercut_runway_ok(telemetry, laps_remain=laps_remain):
        return None
    rv = telemetry.get("rv", {}) if isinstance(telemetry.get("rv"), dict) else {}
    ahead = rv.get("ahead") if isinstance(rv.get("ahead"), dict) else {}
    ahead_pos = _safe_int(ahead.get("pos"), 0)
    pos_label = f"P{ahead_pos}" if ahead_pos > 0 else "car ahead"
    return format_engineer_advice(
        "PIT NOW",
        "THIS LAP",
        "4 TIRES",
        f"Undercut active on {pos_label}. Out-lap delta yields track position.",
        trigger="OVERTAKE",
        conf="H",
        telemetry=telemetry,
    )


def tactical_defensive_advice(telemetry: dict[str, Any]) -> str | None:
    """§16 defensive — apex loss, thermal stress, divebomb (fi.tac)."""
    if _under_caution(telemetry):
        return None
    if _strategy_mode(telemetry) != "DEFENSIVE":
        return None
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    tac = fi.get("tac")
    if not isinstance(tac, dict):
        return None
    if tac.get("def_line"):
        return format_engineer_advice(
            "ADJUST DEFENSIVE LINE",
            "THIS LAP",
            "NONE",
            "Over-defending hurting exit speed. Focus on clean exits.",
            trigger="TRACK",
            conf="M",
            telemetry=telemetry,
        )
    if tac.get("cool"):
        return format_engineer_advice(
            "COOL TIRES",
            "THIS LAP",
            "NONE",
            "Front tires overheating from defensive slides. Give up entry, prioritize traction.",
            trigger="TIRES",
            conf="H",
            telemetry=telemetry,
        )
    if tac.get("db"):
        return format_engineer_advice(
            "GUARD INSIDE",
            "THIS LAP",
            "NONE",
            "Divebomb threat inside — protect the entry.",
            trigger="TRACK",
            conf="M",
            telemetry=telemetry,
        )
    return None


def _fuel_critical_live_advice(telemetry: dict[str, Any]) -> str | None:
    """Force pit when tank is empty — suppresses tactical defensive/undercut overlays."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    if m.get("pr") or m.get("ps"):
        return None
    fuel_laps_left, _, is_fuel_critical = _fuel_context(telemetry)
    if not (is_fuel_critical or fuel_laps_left <= _fuel_critical_threshold(telemetry)):
        return None
    r = telemetry.get("r", {}) if isinstance(telemetry.get("r"), dict) else {}
    tire_sets = _safe_int(r.get("ts"), 0)
    service = "4 TIRES" if tire_sets > 0 else "FUEL ONLY"
    why = "Fuel critical; absolute limit of current tank reached."
    if _gwc_overtime_prep(telemetry):
        why = (
            "Fuel critical for GWC window — reserve an extra lap; "
            "absolute limit of current tank reached."
        )
    return format_engineer_advice(
        "PIT NOW",
        "THIS LAP",
        service,
        why,
        trigger="FUEL",
        conf="H",
        telemetry=telemetry,
    )


def _white_flag_coast_live_advice(telemetry: dict[str, Any]) -> str | None:
    """Suppress pit calls on the final lap when fuel covers the checkered."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    if m.get("pr") or m.get("ps"):
        return None
    fuel_laps_left, _, _ = _fuel_context(telemetry)
    if not _coast_to_checkered_ok(telemetry, fuel_laps_left=fuel_laps_left):
        return None
    return format_engineer_advice(
        "STAY OUT",
        "THIS LAP",
        "NONE",
        "Final lap — fuel covers checkered; pitting forfeits track position.",
        trigger="FUEL",
        conf="H",
        telemetry=telemetry,
    )


def _macro_strategy_pit_live_advice(telemetry: dict[str, Any]) -> str | None:
    """Strategy PIT/PIT NOW beats tactical overlays — fuel window, undercut yourself, etc."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    if m.get("pr") or m.get("ps"):
        return None
    if _under_caution(telemetry):
        return None
    result = evaluate_and_forecast_strategy(telemetry)
    imm = result.get("immediate_directive", {})
    if not isinstance(imm, dict):
        return None
    action = str(imm.get("ACTION", "") or "").upper()
    if action not in ("PIT", "PIT NOW"):
        return None
    service = str(imm.get("SERVICE", "NONE") or "NONE")
    why = str(imm.get("WHY", "") or "")
    trigger = "FUEL"
    if "undercut yourself" in why.lower():
        trigger = "OVERTAKE"
    elif "undercut" in why.lower():
        trigger = "OVERTAKE"
    return format_engineer_advice(
        action,
        "THIS LAP",
        service,
        why,
        trigger=trigger,
        conf="H",
        telemetry=telemetry,
    )


def resolve_live_advice(telemetry: dict[str, Any], *, mode: str = "live", track_name: str | None = None) -> str:
    """Priority chain: fuel-critical → white-flag coast → incident → macro pit → tactical → strategy."""
    fuel_advice = _fuel_critical_live_advice(telemetry)
    if fuel_advice:
        return fuel_advice
    coast_advice = _white_flag_coast_live_advice(telemetry)
    if coast_advice:
        return coast_advice
    incident = incident_push_advice(telemetry)
    if incident:
        return incident
    macro_pit = _macro_strategy_pit_live_advice(telemetry)
    if macro_pit:
        return macro_pit
    for producer in (tactical_undercut_advice, tactical_defensive_advice):
        advice = producer(telemetry)
        if advice:
            return advice
    return run_strategy(telemetry, mode=mode, track_name=track_name)


def _caution_podium_stay_out(telemetry: dict[str, Any], *, fuel_laps_left: float) -> bool:
    """P1–P3 stay out on yellow when fuel comfortably covers restart + green run."""
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    position = _safe_int(m.get("p"), 0)
    if position < 1 or position > CAUTION_PODIUM_MAX_POSITION:
        return False
    min_fuel = float(CAUTION_GREEN_RUN_BUFFER_LAPS + CAUTION_PODIUM_FUEL_BUFFER_LAPS)
    return fuel_laps_left >= min_fuel


def _caution_top_ten_fuel_abundant(fuel_laps_left: float) -> bool:
    """Top ten should not burn track position on early yellow with a full tank."""
    return fuel_laps_left >= float(CAUTION_TOP10_FUEL_ABUNDANCE_LAPS)


def _caution_immediate(
    telemetry: dict[str, Any],
    *,
    fuel_laps_left: float,
    is_fuel_critical: bool,
) -> dict[str, str] | None:
    """Caution/yellow immediate directive; None if not under caution."""
    s = telemetry.get("s", {})
    r = telemetry.get("r", {})
    fi = telemetry.get("fi", {})
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    flags = s.get("flb", {}) if isinstance(s.get("flb"), dict) else {}
    if not (flags.get("yel") or flags.get("cau")):
        return None

    if is_fuel_critical:
        return {
            "ACTION": "PIT NOW",
            "SERVICE": "4 TIRES" if _safe_int(r.get("ts"), 0) > 0 else "FUEL ONLY",
            "WHY": "Fuel range critical under caution; servicing full tank and fresh tires.",
        }

    if _caution_podium_stay_out(telemetry, fuel_laps_left=fuel_laps_left):
        return {
            "ACTION": "STAY OUT",
            "SERVICE": "NONE",
            "WHY": "Staying out under caution — podium track position; fuel covers restart.",
        }

    if _caution_stay_out_for_track_position(telemetry, fuel_laps_left=fuel_laps_left):
        return {
            "ACTION": "STAY OUT",
            "SERVICE": "NONE",
            "WHY": "Staying out under caution — top-ten track position outweighs pit cycle.",
        }

    position = _safe_int(m.get("p"), 0)
    cpi = fi.get("cpi") if isinstance(fi.get("cpi"), dict) else {}
    spots_lost = _safe_int(cpi.get("ll"), 99)
    herd = fi.get("hd") if isinstance(fi.get("hd"), dict) else {}
    herd_pit_ratio = _safe_float(herd.get("pra"), 0.0)

    if 1 <= position <= CAUTION_STAY_OUT_TOP_POSITION:
        if _caution_top_ten_fuel_abundant(fuel_laps_left):
            return {
                "ACTION": "STAY OUT",
                "SERVICE": "NONE",
                "WHY": "Staying out under caution — top-ten fuel abundance protects track position.",
            }
        if herd_pit_ratio < CAUTION_HERD_PIT_RATIO_MIN:
            return {
                "ACTION": "STAY OUT",
                "SERVICE": "NONE",
                "WHY": "Staying out under caution — field not boxing; protect top-ten position.",
            }
        if spots_lost > CAUTION_TOP10_MAX_SPOTS_LOST:
            return {
                "ACTION": "STAY OUT",
                "SERVICE": "NONE",
                "WHY": "Staying out under caution to secure critical track position.",
            }
        return {
            "ACTION": "PIT",
            "SERVICE": "4 TIRES",
            "WHY": "Caution window viable; field is boxing with low track position penalty.",
        }

    if herd_pit_ratio >= CAUTION_HERD_PIT_RATIO_MIN and spots_lost <= 3:
        return {
            "ACTION": "PIT",
            "SERVICE": "4 TIRES",
            "WHY": "Caution window viable; field is boxing with low track position penalty.",
        }
    return {
        "ACTION": "STAY OUT",
        "SERVICE": "NONE",
        "WHY": "Staying out under caution to secure critical track position.",
    }


def evaluate_and_forecast_strategy(telemetry: dict[str, Any]) -> dict[str, Any]:
    """
    1. Determines immediate action for the current lap.
    2. Simulates the rest of the race assuming green flag conditions from this point.
    """
    x = telemetry.get("x", {})
    s = telemetry.get("s", {})
    m = telemetry.get("m", {})
    r = telemetry.get("r", {})
    fi = telemetry.get("fi", {})

    current_lap = _safe_int(m.get("l"), 1)
    laps_remain = m.get("lr")
    if laps_remain is None:
        s_lt = s.get("lt")
        if isinstance(s_lt, int):
            laps_remain = max(0, int(s_lt) - current_lap)
        else:
            laps_remain = 0
    laps_remain = max(0, _safe_int(laps_remain, 0))

    fuel_laps_left, inside_window, is_fuel_critical = _fuel_context(telemetry)
    flb = s.get("flb", {}) if isinstance(s.get("flb"), dict) else {}
    rej = fi.get("rej") if isinstance(fi.get("rej"), dict) else {}
    rej_v = _effective_reentry_verdict(rej, telemetry)
    tire_sets = _safe_int(r.get("ts"), 0)
    max_tank_stint = max(1.0, _safe_float(r.get("ftl"), 25.0) - 1.0)

    # --- PART 1: IMMEDIATE LIVE DECISION (current lap) ---
    if flb.get("pcl", False):
        immediate_action = "STAY OUT"
        immediate_service = "NONE"
        why_reason = "Pit lane closed by race control."
    elif m.get("pr", False) or m.get("ps", False):
        if _safe_float(m.get("rr"), 0) > 0:
            immediate_action = "STAY OUT"
            immediate_service = "REPAIR"
            why_reason = "Stationary in pit stall; mandatory repairs ticking down."
        else:
            immediate_action = "STAY OUT"
            immediate_service = "NONE"
            why_reason = "Already on pit road or in the service box."
    elif (caution := _caution_immediate(telemetry, fuel_laps_left=fuel_laps_left, is_fuel_critical=is_fuel_critical)):
        immediate_action = caution["ACTION"]
        immediate_service = caution["SERVICE"]
        why_reason = caution["WHY"]
    elif _lapped_danger_should_stay_out(
        telemetry,
        inside_window=inside_window,
        fuel_laps_left=fuel_laps_left,
        rej=rej,
    ):
        immediate_action = "STAY OUT"
        immediate_service = "NONE"
        why_reason = LAPPED_DANGER_WHY
    elif is_fuel_critical:
        immediate_action = "PIT NOW"
        immediate_service = "4 TIRES" if tire_sets > 0 else "FUEL ONLY"
        why_reason = "Fuel critical; absolute limit of current tank reached."
    elif _coast_to_checkered_ok(telemetry, fuel_laps_left=fuel_laps_left):
        immediate_action = "STAY OUT"
        immediate_service = "NONE"
        why_reason = "Final lap — fuel covers checkered; pitting forfeits track position."
    elif _undercut_opportunity(
        telemetry,
        inside_window=inside_window,
        rej_v=rej_v,
        laps_remain=laps_remain,
        is_fuel_critical=is_fuel_critical,
    ):
        immediate_action = "PIT NOW"
        immediate_service = "4 TIRES" if tire_sets > 0 else "FUEL ONLY"
        why_reason = OFFENSIVE_UNDERCUT_WHY
    elif _undercut_yourself_opportunity(
        telemetry,
        inside_window=inside_window,
        fuel_laps_left=fuel_laps_left,
        rej_v=rej_v,
        laps_remain=laps_remain,
        is_fuel_critical=is_fuel_critical,
    ):
        immediate_action = "PIT NOW"
        immediate_service = "4 TIRES" if tire_sets > 0 else "FUEL ONLY"
        why_reason = LEADER_UNDERCUT_SELF_WHY
    elif _leader_clean_air_stretch_ok(
        telemetry,
        fuel_laps_left=fuel_laps_left,
        inside_window=inside_window,
        is_fuel_critical=is_fuel_critical,
        laps_remain=laps_remain,
    ):
        immediate_action = "STAY OUT"
        immediate_service = "NONE"
        why_reason = LEADER_CLEAN_AIR_STRETCH_WHY
    elif inside_window and rej_v == "CLEAN":
        position = _safe_int(m.get("p"), 0)
        leader_bind = (
            1 <= position <= LEADER_STRETCH_MAX_POSITION
            and fuel_laps_left <= _leader_stretch_fuel_floor(telemetry)
        )
        binding_fuel = is_fuel_critical or fuel_laps_left <= 1.0 or leader_bind
        if (
            _post_pit_alert_quiet(telemetry)
            and not is_fuel_critical
            and not binding_fuel
        ):
            immediate_action = "STAY OUT"
            immediate_service = "NONE"
            why_reason = "Fresh stint after pit stop; holding position before next window."
        else:
            immediate_action = "PIT NOW"
            immediate_service = "4 TIRES" if tire_sets > 0 else "FUEL ONLY"
            why_reason = "Pit window open; clean reentry air window confirmed."
    elif inside_window and rej_v == "PACK":
        immediate_action = "STAY OUT"
        immediate_service = "NONE"
        why_reason = "Pit window open but reentry projects heavy pack traffic; delay one lap."
    else:
        immediate_action = "STAY OUT"
        immediate_service = "NONE"
        why_reason = "Maintaining current green flag stint pacing."

    rej_v = _effective_reentry_verdict(rej, telemetry)
    immediate_action, immediate_service, why_reason = _apply_context_directive_overrides(
        telemetry,
        immediate_action,
        immediate_service,
        why_reason,
        inside_window=inside_window,
        is_fuel_critical=is_fuel_critical,
        rej_v=rej_v,
        tire_sets=tire_sets,
        laps_remain=laps_remain,
    )

    # --- PART 2: REST-OF-RACE GREEN FORECAST (skip under active caution) ---
    future_stops: list[dict[str, Any]] = []
    is_caution = bool(flb.get("yel") or flb.get("cau"))

    if not is_caution and laps_remain > 0:
        steer_penalty = _steer_forecast_penalty(telemetry)
        s = telemetry.get("s", {}) if isinstance(telemetry.get("s"), dict) else {}
        tenv = s.get("tenv") if isinstance(s.get("tenv"), dict) else {}
        tire_cap = _safe_int(tenv.get("tsc"), 0)
        if tire_cap <= 0:
            tire_cap = max(1, int(max_tank_stint))
        effective_tire_cap = max(1, tire_cap - steer_penalty)
        sim_stint_laps = _safe_int(m.get("sl"), 0)

        if immediate_action in ("PIT", "PIT NOW"):
            sim_current_lap = current_lap + 1
            sim_laps_remaining = laps_remain - 1
            sim_fuel_laps_remaining = max_tank_stint
            sim_stint_laps = 0
        else:
            sim_current_lap = current_lap + 1
            sim_laps_remaining = laps_remain - 1
            sim_fuel_laps_remaining = _forecast_fuel_laps_seed(telemetry, max_tank_stint=max_tank_stint)

        stop_counter = 1
        run_to_finish = _can_run_to_finish(telemetry)
        session_end = _session_total_laps(telemetry)
        while sim_laps_remaining > 0:
            tire_limited = sim_stint_laps >= effective_tire_cap
            fuel_limited = sim_fuel_laps_remaining <= 1.0
            if fuel_limited and run_to_finish:
                fuel_limited = False
            if tire_limited and not _tire_pit_worth_it(telemetry, laps_remaining=sim_laps_remaining):
                tire_limited = False
            if tire_limited or fuel_limited:
                if sim_current_lap > session_end:
                    break
                tires_available = tire_sets - stop_counter
                if tire_limited and fuel_limited:
                    service_call = "4 TIRES" if tires_available > 0 else "FUEL ONLY"
                    context = "Stint limits reached under projected green flag run."
                elif tire_limited:
                    service_call = "4 TIRES" if tires_available > 0 else "FUEL ONLY"
                    context = "Tire wear / steering fatigue limit under projected green flag run."
                else:
                    service_call = "4 TIRES" if tires_available > 0 else "FUEL ONLY"
                    context = "Tank limits reached under projected green flag run."
                future_stops.append(
                    {
                        "forecast_stop_number": stop_counter,
                        "estimated_pit_lap": min(sim_current_lap, session_end),
                        "laps_from_now": sim_current_lap - current_lap,
                        "service_required": service_call,
                        "context": context,
                    }
                )
                sim_fuel_laps_remaining = max_tank_stint
                sim_stint_laps = 0
                stop_counter += 1

            sim_current_lap += 1
            sim_laps_remaining -= 1
            sim_fuel_laps_remaining -= 1.0
            sim_stint_laps += 1

    return {
        "current_lap": current_lap,
        "laps_remaining_in_race": laps_remain,
        "immediate_directive": {
            "ACTION": immediate_action,
            "SERVICE": immediate_service,
            "WHY": why_reason,
        },
        "green_flag_rest_of_race_forecast": {
            "total_estimated_future_stops": len(future_stops),
            "projected_pit_schedule": future_stops,
            "paused_for_caution": is_caution,
        },
    }


def evaluate_race_strategy(telemetry: dict[str, Any]) -> tuple[str, str, str, str]:
    """Backward-compatible tuple view of the immediate live directive."""
    result = evaluate_and_forecast_strategy(telemetry)
    imm = result.get("immediate_directive", {})
    return (
        str(imm.get("ACTION", "STAY OUT")),
        "THIS LAP",
        str(imm.get("SERVICE", "NONE")),
        str(imm.get("WHY", "")),
    )


def _derive_trigger_conf(
    action: str,
    service: str,
    why: str,
    *,
    is_caution: bool = False,
    is_fuel_critical: bool = False,
) -> tuple[str, str]:
    why_l = (why or "").lower()
    if "undercut active" in why_l:
        return "OVERTAKE", "H"
    if "offensive undercut" in why_l or "undercut threat" in why_l:
        return "TRACK", "M"
    if "over-defending" in why_l or "divebomb" in why_l:
        return "TRACK", "M"
    if "overheating" in why_l and "defensive" in why_l:
        return "TIRES", "H"
    if "lap down" in why_l or "green stop will put us" in why_l:
        return "TRACK", "M"
    if "pit lane" in why_l or "race control" in why_l or "flags" in why_l:
        return "FLAGS", "H"
    if "repair" in why_l or service == "REPAIR":
        return "REPAIR", "H"
    if is_caution:
        return "FLAGS", "M"
    if is_fuel_critical or "fuel" in why_l:
        return "FUEL", "H" if action in ("PIT NOW",) else "M"
    if "tire" in why_l or "degradation" in why_l:
        return "TIRES", "M"
    if "traffic" in why_l or "pack" in why_l or "reentry" in why_l:
        return "TRACK", "M"
    return "FUEL", "M"


# ================================================================
# 2b. DASHBOARD ADVICE FORMAT (overlay / voice)
# ================================================================

_DASHBOARD_WIDTH = 98


def _dash_sep(char: str = "=") -> str:
    return char * _DASHBOARD_WIDTH


def _why_headline(why: str) -> str:
    """Uppercase engineer headline for the dashboard WHY row."""
    w = (why or "").strip()
    if not w:
        return "NO CHANGE TO PLAN"
    wl = w.lower()
    if "lap down" in wl and ("delaying pit" in wl or "green stop" in wl):
        return "DELAYING PIT STOP — GREEN STOP WILL PUT US A LAP DOWN. EXTENDING TO FIND CLEANER WINDOW."
    if "incident limit tight" in wl:
        return "INCIDENT LIMIT TIGHT — DELAY PIT ENTRY"
    if "incident license" in wl or "pressure car" in wl:
        return "PROTECT LICENSE — LET PRESSURE CAR THROUGH"
    if "pack traffic" in wl or ("pack" in wl and "reentry" in wl):
        return "PIT WINDOW OPEN — AVOIDING HEAVY TRAFFIC PACK"
    if "clean reentry" in wl and "pit window" in wl:
        return "PIT WINDOW OPEN — CLEAN REENTRY CONFIRMED"
    if "pass on track" in wl:
        return "PIT WINDOW OPEN — PACE ADVANTAGE, STAY OUT"
    if "back it down" in wl:
        return "PUSHING TOO HARD — BACK IT DOWN 2%"
    if "offensive undercut" in wl:
        return "OFFENSIVE UNDERCUT — BOX FOR CLEAN MERGE"
    if "undercut active" in wl:
        return "UNDERCUT ACTIVE — OUT-LAP DELTA YIELDS POSITION"
    if "over-defending" in wl:
        return "OVER-DEFENDING — FOCUS ON CLEAN EXITS"
    if "overheating from defensive" in wl:
        return "FRONT TIRES OVERHEATING — PRIORITIZE TRACTION"
    if "divebomb threat" in wl:
        return "DIVEBOMB THREAT — GUARD THE ENTRY"
    if "undercut threat" in wl:
        return "UNDERCUT THREAT — PIT WHILE WINDOW OPEN"
    if "gwc window" in wl:
        return "GWC FUEL RESERVE — PIT THIS LAP"
    if "fuel critical" in wl and "unless fuel critical" not in wl:
        return "FUEL CRITICAL — PIT THIS LAP"
    if "absolute limit" in wl:
        return "FUEL CRITICAL — PIT THIS LAP"
    if "fuel range critical" in wl:
        return "FUEL CRITICAL UNDER CAUTION — BOX NOW"
    if "top-ten track position" in wl:
        return "CAUTION — STAY OUT FOR TOP-TEN POSITION"
    if "podium track position" in wl:
        return "CAUTION — STAY OUT FOR PODIUM POSITION"
    if "top-ten fuel abundance" in wl:
        return "CAUTION — STAY OUT (TOP 10 FUEL)"
    if "field not boxing" in wl:
        return "CAUTION — STAY OUT (FIELD NOT BOXING)"
    if "final lap" in wl and "checkered" in wl:
        return "WHITE FLAG — COAST TO CHECKERED ON FUEL"
    if "undercut yourself" in wl:
        return "UNDERCUT YOURSELF — BOX BEFORE INHERITING LEAD"
    if "leader clean-air stretch" in wl or "clean-air stretch" in wl:
        return "LEADER STRETCH — PROTECT CLEAN AIR"
    if "caution window viable" in wl or "field is boxing" in wl:
        return "CAUTION WINDOW — FIELD BOXING, LOW POSITION COST"
    if "staying out under caution" in wl:
        return "CAUTION — STAY OUT FOR TRACK POSITION"
    if "fresh stint" in wl:
        return "FRESH STINT — HOLD BEFORE NEXT WINDOW"
    if "pit lane closed" in wl:
        return "PIT LANE CLOSED — RACE CONTROL"
    if "pit road" in wl or "service box" in wl:
        return "ON PIT ROAD — HOLD POSITION"
    if "repair" in wl or "mandatory repairs" in wl:
        return "MANDATORY REPAIRS — STAY IN STALL"
    if "maintaining" in wl:
        return "GREEN FLAG — MAINTAIN STINT PACE"
    return w.upper()


def _why_detail_tail(why: str, headline: str) -> str | None:
    """Extra context lines (marbles, steering, track temp) below the headline."""
    wl = (why or "").lower()
    if "pickup on tires" in wl:
        return why[wl.find("pickup on tires") :].strip()
    if "car is loose" in wl:
        return why[wl.find("car is loose") :].strip()
    for key in ("track cooled", "track heated"):
        if key in wl:
            return why[wl.find(key) :].strip()
    return None


def _display_service_type(service: str, *, action: str, inside_window: bool) -> str:
    s = (service or "NONE").upper()
    act = (action or "").upper()
    if s == "REPAIR":
        return "REPAIR"
    if s == "NONE":
        return "NONE"
    if "FUEL ONLY" in s:
        return "FUEL"
    if "4 TIRES" in s or s == "BOTH":
        if inside_window or act in ("PIT", "PIT NOW"):
            return "4 TIRES + FUEL"
        return "4 TIRES"
    if act in ("PIT", "PIT NOW") and s == "NONE":
        return "FUEL"
    return s


def _target_pit_label(
    result: dict[str, Any],
    telemetry: dict[str, Any],
    *,
    action: str,
    fuel_laps_left: float,
) -> str:
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    current = _safe_int(m.get("l"), 1)
    session_end = _session_total_laps(telemetry)
    act = (action or "").upper()
    if act in ("PIT", "PIT NOW"):
        return str(current)
    if _can_run_to_finish(telemetry) and not _under_caution(telemetry):
        return "CHECKERED"
    window_fuel = _fuel_laps_for_pit_window(telemetry, fuel_laps_left=fuel_laps_left)
    if window_fuel > 0:
        pit_lap = current + max(0, int(math.floor(window_fuel)))
        if pit_lap > session_end:
            return "CHECKERED"
        return str(pit_lap)
    forecast = result.get("green_flag_rest_of_race_forecast", {})
    stops = forecast.get("projected_pit_schedule", []) if isinstance(forecast, dict) else []
    if isinstance(stops, list) and stops:
        first = stops[0]
        if isinstance(first, dict):
            pit_lap = _safe_int(first.get("estimated_pit_lap"), current)
            if pit_lap > session_end:
                return "CHECKERED"
            return str(pit_lap)
    return "CHECKERED" if _can_run_to_finish(telemetry) else str(current)


def _target_box_laps(
    telemetry: dict[str, Any],
    *,
    fuel_laps_left: float,
    payback_laps: float,
) -> tuple[int, int] | None:
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    current = _safe_int(m.get("l"), 1)
    session_end = _session_total_laps(telemetry)
    if _can_run_to_finish(telemetry) and not _under_caution(telemetry):
        return None
    window_fuel = _fuel_laps_for_pit_window(telemetry, fuel_laps_left=fuel_laps_left)
    if window_fuel <= 0:
        return None
    end_lap = min(session_end, current + max(1, int(math.floor(window_fuel))))
    if end_lap <= current:
        return None
    if payback_laps > 0:
        start_lap = max(current + 1, end_lap - max(1, int(round(payback_laps))))
    else:
        start_lap = max(current, end_lap - 1)
    if start_lap > end_lap:
        start_lap = end_lap
    return start_lap, end_lap


def _pace_delta_label(telemetry: dict[str, Any]) -> str:
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    pc = m.get("pc") if isinstance(m.get("pc"), dict) else {}
    trend = _safe_float(pc.get("trend_s"), 0.0)
    fo = m.get("fo")
    if isinstance(fo, (int, float)) and abs(float(fo)) > 0.01:
        delta = float(fo)
        label = "Stable" if abs(delta) < 0.05 else ("Slowing" if delta > 0 else "Improving")
        return f"{delta:+.2f}s ({label})"
    if abs(trend) < 0.05:
        return f"{trend:+.2f}s (Stable)"
    label = "Slowing" if trend > 0 else "Improving"
    return f"{trend:+.2f}s ({label})"


def _input_smoothness_pct(telemetry: dict[str, Any]) -> str | None:
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    drv = m.get("drv") if isinstance(m.get("drv"), dict) else None
    if not drv:
        return None
    ssr = _safe_float(drv.get("ssr"), 1.0)
    if ssr <= 0:
        return None
    return f"{max(0, min(100, int(round(100.0 / ssr))))}%"


def _incidents_label(telemetry: dict[str, Any]) -> str | None:
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    inc = m.get("inc") if isinstance(m.get("inc"), dict) else None
    if not inc:
        return None
    tot = inc.get("tot")
    hr = inc.get("hr")
    if isinstance(tot, int):
        if isinstance(hr, int) and hr >= 0:
            limit = tot + hr
            return f"{tot} / {limit}"
        return str(tot)
    return None


def _fuel_burn_status(telemetry: dict[str, Any], *, fuel_laps_left: float) -> str:
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    if m.get("mk") is True:
        return "MATCH"
    ls = m.get("ls")
    if isinstance(ls, (int, float)) and float(ls) > 0.05:
        return "HIGH"
    if fuel_laps_left <= 1.5:
        return "RICH"
    return "MATCH"


def _green_ema_liters(telemetry: dict[str, Any]) -> float | None:
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    for key in ("fpe", "fpl"):
        v = m.get(key)
        if isinstance(v, (int, float)) and float(v) > 0:
            return float(v) / 0.2641720523581484
    return None


def _reentry_detail_lines(telemetry: dict[str, Any]) -> str | None:
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    rej = fi.get("rej") if isinstance(fi.get("rej"), dict) else {}
    verdict = str(rej.get("v", "") or "").upper()
    if not verdict or verdict == "UNKNOWN":
        return None
    pack = _safe_int(rej.get("n"), 0)
    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    pos = _safe_int(m.get("p"), 0)
    merge_pos = pos + pack if pos > 0 and pack > 0 else pos
    bits = [f"[ Reentry Zone: {verdict} ({pack} Cars in Window) ]"]
    if merge_pos > 0:
        bits.append(f"[ Projected Merge Order: P{merge_pos} ]")
    return "  ·  ".join(bits)


def format_strategy_dashboard(
    result: dict[str, Any],
    telemetry: dict[str, Any],
    *,
    trigger: str | None = None,
    conf: str | None = None,
) -> str:
    """Rich multi-section engineer dashboard for overlay display."""
    imm = result.get("immediate_directive", {}) if isinstance(result.get("immediate_directive"), dict) else {}
    action = str(imm.get("ACTION", "STAY OUT"))
    service = str(imm.get("SERVICE", "NONE"))
    why = str(imm.get("WHY", ""))

    flags = telemetry.get("s", {}).get("flb", {})
    is_caution = isinstance(flags, dict) and (flags.get("yel") or flags.get("cau"))
    fuel_laps_left, inside_window, is_fuel_critical = _fuel_context(telemetry)
    if trigger is None or conf is None:
        trigger, conf = _derive_trigger_conf(
            action,
            service,
            why,
            is_caution=bool(is_caution),
            is_fuel_critical=is_fuel_critical,
        )

    m = telemetry.get("m", {}) if isinstance(telemetry.get("m"), dict) else {}
    s = telemetry.get("s", {}) if isinstance(telemetry.get("s"), dict) else {}
    fi = telemetry.get("fi", {}) if isinstance(telemetry.get("fi"), dict) else {}
    current_lap = _safe_int(m.get("l"), 1)
    payback = _resolve_pit_payback_laps(telemetry)
    target_pit = _target_pit_label(result, telemetry, action=action, fuel_laps_left=fuel_laps_left)
    service_display = _display_service_type(service, action=action, inside_window=inside_window)
    headline = _why_headline(why)
    reentry_extra = _reentry_detail_lines(telemetry)

    call_line = (
        f"STRATEGY CALL :   [ {action.upper()} ]   ·   "
        f"TARGET PIT: {target_pit}   ·   SERVICE TYPE: {service_display}"
    )
    why_line = f" WHY           :   {headline}"
    lines = [_dash_sep(), call_line, why_line]
    detail = _why_detail_tail(why, headline)
    if detail:
        lines.append(f"                   {detail}")
    if reentry_extra:
        lines.append(f"                   {reentry_extra}")
    lines.append(_dash_sep())

    fuel_short = m.get("ls")
    short_val = _safe_float(fuel_short, 0.0) if isinstance(fuel_short, (int, float)) else 0.0
    ema_L = _green_ema_liters(telemetry)
    ema_bit = f"  ·   [ Green EMA: {ema_L:.2f} L/lap ]" if ema_L is not None else ""
    box = _target_box_laps(telemetry, fuel_laps_left=fuel_laps_left, payback_laps=payback)
    box_bit = f"  ·   [ Target Box: L{box[0]} - L{box[1]} ]" if box else ""

    machine_line1 = (
        f" MACHINE STATE :   CURRENT LAP: {current_lap}  ·  "
        f"FUEL LEFT: {fuel_laps_left:.1f} Laps  ·  "
        f"TARGET FUEL BURN: {_fuel_burn_status(telemetry, fuel_laps_left=fuel_laps_left)}"
    )
    machine_extras = []
    if m.get("mk") is False and short_val > 0.01:
        machine_extras.append(f"[ Short: {-short_val:+.1f} Laps ]")
    if ema_bit:
        machine_extras.append(ema_bit.strip("  · "))
    if box_bit:
        machine_extras.append(box_bit.strip("  · "))
    machine_line2 = f"                   {'  ·   '.join(machine_extras)}" if machine_extras else ""
    lines.append(machine_line1)
    if machine_line2:
        lines.append(machine_line2)
    lines.append(_dash_sep("-"))

    pace = _pace_delta_label(telemetry)
    smooth = _input_smoothness_pct(telemetry)
    inc_label = _incidents_label(telemetry)
    perf_bits = [f"LAP PACE: {pace}"]
    if smooth:
        perf_bits.append(f"INPUT SMOOTHNESS: {smooth}")
    if inc_label:
        perf_bits.append(f"INCIDENTS: {inc_label}")
    lines.append(f" PERFORMANCE   :   {'   ·   '.join(perf_bits)}")
    lines.append(_dash_sep())

    env_bits: list[str] = []
    draft = fi.get("draft") if isinstance(fi.get("draft"), dict) else {}
    if draft.get("on") or _safe_int(draft.get("streak"), 0) > 0:
        ga = _safe_float(m.get("ga"), 0.0)
        env_bits.append(f"DRAFT: ACTIVE ({ga:.1f}s Window)")
    else:
        env_bits.append("DRAFT: CLEAR")
    ttc = s.get("ttc")
    if isinstance(ttc, (int, float)):
        env_bits.append(f"TRACK TEMP: {float(ttc):.1f}°C")
    elif isinstance(s.get("tt"), (int, float)):
        env_bits.append(f"TRACK TEMP: {float(s['tt']):.1f}°F")
    ga = m.get("ga")
    pit_need = _safe_float(telemetry.get("r", {}).get("pl"), payback * 10.0 if payback else 22.0)
    if isinstance(ga, (int, float)):
        env_bits.append(f"REENTRY GAP: {float(ga):+.1f}s (Need {pit_need:.1f}s)")
    lines.append(f" ENVIRONMENT   :   {' · '.join(env_bits)}")
    lines.append(_dash_sep())

    # Compact footer for automation hooks (alerts, logging) — not spoken aloud.
    forecast = result.get("green_flag_rest_of_race_forecast", {})
    if forecast.get("paused_for_caution"):
        forecast_line = "FORECAST: Paused under caution — re-run after green"
    else:
        stops = forecast.get("projected_pit_schedule", []) if isinstance(forecast, dict) else []
        if isinstance(stops, list) and stops:
            bits = [
                f"L{stop['estimated_pit_lap']} {stop['service_required']}"
                for stop in stops[:5]
                if isinstance(stop, dict)
            ]
            n = forecast.get("total_estimated_future_stops", len(stops))
            forecast_line = f"FORECAST: {n} green-flag stop(s) — " + "; ".join(bits)
        else:
            forecast_line = "FORECAST: No further stops if green to the end"
    lines.append(forecast_line)
    lines.append(f"TRIGGER: {trigger}  CONF: {conf}")

    return "\n".join(lines)


def format_live_strategy(result: dict[str, Any], telemetry: dict[str, Any]) -> str:
    """Format immediate call plus dashboard sections for the UI."""
    return format_strategy_dashboard(result, telemetry)


def format_engineer_advice(
    action: str,
    timing: str,
    service: str,
    why: str,
    *,
    trigger: str | None = None,
    conf: str | None = None,
    telemetry: dict[str, Any] | None = None,
) -> str:
    """Format engineer advice; uses dashboard layout when telemetry is available."""
    if isinstance(telemetry, dict):
        result = {
            "immediate_directive": {"ACTION": action, "SERVICE": service, "WHY": why},
            "green_flag_rest_of_race_forecast": {
                "paused_for_caution": False,
                "projected_pit_schedule": [],
                "total_estimated_future_stops": 0,
            },
        }
        return format_strategy_dashboard(result, telemetry, trigger=trigger, conf=conf)
    trig = trigger or "FUEL"
    level = conf or "M"
    headline = _why_headline(why)
    lines = [
        _dash_sep(),
        f"STRATEGY CALL :   [ {action.upper()} ]   ·   TARGET PIT: LAP --   ·   SERVICE TYPE: {service.upper()}",
        f" WHY           :   {headline}",
        _dash_sep(),
        f"TRIGGER: {trig}  CONF: {level}",
    ]
    return "\n".join(lines)


def _first_call_line(advice: str) -> str:
    for line in (advice or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s and not s.startswith(("=", "-")) and "STRATEGY CALL" not in s.upper():
            if s.upper().startswith("WHY"):
                continue
            if s.upper().startswith(("FORECAST:", "TRIGGER:", "MACHINE STATE", "PERFORMANCE", "ENVIRONMENT")):
                continue
            return s
        if "STRATEGY CALL" in s.upper():
            return s
    return ""


def advice_call_line(advice: str) -> str:
    """Canonical dedup key: ACTION — THIS LAP — SERVICE (parsed from any layout)."""
    action, timing, service = parse_call_line(advice)
    if action:
        return f"{action} — {timing or 'THIS LAP'} — {service or 'NONE'}"
    return _first_call_line(advice)


def _normalize_display_service(service_raw: str) -> str:
    s = (service_raw or "").upper().strip()
    if "4 TIRES" in s and "FUEL" in s:
        return "4 TIRES"
    if s == "FUEL" or "FUEL ONLY" in s:
        return "FUEL ONLY"
    if s == "REPAIR":
        return "REPAIR"
    if s == "NONE":
        return "NONE"
    return s.split("·")[0].strip()


def parse_call_line(advice: str) -> tuple[str, str, str]:
    """Parse ACTION, TIMING, SERVICE from dashboard or legacy em-dash layout."""
    text = advice or ""
    bracket = re.search(r"\[\s*([^\]]+)\s*\]", text)
    if bracket and "STRATEGY CALL" in text.upper():
        action = bracket.group(1).strip().upper()
        sm = re.search(r"SERVICE TYPE:\s*([^\n]+)", text, re.IGNORECASE)
        service = _normalize_display_service(sm.group(1)) if sm else "NONE"
        return action, "THIS LAP", service

    head = _first_call_line(text)
    if "STRATEGY CALL" in head.upper() and bracket:
        action = bracket.group(1).strip().upper()
        sm = re.search(r"SERVICE TYPE:\s*([^\n]+)", head, re.IGNORECASE)
        service = _normalize_display_service(sm.group(1)) if sm else "NONE"
        return action, "THIS LAP", service

    parts = [p.strip() for p in head.split("—")]
    action = parts[0].upper() if parts else ""
    timing = parts[1].upper() if len(parts) > 1 else "THIS LAP"
    service = parts[2].upper() if len(parts) > 2 else "NONE"
    return action, timing, service


def parse_advice_why(advice: str) -> str:
    """Extract WHY text from dashboard or legacy advice."""
    parts: list[str] = []
    collecting = False
    for line in (advice or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        upper = s.upper()
        if upper.startswith("WHY"):
            collecting = True
            parts.append(s.split(":", 1)[-1].strip())
            continue
        if collecting:
            if upper.startswith("=") or upper.startswith("MACHINE STATE"):
                break
            parts.append(s)
    return " ".join(parts).strip()


def _fuel_laps_left(telemetry: dict[str, Any]) -> float | None:
    m = telemetry.get("m", {})
    x = telemetry.get("x", {})
    if x.get("fe", 0) == 1 and m.get("fcq") in ("hi", "med"):
        try:
            return float(m.get("fl", 0.0))
        except (TypeError, ValueError):
            return None
    try:
        fpl = float(m.get("fpl", 0.0))
        ful = float(m.get("ful", 0.0))
    except (TypeError, ValueError):
        return None
    if fpl <= 0:
        return None
    return ful / fpl


def laps_until_pit(action: str, timing: str) -> int | None:
    """0 = pit this lap, N = pit in N laps, None = not an active pit call."""
    act = (action or "").upper()
    if act not in ("PIT", "PIT NOW"):
        return None
    if act == "PIT NOW":
        return 0
    t = (timing or "").upper()
    if "THIS LAP" in t:
        return 0
    if "PIT IN" in t and "LAP" in t:
        try:
            tokens = t.replace("LAPS", "").replace("LAP", "").split()
            n = int(tokens[tokens.index("IN") + 1])
            return max(0, n)
        except Exception:
            return None
    return None


def _under_caution(telemetry: dict[str, Any]) -> bool:
    flags = telemetry.get("s", {}).get("flb", {})
    if not isinstance(flags, dict):
        return False
    return bool(flags.get("yel") or flags.get("cau"))


def should_auto_alert(
    telemetry: dict[str, Any],
    advice: str,
    *,
    max_laps: int = ALERT_LAP_HORIZON,
    forecast_result: dict[str, Any] | None = None,
    caution_started: bool = False,
    caution_ended: bool = False,
) -> bool:
    """True when a pit stop is due within max_laps (or fuel window is inside that range)."""
    if incident_push_advice(telemetry) is not None:
        return True
    if tactical_undercut_advice(telemetry) is not None:
        return True
    if tactical_defensive_advice(telemetry) is not None:
        return True

    if caution_started and _under_caution(telemetry):
        return True

    action, timing, _ = parse_call_line(advice)
    n = laps_until_pit(action, timing)
    fuel_laps = _fuel_laps_left(telemetry)
    fuel_critical = fuel_laps is not None and fuel_laps <= 1.0

    if fuel_critical and action in ("PIT", "PIT NOW"):
        return True

    if caution_ended:
        return False

    quiet = _post_pit_alert_quiet(telemetry, alert_horizon=max_laps)
    if quiet:
        return False

    if n is not None and n <= max_laps:
        return True

    if fuel_laps is not None and fuel_laps <= max_laps:
        m = telemetry.get("m", {})
        if action in ("PIT", "PIT NOW"):
            return True
        if m.get("mk") is False:
            return True
        pb = _resolve_pit_payback_laps(telemetry)
        if pb > 0 and fuel_laps <= pb + 1:
            return True

        if _under_caution(telemetry):
            if fuel_laps <= min(3.0, max_laps):
                return action in ("PIT", "PIT NOW")

    result = forecast_result if isinstance(forecast_result, dict) else evaluate_and_forecast_strategy(telemetry)
    forecast = result.get("green_flag_rest_of_race_forecast", {})
    if not forecast.get("paused_for_caution"):
        for stop in forecast.get("projected_pit_schedule", []) or []:
            if isinstance(stop, dict) and _safe_int(stop.get("laps_from_now"), 99) <= max_laps:
                return True
    return False


from .pre_race_strategy import run_pre_race_plan


def run_strategy(telemetry: dict[str, Any], *, mode: str = "live", track_name: str | None = None) -> str:
    """Evaluate telemetry and return formatted engineer advice text."""
    if (mode or "live").lower() == "strategy":
        return run_pre_race_plan(telemetry, track_name=track_name)

    result = evaluate_and_forecast_strategy(telemetry)
    return format_live_strategy(result, telemetry)


# ================================================================
# 3. PYIRSDK CRITICAL LIVE MEMORY EXTENSIONS
# ================================================================
REQUIRED_PYIRSDK_VARIABLES = [
    "CarIdxPitStopCount",
    "CarIdxTrackSurface",
    "TrackWetness",
    "Precipitation",
    "TireSetsAvailable",
    "PlayerCarDryTireSetLimit",
    "CarIdxLapTires",
    "EngineWarnings",
    "FuelLevelPct",
]
