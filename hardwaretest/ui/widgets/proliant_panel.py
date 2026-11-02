"""ProLiant Info: iLO/BMC-Daten per ipmitool – Übersicht, Sensoren, Ereignisprotokoll, IML, Speicher/ECC, RAID."""

from __future__ import annotations

import os
import shutil
import subprocess

from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from hardwaretest.core import hpe_raid, ipmi, privilege
from hardwaretest.core.root_broker import RootAuthorizationError, RootBroker
from hardwaretest.core.system_info import read_edac_info
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.worker_lifecycle import connect_finished, release_worker

_CSS = {"ok": "color: #2e7d32; font-weight: bold;", "warn": "color: #e65100; font-weight: bold;",
        "error": "color: #c62828; font-weight: bold;", "info": "color: #757575; font-weight: bold;"}


def _t(de: str, en: str) -> str:
    return en if language_manager.language == "en" else de


def build_ecc_report(run) -> tuple[str, str, list[str]]:
    """Liefert (Zustand, Kurzfassung, Detailzeilen). ``run`` ist ein Root-Aufruf oder None."""
    lines: list[str] = []
    state = "ok"
    ecc_present = None

    lines.append(_t("== Verbauter Speicher (DMI) ==", "== Installed memory (DMI) =="))
    if run is None:
        lines.append(_t("Keine Root-Rechte – DMI nicht lesbar.", "No root privileges – DMI unavailable."))
    else:
        dmi, error = ipmi.read_dmi_memory(run)
        if dmi is None:
            lines.append(_t("dmidecode fehlgeschlagen: ", "dmidecode failed: ") + error)
        else:
            ecc_present = dmi.ecc
            lines.append(_t("Fehlerkorrektur: ", "Error correction: ") + (", ".join(dmi.error_correction) or "?"))
            for module in dmi.modules:
                lines.append(f"  {module.locator}: {module.size} {module.kind} {module.speed} "
                             f"{module.manufacturer} {module.part_number}")
            lines.append(_t(f"{len(dmi.modules)} Module bestückt.", f"{len(dmi.modules)} modules installed."))

    lines.append("")
    lines.append(_t("== Kernel-EDAC-Zähler ==", "== Kernel EDAC counters =="))
    edac = read_edac_info()
    if not edac.available or not edac.controllers:
        lines.append(_t("Kein EDAC-Speichercontroller geladen (bei HPE oft Firmware-First: Fehler nur im iLO).",
                        "No EDAC memory controller loaded (HPE often uses firmware-first: errors only in iLO)."))
    else:
        lines.append(_t(f"Controller: {', '.join(edac.controllers)} | korrigierbar: {edac.correctable_errors} | "
                        f"unkorrigierbar: {edac.uncorrectable_errors}",
                        f"Controllers: {', '.join(edac.controllers)} | correctable: {edac.correctable_errors} | "
                        f"uncorrectable: {edac.uncorrectable_errors}"))
        if edac.uncorrectable_errors:
            state = "error"
        elif edac.correctable_errors:
            state = "warn"

    lines.append("")
    lines.append(_t("== BMC-Ereignisprotokoll (IPMI, z. B. HPE iLO) ==", "== BMC event log (IPMI, e.g. HPE iLO) =="))
    sel_events = None
    if not shutil.which("ipmitool"):
        lines.append(_t("ipmitool nicht installiert (sudo apt install ipmitool).",
                        "ipmitool not installed (sudo apt install ipmitool)."))
    elif run is None:
        lines.append(_t("Keine Root-Rechte – BMC nicht lesbar.", "No root privileges – BMC unavailable."))
    else:
        ipmi.ensure_ipmi_driver(run)
        sel_events, error = ipmi.read_sel_memory(run)
        if sel_events is None:
            lines.append(_t("Kein BMC erreichbar: ", "No BMC reachable: ") + error)
        elif not sel_events:
            lines.append(_t("Keine Speicherereignisse im Protokoll.", "No memory events in the log."))
        else:
            ce = sum(not e.uncorrectable for e in sel_events)
            ue = len(sel_events) - ce
            lines.append(_t(f"{ce} korrigierbare, {ue} unkorrigierbare Speicherereignisse:",
                            f"{ce} correctable, {ue} uncorrectable memory events:"))
            lines.extend("  " + event.line for event in sel_events[-200:])
            if ue:
                state = "error"
            elif ce and state == "ok":
                state = "warn"

    if state == "error":
        summary = _t("✗ Unkorrigierbare Speicherfehler gefunden – Modul prüfen/tauschen.",
                     "✗ Uncorrectable memory errors found – check/replace the module.")
    elif state == "warn":
        summary = _t("⚠ Korrigierte ECC-Fehler gefunden – betroffenes Modul beobachten.",
                     "⚠ Corrected ECC errors found – keep an eye on the affected module.")
    elif ecc_present is False:
        state = "info"
        summary = _t("Kein ECC-Speicher erkannt.", "No ECC memory detected.")
    elif ecc_present is None and sel_events is None and not edac.controllers:
        state = "info"
        summary = _t("ECC-Zustand nicht ermittelbar (siehe Details).", "ECC state unknown (see details).")
    else:
        summary = _t("✓ ECC aktiv, keine Speicherfehler gemeldet.", "✓ ECC active, no memory errors reported.")
    return state, summary, lines


