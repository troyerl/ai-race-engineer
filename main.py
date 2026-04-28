import sys
import json
import boto3
import threading
import os
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer, Signal, QObject
import irsdk

# --- AWS CONFIG ---
MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

class BedrockWorker(QObject):
    finished = Signal(str)

    def invoke_ai(self, race_json):
        def run():
            try:
                # Read the token from the environment variable inside the worker
                token = os.getenv("IRACING_BEDROCK_TOKEN")
                
                if not token:
                    self.finished.emit("Error: Env variable IRACING_BEDROCK_TOKEN is not set.")
                    return

                # Set the specific AWS environment variable Bedrock expects
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token

                # Region set to us-east-2 based on your previous token data
                client = boto3.client("bedrock-runtime", region_name="us-east-2")
                
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
        self.setFixedSize(400, 150)

        self.layout = QVBoxLayout()
        
        # Advice Label
        self.label = QLabel("Engineer Standby")
        self.label.setStyleSheet("color: #00FF00; font-family: 'Segoe UI'; font-size: 18px; font-weight: bold; background: rgba(0,0,0,100); padding: 5px; border-radius: 5px;")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        
        # Check if environment variable exists on startup
        if not os.getenv("IRACING_BEDROCK_TOKEN"):
            self.label.setText("CRITICAL: Set IRACING_BEDROCK_TOKEN env var!")
            self.label.setStyleSheet("color: #FF0000; font-weight: bold; background: rgba(0,0,0,150);")

        # Trigger Button
        self.btn = QPushButton("ASK ENGINEER")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-family: 'Segoe UI';
                font-size: 16px;
                font-weight: bold;
                border-radius: 10px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        self.btn.clicked.connect(self.trigger_ai_request)

        self.layout.addWidget(self.label)
        self.layout.addWidget(self.btn)
        self.setLayout(self.layout)

        # Data Tracking
        self.lap_history = {}
        self.last_recorded_lap = {}
        
        # AI Worker
        self.ai_worker = BedrockWorker()
        self.ai_worker.finished.connect(self.display_advice)

        # Telemetry Timer
        self.telemetry_timer = QTimer()
        self.telemetry_timer.timeout.connect(self.update_history)
        self.telemetry_timer.start(100)

    def update_history(self):
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
                    self.lap_history[i] = self.lap_history[i][-5:]
                self.last_recorded_lap[i] = curr_lap

    def trigger_ai_request(self):
        if not self.ir.is_connected:
            self.label.setText("ENGINEER: Connection Lost")
            return

        self.label.setText("Consulting Engineer...")
        self.btn.setEnabled(False) # Prevent double clicking
        
        packet = {
            "lap": self.ir['Lap'],
            "pos": self.ir['PlayerCarClassPosition'],
            "fuel": round(self.ir['FuelLevel'], 2),
            "history": self.lap_history.get(self.ir['PlayerCarIdx'], [])
        }
        
        self.ai_worker.invoke_ai(json.dumps(packet, separators=(',', ':')))

    def display_advice(self, text):
        self.label.setText(f"STRATEGY: {text}")
        self.btn.setEnabled(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.offset)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AIRaceEngineer()
    window.show()
    sys.exit(app.exec())