# Features

AI Race Engineer is an always-on-top overlay for iRacing that turns live telemetry into pit and strategy calls. All advice is produced by a **local deterministic engine** — no cloud AI, no API keys required for strategy.

Related docs: [CALCULATIONS.md](CALCULATIONS.md) (formulas and decision rules), [SIMULATIONS.md](SIMULATIONS.md) (offline testing).

---

## Deployment modes

| Mode | Where it runs | Best for |
|------|---------------|----------|
| **Local** | Single PC with iRacing | Solo testing, one-machine setup |
| **Broadcaster** | Sim PC (beside iRacing) | Streams telemetry; can speak calls in the headset |
| **Receiver** | Engineer PC on the LAN | Runs the overlay and strategy engine; connects to the sim PC |

A startup role picker chooses broadcaster vs receiver. Pass `--role local`, `--role broadcaster`, or `--role receiver` to skip the dialog.

Packaged builds (Windows `.exe`, macOS `.app`) support double-click launch — see the README **Packaging** section.

---

## Live race engineer

### Telemetry and field tracking

- Connects to iRacing via the SDK (`pyirsdk`) and polls telemetry ~4× per second.
- Tracks recent lap times (last 5 laps) for you, top 3, and cars within ±2 positions.
- Builds a compact JSON packet: position, fuel, laps remaining, flags, pit inputs, and rival pace.
- Auto-syncs **tire sets remaining** from the SDK when the series limits sets.
- **Clean-lap pace gate**: rolling stddev of clean laps before offensive undercut calls (avoids gambling on unstable pace).
- **Track pit-loss database**: SQLite cache (`~/.ai_race_engineer_tracks.db`) with measured pit loss and curated seeds; auto-applied on connect when enabled.
- **Dynamic corner sectors**: practice laps learn peak lateral-g sector per track for defensive context sampling.
- **Predictive tire falloff**: `effective_tire_falloff_s()` blends pace falloff with thermal, steering fatigue, track temp, and oval stagger (`m.foe` in packet).
- **GWC fuel reserve**: on late-race ovals, +1 lap fuel critical threshold when few laps remain (green-white-checker prep).

### Instant pit / stay-out calls

On **Analyze** (button or hotkey), the engine returns:

- **Action line** — e.g. pit this lap, stay out, fuel only, four tires.
- **WHY** — short rationale tied to fuel, tires, traffic, or caution context.
- **FORECAST** — projected green-flag stop laps and service for the rest of the race.
- **TRIGGER / CONF** — which rule fired and confidence hints where applicable.

### Auto pit alerts

- Optional alerts on every lap and on caution transitions when a stop is due within 5 laps.
- Uses the same call text as manual Analyze (tested in the unit suite).
- Dedupes repeated calls so the overlay does not spam the same line.

### Tactical advice (driver context)

Beyond fuel-and-stops math, the engine evaluates:

- **Offensive undercut** — car ahead on degrading tires, runway and pace-stability gates.
- **Undercut yourself** — stretch a stint to force a rival behind to pit first.
- **Defensive undercut threat** — pressure from a car behind while leading.
- **Incident / push advice** — yellow-flag and incident-driven stay-out or pit guidance.
- **Lapped-car danger** — stay-out warnings when rejoin traffic is risky.
- **Caution-aware logic** — leader stretch, track position under yellow, reentry verdict.

Driver context (gaps, draft, apex loss, thermal stress, divebomb signals) feeds these rules via the context engine.

### Pit impact line

After a pit call, an optional **pit impact** summary shows estimated positions lost/gained on pit road under green or caution (toggle in preferences).

---

## Pre-race strategy

### Garage plan (GET RACE STRATEGY)

In the garage, **GET RACE STRATEGY** builds a full green-flag plan from SessionInfo YAML plus your pace and fuel baselines:

- Stint length, stop count, and lap-by-lap pit schedule.
- Fuel-only vs four-tire service where rules apply.

### Simulation-based pre-race plan

The pre-race engine also runs **three caution-density branches** (light / moderate / heavy) through the offline race simulator:

- Projects finish position and pit schedule under different yellow counts.
- Seeds the field from **qualifying / practice results** when available (grid pace spread).
- Shows grid pace summary (P1 vs your delta).

### Strategy compare (local mode)

When you fetch a pre-race plan then go on track:

