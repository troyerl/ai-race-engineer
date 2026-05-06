import sys
import json
import boto3
import threading
import os
from collections import deque
from dotenv import load_dotenv
import yaml
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget, QPushButton, QVBoxLayout, QLabel, QSpinBox, QHBoxLayout
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint
import irsdk
from botocore.config import Config


load_dotenv()

# --- AWS CONFIG ---
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

class BedrockWorker(QObject):
    finished = Signal(int, str)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._active_request_id = 0
        self._cancelled_request_ids = set()

    def cancel_active(self) -> int:
        with self._lock:
            req_id = self._active_request_id
            if req_id:
                self._cancelled_request_ids.add(req_id)
            return req_id

    def _clear_cancelled(self, request_id: int) -> None:
        with self._lock:
            self._cancelled_request_ids.discard(request_id)

    def invoke_ai(self, request_id: int, race_json: str):
        def run():
            try:
                token = os.getenv("IRACING_BEDROCK_TOKEN")
                if not token:
                    error_msg = "Error: IRACING_BEDROCK_TOKEN not found in environment."
                    print(f"[ERROR] {error_msg}") # Console Print
                    self.finished.emit(request_id, error_msg)
                    return

                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        return

                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token
                client = boto3.client(
                    "bedrock-runtime",
                    region_name="us-east-2",
                    config=Config(
                        connect_timeout=5,
                        read_timeout=25,
                        retries={"max_attempts": 1, "mode": "standard"},
                    ),
                )
                
                prompt = (
                    "You are a Lead Race Engineer. Analyze the telemetry and race status. "
                    "Primary Goal: Optimize track position vs fuel/tire life. "
                    "If 'flags' indicates a Caution, prioritize whether to 'Pit Now' or 'Stay Out' based on field behavior and fuel. "
                    "If Green, compare User lap fall-off against the field. "
                    "Output: 10 words max. Be decisive (e.g., 'Caution out, Pit Now for tires/fuel' or 'Green. Stay out, +5 laps'). "
                    f"Data: {race_json}"
                )
                
                body = json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": prompt}]
                })

                print(f"[DEBUG] Sending data to AI: {race_json[:100]}...") # Print snippet of data
                response = client.invoke_model(body=body, modelId=MODEL_ID)
                response_body = json.loads(response.get("body").read())
                advice = response_body['content'][0]['text']
                
                print(f"[SUCCESS] AI Advice: {advice}") # Console Print
                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        self._clear_cancelled(request_id)
                        return
                self.finished.emit(request_id, advice)
                self._clear_cancelled(request_id)
            except Exception as e:
                print(f"[ERROR] {str(e)}") # Console Print
                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        self._clear_cancelled(request_id)
                        return
                self.finished.emit(request_id, f"AI Error: {str(e)}")
                self._clear_cancelled(request_id)
        
        t = threading.Thread(target=run, daemon=True)
        t.start()

