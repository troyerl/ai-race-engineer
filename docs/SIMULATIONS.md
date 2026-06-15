# Race simulations

Offline race simulation exercises the **same deterministic stack** as live iRacing sessions: telemetry packets are synthesized from a simplified field model, then passed through `context_engine`, `strategy_engine`, `auto_alert_engine`, and `speech`.

Primary module: `sim/race_simulator.py` (CLI: `python race_simulator.py` or `python -m sim.race_simulator`)  
Calculation reference: [CALCULATIONS.md](CALCULATIONS.md)

---

## Quick start

```bash
# List built-in scenarios
python3 race_simulator.py --list-scenarios

# Full race from lap 1
python3 race_simulator.py --scenario race_full

# Mid-pack traffic join
python3 race_simulator.py --scenario join_traffic_p5_lap22 -v

# Dead last, catching the pack
python3 race_simulator.py --scenario join_last_lap18

# Leader defending
python3 race_simulator.py --scenario join_leader_lap12

# Tactical regression gate (yellow L2–3)
python3 race_simulator.py --scenario tactical_caution_gate -v

# Pre-race caution-branch strategy (from saved telemetry packet)
python3 scripts/pre_race_sim.py --packet sim_logs/my_packet.json

# Long green-flag races by track class
python3 race_simulator.py --scenario long_short_track    # 120 laps, short oval
python3 race_simulator.py --scenario long_medium_track     # 90 laps, intermediate
python3 race_simulator.py --scenario long_large_track    # 60 laps, superspeedway

# Custom lap count / log path
python3 race_simulator.py --scenario undercut --laps 30 --log sim_logs/my_run.log

# Run every built-in scenario (logs under sim_logs/)
python3 scripts/run_all_simulations.py
```

No iRacing install required. Uses the same Python environment as the app (`python3 -m unittest discover -s tests` includes `tests/test_race_simulator.py`).

---

## What gets exercised

Each simulated lap:

1. **Physics step** — green-flag pace/tire wear/gap evolution, or caution pacing (compressed 0.15s gaps, no draft).
2. **Packet build** — `m`, `s`, `r`, `fi`, `rv`, `x` shaped like live telemetry (`compute_reentry_verdict()` for `fi.rej`).
3. **Tactical telemetry synthesis** — `generate_simulated_packet_extras()` maps scenario state + `irsdk_overrides` into `SimIRSDK` vars (`Speed`, tire temps, corner sector) and optional `fi.hd` / `fi.cpi` patches.
4. **Context poll** — `DriverContextTracker.poll(..., is_caution=...)` + `build_packet_extras()` → `fi.sm`, `fi.odi`, `fi.tac`, `m.drv` (apex loss, thermal stress).
5. **Strategy** — `resolve_live_advice()` (incident → undercut → defensive → main strategy) then `evaluate_and_forecast_strategy()`.
6. **Auto-alert** — `evaluate_auto_alert_tick()` (lap/caution transitions).
7. **Voice** — `_speech_lines()` for TTS preview.

The simulator does **not** duplicate strategy rules in a second engine; output reflects production code paths.

---

## Tactical state orchestration

Production `StrategyMode` (`context_engine`) has three modes: **BALANCED**, **OFFENSIVE**, **DEFENSIVE**. There is no separate caution pacing mode — yellow-flag behavior is gated by `is_caution` on the context poll and by `s.flb` (`yel` / `cau`) in the packet.

| Mode | Gap rule (approx.) | Context metrics active |
|------|--------------------|-------------------------|
| `OFFENSIVE` | Car ahead &lt; 0.7s, car behind ≥ 0.8s | Draft streak, `fi.odi.uc` undercut predictor |
| `DEFENSIVE` | Car behind &lt; 0.5s | Apex loss (`m.drv.al`), thermal (`m.drv.tsn`), divebomb (`fi.tac.db`) |
| `BALANCED` | Otherwise | Incident/offline only |

**Caution gates**

- `poll(..., is_caution=True)` disables draft detection and offensive undercut inputs.
- `tactical_undercut_advice()` and `tactical_defensive_advice()` return `None` when `s.flb` indicates yellow.
- Caution physics compresses hero gaps to **0.15s** ahead and behind → typically **DEFENSIVE** mode, but **no** `fi.odi.uc` under yellow.
- `fi.hd.pra` / `fi.cpi` come from `CautionWindow` (overridable per lap via `irsdk_overrides`).

**Synthetic defaults** (`generate_simulated_packet_extras`)

When a lap has no explicit `irsdk_overrides`, the helper derives IRSDK values from field geometry:

