"""
Race history tab — line chart of pre-race branch plans vs actual session.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .race_history import RaceHistoryEntry, list_race_history
from .race_results_chart import RaceResultsChartWidget


class RaceHistoryTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[RaceHistoryEntry] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Previous races — plan vs actual")
        title.setObjectName("cardHint")
        header.addWidget(title)
        header.addStretch(1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("ghostBtn")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        hint = QLabel(
            "Requires a pre-race strategy (3 caution branches) and a recorded session with lap data."
        )
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        splitter = QSplitter(Qt.Horizontal)
        self._list = QListWidget()
        self._list.setMinimumWidth(220)
        self._list.currentItemChanged.connect(self._on_selection)
        splitter.addWidget(self._list)

        self._chart = RaceResultsChartWidget()
        splitter.addWidget(self._chart)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.refresh()

    def refresh(self) -> None:
        self._entries = list_race_history()
        self._list.clear()
        if not self._entries:
            item = QListWidgetItem("No chart-ready sessions yet.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
            self._chart.set_entry(None)
            return
        for entry in self._entries:
            start = finish = "?"
            for s in entry.series:
                if s.color_key == "actual":
                    if s.start_position is not None:
                        start = str(s.start_position)
                    if s.finish_position is not None:
                        finish = str(s.finish_position)
            label = f"{entry.track_name}  ·  P{start}→P{finish}"
            if entry.recorded_at:
                label += f"  ·  {entry.recorded_at[:10]}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._list.addItem(item)
        self._list.setCurrentRow(0)

    def _on_selection(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if current is None:
            self._chart.set_entry(None)
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        self._chart.set_entry(entry if isinstance(entry, RaceHistoryEntry) else None)
