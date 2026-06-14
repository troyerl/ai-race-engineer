"""
Race Engineer Agent - Pre-Race Strategy Engine (Green Flag Focus)
===================================================================
Extracts session parameters and calculates an optimal green-flag tire,
fuel, and pit stop strategy layout.
"""

from __future__ import annotations

import re
from typing import Any

from race_constants import IRSDK_TIRE_SETS_UNLIMITED, TIRE_COST_THRESHOLD_BUMP, first_lap_triangular_cost_exceeds

_IRACING_LAPS_UNKNOWN_MIN = 32000

PRE_RACE_SCHEMA: dict[str, Any] = {
    "session_info": {
        "WeekendInfo": {
            "TrackName": "Track short name identifier",
            "TrackLengthOfficial": "Official length string",
            "TrackLength": "Raw float length conversion parameter",
        },
        "SessionInfo": {
            "Sessions": [
                {
                    "SessionType": "Race",
                    "SessionTime": "Total scheduled time limit string",
                    "SessionLaps": "Total scheduled lap limit (int or unlimited)",
                }
            ]
        },
    },
    "historical_driver_baseline": {
        "avg_lap_time_s": "Estimated driver race pace average in seconds",
        "fuel_burn_per_lap_gal": "Historical fuel burn rate per lap",
        "tire_wear_per_lap_pct": "Estimated percentage drop per lap on heaviest tire",
        "tire_wear_pace_falloff_per_lap_s": "Pace loss in seconds per lap from tire wear",
        "pit_lane_loss_time_s": "Total pit road time loss in seconds",
        "fuel_tank_capacity_gal": "Fuel tank capacity in US gallons",
    },
    "rules": {
        "fuel_tank_capacity_pct": "Max fuel cell capacity multiplier (session rules)",
        "max_tire_sets": "Maximum tire sets allowed for the event",
    },
}


def _sanitize_lap_count(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, str):
        raw = v.strip().lower()
        if raw in ("", "unlimited", "none", "n/a"):
            return None
        if raw.isdigit():
            v = int(raw)
        else:
            m = re.search(r"\d+", raw)
            if not m:
                return None
            v = int(m.group(0))
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    if i < 0 or i >= _IRACING_LAPS_UNKNOWN_MIN:
        return None
    return i


def _parse_tire_set_count(v: Any) -> int | None:
    try:
        if v is None:
            return None
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n < 0 or n >= IRSDK_TIRE_SETS_UNLIMITED:
        return None
    return n


def _is_race_session_entry(session: dict[str, Any]) -> bool:
    """Match iRacing race sessions (SessionName is often uppercase RACE)."""
    st = str(session.get("SessionType") or "").strip().lower()
    sn = str(session.get("SessionName") or "").strip().upper()
    if st == "race":
        return True
    if sn in ("RACE", "MAIN RACE", "FEATURE RACE", "HEAT RACE", "CONSOLATION RACE"):
        return True
    if "RACE" in sn and "PRACTICE" not in sn and "QUAL" not in sn:
        return True
    return False


def find_race_session(session_yaml: dict[str, Any]) -> dict[str, Any]:
    """Return the main race session from SessionInfo, even when currently in P/Q."""
    sessions_root = session_yaml.get("SessionInfo", {})
    if not isinstance(sessions_root, dict):
        return {}
    entries = sessions_root.get("Sessions", []) or []
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict) and _is_race_session_entry(entry):
            candidates.append(entry)
    if not candidates:
        return {}
    for entry in reversed(candidates):
        if not bool(entry.get("SessionSkipped")):
            return entry
    return candidates[-1]


def race_lap_total_from_session(race_session: dict[str, Any]) -> int | None:
    if not race_session:
        return None
    return _sanitize_lap_count(race_session.get("SessionLaps"))


def race_lap_total_from_yaml(session_yaml: dict[str, Any]) -> int | None:
    return race_lap_total_from_session(find_race_session(session_yaml))


def _is_race_current_session(session_type: str | None) -> bool:
    ty = str(session_type or "").strip().lower()
    return ty == "race" or ty.endswith(" race")


def race_tire_set_limit(session_yaml: dict[str, Any], telemetry: dict[str, Any]) -> int:
    """Race-weekend tire allocation (not practice-session remaining sets)."""
    r = telemetry.get("r") if isinstance(telemetry.get("r"), dict) else {}
    s = telemetry.get("s") if isinstance(telemetry.get("s"), dict) else {}

    for key in ("tsl", "ts_limit"):
        limit = _parse_tire_set_count(r.get(key))
        if limit is not None:
            return max(1, limit)

    weekend = session_yaml.get("WeekendInfo")
    if isinstance(weekend, dict):
        opts = weekend.get("WeekendOptions")
        if isinstance(opts, dict):
            for key in ("TireSets", "MaxTireSets", "NumTireSets"):
                limit = _parse_tire_set_count(opts.get(key))
                if limit is not None:
                    return max(1, limit)

    race_session = find_race_session(session_yaml)
    for key in ("TireSets", "MaxTireSets", "TireSetCount"):
        limit = _parse_tire_set_count(race_session.get(key))
        if limit is not None:
            return max(1, limit)

    if _is_race_current_session(s.get("ty")):
        remaining = _parse_tire_set_count(r.get("ts"))
        if remaining is not None:
            return max(1, remaining)

    try:
        return max(1, int(r.get("ts", 2) or 2))
    except (TypeError, ValueError):
        return 2


