"""Explicit, manual-only control of Intel's installed Xeon diagnostic."""

from __future__ import annotations

from datetime import datetime
import threading
import time

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget

from hardwaretest.core.report import (
    STATUS_CANCELLED, STATUS_FAILED, STATUS_PASSED, STATUS_SKIPPED, STATUS_WARNING, Report,
)
from hardwaretest.core.root_broker import RootBroker
from hardwaretest.tests.dcdiag import DcDiagRunner, available_dcdiag_path
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.report_bar import ReportBar
from hardwaretest.ui.widgets.temperature_widget import TemperatureWidget
from hardwaretest.ui.widgets.worker_lifecycle import connect_finished, release_worker


_OUTCOMES = {
    "passed": (STATUS_PASSED, "DCDiag: Keine Probleme erkannt."),
    "failed": (STATUS_FAILED, "DCDiag: Test fehlgeschlagen. Originalausgabe beachten."),
    "unsupported": (STATUS_SKIPPED, "DCDiag: Prozessor wird von dieser Version nicht unterstützt."),
    "inconclusive": (STATUS_WARNING, "DCDiag: Ergebnis nicht eindeutig. Originalausgabe und Systemlogs prüfen."),
    "unknown": (STATUS_WARNING, "DCDiag: Ergebnis unbekannt; Exit-Code allein wird nicht als Erfolg gewertet."),
    "cancelled": (STATUS_CANCELLED, "DCDiag: Abgebrochen; kein vollständiges Ergebnis."),
    "timeout": (STATUS_WARNING, "DCDiag: Sicherheitslimit erreicht; kein vollständiges Ergebnis."),
}


class _RunWorker(QThread):
    launched = Signal()

    def __init__(self, runner: DcDiagRunner, parent: QWidget) -> None:
        super().__init__(parent)
        self.runner = runner
        self.error = ""
        self.stop_requested = threading.Event()
        self.cancelled = False

    def run(self) -> None:
        try:
            self.runner.root_broker.start()
            if self.stop_requested.is_set():
                self.cancelled = True
                return
            self.runner.start()
            self.launched.emit()
            # Linux PDEATHSIG is tied to the spawning thread, not just the GUI
            # process. Keep that thread alive for the entire foreground test.
            self.runner.wait_finished()
        except Exception as exc:
            self.error = str(exc)


