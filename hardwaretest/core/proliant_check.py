"""ProLiant-/RAID-Prüfung für den Gesamttest: Momentaufnahme vor und nach den Tests.

Erfasst RAID-Controller (``hpe_raid``), das Integrated Management Log (IML) und die
iLO-Sensoren über **einen** Root-Runner.  Der Vergleich liefert den Schritt
„ProLiant / RAID-Controller“: FEHLGESCHLAGEN nur, wenn während des Tests ein neuer
RAID-Fehler aufgetreten ist (neuer Fehlerbefund oder Statusverschlechterung); neue
IML-Einträge und Sensorwarnungen werden als Warnung im Bericht aufgeführt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from hardwaretest.core import hpe_raid, ipmi
from hardwaretest.core.ipmi import Runner


@dataclass
class ProliantSnapshot:
    proliant: bool = False
    raid: Optional[hpe_raid.RaidReport] = None
    iml: Optional[list[ipmi.ImlEntry]] = None
    iml_error: str = ""
    sensors: Optional[list[ipmi.Sensor]] = None
    sensors_error: str = ""
    errors: list[str] = field(default_factory=list)


def relevant() -> bool:
    """Lohnt sich die Prüfung? ProLiant/Synergy erkannt oder ein RAID-Werkzeug vorhanden."""
    return ipmi.is_proliant() or hpe_raid.tools_available()


def snapshot(run: Runner, proliant: Optional[bool] = None) -> ProliantSnapshot:
    """Liest RAID, IML und Sensoren; einzelne Fehler stoppen die Aufnahme nie."""
    snap = ProliantSnapshot(proliant=ipmi.is_proliant() if proliant is None else proliant)
    try:
        if hpe_raid.tools_available():
            snap.raid = hpe_raid.collect_raid(run)
    except Exception as exc:  # defensiv: Bericht soll trotzdem entstehen
        snap.errors.append(f"RAID: {exc}")
    if snap.proliant:
        try:
            snap.iml, snap.iml_error = ipmi.read_iml(run)
        except Exception as exc:
            snap.iml_error = str(exc)
    if snap.proliant or ipmi.ipmi_available():
        try:
            ipmi.ensure_ipmi_driver(run)
            snap.sensors, snap.sensors_error = ipmi.read_sensors(run)
        except Exception as exc:
            snap.sensors_error = str(exc)
    return snap


def _iml_key(entry: ipmi.ImlEntry) -> tuple[str, str, str]:
    return (entry.number, entry.created, entry.message)


def new_iml_entries(before: Optional[list[ipmi.ImlEntry]],
                    after: Optional[list[ipmi.ImlEntry]]) -> list[ipmi.ImlEntry]:
    """IML-Einträge, die nach dem Test neu sind oder deren Zähler gestiegen ist."""
    if after is None:
        return []
    known = {_iml_key(e): e for e in before or []}
    result = []
    for entry in after:
        old = known.get(_iml_key(entry))
        if old is None or (old.count != entry.count and entry.count.isdigit() and old.count.isdigit()
                           and int(entry.count) > int(old.count)):
            result.append(entry)
    return result


def new_sensor_warnings(before: Optional[list[ipmi.Sensor]],
                        after: Optional[list[ipmi.Sensor]]) -> list[ipmi.Sensor]:
    """Sensoren, die nach dem Test Warnung/Fehler melden, vorher aber nicht."""
    if after is None:
        return []
    old = {s.name: s.severity for s in before or []}
    return [s for s in after if s.severity in ("warn", "error") and old.get(s.name, "ok") not in ("warn", "error")]


def _error_findings(report: Optional[hpe_raid.RaidReport], language: str) -> set[tuple[str, str]]:
    if report is None:
        return set()
    result = set()
    for controller in report.controllers:
        for finding in controller.all_findings():
            if finding.severity == "error":
                result.add((controller.key, finding.text(language)))
    return result


def evaluate(before: Optional[ProliantSnapshot], after: Optional[ProliantSnapshot],
             language: str = "de") -> tuple[bool, str, list[str]]:
    """Liefert (fehlgeschlagen, Kurzfassung, Detailzeilen) für den Gesamttest-Schritt."""
    en = language == "en"
    details: list[str] = []
    failed = False
    summary_parts: list[str] = []
    if after is None:
        return False, ("No data recorded." if en else "Keine Daten erfasst."), details

    # --- RAID ---
    raid = after.raid
    if raid is not None and raid.available:
        summary_parts.append(hpe_raid.summary_line(raid, language))
        details.append("== RAID ==")
        for controller in raid.controllers:
            label = {"ok": "OK", "warn": "WARNING" if en else "WARNUNG", "error": "ERROR" if en else "FEHLER",
                     "info": "INFO"}.get(controller.severity, controller.severity)
            details.append(f"[{label}] {controller.label}: {controller.status or '?'}"
                           + (f", {'battery' if en else 'Akku'} {controller.battery_status}" if controller.battery_status else "")
                           + (f", {controller.temperature} °C" if controller.temperature is not None else ""))
            for finding in controller.all_findings():
                if finding.severity in ("warn", "error"):
                    details.append(f"    {finding.target}: {finding.text(language)}")
        details.extend(f"  {'Error' if en else 'Fehler'}: {error}" for error in raid.errors)
        changes = hpe_raid.diff_reports(before.raid if before else None, raid, language)
        new_errors = _error_findings(raid, language) - _error_findings(before.raid if before else None, language)
        if changes:
            details.append("-- " + ("Changes during the test" if en else "Änderungen während des Tests") + " --")
            details.extend(f"  {line}" for line in changes)
        if new_errors:
            failed = True
            summary_parts.append(f"{len(new_errors)} " + ("new RAID error(s)!" if en else "neue RAID-Fehler!"))
        elif raid.severity == "error":
            summary_parts.append("RAID errors already present before the test." if en
                                 else "RAID-Fehler bestanden bereits vor dem Test.")
        elif changes:
            summary_parts.append(f"{len(changes)} " + ("RAID change(s)" if en else "RAID-Änderungen"))
    elif raid is not None:
        summary_parts.append(raid.hint or ("No RAID tool" if en else "Kein RAID-Werkzeug"))

    # --- IML ---
    if after.iml is not None:
        fresh = new_iml_entries(before.iml if before else None, after.iml)
        crit = sum(e.severity == "error" for e in fresh)
        warn = sum(e.severity == "warn" for e in fresh)
        summary_parts.append((f"IML: {len(fresh)} new entries ({crit} critical, {warn} warnings)" if en
                              else f"IML: {len(fresh)} neue Einträge ({crit} kritisch, {warn} Warnungen)"))
        if fresh:
            details.append("== " + ("New IML entries" if en else "Neue IML-Einträge") + " ==")
            details.extend(f"  #{e.number} {e.created} [{e.severity}] {e.category}: {e.message}" for e in fresh)
    elif after.proliant and after.iml_error:
        details.append("IML: " + after.iml_error)

    # --- Sensoren ---
    if after.sensors is not None:
        fresh = new_sensor_warnings(before.sensors if before else None, after.sensors)
        current = [s for s in after.sensors if s.severity in ("warn", "error")]
        summary_parts.append((f"Sensors: {len(fresh)} new warnings, {len(current)} total" if en
                              else f"Sensoren: {len(fresh)} neue Warnungen, {len(current)} gesamt"))
        if current:
            details.append("== " + ("Sensor warnings after the test" if en else "Sensorwarnungen nach dem Test") + " ==")
            details.extend(f"  {'NEW ' if s in fresh else ''}{s.name}: {s.reading} ({s.status})" if en else
                           f"  {'NEU ' if s in fresh else ''}{s.name}: {s.reading} ({s.status})" for s in current)
    elif after.sensors_error:
        details.append(("Sensors: " if en else "Sensoren: ") + after.sensors_error)
    details.extend(after.errors)
    if before is None:
        details.append("No snapshot before the test – comparison not possible." if en
                       else "Keine Momentaufnahme vor dem Test – Vergleich nicht möglich.")
    summary = " | ".join(summary_parts) or ("No ProLiant/RAID data." if en else "Keine ProLiant-/RAID-Daten.")
    return failed, summary, details
