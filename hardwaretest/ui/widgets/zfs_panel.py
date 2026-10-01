"""ZFS pool status and explicit scrub control with background progress polling."""

import csv
from contextlib import suppress
from datetime import datetime
from html import escape
import os
from pathlib import Path
import shutil
import uuid

from PySide6.QtCore import QObject, QProcess, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from hardwaretest.core.zfs import list_pools, read_status, worker_command
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.report_browser import open_html_report


class ZfsWorker(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn())
        except Exception as exc:
            self.error.emit(str(exc))


class ZfsPanel(QWidget):
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.can_start = lambda: True
        self._worker = self._process = self._pool = self._last = None
        self._monitoring = False
        self._html = self._csv_file = self._base = None
        self._initial = ""
        self._samples = self._waits = 0
        self._report_language = "de"
        self._labels = []
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.request_status)
        layout = QVBoxLayout(self)
        hint = QLabel()
        hint.setWordWrap(True)
        self.bind(hint,
                  "ZFS prüft Speicherpools, nicht einzelne Partitionen. Ein Scrub liest Daten und prüft die Prüfsummen; "
                  "mit redundanten Kopien kann ZFS Schäden automatisch korrigieren. Der Pool kann eingehängt bleiben. "
                  "Es werden nur bereits importierte Pools angeboten.",
                  "ZFS checks storage pools, not individual partitions. A scrub reads data and verifies checksums; "
                  "with redundant copies ZFS can automatically repair damage. The pool may stay mounted. "
                  "Only already imported pools are offered.")
        layout.addWidget(hint)
        self.pools = QComboBox()
        self.pools.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pools.setMinimumContentsLength(30)
        layout.addWidget(self.pools)
        self.directory = QLineEdit(str(Path.home() / "Hardwaretest-Berichte"))
        self.choose = QPushButton()
        self.bind(self.choose, "Protokollordner …", "Report folder …")
        row = QHBoxLayout()
        row.addWidget(self.directory, 1)
        row.addWidget(self.choose)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.refresh = QPushButton()
        self.read = QPushButton()
        self.start_btn = QPushButton()
        self.stop_btn = QPushButton()
        for button, de, en in (
            (self.refresh, "ZFS-Pools suchen", "Scan ZFS pools"),
            (self.read, "Pool-Status lesen", "Read pool status"),
            (self.start_btn, "Scrub starten / fortsetzen …", "Start / resume scrub …"),
            (self.stop_btn, "Scrub stoppen …", "Stop scrub …"),
        ):
            self.bind(button, de, en)
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.detach = QPushButton()
        self.open_btn = QPushButton()
        self.bind(self.detach, "Nur Überwachung beenden", "Stop monitoring only")
        self.bind(self.open_btn, "HTML-Protokoll öffnen", "Open HTML report")
        self.detach.setToolTip(self.text("Der Scrub läuft im ZFS-Kernel weiter.", "The scrub continues in the ZFS kernel."))
        row.addWidget(self.detach)
        row.addWidget(self.open_btn)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(1500)
        layout.addWidget(self.details, 1)
        self.refresh.clicked.connect(self.scan)
        self.read.clicked.connect(self.request_status)
        self.start_btn.clicked.connect(lambda: self.control("start"))
        self.stop_btn.clicked.connect(lambda: self.control("stop"))
        self.detach.clicked.connect(lambda: self.finish_monitoring(self.text(
            "Überwachung beendet. Der Scrub wurde nicht gestoppt; noch kein abschließendes Testergebnis.",
            "Monitoring ended. The scrub was not stopped; no final test result yet.")))
        self.open_btn.clicked.connect(lambda: open_html_report(self._html, self) if self._html else None)
        self.choose.clicked.connect(self.choose_directory)
        self.pools.currentIndexChanged.connect(self.selection)
        language_manager.language_changed.connect(self.retranslate)
        for obj in [self, *self.findChildren(QObject)]:
            obj._hardwaretest_skip_tree = True
        self.retranslate()
        self.status.setText(self.text("Bitte ZFS-Pools suchen. Benötigt zfsutils-linux und ZFS-Kernelunterstützung.",
                                      "Scan ZFS pools. Requires zfsutils-linux and ZFS kernel support."))
        self.update_controls()

    def text(self, de, en):
        return en if language_manager.language == "en" else de

    def bind(self, widget, de, en):
        self._labels.append((widget, de, en))

    def retranslate(self, *_):
        for widget, de, en in self._labels:
            widget.setText(self.text(de, en))
        self.detach.setToolTip(self.text("Der Scrub läuft im ZFS-Kernel weiter.", "The scrub continues in the ZFS kernel."))

    def choose_directory(self):
        folder = QFileDialog.getExistingDirectory(self, self.choose.text(), self.directory.text())
        if folder:
            self.directory.setText(folder)

    def is_busy(self):
        return self._worker is not None or self._process is not None or self._monitoring

    def update_controls(self):
        querying = self._worker is not None or self._process is not None
        selected = self.pools.currentData() is not None
        for widget in (self.refresh, self.pools, self.choose, self.directory):
            widget.setEnabled(not self.is_busy())
        self.read.setEnabled(selected and not querying)
        active = self._last and self._last.scan_result in {"running", "resilver"}
        self.start_btn.setEnabled(selected and not self.is_busy() and not active)
        # A paused scrub can be resumed explicitly after ending monitoring.
        self.stop_btn.setEnabled(bool(self._last and self._last.scan_result in {"running", "paused"}) and not querying)
        self.detach.setEnabled(self._monitoring and not querying)
        self.open_btn.setEnabled(self._html is not None)

    def selection(self, *_):
        self._last = None
        self._pool = self.pools.currentData()
        self._initial = ""
        self._samples = 0
        self.update_controls()

    def run_worker(self, fn, callback):
        if self._worker is not None or self._process is not None:
            return
        worker = ZfsWorker(fn, self)
        self._worker = worker
        worker.result.connect(callback)
        worker.error.connect(self.failed)
        worker.finished.connect(self.worker_finished)
        self.update_controls()
        worker.start()

    def worker_finished(self):
        self._worker.deleteLater()
        self._worker = None
        self.busy_changed.emit(self._monitoring or self._process is not None)
        self.update_controls()
        if self._monitoring and not self._timer.isActive():
            self._timer.start()

    def scan(self):
        if self.is_busy():
            return
        self.status.setText(self.text("ZFS-Pools werden gesucht …", "Scanning ZFS pools …"))
        self.run_worker(list_pools, self.scanned)

    def scanned(self, pools):
        self.pools.clear()
        for pool in pools:
            self.pools.addItem(f"{pool.name} | {pool.health} | {pool.size / (1024**3):.1f} GiB | GUID {pool.guid}", pool)
        self.status.setText(self.text(f"{len(pools)} importierte Pools gefunden.", f"{len(pools)} imported pools found."))
        if not pools:
            self.details.setPlainText(self.text(
                "Keine importierten ZFS-Pools. Eine zfs_member-Signatur allein ist kein aktiver Pool. Die App importiert keine Laufwerke.",
                "No imported ZFS pools. A zfs_member signature alone is not an active pool. The app does not import drives."))

    def request_status(self):
        pool = self._pool or self.pools.currentData()
        if pool is not None:
            self.run_worker(lambda: read_status(pool), self.show_status)

    def show_status(self, status):
        self._last = status
        self.details.setPlainText(status.raw)
        self.progress.setRange(0, 100 if status.progress is not None else (0 if status.scan_result in {"running", "resilver"} else 100))
        self.progress.setValue(min(99, max(0, int(status.progress or 0))))
        self.status.setText(f"{self._pool.name}: {status.state}\n{status.scan}")
        if not self._monitoring:
            if status.scan_result in {"running", "paused"}:
                self.begin_monitoring(status.raw)
            else:
                self.save_report(self.text("Pool-Status (kein neuer Scrub durchgeführt)", "Pool status (no new scrub performed)"), status.raw)
            return
        self._samples += 1
        try:
            if self._csv_file:
                self._csv.writerow([datetime.now().astimezone().isoformat(), self._pool.name,
                                    status.state, status.scan_result, status.progress, status.has_errors])
                self._csv_file.flush()
        except OSError as exc:
            self.failed(str(exc))
            return
        if status.scan_result in {"running", "paused"}:
            self._waits = 0
            return
        if status.scan_result in {"passed", "failed", "cancelled"} and status.scan != self._baseline_scan:
            if status.scan_result == "cancelled":
                message = self.text("Scrub abgebrochen – kein vollständiges Prüfergebnis.", "Scrub cancelled – no complete test result.")
            elif status.has_errors:
                message = self.text("Scrub abgeschlossen – Fehler/Warnungen vorhanden. Pool-Status prüfen.",
                                    "Scrub completed – errors/warnings present. Check pool status.")
            else:
                message = self.text("Scrub abgeschlossen – keine verbleibenden Fehler gemeldet. Siehe Reparaturmenge im Protokoll.",
                                    "Scrub completed – no remaining errors reported. See repaired amount in log.")
                self.progress.setValue(100)
            self.finish_monitoring(message)
        else:
            self._waits += 1
            if self._waits >= 3:
                self.finish_monitoring(self.text("Kein eindeutig neues Scrub-Ergebnis ermittelt. Pool-Status prüfen.",
                                                 "No unambiguous new scrub result detected. Check pool status."))

    def begin_monitoring(self, initial):
        self._initial = initial
        self._baseline_scan = self._last.scan if self._last else ""
        self._samples = self._waits = 0
        self._report_language = language_manager.language
        try:
            self._base = self.new_base()
            self._csv_file = self._base.with_suffix(".csv").open("x", encoding="utf-8", newline="")
            self._csv = csv.writer(self._csv_file)
            self._csv.writerow(["timestamp", "pool", "state", "scan", "percent", "has_errors"])
        except OSError as exc:
            if self._csv_file:
                with suppress(OSError):
                    self._csv_file.close()
                self._csv_file = None
            self.failed(str(exc))
            return False
        self._monitoring = True
        self.busy_changed.emit(True)
        self._timer.start()
        self.update_controls()
        return True

    def control(self, action):
        if self._worker is not None or self._process is not None:
            return
        pool = self._pool or self.pools.currentData()
        if pool is None or (action == "start" and not self.can_start()):
            self.status.setText(self.text("Zuerst andere Tests beenden.", "Stop other tests first."))
            return
        prompt = (self.text(f"Pool {pool.name}: Scrub starten? Prüft Daten und Prüfsummen, kann Stunden dauern und erhöht die Laufwerkslast. "
                            "Mit redundanten Kopien kann ZFS beschädigte Daten automatisch reparieren. Ein aktuelles Backup wird empfohlen.",
                            f"Pool {pool.name}: start scrub? Verifies data/checksums, may take hours and increases disk load. "
                            "With redundant copies ZFS may automatically repair damaged data. A current backup is recommended.")
                  if action == "start" else self.text(f"Den Scrub von Pool {pool.name} stoppen? Der aktuelle Lauf wird nicht vollständig geprüft.",
                                                       f"Stop the scrub of pool {pool.name}? This run will not provide a full check."))
        if QMessageBox.question(self, "ZFS", prompt, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        command = worker_command(action, pool.guid)
        if os.geteuid() != 0:
            if not shutil.which("pkexec"):
                self.failed("pkexec missing / pkexec fehlt.")
                return
            command.insert(0, "pkexec")
        self._pool = pool
        if action == "start" and not self.begin_monitoring(self._last.raw if self._last else ""):
            return
        self._action = action
        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._command_output = ""
        process.readyReadStandardOutput.connect(self.control_output)
        process.finished.connect(self.control_finished)
        process.errorOccurred.connect(self.control_error)
        self.status.setText(self.text("Administratoranmeldung / ZFS-Aktion läuft …", "Administrator authentication / ZFS action running …"))
        self.update_controls()
        process.start(command[0], command[1:])

    def control_output(self):
        if self._process:
            self._command_output += bytes(self._process.readAllStandardOutput()).decode(errors="replace")

    def control_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._command_output += self._process.errorString()
            self.control_finished(1)

    def control_finished(self, code, exit_status=None):
        if self._process is None:
            return
        self.control_output()
        if exit_status == QProcess.ExitStatus.CrashExit:
            code = 1
        self._process.deleteLater()
        self._process = None
        if code:
            self.failed(self._command_output or f"ZFS exit {code}")
        else:
            self.status.setText(self.text("ZFS-Aktion angenommen; Status wird gelesen …", "ZFS action accepted; reading status …"))
            self.request_status()
        self.update_controls()

    def failed(self, message):
        if self._monitoring:
            self.finish_monitoring(self.text("Überwachung unvollständig: ", "Monitoring incomplete: ") + message)
        else:
            self.status.setText(message)

    def finish_monitoring(self, message):
        self._timer.stop()
        if self._csv_file:
            try:
                self._csv_file.close()
            except OSError as exc:
                message += self.text("\nCSV unvollständig: ", "\nCSV incomplete: ") + str(exc)
            self._csv_file = None
        if self._monitoring:
            self._monitoring = False
            self.busy_changed.emit(False)
            self.save_report(message, self._last.raw if self._last else "", self._base)
        self.status.setText(message + (f"\n{self._html}" if self._html else ""))
        self.update_controls()

    def new_base(self):
        directory = Path(self.directory.text()).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"zfs-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"

    def save_report(self, summary, raw, base=None):
        try:
            base = base or self.new_base()
            report = f"Hardwaretest ZFS | {datetime.now().astimezone().isoformat()}\n{summary}\n"
            if self._pool:
                report += f"Pool: {self._pool.name} | GUID: {self._pool.guid}\n"
            report += f"{self._samples} samples\n\n{self._initial[:24000]}\n\n{raw[:24000]}\n"
            base.with_suffix(".txt").write_text(report, encoding="utf-8")
            self._html = base.with_suffix(".html")
            self._html.write_text('<!doctype html><meta charset="utf-8"><title>Hardwaretest ZFS</title>'
                                  '<h1>Hardwaretest ZFS</h1><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'
                                  + escape(report) + '</pre>', encoding="utf-8")
            self.open_btn.setEnabled(True)
        except OSError as exc:
            self._html = None
            self.open_btn.setEnabled(False)
            self.details.appendPlainText(self.text("Protokollfehler: ", "Report error: ") + str(exc))
