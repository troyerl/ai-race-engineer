import sys
import json
import boto3
import threading
import os
from dotenv import load_dotenv
import yaml
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget, QPushButton, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint
import irsdk


load_dotenv()

# --- AWS CONFIG ---
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

class BedrockWorker(QObject):
    finished = Signal(str)

    def invoke_ai(self, race_json):
        def run():
            try:
                token = os.getenv("IRACING_BEDROCK_TOKEN")
                if not token:
                    error_msg = "Error: IRACING_BEDROCK_TOKEN not found in environment."
                    print(f"[ERROR] {error_msg}") # Console Print
                    self.finished.emit(error_msg)
                    return

                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token
                client = boto3.client("bedrock-runtime", region_name="us-east-2")
                
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
                self.finished.emit(advice)
            except Exception as e:
                print(f"[ERROR] {str(e)}") # Console Print
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
        
        self.setMinimumWidth(450)
        self.setMaximumWidth(500) # Give it a little breathing room
        
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(10, 10, 10, 10)

        self.label = QLabel("Engineer Standby")
        self.label.setStyleSheet("""
            color: #00FF00; 
            font-family: 'Segoe UI'; 
            font-size: 18px; 
            font-weight: bold; 
            background: rgba(0, 0, 0, 200);
            padding: 10px; 
            border-radius: 5px;
        """)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)

        self.label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        
        self.btn = QPushButton("ANALYZE FIELD & ADVISE")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; border-radius: 10px; padding: 12px;")
        self.btn.clicked.connect(self.trigger_ai_request)

        self.layout.addWidget(self.label)
        self.layout.addWidget(self.btn)
        self.setLayout(self.layout)

        # Global Pace Tracking
        self.field_history = {} 
        self.last_recorded_lap = {} 
        
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
                "laps_remain": self.ir['SessionLapsRemain'], # Crucial for strategy
                "pos": player_pos,
                "fuel": round(self.ir['FuelLevel'], 2),
                "fuel_per_lap": round(self.ir['FuelUsePerHour'], 2), # Or your own calculation
                "times": self.field_history.get(player_idx, []),
                "flags": self.ir['SessionFlags'] # Tells AI if it's a Caution
            },
            "race_info": {
                "pit_loss_sec": 8, # Hardcode or calculate for the track
                "est_laps_on_fuel": round(self.ir['FuelLevel'] / max(self.ir['FuelUsePerHour'], 0.1), 1)
            },
            "field": relevant_history
        }
        
        self.ai_worker.invoke_ai(json.dumps(packet, separators=(',', ':')))

    def display_advice(self, text):
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