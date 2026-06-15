# Calculation reference

This document explains how **AI Race Engineer** turns iRacing telemetry into fuel estimates, pit-impact projections, strategy calls, and pre-race plans. All logic is **deterministic** (no cloud AI).

Primary source files:

| Area | Module |
|------|--------|
| Telemetry ingestion & packet | `telemetry.py` |
| Live calls & forecast | `strategy_engine.py` |
| Garage / pre-race plan | `pre_race_strategy.py` |
| Track pit-loss default | `ui.py` |
| Receiver LAN snapshots | `remote_telemetry.py`, `race_memory.py` |
| Voice relay | `speech.py`, `broadcaster_ui.py` |
| Auto-alert orchestration | `auto_alert_engine.py` |
| Driver / environment context (§16) | `context_engine.py` |
| Shared scalars | `race_constants.py` |

§§1–16 = **implemented** in the repository (SDK fields may be missing per car/session; documented fallbacks apply).

---

## Data flow (high level)

```mermaid
flowchart LR
  SDK[iRacing SDK] --> TT[TelemetryTracker]
  TT --> CTX[context_engine]
  CTX --> PKT[JSON packet]
  PKT --> SE[strategy_engine]
  SE --> AA[auto_alert_engine]
  AA --> UI[Overlay / voice]
  TT --> PIT[Pit impact UI]
```

1. **Poll** (~250 ms): read SDK vars, update bounded lap history, fuel EMA, and §16 context metrics.
2. **Build packet**: compact JSON (`m` = you, `s` = session, `r` = race inputs, `fi` = field intel, `rv` = rivals).
3. **Strategy engine**: `resolve_live_advice()` → immediate pit/stay-out call + green-flag stop forecast.
4. **UI**: display advice, pit-impact line, auto alerts, voice relay.

User-adjustable inputs that feed calculations:

- **Pit loss (sec)** — time lost vs staying out (default from track length; see below).
- **Tire sets remaining** — affects whether stops are `4 TIRES` vs `FUEL ONLY`. Auto-syncs from SDK (`TireSetsAvailable` in race; `PlayerCarDryTireSetLimit` in practice/qualifying when the series caps sets).

---

## 1. Lap pace history

**Goal:** Recent pace for you and key rivals without unbounded memory.

**Who is tracked** (`_tracked_indices`):

- Your car
- Class positions **P1–P3**
- Anyone within **±2** positions of you

**Per car:** keep last **`LAP_HISTORY_DEPTH` (5)** completed lap times in a ring buffer. A lap is recorded only when `CarIdxLap` increments and `CarIdxLastLapTime > 0`.

**Pace stats** (`pace_stats`):

```
avg_last3_s = mean(last 3 lap times)
avg_last5_s = mean(last 5 lap times)
trend_s     = avg_last3_s − avg_last5_s   # positive = slowing
```

**Average lap for a car** (`_avg_lap_s_for_idx`):

```
avg = mean(last up to 3 laps)   # fallback 90.0 s if no history
```

---

## 2. Fuel burn & laps remaining

Internal math uses **liters** and **kg/h** from the SDK. The packet exposes US gallons for display/strategy.

### 2.1 Lap-boundary EMA (preferred)

On each lap boundary (`Lap` increases):

```
sample_L_per_lap = (fuel_at_prev_lap − fuel_now) / (lap_delta)
```

Sample is accepted only if:

- `lap_delta ≥ 1`
- burn `> 0.02` L
- burn `≤ 98%` of tank capacity (rejects bad refuels / noise)

EMA update (`FUEL_EMA_ALPHA = 0.45`):

```
fuel_per_lap_ema = FUEL_EMA_ALPHA × sample + (1 − FUEL_EMA_ALPHA) × previous_ema
```

Lap counter going **backward** (session reset) clears the EMA.

**After pit exit** (transition `OnPitRoad: true → false`): if `FuelLevel ≥ 88%` of tank capacity, clear the lap EMA and lap-boundary trackers so the first flying laps after a fill-up are not skewed by pit-road burn.

### 2.2 Instantaneous estimate (fallback)

`FuelUsePerHour` is **kg/h**. Convert to L/h using **`GASOLINE_KG_PER_L` (0.73 kg/L)** — Sunoco Green E15 nominal density:

```
fuel_L_per_h = FuelUsePerHour_kg_h / GASOLINE_KG_PER_L
fuel_L_per_lap_inst = fuel_L_per_h × (avg_lap_s / 3600)
```

### 2.3 Combined burn rate

Under **caution/yellow**, bypass the EMA and use instantaneous burn only (fuel-saving pace), floored at **`DEFAULT_CAUTION_BURN_L` (0.20 L/lap)**:

```
fuel_use_per_lap_est = resolve_combined_burn_rate(kg_h, avg_lap_s, ema_L, is_caution=True)
```

Under **green-flag** racing:

```
fuel_use_per_lap_est = resolve_combined_burn_rate(kg_h, avg_lap_s, ema_L, is_caution=False)
                     = max(fuel_L_per_lap_inst, fuel_per_lap_ema)
```

`r.ftl` (full-tank stint) always uses the **saved green-flag EMA** when available, not the caution-instant rate.

### 2.4 Laps of fuel left

```
laps_of_fuel_left = FuelLevel_L / fuel_use_per_lap_est
```

**Sanity clamp** (when `SessionLapsRemain` is a verified integer): if

```
laps_of_fuel_left > laps_remain × FUEL_LAPS_CLAMP_MULTIPLIER   (2×)
OR
laps_of_fuel_left > laps_remain + FUEL_LAPS_CLAMP_OFFSET        (+30)
```

then force-correct burn upward:

```
fuel_use_per_lap_est = max(fuel_use_per_lap_est, fuel_level / laps_remain)
laps_of_fuel_left    = fuel_level / fuel_use_per_lap_est
```

