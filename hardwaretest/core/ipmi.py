"""ECC- und Lüfterdaten über IPMI (z. B. HPE iLO, Dell iDRAC) und DMI.

Alle Abfragen brauchen root.  ``run`` ist ein ``subprocess.run``-kompatibler
Aufruf – im Normalfall ``RootBroker.run``, als root direkt ``subprocess.run``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class SelCounts:
    correctable: int
    uncorrectable: int
    timestamp: float
    new_correctable: int = 0
    new_uncorrectable: int = 0


# Letzter erfolgreich gelesener Stand des BMC-Protokolls (ProLiant-Info-Tab, Monitoring, Gesamttest).
# Die ECC-Anzeigen der Testtabs lesen ihn, weil sie selbst keine Root-Rechte haben.
last_sel_counts: Optional[SelCounts] = None
# Ereignisse, die beim ersten Lesen nach App-Start schon im Protokoll standen.
# Nur danach hinzugekommene Ereignisse gelten als neu (Warnfarbe).
_sel_baseline: Optional[set[str]] = None


def _sel_id(line: str) -> str:
    return line.split("|", 1)[0].strip().lower().lstrip("0") or "0"


def _remember(events) -> None:
    global last_sel_counts, _sel_baseline
    # Schlüssel ist die Eintragsnummer (erste Spalte): ``sel list`` und
    # ``sel elist`` formatieren dieselben Einträge unterschiedlich.
    if _sel_baseline is None:
        _sel_baseline = {_sel_id(e.line) for e in events}
    new = [e for e in events if _sel_id(e.line) not in _sel_baseline]
    ue = sum(e.uncorrectable for e in events)
    new_ue = sum(e.uncorrectable for e in new)
    last_sel_counts = SelCounts(len(events) - ue, ue, time.time(),
                                len(new) - new_ue, new_ue)

_SERVER_VENDORS = re.compile(r"\b(hpe?|hewlett|dell|supermicro|lenovo|fujitsu|cisco|inspur|quanta|gigabyte|asrock ?rack)\b", re.I)
_SEL_MEMORY = re.compile(r"memory|ecc|dimm", re.I)
_SEL_UNCORRECTABLE = re.compile(r"uncorrectable|multi-?bit|fatal|parity", re.I)
_SEL_CORRECTABLE = re.compile(r"correctable|single-?bit|scrub|threshold|logging limit", re.I)


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def ipmi_available() -> bool:
    """ipmitool installiert und ein BMC wahrscheinlich (geladenes Modul oder Server-Hersteller)."""
    if not shutil.which("ipmitool"):
        return False
    if Path("/dev/ipmi0").exists() or Path("/sys/class/ipmi").exists() or Path("/sys/module/ipmi_si").exists():
        return True
    return bool(_SERVER_VENDORS.search(_read("/sys/class/dmi/id/sys_vendor")))


def default_runner() -> Optional[Runner]:
    return subprocess.run if os.geteuid() == 0 else None


def ensure_ipmi_driver(run: Runner) -> None:
    if Path("/dev/ipmi0").exists():
        return
    for module in ("ipmi_si", "ipmi_devintf"):
        with contextlib.suppress(OSError, subprocess.SubprocessError, RuntimeError):
            run(["modprobe", "-q", module], capture_output=True, text=True, timeout=20)


@dataclass
class SdrReading:
    name: str
    value: float
    unit: str
    status: str


def _numeric_sdr_reading(text: str) -> Optional[tuple[float, str]]:
    # ipmitool folgt der Locale: unter de_DE steht ein Dezimalkomma („15,68 percent“).
    match = re.match(r"(-?\d+(?:[.,]\d+)?)\s*(.*)", text.strip())
    if not match:
        return None
    unit = match.group(2).strip().lower()
    return float(match.group(1).replace(",", ".")), {"percent": "%", "degrees c": "°C"}.get(unit, unit)


def parse_sdr(text: str) -> list[SdrReading]:
    """``ipmitool sdr type …``: ``Fan 1 | 30h | ok | 29.1 | 30.58 percent``."""
    readings = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 5:
            continue
        reading = _numeric_sdr_reading(parts[4])
        if reading is None:
            continue  # "no reading", "Disabled", "Transition to Running" …
        readings.append(SdrReading(parts[0], *reading, parts[2].lower()))
    return readings


def fan_metrics(run: Runner) -> tuple[dict[str, float], list[str]]:
    """Lüfter als Monitoring-Messreihen ``fan.ipmi.<Name> (<Einheit>)``."""
    sensors, error = read_sensors(run, timeout=60)
    if sensors is None:
        detail = " ".join(error.split())[:160]
        return {}, [f"fan.ipmi ({detail})" if detail else "fan.ipmi"]
    metrics = {}
    for sensor in environment_sensors(sensors):
        if sensor_kind(sensor) != "fan":
            continue
        reading = _numeric_sdr_reading(sensor.reading)
        if reading is not None:
            value, unit = reading
            if unit in {"%", "rpm"}:
                metrics[f"fan.ipmi.{sensor.name} ({unit})"] = value
    return metrics, ([] if metrics else ["fan.ipmi"])


@dataclass
class SelEvent:
    line: str
    uncorrectable: bool


def parse_sel_memory(text: str) -> list[SelEvent]:
    """Speicherereignisse aus ``ipmitool sel list``/``elist``; nur ausgelöste (Asserted)."""
    events = []
    for line in text.splitlines():
        if not _SEL_MEMORY.search(line) or "deasserted" in line.lower():
            continue
        if _SEL_UNCORRECTABLE.search(line.replace("Correctable", "")):
            events.append(SelEvent(line.strip(), True))
        elif _SEL_CORRECTABLE.search(line):
            events.append(SelEvent(line.strip(), False))
    return events


def sel_ecc_metrics(run: Runner) -> tuple[dict[str, float], list[str]]:
    """Zähler korrigierbarer/unkorrigierbarer Speicherereignisse im BMC-Protokoll."""
    try:
        result = run(["ipmitool", "sel", "list"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return {}, ["ecc.ipmi_sel"]
    if result.returncode:
        return {}, ["ecc.ipmi_sel"]
    events = parse_sel_memory(result.stdout)
    _remember(events)
    return {
        "ecc.ipmi_sel.ce_count": float(sum(not e.uncorrectable for e in events)),
        "ecc.ipmi_sel.ue_count": float(sum(e.uncorrectable for e in events)),
    }, []


@dataclass
class MemoryModule:
    locator: str
    size: str
    kind: str
    speed: str
    manufacturer: str
    part_number: str


@dataclass
class DmiMemory:
    error_correction: list[str] = field(default_factory=list)
    modules: list[MemoryModule] = field(default_factory=list)

    @property
    def ecc(self) -> Optional[bool]:
        if not self.error_correction:
            return None
        return any("ecc" in value.lower() for value in self.error_correction)


def parse_dmidecode_memory(text: str) -> DmiMemory:
    info = DmiMemory()
    for block in re.split(r"\n\s*\n", text):
        values = dict(re.findall(r"^\s+([^:\n]+):\s*(.*)$", block, re.M))
        if "Physical Memory Array" in block and "Error Correction Type" in values:
            info.error_correction.append(values["Error Correction Type"])
        elif "Memory Device" in block and values.get("Size", "").lower() not in {"", "no module installed", "0"}:
            info.modules.append(MemoryModule(
                values.get("Locator", "?"), values.get("Size", "?"), values.get("Type", "?"),
                values.get("Configured Memory Speed", values.get("Speed", "?")),
                values.get("Manufacturer", "?"), values.get("Part Number", "?").strip(),
            ))
    return info


def read_dmi_memory(run: Runner) -> tuple[Optional[DmiMemory], str]:
    try:
        result = run(["dmidecode", "-t", "memory"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return None, str(exc)
    if result.returncode:
        return None, (result.stderr or result.stdout).strip() or f"Exit {result.returncode}"
    return parse_dmidecode_memory(result.stdout), ""


def read_sel_memory(run: Runner) -> tuple[Optional[list[SelEvent]], str]:
    try:
        result = run(["ipmitool", "sel", "elist"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return None, str(exc)
    if result.returncode:
        return None, (result.stderr or result.stdout).strip() or f"Exit {result.returncode}"
    events = parse_sel_memory(result.stdout)
    _remember(events)
    return events, ""


_poll_lock = threading.Lock()
_poll_running = False


def _noninteractive_runner() -> Optional[Runner]:
    """Root-Aufruf ohne Passwortabfrage: als root direkt, sonst ``sudo -n`` (z. B. Live-ISO)."""
    if os.geteuid() == 0:
        return subprocess.run
    if not shutil.which("sudo"):
        return None

    def run(cmd, **kwargs):
        return subprocess.run(["sudo", "-n", *cmd], **kwargs)
    return run


def refresh_sel_counts_async(max_age: float = 60.0) -> None:
    """Aktualisiert ``last_sel_counts`` im Hintergrund, wenn das ohne Passwort möglich ist."""
    global _poll_running
    if last_sel_counts is not None and time.time() - last_sel_counts.timestamp < max_age:
        return
    if not shutil.which("ipmitool") or not ipmi_available():
        return
    run = _noninteractive_runner()
    if run is None:
        return
    with _poll_lock:
        if _poll_running:
            return
        _poll_running = True

    def worker():
        global _poll_running
        try:
            sel_ecc_metrics(run)
        finally:
            _poll_running = False
    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# ProLiant-Info: Erkennung, Systemdaten, alle Sensoren, komplettes Ereignisprotokoll

# HPE-Server mit iLO: ProLiant und Synergy-Compute-Module (z. B. "Synergy 480 Gen10").
_HPE_ILO_MODELS = re.compile(r"proliant|synergy", re.I)


def is_proliant() -> bool:
    vendor = _read("/sys/class/dmi/id/sys_vendor")
    product = _read("/sys/class/dmi/id/product_name")
    return bool(re.search(r"\bhpe?\b|hewlett", vendor, re.I)
                and _HPE_ILO_MODELS.search(product))


def is_synergy() -> bool:
    """HPE Synergy Compute Module: GPUs sitzen auf dem Grafikmodul hinter einem PCIe-Switch."""
    return "synergy" in _read("/sys/class/dmi/id/product_name").lower()


def system_identity() -> dict[str, str]:
    base = "/sys/class/dmi/id/"
    return {key: _read(base + name) for key, name in (
        ("vendor", "sys_vendor"), ("product", "product_name"), ("bios", "bios_version"),
        ("bios_date", "bios_date"))}


def _run_text(run: Runner, cmd: list[str], timeout: float = 60) -> tuple[Optional[str], str]:
    try:
        result = run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return None, str(exc)
    if result.returncode:
        return None, (result.stderr or result.stdout).strip() or f"Exit {result.returncode}"
    return result.stdout, ""


def parse_key_values(text: str, separator: str = ":") -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition(separator)
        if sep and key.strip() and value.strip() and key.strip() not in values:
            values[key.strip()] = value.strip()
    return values


def read_bmc_info(run: Runner) -> tuple[dict[str, str], str]:
    """``ipmitool mc info``: Firmware des BMC (iLO), Hersteller, IPMI-Version."""
    text, error = _run_text(run, ["ipmitool", "mc", "info"], 30)
    return (parse_key_values(text) if text else {}), error


def read_dmi_system(run: Runner) -> dict[str, str]:
    """Seriennummer/SKU stehen nur root-lesbar in DMI."""
    text, _error = _run_text(run, ["dmidecode", "-t", "system"], 30)
    return parse_key_values(text) if text else {}


@dataclass
class Sensor:
    name: str
    status: str
    entity: str
    reading: str
    severity: str  # ok | warn | error | info


def _sensor_severity(status: str, reading: str) -> str:
    status = status.lower()
    text = reading.lower()
    if status in {"cr", "nr", "lnr", "lcr", "ucr", "unr"} or re.search(r"fail|fault|critical|lost|non-recoverable", text):
        return "error"
    if status in {"nc", "lnc", "unc"} or re.search(r"degraded|predictive|warning|redundancy lost|absent", text):
        return "warn"
    if status == "ok":
        return "ok"
    return "info"  # ns = keine Messung (Steckplatz leer, Sensor deaktiviert)


def parse_sensors(text: str) -> list[Sensor]:
    """``ipmitool sdr elist``: ``Name | 30h | ok | 7.1 | 35 degrees C``."""
    sensors = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 5 or not parts[0]:
            continue
        sensors.append(Sensor(parts[0], parts[2], parts[3], parts[4], _sensor_severity(parts[2], parts[4])))
    return sensors


def read_sensors(run: Runner, timeout: float = 120) -> tuple[Optional[list[Sensor]], str]:
    text, error = _run_text(run, ["ipmitool", "sdr", "elist"], timeout)
    return (parse_sensors(text) if text is not None else None), error


def sensor_kind(sensor: Sensor) -> str:
    """Grobe Einordnung nach Messwert: temperature | fan | power | other."""
    reading = sensor.reading.lower()
    name = sensor.name.lower()
    if "degrees" in reading or re.search(r"\btemp", name):
        return "temperature"
    if "rpm" in reading or ("percent" in reading and "fan" in name) or re.search(r"\bfan", name):
        return "fan"
    if "watts" in reading or re.search(r"\bpower meter|\bpwr", name):
        return "power"
    return "other"


def environment_sensors(sensors: list[Sensor]) -> list[Sensor]:
    """Temperatur-, Lüfter- und Leistungssensoren mit Messwert (ohne leere Steckplätze)."""
    kinds = ("temperature", "fan", "power")
    picked = [s for s in sensors if sensor_kind(s) in kinds and s.status.lower() != "ns"
              and not re.search(r"no reading|disabled", s.reading, re.I)]
    return sorted(picked, key=lambda s: (kinds.index(sensor_kind(s)), s.name))


# Sensortypen laut IPMI-Spezifikation: 0x08 Power Supply, 0x09 Power Unit (Redundanz).
POWER_SENSOR_TYPES = ("0x08", "0x09")


def read_power_sensors(run: Runner) -> tuple[Optional[list[Sensor]], str]:
    """Netzteil- und Redundanzsensoren (``ipmitool sdr type 0x08`` / ``0x09``).

    Zeilen wie ``Power Supply 1 | 9Eh | ok | 10.1 | Presence detected`` oder
    ``Power Supplies | A4h | ok | 21.1 | Fully Redundant``.
    """
    sensors: list[Sensor] = []
    errors: list[str] = []
    for code in POWER_SENSOR_TYPES:
        text, error = _run_text(run, ["ipmitool", "sdr", "type", code], 60)
        if text is None:
            errors.append(error)
            continue
        for sensor in parse_sensors(text):
            if sensor.name not in {s.name for s in sensors}:
                sensors.append(sensor)
    if not sensors and len(errors) == len(POWER_SENSOR_TYPES):
        return None, errors[0]
    return sensors, ""


def power_redundancy(sensors: list[Sensor]) -> Optional[str]:
    """Redundanzaussage aus den Power-Unit-Sensoren: ``redundant`` | ``lost`` | ``degraded`` | None."""
    for sensor in sensors:
        text = sensor.reading.lower()
        if "fully redundant" in text or text.strip() == "redundant":
            return "redundant"
        if "redundancy lost" in text or "non-redundant" in text or "insufficient" in text:
            return "lost"
        if "redundancy degraded" in text:
            return "degraded"
    return None


@dataclass
class LogEvent:
    number: str
    date: str
    time: str
    sensor: str
    event: str
    direction: str
    severity: str  # error | warn | info

    @property
    def memory(self) -> bool:
        return bool(_SEL_MEMORY.search(self.sensor + " " + self.event))


_EVENT_ERROR = re.compile(r"uncorrectable|multi-?bit|fatal|failure|fault|critical|non-recoverable|ierr|"
                          r"machine check|bus (?:fatal|uncorrectable)|lost|power off|ac lost|thermal trip|"
                          r"processor.*error|config(?:uration)? error", re.I)
_EVENT_WARN = re.compile(r"correctable|single-?bit|predictive|non-critical|degraded|warning|upper non|lower non|"
                         r"redundancy|watchdog|timeout|throttl|limit|presence|removed|absent|power cycle|reset", re.I)


def _event_severity(sensor: str, event: str, direction: str) -> str:
    if direction.lower().startswith("deasserted"):
        return "info"
    text = f"{sensor} {event}"
    if _EVENT_ERROR.search(text.replace("Correctable", "")):
        return "error"
    if _EVENT_WARN.search(text):
        return "warn"
    return "info"


def parse_event_log(text: str) -> list[LogEvent]:
    """``ipmitool sel elist``: ``1 | 10/15/2026 | 12:00:00 | Memory #0x01 | Correctable ECC | Asserted``."""
    events = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 5 or not parts[0]:
            continue
        number, date, clock, sensor, event = parts[:5]
        direction = parts[5] if len(parts) > 5 else ""
        events.append(LogEvent(number, date, clock, sensor, event, direction,
                               _event_severity(sensor, event, direction)))
    return events


