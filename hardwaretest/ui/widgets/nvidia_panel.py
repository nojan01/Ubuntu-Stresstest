"""NVIDIA GPU inventory and optional DCGM diagnostics UI."""

from __future__ import annotations

import os
import shutil
from typing import Optional

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

from hardwaretest.core.nvidia import (
    LinuxNvidiaStatus,
    NvidiaGpu,
    inspect_linux_nvidia,
    list_nvidia_gpus,
)
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.nvidia_runner import DcgmDiagnosticRunner, dcgmi_available
from hardwaretest.ui.i18n import language_manager


class _GpuScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.completed.emit(list_nvidia_gpus())
        except Exception as exc:  # pragma: no cover - driver dependent
            self.failed.emit(str(exc))


class _GpuTelemetryWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.completed.emit(list_nvidia_gpus())
        except Exception as exc:  # pragma: no cover - driver dependent
            self.failed.emit(str(exc))


class NvidiaPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._gpus: list[NvidiaGpu] = []
        self._gpu_checks: dict[int, QCheckBox] = {}
        self._scan_worker: Optional[_GpuScanWorker] = None
        self._telemetry_worker: Optional[_GpuTelemetryWorker] = None
        self._monitoring = False
        self._runner: Optional[DcgmDiagnosticRunner] = None
        self._linux_status: Optional[LinuxNvidiaStatus] = None

        self.hint = QLabel(
            "nvidia-smi liefert Inventar- und Gesundheitsdaten. NVIDIA DCGM führt optional aktive "
            "Bereitschafts-, Speicher-, PCIe- und Belastungsdiagnosen aus."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #bbbbbb; font-style: italic;")

        self.scan_btn = QPushButton("NVIDIA-GPUs suchen")
        self.linux_check_btn = QPushButton("Linux-Basisprüfung")
        self.select_all = QCheckBox("Alle NVIDIA-GPUs auswählen")
        self.gpu_container = QWidget()
        self.gpu_layout = QVBoxLayout(self.gpu_container)
        self.gpu_layout.setContentsMargins(0, 0, 0, 0)
        self.inventory_status = QLabel("NVIDIA-GPUs werden gesucht …")
        self.inventory_status.setWordWrap(True)
        self.driver_result = QLabel("")
        self.driver_result.setWordWrap(True)
        self.install_hint = QLabel(
            "Installationsanleitung: /opt/hardwaretest/docs/NVIDIA_GPU.md"
        )
        self.install_hint.setWordWrap(True)
        self.install_hint.setStyleSheet("color: #88bbdd;")
        self.inventory = QTextEdit()
        self.inventory.setReadOnly(True)
        self.inventory.setMaximumHeight(190)
        self.inventory.setStyleSheet("font-family: monospace; font-size: 11px;")

        self.monitor_interval = QSpinBox()
        self.monitor_interval.setRange(1, 60)
        self.monitor_interval.setValue(2)
        self.monitor_interval.setSuffix(" s")
        self.monitor_start_btn = QPushButton("Liveüberwachung starten")
        self.monitor_stop_btn = QPushButton("Liveüberwachung stoppen")
        self.monitor_start_btn.setEnabled(False)
        self.monitor_stop_btn.setEnabled(False)
        self.monitor_status = QLabel("Liveüberwachung ist gestoppt.")
        self.monitor_status.setWordWrap(True)
        self.telemetry = QTextEdit()
        self.telemetry.setReadOnly(True)
        self.telemetry.setMaximumHeight(135)
        self.telemetry.setStyleSheet("font-family: monospace; font-size: 11px;")

        monitor_form = QFormLayout()
        monitor_form.addRow("Messintervall", self.monitor_interval)
        monitor_buttons = QHBoxLayout()
        monitor_buttons.addWidget(self.monitor_start_btn)
        monitor_buttons.addWidget(self.monitor_stop_btn)
        monitor_layout = QVBoxLayout()
        monitor_layout.addLayout(monitor_form)
        monitor_layout.addLayout(monitor_buttons)
        monitor_layout.addWidget(self.monitor_status)
        monitor_layout.addWidget(self.telemetry)
        monitor_group = QGroupBox("GPU-Liveüberwachung")
        monitor_group.setLayout(monitor_layout)

        select_layout = QVBoxLayout()
        select_layout.addWidget(self.select_all)
        select_layout.addWidget(self.gpu_container)
        select_group = QGroupBox("GPU-Auswahl")
        select_group.setLayout(select_layout)

        self.level_box = QComboBox()
        self.level_box.addItem("Stufe 1 – Schnelltest / Einsatzbereitschaft", userData=1)
        self.level_box.addItem("Stufe 2 – Mittel / PCIe und Speicher", userData=2)
        self.level_box.addItem("Stufe 3 – Lange Hardwarediagnose", userData=3)
        self.level_box.addItem("Stufe 4 – Erweiterte Langzeitdiagnose", userData=4)
        self.start_btn = QPushButton("DCGM-Diagnose starten")
        self.stop_btn = QPushButton("DCGM-Diagnose stoppen")
        self.stop_btn.setEnabled(False)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.diag_status = QLabel("")
        self.diag_status.setWordWrap(True)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("font-family: monospace; font-size: 11px;")

        diag_form = QFormLayout()
        diag_form.addRow("DCGM-Diagnosestufe", self.level_box)
        diag_buttons = QHBoxLayout()
        diag_buttons.addWidget(self.start_btn)
        diag_buttons.addWidget(self.stop_btn)
        diag_layout = QVBoxLayout()
        diag_layout.addLayout(diag_form)
        diag_layout.addLayout(diag_buttons)
        diag_layout.addWidget(self.result)
        diag_layout.addWidget(self.progress)
        diag_layout.addWidget(self.diag_status)
        diag_layout.addWidget(self.output)
        diag_group = QGroupBox("NVIDIA-DCGM-Diagnose")
        diag_group.setLayout(diag_layout)

        top = QHBoxLayout()
        top.addWidget(self.scan_btn)
        top.addWidget(self.linux_check_btn)
        top.addWidget(self.inventory_status, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.install_hint)
        layout.addLayout(top)
        layout.addWidget(self.driver_result)
        layout.addWidget(select_group)
        layout.addWidget(self.inventory)
        layout.addWidget(monitor_group)
        layout.addWidget(diag_group, 1)

        self.scan_btn.clicked.connect(self._scan)
        self.linux_check_btn.clicked.connect(self._run_linux_check)
        self.select_all.toggled.connect(self._select_all)
        self.start_btn.clicked.connect(self._start_diagnostic)
        self.stop_btn.clicked.connect(self._stop_diagnostic)
        self.monitor_start_btn.clicked.connect(self._start_monitoring)
        self.monitor_stop_btn.clicked.connect(self._stop_monitoring)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll_diagnostic)
        self._monitor_timer = QTimer(self)
        self._monitor_timer.timeout.connect(self._request_telemetry)
        language_manager.language_changed.connect(self._refresh_inventory_text)
        QTimer.singleShot(0, self._scan)

    def _set_runtime_text(self, widget: QLabel, source: str, **values: object) -> None:
        widget._hardwaretest_runtime_text = (source, values)
        widget.setText(language_manager.tr(source, **values))

    @staticmethod
    def _set_raw_text(widget: QLabel, value: str) -> None:
        widget._hardwaretest_runtime_text = None
        widget.setText(value)

    def _scan(self) -> None:
        if (
            self._scan_worker is not None
            or self._telemetry_worker is not None
            or self._runner is not None
        ):
            return
        status = inspect_linux_nvidia()
        self._show_linux_status(status)
        if not status.driver_ready:
            self._gpus = []
            self._clear_checks()
            self._set_diagnostic_enabled(False)
            self._set_monitoring_available(False)
            return
        self._set_runtime_text(self.inventory_status, "NVIDIA-GPUs werden gesucht …")
        self.scan_btn.setEnabled(False)
        worker = _GpuScanWorker(self)
        self._scan_worker = worker
        worker.completed.connect(self._show_gpus)
        worker.failed.connect(self._scan_failed)
        worker.finished.connect(self._scan_finished)
        worker.start()

    def _run_linux_check(self) -> None:
        status = inspect_linux_nvidia()
        self._show_linux_status(status)
        if not status.driver_ready:
            self._gpus = []
            self._clear_checks()
            self._set_diagnostic_enabled(False)
            self._set_monitoring_available(False)
        elif self._gpus:
            self._set_diagnostic_enabled(dcgmi_available())

    def _show_linux_status(self, status: LinuxNvidiaStatus) -> None:
        self._linux_status = status
        self.inventory.setPlainText(self._format_linux_status(status))
        if not status.hardware_present:
            self._set_runtime_text(
                self.driver_result, "Linux-Basisprüfung: Keine NVIDIA-PCIe-GPU erkannt."
            )
            self.driver_result.setStyleSheet("color: #ffaa00; font-weight: bold;")
            self._set_runtime_text(
                self.inventory_status, "Nur die Linux-Basisprüfung ist verfügbar."
            )
        elif status.driver_ready:
            self._set_runtime_text(
                self.driver_result, "✓ NVIDIA-Treiberinstallation BESTANDEN"
            )
            self.driver_result.setStyleSheet("color: #44ff44; font-weight: bold;")
            self._set_runtime_text(
                self.inventory_status, "NVIDIA-Treiber und nvidia-smi funktionieren."
            )
        else:
            self._set_runtime_text(
                self.driver_result, "✗ NVIDIA-Treiber fehlt oder funktioniert nicht korrekt"
            )
            self.driver_result.setStyleSheet("color: #ff4444; font-weight: bold;")
            self._set_runtime_text(
                self.inventory_status,
                "Aktive NVIDIA-Tests sind deaktiviert. Die Linux-Basisprüfung zeigt die Ursache.",
            )

    def _show_gpus(self, value: object) -> None:
        self._gpus = list(value) if isinstance(value, list) else []
        self._clear_checks()
        for position, gpu in enumerate(self._gpus):
            checkbox = QCheckBox(f"GPU {gpu.index}: {gpu.model} – {gpu.uuid}")
            checkbox.setChecked(position == 0)
            checkbox.toggled.connect(self._update_select_all)
            self._gpu_checks[gpu.index] = checkbox
            self.gpu_layout.addWidget(checkbox)
        self._update_select_all()
        if not self._gpus:
            self._set_runtime_text(self.inventory_status, "Keine NVIDIA-GPUs gefunden.")
            self.inventory.clear()
            self._set_diagnostic_enabled(False)
            self._set_monitoring_available(False)
            return
        self._set_runtime_text(
            self.inventory_status, "{count} NVIDIA-GPU(s) gefunden.", count=len(self._gpus)
        )
        self.inventory.setPlainText("\n\n".join(self._format_gpu(gpu) for gpu in self._gpus))
        self._set_diagnostic_enabled(dcgmi_available())
        self._set_monitoring_available(True)
        if not dcgmi_available():
            self._set_runtime_text(
                self.diag_status,
                "DCGM ist nicht installiert. Inventar und Gesundheitsdaten sind verfügbar; aktive Diagnosen benötigen dcgmi.",
            )

    def _scan_failed(self, message: str) -> None:
        self._set_runtime_text(self.inventory_status, "NVIDIA-GPU-Erkennung fehlgeschlagen.")
        self.inventory.setPlainText(message)
        self._set_diagnostic_enabled(False)
        self._set_monitoring_available(False)

    def _scan_finished(self) -> None:
        self._scan_worker = None
        self.scan_btn.setEnabled(True)

    def _start_diagnostic(self) -> None:
        indices = [index for index, checkbox in self._gpu_checks.items() if checkbox.isChecked()]
        if not indices:
            QMessageBox.warning(
                self,
                language_manager.tr("Keine NVIDIA-GPU ausgewählt"),
                language_manager.tr("Bitte mindestens eine NVIDIA-GPU auswählen."),
            )
            return
        if not dcgmi_available():
            QMessageBox.warning(
                self,
                language_manager.tr("DCGM nicht installiert"),
                language_manager.tr("Für aktive NVIDIA-Diagnosen wird dcgmi benötigt."),
            )
            return
        level = int(self.level_box.currentData() or 1)
        answer = QMessageBox.question(
            self,
            language_manager.tr("DCGM-Diagnose starten"),
            language_manager.tr(
                "DCGM-Stufe {level} auf {count} GPU(s) starten? Laufende GPU-Arbeiten sollten vorher beendet werden.",
                level=level,
                count=len(indices),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        lines: list[str] = []
        try:
            runner = DcgmDiagnosticRunner(
                TestParameters(duration_seconds=0),
                indices,
                level,
                log_fn=lines.append,
                use_pkexec=os.geteuid() != 0 and shutil.which("pkexec") is not None,
            )
            runner.start()
        except Exception as exc:  # pragma: no cover - DCGM/polkit dependent
            QMessageBox.warning(self, language_manager.tr("DCGM-Diagnose fehlgeschlagen"), str(exc))
            return
        runner._ui_log_lines = lines
        self._runner = runner
        self.output.clear()
        self._set_runtime_text(self.result, "⌛ DCGM-Diagnose läuft")
        self.result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")
        self._set_runtime_text(
            self.diag_status,
            "DCGM-Stufe {level} läuft auf {count} GPU(s) …",
            level=level,
            count=len(indices),
        )
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self._set_running(True)
        self._start_monitoring()
        self._timer.start()

    def _start_monitoring(self) -> None:
        if not self._gpus:
            return
        if not self._selected_indices():
            QMessageBox.warning(
                self,
                language_manager.tr("Keine NVIDIA-GPU ausgewählt"),
                language_manager.tr("Bitte mindestens eine NVIDIA-GPU auswählen."),
            )
            return
        self._monitoring = True
        self._monitor_timer.setInterval(self.monitor_interval.value() * 1000)
        self._monitor_timer.start()
        self.monitor_interval.setEnabled(False)
        self.monitor_start_btn.setEnabled(False)
        self.monitor_stop_btn.setEnabled(True)
        self._set_runtime_text(self.monitor_status, "GPU-Liveüberwachung läuft …")
        self._request_telemetry()

    def _stop_monitoring(self) -> None:
        self._monitoring = False
        self._monitor_timer.stop()
        self.monitor_interval.setEnabled(True)
        self.monitor_start_btn.setEnabled(bool(self._gpus))
        self.monitor_stop_btn.setEnabled(False)
        self._set_runtime_text(self.monitor_status, "Liveüberwachung ist gestoppt.")

    def _request_telemetry(self) -> None:
        if (
            not self._monitoring
            or self._telemetry_worker is not None
            or self._scan_worker is not None
        ):
            return
        worker = _GpuTelemetryWorker(self)
        self._telemetry_worker = worker
        worker.completed.connect(self._show_telemetry)
        worker.failed.connect(self._telemetry_failed)
        worker.finished.connect(self._telemetry_finished)
        worker.start()

    def _show_telemetry(self, value: object) -> None:
        fresh = list(value) if isinstance(value, list) else []
        if fresh:
            self._gpus = fresh
            self.inventory.setPlainText("\n\n".join(self._format_gpu(gpu) for gpu in fresh))
        selected = set(self._selected_indices())
        visible = [gpu for gpu in fresh if gpu.index in selected]
        if visible:
            self.telemetry.setPlainText("\n".join(self._format_telemetry(gpu) for gpu in visible))
            self._set_runtime_text(
                self.monitor_status,
                "GPU-Liveüberwachung läuft – {count} GPU(s)",
                count=len(visible),
            )
        else:
            self._set_runtime_text(
                self.monitor_status, "Keine Messwerte für die ausgewählten GPUs verfügbar."
            )

    def _telemetry_failed(self, message: str) -> None:
        self.telemetry.setPlainText(message)
        self._set_runtime_text(self.monitor_status, "GPU-Liveüberwachung fehlgeschlagen.")

    def _telemetry_finished(self) -> None:
        self._telemetry_worker = None

    def _poll_diagnostic(self) -> None:
        runner = self._runner
        if runner is None:
            self._timer.stop()
            return
        lines = getattr(runner, "_ui_log_lines", [])
        self.output.setPlainText("\n".join(lines))
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
        if not runner.is_running() and runner.get_result() is not None:
            self._finish_diagnostic()

    def _stop_diagnostic(self) -> None:
        if self._runner:
            self._runner.stop(aborted=True)
        self._finish_diagnostic(aborted=True)

    def _finish_diagnostic(self, aborted: bool = False) -> None:
        runner = self._runner
        if runner is None:
            return
        self._timer.stop()
        result = runner.get_result()
        counts = runner.status_counts()
        self.output.setPlainText("\n".join(runner.output_lines))
        self._runner = None
        self.progress.setVisible(False)
        self._set_running(False)
        if aborted:
            self._set_runtime_text(self.result, "? DCGM-Diagnose abgebrochen")
            self.result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_runtime_text(self.diag_status, "DCGM-Diagnose wurde manuell beendet.")
        elif result is not None and result.passed and counts["fail"] == 0:
            self._set_runtime_text(self.result, "✓ DCGM-Diagnose BESTANDEN")
            self.result.setStyleSheet("color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_runtime_text(
                self.diag_status,
                "Ergebnisse: {passed} bestanden, {warned} Warnungen, {skipped} übersprungen.",
                passed=counts["pass"], warned=counts["warn"], skipped=counts["skip"],
            )
        else:
            self._set_runtime_text(self.result, "✗ DCGM-Diagnose FEHLGESCHLAGEN")
            self.result.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_runtime_text(
                self.diag_status,
                "DCGM meldet {failed} fehlgeschlagene Prüfung(en). Details stehen im Protokoll.",
                failed=counts["fail"],
            )

    def _set_running(self, running: bool) -> None:
        self.scan_btn.setEnabled(not running)
        self.linux_check_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.level_box.setEnabled(not running)
        self.select_all.setEnabled(not running)
        for checkbox in self._gpu_checks.values():
            checkbox.setEnabled(not running)
        self.start_btn.setEnabled(not running and bool(self._gpus) and dcgmi_available())

    def _set_diagnostic_enabled(self, enabled: bool) -> None:
        self.start_btn.setEnabled(enabled)
        self.level_box.setEnabled(enabled)
        self.select_all.setEnabled(bool(self._gpus))

    def _set_monitoring_available(self, available: bool) -> None:
        if not available and self._monitoring:
            self._stop_monitoring()
        self.monitor_start_btn.setEnabled(available and not self._monitoring)
        self.monitor_stop_btn.setEnabled(available and self._monitoring)
        self.monitor_interval.setEnabled(not self._monitoring)

    def _selected_indices(self) -> list[int]:
        return [index for index, checkbox in self._gpu_checks.items() if checkbox.isChecked()]

    def _select_all(self, checked: bool) -> None:
        for checkbox in self._gpu_checks.values():
            blocker = QSignalBlocker(checkbox)
            checkbox.setChecked(checked)
            del blocker

    def _update_select_all(self) -> None:
        checks = list(self._gpu_checks.values())
        blocker = QSignalBlocker(self.select_all)
        self.select_all.setChecked(bool(checks) and all(item.isChecked() for item in checks))
        del blocker

    def _clear_checks(self) -> None:
        self._gpu_checks.clear()
        while self.gpu_layout.count():
            item = self.gpu_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_inventory_text(self, _language: str | None = None) -> None:
        for widget in (self.result, self.diag_status, self.monitor_status):
            state = getattr(widget, "_hardwaretest_runtime_text", None)
            if state:
                source, values = state
                widget.setText(language_manager.tr(source, **values))
        if self._linux_status is not None:
            self._show_linux_status(self._linux_status)
        if self._gpus:
            self.inventory.setPlainText("\n\n".join(self._format_gpu(gpu) for gpu in self._gpus))
            self._set_runtime_text(
                self.inventory_status, "{count} NVIDIA-GPU(s) gefunden.", count=len(self._gpus)
            )

    @staticmethod
    def _format_linux_status(status: LinuxNvidiaStatus) -> str:
        smi = language_manager.tr("OK") if status.nvidia_smi_works else (
            language_manager.tr("nicht installiert")
            if not status.nvidia_smi_installed else language_manager.tr("FEHLER")
        )
        lines = [
            language_manager.tr("Linux-Basisprüfung (nur lesend)"),
            language_manager.tr("NVIDIA-PCIe-Geräte: {value}", value=", ".join(status.pci_devices) or "–"),
            language_manager.tr("Gebundene Treiber: {value}", value=", ".join(status.bound_drivers) or "–"),
            language_manager.tr("Kernelmodule: {value}", value=", ".join(status.kernel_modules) or "–"),
            language_manager.tr("Geräteknoten: {value}", value=", ".join(status.device_nodes) or "–"),
            language_manager.tr("nvidia-smi: {value}", value=smi),
        ]
        if status.nvidia_smi_error:
            lines.append(language_manager.tr("Fehlermeldung: {value}", value=status.nvidia_smi_error))
        return "\n".join(lines)

    def _format_gpu(self, gpu: NvidiaGpu) -> str:
        return "\n".join([
            f"GPU {gpu.index}: {gpu.model}",
            f"UUID: {gpu.uuid}",
            language_manager.tr("Treiber: {driver} | CUDA: {cuda}", driver=gpu.driver_version, cuda=gpu.cuda_version),
            language_manager.tr(
                "VRAM: {used}/{total} MiB", used=_value(gpu.memory_used_mib), total=_value(gpu.memory_total_mib)
            ),
            language_manager.tr("Temperatur: {value} °C", value=_value(gpu.temperature_c)),
            language_manager.tr(
                "Leistung: {draw}/{limit} W", draw=_value(gpu.power_draw_w), limit=_value(gpu.power_limit_w)
            ),
            language_manager.tr(
                "ECC: {mode} | korrigiert: {corrected} | unkorrigiert: {uncorrected}",
                mode=gpu.ecc_mode,
                corrected=_value(gpu.ecc_corrected),
                uncorrected=_value(gpu.ecc_uncorrected),
            ),
            language_manager.tr(
                "Auslastung: {value} %", value=_value(gpu.utilization_gpu_percent)
            ),
            language_manager.tr(
                "Takte: Grafik {graphics} MHz | SM {sm} MHz | Speicher {memory} MHz",
                graphics=_value(gpu.clock_graphics_mhz),
                sm=_value(gpu.clock_sm_mhz),
                memory=_value(gpu.clock_memory_mhz),
            ),
            language_manager.tr(
                "PCIe: Gen {generation}/{max_generation} | Breite x{width}/x{max_width}",
                generation=_value(gpu.pcie_generation_current),
                max_generation=_value(gpu.pcie_generation_max),
                width=_value(gpu.pcie_width_current),
                max_width=_value(gpu.pcie_width_max),
            ),
        ])

    @staticmethod
    def _format_telemetry(gpu: NvidiaGpu) -> str:
        return language_manager.tr(
            "GPU {index}: Last {util}% | {temperature} °C | VRAM {used}/{total} MiB | "
            "{power} W | Grafik/SM/Speicher {graphics}/{sm}/{memory} MHz | "
            "PCIe Gen {generation} x{width}",
            index=gpu.index,
            util=_value(gpu.utilization_gpu_percent),
            temperature=_value(gpu.temperature_c),
            used=_value(gpu.memory_used_mib),
            total=_value(gpu.memory_total_mib),
            power=_value(gpu.power_draw_w),
            graphics=_value(gpu.clock_graphics_mhz),
            sm=_value(gpu.clock_sm_mhz),
            memory=_value(gpu.clock_memory_mhz),
            generation=_value(gpu.pcie_generation_current),
            width=_value(gpu.pcie_width_current),
        )


def _value(value: object) -> str:
    return "N/A" if value is None else str(value)
