"""
Main PyQt6 application window.

Live Session tab:
  - Big metric cards: Cadence | Power | HR | Speed | Resistance
  - Real-time pyqtgraph plots (rolling 5 min window)
  - Session timer + interval tracker
  - VO2 max live estimate
  - HR zone indicator

Tabs: LIVE SESSION | HISTORY | SESSION DETAIL | SETTINGS
"""

import asyncio
import time
import logging
from dataclasses import dataclass
from collections import deque
from typing import Optional
from math import sqrt

import pyqtgraph as pg

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QTabWidget, QSplitter,
    QStatusBar, QDialog, QListWidget, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot, pyqtSignal

import config
from ble.ftms_client import FTMSClient, BikeData
from ble.hr_client import HeartRateClient, HeartRateSample
from db.database import SessionDB
from analytics.metrics import (
    estimate_power, RollingMetrics, LiveVO2Estimator,
    classify_vo2max
)
from ui.widgets import MetricCard, SectionHeader, HRule
from ui.history import HistoryTab, SessionDetailTab
import ui.styles as styles

log = logging.getLogger(__name__)

# Rolling window: 5 minutes @ ~2 samples/sec
WINDOW = 600
@dataclass
class SessionStats:
    cadence_sum: float = 0.0
    cadence_count: int = 0
    hr_sum: int = 0
    hr_count: int = 0
    max_hr: int = 0
    total_kcal: float = 0.0
    last_kcal: Optional[float] = None

    def push(self, bd: BikeData, hr: Optional[int] = None):
        if bd.cadence is not None:
            self.cadence_sum += bd.cadence
            self.cadence_count += 1

        hr_value = hr if hr is not None else bd.hr
        if hr_value:
            self.hr_sum += hr_value
            self.hr_count += 1
            self.max_hr = max(self.max_hr, hr_value)

        if bd.total_energy_kcal is not None:
            if self.last_kcal is None:
                self.total_kcal = float(bd.total_energy_kcal)
            else:
                self.total_kcal = max(self.total_kcal, float(bd.total_energy_kcal))
            self.last_kcal = self.total_kcal

    def avg_cadence(self) -> float:
        if self.cadence_count == 0:
            return 0.0
        return self.cadence_sum / self.cadence_count

    def avg_hr(self) -> float:
        if self.hr_count == 0:
            return 0.0
        return self.hr_sum / self.hr_count


@dataclass
class LiveDisplayState:
    cadence: Optional[float] = None
    power: Optional[float] = None
    speed: Optional[float] = None
    resistance: Optional[int] = None
    total_kcal: Optional[float] = None
    distance_km: Optional[float] = None
    hr: Optional[int] = None
    rr_ms: Optional[float] = None
    hrv_rmssd_ms: Optional[float] = None


HR_ZONES = [
    (0.50, "#4A90D9", "Z1"),
    (0.60, "#4DB87A", "Z2"),
    (0.70, "#F5C842", "Z3"),
    (0.80, "#F5A623", "Z4"),
    (0.90, "#E04040", "Z5"),
    (1.00, "#B00000", "Z5+"),
]


def hr_zone(hr: int, max_hr: int) -> tuple[str, str]:
    pct = hr / max_hr
    for threshold, color, label in HR_ZONES:
        if pct <= threshold:
            return label, color
    return "Z5+", "#B00000"


def rmssd(rr_intervals_ms: list[float]) -> Optional[float]:
    if len(rr_intervals_ms) < 2:
        return None
    diffs = [
        (rr_intervals_ms[i] - rr_intervals_ms[i - 1]) ** 2
        for i in range(1, len(rr_intervals_ms))
    ]
    return sqrt(sum(diffs) / len(diffs))


class DeviceScanDialog(QDialog):
    def __init__(self, devices: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Device")
        self.setMinimumWidth(400)
        self.selected = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("FTMS-compatible devices found:"))

        self.list = QListWidget()
        for d in devices:
            self.list.addItem(f"{d['name']}  [{d['address']}]")
        self.list.setCurrentRow(0)
        layout.addWidget(self.list)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._devices = devices

    def _accept(self):
        row = self.list.currentRow()
        if row >= 0:
            self.selected = self._devices[row]
        self.accept()


class LiveSessionTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cadence_buf: deque[float] = deque([0.0] * WINDOW, maxlen=WINDOW)
        self._speed_buf:   deque[float] = deque([0.0] * WINDOW, maxlen=WINDOW)
        self._power_buf:   deque[float] = deque([0.0] * WINDOW, maxlen=WINDOW)
        self._rr_buf:      deque[float] = deque(maxlen=120)
        self._xs = list(range(-WINDOW, 0))
        self._setup_ui()

        self._session_start: Optional[float] = None
        self._rolling: Optional[RollingMetrics] = None
        self._vo2_est: Optional[LiveVO2Estimator] = None

        # UI update timer
        self._ui_timer = QTimer()
        self._ui_timer.setInterval(500)
        self._ui_timer.timeout.connect(self._tick)

        self._last_data: Optional[BikeData] = None
        self._display = LiveDisplayState()
        self._max_hr = config.USER.get("max_hr") or (220 - config.USER["age"])

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── Header row ─────────────────────────────────
        header = QHBoxLayout()
        self.session_status = QLabel("● DISCONNECTED")
        self.session_status.setStyleSheet("color: #E04040; font-size: 11px; letter-spacing: 2px;")
        header.addWidget(self.session_status)
        header.addStretch()

        self.timer_label = QLabel("00:00:00")
        self.timer_label.setStyleSheet("color: #F5A623; font-size: 24px; font-weight: bold;")
        header.addWidget(self.timer_label)
        root.addLayout(header)

        root.addWidget(HRule())

        # ── Big metric cards ───────────────────────────
        grid = QGridLayout()
        grid.setSpacing(8)

        self.card_cadence  = MetricCard("Cadence",    "RPM")
        self.card_power    = MetricCard("Power",      "W")
        self.card_hr       = MetricCard("Heart Rate", "BPM")
        self.card_speed    = MetricCard("Speed",      "km/h", size="small")
        self.card_resist   = MetricCard("Resistance", "%",    size="small")
        self.card_kcal     = MetricCard("Energy",     "kcal", size="small")
        self.card_distance = MetricCard("Distance",   "km",   size="small")
        self.card_np       = MetricCard("NP",         "W",    size="small")
        self.card_tss      = MetricCard("TSS",        "",     size="small")
        self.card_rr       = MetricCard("RR",         "ms",   size="small")
        self.card_hrv      = MetricCard("HRV RMSSD",  "ms",   size="small")

        grid.addWidget(self.card_cadence,  0, 0)
        grid.addWidget(self.card_power,    0, 1)
        grid.addWidget(self.card_hr,       0, 2)

        grid.addWidget(self.card_speed,    1, 0)
        grid.addWidget(self.card_resist,   1, 1)
        grid.addWidget(self.card_kcal,     1, 2)
        grid.addWidget(self.card_np,       2, 0)
        grid.addWidget(self.card_tss,      2, 1)
        grid.addWidget(self.card_distance, 2, 2)
        grid.addWidget(self.card_rr,       3, 0)
        grid.addWidget(self.card_hrv,      3, 1)

        # VO2 estimate card
        vo2_card = QWidget()
        vo2_card.setObjectName("metric-card")
        vo2_layout = QVBoxLayout(vo2_card)
        vo2_layout.setContentsMargins(16, 12, 16, 12)
        vo2_lbl = QLabel("VO2 MAX EST.")
        vo2_lbl.setObjectName("metric-label")
        vo2_layout.addWidget(vo2_lbl)
        self.vo2_value = QLabel("---")
        self.vo2_value.setObjectName("vo2-display")
        vo2_layout.addWidget(self.vo2_value)
        self.vo2_class = QLabel("")
        self.vo2_class.setObjectName("vo2-class")
        vo2_layout.addWidget(self.vo2_class)
        grid.addWidget(vo2_card, 3, 2)

        root.addLayout(grid)
        root.addWidget(HRule())

        # ── Training load row ──────────────────────────
        zone_row = QHBoxLayout()
        zone_lbl = QLabel("ZONE:")
        zone_lbl.setStyleSheet("color: #55606E; font-size: 10px; letter-spacing: 2px;")
        zone_row.addWidget(zone_lbl)
        self.zone_label = QLabel("---")
        self.zone_label.setStyleSheet("color: #E8E8E8; font-size: 14px; font-weight: bold;")
        zone_row.addWidget(self.zone_label)
        zone_row.addStretch()
        zone_row.addStretch()
        tss_lbl = QLabel("TSS:")
        tss_lbl.setStyleSheet("color: #55606E; font-size: 10px; letter-spacing: 2px;")
        zone_row.addWidget(tss_lbl)
        self.tss_label = QLabel("0")
        self.tss_label.setStyleSheet("color: #E8E8E8; font-size: 14px;")
        zone_row.addWidget(self.tss_label)

        if_lbl = QLabel("  IF:")
        if_lbl.setStyleSheet("color: #55606E; font-size: 10px; letter-spacing: 2px;")
        zone_row.addWidget(if_lbl)
        self.if_label = QLabel("0.00")
        self.if_label.setStyleSheet("color: #E8E8E8; font-size: 14px;")
        zone_row.addWidget(self.if_label)
        root.addLayout(zone_row)

        # ── Real-time plots ────────────────────────────
        pg.setConfigOptions(antialias=True, background=styles.PG_BACKGROUND)

        plots_split = QSplitter(Qt.Orientation.Horizontal)

        self.cad_plot = pg.PlotWidget(background=styles.PG_BACKGROUND)
        self.cad_plot.setMaximumHeight(160)
        self.cad_plot.setLabel('left', 'RPM', color=styles.PG_FOREGROUND)
        self.cad_plot.showGrid(x=False, y=True, alpha=0.15)
        self.cad_plot.setYRange(0, 120, padding=0)
        self.cad_curve = self.cad_plot.plot(
            self._xs, [0]*WINDOW, pen=pg.mkPen(**styles.PG_CADENCE_PEN)
        )
        self.cad_plot.setTitle("CADENCE", color=styles.PG_FOREGROUND, size="9pt")

        self.power_plot = pg.PlotWidget(background=styles.PG_BACKGROUND)
        self.power_plot.setMaximumHeight(160)
        self.power_plot.setLabel('left', 'W', color=styles.PG_FOREGROUND)
        self.power_plot.showGrid(x=False, y=True, alpha=0.15)
        self.power_plot.setYRange(0, 400, padding=0)
        self.power_curve = self.power_plot.plot(
            self._xs, [0]*WINDOW, pen=pg.mkPen(**styles.PG_POWER_PEN)
        )
        self.power_plot.setTitle("POWER", color=styles.PG_FOREGROUND, size="9pt")

        self.speed_plot = pg.PlotWidget(background=styles.PG_BACKGROUND)
        self.speed_plot.setMaximumHeight(160)
        self.speed_plot.setLabel('left', 'km/h', color=styles.PG_FOREGROUND)
        self.speed_plot.showGrid(x=False, y=True, alpha=0.15)
        self.speed_plot.setYRange(0, 60, padding=0)
        self.speed_curve = self.speed_plot.plot(
            self._xs, [0]*WINDOW, pen=pg.mkPen(**styles.PG_SPEED_PEN)
        )
        self.speed_plot.setTitle("SPEED", color=styles.PG_FOREGROUND, size="9pt")

        plots_split.addWidget(self.cad_plot)
        plots_split.addWidget(self.power_plot)
        plots_split.addWidget(self.speed_plot)
        root.addWidget(plots_split)

    def start_session(self, rolling: RollingMetrics, vo2_est: LiveVO2Estimator):
        self._session_start = time.time()
        self._rolling = rolling
        self._vo2_est = vo2_est
        self._ui_timer.start()
        self.session_status.setText("● RECORDING")
        self.session_status.setStyleSheet(
            "color: #4DB87A; font-size: 11px; letter-spacing: 2px;"
        )

    def stop_session(self):
        self._ui_timer.stop()
        self._session_start = None
        self.session_status.setText("● STOPPED")
        self.session_status.setStyleSheet(
            "color: #55606E; font-size: 11px; letter-spacing: 2px;"
        )

    def set_connected(self, name: str):
        self.session_status.setText(f"● CONNECTED — {name}")
        self.session_status.setStyleSheet(
            "color: #4DB87A; font-size: 11px; letter-spacing: 2px;"
        )

    def set_disconnected(self):
        self.session_status.setText("● DISCONNECTED")
        self.session_status.setStyleSheet(
            "color: #E04040; font-size: 11px; letter-spacing: 2px;"
        )
        self._ui_timer.stop()
        self._session_start = None

    def update_data(self, bd: BikeData, power_w: Optional[float]):
        self._last_data = bd
        bd.instantaneous_power_w = int(power_w) if power_w is not None else bd.instantaneous_power_w

        # Buffer updates (called from async context via QMetaObject)
        if bd.cadence is not None:
            self._cadence_buf.append(bd.cadence)
            self._display.cadence = bd.cadence
        else:
            self._cadence_buf.append(self._cadence_buf[-1])

        speed = bd.speed if bd.speed is not None else self._speed_buf[-1]
        self._speed_buf.append(speed)
        if bd.speed is not None:
            self._display.speed = bd.speed
        if bd.resistance_level is not None:
            self._display.resistance = bd.resistance_level
        if bd.total_energy_kcal is not None:
            self._display.total_kcal = float(bd.total_energy_kcal)
        if bd.total_distance_m is not None:
            self._display.distance_km = bd.total_distance_m / 1000

        pw = power_w or 0
        self._power_buf.append(pw)
        if power_w is not None:
            self._display.power = power_w

    def update_watch_metrics(self, sample: Optional[HeartRateSample]):
        if sample is None:
            self._display.hr = None
            self._display.rr_ms = None
            self._display.hrv_rmssd_ms = None
            self._rr_buf.clear()
            return

        self._display.hr = sample.bpm
        if sample.rr_intervals_ms:
            self._rr_buf.extend(sample.rr_intervals_ms)
            self._display.rr_ms = sample.rr_intervals_ms[-1]
            self._display.hrv_rmssd_ms = rmssd(list(self._rr_buf))

    @pyqtSlot()
    def _tick(self):
        """500ms UI refresh."""
        bd = self._last_data
        if bd is None:
            return

        # Metric cards
        self.card_cadence.set_value(self._display.cadence, "{:.0f}")
        self.card_power.set_value(self._display.power, "{:.0f}")
        self.card_hr.set_value(self._display.hr, "{:.0f}")
        self.card_speed.set_value(self._display.speed, "{:.1f}")
        self.card_resist.set_value(self._display.resistance, "{}")
        self.card_kcal.set_value(self._display.total_kcal, "{:.0f}")
        self.card_distance.set_value(self._display.distance_km, "{:.2f}")
        self.card_rr.set_value(self._display.rr_ms, "{:.0f}")
        self.card_hrv.set_value(self._display.hrv_rmssd_ms, "{:.0f}")

        if self._display.hr:
            zone, color = hr_zone(self._display.hr, self._max_hr)
            self.zone_label.setText(zone)
            self.zone_label.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
            self.card_hr.set_color(color)
        else:
            self.zone_label.setText("---")
            self.card_hr.set_color(None)

        # NP + TSS + IF
        if self._rolling:
            np_w = self._rolling.np()
            self.card_np.set_value(np_w, "{:.0f}")
            dur = (time.time() - self._session_start) if self._session_start else 0
            tss = self._rolling.tss(dur)
            self.card_tss.set_value(tss, "{:.0f}")
            self.tss_label.setText(f"{tss:.0f}")
            self.if_label.setText(f"{self._rolling.intensity_factor():.2f}")

        # VO2 max
        if self._vo2_est and self._vo2_est.current_estimate:
            v = self._vo2_est.current_estimate
            self.vo2_value.setText(f"{v:.1f}")
            cls = classify_vo2max(v, config.USER["age"])
            self.vo2_class.setText(cls.upper())

        # Plots
        cad = list(self._cadence_buf)
        speed = list(self._speed_buf)
        pw  = list(self._power_buf)
        xs  = self._xs

        self.cad_curve.setData(xs, cad)
        self.speed_curve.setData(xs, speed)
        self.power_curve.setData(xs, pw)

        # Timer
        if self._session_start:
            elapsed = int(time.time() - self._session_start)
            h, r = divmod(elapsed, 3600)
            m, s = divmod(r, 60)
            self.timer_label.setText(f"{h:02d}:{m:02d}:{s:02d}")


