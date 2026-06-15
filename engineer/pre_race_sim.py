"""
Pre-race strategy via offline simulation.

Reads the current iRacing session packet (track, temps, grid, pace, fuel) and runs
three caution-density branches through the real strategy engine + RaceSimulator:

  LIGHT    — 0–3 cautions  (sim uses 2)
  MODERATE — 4–7 cautions  (sim uses 5)
  HEAVY    — 8–11 cautions (sim uses 9)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sim.race_simulator import (
    CautionWindow,
    RaceScenario,
    RaceSimulator,
    clamp_field_size,
)

from .race_constants import get_default_pit_loss_seconds

from .pre_race_strategy import (
    baseline_from_telemetry,
    find_race_session,
    generate_pre_race_green_plan,
    race_lap_total_from_yaml,
    rules_from_telemetry,
    session_yaml_from_telemetry,
)

# (label, representative caution count for the bucket)
CAUTION_BUCKETS: tuple[tuple[str, int], ...] = (
    ("LIGHT (0–3 cautions)", 2),
    ("MODERATE (4–7 cautions)", 5),
    ("HEAVY (8–11 cautions)", 9),
)


@dataclass
class PreRaceSimContext:
    """Session parameters extracted from a live/strategy telemetry packet."""

    track_name: str
    total_laps: int
    hero_position: int
    field_size: int
    fuel_tank_laps: float
    pit_loss_sec: float
    tire_sets: int
    hero_base_pace_s: float
    hero_tire_wear_per_lap: float
    track_length_miles: float | None
    track_temp_c: float | None
    hero_lap_times: list[float]
    avg_lap_time_s: float
    field_pace_by_position: dict[int, float]
    quali_pace_source: str


@dataclass
class PreRaceBranchResult:
    label: str
    caution_count: int
    caution_laps: list[int]
    pit_stops: list[int]
    finish_position: int
    start_position: int
    green_stops: int


def _parse_track_length_miles(sy: dict[str, Any]) -> float | None:
    wi = sy.get("WeekendInfo")
    if not isinstance(wi, dict):
        return None
    for key in ("TrackLength", "TrackLengthOfficial"):
        raw = wi.get(key)
        if raw is None:
            continue
        try:
            if isinstance(raw, str):
                s = raw.strip().lower()
                if "mi" in s:
                    return float(s.replace("mi", "").strip())
                if "km" in s:
                    return float(s.replace("km", "").strip()) * 0.621371
                return float(s) * 0.621371 if float(s) <= 10 else float(s)
            n = float(raw)
            return n * 0.621371 if n <= 10 else n
        except (TypeError, ValueError):
            continue
    return None


def _drivers_from_session(sy: dict[str, Any]) -> list[dict[str, Any]]:
    di = sy.get("DriverInfo")
    if not isinstance(di, dict):
        return []
    drivers = di.get("Drivers")
    return [d for d in drivers if isinstance(d, dict)] if isinstance(drivers, list) else []


def _player_car_idx(sy: dict[str, Any]) -> int | None:
    for d in _drivers_from_session(sy):
        if d.get("CarIsPlayer"):
            try:
                return int(d["CarIdx"])
            except (TypeError, ValueError):
                continue
    return None


def _qualifying_grid_position(sy: dict[str, Any], car_idx: int) -> int | None:
    sessions = sy.get("SessionInfo", {})
    if not isinstance(sessions, dict):
        return None
    entries = sessions.get("Sessions")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        st = str(entry.get("SessionType") or "").strip().lower()
        if st not in ("qualify", "open", "practice"):
            continue
        results = entry.get("ResultsPositions") or entry.get("Results")
        if not isinstance(results, list):
            continue
        for row in results:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("CarIdx", -1)) == car_idx:
                    pos = int(row.get("Position", 0))
                    return pos if pos > 0 else None
            except (TypeError, ValueError):
                continue
    return None


def field_size_from_telemetry(telemetry: dict[str, Any]) -> int:
    sy = telemetry.get("sy")
    if isinstance(sy, dict):
        drivers = _drivers_from_session(sy)
        if len(drivers) >= 2:
            return clamp_field_size(len(drivers))
        wi = sy.get("WeekendInfo")
        if isinstance(wi, dict):
            for key in ("NumStarters", "MaxDrivers", "EntryCount"):
                try:
                    n = int(wi[key])
                    if n >= 2:
                        return clamp_field_size(n)
                except (TypeError, ValueError, KeyError):
                    continue
    return clamp_field_size(32)


def grid_position_from_telemetry(telemetry: dict[str, Any], *, field_size: int) -> int:
    m = telemetry.get("m") if isinstance(telemetry.get("m"), dict) else {}
    try:
        p = int(m.get("p", 0))
        if p > 0:
            return min(p, field_size)
    except (TypeError, ValueError):
        pass

    sy = telemetry.get("sy")
    if isinstance(sy, dict):
        car_idx = _player_car_idx(sy)
        if car_idx is not None:
            quali = _qualifying_grid_position(sy, car_idx)
            if quali is not None:
                return min(max(1, quali), field_size)
            for d in _drivers_from_session(sy):
                if d.get("CarIsPlayer"):
                    for key in ("StartingPosition", "CarClassPosition"):
                        try:
                            sp = int(d.get(key, 0))
                            if sp > 0:
                                return min(sp, field_size)
                        except (TypeError, ValueError):
                            continue

    return max(1, field_size // 2)


def _parse_lap_time_seconds(raw: Any) -> float | None:
    """Parse iRacing session YAML lap time (seconds or M:SS.mmm)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        t = float(raw)
        return t if t > 30.0 else None
    s = str(raw).strip()
    if not s or s in ("-1", "0", "0.0"):
        return None
    if ":" in s:
        try:
            parts = [float(p) for p in s.split(":")]
            if len(parts) == 2:
                t = parts[0] * 60.0 + parts[1]
            elif len(parts) == 3:
                t = parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
            else:
                return None
            return t if t > 30.0 else None
        except (TypeError, ValueError):
            return None
    try:
        t = float(s)
        return t if t > 30.0 else None
    except (TypeError, ValueError):
        return None


