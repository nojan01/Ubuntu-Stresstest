"""OpenDCDiag control panel – sucht stille Datenfehler je CPU-Kern."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import (
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

from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.opendcdiag import OpenDcDiagRunner, default_opendcdiag_path, runs_as_root
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.utils import build_mcelog_command, launch_command_in_terminal
from hardwaretest.ui.widgets.temperature_widget import TemperatureWidget


class OpenDcDiagPanel(QWidget):
    log_signal = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.runner: Optional[OpenDcDiagRunner] = None

        default_binary = default_opendcdiag_path()
        variant = ("generische Variante (CPU ohne AVX2)" if default_binary.endswith("-generic")
                   else "AVX2-Variante")
        self.info_label = QLabel(
            "OpenDCDiag prüft jeden CPU-Kern mit Rechenaufgaben und bekannten Ergebnissen "
            "auf stille Datenfehler. Gewählt: " + variant + "."
        )
        self.info_label.setWordWrap(True)
        if os.geteuid() == 0:
            root_text = ""
        elif runs_as_root(default_binary):
            root_text = "Läuft per sudo als root (ohne Passwort, siehe /etc/sudoers.d/hardwaretest-opendcdiag)."
        else:
            root_text = "Ohne root werden Tests mit MSR-Zugriff und Intel In-Field Scan übersprungen."
        self.root_label = QLabel(root_text)
        self.root_label.setWordWrap(True)

        self.binary_path = QLineEdit(default_binary)
        self.binary_path.setClearButtonEnabled(True)
        self.cpuset = QLineEdit()
        self.cpuset.setPlaceholderText("leer = alle CPUs, z. B. 0-7,16 oder p1 für Sockel 1")

        self.duration_hours = QSpinBox()
        self.duration_hours.setRange(0, 240)
        self.duration_hours.setSuffix(" h")
        self.duration_minutes = QSpinBox()
        self.duration_minutes.setRange(0, 59)
        self.duration_minutes.setSuffix(" m")
        self.duration_minutes.setValue(10)
        self.duration_seconds = QSpinBox()
        self.duration_seconds.setRange(0, 59)
        self.duration_seconds.setSuffix(" s")

        self.result_label = QLabel("")
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setWordWrap(True)
        self.progress = QLabel("Bereit")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(5000)
        self.temp_widget = TemperatureWidget()

        self.start_btn = QPushButton("OpenDCDiag starten")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.btop_btn = QPushButton("btop starten")
        self.mce_btn = QPushButton("MCE-Logs")

        duration = QWidget()
        duration_layout = QHBoxLayout(duration)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        for spin in (self.duration_hours, self.duration_minutes, self.duration_seconds):
            duration_layout.addWidget(spin)

        form = QFormLayout()
        form.addRow("Binary", self.binary_path)
        form.addRow("CPU-Auswahl", self.cpuset)
        form.addRow("Dauer", duration)

        buttons = QHBoxLayout()
        for button in (self.start_btn, self.stop_btn, self.btop_btn, self.mce_btn):
            buttons.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.addWidget(self.root_label)
        layout.addWidget(self.temp_widget)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.result_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_view)

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_progress)
        self.start_btn.clicked.connect(self.start_test)
        self.stop_btn.clicked.connect(self.stop_test)
        self.btop_btn.clicked.connect(self._launch_btop)
        self.mce_btn.clicked.connect(self._launch_mcelog)
        self.log_signal.connect(self.log_view.append)

    def _total_duration_seconds(self) -> int:
        return (self.duration_hours.value() * 3600 + self.duration_minutes.value() * 60
                + self.duration_seconds.value())

    def start_test(self) -> None:
        if self.runner and self.runner.is_running():
            return
        duration = self._total_duration_seconds()
        if duration <= 0:
            self.log_view.append(language_manager.tr("Bitte eine Dauer groesser als 0 Sekunden auswaehlen."))
            return
        binary = Path(self.binary_path.text().strip() or default_opendcdiag_path()).expanduser()
        if not binary.is_file():
            self.log_view.append(language_manager.tr("OpenDCDiag-Binary nicht gefunden."))
            return
        self.runner = OpenDcDiagRunner(
            TestParameters(duration_seconds=duration),
            binary_path=str(binary),
            cpuset=self.cpuset.text(),
            log_fn=self.log_signal.emit,
        )
        try:
            self.runner.start()
        except Exception as exc:  # pragma: no cover
            self.log_view.append(f"Fehler beim Start: {exc}")
            self.runner = None
            return
        self.timer.start()
        self.temp_widget.start_monitoring()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress.setText(language_manager.tr("Fortschritt: 0%"))
        self.result_label.setText("")
        self.result_label.setStyleSheet("")

    def stop_test(self) -> None:
        if not self.runner:
            return
        self.runner.stop(aborted=self.runner.is_running())
        self.timer.stop()
        self.temp_widget.stop_monitoring()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress.setText(language_manager.tr("Manuell gestoppt"))
        self._show_result()

    def _update_progress(self) -> None:
        if not self.runner:
            return
        progress = int(self.runner.progress() * 100)
        self.progress_bar.setValue(progress)
        self.progress.setText(language_manager.tr("Fortschritt: {progress}%", progress=progress))
        if not self.runner.is_running() and self.runner.get_result() is not None:
            self.timer.stop()
            self.temp_widget.stop_monitoring()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.progress_bar.setValue(100)
            self.progress.setText(language_manager.tr("Fertig"))
            self._show_result()

    def _show_result(self) -> None:
        result = self.runner.get_result() if self.runner else None
        if result is None:
            return
        if result.passed:
            self.result_label.setText(language_manager.tr("✓ BESTANDEN ({seconds:.0f}s)", seconds=result.duration_actual))
            self.result_label.setStyleSheet("color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;")
        else:
            self.result_label.setText(language_manager.tr("✗ FEHLER – {error}", error="; ".join(result.errors[:3])))
            self.result_label.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;")

    def _launch_btop(self) -> None:
        if not launch_command_in_terminal(["btop"], geometry=(110, 44)):
            self.log_view.append(language_manager.tr("btop konnte nicht gestartet werden. Bitte Installation pruefen."))

    def _launch_mcelog(self) -> None:
        if not launch_command_in_terminal(build_mcelog_command()):
            self.log_view.append(language_manager.tr("MCE-Logs konnten nicht gestartet werden. Bitte Installation pruefen."))