### 2.5 Fuel estimate quality (`fcq`)

| Quality | Condition |
|---------|-----------|
| `hi` | EMA exists and current lap ≥ 3 |
| `low` | On pit road and no EMA yet |
| `med` | Otherwise |

Packet flag `x.fe = 1` only when estimates pass plausibility checks (reasonable gal/lap, laps left &lt; 420, on-track data, etc.). If `fe = 0`, fuel-dependent fields are stripped from the packet.

### 2.6 Full-tank stint length (`r.ftl`)

```
ftl = FuelCapacity_L / fuel_use_per_lap_est
```

Discarded if `ftl < 0.8` or `ftl > 320` laps.

### 2.7 Make it to the end

```
can_make_to_end = (laps_of_fuel_left ≥ SessionLapsRemain)
laps_short      = max(0, SessionLapsRemain − laps_of_fuel_left)
```

### 2.8 Unit conversion (packet only)

```
US_gal = liters × 0.2641720523581484
lb/h   = kg/h × 2.204622621847693185
```

---

## 3. Tire stint & degradation

### 3.1 Stint length

`sl` = laps since last **exit** from pit road (transition `OnPitRoad: true → false` resets stint).

`lp` = lap number recorded at that exit (`last_pit_lap`). Strategy helpers also derive stint length as `Lap − lp` when `sl` is absent.

### 3.2 Tire wear snapshot

Corner wear = average of L/M/R tread (`LFwearL`, etc.) or `PitSv*Toffset` in the stall.

Wear is often only fresh in the pit box; off-track the packet uses **last known** wear (`tws` = stale flag).

### 3.3 Wear rate estimate

If last-known wear exists over `twsl` stint laps:

```
twr[corner] = wear_value / twsl
```

### 3.4 Pace falloff (`m.fo`)

```
falloff_s = avg_last3_s − best_lap_in_history
```

Positive = slower than your best recent lap (tire/conditions degradation proxy).

### 3.5 Pit payback laps (`m.pb`)

Triangular cumulative wear model (aligned with Section 12.3), implemented as `triangular_payback_lap()` in `race_constants.py`:

```
For lap = 1, 2, 3, …:
    cumulative += lap × falloff_s
    if cumulative >= pit_loss_sec:
        pit_payback_laps = lap
```

`falloff_s` = `m.fo` (avg_last3 − best lap). Strategy engine falls back to the same function via `_resolve_pit_payback_laps()` when `m.pb` is absent.

---

## 4. Pit window helpers

Telemetry exposes two related flags:

```
can_make_to_end (mk) = (laps_of_fuel_left ≥ SessionLapsRemain)
pit_window_open   (pw) = can_make_to_end    # same value in packet; informational
laps_until_window (pwu) = max(0, laps_remain − laps_of_fuel_left)
```

**Important:** `mk = true` means you have **enough fuel to finish the race** — it does **not** by itself mean “pit now.”

The strategy engine’s **`inside_window`** (trusted fuel) is:

```
inside_window = (mk is false)           # cannot make it to the end on fuel → must plan a stop
             OR (fuel_laps_left ≤ pb)   # within tire payback range (m.pb)
```

Untrusted fuel (`x.fe = 0` or `fcq` low): `inside_window = fuel_laps_left ≤ 2`.

---

## 5. Gap estimates

**Between two car indices** (`_gap_est_s`):

1. Prefer `CarIdxF2Time` difference if both ≥ 0.
2. Else use lap distance percent with start/finish wrap (`wrap_lap_distance_delta()`):

```
delta = dist_b − dist_a
if delta > LAP_DIST_WRAP_HALF (0.5):  delta -= 1.0
if delta < −LAP_DIST_WRAP_HALF:       delta += 1.0
gap_s = |delta| × lap_s_fallback
```

Used for `m.ga` / `m.gb` (gap ahead/behind) and green-flag pit-loss simulation.

---

## 6. Field intel (`fi`)

Built each packet for tracked cars.

### 6.1 Herd counts (`fi.hd`) — `HERD_POSITION_WINDOW` (±5 positions)

| Key | Meaning |
|-----|---------|
| `apa` | Cars ahead on **approaching pits** surface |
| `isa` | Cars ahead **in stall** |
| `apb` / `isb` | Same behind you |

### 6.2 Pitting ratios under caution (`pra`, `prb`)

Within ±`HERD_POSITION_WINDOW` class positions:

```
pra = (ahead cars pitting) / (ahead cars total)
prb = (behind cars pitting) / (behind cars total)
```

“Pitting” = `CarIdxOnPitRoad` or surface in `{approaching pits, in stall}`.

### 6.3 Reentry traffic (`fi.rej`)

Projects where on track you merge after a pit:

```
pit_frac     = (pit_loss_sec / avg_lap_s) mod 1.0
reentry_dist = (your_lap_dist_pct + pit_frac) mod 1.0
```

Count on-track cars whose `LapDistPct` is within **`REENTRY_WINDOW_PCT` (±3.5%)** of `reentry_dist`:

| Cars in window | Base verdict (`bv`) |
|----------------|---------------------|
| 0 | `CLEAN` |
| 1 | `TRAFFIC` |
| ≥ 2 | `PACK` |

**Leader lap-down risk** (`compute_reentry_verdict()` in `telemetry.py`):

```
leader_lap   = max(CarIdxLap) over field
gap_to_leader = CarIdxF2Time[player] or lap-distance fallback (§5)
projected_laps_lost = floor((pit_loss_sec − gap_to_leader) / avg_lap_s)   when pit_loss > gap
```

| Condition | Verdict `v` |
|-----------|-------------|
| `projected_laps_lost ≥ 1` | `LAPPED_DANGER` |
| else | base density verdict (`bv`) |

**Packet shape:** `fi.rej = {v, bv, n, dp, gtl?, pll?, ldw?, ll?}`

