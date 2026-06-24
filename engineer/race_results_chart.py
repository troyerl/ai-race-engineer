"""
Line chart: pre-race branch position projections vs actual race trajectory.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .race_history import PositionSeries, RaceHistoryEntry

SERIES_COLORS: dict[str, QColor] = {
    "light": QColor(100, 200, 130),
    "moderate": QColor(230, 190, 80),
    "heavy": QColor(230, 120, 90),
    "actual": QColor(90, 180, 255),
    "plan": QColor(160, 160, 180),
}


class RaceResultsChartWidget(QWidget):
    """Position-by-lap chart (lower Y = better finish position)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry: RaceHistoryEntry | None = None
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_entry(self, entry: RaceHistoryEntry | None) -> None:
        self._entry = entry
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)

        painter.fillRect(self.rect(), QColor(12, 14, 20))

        if self._entry is None or not self._entry.series:
            painter.setPen(QColor(180, 190, 200))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Select a recorded race to compare plans vs actual.")
            painter.end()
            return

        margin_left = 42
        margin_bottom = 36
        margin_top = 28
        margin_right = 12
        plot = rect.adjusted(margin_left, margin_top, -margin_right, -margin_bottom)

        all_points: list[list[int]] = []
        for s in self._entry.series:
            all_points.extend(s.positions)
        if not all_points:
            painter.setPen(QColor(180, 190, 200))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No position data in this session.")
            painter.end()
            return

        laps = [p[0] for p in all_points]
        positions = [p[1] for p in all_points]
        lap_min, lap_max = min(laps), max(laps)
        pos_min, pos_max = min(positions), max(positions)
        if lap_max == lap_min:
            lap_max = lap_min + 1
        if pos_max == pos_min:
            pos_max = pos_min + 1

        # Pad position range for readability.
        pos_min = max(1, pos_min - 1)
        pos_max = pos_max + 1

        def x_for_lap(lap: int) -> float:
            return plot.left() + (lap - lap_min) / (lap_max - lap_min) * plot.width()

        def y_for_pos(pos: int) -> float:
            return plot.top() + (pos - pos_min) / (pos_max - pos_min) * plot.height()

        # Grid
        painter.setPen(QPen(QColor(50, 54, 66), 1))
        for p in range(pos_min, pos_max + 1):
            y = y_for_pos(p)
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
            painter.setPen(QColor(140, 150, 165))
            painter.drawText(4, int(y + 4), f"P{p}")
            painter.setPen(QPen(QColor(50, 54, 66), 1))

        # Title
        title = self._entry.track_name
        if self._entry.recorded_at:
            title += f"  ·  {self._entry.recorded_at[:16].replace('T', ' ')}"
        painter.setPen(QColor(220, 230, 235))
        painter.setFont(QFont("", 11, QFont.Weight.Bold))
        painter.drawText(rect.left(), rect.top() + 14, title)

        # Lines
        legend_y = rect.bottom() - 8
        legend_x = plot.left()
        for series in self._entry.series:
            if len(series.positions) < 2:
                continue
            color = SERIES_COLORS.get(series.color_key, SERIES_COLORS["plan"])
            width = 3 if series.color_key == "actual" else 2
            pen = QPen(color, width)
            if series.color_key != "actual":
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            pts = series.positions
            for i in range(1, len(pts)):
                x0, y0 = x_for_lap(pts[i - 1][0]), y_for_pos(pts[i - 1][1])
                x1, y1 = x_for_lap(pts[i][0]), y_for_pos(pts[i][1])
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))

            start = series.start_position
            finish = series.finish_position
            leg = series.label.split("(")[0].strip()[:18]
            if start is not None and finish is not None:
                leg += f" P{start}→P{finish}"
            painter.setPen(color)
            painter.setFont(QFont("", 9))
            painter.drawText(int(legend_x), int(legend_y), leg)
            legend_x += min(220, plot.width() / max(1, len(self._entry.series)))

        # Lap axis label
        painter.setPen(QColor(140, 150, 165))
        painter.drawText(plot.center().x() - 20, rect.bottom() - 2, "Lap →")

        painter.end()