def collect_proliant(run) -> dict:
    """Alle Stufe-1-Daten. Läuft im Worker-Thread; ``run`` darf None sein (keine Root-Rechte)."""
    data = {"identity": ipmi.system_identity(), "proliant": ipmi.is_proliant(), "time": datetime.now(),
            "dmi_system": {}, "bmc": {}, "bmc_error": "", "sel_info": {}, "sensors": None,
            "sensors_error": "", "events": None, "events_error": "", "iml": None, "iml_error": ""}
    ecc_state, ecc_summary, ecc_lines = build_ecc_report(run)
    data["ecc"] = (ecc_state, ecc_summary, "\n".join(ecc_lines))
    # RAID-Controller (ssacli/storcli) auch auf Nicht-ProLiant, sobald ein Werkzeug vorhanden ist.
    data["raid"] = hpe_raid.collect_raid(run)
    if run is None:
        data["bmc_error"] = _t("Keine Root-Rechte.", "No root privileges.")
        data["iml_error"] = data["bmc_error"]
        return data
    data["dmi_system"] = ipmi.read_dmi_system(run)
    if data["proliant"]:
        data["iml"], data["iml_error"] = ipmi.read_iml(run)
    else:
        data["iml_error"] = _t("Kein HPE ProLiant/Synergy.", "No HPE ProLiant/Synergy.")
    if not shutil.which("ipmitool"):
        data["bmc_error"] = _t("ipmitool nicht installiert.", "ipmitool not installed.")
        return data
    data["bmc"], data["bmc_error"] = ipmi.read_bmc_info(run)
    if data["bmc_error"]:
        return data
    data["sel_info"] = ipmi.read_sel_info(run)
    data["sensors"], data["sensors_error"] = ipmi.read_sensors(run)
    data["events"], data["events_error"] = ipmi.read_event_log(run)
    return data


_COLORS = {"error": QColor("#c62828"), "warn": QColor("#e65100"), "ok": QColor("#2e7d32"), "info": QColor("#9e9e9e")}
_LABEL = {"error": ("Fehler", "Error"), "warn": ("Warnung", "Warning"), "ok": ("OK", "OK"), "info": ("Info", "Info")}


def _sev(severity: str) -> str:
    de, en = _LABEL.get(severity, (severity, severity))
    return _t(de, en)


class ProliantWorker(QThread):
    done = Signal(object)

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def run(self):
        try:
            self.done.emit(collect_proliant(self.panel.root_runner()))
        except Exception as exc:  # pragma: no cover - runtime safety
            self.done.emit({"exception": str(exc)})


class SsaducliWorker(QThread):
    """Erstellt den ssaducli-Diagnosebericht über die Root-Sitzung des Panels."""
    done = Signal(bool, str)

    def __init__(self, panel, target: str):
        super().__init__(panel)
        self.panel = panel
        self.target = target

    def run(self):
        run = self.panel.root_runner()
        if run is None:
            self.done.emit(False, _t("Keine Root-Rechte.", "No root privileges."))
            return
        ok, message = hpe_raid.save_ssaducli_report(run, self.target)
        self.done.emit(ok, message)


