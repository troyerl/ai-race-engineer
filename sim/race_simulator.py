"""
Offline race simulation for AI Race Engineer.

Builds telemetry packets from a simplified field model, runs the **real**
strategy engine (resolve_live_advice, evaluate_and_forecast_strategy) and
context tracker, and logs lap-by-lap output to a file.

Usage:
    python race_simulator.py --laps 25 --scenario caution_lap8
    python race_simulator.py --list-scenarios
    python race_simulator.py --laps 30 --scenario undercut --log sim_logs/custom.log
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from engineer.auto_alert_engine import AutoMonitorState, evaluate_auto_alert_tick
from engineer.context_engine import DriverContextTracker, StrategyMode
from engineer.race_constants import (
    APEX_LOSS_DEFEND_PCT,
    DEFAULT_AVG_LAP_S,
    HIGH_LAT_SECTOR_END,
    HIGH_LAT_SECTOR_START,
    LAP_HISTORY_DEPTH,
    LONG_RACE_LAPS_LARGE_TRACK,
    LONG_RACE_LAPS_MEDIUM_TRACK,
    LONG_RACE_LAPS_SHORT_TRACK,
    LONG_RACE_LARGE_TRACK_LENGTH_MI,
    LONG_RACE_MEDIUM_TRACK_LENGTH_MI,
    LONG_RACE_SHORT_TRACK_LENGTH_MI,
    THERMAL_GREASY_C,
    THERMAL_WARM_C,
    get_default_pit_loss_seconds,
)
from engineer.speech import _speech_lines
from engineer.strategy_engine import (
    evaluate_and_forecast_strategy,
    incident_push_advice,
    resolve_live_advice,
    tactical_defensive_advice,
    tactical_undercut_advice,
)
from engineer.telemetry import compute_reentry_verdict

# iRacing track-surface codes used by compute_reentry_verdict
_TRK_ON_TRACK = 3
_TRK_APPROACHING_PITS = 2

MIN_FIELD_SIZE = 22
MAX_FIELD_SIZE = 40
DEFAULT_FIELD_SIZE = 32


def clamp_field_size(size: int) -> int:
    """Clamp entry list size to supported iRacing-style fields (22–40)."""
    return max(MIN_FIELD_SIZE, min(MAX_FIELD_SIZE, int(size)))


# =====================================================================
# Field model
# =====================================================================


@dataclass
class SimCar:
    """One car in the simulated field."""

    name: str
    position: int
    base_pace_s: float
    tire_wear_per_lap: float = 0.06
    lap: int = 1
    lap_dist_pct: float = 0.0
    stint_laps: int = 0
    laps_since_pit: int = 0
    last_pit_lap: int | None = None
    tire_wear: float = 0.0
    on_pit_road: bool = False
    in_stall: bool = False
    lap_times: list[float] = field(default_factory=list)
    total_time_s: float = 0.0
    gap_to_ahead_s: float = 1.0
    gap_to_behind_s: float = 1.0
    in_draft: bool = False

    @property
    def current_lap_time_s(self) -> float:
        return self.base_pace_s + self.tire_wear

    def record_lap(self, lap_time_s: float) -> None:
        self.lap_times.append(lap_time_s)
        if len(self.lap_times) > LAP_HISTORY_DEPTH:
            self.lap_times = self.lap_times[-LAP_HISTORY_DEPTH:]
        self.total_time_s += lap_time_s
        self.stint_laps += 1
        self.laps_since_pit += 1
        self.tire_wear += self.tire_wear_per_lap

    def pit(self, *, lap: int, reset_wear: bool = True) -> None:
        self.last_pit_lap = lap
        self.laps_since_pit = 0
        self.stint_laps = 0
        self.on_pit_road = False
        self.in_stall = False
        if reset_wear:
            self.tire_wear = 0.0

    def begin_pit_service(self) -> None:
        """Enter pit road + stall (servicing)."""
        self.on_pit_road = True
        self.in_stall = True

    def exit_pit(self) -> None:
        self.on_pit_road = False
        self.in_stall = False

    def avg_last3_s(self) -> float:
        if not self.lap_times:
            return self.base_pace_s
        recent = self.lap_times[-3:]
        return sum(recent) / len(recent)


@dataclass
class CautionWindow:
    """Yellow-flag window within a race."""

    start_lap: int
    duration_laps: int
    herd_pit_ratio: float = 0.75
    lead_spots_lost: int = 1
    total_spots_lost: int = 2

    def active_on_lap(self, lap: int) -> bool:
        return self.start_lap <= lap < self.start_lap + self.duration_laps


@dataclass
class RaceStartState:
    """Bootstrap state when joining a session mid-race (not from lap 1)."""

    start_lap: int = 1
    fuel_laps_left: float | None = None
    stint_laps: int = 0
    laps_since_pit: int = 0
    tire_wear: float = 0.0
    gap_to_ahead_s: float | None = None
    gap_to_behind_s: float | None = None
    tire_sets_remaining: int | None = None
    field_size: int | None = None
    in_draft: bool | None = None
    # Extra tire falloff on cars ahead (simulates mid-stint deg in traffic)
    rival_ahead_wear: float = 0.0


@dataclass
class RaceScenario:
    """Configurable race script."""

    name: str
    description: str
    total_laps: int = 25
    hero_position: int = 3
    pit_loss_sec: float = 46.0
    fuel_tank_laps: float = 22.0
    pit_payback_laps: int = 2
    tire_sets: int = 3
    track_name: str = ""
    track_length_miles: float | None = None
    cautions: list[CautionWindow] = field(default_factory=list)
    start: RaceStartState | None = None
    on_lap_start: Callable[[int, list[SimCar], SimCar], None] | None = None
    irsdk_overrides: dict[int, dict[str, Any]] = field(default_factory=dict)
    force_pit_laps: set[int] = field(default_factory=set)
    field_size: int = DEFAULT_FIELD_SIZE


def resolve_field_size(scenario: RaceScenario) -> int:
    """Effective field size from scenario + optional start override."""
    if scenario.start is not None and scenario.start.field_size is not None:
        return clamp_field_size(scenario.start.field_size)
    return clamp_field_size(scenario.field_size)


class SimIRSDK:
    """Minimal irsdk callable for DriverContextTracker.poll()."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})

    def __call__(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def update(self, **kwargs: Any) -> None:
        self.state.update(kwargs)


# =====================================================================
# Scenarios
# =====================================================================

SCENARIOS: dict[str, RaceScenario] = {}


def _register_scenario(scenario: RaceScenario) -> RaceScenario:
    fs = resolve_field_size(scenario)
    hp = max(1, min(scenario.hero_position, fs))
    scenario.field_size = fs
    scenario.hero_position = hp
    if scenario.track_length_miles is not None:
        scenario.pit_loss_sec = get_default_pit_loss_seconds(
            scenario.track_name,
            float(scenario.track_length_miles),
        )
    SCENARIOS[scenario.name] = scenario
    return scenario


def _long_race_scenario(
    *,
    name: str,
    description: str,
    track_name: str,
    track_length_miles: float,
    total_laps: int,
    fuel_tank_laps: float,
    tire_sets: int = 5,
    pit_payback_laps: int = 3,
    hero_position: int = 12,
    field_size: int = 32,
) -> RaceScenario:
    """Green-flag long race from lap 1 with track-class pit loss and fuel cycling."""
    return _register_scenario(
        RaceScenario(
            name=name,
            description=description,
            total_laps=total_laps,
            hero_position=hero_position,
            field_size=field_size,
            track_name=track_name,
            track_length_miles=track_length_miles,
            fuel_tank_laps=fuel_tank_laps,
            tire_sets=tire_sets,
            pit_payback_laps=pit_payback_laps,
            start=RaceStartState(start_lap=1),
        )
    )


def _scenario_long_short_track() -> RaceScenario:
    return _long_race_scenario(
        name="long_short_track",
        description=(
            f"Long short-track race ({LONG_RACE_LAPS_SHORT_TRACK} laps) — Bristol-style "
            f"0.53 mi; multi-stop fuel and tire strategy."
        ),
        track_name="Bristol Motor Speedway",
        track_length_miles=LONG_RACE_SHORT_TRACK_LENGTH_MI,
        total_laps=LONG_RACE_LAPS_SHORT_TRACK,
        fuel_tank_laps=35.0,
    )


def _scenario_long_medium_track() -> RaceScenario:
    return _long_race_scenario(
        name="long_medium_track",
        description=(
            f"Long intermediate race ({LONG_RACE_LAPS_MEDIUM_TRACK} laps) — Charlotte-style "
            f"1.5 mi; green-flag stint wear and pit windows."
        ),
        track_name="Charlotte Motor Speedway",
        track_length_miles=LONG_RACE_MEDIUM_TRACK_LENGTH_MI,
        total_laps=LONG_RACE_LAPS_MEDIUM_TRACK,
        fuel_tank_laps=28.0,
    )


def _scenario_long_large_track() -> RaceScenario:
    return _long_race_scenario(
        name="long_large_track",
        description=(
            f"Long superspeedway race ({LONG_RACE_LAPS_LARGE_TRACK} laps) — Daytona-style "
            f"2.5 mi; draft traffic and extended green runs."
        ),
        track_name="Daytona International Speedway",
        track_length_miles=LONG_RACE_LARGE_TRACK_LENGTH_MI,
        total_laps=LONG_RACE_LAPS_LARGE_TRACK,
        fuel_tank_laps=18.0,
    )


def _scenario_default() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="default",
            description="Green-flag stint with gradual tire degradation and balanced gaps (28 cars).",
            total_laps=20,
            hero_position=11,
            field_size=28,
        )
    )


