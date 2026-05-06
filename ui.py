from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QPushButton,
    QVBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QHBoxLayout,
)

from bedrock_worker import BedrockWorker
from telemetry import TelemetryTracker


class AIRaceEngineer(QWidget):
    def __init__(self):
        super().__init__()
        self.telemetry = TelemetryTracker()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.setMinimumWidth(450)
        self.setMaximumWidth(500)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(10)

        self.setStyleSheet(
            """
            QWidget { font-family: "Segoe UI"; }
            QLabel#statusLabel {
                color: #E8F5E9;
                font-size: 18px;
                font-weight: 700;
                background: rgba(8, 10, 14, 215);
                border: 1px solid rgba(255, 255, 255, 22);
                padding: 12px 12px;
                border-radius: 12px;
            }
            QLabel#subLabel { color: rgba(255,255,255,190); font-size: 12px; }
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
            QSpinBox#tireSpin, QSpinBox#pitSpin {
                background-color: rgba(8, 10, 14, 215);
                color: white;
                border-radius: 10px;
                padding: 6px 10px;
                border: 1px solid rgba(255,255,255,22);
                min-width: 70px;
                font-weight: 700;
            }
            QSpinBox#tireSpin::up-button, QSpinBox#tireSpin::down-button,
            QSpinBox#pitSpin::up-button, QSpinBox#pitSpin::down-button {
                width: 20px;
                border-radius: 8px;
                background: rgba(255,255,255,14);
                border: none;
            }
            QSpinBox#tireSpin::up-button:hover, QSpinBox#tireSpin::down-button:hover,
            QSpinBox#pitSpin::up-button:hover, QSpinBox#pitSpin::down-button:hover {
                background: rgba(255,255,255,22);
            }
            """
        )

        self.label = QLabel("Engineer Standby")
        self.label.setObjectName("statusLabel")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        self.btn = QPushButton("ANALYZE FIELD & ADVISE")
        self.btn.setObjectName("analyzeBtn")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.trigger_ai_request)

        tire_row = QHBoxLayout()
        tire_row.setContentsMargins(0, 0, 0, 0)
        tire_label = QLabel("New tire sets left")
        tire_label.setObjectName("subLabel")
        self.tire_spin = QSpinBox()
        self.tire_spin.setObjectName("tireSpin")
        self.tire_spin.setMinimum(0)
        self.tire_spin.setMaximum(99)
        self.tire_spin.setValue(0)
        tire_row.addWidget(tire_label)
        tire_row.addWidget(self.tire_spin)
        tire_row.addStretch(1)

        pit_row = QHBoxLayout()
        pit_row.setContentsMargins(0, 0, 0, 0)
        pit_label = QLabel("Pit loss (sec)")
        pit_label.setObjectName("subLabel")
        self.pit_spin = QSpinBox()
        self.pit_spin.setObjectName("pitSpin")
        self.pit_spin.setMinimum(0)
        self.pit_spin.setMaximum(300)
        self.pit_spin.setValue(8)
        pit_row.addWidget(pit_label)
        pit_row.addWidget(self.pit_spin)
        pit_row.addStretch(1)

        self.clear_btn = QPushButton("CLEAR / CANCEL")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_and_cancel)

        self.layout.addWidget(self.label)
        self.layout.addLayout(tire_row)
        self.layout.addLayout(pit_row)
        self.layout.addWidget(self.btn)
        self.layout.addWidget(self.clear_btn)
        self.setLayout(self.layout)

        self.ai_worker = BedrockWorker()
        self.ai_worker.partial.connect(self.display_partial)
        self.ai_worker.finished.connect(self.display_advice)

        self._next_request_id = 0
        self._active_request_id = 0

        self._request_watchdog = QTimer(self)
        self._request_watchdog.setSingleShot(True)
        self._request_watchdog.timeout.connect(self._on_request_timeout)

        # Throttle streaming UI updates (stream chunks can arrive very frequently).
        self._partial_buffer = None
        self._partial_flush_timer = QTimer(self)
        self._partial_flush_timer.setSingleShot(True)
        self._partial_flush_timer.timeout.connect(self._flush_partial)

        # Clears the last shown advice after a period of inactivity.
        self._idle_clear_timer = QTimer(self)
        self._idle_clear_timer.setSingleShot(True)
        self._idle_clear_timer.timeout.connect(self._clear_if_idle)

        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self.telemetry.update_field_history)
        # 250ms is typically indistinguishable in-race, but cuts polling overhead.
        self.telemetry_timer.start(250)

    def trigger_ai_request(self):
        self._idle_clear_timer.stop()
        self._partial_flush_timer.stop()
        self._partial_buffer = None
        if not self.telemetry.ensure_connected():
            self.label.setText("ENGINEER: No Signal")
            return

        self.label.setText("Processing Field Data...")
        self.btn.setEnabled(False)
        self._request_watchdog.start(30000)

        self._next_request_id += 1
        self._active_request_id = self._next_request_id
        self.ai_worker.set_active(self._active_request_id)

        packet_json = self.telemetry.build_packet(
            tire_sets_remaining=int(self.tire_spin.value()),
            pit_loss_sec=int(self.pit_spin.value()),
        )
        self.ai_worker.invoke_ai(self._active_request_id, packet_json)

    def clear_and_cancel(self):
        self._request_watchdog.stop()
        self._idle_clear_timer.stop()
        self._partial_flush_timer.stop()
        self._partial_buffer = None
        cancelled_id = self.ai_worker.cancel_active()
        self._active_request_id = 0
        self.label.setText("Engineer Standby")
        self.btn.setEnabled(True)
        if cancelled_id:
            print(f"[INFO] Cancel requested for request_id={cancelled_id}")

    def display_partial(self, request_id: int, text: str):
        if request_id != self._active_request_id:
            return
        # Buffer and coalesce updates to avoid UI churn.
        self._partial_buffer = text
        if not self._partial_flush_timer.isActive():
            self._partial_flush_timer.start(80)

    def _on_request_timeout(self):
        if self._active_request_id:
            print(f"[WARN] AI request timed out (request_id={self._active_request_id})")
            self._active_request_id = 0
            self.label.setText("Timed out. Try again or Clear/Cancel.")
            self.btn.setEnabled(True)

    def display_advice(self, request_id: int, text: str):
        if request_id != self._active_request_id:
            return
        self._request_watchdog.stop()
        self._partial_flush_timer.stop()
        self._partial_buffer = None
        self.label.setText(text)
        self.btn.setEnabled(True)
        self.layout.activate()
        self.adjustSize()
        # Mark request complete and schedule auto-clear if no further interaction.
        self._active_request_id = 0
        self._idle_clear_timer.start(120000)

    def _flush_partial(self):
        if self._active_request_id == 0 or self._partial_buffer is None:
            return
        self.label.setText(self._partial_buffer)
        self.layout.activate()
        self.adjustSize()

    def _clear_if_idle(self):
        # Only clear if we are not currently waiting on a request.
        if self._active_request_id == 0 and self.btn.isEnabled():
            self.label.setText("Engineer Standby")
            self.layout.activate()
            self.adjustSize()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.offset)

