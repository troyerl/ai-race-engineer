import os
import subprocess
import sys
import threading
import json

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bedrock_worker import BedrockWorker
from telemetry import TelemetryTracker


TELEMETRY_POLL_MS = 250
CONNECTION_POLL_MS = 1000
REQUEST_TIMEOUT_MS = 30000
STREAM_UI_THROTTLE_MS = 80

FEATURE_REJOIN_ENV = "AIRACE_FEATURE_REJOIN"
FEATURE_VOICE_ENV = "AIRACE_FEATURE_VOICE"

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ai_race_engineer.json")

# Rough $ estimate for the usage counter (edit if your model/region pricing differs).
BEDROCK_USD_PER_MILLION_INPUT = 3.0
BEDROCK_USD_PER_MILLION_OUTPUT = 15.0

BTN_LIVE = "ANALYZE FIELD & ADVISE"
BTN_STRATEGY = "GET RACE STRATEGY"
BTN_DISCONNECTED = "ANALYZE FIELD & ADVISE"


def _migrate_token_counts(c: dict) -> None:
    """Legacy configs only had combined totals; split into prior (you set) + runtime (this app)."""
    if "bedrock_tokens_runtime_input" not in c:
        c["bedrock_tokens_runtime_input"] = int(c.get("bedrock_tokens_input_total", 0) or 0)
        c["bedrock_tokens_runtime_output"] = int(c.get("bedrock_tokens_output_total", 0) or 0)
    c.setdefault("bedrock_tokens_prior_input", 0)
    c.setdefault("bedrock_tokens_prior_output", 0)


def _sync_legacy_total_keys(data: dict) -> None:
    """Keep bedrock_tokens_*_total as prior+runtime so older tooling still reads one number."""
    pi = int(data.get("bedrock_tokens_prior_input", 0) or 0)
    po = int(data.get("bedrock_tokens_prior_output", 0) or 0)
    ri = int(data.get("bedrock_tokens_runtime_input", 0) or 0)
    ro = int(data.get("bedrock_tokens_runtime_output", 0) or 0)
    data["bedrock_tokens_input_total"] = pi + ri
    data["bedrock_tokens_output_total"] = po + ro


def _merge_config_defaults(cfg: dict) -> dict:
    c = dict(cfg) if isinstance(cfg, dict) else {}
    c.setdefault("clear_after_sec", 120)
    c.setdefault("voice_read_why", False)
    c.setdefault("auto_apply_track_pit_loss", True)
    _migrate_token_counts(c)
    return c


def _bedrock_estimated_charge_usd(input_tokens: int, output_tokens: int) -> float:
    """User-facing estimate: $1 / 1M input, $5 / 1M output."""
    return (max(0, int(input_tokens)) / 1_000_000.0) * BEDROCK_USD_PER_MILLION_INPUT + (
        max(0, int(output_tokens)) / 1_000_000.0
    ) * BEDROCK_USD_PER_MILLION_OUTPUT


def _format_charge_usd(amount: float) -> str:
    if amount <= 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def _first_nonempty_line(text: str) -> str:
    """Multi-line engineer replies keep the pit call on line 1 for parsers / voice."""
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s:
            return s
    return ""


