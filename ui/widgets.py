"""
Custom PyQt6 widgets for the cycling dashboard.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt


class MetricCard(QWidget):
    """
    Big number metric card:
      ┌─────────────────┐
      │  CADENCE        │
      │  87        RPM  │
      └─────────────────┘
    """

    def __init__(self, label: str, unit: str = "", size: str = "large", parent=None):
        super().__init__(parent)
        self.setObjectName("metric-card")
        self._size = size
        self._default_value_style = ""
        self._last_text = "---"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        # Label row
        self._label = QLabel(label.upper())
        self._label.setObjectName("metric-label")
        layout.addWidget(self._label)

        # Value + unit row
        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        value_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        obj = "metric-value" if size == "large" else "metric-value-sm"
        self._value = QLabel("---")
        self._value.setObjectName(obj)
        value_row.addWidget(self._value)

        if unit:
            self._unit = QLabel(unit)
            self._unit.setObjectName("metric-unit")
            self._unit.setAlignment(Qt.AlignmentFlag.AlignBottom)
            value_row.addWidget(self._unit)

        value_row.addStretch()
        layout.addLayout(value_row)

    def set_value(self, val, fmt: str = "{}", color: str = None):
        text = "---" if val is None else fmt.format(val)
        if text != self._last_text:
            self._value.setText(text)
            self._last_text = text
        if color:
            self.set_color(color)

    def set_color(self, hex_color: str | None):
        style = f"color: {hex_color};" if hex_color else self._default_value_style
        if self._value.styleSheet() != style:
            self._value.setStyleSheet(style)


class SectionHeader(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("section-header")


class HRule(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
