"""Explicit offline checks and conservative repairs, outside unattended test plans."""

from datetime import datetime
from html import escape
import os
from pathlib import Path
import shutil
import uuid

from PySide6.QtCore import QObject, QProcess, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from hardwaretest.core.filesystem_check import list_filesystems, result_text, worker_command
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.report_browser import open_html_report


class ScanWorker(QThread):
    result = Signal(object)
    error = Signal(str)

    def run(self):
        try:
            self.result.emit(list_filesystems())
        except Exception as exc:
            self.error.emit(str(exc))


class FilesystemPanel(QWidget):
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan = None
        self._process = None
        self._report = None
        self._html = None
        self._action = "check"
        self._language = "de"
        self.can_start = lambda: True
        self._labels = []
        layout = QVBoxLayout(self)
        hint = QLabel()
        hint.setWordWrap(True)
        self.bind(hint,
                  "Eingehängte Dateisysteme: Statusdiagnose mit Einhängezustand, Speicherplatz und verfügbaren Fehlerhinweisen. "
                  "Vollständige Strukturprüfung und Reparatur für ext2/ext3/ext4 nur ausgehängt; für die Systempartition ein Live-USB verwenden.",
                  "Mounted filesystems: status diagnosis with mount state, free space and available error indications. "
                  "Full integrity check and repair for ext2/ext3/ext4 require an unmounted filesystem; use a live USB for the system partition.")
        layout.addWidget(hint)
        self.devices = QComboBox()
        self.devices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.devices.setMinimumContentsLength(30)
        layout.addWidget(self.devices)
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.output_dir = QLineEdit(str(Path.home() / "Hardwaretest-Berichte"))
        self.choose = QPushButton()
        self.bind(self.choose, "Protokollordner …", "Report folder …")
        row = QHBoxLayout()
        row.addWidget(self.output_dir)
        row.addWidget(self.choose)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.refresh = QPushButton()
        self.check = QPushButton()
        self.repair = QPushButton()
        self.online = QPushButton()
        for button, de, en in (
            (self.refresh, "Laufwerke aktualisieren", "Refresh drives"),
            (self.online, "Statusdiagnose (eingehängt)", "Status diagnosis (mounted)"),
            (self.check, "Struktur prüfen (offline)", "Check integrity (offline)"),
            (self.repair, "Reparieren …", "Repair …"),
        ):
            self.bind(button, de, en)
            row.addWidget(button)
        layout.addLayout(row)
        self.open_html = QPushButton()
        self.bind(self.open_html, "HTML-Protokoll öffnen", "Open HTML report")
        self.open_html.setEnabled(False)
        self.open_html.clicked.connect(lambda: open_html_report(self._html, self) if self._html else None)
        layout.addWidget(self.open_html)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        layout.addWidget(self.log)
        self.refresh.clicked.connect(self.scan)
        self.devices.currentIndexChanged.connect(self.selection)
        self.choose.clicked.connect(self.choose_directory)
        self.check.clicked.connect(lambda: self.start("check"))
        self.online.clicked.connect(lambda: self.start("status"))
        self.repair.clicked.connect(lambda: self.start("repair"))
        language_manager.language_changed.connect(self.retranslate)
        for obj in [self, *self.findChildren(QObject)]:
            obj._hardwaretest_skip_tree = True
        self.retranslate()
        self.selection()

    def text(self, de, en):
        return en if language_manager.language == "en" else de

    def bind(self, widget, de, en):
        self._labels.append((widget, de, en))

    def retranslate(self, *_):
        for widget, de, en in self._labels:
            widget.setText(self.text(de, en))
        self.selection()

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self, self.choose.text(), self.output_dir.text())
        if directory:
            self.output_dir.setText(directory)

    def is_busy(self):
        return self._process is not None or self._scan is not None

    def scan(self):
        if self.is_busy():
            return
        self._scan = ScanWorker(self)
        self._scan.result.connect(self.scanned)
        self._scan.error.connect(self.status.setText)
        self._scan.finished.connect(self.scan_finished)
        self.status.setText(self.text("Suche läuft …", "Scanning …"))
        self.busy_changed.emit(True)
        self.selection()
        self._scan.start()

    def scan_finished(self):
        self._scan.deleteLater()
        self._scan = None
        self.busy_changed.emit(False)
        self.selection()

    def scanned(self, devices):
        self.devices.clear()
        for device in devices:
            mounts = ", ".join(device.mounts) or "–"
            self.devices.addItem(f"{device.path} | {device.fstype} | UUID: {device.uuid or '–'} | {mounts}", device)
        # Start with a useful mounted filesystem rather than a stale disk-level
        # signature or an unsupported parent device reported by lsblk.
        selected = next((i for i, d in enumerate(devices) if "/" in d.mounts), None)
        if selected is None:
            selected = next((i for i, d in enumerate(devices) if d.mounts), 0)
        if devices:
            self.devices.setCurrentIndex(selected)
        self.status.setText(self.text(f"{len(devices)} Dateisysteme gefunden.", f"{len(devices)} filesystems found."))

    def selection(self, *_):
        device = self.devices.currentData()
        self.devices.setToolTip(self.devices.currentText())
        busy = self.is_busy()
        allowed = device is not None and device.supported and not device.mounts and not device.children
        for widget in (self.devices, self.refresh, self.output_dir, self.choose):
            widget.setEnabled(not busy)
        self.check.setEnabled(allowed and not busy)
        self.repair.setEnabled(allowed and not busy)
        self.online.setEnabled(device is not None and bool(device.mounts) and not busy)
        if device is None:
            message = self.text("Bitte Laufwerke aktualisieren und ein Dateisystem auswählen.", "Refresh drives and select a filesystem.")
        elif device.mounts:
            message = self.text("Eingehängt: Statusdiagnose verfügbar. Für Strukturprüfung/Reparatur aushängen oder Live-USB verwenden.",
                                "Mounted: status diagnosis available. Unmount or use a live USB for integrity check/repair.")
        elif not device.supported:
            message = self.text("Dieses Dateisystem wird noch nicht unterstützt. Keine Reparatur verfügbar.", "This filesystem is not supported yet. Repair unavailable.")
        elif not allowed:
            message = self.text("Eingehängt/in Benutzung – Prüfung und Reparatur gesperrt. Live-USB verwenden.", "Mounted/in use – check and repair blocked. Use a live USB.")
        else:
            message = self.text("Offline-Kandidat. Identität und Einhängezustand werden vor der Aktion erneut geprüft.", "Offline candidate. Device identity and mount state will be checked again before the operation.")
        self.reason.setText(message)

    def start(self, action):
        if self.is_busy() or not self.can_start():
            self.status.setText(self.text("Zuerst die anderen Tests beenden.", "Stop the other tests first."))
            return
        device = self.devices.currentData()
        if not device:
            return
        if action == "status" and not device.mounts:
            return
        if action != "status" and (not device.supported or device.mounts or device.children):
            return
        if action == "repair":
            answer = QMessageBox.warning(
                self, self.text("Reparatur bestätigen", "Confirm repair"),
                self.text(f"{device.path} ({device.fstype}, UUID {device.uuid})\n\n"
                          "Eine Reparatur verändert Dateisystem-Metadaten und kann beschädigte Daten entfernen. "
                          "Vorher ein Backup anlegen; bei Hardwaredefekten zuerst ein Abbild erstellen. "
                          "Es werden nur automatisch sicher behebbare Fehler korrigiert (e2fsck -p), keine erzwungene Ja-Antwort. Fortfahren?",
                          f"{device.path} ({device.fstype}, UUID {device.uuid})\n\n"
                          "Repair changes filesystem metadata and may remove damaged data. Back up first; "
                          "if hardware is failing, create an image first. Only problems e2fsck -p considers "
                          "safe to fix automatically will be repaired; no forced yes. Continue?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        command = worker_command(action, device, language_manager.language)
        if os.geteuid() != 0:
            if not shutil.which("pkexec"):
                self.status.setText(self.text("pkexec fehlt; Administratoranmeldung nicht verfügbar.", "pkexec missing; administrator authentication unavailable."))
                return
            command.insert(0, "pkexec")
        try:
            directory = Path(self.output_dir.text()).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            self._report_path = directory / f"filesystem-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.txt"
            self._report = self._report_path.open("x", encoding="utf-8")
        except OSError as exc:
            self.status.setText(str(exc))
            return
        self._action = action
        self._html = None
        self.open_html.setEnabled(False)
        self._language = language_manager.language
        self.log.clear()
        self._write(f"{datetime.now().astimezone().isoformat()}\n{action}: {device.path}\nUUID: {device.uuid}\n")
        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.finished)
        process.errorOccurred.connect(self.process_error)
        self.status.setText(self.text("Aktion läuft / Administratoranmeldung … Nicht aushängen, einhängen oder ausschalten.", "Running / administrator authentication … Do not unmount, mount or power off."))
        self.progress.setRange(0, 0)
        self.busy_changed.emit(True)
        self.selection()
        process.start(command[0], command[1:])

    def _write(self, text):
        self.log.insertPlainText(text)
        if self._report is not None:
            try:
                self._report.write(text)
                self._report.flush()
            except OSError as exc:
                # Never kill a repair just because its log disk is full.
                self._report.close()
                self._report = None
                self.log.appendPlainText(f"Report write error / Protokollfehler: {exc}")

    def read_output(self):
        if self._process is not None:
            self._write(bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace"))

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._write(self._process.errorString() + "\n")
            self.finished(8)

    def finished(self, code, exit_status=None):
        if self._process is None:
            return
        self.read_output()
        if exit_status == QProcess.ExitStatus.CrashExit:
            code = 8
        summary = result_text(code, self._action, self._language)
        self._write("\n" + summary + "\n")
        report_ok = self._report is not None
        if self._report:
            self._report.close()
            self._report = None
        if report_ok:
            # Keep HTML bounded even if e2fsck emitted a huge diagnostic log.
            try:
                with self._report_path.open(encoding="utf-8") as source:
                    excerpt = source.read(64_000)
                html = '<!doctype html><meta charset="utf-8"><title>Filesystem</title><h1>' + escape(summary) + '</h1><pre style="white-space:pre-wrap">' + escape(excerpt) + '</pre><p>Details: ' + escape(self._report_path.name) + '</p>'
                self._report_path.with_suffix(".html").write_text(html, encoding="utf-8")
                self._html = self._report_path.with_suffix(".html")
                self.open_html.setEnabled(True)
                summary += f"\n{self._report_path}"
            except OSError as exc:
                summary += f"\nReport error / Protokollfehler: {exc}"
        else:
            summary += self.text("\nProtokoll unvollständig: Schreibfehler.", "\nReport incomplete: write error.")
        self.status.setText(summary)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if code in (0, 1, 2, 3) else 0)
        self._process.deleteLater()
        self._process = None
        self.busy_changed.emit(False)
        self.selection()