| Condition | `apex_loss` | `max_tire_temp` |
|-----------|-------------|-----------------|
| Green, defending (`gb` &lt; 0.5s) | 5.8% | 107.5°C |
| Green, not defending | 1.2% | 88.0°C |
| Caution | 0.0% | 75.0°C (cooled tires) |

High apex loss or greasy temps auto-select a high-lat corner sector (`LapDistPct` ≈ 0.42, `LatAccel` ≈ 14) so `DriverContextTracker` samples defensive metrics. Apex loss uses a seeded 55 m/s baseline when loss &gt; 1%.

**Tactical alerts** logged per lap (when producers fire):

- `fi_odi_undercut` — offensive undercut advice (`resolve_live_advice` priority #2)
- `m_drv_defensive` — defensive cool/line/divebomb advice (priority #3)
- `m_drv_incident_push` — incident push (priority #1)

---

## Command-line options

| Flag | Default | Description |
|------|---------|-------------|
| `--scenario` | `default` | Built-in race script (see below). |
| `--laps` | scenario default | Override total lap count. |
| `--log` | `sim_logs/<scenario>_<UTC_ts>.log` | Output file path. |
| `--field-size` | scenario default | Entry count (**22–40** cars). |
| `--start-lap` | scenario default | Override mid-race join lap. |
| `--seed` | `42` | RNG seed for pace jitter. |
| `--packet-json` | off | Append full telemetry JSON per lap to the log. |
| `--follow-strategy` | off | Execute `PIT` / `PIT NOW` immediate directives (models stop cost + position loss). |
| `--list-scenarios` | — | Print scenario names and exit. |
| `-v` / `--verbose` | off | Echo the log file to stdout after the run. |

### Field size (22–40 cars)

Every scenario sets an entry count in the **22–40** range (typical iRacing splits). The simulator builds that many cars for `compute_reentry_verdict()`, pack/traffic projection, and position loss.

```bash
python3 race_simulator.py --scenario race_full --field-size 22
python3 race_simulator.py --scenario join_traffic_p8_lap30   # 40-car grid, P18
```

| Scenario | Cars | Hero grid spot |
|----------|------|----------------|
| `race_full` | 35 | P12 |
| `default` | 28 | P11 | Green to checkered on one tank (20-lap sprint); expect **CHECKERED**, no phantom L22 box. |
| `caution_lap8` | 32 | P10 |
| `tactical_caution_gate` | 28 | P2 |
| `defensive_pressure` | 30 | P14 |
| `fuel_window` | 32 | P18 |
| `join_leader_lap12` | 40 | P1 |
| `join_traffic_p5_lap22` | 38 | P5 |
| `join_traffic_p8_lap30` | 40 | P18 |
| `join_last_lap18` | 35 | P35 (last) |
| `undercut` | 35 | P6 |
| `undercut_pace_stable` | 32 | P6 | Join L14 — stable pace, offensive undercut fires |
| `undercut_pace_unstable` | 32 | P6 | Join L14 — erratic pace, undercut suppressed |
| `lapped_danger` | 36 | P28 |
| `long_short_track` | 32 | P12 | 120-lap Bristol-style short oval |
| `long_medium_track` | 32 | P12 | 90-lap Charlotte-style intermediate |
| `long_large_track` | 32 | P12 | 60-lap Daytona-style superspeedway |

### Log file behavior

- **Default:** each run creates a **new** timestamped file. Previous logs in `sim_logs/` are **not** deleted.
- **`--log path`:** overwrites that specific file if it already exists.
- `sim_logs/` is listed in `.gitignore` (local artifacts only).

---

## Built-in scenarios

### Full race

| Name | Laps simulated | Entry | Focus |
|------|----------------|-------|--------|
| `race_full` | 1 → 30 | P12, lap 1 | Complete green-flag race from start to checkered. |
| `default` | 1 → 20 | P11, lap 1 | Shorter opening stint; **run-to-finish** (22-lap tank, 20-lap race) — no pit window. |

### Long races (track class)

Full green-flag races from lap 1 with track-length pit loss (`get_default_pit_loss_seconds`) and fuel cycling tuned for multiple stops over the distance.

| Name | Laps | Track (representative) | Length | Pit loss | Tank (laps) |
|------|------|------------------------|--------|----------|-------------|
| `long_short_track` | **120** | Bristol Motor Speedway | 0.53 mi | 42s | 35 |
| `long_medium_track` | **90** | Charlotte Motor Speedway | 1.5 mi | 46s | 28 |
| `long_large_track` | **60** | Daytona International Speedway | 2.5 mi | 58s | 18 |

```bash
python3 race_simulator.py --scenario long_medium_track -v
python3 race_simulator.py --scenario long_short_track --log sim_logs/bristol_120.log
```

Override lap count for a quicker smoke run: `--laps 30` (still uses scenario fuel/tank profile).

### Mid-race join — position & traffic

| Name | Laps simulated | Entry | Focus |
|------|----------------|-------|--------|
| `join_leader_lap12` | 12 → 30 | **P1**, lap 12 | Leading; car 0.5s behind; defend / fuel management. |
| `join_traffic_p5_lap22` | 22 → 40 | **P5**, lap 22 | Tight train (~0.4s gaps); draft; mid-stint tire deg. |
| `join_traffic_p8_lap30` | 30 → 45 | **P18**, lap 30 | 40-car pack; fuel window; PACK reentry. |
| `join_last_lap18` | 18 → 35 | **P35**, lap 18 | Last row in 35-car field; catch pack ahead. |

### Scripted events (from lap 1)

| Name | Laps | Focus |
|------|------|--------|
| `caution_lap8` | 20 | Yellow L8–L10; field boxes (`pra=0.75`); hero pits L9. |
| `tactical_caution_gate` | 5 | Yellow L2–L3; regression gate for caution + tactical suppression. |
| `undercut` | 25 | L12 offensive window: draft, close gap, rival tire deg. |
| `undercut_pace_stable` | 17 (join 14→30) | Pace stability gate **open** — offensive undercut PIT NOW on L14. |
| `undercut_pace_unstable` | 17 (join 14→30) | Same undercut window; high clean-lap σ suppresses offensive gamble. |
| `defensive_pressure` | 18 | Car &lt;0.5s behind from L10; synthetic apex/thermal stress L11+. |
| `lapped_danger` | 22 | Leader a lap ahead; 190s pit loss → `LAPPED_DANGER` deferral. |
| `fuel_window` | 30 | Mid-race fuel payback window with clean reentry. |

### Mid-race join (`RaceStartState`)

Join scenarios skip laps 1…N−1 and bootstrap hero + field state:

| Field | Purpose |
|-------|---------|
| `start_lap` | First simulated lap (session already underway). |
| `fuel_laps_left` | Tank range at join. |
| `stint_laps` / `laps_since_pit` | Stint length for post-pit quiet / tire model. |
| `tire_wear` | Cumulative falloff at join. |
| `gap_to_ahead_s` / `gap_to_behind_s` | Traffic geometry (0 = no neighbor). |
| `rival_ahead_wear` | Extra deg on cars ahead (undercut / ODI tests). |
| `in_draft` | Draft flag at join. |

Override join lap without editing code:

```bash
python3 race_simulator.py --scenario join_traffic_p5_lap22 --start-lap 25
```

### Scenario details (scripted)

**`caution_lap8`**

- Caution: laps 8–10 (`CautionWindow`: `herd_pit_ratio=0.75`, `lead_spots_lost=1`, `total_spots_lost=2`).
- `force_pit_laps={9}`: hero enters pit road on L9 (`m.pr` / `m.ps`), completes service end of lap.
- After service: position loss, fuel/tire reset, `caution_pit_complete` so L10+ does not repeat **PIT** under yellow.

**`default`**

- 20-lap race, 22-lap tank, 46s pit loss — strategy should **never** project a stop beyond lap 20.
- Expect every lap: `TARGET PIT: CHECKERED`, no `Target Box:` line, `FORECAST: No further stops if green to the end`.
- Validates `_can_run_to_finish()` and forecast fuel-seed fallback for sim packets (`m.fl` only). See [CALCULATIONS.md §10.3](CALCULATIONS.md#103-green-flag-rest-of-race-forecast).

**`tactical_caution_gate`**

- Short 5-lap script for unit tests and manual inspection of caution + tactical behavior.
- Yellow L2–L3; hero P2 in 28-car field.
- Expect **DEFENSIVE** mode under compression, `fi.odi.uc=False`, no `fi_odi_undercut` in tactical alerts.
**Caution vs green fuel paths** ([CALCULATIONS.md §10.3.1](CALCULATIONS.md#1031-target-pit-lap--target-box-dashboard)):

- **Yellow:** Target box uses live `m.fl` (low burn keeps fuel laps high); forecast paused.
- **Green (live):** Box uses `green_flag_fuel_laps_from_telemetry()` after EMA purge on yellow lift.
- **Green (sim, `m.fl` only):** Box uses `floor(m.fl)` — no EMA in sim packets.

- On **L4 green**, expect **TARGET PIT: CHECKERED** and no target box (5-lap race, fuel covers the distance).

**`undercut`**

- Lap 12 hook: rival ahead high tire wear, hero in draft at 0.45s.
- IRSDK overrides on L12–L13 for corner-sector context sampling.

**`undercut_pace_stable` / `undercut_pace_unstable`**

- Mid-race join at **lap 14/30**, P6, fuel inside pit payback window (`fl≈2.2`, `ftl=25`).
- Degraded rival ahead in draft at 0.35s; `cpi_xp=5` projects merge to P5.
- Simulator feeds the real `CleanLapGate` — `m.pc.std_clean_s` / `n_clean` on every packet.
- **Stable:** tight uniform jitter (`±0.03s`) → L14 `PACE STABILITY: offense_ok=True` → **offensive undercut** immediate directive.
- **Unstable:** swing jitter (`±0.50s`) seeded into stint history → σ ≥ 0.40 → undercut blocked; engine holds position or boxes on fuel only.

```bash
python3 race_simulator.py --scenario undercut_pace_stable -v
python3 race_simulator.py --scenario undercut_pace_unstable -v
```

**`defensive_pressure`**

- From L10: `gap_to_behind_s = 0.35`, `gap_to_ahead_s = 2.0`.
- L11 `irsdk_overrides`: high corner load + 108°C fronts → `DEFENSIVE` + greasy thermal path.
- `generate_simulated_packet_extras` also applies defending defaults when `gb < 0.5s`.

**`lapped_danger`**

- Leader `lap = hero.lap + 1`, large `pit_loss_sec` (190s) for `fi.rej.v == LAPPED_DANGER` when window opens.

---

## Log format

Each lap block contains:

```
--------------------------------------------------------------------------------
LAP 09 | CAUTION | P5 | Mode: DEFENSIVE | Gap ahead: 0.15 | Gap behind: 0.15 | ...
TACTICAL: ctx=DEFENSIVE uc=False undercut=False apex=5.8 therm=GREASY tac={...} alerts=[]
SIM TELEMETRY: apex_loss=0.0 max_tire_temp=75.0
--------------------------------------------------------------------------------
IMMEDIATE DIRECTIVE:
{
  "ACTION": "STAY OUT",
  "SERVICE": "NONE",
  "WHY": "Already on pit road or in the service box."
}

ENGINEER ADVICE (resolve_live_advice):
<full strategy dashboard>

FORECAST STOPS:
  - lap N (X from now) — 4 TIRES

AUTO ALERT: deliver=True reason=deliver
VOICE:
  > ...
```

| Field | Meaning |
|-------|---------|
| `Mode` | `fi.sm.n` from context tracker (`BALANCED` / `OFFENSIVE` / `DEFENSIVE`). |
| `PACE STABILITY` | `std_clean_s`, `n_clean`, and `offense_ok` from `CleanLapGate` (when ≥5 clean laps). |
| `TACTICAL` | Post-tick snapshot: context mode, `fi.odi.uc`, apex loss, thermal state, `fi.tac` flags, fired alert IDs. |
| `SIM TELEMETRY` | Values synthesized by `generate_simulated_packet_extras` before the context poll. |
| `TARGET PIT` | `CHECKERED` when run-to-finish; else lap number capped to race distance. |
| `Target Box` | Pit window from `_fuel_laps_for_pit_window()` ([CALCULATIONS.md §10.3.1](CALCULATIONS.md#1031-target-pit-lap--target-box-dashboard)); omitted on run-to-finish. |
| `IMMEDIATE DIRECTIVE` | Raw output from `evaluate_and_forecast_strategy()` §10.2 table. |
| `ENGINEER ADVICE` | What the overlay/voice use (`resolve_live_advice()` priority chain). |
| `FORECAST STOPS` | `green_flag_rest_of_race_forecast.projected_pit_schedule`. |
| `AUTO ALERT` | Whether `evaluate_auto_alert_tick()` would fire on this lap transition. |
| `PIT ROAD` | Hero has `m.pr` or `m.ps` set (servicing). |

With `--packet-json`, the full merged telemetry dict is appended after each lap.

---

## Simulation model (simplified)

| Live SDK | Simulator |
|----------|-----------|
| 22–40 car field, full timing | `field_size` cars; gaps evolve from lap-time deltas |
| Pit road geometry | `on_pit_road` / `in_stall` flags on hero |
| Caution pit herd | `fi.hd.pra`, `fi.cpi` from `CautionWindow` (+ per-lap overrides) |
| Reentry traffic | `compute_reentry_verdict()` from spread `lap_dist_pct` |
| IRSDK vars for §16 | `SimIRSDK` mock via `generate_simulated_packet_extras` + `irsdk_overrides` |
| Tactical orchestration | Real `DriverContextTracker` + `resolve_live_advice` producers |

Constants (`pit_loss_sec`, `fuel_tank_laps`, `pit_payback_laps`, tire sets) come from `RaceScenario` and match [CALCULATIONS.md §14](CALCULATIONS.md#14-constants-reference).

---

## Adding a custom scenario

Register a new `RaceScenario` in `sim/race_simulator.py`:

```python
def _scenario_my_test() -> RaceScenario:
    return _register_scenario(
        RaceScenario(
            name="my_test",
            description="What this script tests.",
            total_laps=15,
            hero_position=4,
            field_size=28,
            cautions=[CautionWindow(start_lap=6, duration_laps=2)],
            force_pit_laps={7},
            on_lap_start=my_lap_hook,       # optional (lap, field, hero) -> None
            irsdk_overrides={
                8: {
                    # Tactical synthesis keys (see table below)
                    "apex_loss": 6.0,
                    "max_tire_temp": 108.0,
                    "pra": 0.8,
                    "cpi_ll": 2,
                    # Raw IRSDK keys (override synthesis output)
                    "LFtempCM": 108.0,
                    "LapDistPct": 0.42,
                    "Speed": 51.0,
                },
            },
        )
    )
```

Add `_scenario_my_test()` to `_load_scenarios()`.

### Hooks

- `on_lap_start` — mutate field/hero before physics (gaps, tire wear, leader lap).
- `force_pit_laps` — hero begins pit service that lap (`pr`/`ps` during tick, complete end of lap).
- `irsdk_overrides` — per-lap `SimIRSDK` state and tactical synthesis inputs.

### `irsdk_overrides` keys

| Key | Layer | Purpose |
|-----|-------|---------|
| `apex_loss` | Tactical | Target apex speed loss % (maps to corner `Speed` vs 55 m/s baseline). |
| `max_tire_temp` | Tactical | Front tire temp target (`LFtempCM` / `RFtempCM`). |
| `pra` | `fi.hd` | Override herd pit ratio for caution immediate logic. |
| `cpi_ll` | `fi.cpi` | Override lead spots lost under caution. |
| `cpi_xp` | `fi.cpi` | Override projected merge position (offensive undercut net-gain tests). |
| `LapDistPct` | IRSDK | Track position (corner sector for lat-g sampling). |
| `LatAccel` | IRSDK | Lateral load for in-corner detection. |
| `Speed` | IRSDK | Corner speed for apex loss tracker. |
| `SteeringWheelAngle` | IRSDK | Steer delta for defensive-line flag. |
| `LFtempCM` / `RFtempCM` | IRSDK | Direct tire temps (override `max_tire_temp`). |
| `PlayerCarInComponentIncidentCount` | IRSDK | Incident push testing. |
| `PlayerTrackSurface` | IRSDK | Off-track / marble detection. |

Explicit IRSDK keys in a lap override win over synthesized defaults from `generate_simulated_packet_extras`.

---

## Tests

```bash
python3 -m unittest tests.test_race_simulator -v
```

Covers:

- Full lap run and log writing
- Caution pit-road state and post-pit gap rebuild
- Mid-race join bootstrap and 22–40 field clamp
- **Tactical state transitions under caution** (`tactical_caution_gate`)
- **Caution suppresses offensive undercut** (yellow on undercut lap)
- **Pace stability gate** (`undercut_pace_stable` vs `undercut_pace_unstable`)
- **Defensive mode under synthetic pressure** (`defensive_pressure` L11)
- **Green-restart target box stability** (`tactical_caution_gate` lap 4 → CHECKERED)
- **`default` run-to-finish** — no L22 phantom box or rolling forecast stops
- `generate_simulated_packet_extras` default geometry
- Tactical block present in written logs

Full suite: `python3 -m unittest discover -s tests` (includes `test_tactical_integration.py`).

---

## Limitations

- Not a physics or AI driving sim — gaps and positions are heuristic.
- No UI overlay; output is log files (and optional stdout with `-v`).
- Strategy output is only as realistic as the synthesized packets; edge cases may need manual `irsdk_overrides` or hooks.
- Does not replay recorded iRacing sessions (synthetic packets only).
- Single context poll per lap; apex baseline is seeded (not multi-sample corner replay).

For live behavior and formulas, see [CALCULATIONS.md](CALCULATIONS.md).