def _scenario_caution_lap8() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="caution_lap8",
            description="Yellow on lap 8 for 3 laps; 32-car field boxes (high herd pit ratio).",
            total_laps=20,
            hero_position=10,
            field_size=32,
            cautions=[CautionWindow(start_lap=8, duration_laps=3, herd_pit_ratio=0.75, lead_spots_lost=1)],
            force_pit_laps={9},
        )
    )


def _scenario_undercut() -> RaceScenario:
    def _lap12(lap: int, field: list[SimCar], hero: SimCar) -> None:
        if lap == 12:
            ahead = next(c for c in field if c.position == hero.position - 1)
            ahead.tire_wear = 2.5
            hero.gap_to_ahead_s = 0.45
            hero.gap_to_behind_s = 1.2
            hero.in_draft = True

    return _register_scenario(
        RaceScenario(
            name="undercut",
            description="Lap 12 offensive window in 35-car field: draft, rival tire deg.",
            total_laps=25,
            hero_position=6,
            field_size=35,
            on_lap_start=_lap12,
            irsdk_overrides={
                12: {"LapDistPct": 0.12, "LatAccel": 12.0, "Speed": 55.0},
                13: {"LapDistPct": 0.12, "LatAccel": 12.0, "Speed": 55.0},
            },
        )
    )


def _scenario_defensive_pressure() -> RaceScenario:
    def _close_behind(lap: int, field: list[SimCar], hero: SimCar) -> None:
        if lap >= 10:
            hero.gap_to_behind_s = 0.35
            hero.gap_to_ahead_s = 2.0

    return _register_scenario(
        RaceScenario(
            name="defensive_pressure",
            description="Car within 0.5s behind from lap 10; 30-car mid-pack defense.",
            total_laps=18,
            hero_position=14,
            field_size=30,
            on_lap_start=_close_behind,
            irsdk_overrides={
                11: {
                    "LapDistPct": 0.10,
                    "LatAccel": 14.0,
                    "Speed": 48.0,
                    "LFtempCM": 108.0,
                    "RFtempCM": 107.0,
                },
            },
        )
    )


def _scenario_lapped_danger() -> RaceScenario:
    def _stretch_leader(lap: int, field: list[SimCar], hero: SimCar) -> None:
        leader = min(field, key=lambda c: c.position)
        leader.lap = hero.lap + 1
        hero.gap_to_ahead_s = 8.0

    return _register_scenario(
        RaceScenario(
            name="lapped_danger",
            description="Leader laps ahead in 36-car field; pit window projects lap down.",
            total_laps=22,
            hero_position=28,
            field_size=36,
            fuel_tank_laps=18.0,
            pit_loss_sec=190.0,
            on_lap_start=_stretch_leader,
        )
    )


def _scenario_fuel_window() -> RaceScenario:
    def _burn_down(lap: int, field: list[SimCar], hero: SimCar) -> None:
        if lap >= 14:
            hero.stint_laps = max(hero.stint_laps, 12)

    return _register_scenario(
        RaceScenario(
            name="fuel_window",
            description="32-car mid-pack: hero enters pit payback window with clean reentry.",
            total_laps=30,
            hero_position=18,
            field_size=32,
            fuel_tank_laps=14.0,
            on_lap_start=_burn_down,
        )
    )


