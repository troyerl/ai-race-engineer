"""Project-wide scalar constants, telemetry safeguards, and track defaults."""

from __future__ import annotations

from typing import Any

# ==============================================================================
# GridNotes v1.0.x - Section 14 Constants Alignment
# ==============================================================================

# Section 2.2: Sunoco Green E15 nominal density
GASOLINE_KG_PER_L = 0.73

# Section 14: Engine-off zero-flow safeguard clamp
MIN_FUEL_USE_KG_PER_H = 0.05

# Section 2.3: Floored pacing burn constraint (caution / missing EMA)
DEFAULT_CAUTION_BURN_L = 0.20

# Section 5: Start/Finish spatial midpoint divider
LAP_DIST_WRAP_HALF = 0.5

# iRacing SDK: TireSetsAvailable == 255 means unlimited sets for the session
IRSDK_TIRE_SETS_UNLIMITED = 255

# Section 2.1: Green-flag filtering smoothing factor
FUEL_EMA_ALPHA = 0.45

# Section 2.4: Stint inflation / absurd-range fuel clamps
FUEL_LAPS_CLAMP_MULTIPLIER = 2.0
FUEL_LAPS_CLAMP_OFFSET = 30.0

# Section 14: Lap pace / distance tracking boundaries
LAP_HISTORY_DEPTH = 5
REENTRY_WINDOW_PCT = 0.035
LAPPED_DANGER_FUEL_MIN_LAPS = 1.5
HERD_POSITION_WINDOW = 5
ALERT_LAP_HORIZON = 5
POST_PIT_ALERT_MIN_STINT_LAPS = 6
DEFAULT_AVG_LAP_S = 90.0
MIN_AVG_LAP_S = 1.0

# Section 12.3: Pre-race triangular wear buffer
TIRE_COST_THRESHOLD_BUMP = 30.0

# Section 16: Dynamic context (track / driver / traffic)
TRACK_TEMP_WEAR_COEFF = -0.004
TRACK_TEMP_COOL_STINT_BONUS = 0.05
TRACK_TEMP_HOT_STINT_PENALTY = 0.08
TRACK_TEMP_SHIFT_THRESHOLD_C = 10.0
MARBLE_LAPS_REMAINING = 2
LAT_ACCEL_OFFLINE_THRESHOLD = 1.8
DRAFT_GAP_SEC = 1.5
DRAFT_STREAK_ODI_PENALTY = 0.3
# Clean-lap gate for tire falloff (m.fo) — oval draft / incident filtering
CLEAN_LAP_DRAFT_GAP_SEC = 1.2
CLEAN_LAP_DRAFT_FRAC_MAX = 0.30
CLEAN_LAP_BUFFER_DEPTH = 5
CLEAN_LAP_FALLOFF_MIN_SAMPLES = 3
PACE_STABILITY_WINDOW = 8
PACE_STABILITY_MIN_SAMPLES = 5
PACE_STABILITY_STD_THRESHOLD_S = 0.40
TELEMETRY_POLL_INTERVAL_S = 0.25
ODI_PACK_PENALTY = 0.5
ODI_STAY_OUT_THRESHOLD = 0.5
ODI_UNDERCUT_THRESHOLD = -0.3
MIN_UNDERCUT_RUNWAY_LAPS = 8
MIN_UNDERCUT_STINT_LAPS = 5
UNDERCUT_GAP_AHEAD_MAX_SEC = 1.0
UNDERCUT_RIVAL_PACE_DELTA_MIN = 0.12
# Tactical racing (§16 offensive / defensive)
RIVAL_DEGRAD_TREND_MIN = 0.15
ODI_RIVAL_DEGRAD_MULT = 0.85
WEAR_STABLE_FALLOFF_MAX = 0.12
THERMAL_WARM_C = 95.0
THERMAL_GREASY_C = 105.0
APEX_LOSS_DEFEND_PCT = 5.0
APEX_STEER_DELTA_MIN = 0.08
DEFENSIVE_GAP_BEHIND_MAX = 0.5
DIVEBOMB_GAP_MAX = 0.4
DIVEBOMB_CLOSING_RATE = -0.2
BRAKE_ZONE_LAP_DIST_START = 0.06
BRAKE_ZONE_LAP_DIST_END = 0.20
TACTICAL_ALERT_COOLDOWN_LAPS = 2
DIVEBOMB_VOICE_COOLDOWN_S = 8.0
# Strategy mode orchestration (offense vs defense)
MODE_DEFENSIVE_GAP_BEHIND_MAX = 0.5
MODE_OFFENSIVE_GAP_AHEAD_MAX = 0.7
MODE_OFFENSIVE_GAP_BEHIND_MIN = 0.8
INCIDENT_WINDOW_LAPS = 3
INCIDENT_OT_ALERT_COUNT = 2
STEER_STD_ELEVATED = 1.4
STEER_STD_HIGH = 1.8
STEER_SAMPLE_LAT_G = 1.2
STEER_BASELINE_ALPHA = 0.35
HIGH_LAT_SECTOR_START = 0.33
HIGH_LAT_SECTOR_END = 0.55

