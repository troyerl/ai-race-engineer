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
- iRacing running (and the iRacing SDK accessible to Python via `irsdk`)
- AWS Bedrock access to the configured model

Python packages used:
- `PySide6`
- `boto3` (and `botocore`)
- `irsdk`
- Optional: `python-dotenv` (only needed if you want `.env` auto-loading)

## Configuration

### Environment variables

- `IRACING_BEDROCK_TOKEN`: required. Used as `AWS_BEARER_TOKEN_BEDROCK` for Bedrock authentication.

You can set it via your shell, or by creating a `.env` file locally (not committed).

See `.env-example`.

## Running the app

From the project folder:

```bash
python3 main.py
```

## Packaging a Windows executable

PyInstaller builds are OS-specific, so **run this on a Windows machine**.

### One-command build (recommended)

From the project folder:

```bat
build_windows.bat
```

Output:
- `dist\AI Race Engineer\AI Race Engineer.exe`

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
  - iRacing isn’t connected yet, or `irsdk` can’t read the telemetry.
  - Start iRacing first, then run the overlay.
- **Analyze hangs**:
  - It should no longer hang indefinitely. You’ll either get a response, or the watchdog will re-enable Analyze after ~30s.
  - You can always hit **CLEAR / CANCEL** to reset immediately.
- **No text appears while streaming**:
  - Bedrock streaming event shapes can vary by model/version. If you see this, grab the console output from a single click and we can adjust the streaming delta parsing in `bedrock_worker.py`.

## Git hygiene

`.gitignore` ignores Python cache files like `__pycache__/` and `*.pyc`.