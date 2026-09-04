"""Echtzeit-Temperatur- und EDAC-Monitor-Widget.

Optimiert fuer Multi-Socket-Systeme mit vielen Cores (z.B. 2×48 Cores).
Zeigt pro CPU/Socket eine kompakte Zusammenfassung (Min/Avg/Max) und
bietet ein aufklappbares Detail-Panel fuer alle Einzel-Cores.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
from hardwaretest.ui.i18n import language_manager


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
    """Bounded, paginated view of individual core temperatures."""

    COLUMNS = 4
    PAGE_SIZE = 24

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._toggle_btn = QToolButton()
        self._toggle_btn.setStyleSheet("QToolButton { border: none; }")
        self._toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle_btn.setText(language_manager.tr("Core-Details anzeigen"))
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.toggled.connect(self._on_toggled)

        self._content = QScrollArea()
        self._content.setWidgetResizable(True)
        self._content.setVisible(False)
        self._content.setMinimumHeight(100)
        self._content.setMaximumHeight(165)
        self._content.setFrameShape(QFrame.Shape.NoFrame)

        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout()
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._grid_layout.setSpacing(2)
        self._grid_widget.setLayout(self._grid_layout)
        self._content.setWidget(self._grid_widget)

        self._core_labels: List[QLabel] = []
        self._temps: List[CpuTemperature] = []
        self._page_index = 0

        self._previous_btn = QPushButton(language_manager.tr("Vorherige"))
        self._next_btn = QPushButton(language_manager.tr("Nächste"))
        self._page_label = QLabel("")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._previous_btn.clicked.connect(lambda: self._change_page(-1))
        self._next_btn.clicked.connect(lambda: self._change_page(1))
        page_layout = QHBoxLayout()
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(self._previous_btn)
        page_layout.addWidget(self._page_label, 1)
        page_layout.addWidget(self._next_btn)
        self._page_widget = QWidget()
        self._page_widget.setLayout(page_layout)
        self._page_widget.setVisible(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle_btn)
        layout.addWidget(self._content)
        layout.addWidget(self._page_widget)
        self.setLayout(layout)

    def _on_toggled(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._toggle_btn.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        count = len(self._temps)
        if checked:
            self._toggle_btn.setText(language_manager.tr(
                "Core-Details ausblenden ({count}, max. {page_size}/Seite)",
                count=count,
                page_size=self.PAGE_SIZE,
            ))
        else:
            self._toggle_btn.setText(language_manager.tr(
                "Core-Details anzeigen ({count}, max. {page_size}/Seite)",
                count=count,
                page_size=self.PAGE_SIZE,
            ))
        self._render_page()

    def update_cores(self, temps: List[CpuTemperature]) -> None:
        """Update data while keeping at most one page of labels visible."""
        self._temps = list(temps)
        last_page = max(0, (len(self._temps) - 1) // self.PAGE_SIZE)
        self._page_index = min(self._page_index, last_page)
        while len(self._core_labels) < min(self.PAGE_SIZE, len(self._temps)):
            label = QLabel()
            position = len(self._core_labels)
            self._grid_layout.addWidget(
                label, position // self.COLUMNS, position % self.COLUMNS
            )
            self._core_labels.append(label)
        self._render_page()

        # Toggle-Button Text aktualisieren
        count = len(self._temps)
        if self._toggle_btn.isChecked():
            self._toggle_btn.setText(language_manager.tr(
                "Core-Details ausblenden ({count}, max. {page_size}/Seite)",
                count=count,
                page_size=self.PAGE_SIZE,
            ))
        else:
            self._toggle_btn.setText(language_manager.tr(
                "Core-Details anzeigen ({count}, max. {page_size}/Seite)",
                count=count,
                page_size=self.PAGE_SIZE,
            ))

    def _change_page(self, offset: int) -> None:
        page_count = max(1, (len(self._temps) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self._page_index = max(0, min(page_count - 1, self._page_index + offset))
        self._render_page()

    def _render_page(self) -> None:
        count = len(self._temps)
        page_count = max(1, (count + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        start = self._page_index * self.PAGE_SIZE
        visible = self._temps[start:start + self.PAGE_SIZE]
        for position, label in enumerate(self._core_labels):
            if position >= len(visible):
                label.setVisible(False)
                continue
            temperature = visible[position]
            name = temperature.label or f"Core {start + position}"
            emoji = _temp_emoji(
                temperature.current, temperature.high, temperature.critical
            )
            label.setText(f"{emoji}{name}: {temperature.current:.0f}°C")
            label.setStyleSheet(
                f"font-size: 11px; "
                f"{_temp_css(temperature.current, temperature.high, temperature.critical)}"
            )
            label.setVisible(True)

        # Den Seitenstatus auch bei kleinen CPUs anzeigen. So ist unmittelbar
        # erkennbar, dass die Ansicht auf großen Systemen begrenzt bleibt.
        self._page_widget.setVisible(bool(count) and self._toggle_btn.isChecked())
        self._previous_btn.setEnabled(self._page_index > 0)
        self._next_btn.setEnabled(self._page_index + 1 < page_count)
        first = start + 1 if count else 0
        last = min(start + len(visible), count)
        self._page_label.setText(language_manager.tr(
            "Seite {page}/{pages} – Cores {first}–{last} von {count}",
            page=self._page_index + 1,
            pages=page_count,
            first=first,
            last=last,
            count=count,
        ))


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

    # Polling-Intervalle: schnell waehrend eines Tests, langsamer im Leerlauf.
    _FAST_INTERVAL = 2000
    _IDLE_INTERVAL = 5000

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(self._IDLE_INTERVAL)
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
        """Aktiviert das schnelle Temperatur-/EDAC-Polling waehrend eines Tests."""
        self._timer.setInterval(self._FAST_INTERVAL)
        self._timer.start()
        self._refresh()

    def stop_monitoring(self) -> None:
        """Beendet das schnelle Polling nach einem Test.

        Das Polling wird nicht komplett gestoppt, sondern nur auf das
        langsamere Leerlauf-Intervall zurueckgeschaltet. So friert die
        Anzeige nach Testende nicht auf dem Hoechstwert ein, sondern zeigt
        weiterhin die aktuellen (abkuehlenden) Werte an. Ist das Widget
        nicht sichtbar, wird das Polling pausiert (siehe hideEvent).
        """
        self._timer.setInterval(self._IDLE_INTERVAL)
        if self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self._refresh()

    # ----- Qt events -----

    def showEvent(self, event) -> None:
        """Startet das Leerlauf-Polling, sobald das Widget sichtbar wird."""
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()
        self._refresh()

    def hideEvent(self, event) -> None:
        """Pausiert das Polling, wenn das Widget nicht sichtbar ist."""
        super().hideEvent(event)
        self._timer.stop()

    # ----- internal -----

    def _refresh(self) -> None:
        self._update_temperature()
        self._update_edac()

    def _update_temperature(self) -> None:
        raw_temps = read_cpu_temperatures()
        if not raw_temps:
            self._temp_summary_label.setText(language_manager.tr("Keine Sensoren gefunden"))
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

        self._temp_summary_label.setText(language_manager.tr("\n".join(lines)))
        if any_critical:
            self._temp_summary_label.setStyleSheet(_CSS_RED)
        elif max_temp > _WARN_TEMP:
            self._temp_summary_label.setStyleSheet(_CSS_YELLOW)
        else:
            self._temp_summary_label.setStyleSheet(_CSS_GREEN)

    @staticmethod
    def _is_cpu_socket_summary(s: CpuChipSummary) -> bool:
        """Prueft ob eine Summary ein CPU-Socket ist (vs. anderer Sensor)."""
        name = s.chip_name.lower()
        return (
            name.startswith("cpu ")
            or name.startswith("cores")
            or "socket" in name
        )

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

        # CPU-Socket Summaries und sonstige Sensoren trennen
        cpu_summaries = [s for s in summaries if self._is_cpu_socket_summary(s)]
        other_summaries = [s for s in summaries if not self._is_cpu_socket_summary(s)]

        for s in cpu_summaries:
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

        # Sonstige Sensoren (z.B. acpitz) kompakt anhaengen
        for s in other_summaries:
            global_max = max(global_max, s.temp_max)
            emoji = _temp_emoji(s.temp_max, s.high, s.critical)
            if s.critical > 0 and s.temp_max >= s.critical:
                any_critical = True
            line = f"{emoji} <b>{s.chip_name}</b>: {s.temp_max:.0f}°C"
            if s.high > 0:
                line += f"  [Limit: {s.high:.0f}°C]"
            lines.append(line)

        # Gesamtuebersicht
        header = (
            f"<b>Gesamt: {total_cores} Cores  –  "
            f"Max {global_max:.0f}°C</b>"
        )
        lines.insert(0, header)

        self._temp_summary_label.setText(language_manager.tr("<br>".join(lines)))

        if any_critical:
            self._temp_summary_label.setStyleSheet(_CSS_RED)
        elif global_max > _WARN_TEMP:
            self._temp_summary_label.setStyleSheet(_CSS_YELLOW)
        else:
            self._temp_summary_label.setStyleSheet(_CSS_GREEN)

        # Core-Detail aktualisieren (nur CPU-Cores, keine Package/Sonstige)
        cpu_core_temps = [
            t for t in all_temps
            if not (t.label or "").lower().startswith("package id")
            and t.chip_name not in ("acpitz",)
        ]
        self._core_detail.update_cores(cpu_core_temps)

    def _update_edac(self) -> None:
        edac = read_edac_info()
        text = format_edac_info(edac)
        self._edac_label.setText(language_manager.tr(text))

        if not edac.available:
            self._edac_label.setStyleSheet(_CSS_GREY)
        elif edac.uncorrectable_errors > 0:
            self._edac_label.setStyleSheet(_CSS_RED)
        elif edac.correctable_errors > 0:
            self._edac_label.setStyleSheet(_CSS_YELLOW)
        else:
            self._edac_label.setStyleSheet(_CSS_GREEN)
