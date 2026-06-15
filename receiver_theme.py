"""Visual design system for the engineer (receiver) workstation UI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget

RECEIVER_QSS = """
QWidget#rootOverlay {
    background-color: #0c0e14;
    color: #e8edf4;
    border: none;
}
QLabel#appTitle {
    font-size: 22px;
    font-weight: 800;
    color: #f4f7fb;
    background: transparent;
}
QLabel#appSubtitle {
    font-size: 12px;
    font-weight: 500;
    color: #8b95a8;
    background: transparent;
}
QWidget#headerBar {
    background: transparent;
}
QWidget#statusBar {
    background: #141820;
    border: 1px solid #262c3a;
    border-radius: 10px;
}
QLabel#statusPill {
    font-size: 12px;
    font-weight: 700;
    color: #c8d0dc;
    background: #1c2130;
    border: 1px solid #303848;
    border-radius: 999px;
    padding: 6px 12px;
}
QWidget#card {
    background: #141820;
    border: 1px solid #262c3a;
    border-radius: 12px;
}
QLabel#cardStep {
    font-size: 11px;
    font-weight: 800;
    color: #5ec996;
    background: transparent;
    letter-spacing: 0.06em;
}
QLabel#cardTitle {
    font-size: 15px;
    font-weight: 700;
    color: #f0f4f8;
    background: transparent;
}
QLabel#cardHint {
    font-size: 12px;
    font-weight: 500;
    color: #8b95a8;
    background: transparent;
}
QLabel#adviceText {
    font-family: "Menlo", "Consolas", "Courier New", monospace;
    font-size: 11px;
    font-weight: 500;
    line-height: 145%;
    color: #eef6f0;
}
QLabel#adviceStandby {
    font-size: 14px;
    font-weight: 500;
    color: #8b95a8;
}
QLabel#fieldLabel {
    font-size: 11px;
    font-weight: 700;
    color: #8b95a8;
    background: transparent;
    letter-spacing: 0.03em;
}
QWidget#actionBar {
    background: #141820;
    border: 1px solid #262c3a;
    border-radius: 12px;
}
QWidget#settingsPanel, QWidget#usagePanel {
    background: #10141c;
    border: 1px solid #262c3a;
    border-radius: 12px;
}
QLabel#settingsSectionTitle {
    color: #7d8aa0;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.08em;
    padding: 14px 0 6px 0;
    background: transparent;
    border: none;
}
QLabel#settingsSectionTitle:first-child {
    padding-top: 4px;
}
QLabel#settingDesc {
    color: #6e7a8f;
    font-size: 11px;
    font-weight: 500;
    line-height: 140%;
    padding: 0 0 8px 0;
    background: transparent;
}
QLabel#settingsHint {
    color: #8b95a8;
    font-size: 12px;
    font-weight: 500;
    line-height: 145%;
}
QLabel#subLabel {
    color: #c8d0dc;
    font-size: 12px;
    font-weight: 700;
    background: transparent;
    border: none;
    padding: 0;
}
QScrollArea#sideScroll {
    background: transparent;
    border: none;
}
QScrollArea#sideScroll > QWidget > QWidget#settingsPanel {
    background: #10141c;
    border: 1px solid #262c3a;
    border-radius: 12px;
}
QWidget#collapseSection {
    background: transparent;
}
QToolButton#collapseHeader {
    background: #1c2130;
    color: #d8dee8;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.06em;
    border: 1px solid #303848;
    border-radius: 8px;
    padding: 10px 12px;
    text-align: left;
}
QToolButton#collapseHeader:hover {
    background: #242b3c;
}
QWidget#collapseBody {
    background: transparent;
}
QLabel#footerMeta, QLabel#statusMeta {
    font-size: 11px;
    font-weight: 600;
    color: #6e7a8f;
    background: transparent;
}
QListWidget#lanDeviceList {
    background: #0c0e14;
    border: 1px solid #222836;
    border-radius: 8px;
    padding: 6px;
    outline: none;
    font-size: 13px;
}
QListWidget#lanDeviceList::item {
    padding: 12px 10px;
    border-radius: 8px;
    margin: 2px 0;
}
QListWidget#lanDeviceList::item:selected {
    background: #1a5c38;
    color: #ffffff;
}
QListWidget#lanDeviceList::item:hover {
    background: #1a1f2b;
}
QPushButton#analyzeBtn {
    background: #1f9d57;
    color: white;
    font-size: 14px;
    font-weight: 800;
    border-radius: 10px;
    padding: 14px 24px;
    border: none;
    min-width: 200px;
}
QPushButton#analyzeBtn:hover { background: #24b364; }
QPushButton#analyzeBtn:pressed { background: #188a4a; }
QPushButton#analyzeBtn:disabled {
    background: #1e3d2c;
    color: #7a8a80;
}
QPushButton#clearBtn {
    background: #1c2130;
    color: #d0d6e0;
    font-weight: 700;
    border-radius: 10px;
    padding: 12px 18px;
    border: 1px solid #303848;
}
QPushButton#clearBtn:hover { background: #242b3c; }
QPushButton#ghostBtn {
    background: transparent;
    color: #8b95a8;
    font-weight: 600;
    border: 1px solid #303848;
    border-radius: 8px;
    padding: 6px 12px;
}
QPushButton#ghostBtn:hover {
    background: #1a1f2b;
    color: #c8d0dc;
}
QPushButton#closeBtn {
    background: #2a1518;
    color: #f0a8a8;
    font-weight: 700;
    border-radius: 10px;
    padding: 10px 16px;
    border: 1px solid #4a2828;
}
QPushButton#closeBtn:hover { background: #3a1c20; }
QSpinBox#pitSpin, QComboBox#pitSpin, QLineEdit#pitSpin {
    background: #0c0e14;
    color: #f0f4f8;
    border: 1px solid #303848;
    border-radius: 8px;
    padding: 8px 10px;
    font-weight: 600;
    min-width: 88px;
}
QToolButton#toggleChip {
    background: #242b3c;
    color: #d0d6e0;
    font-weight: 800;
    border-radius: 999px;
    padding: 6px 14px;
    border: 1px solid #303848;
    min-width: 52px;
    max-width: 52px;
}
QToolButton#toggleChip:checked {
    background: #1a5c38;
    border-color: #2d8a56;
}
"""


def make_card(step: str, title: str, hint: str = "") -> tuple[QWidget, QVBoxLayout]:
    card = QWidget()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(8)
    step_lab = QLabel(step)
    step_lab.setObjectName("cardStep")
    title_lab = QLabel(title)
    title_lab.setObjectName("cardTitle")
    layout.addWidget(step_lab)
    layout.addWidget(title_lab)
    if hint:
        hint_lab = QLabel(hint)
        hint_lab.setObjectName("cardHint")
        hint_lab.setWordWrap(True)
        layout.addWidget(hint_lab)
    return card, layout


def make_field_column(label: str, widget: QWidget) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(4)
    lab = QLabel(label)
    lab.setObjectName("fieldLabel")
    col.addWidget(lab)
    col.addWidget(widget)
    return col


def make_collapsible_section(title: str, *, expanded: bool = True) -> tuple[QWidget, QVBoxLayout]:
    section = QWidget()
    section.setObjectName("collapseSection")
    outer = QVBoxLayout(section)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(6)

    header = QToolButton()
    header.setObjectName("collapseHeader")
    header.setText(title)
    header.setCheckable(True)
    header.setChecked(expanded)
    header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
    header.setCursor(Qt.PointingHandCursor)

    body_wrap = QWidget()
    body_wrap.setObjectName("collapseBody")
    body = QVBoxLayout(body_wrap)
    body.setContentsMargins(0, 0, 0, 4)
    body.setSpacing(8)

    def _toggle(on: bool) -> None:
        body_wrap.setVisible(on)
        header.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    header.toggled.connect(_toggle)
    body_wrap.setVisible(expanded)
    outer.addWidget(header)
    outer.addWidget(body_wrap)
    return section, body


def make_sidebar_scroll(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("sideScroll")
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(inner)
    return scroll