def _table(headers: int) -> QTableWidget:
    table = QTableWidget(0, headers)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSortingEnabled(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fit_height(table: QTableWidget, max_rows: int = 25) -> None:
    """Tabelle so hoch wie ihr Inhalt (bis max_rows), damit der Reiter als Ganzes scrollt."""
    header = table.horizontalHeader().height()
    rows = min(table.rowCount(), max_rows)
    row_height = table.verticalHeader().defaultSectionSize()
    table.setMinimumHeight(max(120, header + rows * row_height + 2 * table.frameWidth() + 4))


def _fill(table: QTableWidget, rows: list[tuple[list[str], str]]) -> None:
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for r, (cells, severity) in enumerate(rows):
        for c, value in enumerate(cells):
            item = QTableWidgetItem(value)
            if severity in ("error", "warn"):
                item.setForeground(QBrush(_COLORS[severity]))
            elif severity == "info":
                item.setForeground(QBrush(_COLORS["info"]))
            table.setItem(r, c, item)
    table.setSortingEnabled(True)


class ProliantPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._ssaducli_worker = None
        self._broker = None
        self._broker_failed = False
        self._data = None
        self._proliant = ipmi.is_proliant()
        self._timer = QTimer(self)
        self._timer.setInterval(5 * 60 * 1000)
        self._timer.timeout.connect(self.start)

        layout = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("background: #5d4037; color: white; padding: 8px; font-weight: bold;")
        self.notice.setVisible(not self._proliant)
        layout.addWidget(self.notice)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        buttons = QHBoxLayout()
        self.read_btn = QPushButton()
        self.read_btn.clicked.connect(self.start)
        self.export_btn = QPushButton()
        self.export_btn.clicked.connect(self.export)
        self.export_btn.setEnabled(False)
        buttons.addWidget(self.read_btn, 1)
        buttons.addWidget(self.export_btn)
        layout.addLayout(buttons)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.pages = QTabWidget()
        self.overview = QPlainTextEdit()
        self.overview.setReadOnly(True)
        self.sensors = _table(5)
        events_page = QWidget()
        events_layout = QVBoxLayout(events_page)
        events_layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.event_filter = QComboBox()
        self.event_filter.setProperty("hardwaretest_dynamic", True)
        self.event_filter.currentIndexChanged.connect(self.show_events)
        self.event_search = QLineEdit()
        self.event_search.textChanged.connect(self.show_events)
        self.event_count = QLabel()
        filters.addWidget(self.event_filter)
        filters.addWidget(self.event_search, 1)
        filters.addWidget(self.event_count)
        events_layout.addLayout(filters)
        self.events = _table(6)
        events_layout.addWidget(self.events, 1)
        iml_page = QWidget()
        iml_layout = QVBoxLayout(iml_page)
        iml_layout.setContentsMargins(0, 0, 0, 0)
        iml_filters = QHBoxLayout()
        self.iml_filter = QComboBox()
        self.iml_filter.setProperty("hardwaretest_dynamic", True)
        self.iml_filter.currentIndexChanged.connect(self.show_iml)
        self.iml_search = QLineEdit()
        self.iml_search.textChanged.connect(self.show_iml)
        self.iml_count = QLabel()
        iml_filters.addWidget(self.iml_filter)
        iml_filters.addWidget(self.iml_search, 1)
        iml_filters.addWidget(self.iml_count)
        iml_layout.addLayout(iml_filters)
        self.iml_error = QLabel()
        self.iml_error.setWordWrap(True)
        self.iml_error.setStyleSheet("color: #e05252;")
        self.iml_error.hide()
        iml_layout.addWidget(self.iml_error)
        self.iml = _table(7)
        iml_layout.addWidget(self.iml, 1)
        self.ecc = QPlainTextEdit()
        self.ecc.setReadOnly(True)
        raid_page = self._build_raid_page()
        self.pages.addTab(self.overview, "")
        self.pages.addTab(self.sensors, "")
        self.pages.addTab(events_page, "")
        self.pages.addTab(iml_page, "")
        self.pages.addTab(self.ecc, "")
        self.pages.addTab(raid_page, "")
        layout.addWidget(self.pages, 1)

        language_manager.language_changed.connect(self.retranslate)
        self.retranslate()
        if self._proliant or ipmi.ipmi_available():
            # Server mit BMC: automatisch auslesen und regelmäßig aktualisieren.
            QTimer.singleShot(1500, self.start)
            self._timer.start()

    def _build_raid_page(self) -> QWidget:
        # Scrollbar wie im System-Tab: Tabellen und Rohausgabe erhalten ihre volle Hoehe,
        # der Reiter scrollt als Ganzes.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.raid_summary = QLabel()
        self.raid_summary.setWordWrap(True)
        header.addWidget(self.raid_summary, 1)
        self.ssaducli_btn = QPushButton()
        self.ssaducli_btn.setEnabled(False)
        self.ssaducli_btn.clicked.connect(self.save_ssaducli)
        header.addWidget(self.ssaducli_btn)
        layout.addLayout(header)
        self.raid_findings = QPlainTextEdit()
        self.raid_findings.setReadOnly(True)
        self.raid_findings.setMinimumHeight(110)
        self.raid_findings.setMaximumHeight(220)
        layout.addWidget(self.raid_findings)
        self.raid_controllers = _table(9)
        self.raid_lds = _table(8)
        self.raid_pds = _table(13)
        for table in (self.raid_controllers, self.raid_lds, self.raid_pds):
            table.setMinimumHeight(120)
            layout.addWidget(table)
        self.raid_raw = QPlainTextEdit()
        self.raid_raw.setReadOnly(True)
        self.raid_raw.setPlaceholderText("ssacli / storcli …")
        self.raid_raw.setMinimumHeight(600)
        layout.addWidget(self.raid_raw, 1)
        return scroll

    def show_raid_tab(self) -> None:
        """Zeigt den Reiter „RAID“ und liest aus, falls noch keine Daten vorliegen."""
        self.pages.setCurrentIndex(self.pages.count() - 1)
        if self._data is None and self._worker is None:
            self.start()

    # ----- Root-Sitzung -----

    def root_runner(self):
        """Läuft im Worker-Thread. Hält eine Root-Sitzung offen (nur eine Passwortabfrage)."""
        if os.geteuid() == 0:
            return subprocess.run
        if self._broker is not None:
            return self._broker.run
        if self._broker_failed or privilege.tool() is None:
            return None
        broker = RootBroker(reason=language_manager.tr("ProLiant / RAID-Controller abfragen"))
        try:
            broker.start()
        except RootAuthorizationError:
            self._broker_failed = True
            return None
        self._broker = broker
        return broker.run

    def close_privileged_session(self):
        self._timer.stop()
        if self._worker is not None:
            self._worker.wait(5000)
        if self._ssaducli_worker is not None:
            self._ssaducli_worker.wait(5000)
        if self._broker is not None:
            self._broker.close()
            self._broker = None

    # ----- UI -----

    def retranslate(self, *_):
        self.notice.setText(_t(
            "Kein HPE ProLiant/Synergy erkannt – die iLO-Informationen (Sensoren, Ereignisprotokoll) sind auf diesem System "
            "nicht aktiv. Speicher/ECC und ggf. ein anderer BMC (IPMI) können trotzdem manuell ausgelesen werden.",
            "No HPE ProLiant/Synergy detected – iLO information (sensors, event log) is not active on this system. "
            "Memory/ECC and possibly another BMC (IPMI) can still be read manually."))
        self.hint.setText(_t(
            "Liest über ipmitool direkt vom iLO: Systemdaten, alle Sensoren (Temperaturen, Lüfter, Netzteile, "
            "Spannungen) und das komplette Ereignisprotokoll (IPMI SEL), über iLOrest das Integrated Management Log "
            "(IML), dazu Speicher/ECC. Auf einem ProLiant wird beim "
            "Start automatisch und danach alle 5 Minuten ausgelesen (einmalige Passwortabfrage).",
            "Reads directly from iLO via ipmitool: system data, all sensors (temperatures, fans, power supplies, "
            "voltages) and the complete event log (IPMI SEL), the Integrated Management Log (IML) via iLOrest, plus memory/ECC. On a ProLiant it reads automatically "
            "at startup and every 5 minutes (password asked once)."))
        self.read_btn.setText(_t("Jetzt auslesen", "Read now"))
        self.export_btn.setText(_t("Als HTML exportieren …", "Export as HTML …"))
        self.ssaducli_btn.setText(_t("Diagnosebericht (ssaducli) speichern …", "Save diagnostic report (ssaducli) …"))
        self.ssaducli_btn.setToolTip(_t(
            "Vollständiger HPE-Smart-Storage-Diagnosebericht als ZIP (für den HPE-Support); nur mit ssaducli.",
            "Complete HPE Smart Storage diagnostic report as ZIP (for HPE support); requires ssaducli."))
        for index, (de, en) in enumerate((("Übersicht", "Overview"), ("Sensoren", "Sensors"),
                                          ("iLO-Ereignisprotokoll", "iLO event log"),
                                          ("Integrated Management Log (IML)", "Integrated Management Log (IML)"),
                                          ("Speicher / ECC", "Memory / ECC"), ("RAID", "RAID"))):
            self.pages.setTabText(index, _t(de, en))
        self.raid_controllers.setHorizontalHeaderLabels([
            _t("Controller", "Controller"), _t("Slot", "Slot"), _t("Firmware", "Firmware"), _t("Seriennummer", "Serial"),
            _t("Status", "Status"), _t("Cache", "Cache"), _t("Akku/Kondensator", "Battery/capacitor"),
            _t("Temp. °C", "Temp. °C"), _t("Bewertung", "Rating")])
        self.raid_lds.setHorizontalHeaderLabels([
            _t("Controller", "Controller"), _t("LD", "LD"), _t("RAID", "RAID"), _t("Größe", "Size"), _t("Status", "Status"),
            _t("Stripe", "Stripe"), _t("OS-Gerät", "OS device"), _t("Bewertung", "Rating")])
        self.raid_pds.setHorizontalHeaderLabels([
            _t("Controller", "Controller"), _t("Position", "Position"), _t("Typ", "Type"), _t("Größe", "Size"),
            _t("Status", "Status"), _t("Modell", "Model"), _t("Seriennummer", "Serial"), _t("Firmware", "Firmware"),
            _t("Temp. °C", "Temp. °C"), _t("Stunden", "Hours"), _t("SSD-Rest %", "SSD life %"), _t("Link", "Link"),
            _t("Bewertung", "Rating")])
        self.sensors.setHorizontalHeaderLabels([_t("Sensor", "Sensor"), _t("Wert", "Reading"), _t("Status", "Status"),
                                                _t("Einheit/Entity", "Entity"), _t("Bewertung", "Rating")])
        self.events.setHorizontalHeaderLabels(["#", _t("Datum", "Date"), _t("Zeit", "Time"), _t("Sensor", "Sensor"),
                                               _t("Ereignis", "Event"), _t("Bewertung", "Rating")])
        with_signals = self.event_filter.currentIndex()
        self.event_filter.blockSignals(True)
        self.event_filter.clear()
        self.event_filter.addItems([_t("Alle Ereignisse", "All events"), _t("Fehler und Warnungen", "Errors and warnings"),
                                    _t("Nur Fehler", "Errors only"), _t("Nur Speicher/ECC", "Memory/ECC only")])
        self.event_filter.setCurrentIndex(max(0, with_signals))
        self.event_filter.blockSignals(False)
        self.event_search.setPlaceholderText(_t("Suchen …", "Search …"))
        self.iml.setHorizontalHeaderLabels(["#", _t("Erstellt", "Created"), _t("Aktualisiert", "Updated"),
                                            _t("Anzahl", "Count"), _t("Kategorie", "Category"),
                                            _t("Meldung", "Message"), _t("Bewertung", "Rating")])
        iml_mode = self.iml_filter.currentIndex()
        self.iml_filter.blockSignals(True)
        self.iml_filter.clear()
        self.iml_filter.addItems([_t("Alle Einträge", "All entries"), _t("Fehler und Warnungen", "Errors and warnings"),
                                  _t("Nur offene (nicht repariert)", "Open only (not repaired)"),
                                  _t("Nur Speicher/ECC", "Memory/ECC only")])
        self.iml_filter.setCurrentIndex(max(0, iml_mode))
        self.iml_filter.blockSignals(False)
        self.iml_search.setPlaceholderText(_t("Suchen …", "Search …"))
        if self._data:
            self.show_result(self._data)

    def start(self):
        if self._worker is not None:
            return
        if self.sender() is self.read_btn:
            self._broker_failed = False
        self.read_btn.setEnabled(False)
        if self._broker is None and os.geteuid() != 0:
            self.summary.setStyleSheet("")
            self.summary.setText(_t("Wird ausgelesen … (Passwort-Abfrage beachten)", "Reading … (watch for password prompt)"))
        worker = ProliantWorker(self)
        self._worker = worker
        worker.done.connect(self.show_result)
        connect_finished(worker, self._finished)
        worker.start()

    def _finished(self, worker=None):
        if release_worker(self, "_worker", worker):
            self.read_btn.setEnabled(True)

    def show_result(self, data):
        if "exception" in data:
            self.summary.setStyleSheet(_CSS["error"])
            self.summary.setText(data["exception"])
            return
        self._data = data
        self.export_btn.setEnabled(True)
        self.overview.setPlainText("\n".join(self._overview_lines(data)))
        sensors = data["sensors"] or []
        _fill(self.sensors, [([s.name, s.reading, s.status, s.entity, _sev(s.severity)], s.severity) for s in sensors])
        self.show_events()
        self.show_iml()
        _ecc_state, ecc_summary, ecc_text = data["ecc"]
        self.ecc.setPlainText(ecc_summary + "\n\n" + ecc_text)
        self.show_raid(data.get("raid"))
        state, text = self._summary(data)
        self.summary.setStyleSheet(_CSS.get(state, ""))
        self.summary.setText(text)

    @staticmethod
    def _raid_rows(report) -> tuple[list, list, list]:
        """Zeilen (Zellen, Schweregrad) für Controller-, LD- und PD-Tabelle."""
        def num(value):
            return "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)
        ctrl_rows, ld_rows, pd_rows = [], [], []
        for c in report.controllers if report else []:
            cache = " / ".join(x for x in (c.cache_size, c.cache_status, c.cache_ratio) if x)
            battery = c.battery_status + (f" ({c.battery_temperature} °C)" if c.battery_temperature is not None else "")
            ctrl_rows.append(([c.model, c.slot or c.pci_address, c.firmware, c.serial, c.status, cache, battery,
                               num(c.temperature), _sev(c.severity)], c.severity))
            for ld in c.logical_drives:
                status = ld.status + (f" ({ld.progress_percent:g} %)" if ld.progress_percent is not None
                                      and "%" not in ld.status else "")
                ld_rows.append(([c.label, ld.number, ld.raid_level, ld.size, status, ld.stripe_size, ld.os_device,
                                 _sev(ld.severity)], ld.severity))
            for pd in c.physical_drives:
                temp = num(pd.temperature) + (f"/{pd.temperature_max}" if pd.temperature_max is not None else "")
                status = pd.status + (" / Predictive Failure" if pd.predictive_failure
                                      and "predictive" not in pd.status.lower() else "")
                pd_rows.append(([c.label, pd.location, pd.drive_type, pd.size, status, pd.model, pd.serial, pd.firmware,
                                 temp, num(pd.power_on_hours), num(pd.ssd_life_percent), pd.phy_rate, _sev(pd.severity)],
                                pd.severity))
        return ctrl_rows, ld_rows, pd_rows

    def show_raid(self, report) -> None:
        lang = language_manager.language
        if report is None:
            self.raid_summary.setText(_t("RAID noch nicht ausgelesen.", "RAID not read yet."))
            self.raid_summary.setStyleSheet(_CSS["info"])
            return
        self.ssaducli_btn.setEnabled("ssaducli" in report.tools and self._ssaducli_worker is None)
        severity = report.severity
        self.raid_summary.setStyleSheet(_CSS.get(severity, ""))
        lines = [hpe_raid.summary_line(report, lang)]
        if report.tools:
            lines.append(_t("Werkzeuge: ", "Tools: ") + ", ".join(f"{k}: {v}" for k, v in sorted(report.tools.items())))
        for controller in report.controllers:
            lines.append(f"{_sev(controller.severity)} – {controller.label}")
        self.raid_summary.setText("\n".join(lines))
        findings = [f"[{_sev(f.severity)}] {f.target}: {f.text(lang)}" if f.target else f"[{_sev(f.severity)}] {f.text(lang)}"
                    for f in report.findings()]
        findings.extend(_t("Fehler: ", "Error: ") + e for e in report.errors)
        self.raid_findings.setPlainText("\n".join(findings) or _t("Keine Befunde.", "No findings."))
        ctrl_rows, ld_rows, pd_rows = self._raid_rows(report)
        for table, rows in ((self.raid_controllers, ctrl_rows), (self.raid_lds, ld_rows), (self.raid_pds, pd_rows)):
            _fill(table, rows)
            _fit_height(table)
        self.raid_raw.setPlainText(report.raw_text() or report.hint)

    def save_ssaducli(self):
        if self._ssaducli_worker is not None:
            return
        default = Path.home() / "Downloads" / f"ssaducli-{datetime.now():%Y%m%d-%H%M%S}.zip"
        path, _ = QFileDialog.getSaveFileName(self, _t("Diagnosebericht speichern", "Save diagnostic report"),
                                              str(default), "ZIP (*.zip)")
        if not path:
            return
        if not path.endswith(".zip"):
            path += ".zip"
        if any(ch.isspace() for ch in path):
            self.raid_summary.setText(_t("Zielpfad darf keine Leerzeichen enthalten.", "Target path must not contain spaces."))
            return
        if Path(path).exists():
            # Root-Skript schreibt nur neue Dateien; der Benutzer hat das Überschreiben bestätigt.
            try:
                Path(path).unlink()
            except OSError as exc:
                self.raid_summary.setText(str(exc))
                return
        self.ssaducli_btn.setEnabled(False)
        self.raid_summary.setText(_t("Diagnosebericht wird erstellt … (kann einige Minuten dauern)",
                                     "Creating diagnostic report … (may take a few minutes)"))
        worker = SsaducliWorker(self, path)
        self._ssaducli_worker = worker
        worker.done.connect(self._ssaducli_done)
        connect_finished(worker, self._ssaducli_finished)
        worker.start()

    def _ssaducli_done(self, ok: bool, message: str):
        prefix = _t("Diagnosebericht gespeichert: ", "Diagnostic report saved: ") if ok else _t("ssaducli: ", "ssaducli: ")
        self.raid_summary.setStyleSheet(_CSS["ok" if ok else "error"])
        self.raid_summary.setText(prefix + message)

    def _ssaducli_finished(self, worker=None):
        if release_worker(self, "_ssaducli_worker", worker):
            report = (self._data or {}).get("raid")
            self.ssaducli_btn.setEnabled(bool(report and "ssaducli" in report.tools))

    def _summary(self, data) -> tuple[str, str]:
        stamp = data["time"].strftime("%H:%M")
        raid = data.get("raid")
        raid_text = ""
        raid_state = "ok"
        if raid is not None and raid.available:
            raid_text = " | " + hpe_raid.summary_line(raid, language_manager.language)
            raid_state = raid.severity
        if data["bmc_error"] and data["events"] is None:
            ecc_state, ecc_summary, _ = data["ecc"]
            state = "info" if ecc_state == "ok" else ecc_state
            if raid_state in ("warn", "error"):
                state = hpe_raid.worst(state if state != "info" else "ok", raid_state)
            return (state, _t(f"[{stamp}] Kein iLO/BMC erreichbar: {data['bmc_error']} | {ecc_summary}{raid_text}",
                              f"[{stamp}] No iLO/BMC reachable: {data['bmc_error']} | {ecc_summary}{raid_text}"))
        events = data["events"] or []
        sensors = data["sensors"] or []
        err = sum(e.severity == "error" for e in events)
        warn = sum(e.severity == "warn" for e in events)
        s_err = sum(s.severity == "error" for s in sensors)
        s_warn = sum(s.severity == "warn" for s in sensors)
        iml = data.get("iml") or []
        i_err = sum(e.severity == "error" for e in iml)
        i_warn = sum(e.severity == "warn" for e in iml)
        iml_text = ""
        if data.get("iml") is not None:
            iml_text = _t(f" | IML: {len(iml)} Einträge ({i_err} Fehler, {i_warn} Warnungen offen)",
                          f" | IML: {len(iml)} entries ({i_err} errors, {i_warn} warnings open)")
        text = _t(f"[{stamp}] Sensoren: {len(sensors)} ({s_err} kritisch, {s_warn} Warnung) | "
                  f"Ereignisprotokoll: {len(events)} Einträge ({err} Fehler, {warn} Warnungen){iml_text} | {data['ecc'][1]}",
                  f"[{stamp}] Sensors: {len(sensors)} ({s_err} critical, {s_warn} warning) | "
                  f"Event log: {len(events)} entries ({err} errors, {warn} warnings){iml_text} | {data['ecc'][1]}")
        text += raid_text
        if s_err or err or i_err or data["ecc"][0] == "error" or raid_state == "error":
            return "error", text
        if s_warn or warn or i_warn or data["ecc"][0] == "warn" or raid_state == "warn":
            return "warn", text
        return "ok", text

    def _overview_lines(self, data) -> list[str]:
        identity, dmi, bmc, sel = data["identity"], data["dmi_system"], data["bmc"], data["sel_info"]
        lines = [_t("== System ==", "== System ==")]
        lines.append(f"{_t('Hersteller', 'Vendor')}: {identity.get('vendor') or '?'}")
        lines.append(f"{_t('Modell', 'Model')}: {identity.get('product') or '?'}")
        for key, label in (("Serial Number", _t("Seriennummer", "Serial number")), ("SKU Number", "SKU"),
                           ("UUID", "UUID")):
            if dmi.get(key):
                lines.append(f"{label}: {dmi[key]}")
        lines.append(f"BIOS/ROM: {identity.get('bios') or '?'} ({identity.get('bios_date') or '?'})")
        lines.append(_t("ProLiant/Synergy erkannt: ", "ProLiant/Synergy detected: ") + (_t("ja", "yes") if data["proliant"] else _t("nein", "no")))
        lines.append("")
        lines.append(_t("== iLO / BMC ==", "== iLO / BMC =="))
        if data["bmc_error"]:
            lines.append(_t("Nicht erreichbar: ", "Not reachable: ") + data["bmc_error"])
        else:
            for key in ("Manufacturer Name", "Product Name", "Firmware Revision", "IPMI Version", "Device Available"):
                if bmc.get(key):
                    lines.append(f"{key}: {bmc[key]}")
        if sel:
            lines.append("")
            lines.append(_t("== Ereignisprotokoll (SEL) ==", "== Event log (SEL) =="))
            for key in ("Entries", "Percent Used", "Last Add Time", "Last Del Time", "Overflow"):
                if sel.get(key):
                    lines.append(f"{key}: {sel[key]}")
            if "100" in sel.get("Percent Used", "") or sel.get("Overflow", "").lower() == "true":
                lines.append(_t("⚠ Protokoll voll – neue Ereignisse gehen verloren. Im iLO löschen (nach Sicherung).",
                                "⚠ Log full – new events are lost. Clear it in iLO (after saving)."))
        if data["sensors_error"]:
            lines.append(_t("Sensoren: ", "Sensors: ") + data["sensors_error"])
        if data["events_error"]:
            lines.append(_t("Ereignisprotokoll: ", "Event log: ") + data["events_error"])
        if data.get("iml_error"):
            lines.append("IML: " + data["iml_error"])
        raid = data.get("raid")
        if raid is not None and raid.available:
            lines.append("")
            lines.append(_t("== RAID-Controller ==", "== RAID controllers =="))
            lines.append(hpe_raid.summary_line(raid, language_manager.language))
            for controller in raid.controllers:
                lines.append(f"  {_sev(controller.severity)} – {controller.label}: {controller.status or '?'}"
                             + (f", {_t('Akku', 'battery')} {controller.battery_status}" if controller.battery_status else ""))
            lines.extend(f"  {_t('Fehler', 'Error')}: {error}" for error in raid.errors)
        lines.append("")
        lines.append(_t("Stand: ", "As of: ") + data["time"].strftime("%d.%m.%Y %H:%M:%S"))
        return lines

    def _filtered_events(self):
        events = (self._data or {}).get("events") or []
        mode = self.event_filter.currentIndex()
        needle = self.event_search.text().strip().lower()
        result = []
        for event in reversed(events):  # neueste zuerst
            if mode == 1 and event.severity not in ("error", "warn"):
                continue
            if mode == 2 and event.severity != "error":
                continue
            if mode == 3 and not event.memory:
                continue
            if needle and needle not in f"{event.date} {event.time} {event.sensor} {event.event} {event.direction}".lower():
                continue
            result.append(event)
        return result

    def show_events(self, *_):
        events = self._filtered_events()
        _fill(self.events, [([e.number, e.date, e.time, e.sensor,
                              f"{e.event} ({e.direction})" if e.direction else e.event, _sev(e.severity)], e.severity)
                             for e in events])
        total = len((self._data or {}).get("events") or [])
        self.event_count.setText(_t(f"{len(events)} von {total}", f"{len(events)} of {total}"))

    def _filtered_iml(self):
        entries = (self._data or {}).get("iml") or []
        mode = self.iml_filter.currentIndex()
        needle = self.iml_search.text().strip().lower()
        result = []
        for entry in reversed(entries):  # neueste zuerst
            if mode == 1 and entry.severity not in ("error", "warn"):
                continue
            if mode == 2 and (entry.repaired or entry.severity == "info"):
                continue
            if mode == 3 and not entry.memory:
                continue
            if needle and needle not in f"{entry.created} {entry.category} {entry.message}".lower():
                continue
            result.append(entry)
        return result

    @staticmethod
    def _iml_rating(entry) -> str:
        return _sev(entry.severity) + (_t(" (repariert)", " (repaired)") if entry.repaired else "")

    def show_iml(self, *_):
        entries = self._filtered_iml()
        _fill(self.iml, [([e.number, e.created, e.updated, e.count, e.category, e.message, self._iml_rating(e)],
                          e.severity) for e in entries])
        data = self._data or {}
        error = data.get("iml_error", "") if data.get("iml") is None else ""
        self.iml_error.setText(error)
        self.iml_error.setVisible(bool(error))
        self.iml_count.setText(_t(f"{len(entries)} von {len(data.get('iml') or [])}",
                                  f"{len(entries)} of {len(data.get('iml') or [])}"))

    def export(self):
        if not self._data:
            return
        default = Path.home() / "Downloads" / f"proliant-info-{datetime.now():%Y%m%d-%H%M%S}.html"
        path, _ = QFileDialog.getSaveFileName(self, _t("Exportieren", "Export"), str(default), "HTML (*.html)")
        if not path:
            return
        try:
            Path(path).write_text(self.html_report(), encoding="utf-8")
            self.summary.setText(self.summary.text() + "\n" + _t("Exportiert: ", "Exported: ") + path)
        except OSError as exc:
            self.summary.setText(str(exc))

    def html_report(self) -> str:
        data = self._data
        color = {"error": "#c62828", "warn": "#e65100", "ok": "#2e7d32", "info": "#757575"}

        def rows(items):
            return "".join(
                f'<tr style="color:{color.get(sev, "")}">' + "".join(f"<td>{escape(c)}</td>" for c in cells) + "</tr>"
                for cells, sev in items)
        sensors = [([s.name, s.reading, s.status, s.entity, _sev(s.severity)], s.severity) for s in data["sensors"] or []]
        events = [([e.number, e.date, e.time, e.sensor, e.event, e.direction, _sev(e.severity)], e.severity)
                  for e in reversed(data["events"] or [])]
        iml = [([e.number, e.created, e.updated, e.count, e.category, e.message, self._iml_rating(e)], e.severity)
               for e in reversed(data.get("iml") or [])]
        raid = data.get("raid")
        raid_html = ""
        if raid is not None and raid.available:
            ctrl_rows, ld_rows, pd_rows = self._raid_rows(raid)
            lang = language_manager.language

            def head(labels):
                return "<tr>" + "".join(f"<th>{escape(label)}</th>" for label in labels) + "</tr>"
            findings = "".join(f'<li style="color:{color.get(f.severity, "")}">{escape(f.target)}: {escape(f.text(lang))}</li>'
                               for f in raid.findings())
            findings += "".join(f"<li>{escape(error)}</li>" for error in raid.errors)
            raid_html = (f"<h2>{_t('RAID-Controller', 'RAID controllers')}</h2>"
                         f"<p><b>{escape(hpe_raid.summary_line(raid, lang))}</b></p><ul>{findings}</ul>"
                         f"<table>{head([_t('Controller', 'Controller'), 'Slot', 'Firmware', _t('Seriennummer', 'Serial'), 'Status', 'Cache', _t('Akku/Kondensator', 'Battery/capacitor'), '°C', _t('Bewertung', 'Rating')])}{rows(ctrl_rows)}</table>"
                         f"<h3>{_t('Logische Laufwerke', 'Logical drives')}</h3>"
                         f"<table>{head([_t('Controller', 'Controller'), 'LD', 'RAID', _t('Größe', 'Size'), 'Status', 'Stripe', _t('OS-Gerät', 'OS device'), _t('Bewertung', 'Rating')])}{rows(ld_rows)}</table>"
                         f"<h3>{_t('Physische Laufwerke', 'Physical drives')}</h3>"
                         f"<table>{head([_t('Controller', 'Controller'), _t('Position', 'Position'), _t('Typ', 'Type'), _t('Größe', 'Size'), 'Status', _t('Modell', 'Model'), _t('Seriennummer', 'Serial'), 'Firmware', '°C', _t('Stunden', 'Hours'), 'SSD %', 'Link', _t('Bewertung', 'Rating')])}{rows(pd_rows)}</table>"
                         f"<details><summary>{_t('Rohausgabe', 'Raw output')}</summary><pre>{escape(raid.raw_text())}</pre></details>")
        style = "<style>body{font-family:sans-serif}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:2px 6px}</style>"
        return ('<!doctype html><meta charset="utf-8"><title>ProLiant Info</title>' + style +
                f"<h1>ProLiant Info</h1><p><b>{escape(self._summary(data)[1])}</b></p>"
                f"<h2>{_t('Übersicht', 'Overview')}</h2><pre>{escape(chr(10).join(self._overview_lines(data)))}</pre>"
                f"<h2>{_t('Sensoren', 'Sensors')}</h2><table><tr><th>Sensor</th><th>{_t('Wert', 'Reading')}</th>"
                f"<th>Status</th><th>Entity</th><th>{_t('Bewertung', 'Rating')}</th></tr>{rows(sensors)}</table>"
                f"<h2>{_t('iLO-Ereignisprotokoll', 'iLO event log')}</h2><table><tr><th>#</th><th>{_t('Datum', 'Date')}</th>"
                f"<th>{_t('Zeit', 'Time')}</th><th>Sensor</th><th>{_t('Ereignis', 'Event')}</th><th></th>"
                f"<th>{_t('Bewertung', 'Rating')}</th></tr>{rows(events)}</table>"
                f"<h2>Integrated Management Log (IML)</h2><table><tr><th>#</th><th>{_t('Erstellt', 'Created')}</th>"
                f"<th>{_t('Aktualisiert', 'Updated')}</th><th>{_t('Anzahl', 'Count')}</th><th>{_t('Kategorie', 'Category')}</th>"
                f"<th>{_t('Meldung', 'Message')}</th><th>{_t('Bewertung', 'Rating')}</th></tr>{rows(iml)}</table>"
                f"<h2>{_t('Speicher / ECC', 'Memory / ECC')}</h2><pre>{escape(data['ecc'][1] + chr(10) + chr(10) + data['ecc'][2])}</pre>"
                + raid_html)
