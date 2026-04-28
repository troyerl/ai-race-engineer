import sys
import json
import boto3
import threading
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import Qt, QTimer, Signal, QObject
import irsdk
import os

# --- AWS CONFIG ---
MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

class BedrockWorker(QObject):
    finished = Signal(str)

    def invoke_ai(self, race_json):
        def run():
            try:

                bedrock_token = "bedrock-api-key-YmVkcm..." # Paste your full token here
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = bedrock_token

                # Update region to your specific AWS region
                client = boto3.client("bedrock-runtime", region_name="us-east-1")
                
                prompt = (
                    "Act as a professional race engineer. Analyze this JSON telemetry "
                    "and provide a concise strategy (10 words max). "
                    f"Data: {race_json}"
                )
                
                body = json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": prompt}]
                })

                response = client.invoke_model(body=body, modelId=MODEL_ID)
                response_body = json.loads(response.get("body").read())
                advice = response_body['content'][0]['text']
                self.finished.emit(advice)
            except Exception as e:
                self.finished.emit(f"Connection Error: {str(e)}")
        
        threading.Thread(target=run).start()

class AIRaceEngineer(QLabel):
    def __init__(self):
        super().__init__("Press Ctrl+Shift+A for AI Advice")
        self.ir = irsdk.IRSDK()
        self.ir.startup()
        
        # Track currently pressed keys
        self.pressed_keys = set()
        
        # UI Setup
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("color: #00FF00; font-family: 'Segoe UI'; font-size: 22px; font-weight: bold;")
        self.setFixedSize(900, 150)
        self.setAlignment(Qt.AlignCenter)

        # Data Tracking for Lap History
        self.lap_history = {}
        self.last_recorded_lap = {}
        
        # AI Worker
        self.ai_worker = BedrockWorker()
        self.ai_worker.finished.connect(self.display_advice)

        # Telemetry Timer (keeps history updated in background)
        self.telemetry_timer = QTimer()
        self.telemetry_timer.timeout.connect(self.update_history)
        self.telemetry_timer.start(100)

    def update_history(self):
        """Silently tracks lap times in the background so data is ready when requested."""
        if not self.ir.is_connected:
            self.ir.startup()
            return

        laps = self.ir['CarIdxLap'] or []
        for i in range(len(laps)):
            curr_lap = laps[i]
            if i not in self.last_recorded_lap:
                self.last_recorded_lap[i] = curr_lap
                self.lap_history[i] = []

            if curr_lap > self.last_recorded_lap[i]:
                last_time = self.ir['CarIdxLastLapTime'][i]
                if last_time > 0:
                    self.lap_history[i].append(round(last_time, 3))
                    self.lap_history[i] = self.lap_history[i][-5:] # Keep last 5
                self.last_recorded_lap[i] = curr_lap

    def keyPressEvent(self, event):
        self.pressed_keys.add(event.key())
        
        # TRIGGER COMBO: Control + Shift + A
        trigger_combo = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_A}
        
        if trigger_combo.issubset(self.pressed_keys):
            self.trigger_ai_request()

    def keyReleaseEvent(self, event):
        if event.key() in self.pressed_keys:
            self.pressed_keys.remove(event.key())

    def trigger_ai_request(self):
        if not self.ir.is_connected:
            self.setText("ENGINEER: Cannot see the car. Is iRacing open?")
            return

        self.setText("ENGINEER: Analyzing data...")
        
        # Compile the JSON packet
        packet = {
            "lap": self.ir['Lap'],
            "pos": self.ir['PlayerCarClassPosition'],
            "fuel": round(self.ir['FuelLevel'], 2),
            "tire_wear": self.ir.get('LFwearL', 1.0), # Note: Live wear isn't always available in all cars
            "history": self.lap_history.get(self.ir['PlayerCarIdx'], [])
        }
        
        self.ai_worker.invoke_ai(json.dumps(packet, separators=(',', ':')))

    def display_advice(self, text):
        self.setText(f"ENGINEER: {text}")

    # Mouse events for moving the overlay
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.offset = event.position().toPoint()
            self.setFocus(Qt.OtherFocusReason) 

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.offset)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AIRaceEngineer()
    window.show()
    sys.exit(app.exec())