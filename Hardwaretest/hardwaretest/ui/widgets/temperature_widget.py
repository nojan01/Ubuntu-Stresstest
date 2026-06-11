"""Echtzeit-Temperatur- und EDAC-Monitor-Widget.

Optimiert fuer Multi-Socket-Systeme mit vielen Cores (z.B. 2×48 Cores).
Zeigt pro CPU/Socket eine kompakte Zusammenfassung (Min/Avg/Max) und
bietet ein aufklappbares Detail-Panel fuer alle Einzel-Cores.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.system_info import (
    CpuChipSummary,
    CpuTemperature,
    format_edac_info,
    read_cpu_temperatures,
    read_edac_info,
    summarize_cpu_temperatures,
)


# ---------------------------------------------------------------------------
# Schwellenwerte
# ---------------------------------------------------------------------------
_WARN_TEMP = 85.0   # Ab hier gelb
_CRIT_TEMP = 95.0   # Fallback, falls kein Sensor-kritisch-Wert

_CSS_GREEN = "color: #44ff44;"
_CSS_YELLOW = "color: #ffaa00;"
_CSS_RED = "color: #ff4444; font-weight: bold;"
_CSS_GREY = "color: #888888;"


def _temp_css(temp: float, high: float = 0.0, critical: float = 0.0) -> str:
    """Gibt das passende Stylesheet fuer eine Temperatur zurueck."""
    crit = critical if critical > 0 else _CRIT_TEMP
    warn = high if high > 0 else _WARN_TEMP
    if temp >= crit:
        return _CSS_RED
    if temp >= warn:
        return _CSS_YELLOW
    return _CSS_GREEN


def _temp_emoji(temp: float, high: float = 0.0, critical: float = 0.0) -> str:
    crit = critical if critical > 0 else _CRIT_TEMP
    warn = high if high > 0 else _WARN_TEMP
    if temp >= crit:
        return "🔴"
    if temp >= warn:
        return "🟡"
    return "🟢"


# ---------------------------------------------------------------------------
# Aufklappbares Core-Detail-Panel
# ---------------------------------------------------------------------------
class _CollapsibleCoreDetail(QWidget):
    """Aufklappbarer Bereich, der alle Einzel-Core-Temperaturen zeigt."""

    # Spalten im Grid (beschraenkt die Breite bei vielen Cores)
    COLUMNS = 6

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._toggle_btn = QToolButton()
        self._toggle_btn.setStyleSheet("QToolButton { border: none; }")
        self._toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle_btn.setText("Alle Cores anzeigen")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.toggled.connect(self._on_toggled)

        self._content = QScrollArea()
        self._content.setWidgetResizable(True)
        self._content.setVisible(False)
        self._content.setMaximumHeight(200)
        self._content.setFrameShape(QFrame.Shape.NoFrame)

        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout()
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._grid_layout.setSpacing(2)
        self._grid_widget.setLayout(self._grid_layout)
        self._content.setWidget(self._grid_widget)

        self._core_labels: Dict[str, QLabel] = {}

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle_btn)
        layout.addWidget(self._content)
        self.setLayout(layout)

    def _on_toggled(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._toggle_btn.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        count = len(self._core_labels)
        if checked:
            self._toggle_btn.setText(f"Alle Cores ausblenden ({count})")
        else:
            self._toggle_btn.setText(f"Alle Cores anzeigen ({count})")

    def update_cores(self, temps: List[CpuTemperature]) -> None:
        """Aktualisiert die Core-Detail-Anzeige."""
        # Labels erstellen/aktualisieren
        for idx, t in enumerate(temps):
            key = t.label or f"#{idx}"
            if key not in self._core_labels:
                lbl = QLabel()
                lbl.setStyleSheet("font-size: 11px;")
                row = len(self._core_labels) // self.COLUMNS
                col = len(self._core_labels) % self.COLUMNS
                self._grid_layout.addWidget(lbl, row, col)
                self._core_labels[key] = lbl
            lbl = self._core_labels[key]
            emoji = _temp_emoji(t.current, t.high, t.critical)
            lbl.setText(f"{emoji}{key}: {t.current:.0f}°C")
            lbl.setStyleSheet(
                f"font-size: 11px; {_temp_css(t.current, t.high, t.critical)}"
            )

        # Toggle-Button Text aktualisieren
        count = len(self._core_labels)
        if self._toggle_btn.isChecked():
            self._toggle_btn.setText(f"Alle Cores ausblenden ({count})")
        else:
            self._toggle_btn.setText(f"Alle Cores anzeigen ({count})")


# ---------------------------------------------------------------------------
# Haupt-Widget
# ---------------------------------------------------------------------------
class TemperatureWidget(QWidget):
    """Kompaktes Widget das CPU-Temperaturen und EDAC-Status live anzeigt.

    Bei Systemen mit vielen Cores (z.B. Dual-Socket mit 90+ Cores)
    wird pro CPU-Chip/Socket nur eine Zusammenfassungszeile angezeigt
    (Min/Avg/Max). Ein aufklappbarer Bereich zeigt bei Bedarf alle
    Einzel-Core-Temperaturen in einem platzsparenden Grid.
    """

    # Ab dieser Anzahl wird automatisch die kompakte Ansicht verwendet
    COMPACT_THRESHOLD = 8

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._refresh)

        # --- Temperatur-Anzeige ---
        temp_group = QGroupBox("CPU-Temperatur")
        self._temp_summary_label = QLabel("Wird ermittelt…")
        self._temp_summary_label.setWordWrap(True)

        self._core_detail = _CollapsibleCoreDetail()
        self._core_detail.setVisible(False)  # Nur bei vielen Cores einblenden

        temp_layout = QVBoxLayout()
        temp_layout.addWidget(self._temp_summary_label)
        temp_layout.addWidget(self._core_detail)
        temp_group.setLayout(temp_layout)

        # --- EDAC-Anzeige ---
        edac_group = QGroupBox("ECC / EDAC")
        self._edac_label = QLabel("Wird ermittelt…")
        self._edac_label.setWordWrap(True)
        edac_layout = QVBoxLayout()
        edac_layout.addWidget(self._edac_label)
        edac_group.setLayout(edac_layout)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(temp_group)
        layout.addWidget(edac_group)
        self.setLayout(layout)

        self._refresh()

    # ----- public API -----

    def start_monitoring(self) -> None:
        """Startet das periodische Temperatur-/EDAC-Polling."""
        self._timer.start()

    def stop_monitoring(self) -> None:
        """Stoppt das periodische Polling."""
        self._timer.stop()

    # ----- internal -----

    def _refresh(self) -> None:
        self._update_temperature()
        self._update_edac()

    def _update_temperature(self) -> None:
        raw_temps = read_cpu_temperatures()
        if not raw_temps:
            self._temp_summary_label.setText("Keine Sensoren gefunden")
            self._temp_summary_label.setStyleSheet(_CSS_GREY)
            self._core_detail.setVisible(False)
            return

        summaries = summarize_cpu_temperatures(raw_temps)
        use_compact = len(raw_temps) > self.COMPACT_THRESHOLD

        if use_compact:
            self._render_compact(summaries, raw_temps)
        else:
            self._render_simple(raw_temps)

    def _render_simple(self, temps: List[CpuTemperature]) -> None:
        """Einfache Darstellung fuer Systeme mit wenigen Cores."""
        self._core_detail.setVisible(False)
        lines = []
        max_temp = 0.0
        any_critical = False
        for t in temps:
            max_temp = max(max_temp, t.current)
            emoji = _temp_emoji(t.current, t.high, t.critical)
            if t.critical > 0 and t.current >= t.critical:
                any_critical = True
            label = t.label or "CPU"
            parts = [f"{emoji} {label}: {t.current:.0f}°C"]
            if t.high > 0:
                parts.append(f"max {t.high:.0f}°C")
            if t.critical > 0:
                parts.append(f"krit {t.critical:.0f}°C")
            lines.append(" / ".join(parts))

        self._temp_summary_label.setText("\n".join(lines))
        if any_critical:
            self._temp_summary_label.setStyleSheet(_CSS_RED)
        elif max_temp > _WARN_TEMP:
            self._temp_summary_label.setStyleSheet(_CSS_YELLOW)
        else:
            self._temp_summary_label.setStyleSheet(_CSS_GREEN)

    def _render_compact(
        self,
        summaries: List[CpuChipSummary],
        all_temps: List[CpuTemperature],
    ) -> None:
        """Kompakte Darstellung fuer Multi-Socket / viele Cores."""
        self._core_detail.setVisible(True)

        lines = []
        global_max = 0.0
        any_critical = False
        total_cores = 0

        for s in summaries:
            total_cores += s.core_count
            global_max = max(global_max, s.temp_max)
            emoji = _temp_emoji(s.temp_max, s.high, s.critical)
            if s.critical > 0 and s.temp_max >= s.critical:
                any_critical = True

            line = (
                f"{emoji} <b>{s.chip_name}</b> "
                f"({s.core_count} Cores): "
                f"Ø {s.temp_avg:.0f}°C  "
                f"Min {s.temp_min:.0f}°C  "
                f"<b>Max {s.temp_max:.0f}°C</b>"
            )
            if s.temp_max > _WARN_TEMP:
                line += f"  (Hotspot: {s.hottest_core_label})"
            if s.high > 0:
                line += f"  [Limit: {s.high:.0f}°C]"
            lines.append(line)

        # Gesamtuebersicht
        header = (
            f"<b>Gesamt: {total_cores} Cores  –  "
            f"Max {global_max:.0f}°C</b>"
        )
        lines.insert(0, header)

        self._temp_summary_label.setText("<br>".join(lines))

        if any_critical:
            self._temp_summary_label.setStyleSheet(_CSS_RED)
        elif global_max > _WARN_TEMP:
            self._temp_summary_label.setStyleSheet(_CSS_YELLOW)
        else:
            self._temp_summary_label.setStyleSheet(_CSS_GREEN)

        # Core-Detail aktualisieren
        self._core_detail.update_cores(all_temps)

    def _update_edac(self) -> None:
        edac = read_edac_info()
        text = format_edac_info(edac)
        self._edac_label.setText(text)

        if not edac.available:
            self._edac_label.setStyleSheet(_CSS_GREY)
        elif edac.uncorrectable_errors > 0:
            self._edac_label.setStyleSheet(_CSS_RED)
        elif edac.correctable_errors > 0:
            self._edac_label.setStyleSheet(_CSS_YELLOW)
        else:
            self._edac_label.setStyleSheet(_CSS_GREEN)
