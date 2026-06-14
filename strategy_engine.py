"""
Race Engineer Agent - Strategy Engine
================================================================
Deterministic decision algorithm and telemetry schema reference for the
local race strategy engine (no cloud AI).
"""

from __future__ import annotations

from typing import Any

from race_constants import (
    ALERT_LAP_HORIZON,
    DEFAULT_CAUTION_BURN_L,
    clamp_nonneg_liters,
    green_flag_fuel_laps_from_telemetry,
    triangular_payback_lap,
)
from pre_race_strategy import run_pre_race_plan

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
        "pc": "Current delta pace",
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
        "rej": "Projected pit reentry condition string (CLEAN | TRAFFIC | PACK)",
        "cpi": "Caution pit analytics {tl, ll, lda, xp, gr, ...}",
    },
    "twl": "Pit box tire wear log array",
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


def _fuel_context(telemetry: dict[str, Any]) -> tuple[float, bool, bool]:
    """Return (fuel_laps_left, inside_window, is_fuel_critical)."""
    x = telemetry.get("x", {})
    m = telemetry.get("m", {})
    if x.get("fe", 0) == 1 and m.get("fcq") in ("hi", "med"):
        fuel_laps_left = _safe_float(m.get("fl"), 0.0)
        pb_laps = _resolve_pit_payback_laps(telemetry)
        inside_window = bool(m.get("pw", False)) or (fuel_laps_left <= pb_laps)
        is_fuel_critical = fuel_laps_left <= 1.0
    else:
        fpl = _safe_float(m.get("fpl"), 0.2)
        ful = _safe_float(m.get("ful"), 0.0)
        fuel_laps_left = ful / fpl if fpl > 0 else 3.0
        inside_window = fuel_laps_left <= 2.0
        is_fuel_critical = fuel_laps_left <= 1.0
    return fuel_laps_left, inside_window, is_fuel_critical


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
    flags = s.get("flb", {}) if isinstance(s.get("flb"), dict) else {}
    if not (flags.get("yel") or flags.get("cau")):
        return None

    if is_fuel_critical:
        return {
            "ACTION": "PIT NOW",
            "SERVICE": "4 TIRES" if _safe_int(r.get("ts"), 0) > 0 else "FUEL ONLY",
            "WHY": "Fuel range critical under caution; servicing full tank and fresh tires.",
        }

    cpi = fi.get("cpi") if isinstance(fi.get("cpi"), dict) else {}
    spots_lost = _safe_int(cpi.get("ll"), 99)
    herd = fi.get("hd") if isinstance(fi.get("hd"), dict) else {}
    herd_pit_ratio = _safe_float(herd.get("pra"), 0.0)

    if spots_lost <= 3 or herd_pit_ratio > 0.6:
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
    elif is_fuel_critical:
        immediate_action = "PIT NOW"
        immediate_service = "4 TIRES" if tire_sets > 0 else "FUEL ONLY"
        why_reason = "Fuel critical; absolute limit of current tank reached."
    elif inside_window and rej.get("v", "CLEAN") == "CLEAN":
        immediate_action = "PIT NOW"
        immediate_service = "4 TIRES" if tire_sets > 0 else "FUEL ONLY"
        why_reason = "Pit window open; clean reentry air window confirmed."
    elif inside_window and rej.get("v") == "PACK":
        immediate_action = "STAY OUT"
        immediate_service = "NONE"
        why_reason = "Pit window open but reentry projects heavy pack traffic; delay one lap."
    else:
        immediate_action = "STAY OUT"
        immediate_service = "NONE"
        why_reason = "Maintaining current green flag stint pacing."

    # --- PART 2: REST-OF-RACE GREEN FORECAST (skip under active caution) ---
    future_stops: list[dict[str, Any]] = []
    is_caution = bool(flb.get("yel") or flb.get("cau"))

    if not is_caution and laps_remain > 0:
        if immediate_action in ("PIT", "PIT NOW"):
            sim_current_lap = current_lap + 1
            sim_laps_remaining = laps_remain - 1
            sim_fuel_laps_remaining = max_tank_stint
        else:
            sim_current_lap = current_lap + 1
            sim_laps_remaining = laps_remain - 1
            # Section 10.3: seed with green-flag EMA laps, not caution-inflated live fl.
            green_fuel_laps = green_flag_fuel_laps_from_telemetry(
                telemetry,
                fallback_l_per_lap=DEFAULT_CAUTION_BURN_L,
            )
            sim_fuel_laps_remaining = max(0.0, green_fuel_laps - 1.0)

        stop_counter = 1
        while sim_laps_remaining > 0:
            if sim_fuel_laps_remaining <= 1.0:
                tires_available = tire_sets - stop_counter
                service_call = "4 TIRES" if tires_available > 0 else "FUEL ONLY"
                future_stops.append(
                    {
                        "forecast_stop_number": stop_counter,
                        "estimated_pit_lap": sim_current_lap,
                        "laps_from_now": sim_current_lap - current_lap,
                        "service_required": service_call,
                        "context": "Tank limits reached under projected green flag run.",
                    }
                )
                sim_fuel_laps_remaining = max_tank_stint
                stop_counter += 1

            sim_current_lap += 1
            sim_laps_remaining -= 1
            sim_fuel_laps_remaining -= 1.0

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