def _seed_lap_history(car: SimCar, *, count: int, wear_ramp: float = 0.04) -> None:
    """Pre-fill lap-time ring buffer as if `count` laps already completed."""
    car.lap_times.clear()
    for i in range(count):
        car.lap_times.append(round(car.base_pace_s + car.tire_wear + i * wear_ramp, 3))
    if count > 0:
        car.tire_wear = max(car.tire_wear, car.tire_wear_per_lap * count)


def _scenario_race_full() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="race_full",
            description="Full 30-lap green race from lap 1 at P12 in a 35-car field.",
            total_laps=30,
            hero_position=12,
            field_size=35,
            fuel_tank_laps=24.0,
            start=RaceStartState(start_lap=1),
        )
    )


def _scenario_join_leader_lap12() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="join_leader_lap12",
            description="Join lap 12/30 leading P1 in 40-car field — defend 0.5s behind.",
            total_laps=30,
            hero_position=1,
            field_size=40,
            fuel_tank_laps=24.0,
            start=RaceStartState(
                start_lap=12,
                fuel_laps_left=16.5,
                stint_laps=11,
                laps_since_pit=11,
                tire_wear=0.72,
                gap_to_ahead_s=0.0,
                gap_to_behind_s=0.52,
                in_draft=False,
            ),
        )
    )


def _scenario_join_traffic_p5_lap22() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="join_traffic_p5_lap22",
            description="Join lap 22/40 at P5 in 38-car tight train — 0.4s gaps, tire deg.",
            total_laps=40,
            hero_position=5,
            field_size=38,
            fuel_tank_laps=22.0,
            start=RaceStartState(
                start_lap=22,
                fuel_laps_left=9.5,
                stint_laps=14,
                laps_since_pit=14,
                tire_wear=0.88,
                gap_to_ahead_s=0.38,
                gap_to_behind_s=0.41,
                in_draft=True,
                rival_ahead_wear=0.35,
            ),
        )
    )


def _scenario_join_traffic_p8_lap30() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="join_traffic_p8_lap30",
            description="Join lap 30/45 at P18 in 40-car pack — fuel window, PACK reentry.",
            total_laps=45,
            hero_position=18,
            field_size=40,
            fuel_tank_laps=20.0,
            pit_payback_laps=2,
            start=RaceStartState(
                start_lap=30,
                fuel_laps_left=2.8,
                stint_laps=16,
                laps_since_pit=16,
                tire_wear=1.05,
                gap_to_ahead_s=0.32,
                gap_to_behind_s=0.28,
                in_draft=True,
                rival_ahead_wear=0.5,
            ),
        )
    )


def _scenario_join_last_lap18() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="join_last_lap18",
            description="Join lap 18/35 dead last P35 in 35-car field — catch pack ahead.",
            total_laps=35,
            hero_position=35,
            field_size=35,
            fuel_tank_laps=26.0,
            start=RaceStartState(
                start_lap=18,
                fuel_laps_left=13.0,
                stint_laps=9,
                laps_since_pit=9,
                tire_wear=0.45,
                gap_to_ahead_s=1.15,
                gap_to_behind_s=0.0,
                in_draft=False,
            ),
        )
    )


def _scenario_tactical_caution_gate() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="tactical_caution_gate",
            description="5-lap gate: yellow L2–3 for tactical state regression checks.",
            total_laps=5,
            hero_position=2,
            field_size=28,
            cautions=[CautionWindow(start_lap=2, duration_laps=2, herd_pit_ratio=0.75)],
        )
    )


def _load_scenarios() -> None:
    _scenario_default()
    _scenario_race_full()
    _scenario_long_short_track()
    _scenario_long_medium_track()
    _scenario_long_large_track()
    _scenario_join_leader_lap12()
    _scenario_join_traffic_p5_lap22()
    _scenario_join_traffic_p8_lap30()
    _scenario_join_last_lap18()
    _scenario_caution_lap8()
    _scenario_tactical_caution_gate()
    _scenario_undercut()
    _scenario_defensive_pressure()
    _scenario_lapped_danger()
    _scenario_fuel_window()


# =====================================================================
# Packet builder
# =====================================================================


def _gap_to_leader_s(hero: SimCar, field: list[SimCar], avg_lap_s: float) -> float:
    leader = min(field, key=lambda c: c.position)
    lap_delta = max(0, leader.lap - hero.lap)
    if lap_delta == 0 and hero.position > 1:
        on_lap = 0.0
        if leader is not hero and leader.lap_dist_pct is not None and hero.lap_dist_pct is not None:
            dd = (hero.lap_dist_pct - leader.lap_dist_pct) % 1.0
            on_lap = dd * avg_lap_s
        positions_back = hero.position - 1
        train_gap = max(hero.gap_to_ahead_s, positions_back * 0.55)
        return round(max(on_lap, train_gap), 2)
    if lap_delta > 0:
        on_lap = 0.0
        if leader is not hero:
            on_lap = max(0.0, (1.0 - hero.lap_dist_pct + leader.lap_dist_pct) % 1.0) * avg_lap_s
        return round(lap_delta * avg_lap_s + on_lap, 2)
    return 0.0


def _build_field_arrays(
    field: list[SimCar],
    hero: SimCar,
    *,
    avg_lap_s: float,
) -> tuple[int, list[float], list[int], list[int], list[float | None]]:
    """Return player_idx and parallel SDK arrays for reentry projection."""
    sorted_cars = sorted(field, key=lambda c: c.position)
    player_idx = next(i for i, c in enumerate(sorted_cars) if c is hero)

    lap_dist: list[float] = []
    laps: list[int] = []
    positions: list[int] = []
    f2_times: list[float | None] = []

    gap_to_leader = _gap_to_leader_s(hero, sorted_cars, avg_lap_s)
    for i, car in enumerate(sorted_cars):
        lap_dist.append(car.lap_dist_pct)
        laps.append(car.lap)
        positions.append(car.position)
        if car is hero:
            f2_times.append(round(gap_to_leader, 2))
        else:
            f2_times.append(None)

    return player_idx, lap_dist, laps, positions, f2_times


def _car_ahead(field: list[SimCar], hero: SimCar) -> SimCar | None:
    return next((c for c in field if c.position == hero.position - 1), None)


def _car_behind(field: list[SimCar], hero: SimCar) -> SimCar | None:
    return next((c for c in field if c.position == hero.position + 1), None)


def _rival_pace_block(car: SimCar | None) -> dict[str, Any] | None:
    if car is None:
        return None
    return {
        "pos": car.position,
        "pace": {
            "avg_last3_s": round(car.avg_last3_s(), 3),
            "n": len(car.lap_times),
            "lap_times": [round(t, 3) for t in car.lap_times[-LAP_HISTORY_DEPTH:]],
        },
    }