class AIRaceEngineer(QWidget):
    @staticmethod
    def _settings_heading(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("settingsSectionTitle")
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

    def __init__(self):
        super().__init__()
        self.telemetry = TelemetryTracker()
        self._feature_rejoin = os.getenv(FEATURE_REJOIN_ENV, "0") == "1"
        self._feature_voice = os.getenv(FEATURE_VOICE_ENV, "0") == "1"
        self._pit_user_modified = False
        self._last_sdk_connected = None
        self._ensure_default_config_file()
        self._config = _merge_config_defaults(self._load_config())

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.setMinimumWidth(520)
        self.setMaximumWidth(720)
        _scr = QGuiApplication.primaryScreen()
        if _scr is not None:
            _ah = _scr.availableGeometry().height()
            self.setMaximumHeight(max(620, int(_ah * 0.92)))

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(10)

        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
            }
            QLabel#statusLabel {
                color: #E8F5E9;
                font-size: 17px;
                font-weight: 700;
                line-height: 142%;
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
            QToolButton#settingsBtn {
                background-color: rgba(20, 22, 28, 235);
                color: rgba(255,255,255,245);
                font-weight: 900;
                border-radius: 12px;
                padding: 10px 12px;
                border: 1px solid rgba(255,255,255,80);
                text-align: left;
            }
            QToolButton#settingsBtn:hover { background-color: rgba(28, 30, 38, 245); }
            QToolButton#settingsBtn:pressed { background-color: rgba(16, 18, 24, 245); }
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
                padding: 8px 12px;
                border: 1px solid rgba(255,255,255,40);
                min-width: 110px;
                font-weight: 700;
                font-size: 14px;
            }
            QSpinBox#tireSpin::up-button, QSpinBox#tireSpin::down-button,
            QSpinBox#pitSpin::up-button, QSpinBox#pitSpin::down-button {
                width: 34px;
                border-radius: 10px;
                background: rgba(255,255,255,55);
                border: 1px solid rgba(255,255,255,80);
            }
            QSpinBox#tireSpin::up-button:hover, QSpinBox#tireSpin::down-button:hover,
            QSpinBox#pitSpin::up-button:hover, QSpinBox#pitSpin::down-button:hover {
                background: rgba(255,255,255,75);
            }
            QSpinBox#tireSpin::up-button:pressed, QSpinBox#tireSpin::down-button:pressed,
            QSpinBox#pitSpin::up-button:pressed, QSpinBox#pitSpin::down-button:pressed {
                background: rgba(255,255,255,45);
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
                background-color: rgb(22, 24, 32);
                border: 1px solid rgba(255, 255, 255, 70);
                border-radius: 12px;
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
                background-color: rgba(55, 58, 70, 255);
                color: rgba(255, 255, 255, 245);
                font-weight: 800;
                font-size: 12px;
                border-radius: 14px;
                padding: 6px 14px;
                border: 1px solid rgba(255, 255, 255, 45);
                min-width: 52px;
                max-width: 52px;
            }
            QToolButton#toggleChip:hover {
                background-color: rgba(65, 68, 82, 255);
            }
            QToolButton#toggleChip:checked {
                background-color: rgba(31, 138, 76, 255);
                border: 1px solid rgba(180, 255, 200, 70);
            }
            QToolButton#toggleChip:checked:hover {
                background-color: rgba(35, 154, 85, 255);
            }
            QToolButton#toggleChip:disabled {
                color: rgba(255, 255, 255, 120);
                background-color: rgba(40, 42, 50, 200);
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

        self.token_label = QLabel("")
        self.token_label.setObjectName("subLabel")
        self.token_label.setWordWrap(True)
        self.token_label.setToolTip(
            "Input and output = prior (Settings) + counted since install. "
            f"Cost estimate: ${BEDROCK_USD_PER_MILLION_INPUT:g} per 1M input, "
            f"${BEDROCK_USD_PER_MILLION_OUTPUT:g} per 1M output (not official AWS billing)."
        )

        self.rejoin_label = None
        if self._feature_rejoin:
            self.rejoin_label = QLabel("Pit-road impact: …")
            self.rejoin_label.setObjectName("subLabel")
            # Hidden until the AI actually recommends a PIT.
            self.rejoin_label.setVisible(False)

        self.btn = QPushButton(BTN_DISCONNECTED)
        self.btn.setObjectName("analyzeBtn")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.trigger_ai_request)

        tire_row = QHBoxLayout()
        tire_row.setContentsMargins(0, 0, 0, 0)
        tire_label = QLabel("Fresh tire sets remaining")
        tire_label.setObjectName("subLabel")
        self.tire_spin = QSpinBox()
        self.tire_spin.setObjectName("tireSpin")
        self.tire_spin.setMinimum(0)
        self.tire_spin.setMaximum(99)
        self.tire_spin.setValue(2)
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

        pit_row = QHBoxLayout()
        pit_row.setContentsMargins(0, 0, 0, 0)
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

        clear_hint = QLabel("Off = message stays until you clear or request again.")
        clear_hint.setObjectName("settingsHint")
        clear_hint.setWordWrap(True)

        voice_row = QHBoxLayout()
        voice_row.setContentsMargins(0, 0, 0, 0)
        voice_label = QLabel("Speak WHY after call")
        voice_label.setObjectName("subLabel")
        self.voice_why_toggle = self._pill_toggle(bool(self._config.get("voice_read_why", False)))
        self.voice_why_toggle.setToolTip("After the action line, reads the WHY line aloud (if voice is enabled).")
        if not self._feature_voice:
            self.voice_why_toggle.setEnabled(False)
            self.voice_why_toggle.setToolTip("Set AIRACE_FEATURE_VOICE=1 to enable speech.")
        voice_row.addWidget(voice_label)
        voice_row.addStretch(1)
        voice_row.addWidget(self.voice_why_toggle)

        voice_hint = QLabel("Voice uses macOS `say`, Windows SAPI, or `espeak` on Linux when the feature flag is on.")
        voice_hint.setObjectName("settingsHint")
        voice_hint.setWordWrap(True)

        prior_in_row = QHBoxLayout()
        prior_in_row.setContentsMargins(0, 0, 0, 0)
        prior_in_label = QLabel("Prior usage · in")
        prior_in_label.setObjectName("subLabel")
        self.prior_input_spin = QSpinBox()
        self.prior_input_spin.setObjectName("pitSpin")
        self.prior_input_spin.setMinimum(0)
        self.prior_input_spin.setMaximum(2_147_483_647)
        self.prior_input_spin.setSingleStep(10_000)
        self.prior_input_spin.setValue(int(self._config.get("bedrock_tokens_prior_input", 0)))
        prior_in_row.addWidget(prior_in_label)
        prior_in_row.addWidget(self.prior_input_spin)
        prior_in_row.addStretch(1)

        prior_out_row = QHBoxLayout()
        prior_out_row.setContentsMargins(0, 0, 0, 0)
        prior_out_label = QLabel("Prior usage · out")
        prior_out_label.setObjectName("subLabel")
        self.prior_output_spin = QSpinBox()
        self.prior_output_spin.setObjectName("pitSpin")
        self.prior_output_spin.setMinimum(0)
        self.prior_output_spin.setMaximum(2_147_483_647)
        self.prior_output_spin.setSingleStep(10_000)
        self.prior_output_spin.setValue(int(self._config.get("bedrock_tokens_prior_output", 0)))
        prior_out_row.addWidget(prior_out_label)
        prior_out_row.addWidget(self.prior_output_spin)
        prior_out_row.addStretch(1)

        prior_hint = QLabel(
            f"Adds Bedrock tokens you used before this app. Cost line: ${BEDROCK_USD_PER_MILLION_INPUT:g}/1M in, "
            f"${BEDROCK_USD_PER_MILLION_OUTPUT:g}/1M out."
        )
        prior_hint.setObjectName("settingsHint")
        prior_hint.setWordWrap(True)

        self._save_config_timer = QTimer(self)
        self._save_config_timer.setSingleShot(True)
        self._save_config_timer.timeout.connect(self._save_config)
        self.clear_after_spin.valueChanged.connect(self._schedule_save_config)
        self.prior_input_spin.valueChanged.connect(self._schedule_save_config)
        self.prior_output_spin.valueChanged.connect(self._schedule_save_config)
        self.voice_why_toggle.toggled.connect(lambda _v: self._schedule_save_config(0))
        self.auto_track_pit_toggle.toggled.connect(lambda _v: self._schedule_save_config(0))
        self.auto_clear_toggle.toggled.connect(self._on_auto_clear_toggled)

        self.settings_widget = QWidget()
        self.settings_widget.setObjectName("settingsPanel")
        settings_layout = QVBoxLayout()
        settings_layout.setContentsMargins(12, 10, 12, 12)
        settings_layout.setSpacing(8)
        settings_layout.addWidget(self._settings_heading("RACE · Pit"))
        settings_layout.addLayout(pit_row)
        settings_layout.addLayout(auto_pit_row)
        settings_layout.addWidget(self.pit_defaults_btn)
        settings_layout.addWidget(self._settings_heading("ADVICE"))
        settings_layout.addLayout(auto_clear_row)
        settings_layout.addWidget(clear_hint)
        settings_layout.addWidget(self._settings_heading("VOICE"))
        settings_layout.addLayout(voice_row)
        settings_layout.addWidget(voice_hint)
        settings_layout.addWidget(self._settings_heading("USAGE · Prior tokens"))
        settings_layout.addLayout(prior_in_row)
        settings_layout.addLayout(prior_out_row)
        settings_layout.addWidget(prior_hint)
        self.settings_widget.setLayout(settings_layout)
        self.settings_widget.setVisible(False)

        def _toggle_settings(checked: bool):
            self.settings_widget.setVisible(checked)
            self.settings_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
            self.layout.activate()
            self._relayout_overlay()

        self.settings_toggle.toggled.connect(_toggle_settings)

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
        self.layout.addWidget(self.token_label)
        if self.rejoin_label is not None:
            self.layout.addWidget(self.rejoin_label)
        self.layout.addWidget(self.label)
        self.layout.addLayout(tire_row)
        self.layout.addLayout(settings_toggle_row)
        self.layout.addWidget(self.settings_widget)
        self.layout.addWidget(self.btn)
        self.layout.addLayout(bottom_row)
        self.setLayout(self.layout)

        self.ai_worker = BedrockWorker()
        self.ai_worker.partial.connect(self.display_partial)
        self.ai_worker.finished.connect(self.display_advice)
        self.ai_worker.usage_report.connect(self._on_bedrock_usage_report)

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
        self.telemetry_timer.start(TELEMETRY_POLL_MS)

        # Connection indicator updates (slow cadence to reduce overhead).
        self._conn_timer = QTimer(self)
        self._conn_timer.timeout.connect(self._update_connection_badge)
        self._conn_timer.start(CONNECTION_POLL_MS)
        self._update_connection_badge()
        self._update_action_button_text()
        self._set_ai_status("Idle")
        self._refresh_bedrock_usage_label()

        if self.rejoin_label is not None:
            # Intentionally blank/hidden until the first PIT recommendation.
            pass

    def _load_config(self) -> dict:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _schedule_save_config(self, _val: int):
        # Debounce disk writes while user is clicking.
        self._save_config_timer.start(400)

    def _relayout_overlay(self):
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

    def _refresh_bedrock_usage_label(self):
        pi = int(self.prior_input_spin.value())
        po = int(self.prior_output_spin.value())
        bi = pi + int(self._config.get("bedrock_tokens_runtime_input", 0))
        bo = po + int(self._config.get("bedrock_tokens_runtime_output", 0))
        charge = _bedrock_estimated_charge_usd(bi, bo)
        self.token_label.setText(
            f"Bedrock: {bi:,} in · {bo:,} out · est. {_format_charge_usd(charge)} total"
        )

    def _on_bedrock_usage_report(self, inp: int, outp: int):
        inp = max(0, int(inp))
        outp = max(0, int(outp))
        self._config["bedrock_tokens_runtime_input"] = int(self._config.get("bedrock_tokens_runtime_input", 0)) + inp
        self._config["bedrock_tokens_runtime_output"] = int(self._config.get("bedrock_tokens_runtime_output", 0)) + outp
        self._save_config()

    def _save_config(self):
        try:
            data = dict(self._config)
            data["clear_after_sec"] = (
                int(self.clear_after_spin.value()) if self.auto_clear_toggle.isChecked() else 0
            )
            data["auto_apply_track_pit_loss"] = bool(self.auto_track_pit_toggle.isChecked())
            data["bedrock_tokens_prior_input"] = int(self.prior_input_spin.value())
            data["bedrock_tokens_prior_output"] = int(self.prior_output_spin.value())
            data["voice_read_why"] = bool(self.voice_why_toggle.isChecked())
            _sync_legacy_total_keys(data)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, CONFIG_PATH)
            self._config = data
            self._refresh_bedrock_usage_label()
        except Exception:
            pass

    def _ensure_default_config_file(self):
        # Create a config file on first run so racers can find/edit it.
        if os.path.exists(CONFIG_PATH):
            return
        try:
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "clear_after_sec": 120,
                        "auto_apply_track_pit_loss": True,
                        "voice_read_why": False,
                        "bedrock_tokens_prior_input": 0,
                        "bedrock_tokens_prior_output": 0,
                        "bedrock_tokens_runtime_input": 0,
                        "bedrock_tokens_runtime_output": 0,
                        "bedrock_tokens_input_total": 0,
                        "bedrock_tokens_output_total": 0,
                    },
                    f,
                )
            os.replace(tmp, CONFIG_PATH)
        except Exception:
            pass

    def trigger_ai_request(self):
        self._idle_clear_timer.stop()
        self._partial_flush_timer.stop()
        self._partial_buffer = None
        if not self.telemetry.ensure_connected():
            self.label.setText("ENGINEER: NO LINK TO SIM")
            return

        mode = self.telemetry.ui_mode()
        self.label.setText(
            "Sketching race strategy (fuel & tires)…"
            if mode == "strategy"
            else "Reading the field & corners…"
        )
        self.btn.setEnabled(False)
        self._request_watchdog.start(REQUEST_TIMEOUT_MS)
        self._set_ai_status("Requesting")

        self._next_request_id += 1
        self._active_request_id = self._next_request_id
        self.ai_worker.set_active(self._active_request_id)

        packet_json = self.telemetry.build_packet(
            tire_sets_remaining=int(self.tire_spin.value()),
            pit_loss_sec=int(self.pit_spin.value()),
        )
        self.ai_worker.invoke_ai(self._active_request_id, packet_json, mode=mode)

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
            self._partial_flush_timer.start(STREAM_UI_THROTTLE_MS)

    def _on_request_timeout(self):
        if self._active_request_id:
            print(f"[WARN] AI request timed out (request_id={self._active_request_id})")
            # Vital: cooperative-cancel the worker stream. Without this, boto3 may keep
            # iterating forever and the UI stays stuck even though we re-enabled Analyze.
            self.ai_worker.cancel_active()
            self._active_request_id = 0
            self.label.setText("Timed out — hit Clear/Cancel or try again.")
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
        self._relayout_overlay()
        # Mark request complete and schedule auto-clear if no further interaction.
        self._active_request_id = 0
        clear_ms = (
            int(self.clear_after_spin.value()) * 1000 if self.auto_clear_toggle.isChecked() else 0
        )
        if clear_ms > 0:
            self._idle_clear_timer.start(clear_ms)
        self._set_ai_status("Error" if str(text).startswith("AI Error") else "Done")
        if self.rejoin_label is not None:
            self._update_pit_impact_from_advice(str(text))
        if self._feature_voice and not str(text).startswith("AI Error"):
            full = str(text)
            head = _first_nonempty_line(full)
            action = head.split("—", 1)[0].strip() if head else ""
            threading.Thread(target=self._speak_action, args=(action,), daemon=True).start()
            if self.voice_why_toggle.isChecked():
                why = ""
                for line in full.replace("\r\n", "\n").split("\n"):
                    s = line.strip()
                    if s.upper().startswith("WHY:"):
                        why = s[4:].strip()
                        break
                if why:
                    def speak_why():
                        try:
                            import time as _t
                            _t.sleep(0.25)
                        except Exception:
                            pass
                        self._speak_action(why)

                    threading.Thread(target=speak_why, daemon=True).start()

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
            self._relayout_overlay()
            self._set_ai_status("Idle")

    def close_app(self):
        # Quit the entire program (not just hide the overlay widget).
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            self.close()

    def _update_connection_badge(self):
        connected = self.telemetry.is_connected()
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

        # On initial connect or reconnect, set a sensible default pit-loss
        # (but only if the user hasn't overridden it).
        if connected and (self._last_sdk_connected is None or self._last_sdk_connected is False):
            self._maybe_set_default_pit_loss()
        self._last_sdk_connected = connected
        self._update_action_button_text()

    def _update_action_button_text(self):
        if not self.telemetry.is_connected():
            self.btn.setText(BTN_DISCONNECTED)
        elif self.telemetry.ui_mode() == "strategy":
            self.btn.setText(BTN_STRATEGY)
        else:
            self.btn.setText(BTN_LIVE)

    def _on_pit_spin_changed(self, _val: int):
        # Mark as user-modified so we don't overwrite with track defaults later.
        self._pit_user_modified = True

    def _apply_track_default_pit_loss(self):
        # Explicit user action: override current value with track-based default.
        self._pit_user_modified = False
        self._maybe_set_default_pit_loss()

    def _maybe_set_default_pit_loss(self):
        if self._pit_user_modified:
            return
        if not self.auto_track_pit_toggle.isChecked():
            return

        name = (self.telemetry.track_name() or "").lower()
        length_mi = self.telemetry.track_length_miles()

        # Classify track type.
        track_type = None
        if "daytona" in name or "talladega" in name:
            track_type = "super"
        elif isinstance(length_mi, (int, float)):
            if length_mi >= 2.3:
                track_type = "super"
            elif length_mi <= 1.2:
                track_type = "short"
            else:
                track_type = "intermediate"
        else:
            track_type = "intermediate"

        # Defaults based on your ranges (choose midpoints).
        if track_type == "short":
            default_sec = 42  # ~40–45
        elif track_type == "super":
            default_sec = 58  # "higher than 1.5mi"; conservative
        else:
            default_sec = 46  # ~45–48

        # Set without marking as user-modified.
        self.pit_spin.blockSignals(True)
        self.pit_spin.setValue(default_sec)
        self.pit_spin.blockSignals(False)

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

    def _update_pit_impact_from_advice(self, text: str):
        if self.rejoin_label is None:
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
        lost = est.get("lost")
        if isinstance(lost, int):
            when = "now" if pit_in_laps == 0 else f"in {pit_in_laps} laps"
            self.rejoin_label.setText(f"Pit-road impact: pit {when}, likely give up ~{lost} spots")
        else:
            self.rejoin_label.setText("Pit-road impact: unknown")

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
                # Avoid nested quoting issues by sanitizing before embedding in PowerShell.
                safe = a.replace("'", " ").replace("\n", " ").replace("\r", " ")
                ps = (
                    "Add-Type -AssemblyName System.Speech; "
                    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("
                    + repr(safe)
                    + ")"
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

