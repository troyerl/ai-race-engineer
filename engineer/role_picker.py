"""Startup modal to choose broadcaster vs receiver without CLI flags."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class RolePickerDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._role: str | None = None
        self.setWindowTitle("AI Race Engineer")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setObjectName("rolePickerRoot")
        self.setStyleSheet(
            """
            QWidget#rolePickerRoot {
                background-color: #0c0e14;
                color: #e8edf4;
            }
            QLabel#rolePickerTitle {
                font-size: 22px;
                font-weight: 800;
                color: #f4f7fb;
            }
            QLabel#rolePickerSubtitle {
                font-size: 13px;
                font-weight: 500;
                color: #8b95a8;
            }
            QPushButton#roleOption {
                text-align: left;
                background: #141820;
                border: 1px solid #303848;
                border-radius: 12px;
                padding: 16px 18px;
                color: #f0f4f8;
                font-size: 15px;
                font-weight: 700;
            }
            QPushButton#roleOption:hover {
                border-color: #5ec996;
                background: #18202c;
            }
            QPushButton#roleOption:pressed {
                background: #1c2836;
            }
            QLabel#roleOptionHint {
                font-size: 12px;
                font-weight: 500;
                color: #8b95a8;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Which machine is this?")
        title.setObjectName("rolePickerTitle")
        root.addWidget(title)

        subtitle = QLabel("Pick the role for this computer. You can still override with --role on the command line.")
        subtitle.setObjectName("rolePickerSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        root.addSpacing(8)

        root.addWidget(self._make_option(
            "Sim PC — Broadcaster",
            "Runs beside iRacing. Streams telemetry and speaks engineer calls.",
            "broadcaster",
        ))
        root.addWidget(self._make_option(
            "Engineer PC — Receiver",
            "Runs the AI overlay. Connect to the sim PC on your LAN.",
            "receiver",
        ))

        root.addStretch(1)

        footer = QLabel("Advanced: --role local for single-PC mode")
        footer.setObjectName("roleOptionHint")
        footer.setAlignment(Qt.AlignCenter)
        root.addWidget(footer)

    def _make_option(self, title: str, hint: str, role: str) -> QWidget:
        btn = QPushButton()
        btn.setObjectName("roleOption")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _checked=False, r=role: self._choose(r))

        layout = QVBoxLayout(btn)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #f0f4f8; background: transparent;")
        title_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(title_lbl)

        hint_lbl = QLabel(hint)
        hint_lbl.setObjectName("roleOptionHint")
        hint_lbl.setWordWrap(True)
        hint_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(hint_lbl)

        return btn

    def _choose(self, role: str) -> None:
        self._role = role
        self.accept()

    def selected_role(self) -> str | None:
        return self._role


def pick_startup_role(parent: QWidget | None = None) -> str | None:
    dialog = RolePickerDialog(parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_role()
