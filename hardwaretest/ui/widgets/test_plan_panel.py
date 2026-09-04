"""GUI for sequential test plans with text and HTML reports."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Optional

from PySide6.QtCore import QProcess, QStandardPaths, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.test_plan import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PASSED,
    TestPlanConfig,
    TestPlanExecutor,
    TestPlanResult,
)
from hardwaretest.tests.nvidia_runner import dcgmi_available
from hardwaretest.ui.i18n import language_manager


class _TestPlanWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, config: TestPlanConfig, parent: QWidget) -> None:
        super().__init__(parent)
        self.executor = TestPlanExecutor(config, self.log.emit, self.progress.emit)

    def run(self) -> None:
        try:
            self.completed.emit(self.executor.execute())
        except Exception as exc:  # pragma: no cover - filesystem/system dependent
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self.executor.cancel()


class TestPlanPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_TestPlanWorker] = None
        self._last_result: Optional[TestPlanResult] = None

        self.hint = QLabel(
            "Der Gesamttestplan führt ausgewählte, nicht-destruktive Prüfungen "
            "nacheinander aus und erstellt ein Text- sowie ein HTML-Protokoll."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #bbbbbb; font-style: italic;")

        self.cpu_check = QCheckBox("CPU-Stresstest (stress-ng)")
        self.cpu_check.setChecked(True)
        self.cpu_duration = self._seconds_spin(60)
        self.ram_check = QCheckBox("RAM-Verifikation (stress-ng)")
        self.ram_check.setChecked(True)
        self.ram_duration = self._seconds_spin(60)
        self.ram_percent = QSpinBox()
        self.ram_percent.setRange(10, 90)
        self.ram_percent.setValue(70)
        self.ram_percent.setSuffix(" %")

        stress_form = QFormLayout()
        stress_form.addRow(self.cpu_check, self.cpu_duration)
        stress_form.addRow(self.ram_check, self.ram_duration)
        stress_form.addRow("RAM-Anteil des verfügbaren Speichers", self.ram_percent)
        stress_group = QGroupBox("CPU und Arbeitsspeicher")
        stress_group.setLayout(stress_form)

        self.nvme_check = QCheckBox("NVMe-Inventar und SMART-Gesundheit")
        self.nvme_check.setChecked(True)
        self.nvidia_check = QCheckBox("NVIDIA-Treiber und GPU-Inventar")
        self.nvidia_check.setChecked(True)
        self.dcgm_check = QCheckBox("NVIDIA-DCGM-Diagnose auf allen GPUs")
        self.dcgm_check.setEnabled(dcgmi_available())
        if not dcgmi_available():
            self.dcgm_check.setToolTip("DCGM ist nicht installiert; der Schritt wird nicht angeboten.")
        self.dcgm_level = QSpinBox()
        self.dcgm_level.setRange(1, 4)
        self.dcgm_level.setValue(1)
        self.dcgm_level.setEnabled(dcgmi_available())

        hardware_form = QFormLayout()
        hardware_form.addRow(self.nvme_check)
        hardware_form.addRow(self.nvidia_check)
        hardware_form.addRow(self.dcgm_check, self.dcgm_level)
        hardware_group = QGroupBox("Datenträger und GPU")
        hardware_group.setLayout(hardware_form)

        self.ping_check = QCheckBox("Ping-Test")
        self.iperf_check = QCheckBox("iperf3-Durchsatztest")
        self.network_target = QLineEdit()
        self.network_target.setPlaceholderText("IP-Adresse oder Hostname des Testziels")
        self.ping_count = QSpinBox()
        self.ping_count.setRange(1, 10_000)
        self.ping_count.setValue(20)
        self.iperf_duration = self._seconds_spin(30)

        network_form = QFormLayout()
        network_form.addRow("Zielhost oder IP-Adresse", self.network_target)
        network_form.addRow(self.ping_check, self.ping_count)
        network_form.addRow(self.iperf_check, self.iperf_duration)
        network_group = QGroupBox("Netzwerk")
        network_group.setLayout(network_form)

        self.stop_on_error = QCheckBox("Bei erstem Fehler abbrechen")
        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        report_root = Path(documents) if documents else Path.home()
        self.output_dir = QLineEdit(str(report_root / "Hardwaretest-Berichte"))
        self.choose_dir_btn = QPushButton("Ordner wählen …")
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir, 1)
        output_row.addWidget(self.choose_dir_btn)
        output_form = QFormLayout()
        output_form.addRow("Protokollordner", output_row)
        output_form.addRow(self.stop_on_error)
        output_group = QGroupBox("Ablauf und Protokoll")
        output_group.setLayout(output_form)

        self.start_btn = QPushButton("Gesamttest starten")
        self.stop_btn = QPushButton("Gesamttest abbrechen")
        self.stop_btn.setEnabled(False)
        self.open_html_btn = QPushButton("HTML-Protokoll öffnen")
        self.open_html_btn.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.open_html_btn)

        self.result = QLabel("Bereit")
        self.result.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.current_step = QLabel("")
        self.current_step.setWordWrap(True)

        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(["Status", "Teiltest", "Dauer", "Zusammenfassung"])
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.setMaximumHeight(210)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family: monospace; font-size: 11px;")

        groups = QHBoxLayout()
        groups.addWidget(stress_group)
        groups.addWidget(hardware_group)
        groups.addWidget(network_group)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addLayout(groups)
        layout.addWidget(output_group)
        layout.addLayout(buttons)
        layout.addWidget(self.result)
        layout.addWidget(self.progress)
        layout.addWidget(self.current_step)
        layout.addWidget(self.results)
        layout.addWidget(self.log_view, 1)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.choose_dir_btn.clicked.connect(self._choose_output_dir)
        self.open_html_btn.clicked.connect(self._open_html)
        self.dcgm_check.toggled.connect(self.nvidia_check.setChecked)
        language_manager.language_changed.connect(self._retranslate_runtime)

    @staticmethod
    def _seconds_spin(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(5, 86_400)
        spin.setValue(value)
        spin.setSuffix(" s")
        return spin

    def _choose_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            language_manager.tr("Protokollordner wählen"),
            self.output_dir.text().strip() or str(Path.home()),
        )
        if selected:
            self.output_dir.setText(selected)

    def _start(self) -> None:
        if self._worker is not None:
            return
        if not self._any_test_selected():
            QMessageBox.warning(
                self,
                language_manager.tr("Kein Teiltest ausgewählt"),
                language_manager.tr("Bitte mindestens einen Teiltest auswählen."),
            )
            return
        if (self.ping_check.isChecked() or self.iperf_check.isChecked()) and not self.network_target.text().strip():
            QMessageBox.warning(
                self,
                language_manager.tr("Zielhost fehlt"),
                language_manager.tr("Bitte einen Zielhost oder eine IP-Adresse angeben."),
            )
            return
        if self.iperf_check.isChecked():
            answer = QMessageBox.question(
                self,
                language_manager.tr("iperf3-Server auf dem Ziel erforderlich"),
                language_manager.tr(
                    "Auf dem Zielsystem muss während des Gesamttests „iperf3 -s“ laufen. "
                    "Der Standardport TCP 5201 muss erreichbar sein. Fortfahren?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        config = self._config()
        self._last_result = None
        self.results.setRowCount(0)
        self.log_view.clear()
        self.open_html_btn.setEnabled(False)
        self.progress.setValue(0)
        self._set_runtime_text(self.result, "⌛ Gesamttest läuft")
        self.result.setStyleSheet("color: #ffaa00; font-weight: bold;")
        worker = _TestPlanWorker(config, self)
        self._worker = worker
        worker.log.connect(self.log_view.append)
        worker.progress.connect(self._show_progress)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        self._set_running(True)
        worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._set_runtime_text(self.current_step, "Testplan wird abgebrochen …")
            self.stop_btn.setEnabled(False)
            self._worker.cancel()

    def _show_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(int(done / max(1, total) * 100))
        if name:
            self._set_runtime_text(
                self.current_step,
                "Teiltest {current} von {total}: {name}",
                current=min(done + 1, total),
                total=total,
                name=name,
            )

    def _completed(self, value: object) -> None:
        if not isinstance(value, TestPlanResult):
            self._failed("Ungültiges Testergebnis")
            return
        self._last_result = value
        self.progress.setValue(100)
        self._populate_results(value)
        self._set_raw_text(
            self.current_step,
            language_manager.tr("Textprotokoll: {path}", path=value.text_report)
            + "\n"
            + language_manager.tr("HTML-Protokoll: {path}", path=value.html_report),
        )
        if value.status == STATUS_PASSED:
            self._set_runtime_text(self.result, "✓ Gesamttest BESTANDEN")
            self.result.setStyleSheet("color: #44ff44; font-weight: bold;")
        elif value.status == STATUS_CANCELLED:
            self._set_runtime_text(self.result, "? Gesamttest ABGEBROCHEN – Protokoll wurde gespeichert")
            self.result.setStyleSheet("color: #ffaa00; font-weight: bold;")
        else:
            self._set_runtime_text(self.result, "✗ Gesamttest FEHLGESCHLAGEN")
            self.result.setStyleSheet("color: #ff4444; font-weight: bold;")
        self.open_html_btn.setEnabled(value.html_report is not None)

    def _failed(self, message: str) -> None:
        self._set_runtime_text(self.result, "✗ Gesamttest konnte nicht abgeschlossen werden")
        self.result.setStyleSheet("color: #ff4444; font-weight: bold;")
        self._set_raw_text(self.current_step, message)

    def _worker_finished(self) -> None:
        self._worker = None
        self._set_running(False)

    def _populate_results(self, result: TestPlanResult) -> None:
        self.results.setRowCount(len(result.steps))
        colors = {
            STATUS_PASSED: QColor("#44ff44"),
            STATUS_FAILED: QColor("#ff4444"),
            STATUS_CANCELLED: QColor("#ffaa00"),
        }
        for row, step in enumerate(result.steps):
            values = [
                self._status_text(step.status),
                step.name,
                f"{step.duration_seconds:.1f} s",
                step.summary,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setForeground(colors.get(step.status, QColor("#cccccc")))
                self.results.setItem(row, column, item)
        self.results.resizeColumnsToContents()
        self.results.horizontalHeader().setStretchLastSection(True)

    def _open_html(self) -> None:
        if not self._last_result or not self._last_result.html_report:
            return
        path = self._last_result.html_report
        if not path.exists():
            QMessageBox.warning(
                self,
                language_manager.tr("HTML-Protokoll nicht gefunden"),
                str(path),
            )
            return
        url = QUrl.fromLocalFile(str(path)).toString()
        # QDesktopServices follows the text/html file association. On some
        # desktop systems that association points to an application which is
        # not a browser. Prefer an installed real browser explicitly.
        for browser in (
            "firefox", "google-chrome", "chromium", "chromium-browser", "brave-browser"
        ):
            executable = shutil.which(browser)
            if executable and _detached_started(QProcess.startDetached(executable, [url])):
                return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            return
        QMessageBox.warning(
            self,
            language_manager.tr("HTML-Protokoll konnte nicht geöffnet werden"),
            language_manager.tr(
                "Bitte die Datei manuell in einem Browser öffnen:\n{path}", path=path
            ),
        )

    def _config(self) -> TestPlanConfig:
        return TestPlanConfig(
            output_dir=Path(self.output_dir.text().strip()).expanduser(),
            language=language_manager.language,
            stop_on_error=self.stop_on_error.isChecked(),
            cpu_enabled=self.cpu_check.isChecked(),
            cpu_seconds=self.cpu_duration.value(),
            ram_enabled=self.ram_check.isChecked(),
            ram_seconds=self.ram_duration.value(),
            ram_percent=self.ram_percent.value(),
            nvme_enabled=self.nvme_check.isChecked(),
            ping_enabled=self.ping_check.isChecked(),
            network_target=self.network_target.text().strip(),
            ping_count=self.ping_count.value(),
            iperf_enabled=self.iperf_check.isChecked(),
            iperf_seconds=self.iperf_duration.value(),
            nvidia_enabled=self.nvidia_check.isChecked(),
            dcgm_enabled=self.dcgm_check.isChecked(),
            dcgm_level=self.dcgm_level.value(),
        )

    def _any_test_selected(self) -> bool:
        return any(checkbox.isChecked() for checkbox in (
            self.cpu_check,
            self.ram_check,
            self.nvme_check,
            self.ping_check,
            self.iperf_check,
            self.nvidia_check,
            self.dcgm_check,
        ))

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.choose_dir_btn.setEnabled(not running)
        for widget in (
            self.cpu_check, self.cpu_duration, self.ram_check, self.ram_duration,
            self.ram_percent, self.nvme_check, self.ping_check, self.ping_count,
            self.iperf_check, self.iperf_duration, self.network_target,
            self.nvidia_check, self.dcgm_check, self.dcgm_level,
            self.stop_on_error, self.output_dir,
        ):
            widget.setEnabled(not running)
        if not running and not dcgmi_available():
            self.dcgm_check.setEnabled(False)
            self.dcgm_level.setEnabled(False)

    def _set_runtime_text(self, widget: QLabel, source: str, **values: object) -> None:
        widget._hardwaretest_runtime_text = (source, values)
        widget.setText(language_manager.tr(source, **values))

    @staticmethod
    def _set_raw_text(widget: QLabel, value: str) -> None:
        widget._hardwaretest_runtime_text = None
        widget.setText(value)

    def _retranslate_runtime(self, _language: str | None = None) -> None:
        for widget in (self.result, self.current_step):
            state = getattr(widget, "_hardwaretest_runtime_text", None)
            if state:
                source, values = state
                widget.setText(language_manager.tr(source, **values))
        if self._last_result is not None:
            self._populate_results(self._last_result)

    @staticmethod
    def _status_text(status: str) -> str:
        sources = {
            STATUS_PASSED: "BESTANDEN",
            STATUS_FAILED: "FEHLGESCHLAGEN",
            STATUS_CANCELLED: "ABGEBROCHEN",
        }
        return language_manager.tr(sources.get(status, "ÜBERSPRUNGEN"))


def _detached_started(result: object) -> bool:
    """Normalize PySide versions returning either bool or (bool, pid)."""
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)
