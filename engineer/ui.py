import os
import sys
import threading
import json
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .app_config import (
    ensure_default_config_file,
    load_config,
    merge_config_defaults,
    write_config,
)
from .auto_alert_engine import AutoMonitorState, evaluate_auto_alert_tick, reset_auto_monitor_state
from .race_constants import ALERT_LAP_HORIZON, get_default_pit_loss_seconds, resolve_track_length_miles
from .strategy_engine import advice_call_line, run_strategy
from .strategy_worker import StrategyWorker
from .packet_snapshot import save_telemetry_packet
from .hotkey import (
    DEFAULT_HOTKEY,
    HOTKEY_CHOICES,
    AnalyzeHotkey,
    hotkey_display_label,
    hotkey_qt_sequence,
    mac_accessibility_trusted,
    normalize_hotkey,
)
from .race_link import DEFAULT_RACE_LINK_PORT, ReceiverClient
from .lan_discovery import LanDeviceDiscovery
from .receiver_theme import RECEIVER_QSS, make_card, make_collapsible_section, make_field_column, make_sidebar_scroll
from .remote_telemetry import RemoteTelemetrySource
from .speech import speak_engineer_advice
from .telemetry import TelemetryTracker


TELEMETRY_POLL_MS = 250
CONNECTION_POLL_MS = 1000
STREAM_UI_THROTTLE_MS = 80
REQUEST_WATCHDOG_MS = 30_000
BTN_LIVE = "ANALYZE FIELD & ADVISE"
BTN_STRATEGY = "GET RACE STRATEGY"
BTN_SAVE_PACKET = "SAVE PACKET"
BTN_DISCONNECTED = "ANALYZE FIELD & ADVISE"


def _first_nonempty_line(text: str) -> str:
    """Multi-line engineer replies keep the pit call on line 1 for parsers / voice."""
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s:
            return s
    return ""


def _standby_advice_text(is_receiver: bool) -> str:
    if is_receiver:
        return "Connect to a sim PC above, then press Analyze."
    return "Engineer standby — press Analyze or your hotkey when ready."