# Unit conversion (packet US gal display)
_L_TO_US_GAL = 0.2641720523581484

# --- Default track pit loss matrix (Section 9) ---

PIT_LOSS_SUPER_SEC = 58.0
PIT_LOSS_SHORT_SEC = 42.0
PIT_LOSS_INTERMEDIATE_SEC = 46.0
PIT_LOSS_SUPER_MIN_LENGTH_MI = 2.3
PIT_LOSS_SHORT_MAX_LENGTH_MI = 1.2

_PIT_LOSS_UNKNOWN_LENGTH_MI = 1.5

# Long-race offline simulation distances (sim/race_simulator.py scenarios)
LONG_RACE_LAPS_SHORT_TRACK = 120
LONG_RACE_LAPS_MEDIUM_TRACK = 90
LONG_RACE_LAPS_LARGE_TRACK = 60

# Representative track lengths (mi) for long-race sim pit-loss defaults
LONG_RACE_SHORT_TRACK_LENGTH_MI = 0.533
LONG_RACE_MEDIUM_TRACK_LENGTH_MI = 1.5
LONG_RACE_LARGE_TRACK_LENGTH_MI = 2.5

# End-of-race / caution track-position guards (strategy_engine.py)
WHITE_FLAG_LAPS_REMAINING = 1
CAUTION_STAY_OUT_TOP_POSITION = 10
CAUTION_STAY_OUT_MIN_SPOTS_LOST = 1
CAUTION_GREEN_RUN_BUFFER_LAPS = 5
CAUTION_HERD_PIT_RATIO_MIN = 0.6
CAUTION_PODIUM_MAX_POSITION = 3
CAUTION_PODIUM_FUEL_BUFFER_LAPS = 3
CAUTION_TOP10_FUEL_ABUNDANCE_LAPS = 10
CAUTION_TOP10_MAX_SPOTS_LOST = 1

# Leader track-position premium (strategy_engine.py)
LEADER_STRETCH_MAX_POSITION = 3
LEADER_STRETCH_MIN_LAPS_REMAIN = 8
LEADER_STRETCH_FUEL_FLOOR = 1.5
LEADER_STRETCH_FUEL_FLOOR_NO_HERD = 2.0
LEADER_STRETCH_HERD_PIT_BELOW = 0.4
LEADER_FORCED_PIT_GAP_BEHIND = 0.8
LEADER_UNDERCUT_SELF_MIN_POSITION = 3
LEADER_UNDERCUT_SELF_MAX_POSITION = 5
LEADER_UNDERCUT_SELF_FUEL_MAX = 4.0
LEADER_UNDERCUT_SELF_MIN_LAPS_REMAIN = 10

# Offline sim field realism (sim/race_simulator.py)
SIM_OPPONENT_PIT_FUEL_THRESHOLD = 2.0
SIM_STINT_PACE_PENALTY_PER_LAP = 0.012
SIM_FRESH_TIRE_BONUS_S = 0.30
SIM_PASS_BLOCKED_HERO_STINT = 28
SIM_PASS_FROM_BEHIND_STREAK = 2


# ==============================================================================
# Section 14: Telemetry boundary clamps (zero / negative payload shields)
# ==============================================================================


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp_fuel_use_kg_h(value: Any) -> float:
    """Shield fuel burn nodes from engine-off or negative kg/h telemetry."""
    return max(_as_float(value, 0.0), MIN_FUEL_USE_KG_PER_H)