def _parse_session_time_seconds(time_str: Any) -> float:
    raw = str(time_str or "0").strip().lower()
    try:
        if "sec" in raw:
            return max(60.0, float(raw.split()[0]))
        if "min" in raw:
            return max(60.0, float(raw.split()[0]) * 60.0)
        if "hour" in raw:
            return max(60.0, float(raw.split()[0]) * 3600.0)
        if raw.replace(".", "", 1).isdigit():
            return max(60.0, float(raw))
    except (TypeError, ValueError):
        pass
    return 3600.0


def generate_pre_race_green_plan(
    session_yaml: dict[str, Any],
    baseline: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any]:
    """
    Calculates a definitive pit stop strategy assuming standard uninterrupted green flag conditions.
    """
    race_session = find_race_session(session_yaml)

    total_laps = race_lap_total_from_session(race_session)
    avg_lap = max(30.0, float(baseline.get("avg_lap_time_s", 90.0)))
    if total_laps is None and race_session:
        time_str = race_session.get("SessionTime", "0")
        if str(time_str or "").strip().lower() not in ("", "0", "unlimited"):
            total_seconds = _parse_session_time_seconds(time_str)
            total_laps = max(1, int(total_seconds / avg_lap))

    if total_laps is None:
        total_laps = max(1, int(3600.0 / avg_lap))

    fuel_burn = max(0.01, float(baseline.get("fuel_burn_per_lap_gal", 0.12)))
    max_capacity = float(baseline.get("fuel_tank_capacity_gal", 20.0)) * (
        float(rules.get("fuel_tank_capacity_pct", 100.0)) / 100.0
    )
    fuel_stint_cap = max(1, int(max_capacity / fuel_burn) - 1)

    pit_loss = float(baseline.get("pit_lane_loss_time_s", 45.0))
    tire_falloff = max(0.01, float(baseline.get("tire_wear_pace_falloff_per_lap_s", 0.08)))
    tire_stint_cap = fuel_stint_cap
    exceed_lap = first_lap_triangular_cost_exceeds(
        pit_loss + TIRE_COST_THRESHOLD_BUMP,
        tire_falloff,
        max_lap=fuel_stint_cap,
    )
    if exceed_lap is not None:
        tire_stint_cap = exceed_lap

    stint_length = max(1, min(fuel_stint_cap, tire_stint_cap))
    total_stops = max(0, int(total_laps / stint_length))
    if total_laps % stint_length == 0 and total_stops > 0:
        total_stops -= 1

    max_tire_sets = int(rules.get("max_tire_sets", 1) or 1)
    pit_schedule: list[dict[str, Any]] = []
    current_lap = stint_length
    for i in range(total_stops):
        need_fuel = True
        need_tires = (stint_length == tire_stint_cap) or (max_tire_sets > total_stops)
        if need_fuel and need_tires:
            service_type = "BOTH"
            reason_bits = ["Fuel", "Tires"]
        elif need_fuel:
            service_type = "FUEL ONLY"
            reason_bits = ["Fuel"]
        else:
            service_type = "4 TIRES"
            reason_bits = ["Tires"]
        pit_schedule.append(
            {
                "stop_number": i + 1,
                "pit_on_lap": current_lap,
                "service": service_type,
                "reason": f"Stint limit reached via {' & '.join(reason_bits)} limits.",
            }
        )
        current_lap += stint_length

    weekend = session_yaml.get("WeekendInfo", {})
    track_name = "Unknown"
    if isinstance(weekend, dict):
        track_name = str(weekend.get("TrackDisplayName") or weekend.get("TrackName") or "Unknown")

    return {
        "track_name": track_name,
        "calculated_total_race_laps": int(total_laps),
        "max_laps_per_fuel_tank": int(fuel_stint_cap),
        "max_laps_per_tire_set": int(tire_stint_cap),
        "recommended_stint_length": int(stint_length),
        "total_stops_required": int(total_stops),
        "scheduled_pit_stops": pit_schedule,
    }


