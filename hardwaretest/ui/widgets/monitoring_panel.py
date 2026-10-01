"""Background telemetry with bounded UI output and incremental disk recording."""

from pathlib import Path
import threading
import time

from PySide6.QtCore import QObject, QThread, Signal, QByteArray, QSignalBlocker
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from hardwaretest.core.monitoring import MonitorSession
from hardwaretest.core.monitor_history import is_temperature, render_chart
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.report_browser import open_html_report
from hardwaretest.ui.sensor_limits import edit_sensor_limits, load_sensor_limits


class MonitorWorker(QThread):
    snapshot = Signal(object)
    reports = Signal(object)
    error = Signal(str)

    def __init__(self, directory, interval, duration, limit, language, parent=None):
        super().__init__(parent)
        self.directory, self.interval, self.duration = directory, interval, duration
        self.limit, self.language = limit, language
        self.stop_event = threading.Event()
        self.sensor_limits = {}

    def run(self):
        session = None
        try:
            session = MonitorSession(self.directory, self.limit, self.language)
            session.set_sensor_limits(self.sensor_limits)
            started = time.monotonic()
            while not self.stop_event.is_set():
                snapshot = session.sample()
                snapshot.update(session.chart_snapshot())
                self.snapshot.emit(snapshot)
                elapsed = time.monotonic() - started
                if self.duration and elapsed >= self.duration:
                    break
                wait = min(self.interval, max(0, self.duration - elapsed)) if self.duration else self.interval
                if self.stop_event.wait(wait):
                    break
        except Exception as exc:
            if session:
                session.record_error(str(exc))
            self.error.emit(str(exc))
        finally:
            if session:
                try:
                    self.reports.emit((session.close(), session.summary()))
                except Exception as exc:
                    self.error.emit(str(exc))


