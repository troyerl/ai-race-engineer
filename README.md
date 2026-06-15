# ai-race-engineer

A small always-on-top iRacing overlay that builds a compact strategy snapshot from live telemetry and runs a **local deterministic race-engineer engine** (no cloud AI).

## What it does

- **Collects recent pace**: keeps the last ~5 lap times for you + a small slice of the field.
- **Builds a compact JSON packet**: your position, fuel, laps remaining, flags, plus nearby/top competitors’ recent lap times.
- **Runs a local strategy engine**: instant pit/stay-out calls plus a **green-flag rest-of-race forecast** (projected stop laps and service).
- **Pre-race green-flag plan**: in the garage, **GET RACE STRATEGY** builds fuel/tire stint length, stop count, and lap-by-lap pit schedule from SessionInfo YAML + your pace/fuel baselines.
- **Auto pit alerts**: every lap and under caution, shows and speaks pit calls when a stop is due within 5 laps (toggle in ADVICE preferences).
- **Prevents “stuck forever” UI**:
  - Request timeout watchdog re-enables the Analyze button if a request stalls.
  - Clear/Cancel immediately resets the UI and stops applying late results.

## Repository layout

```
main.py                 # Qt app entrypoint
race_simulator.py       # CLI shim → sim/race_simulator.py
build_windows.bat       # Windows PyInstaller build (delegates to scripts/)
build_mac.sh            # macOS PyInstaller build (delegates to scripts/)
ai_race_engineer.spec   # PyInstaller spec (Windows folder + macOS .app)

engineer/               # Core app: strategy, telemetry, UI, networking
  strategy_engine.py    # Deterministic pit/strategy rules
  strategy_worker.py    # Engine worker thread
  telemetry.py          # iRacing SDK tracking + packet builder
  context_engine.py     # §16 tactical driver context
  ui.py                 # Overlay UI
  …

sim/                    # Offline race simulation
  race_simulator.py     # Field model + scenario CLI

tests/                  # unittest suite (169 tests)
docs/
  CALCULATIONS.md       # Formula & decision reference
  SIMULATIONS.md        # Offline sim guide
assets/                 # icon.png, icon.ico, UI SVGs
scripts/                # make_icon_ico.py, build_windows.bat, build_mac.sh
```

## Requirements

- Python 3.10+ recommended
- iRacing running (and the iRacing SDK accessible to Python via `pyirsdk` — imported as `irsdk` in code)

Python packages used:
- `PySide6`
- `pyirsdk` (PyPI name; `import irsdk` in code)
- Optional: `python-dotenv` (loads `.env` on startup)
- Optional: `pynput` (global hotkey while iRacing has focus; macOS needs Accessibility)

Install everything:

```bash
pip install -r requirements.txt
```

Or manually:

```bash
pip install PySide6 pyirsdk python-dotenv pynput
```

Or with a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Settings are saved to `~/.ai_race_engineer.json` (hotkey, voice, pit loss, etc.).

Optional: create a `.env` file if you use `python-dotenv` for other local secrets (not required for the strategy engine).

### Unit tests (strategy & calculations)

Run the calculation, strategy, and **auto-alert display trigger** test suite with the standard library (no extra test dependencies):

```bash
python3 -m unittest discover -s tests -v
```

`tests/test_advice_outputs.py` asserts the **full formatted text** (call line, WHY, FORECAST, TRIGGER/CONF) for every live scenario. `tests/test_auto_alert_engine.py` verifies the **same text** is what gets delivered when auto-alerts fire.

## Local testing (single PC)

Use this path when you want to try the app on **one machine** with iRacing and the engineer overlay together — no LAN, no second PC.

### 1. Prerequisites

- Python 3.10+
- iRacing installed and able to enter a session (test drive, practice, or race)
- Dependencies installed (see **Requirements** above)

### 2. Start iRacing first

Launch iRacing and load into a session (garage, test drive, or on track). The SDK only provides telemetry once the sim is running.

### 3. Run local mode

Skip the startup role picker and open the single-PC overlay:

```bash
python3 main.py --role local
```

You should see the overlay window. The status badge should change from **iRacing: Offline** to **iRacing: Online** once telemetry connects.

### 4. Quick functional test

1. Drive a few laps so the app has lap times and fuel data.
2. Set **Pit loss (sec)** and **New tire sets left** if needed (or enable auto pit-loss in preferences).
3. Click **ANALYZE FIELD & ADVISE** (or press **Spacebar** when the overlay is focused).
4. Confirm the engineer call appears (action, timing, service, and WHY line).
5. Click **CLEAR / CANCEL** to reset and try again.

Optional checks:

- **Voice enabled** (preferences): reads the finished call aloud on this PC.
- **Show pit impact**: after a PIT call, a caution-aware pit-road impact line appears under the advice.
- **Hotkey**: Space works in-app always; with `pynput` installed, Space can work globally (on macOS, grant **Accessibility** to your terminal or Python in System Settings → Privacy & Security).

### 5. Config file

Settings are saved to `~/.ai_race_engineer.json`. Delete that file to reset preferences.

### Local troubleshooting

| Symptom | What to try |
|---------|-------------|
| **iRacing: Offline** | Start iRacing before the overlay; restart the overlay after joining a session. |
| **Analyze does nothing / times out** | Watch the terminal for errors; try **CLEAR / CANCEL** and analyze again. |
| **No global hotkey on Mac** | Install `pynput` and enable Accessibility for the app running Python. |
| **trace trap on Mac at startup** | Usually a `pynput`/Accessibility issue — the in-app Space shortcut still works. |

### Testing the two-machine setup locally

You can also simulate broadcaster + receiver on one computer (useful before splitting across two PCs):

**Terminal 1 — broadcaster (needs iRacing running):**

