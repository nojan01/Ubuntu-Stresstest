"""ECC-Übersicht: verbauter Speicher (DMI), Kernel-EDAC-Zähler und BMC-Ereignisprotokoll (IPMI)."""

from __future__ import annotations

import os
import shutil
import subprocess

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from hardwaretest.core import ipmi
from hardwaretest.core.root_broker import RootAuthorizationError, RootBroker
from hardwaretest.core.system_info import read_edac_info
from hardwaretest.ui.i18n import language_manager

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


class EccWorker(QThread):
    done = Signal(str, str, str)

    def run(self):
        broker = None
        try:
            if os.geteuid() == 0:
                run = subprocess.run
            elif shutil.which("pkexec"):
                broker = RootBroker(shutil.which("pkexec"))
                try:
                    broker.start()
                    run = broker.run
                except RootAuthorizationError:
                    broker, run = None, None
            else:
                run = None
            state, summary, lines = build_ecc_report(run)
            self.done.emit(state, summary, "\n".join(lines))
        except Exception as exc:  # pragma: no cover - runtime safety
            self.done.emit("error", str(exc), "")
        finally:
            if broker is not None:
                broker.close()


class EccPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        layout = QVBoxLayout(self)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.read_btn = QPushButton()
        self.read_btn.clicked.connect(self.start)
        layout.addWidget(self.read_btn)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        language_manager.language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self, *_):
        self.hint.setText(_t(
            "Zeigt verbauten Speicher und ECC-Typ (dmidecode), Kernel-EDAC-Zähler und Speicherereignisse "
            "aus dem BMC-Protokoll (ipmitool, z. B. HPE iLO, Dell iDRAC). Benötigt einmalig das Administrator-Passwort.",
            "Shows installed memory and ECC type (dmidecode), kernel EDAC counters and memory events from the "
            "BMC log (ipmitool, e.g. HPE iLO, Dell iDRAC). Requires the administrator password once."))
        self.read_btn.setText(_t("ECC-Status auslesen", "Read ECC status"))

    def start(self):
        if self._worker is not None:
            return
        self.read_btn.setEnabled(False)
        self.summary.setStyleSheet("")
        self.summary.setText(_t("Wird ausgelesen … (Passwort-Abfrage beachten)", "Reading … (watch for password prompt)"))
        self._worker = EccWorker(self)
        self._worker.done.connect(self.show_result)
        self._worker.finished.connect(self._finished)
        self._worker.start()

    def show_result(self, state, summary, details):
        self.summary.setText(summary)
        self.summary.setStyleSheet(_CSS.get(state, ""))
        self.details.setPlainText(details)

    def _finished(self):
        self._worker = None
        self.read_btn.setEnabled(True)