- **BASE PLAN** (left) — pinned garage strategy for the session.
- **LIVE CALL** (right) — updates on Analyze and auto-alerts.
- Base plan clears on disconnect/reconnect; live side resets when leaving the garage.
- Pinned BASE PLAN is saved to the session `.meta.json` sidecar when session recording is active.

---

## Post-race analytics

After a session with `"session_recording": true`:

| Tool | What it shows |
|------|----------------|
| **REVIEW SESSION** (overlay) | Executive summary, optional BASE PLAN panel, lap timeline (pos, fuel, mode, thermal, replayed calls) |
| `scripts/post_race_report.py` | Markdown or JSON report: pit position deltas, stint pace σ, stop timing vs plan |
| `scripts/replay_session.py` | Lap-by-lap replay through the production strategy engine |

Reports compare actual pit laps to the pinned garage plan (`L12`, `lap N` patterns). **Executive summary** is template-based (no LLM required); export JSON for optional offline rewriting.

See [CALCULATIONS.md §17](CALCULATIONS.md#17-post-race-session-analytics) for metric definitions.

---

## Overlay and controls

- Always-on-top Qt overlay with dark theme; receiver mode uses an expanded sidebar layout.
- **ANALYZE FIELD & ADVISE** — live pit/strategy call.
- **GET RACE STRATEGY** — pre-race plan (garage).
- **SAVE PACKET** — writes current telemetry JSON to `sim_logs/packets/` (local/sim PC only).
- **REVIEW SESSION** — opens post-race strategy review for the latest JSONL recording (local mode).
- **Clear / Cancel** — resets the live message and cancels in-flight requests.
- **30s watchdog** — re-enables Analyze if a request stalls.
- Configurable **auto-clear** timer for how long advice stays on screen.

### User inputs

- **Tire sets left** — manual override; auto-updates from SDK when available.
- **Pit loss (sec)** — manual override; optional auto-apply from track length.
- **Apply track default** — one-click pit-loss estimate from track mileage.

### Hotkeys

- In-app **Space** (default) triggers Analyze.
- Optional global hotkey via `pynput` (macOS requires Accessibility permission).
- **Ctrl+Shift+S** saves a telemetry packet snapshot (local mode).

Settings persist to `~/.ai_race_engineer.json`. Per-track pit loss and learned corner sectors persist to `~/.ai_race_engineer_tracks.db`.

---

## Voice

- **Voice enabled** — reads finished calls on this PC (local mode).
- **Speak on sim PC** — relays calls to the broadcaster for headset TTS.
- **Speak WHY after call** — optional second line with the rationale.
- TTS backends: Windows neural voice (`edge-tts`) or SAPI; macOS `say`; Linux `espeak`.

---

## Two-machine LAN setup

- **Broadcaster** listens on the LAN and pushes telemetry snapshots.
- **Receiver** discovers sim PCs on the network and connects over TCP.
- Strategy runs on the engineer PC; **RaceMemory** accumulates long-horizon context from streamed snapshots.
- Voice can play on the sim PC while advice displays on the engineer PC.

---

## Offline simulation and developer tools

- **Race simulator** — replays scenarios without iRacing; exercises the same strategy, context, auto-alert, and speech paths as live sessions.
- **Sim fuel EMA parity** — offline sim injects `m.ful` / `m.fpe` and purges on yellow lift (matches live forecast path).
- **Session replay** — record laps to `sim_logs/sessions/*.jsonl` (`session_recording` in config); replay via `scripts/replay_session.py`.
- **Post-race report** — deterministic analytics (pit position deltas, stint σ, stop timing vs BASE PLAN) via `scripts/post_race_report.py`.
- **Strategy review dashboard** — local-mode **REVIEW SESSION** button opens a lap timeline with replayed LIVE CALLS and optional pinned base plan (`.meta.json` sidecar).
- **Executive summary** — template-based race recap from report metrics (no LLM required; export via markdown/JSON).
- **Pre-race sim CLI** — `scripts/pre_race_sim.py --packet <json>` for caution-branch plans from saved packets.
- **Run all scenarios** — `scripts/run_all_simulations.py` batch regression.
- **Unit tests** — broad coverage of strategy outputs, auto-alerts, pre-race sim, session reports, and telemetry gates (`python3 -m unittest discover -s tests`).

---

## What it does not do

- No cloud LLM or paid API for race calls — strategy is rule-based and deterministic.
- No automatic car control — advice only; you still drive and pit.
- iRacing remains Windows-only; Mac builds are intended for receiver/engineer PC use, not running the sim.
