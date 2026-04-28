import sys
import json
import boto3
import threading
import os
import yaml
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint
import irsdk

# --- AWS CONFIG ---
MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

class BedrockWorker(QObject):
    finished = Signal(str)

    def invoke_ai(self, race_json):
        def run():
            try:
                token = os.getenv("IRACING_BEDROCK_TOKEN")
                if not token:
                    self.finished.emit("Error: Set IRACING_BEDROCK_TOKEN")
                    return

                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token
                client = boto3.client("bedrock-runtime", region_name="us-east-2")
                
                prompt = (
                    "You are a Lead Race Engineer. Analyze the telemetry for the User and the Field. "
                    "Compare lap times to determine if the User is losing ground. "
                    "Provide a 10-word max strategy (e.g., 'Leaders pitting, stay out to gain track position'). "
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
                self.finished.emit(f"AI Error: {str(e)}")
        
        threading.Thread(target=run).start()

class AIRaceEngineer(QWidget):
    def __init__(self):
        super().__init__()
        self.ir = irsdk.IRSDK()
        self.ir.startup()
        
        # UI Setup
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(450, 180)

        self.layout = QVBoxLayout()
        self.label = QLabel("Engineer Standby")
        self.label.setStyleSheet("color: #00FF00; font-family: 'Segoe UI'; font-size: 18px; font-weight: bold; background: rgba(0,0,0,120); padding: 8px; border-radius: 5px;")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        
        self.btn = QPushButton("ANALYZE FIELD & ADVISE")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; border-radius: 10px; padding: 12px;")
        self.btn.clicked.connect(self.trigger_ai_request)

        self.layout.addWidget(self.label)
        self.layout.addWidget(self.btn)
        self.setLayout(self.layout)

        # Global Pace Tracking
        self.field_history = {} # {car_idx: [lap_times]}
        self.last_recorded_lap = {} # {car_idx: lap_num}
        
        self.ai_worker = BedrockWorker()
        self.ai_worker.finished.connect(self.display_advice)

        self.telemetry_timer = QTimer()
        self.telemetry_timer.timeout.connect(self.update_field_history)
        self.telemetry_timer.start(100)

    def update_field_history(self):
        if not self.ir.is_connected:
            self.ir.startup()
            return

        laps = self.ir['CarIdxLap'] or []
        last_lap_times = self.ir['CarIdxLastLapTime'] or []

        for i in range(len(laps)):
            curr_lap = laps[i]
            if i not in self.last_recorded_lap:
                self.last_recorded_lap[i] = curr_lap
                self.field_history[i] = []

            # When a driver completes a lap
            if curr_lap > self.last_recorded_lap[i]:
                t = last_lap_times[i]
                if t > 0:
                    self.field_history[i].append(round(t, 3))
                    self.field_history[i] = self.field_history[i][-5:]
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
                    relevant_history[label] = self.field_history[idx]

        packet = {
            "me": {
                "lap": self.ir['Lap'],
                "pos": player_pos,
                "fuel": round(self.ir['FuelLevel'], 2),
                "times": self.field_history.get(player_idx, [])
            },
            "field": relevant_history
        }
        
        self.ai_worker.invoke_ai(json.dumps(packet, separators=(',', ':')))

    def display_advice(self, text):
        self.label.setText(text)
        self.btn.setEnabled(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton: self.move(event.globalPosition().toPoint() - self.offset)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AIRaceEngineer()
    window.show()
    sys.exit(app.exec())