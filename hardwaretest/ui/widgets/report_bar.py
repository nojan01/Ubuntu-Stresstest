"""Gemeinsame Berichtszeile für Test-Tabs: schreibt den Bericht und bietet „Bericht öffnen“ an."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from hardwaretest.core.report import STATUS_CANCELLED, STATUS_FAILED, STATUS_PASSED, Report, write_report
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.report_browser import open_folder, open_html_report


class ReportBar(QWidget):
    """Statuszeile mit Pfad des letzten Berichts sowie Knöpfen „Bericht öffnen“ / „Berichtsordner“."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.last_path: Optional[Path] = None
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.open_btn = QPushButton()
        self.folder_btn = QPushButton()
        self.open_btn.setEnabled(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.open_btn)
        layout.addWidget(self.folder_btn)
        self.open_btn.clicked.connect(self.open_report)
        self.folder_btn.clicked.connect(self.open_folder)
        language_manager.language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.open_btn.setText(language_manager.tr("Bericht öffnen"))
        self.folder_btn.setText(language_manager.tr("Berichtsordner"))
        self.folder_btn.setToolTip(language_manager.tr("Öffnet ~/Downloads mit allen hwtest-Berichten"))
        if self.last_path is None:
            self.label.setText(language_manager.tr("Noch kein Bericht – wird nach Testende in ~/Downloads geschrieben."))
        else:
            self.label.setText(language_manager.tr("Bericht: {path}", path=self.last_path))

    def publish(self, report: Report) -> Optional[Path]:
        """Schreibt den Bericht (Sprache der Oberfläche) und zeigt den Pfad an."""
        report.language = language_manager.language
        try:
            self.last_path = write_report(report)
        except OSError as exc:
            self.last_path = None
            self.label.setText(language_manager.tr("Bericht konnte nicht geschrieben werden: {error}", error=exc))
            self.open_btn.setEnabled(False)
            return None
        self.open_btn.setEnabled(True)
        self.retranslate()
        return self.last_path

    def set_path(self, path: Optional[Path]) -> None:
        self.last_path = Path(path) if path else None
        self.open_btn.setEnabled(self.last_path is not None)
        self.retranslate()

    def open_report(self) -> None:
        if self.last_path is not None:
            open_html_report(self.last_path, self)

    def open_folder(self) -> None:
        open_folder(Path.home() / "Downloads", self)


def runner_report(test: str, title: str, result, *, started: Optional[datetime], parameters: Sequence[tuple[str, str]],
                  log_text: str = "", cancelled: bool = False) -> Report:
    """Bericht für Tabs, die einen ``BaseTestRunner`` mit ``TestResult`` verwenden."""
    tr = language_manager.tr
    if result is None:
        status, summary = STATUS_CANCELLED, tr("Kein Ergebnis – Test wurde vorzeitig beendet.")
    elif cancelled:
        status = STATUS_FAILED if result.errors else STATUS_CANCELLED
        summary = tr("Manuell gestoppt nach {seconds:.0f} s.", seconds=result.duration_actual)
    elif result.passed:
        status, summary = STATUS_PASSED, tr("Keine Fehler gefunden ({seconds:.0f} s).", seconds=result.duration_actual)
    else:
        status, summary = STATUS_FAILED, tr("{count} Fehler gefunden.", count=len(result.errors))
    report = Report(test=test, title=title, status=status, summary=summary, started=started,
                    finished=datetime.now(), parameters=list(parameters))
    if result is not None:
        report.add_section(tr("Ergebnis"), "pairs", [
            (tr("Tatsächliche Dauer"), f"{result.duration_actual:.0f} s"),
            (tr("Exit-Code"), "–" if result.exit_code is None else str(result.exit_code)),
            (tr("Fehler"), str(len(result.errors))),
            (tr("Warnungen"), str(len(result.warnings))),
        ])
        if result.errors:
            report.add_section(tr("Fehler"), "list", result.errors)
        if result.warnings:
            report.add_section(tr("Warnungen"), "list", result.warnings)
    report.raw_output = log_text
    return report
