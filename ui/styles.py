"""
Dark industrial theme. Amber accent. Dense data layout.
Think Garmin Edge meets a Grafana dashboard.
"""

STYLESHEET = """
QMainWindow, QWidget {
    background-color: #0D0F12;
    color: #E8E8E8;
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
    font-size: 13px;
}

/* ── Tabs ─────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #1E2330;
    background: #0D0F12;
}
QTabBar::tab {
    background: #141720;
    color: #666B7A;
    padding: 8px 22px;
    border: 1px solid #1E2330;
    border-bottom: none;
    font-size: 11px;
    letter-spacing: 2px;
    text-transform: uppercase;
}
QTabBar::tab:selected {
    background: #0D0F12;
    color: #F5A623;
    border-top: 2px solid #F5A623;
}
QTabBar::tab:hover:!selected {
    color: #BBBBBB;
}

/* ── Metric cards ─────────────────────────────────────── */
#metric-card {
    background: #141720;
    border: 1px solid #1E2330;
    border-radius: 4px;
}
#metric-card:hover {
    border-color: #F5A623;
}
#metric-value {
    color: #FFFFFF;
    font-size: 48px;
    font-weight: bold;
    letter-spacing: -1px;
}
#metric-label {
    color: #55606E;
    font-size: 10px;
    letter-spacing: 3px;
    text-transform: uppercase;
}
#metric-unit {
    color: #F5A623;
    font-size: 13px;
}
#metric-value-sm {
    color: #FFFFFF;
    font-size: 28px;
    font-weight: bold;
}

/* ── HR zone accent colors ───────────────────────────── */
#zone1 { color: #4A90D9; }  /* Z1 active recovery */
#zone2 { color: #4DB87A; }  /* Z2 endurance */
#zone3 { color: #F5C842; }  /* Z3 tempo */
#zone4 { color: #F5A623; }  /* Z4 threshold */
#zone5 { color: #E04040; }  /* Z5 VO2max */

/* ── Buttons ──────────────────────────────────────────── */
QPushButton {
    background: #1E2330;
    color: #E8E8E8;
    border: 1px solid #2A3040;
    border-radius: 3px;
    padding: 7px 18px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 2px;
    text-transform: uppercase;
}
QPushButton:hover {
    background: #252B3D;
    border-color: #F5A623;
    color: #F5A623;
}
QPushButton:pressed {
    background: #F5A623;
    color: #0D0F12;
}
QPushButton:disabled {
    color: #333840;
    border-color: #1A1E28;
}
QPushButton#connect-btn {
    background: #0F2A12;
    border-color: #2A6B2E;
    color: #4DB87A;
}
QPushButton#connect-btn:hover {
    background: #1A3D1E;
    border-color: #4DB87A;
}
QPushButton#stop-btn {
    background: #2A0F0F;
    border-color: #6B2A2A;
    color: #E04040;
}

/* ── Status bar ───────────────────────────────────────── */
QStatusBar {
    background: #080A0E;
    color: #55606E;
    font-size: 10px;
    letter-spacing: 1px;
}

/* ── Table (history) ──────────────────────────────────── */
QTableWidget {
    background: #0D0F12;
    gridline-color: #1A1E28;
    border: none;
    font-size: 12px;
}
QTableWidget::item {
    padding: 6px 10px;
    border-bottom: 1px solid #141720;
}
QTableWidget::item:selected {
    background: #1E2330;
    color: #F5A623;
}
QHeaderView::section {
    background: #080A0E;
    color: #55606E;
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid #1E2330;
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
}

/* ── ComboBox ─────────────────────────────────────────── */
QComboBox {
    background: #1E2330;
    border: 1px solid #2A3040;
    color: #E8E8E8;
    padding: 5px 10px;
    border-radius: 3px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background: #141720;
    border: 1px solid #2A3040;
    selection-background-color: #252B3D;
}

/* ── Scrollbar ────────────────────────────────────────── */
QScrollBar:vertical {
    background: #0D0F12;
    width: 6px;
    border: none;
}
QScrollBar::handle:vertical {
    background: #2A3040;
    border-radius: 3px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #F5A623;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* ── Labels ───────────────────────────────────────────── */
QLabel#section-header {
    color: #55606E;
    font-size: 10px;
    letter-spacing: 3px;
    text-transform: uppercase;
    padding: 4px 0;
    border-bottom: 1px solid #1E2330;
}
QLabel#vo2-display {
    color: #F5A623;
    font-size: 52px;
    font-weight: bold;
}
QLabel#vo2-class {
    color: #4DB87A;
    font-size: 14px;
    letter-spacing: 3px;
}

/* ── Separator ────────────────────────────────────────── */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {
    color: #1E2330;
}

/* ── Tooltip ──────────────────────────────────────────── */
QToolTip {
    background: #141720;
    border: 1px solid #F5A623;
    color: #E8E8E8;
    padding: 4px 8px;
}
"""

# pyqtgraph plot config
PG_BACKGROUND   = "#0D0F12"
PG_FOREGROUND   = "#55606E"
PG_CADENCE_PEN  = {"color": "#F5A623", "width": 2}
PG_HR_PEN       = {"color": "#E04040",  "width": 2}
PG_POWER_PEN    = {"color": "#4A90D9", "width": 2}
PG_SPEED_PEN    = {"color": "#4DB87A", "width": 2}
PG_GRID_ALPHA   = 30