def _apply_fi_patch(fi: dict[str, Any], patch: dict[str, Any]) -> None:
    """Shallow-merge fi.hd / fi.cpi fragments from sim tactical overrides."""
    for key, value in patch.items():
        if not isinstance(value, dict):
            fi[key] = value
            continue
        base = fi.setdefault(key, {})
        if isinstance(base, dict):
            base.update(value)


def generate_simulated_packet_extras(
    hero: SimCar,
    scenario: RaceScenario,
    current_lap: int,
    is_caution: bool,
) -> dict[str, Any]:
    """
    Synthesize §16 tactical driver context from scenario irsdk_overrides and field state.

    Returns irsdk keys (apex_loss, max_tire_temp), optional fi.hd/cpi patches, and
    metadata for simulation logs.
    """
    lap_overrides = scenario.irsdk_overrides.get(current_lap, {})

    if is_caution:
        sim_apex_loss = float(lap_overrides.get("apex_loss", 0.0))
        sim_max_tire_temp = float(lap_overrides.get("max_tire_temp", 75.0))
    else:
        is_defending = hero.gap_to_behind_s is not None and hero.gap_to_behind_s < 0.5
        sim_apex_loss = float(lap_overrides.get("apex_loss", 5.8 if is_defending else 1.2))
        sim_max_tire_temp = float(
            lap_overrides.get("max_tire_temp", 107.5 if is_defending else 88.0)
        )

    in_stress_corner = (
        not is_caution
        and (sim_apex_loss > APEX_LOSS_DEFEND_PCT or sim_max_tire_temp >= THERMAL_GREASY_C)
    )
    lap_dist = float(
        lap_overrides.get("LapDistPct", 0.42 if in_stress_corner else 0.12)
    )
    lat_accel = float(lap_overrides.get("LatAccel", 14.0 if in_stress_corner else 11.0))
    apex_baseline_speed = 55.0
    corner_speed = apex_baseline_speed * (1.0 - sim_apex_loss / 100.0)
    speed = float(
        lap_overrides.get(
            "Speed",
            52.0 if is_caution else (corner_speed if sim_apex_loss > 1.0 else 52.0),
        )
    )
    steer = float(
        lap_overrides.get(
            "SteeringWheelAngle",
            0.12 if in_stress_corner else 0.05,
        )
    )

    irsdk: dict[str, Any] = {
        "LapDistPct": lap_dist,
        "LatAccel": lat_accel,
        "Speed": speed,
        "SteeringWheelAngle": steer,
        "LFtempCM": float(lap_overrides.get("LFtempCM", sim_max_tire_temp)),
        "RFtempCM": float(lap_overrides.get("RFtempCM", sim_max_tire_temp - 1.0)),
        "PlayerCarInComponentIncidentCount": lap_overrides.get(
            "PlayerCarInComponentIncidentCount", [0, 0, 0, 0]
        ),
        "PlayerTrackSurface": lap_overrides.get("PlayerTrackSurface", _TRK_ON_TRACK),
    }

    fi_patch: dict[str, Any] = {}
    if "pra" in lap_overrides:
        fi_patch.setdefault("hd", {})["pra"] = lap_overrides["pra"]
    if "cpi_ll" in lap_overrides:
        fi_patch.setdefault("cpi", {})["ll"] = lap_overrides["cpi_ll"]

    tactical_meta = {
        "apex_loss": sim_apex_loss,
        "max_tire_temp": sim_max_tire_temp,
        "apex_baseline_speed": apex_baseline_speed if sim_apex_loss > 1.0 and not is_caution else None,
        "pra": lap_overrides.get("pra"),
        "cpi_ll": lap_overrides.get("cpi_ll"),
    }

    return {
        "is_caution": is_caution,
        "irsdk": irsdk,
        "fi_patch": fi_patch,
        "tactical": tactical_meta,
    }


def _extract_tactical_snapshot(packet: dict[str, Any], context: DriverContextTracker) -> dict[str, Any]:
    fi = packet.get("fi", {}) if isinstance(packet.get("fi"), dict) else {}
    odi = fi.get("odi", {}) if isinstance(fi.get("odi"), dict) else {}
    tac = fi.get("tac", {}) if isinstance(fi.get("tac"), dict) else {}
    m = packet.get("m", {}) if isinstance(packet.get("m"), dict) else {}
    drv = m.get("drv", {}) if isinstance(m.get("drv"), dict) else {}
    return {
        "context_mode": context.current_mode.name,
        "odi_uc": bool(odi.get("uc")),
        "fi_odi_undercut": context.fi_odi_undercut,
        "tac": dict(tac),
        "apex_al": drv.get("al"),
        "therm": drv.get("tsn"),
    }


def _collect_tactical_alerts(packet: dict[str, Any]) -> list[str]:
    alerts: list[str] = []
    if incident_push_advice(packet):
        alerts.append("m_drv_incident_push")
    if tactical_undercut_advice(packet):
        alerts.append("fi_odi_undercut")
    if tactical_defensive_advice(packet):
        alerts.append("m_drv_defensive")
    return alerts