- `gtl` — gap to leader (seconds)
- `pll` — projected laps lost if pitting now
- `ldw` — lap-down cars inside reentry window
- `ll` — session leader lap count

When `x.fe = 0`, strategy uses `bv` only (ignores `LAPPED_DANGER` for pit deferral).

Live strategy **delays** a pit when window is open but verdict is `PACK` or `LAPPED_DANGER` (unless fuel critical).

### 6.4 Caution pit intel (`fi.cpi`)

See [Section 7](#7-caution-pit-impact). Compact keys: `ll` lead-lap loss, `tl` total class loss, `xp` exit class position, `gr` restart grid rows, `lda` lap-down traffic count.

---

## 7. Caution pit impact

Used for yellow-flag strategy and the **pit-impact** overlay line.

### 7.1 Lead-lap universe

```
leader_lap = max(CarIdxLap) over field
lead_positions = class positions of all cars on leader_lap (including you if on lead lap)
player_rank_lead = index of your position in sorted lead_positions + 1
```

### 7.2 Who is modeled

- **Lead-lap ahead** — on leader lap, position &lt; yours
- **Lap-down ahead** — laps &lt; leader, position &lt; yours (stay-out traffic)
- **Behind stay-out** — position &gt; yours, not pitting

### 7.3 Herd-adjusted pitting

From `hd.pra` / `hd.prb` (defaults 0.35 / 0.25 if missing):

```
stay_out_lead_ahead ≈ max(actual, round(lead_ahead_count × (1 − pra)))
pitting_lead_ahead  ≈ max(actual, round(lead_ahead_count × pra))
```

### 7.4 Exit rank & losses

```
exit_rank_lead = min(lead_field_size, stay_out_lead_ahead + pitting_lead_ahead + 1)
lost_lead      = max(0, exit_rank_lead − player_rank_lead)
```

If pit is **N laps away**, add `min(2, N)` to `lost_lead`.

```
exit_class = min(field_size, player_pos + lost_lead + cars_behind_passing)
total_lost = max(0, exit_class − player_pos)
grid_rows  = round(lost_lead × 0.75)
lda        = lap-down cars likely still ahead on track after stop
```

**Caution strategy** uses `cpi.ll` (lead-lap spots) and `hd.pra`:

- **PIT** if `ll ≤ 3` OR `pra > 0.6` (field boxing)
- **STAY OUT** otherwise (unless fuel critical)
- **PIT NOW** if fuel ≤ 1 lap under yellow

---

## 8. Green-flag pit position loss

### 8.1 Class P+1…P+5 projection

For each car **P+1 … P+5**:

```
projected_gap = gap_behind_now + pit_in_laps × (their_avg_lap − your_avg_lap)
```

If `projected_gap < pit_loss_sec`, you lose one more position:

```
lost = count of such cars
```

Receiver mode without full field sim uses a shortcut: if `gap_behind < pit_loss_sec` → `lost = 1`.

### 8.2 Leader lap-down deferral (§6.3 extension)

Used by live strategy row **3b** (before fuel-critical pit). Requires `x.fe = 1` and `fuel_laps_left > LAPPED_DANGER_FUEL_MIN_LAPS` (1.5):

```
STAY OUT when inside_window AND rej.v == LAPPED_DANGER
WHY: Delaying pit stop — green stop will put us a lap down. Extending to find cleaner window.
TRIGGER: TRACK  CONF: M
```

Voice (`speech.py`): *"Stay out, stay out. Pitting now puts us a lap down. Extend this stint."*

---

## 9. Default pit-road loss (UI)

Defined in `race_constants.py` as `get_default_pit_loss_seconds(track_name, track_length_miles)`.

When **Auto pit loss on connect** is on and you have not manually changed pit loss:

| Track type | Rule | Constant / value |
|------------|------|------------------|
| Super | Name contains Daytona/Talladega, or length ≥ `PIT_LOSS_SUPER_MIN_LENGTH_MI` (2.3 mi) | `PIT_LOSS_SUPER_SEC` = **58** |
| Short | Length ≤ `PIT_LOSS_SHORT_MAX_LENGTH_MI` (1.2 mi) | `PIT_LOSS_SHORT_SEC` = **42** |
| Intermediate | Otherwise | `PIT_LOSS_INTERMEDIATE_SEC` = **46** |

If SDK track length is missing, `resolve_track_length_miles()` supplies **1.5 mi** so unknown tracks default to intermediate (not short).

Track length is parsed from `TrackLength` (mi/km string or numeric heuristic) in `telemetry.py`.

---

## 10. Live strategy engine

Entry: `evaluate_and_forecast_strategy(telemetry)` in `strategy_engine.py`.

### 10.1 Fuel context

**Trusted** (`x.fe = 1` and `fcq ∈ {hi, med}`):

```
fuel_laps_left = m.fl
inside_window  = (m.mk is false) OR (fuel_laps_left ≤ m.pb)
fuel_critical  = fuel_laps_left ≤ 1
```

**Untrusted:**

```
fuel_laps_left = ful_liters / max(fpl, 0.2)
inside_window  = fuel_laps_left ≤ 2
fuel_critical  = fuel_laps_left ≤ 1
```

**Post-pit quiet** (`_post_pit_alert_quiet`): if stint laps since last pit exit are below:

```
min_stint_before_alert = max(POST_PIT_ALERT_MIN_STINT_LAPS, ftl − ALERT_LAP_HORIZON − 1)
```

(default `POST_PIT_ALERT_MIN_STINT_LAPS = 6`), suppress **PIT NOW** from an open window unless fuel is critical. Holds **STAY OUT** with reason “fresh stint after pit stop.”

### 10.2 Immediate decision (priority order)

| # | Condition | Action |
|---|-----------|--------|
| 1 | Pit lane closed (`flb.pcl`) | STAY OUT |
| 2 | On pit road / in stall | STAY OUT (REPAIR if `PitRepairLeft > 0`) |
| 3 | Yellow/caution | Caution rules ([§7](#7-caution-pit-impact), fuel critical) |
| 3b | `inside_window` + `rej.v == LAPPED_DANGER` + `fuel_laps_left > 1.5` + `x.fe == 1` | STAY OUT (lap-down deferral — [§8.2](#82-leader-lap-down-deferral)) |
| 4 | Fuel critical | PIT NOW |
| 4b | Offensive undercut matrix ([§16.4.3](#1643-offensive-undercut--runway-guards)) | PIT NOW |
| 5 | Pit window open + reentry `CLEAN` + not in post-pit quiet | PIT NOW |
| 5b | Pit window open + reentry `CLEAN` + post-pit quiet | STAY OUT (fresh stint) |
| 6 | Pit window open + reentry `PACK` | STAY OUT (delay 1 lap) |
| 7 | Default | STAY OUT |

After rows 1–7, **`_apply_context_directive_overrides()`** may adjust the call ([§10.5](#105-context-directive-overrides)).

**Service** on pit calls:

```
4 TIRES   if tire_sets_remaining > 0
FUEL ONLY otherwise
```

### 10.3 Green-flag rest-of-race forecast

Skipped while yellow is active (`paused_for_caution`).

Simulation state after immediate call:

- If pitting now: next lap starts with full tank stint `max_tank_stint = max(1, ftl − 1)`
- If staying out: seed fuel via `_forecast_fuel_laps_seed()`:
  - **Laps 0–3 after pit exit:** `sim_fuel = max(max_tank_stint, green_fuel_laps) − 1` (full-tank stint, not stale pre-pit burn)
  - **Otherwise:** `sim_fuel = green_fuel_laps − 1` from green-flag EMA only (Section 2.3; not caution-inflated `m.fl`)

```
green_fuel_laps = fuel_liters / m.fpe_L     # m.fpe in packet (US gal/lap); convert to L/lap for sim
```

Implemented as `green_flag_fuel_laps_from_telemetry()` in `race_constants.py`.

Each simulated lap:

```
if sim_fuel ≤ 1:
    schedule stop at sim_current_lap (4 TIRES if sets remain else FUEL ONLY)
    reset sim_fuel = max_tank_stint
sim_current_lap += 1
sim_laps_remaining −= 1
sim_fuel −= 1
```

Output: list of `{estimated_pit_lap, laps_from_now, service_required}`.

### 10.4 Trigger & confidence labels

Heuristic from WHY text and flags:

| Trigger | Typical CONF |
|---------|----------------|
| FLAGS | H |
| REPAIR | H |
| OVERTAKE (undercut active) | H |
| FUEL | H if PIT NOW, else M |
| TIRES | M (or H for cool-tires defensive) |
| TRACK (traffic/pack/lap-down deferral) | M |

### 10.5 Context directive overrides

Applied after the immediate table (still before forecast). Requires `inside_window` unless fuel critical.

| Condition | Override |
|-----------|----------|
| `ODI ≥ 0.5` and `rej == PACK` | STAY OUT — pace to pass on track |
| `ODI ≥ 0.5` and would pit (not fuel critical) | STAY OUT — defer one lap |
| `ODI < −0.3` and `pace_behind > 0.2` and **undercut runway OK** ([§16.4.3](#1643-offensive-undercut--runway-guards)) | PIT NOW — undercut threat from behind |
| `incident headroom ≤ 1` and would pit | STAY OUT — protect license |
| `incident headroom ≤ 2` and `gap_behind < 1.0 s` | STAY OUT — let pressure car through |

Appends context notes for marbles, loose steering, track temp when present.

### 10.6 Live advice resolution (`resolve_live_advice`)

`auto_alert_engine.py` and the overlay use this priority chain **before** the main strategy dashboard:

```
1. incident_push_advice()      # §16.2.1
2. tactical_undercut_advice()    # §16.4.3 — OFFENSIVE mode only
3. tactical_defensive_advice()   # §16.4.4 — DEFENSIVE mode only
4. run_strategy()              # §10.2 table + dashboard
```

Tactical calls are suppressed under caution and when `fi.sm.n` does not match the required mode.

---

## 11. Auto pit alerts

Evaluated on the engineer PC when **Auto pit alerts** is on (`ui.py` → `_check_auto_strategy()`). Triggers on **lap change** or **caution state change** (receiver: once per LAN snapshot, not duplicated on the local poll timer).

Default horizon: **`ALERT_LAP_HORIZON` = 5** laps.

### 11.1 When `should_auto_alert()` returns true

0. **Incident push** (`incident_push_advice()` not None) — always alert.
0b. **Tactical undercut** (`tactical_undercut_advice()` not None) — OFFENSIVE mode.
0c. **Tactical defensive** (`tactical_defensive_advice()` not None) — DEFENSIVE mode.
1. **Caution just started** (`caution_started` and yellow/caution flags active) — always announce the engineer call (pit / stay out), even if the call line matches a prior alert.
2. **Explicit pit timing** — parsed call is `PIT` / `PIT NOW` with `laps_until_pit ≤ N`.
3. **Fuel pressure** — `fuel_laps_left ≤ N` and (pit call, `mk` is false, or `fuel ≤ pb + 1`). **Fuel critical** (`≤ 1` lap) always alerts even during post-pit quiet.
4. **Forecast stop** — projected stop with `laps_from_now ≤ N`, unless:
   - caution **just ended** (`caution_ended` — forecast resume alone does not alert), or
   - **post-pit quiet** (Section 10.1) is active.

`evaluate_auto_alert_tick()` in `auto_alert_engine.py` calls `resolve_live_advice()` ([§10.6](#106-live-advice-resolution)) on each lap or caution transition.

### 11.2 Delivery deduplication

- **Call line** (first line: `ACTION — TIMING — SERVICE`) is the dedup key for voice and relay — not the full text (FORECAST changes every lap).
- Same call line on consecutive laps does not re-speak unless caution started.
- One caution announcement per yellow (`_auto_caution_announced` until green).
- Sim PC (`broadcaster_ui.py`) also dedupes TTS by call line.

### 11.3 Voice (sim PC)

TTS reads the **call line** and optional **WHY** only. **FORECAST** and **TRIGGER** lines are display-only (skipped by `speech.py`).

**Voice overrides** (fixed phrases, skip normal line parsing):

| Condition | Spoken text |
|-----------|-------------|
| Lap-down deferral WHY ([§8.2](#82-leader-lap-down-deferral)) | *"Stay out, stay out. Pitting now puts us a lap down. Extend this stint."* |
| Divebomb / guard inside ([§16.4.4](#1644-defensive-tactical-metrics)) | *"Divebomb threat inside, guard the entry."* |

Lap-down voice takes priority over divebomb when both match.

---

## 12. Pre-race green-flag plan

Entry: `run_pre_race_plan()` → `generate_pre_race_green_plan()`.

Used in **garage / off-track** mode (`ui_mode() == strategy`) — including when you are in **practice or qualifying** on a race server but want the **race** stint plan.

### 12.1 Race session resolution

`find_race_session()` scans embedded SessionInfo YAML (`packet.sy`) for the **Race** session, not the current session:

- `SessionType` case-insensitive `"race"`
- `SessionName` in `{RACE, MAIN RACE, FEATURE RACE, …}` (iRacing often uses uppercase `RACE`)
- Prefers the last non-skipped race session in the weekend list

Packet hints when YAML is slim:

- `s.race_lt` — race `SessionLaps` from YAML (never practice/qual lap count)
- `r.tsl` — race-weekend tire allocation (`PlayerCarDryTireSetLimit`)

Fallback synthesis (`session_yaml_from_telemetry`) uses `s.race_lt`, not `s.lt`, unless the current session is already the race.

### 12.2 Tire set limit (pre-race rules)

`race_tire_set_limit()` priority:

1. `r.tsl` / `r.ts_limit` in packet (`PlayerCarDryTireSetLimit`)
2. `WeekendInfo.WeekendOptions` tire keys
3. Race session YAML tire keys
4. `r.ts` remaining sets **only** when current session is race
5. Default **2**

SDK value **255** = unlimited (`IRSDK_TIRE_SETS_UNLIMITED`) → keep manual UI value.

### 12.3 Baseline from telemetry

| Input | Source |
|-------|--------|
| `avg_lap_time_s` | Mean of your lap history, else best lap, else rival pace avg |
| `fuel_burn_per_lap_gal` | `fpe` or `fpl`, else `fc / ftl` |
| `tire_wear_pace_falloff_per_lap_s` | `m.fo` or default **0.08** s/lap |
| `pit_lane_loss_time_s` | User `r.pl` (default 45) |
| `fuel_tank_capacity_gal` | `r.fc` |

### 12.4 Race length

From SessionInfo YAML **race** session (`find_race_session`):

- Use `SessionLaps` if valid (integer &lt; 32000; string `"unlimited"` rejected)
- Else scheduled `SessionTime` ÷ `avg_lap_time_s` (not current-session `SessionLapsTotal`)

### 12.5 Stint caps

```
fuel_stint_cap = floor(max_capacity / fuel_burn) − 1

tire_stint_cap: smallest lap L where sum_{i=1..L}(i × tire_falloff) > pit_loss + TIRE_COST_THRESHOLD_BUMP
                (cumulative triangular wear cost)

stint_length = min(fuel_stint_cap, tire_stint_cap)
```

### 12.6 Stop count & schedule

```
total_stops = floor(total_laps / stint_length)
if total_laps % stint_length == 0 and total_stops > 0:
    total_stops -= 1   # exact fit → one fewer stop

Pit on laps: stint_length, 2×stint_length, … 
Service BOTH if fuel-limited stint or tire sets allow; else FUEL ONLY or 4 TIRES
```

NOTE: ~{total} race laps at {track}; plan assumes green-flag run — adjust for cautions

Formatted output lines: FUEL / TIRES / STOPS / NOTE / TRIGGER.

---

## 13. Receiver (engineer PC) differences

- Telemetry arrives as **LAN snapshots** (~250 ms); `RemoteTelemetry` overlays your pit loss and tire sets on the cached packet.
- Auto alerts run on snapshot ingest only (not also on the local telemetry poll) to avoid duplicate triggers.
- **Caution pit impact** reuses broadcaster-computed `fi.cpi` when present.
- **Green-flag loss** uses gap-behind shortcut when full field projection is unavailable.
- `RaceMemory` rolls snapshots into lap rollups, caution/pit events, and trend features (`packet.h`) — does not change core strategy formulas above.
- Voice is relayed to the sim PC when **Speak on sim PC** is on; engineer PC does not speak locally while linked.

---

## 14. Constants quick reference

All alignment scalars live in **`race_constants.py`** (GridNotes v1.0.x — Section 14):

| Parameter | Constant | Value | Used in |
|-----------|----------|-------|---------|
| Sunoco E15 density | `GASOLINE_KG_PER_L` | 0.73 kg/L | `race_constants.py` |
| Zero-flow safeguard | `MIN_FUEL_USE_KG_PER_H` | 0.05 kg/h | `race_constants.py` |
| Caution burn floor | `DEFAULT_CAUTION_BURN_L` | 0.20 L/lap | `race_constants.py` |
| S/F wrap divider | `LAP_DIST_WRAP_HALF` | 0.5 | `race_constants.py` |
| Fuel EMA α | `FUEL_EMA_ALPHA` | 0.45 | `telemetry.py` |
| Pace history ring buffer | `LAP_HISTORY_DEPTH` | 5 | `telemetry.py` |
| Default lap fallback | `DEFAULT_AVG_LAP_S` | 90.0 s | `telemetry.py` |
| Min lap time clamp | `MIN_AVG_LAP_S` | 1.0 s | `race_constants.py` |
| Reentry traffic window | `REENTRY_WINDOW_PCT` | 0.035 (3.5%) | `telemetry.py` |
| Herd tracking window | `HERD_POSITION_WINDOW` | ±5 positions | `telemetry.py` |
| Auto alert horizon | `ALERT_LAP_HORIZON` | 5 laps | `strategy_engine.py`, `ui.py` |
| Post-pit alert quiet min | `POST_PIT_ALERT_MIN_STINT_LAPS` | 6 laps | `race_constants.py`, `strategy_engine.py` |
| Unlimited tire sets sentinel | `IRSDK_TIRE_SETS_UNLIMITED` | 255 | `race_constants.py`, `telemetry.py` |
| Pre-race tire cost buffer | `TIRE_COST_THRESHOLD_BUMP` | 30.0 s | `pre_race_strategy.py` |
| Fuel laps clamp multiplier | `FUEL_LAPS_CLAMP_MULTIPLIER` | 2.0 | `telemetry.py` |
| Fuel laps clamp offset | `FUEL_LAPS_CLAMP_OFFSET` | 30.0 laps | `telemetry.py` |
| Super pit loss | `PIT_LOSS_SUPER_SEC` | 58.0 s | `race_constants.py` → `ui.py` |
| Short pit loss | `PIT_LOSS_SHORT_SEC` | 42.0 s | `race_constants.py` → `ui.py` |
| Intermediate pit loss | `PIT_LOSS_INTERMEDIATE_SEC` | 46.0 s | `race_constants.py` → `ui.py` |
| Lap-down deferral fuel floor | `LAPPED_DANGER_FUEL_MIN_LAPS` | 1.5 laps | `race_constants.py` |
| Undercut runway (session + stint) | `MIN_UNDERCUT_RUNWAY_LAPS` | 8 laps | `race_constants.py` |
| Undercut min stint laps | `MIN_UNDERCUT_STINT_LAPS` | 5 laps | `race_constants.py` |
| Offensive gap ahead max | `UNDERCUT_GAP_AHEAD_MAX_SEC` | 1.0 s | `race_constants.py` |
| Rival pace delta min (deg proxy) | `UNDERCUT_RIVAL_PACE_DELTA_MIN` | 0.12 s | `race_constants.py` |
| ODI stay-out / undercut thresholds | `ODI_STAY_OUT_THRESHOLD` / `ODI_UNDERCUT_THRESHOLD` | 0.5 / −0.3 | `race_constants.py` |
| ODI pack / draft penalties | `ODI_PACK_PENALTY` / `DRAFT_STREAK_ODI_PENALTY` | 0.5 / 0.3 | `race_constants.py` |
| Rival deg trend threshold | `RIVAL_DEGRAD_TREND_MIN` | 0.15 s/lap | `race_constants.py` |
| ODI rival-degrad multiplier | `ODI_RIVAL_DEGRAD_MULT` | 0.85 | `race_constants.py` |
| Strategy mode — defensive gap | `MODE_DEFENSIVE_GAP_BEHIND_MAX` | 0.5 s | `race_constants.py` |
| Strategy mode — offensive gap ahead | `MODE_OFFENSIVE_GAP_AHEAD_MAX` | 0.7 s | `race_constants.py` |
| Strategy mode — offensive gap behind min | `MODE_OFFENSIVE_GAP_BEHIND_MIN` | 0.8 s | `race_constants.py` |
| Thermal warm / greasy | `THERMAL_WARM_C` / `THERMAL_GREASY_C` | 95 / 105 °C | `race_constants.py` |
| Apex loss alert threshold | `APEX_LOSS_DEFEND_PCT` | 5.0 % | `race_constants.py` |
| Divebomb gap / closing rate | `DIVEBOMB_GAP_MAX` / `DIVEBOMB_CLOSING_RATE` | 0.4 s / −0.2 s/s | `race_constants.py` |
| Divebomb voice cooldown | `DIVEBOMB_VOICE_COOLDOWN_S` | 8.0 s | `race_constants.py` |
| Tactical alert cooldown | `TACTICAL_ALERT_COOLDOWN_LAPS` | 2 laps | `race_constants.py` |

Other module-local values:

| Constant | Value | Where |
|----------|-------|--------|
| Caution pit delay bump | max 2 laps | `telemetry.py` |
| Request watchdog | 30 s | `ui.py` |

Shared helpers in `race_constants.py`: `clamp_fuel_use_kg_h()`, `clamp_nonneg_liters()`, `clamp_avg_lap_seconds()`, `clamp_lap_distance_pct()`, `resolve_combined_burn_rate()`, `green_flag_fuel_laps_from_telemetry()`, `triangular_payback_lap()`, `wrap_lap_distance_delta()`, `cumulative_triangular_wear_cost()`.

Pre-race helpers in `pre_race_strategy.py`: `find_race_session()`, `race_lap_total_from_yaml()`, `race_tire_set_limit()`.

Strategy helpers in `strategy_engine.py`: `_laps_since_pit_stop()`, `_post_pit_alert_quiet()`, `_forecast_fuel_laps_seed()`, `advice_call_line()`, `resolve_live_advice()`, `tactical_undercut_advice()`, `tactical_defensive_advice()`.

Context helpers in `context_engine.py`: `evaluate_strategy_mode()`, `calculate_rolling_trend()`, `compute_overtake_difficulty_index()`, `adjust_tire_stint_cap_for_track_temp()`.

Reentry helpers in `telemetry.py`: `compute_reentry_verdict()`.

---

## 15. Output format (engineer call)

**Live mode** uses a multi-section **dashboard** (`format_strategy_dashboard()`):

```
================================================================================
STRATEGY CALL :   [ ACTION ]   ·   TARGET PIT: LAP N   ·   SERVICE TYPE: …
 WHY           :   HEADLINE …
                   (optional detail / reentry / context notes)
================================================================================
 MACHINE STATE :   CURRENT LAP … · FUEL LEFT … · TARGET FUEL BURN …
--------------------------------------------------------------------------------
 PERFORMANCE   :   LAP PACE … · INPUT SMOOTHNESS … · INCIDENTS …
================================================================================
 ENVIRONMENT   :   DRAFT … · TRACK TEMP … · GAP AHEAD …
================================================================================
FORECAST: …                                    # display only; not spoken
TRIGGER: FUEL|FLAGS|OVERTAKE|…  CONF: H|M|L    # display only; not spoken
```

Legacy 4-line format (`ACTION — THIS LAP — SERVICE`) is still parsed for dedup and relay.

Voice reads the **STRATEGY CALL** action block and optional **WHY** headline. See [§11.3](#113-voice-sim-pc) for fixed voice overrides.

**Garage / strategy mode** (5 lines):

```
FUEL: ~X laps/tank · Y-lap stints
TIRES: ~X laps/set · N planned stop(s)
STOPS: L27 BOTH; L54 BOTH; ...
NOTE: ~L race laps at Track; green-flag assumption
TRIGGER: FUEL|TIRES  CONF: M
```

---

## 16. Driver context & environment extensions

Implemented in `context_engine.py` (`DriverContextTracker`), merged into each telemetry packet by `telemetry.py`, and consumed by `strategy_engine.py` / `auto_alert_engine.py`. Building blocks: `m.twr` / `twsl` (§3), `pace_stats` (§1), `fi.rej` (§6.3), fuel EMA (§2), rival pace (§1).

---

### 16.1 Dynamic track & environmental evolution

Stint caps and call timing are temperature- and line-dependent when SDK data is available.

#### 16.1.1 Track temperature vs. tire wear rate

**SDK inputs:** `TrackTempCrew` / `TrackTemp` (`s.ttc` / `s.tt`), corner wear in pit box (`twl[].tt`).

**Temperature-adjusted stint cap** (`adjust_tire_stint_cap_for_track_temp()`):

```
delta_T = T_current − T_ref
If |delta_T| < TRACK_TEMP_SHIFT_THRESHOLD_C (10°C): cap unchanged
If delta_T ≤ −10°C: cap × (1 + TRACK_TEMP_COOL_STINT_BONUS × |delta_T| / 10)   # +5% per 10°C cool
If delta_T ≥ +10°C: cap × (1 − TRACK_TEMP_HOT_STINT_PENALTY × delta_T / 10)    # −8% per 10°C hot
```

**Packet shape:** `s.tenv = {ref, cur, dt?, tsc?}` where `tsc` is the adjusted stint cap.

#### 16.1.2 Marbles / off-line accumulation

**Triggers:** `LatAccel` spike above threshold, or `PlayerTrackSurface == off-track`.

```
marble_laps_remaining = MARBLE_LAPS_REMAINING (2) after each event
Decays −1 per completed green lap in clean air
```

**Packet shape:** `m.mar = {lr: laps_remaining}`.

Strategy appends a WHY note when marbles are active; `TRIGGER: TIRES`, `CONF: M`.

---

### 16.2 Driver consistency & psychology (incident tracker)

#### 16.2.1 Incident point tracking

**SDK inputs:** `PlayerCarInComponentIncidentCount`, session incident limit when known.

```
off_track_events = sum of component increments in last INCIDENT_WINDOW_LAPS (3)
license_headroom = incident_limit − session_incidents
```

| Condition | Effect |
|-----------|--------|
| `off_track_events ≥ 2` in 3 laps | `incident_push_advice()` — *"Pushing too hard — back it down 2%."* |
| `license_headroom ≤ 2` and `gap_behind < 1.0 s` | Bias **STAY OUT** (§10.5) |
| `license_headroom ≤ 1` | Suppress aggressive **PIT NOW** unless fuel critical |

**Packet shape:** `m.inc = {ot, hr?, tot?}`.

#### 16.2.2 Steering input smoothness (micro-correction delta)

High-rate `SteeringWheelAngle` samples in the active corner sector; per-lap std dev vs stint baseline:

```
delta_steer = steer_std / max(steer_baseline, ε)
```

When `delta_steer > 1.4`, forecast may pull tire stop forward 1–2 laps (`forecast_tire_pull_laps()`).

**Packet shape:** `m.drv = {ss, ssr, al?, ts, tsn}` — see also §16.4.4 for `al` (apex loss) and `ts`/`tsn` (thermal).

---

### 16.3 Strategy mode orchestration

Evaluated at the start of each `DriverContextTracker.poll()` via `evaluate_strategy_mode()`:

| Mode | Condition | Tactical metrics enabled |
|------|-----------|--------------------------|
| `DEFENSIVE` | `0 < gap_behind < MODE_DEFENSIVE_GAP_BEHIND_MAX` (0.5 s) | `fi.tac`, apex / thermal / divebomb |
| `OFFENSIVE` | `0 < gap_ahead < MODE_OFFENSIVE_GAP_AHEAD_MAX` (0.7 s) **and** `gap_behind ≥ MODE_OFFENSIVE_GAP_BEHIND_MIN` (0.8 s) | `fi.odi.uc`, `fi.odi.rd` |
| `BALANCED` | otherwise | Macro strategy only (fuel, reentry, forecast) |

Priority: **DEFENSIVE → OFFENSIVE → BALANCED**.

**Packet shape:** `fi.sm = {m: 0|1|2, n: "BALANCED"|"OFFENSIVE"|"DEFENSIVE"}`.

Tactical advice producers (`tactical_undercut_advice`, `tactical_defensive_advice`) require matching `fi.sm.n` ([§10.6](#106-live-advice-resolution)).

---

### 16.4 Advanced traffic & drafting logic

Extends §6 (field intel) and §8 (green-flag loss) with fuel and pace context.

#### 16.4.1 Drafting / aerodynamic wake

```
in_draft = gap_ahead < DRAFT_GAP_SEC (1.5 s) AND not passing AND on_track
```

On lap complete: if drafted, skip next fuel EMA sample and increment `draft_streak`.

**Packet shape:** `fi.draft = {on, streak, ex}` (`ex` = laps excluded from EMA).

#### 16.4.2 Relative pace dynamic (overtake difficulty index)

**Inputs:** rival `avg_last3_s`, your `pc.avg_last3_s`, `fi.rej.v`, `fi.draft.streak`, rival degradation trend.

```
pace_delta_ahead  = your_avg3 − ahead_avg3
pace_delta_behind = behind_avg3 − your_avg3

ODI = pace_delta_ahead
      − ODI_PACK_PENALTY (0.5)   if rej.v == PACK
      − DRAFT_STREAK_ODI_PENALTY (0.3)   if draft_streak ≥ 3
      × ODI_RIVAL_DEGRAD_MULT (0.85)   if rival_degrad > RIVAL_DEGRAD_TREND_MIN and wear stable
```

**Strategy overrides** ([§10.5](#105-context-directive-overrides)) when `inside_window`:

| Condition | Call |
|-----------|------|
| `ODI ≥ ODI_STAY_OUT_THRESHOLD` and `rej == PACK` | **STAY OUT** |
| `ODI ≥ ODI_STAY_OUT_THRESHOLD` and would pit (not fuel critical) | Defer pit 1 lap |
| `ODI < ODI_UNDERCUT_THRESHOLD` and `pace_behind > 0.2` and runway OK | **PIT NOW** — undercut threat |

**Packet shape:** `fi.odi = {pa, pb, score, uc?, rd?}` — `uc`/`rd` only emitted in OFFENSIVE mode.

#### 16.4.3 Offensive undercut & runway guards

**Runway guard** (`_undercut_runway_ok()`): all must pass before any undercut call (immediate row 4b, tactical advice, or defensive undercut override):

```
laps_remain ≥ MIN_UNDERCUT_RUNWAY_LAPS (8)
fuel_runway (ftl − stint_laps) ≥ MIN_UNDERCUT_RUNWAY_LAPS
stint_laps ≥ MIN_UNDERCUT_STINT_LAPS (5)
```

**Immediate PIT NOW** (`_undercut_opportunity()`): `inside_window`, `rej == CLEAN`, not fuel critical, `0 < gap_ahead < UNDERCUT_GAP_AHEAD_MAX_SEC`, runway OK, and either:

- `fi.odi.uc == true` in OFFENSIVE mode (draft undercut predictor), or
- rival tire decay proxy: `pace_delta_ahead ≥ UNDERCUT_RIVAL_PACE_DELTA_MIN` (0.12 s)

**Draft undercut predictor** (`_tick_undercut_predictor()`), OFFENSIVE only:

```
projected_fresh_out = best_lap_or_opponent_pace + 0.15 × (pit_loss / fresh_out)
undercut_margin = (opponent_pace − projected_fresh_out) − gap_ahead
fi.odi.uc = true when in_draft AND pace_delta_ahead > 0 AND undercut_margin > 0
```

**Tactical call** (`tactical_undercut_advice()`): separate high-priority **PIT NOW** with `TRIGGER: OVERTAKE`, `CONF: H` when `uc` is set and runway/reentry gates pass.

**Rival degradation** (`calculate_rolling_trend()` on car-ahead lap history):

```
fi.odi.rd = lap-over-lap trend (s/lap); positive = rival slowing
Biases ODI score down (stay-out) when rd > RIVAL_DEGRAD_TREND_MIN
```

#### 16.4.4 Defensive tactical metrics

Computed only in **DEFENSIVE** mode; alerts respect `TACTICAL_ALERT_COOLDOWN_LAPS` (2) per alert type.

| Metric | Detection | Packet / call |
|--------|-----------|---------------|
| **Apex speed loss** | Peak speed baseline vs current in corner; loss > `APEX_LOSS_DEFEND_PCT` (5%) + steer delta | `m.drv.al`; `fi.tac.def_line` → ADJUST DEFENSIVE LINE |
| **Thermal stress** | Max LF/RF temp: WARM > 95°C, GREASY > 105°C | `m.drv.ts` / `tsn`; `fi.tac.cool` → COOL TIRES |
| **Divebomb** | Brake zone (`LapDistPct` 0.06–0.20), `gap_behind < DIVEBOMB_GAP_MAX`, closing rate < `DIVEBOMB_CLOSING_RATE` | `fi.tac.db` → GUARD INSIDE; voice override ([§11.3](#113-voice-sim-pc)) |

**Packet shape:** `fi.tac = {def_line?, cool?, db?}` (DEFENSIVE mode only).

---

### 16.5 Implementation notes

| Topic | Depends on | Notes |
|-------|------------|-------|
| Track temp wear | `twl` + `s.ttc` | Coefficients in `race_constants.py`; tune per series |
| Marbles | LatAccel + surface | May false-positive on street circuits |
| Incidents | Component incident array | License limit optional in session YAML |
| Steering std | 10 Hz sector sampling | Per-track corner sector from highest LatAccel |
| Draft EMA skip | Gap + lap dist | Prevents draft-skewed fuel projection |
| ODI / undercut | Rival pace buffers | Requires stable `rv.ahead` link |
| Strategy mode | `m.ga`, `m.gb` | Re-evaluated every poll tick |

Constants for §16 live in `race_constants.py`; engineer-call text and priority tables in `strategy_engine.py`, matching §10–§11.

---

*This file describes behavior as implemented in the repository (§§1–16). iRacing SDK availability varies by car/session; when data is missing, documented fallbacks apply.*
