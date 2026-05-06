from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QPushButton,
    QVBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QHBoxLayout,
    QApplication,
)
import os
import threading
import subprocess
import sys

from bedrock_worker import BedrockWorker
from telemetry import TelemetryTracker


class AIRaceEngineer(QWidget):
    def __init__(self):
        super().__init__()
        self.telemetry = TelemetryTracker()
        self._feature_rejoin = os.getenv("AIRACE_FEATURE_REJOIN", "0") == "1"
        self._feature_voice = os.getenv("AIRACE_FEATURE_VOICE", "0") == "1"

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
            QLabel#subLabel {
                color: rgba(255,255,255,245);
                font-size: 12px;
                font-weight: 700;
                background: rgba(20, 22, 28, 235);
                border: 1px solid rgba(255,255,255,30);
                padding: 4px 8px;
                border-radius: 10px;
            }
            QLabel#connBadge {
                color: rgba(255,255,255,245);
                font-size: 12px;
                font-weight: 800;
                background: rgba(20, 22, 28, 235);
                border: 1px solid rgba(255,255,255,30);
                padding: 4px 8px;
                border-radius: 10px;
            }
            QPushButton#analyzeBtn {
                background-color: rgba(31, 138, 76, 235);
                color: white;
                font-weight: 700;
                border-radius: 12px;
                padding: 12px;
                border: 1px solid rgba(255,255,255,40);
            }
            QPushButton#analyzeBtn:hover { background-color: rgba(35, 154, 85, 245); }
            QPushButton#analyzeBtn:pressed { background-color: rgba(25, 122, 67, 245); }
            QPushButton#analyzeBtn:disabled {
                background-color: rgba(31, 138, 76, 90);
                color: rgba(255,255,255,160);
            }
            QPushButton#clearBtn {
                background-color: rgba(20, 22, 28, 235);
                color: rgba(255,255,255,245);
                font-weight: 700;
                border-radius: 12px;
                padding: 10px;
                border: 1px solid rgba(255,255,255,40);
            }
            QPushButton#clearBtn:hover { background-color: rgba(28, 30, 38, 245); }
            QPushButton#clearBtn:pressed { background-color: rgba(16, 18, 24, 245); }
            QPushButton#closeBtn {
                background-color: rgba(231, 76, 60, 235);
                color: rgba(255,255,255,240);
                font-weight: 800;
                border-radius: 12px;
                padding: 10px;
                border: 1px solid rgba(255,255,255,40);
            }
            QPushButton#closeBtn:hover { background-color: rgba(231, 76, 60, 245); }
            QPushButton#closeBtn:pressed { background-color: rgba(200, 55, 45, 245); }
            QSpinBox#tireSpin, QSpinBox#pitSpin {
                background-color: rgba(8, 10, 14, 215);
                color: white;
                border-radius: 10px;
                padding: 6px 10px;
                border: 1px solid rgba(255,255,255,40);
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

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self.conn_badge = QLabel("iRacing: …")
        self.conn_badge.setObjectName("connBadge")
        top_row.addWidget(self.conn_badge)
        self.ai_badge = QLabel("AI: Idle")
        self.ai_badge.setObjectName("connBadge")
        top_row.addWidget(self.ai_badge)
        top_row.addStretch(1)

        self.rejoin_label = None
        if self._feature_rejoin:
            self.rejoin_label = QLabel("Gap/Rejoin: …")
            self.rejoin_label.setObjectName("subLabel")

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

        self.close_btn = QPushButton("CLOSE")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.close_app)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.addWidget(self.clear_btn)
        bottom_row.addWidget(self.close_btn)

        self.layout.addLayout(top_row)
        if self.rejoin_label is not None:
            self.layout.addWidget(self.rejoin_label)
        self.layout.addWidget(self.label)
        self.layout.addLayout(tire_row)
        self.layout.addLayout(pit_row)
        self.layout.addWidget(self.btn)
        self.layout.addLayout(bottom_row)
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

        # Connection indicator updates (slow cadence to reduce overhead).
        self._conn_timer = QTimer(self)
        self._conn_timer.timeout.connect(self._update_connection_badge)
        self._conn_timer.start(1000)
        self._update_connection_badge()
        self._set_ai_status("Idle")

        if self.rejoin_label is not None:
            self._rejoin_timer = QTimer(self)
            self._rejoin_timer.timeout.connect(self._update_rejoin_label)
            self._rejoin_timer.start(1000)
            self._update_rejoin_label()

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
        self._set_ai_status("Requesting")

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
            self._set_ai_status("Cancelled")
        if cancelled_id:
            print(f"[INFO] Cancel requested for request_id={cancelled_id}")

    def display_partial(self, request_id: int, text: str):
        if request_id != self._active_request_id:
            return
        self._set_ai_status("Streaming")
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
            self._set_ai_status("Timed out")

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
        self._set_ai_status("Error" if str(text).startswith("AI Error") else "Done")
        if self._feature_voice and not str(text).startswith("AI Error"):
            action = str(text).split("—", 1)[0].strip()
            threading.Thread(target=self._speak_action, args=(action,), daemon=True).start()

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
            self._set_ai_status("Idle")

    def close_app(self):
        # Quit the entire program (not just hide the overlay widget).
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            self.close()

    def _update_connection_badge(self):
        if self.telemetry.is_connected():
            self.conn_badge.setText("iRacing: Connected")
            self.conn_badge.setStyleSheet(
                "QLabel#connBadge { border-color: rgba(46, 204, 113, 140); }"
            )
        else:
            self.conn_badge.setText("iRacing: No Signal")
            self.conn_badge.setStyleSheet(
                "QLabel#connBadge { border-color: rgba(231, 76, 60, 160); }"
            )

    def _set_ai_status(self, status: str):
        self.ai_badge.setText(f"AI: {status}")
        status_l = (status or "").lower()
        if status_l in ("idle", "done"):
            color = "rgba(46, 204, 113, 140)"
        elif status_l in ("requesting", "streaming"):
            color = "rgba(52, 152, 219, 160)"
        elif status_l in ("timed out", "timeout"):
            color = "rgba(241, 196, 15, 170)"
        elif status_l in ("cancelled", "canceled"):
            color = "rgba(149, 165, 166, 170)"
        else:  # error/unknown
            color = "rgba(231, 76, 60, 170)"
        self.ai_badge.setStyleSheet(f"QLabel#connBadge {{ border-color: {color}; }}")

    def _update_rejoin_label(self):
        if self.rejoin_label is None:
            return
        est = self.telemetry.estimate_rejoin(int(self.pit_spin.value()))
        ga = est.get("ga")
        gb = est.get("gb")
        v = est.get("v", "UNKNOWN")
        parts = []
        parts.append(f"GA:{ga:.1f}s" if isinstance(ga, (int, float)) else "GA:—")
        parts.append(f"GB:{gb:.1f}s" if isinstance(gb, (int, float)) else "GB:—")
        parts.append(f"PL:{int(self.pit_spin.value())}s")
        if v == "LIKELY_LOSE_POSITION":
            verdict = "Rejoin: likely lose 1+"
        elif v == "UNDERCUT_POSSIBLE":
            verdict = "Rejoin: undercut possible"
        elif v == "REJOIN_NEARBY":
            verdict = "Rejoin: nearby"
        else:
            verdict = "Rejoin: unknown"
        self.rejoin_label.setText("Gap/Rejoin: " + " • ".join(parts) + " • " + verdict)

    def _speak_action(self, action: str):
        # Feature-flagged. Speaks only the ACTION (STAY OUT / PIT / PIT NOW).
        try:
            a = (action or "").strip()
            if not a:
                return
            if sys.platform.startswith("darwin"):
                subprocess.run(["say", a], check=False)
            elif sys.platform.startswith("win"):
                # Built-in SAPI (no extra dependency)
                ps = (
                    "Add-Type -AssemblyName System.Speech; "
                    f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{a.replace(\"'\", \" \")}')"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
        except Exception:
            pass

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.offset)

