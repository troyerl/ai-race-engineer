"""Sim PC bridge — streams iRacing telemetry and speaks engineer calls (no on-screen advice)."""

from __future__ import annotations

import json
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app_config import ensure_default_config_file, load_config, write_config
from race_link import DEFAULT_RACE_LINK_PORT, BroadcasterService
from lan_discovery import BroadcasterBeacon
from speech import speak_engineer_advice
from telemetry import TelemetryTracker

# Receiver overrides tire/pit inputs when it runs analyze.
_BCAST_TIRE_SETS = 2
_BCAST_PIT_LOSS_SEC = 8


class BroadcasterWindow(QWidget):
    _voice_done = Signal()

    def __init__(self, bind_host: str = "0.0.0.0", port: int = DEFAULT_RACE_LINK_PORT):
        super().__init__()
        ensure_default_config_file()
        self._config = load_config()
        self.telemetry = TelemetryTracker()
        self._bind_host = bind_host
        self._port = int(port)
        self._last_spoken_advice = ""

        self._voice_done.connect(self._on_voice_done)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("broadcasterRoot")
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.setStyleSheet(
            """
            QWidget#broadcasterRoot {
                background-color: rgb(12, 14, 20);
                color: #E8EEF2;
                border: 1px solid rgb(42, 46, 58);
                border-radius: 14px;
            }
            QWidget#broadcasterRoot QLabel {
                background: transparent;
                border: none;
            }
            QLabel#titleLabel {
                color: #E8F5E9;
                font-size: 14px;
                font-weight: 800;
                background: rgb(18, 20, 28);
                border: 1px solid rgb(50, 54, 66);
                border-radius: 10px;
                padding: 8px 12px;
            }
            QLabel#subLabel {
                color: #E8EEF2;
                font-size: 12px;
                font-weight: 700;
                background: rgb(24, 26, 34);
                border: 1px solid rgb(50, 54, 66);
                padding: 6px 10px;
                border-radius: 10px;
            }
            QLineEdit#lanNameEdit {
                background: rgb(12, 14, 20);
                color: #E8EEF2;
                border: 1px solid rgb(50, 54, 66);
                border-radius: 8px;
                padding: 8px 10px;
                font-weight: 600;
            }
            QPushButton#minBtn {
                background-color: rgb(42, 46, 58);
                color: #E8EEF2;
                font-weight: 800;
                border-radius: 10px;
                padding: 10px;
                border: 1px solid rgb(60, 64, 76);
            }
            QPushButton#minBtn:hover { background-color: rgb(56, 60, 72); }
            QPushButton#minBtn:pressed { background-color: rgb(34, 38, 48); }
            QPushButton#closeBtn {
                background-color: rgb(192, 57, 43);
                color: white;
                font-weight: 800;
                border-radius: 10px;
                padding: 10px;
                border: 1px solid rgb(140, 40, 30);
            }
            QPushButton#closeBtn:hover { background-color: rgb(210, 65, 50); }
            QPushButton#closeBtn:pressed { background-color: rgb(165, 48, 36); }
            """
        )

        title = QLabel("RACE LINK · SIM PC")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignCenter)

        row = QHBoxLayout()
        self.ir_badge = QLabel("iRacing: …")
        self.ir_badge.setObjectName("subLabel")
        self.clients_badge = QLabel("Engineer PC: …")
        self.clients_badge.setObjectName("subLabel")
        row.addWidget(self.ir_badge)
        row.addWidget(self.clients_badge)

        self.voice_badge = QLabel("Voice: waiting for engineer PC")
        self.voice_badge.setObjectName("subLabel")

        self.addr_label = QLabel("")
        self.addr_label.setObjectName("subLabel")
        self.addr_label.setWordWrap(True)

        lan_name_row = QHBoxLayout()
        lan_name_label = QLabel("LAN name")
        lan_name_label.setObjectName("subLabel")
        self.lan_name_edit = QLineEdit()
        self.lan_name_edit.setObjectName("lanNameEdit")
        self.lan_name_edit.setPlaceholderText("e.g. Logan's sim rig")
        self.lan_name_edit.setText(str(self._config.get("lan_display_name", "")))
        lan_name_row.addWidget(lan_name_label)
        lan_name_row.addWidget(self.lan_name_edit)

        lan_name_hint = QLabel("Shown on the engineer PC when discovering sim PCs on your network.")
        lan_name_hint.setObjectName("subLabel")
        lan_name_hint.setWordWrap(True)

        hint = QLabel(
            "On the engineer PC, start the app and choose Receiver — then click this machine in the LAN list."
        )
        hint.setObjectName("subLabel")
        hint.setWordWrap(True)

        min_btn = QPushButton("MINIMIZE")
        min_btn.setObjectName("minBtn")
        min_btn.clicked.connect(self.showMinimized)

        close_btn = QPushButton("CLOSE")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self._close)

        layout.addWidget(title)
        layout.addLayout(row)
        layout.addWidget(self.voice_badge)
        layout.addWidget(self.addr_label)
        layout.addLayout(lan_name_row)
        layout.addWidget(lan_name_hint)
        layout.addWidget(hint)
        layout.addWidget(min_btn)
        layout.addWidget(close_btn)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_config)
        self.lan_name_edit.textChanged.connect(lambda _t: self._save_timer.start())

        self._hist_timer = QTimer(self)
        self._hist_timer.timeout.connect(self.telemetry.update_field_history)
        self._hist_timer.start(250)

        self._service = BroadcasterService(self._build_snap_payload, bind_host=self._bind_host, port=self._port)
        self._service.clients_changed.connect(self._on_clients)
        self._service.iracing_changed.connect(self._on_iracing)
        self._service.advice_received.connect(self._on_advice)
        self._service.start()

        self._beacon = BroadcasterBeacon(self._port, self)
        self._beacon.set_status_provider(self._beacon_status)
        self._beacon.set_name_provider(lambda: self.lan_name_edit.text())
        self._beacon.start()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_ir_badge)
        self._status_timer.start(1000)

        self._refresh_addr()
        self._refresh_ir_badge()
        self._on_clients(self._service.client_count())

    def _save_config(self) -> None:
        try:
            data = dict(self._config)
            data["lan_display_name"] = str(self.lan_name_edit.text()).strip()
            write_config(data)
            self._config = load_config()
        except Exception:
            pass

    def _beacon_status(self) -> dict:
        return {
            "iracing": self.telemetry.is_connected(),
            "track": self.telemetry.track_name(),
        }

    def _lan_ip_hint(self) -> str:
        try:
            import socket

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _refresh_addr(self) -> None:
        ip = self._lan_ip_hint()
        bind = "all interfaces" if self._bind_host in ("0.0.0.0", "") else self._bind_host
        self.addr_label.setText(f"Streaming telemetry on {bind}:{self._port} · LAN IP {ip}")

    def _build_snap_payload(self) -> dict | None:
        connected = self.telemetry.ensure_connected()
        mode = self.telemetry.ui_mode()
        packet = None
        if connected:
            try:
                packet = json.loads(
                    self.telemetry.build_packet(
                        tire_sets_remaining=_BCAST_TIRE_SETS,
                        pit_loss_sec=_BCAST_PIT_LOSS_SEC,
                    )
                )
            except Exception:
                packet = None
        return {
            "iracing": connected,
            "mode": mode,
            "track": self.telemetry.track_name(),
            "track_mi": self.telemetry.track_length_miles(),
            "packet": packet,
        }

    def _on_advice(self, text: str, partial: bool, speak: bool, include_why: bool) -> None:
        if partial or not speak:
            return
        body = (text or "").strip()
        if not body or body.startswith("AI Error"):
            self.voice_badge.setText("Voice: engineer PC reported an error")
            return
        if body == self._last_spoken_advice:
            return
        self._last_spoken_advice = body
        self.voice_badge.setText("Voice: speaking engineer call…")

        def _speak() -> None:
            try:
                speak_engineer_advice(body, include_why=include_why)
            finally:
                self._voice_done.emit()

        threading.Thread(target=_speak, daemon=True).start()

    def _on_voice_done(self) -> None:
        if self._service.client_count() > 0:
            self.voice_badge.setText("Voice: ready")
        else:
            self.voice_badge.setText("Voice: waiting for engineer PC")

    def _on_clients(self, n: int) -> None:
        if n > 0:
            self.clients_badge.setText(f"Engineer PC: linked ({n})")
            color = "rgba(46, 204, 113, 140)"
            if self.voice_badge.text().startswith("Voice: waiting"):
                self.voice_badge.setText("Voice: ready")
        else:
            self.clients_badge.setText("Engineer PC: offline")
            color = "rgba(231, 76, 60, 160)"
            self.voice_badge.setText("Voice: waiting for engineer PC")
        self.clients_badge.setStyleSheet(f"QLabel#subLabel {{ border-color: {color}; }}")

    def _on_iracing(self, on: bool) -> None:
        self._set_ir_badge(on)

    def _refresh_ir_badge(self) -> None:
        self._set_ir_badge(self.telemetry.is_connected())

    def _set_ir_badge(self, on: bool) -> None:
        self.ir_badge.setText("iRacing: Online" if on else "iRacing: Offline")
        color = "rgba(46, 204, 113, 140)" if on else "rgba(231, 76, 60, 160)"
        self.ir_badge.setStyleSheet(f"QLabel#subLabel {{ border-color: {color}; }}")

    def _close(self) -> None:
        self._save_timer.stop()
        self._save_config()
        self._beacon.stop()
        self._service.stop()
        app = QGuiApplication.instance()
        if app is not None:
            app.quit()
        else:
            self.close()

    def closeEvent(self, event) -> None:
        self._save_timer.stop()
        self._save_config()
        self._beacon.stop()
        self._service.stop()
        super().closeEvent(event)