def clamp_nonneg_liters(value: Any, *, default: float = 0.0) -> float:
    """Fuel level and tank deltas must not propagate negative SDK payloads."""
    return max(_as_float(value, default), 0.0)


def clamp_avg_lap_seconds(
    value: Any,
    *,
    default: float = DEFAULT_AVG_LAP_S,
    minimum: float = MIN_AVG_LAP_S,
) -> float:
    """Lap-time inputs used in L/h → L/lap conversion."""
    return max(_as_float(value, default), minimum)


def clamp_lap_distance_pct(value: Any) -> float | None:
    """Valid CarIdxLapDistPct is [0, 1]; reject out-of-range telemetry."""
    v = _as_float(value, -1.0)
    if v < 0.0 or v > 1.0:
        return None
    return v


def clamp_positive_rate(value: Any, *, minimum: float, default: float) -> float:
    """Wear falloff and similar per-lap rates must stay strictly positive."""
    v = _as_float(value, default)
    return max(v, minimum)


# ==============================================================================
# Track pit-loss defaults
# ==============================================================================


def get_default_pit_loss_seconds(track_name: str, track_length_miles: float) -> float:
    """
    Section 9 track type heuristic for pit_loss_sec fallback.

    When length is unavailable from telemetry, pass ``_PIT_LOSS_UNKNOWN_LENGTH_MI`` (1.5 mi)
    so only name-based super speedway detection applies and intermediate is the default.
    """
    clean_name = track_name.lower()

    if (
        "daytona" in clean_name
        or "talladega" in clean_name
        or track_length_miles >= PIT_LOSS_SUPER_MIN_LENGTH_MI
    ):
        return PIT_LOSS_SUPER_SEC

    if track_length_miles <= PIT_LOSS_SHORT_MAX_LENGTH_MI:
        return PIT_LOSS_SHORT_SEC

    return PIT_LOSS_INTERMEDIATE_SEC


def resolve_track_length_miles(track_length_miles: float | None) -> float:
    """Coerce SDK track length for ``get_default_pit_loss_seconds``."""
    if isinstance(track_length_miles, (int, float)):
        return float(track_length_miles)
    return _PIT_LOSS_UNKNOWN_LENGTH_MI


# ==============================================================================
# Section 3.5 / 12.3: Non-linear triangular tire wear
# ==============================================================================


def cumulative_triangular_wear_cost(lap: int, falloff_s_per_lap: float) -> float:
    """Sum of lap * falloff for laps 1..lap (Section 12.3 triangular wear model)."""
    falloff_s_per_lap = clamp_positive_rate(falloff_s_per_lap, minimum=0.01, default=0.01)
    if lap < 1:
        return 0.0
    return sum(i * falloff_s_per_lap for i in range(1, lap + 1))


def triangular_payback_lap(
    pit_loss_sec: float,
    falloff_s_per_lap: float,
    *,
    max_lap: int = 500,
) -> int | None:
    """
    Smallest lap L where cumulative triangular wear cost >= pit_loss_sec (Section 3.5).
    """
    pit_loss_sec = clamp_nonneg_liters(pit_loss_sec)
    falloff_s_per_lap = clamp_positive_rate(falloff_s_per_lap, minimum=0.01, default=0.01)
    if pit_loss_sec <= 0:
        return None
    cumulative = 0.0
    for lap in range(1, max_lap + 1):
        cumulative += lap * falloff_s_per_lap
        if cumulative >= pit_loss_sec:
            return lap
    return None


def first_lap_triangular_cost_exceeds(
    cost_threshold: float,
    falloff_s_per_lap: float,
    *,
    max_lap: int = 500,
) -> int | None:
    """First lap where cumulative triangular cost strictly exceeds threshold (pre-race stint cap)."""
    cost_threshold = clamp_nonneg_liters(cost_threshold)
    falloff_s_per_lap = clamp_positive_rate(falloff_s_per_lap, minimum=0.01, default=0.01)
    if cost_threshold <= 0:
        return None
    cumulative = 0.0
    for lap in range(1, max_lap + 1):
        cumulative += lap * falloff_s_per_lap
        if cumulative > cost_threshold:
            return lap
    return None