class MainWindow(QMainWindow):
    # Signal to push data from async thread to Qt thread
    _data_ready = pyqtSignal(object, object)  # (BikeData, power_w)
    _connected_sig = pyqtSignal(str)
    _disconnected_sig = pyqtSignal()
    _connect_result_sig = pyqtSignal(bool, str)
    _watch_connected_sig = pyqtSignal(str)
    _watch_disconnected_sig = pyqtSignal()
    _watch_hr_sig = pyqtSignal(object)

    def __init__(self, db):
        super().__init__()
        self.db = db

        self.setWindowTitle("BIKE TRACKER")
        self.setMinimumSize(1200, 800)

        self._ble: Optional[FTMSClient] = None
        self._watch: Optional[HeartRateClient] = None
        self._session_db = SessionDB(db)
        self._rolling: Optional[RollingMetrics] = None
        self._vo2_est: Optional[LiveVO2Estimator] = None
        self._recording = False
        self._scanned_devices: list[dict] = []
        self._session_stats: Optional[SessionStats] = None
        self._closing = False

        self._setup_ui()
        self._setup_signals()
        if config.BIKE.get("auto_connect"):
            QTimer.singleShot(0, self._begin_auto_connect)

    def _setup_ui(self):
        from ui.styles import STYLESHEET
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ─────────────────────────────────
        toolbar = QWidget()
        toolbar.setStyleSheet("background: #080A0E; padding: 4px 12px;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 6, 8, 6)

        logo = QLabel("⬡ BIKE TRACKER")
        logo.setStyleSheet("color: #F5A623; font-size: 14px; font-weight: bold; letter-spacing: 3px;")
        tb_layout.addWidget(logo)
        tb_layout.addStretch()

        self.scan_btn = QPushButton("SCAN DEVICES")
        self.scan_btn.setObjectName("connect-btn")
        self.scan_btn.clicked.connect(self._on_scan)
        tb_layout.addWidget(self.scan_btn)

        self.start_btn = QPushButton("START SESSION")
        self.start_btn.clicked.connect(self._on_start)
        self.start_btn.setEnabled(False)
        tb_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setObjectName("stop-btn")
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        tb_layout.addWidget(self.stop_btn)

        self.disconnect_btn = QPushButton("DISCONNECT")
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        self.disconnect_btn.setEnabled(False)
        tb_layout.addWidget(self.disconnect_btn)

        self.watch_btn = QPushButton("CONNECT HR SENSOR")
        self.watch_btn.clicked.connect(self._on_connect_watch)
        tb_layout.addWidget(self.watch_btn)

        root.addWidget(toolbar)

        # ── Tabs ─────────────────────────────────────
        self.tabs = QTabWidget()
        self.live_tab = LiveSessionTab()
        self.history_tab = HistoryTab(self._session_db)
        self.detail_tab = SessionDetailTab(self._session_db)

        self.tabs.addTab(self.live_tab, "LIVE SESSION")
        self.tabs.addTab(self.history_tab, "HISTORY")
        self.tabs.addTab(self.detail_tab, "SESSION DETAIL")

        self.history_tab.session_selected.connect(self._on_session_selected)

        root.addWidget(self.tabs)

        # ── Status bar ────────────────────────────────
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready — click SCAN DEVICES to find your bike")

    def _setup_signals(self):
        self._data_ready.connect(self._on_data_ready)
        self._connected_sig.connect(self._on_ble_connected)
        self._disconnected_sig.connect(self._on_ble_disconnected)
        self._connect_result_sig.connect(self._on_connect_result)
        self._watch_connected_sig.connect(self._on_watch_connected)
        self._watch_disconnected_sig.connect(self._on_watch_disconnected)
        self._watch_hr_sig.connect(self._on_watch_hr)

    # ── BLE scanning ────────────────────────────────────────────────────────

    def _on_scan(self):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("SCANNING...")
        asyncio.ensure_future(self._async_scan())

    def _begin_auto_connect(self):
        if self._recording:
            return
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("CONNECTING...")
        asyncio.ensure_future(self._async_auto_connect())

    async def _async_scan(self):
        try:
            self._ble = FTMSClient(
                on_data=self._async_on_data,
                on_connect=lambda: self._connected_sig.emit(self._ble.device_name or "bike"),
                on_disconnect=lambda: self._disconnected_sig.emit(),
            )
            devices = await self._ble.scan(timeout=config.BLE_SCAN_TIMEOUT)
            if not devices:
                self.status.showMessage(
                    "No FTMS bike found. Ensure the bike is awake, nearby, and advertising Bluetooth."
                )
            else:
                self._scanned_devices = devices
                self._show_device_picker(devices)
        except Exception as e:
            log.error(f"Scan error: {e}")
            self.status.showMessage(f"Scan error: {e}")
        finally:
            self.scan_btn.setEnabled(True)
            self.scan_btn.setText("SCAN DEVICES")

    async def _async_auto_connect(self):
        try:
            self.status.showMessage("Searching for preferred bike...")
            self._ble = FTMSClient(
                on_data=self._async_on_data,
                on_connect=lambda: self._connected_sig.emit(self._ble.device_name or "bike"),
                on_disconnect=lambda: self._disconnected_sig.emit(),
                device_address=config.BIKE.get("address"),
            )
            target = await self._ble.find_preferred_device(
                name=config.BIKE.get("name"),
                address=config.BIKE.get("address"),
                aliases=config.BIKE.get("aliases", []),
                timeout=config.BIKE.get("connect_timeout", config.BLE_SCAN_TIMEOUT),
            )
            if not target:
                self.status.showMessage("Preferred bike not found. Power on the bike, then click SCAN DEVICES.")
                return
            await self._async_connect(target["address"])
        except Exception as e:
            log.exception("Auto-connect failed")
            self.status.showMessage(f"Auto-connect failed: {e}")
        finally:
            if not self._ble or not self._ble.connected:
                self.scan_btn.setEnabled(True)
                self.scan_btn.setText("SCAN DEVICES")

    def _show_device_picker(self, devices: list[dict]):
        dlg = DeviceScanDialog(devices, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected:
            asyncio.ensure_future(self._async_connect(dlg.selected["address"]))

    async def _async_connect(self, address: str):
        self.status.showMessage(f"Connecting to {address}...")
        self.scan_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        try:
            ok = await self._ble.connect(address)
            if not ok:
                self._connect_result_sig.emit(
                    False,
                    "Connection failed or device is not FTMS-compatible. Try scanning again.",
                )
                return

            asyncio.ensure_future(self._ble.start_streaming())
            if config.WATCH.get("auto_connect"):
                asyncio.ensure_future(self._async_connect_watch())
            self._connect_result_sig.emit(True, f"Connected — {self._ble.device_name}")
        except Exception as e:
            log.exception("UI connect flow failed")
            self._connect_result_sig.emit(False, f"Connection error: {e}")
        finally:
            if self._ble and self._ble.connected:
                self.scan_btn.setEnabled(True)
                self.scan_btn.setText("RECONNECT")
            else:
                self.scan_btn.setEnabled(True)
                self.scan_btn.setText("SCAN DEVICES")

    # ── Session control ─────────────────────────────────────────────────────

    def _on_start(self):
        self._rolling = RollingMetrics()
        self._vo2_est = LiveVO2Estimator(self._rolling)
        self._session_stats = SessionStats()
        self._session_db.start_session()
        self._recording = True
        self.live_tab.start_session(self._rolling, self._vo2_est)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.tabs.setCurrentIndex(0)
        self.status.showMessage("Session started — ride hard.")

    def load_demo_session(self, samples: list[tuple[BikeData, Optional[float], Optional[int]]], *, bike_name: str = "Demo Bike"):
        """Populate the live UI with deterministic sample ride data for screenshots."""
        if not samples:
            return

        self.live_tab.set_connected(bike_name)
        self._on_start()

        elapsed_s = samples[-1][0].elapsed_time_s or len(samples)
        self.live_tab._session_start = time.time() - elapsed_s

        for bd, power_w, hr in samples:
            if hr is not None:
                self._on_watch_hr(
                    HeartRateSample(
                        bpm=hr,
                        rr_intervals_ms=[60000.0 / hr],
                    )
                )
            self._on_data_ready(bd, power_w)

        self.live_tab._tick()
        self.status.showMessage("Demo mode — seeded live session for screenshots")

    def _on_disconnect(self):
        self.disconnect_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.status.showMessage("Disconnecting from bike...")
        asyncio.ensure_future(self._async_disconnect_devices())

    def _on_connect_watch(self):
        self.watch_btn.setEnabled(False)
        self.watch_btn.setText("SEARCHING WATCH...")
        self.status.showMessage("Searching for BLE heart-rate sensor...")
        asyncio.ensure_future(self._async_connect_watch())

    async def _async_connect_watch(self):
        if self._watch and self._watch.connected:
            self.watch_btn.setEnabled(True)
            self.watch_btn.setText("WATCH CONNECTED")
            return
        try:
            watch = HeartRateClient(
                on_data=self._async_on_watch_hr,
                on_connect=lambda name: self._watch_connected_sig.emit(name),
                on_disconnect=lambda: self._watch_disconnected_sig.emit(),
            )
            devices = await watch.scan(
                timeout=config.WATCH.get("connect_timeout", 10.0),
                name=config.WATCH.get("name"),
                aliases=config.WATCH.get("aliases", []),
            )
            if not devices:
                self.status.showMessage("No BLE heart-rate sensor found. Wake the strap or enable HR broadcast and try again.")
                self.watch_btn.setEnabled(True)
                self.watch_btn.setText("CONNECT HR SENSOR")
                return
            ok = await watch.connect(devices[0]["address"])
            if not ok:
                self.status.showMessage("Found a sensor but could not connect to its heart-rate service.")
                self.watch_btn.setEnabled(True)
                self.watch_btn.setText("CONNECT HR SENSOR")
                return
            self._watch = watch
            await self._watch.start_streaming()
        except Exception:
            log.exception("Watch connect flow failed")
            self.status.showMessage("HR sensor connect failed. Wake the strap or enable watch HR broadcast and try again.")
            self.watch_btn.setEnabled(True)
            self.watch_btn.setText("CONNECT HR SENSOR")

    async def _async_disconnect_devices(self):
        if self._watch:
            await self._watch.disconnect()
            self._watch = None
        if self._ble:
            await self._ble.disconnect()

    def _on_stop(self):
        if not self._recording:
            return
        self._recording = False
        self.live_tab.stop_session()
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(True)

        # Build summary from rolling metrics
        from time import time
        dur = int(time() - (self.live_tab._session_start or time()))
        summary = {
            "duration_s":  dur,
            "distance_m":  (self._session_db.db.execute(
                "SELECT MAX(distance_m) FROM datapoints WHERE session_id=?",
                (self._session_db.session_id,)
            ).fetchone()[0] or 0),
            "avg_cadence": self._session_stats.avg_cadence() if self._session_stats else 0.0,
            "avg_power":   self._rolling.avg_power(),
            "max_power":   self._rolling.max_power(),
            "avg_hr":      self._session_stats.avg_hr() if self._session_stats else 0.0,
            "max_hr":      self._session_stats.max_hr if self._session_stats else 0,
            "total_kcal":  self._session_stats.total_kcal if self._session_stats else 0.0,
            "np_watts":    self._rolling.np(),
            "tss":         self._rolling.tss(dur),
        }
        vo2 = self._vo2_est.current_estimate or 0.0
        self._session_db.end_session(summary, vo2)
        self._session_stats = None
        self.history_tab.refresh()
        self.status.showMessage(f"Session saved — TSS: {summary['tss']:.0f}, VO2 est: {vo2:.1f}")

    # ── Data flow ────────────────────────────────────────────────────────────

    async def _async_on_data(self, bd: BikeData):
        """Called from bleak notification thread — emit to Qt thread."""
        # Resolve power
        power_w = None
        if config.POWER_MODEL["use_ftms_power"] and bd.power is not None:
            power_w = float(bd.power)
        else:
            power_w = estimate_power(bd.resistance_level, bd.cadence)

        self._data_ready.emit(bd, power_w)

    async def _async_on_watch_hr(self, sample: HeartRateSample):
        self._watch_hr_sig.emit(sample)

    @pyqtSlot(object, object)
    def _on_data_ready(self, bd: BikeData, power_w):
        """Qt main thread — safe to update UI."""
        self.live_tab.update_data(bd, power_w)

        if self._recording:
            if self._session_stats:
                self._session_stats.push(bd, hr=self.live_tab._display.hr)
            if self._rolling and power_w is not None:
                self._rolling.push(power_w)
            if self._vo2_est:
                self._vo2_est.update(power_w, self.live_tab._display.hr)

            self._session_db.record_point(
                cadence=bd.cadence,
                power=int(power_w) if power_w else None,
                speed=bd.speed,
                hr=self.live_tab._display.hr,
                resistance=bd.resistance_level,
                distance_m=bd.total_distance_m,
                elapsed_s=bd.elapsed_time_s,
            )

    @pyqtSlot(str)
    def _on_ble_connected(self, name: str):
        self.live_tab.set_connected(name)
        self.status.showMessage(f"Connected to {name} — click START SESSION when ready")
        self.start_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(True)
        self.scan_btn.setText("RECONNECT")

    @pyqtSlot(bool, str)
    def _on_connect_result(self, ok: bool, message: str):
        self.status.showMessage(message)
        if not ok:
            self.start_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(False)
            self.scan_btn.setText("SCAN DEVICES")

    @pyqtSlot()
    def _on_ble_disconnected(self):
        self.live_tab.set_disconnected()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(False)
        self.scan_btn.setText("SCAN DEVICES")
        if self._recording:
            self._on_stop()
        if self._closing:
            self.close()
            return
        self.status.showMessage("Disconnected — bike should return to its normal display mode")

    @pyqtSlot(str)
    def _on_watch_connected(self, name: str):
        bike_name = self._ble.device_name if self._ble else "bike"
        self.status.showMessage(f"Connected to {bike_name}; HR sensor active from {name}")
        self.watch_btn.setEnabled(True)
        self.watch_btn.setText("HR SENSOR CONNECTED")

    @pyqtSlot()
    def _on_watch_disconnected(self):
        self.live_tab.update_watch_metrics(None)
        self.watch_btn.setEnabled(True)
        self.watch_btn.setText("CONNECT HR SENSOR")

    @pyqtSlot(object)
    def _on_watch_hr(self, sample: HeartRateSample):
        self.live_tab.update_watch_metrics(sample)

    @pyqtSlot(int)
    def _on_session_selected(self, session_id: int):
        self.detail_tab.load_session(session_id)
        self.tabs.setCurrentIndex(2)

    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return

        if self._ble and self._ble.connected:
            event.ignore()
            self._closing = True
            if self._recording:
                self._on_stop()
            self.status.showMessage("Closing — disconnecting from bike cleanly...")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(False)
            self.scan_btn.setEnabled(False)
            asyncio.ensure_future(self._async_disconnect_devices())
            return

        if self._recording:
            self._on_stop()
        event.accept()
