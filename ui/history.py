"""
History tab: session log + VO2 max trend chart + power curve.
"""

import datetime
import pyqtgraph as pg
import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QSplitter,
    QPushButton, QHeaderView, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from ui.widgets import SectionHeader
from ui.styles import (
    PG_BACKGROUND, PG_FOREGROUND,
    PG_CADENCE_PEN, PG_HR_PEN, PG_POWER_PEN,
)
import pyqtgraph as pg


class HistoryTab(QWidget):
    session_selected = pyqtSignal(int)  # session_id

    def __init__(self, session_db, parent=None):
        super().__init__(parent)
        self.db = session_db
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(SectionHeader("Session History"))

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Session table ──────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "Date", "Duration", "Distance", "Avg Power", "NP",
            "TSS", "Avg Cad", "Avg HR", "VO2 Est.", "kcal"
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.cellClicked.connect(self._on_row_click)
        splitter.addWidget(self.table)

        # ── Charts panel ───────────────────────────────
        charts = QWidget()
        charts_layout = QHBoxLayout(charts)
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(10)

        # VO2 max trend
        self.vo2_plot = pg.PlotWidget(background=PG_BACKGROUND)
        self.vo2_plot.setLabel('left', 'VO2max', units='ml/kg/min', color=PG_FOREGROUND)
        self.vo2_plot.setLabel('bottom', 'Date', color=PG_FOREGROUND)
        self.vo2_plot.showGrid(x=True, y=True, alpha=0.15)
        self.vo2_plot.setTitle("VO2 MAX TREND", color=PG_FOREGROUND, size="10pt")
        charts_layout.addWidget(self.vo2_plot)

        # Power curve (all-time bests)
        self.pc_plot = pg.PlotWidget(background=PG_BACKGROUND)
        self.pc_plot.setLabel('left', 'Power', units='W', color=PG_FOREGROUND)
        self.pc_plot.setLabel('bottom', 'Duration', units='s', color=PG_FOREGROUND)
        self.pc_plot.showGrid(x=True, y=True, alpha=0.15)
        self.pc_plot.setTitle("POWER CURVE (ALL-TIME BESTS)", color=PG_FOREGROUND, size="10pt")
        self.pc_plot.setLogMode(x=True, y=False)
        charts_layout.addWidget(self.pc_plot)

        splitter.addWidget(charts)
        splitter.setSizes([300, 350])
        layout.addWidget(splitter)

        # Refresh button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        refresh_btn = QPushButton("↻  REFRESH")
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(refresh_btn)
        layout.addLayout(btn_row)

    def refresh(self):
        sessions = self.db.get_sessions(limit=100)
        self._populate_table(sessions)
        self._update_vo2_chart()
        self._update_power_curve()

    def _populate_table(self, sessions: list[dict]):
        self.table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            dt = datetime.datetime.fromtimestamp(s["started_at"])
            dur_s = s.get("duration_s") or 0
            h, rem = divmod(dur_s, 3600)
            m, sec = divmod(rem, 60)
            dur_str = f"{h:01d}:{m:02d}:{sec:02d}"

            def cell(val):
                item = QTableWidgetItem(str(val) if val is not None else "—")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                return item

            km = (s.get("distance_m") or 0) / 1000
            self.table.setItem(row, 0, cell(dt.strftime("%Y-%m-%d %H:%M")))
            self.table.setItem(row, 1, cell(dur_str))
            self.table.setItem(row, 2, cell(f"{km:.1f} km"))
            self.table.setItem(row, 3, cell(f"{s.get('avg_power') or 0:.0f} W"))
            self.table.setItem(row, 4, cell(f"{s.get('np_watts') or 0:.0f} W"))
            self.table.setItem(row, 5, cell(f"{s.get('tss') or 0:.0f}"))
            self.table.setItem(row, 6, cell(f"{s.get('avg_cadence') or 0:.0f}"))
            self.table.setItem(row, 7, cell(f"{s.get('avg_hr') or 0:.0f}"))
            vo2 = s.get("vo2max_est")
            vo2_cell = cell(f"{vo2:.1f}" if vo2 else "—")
            if vo2:
                vo2_cell.setForeground(QColor("#F5A623"))
            self.table.setItem(row, 8, vo2_cell)
            self.table.setItem(row, 9, cell(f"{s.get('total_kcal') or 0:.0f}"))

            # Alternate row shading
            shade = "#141720" if row % 2 else "#0D0F12"
            for col in range(10):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(QColor(shade))

    def _update_vo2_chart(self):
        history = self.db.get_vo2_history()
        if not history:
            return
        self.vo2_plot.clear()
        ts  = [h["ts"] for h in history]
        est = [h["estimate"] for h in history]
        ver = [(h["ts"], h["estimate"]) for h in history if h["verified"]]

        # Trend line
        self.vo2_plot.plot(ts, est, pen=PG_POWER_PEN, symbol='o',
                           symbolBrush='#4A90D9', symbolSize=6)

        # Verified markers
        if ver:
            vx, vy = zip(*ver)
            self.vo2_plot.plot(list(vx), list(vy), pen=None, symbol='star',
                               symbolBrush='#F5A623', symbolSize=14,
                               name="Verified")

        # Moving average (if enough points)
        if len(est) >= 5:
            window = min(5, len(est))
            ma = np.convolve(est, np.ones(window)/window, mode='valid')
            ma_ts = ts[window-1:]
            self.vo2_plot.plot(ma_ts, list(ma),
                               pen={"color": "#F5A623", "width": 2, "style": Qt.PenStyle.DashLine})

    def _update_power_curve(self):
        bests = self.db.get_best_powers()
        if not bests:
            return
        self.pc_plot.clear()
        durations = sorted(bests.keys())
        powers = [bests[d] for d in durations]
        self.pc_plot.plot(durations, powers,
                          pen={"color": "#F5A623", "width": 2},
                          symbol='o', symbolBrush='#F5A623', symbolSize=5)

    def _on_row_click(self, row, col):
        sessions = self.db.get_sessions(limit=100)
        if row < len(sessions):
            self.session_selected.emit(sessions[row]["id"])


class SessionDetailTab(QWidget):
    """Drill into a single session — shows power/HR/cadence chart."""

    def __init__(self, session_db, parent=None):
        super().__init__(parent)
        self.db = session_db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        self.title = QLabel("SELECT A SESSION FROM HISTORY")
        self.title.setObjectName("section-header")
        layout.addWidget(self.title)

        self.plot = pg.PlotWidget(background=PG_BACKGROUND)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.addLegend()
        layout.addWidget(self.plot)

    def load_session(self, session_id: int):
        points = self.db.get_session_datapoints(session_id)
        if not points:
            return
        self.plot.clear()
        t0 = points[0]["ts"]
        xs = [(p["ts"] - t0) / 60 for p in points]  # minutes

        def series(key, default=0):
            return [p.get(key) or default for p in points]

        self.plot.setLabel('bottom', 'Time', units='min', color=PG_FOREGROUND)
        self.plot.plot(xs, series("cadence"), pen=PG_CADENCE_PEN, name="Cadence (rpm)")
        self.plot.plot(xs, series("hr"),      pen=PG_HR_PEN,      name="Heart Rate (bpm)")
        self.plot.plot(xs, series("power"),   pen=PG_POWER_PEN,   name="Power (W)")
        self.title.setText(f"SESSION  #{session_id}")
