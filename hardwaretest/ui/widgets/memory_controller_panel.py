"""Panel for stressing memory controllers via stress-ng cache/atomic tests."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.system_info import SystemInfo, read_system_info
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters
from hardwaretest.tests.memory_controller import MC_STRESSORS, MemoryControllerRunner
from hardwaretest.ui.utils import launch_command_in_terminal, build_klog_command, build_mcelog_command
from hardwaretest.ui.widgets.temperature_widget import TemperatureWidget


SystemInfoProvider = Callable[[], SystemInfo]


class MemoryControllerPanel(QWidget):
    log_signal = Signal(str)

    def __init__(
        self,
        system_info_provider: SystemInfoProvider = read_system_info,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.system_info_provider = system_info_provider
        self.runner: Optional[BaseTestRunner] = None
        self._system_info = self.system_info_provider()
        self._reserved_core_limit = self._system_info.reserved_core_limit()

        self.info_label = QLabel("Systemdaten werden ermittelt...")
        self.refresh_btn = QPushButton("Systemwerte aktualisieren")
        self.refresh_btn.clicked.connect(lambda: self._refresh_system_info())

        self.duration_hours = QSpinBox()
        self.duration_hours.setRange(0, 240)
        self.duration_hours.setSuffix(" h")
        self.duration_minutes = QSpinBox()
        self.duration_minutes.setRange(0, 59)
        self.duration_minutes.setSuffix(" m")
        self.duration_seconds = QSpinBox()
        self.duration_seconds.setRange(0, 59)
        self.duration_seconds.setSuffix(" s")
        self._set_default_duration(600)

        self.threads = QSpinBox()
        self.threads.setRange(1, self._system_info.cpu_cores)
        self.threads.setValue(self._reserved_core_limit)

        self.operations = QSpinBox()
        self.operations.setRange(0, 10_000_000)
        self.operations.setValue(0)
        self.operations.setSpecialValueText("unbegrenzt")

        # Stressor-Checkboxen
        self._stressor_checks: dict[str, QCheckBox] = {}
        default_on = {"cache", "atomic", "mcontend"}
        for key, desc in MC_STRESSORS.items():
            cb = QCheckBox(f"{key} – {desc}")
            cb.setChecked(key in default_on)
            self._stressor_checks[key] = cb

        # Ergebnis-Anzeige
        self.result_label = QLabel("")
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress = QLabel("Bereit")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        self.start_btn = QPushButton("Speichercontroller-Test starten")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.btop_btn = QPushButton("btop starten")
        self.klog_btn = QPushButton("Kernel-Logs")
        self.mce_btn = QPushButton("MCE-Logs")

        # Temperatur-Widget
        self.temp_widget = TemperatureWidget()

        form = QFormLayout()
        form.addRow("Dauer", self._build_duration_widget())
        form.addRow("Threads", self.threads)
        form.addRow("Operationen", self.operations)

        stressor_layout = QVBoxLayout()
        for cb in self._stressor_checks.values():
            stressor_layout.addWidget(cb)

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
        layout.addWidget(QLabel("Stressoren:"))
        layout.addLayout(stressor_layout)
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

        self._refresh_system_info()

    def _get_selected_stressors(self) -> list[str]:
        return [key for key, cb in self._stressor_checks.items() if cb.isChecked()]

    def start_test(self) -> None:
        if self.runner and self.runner.is_running():
            return
        self._refresh_system_info()
        duration_seconds = self._total_duration_seconds()
        if duration_seconds <= 0:
            self._append_log("Bitte eine Dauer groesser als 0 Sekunden auswaehlen.")
            return
        stressors = self._get_selected_stressors()
        if not stressors:
            self._append_log("Bitte mindestens einen Stressor auswaehlen.")
            return
        usable_threads = max(1, min(self.threads.value(), self._reserved_core_limit))
        if usable_threads != self.threads.value():
            self._append_log(
                f"Threads automatisch auf {usable_threads} reduziert, damit die GUI responsiv bleibt."
            )
        params = TestParameters(duration_seconds=duration_seconds, cpu_cores=usable_threads)
        self._append_log(f"Stressoren: {', '.join(stressors)}")
        self.runner = MemoryControllerRunner(
            params,
            stressors=stressors,
            operations=self.operations.value(),
            log_fn=self._handle_runner_log,
        )
        try:
            self.runner.start()
        except Exception as exc:  # pragma: no cover
            self._append_log(f"Fehler beim Start: {exc}")
            self.runner = None
            return
        self.timer.start()
        self.temp_widget.start_monitoring()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress.setText("Fortschritt: 0%")
        self.result_label.setText("")
        self.result_label.setStyleSheet("")
        self._append_log("Speichercontroller-Test gestartet...")

    def stop_test(self) -> None:
        if not self.runner:
            return
        self.runner.stop()
        self.timer.stop()
        self.temp_widget.stop_monitoring()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress.setText("Manuell gestoppt")
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
        self.progress.setText(f"Fortschritt: {progress}%")
        if not self.runner.is_running():
            self.timer.stop()
            self.temp_widget.stop_monitoring()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.progress_bar.setValue(100)
            self.progress.setText("Fertig")
            self._show_result()

    def _show_result(self) -> None:
        """Zeigt das Pass/Fail-Ergebnis an."""
        if not self.runner:
            return
        result = self.runner.get_result()
        if result is None:
            return
        if result.passed:
            self.result_label.setText(f"✓ BESTANDEN ({result.duration_actual:.0f}s)")
            self.result_label.setStyleSheet(
                "color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;"
            )
        else:
            error_summary = "; ".join(result.errors[:3])
            self.result_label.setText(f"✗ FEHLER – {error_summary}")
            self.result_label.setStyleSheet(
                "color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;"
            )

    def _refresh_system_info(self) -> None:
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
        self.threads.setMaximum(info.cpu_cores)

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

    def _launch_btop(self) -> None:
        if not launch_command_in_terminal(["btop"], geometry=(110, 44)):
            self._append_log("btop konnte nicht gestartet werden. Bitte Installation pruefen.")

    def _launch_klogs(self) -> None:
        if not launch_command_in_terminal(build_klog_command()):
            self._append_log("Kernel-Logs konnten nicht gestartet werden. Bitte Installation pruefen.")

    def _launch_mcelog(self) -> None:
        if not launch_command_in_terminal(build_mcelog_command()):
            self._append_log("MCE-Logs konnten nicht gestartet werden. Bitte Installation pruefen.")
