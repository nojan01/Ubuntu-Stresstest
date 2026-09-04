"""Safe NVMe health and device self-test user interface."""

from __future__ import annotations

import os
import shutil
from typing import Callable, Optional

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.nvme import (
    NvmeDevice,
    NvmeHealth,
    NvmeSelfTestStatus,
    is_self_test_in_progress_error,
    list_nvme_devices,
    nvme_available,
    read_nvme_smart,
    read_nvme_self_test_status,
    start_nvme_self_test,
)
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters
from hardwaretest.tests.fio_runner import FioNvmeFullReadRunner, FioNvmeReadBenchmarkRunner
from hardwaretest.ui.i18n import language_manager


class _NvmeWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, action: Callable[[], object], parent: QWidget) -> None:
        super().__init__(parent)
        self._action = action

    def run(self) -> None:
        try:
            self.completed.emit(self._action())
        except Exception as exc:  # pragma: no cover - system and polkit dependent
            self.failed.emit(str(exc))


class NvmePanel(QWidget):
    """Diagnose data and non-destructive self-tests for NVMe drives."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_NvmeWorker] = None
        self._self_test_active = False
        self._devices: dict[str, NvmeDevice] = {}
        self._io_runner: Optional[BaseTestRunner] = None
        self._io_timed = False
        self._last_health: Optional[NvmeHealth] = None
        self._last_health_device = NvmeDevice("", "", "", "", "", 0)

        self.device_box = QComboBox()
        self._io_drive_checks: dict[str, QCheckBox] = {}
        self.select_all_nvme = QCheckBox("Alle NVMe-Laufwerke auswählen")
        self.io_drive_container = QWidget()
        self.io_drive_layout = QVBoxLayout(self.io_drive_container)
        self.io_drive_layout.setContentsMargins(0, 0, 0, 0)
        self.scan_btn = QPushButton("NVMe-Laufwerke suchen")
        self.health_btn = QPushButton("SMART-/Gesundheitsdaten lesen")
        self.self_test_box = QComboBox()
        self.self_test_box.addItem("Kurzer NVMe-Selbsttest", userData="short")
        self.self_test_box.addItem("Erweiterter NVMe-Selbsttest", userData="extended")
        self.self_test_btn = QPushButton("NVMe-Selbsttest starten")
        self.self_test_log_btn = QPushButton("Selbsttest-Protokoll lesen")

        self.benchmark_mode = QComboBox()
        self.benchmark_mode.addItem("Sequenzielles Lesen", userData="read")
        self.benchmark_mode.addItem("Zufälliges Lesen", userData="randread")
        self.benchmark_duration = QSpinBox()
        self.benchmark_duration.setRange(5, 3600)
        self.benchmark_duration.setValue(30)
        self.benchmark_duration.setSuffix(" s")
        self.benchmark_btn = QPushButton("Lese-Benchmark starten")
        self.full_read_btn = QPushButton("Vollständigen Lese-Test starten")
        self.stop_io_btn = QPushButton("Lese-Test stoppen")
        self.stop_io_btn.setEnabled(False)

        self.status = QLabel("Bereit")
        self.status.setWordWrap(True)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.self_test_progress = QProgressBar()
        self.self_test_progress.setVisible(False)
        self.io_result = QLabel("")
        self.io_result.setWordWrap(True)
        self.io_progress = QProgressBar()
        self.io_progress.setVisible(False)
        self.io_status = QLabel("")
        self.io_status.setWordWrap(True)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setStyleSheet("font-family: monospace; font-size: 11px;")

        self.hint = QLabel(
            "Die Diagnose liest Controller- und SMART-Daten. NVMe-Selbsttests sind "
            "nicht destruktiv; sie können jedoch je nach Laufwerk einige Zeit dauern."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #bbbbbb; font-style: italic;")

        form = QFormLayout()
        form.addRow("NVMe-Laufwerk für Diagnose/Selbsttest", self.device_box)
        form.addRow("Selbsttest", self.self_test_box)

        buttons = QHBoxLayout()
        buttons.addWidget(self.scan_btn)
        buttons.addWidget(self.health_btn)
        buttons.addWidget(self.self_test_btn)
        buttons.addWidget(self.self_test_log_btn)

        io_form = QFormLayout()
        io_form.addRow("NVMe-Ziellaufwerke", self.io_drive_container)
        io_form.addRow("Benchmark-Modus", self.benchmark_mode)
        io_form.addRow("Benchmark-Dauer", self.benchmark_duration)
        io_buttons = QHBoxLayout()
        io_buttons.addWidget(self.benchmark_btn)
        io_buttons.addWidget(self.full_read_btn)
        io_buttons.addWidget(self.stop_io_btn)
        io_group = QGroupBox("NVMe-Lesetests (nicht destruktiv)")
        io_layout = QVBoxLayout()
        io_layout.addWidget(QLabel(
            "Der Benchmark und der vollständige Lese-Test schreiben keine Daten. "
            "Der vollständige Test liest jeden Block und kann viele Stunden dauern."
        ))
        io_layout.itemAt(0).widget().setWordWrap(True)
        io_layout.addWidget(self.select_all_nvme)
        io_layout.addLayout(io_form)
        io_layout.addLayout(io_buttons)
        io_layout.addWidget(self.io_result)
        io_layout.addWidget(self.io_progress)
        io_layout.addWidget(self.io_status)
        io_group.setLayout(io_layout)

        layout = QVBoxLayout()
        layout.addWidget(self.hint)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.result)
        layout.addWidget(self.status)
        layout.addWidget(self.self_test_progress)
        layout.addWidget(io_group)
        layout.addWidget(self.details)
        self.setLayout(layout)

        self.scan_btn.clicked.connect(self._scan)
        self.health_btn.clicked.connect(self._read_health)
        self.self_test_btn.clicked.connect(self._start_self_test)
        self.self_test_log_btn.clicked.connect(self._read_self_test_log)
        self.benchmark_btn.clicked.connect(self._start_benchmark)
        self.full_read_btn.clicked.connect(self._start_full_read)
        self.stop_io_btn.clicked.connect(self._stop_io_test)
        self.select_all_nvme.toggled.connect(self._select_all_io_devices)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(8_000)
        self._status_timer.timeout.connect(self._poll_self_test_status)
        self._io_timer = QTimer(self)
        self._io_timer.setInterval(500)
        self._io_timer.timeout.connect(self._poll_io_test)
        language_manager.language_changed.connect(self._retranslate_runtime_texts)
        self._scan()

    def _set_runtime_text(self, widget: QLabel, source: str, **values: object) -> None:
        widget._hardwaretest_runtime_text = (source, values)
        translated_values = {
            key: language_manager.tr(value) if isinstance(value, str) else value
            for key, value in values.items()
        }
        widget.setText(language_manager.tr(source, **translated_values))

    @staticmethod
    def _set_raw_text(widget: QLabel, text: str) -> None:
        widget._hardwaretest_runtime_text = None
        widget.setText(text)

    def _retranslate_runtime_texts(self, _language: str | None = None) -> None:
        for widget in (self.status, self.result, self.io_result, self.io_status):
            state = getattr(widget, "_hardwaretest_runtime_text", None)
            if state:
                source, values = state
                translated_values = {
                    key: language_manager.tr(value) if isinstance(value, str) else value
                    for key, value in values.items()
                }
                widget.setText(language_manager.tr(source, **translated_values))
        if self._last_health is not None:
            self._render_health_details(self._last_health, self._last_health_device)

    def _scan(self) -> None:
        self.device_box.clear()
        self._clear_io_device_checks()
        self._devices.clear()
        if not nvme_available():
            self._set_runtime_text(self.status, "nvme-cli ist nicht installiert. Bitte das Paket nvme-cli installieren.")
            self._set_device_actions_enabled(False)
            return
        devices = list_nvme_devices()
        for index, device in enumerate(devices):
            self._devices[device.namespace_path] = device
            label = self._device_label(device)
            self.device_box.addItem(label, userData=device.namespace_path)
            checkbox = QCheckBox(label)
            checkbox.setChecked(index == 0)
            checkbox.toggled.connect(self._update_select_all_checkbox)
            self._io_drive_checks[device.namespace_path] = checkbox
            self.io_drive_layout.addWidget(checkbox)
        self._update_select_all_checkbox()
        if devices:
            self._set_runtime_text(self.status, "{count} NVMe-Laufwerk(e) gefunden.", count=len(devices))
            self._set_device_actions_enabled(True)
        else:
            self._set_runtime_text(self.status, "Keine NVMe-Laufwerke gefunden.")
            self._set_device_actions_enabled(False)

    def _clear_io_device_checks(self) -> None:
        self._io_drive_checks.clear()
        while self.io_drive_layout.count():
            item = self.io_drive_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _select_all_io_devices(self, checked: bool) -> None:
        for checkbox in self._io_drive_checks.values():
            blocker = QSignalBlocker(checkbox)
            checkbox.setChecked(checked)
            del blocker

    def _update_select_all_checkbox(self) -> None:
        checks = list(self._io_drive_checks.values())
        blocker = QSignalBlocker(self.select_all_nvme)
        self.select_all_nvme.setChecked(bool(checks) and all(item.isChecked() for item in checks))
        del blocker

    def _selected_io_devices(self) -> list[NvmeDevice]:
        return [
            self._devices[path]
            for path, checkbox in self._io_drive_checks.items()
            if checkbox.isChecked() and path in self._devices
        ]

    @staticmethod
    def _device_label(device: NvmeDevice) -> str:
        parts = [device.namespace_path]
        if device.model:
            parts.append(device.model)
        if device.serial:
            parts.append(f"SN {device.serial}")
        if device.firmware:
            parts.append(f"FW {device.firmware}")
        return " – ".join(parts)

    def _read_health(self) -> None:
        device = self._selected_device()
        if not device:
            return
        self._set_runtime_text(self.status, "NVMe-Gesundheitsdaten werden gelesen …")
        self._set_device_actions_enabled(False)
        self._run_worker(lambda: read_nvme_smart(device, self._privilege_prefix()), self._show_health)

    def _start_self_test(self) -> None:
        device = self._selected_device()
        if not device:
            return
        test_type = self.self_test_box.currentData() or "short"
        test_name = language_manager.tr("kurzen" if test_type == "short" else "erweiterten")
        answer = QMessageBox.question(
            self,
            language_manager.tr("NVMe-Selbsttest starten"),
            language_manager.tr(
                "Soll der {test} NVMe-Selbsttest auf {device} gestartet werden? "
                "Der Test ist nicht destruktiv.", test=test_name, device=device
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_runtime_text(self.status, "NVMe-Selbsttest wird gestartet …")
        self._set_device_actions_enabled(False)
        self._run_worker(
            lambda: start_nvme_self_test(device, test_type, self._privilege_prefix()),
            self._self_test_started,
        )

    def _read_self_test_log(self) -> None:
        """Read a previous or currently running test without starting another."""
        device = self._selected_device()
        if not device:
            return
        self._set_runtime_text(self.status, "NVMe-Selbsttest-Protokoll wird gelesen …")
        self._set_device_actions_enabled(False)
        self._run_worker(
            lambda: read_nvme_self_test_status(device, self._privilege_prefix()),
            self._show_self_test_status,
        )

    def _run_worker(
        self,
        action: Callable[[], object],
        on_success: Callable[[object], None],
        show_failure: bool = True,
    ) -> None:
        worker = _NvmeWorker(action, self)
        self._worker = worker
        worker.completed.connect(on_success)
        worker.completed.connect(lambda _: self._worker_finished())
        worker.failed.connect(lambda message: self._worker_failed(message, show_failure))
        worker.start()

    def _show_health(self, value: object) -> None:
        health = value
        if not isinstance(health, NvmeHealth):
            return
        self._last_health = health
        self._last_health_device = self._selected_device_info()
        self._render_health_details(health, self._last_health_device)
        self._set_runtime_text(self.status, "NVMe-Gesundheitsdaten aktualisiert.")

    def _render_health_details(self, health: NvmeHealth, device: NvmeDevice) -> None:
        warning = language_manager.tr("Keine") if health.critical_warning == 0 else str(health.critical_warning)
        lines = [
            language_manager.tr("NVMe-Gerät: {device}", device=health.device),
            language_manager.tr("Modell: {value}", value=device.model or "–"),
            language_manager.tr("Seriennummer: {value}", value=device.serial or "–"),
            language_manager.tr("Firmware: {value}", value=device.firmware or "–"),
            language_manager.tr("Status: {status}", status=language_manager.tr("OK") if health.healthy else language_manager.tr("WARNUNG")),
            language_manager.tr("Kritische Warnungen: {value}", value=warning),
            language_manager.tr("Temperatur: {value}", value=_unit(health.temperature_c, "°C")),
            language_manager.tr("Verfügbarer Reserveplatz: {value}", value=_unit(health.available_spare, "%")),
            language_manager.tr("Verschleiß: {value}", value=_unit(health.percentage_used, "%")),
            language_manager.tr("Betriebsstunden: {value}", value=_number(health.power_on_hours)),
            language_manager.tr("Unsichere Abschaltungen: {value}", value=_number(health.unsafe_shutdowns)),
            language_manager.tr("Medienfehler: {value}", value=_number(health.media_errors)),
            language_manager.tr("Fehlerlog-Einträge: {value}", value=_number(health.error_log_entries)),
            language_manager.tr("Gelesene Dateneinheiten: {value}", value=_number(health.data_units_read)),
            language_manager.tr("Geschriebene Dateneinheiten: {value}", value=_number(health.data_units_written)),
        ]
        self.details.setPlainText("\n".join(lines))

    def _self_test_started(self, _value: object) -> None:
        self._set_self_test_active(None)

    def _poll_self_test_status(self) -> None:
        if self._worker is not None or not self._self_test_active:
            return
        device = self._selected_device()
        if device:
            self._run_worker(
                lambda: read_nvme_self_test_status(device, self._privilege_prefix()),
                self._show_self_test_status,
                show_failure=False,
            )

    def _show_self_test_status(self, value: object) -> None:
        if not isinstance(value, NvmeSelfTestStatus):
            return
        if value.active:
            self._set_self_test_active(value.completion_percent)
        else:
            self._self_test_active = False
            self._status_timer.stop()
            self.self_test_progress.setVisible(False)
            self._show_self_test_result(value)

    def _start_benchmark(self) -> None:
        """Start a timed, raw-device NVMe read benchmark."""
        if self._self_test_active:
            QMessageBox.information(
                self,
                language_manager.tr("NVMe-Selbsttest läuft"),
                language_manager.tr(
                    "Während eines NVMe-Selbsttests kann kein zusätzlicher Lese-Test gestartet werden."
                ),
            )
            return
        devices = self._selected_io_devices()
        if not devices:
            self._show_no_io_device_selected()
            return
        if not self._fio_available():
            return
        rw = str(self.benchmark_mode.currentData() or "read")
        self._start_io_runner(
            FioNvmeReadBenchmarkRunner(
                TestParameters(duration_seconds=self.benchmark_duration.value()),
                [device.namespace_path for device in devices],
                rw=rw,
                block_size="1m" if rw == "read" else "4k",
                io_depth=32,
                use_pkexec=True,
            ),
            "NVMe-Lese-Benchmark läuft …",
            timed=True,
        )

    def _start_full_read(self) -> None:
        """Read every namespace block once; the dedicated runner never writes."""
        if self._self_test_active:
            QMessageBox.information(
                self,
                language_manager.tr("NVMe-Selbsttest läuft"),
                language_manager.tr(
                    "Während eines NVMe-Selbsttests kann kein zusätzlicher Lese-Test gestartet werden."
                ),
            )
            return
        devices = self._selected_io_devices()
        if not devices:
            self._show_no_io_device_selected()
            return
        if not self._fio_available():
            return
        paths = ", ".join(device.namespace_path for device in devices)
        answer = QMessageBox.question(
            self,
            language_manager.tr("Vollständigen NVMe-Lese-Test starten"),
            language_manager.tr(
                "Der vollständige Lese-Test liest jeden Block der ausgewählten Laufwerke ({count}): {devices}. Er schreibt keine Daten, kann aber mehrere Stunden dauern. Fortfahren?",
                count=len(devices),
                devices=paths,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_io_runner(
            FioNvmeFullReadRunner(
                TestParameters(duration_seconds=0),
                [device.namespace_path for device in devices],
                block_size="1m",
                io_depth=32,
                use_pkexec=True,
                total_bytes=sum(device.size_bytes for device in devices),
            ),
            "Vollständiger NVMe-Lese-Test läuft – alle Blöcke werden gelesen …",
            timed=True,
        )

    def _show_no_io_device_selected(self) -> None:
        QMessageBox.warning(
            self,
            language_manager.tr("Kein NVMe-Laufwerk ausgewählt"),
            language_manager.tr("Bitte mindestens ein NVMe-Ziellaufwerk auswählen."),
        )

    def _fio_available(self) -> bool:
        if shutil.which("fio"):
            return True
        QMessageBox.warning(
            self,
            language_manager.tr("Werkzeug nicht installiert"),
            language_manager.tr("Für NVMe-Lesetests wird das Paket fio benötigt."),
        )
        return False

    def _start_io_runner(self, runner: BaseTestRunner, status: str, timed: bool) -> None:
        try:
            runner.start()
        except Exception as exc:  # pragma: no cover - system and polkit dependent
            self._set_runtime_text(self.io_result, "✗ NVMe-Lese-Test konnte nicht gestartet werden")
            self.io_result.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_raw_text(self.io_status, str(exc))
            return
        self._io_runner = runner
        self._io_timed = timed
        self._set_runtime_text(self.io_result, "⌛ NVMe-Lese-Test läuft")
        self.io_result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")
        self._set_runtime_text(self.io_status, status)
        self.io_progress.setVisible(True)
        if timed:
            self.io_progress.setRange(0, 100)
            self.io_progress.setValue(0)
        else:
            self.io_progress.setRange(0, 0)
        self._set_io_actions_enabled(False)
        self.stop_io_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)
        self.device_box.setEnabled(False)
        self._io_timer.start()

    def _stop_io_test(self) -> None:
        if self._io_runner:
            self._io_runner.stop(aborted=True)
        self._finish_io_test(aborted=True)

    def _poll_io_test(self) -> None:
        runner = self._io_runner
        if runner is None:
            self._io_timer.stop()
            return
        if self._io_timed:
            progress = min(99, int(runner.progress() * 100)) if runner.is_running() else 100
            self.io_progress.setValue(progress)
            self._set_runtime_text(self.io_status, "Fortschritt: {progress}%", progress=progress)
        if not runner.is_running() and runner.get_result() is not None:
            self._finish_io_test()

    def _finish_io_test(self, aborted: bool = False) -> None:
        runner = self._io_runner
        if runner is None:
            return
        self._io_timer.stop()
        result = runner.get_result()
        self._io_runner = None
        self.io_progress.setVisible(False)
        self._set_io_actions_enabled(self.device_box.count() > 0)
        self.stop_io_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        self.device_box.setEnabled(True)
        if aborted:
            self._set_runtime_text(self.io_result, "? NVMe-Lese-Test abgebrochen")
            self.io_result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_runtime_text(self.io_status, "Lese-Test wurde manuell beendet.")
            return
        if result is None:
            self._set_runtime_text(self.io_result, "✗ NVMe-Lese-Test ohne Ergebnis beendet")
            self.io_result.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")
            return
        if not result.passed:
            self._set_runtime_text(self.io_result, "✗ NVMe-Lese-Test FEHLGESCHLAGEN")
            self.io_result.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")
            if result.errors:
                self._set_raw_text(self.io_status, "\n".join(result.errors))
            else:
                self._set_runtime_text(self.io_status, "Unbekannter Lese-Fehler.")
            return
        if isinstance(runner, FioNvmeReadBenchmarkRunner):
            summary = runner.summary()
            if summary:
                self._set_runtime_text(
                    self.io_result,
                    "✓ NVMe-Lese-Benchmark BESTANDEN – {throughput:.1f} MiB/s | {iops:.0f} IOPS | Ø-Latenz: {latency:.3f} ms",
                    throughput=summary["throughput_mib_s"],
                    iops=summary["iops"],
                    latency=summary["latency_ms"],
                )
                per_device = []
                for item in runner.device_summaries():
                    per_device.append(
                        f"{item['device']}: {float(item['throughput_mib_s']):.1f} MiB/s | "
                        f"{float(item['iops']):.0f} IOPS | "
                        f"{float(item['latency_ms']):.3f} ms"
                    )
                self._set_raw_text(self.io_status, "\n".join(per_device))
            else:
                self._set_runtime_text(self.io_result, "✓ NVMe-Lese-Benchmark BESTANDEN")
                self._set_runtime_text(self.io_status, "Keine Lese-Fehler erkannt.")
        else:
            self._set_runtime_text(self.io_result, "✓ Vollständiger NVMe-Lese-Test BESTANDEN")
            self._set_runtime_text(
                self.io_status,
                "Keine Lese-Fehler auf {count} Laufwerk(en) erkannt.",
                count=len(getattr(runner, "devices", [])),
            )
        self.io_result.setStyleSheet("color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;")

    def _worker_finished(self) -> None:
        self._worker = None
        self._set_device_actions_enabled(self.device_box.count() > 0)

    def _worker_failed(self, message: str, show_failure: bool = True) -> None:
        self._worker = None
        self._set_device_actions_enabled(self.device_box.count() > 0)
        if is_self_test_in_progress_error(message):
            self._set_self_test_active(None)
            return
        if not show_failure and self._self_test_active:
            # Some bridge/controller combinations do not expose a readable
            # in-progress log.  The test itself remains active; never turn
            # that into a red failure result just because polling failed.
            self._status_timer.stop()
            self._set_runtime_text(
                self.status,
                "NVMe-Selbsttest läuft. Der Fortschritt kann von diesem Laufwerk derzeit nicht abgefragt werden.",
            )
            self._set_runtime_text(self.result, "⌛ NVMe-Selbsttest läuft")
            self.result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")
            return
        self._set_runtime_text(self.status, "NVMe-Aktion fehlgeschlagen.")
        self._set_runtime_text(self.result, "✗ NVMe-Aktion fehlgeschlagen")
        self.result.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")
        if show_failure:
            QMessageBox.warning(self, language_manager.tr("NVMe-Aktion fehlgeschlagen"), message)

    def _set_self_test_active(self, completion_percent: Optional[int]) -> None:
        self._self_test_active = True
        self.self_test_progress.setVisible(True)
        if completion_percent is None:
            self.self_test_progress.setRange(0, 0)
            self._set_runtime_text(
                self.status,
                "NVMe-Selbsttest läuft – Fortschritt wird vom Laufwerk nicht gemeldet. "
                "Start weiterer Selbsttests ist gesperrt.",
            )
        else:
            self.self_test_progress.setRange(0, 100)
            self.self_test_progress.setValue(completion_percent)
            self._set_runtime_text(
                self.status,
                "NVMe-Selbsttest läuft – Fortschritt: {progress}%. "
                "Start weiterer Selbsttests ist gesperrt.",
                progress=completion_percent,
            )
        self._set_runtime_text(self.result, "⌛ NVMe-Selbsttest läuft")
        self.result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")
        if not self._status_timer.isActive():
            self._status_timer.start()
        self._set_device_actions_enabled(self.device_box.count() > 0)

    def _selected_device(self) -> str:
        return str(self.device_box.currentData() or "")

    def _selected_device_info(self) -> NvmeDevice:
        device = self._devices.get(self._selected_device())
        return device or NvmeDevice("", "", "", "", "", 0)

    def _show_self_test_result(self, status: NvmeSelfTestStatus) -> None:
        if status.passed is True:
            self._set_runtime_text(self.status, "NVMe-Selbsttest bestanden.")
            self._set_runtime_text(self.result, "✓ NVMe-Selbsttest BESTANDEN")
            self.result.setStyleSheet("color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;")
        elif status.passed is False:
            self._set_runtime_text(self.status, "NVMe-Selbsttest fehlgeschlagen.")
            self._set_runtime_text(
                self.result,
                "✗ NVMe-Selbsttest FEHLGESCHLAGEN (Code {code})",
                code=status.result_code,
            )
            self.result.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")
        else:
            self._set_runtime_text(
                self.status,
                "NVMe-Selbsttest abgeschlossen. Das Laufwerk liefert kein auswertbares Ergebnisprotokoll.",
            )
            self._set_runtime_text(
                self.result,
                "? NVMe-Selbsttest abgeschlossen – Ergebnis nicht verfügbar",
            )
            self.result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")

    @staticmethod
    def _privilege_prefix() -> tuple[str, ...]:
        if os.geteuid() == 0:
            return ()
        return ("pkexec",) if shutil.which("pkexec") else ()

    def _set_device_actions_enabled(self, enabled: bool) -> None:
        self.health_btn.setEnabled(enabled)
        self.self_test_btn.setEnabled(enabled and not self._self_test_active)
        self.self_test_log_btn.setEnabled(enabled)
        self.self_test_box.setEnabled(enabled and not self._self_test_active)
        if self._io_runner is None:
            self._set_io_actions_enabled(enabled)

    def _set_io_actions_enabled(self, enabled: bool) -> None:
        self.select_all_nvme.setEnabled(enabled)
        for checkbox in self._io_drive_checks.values():
            checkbox.setEnabled(enabled)
        self.benchmark_mode.setEnabled(enabled)
        self.benchmark_duration.setEnabled(enabled)
        self.benchmark_btn.setEnabled(enabled)
        self.full_read_btn.setEnabled(enabled)


def _number(value: Optional[int]) -> str:
    return "–" if value is None else f"{value:,}"


def _unit(value: Optional[int], suffix: str) -> str:
    return "–" if value is None else f"{value}{suffix}"