def _lap_time_from_result_row(row: dict[str, Any]) -> float | None:
    for key in ("FastestLap", "BestLapTime", "FastestTime", "LapTime", "LastTime"):
        t = _parse_lap_time_seconds(row.get(key))
        if t is not None:
            return t
    return None


def _session_type_priority(session_type: str) -> int:
    st = session_type.strip().lower()
    if "qual" in st:
        return 3
    if st == "open":
        return 2
    if "pract" in st:
        return 1
    return 0


def _best_session_results(sy: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Return results rows from the best pace session (quali > open > practice)."""
    sessions_root = sy.get("SessionInfo", {})
    if not isinstance(sessions_root, dict):
        return [], "none"
    entries = sessions_root.get("Sessions")
    if not isinstance(entries, list):
        return [], "none"

    best_rows: list[dict[str, Any]] = []
    best_priority = -1
    best_label = "none"
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        st = str(entry.get("SessionType") or entry.get("SessionName") or "")
        priority = _session_type_priority(st)
        if priority <= 0:
            continue
        results = entry.get("ResultsPositions") or entry.get("Results")
        if not isinstance(results, list) or not results:
            continue
        if priority > best_priority:
            best_rows = [r for r in results if isinstance(r, dict)]
            best_priority = priority
            best_label = st.strip() or "session"

    return best_rows, best_label


def quali_pace_by_position_from_session(sy: dict[str, Any]) -> tuple[dict[int, float], str]:
    """Map class position → best lap (seconds) from quali/open/practice results."""
    rows, label = _best_session_results(sy)
    pace: dict[int, float] = {}
    for row in rows:
        try:
            pos = int(row.get("Position", 0))
        except (TypeError, ValueError):
            continue
        if pos <= 0:
            continue
        lap_t = _lap_time_from_result_row(row)
        if lap_t is None:
            continue
        pace[pos] = round(lap_t, 3)
    return pace, label


def _pace_from_telemetry_neighbors(telemetry: dict[str, Any]) -> dict[int, float]:
    """Fill gaps from compact ``f`` lap-history block (P1, P3, YOU, etc.)."""
    f = telemetry.get("f")
    if not isinstance(f, dict):
        return {}
    out: dict[int, float] = {}
    for key, times in f.items():
        if not isinstance(times, list) or not times:
            continue
        label = str(key).upper()
        pos: int | None = None
        if label == "YOU":
            m = telemetry.get("m")
            if isinstance(m, dict):
                try:
                    p = int(m.get("p", 0))
                    if p > 0:
                        pos = p
                except (TypeError, ValueError):
                    pos = None
        else:
            m = re.match(r"^P(\d+)$", label)
            if m:
                pos = int(m.group(1))
        if pos is None:
            continue
        valid = [float(t) for t in times if isinstance(t, (int, float)) and float(t) > 30.0]
        if not valid:
            continue
        out[pos] = round(min(valid), 3)
    return out


def build_field_pace_map(
    telemetry: dict[str, Any],
    *,
    hero_position: int,
    hero_pace_s: float,
) -> tuple[dict[int, float], str]:
    """
    Merge session quali results with live neighbor lap history.

    Hero position always uses ``hero_pace_s`` (practice/quali average from packet).
    """
    sy = telemetry.get("sy")
    pace: dict[int, float] = {}
    source = "synthetic"

    if isinstance(sy, dict):
        quali, quali_label = quali_pace_by_position_from_session(sy)
        if quali:
            pace.update(quali)
            source = quali_label

    neighbors = _pace_from_telemetry_neighbors(telemetry)
    for pos, lap_t in neighbors.items():
        pace.setdefault(pos, lap_t)
        if source == "synthetic" and neighbors:
            source = "live neighbors"

    pace[hero_position] = round(hero_pace_s, 3)
    return pace, source


def _interpolate_field_pace(
    position: int,
    pace_map: dict[int, float],
    *,
    hero_position: int,
    hero_anchor: float,
    spread: float,
) -> float:
    if position in pace_map:
        return pace_map[position]
    known = sorted(pace_map.keys())
    if not known:
        return hero_anchor + (position - hero_position) * spread
    if position <= known[0]:
        return pace_map[known[0]] + (position - known[0]) * spread
    if position >= known[-1]:
        return pace_map[known[-1]] + (position - known[-1]) * spread
    for i in range(len(known) - 1):
        lo, hi = known[i], known[i + 1]
        if lo <= position <= hi:
            if hi == lo:
                return pace_map[lo]
            frac = (position - lo) / float(hi - lo)
            return pace_map[lo] + frac * (pace_map[hi] - pace_map[lo])
    return hero_anchor + (position - hero_position) * spread


def distribute_caution_windows(total_laps: int, caution_count: int) -> list[CautionWindow]:
    """Spread `caution_count` yellows across the race distance (2-lap each)."""
    if caution_count <= 0 or total_laps < 8:
        return []

    first_lap = 3
    last_lap = max(first_lap, total_laps - 3)
    if caution_count == 1:
        starts = [max(first_lap, min(last_lap, total_laps // 3))]
    else:
        step = (last_lap - first_lap) / float(caution_count - 1)
        starts = [int(round(first_lap + i * step)) for i in range(caution_count)]

    deduped: list[int] = []
    for lap in sorted(starts):
        if not deduped or lap - deduped[-1] >= 3:
            deduped.append(max(first_lap, min(lap, last_lap)))

    return [
        CautionWindow(
            start_lap=lap,
            duration_laps=2,
            herd_pit_ratio=0.65,
            lead_spots_lost=1,
            total_spots_lost=2,
        )
        for lap in deduped
    ]


def extract_pre_race_sim_context(
    telemetry: dict[str, Any],
    *,
    track_name: str | None = None,
) -> PreRaceSimContext:
    """Build simulation inputs from the current session packet."""
    session_yaml = session_yaml_from_telemetry(telemetry, track_name=track_name)
    baseline = baseline_from_telemetry(telemetry)
    rules = rules_from_telemetry(telemetry)

    m = telemetry.get("m") if isinstance(telemetry.get("m"), dict) else {}
    r = telemetry.get("r") if isinstance(telemetry.get("r"), dict) else {}
    s = telemetry.get("s") if isinstance(telemetry.get("s"), dict) else {}
    sy = telemetry.get("sy") if isinstance(telemetry.get("sy"), dict) else session_yaml

    total_laps = race_lap_total_from_yaml(session_yaml) or 30
    field_size = field_size_from_telemetry(telemetry)
    hero_position = grid_position_from_telemetry(telemetry, field_size=field_size)

    ftl = r.get("ftl")
    try:
        fuel_tank_laps = float(ftl) if ftl is not None else 22.0
    except (TypeError, ValueError):
        fuel_tank_laps = 22.0
    if fuel_tank_laps < 1 or fuel_tank_laps > 200:
        avg = float(baseline.get("avg_lap_time_s", 90.0))
        fc = float(baseline.get("fuel_tank_capacity_gal", 20.0))
        burn = float(baseline.get("fuel_burn_per_lap_gal", 0.12))
        fuel_tank_laps = max(8.0, fc / max(0.01, burn))

    pit_loss = float(baseline.get("pit_lane_loss_time_s", 45.0))
    track_mi = _parse_track_length_miles(sy) if sy else None
    if track_mi is not None and pit_loss <= 50:
        pit_loss = float(get_default_pit_loss_seconds(track_name or "", track_mi))

    track_temp_c = None
    try:
        if s.get("ttc") is not None:
            track_temp_c = float(s["ttc"])
    except (TypeError, ValueError):
        track_temp_c = None

    times_raw = m.get("t") if isinstance(m.get("t"), list) else []
    hero_times = [float(t) for t in times_raw if isinstance(t, (int, float)) and float(t) > 0]

    avg_lap = float(baseline.get("avg_lap_time_s", 90.0))
    falloff = float(baseline.get("tire_wear_pace_falloff_per_lap_s", 0.08))
    tire_wear = max(0.02, min(0.25, falloff * 0.5))

    weekend = sy.get("WeekendInfo", {}) if isinstance(sy, dict) else {}
    tn = track_name
    if not tn and isinstance(weekend, dict):
        tn = str(weekend.get("TrackDisplayName") or weekend.get("TrackName") or "Unknown")
    if not tn:
        tn = "Unknown"

    field_pace, pace_source = build_field_pace_map(
        telemetry,
        hero_position=hero_position,
        hero_pace_s=avg_lap,
    )

    return PreRaceSimContext(
        track_name=tn,
        total_laps=int(total_laps),
        hero_position=hero_position,
        field_size=field_size,
        fuel_tank_laps=round(fuel_tank_laps, 1),
        pit_loss_sec=round(pit_loss, 1),
        tire_sets=int(rules.get("max_tire_sets", 3) or 3),
        hero_base_pace_s=round(avg_lap, 3),
        hero_tire_wear_per_lap=round(tire_wear, 4),
        track_length_miles=track_mi,
        track_temp_c=track_temp_c,
        hero_lap_times=hero_times,
        avg_lap_time_s=round(avg_lap, 2),
        field_pace_by_position=field_pace,
        quali_pace_source=pace_source,
    )


def build_scenario_from_context(
    ctx: PreRaceSimContext,
    *,
    caution_count: int,
    name: str,
) -> RaceScenario:
    cautions = distribute_caution_windows(ctx.total_laps, caution_count)
    return RaceScenario(
        name=name,
        description=f"Pre-race sim — {ctx.track_name} P{ctx.hero_position}",
        total_laps=ctx.total_laps,
        hero_position=ctx.hero_position,
        field_size=ctx.field_size,
        pit_loss_sec=ctx.pit_loss_sec,
        fuel_tank_laps=ctx.fuel_tank_laps,
        pit_payback_laps=2,
        tire_sets=ctx.tire_sets,
        track_name=ctx.track_name,
        track_length_miles=ctx.track_length_miles,
        cautions=cautions,
        hero_base_pace_s=ctx.hero_base_pace_s,
        hero_tire_wear_per_lap=ctx.hero_tire_wear_per_lap,
        hero_lap_times_seed=ctx.hero_lap_times if ctx.hero_lap_times else None,
        field_pace_by_position=ctx.field_pace_by_position or None,
        track_temp_c=ctx.track_temp_c,
        pace_jitter_half_span=0.04,
    )


def _extract_pit_stops(records: list[Any]) -> list[int]:
    stops: list[int] = []
    seen: set[int] = set()
    for rec in records:
        directive = rec.immediate_directive if isinstance(rec.immediate_directive, dict) else {}
        act = str(directive.get("ACTION", "") or "").upper()
        if act not in ("PIT", "PIT NOW"):
            continue
        if rec.lap in seen:
            continue
        seen.add(rec.lap)
        stops.append(int(rec.lap))
    return stops


def run_caution_branch(
    ctx: PreRaceSimContext,
    *,
    label: str,
    caution_count: int,
    seed: int = 42,
) -> PreRaceBranchResult:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:32]
    scenario = build_scenario_from_context(ctx, caution_count=caution_count, name=slug or "branch")
    sim = RaceSimulator(scenario, seed=seed, follow_strategy=True)
    records = sim.run()
    finish = int(records[-1].position) if records else ctx.hero_position
    pit_stops = _extract_pit_stops(records)
    caution_laps = [c.start_lap for c in scenario.cautions]
    return PreRaceBranchResult(
        label=label,
        caution_count=caution_count,
        caution_laps=caution_laps,
        pit_stops=pit_stops,
        finish_position=finish,
        start_position=ctx.hero_position,
        green_stops=len(pit_stops),
    )


def run_pre_race_simulation(
    telemetry: dict[str, Any],
    *,
    track_name: str | None = None,
    seed: int = 42,
) -> tuple[PreRaceSimContext, dict[str, Any], list[PreRaceBranchResult]]:
    """Run green-flag analytic plan + three caution-density sim branches."""
    ctx = extract_pre_race_sim_context(telemetry, track_name=track_name)
    session_yaml = session_yaml_from_telemetry(telemetry, track_name=track_name or ctx.track_name)
    baseline = baseline_from_telemetry(telemetry)
    rules = rules_from_telemetry(telemetry)

    s = telemetry.get("s") if isinstance(telemetry.get("s"), dict) else {}
    track_temp_c = ctx.track_temp_c
    track_temp_ref_c = None
    tenv = s.get("tenv")
    if isinstance(tenv, dict):
        try:
            if tenv.get("ref") is not None:
                track_temp_ref_c = float(tenv["ref"])
        except (TypeError, ValueError):
            track_temp_ref_c = None

    green_plan = generate_pre_race_green_plan(
        session_yaml,
        baseline,
        rules,
        track_temp_c=track_temp_c,
        track_temp_ref_c=track_temp_ref_c,
    )

    branches: list[PreRaceBranchResult] = []
    for label, count in CAUTION_BUCKETS:
        branches.append(
            run_caution_branch(ctx, label=label, caution_count=count, seed=seed)
        )

    return ctx, green_plan, branches


def _format_stop_list(laps: list[int]) -> str:
    if not laps:
        return "no stops"
    return "; ".join(f"L{lap}" for lap in laps)


def _format_caution_laps(laps: list[int]) -> str:
    if not laps:
        return "none"
    if len(laps) <= 6:
        return ", ".join(f"L{lap}" for lap in laps)
    return f"{len(laps)} events (first L{laps[0]}, last L{laps[-1]})"


def format_pre_race_sim_plan(
    ctx: PreRaceSimContext,
    green_plan: dict[str, Any],
    branches: list[PreRaceBranchResult],
) -> str:
    """Multi-branch pre-race layout for overlay / voice."""
    temp_s = f"{ctx.track_temp_c:.0f}°C track" if ctx.track_temp_c is not None else "track temp n/a"
    len_s = f"{ctx.track_length_miles:.2f} mi" if ctx.track_length_miles else "length n/a"

    header = (
        f"SESSION: {ctx.track_name} · {ctx.total_laps} laps · {len_s} · "
        f"P{ctx.hero_position}/{ctx.field_size} · {temp_s}\n"
        f"PACE: {ctx.avg_lap_time_s:.1f}s avg · FUEL: {ctx.fuel_tank_laps:.0f} laps/tank · "
        f"PIT LOSS: {ctx.pit_loss_sec:.0f}s"
    )
    if ctx.field_pace_by_position and ctx.quali_pace_source not in ("none", "synthetic"):
        p1 = ctx.field_pace_by_position.get(1)
        hero = ctx.field_pace_by_position.get(ctx.hero_position, ctx.hero_base_pace_s)
        if p1 is not None and ctx.hero_position > 1:
            delta = hero - p1
            header += f"\nGRID PACE: {ctx.quali_pace_source} · P1 {p1:.2f}s · you {hero:.2f}s ({delta:+.2f}s)"

    green_stops = green_plan.get("scheduled_pit_stops") or []
    if green_stops:
        g_bits = [f"L{s['pit_on_lap']} {s['service']}" for s in green_stops if isinstance(s, dict)]
        green_line = f"GREEN BASELINE: {len(green_stops)} stop(s) — {'; '.join(g_bits)}"
    else:
        green_line = "GREEN BASELINE: no stops projected on green-flag run"

    branch_lines: list[str] = []
    for br in branches:
        delta = br.finish_position - br.start_position
        delta_s = f"{delta:+d}" if delta else "±0"
        branch_lines.append(
            f"{br.label}: ~{br.caution_count} yellows ({_format_caution_laps(br.caution_laps)}) · "
            f"{br.green_stops} stop(s) ({_format_stop_list(br.pit_stops)}) · "
            f"finish ~P{br.finish_position} ({delta_s})"
        )

    body = "\n".join([green_line, *branch_lines])
    note = (
        "NOTE: Branches use your session pace/fuel/grid; sim follows engineer PIT calls under yellow herd pressure.\n"
        "TRIGGER: FUEL|CAUTION  CONF: M"
    )
    return f"{header}\n{body}\n{note}"


def run_pre_race_sim_plan(telemetry: dict[str, Any], *, track_name: str | None = None) -> str:
    """Entry point: session packet → formatted multi-branch pre-race strategy."""
    ctx, green_plan, branches = run_pre_race_simulation(telemetry, track_name=track_name)
    return format_pre_race_sim_plan(ctx, green_plan, branches)
