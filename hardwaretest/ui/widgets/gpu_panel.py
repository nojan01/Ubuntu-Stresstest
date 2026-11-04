"""Herstellerneutraler GPU-Tab: Vulkan-Lasttest und sysfs-Messwerte (AMD, Intel, NVIDIA)."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.gpu_sysfs import DrmGpu, IntelGpuSampler, list_drm_gpus, match_drm
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.vulkan_runner import VulkanStressRunner, list_vulkan_gpus
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.report_bar import ReportBar, runner_report
from hardwaretest.ui.widgets.worker_lifecycle import connect_finished, release_worker

_TYPES = {"integrated": "integriert", "discrete": "dediziert", "virtual": "virtuell", "other": "sonstige"}


class _ScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.completed.emit(list_vulkan_gpus())
        except Exception as exc:  # pragma: no cover - treiberabhängig
            self.failed.emit(str(exc))


class GpuPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._gpus: list[dict] = []
        self._started_at: Optional[datetime] = None
        self._run_params: list[tuple[str, str]] = []
        self.report_bar = ReportBar()
        self._gpu_checks: dict[int, QCheckBox] = {}
        self._scan_worker: Optional[_ScanWorker] = None
        self._runner: Optional[VulkanStressRunner] = None
        self._monitoring = False

        self.hint = QLabel(
            "Lasttest für Grafikkarten von AMD, Intel und NVIDIA über Vulkan (Mesa RADV/ANV/NVK oder "
            "Herstellertreiber): Rechenlast und Mustertest im Grafikspeicher, Fehler werden auf der GPU "
            "gezählt. Bei integrierten GPUs werden höchstens 25 % des gemeinsamen Speichers belegt. Für "
            "NVIDIA-Karten mit proprietärem Treiber gibt es zusätzlich den Tab „NVIDIA GPU“ (CUDA, DCGM)."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #bbbbbb; font-style: italic;")

        self.scan_btn = QPushButton("GPUs suchen")
        self.inventory_status = QLabel("GPUs werden gesucht …")
        self.inventory_status.setWordWrap(True)
        self.select_all = QCheckBox("Alle GPUs auswählen")
        self.gpu_container = QWidget()
        self.gpu_layout = QVBoxLayout(self.gpu_container)
        self.gpu_layout.setContentsMargins(0, 0, 0, 0)
        select_layout = QVBoxLayout()
        select_layout.addWidget(self.select_all)
        select_layout.addWidget(self.gpu_container)
        select_group = QGroupBox("GPU-Auswahl")
        select_group.setLayout(select_layout)

        self.monitor_interval = QSpinBox()
        self.monitor_interval.setRange(1, 60)
        self.monitor_interval.setValue(2)
        self.monitor_interval.setSuffix(" s")
        self.monitor_start_btn = QPushButton("Liveüberwachung starten")
        self.monitor_stop_btn = QPushButton("Liveüberwachung stoppen")
        self.monitor_stop_btn.setEnabled(False)
        self.telemetry = QTextEdit()
        self.telemetry.setReadOnly(True)
        self.telemetry.setMaximumHeight(150)
        self.telemetry.setStyleSheet("font-family: monospace; font-size: 11px;")
        monitor_form = QFormLayout()
        monitor_form.addRow("Messintervall", self.monitor_interval)
        monitor_buttons = QHBoxLayout()
        monitor_buttons.addWidget(self.monitor_start_btn)
        monitor_buttons.addWidget(self.monitor_stop_btn)
        monitor_layout = QVBoxLayout()
        monitor_layout.addLayout(monitor_form)
        monitor_layout.addLayout(monitor_buttons)
        monitor_layout.addWidget(self.telemetry)
        monitor_group = QGroupBox("GPU-Liveüberwachung (sysfs)")
        monitor_group.setLayout(monitor_layout)

        self.stress_duration = QSpinBox()
        self.stress_duration.setRange(1, 1440)
        self.stress_duration.setValue(10)
        self.stress_duration.setSuffix(" min")
        self.stress_memory = QSpinBox()
        self.stress_memory.setRange(10, 95)
        self.stress_memory.setValue(80)
        self.stress_memory.setSuffix(" %")
        self.stress_start_btn = QPushButton("GPU-Lasttest starten")
        self.stress_stop_btn = QPushButton("GPU-Lasttest stoppen")
        self.stress_start_btn.setEnabled(False)
        self.stress_stop_btn.setEnabled(False)
        stress_form = QFormLayout()
        stress_form.addRow("Dauer", self.stress_duration)
        stress_form.addRow("Anteil des freien Grafikspeichers", self.stress_memory)
        stress_buttons = QHBoxLayout()
        stress_buttons.addWidget(self.stress_start_btn)
        stress_buttons.addWidget(self.stress_stop_btn)
        stress_layout = QVBoxLayout()
        stress_layout.addLayout(stress_form)
        stress_layout.addLayout(stress_buttons)
        stress_group = QGroupBox("GPU-Lasttest (Vulkan)")
        stress_group.setLayout(stress_layout)

        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(160)
        self.output.setStyleSheet("font-family: monospace; font-size: 11px;")
        result_layout = QVBoxLayout()
        result_layout.addWidget(self.result)
        result_layout.addWidget(self.progress)
        result_layout.addWidget(self.status)
        result_layout.addWidget(self.report_bar)
        result_layout.addWidget(self.output)
        result_group = QGroupBox("Testergebnis")
        result_group.setLayout(result_layout)

        top = QHBoxLayout()
        top.addWidget(self.scan_btn)
        top.addWidget(self.inventory_status, 1)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(self.hint)
        layout.addLayout(top)
        layout.addWidget(select_group)
        layout.addWidget(monitor_group)
        layout.addWidget(stress_group)
        layout.addWidget(result_group, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.scan_btn.clicked.connect(self._scan)
        self.select_all.toggled.connect(self._select_all)
        self.monitor_start_btn.clicked.connect(self._start_monitoring)
        self.monitor_stop_btn.clicked.connect(self._stop_monitoring)
        self.stress_start_btn.clicked.connect(self._start_stress)
        self.stress_stop_btn.clicked.connect(self.stop_test)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._monitor_timer = QTimer(self)
        self._intel_sampler = IntelGpuSampler()
        self._monitor_timer.timeout.connect(self._refresh_telemetry)
        language_manager.language_changed.connect(self._refresh_texts)
        QTimer.singleShot(0, self._scan)

    # --- Texte -----------------------------------------------------------------
    def _set_text(self, widget: QLabel, source: str, **values: object) -> None:
        widget._hardwaretest_runtime_text = (source, values)
        widget.setText(language_manager.tr(source, **values))

    def _refresh_texts(self, _language: str | None = None) -> None:
        for widget in (self.result, self.status, self.inventory_status):
            state = getattr(widget, "_hardwaretest_runtime_text", None)
            if state:
                source, values = state
                widget.setText(language_manager.tr(source, **values))
        for index, checkbox in self._gpu_checks.items():
            gpu = next((item for item in self._gpus if item["index"] == index), None)
            if gpu is not None:
                checkbox.setText(self._format_gpu(gpu))
        if self._monitoring:
            self._refresh_telemetry()

    # --- Suche -----------------------------------------------------------------
    def _scan(self) -> None:
        if self._scan_worker is not None or self._runner is not None:
            return
        self.scan_btn.setEnabled(False)
        self._set_text(self.inventory_status, "GPUs werden gesucht …")
        worker = _ScanWorker(self)
        self._scan_worker = worker
        worker.completed.connect(self._show_gpus)
        worker.failed.connect(self._scan_failed)
        connect_finished(worker, self._scan_finished)
        worker.start()

    def _show_gpus(self, value: object) -> None:
        self._gpus = list(value) if isinstance(value, list) else []
        self._clear_checks()
        for gpu in self._gpus:
            checkbox = QCheckBox(self._format_gpu(gpu))
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._update_select_all)
            self._gpu_checks[gpu["index"]] = checkbox
            self.gpu_layout.addWidget(checkbox)
        self._update_select_all()
        if self._gpus:
            self._set_text(self.inventory_status, "{count} GPU(s) mit Vulkan gefunden.", count=len(self._gpus))
        else:
            self._set_text(
                self.inventory_status,
                "Keine GPU mit Vulkan-Treiber gefunden (z. B. nur Server-Grafik wie Matrox/ASPEED oder "
                "NVIDIA mit Compute-Treiber ohne Vulkan).",
            )
        self._set_running(False)
        self._refresh_telemetry()

    def _scan_failed(self, message: str) -> None:
        self._gpus = []
        self._clear_checks()
        self._set_text(self.inventory_status, "GPU-Suche fehlgeschlagen: {message}", message=message)
        self._set_running(False)

    def _scan_finished(self, worker=None) -> None:
        if release_worker(self, "_scan_worker", worker):
            self.scan_btn.setEnabled(self._runner is None)

    # --- Liveüberwachung -------------------------------------------------------
    def _start_monitoring(self) -> None:
        self._monitoring = True
        self._intel_sampler.reset()
        self._monitor_timer.setInterval(self.monitor_interval.value() * 1000)
        self._monitor_timer.start()
        self.monitor_interval.setEnabled(False)
        self.monitor_start_btn.setEnabled(False)
        self.monitor_stop_btn.setEnabled(True)
        self._refresh_telemetry()

    def _stop_monitoring(self) -> None:
        self._monitoring = False
        self._monitor_timer.stop()
        self.monitor_interval.setEnabled(True)
        self.monitor_start_btn.setEnabled(True)
        self.monitor_stop_btn.setEnabled(False)
        lines = self.telemetry.toPlainText().splitlines()
        if lines and lines[0].startswith(language_manager.tr("Aktualisiert {time}", time="")):
            lines.pop(0)
        lines.insert(0, language_manager.tr("Überwachung gestoppt {time} – letzte Messwerte:",
                                            time=time.strftime("%H:%M:%S")))
        self.telemetry.setPlainText("\n".join(lines))

    def _refresh_telemetry(self) -> None:
        drm = list_drm_gpus()
        for card in drm:
            self._intel_sampler.update(card)
        matched = match_drm(self._gpus, drm)
        lines = []
        for gpu in self._gpus:
            card = matched.get(gpu["index"])
            lines.append(self._format_telemetry(gpu["index"], gpu["name"], card))
        known = set(id(card) for card in matched.values())
        for card in drm:
            if id(card) not in known:
                lines.append(self._format_telemetry(None, language_manager.tr("ohne Vulkan"), card))
        if not lines:
            lines.append(language_manager.tr("Keine Grafikkarte gefunden."))
        if self._monitoring:
            lines.insert(0, language_manager.tr("Aktualisiert {time}", time=time.strftime("%H:%M:%S")))
        self.telemetry.setPlainText("\n".join(lines))

    # --- Lasttest --------------------------------------------------------------
    def _start_stress(self) -> None:
        indices = self._selected_indices()
        if not indices:
            QMessageBox.warning(self, language_manager.tr("Keine GPU ausgewählt"),
                                language_manager.tr("Bitte mindestens eine GPU auswählen."))
            return
        minutes = self.stress_duration.value()
        lines: list[str] = []
        try:
            runner = VulkanStressRunner(TestParameters(duration_seconds=minutes * 60), indices,
                                        memory_percent=self.stress_memory.value(), log_fn=lines.append)
            runner.start()
        except Exception as exc:  # pragma: no cover - treiberabhängig
            QMessageBox.warning(self, language_manager.tr("GPU-Lasttest fehlgeschlagen"), str(exc))
            return
        runner._ui_log_lines = lines
        self._runner = runner
        self._started_at = datetime.now()
        tr = language_manager.tr
        self._run_params = [(tr("Dauer (geplant)"), f"{minutes} min"),
                            (tr("Speicherbelegung"), f"{self.stress_memory.value()} %")]
        self._run_params += [(f"GPU {gpu['index']}", self._format_gpu(gpu)) for gpu in self._gpus
                             if gpu["index"] in indices]
        self.output.clear()
        self._set_text(self.result, "⌛ GPU-Lasttest läuft")
        self.result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")
        self._set_text(self.status, "GPU-Lasttest läuft {minutes} min auf {count} GPU(s) …",
                       minutes=minutes, count=len(indices))
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_running(True)
        self._auto_monitoring = not self._monitoring
        if self._auto_monitoring:
            self._start_monitoring()
        self._timer.start()

    def _poll(self) -> None:
        runner = self._runner
        if runner is None:
            self._timer.stop()
            return
        self.output.setPlainText("\n".join(getattr(runner, "_ui_log_lines", [])))
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
        self.progress.setValue(int(runner.progress() * 1000))
        if not runner.is_running() and runner.get_result() is not None:
            self._finish()

    def stop_test(self) -> None:
        if self._runner:
            self._runner.stop(aborted=True)
            self._finish(aborted=True)

    def _finish(self, aborted: bool = False) -> None:
        runner = self._runner
        if runner is None:
            return
        self._timer.stop()
        result = runner.get_result()
        lines = getattr(runner, "_ui_log_lines", [])
        if aborted:
            lines.append(language_manager.tr("Test abgebrochen."))
        self.output.setPlainText("\n".join(lines))
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
        self._runner = None
        if getattr(self, "_auto_monitoring", False) and self._monitoring:
            self._stop_monitoring()
        self._auto_monitoring = False
        self.progress.setVisible(False)
        self._set_running(False)
        if aborted and result is None:
            self._set_text(self.result, "? GPU-Lasttest abgebrochen")
            self.result.setStyleSheet("color: #ffaa00; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_text(self.status, "GPU-Lasttest wurde manuell beendet.")
        elif aborted and result.passed:
            self._set_text(self.result,
                           "✓ GPU-Lasttest ABGEBROCHEN – bis zum Abbruch keine Fehler erkannt ({seconds:.0f}s)",
                           seconds=float(result.duration_actual or 0.0))
            self.result.setStyleSheet("color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_text(self.status, "GPU-Lasttest wurde manuell beendet.")
        elif aborted:
            self._set_text(self.result, "✗ GPU-Lasttest ABGEBROCHEN – {count} Fehlermeldung(en) bis zum Abbruch",
                           count=len(result.errors))
            self.result.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_text(self.status, "{count} Fehlermeldung(en). Details stehen im Protokoll.",
                           count=len(result.errors))
        elif result is not None and result.passed:
            self._set_text(self.result, "✓ GPU-Lasttest BESTANDEN")
            self.result.setStyleSheet("color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_text(self.status, "Keine Rechen- oder Speicherfehler gefunden.")
        else:
            self._set_text(self.result, "✗ GPU-Lasttest FEHLGESCHLAGEN")
            self.result.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")
            self._set_text(self.status, "{count} Fehlermeldung(en). Details stehen im Protokoll.",
                           count=len(result.errors) if result is not None else 0)
        self.report_bar.publish(runner_report("gpu-vulkan", language_manager.tr("GPU – Vulkan-Lasttest"), result,
                                              started=self._started_at, parameters=self._run_params,
                                              log_text="\n".join(lines), cancelled=aborted))

    # --- Hilfen ----------------------------------------------------------------
    def _set_running(self, running: bool) -> None:
        self.scan_btn.setEnabled(not running and self._scan_worker is None)
        self.select_all.setEnabled(not running and bool(self._gpus))
        for checkbox in self._gpu_checks.values():
            checkbox.setEnabled(not running)
        self.stress_start_btn.setEnabled(not running and bool(self._gpus))
        self.stress_stop_btn.setEnabled(running)
        self.stress_duration.setEnabled(not running)
        self.stress_memory.setEnabled(not running)

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
            widget = self.gpu_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _format_gpu(gpu: dict) -> str:
        memory = gpu.get("memory") or 0
        return language_manager.tr(
            "GPU {index}: {name} ({vendor}, {kind}, {memory:.1f} GiB, Vulkan {api})",
            index=gpu["index"], name=gpu["name"], vendor=gpu.get("vendor", "?"),
            kind=language_manager.tr(_TYPES.get(gpu.get("type", "other"), "sonstige")),
            memory=memory / 2**30, api=gpu.get("api", "?"),
        )

    @staticmethod
    def _format_telemetry(index: Optional[int], name: str, card: Optional[DrmGpu]) -> str:
        label = f"GPU {index}" if index is not None else "–"
        if card is None:
            return language_manager.tr("{label} {name}: keine sysfs-Messwerte", label=label, name=name)

        def value(number: object, unit: str, digits: int = 0) -> str:
            if number is None:
                return "–"
            return f"{number:.{digits}f} {unit}" if isinstance(number, float) else f"{number} {unit}"

        clock = value(card.clock_mhz, "MHz")
        if card.max_clock_mhz:
            clock = f"{card.clock_mhz if card.clock_mhz is not None else '–'}/{card.max_clock_mhz} MHz"
        temp = value(card.temperature_c, "°C", 1)
        if card.package_temperature:
            temp += " " + language_manager.tr("(CPU-Paket)")
        power = value(card.power_w, "W", 1)
        if card.power_rapl:
            power += " (RAPL)"
        elif card.power_needs_root:
            power += " " + language_manager.tr("(Leistung nur mit root)")
        vram = "–"
        if card.vram_total:
            vram = f"{(card.vram_used or 0) / 2**30:.1f}/{card.vram_total / 2**30:.1f} GiB"
        return language_manager.tr(
            "{label} {name} [{driver} {pci}]: {temp} | {power} | Last {busy} | Takt {clock} | "
            "Speichertakt {mclk} | VRAM {vram} | Lüfter {fan}",
            label=label, name=name, driver=card.driver, pci=card.pci,
            temp=temp, power=power,
            busy=value(card.busy_percent, "%"), clock=clock,
            mclk=value(card.memory_clock_mhz, "MHz"), vram=vram, fan=value(card.fan_rpm, "U/min"),
        )