def build_telemetry_packet(
    *,
    hero: SimCar,
    field: list[SimCar],
    scenario: RaceScenario,
    lap: int,
    laps_total: int,
    is_caution: bool,
    caution: CautionWindow | None,
    context_extras: dict[str, Any],
    fuel_laps_left: float,
    can_make_to_end: bool,
    caution_pit_complete: bool = False,
) -> dict[str, Any]:
    """Assemble a strategy-engine-ready telemetry dict from sim state."""
    avg_lap = hero.avg_last3_s() or DEFAULT_AVG_LAP_S
    player_idx, lap_dist, laps, positions, f2_times = _build_field_arrays(field, hero, avg_lap_s=avg_lap)

    surfaces = [_TRK_ON_TRACK] * len(field)
    rej = compute_reentry_verdict(
        player_idx,
        lap_dist=lap_dist,
        surfaces=surfaces,
        laps=laps,
        positions=positions,
        f2_times=f2_times,
        pit_loss_sec=scenario.pit_loss_sec,
        lap_s=avg_lap,
    )

    ahead_car = _car_ahead(field, hero)
    behind_car = _car_behind(field, hero)

    flags: dict[str, bool] = {}
    if is_caution:
        flags = {"yel": True, "cau": True}

    fi: dict[str, Any] = {"rej": rej}
    if is_caution and caution is not None:
        pra = 0.2 if caution_pit_complete else caution.herd_pit_ratio
        ll = 8 if caution_pit_complete else caution.lead_spots_lost
        tl = 8 if caution_pit_complete else caution.total_spots_lost
        fi["hd"] = {"pra": pra, "prb": 0.25}
        fi["cpi"] = {
            "ll": ll,
            "tl": tl,
            "lda": 0,
            "xp": hero.position + (0 if caution_pit_complete else caution.total_spots_lost),
            "gr": 0 if caution_pit_complete else max(1, caution.lead_spots_lost),
        }

    fi.update(context_extras.get("fi", {}))
    if "rej" not in fi:
        fi["rej"] = rej
    else:
        fi["rej"] = {**rej, **fi["rej"]}

    m: dict[str, Any] = {
        "l": lap,
        "p": hero.position,
        "lr": max(0, laps_total - lap),
        "sl": hero.stint_laps,
        "lp": hero.laps_since_pit,
        "fl": round(fuel_laps_left, 3),
        "fcq": "hi",
        "mk": can_make_to_end,
        "pb": scenario.pit_payback_laps,
        "pw": True,
        "fo": round(hero.tire_wear, 3),
        "ga": round(hero.gap_to_ahead_s, 3) if ahead_car else None,
        "gb": round(hero.gap_to_behind_s, 3) if behind_car else None,
        "pr": hero.on_pit_road,
        "ps": hero.in_stall,
        "pc": {"avg_last3_s": round(avg_lap, 3), "n": len(hero.lap_times)},
    }
    m.update(context_extras.get("m", {}))

    s: dict[str, Any] = {
        "st": "Racing",
        "ty": "Race",
        "lt": laps_total,
        "flb": flags,
        "ttc": 32.0,
        "te": round(hero.total_time_s, 1),
    }
    s.update(context_extras.get("s", {}))

    rv: dict[str, Any] = {}
    ah = _rival_pace_block(ahead_car)
    bh = _rival_pace_block(behind_car)
    if ah:
        rv["ahead"] = ah
    if bh:
        rv["behind"] = bh

    return {
        "x": {"md": "live", "u": "us", "fe": 1, "ll": 1},
        "s": s,
        "m": m,
        "r": {
            "pl": scenario.pit_loss_sec,
            "ts": scenario.tire_sets,
            "ftl": scenario.fuel_tank_laps,
            "fc": 20.0,
        },
        "fi": fi,
        "rv": rv,
    }


# =====================================================================
# Simulator
# =====================================================================


@dataclass
class LapLogRecord:
    lap: int
    is_caution: bool
    mode: str
    gap_ahead: float | None
    gap_behind: float | None
    fuel_laps_left: float
    reentry_verdict: str
    position: int
    on_pit_road: bool
    in_stall: bool
    advice: str
    immediate_directive: dict[str, Any]
    forecast_stops: list[dict[str, Any]]
    auto_alert: str
    voice_lines: list[str]
    tactical_summary: dict[str, Any]
    tactical_alerts: list[str]
    sim_extras: dict[str, Any]
    packet: dict[str, Any]