def format_live_strategy(result: dict[str, Any], telemetry: dict[str, Any]) -> str:
    """Format immediate call plus green-flag rest-of-race forecast for the UI."""
    imm = result.get("immediate_directive", {})
    action = str(imm.get("ACTION", "STAY OUT"))
    service = str(imm.get("SERVICE", "NONE"))
    why = str(imm.get("WHY", ""))
    timing = "THIS LAP"

    flags = telemetry.get("s", {}).get("flb", {})
    is_caution = isinstance(flags, dict) and (flags.get("yel") or flags.get("cau"))
    fuel_laps_left, _, is_fuel_critical = _fuel_context(telemetry)
    trigger, conf = _derive_trigger_conf(
        action,
        service,
        why,
        is_caution=bool(is_caution),
        is_fuel_critical=is_fuel_critical,
    )

    forecast = result.get("green_flag_rest_of_race_forecast", {})
    if forecast.get("paused_for_caution"):
        forecast_line = "FORECAST: Paused under caution — re-run after green"
    else:
        stops = forecast.get("projected_pit_schedule", [])
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

    return (
        f"{action} — {timing} — {service}\n"
        f"WHY: {why}\n"
        f"{forecast_line}\n"
        f"TRIGGER: {trigger}  CONF: {conf}"
    )


def format_engineer_advice(
    action: str,
    timing: str,
    service: str,
    why: str,
    *,
    trigger: str | None = None,
    conf: str | None = None,
) -> str:
    """Format the 3-line engineer call expected by the UI and voice relay."""
    trig = trigger or "FUEL"
    level = conf or "M"
    line1 = f"{action} — {timing} — {service}"
    line2 = f"WHY: {why}"
    line3 = f"TRIGGER: {trig}  CONF: {level}"
    return f"{line1}\n{line2}\n{line3}"


def _first_call_line(advice: str) -> str:
    for line in (advice or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s:
            return s
    return ""


def parse_call_line(advice: str) -> tuple[str, str, str]:
    """Parse line 1 into ACTION, TIMING, SERVICE."""
    head = _first_call_line(advice)
    parts = [p.strip() for p in head.split("—")]
    action = parts[0].upper() if parts else ""
    timing = parts[1].upper() if len(parts) > 1 else ""
    service = parts[2].upper() if len(parts) > 2 else ""
    return action, timing, service


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


def should_auto_alert(
    telemetry: dict[str, Any],
    advice: str,
    *,
    max_laps: int = ALERT_LAP_HORIZON,
    forecast_result: dict[str, Any] | None = None,
) -> bool:
    """True when a pit stop is due within max_laps (or fuel window is inside that range)."""
    action, timing, _ = parse_call_line(advice)
    n = laps_until_pit(action, timing)
    if n is not None and n <= max_laps:
        return True

    fuel_laps = _fuel_laps_left(telemetry)
    if fuel_laps is not None and fuel_laps <= max_laps:
        m = telemetry.get("m", {})
        if action in ("PIT", "PIT NOW"):
            return True
        if m.get("pw"):
            return True
        pb = _resolve_pit_payback_laps(telemetry)
        if pb > 0 and fuel_laps <= pb + 1:
            return True

        flags = telemetry.get("s", {}).get("flb", {})
        if isinstance(flags, dict) and (flags.get("yel") or flags.get("cau")):
            if fuel_laps <= min(3.0, max_laps):
                return action in ("PIT", "PIT NOW") or m.get("pw", False)

    result = forecast_result if isinstance(forecast_result, dict) else evaluate_and_forecast_strategy(telemetry)
    forecast = result.get("green_flag_rest_of_race_forecast", {})
    if not forecast.get("paused_for_caution"):
        for stop in forecast.get("projected_pit_schedule", []) or []:
            if isinstance(stop, dict) and _safe_int(stop.get("laps_from_now"), 99) <= max_laps:
                return True
    return False


from pre_race_strategy import run_pre_race_plan


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