def baseline_from_telemetry(telemetry: dict[str, Any]) -> dict[str, Any]:
    """Build driver baseline estimates from a compact telemetry packet."""
    m = telemetry.get("m") if isinstance(telemetry.get("m"), dict) else {}
    r = telemetry.get("r") if isinstance(telemetry.get("r"), dict) else {}

    times = m.get("t") if isinstance(m.get("t"), list) else []
    valid_times = [float(t) for t in times if isinstance(t, (int, float)) and float(t) > 0]
    if valid_times:
        avg_lap = sum(valid_times) / len(valid_times)
    elif isinstance(m.get("bl"), (int, float)) and float(m["bl"]) > 0:
        avg_lap = float(m["bl"])
    else:
        pc = m.get("pc") if isinstance(m.get("pc"), dict) else {}
        avg_lap = float(pc.get("avg_last5_s") or pc.get("avg_last3_s") or 90.0)

    fpl = m.get("fpe") or m.get("fpl")
    if isinstance(fpl, (int, float)) and float(fpl) > 0:
        fuel_burn = float(fpl)
    elif isinstance(r.get("ftl"), (int, float)) and float(r["ftl"]) > 0 and isinstance(r.get("fc"), (int, float)):
        fuel_burn = float(r["fc"]) / float(r["ftl"])
    else:
        fuel_burn = 0.12

    fo = m.get("fo")
    tire_falloff = float(fo) if isinstance(fo, (int, float)) and float(fo) > 0 else 0.08

    twr = m.get("twr")
    tire_pct = 1.5
    if isinstance(twr, dict):
        vals = [float(v) for v in twr.values() if isinstance(v, (int, float)) and float(v) > 0]
        if vals:
            peak = max(vals)
            tire_pct = peak * 100.0 if peak < 1.0 else peak

    pit_loss = float(r.get("pl", 45) or 45)
    fc = float(r.get("fc", 20.0) or 20.0)

    return {
        "avg_lap_time_s": max(30.0, avg_lap),
        "fuel_burn_per_lap_gal": max(0.01, fuel_burn),
        "tire_wear_per_lap_pct": max(0.1, tire_pct),
        "tire_wear_pace_falloff_per_lap_s": max(0.01, tire_falloff),
        "pit_lane_loss_time_s": max(10.0, pit_loss),
        "fuel_tank_capacity_gal": max(1.0, fc),
    }


def rules_from_telemetry(telemetry: dict[str, Any]) -> dict[str, Any]:
    sy = telemetry.get("sy") if isinstance(telemetry.get("sy"), dict) else {}
    session_yaml = sy if sy.get("SessionInfo") else {}
    return {
        "fuel_tank_capacity_pct": 100.0,
        "max_tire_sets": race_tire_set_limit(session_yaml, telemetry),
    }


def session_yaml_from_telemetry(telemetry: dict[str, Any], *, track_name: str | None = None) -> dict[str, Any]:
    """Use embedded SessionInfo YAML or synthesize race session from packet hints."""
    sy = telemetry.get("sy")
    if isinstance(sy, dict) and sy.get("SessionInfo"):
        return sy

    s = telemetry.get("s") if isinstance(telemetry.get("s"), dict) else {}
    wi: dict[str, Any] = {}
    if track_name:
        wi["TrackName"] = track_name

    race_sess: dict[str, Any] = {"SessionType": "Race", "SessionName": "RACE"}
    lt = _sanitize_lap_count(s.get("race_lt"))
    if lt is None and _is_race_current_session(s.get("ty")):
        lt = _sanitize_lap_count(s.get("lt"))
    if lt is not None:
        race_sess["SessionLaps"] = lt
    tr = s.get("race_tr") or s.get("tr")
    if isinstance(tr, (int, float)) and float(tr) > 0:
        race_sess["SessionTime"] = f"{int(tr)} sec"

    return {
        "WeekendInfo": wi,
        "SessionInfo": {"Sessions": [race_sess]},
    }


def format_pre_race_plan(plan: dict[str, Any]) -> str:
    """Five-line pre-race layout for the engineer overlay and voice relay."""
    stops = plan.get("scheduled_pit_stops") if isinstance(plan.get("scheduled_pit_stops"), list) else []
    if stops:
        stop_bits = [f"L{stop['pit_on_lap']} {stop['service']}" for stop in stops if isinstance(stop, dict)]
        stops_line = "; ".join(stop_bits)
    else:
        stops_line = "None projected — one-stop or no-stop race"

    stint = plan.get("recommended_stint_length", "?")
    total = plan.get("calculated_total_race_laps", "?")
    track = plan.get("track_name", "Unknown")

    return (
        f"FUEL: ~{plan.get('max_laps_per_fuel_tank', '?')} laps/tank · {stint}-lap green-flag stints\n"
        f"TIRES: ~{plan.get('max_laps_per_tire_set', '?')} laps/set · {plan.get('total_stops_required', 0)} planned stop(s)\n"
        f"STOPS: {stops_line}\n"
        f"NOTE: ~{total} race laps at {track}; plan assumes green-flag run — adjust for cautions\n"
        f"TRIGGER: FUEL|TIRES  CONF: M"
    )


def run_pre_race_plan(telemetry: dict[str, Any], *, track_name: str | None = None) -> str:
    """Evaluate telemetry + session YAML and return formatted pre-race strategy text."""
    session_yaml = session_yaml_from_telemetry(telemetry, track_name=track_name)
    baseline = baseline_from_telemetry(telemetry)
    rules = rules_from_telemetry(telemetry)
    plan = generate_pre_race_green_plan(session_yaml, baseline, rules)
    return format_pre_race_plan(plan)