class MonitoringPanel(QWidget):
    safety_stop = Signal()
    guard_released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._html = None
        self._tripped = False
        self._failed = False
        self._warning = False
        self._labels = []
        self._history = {}
        self._chart_limits = {}
        self._last_snapshot = None
        self._state = "ready"
        layout = QVBoxLayout(self)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.bind(self.hint,
                  "Langzeitmonitoring für PCs und Server: Temperaturen, Lüfter, RAM-Auslastung, ECC- und Dateisystemfehler sowie NVIDIA-Werte, soweit verfügbar. "
                  "Fehlende Sensoren sind keine Hardwarefehler, bedeuten aber eine Lücke in der Überwachung. "
                  "Grenze an die Hardware anpassen. Kein Ersatz für den thermischen Schutz der Hardware.",
                  "Long-term monitoring for PCs and servers: temperatures, fans, RAM usage, ECC/filesystem errors and NVIDIA metrics where available. "
                  "Missing sensors are not hardware faults, but leave a gap in monitoring. "
                  "Adjust the limit for your hardware. This does not replace hardware thermal protection.")
        layout.addWidget(self.hint)
        form = QFormLayout()
        self.interval = QSpinBox()
        self.interval.setRange(5, 3600)
        self.interval.setValue(10)
        self.interval.setSuffix(" s")
        self.duration = QSpinBox()
        self.duration.setRange(0, 720)
        self.duration.setValue(0)
        self.duration.setSuffix(" h")
        self.limit = QSpinBox()
        self.limit.setRange(30, 120)
        self.limit.setValue(90)
        self.limit.setSuffix(" °C")
        self.directory = QLineEdit(str(Path.home() / "Hardwaretest-Berichte"))
        for widget, de, en in (
            (self.interval, "Messintervall", "Sample interval"),
            (self.duration, "Dauer (0 = bis zum Stoppen)", "Duration (0 = until stopped)"),
            (self.limit, "Temperaturgrenze (Standard)", "Temperature limit (default)"),
        ):
            label = QLabel()
            self.bind(label, de, en)
            form.addRow(label, widget)
        self.choose = QPushButton()
        self.bind(self.choose, "Protokollordner …", "Report folder …")
        form.addRow(self.choose, self.directory)
        self.sensor_limits_btn = QPushButton()
        self.bind(self.sensor_limits_btn, "Individuelle Temperaturgrenzen …", "Individual temperature limits …")
        form.addRow(self.sensor_limits_btn)
        layout.addLayout(form)
        self.auto_stop = QCheckBox()
        self.auto_stop.setChecked(True)
        self.bind(self.auto_stop, "Bei Temperaturgrenze/neuen unkorrigierbaren ECC-Fehlern laufende App-Stresstests stoppen",
                  "Stop this app's stress tests at the temperature limit / on new uncorrectable ECC errors")
        layout.addWidget(self.auto_stop)
        buttons = QHBoxLayout()
        self.start_btn = QPushButton()
        self.stop_btn = QPushButton()
        self.open_btn = QPushButton()
        for button, de, en in (
            (self.start_btn, "Monitoring starten", "Start monitoring"),
            (self.stop_btn, "Stoppen und Protokoll abschließen", "Stop and finish report"),
            (self.open_btn, "HTML-Protokoll öffnen", "Open HTML report"),
        ):
            self.bind(button, de, en)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.chart_box = QComboBox()
        self.chart_box.setMinimumContentsLength(25)
        self.chart_box.setToolTip(self.text("Diagramme für maximal 256 Messreihen; alle Messwerte in CSV.",
                                           "Charts for up to 256 series; all measurements in CSV."))
        self.chart_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.chart_box)
        self.chart = QSvgWidget()
        self.chart.setMinimumHeight(120)
        self.chart.setMaximumHeight(185)
        layout.addWidget(self.chart)
        self.metrics = QPlainTextEdit()
        self.metrics.setReadOnly(True)
        self.metrics.setMaximumBlockCount(1500)
        layout.addWidget(self.metrics, 2)
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setMaximumBlockCount(200)
        layout.addWidget(self.events, 1)
        self.stop_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.open_btn.clicked.connect(self.open_report)
        self.choose.clicked.connect(self.choose_directory)
        self.sensor_limits_btn.clicked.connect(lambda: edit_sensor_limits(self, self.limit.value()))
        self.chart_box.currentTextChanged.connect(self.update_chart)
        language_manager.language_changed.connect(self.retranslate)
        for obj in [self, *self.findChildren(QObject)]:
            obj._hardwaretest_skip_tree = True
        self.retranslate()

    def text(self, de, en):
        return en if language_manager.language == "en" else de

    def bind(self, widget, de, en):
        self._labels.append((widget, de, en))

    def retranslate(self, *_):
        for widget, de, en in self._labels:
            widget.setText(self.text(de, en))
        self.chart_box.setToolTip(self.text("Diagramme für maximal 256 Messreihen; alle Messwerte in CSV.",
                                           "Charts for up to 256 series; all measurements in CSV."))
        self.update_chart()
        if self._state == "running" and self._last_snapshot:
            self.render_status(self._last_snapshot)
        elif self._state == "finishing":
            self.status.setText(self.text("Monitoring wird abgeschlossen …", "Finishing monitoring …"))
        elif self._state == "ended":
            self.render_end_status()

    def update_chart(self, *_):
        key = self.chart_box.currentText()
        svg = render_chart(key, self._history.get(key, ()), self._chart_limits.get(key), language_manager.language)
        self.chart.load(QByteArray(svg.encode("utf-8")))

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self, self.choose.text(), self.directory.text())
        if directory:
            self.directory.setText(directory)

    def is_busy(self):
        return self._worker is not None

    def start(self):
        if self.is_busy():
            return
        try:
            sensor_limits = load_sensor_limits()
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Hardwaretest", str(exc))
            return
        self._failed = self._tripped = False
        self._warning = False
        self._html = None
        self.open_btn.setEnabled(False)
        self.events.clear()
        self.metrics.clear()
        self._history = {}
        self._chart_limits = {}
        self._last_snapshot = None
        self._state = "running"
        self.chart_box.clear()
        self.update_chart()
        self.status.setText(self.text("Monitoring läuft …", "Monitoring running …"))
        worker = MonitorWorker(Path(self.directory.text()).expanduser(), self.interval.value(),
                               self.duration.value() * 3600, self.limit.value(), language_manager.language, self)
        self._worker = worker
        worker.sensor_limits = sensor_limits
        worker.snapshot.connect(self.show_snapshot)
        worker.reports.connect(self.show_reports)
        worker.error.connect(self.show_error)
        worker.finished.connect(self.finished)
        for widget in (self.start_btn, self.interval, self.duration, self.limit, self.directory, self.choose, self.auto_stop, self.sensor_limits_btn):
            widget.setEnabled(False)
        self.stop_btn.setEnabled(True)
        worker.start()

    def stop(self):
        if self._worker:
            self._worker.stop_event.set()
            self.stop_btn.setEnabled(False)
            self._state = "finishing"
            self.status.setText(self.text("Monitoring wird abgeschlossen …", "Finishing monitoring …"))

    def show_snapshot(self, snapshot):
        self._last_snapshot = snapshot
        if self._state != "finishing":
            self._state = "running"
        self._history = snapshot.get("history", {})
        self._chart_limits = snapshot.get("limits", {})
        with QSignalBlocker(self.chart_box):
            selected = self.chart_box.currentText()
            known = {self.chart_box.itemText(index) for index in range(self.chart_box.count())}
            for key in sorted(set(self._history) - known, key=lambda name: (not is_temperature(name), name)):
                self.chart_box.addItem(key)
            if selected:
                self.chart_box.setCurrentText(selected)
        self.update_chart()
        self._warning = bool(snapshot["alert_count"])
        values = "\n".join(f"{key}: {value:g}" for key, value in sorted(snapshot["metrics"].items()))
        missing = ", ".join(snapshot.get("unavailable", []))
        if missing:
            values += "\n\n" + self.text("Nicht verfügbar / zeitweise ausgefallen: ", "Unavailable / intermittent: ") + missing
        self.metrics.setPlainText(values)
        if self._state == "running":
            self.render_status(snapshot)
        for alert in snapshot["alerts"]:
            self.events.appendPlainText(snapshot["timestamp"] + ": " + alert)
        if snapshot["critical"] and not self._tripped and self.auto_stop.isChecked():
            self._tripped = True
            self.safety_stop.emit()

    def render_status(self, snapshot):
        self.status.setText(self.text("Monitoring läuft", "Monitoring running") + f" | {snapshot['timestamp']} | "
                            + self.text("Messungen", "Samples") + f": {snapshot['samples']} | "
                            + self.text("Ereignisse", "Events") + f": {snapshot['alert_count']}\nCSV: {snapshot['csv']}")
        if snapshot["critical"]:
            self.status.setText(self.status.text() + self.text("\nWARNUNG: Sicherheitsgrenze ausgelöst.", "\nWARNING: safety limit triggered."))

    def show_error(self, message):
        self._failed = True
        self.events.appendPlainText(self.text("Monitoring-Fehler: ", "Monitoring error: ") + message)
        if self.auto_stop.isChecked() and not self._tripped:
            self._tripped = True
            self.safety_stop.emit()

    def show_reports(self, result):
        paths, summary = result
        self._html = paths[1]
        self.metrics.setPlainText(summary)
        self.open_btn.setEnabled(True)

    def finished(self):
        self._worker.deleteLater()
        self._worker = None
        self.guard_released.emit()
        self.stop_btn.setEnabled(False)
        for widget in (self.start_btn, self.interval, self.duration, self.limit, self.directory, self.choose, self.auto_stop, self.sensor_limits_btn):
            widget.setEnabled(True)
        self._state = "ended"
        self.render_end_status()

    def render_end_status(self):
        self.status.setText(self.text("Monitoring beendet", "Monitoring ended")
                            + (self.text(" – mit Fehler/Warnung; Protokoll prüfen.", " – error/warning; check report.")
                               if self._failed or self._tripped or self._warning else "")
                            + (f"\n{self._html}" if self._html else ""))

    def open_report(self):
        if self._html:
            open_html_report(self._html, self)
