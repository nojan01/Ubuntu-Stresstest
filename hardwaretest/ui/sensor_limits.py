"""Shared, persistent temperature overrides for standalone and plan monitoring."""

import json

from PySide6.QtCore import QSettings, QThread
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QLabel,
    QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from hardwaretest.core.monitoring import collect_metrics
from hardwaretest.core.monitor_history import is_temperature, validated_sensor_limits
from hardwaretest.ui.i18n import language_manager


def profile_settings():
    return QSettings("Hardwaretest", "Hardwaretest")


def load_sensor_limits() -> dict[str, float]:
    settings = profile_settings()
    raw = settings.value("monitoring/temperature_limits", "{}")
    if settings.status() != QSettings.Status.NoError:
        raise RuntimeError("Sensorprofil nicht lesbar / Cannot read sensor profile")
    try:
        values = json.loads(raw)
        if not isinstance(values, dict):
            raise ValueError("profile must be a dictionary")
        return validated_sensor_limits(values)
    except (ValueError, TypeError) as exc:
        raise ValueError("Sensorprofil ungültig / Invalid sensor profile") from exc


def save_sensor_limits(values: dict[str, float]) -> None:
    values = validated_sensor_limits(values)
    settings = profile_settings()
    settings.setValue("monitoring/temperature_limits", json.dumps(values, sort_keys=True))
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        raise OSError("Sensorprofil konnte nicht gespeichert werden / Cannot save sensor profile")


class _SensorScan(QThread):
    def __init__(self, parent):
        super().__init__(parent)
        self.metrics = {}
        self.error = ""

    def run(self):
        try:
            self.metrics, _missing = collect_metrics()
        except Exception as exc:
            self.error = str(exc)


class SensorLimitsDialog(QDialog):
    def __init__(self, default_limit: float, values: dict[str, float], parent=None, scan=True):
        super().__init__(parent)
        self.default_limit = default_limit
        self.values = validated_sensor_limits(values)
        self._scan = None
        self._rows = {}
        self.setWindowTitle(self.text("Individuelle Temperaturgrenzen", "Individual temperature limits"))
        self.resize(850, 460)
        layout = QVBoxLayout(self)
        hint = QLabel(self.text(
            "Nur aktivierte Zeilen überschreiben die allgemeine Temperaturgrenze. Sensorgrenzen gelten für Langzeit- und Begleitmonitoring und werden gespeichert. Werte an die Herstellerangaben anpassen. Nicht verfügbare Sensoren werden nicht überwacht.",
            "Only checked rows override the general temperature limit. Limits are saved and shared by standalone and accompanying monitoring. Follow hardware specifications. Unavailable sensors are not monitored."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            self.text("Sensor", "Sensor"), self.text("Aktuell", "Current"),
            self.text("Individuell", "Override"), self.text("Grenze", "Limit")])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 480)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(self.text("Speichern", "Save"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.text("Abbrechen", "Cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.populate({})
        if scan:
            self.buttons.setEnabled(False)
            self.table.setEnabled(False)
            self.status.setText(self.text("Sensoren werden gelesen …", "Reading sensors …"))
            self.scan()

    @staticmethod
    def text(de, en):
        return en if language_manager.language == "en" else de

    def scan(self):
        self._scan = _SensorScan(self)
        self._scan.finished.connect(self.scanned)
        self._scan.start()

    def scanned(self):
        scan = self._scan
        self._scan = None
        self.populate(scan.metrics)
        self.status.setText(scan.error or self.text("Sensoren gelesen; fehlende Profileinträge bleiben erhalten.",
                                                   "Sensors read; unavailable profile entries are retained."))
        self.table.setEnabled(True)
        self.buttons.setEnabled(True)
        scan.deleteLater()

    def populate(self, metrics):
        keys = sorted(set(self.values) | {key for key in metrics if is_temperature(key)})
        for row in range(self.table.rowCount()):
            for column in (2, 3):
                widget = self.table.cellWidget(row, column)
                if widget is not None:
                    widget.hide()
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setRowCount(len(keys))
        self._rows = {}
        for row, key in enumerate(keys):
            item = QTableWidgetItem(key)
            item.setToolTip(key)
            self.table.setItem(row, 0, item)
            value = metrics.get(key)
            self.table.setItem(row, 1, QTableWidgetItem(f"{value:g} °C" if value is not None
                                                      else self.text("Nicht verfügbar", "Unavailable")))
            enabled = QCheckBox()
            enabled.setChecked(key in self.values)
            spin = QDoubleSpinBox()
            spin.setRange(30, 120)
            spin.setDecimals(1)
            spin.setSuffix(" °C")
            spin.setValue(self.values.get(key, self.default_limit))
            spin.setEnabled(enabled.isChecked())
            enabled.toggled.connect(spin.setEnabled)
            self.table.setCellWidget(row, 2, enabled)
            self.table.setCellWidget(row, 3, spin)
            self._rows[key] = (enabled, spin)

    def selected_limits(self):
        return {key: spin.value() for key, (enabled, spin) in self._rows.items() if enabled.isChecked()}

    def accept(self):
        if self._scan is not None:
            return
        try:
            save_sensor_limits(self.selected_limits())
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        super().accept()

    def reject(self):
        if self._scan is None:
            super().reject()

    def closeEvent(self, event):
        if self._scan is not None:
            event.ignore()
        else:
            super().closeEvent(event)


def edit_sensor_limits(parent, default_limit):
    try:
        values = load_sensor_limits()
    except (ValueError, RuntimeError) as exc:
        QMessageBox.warning(parent, "Hardwaretest", str(exc))
        values = {}
    dialog = SensorLimitsDialog(default_limit, values, parent)
    dialog.exec()