class AIRaceEngineer(QWidget):
    @staticmethod
    def _settings_heading(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("settingsSectionTitle")
        return lab

    @staticmethod
    def _setting_desc(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("settingDesc")
        lab.setWordWrap(True)
        return lab

    def _pill_toggle(self, checked: bool) -> QToolButton:
        b = QToolButton()
        b.setObjectName("toggleChip")
        b.setCheckable(True)
        b.setChecked(checked)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(28)

        def _label(on: bool) -> None:
            b.setText("ON" if on else "OFF")

        _label(checked)
        b.toggled.connect(_label)
        return b

    def __init__(self, role: str = "local", link_host: str | None = None, link_port: int | None = None):
        super().__init__()
        self._role = (role or "local").lower()
        if self._role not in ("local", "receiver"):
            self._role = "local"
        self._receiver_link: ReceiverClient | None = None

        self._ensure_default_config_file()
        _loaded_cfg = self._load_config()
        self._config = merge_config_defaults(_loaded_cfg)
        self._hotkey_migrated = normalize_hotkey(
            str(_loaded_cfg.get("analyze_hotkey", DEFAULT_HOTKEY))
        ) != str(self._config.get("analyze_hotkey", DEFAULT_HOTKEY))

        if self._role == "receiver":
            self.telemetry = RemoteTelemetrySource()
            self._receiver_link = ReceiverClient(self)
            self._receiver_link.snapshot.connect(self._on_remote_snapshot)
            self._receiver_link.link_changed.connect(self._on_receiver_link_changed)
        else:
            self.telemetry = TelemetryTracker()

        self._pit_user_modified = False
        self._tire_user_modified = False
        self._last_sdk_connected = None
        self._auto_monitor = AutoMonitorState()
        self._auto_last_call_line = ""
        self._last_delivered_call_line = ""
        self._baseline_strategy_text: str | None = None
        self._last_request_mode = "live"
        self._last_ui_mode: str | None = None
        self._strategy_compare_active = False
        self._link_host_boot = (link_host or "").strip() or str(self._config.get("race_link_host", "")).strip()
        self._link_port_boot = int(link_port if link_port is not None else self._config.get("race_link_port", DEFAULT_RACE_LINK_PORT))
        if self._role == "receiver":
            self._link_host = self._link_host_boot
            self._link_port = self._link_port_boot

        self._is_receiver = self._role == "receiver"

        if self._is_receiver:
            self.setWindowTitle("AI Race Engineer — Engineer PC")
            self.setWindowFlags(Qt.Window)
            self.setMinimumSize(960, 760)
            self.resize(1040, 900)
        else:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setMinimumWidth(520)
            self.setMaximumWidth(720)
            _scr = QGuiApplication.primaryScreen()
            if _scr is not None:
                _ah = _scr.availableGeometry().height()
                self.setMaximumHeight(max(620, int(_ah * 0.92)))

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(16 if self._is_receiver else 12, 16 if self._is_receiver else 12, 16 if self._is_receiver else 12, 16 if self._is_receiver else 12)
        self.layout.setSpacing(12 if self._is_receiver else 10)

        self.setObjectName("rootOverlay")
        self.setStyleSheet(
            """
            QWidget#rootOverlay {
                background-color: rgb(12, 14, 20);
                color: #E8EEF2;
                border: 1px solid rgb(42, 46, 58);
                border-radius: 14px;
            }
            QWidget {
                font-size: 13px;
            }
            QLabel#statusLabel {
                color: #E8F5E9;
                font-size: 17px;
                font-weight: 700;
                line-height: 142%;
                background: rgb(18, 20, 28);
                border: 1px solid rgb(50, 54, 66);
                padding: 12px 12px;
                border-radius: 12px;
            }
            QWidget#advicePanel {
                background-color: rgb(18, 20, 28);
                border: 1px solid rgb(50, 54, 66);
                border-radius: 12px;
            }
            QLabel#adviceHeader {
                color: rgba(180, 220, 190, 255);
                font-size: 11px;
                font-weight: 800;
                background: transparent;
                border: none;
                padding: 0;
                letter-spacing: 0.06em;
            }
            QLabel#adviceText {
                color: #E8F5E9;
                font-family: "Menlo", "Consolas", "Courier New", monospace;
                font-size: 10px;
                font-weight: 500;
                line-height: 145%;
                background: transparent;
                border: none;
                padding: 0;
            }
            QLabel#adviceBaselineText {
                color: rgba(190, 210, 195, 230);
                font-family: "Menlo", "Consolas", "Courier New", monospace;
                font-size: 9px;
                font-weight: 500;
                line-height: 145%;
                background: transparent;
                border: none;
                padding: 0;
            }
            QLabel#adviceColumnHeader {
                color: rgba(160, 185, 165, 255);
                font-size: 10px;
                font-weight: 800;
                background: transparent;
                border: none;
                padding: 0 0 4px 0;
                letter-spacing: 0.06em;
            }
            QLabel#adviceLiveHeader {
                color: rgba(180, 220, 190, 255);
                font-size: 10px;
                font-weight: 800;
                background: transparent;
                border: none;
                padding: 0 0 4px 0;
                letter-spacing: 0.06em;
            }
            QWidget#baselineStrategyColumn {
                background-color: rgb(14, 16, 22);
                border: 1px solid rgb(42, 46, 58);
                border-radius: 10px;
            }
            QWidget#liveStrategyColumn {
                background: transparent;
                border: none;
            }
            QLabel#subLabel {
                color: #E8EEF2;
                font-size: 12px;
                font-weight: 700;
                background: rgb(24, 26, 34);
                border: 1px solid rgb(50, 54, 66);
                padding: 4px 8px;
                border-radius: 10px;
            }
            QLabel#connBadge {
                color: #E8EEF2;
                font-size: 12px;
                font-weight: 800;
                background: rgb(24, 26, 34);
                border: 1px solid rgb(50, 54, 66);
                padding: 4px 8px;
                border-radius: 10px;
            }
            QPushButton#analyzeBtn {
                background-color: rgb(31, 138, 76);
                color: white;
                font-weight: 700;
                border-radius: 12px;
                padding: 12px;
                border: 1px solid rgb(25, 110, 60);
            }
            QPushButton#analyzeBtn:hover { background-color: rgb(35, 154, 85); }
            QPushButton#analyzeBtn:pressed { background-color: rgb(25, 122, 67); }
            QPushButton#analyzeBtn:disabled {
                background-color: rgb(28, 62, 44);
                color: rgb(180, 190, 185);
            }
            QPushButton#clearBtn {
                background-color: rgb(24, 26, 34);
                color: #E8EEF2;
                font-weight: 700;
                border-radius: 12px;
                padding: 10px;
                border: 1px solid rgb(50, 54, 66);
            }
            QPushButton#clearBtn:hover { background-color: rgb(32, 34, 42); }
            QPushButton#clearBtn:pressed { background-color: rgb(18, 20, 28); }
            QToolButton#settingsBtn {
                background-color: rgb(24, 26, 34);
                color: #E8EEF2;
                font-weight: 900;
                border-radius: 12px;
                padding: 10px 12px;
                border: 1px solid rgb(58, 62, 74);
                text-align: left;
            }
            QToolButton#settingsBtn:hover { background-color: rgb(32, 34, 42); }
            QToolButton#settingsBtn:pressed { background-color: rgb(18, 20, 28); }
            QPushButton#closeBtn {
                background-color: rgb(192, 57, 43);
                color: white;
                font-weight: 800;
                border-radius: 12px;
                padding: 10px;
                border: 1px solid rgb(140, 40, 30);
            }
            QPushButton#closeBtn:hover { background-color: rgb(210, 65, 50); }
            QPushButton#closeBtn:pressed { background-color: rgb(165, 48, 36); }
            QSpinBox#tireSpin, QSpinBox#pitSpin, QComboBox#pitSpin, QLineEdit#pitSpin {
                background-color: rgb(18, 20, 28);
                color: white;
                border-radius: 10px;
                padding: 8px 12px;
                border: 1px solid rgb(50, 54, 66);
                min-width: 110px;
                font-weight: 700;
                font-size: 14px;
            }
            QSpinBox#tireSpin::up-button, QSpinBox#tireSpin::down-button,
            QSpinBox#pitSpin::up-button, QSpinBox#pitSpin::down-button,
            QComboBox#pitSpin::drop-down {
                width: 34px;
                border-radius: 10px;
                background: rgb(42, 46, 58);
                border: 1px solid rgb(58, 62, 74);
            }
            QSpinBox#tireSpin::up-button:hover, QSpinBox#tireSpin::down-button:hover,
            QSpinBox#pitSpin::up-button:hover, QSpinBox#pitSpin::down-button:hover {
                background: rgb(52, 56, 68);
            }
            QSpinBox#tireSpin::up-button:pressed, QSpinBox#tireSpin::down-button:pressed,
            QSpinBox#pitSpin::up-button:pressed, QSpinBox#pitSpin::down-button:pressed {
                background: rgb(36, 40, 50);
            }
            QSpinBox#tireSpin::up-arrow, QSpinBox#pitSpin::up-arrow {
                width: 18px;
                height: 18px;
                image: url("assets/spin_up.svg");
            }
            QSpinBox#tireSpin::down-arrow, QSpinBox#pitSpin::down-arrow {
                width: 18px;
                height: 18px;
                image: url("assets/spin_down.svg");
            }
            QWidget#settingsPanel {
                background-color: rgb(18, 20, 28);
                border: 1px solid rgb(50, 54, 66);
                border-radius: 12px;
            }
            QWidget#mainColumn {
                background: transparent;
                border: none;
            }
            QLabel#settingsSectionTitle {
                color: rgba(190, 200, 215, 255);
                font-size: 11px;
                font-weight: 800;
                background: transparent;
                border: none;
                padding: 6px 0 2px 0;
                letter-spacing: 0.04em;
            }
            QLabel#settingsHint {
                color: rgba(175, 185, 200, 230);
                font-size: 11px;
                font-weight: 600;
                background: transparent;
                border: none;
                padding: 0 0 4px 0;
            }
            QToolButton#toggleChip {
                background-color: rgb(55, 58, 70);
                color: #E8EEF2;
                font-weight: 800;
                font-size: 12px;
                border-radius: 14px;
                padding: 6px 14px;
                border: 1px solid rgb(68, 72, 84);
                min-width: 52px;
                max-width: 52px;
            }
            QToolButton#toggleChip:hover {
                background-color: rgb(65, 68, 82);
            }
            QToolButton#toggleChip:checked {
                background-color: rgb(31, 138, 76);
                border: 1px solid rgb(45, 160, 95);
            }
            QToolButton#toggleChip:checked:hover {
                background-color: rgb(35, 154, 85);
            }
            QToolButton#toggleChip:disabled {
                color: rgb(140, 145, 155);
                background-color: rgb(36, 38, 46);
            }
            QWidget#lanPanel {
                background-color: rgb(18, 20, 28);
                border: 1px solid rgb(50, 54, 66);
                border-radius: 12px;
            }
            QLabel#lanPanelTitle {
                color: rgba(190, 200, 215, 255);
                font-size: 11px;
                font-weight: 800;
                background: transparent;
                border: none;
                padding: 0;
                letter-spacing: 0.04em;
            }
            QListWidget#lanDeviceList {
                background-color: rgb(14, 16, 22);
                color: #E8EEF2;
                border: 1px solid rgb(42, 46, 58);
                border-radius: 10px;
                padding: 4px;
                outline: none;
            }
            QListWidget#lanDeviceList::item {
                padding: 10px 8px;
                border-radius: 8px;
            }
            QListWidget#lanDeviceList::item:selected {
                background-color: rgb(31, 138, 76);
                color: white;
            }
            QListWidget#lanDeviceList::item:hover {
                background-color: rgb(32, 34, 42);
            }
            """
        )
        if self._is_receiver:
            self.setStyleSheet(self.styleSheet() + RECEIVER_QSS)

        self._lan_panel: QWidget | None = None
        self._lan_discovery: LanDeviceDiscovery | None = None

        self.advice_panel = QWidget()
        self.advice_panel.setObjectName("advicePanel")
        advice_layout = QVBoxLayout(self.advice_panel)
        advice_layout.setContentsMargins(14, 12, 14, 14)
        advice_layout.setSpacing(8)
        self.advice_header = QLabel("Engineer call" if self._is_receiver else "RACE ENGINEER")
        self.advice_header.setObjectName("adviceHeader")
        self.advice_label = QLabel(_standby_advice_text(self._is_receiver))
        self.advice_label.setObjectName("adviceStandby" if self._is_receiver else "adviceText")
        self.advice_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.advice_label.setWordWrap(True)
        self.advice_label.setMinimumHeight(280 if self._is_receiver else 160)
        self.advice_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        self.baseline_strategy_column: QWidget | None = None
        self.baseline_strategy_label: QLabel | None = None
        self.live_strategy_column: QWidget | None = None
        self.advice_compare_row: QHBoxLayout | None = None

        if not self._is_receiver:
            self.baseline_strategy_column = QWidget()
            self.baseline_strategy_column.setObjectName("baselineStrategyColumn")
            baseline_col_layout = QVBoxLayout(self.baseline_strategy_column)
            baseline_col_layout.setContentsMargins(10, 10, 10, 10)
            baseline_col_layout.setSpacing(6)
            self.baseline_header = QLabel("BASE PLAN")
            self.baseline_header.setObjectName("adviceColumnHeader")
            self.baseline_strategy_label = QLabel("")
            self.baseline_strategy_label.setObjectName("adviceBaselineText")
            self.baseline_strategy_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.baseline_strategy_label.setWordWrap(True)
            self.baseline_strategy_label.setMinimumHeight(140)
            self.baseline_strategy_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            baseline_col_layout.addWidget(self.baseline_header)
            baseline_col_layout.addWidget(self.baseline_strategy_label, 1)
            self.baseline_strategy_column.setVisible(False)

            self.live_strategy_column = QWidget()
            self.live_strategy_column.setObjectName("liveStrategyColumn")
            live_col_layout = QVBoxLayout(self.live_strategy_column)
            live_col_layout.setContentsMargins(0, 0, 0, 0)
            live_col_layout.setSpacing(6)
            self.live_header = QLabel("LIVE CALL")
            self.live_header.setObjectName("adviceLiveHeader")
            live_col_layout.addWidget(self.live_header)
            live_col_layout.addWidget(self.advice_label, 1)

            self.advice_compare_row = QHBoxLayout()
            self.advice_compare_row.setContentsMargins(0, 0, 0, 0)
            self.advice_compare_row.setSpacing(10)
            self.advice_compare_row.addWidget(self.baseline_strategy_column, 1)
            self.advice_compare_row.addWidget(self.live_strategy_column, 1)

            advice_layout.addWidget(self.advice_header)
            advice_layout.addLayout(self.advice_compare_row, 1)
        else:
            advice_layout.addWidget(self.advice_label)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self.conn_badge = QLabel("Telemetry: …" if self._is_receiver else "iRacing: …")
        self.conn_badge.setObjectName("statusPill" if self._is_receiver else "connBadge")
        self.ai_badge = QLabel("Engine · Idle")
        self.ai_badge.setObjectName("statusPill" if self._is_receiver else "connBadge")
        top_row.addWidget(self.conn_badge)
        top_row.addWidget(self.ai_badge)
        top_row.addStretch(1)

        self._header_widget: QWidget | None = None
        self._advice_card: QWidget | None = None
        self._action_bar: QWidget | None = None

        if self._role == "receiver":
            self.lan_panel, lan_inner = make_card(
                "STEP 1",
                "Connect to sim PC",
                "Pick the machine running iRacing in broadcaster mode. Advice is read aloud there.",
            )
            self.lan_panel.setObjectName("card")
            lan_toolbar = QHBoxLayout()
            lan_toolbar.setContentsMargins(0, 0, 0, 0)
            lan_toolbar.addStretch(1)
            self.lan_refresh_btn = QPushButton("Refresh")
            self.lan_refresh_btn.setObjectName("ghostBtn")
            self.lan_refresh_btn.setCursor(Qt.PointingHandCursor)
            self.lan_refresh_btn.clicked.connect(self._refresh_lan_device_list)
            lan_toolbar.addWidget(self.lan_refresh_btn)
            self.lan_device_list = QListWidget()
            self.lan_device_list.setObjectName("lanDeviceList")
            self.lan_device_list.setMinimumHeight(108)
            self.lan_device_list.setMaximumHeight(180)
            self.lan_device_list.itemClicked.connect(self._on_lan_device_clicked)
            self.lan_status_label = QLabel("")
            self.lan_status_label.setObjectName("cardHint")
            lan_inner.addLayout(lan_toolbar)
            lan_inner.addWidget(self.lan_device_list)
            lan_inner.addWidget(self.lan_status_label)
            self._lan_panel = self.lan_panel

            self._advice_card, advice_card_layout = make_card(
                "STEP 2",
                "Review engineer call",
                "Press Analyze when you want a pit/strategy recommendation.",
            )
            self._advice_card.setObjectName("card")
            advice_card_layout.addWidget(self.advice_label, 1)

            self._lan_discovery = LanDeviceDiscovery(self)
            self._lan_discovery.devices_changed.connect(self._refresh_lan_device_list)
            self._lan_discovery.start()
            self._refresh_lan_device_list()

        self.rejoin_label = QLabel("Pit-road impact: …")
        self.rejoin_label.setObjectName("cardHint" if self._is_receiver else "subLabel")
        self.rejoin_label.setWordWrap(True)
        self.rejoin_label.setVisible(False)
        if not bool(self._config.get("show_pit_impact", True)):
            self.rejoin_label.hide()

        self.btn = QPushButton(BTN_DISCONNECTED)
        self.btn.setObjectName("analyzeBtn")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setMinimumHeight(52 if self._is_receiver else 48)
        self.btn.clicked.connect(self.trigger_ai_request)

        self.save_packet_btn = QPushButton(BTN_SAVE_PACKET)
        self.save_packet_btn.setObjectName("ghostBtn")
        self.save_packet_btn.setCursor(Qt.PointingHandCursor)
        self.save_packet_btn.setToolTip(
            "Dump the current telemetry JSON to sim_logs/packets/ (Ctrl+Shift+S)."
        )
        self.save_packet_btn.clicked.connect(self.save_packet_snapshot)

        self.hotkey_hint = QLabel("")
        self.hotkey_hint.setObjectName("settingsHint")
        self.hotkey_hint.setWordWrap(True)

        action_col = QVBoxLayout()
        action_col.setContentsMargins(0, 0, 0, 0)
        action_col.setSpacing(4)
        if not self._is_receiver:
            action_col.addWidget(self.btn)
            action_col.addWidget(self.save_packet_btn)
            action_col.addWidget(self.hotkey_hint)

        tire_row = QHBoxLayout()
        tire_row.setContentsMargins(0, 0, 0, 0)
        tire_label = QLabel("Tire sets left")
        tire_label.setObjectName("subLabel")
        self.tire_spin = QSpinBox()
        self.tire_spin.setObjectName("tireSpin")
        self.tire_spin.setMinimum(0)
        self.tire_spin.setMaximum(99)
        self.tire_spin.setValue(2)
        self.tire_spin.setToolTip(
            "Auto-updates from iRacing when the series limits tire sets (SDK TireSetsAvailable). "
            "Change manually to override; 255/unlimited sessions keep your value."
        )
        self.tire_spin.valueChanged.connect(self._on_tire_spin_changed)
        pit_label = QLabel("Pit-road loss")
        pit_label.setObjectName("subLabel")
        self.pit_spin = QSpinBox()
        self.pit_spin.setObjectName("pitSpin")
        self.pit_spin.setMinimum(0)
        self.pit_spin.setMaximum(300)
        self.pit_spin.setSuffix(" s")
        self.pit_spin.setValue(8)
        self.pit_spin.setToolTip("Seconds lost versus green-flag laps (entry + stop + exit).")
        self.pit_spin.valueChanged.connect(self._on_pit_spin_changed)
        if self._is_receiver:
            strategy_row = None
            self._action_bar = QWidget()
            self._action_bar.setObjectName("actionBar")
            action_bar_layout = QHBoxLayout(self._action_bar)
            action_bar_layout.setContentsMargins(16, 14, 16, 14)
            action_bar_layout.setSpacing(16)
            action_bar_layout.addLayout(make_field_column("Pit-road loss (sec)", self.pit_spin))
            action_bar_layout.addLayout(make_field_column("Fresh tire sets", self.tire_spin))
            action_bar_layout.addStretch(1)
            self.clear_btn = QPushButton("Clear")
            self.clear_btn.setObjectName("clearBtn")
            self.clear_btn.setCursor(Qt.PointingHandCursor)
            self.clear_btn.clicked.connect(self.clear_and_cancel)
            action_bar_layout.addWidget(self.clear_btn)
            action_bar_layout.addWidget(self.btn)
        else:
            strategy_row = None
            tire_row.addWidget(tire_label)
            tire_row.addWidget(self.tire_spin)
            tire_row.addStretch(1)

        settings_toggle_row = QHBoxLayout()
        settings_toggle_row.setContentsMargins(0, 0, 0, 0)
        self.settings_toggle = QToolButton()
        self.settings_toggle.setText("SETTINGS")
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(False)
        self.settings_toggle.setArrowType(Qt.RightArrow)
        self.settings_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.settings_toggle.setCursor(Qt.PointingHandCursor)
        self.settings_toggle.setObjectName("settingsBtn")
        self.settings_toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.settings_toggle.setMinimumHeight(40)
        settings_toggle_row.addWidget(self.settings_toggle, 1)

        if not self._is_receiver:
            pit_row = QHBoxLayout()
            pit_row.setContentsMargins(0, 0, 0, 0)
            pit_row.addWidget(pit_label)
            pit_row.addWidget(self.pit_spin)
            pit_row.addStretch(1)

        auto_pit_row = QHBoxLayout()
        auto_pit_row.setContentsMargins(0, 0, 0, 0)
        auto_pit_label = QLabel("Auto pit loss on connect")
        auto_pit_label.setObjectName("subLabel")
        self.auto_track_pit_toggle = self._pill_toggle(bool(self._config.get("auto_apply_track_pit_loss", True)))
        self.auto_track_pit_toggle.setToolTip(
            "When on, applies a track-length-based default when iRacing connects (unless you changed pit loss)."
        )
        auto_pit_row.addWidget(auto_pit_label)
        auto_pit_row.addStretch(1)
        auto_pit_row.addWidget(self.auto_track_pit_toggle)

        self.pit_defaults_btn = QPushButton("Apply track default now")
        self.pit_defaults_btn.setObjectName("clearBtn")
        self.pit_defaults_btn.setCursor(Qt.PointingHandCursor)
        self.pit_defaults_btn.setToolTip("Sets pit-road loss from track length immediately.")
        self.pit_defaults_btn.clicked.connect(self._apply_track_default_pit_loss)

        clear_sec_init = int(self._config.get("clear_after_sec", 120))
        clear_on = clear_sec_init > 0
        auto_clear_row = QHBoxLayout()
        auto_clear_row.setContentsMargins(0, 0, 0, 0)
        auto_clear_label = QLabel("Auto-clear advice")
        auto_clear_label.setObjectName("subLabel")
        self.auto_clear_toggle = self._pill_toggle(clear_on)
        self.clear_after_spin = QSpinBox()
        self.clear_after_spin.setObjectName("pitSpin")
        self.clear_after_spin.setMinimum(10)
        self.clear_after_spin.setMaximum(600)
        self.clear_after_spin.setSuffix(" s")
        self.clear_after_spin.setValue(max(10, min(600, clear_sec_init)) if clear_on else 120)
        self.clear_after_spin.setVisible(clear_on)
        self.clear_after_spin.setEnabled(clear_on)
        self.clear_after_spin.setToolTip("How long to show the last call before the overlay clears.")
        auto_clear_row.addWidget(auto_clear_label)
        auto_clear_row.addStretch(1)
        auto_clear_row.addWidget(self.clear_after_spin)
        auto_clear_row.addWidget(self.auto_clear_toggle)

        clear_hint = self._setting_desc("Off keeps the last call on screen until you clear or analyze again.")

        rejoin_row = QHBoxLayout()
        rejoin_row.setContentsMargins(0, 0, 0, 0)
        rejoin_label_setting = QLabel("Show pit-impact line")
        rejoin_label_setting.setObjectName("subLabel")
        self.show_pit_impact_toggle = self._pill_toggle(bool(self._config.get("show_pit_impact", True)))
        self.show_pit_impact_toggle.setToolTip(
            "After a PIT call, shows estimated positions lost based on pit-road loss."
        )
        rejoin_row.addWidget(rejoin_label_setting)
        rejoin_row.addStretch(1)
        rejoin_row.addWidget(self.show_pit_impact_toggle)

        rejoin_hint = self._setting_desc(
            "Under yellow, shows on-track position vs lead-lap loss and restart rows."
        )

        auto_alert_row = QHBoxLayout()
        auto_alert_row.setContentsMargins(0, 0, 0, 0)
        auto_alert_label = QLabel("Auto pit alerts")
        auto_alert_label.setObjectName("subLabel")
        self.auto_pit_alerts_toggle = self._pill_toggle(bool(self._config.get("auto_pit_alerts", True)))
        self.auto_pit_alerts_toggle.setToolTip(
            f"Alert every lap and under caution when a pit is due within {ALERT_LAP_HORIZON} laps."
        )
        auto_alert_row.addWidget(auto_alert_label)
        auto_alert_row.addStretch(1)
        auto_alert_row.addWidget(self.auto_pit_alerts_toggle)
        auto_pit_alert_desc = self._setting_desc(
            f"Shows the call and sends voice to the sim PC when a stop is due within {ALERT_LAP_HORIZON} laps."
        )

        voice_enable_row = QHBoxLayout()
        voice_enable_row.setContentsMargins(0, 0, 0, 0)
        voice_enable_label = QLabel("Voice enabled")
        voice_enable_label.setObjectName("subLabel")
        self.voice_enabled_toggle = self._pill_toggle(bool(self._config.get("voice_enabled", True)))
        self.voice_enabled_toggle.setToolTip("Read engineer calls aloud on this PC (single-PC / local mode).")
        voice_enable_row.addWidget(voice_enable_label)
        voice_enable_row.addStretch(1)
        voice_enable_row.addWidget(self.voice_enabled_toggle)

        voice_sim_row = QHBoxLayout()
        voice_sim_row.setContentsMargins(0, 0, 0, 0)
        voice_sim_label = QLabel("Speak on sim PC")
        voice_sim_label.setObjectName("subLabel")
        self.voice_sim_toggle = self._pill_toggle(bool(self._config.get("voice_sim_enabled", True)))
        self.voice_sim_toggle.setToolTip("Send finished calls to the sim PC for text-to-speech in the headset.")
        voice_sim_row.addWidget(voice_sim_label)
        voice_sim_row.addStretch(1)
        voice_sim_row.addWidget(self.voice_sim_toggle)

        voice_sim_desc = self._setting_desc("Reads the finished engineer call aloud on the sim PC.")
        voice_why_desc = self._setting_desc("Also reads the WHY explanation line after the pit call.")

        voice_row = QHBoxLayout()
        voice_row.setContentsMargins(0, 0, 0, 0)
        voice_label = QLabel("Speak WHY after call")
        voice_label.setObjectName("subLabel")
        self.voice_why_toggle = self._pill_toggle(bool(self._config.get("voice_read_why", True)))
        self.voice_why_toggle.setToolTip("After the action line, reads the WHY line aloud.")
        voice_row.addWidget(voice_label)
        voice_row.addStretch(1)
        voice_row.addWidget(self.voice_why_toggle)

        voice_hint = self._setting_desc(
            "Sim PC: Windows neural voice (edge-tts) or SAPI; macOS say; Linux espeak."
            if self._is_receiver
            else "Reads calls on this PC (Windows: neural edge-tts when installed; macOS say; Linux espeak)."
        )

        auto_pit_desc = self._setting_desc("Sets pit-road loss from track length when telemetry connects.")
        pit_defaults_desc = self._setting_desc("Immediately applies the track-length pit-loss estimate.")

        hotkey_enable_desc = self._setting_desc("Turn off to use only the Analyze button.")
        hotkey_combo_desc = self._setting_desc("Key that triggers Analyze (default: Spacebar).")

        hotkey_row = QHBoxLayout()
        hotkey_row.setContentsMargins(0, 0, 0, 0)
        hotkey_label = QLabel("Analyze hotkey")
        hotkey_label.setObjectName("subLabel")
        self.hotkey_combo = QComboBox()
        self.hotkey_combo.setObjectName("pitSpin")
        for k in HOTKEY_CHOICES:
            self.hotkey_combo.addItem(hotkey_display_label(k), k)
        _hk = normalize_hotkey(str(self._config.get("analyze_hotkey", DEFAULT_HOTKEY)))
        idx = self.hotkey_combo.findData(_hk)
        self.hotkey_combo.setCurrentIndex(idx if idx >= 0 else 0)
        hotkey_row.addWidget(hotkey_label)
        hotkey_row.addWidget(self.hotkey_combo)
        hotkey_row.addStretch(1)

        hotkey_enable_row = QHBoxLayout()
        hotkey_enable_row.setContentsMargins(0, 0, 0, 0)
        hotkey_enable_label = QLabel("Hotkey enabled")
        hotkey_enable_label.setObjectName("subLabel")
        self.hotkey_enabled_toggle = self._pill_toggle(bool(self._config.get("analyze_hotkey_enabled", True)))
        hotkey_enable_row.addWidget(hotkey_enable_label)
        hotkey_enable_row.addStretch(1)
        hotkey_enable_row.addWidget(self.hotkey_enabled_toggle)

        hotkey_hint_settings = self._setting_desc(
            "On Mac, grant Accessibility for a global hotkey outside this window."
            if self._is_receiver
            else "Bind the same F-key in iRacing for a wheel button. Global on Windows."
        )

        self._save_config_timer = QTimer(self)
        self._save_config_timer.setSingleShot(True)
        self._save_config_timer.timeout.connect(self._save_config)
        self.clear_after_spin.valueChanged.connect(self._schedule_save_config)
        self.voice_enabled_toggle.toggled.connect(self._on_voice_settings_changed)
        self.voice_sim_toggle.toggled.connect(self._on_voice_settings_changed)
        self.voice_why_toggle.toggled.connect(self._on_voice_settings_changed)
        self.show_pit_impact_toggle.toggled.connect(self._on_show_pit_impact_toggled)
        self.auto_pit_alerts_toggle.toggled.connect(self._schedule_save_config)
        self.auto_track_pit_toggle.toggled.connect(lambda _v: self._schedule_save_config(0))
        self.auto_clear_toggle.toggled.connect(self._on_auto_clear_toggled)
        self.hotkey_enabled_toggle.toggled.connect(self._on_hotkey_settings_changed)
        self.hotkey_combo.currentTextChanged.connect(self._on_hotkey_settings_changed)

        self.settings_widget = QWidget()
        self.settings_widget.setObjectName("settingsPanel")
        settings_layout = QVBoxLayout()
        settings_layout.setContentsMargins(12, 10, 12, 12)
        settings_layout.setSpacing(8)

        self._sidebar_scroll = None
        if self._is_receiver:
            self.sidebar_inner = QWidget()
            self.sidebar_inner.setObjectName("settingsPanel")
            sidebar_root = QVBoxLayout(self.sidebar_inner)
            sidebar_root.setContentsMargins(12, 10, 12, 12)
            sidebar_root.setSpacing(10)

            advice_sec, advice_body = make_collapsible_section("ADVICE", expanded=True)
            advice_body.addLayout(auto_clear_row)
            advice_body.addWidget(clear_hint)
            advice_body.addLayout(rejoin_row)
            advice_body.addWidget(rejoin_hint)
            advice_body.addLayout(auto_alert_row)
            advice_body.addWidget(auto_pit_alert_desc)
            sidebar_root.addWidget(advice_sec)

            pit_sec, pit_body = make_collapsible_section("PIT", expanded=False)
            pit_body.addLayout(auto_pit_row)
            pit_body.addWidget(auto_pit_desc)
            pit_body.addWidget(self.pit_defaults_btn)
            pit_body.addWidget(pit_defaults_desc)
            sidebar_root.addWidget(pit_sec)

            voice_sec, voice_body = make_collapsible_section("VOICE", expanded=True)
            voice_body.addLayout(voice_sim_row)
            voice_body.addWidget(voice_sim_desc)
            voice_body.addLayout(voice_row)
            voice_body.addWidget(voice_why_desc)
            voice_body.addWidget(voice_hint)
            sidebar_root.addWidget(voice_sec)

            hotkey_sec, hotkey_body = make_collapsible_section("HOTKEY", expanded=False)
            hotkey_body.addLayout(hotkey_enable_row)
            hotkey_body.addWidget(hotkey_enable_desc)
            hotkey_body.addLayout(hotkey_row)
            hotkey_body.addWidget(hotkey_combo_desc)
            hotkey_body.addWidget(hotkey_hint_settings)
            sidebar_root.addWidget(hotkey_sec)

            sidebar_root.addStretch(1)
            self._sidebar_scroll = make_sidebar_scroll(self.sidebar_inner)
            self._sidebar_scroll.setMinimumWidth(300)
            self._sidebar_scroll.setMaximumWidth(340)
            self._sidebar_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        else:
            settings_layout.addWidget(self._settings_heading("RACE · Pit"))
            settings_layout.addLayout(pit_row)
            settings_layout.addLayout(auto_pit_row)
            settings_layout.addWidget(self.pit_defaults_btn)
            settings_layout.addWidget(self._settings_heading("ADVICE"))
            settings_layout.addLayout(auto_clear_row)
            settings_layout.addWidget(clear_hint)
            settings_layout.addLayout(rejoin_row)
            settings_layout.addWidget(rejoin_hint)
            settings_layout.addLayout(auto_alert_row)
            settings_layout.addWidget(auto_pit_alert_desc)
            settings_layout.addWidget(self._settings_heading("VOICE"))
            settings_layout.addLayout(voice_enable_row)
            settings_layout.addWidget(self._setting_desc("Reads engineer calls aloud on this PC."))
            settings_layout.addLayout(voice_row)
            settings_layout.addWidget(voice_why_desc)
            settings_layout.addWidget(voice_hint)
            settings_layout.addWidget(self._settings_heading("HOTKEY"))
            settings_layout.addLayout(hotkey_enable_row)
            settings_layout.addWidget(hotkey_enable_desc)
            settings_layout.addLayout(hotkey_row)
            settings_layout.addWidget(hotkey_combo_desc)
            settings_layout.addWidget(hotkey_hint_settings)
            self.settings_widget.setLayout(settings_layout)

        if self._is_receiver:
            self.settings_widget.setVisible(False)
            self.settings_toggle.setVisible(False)
        else:
            self.settings_widget.setVisible(False)

        def _toggle_settings(checked: bool):
            if self._is_receiver:
                return
            self.settings_widget.setVisible(checked)
            self.settings_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
            self.layout.activate()
            self._relayout_overlay()

        self.settings_toggle.toggled.connect(_toggle_settings)

        if not self._is_receiver:
            self.clear_btn = QPushButton("CLEAR / CANCEL")
            self.clear_btn.setObjectName("clearBtn")
            self.clear_btn.setCursor(Qt.PointingHandCursor)
            self.clear_btn.clicked.connect(self.clear_and_cancel)

        self.close_btn = QPushButton("Quit" if self._is_receiver else "CLOSE")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.close_app)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        if not self._is_receiver:
            bottom_row.addWidget(self.clear_btn)
            bottom_row.addWidget(self.close_btn)

        if self._is_receiver:
            header = QWidget()
            header.setObjectName("headerBar")
            header_layout = QVBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(10)
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(12)
            title_block = QVBoxLayout()
            title_block.setSpacing(2)
            app_title = QLabel("AI Race Engineer")
            app_title.setObjectName("appTitle")
            app_sub = QLabel("Engineer workstation — voice plays on the sim PC")
            app_sub.setObjectName("appSubtitle")
            title_block.addWidget(app_title)
            title_block.addWidget(app_sub)
            title_row.addLayout(title_block, 1)
            self.close_btn.setFixedHeight(34)
            self.close_btn.setMinimumWidth(76)
            title_row.addWidget(self.close_btn, 0, Qt.AlignTop)
            header_layout.addLayout(title_row)
            status_wrap = QWidget()
            status_wrap.setObjectName("statusBar")
            status_layout = QHBoxLayout(status_wrap)
            status_layout.setContentsMargins(10, 8, 10, 8)
            status_layout.setSpacing(8)
            status_layout.addWidget(self.conn_badge)
            status_layout.addWidget(self.ai_badge)
            status_layout.addStretch(1)
            header_layout.addWidget(status_wrap)
            self._header_widget = header
            self.layout.addWidget(header)

            left_col = QWidget()
            left_col.setObjectName("mainColumn")
            left_layout = QVBoxLayout(left_col)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(14)
            if self._lan_panel is not None:
                left_layout.addWidget(self._lan_panel)
            if self._advice_card is not None:
                left_layout.addWidget(self._advice_card, 1)
            if self._action_bar is not None:
                left_layout.addWidget(self._action_bar)
            if self.rejoin_label is not None:
                left_layout.addWidget(self.rejoin_label)

            split_row = QHBoxLayout()
            split_row.setContentsMargins(0, 0, 0, 0)
            split_row.setSpacing(20)
            split_row.addWidget(left_col, 1)
            if self._sidebar_scroll is not None:
                split_row.addWidget(self._sidebar_scroll, 0)
            self.layout.addLayout(split_row, 1)
        else:
            self.layout.addLayout(top_row)
            if self._lan_panel is not None:
                self.layout.addWidget(self._lan_panel)
            self.layout.addWidget(self.advice_panel)
            self.layout.addLayout(tire_row)
            self.layout.addLayout(action_col)
            if self.rejoin_label is not None:
                self.layout.addWidget(self.rejoin_label)
            self.layout.addLayout(settings_toggle_row)
            self.layout.addWidget(self.settings_widget)

        if not self._is_receiver:
            self.layout.addLayout(bottom_row)
        self.setLayout(self.layout)

        self._analyze_hotkey = AnalyzeHotkey(self._hotkey_trigger_analyze)
        self._local_hotkey = QShortcut(QKeySequence(), self)
        self._local_hotkey.setContext(Qt.ApplicationShortcut)
        self._local_hotkey.activated.connect(self._hotkey_trigger_analyze)
        self._apply_analyze_hotkey()
        self._packet_save_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self._packet_save_shortcut.setContext(Qt.ApplicationShortcut)
        self._packet_save_shortcut.activated.connect(self.save_packet_snapshot)

        self.ai_worker = StrategyWorker()
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
        self.telemetry_timer.timeout.connect(self._on_telemetry_poll)
        # 250ms is typically indistinguishable in-race, but cuts polling overhead.
        self.telemetry_timer.start(TELEMETRY_POLL_MS)

        # Connection indicator updates (slow cadence to reduce overhead).
        self._conn_timer = QTimer(self)
        self._conn_timer.timeout.connect(self._update_connection_badge)
        self._conn_timer.start(CONNECTION_POLL_MS)
        self._update_connection_badge()
        self._update_action_button_text()
        self._set_ai_status("Idle")
        if self._role == "receiver" and self._receiver_link is not None:
            self._connect_receiver_link()

        if self.rejoin_label is not None:
            # Intentionally blank/hidden until the first PIT recommendation.
            pass

        if self._hotkey_migrated:
            self._save_config()

    def _load_config(self) -> dict:
        return load_config()

    def _schedule_save_config(self, _val: int):
        # Debounce disk writes while user is clicking.
        self._save_config_timer.start(400)

    def _relayout_overlay(self):
        if self._is_receiver:
            return
        self.adjustSize()
        w = min(max(self.minimumWidth(), self.width()), self.maximumWidth())
        h = min(max(self.minimumHeight(), self.height()), self.maximumHeight())
        scr = self.screen()
        if scr is not None:
            ag = scr.availableGeometry()
            h = min(h, max(400, ag.height() - 8))
            w = min(w, max(self.minimumWidth(), ag.width() - 8))
        if w > 0 and h > 0:
            self.resize(w, h)

    def _on_auto_clear_toggled(self, on: bool):
        self.clear_after_spin.setVisible(on)
        self.clear_after_spin.setEnabled(on)
        if on and self.clear_after_spin.value() < 10:
            self.clear_after_spin.blockSignals(True)
            self.clear_after_spin.setValue(120)
            self.clear_after_spin.blockSignals(False)
        self._schedule_save_config(0)

    def _on_voice_settings_changed(self, *_args):
        self._schedule_save_config(0)

    def _on_show_pit_impact_toggled(self, on: bool) -> None:
        if on:
            self.rejoin_label.show()
        else:
            self.rejoin_label.hide()
            self.rejoin_label.setVisible(False)
        self._schedule_save_config(0)

    def _save_config(self):
        try:
            data = dict(self._config)
            data["clear_after_sec"] = (
                int(self.clear_after_spin.value()) if self.auto_clear_toggle.isChecked() else 0
            )
            data["auto_apply_track_pit_loss"] = bool(self.auto_track_pit_toggle.isChecked())
            data["voice_enabled"] = bool(self.voice_enabled_toggle.isChecked())
            data["voice_sim_enabled"] = bool(self.voice_sim_toggle.isChecked())
            data["voice_read_why"] = bool(self.voice_why_toggle.isChecked())
            data["show_pit_impact"] = bool(self.show_pit_impact_toggle.isChecked())
            data["auto_pit_alerts"] = bool(self.auto_pit_alerts_toggle.isChecked())
            data["analyze_hotkey"] = self._current_hotkey()
            data["analyze_hotkey_enabled"] = bool(self.hotkey_enabled_toggle.isChecked())
            if self._role == "receiver":
                data["race_link_host"] = str(getattr(self, "_link_host", "")).strip()
                data["race_link_port"] = int(getattr(self, "_link_port", DEFAULT_RACE_LINK_PORT))
            write_config(data)
            self._config = load_config()
        except Exception:
            pass

    def _ensure_default_config_file(self):
        ensure_default_config_file()

    def _refresh_lan_device_list(self) -> None:
        if self._role != "receiver" or not hasattr(self, "lan_device_list"):
            return
        connected_host = ""
        if self._receiver_link and self._receiver_link.is_linked():
            connected_host = str(getattr(self, "_link_host", "")).strip()

        self.lan_device_list.clear()
        devices = self._lan_discovery.devices() if self._lan_discovery is not None else []
        if not devices:
            item = QListWidgetItem("No sim PCs found — start broadcaster mode on the sim PC.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.lan_device_list.addItem(item)
            if hasattr(self, "lan_status_label"):
                self.lan_status_label.setText("Listening on LAN — start broadcaster on the sim PC.")
        else:
            for dev in devices:
                host = dev.get("host", "")
                port = dev.get("port", DEFAULT_RACE_LINK_PORT)
                name = dev.get("name") or host
                label = f"{name}  ·  {host}:{port}"
                if dev.get("iracing"):
                    label += "  ·  iRacing online"
                elif dev.get("track"):
                    label += f"  ·  {dev['track']}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, dev)
                if connected_host and host == connected_host:
                    item.setSelected(True)
                self.lan_device_list.addItem(item)
            if hasattr(self, "lan_status_label"):
                n = len(devices)
                if self._receiver_link and self._receiver_link.is_linked():
                    self.lan_status_label.setText(f"{n} sim PC{'s' if n != 1 else ''} on LAN.")
                else:
                    self.lan_status_label.setText(
                        f"{n} sim PC{'s' if n != 1 else ''} on LAN — auto-connecting to best match…"
                    )

        self._maybe_auto_connect_lan(devices)
        self.layout.activate()
        self._relayout_overlay()

    def _on_lan_device_clicked(self, item: QListWidgetItem) -> None:
        dev = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(dev, dict):
            return
        host = str(dev.get("host") or "").strip()
        if not host:
            return
        try:
            port = int(dev.get("port", DEFAULT_RACE_LINK_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_RACE_LINK_PORT
        self._link_host = host
        self._link_port = port
        self._connect_receiver_link()
        self._schedule_save_config(0)
        if hasattr(self, "lan_status_label"):
            self.lan_status_label.setText(f"Connecting to {host}:{port}…")

    def _maybe_auto_connect_lan(self, devices: list | None = None) -> None:
        """Connect to the best discovered broadcaster when not already linked."""
        if self._role != "receiver" or self._receiver_link is None:
            return
        if self._receiver_link.is_linked():
            return
        if devices is None:
            devices = self._lan_discovery.devices() if self._lan_discovery is not None else []
        if not devices:
            return
        pick = next((d for d in devices if d.get("iracing")), devices[0])
        host = str(pick.get("host") or "").strip()
        if not host:
            return
        try:
            port = int(pick.get("port", DEFAULT_RACE_LINK_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_RACE_LINK_PORT
        cur_host = str(getattr(self, "_link_host", "")).strip()
        try:
            cur_port = int(getattr(self, "_link_port", DEFAULT_RACE_LINK_PORT))
        except (TypeError, ValueError):
            cur_port = DEFAULT_RACE_LINK_PORT
        if cur_host == host and cur_port == port:
            return
        self._link_host = host
        self._link_port = port
        self._connect_receiver_link()
        self._schedule_save_config(0)
        if hasattr(self, "lan_status_label"):
            name = str(pick.get("name") or host).strip() or host
            self.lan_status_label.setText(f"Auto-connecting to {name} ({host}:{port})…")

    def _connect_receiver_link(self) -> None:
        if self._receiver_link is None:
            return
        host = str(getattr(self, "_link_host", self._link_host_boot)).strip()
        port = int(getattr(self, "_link_port", self._link_port_boot))
        if host:
            self._receiver_link.connect_to(host, port)
        else:
            self._receiver_link.disconnect_link()
            self.telemetry.set_link_up(False)

    def _on_receiver_link_changed(self, up: bool) -> None:
        self.telemetry.set_link_up(up)
        self._update_connection_badge()
        if self._role == "receiver":
            self._refresh_lan_device_list()
            if hasattr(self, "lan_status_label"):
                if up:
                    host = str(getattr(self, "_link_host", "")).strip()
                    self.lan_status_label.setText(f"Connected to {host}")

    def _on_remote_snapshot(self, msg: dict) -> None:
        self.telemetry.ingest_snapshot(msg)
        connected = self.telemetry.is_connected()
        if connected and (self._last_sdk_connected is None or self._last_sdk_connected is False):
            self._maybe_set_default_pit_loss()
            self._maybe_sync_tire_sets_from_sdk()
        if not connected and self._last_sdk_connected:
            self._reset_auto_monitor()
        self._last_sdk_connected = connected
        self._update_connection_badge()
        self._update_action_button_text()
        self._maybe_sync_tire_sets_from_sdk()
        self._check_auto_strategy()

    def _on_telemetry_poll(self) -> None:
        self.telemetry.update_field_history()
        self._maybe_sync_tire_sets_from_sdk()
        if self._role != "receiver":
            self._check_auto_strategy()

    def _style_status_pill(self, label: QLabel, tone: str) -> None:
        if not self._is_receiver:
            return
        border = {
            "ok": "#2d8a56",
            "warn": "#b8922e",
            "bad": "#b84a4a",
            "info": "#3d8fd9",
            "muted": "#404858",
        }.get(tone, "#404858")
        label.setStyleSheet(f"QLabel#statusPill {{ border-color: {border}; }}")

    def _is_standby_advice(self, text: str) -> bool:
        if not self._is_receiver:
            return False
        standby = _standby_advice_text(True)
        return (
            text == standby
            or text.startswith("Waiting for live")
            or text.startswith("Timed out")
        )

    def _is_live_compare_mode(self) -> bool:
        return (
            not self._is_receiver
            and bool(self._baseline_strategy_text)
            and self.telemetry.ensure_connected()
            and self.telemetry.ui_mode() != "strategy"
        )

    def _pin_baseline_strategy(self, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned or cleaned.startswith(("AI Error", "Error:")):
            return
        self._baseline_strategy_text = cleaned
        if self.baseline_strategy_label is not None:
            self.baseline_strategy_label.setText(cleaned)
        self._apply_strategy_compare_layout()

    def _clear_baseline_strategy(self) -> None:
        self._baseline_strategy_text = None
        if self.baseline_strategy_label is not None:
            self.baseline_strategy_label.clear()
        self._apply_strategy_compare_layout()

    def _apply_strategy_compare_layout(self) -> None:
        if self._is_receiver:
            return
        compare = self._is_live_compare_mode()
        self._strategy_compare_active = compare
        if self.baseline_strategy_column is not None:
            self.baseline_strategy_column.setVisible(compare)
        if compare:
            self.advice_header.setText("STRATEGY COMPARE")
            self.setMaximumWidth(980)
            self.setMinimumWidth(680)
        else:
            self.advice_header.setText("RACE ENGINEER")
            self.setMaximumWidth(720)
            self.setMinimumWidth(520)
        self._relayout_overlay()

    def _set_advice_text(self, text: str):
        self.advice_label.setText(text)
        if self._is_receiver:
            name = "adviceStandby" if self._is_standby_advice(text) else "adviceText"
            if self.advice_label.objectName() != name:
                self.advice_label.setObjectName(name)
                self.advice_label.style().unpolish(self.advice_label)
                self.advice_label.style().polish(self.advice_label)
        self._apply_strategy_compare_layout()
        self.layout.activate()
        self._relayout_overlay()

    def _current_hotkey(self) -> str:
        data = self.hotkey_combo.currentData()
        if data:
            return normalize_hotkey(str(data))
        return normalize_hotkey(self.hotkey_combo.currentText())

    def _hotkey_trigger_analyze(self):
        if self.btn.isEnabled():
            self.trigger_ai_request()

    def _on_hotkey_settings_changed(self, *_args):
        self._apply_analyze_hotkey()
        self._schedule_save_config(0)

    def _apply_analyze_hotkey(self):
        key = self._current_hotkey()
        enabled = bool(self.hotkey_enabled_toggle.isChecked())
        self._analyze_hotkey.stop()
        self._local_hotkey.setKey(QKeySequence(hotkey_qt_sequence(key)))
        self._local_hotkey.setEnabled(enabled)
        global_ok = False
        if enabled:
            global_ok = self._analyze_hotkey.start(key)
        label = hotkey_display_label(key)
        if enabled and global_ok:
            self.hotkey_hint.setText(
                f"Hotkey: {label} — bind this key in iRacing for your wheel button (works while racing)."
            )
        elif enabled and sys.platform.startswith("darwin") and not mac_accessibility_trusted():
            self.hotkey_hint.setText(
                f"Hotkey: {label} — works while this window is focused. "
                "For global hotkey: System Settings → Privacy & Security → Accessibility → enable Terminal or Python."
            )
        elif enabled:
            self.hotkey_hint.setText(
                f"Hotkey: {label} — works when this window is focused (install pynput on Mac for global)."
            )
        else:
            self.hotkey_hint.setText("Hotkey off — use the Analyze button only.")

    def _reset_auto_monitor(self) -> None:
        self._auto_monitor = reset_auto_monitor_state()
        self._auto_last_call_line = ""
        self._last_delivered_call_line = ""

    def _check_auto_strategy(self) -> None:
        if not bool(self.auto_pit_alerts_toggle.isChecked()):
            return
        if self._active_request_id:
            return
        if not self.telemetry.ensure_connected():
            return
        if self.telemetry.ui_mode() == "strategy":
            return

        get_state = getattr(self.telemetry, "get_monitor_state", None)
        if not callable(get_state):
            return
        lap, is_caution = get_state()
        if lap is None:
            return
        lap_changed = self._auto_monitor.last_lap is None or lap != self._auto_monitor.last_lap
        caution_changed = is_caution != self._auto_monitor.last_caution
        if not lap_changed and not caution_changed:
            return

        try:
            packet_json = self.telemetry.build_packet(
                tire_sets_remaining=int(self.tire_spin.value()),
                pit_loss_sec=int(self.pit_spin.value()),
            )
            telemetry = json.loads(packet_json)
        except Exception:
            return
        if not isinstance(telemetry, dict):
            return

        self._auto_monitor, decision = evaluate_auto_alert_tick(
            self._auto_monitor,
            lap=lap,
            is_caution=is_caution,
            telemetry=telemetry,
            last_delivered_call_line=self._last_delivered_call_line,
        )
        if not decision.deliver or not decision.advice:
            return

        self._auto_last_call_line = decision.call_line or ""
        self._deliver_advice(decision.advice, auto=True)

    def _deliver_advice(self, text: str, *, auto: bool = False) -> None:
        call_line = advice_call_line(str(text))
        if auto and call_line and call_line == self._last_delivered_call_line:
            self._set_advice_text(str(text))
            self._update_action_button_text()
            self._set_ai_status("Auto")
            if self.show_pit_impact_toggle.isChecked():
                self._update_pit_impact_from_advice(str(text))
            return

        if call_line:
            self._last_delivered_call_line = call_line

        self._idle_clear_timer.stop()
        self._set_advice_text(text)
        self._update_action_button_text()
        self._set_ai_status("Auto" if auto else "Done")
        clear_ms = (
            int(self.clear_after_spin.value()) * 1000 if self.auto_clear_toggle.isChecked() else 0
        )
        if clear_ms > 0:
            self._idle_clear_timer.start(clear_ms)
        if self.show_pit_impact_toggle.isChecked():
            self._update_pit_impact_from_advice(str(text))
        self._relay_advice_to_broadcaster(str(text), partial=False)
        if self._role == "receiver" and hasattr(self.telemetry, "record_advice"):
            self.telemetry.record_advice(str(text))
        if self._should_speak_locally() and not str(text).startswith(("AI Error", "Error:")):
            include_why = bool(self.voice_why_toggle.isChecked())
            threading.Thread(
                target=speak_engineer_advice,
                args=(str(text),),
                kwargs={"include_why": include_why},
                daemon=True,
            ).start()

    def trigger_ai_request(self):
        self._idle_clear_timer.stop()
        self._partial_flush_timer.stop()
        self._partial_buffer = None
        if not self.telemetry.ensure_connected():
            if self._role == "receiver":
                if not (self._receiver_link and self._receiver_link.is_linked()):
                    self._set_advice_text(_standby_advice_text(True))
                else:
                    self._set_advice_text("Waiting for live telemetry from the sim PC…")
            else:
                self._set_advice_text("ENGINEER: NO LINK TO SIM")
            return

        mode = self.telemetry.ui_mode()
        self._set_advice_text(
            "Building pre-race plan…"
            if mode == "strategy"
            else "Running strategy engine…"
        )
        self.btn.setEnabled(False)
        self._request_watchdog.start(REQUEST_WATCHDOG_MS)
        self._set_ai_status("Requesting")

        self._next_request_id += 1
        self._active_request_id = self._next_request_id
        self._last_request_mode = mode
        self.ai_worker.set_active(self._active_request_id)

        packet_json = self.telemetry.build_packet(
            tire_sets_remaining=int(self.tire_spin.value()),
            pit_loss_sec=int(self.pit_spin.value()),
        )
        self.ai_worker.invoke_ai(self._active_request_id, packet_json, mode=mode)

    def save_packet_snapshot(self) -> None:
        """Write the current live telemetry packet to sim_logs/packets/."""
        if self._is_receiver:
            self._set_advice_text("Packet save is available on the sim PC (local mode).")
            return
        if not self.telemetry.ensure_connected():
            self._set_advice_text("Connect to iRacing to save a packet snapshot.")
            return
        try:
            packet_json = self.telemetry.build_packet(
                tire_sets_remaining=int(self.tire_spin.value()),
                pit_loss_sec=int(self.pit_spin.value()),
            )
            track = self.telemetry.track_name() if hasattr(self.telemetry, "track_name") else None
            path = save_telemetry_packet(packet_json, track_name=track)
        except Exception as exc:
            self._set_advice_text(f"Packet save failed: {exc}")
            return
        self._set_ai_status("Saved")
        self._set_advice_text(f"Packet saved → {path.resolve()}")

    def clear_and_cancel(self):
        self._request_watchdog.stop()
        self._idle_clear_timer.stop()
        self._partial_flush_timer.stop()
        self._partial_buffer = None
        cancelled_id = self.ai_worker.cancel_active()
        self._active_request_id = 0
        self._auto_last_call_line = ""
        self._last_delivered_call_line = ""
        self._set_advice_text(_standby_advice_text(self._is_receiver))
        self._update_action_button_text()
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
            self._partial_flush_timer.start(STREAM_UI_THROTTLE_MS)

    def _on_request_timeout(self):
        if self._active_request_id:
            print(f"[WARN] Strategy request timed out (request_id={self._active_request_id})")
            # Vital: cooperative-cancel the worker stream. Without this, boto3 may keep
            # iterating forever and the UI stays stuck even though we re-enabled Analyze.
            self.ai_worker.cancel_active()
            self._active_request_id = 0
            self._set_advice_text("Timed out — hit Clear or try again.")
            self._update_action_button_text()
            self._set_ai_status("Timed out")

    def display_advice(self, request_id: int, text: str):
        if request_id != self._active_request_id:
            return
        self._request_watchdog.stop()
        self._partial_flush_timer.stop()
        self._partial_buffer = None
        self._active_request_id = 0
        if str(text).startswith(("AI Error", "Error:")):
            self._set_advice_text(text)
            self._update_action_button_text()
            self._set_ai_status("Error")
            self.btn.setEnabled(True)
            return
        if self._last_request_mode == "strategy":
            self._pin_baseline_strategy(str(text))
        call_line = advice_call_line(str(text))
        if call_line:
            self._auto_last_call_line = call_line
            self._last_delivered_call_line = call_line
        self._deliver_advice(str(text), auto=False)
        self.btn.setEnabled(True)

    def _flush_partial(self):
        if self._active_request_id == 0 or self._partial_buffer is None:
            return
        self._set_advice_text(self._partial_buffer)

    def _clear_if_idle(self):
        # Only clear if we are not currently waiting on a request.
        if self._active_request_id == 0 and self.btn.isEnabled():
            self._set_advice_text(_standby_advice_text(self._is_receiver))
            self._set_ai_status("Idle")

    def close_app(self):
        self._analyze_hotkey.stop()
        if self._lan_discovery is not None:
            self._lan_discovery.stop()
        if self._receiver_link is not None:
            self._receiver_link.disconnect_link()
        # Quit the entire program (not just hide the overlay widget).
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            self.close()

    def _update_connection_badge(self):
        if self._role == "receiver":
            link = bool(self._receiver_link and self._receiver_link.is_linked())
            feed = self.telemetry.is_connected()
            if feed:
                self.conn_badge.setText("Telemetry · Live")
                self._style_status_pill(self.conn_badge, "ok")
            elif link:
                self.conn_badge.setText("Telemetry · Waiting")
                self._style_status_pill(self.conn_badge, "warn")
            else:
                self.conn_badge.setText("Telemetry · Offline")
                self._style_status_pill(self.conn_badge, "bad")
            self._update_action_button_text()
            return

        connected = self.telemetry.is_connected()
        ui_mode = self.telemetry.ui_mode() if connected else None
        if (
            not self._is_receiver
            and connected
            and self._last_ui_mode == "strategy"
            and ui_mode == "live"
            and self._baseline_strategy_text
        ):
            self._set_advice_text(_standby_advice_text(False))
        self._last_ui_mode = ui_mode
        if connected:
            self.conn_badge.setText("iRacing: Online")
            self.conn_badge.setStyleSheet(
                "QLabel#connBadge { border-color: rgba(46, 204, 113, 140); }"
            )
        else:
            self.conn_badge.setText("iRacing: Offline")
            self.conn_badge.setStyleSheet(
                "QLabel#connBadge { border-color: rgba(231, 76, 60, 160); }"
            )
            if self._last_sdk_connected:
                self._reset_auto_monitor()
                self._clear_baseline_strategy()

        # On initial connect or reconnect, set a sensible default pit-loss
        # (but only if the user hasn't overridden it).
        if connected and (self._last_sdk_connected is None or self._last_sdk_connected is False):
            self._maybe_set_default_pit_loss()
            self._maybe_sync_tire_sets_from_sdk()
            self._reset_auto_monitor()
            self._clear_baseline_strategy()
        self._last_sdk_connected = connected
        self._apply_strategy_compare_layout()
        self._update_action_button_text()

    def _update_action_button_text(self):
        if self._role == "receiver":
            linked = bool(self._receiver_link and self._receiver_link.is_linked())
            live = self.telemetry.is_connected()
            if not linked or not live:
                self.btn.setText("Analyze")
            elif self.telemetry.ui_mode() == "strategy":
                self.btn.setText("Race strategy")
            else:
                self.btn.setText("Analyze")
            if self._active_request_id == 0:
                self.btn.setEnabled(linked and live)
            return
        if not self.telemetry.is_connected():
            self.btn.setText(BTN_DISCONNECTED)
        elif self.telemetry.ui_mode() == "strategy":
            self.btn.setText(BTN_STRATEGY)
        else:
            self.btn.setText(BTN_LIVE)

    def _on_pit_spin_changed(self, _val: int):
        # Mark as user-modified so we don't overwrite with track defaults later.
        self._pit_user_modified = True

    def _on_tire_spin_changed(self, _val: int):
        self._tire_user_modified = True

    def _maybe_sync_tire_sets_from_sdk(self) -> None:
        if self._tire_user_modified:
            return
        if not self.telemetry.is_connected():
            return
        read_fn = getattr(self.telemetry, "read_tire_sets_remaining", None)
        if not callable(read_fn):
            return
        sdk_ts = read_fn()
        if sdk_ts is None:
            return
        if int(self.tire_spin.value()) == int(sdk_ts):
            return
        self.tire_spin.blockSignals(True)
        self.tire_spin.setValue(int(sdk_ts))
        self.tire_spin.blockSignals(False)

    def _apply_track_default_pit_loss(self):
        # Explicit user action: override current value with track-based default.
        self._pit_user_modified = False
        self._maybe_set_default_pit_loss()

    def _maybe_set_default_pit_loss(self):
        if self._pit_user_modified:
            return
        if not self.auto_track_pit_toggle.isChecked():
            return

        name = self.telemetry.track_name() or ""
        length_mi = resolve_track_length_miles(self.telemetry.track_length_miles())
        default_sec = int(get_default_pit_loss_seconds(name, length_mi))

        # Set without marking as user-modified.
        self.pit_spin.blockSignals(True)
        self.pit_spin.setValue(default_sec)
        self.pit_spin.blockSignals(False)

    def _set_ai_status(self, status: str):
        self.ai_badge.setText(f"Engine · {status}")
        status_l = (status or "").lower()
        if self._is_receiver:
            if status_l in ("idle", "done"):
                self._style_status_pill(self.ai_badge, "ok")
            elif status_l in ("requesting", "streaming"):
                self._style_status_pill(self.ai_badge, "info")
            elif status_l == "auto":
                self._style_status_pill(self.ai_badge, "warn")
            elif status_l in ("timed out", "timeout"):
                self._style_status_pill(self.ai_badge, "warn")
            elif status_l in ("cancelled", "canceled"):
                self._style_status_pill(self.ai_badge, "muted")
            else:
                self._style_status_pill(self.ai_badge, "bad")
            return
        if status_l in ("idle", "done"):
            color = "rgba(46, 204, 113, 140)"
        elif status_l == "auto":
            color = "rgba(241, 196, 15, 160)"
        elif status_l in ("requesting", "streaming"):
            color = "rgba(52, 152, 219, 160)"
        elif status_l in ("timed out", "timeout"):
            color = "rgba(241, 196, 15, 170)"
        elif status_l in ("cancelled", "canceled"):
            color = "rgba(149, 165, 166, 170)"
        else:
            color = "rgba(231, 76, 60, 170)"
        self.ai_badge.setStyleSheet(f"QLabel#connBadge {{ border-color: {color}; }}")

    def _format_pit_impact_line(self, est: dict[str, Any], pit_in_laps: int) -> str:
        lost = est.get("lost")
        if not isinstance(lost, int):
            return "Pit-road impact: unknown"
        when = "now" if pit_in_laps == 0 else f"in {pit_in_laps} laps"
        if est.get("caution"):
            exit_p = est.get("exit_p")
            lost_lead = est.get("lost_lead")
            lda = est.get("lda") or 0
            rows = est.get("grid_rows") or 0
            parts = [f"Pit impact (yellow): pit {when}"]
            if isinstance(exit_p, int):
                parts.append(f"~P{exit_p} on track")
            if isinstance(lost_lead, int) and lost_lead != lost:
                parts.append(f"+{lost} spots on screen, ~{lost_lead} lead-lap")
            elif lost:
                parts.append(f"~{lost} spots")
            if lda:
                parts.append(f"{lda} lap-down stay out (pass on pit road)")
            if rows:
                parts.append(f"~{rows} rows back on restart grid")
            return " · ".join(parts)
        return f"Pit-road impact: pit {when}, likely give up ~{lost} spots"

    def _update_pit_impact_from_advice(self, text: str):
        if not self.show_pit_impact_toggle.isChecked():
            return
        # Expected format: ACTION — TIMING — REASON [tag] [H|M|L]
        head = _first_nonempty_line(text)
        parts = [p.strip() for p in head.split("—")]
        if len(parts) < 2:
            self.rejoin_label.setText("Pit-road impact: couldn’t read call (bad format)")
            return
        action = parts[0].upper()
        timing = parts[1].upper()

        # Only show pit impact when the AI recommends pitting.
        # Hide it on STAY OUT so the overlay stays uncluttered.
        if action not in ("PIT", "PIT NOW"):
            self.rejoin_label.setVisible(False)
            self.rejoin_label.setText("")
            return
        self.rejoin_label.setVisible(True)

        pit_in_laps = None
        if action == "PIT NOW":
            pit_in_laps = 0
        elif action == "PIT":
            if "PIT IN" in timing and "LAP" in timing:
                # e.g. "PIT IN 5 LAPS"
                try:
                    tokens = timing.replace("LAPS", "").replace("LAP", "").split()
                    pit_in_laps = int(tokens[tokens.index("IN") + 1])
                except Exception:
                    pit_in_laps = None
            elif "THIS LAP" in timing:
                pit_in_laps = 0

        if pit_in_laps is None:
            self.rejoin_label.setText("Pit-road impact: need a lap count on the pit call")
            return

        est = self.telemetry.predict_pit_position_loss(int(self.pit_spin.value()), int(pit_in_laps))
        self.rejoin_label.setText(self._format_pit_impact_line(est, int(pit_in_laps)))

    def _relay_advice_to_broadcaster(self, text: str, *, partial: bool = False) -> None:
        if self._role != "receiver" or self._receiver_link is None:
            return
        if not self._receiver_link.is_linked():
            print("[WARN] Advice not relayed — engineer PC not linked to sim PC")
            return
        speak = bool(self.voice_sim_toggle.isChecked())
        sent = self._receiver_link.send_advice(
            text,
            partial=partial,
            speak=speak,
            include_why=bool(self.voice_why_toggle.isChecked()),
        )
        if speak and not sent:
            print("[WARN] Advice voice relay to sim PC failed (socket write)")

    def _should_speak_locally(self) -> bool:
        if not bool(self.voice_enabled_toggle.isChecked()):
            return False
        if self._role == "receiver" and self._receiver_link is not None and self._receiver_link.is_linked():
            return False
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.offset)

