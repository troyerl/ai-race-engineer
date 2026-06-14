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

- `main.py`: app entrypoint. Starts the Qt application.
- `ui.py`: the overlay UI (buttons, inputs, timeout watchdog) + request-id gating so late results can’t overwrite the screen.
- `strategy_engine.py`: deterministic pit/strategy decision rules.
- `strategy_worker.py`: runs the engine off the UI thread (same signals as the old AI worker).
- `telemetry.py`: iRacing telemetry tracking + bounded in-memory lap history + packet builder.

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

## Packaging a Windows executable

PyInstaller builds are OS-specific, so **run this on a Windows machine**.

### One-command build (recommended)

From the project folder on **Windows**:

```bat
build_windows.bat
```

The script installs `requirements.txt` + build tools, generates `icon.ico`, and runs PyInstaller via `ai_race_engineer.spec`.

Output:
- `dist\AI Race Engineer\AI Race Engineer.exe`

Notes:
- The app uses `icon.png` at runtime (Qt window/app icon).
- The Windows build script generates a **multi-size** `icon.ico` from `icon.png` (via `make_icon_ico.py`).
- iRacing SDK package is **`pyirsdk`** on PyPI (`import irsdk` in code). If pip says *no matching distribution for irsdk*, use `pyirsdk`.

### Manual build (if you prefer)

```bat
py -m pip install -r requirements.txt
py -m pip install -r requirements-build.txt
py make_icon_ico.py
py -m PyInstaller --noconfirm --clean ai_race_engineer.spec
```

### Build troubleshooting

| Error | Fix |
|-------|-----|
| `No matching distribution found for irsdk` | Use `pyirsdk`: `pip install pyirsdk` |
| `ModuleNotFoundError` during PyInstaller analysis | Run `pip install -r requirements.txt` first |
| `'py' is not recognized` | Use `python` instead, or install the [Python launcher](https://docs.python.org/3/using/windows.html#python-launcher-for-windows) |
| `icon.png not found` | Run the build from the project root (same folder as `main.py`) |
| Build succeeds but exe crashes on start | Rebuild with `build_windows.bat` (uses `ai_race_engineer.spec` with PySide6 bundled) |

**Mac/Linux:** PyInstaller builds are OS-specific. You can build a Mac `.app` for the receiver UI, but the Windows `.exe` must be built on Windows.

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