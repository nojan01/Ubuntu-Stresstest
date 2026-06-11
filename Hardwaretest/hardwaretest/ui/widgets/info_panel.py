"""Information tab to show system stats and run lshw commands."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shlex
import re
import shutil
import subprocess
from typing import Callable, Optional

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.system_info import SystemInfo, read_system_info
from hardwaretest.ui.report_html import json_to_html_report
from hardwaretest.ui.utils import launch_command_in_terminal


SystemInfoProvider = Callable[[], SystemInfo]


class InfoPanel(QWidget):
    def __init__(
        self,
        system_info_provider: SystemInfoProvider = read_system_info,
        parent: Optional[QWidget] = None,
        compact_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.system_info_provider = system_info_provider

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        summary_min_height = 70 if not compact_mode else 55
        summary_max_height = 120 if not compact_mode else 95
        self.summary.setMinimumHeight(summary_min_height)
        self.summary.setMaximumHeight(summary_max_height)

        self.fastfetch_view = QTextEdit()
        self.fastfetch_view.setReadOnly(True)
        fastfetch_min_height = 260 if not compact_mode else 190
        self.fastfetch_view.setMinimumHeight(fastfetch_min_height)
        self.fastfetch_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.refresh_btn = QPushButton("Systemdaten aktualisieren")
        self.refresh_btn.clicked.connect(self._update_summary)

        self.export_btn = QPushButton("Systemreport (JSON)")
        self.export_btn.clicked.connect(self._export_info)

        self.open_report_btn = QPushButton("Report öffnen")
        self.open_report_btn.clicked.connect(self._open_report_in_viewer)

        self.open_html_report_btn = QPushButton("Report als Webseite")
        self.open_html_report_btn.clicked.connect(self._open_html_report)
        self.last_report_path: Optional[Path] = None

        self.button_defs = [
            ("sudo lshw -class cpu", ["sudo", "lshw", "-class", "cpu"], True),
            ("sudo lshw -class memory", ["sudo", "lshw", "-class", "memory"], True),
            ("sudo lshw -class storage", ["sudo", "lshw", "-class", "storage"], True),
            ("sudo lshw -class disk", ["sudo", "lshw", "-class", "disk"], True),
            ("sudo lshw -class raid", ["sudo", "lshw", "-class", "raid"], True),
            ("sudo lshw -class scsi", ["sudo", "lshw", "-class", "scsi"], True),
            ("sudo lshw -class display", ["sudo", "lshw", "-class", "display"], True),
            ("sudo lshw -class network", ["sudo", "lshw", "-class", "network"], True),
            ("sudo lspci -vvv", ["sudo", "lspci", "-vvv"], True),
            ("sudo dmesg", ["sudo", "dmesg"], True),
        ]

        layout = QVBoxLayout()
        info_form = QFormLayout()
        info_form.addRow("Systemstatus", self.summary)
        info_form.addRow("fastfetch", self.fastfetch_view)
        layout.addLayout(info_form)
        layout.addSpacing(12)
        layout.addStretch(2)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.export_btn)
        layout.addWidget(self.open_report_btn)
        layout.addWidget(self.open_html_report_btn)
        for label, command, hold in self.button_defs:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, cmd=command, h=hold: self._run_command(cmd, h))
            layout.addWidget(btn)
        layout.addStretch(1)
        self.setLayout(layout)

        self._update_summary()

    def _update_summary(self) -> None:
        info = self.system_info_provider()
        lines = [
            f"RAM gesamt: {info.total_memory_bytes // (1024 * 1024)} MB",
            f"RAM verfuegbar: {info.available_memory_mb} MB",
            f"Swap aktiv: {'ja' if info.swap_enabled else 'nein'}",
            f"Kerne logisch: {info.cpu_cores}",
        ]
        if info.physical_cpu_cores:
            lines.append(f"Kerne physisch: {info.physical_cpu_cores}")
        self.summary.setPlainText("\n".join(lines))
        self._update_fastfetch_output()

    def _run_command(self, command: list[str], hold: bool) -> None:
        if not launch_command_in_terminal(command, hold=hold):
            self.summary.append(f"Konnte {' '.join(command)} nicht starten.")

    def _export_info(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = Path.home() / "Downloads" / f"hardwaretest_system_report_{timestamp}.json"
        self.last_report_path = target
        self._launch_report_script(target)

    def _launch_report_script(self, target: Path) -> None:
        script_path = Path(__file__).resolve().parents[3] / "scripts" / "collect_system_report.sh"
        if not script_path.exists():
            QMessageBox.critical(
                self,
                "Script fehlt",
                f"{script_path} wurde nicht gefunden. Bitte Installation prüfen.",
            )
            return
        command = ["sudo", str(script_path), str(target)]
        if not launch_command_in_terminal(command, hold=True):
            QMessageBox.critical(
                self,
                "Export fehlgeschlagen",
                "Terminal konnte nicht gestartet werden.",
            )
            return

    def _open_report_in_viewer(self) -> None:
        path = self._resolve_latest_report()
        if not path:
            QMessageBox.information(
                self,
                "Kein Report",
                "Es wurde noch kein Systemreport erzeugt.",
            )
            return
        viewer = self._find_json_viewer()
        if viewer is None:
            QMessageBox.critical(
                self,
                "Kein JSON-Reader",
                "Es wurde kein geeigneter JSON-Reader gefunden.",
            )
            return
        viewer_name = Path(viewer).name
        if viewer_name == "app":
            # JSON Viewer (Tauri-App) – bevorzugter grafischer JSON-Viewer
            if not QProcess.startDetached(viewer, [str(path)]):
                QMessageBox.critical(
                    self,
                    "Fehler",
                    "JSON Viewer konnte nicht gestartet werden.",
                )
            return
        if viewer_name == "jless":
            if not launch_command_in_terminal([viewer, str(path)], hold=False):
                QMessageBox.critical(
                    self,
                    "Fehler",
                    "jless konnte nicht gestartet werden.",
                )
            return
        if viewer_name == "fx":
            if not launch_command_in_terminal([viewer, str(path)], hold=True):
                QMessageBox.critical(
                    self,
                    "Fehler",
                    "fx konnte nicht gestartet werden.",
                )
            return
        if viewer_name == "jq":
            quoted = shlex.quote(str(path))
            command = [
                "bash",
                "-lc",
                f"jq . {quoted} | less -R",
            ]
            if not launch_command_in_terminal(command, hold=False):
                QMessageBox.critical(
                    self,
                    "Fehler",
                    "jq konnte nicht gestartet werden.",
                )
            return
        if not QProcess.startDetached(viewer, [str(path)]):
            QMessageBox.critical(
                self,
                "Fehler",
                f"Report konnte nicht geöffnet werden:\n{path}",
            )

    def _open_html_report(self) -> None:
        """Generate an HTML report from the latest JSON report and open it in the browser."""
        json_path = self._resolve_latest_report()
        if not json_path:
            QMessageBox.information(
                self,
                "Kein Report",
                "Es wurde noch kein Systemreport erzeugt.\n"
                "Bitte zuerst einen Report über 'Systemreport (JSON)' erstellen.",
            )
            return
        try:
            html_path = json_to_html_report(json_path)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Fehler",
                f"HTML-Report konnte nicht generiert werden:\n{exc}",
            )
            return
        import webbrowser
        webbrowser.open(html_path.as_uri())

    def _resolve_latest_report(self) -> Optional[Path]:
        downloads = Path.home() / "Downloads"
        candidates = []
        if self.last_report_path:
            candidates.append(self.last_report_path)
        candidates.extend(sorted(downloads.glob("hardwaretest_system_report_*.json"), reverse=True))
        for entry in candidates:
            if entry.exists():
                return entry
        fallback = downloads / "hardwaretest_system_report.json"
        return fallback if fallback.exists() else None

    @staticmethod
    def _find_json_viewer() -> Optional[str]:
        candidates = [
            "app",
            "jless",
            "fx",
            "jq",
            "gnome-text-editor",
            "gedit",
            "xed",
            "kate",
            "code",
            "xdg-open",
        ]
        for binary in candidates:
            resolved = shutil.which(binary)
            if resolved:
                return resolved
        return None

    def _update_fastfetch_output(self) -> None:
        if shutil.which("fastfetch") is None:
            self.fastfetch_view.setPlainText(
                "fastfetch ist nicht installiert. sudo apt install fastfetch"
            )
            return
        commands = [["fastfetch", "--logo", "none", "--pipe"], ["fastfetch", "--logo", "none"]]
        result = None
        last_error: Optional[Exception] = None
        for cmd in commands:
            try:
                trial = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception as exc:  # pragma: no cover - depends on system
                last_error = exc
                continue
            if (trial.stdout and trial.stdout.strip()) or (trial.stderr and trial.stderr.strip()):
                result = trial
                break
        if result is None:
            if last_error is not None:
                self.fastfetch_view.setPlainText(f"fastfetch konnte nicht gestartet werden: {last_error}")
            else:
                self.fastfetch_view.setPlainText("fastfetch lieferte keine Ausgabe.")
            return
        output = (result.stdout or "").strip()
        if not output:
            output = (result.stderr or "").strip()
        if not output:
            output = "fastfetch lieferte keine Ausgabe."
        output = self._strip_ansi(output)
        self.fastfetch_view.setPlainText(output)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        ansi_pattern = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
        return ansi_pattern.sub("", text)