# ==============================================================================
# Section 5: Spatial wrap-around boundary filter
# ==============================================================================


def wrap_lap_distance_delta(dist_a: float, dist_b: float) -> float:
    """Normalized lap-distance delta with start/finish wrap protection (Section 5)."""
    da = clamp_lap_distance_pct(dist_a)
    db = clamp_lap_distance_pct(dist_b)
    if da is None or db is None:
        return 0.0
    delta = db - da
    if delta > LAP_DIST_WRAP_HALF:
        delta -= 1.0
    elif delta < -LAP_DIST_WRAP_HALF:
        delta += 1.0
    return delta


# ==============================================================================
# Section 2.3: Combined fuel burn (caution bypass + green EMA)
# ==============================================================================


def resolve_combined_burn_rate(
    fuel_use_kg_h: float,
    avg_lap_s: float,
    fuel_per_lap_ema: float | None,
    *,
    is_caution: bool = False,
) -> float:
    """
    Section 2.3: liters/lap for live fuel state.

    Under caution, bypass EMA and use instantaneous burn (fuel-saving pace).
    Under green, use max(instantaneous, EMA). Floor at DEFAULT_CAUTION_BURN_L.
    """
    fuel_use_kg_h = clamp_fuel_use_kg_h(fuel_use_kg_h)
    avg_lap_s = clamp_avg_lap_seconds(avg_lap_s)
    fuel_L_per_h = fuel_use_kg_h / GASOLINE_KG_PER_L
    fuel_L_per_lap_inst = fuel_L_per_h * (avg_lap_s / 3600.0)

    if fuel_per_lap_ema is not None:
        fuel_per_lap_ema = clamp_nonneg_liters(fuel_per_lap_ema)

    if fuel_per_lap_ema is None:
        return max(fuel_L_per_lap_inst, DEFAULT_CAUTION_BURN_L)
    if is_caution:
        return max(fuel_L_per_lap_inst, DEFAULT_CAUTION_BURN_L)
    return max(fuel_L_per_lap_inst, float(fuel_per_lap_ema))


def liters_per_lap_from_us_gal(us_gal_per_lap: float) -> float:
    """Convert packet fuel-per-lap (US gal) back to liters."""
    return clamp_nonneg_liters(us_gal_per_lap) / _L_TO_US_GAL


def green_flag_fuel_laps_remaining(
    fuel_liters: float,
    fuel_per_lap_ema_L: float | None,
    *,
    fallback_l_per_lap: float,
) -> float:
    """
    Section 10.3: green-flag laps remaining from tank liters and saved EMA only.

    Avoids caution-inflated live burn when seeding the rest-of-race forecast.
    """
    fuel_liters = clamp_nonneg_liters(fuel_liters)
    if fuel_per_lap_ema_L is not None and fuel_per_lap_ema_L > 0:
        burn_L = clamp_nonneg_liters(fuel_per_lap_ema_L)
    else:
        burn_L = max(clamp_nonneg_liters(fallback_l_per_lap), DEFAULT_CAUTION_BURN_L)
    return fuel_liters / max(burn_L, DEFAULT_CAUTION_BURN_L)


def green_flag_fuel_laps_from_telemetry(
    telemetry: dict,
    *,
    fallback_l_per_lap: float,
) -> float:
    """Resolve Section 10.3 seed laps from a compact telemetry packet."""
    m = telemetry.get("m") if isinstance(telemetry.get("m"), dict) else {}
    fuel_L = clamp_nonneg_liters(m.get("ful"))

    ema_L: float | None = None
    fpe = m.get("fpe")
    if fpe is not None:
        try:
            ema_L = liters_per_lap_from_us_gal(float(fpe))
        except (TypeError, ValueError):
            ema_L = None

    if ema_L is None:
        fpl = m.get("fpl")
        if fpl is not None:
            try:
                fallback_l_per_lap = liters_per_lap_from_us_gal(float(fpl))
            except (TypeError, ValueError):
                pass

    return green_flag_fuel_laps_remaining(
        fuel_L,
        ema_L,
        fallback_l_per_lap=clamp_nonneg_liters(fallback_l_per_lap, default=DEFAULT_CAUTION_BURN_L),
    )
