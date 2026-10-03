"""Prime95/mprime specific control panel."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.system_info import SystemInfo, read_system_info
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters
from hardwaretest.tests.mprime import MprimeRunner, _default_mprime_path
from hardwaretest.ui.utils import launch_command_in_terminal, build_klog_command, build_mcelog_command
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.temperature_widget import TemperatureWidget


Modes = [
    ("Blend", "blend", "Ausgewogener RAM/CPU-Test"),
    ("Small FFTs", "small_ffts", "Maximale CPU-Hitze, wenig RAM"),
    ("In-place large FFTs", "inplace_large_ffts", "Belastet Stromversorgung"),
    ("Custom", "custom", "Eigene RAM-Menge nutzen"),
]


class MprimePanel(QWidget):
    log_signal = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        system_info_provider: Callable[[], SystemInfo] = read_system_info,
    ) -> None:
        super().__init__(parent)
        self.system_info_provider = system_info_provider
        self.runner: Optional[BaseTestRunner] = None
        self._system_info = self.system_info_provider()
        self._reserved_core_limit = self._system_info.reserved_core_limit()

        self.info_label = QLabel("Systemdaten werden ermittelt...")
        self.refresh_btn = QPushButton("Systemwerte aktualisieren")
        self.refresh_btn.clicked.connect(self._handle_refresh_request)

        self.binary_path = QLineEdit(str(Path(_default_mprime_path())))
        self.binary_path.setClearButtonEnabled(True)

        self.mode_box = QComboBox()
        for label, data, tooltip in Modes:
            self.mode_box.addItem(label, userData=data)
            idx = self.mode_box.count() - 1
            self.mode_box.setItemData(idx, tooltip, role=Qt.ItemDataRole.ToolTipRole)
        self.mode_box.currentIndexChanged.connect(self._sync_custom_controls)

        self.memory_mb = QSpinBox()
        self.memory_mb.setRange(64, 1024 * 1024)
        self.memory_mb.setValue(1024)

        self.fft_minutes = QSpinBox()
        self.fft_minutes.setRange(1, 60)
        self.fft_minutes.setValue(15)

        self.worker_threads = QSpinBox()
        self.worker_threads.setRange(1, self._system_info.cpu_cores)
        self.worker_threads.setValue(self._reserved_core_limit)

        self.duration_hours = QSpinBox()
        self.duration_hours.setRange(0, 240)
        self.duration_hours.setSuffix(" h")

        self.duration_minutes = QSpinBox()
        self.duration_minutes.setRange(0, 59)
        self.duration_minutes.setSuffix(" m")

        self.duration_seconds = QSpinBox()
        self.duration_seconds.setRange(0, 59)
        self.duration_seconds.setSuffix(" s")
        self._set_default_duration(seconds=600)

        self.direct_mode = QCheckBox("Nur mprime -t (bestehende local.txt nutzen)")
        self.direct_mode.toggled.connect(self._sync_custom_controls)

        # Ergebnis-Anzeige
        self.result_label = QLabel("")
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress = QLabel("Bereit")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(5000)

        # Temperatur-Widget
        self.temp_widget = TemperatureWidget()

        self.start_btn = QPushButton("Prime95 starten")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.btop_btn = QPushButton("btop starten")
        self.klog_btn = QPushButton("Kernel-Logs")
        self.mce_btn = QPushButton("MCE-Logs")

        form = QFormLayout()
        form.addRow("Binary", self.binary_path)
        form.addRow("Modus", self.mode_box)
        form.addRow("RAM (MB)", self.memory_mb)
        form.addRow("Minuten pro FFT", self.fft_minutes)
        form.addRow("Worker Threads", self.worker_threads)
        form.addRow("Dauer", self._build_duration_widget())
        form.addRow("Direktmodus", self.direct_mode)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.btop_btn)
        btn_row.addWidget(self.klog_btn)
        btn_row.addWidget(self.mce_btn)

        layout = QVBoxLayout()
        layout.addWidget(self.info_label)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.temp_widget)
        layout.addLayout(form)
        layout.addLayout(btn_row)
        layout.addWidget(self.result_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_view)
        self.setLayout(layout)

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_progress)

        self.start_btn.clicked.connect(self.start_test)
        self.stop_btn.clicked.connect(self.stop_test)
        self.btop_btn.clicked.connect(self._launch_btop)
        self.klog_btn.clicked.connect(self._launch_klogs)
        self.mce_btn.clicked.connect(self._launch_mcelog)
        self.log_signal.connect(self._append_log)

        self._sync_custom_controls()
        self._refresh_system_info(update_memory_value=False)

    def start_test(self) -> None:
        if self.runner and self.runner.is_running():
            return
        self._refresh_system_info(update_memory_value=False)
        duration_seconds = self._total_duration_seconds()
        if duration_seconds <= 0:
            self._append_log("Bitte eine Dauer groesser als 0 Sekunden auswaehlen.")
            return
        binary = self._resolve_binary_path()
        if not binary:
            self._append_log("mprime Binary nicht gefunden.")
            return
        usable_threads = max(1, min(self.worker_threads.value(), self._reserved_core_limit))
        if usable_threads != self.worker_threads.value():
            self._append_log(
                f"Threads automatisch auf {usable_threads} reduziert, damit die GUI responsiv bleibt."
            )
        params = TestParameters(duration_seconds=duration_seconds, cpu_cores=usable_threads)
        mem_value = self.memory_mb.value() if self.mode_box.currentData() == "custom" else None
        direct = self.direct_mode.isChecked()
        self.runner = MprimeRunner(
            params,
            mode=self.mode_box.currentData(),
            worker_threads=usable_threads,
            custom_memory_mb=mem_value,
            fft_minutes=self.fft_minutes.value(),
            binary_path=binary,
            use_config=not direct,
            log_fn=self._handle_runner_log,
        )
        try:
            self.runner.start()
        except Exception as exc:  # pragma: no cover
            self._append_log(f"Fehler beim Start: {exc}")
            self.runner = None
            return
        # Swap-Warnung bei Blend/Custom-Modus
        if self._system_info.swap_enabled and self.mode_box.currentData() in ("blend", "custom"):
            self._append_log(
                "⚠ WARNUNG: Swap ist aktiv! Blend/Custom-Tests koennen auf die "
                "Festplatte ausweichen. Swap vorher deaktivieren empfohlen."
            )
        self.timer.start()
        self.temp_widget.start_monitoring()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress.setText(language_manager.tr("Fortschritt: 0%"))
        self.result_label.setText("")
        self.result_label.setStyleSheet("")
        self._append_log("mprime gestartet...")

    def stop_test(self) -> None:
        if not self.runner:
            return
        self.runner.stop()
        self.timer.stop()
        self.temp_widget.stop_monitoring()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress.setText(language_manager.tr("Manuell gestoppt"))
        self._show_result()
        self._append_log("Test manuell gestoppt.")

    def _append_log(self, text: str) -> None:
        self.log_view.append(text)

    def _handle_runner_log(self, text: str) -> None:
        self.log_signal.emit(text)

    def _update_progress(self) -> None:
        if not self.runner:
            return
        progress = int(self.runner.progress() * 100)
        self.progress_bar.setValue(progress)
        self.progress.setText(language_manager.tr("Fortschritt: {progress}%", progress=progress))
        if not self.runner.is_running():
            self.timer.stop()
            self.temp_widget.stop_monitoring()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.progress_bar.setValue(100)
            self.progress.setText(language_manager.tr("Fertig"))
            self._show_result()

    def _show_result(self) -> None:
        """Zeigt das Pass/Fail-Ergebnis an."""
        if not self.runner:
            return
        result = self.runner.get_result()
        if result is None:
            return
        if result.passed:
            self.result_label.setText(language_manager.tr("✓ BESTANDEN ({seconds:.0f}s)", seconds=result.duration_actual))
            self.result_label.setStyleSheet(
                "color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;"
            )
        else:
            error_summary = "; ".join(result.errors[:3])
            self.result_label.setText(language_manager.tr("✗ FEHLER – {error}", error=error_summary))
            self.result_label.setStyleSheet(
                "color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;"
            )

    def _refresh_system_info(self, update_memory_value: bool) -> None:
        info = self.system_info_provider()
        self._system_info = info
        swap_state = "aktiv" if info.swap_enabled else "deaktiviert"
        reserve_note = "mind. 1 physischer Kern fuer GUI reserviert"
        if info.physical_cpu_cores > 0:
            core_text = (
                f"Kerne: {info.cpu_cores} logisch / {info.physical_cpu_cores} physisch"
            )
        else:
            core_text = f"Kerne: {info.cpu_cores} logisch"
        self.info_label.setText(
            f"Verfuegbar: {info.available_memory_mb} MB | {core_text} ({reserve_note}) | Swap {swap_state}"
        )
        self._reserved_core_limit = info.reserved_core_limit()
        self.worker_threads.setMaximum(info.cpu_cores)
        if update_memory_value:
            new_value = min(self.memory_mb.maximum(), info.available_memory_mb)
            self.memory_mb.setValue(max(self.memory_mb.minimum(), new_value))

    def _handle_refresh_request(self) -> None:
        self._refresh_system_info(update_memory_value=True)

    def _sync_custom_controls(self) -> None:
        direct = self.direct_mode.isChecked()
        is_custom = self.mode_box.currentData() == "custom"
        self.mode_box.setEnabled(not direct)
        self.memory_mb.setEnabled((not direct) and is_custom)
        self.fft_minutes.setEnabled(not direct)
        self.worker_threads.setEnabled(True)

    def _total_duration_seconds(self) -> int:
        return (
            self.duration_hours.value() * 3600
            + self.duration_minutes.value() * 60
            + self.duration_seconds.value()
        )

    def _set_default_duration(self, seconds: int) -> None:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        self.duration_hours.setValue(hours)
        self.duration_minutes.setValue(minutes)
        self.duration_seconds.setValue(secs)

    def _build_duration_widget(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.duration_hours)
        layout.addWidget(self.duration_minutes)
        layout.addWidget(self.duration_seconds)
        container.setLayout(layout)
        return container

    def _resolve_binary_path(self) -> Optional[str]:
        path = Path(self.binary_path.text().strip() or _default_mprime_path()).expanduser()
        if path.exists() and path.is_file():
            return str(path)
        return None

    def _launch_btop(self) -> None:
        if not launch_command_in_terminal(["btop"], geometry=(110, 44)):
            self._append_log("btop konnte nicht gestartet werden. Bitte Installation pruefen.")

    def _launch_klogs(self) -> None:
        if not launch_command_in_terminal(build_klog_command()):
            self._append_log("Kernel-Logs konnten nicht gestartet werden. Bitte Installation pruefen.")

    def _launch_mcelog(self) -> None:
        if not launch_command_in_terminal(build_mcelog_command()):
            self._append_log("MCE-Logs konnten nicht gestartet werden. Bitte Installation pruefen.")
