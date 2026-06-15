"""
Qt strategy review dashboard — lap timeline with BASE PLAN vs LIVE CALLS.

Opens recorded session JSONL files for post-race analysis without changing
the live strategy engine.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .session_recorder import default_session_dir, latest_session_file
from .session_report import (
    SessionReport,
    analyze_session_file,
    generate_executive_summary,
    load_session_meta,
)


class ReviewDashboardDialog(QDialog):
    """Post-race review: summary, optional baseline plan, lap-by-lap timeline."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        session_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Strategy review")
        self.setMinimumSize(880, 560)
        self.resize(1020, 680)
        self._session_path: Path | None = Path(session_path) if session_path else None
        self._report: SessionReport | None = None

        self.setStyleSheet(
            """
            QDialog {
                background-color: rgb(12, 14, 20);
                color: #E8EEF2;
            }
            QLabel#reviewTitle {
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#reviewSummary {
                color: rgba(200, 220, 210, 255);
                padding: 8px;
                background: rgb(18, 20, 28);
                border: 1px solid rgb(50, 54, 66);
                border-radius: 8px;
            }
            QPlainTextEdit, QTableWidget {
                background: rgb(18, 20, 28);
                border: 1px solid rgb(50, 54, 66);
                border-radius: 8px;
                color: #E8EEF2;
            }
            QPushButton {
                background: rgb(32, 36, 48);
                border: 1px solid rgb(60, 64, 78);
                border-radius: 8px;
                padding: 8px 14px;
            }
            QPushButton:hover { background: rgb(42, 46, 58); }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        self._title = QLabel("Strategy review")
        self._title.setObjectName("reviewTitle")
        header.addWidget(self._title)
        header.addStretch(1)
        self._open_btn = QPushButton("Open session…")
        self._open_btn.clicked.connect(self._pick_session)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._reload)
        header.addWidget(self._open_btn)
        header.addWidget(self._refresh_btn)
        root.addLayout(header)

        self._summary = QLabel("Open a recorded session JSONL file to begin.")
        self._summary.setObjectName("reviewSummary")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        splitter = QSplitter(Qt.Horizontal)
        self._baseline_edit = QPlainTextEdit()
        self._baseline_edit.setPlaceholderText(
            "Optional BASE PLAN text (L12 pit stops, etc.) — loaded from .meta.json when pinned during recording."
        )
        self._baseline_edit.setMinimumWidth(280)
        baseline_wrap = QWidget()
        bl = QVBoxLayout(baseline_wrap)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(QLabel("BASE PLAN (optional)"))
        bl.addWidget(self._baseline_edit, 1)
        apply_plan_btn = QPushButton("Apply plan to report")
        apply_plan_btn.clicked.connect(self._reload)
        bl.addWidget(apply_plan_btn)
        splitter.addWidget(baseline_wrap)

        timeline_wrap = QWidget()
        tl = QVBoxLayout(timeline_wrap)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("Stop plan vs actual"))
        self._stop_table = QTableWidget(0, 6)
        self._stop_table.setHorizontalHeaderLabels(
            ["Stop", "Plan", "Actual", "Δ Lap", "Fuel@plan", "Δ Fuel"]
        )
        self._stop_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._stop_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._stop_table.setMaximumHeight(130)
        tl.addWidget(self._stop_table)

        tl.addWidget(QLabel("Lap timeline (LIVE CALLS)"))
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels(
            ["Lap", "Pos", "Fuel", "Plan", "Actual", "Mode", "Therm", "Call", "Cau"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        tl.addWidget(self._table, 1)
        splitter.addWidget(timeline_wrap)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        if self._session_path is not None:
            self._reload()

    def _pick_session(self) -> None:
        start_dir = str(default_session_dir())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open session recording",
            start_dir,
            "Session recordings (*.jsonl);;All files (*)",
        )
        if path:
            self._session_path = Path(path)
            self._reload()

    def _reload(self) -> None:
        if self._session_path is None:
            latest = latest_session_file()
            if latest is None:
                return
            self._session_path = latest

        baseline_override = self._baseline_edit.toPlainText().strip() or None
        meta = load_session_meta(self._session_path)
        if not baseline_override and meta.get("baseline_plan"):
            self._baseline_edit.setPlainText(str(meta["baseline_plan"]))
            baseline_override = str(meta["baseline_plan"])

        try:
            report = analyze_session_file(
                self._session_path,
                baseline_plan=baseline_override,
                include_replay=True,
            )
        except Exception as exc:
            self._summary.setText(f"Failed to load session: {exc}")
            return

        self._report = report
        self._title.setText(f"Strategy review — {report.track_name}")
        summary_bits = [generate_executive_summary(report)]
        if report.start_position is not None and report.finish_position is not None:
            summary_bits.insert(
                0,
                f"P{report.start_position} → P{report.finish_position} · "
                f"{len(report.actual_stop_laps)} stop(s) · {report.packet_count} laps recorded",
            )
        if report.stop_comparisons:
            fuel_bits = [
                f"Δfuel {sc.fuel_delta_laps:+.1f}L"
                for sc in report.stop_comparisons
                if sc.fuel_delta_laps is not None
            ]
            if fuel_bits:
                summary_bits.append("Fuel at stops vs plan: " + ", ".join(fuel_bits))
        self._summary.setText("\n\n".join(summary_bits))

        planned_set = set(report.planned_stop_laps)
        actual_set = set(report.actual_stop_laps)
        mismatch_laps = set()
        for sc in report.stop_comparisons:
            if sc.lap_delta not in (None, 0):
                if sc.planned_lap is not None:
                    mismatch_laps.add(sc.planned_lap)
                if sc.actual_lap is not None:
                    mismatch_laps.add(sc.actual_lap)

        self._stop_table.setRowCount(len(report.stop_comparisons))
        warn_bg = QColor(80, 55, 30)
        for row_idx, sc in enumerate(report.stop_comparisons):
            cells = [
                str(sc.stop_index),
                f"L{sc.planned_lap}" if sc.planned_lap is not None else "—",
                f"L{sc.actual_lap}" if sc.actual_lap is not None else "—",
                f"{sc.lap_delta:+d}" if sc.lap_delta is not None else "",
                f"{sc.fuel_at_planned:.1f}" if sc.fuel_at_planned is not None else "",
                f"{sc.fuel_delta_laps:+.1f}" if sc.fuel_delta_laps is not None else "",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if sc.lap_delta not in (None, 0):
                    item.setBackground(warn_bg)
                self._stop_table.setItem(row_idx, col, item)

        self._table.setRowCount(len(report.laps))
        for row_idx, lap_row in enumerate(report.laps):
            plan_mark = "PLAN" if lap_row.lap in planned_set else ""
            actual_mark = "STOP" if lap_row.lap in actual_set else ""
            cells = [
                str(lap_row.lap),
                str(lap_row.position or ""),
                f"{lap_row.fuel_laps:.1f}" if lap_row.fuel_laps is not None else "",
                plan_mark,
                actual_mark,
                lap_row.mode or "",
                lap_row.thermal or "",
                lap_row.call_line or lap_row.call_action or "",
                "Y" if lap_row.is_caution else "",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if lap_row.lap in mismatch_laps:
                    item.setBackground(warn_bg)
                self._table.setItem(row_idx, col, item)


def open_review_dashboard(
    parent: QWidget | None = None,
    *,
    session_path: Path | str | None = None,
) -> ReviewDashboardDialog:
    """Show the review dashboard (non-modal)."""
    dlg = ReviewDashboardDialog(parent, session_path=session_path)
    dlg.show()
    return dlg
