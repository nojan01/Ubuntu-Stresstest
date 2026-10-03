"""Panel fuer den zyklischen RAM-Fuelltest (Speicher vollschreiben/freigeben)."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.system_info import (
    SystemInfo,
    check_swapoff_safe,
    disable_swap,
    enable_swap,
    needs_password_for_swap,
    read_system_info,
)
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters
from hardwaretest.tests.memory_fill import MemoryFillRunner
from hardwaretest.ui.utils import launch_command_in_terminal, build_klog_command, build_mcelog_command
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.temperature_widget import TemperatureWidget


# ---------------------------------------------------------------------------
# Background worker so swapoff never blocks the Qt event loop
# ---------------------------------------------------------------------------

class _SwapWorker(QThread):
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, password: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._password = password

    def run(self) -> None:
        try:
            disable_swap(password=self._password)
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _SwapOnWorker(QThread):
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, password: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._password = password

    def run(self) -> None:
        try:
            enable_swap(password=self._password)
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


SystemInfoProvider = Callable[[], SystemInfo]


class MemoryFillPanel(QWidget):
    """Konfiguriert und startet den zyklischen RAM-Fuelltest."""

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

        # --- System-Info ---
        self.info_label = QLabel("Systemdaten werden ermittelt...")
        self.refresh_btn = QPushButton("Systemwerte aktualisieren")
        self.refresh_btn.clicked.connect(lambda: self._refresh_system_info(update_memory_value=True))

        # --- Temperatur-Widget ---
        self.temp_widget = TemperatureWidget()

        # --- Dauer ---
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

        # --- Speicher ---
        self.memory_mb = QSpinBox()
        self.memory_mb.setRange(0, 1024 * 1024)
        self.memory_mb.setSuffix(" MB")
        self.memory_mb.setSpecialValueText("automatisch")
        self.memory_mb.setValue(0)
        self.memory_mb.setToolTip(
            "0 = automatisch (nutzt den gesamten verfuegbaren Speicher abzueglich Reserve). "
            "Manueller Wert: fester Allokationsbetrag pro Zyklus."
        )

        self.reserve_mb = QSpinBox()
        self.reserve_mb.setRange(128, 4096)
        self.reserve_mb.setSuffix(" MB")
        self.reserve_mb.setValue(512)
        self.reserve_mb.setToolTip(
            "Speicher der fuer das Betriebssystem und die GUI frei bleibt. "
            "Wird nur bei automatischer Speichermenge beruecksichtigt."
        )

        self.chunk_mb = QSpinBox()
        self.chunk_mb.setRange(16, 4096)
        self.chunk_mb.setSuffix(" MB")
        self.chunk_mb.setValue(256)
        self.chunk_mb.setToolTip(
            "Groesse der einzelnen Speicherbloecke. Kleinere Werte sind "
            "bei fragmentiertem Speicher erfolgreicher, groessere effizienter."
        )

        self.pause_seconds = QSpinBox()
        self.pause_seconds.setRange(0, 60)
        self.pause_seconds.setSuffix(" s")
        self.pause_seconds.setValue(3)
        self.pause_seconds.setToolTip(
            "Pause zwischen den Zyklen. Waehrend dieser Zeit bleibt der "
            "Speicher freigegeben, sodass der Rueckgang z.B. in btop sichtbar "
            "wird. 0 = ohne Pause (Speicher wird sofort wieder gefuellt)."
        )

        # --- Ergebnis-Anzeige ---
        self.result_label = QLabel("")
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # --- Fortschritt ---
        self.progress = QLabel("Bereit")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(5000)

        # --- Beschreibung ---
        desc = QLabel(
            "Allokiert den gesamten verfuegbaren RAM, schreibt in jedem Zyklus "
            "wechselnde Bitmuster (0xAA, 0x55, 0xFF, 0x00, Walking-Bits sowie "
            "zyklus-abhaengige Wort- und Zufallsmuster), verifiziert, gibt frei "
            "und wiederholt. Findet defekte Speicherzellen und Timing-Fehler im RAM."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #bbbbbb; font-style: italic; padding: 4px 0;")

        # --- Buttons ---
        # --- Swap-Button ---
        self.swap_btn = QPushButton("Swap deaktivieren")
        self.swap_btn.setToolTip(
            "Deaktiviert Swap, damit der RAM-Test den physischen Speicher testet "
            "und nicht auf die Festplatte ausweicht."
        )
        self.swap_btn.clicked.connect(self._disable_swap)

        self.swap_on_btn = QPushButton("Swap aktivieren")
        self.swap_on_btn.setToolTip(
            "Aktiviert den Swap wieder (swapon -a), z.B. nachdem er fuer den "
            "RAM-Test deaktiviert wurde."
        )
        self.swap_on_btn.clicked.connect(self._enable_swap)

        self.start_btn = QPushButton("RAM-Fuelltest starten")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.btop_btn = QPushButton("btop starten")
        self.klog_btn = QPushButton("Kernel-Logs")
        self.mce_btn = QPushButton("MCE-Logs")

        # --- Layout ---
        form = QFormLayout()
        form.addRow("Dauer", self._build_duration_widget())
        form.addRow("Speicher", self.memory_mb)
        form.addRow("Reserve (OS/GUI)", self.reserve_mb)
        form.addRow("Blockgroesse", self.chunk_mb)
        form.addRow("Pause je Zyklus", self.pause_seconds)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.btop_btn)
        btn_row.addWidget(self.klog_btn)
        btn_row.addWidget(self.mce_btn)

        util_row = QHBoxLayout()
        util_row.addWidget(self.swap_btn)
        util_row.addWidget(self.swap_on_btn)

        layout = QVBoxLayout()
        layout.addWidget(self.info_label)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.temp_widget)
        layout.addWidget(desc)
        layout.addLayout(form)
        layout.addLayout(util_row)
        layout.addLayout(btn_row)
        layout.addWidget(self.result_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_view)
        self.setLayout(layout)

        # --- Timer ---
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_progress)

        # --- Signale ---
        self.start_btn.clicked.connect(self.start_test)
        self.stop_btn.clicked.connect(self.stop_test)
        self.btop_btn.clicked.connect(self._launch_btop)
        self.klog_btn.clicked.connect(self._launch_klogs)
        self.mce_btn.clicked.connect(self._launch_mcelog)
        self.log_signal.connect(self._append_log)

        self._refresh_system_info(update_memory_value=True)

    # --------------------------------------------------------------------- #
    #  Test-Steuerung                                                        #
    # --------------------------------------------------------------------- #

    def start_test(self) -> None:
        if self.runner and self.runner.is_running():
            return
        self._refresh_system_info(update_memory_value=False)

        duration = self._total_duration_seconds()
        if duration <= 0:
            self._append_log("Bitte eine Dauer groesser als 0 Sekunden auswaehlen.")
            return

        mem = self.memory_mb.value()
        reserve = self.reserve_mb.value()
        chunk = self.chunk_mb.value()

        # Bei aktivem Swap: automatisch deaktivieren
        if self._system_info.swap_enabled:
            self._append_log("Swap ist aktiv – versuche automatisch zu deaktivieren …")
            if not self._try_disable_swap_blocking():
                # Swap konnte nicht deaktiviert werden – Nutzer fragen ob trotzdem starten
                answer = QMessageBox.question(
                    self,
                    "Swap noch aktiv",
                    "Swap konnte nicht deaktiviert werden.\n\n"
                    "Der Test koennte auf die Festplatte ausweichen und "
                    "unzuverlaessige Ergebnisse liefern.\n\n"
                    "Trotzdem starten?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._append_log("Test abgebrochen – Swap ist noch aktiv.")
                    return
                self._append_log("⚠ WARNUNG: Test startet mit aktivem Swap!")
            else:
                self._append_log("✓ Swap erfolgreich deaktiviert.")
                self._refresh_system_info(update_memory_value=False)

        params = TestParameters(duration_seconds=duration)
        self.runner = MemoryFillRunner(
            params,
            memory_mb=mem,
            reserve_mb=reserve,
            chunk_mb=chunk,
            pause_seconds=self.pause_seconds.value(),
            log_fn=self._handle_runner_log,
        )
        try:
            self.runner.start()
        except Exception as exc:
            self._append_log(f"Fehler beim Start: {exc}")
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
        self._append_log("Zyklischer RAM-Fuelltest gestartet …")

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

    # --------------------------------------------------------------------- #
    #  Log / Fortschritt / Ergebnis                                          #
    # --------------------------------------------------------------------- #

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
            self.result_label.setText(language_manager.tr(
                "✓ BESTANDEN – Keine Fehler gefunden ({seconds:.0f}s)", seconds=result.duration_actual
            ))
            self.result_label.setStyleSheet(
                "color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;"
            )
        else:
            error_summary = "; ".join(result.errors[:3])
            if len(result.errors) > 3:
                error_summary += f" … (+{len(result.errors) - 3} weitere)"
            self.result_label.setText(language_manager.tr("✗ FEHLER GEFUNDEN – {error}", error=error_summary))
            self.result_label.setStyleSheet(
                "color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;"
            )

    # --------------------------------------------------------------------- #
    #  System-Info                                                            #
    # --------------------------------------------------------------------- #

    def _refresh_system_info(self, update_memory_value: bool = False) -> None:
        info = self.system_info_provider()
        self._system_info = info
        swap_state = "aktiv" if info.swap_enabled else "deaktiviert"
        if info.physical_cpu_cores > 0:
            core_text = (
                f"Kerne: {info.cpu_cores} logisch / {info.physical_cpu_cores} physisch"
            )
        else:
            core_text = f"Kerne: {info.cpu_cores} logisch"
        self.info_label.setText(
            f"Verfuegbar: {info.available_memory_mb} MB | {core_text} | Swap {swap_state}"
        )
        self.memory_mb.setMaximum(max(info.available_memory_mb, self.memory_mb.minimum()))
        if update_memory_value and self.memory_mb.value() == 0:
            pass  # auto-Modus beibehalten

        # Swap-Button Status
        self.swap_btn.setEnabled(info.swap_enabled)
        if not info.swap_enabled:
            self.swap_btn.setText(language_manager.tr("Swap bereits deaktiviert"))
        else:
            self.swap_btn.setText(language_manager.tr("Swap deaktivieren"))

        # Swap-aktivieren-Button: nur sinnvoll wenn Swap aktuell aus ist
        self.swap_on_btn.setEnabled(not info.swap_enabled)
        if info.swap_enabled:
            self.swap_on_btn.setText(language_manager.tr("Swap bereits aktiv"))
        else:
            self.swap_on_btn.setText(language_manager.tr("Swap aktivieren"))

    # --------------------------------------------------------------------- #
    #  Hilfsfunktionen                                                       #
    # --------------------------------------------------------------------- #

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
            self._append_log("btop konnte nicht gestartet werden.")

    def _launch_klogs(self) -> None:
        if not launch_command_in_terminal(build_klog_command()):
            self._append_log("Kernel-Logs konnten nicht gestartet werden.")

    def _launch_mcelog(self) -> None:
        if not launch_command_in_terminal(build_mcelog_command()):
            self._append_log("MCE-Logs konnten nicht gestartet werden.")

    # --------------------------------------------------------------------- #
    #  Swap-Verwaltung                                                       #
    # --------------------------------------------------------------------- #

    def _try_disable_swap_blocking(self) -> bool:
        """Versucht Swap synchron zu deaktivieren. Gibt True bei Erfolg zurueck."""
        safe, msg = check_swapoff_safe(self._system_info)
        if not safe:
            self._append_log(f"\u26a0 {msg}")
            return False

        password: Optional[str] = None
        if needs_password_for_swap():
            pw, ok = QInputDialog.getText(
                self,
                "Swap deaktivieren \u2013 Passwort erforderlich",
                "sudo-Passwort eingeben:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not pw:
                self._append_log("Swap-Deaktivierung abgebrochen (kein Passwort).")
                return False
            password = pw

        try:
            disable_swap(password=password)
            return True
        except Exception as exc:
            self._append_log(f"Swap-Deaktivierung fehlgeschlagen: {exc}")
            return False

    def _disable_swap(self) -> None:
        """Manueller Swap-Button: deaktiviert Swap im Hintergrund-Thread."""
        self._refresh_system_info(update_memory_value=False)

        safe, msg = check_swapoff_safe(self._system_info)
        if not safe:
            self._append_log(f"\u26a0 {msg}")
            QMessageBox.warning(self, "Swap-Deaktivierung nicht sicher", msg)
            return

        password: Optional[str] = None
        if needs_password_for_swap():
            pw, ok = QInputDialog.getText(
                self,
                "Swap deaktivieren \u2013 Passwort erforderlich",
                "sudo-Passwort eingeben:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not pw:
                self._append_log("Swap-Deaktivierung abgebrochen (kein Passwort).")
                return
            password = pw

        self.swap_btn.setEnabled(False)
        self.swap_btn.setText(language_manager.tr("Swap wird deaktiviert …"))
        self._append_log("Swap-Deaktivierung laeuft (Hintergrund-Thread) \u2026")

        worker = _SwapWorker(password=password, parent=self)
        self._swap_worker = worker
        worker.finished_ok.connect(self._on_swap_done)
        worker.failed.connect(self._on_swap_failed)
        worker.start()

    def _on_swap_done(self) -> None:
        self._append_log("\u2713 Swap erfolgreich deaktiviert.")
        self._refresh_system_info(update_memory_value=False)
        self._swap_worker = None

    def _on_swap_failed(self, error_msg: str) -> None:
        self._append_log(f"Swap-Deaktivierung fehlgeschlagen: {error_msg}")
        self._refresh_system_info(update_memory_value=False)
        QMessageBox.warning(
            self,
            "Swap-Deaktivierung",
            f"Swap konnte nicht deaktiviert werden:\n{error_msg}\n\n"
            "Manuell ausfuehren: sudo swapoff -a",
        )
        self._swap_worker = None

    def _enable_swap(self) -> None:
        """Manueller Button: aktiviert Swap im Hintergrund-Thread wieder."""
        password: Optional[str] = None
        if needs_password_for_swap():
            pw, ok = QInputDialog.getText(
                self,
                "Swap aktivieren \u2013 Passwort erforderlich",
                "sudo-Passwort eingeben:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not pw:
                self._append_log("Swap-Aktivierung abgebrochen (kein Passwort).")
                return
            password = pw

        self.swap_on_btn.setEnabled(False)
        self.swap_on_btn.setText(language_manager.tr("Swap wird aktiviert …"))
        self._append_log("Swap-Aktivierung laeuft (Hintergrund-Thread) \u2026")

        worker = _SwapOnWorker(password=password, parent=self)
        self._swap_on_worker = worker
        worker.finished_ok.connect(self._on_swap_on_done)
        worker.failed.connect(self._on_swap_on_failed)
        worker.start()

    def _on_swap_on_done(self) -> None:
        self._append_log("\u2713 Swap erfolgreich aktiviert.")
        self._refresh_system_info(update_memory_value=False)
        self._swap_on_worker = None

    def _on_swap_on_failed(self, error_msg: str) -> None:
        self._append_log(f"Swap-Aktivierung fehlgeschlagen: {error_msg}")
        self._refresh_system_info(update_memory_value=False)
        QMessageBox.warning(
            self,
            "Swap-Aktivierung",
            f"Swap konnte nicht aktiviert werden:\n{error_msg}\n\n"
            "Manuell ausfuehren: sudo swapon -a",
        )
        self._swap_on_worker = None