class AIRaceEngineer(QWidget):
    def __init__(self):
        super().__init__()
        self.ir = irsdk.IRSDK()
        self.ir.startup()
        
        # UI Setup
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        self.setMinimumWidth(450)
        self.setMaximumWidth(500) # Give it a little breathing room
        
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(10)

        # Background "card" look via widget stylesheet
        self.setStyleSheet("""
            QWidget {
                font-family: "Segoe UI";
            }
            QLabel#statusLabel {
                color: #E8F5E9;
                font-size: 18px;
                font-weight: 700;
                background: rgba(8, 10, 14, 215);
                border: 1px solid rgba(255, 255, 255, 22);
                padding: 12px 12px;
                border-radius: 12px;
            }
            QLabel#subLabel {
                color: rgba(255,255,255,190);
                font-size: 12px;
            }
            QPushButton#analyzeBtn {
                background-color: #1F8A4C;
                color: white;
                font-weight: 700;
                border-radius: 12px;
                padding: 12px;
                border: 1px solid rgba(255,255,255,18);
            }
            QPushButton#analyzeBtn:hover { background-color: #239A55; }
            QPushButton#analyzeBtn:pressed { background-color: #197A43; }
            QPushButton#analyzeBtn:disabled {
                background-color: rgba(31, 138, 76, 90);
                color: rgba(255,255,255,160);
            }
            QPushButton#clearBtn {
                background-color: rgba(255,255,255,35);
                color: rgba(255,255,255,220);
                font-weight: 700;
                border-radius: 12px;
                padding: 10px;
                border: 1px solid rgba(255,255,255,18);
            }
            QPushButton#clearBtn:hover { background-color: rgba(255,255,255,50); }
            QPushButton#clearBtn:pressed { background-color: rgba(255,255,255,28); }
            QSpinBox#tireSpin {
                background-color: rgba(8, 10, 14, 215);
                color: white;
                border-radius: 10px;
                padding: 6px 10px;
                border: 1px solid rgba(255,255,255,22);
                min-width: 70px;
                font-weight: 700;
            }
            QSpinBox#tireSpin::up-button, QSpinBox#tireSpin::down-button {
                width: 20px;
                border-radius: 8px;
                background: rgba(255,255,255,14);
                border: none;
            }
            QSpinBox#tireSpin::up-button:hover, QSpinBox#tireSpin::down-button:hover {
                background: rgba(255,255,255,22);
            }
        """)

        self.label = QLabel("Engineer Standby")
        self.label.setObjectName("statusLabel")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)

        self.label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        
        self.btn = QPushButton("ANALYZE FIELD & ADVISE")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setObjectName("analyzeBtn")
        self.btn.clicked.connect(self.trigger_ai_request)

        # Tire sets input (can be corrected manually)
        tire_row = QHBoxLayout()
        tire_row.setContentsMargins(0, 0, 0, 0)
        self.tire_label = QLabel("New tire sets left")
        self.tire_label.setObjectName("subLabel")
        self.tire_spin = QSpinBox()
        self.tire_spin.setObjectName("tireSpin")
        self.tire_spin.setMinimum(0)
        self.tire_spin.setMaximum(99)
        tire_row.addWidget(self.tire_label)
        tire_row.addWidget(self.tire_spin)
        tire_row.addStretch(1)

        self.clear_btn = QPushButton("CLEAR / CANCEL")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.clicked.connect(self.clear_and_cancel)

        self.layout.addWidget(self.label)
        self.layout.addLayout(tire_row)
        self.layout.addWidget(self.btn)
        self.layout.addWidget(self.clear_btn)
        self.setLayout(self.layout)

        # Global Pace Tracking
        self.field_history = {}  # car_idx -> deque(maxlen=5) of lap times
        self.last_recorded_lap = {}  # car_idx -> last lap int
        
        self.ai_worker = BedrockWorker()
        self.ai_worker.finished.connect(self.display_advice)
        self._next_request_id = 0
        self._active_request_id = 0

        detected_sets = self._tire_sets_remaining()
        if isinstance(detected_sets, int) and detected_sets >= 0:
            self.tire_spin.setValue(detected_sets)
        else:
            self.tire_spin.setValue(0)

        self.telemetry_timer = QTimer()
        self.telemetry_timer.timeout.connect(self.update_field_history)
        self.telemetry_timer.start(100)

    def _ir_get(self, key: str, default=None):
        try:
            v = self.ir[key]
        except Exception:
            return default
        return default if v is None else v

    def _tire_sets_remaining(self):
        # iRacing has exposed this under different names across builds/sims.
        for key in (
            "PlayerTireSetsRemaining",
            "PlayerTireSetsAvail",
            "PlayerTireSetsAvailable",
            "TireSetsRemaining",
            "TireSetsAvailable",
        ):
            v = self._ir_get(key, None)
            if v is not None:
                try:
                    return int(v)
                except Exception:
                    return v
        return None

    def update_field_history(self):
        if not self.ir.is_connected:
            self.ir.startup()
            return

        laps = self.ir['CarIdxLap'] or []
        last_lap_times = self.ir['CarIdxLastLapTime'] or []
        positions = self.ir['CarIdxClassPosition'] or []
        player_idx = self.ir['PlayerCarIdx']
        player_pos = self.ir['PlayerCarClassPosition']

        # Track only the drivers we care about to keep memory bounded.
        tracked = set()
        for idx, pos in enumerate(positions):
            if pos <= 3 or abs(pos - player_pos) <= 2:
                tracked.add(idx)
        tracked.add(player_idx)

        # Prune any old drivers we no longer care about.
        for idx in list(self.field_history.keys()):
            if idx not in tracked:
                self.field_history.pop(idx, None)
                self.last_recorded_lap.pop(idx, None)

        for i in tracked:
            if i >= len(laps) or i >= len(last_lap_times):
                continue
            curr_lap = laps[i]
            if i not in self.last_recorded_lap:
                self.last_recorded_lap[i] = curr_lap
                self.field_history[i] = deque(maxlen=5)

            # When a driver completes a lap
            if curr_lap > self.last_recorded_lap[i]:
                t = last_lap_times[i]
                if t > 0:
                    self.field_history[i].append(round(t, 3))
                self.last_recorded_lap[i] = curr_lap

    def trigger_ai_request(self):
        if not self.ir.is_connected:
            self.label.setText("ENGINEER: No Signal")
            return

        self.label.setText("Processing Field Data...")
        self.btn.setEnabled(False)
        
        player_idx = self.ir['PlayerCarIdx']
        player_pos = self.ir['PlayerCarClassPosition']
        
        # Filter Field Data to save tokens (Top 3 + Immediate Rivals)
        # We only send data for relevant drivers
        relevant_history = {}
        positions = self.ir['CarIdxClassPosition'] or []
        
        for idx, pos in enumerate(positions):
            # Include: Top 3, and anyone within 2 positions of the player
            if pos <= 3 or abs(pos - player_pos) <= 2:
                if self.field_history.get(idx):
                    label = f"P{pos}" if idx != player_idx else "YOU"
                    relevant_history[label] = list(self.field_history[idx])

        packet = {
            "me": {
                "lap": self.ir['Lap'],
                "laps_remain": self.ir['SessionLapsRemain'], # Crucial for strategy
                "pos": player_pos,
                "fuel": round(self.ir['FuelLevel'], 2),
                "fuel_per_lap": round(self.ir['FuelUsePerHour'], 2), # Or your own calculation
                "times": self.field_history.get(player_idx, []),
                "flags": self.ir['SessionFlags'] # Tells AI if it's a Caution
            },
            "race_info": {
                "pit_loss_sec": 8, # Hardcode or calculate for the track
                "est_laps_on_fuel": round(self.ir['FuelLevel'] / max(self.ir['FuelUsePerHour'], 0.1), 1),
                "tire_sets_remaining": int(self.tire_spin.value()),
            },
            "field": relevant_history
        }
        
        self._next_request_id += 1
        self._active_request_id = self._next_request_id
        with self.ai_worker._lock:
            self.ai_worker._active_request_id = self._active_request_id
        self.ai_worker.invoke_ai(self._active_request_id, json.dumps(packet, separators=(',', ':')))

    def clear_and_cancel(self):
        cancelled_id = self.ai_worker.cancel_active()
        self._active_request_id = 0
        self.label.setText("Engineer Standby")
        self.btn.setEnabled(True)
        if cancelled_id:
            print(f"[INFO] Cancel requested for request_id={cancelled_id}")

    def display_advice(self, request_id: int, text: str):
        if request_id != self._active_request_id:
            return
        self.label.setText(text)
        self.btn.setEnabled(True)

        self.layout.activate() 
        self.adjustSize()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton: self.move(event.globalPosition().toPoint() - self.offset)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AIRaceEngineer()
    window.show()
    sys.exit(app.exec())