def read_event_log(run: Runner) -> tuple[Optional[list[LogEvent]], str]:
    text, error = _run_text(run, ["ipmitool", "sel", "elist"], 180)
    if text is None:
        return None, error
    events = parse_event_log(text)
    _remember(parse_sel_memory(text))
    return events, ""


def read_sel_info(run: Runner) -> dict[str, str]:
    text, _error = _run_text(run, ["ipmitool", "sel", "info"], 30)
    return parse_key_values(text) if text else {}


# ----- HPE Integrated Management Log (IML) über iLOrest (lokaler CHIF-Kanal) -----

ILOREST_IML_CMD = ["ilorest", "--nologo", "serverlogs", "--selectlog=IML", "--json"]


@dataclass
class ImlEntry:
    number: str
    created: str
    updated: str
    count: str
    severity: str  # error | warn | info
    category: str
    message: str
    repaired: bool

    @property
    def memory(self) -> bool:
        return bool(_SEL_MEMORY.search(self.category + " " + self.message))


_IML_SEVERITY = {"critical": "error", "warning": "warn", "caution": "warn"}


def _iml_time(value: str) -> str:
    return (value or "").replace("T", " ").replace("Z", "").strip()


def parse_iml(text: str) -> list[ImlEntry]:
    """IML-JSON (``ilorest serverlogs --selectlog=IML --json`` oder Redfish-Collection) umwandeln."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("keine JSON-Antwort")
    data, _end = json.JSONDecoder().raw_decode(text[min(starts):])
    members = data if isinstance(data, list) else (data.get("Members") or data.get("Items") or [])
    entries = []
    for member in members:
        if not isinstance(member, dict):
            continue
        oem = (member.get("Oem") or {})
        oem = oem.get("Hpe") or oem.get("Hp") or {}
        repaired = bool(oem.get("Repaired"))
        severity = "info" if repaired else _IML_SEVERITY.get(str(member.get("Severity", "")).lower(), "info")
        categories = oem.get("Categories") or []
        category = ", ".join(str(c) for c in categories) if categories else str(oem.get("Class", ""))
        if oem.get("ClassDescription"):
            category = f"{category} / {oem['ClassDescription']}" if category else str(oem["ClassDescription"])
        entries.append(ImlEntry(
            number=str(member.get("Id", "")),
            created=_iml_time(member.get("Created", "")),
            updated=_iml_time(oem.get("Updated", "")),
            count=str(oem.get("Count", "")),
            severity=severity,
            category=category,
            message=str(member.get("Message", "")).strip(),
            repaired=repaired,
        ))
    entries.sort(key=lambda e: (e.created, int(e.number) if e.number.isdigit() else 0))
    return entries


def _iml_error_text(error: str) -> str:
    lines = [re.sub(r"^\s*ERROR\s*:\s*", "", ln).strip() for ln in error.splitlines()]
    lines = [ln for ln in lines if ln][:3]
    text = " | ".join(lines) or error
    if "chif" in error.lower() or "blobstore" in error.lower():
        text += _t_both(" – Lokaler iLO-Zugriff (CHIF) fehlgeschlagen: Bibliothek ilorest_chif.so nicht gefunden "
                        "oder in iLO ist 'Require Host Authentication' aktiv.",
                        "Local iLO access (CHIF) failed: ilorest_chif.so not found or "
                        "'Require Host Authentication' is enabled in iLO.")
    return text


def _t_both(de: str, en: str) -> str:
    return f"{de} / {en}"


def read_iml(run: Runner) -> tuple[Optional[list[ImlEntry]], str]:
    if not shutil.which("ilorest"):
        return None, "ilorest nicht installiert / not installed"
    text, error = _run_text(run, ILOREST_IML_CMD, 240)
    if text is None:
        return None, _iml_error_text(error)
    try:
        return parse_iml(text), ""
    except ValueError as exc:
        return None, f"IML: {exc}"


# ----- Protokolle löschen (ProLiant Info) -----

ILOREST_IEL_CMD = ["ilorest", "--nologo", "serverlogs", "--selectlog=IEL", "--json"]
# Schlüssel -> (Befehl, benötigtes Programm). SEL: IPMI-Ereignisprotokoll des BMC,
# IML: Integrated Management Log, IEL: iLO Event Log (Ereignisse des iLO selbst).
CLEAR_LOG_COMMANDS = {
    "SEL": ["ipmitool", "sel", "clear"],
    "IML": ["ilorest", "--nologo", "serverlogs", "--selectlog=IML", "--clearlog"],
    "IEL": ["ilorest", "--nologo", "serverlogs", "--selectlog=IEL", "--clearlog"],
}


def clearable_logs() -> list[str]:
    """Protokolle, für die das Löschwerkzeug installiert ist (IML/IEL nur auf ProLiant/Synergy)."""
    logs = []
    if shutil.which("ipmitool"):
        logs.append("SEL")
    if shutil.which("ilorest") and is_proliant():
        logs += ["IML", "IEL"]
    return logs


def backup_iel(run: Runner, target: Path) -> tuple[bool, str]:
    """iLO Event Log als JSON sichern (das Panel zeigt es nicht an, der Bericht enthält es also nicht)."""
    text, error = _run_text(run, ILOREST_IEL_CMD, 240)
    if text is None:
        return False, _iml_error_text(error)
    try:
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        return False, str(exc)
    return True, str(target)


def clear_log(run: Runner, log: str) -> tuple[bool, str]:
    """Ein Protokoll unwiderruflich löschen; Rückgabe (Erfolg, Meldung des Werkzeugs)."""
    global _sel_baseline
    command = CLEAR_LOG_COMMANDS[log]
    text, error = _run_text(run, command, 240)
    if text is None:
        return False, _iml_error_text(error) if command[0] == "ilorest" else error
    if log == "SEL":
        # Eintragsnummern beginnen neu – der Vergleichsstand „beim Start vorhanden“ gilt nicht mehr.
        _sel_baseline = None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return True, lines[-1] if lines else ""