class RaceSimulator:
    """Step a synthetic race and evaluate real strategy output each lap."""

    CAUTION_LAP_TIME_S = 105.0

    def __init__(self, scenario: RaceScenario, *, seed: int | None = None) -> None:
        self.scenario = scenario
        self.rng = __import__("random").Random(seed)
        self.field = self._init_field()
        self.hero = next(c for c in self.field if c.name == "HERO")
        self.context = DriverContextTracker()
        self.auto_state = AutoMonitorState()
        self.last_delivered_call = ""
        self.fuel_laps_left = scenario.fuel_tank_laps
        self.records: list[LapLogRecord] = []
        self._caution_pit_complete = False
        self._pit_service_lap: int | None = None
        self.tire_sets_remaining = scenario.tire_sets
        self._apply_start_state()

    @property
    def current_strategy_mode(self) -> StrategyMode:
        return self.context.current_mode

    def _start_lap(self) -> int:
        st = self.scenario.start
        return max(1, st.start_lap if st is not None else 1)

    def _apply_start_state(self) -> None:
        st = self.scenario.start
        if st is None:
            return

        hero = self.hero
        lap = st.start_lap

        for car in self.field:
            car.lap = lap
            offset = car.position - hero.position
            stint = max(1, st.stint_laps + (offset // 2))
            _seed_lap_history(car, count=min(LAP_HISTORY_DEPTH, stint))
            car.stint_laps = stint
            car.laps_since_pit = stint
            car.total_time_s = stint * car.avg_last3_s()

        hero.stint_laps = st.stint_laps
        hero.laps_since_pit = st.laps_since_pit
        hero.tire_wear = st.tire_wear
        hero.lap = lap
        if st.laps_since_pit > 0:
            hero.last_pit_lap = lap - st.laps_since_pit

        if st.fuel_laps_left is not None:
            self.fuel_laps_left = st.fuel_laps_left
        if st.tire_sets_remaining is not None:
            self.tire_sets_remaining = st.tire_sets_remaining

        if st.gap_to_ahead_s is not None:
            hero.gap_to_ahead_s = st.gap_to_ahead_s
        if st.gap_to_behind_s is not None:
            hero.gap_to_behind_s = st.gap_to_behind_s
        if st.in_draft is not None:
            hero.in_draft = st.in_draft
        elif hero.gap_to_ahead_s > 0 and hero.gap_to_ahead_s < 1.5:
            hero.in_draft = True

        if st.rival_ahead_wear > 0:
            for car in self.field:
                if car.position < hero.position:
                    car.tire_wear += st.rival_ahead_wear

        hero.total_time_s = lap * hero.avg_last3_s()
        self._spread_on_track()

        if st.stint_laps >= 3:
            self.context.reset_stint(track_temp_c=32.0)

    def _rebuild_hero_gaps(self, *, under_caution: bool = False) -> None:
        """Recompute hero gaps from position neighbors after a position change."""
        hero = self.hero
        ahead = _car_ahead(self.field, hero)
        behind = _car_behind(self.field, hero)
        if under_caution:
            hero.gap_to_ahead_s = 0.15 if ahead else 0.0
            hero.gap_to_behind_s = 0.15 if behind else 0.0
        else:
            hero.gap_to_ahead_s = 0.45 if ahead else 0.0
            hero.gap_to_behind_s = 0.85 if behind else 0.0
        hero.in_draft = bool(ahead and hero.gap_to_ahead_s < 1.5)

    def _apply_pit_position_loss(self, spots_lost: int) -> None:
        """Move hero back `spots_lost` positions and shift intervening cars forward."""
        hero = self.hero
        old_pos = hero.position
        new_pos = min(len(self.field), old_pos + max(1, spots_lost))
        if new_pos == old_pos:
            self._rebuild_hero_gaps(under_caution=self._active_caution(hero.lap) is not None)
            return
        for car in self.field:
            if car is hero:
                continue
            if old_pos < car.position <= new_pos:
                car.position -= 1
        hero.position = new_pos
        self._rebuild_hero_gaps(under_caution=self._active_caution(hero.lap) is not None)

    def _complete_hero_pit(self, lap: int, *, caution: CautionWindow | None) -> None:
        spots = caution.total_spots_lost if caution is not None else 1
        self.hero.exit_pit()
        self.hero.pit(lap=lap)
        self._apply_pit_position_loss(spots)
        self.fuel_laps_left = self.scenario.fuel_tank_laps
        self.tire_sets_remaining = max(0, self.tire_sets_remaining - 1)
        self.context.reset_stint(track_temp_c=32.0)
        if caution is not None:
            self._caution_pit_complete = True
        self._pit_service_lap = None

    def _init_field(self) -> list[SimCar]:
        n = resolve_field_size(self.scenario)
        hero_pos = self.scenario.hero_position
        cars: list[SimCar] = []
        for pos in range(1, n + 1):
            base = DEFAULT_AVG_LAP_S + (pos - hero_pos) * 0.12 + self.rng.uniform(-0.2, 0.2)
            wear = 0.04 + (pos % 3) * 0.02
            name = "HERO" if pos == hero_pos else f"#{pos:02d}"
            cars.append(
                SimCar(
                    name=name,
                    position=pos,
                    base_pace_s=base,
                    tire_wear_per_lap=wear,
                    gap_to_ahead_s=0.5 if pos > 1 else 0.0,
                    gap_to_behind_s=0.8 if pos < n else 0.0,
                )
            )
        return cars

    def _active_caution(self, lap: int) -> CautionWindow | None:
        for c in self.scenario.cautions:
            if c.active_on_lap(lap):
                return c
        return None

    def _spread_on_track(self) -> None:
        """Assign lap_dist_pct from gaps (hero-centric, scales with field size)."""
        hero = self.hero
        hero.lap_dist_pct = 0.45
        avg = hero.avg_last3_s() or DEFAULT_AVG_LAP_S
        inter_car_s = 0.55
        for car in sorted(self.field, key=lambda c: c.position):
            if car is hero:
                continue
            if car.position < hero.position:
                steps = hero.position - car.position
                gap_s = hero.gap_to_ahead_s if steps == 1 else hero.gap_to_ahead_s + (steps - 1) * inter_car_s
                offset = -gap_s / avg
            else:
                steps = car.position - hero.position
                gap_s = hero.gap_to_behind_s if steps == 1 else hero.gap_to_behind_s + (steps - 1) * inter_car_s
                offset = gap_s / avg
            car.lap_dist_pct = (hero.lap_dist_pct + offset) % 1.0

    def _step_green_physics(self, lap: int) -> None:
        for car in self.field:
            if car.on_pit_road:
                continue
            lap_time = car.current_lap_time_s + self.rng.uniform(-0.05, 0.05)
            car.record_lap(lap_time)

        hero = self.hero
        ahead = _car_ahead(self.field, hero)
        behind = _car_behind(self.field, hero)

        if ahead:
            delta = hero.lap_times[-1] - ahead.lap_times[-1]
            hero.gap_to_ahead_s = max(0.12, hero.gap_to_ahead_s + delta)
            hero.in_draft = hero.gap_to_ahead_s < 1.5
        if behind and behind.lap_times:
            delta = behind.lap_times[-1] - hero.lap_times[-1]
            hero.gap_to_behind_s = max(0.12, hero.gap_to_behind_s + delta)

        self.fuel_laps_left = max(0.0, self.fuel_laps_left - 1.0)
        self._spread_on_track()

    def _step_caution_physics(self, caution: CautionWindow) -> None:
        pace = self.CAUTION_LAP_TIME_S
        for car in self.field:
            if car is self.hero and (car.on_pit_road or car.in_stall):
                continue
            car.record_lap(pace)
            car.tire_wear = max(0.0, car.tire_wear - 0.01)
        if not (self.hero.on_pit_road or self.hero.in_stall):
            self.hero.gap_to_ahead_s = 0.15
            self.hero.gap_to_behind_s = 0.15
            self.hero.in_draft = False
            self.fuel_laps_left = max(0.0, self.fuel_laps_left - 0.25)

    def _poll_context(
        self,
        *,
        lap: int,
        is_caution: bool,
        telemetry: dict[str, Any],
        irsdk: SimIRSDK,
    ) -> dict[str, Any]:
        m = telemetry.get("m", {})
        rv = telemetry.get("rv", {})
        ahead = rv.get("ahead", {}) if isinstance(rv.get("ahead"), dict) else {}
        ah_pace = ahead.get("pace", {}) if isinstance(ahead.get("pace"), dict) else {}
        ahead_laps = ah_pace.get("lap_times") if isinstance(ah_pace.get("lap_times"), list) else None

        ga = m.get("ga")
        gb = m.get("gb")
        gap_ahead = float(ga) if isinstance(ga, (int, float)) else None
        gap_behind = float(gb) if isinstance(gb, (int, float)) else None

        pa = None
        you_avg = (m.get("pc") or {}).get("avg_last3_s")
        ah_avg = ah_pace.get("avg_last3_s")
        if isinstance(you_avg, (int, float)) and isinstance(ah_avg, (int, float)):
            pa = float(you_avg) - float(ah_avg)

        self.context.poll(
            irsdk,
            player_idx=0,
            lap=lap,
            on_track=not (self.hero.on_pit_road or self.hero.in_stall),
            is_caution=is_caution,
            gap_ahead_s=gap_ahead,
            gap_behind_s=gap_behind,
            session_time_elapsed=(telemetry.get("s") or {}).get("te"),
            ahead_lap_times=[float(x) for x in ahead_laps] if ahead_laps else None,
            best_lap_s=float(min(self.hero.lap_times)) if self.hero.lap_times else None,
            pace_delta_ahead=pa,
            pit_loss_sec=float((telemetry.get("r") or {}).get("pl", 46)),
            tire_falloff_s=float(m.get("fo", 0)),
        )

        fi = telemetry.get("fi", {})
        rej = fi.get("rej", {}) if isinstance(fi.get("rej"), dict) else {}
        return self.context.build_packet_extras(
            track_temp_c=32.0,
            tire_wear_rate_est=None,
            pit_loss_sec=float((telemetry.get("r") or {}).get("pl", 46)),
            tire_falloff_s=float(m.get("fo", 0)),
            fuel_stint_cap=int((telemetry.get("r") or {}).get("ftl", 22)),
            rivals=rv,
            you_pace=m.get("pc", {}),
            reentry_verdict=str(rej.get("v", "CLEAN")),
            current_lap=lap,
            gap_behind_s=gap_behind,
        )

    @staticmethod
    def _merge_extras(packet: dict[str, Any], extras: dict[str, Any]) -> None:
        for key in ("m", "s", "fi"):
            part = extras.get(key)
            if isinstance(part, dict):
                base = packet.setdefault(key, {})
                if isinstance(base, dict):
                    base.update(part)

    def run_lap(self, lap: int) -> LapLogRecord:
        if self.scenario.on_lap_start:
            self.scenario.on_lap_start(lap, self.field, self.hero)

        caution = self._active_caution(lap)
        is_caution = caution is not None
        if not is_caution:
            self._caution_pit_complete = False

        # Begin pit service at start of scheduled lap (before packet build).
        if lap in self.scenario.force_pit_laps and self._pit_service_lap is None:
            self.hero.begin_pit_service()
            self._pit_service_lap = lap

        if is_caution:
            self._step_caution_physics(caution)  # type: ignore[arg-type]
        else:
            self._step_green_physics(lap)

        laps_total = self.scenario.total_laps
        can_make = self.fuel_laps_left >= max(0, laps_total - lap)

        base_packet = build_telemetry_packet(
            hero=self.hero,
            field=self.field,
            scenario=self.scenario,
            lap=lap,
            laps_total=laps_total,
            is_caution=is_caution,
            caution=caution,
            context_extras={},
            fuel_laps_left=self.fuel_laps_left,
            can_make_to_end=can_make,
            caution_pit_complete=self._caution_pit_complete,
        )

        sim_extras = generate_simulated_packet_extras(
            self.hero,
            self.scenario,
            lap,
            is_caution,
        )
        if sim_extras.get("fi_patch"):
            _apply_fi_patch(base_packet.setdefault("fi", {}), sim_extras["fi_patch"])

        ir_overrides = self.scenario.irsdk_overrides.get(lap, {})
        irsdk_state = {**sim_extras["irsdk"], **ir_overrides}
        tactical_meta = sim_extras.get("tactical", {})
        apex_baseline = tactical_meta.get("apex_baseline_speed")
        if apex_baseline and not is_caution:
            self.context._apex_speed_baseline = float(apex_baseline)
        if self.context._prev_steer is None and float(irsdk_state.get("SteeringWheelAngle", 0.05)) > 0.08:
            self.context._prev_steer = 0.02

        irsdk = SimIRSDK({"Lap": lap, **irsdk_state})

        extras = self._poll_context(lap=lap, is_caution=is_caution, telemetry=base_packet, irsdk=irsdk)
        packet = json.loads(json.dumps(base_packet))
        packet.setdefault("r", {})["ts"] = self.tire_sets_remaining
        self._merge_extras(packet, extras)

        advice = resolve_live_advice(packet, mode="live")
        tactical_alerts = _collect_tactical_alerts(packet)
        tactical_summary = _extract_tactical_snapshot(packet, self.context)
        eval_result = evaluate_and_forecast_strategy(packet)
        immediate = eval_result.get("immediate_directive", {})
        if not isinstance(immediate, dict):
            immediate = {}
        gfc = eval_result.get("green_flag_rest_of_race_forecast", {})
        forecast = gfc.get("projected_pit_schedule", []) if isinstance(gfc, dict) else []
        if not isinstance(forecast, list):
            forecast = []

        self.auto_state, alert_decision = evaluate_auto_alert_tick(
            self.auto_state,
            lap=lap,
            is_caution=is_caution,
            telemetry=packet,
            last_delivered_call_line=self.last_delivered_call,
        )
        if alert_decision.deliver and alert_decision.call_line:
            self.last_delivered_call = alert_decision.call_line

        voice = _speech_lines(advice, include_why=True)

        fi = packet.get("fi", {})
        sm = fi.get("sm", {}) if isinstance(fi.get("sm"), dict) else {}
        rej = fi.get("rej", {}) if isinstance(fi.get("rej"), dict) else {}
        mode = str(sm.get("n", "BALANCED"))
        m = packet.get("m", {})

        record = LapLogRecord(
            lap=lap,
            is_caution=is_caution,
            mode=mode,
            gap_ahead=m.get("ga"),
            gap_behind=m.get("gb"),
            fuel_laps_left=float(m.get("fl", 0)),
            reentry_verdict=str(rej.get("v", "?")),
            position=int(m.get("p", self.hero.position)),
            on_pit_road=bool(m.get("pr")),
            in_stall=bool(m.get("ps")),
            advice=advice,
            immediate_directive=immediate,
            forecast_stops=forecast,
            auto_alert=f"deliver={alert_decision.deliver} reason={alert_decision.reason}",
            voice_lines=voice,
            tactical_summary=tactical_summary,
            tactical_alerts=tactical_alerts,
            sim_extras=sim_extras.get("tactical", {}),
            packet=packet,
        )
        self.records.append(record)

        # Complete pit service at end of the service lap.
        if self._pit_service_lap == lap and (self.hero.on_pit_road or self.hero.in_stall):
            self._complete_hero_pit(lap, caution=caution)

        self.hero.lap = lap
        return record

    def run(self) -> list[LapLogRecord]:
        first = self._start_lap()
        for lap in range(first, self.scenario.total_laps + 1):
            self.run_lap(lap)
        return self.records


# =====================================================================
# Logging
# =====================================================================


def _default_log_path(scenario_name: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path("sim_logs") / f"{scenario_name}_{ts}.log"


def write_sim_log(
    records: list[LapLogRecord],
    *,
    scenario: RaceScenario,
    log_path: Path,
    include_packet: bool = False,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"AI Race Engineer — simulation log")
    lines.append(f"Scenario: {scenario.name} — {scenario.description}")
    if scenario.track_name:
        mi = scenario.track_length_miles
        mi_s = f" ({mi:g} mi)" if isinstance(mi, (int, float)) else ""
        lines.append(f"Track: {scenario.track_name}{mi_s}")
    fs = resolve_field_size(scenario)
    if scenario.start and scenario.start.start_lap > 1:
        st = scenario.start
        lines.append(
            f"Field: {fs} cars · Join lap {st.start_lap}/{scenario.total_laps} · P{scenario.hero_position} · "
            f"fuel {st.fuel_laps_left} laps · stint {st.stint_laps} · "
            f"gaps {st.gap_to_ahead_s}/{st.gap_to_behind_s}s"
        )
    else:
        lines.append(
            f"Field: {fs} cars · Start lap 1/{scenario.total_laps} · P{scenario.hero_position}"
        )
    lines.append(f"Pit loss: {scenario.pit_loss_sec}s | Tank: {scenario.fuel_tank_laps} laps")
    lines.append(f"Written: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 80)
    lines.append("")

    for rec in records:
        flag = "CAUTION" if rec.is_caution else "GREEN"
        lines.append("-" * 80)
        lines.append(
            f"LAP {rec.lap:02d} | {flag} | P{rec.position} | Mode: {rec.mode} | "
            f"Gap ahead: {rec.gap_ahead} | Gap behind: {rec.gap_behind} | "
            f"Fuel: {rec.fuel_laps_left:.2f} laps | Reentry: {rec.reentry_verdict}"
            + (f" | PIT ROAD" if rec.on_pit_road or rec.in_stall else "")
        )
        tac = rec.tactical_summary
        if tac:
            tac_flags = tac.get("tac") or {}
            lines.append(
                f"TACTICAL: ctx={tac.get('context_mode')} uc={tac.get('odi_uc')} "
                f"undercut={tac.get('fi_odi_undercut')} apex={tac.get('apex_al')} "
                f"therm={tac.get('therm')} tac={json.dumps(tac_flags)} "
                f"alerts={rec.tactical_alerts or []}"
            )
            if rec.sim_extras:
                lines.append(
                    f"SIM TELEMETRY: apex_loss={rec.sim_extras.get('apex_loss')} "
                    f"max_tire_temp={rec.sim_extras.get('max_tire_temp')}"
                )
        lines.append("-" * 80)
        lines.append("IMMEDIATE DIRECTIVE:")
        lines.append(json.dumps(rec.immediate_directive, indent=2))
        lines.append("")
        lines.append("ENGINEER ADVICE (resolve_live_advice):")
        lines.append(rec.advice)
        lines.append("")
        if rec.forecast_stops:
            lines.append("FORECAST STOPS:")
            for stop in rec.forecast_stops:
                lines.append(f"  - lap {stop.get('estimated_pit_lap')} "
                             f"({stop.get('laps_from_now')} from now) — {stop.get('service_required')}")
            lines.append("")
        lines.append(f"AUTO ALERT: {rec.auto_alert}")
        if rec.voice_lines:
            lines.append("VOICE:")
            for vl in rec.voice_lines:
                lines.append(f"  > {vl}")
        lines.append("")
        if include_packet:
            lines.append("PACKET JSON:")
            lines.append(json.dumps(rec.packet, indent=2))
            lines.append("")

    lines.append("=" * 80)
    lines.append("END OF SIMULATION")
    lines.append("=" * 80)

    log_path.write_text("\n".join(lines), encoding="utf-8")


# =====================================================================
# CLI
# =====================================================================


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    _load_scenarios()
    parser = argparse.ArgumentParser(
        description="Simulate a race and log AI Race Engineer strategy output.",
    )
    parser.add_argument(
        "--scenario",
        default="default",
        choices=sorted(SCENARIOS.keys()),
        help="Predefined race script (default: default)",
    )
    parser.add_argument("--laps", type=int, default=None, help="Override scenario lap count")
    parser.add_argument(
        "--field-size",
        type=int,
        default=None,
        help=f"Override entry count ({MIN_FIELD_SIZE}–{MAX_FIELD_SIZE} cars)",
    )
    parser.add_argument("--start-lap", type=int, default=None, help="Override scenario join lap (mid-race entry)")
    parser.add_argument("--log", type=Path, default=None, help="Log file path (default: sim_logs/<scenario>_<ts>.log)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for pace jitter")
    parser.add_argument("--packet-json", action="store_true", help="Include full packet JSON per lap in log")
    parser.add_argument("--list-scenarios", action="store_true", help="Print scenarios and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Echo log lines to stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.list_scenarios:
        for name, sc in sorted(SCENARIOS.items()):
            print(f"  {name:22} {sc.description}")
        return 0

    scenario = SCENARIOS[args.scenario]
    if args.laps is not None or args.start_lap is not None or args.field_size is not None:
        base = scenario
        st = base.start
        if args.start_lap is not None:
            st = RaceStartState(
                start_lap=args.start_lap,
                fuel_laps_left=st.fuel_laps_left if st else base.fuel_tank_laps * 0.55,
                stint_laps=st.stint_laps if st else max(5, args.start_lap // 2),
                laps_since_pit=st.laps_since_pit if st else max(5, args.start_lap // 2),
                tire_wear=st.tire_wear if st else 0.55,
                gap_to_ahead_s=st.gap_to_ahead_s if st else 0.45,
                gap_to_behind_s=st.gap_to_behind_s if st else 0.75,
                tire_sets_remaining=st.tire_sets_remaining if st else base.tire_sets,
                field_size=(
                    clamp_field_size(args.field_size)
                    if args.field_size is not None
                    else (st.field_size if st else None)
                ),
                in_draft=st.in_draft if st else None,
                rival_ahead_wear=st.rival_ahead_wear if st else 0.0,
            )
        fs = clamp_field_size(args.field_size) if args.field_size is not None else base.field_size
        scenario = RaceScenario(
            name=base.name,
            description=base.description,
            total_laps=args.laps if args.laps is not None else base.total_laps,
            hero_position=min(base.hero_position, fs),
            pit_loss_sec=base.pit_loss_sec,
            fuel_tank_laps=base.fuel_tank_laps,
            pit_payback_laps=base.pit_payback_laps,
            tire_sets=base.tire_sets,
            cautions=list(base.cautions),
            start=st,
            on_lap_start=base.on_lap_start,
            irsdk_overrides=dict(base.irsdk_overrides),
            force_pit_laps=set(base.force_pit_laps),
            field_size=fs,
        )

    sim = RaceSimulator(scenario, seed=args.seed)
    records = sim.run()

    log_path = args.log or _default_log_path(scenario.name)
    write_sim_log(records, scenario=scenario, log_path=log_path, include_packet=args.packet_json)

    print(f"Simulation complete: {scenario.name} ({len(records)} laps)")
    print(f"Log written to: {log_path.resolve()}")

    if args.verbose:
        print()
        print(log_path.read_text(encoding="utf-8"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