class DcDiagPanel(QWidget):
    log_signal = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner: DcDiagRunner | None = None
        self._worker: _RunWorker | None = None
        self._launching = False
        self._broker: RootBroker | None = None
        self._started_at: datetime | None = None
        self._started_clock = 0.0
        self._reported = False
        self.info = QLabel(
            "Intel Data Center Diagnostic Tool: manueller Vordergrundtest, etwa 45 Minuten "
            "mit hoher CPU-Auslastung. Sicherheitslimit: 90 Minuten. Nicht im Gesamttest. "
            "Xeon-Erkennung garantiert keine Unterstützung durch diese Tool-Version. "
            "Start über die Root-Sitzung; ein Dienst wird nicht aktiviert."
        )
        self.info.setWordWrap(True)
        self.start_btn = QPushButton("Intel DCDiag starten")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.result_label = QLabel("Bereit")
        self.result_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.temp_widget = TemperatureWidget()
        self.report_bar = ReportBar()
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(10000)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.info)
        layout.addWidget(self.temp_widget)
        layout.addLayout(buttons)
        layout.addWidget(self.result_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.report_bar)
        layout.addWidget(self.log_view)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_progress)
        self.start_btn.clicked.connect(self.start_test)
        self.stop_btn.clicked.connect(self.stop_test)
        self.log_signal.connect(self.log_view.append)

    def start_test(self) -> None:
        if self._worker is not None or (self.runner and self.runner.is_running()):
            return
        binary = available_dcdiag_path()
        if not binary:
            self.result_label.setText(language_manager.tr("DCDiag: Intel-Xeon-CPU oder Binary fehlt."))
            return
        self.close_privileged_session()
        self.log_view.clear()
        self._reported = False
        self._started_at = datetime.now()
        self._started_clock = time.monotonic()
        self.runner = DcDiagRunner(binary, log_fn=self.log_signal.emit)
        self._broker = RootBroker(reason=language_manager.tr("Intel DCDiag starten"))
        self.runner.root_broker = self._broker
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.result_label.setText(language_manager.tr("DCDiag: Autorisierung läuft; im Passwortdialog abbrechbar."))
        self.progress.setRange(0, 0)
        self._launching = True
        self._worker = _RunWorker(self.runner, self)
        self._worker.launched.connect(self._launched)
        connect_finished(self._worker, self._start_finished)
        self._worker.start()

    def _launched(self) -> None:
        self._launching = False
        if self._worker is not None and self._worker.stop_requested.is_set():
            self.stop_test()
            return
        self.temp_widget.start_monitoring()
        self.timer.start()
        self._update_progress()

    def _start_finished(self, worker: _RunWorker) -> None:
        error = worker.error
        cancelled = worker.cancelled
        release_worker(self, "_worker", worker)
        self._launching = False
        if error or cancelled:
            if error:
                self.log_view.append(error)
            self.result_label.setText(
                language_manager.tr("Fehler beim Start: {error}", error=error) if error
                else language_manager.tr(_OUTCOMES["cancelled"][1])
            )
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.progress.setRange(0, 1)
            self.close_privileged_session()
            return
        self._update_progress()

    def stop_test(self) -> None:
        # Do not race process creation while the authorization thread is active.
        if self._worker is not None and self._launching:
            self._worker.stop_requested.set()
            self.result_label.setText(language_manager.tr(
                "DCDiag: Stopp angefordert; laufenden Passwortdialog bitte abbrechen."
            ))
            return
        if self.runner and self.runner.is_running():
            self.runner.stop(aborted=True)
        self._update_progress()

    def _update_progress(self) -> None:
        if self.runner is None or self._launching:
            return
        if self.runner.is_running():
            seconds = int(time.monotonic() - self._started_clock)
            self.result_label.setText(language_manager.tr(
                "DCDiag läuft seit {minutes} min; etwa 45 min erwartet (keine Fortschrittsdaten).",
                minutes=seconds // 60,
            ))
            return
        if self._worker is not None:
            return
        result = self.runner.get_result()
        if result is None or self._reported:
            return
        self._reported = True
        self.timer.stop()
        self.temp_widget.stop_monitoring()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setRange(0, 1)
        status, text = _OUTCOMES[result.outcome]
        summary = language_manager.tr(text)
        summary += language_manager.tr(" Exit-Code: {code} (keine dokumentierte Exitcode-Tabelle).", code=result.exit_code)
        self.result_label.setText(summary)
        report = Report(
            test="intel-dcdiag", title="Intel Data Center Diagnostic Tool",
            status=status, summary=summary, started=self._started_at, finished=datetime.now(),
            parameters=[("Programm", self.runner.binary_path), ("Aufruf", "dcdiag"),
                        ("Timeout", f"{self.runner.params.duration_seconds} s")],
        )
        report.add_section(language_manager.tr("Ergebnis"), "pairs", [
            (language_manager.tr("Tatsächliche Dauer"), f"{result.duration_actual:.0f} s"),
            (language_manager.tr("Exit-Code"), str(result.exit_code)),
        ])
        if result.errors:
            report.add_section(language_manager.tr("Fehler"), "list", result.errors)
        if result.warnings:
            report.add_section(language_manager.tr("Warnungen"), "list", result.warnings)
        report.raw_output = self.log_view.toPlainText()
        self.report_bar.publish(report)
        self.close_privileged_session()

    def close_privileged_session(self) -> None:
        if self._broker is not None:
            self._broker.close()
            self._broker = None
