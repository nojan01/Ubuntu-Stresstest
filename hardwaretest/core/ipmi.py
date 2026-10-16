"""ECC- und Lüfterdaten über IPMI (z. B. HPE iLO, Dell iDRAC) und DMI.

Alle Abfragen brauchen root.  ``run`` ist ein ``subprocess.run``-kompatibler
Aufruf – im Normalfall ``RootBroker.run``, als root direkt ``subprocess.run``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
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


# Letzter erfolgreich gelesener Stand des BMC-Protokolls (ECC-Tab, Monitoring, Gesamttest).
# Die ECC-Anzeigen der Testtabs lesen ihn, weil sie selbst keine Root-Rechte haben.
last_sel_counts: Optional[SelCounts] = None


def _remember(events) -> None:
    global last_sel_counts
    ue = sum(e.uncorrectable for e in events)
    last_sel_counts = SelCounts(len(events) - ue, ue, time.time())

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


def parse_sdr(text: str) -> list[SdrReading]:
    """``ipmitool sdr type …``: ``Fan 1 | 30h | ok | 29.1 | 30.58 percent``."""
    readings = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 5:
            continue
        match = re.match(r"(-?\d+(?:\.\d+)?)\s*(.*)", parts[4])
        if not match:
            continue  # "no reading", "Disabled", "Transition to Running" …
        unit = match.group(2).strip().lower()
        unit = {"percent": "%", "degrees c": "°C"}.get(unit, unit)
        readings.append(SdrReading(parts[0], float(match.group(1)), unit, parts[2].lower()))
    return readings


def fan_metrics(run: Runner) -> tuple[dict[str, float], list[str]]:
    """Lüfter als Monitoring-Messreihen ``fan.ipmi.<Name> (<Einheit>)``."""
    try:
        result = run(["ipmitool", "sdr", "type", "Fan"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return {}, ["fan.ipmi"]
    if result.returncode:
        return {}, ["fan.ipmi"]
    metrics = {}
    for reading in parse_sdr(result.stdout):
        if reading.unit in {"%", "rpm"}:
            metrics[f"fan.ipmi.{reading.name} ({reading.unit})"] = reading.value
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
