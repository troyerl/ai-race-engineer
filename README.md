# ai-race-engineer

A small always-on-top iRacing overlay that sends a compact “strategy snapshot” to an LLM on AWS Bedrock and shows **streaming** race-engineer advice (updates live as tokens arrive).

## What it does

- **Collects recent pace**: keeps the last ~5 lap times for you + a small slice of the field.
- **Builds a compact JSON packet**: your position, fuel, laps remaining, flags, plus nearby/top competitors’ recent lap times.
- **Calls Bedrock with streaming enabled**: the overlay shows advice as it arrives (no waiting for the full response).
- **Prevents “stuck forever” UI**:
  - Bedrock client has connect/read timeouts.
  - A 30s UI watchdog re-enables the Analyze button if a request stalls.
  - Clear/Cancel immediately resets the UI and stops applying late results.

## Repository layout

- `main.py`: app entrypoint. Starts the Qt application.
- `ui.py`: the overlay UI (buttons, inputs, timeout watchdog) + request-id gating so late results can’t overwrite the screen.
- `telemetry.py`: iRacing telemetry tracking + bounded in-memory lap history + packet builder.
- `bedrock_worker.py`: background Bedrock client with streaming + cooperative cancel.

## Requirements

- Python 3.10+ recommended
- iRacing running (and the iRacing SDK accessible to Python via `pyirsdk` — imported as `irsdk` in code)
- AWS Bedrock access to the configured model

Python packages used:
- `PySide6`
- `boto3` (and `botocore`)
- `pyirsdk` (PyPI name; `import irsdk` in code)
- Optional: `python-dotenv` (loads `.env` on startup)
- Optional: `pynput` (global hotkey while iRacing has focus; macOS needs Accessibility)

Install everything:

```bash
pip install -r requirements.txt
```

Or manually:

```bash
pip install PySide6 boto3 pyirsdk python-dotenv pynput
```

Or with a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

### Environment variables

- `IRACING_BEDROCK_TOKEN`: required. Used as `AWS_BEARER_TOKEN_BEDROCK` for Bedrock authentication.

You can set it via your shell, or by creating a `.env` file locally (not committed).

See `.env-example`.

## Local testing (single PC)

Use this path when you want to try the app on **one machine** with iRacing and the AI overlay together — no LAN, no second PC.

### 1. Prerequisites

- Python 3.10+
- iRacing installed and able to enter a session (test drive, practice, or race)
- AWS Bedrock access to the configured model (`us.anthropic.claude-sonnet-4-6-v1:0` by default)
- Dependencies installed (see **Requirements** above)

### 2. Set your Bedrock token

Copy the example env file and add your token:

```bash
cp .env-example .env
```

Edit `.env`:

```bash
IRACING_BEDROCK_TOKEN=your_bedrock_bearer_token_here
```

### 3. Start iRacing first

Launch iRacing and load into a session (garage, test drive, or on track). The SDK only provides telemetry once the sim is running.

### 4. Run local mode

Skip the startup role picker and open the single-PC overlay:

```bash
python3 main.py --role local
```

You should see the overlay window. The status badge should change from **iRacing: Offline** to **iRacing: Online** once telemetry connects.

### 5. Quick functional test

1. Drive a few laps so the app has lap times and fuel data.
2. Set **Pit loss (sec)** and **New tire sets left** if needed (or enable auto pit-loss in preferences).
3. Click **ANALYZE FIELD & ADVISE** (or press **Spacebar** when the overlay is focused).
4. Confirm advice streams in live — you should see text appear incrementally, then a final pit/stay-out call.
5. Click **CLEAR / CANCEL** to reset and try again.

Optional checks:

- **Voice enabled** (preferences): reads the finished call aloud on this PC.
- **Show pit impact**: after a PIT call, a caution-aware pit-road impact line appears under the advice.
- **Hotkey**: Space works in-app always; with `pynput` installed, Space can work globally (on macOS, grant **Accessibility** to your terminal or Python in System Settings → Privacy & Security).

### 6. Config file

Settings are saved to `~/.ai_race_engineer.json` (hotkey, voice, pit loss, token usage counters, etc.). Delete that file to reset preferences.

### Local troubleshooting

| Symptom | What to try |
|---------|-------------|
| **iRacing: Offline** | Start iRacing before the overlay; restart the overlay after joining a session. |
| **Error: IRACING_BEDROCK_TOKEN not found** | Add the token to `.env` or export it in your shell. |
| **Analyze does nothing / times out** | Check Bedrock credentials and model access; watch the terminal for errors. |
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

From the project folder:

```bat
build_windows.bat
```

Output:
- `dist\AI Race Engineer\AI Race Engineer.exe`

Notes:
- The app uses `icon.png` at runtime (Qt window/app icon).
- The Windows build script generates a **multi-size** `icon.ico` from `icon.png` (via `make_icon_ico.py`) and passes it to PyInstaller (`--icon`). Multi-size ICOs are much more reliable in Explorer/taskbar than single-size ICOs.

### Manual build (if you prefer)

```bat
py -m pip install -U pip
py -m pip install -U pyinstaller
py -m PyInstaller --noconfirm --windowed --name "AI Race Engineer" main.py
```

If you want a single-file exe (sometimes less reliable with GUI apps):

```bat
py -m PyInstaller --noconfirm --onefile --windowed --name "AI Race Engineer" main.py
```

### UI controls

- **ANALYZE FIELD & ADVISE**: sends the current snapshot to Bedrock and disables itself until:
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
- **No text appears while streaming**:
  - Bedrock streaming event shapes can vary by model/version. If you see this, grab the console output from a single click and we can adjust the streaming delta parsing in `bedrock_worker.py`.

## Git hygiene

`.gitignore` ignores Python cache files like `__pycache__/` and `*.pyc`.