```bash
python3 main.py --role broadcaster
```

**Terminal 2 — receiver:**

```bash
python3 main.py --role receiver
```

On the receiver, pick the broadcaster from the LAN device list. Advice is generated on the receiver; calls can be spoken on the broadcaster if voice relay is enabled.

## Running the app

From the project folder on either machine:

```bash
python3 main.py
```

A startup dialog asks whether this PC is the **Sim PC (Broadcaster)** or **Engineer PC (Receiver)**.

| Role | Machine | What it does |
|------|---------|----------------|
| **Broadcaster** | Sim PC (iRacing) | Streams telemetry; speaks engineer calls aloud |
| **Receiver** | Engineer PC | Connects over LAN; runs AI advice overlay |

Advanced: pass `--role local`, `--role broadcaster`, or `--role receiver` to skip the picker. Single-PC mode (`--role local`) runs iRacing + AI on one machine.

## Packaging (Windows & Mac)

PyInstaller builds are **OS-specific**: build the Windows `.exe` on Windows and the Mac `.app` on macOS. Each produces a double-click launcher — no Python install required on the target machine.

### Windows (Sim PC or Engineer PC)

From the project folder on **Windows**:

```bat
build_windows.bat
```

Output:

- `dist\AI Race Engineer\AI Race Engineer.exe` — double-click or pin to the taskbar

Optional Desktop shortcut after a successful build:

```bat
powershell -ExecutionPolicy Bypass -File scripts\create_windows_shortcut.ps1
```

The script installs dependencies, generates `assets/icon.ico`, and runs PyInstaller via `ai_race_engineer.spec`.

Notes:

- The app uses `assets/icon.png` at runtime (Qt window/app icon).
- iRacing SDK package is **`pyirsdk`** on PyPI (`import irsdk` in code).

### Mac (Engineer PC / receiver UI)

From the project folder on **macOS**:

```bash
chmod +x build_mac.sh   # first time only
./build_mac.sh
```

Output:

- `dist/AI Race Engineer.app` — double-click, or drag to **Applications** / the **Dock**

Quick launch after building:

```bash
open "dist/AI Race Engineer.app"
```

Notes:

- For the **global Space hotkey** while iRacing has focus, grant **Accessibility** to **AI Race Engineer** in System Settings → Privacy & Security (same as running from source with `pynput`).
- Mac builds are ideal for the **receiver** role (engineer PC). The **broadcaster** role still needs Windows + iRacing on the sim PC.

### Manual build (either OS)

```bash
pip install -r requirements.txt
pip install -r requirements-build.txt
# Windows only:
python scripts/make_icon_ico.py
# macOS only:
python3 scripts/make_icon_icns.py
python -m PyInstaller --noconfirm --clean ai_race_engineer.spec
```

### Build troubleshooting

| Error | Fix |
|-------|-----|
| `No matching distribution found for irsdk` | Use `pyirsdk`: `pip install pyirsdk` |
| `ModuleNotFoundError` during PyInstaller analysis | Run `pip install -r requirements.txt` first |
| `'py' is not recognized` (Windows) | Use `python` instead, or install the [Python launcher](https://docs.python.org/3/using/windows.html#python-launcher-for-windows) |
| `icon.png not found` | Run the build from the project root; icon lives in `assets/icon.png` |
| Build succeeds but app crashes on start | Rebuild with the platform build script (bundles PySide6 via `ai_race_engineer.spec`) |
| Mac: “app is damaged” / Gatekeeper block | Right-click → Open the first time, or `xattr -cr "dist/AI Race Engineer.app"` for local dev builds |

### UI controls

- **ANALYZE FIELD & ADVISE**: sends the current snapshot to the local strategy engine and disables itself until:
  - a response arrives, or
  - you click **CLEAR / CANCEL**, or
  - the 30s watchdog times out.
- **CLEAR / CANCEL**: clears the message area and cancels the in-flight request (cooperatively).
- **New tire sets left**: manual correction input (0+). The AI uses this exact value.
- **Pit loss (sec)**: manual correction input (0–300). The AI uses this exact value.

## Data sent to the AI (shape)

The prompt includes a compact JSON payload that looks like:

```json
{
  "me": {
    "lap": 24,
    "laps_remain": 18,
    "pos": 4,
    "fuel": 18.5,
    "fuel_per_lap": 2.41,
    "times": [92.45, 92.1, 92.87, 93.12, 92.55],
    "flags": 0
  },
  "race_info": {
    "pit_loss_sec": 8,
    "est_laps_on_fuel": 7.7,
    "tire_sets_remaining": 2
  },
  "field": {
    "P1": [91.2, 91.15, 91.18, 91.22, 91.19],
    "P3": [92.3, 92.25, 92.4, 92.35, 92.38],
    "YOU": [92.45, 92.1, 92.87, 93.12, 92.55]
  }
}
```

Notes:
- The overlay intentionally tracks only **Top 3** and cars within **±2 positions** of you (plus you) to keep memory stable and keep the packet small.
- Lap history per driver is bounded to **5** values.

## Troubleshooting

- **Overlay says “ENGINEER: No Signal”**:
  - iRacing isn’t connected yet, or `pyirsdk` can’t read the telemetry.
  - Start iRacing first, then run the overlay.
- **Analyze hangs**:
  - It should no longer hang indefinitely. You’ll either get a response, or the watchdog will re-enable Analyze after ~30s.
  - You can always hit **CLEAR / CANCEL** to reset immediately.
- **No text appears after Analyze**:
  - Check the terminal for errors from `strategy_engine.py`.
  - Try **CLEAR / CANCEL** and analyze again with iRacing connected.

## Git hygiene

`.gitignore` ignores Python cache files like `__pycache__/` and `*.pyc`.