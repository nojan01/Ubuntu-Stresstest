"""HPE-RAID-Controller strukturiert auswerten: Smart Array (``ssacli``) und MegaRAID (``storcli``).

Die Parser arbeiten auf reinem Text bzw. JSON und sind ohne Hardware testbar.
``collect_raid`` ruft die Werkzeuge über einen einzigen Root-Runner auf
(``RootBroker.run`` oder als root ``subprocess.run``) – keine eigenen
pkexec-Aufrufe.  Die Bewertung (``rate_controller``) liefert Ampelfarben
(``ok``/``warn``/``error``/``info``) mit zweisprachigen Befunden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Optional

Runner = Callable[..., subprocess.CompletedProcess]

# Timeouts: ssacli ist auf großen Konfigurationen langsam (bis zu einer Minute pro Aufruf).
SSACLI_TIMEOUT = 120
STORCLI_TIMEOUT = 90

# Suchpfade außerhalb von PATH. Kommandonamen in Standardverzeichnissen werden als
# Name übergeben (die Root-Sitzung löst sie über ihren festen PATH auf), alle
# anderen als absoluter Pfad – beide Formen stehen in der Freigabeliste der Root-Sitzung.
SSACLI_NAMES = ("ssacli", "hpssacli", "hpacucli")
STORCLI_NAMES = ("storcli64", "storcli2", "storcli")
SSACLI_DIRS = ("/opt/smartstorageadmin/ssacli/bin", "/opt/smartstorageadmin/ssacli/bld",
               "/opt/hp/hpssacli/bld", "/opt/compaq/hpacucli/bld")
STORCLI_DIRS = ("/opt/MegaRAID/storcli", "/opt/MegaRAID/storcli2")
SSADUCLI_DIRS = ("/opt/smartstorageadmin/ssaducli/bin", "/opt/smartstorageadmin/ssaducli/bld")
# Muss dem festen PATH der Root-Sitzung entsprechen (root_broker: /usr/sbin:/usr/bin:/sbin:/bin);
# alles andere (z. B. /usr/local/sbin/storcli64 im Live-ISO) wird als absoluter Pfad übergeben.
_STANDARD_DIRS = ("/usr/sbin", "/usr/bin", "/sbin", "/bin")
_PATH_FALLBACK_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")

# Schwellwerte der Bewertung
SSD_LIFE_ERROR_PERCENT = 10.0
SSD_LIFE_WARN_PERCENT = 25.0
PD_TEMPERATURE_WARN = 60
CONTROLLER_TEMPERATURE_WARN = 85

SEVERITY_ORDER = {"ok": 0, "info": 0, "warn": 1, "error": 2}


def worst(*severities: str) -> str:
    """Schlimmste Ampelstufe aus mehreren."""
    best = "ok"
    for severity in severities:
        if SEVERITY_ORDER.get(severity, 0) > SEVERITY_ORDER.get(best, 0):
            best = severity
    return best


@dataclass
class Finding:
    """Ein Befund der Bewertung mit deutschem und englischem Text."""
    severity: str
    de: str
    en: str
    target: str = ""  # z. B. "Controller", "LD 1", "PD 1I:1:3"

    def text(self, language: str = "de") -> str:
        message = self.en if language == "en" else self.de
        return f"{self.target}: {message}" if self.target else message


@dataclass
class PhysicalDrive:
    location: str = ""            # ssacli "1I:1:3" bzw. storcli "252:3"
    port: str = ""
    box: str = ""
    bay: str = ""
    array: str = ""
    drive_type: str = ""          # "SAS HDD", "SATA SSD", …
    size: str = ""
    status: str = ""
    model: str = ""
    serial: str = ""
    firmware: str = ""
    temperature: Optional[int] = None
    temperature_max: Optional[int] = None
    power_on_hours: Optional[int] = None
    ssd_life_percent: Optional[float] = None
    wear_trip: Optional[bool] = None
    phy_rate: str = ""
    exposed_to_os: Optional[bool] = None
    predictive_failure: bool = False
    media_errors: Optional[int] = None
    other_errors: Optional[int] = None
    extra: dict[str, str] = field(default_factory=dict)
    severity: str = "ok"
    reasons: list[Finding] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"PD {self.location}" if self.location else "PD"


@dataclass
class LogicalDrive:
    number: str = ""
    array: str = ""
    raid_level: str = ""
    size: str = ""
    status: str = ""
    stripe_size: str = ""
    os_device: str = ""
    progress_percent: Optional[float] = None
    parity_init: str = ""
    media_errors: Optional[int] = None
    caching: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    severity: str = "ok"
    reasons: list[Finding] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"LD {self.number}" if self.number else "LD"


@dataclass
class RaidController:
    vendor: str = "ssacli"        # "ssacli" | "storcli"
    model: str = ""
    slot: str = ""
    pci_address: str = ""
    serial: str = ""
    firmware: str = ""
    status: str = ""
    cache_present: Optional[bool] = None
    cache_size: str = ""
    cache_status: str = ""
    cache_ratio: str = ""
    battery_count: Optional[int] = None
    battery_status: str = ""
    battery_temperature: Optional[int] = None
    temperature: Optional[int] = None
    cache_temperature: Optional[int] = None
    rebuild_priority: str = ""
    surface_scan: str = ""
    encryption: str = ""
    drive_write_cache: str = ""
    no_battery_write_cache: str = ""
    mode: str = ""
    driver: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    logical_drives: list[LogicalDrive] = field(default_factory=list)
    physical_drives: list[PhysicalDrive] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    severity: str = "ok"

    @property
    def label(self) -> str:
        slot = f" Slot {self.slot}" if self.slot else ""
        return f"{self.model or self.vendor}{slot}"

    @property
    def key(self) -> str:
        """Stabile Kennung für den Vergleich zweier Berichte."""
        return self.serial or f"{self.model}|{self.slot}|{self.pci_address}"

    def find_pd(self, location: str) -> Optional[PhysicalDrive]:
        for drive in self.physical_drives:
            if drive.location == location:
                return drive
        return None

    def find_ld(self, number: str) -> Optional[LogicalDrive]:
        for drive in self.logical_drives:
            if drive.number == number:
                return drive
        return None

    def all_findings(self) -> list[Finding]:
        findings = list(self.warnings)
        for ld in self.logical_drives:
            findings.extend(ld.reasons)
        for pd in self.physical_drives:
            findings.extend(pd.reasons)
        return findings


@dataclass
class RaidReport:
    available: bool = False
    tools: dict[str, str] = field(default_factory=dict)   # "ssacli"/"storcli"/"ssaducli" -> Pfad
    controllers: list[RaidController] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw_outputs: dict[str, str] = field(default_factory=dict)
    hint: str = ""

    @property
    def severity(self) -> str:
        if not self.available:
            return "info"
        if not self.controllers:
            return "error" if self.errors else "info"
        return worst(*(c.severity for c in self.controllers))

    def findings(self) -> list[Finding]:
        result: list[Finding] = []
        for controller in self.controllers:
            result.extend(controller.all_findings())
        return result

    def raw_text(self) -> str:
        return "\n\n".join(f"=== {name} ===\n{text.rstrip()}" for name, text in self.raw_outputs.items())


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _int(value: str) -> Optional[int]:
    match = re.search(r"-?\d+", value or "")
    return int(match.group()) if match else None


def _float(value: str) -> Optional[float]:
    match = re.search(r"-?\d+(?:\.\d+)?", (value or "").replace(",", "."))
    return float(match.group()) if match else None


def _bool(value: str) -> Optional[bool]:
    text = (value or "").strip().lower()
    if text in {"true", "yes", "enabled", "present", "on"}:
        return True
    if text in {"false", "no", "disabled", "not present", "absent", "off"}:
        return False
    return None


def _percent(value: str) -> Optional[float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", value or "")
    return float(match.group(1)) if match else None


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _drive_type(interface: str, hint: str = "") -> str:
    """``Interface Type: Solid State SAS`` → ``SAS SSD``; ``SAS`` → ``SAS HDD``."""
    text = f"{interface} {hint}".lower()
    if "nvme" in text:
        return "NVMe SSD"
    bus = "SAS" if "sas" in text else "SATA" if "sata" in text else interface.strip() or "?"
    media = "SSD" if ("solid" in text or "ssd" in text) else "HDD"
    return f"{bus} {media}"


# ---------------------------------------------------------------------------
# ssacli (HPE Smart Array)
# ---------------------------------------------------------------------------

_CTRL_HEADER = re.compile(r"^(\S.*?)\s+in\s+Slot\s+(\S+?)\s*(?:\((.*)\))?\s*$", re.I)
_ARRAY_HEADER = re.compile(r"^\s*array:?\s+(\w+)(?:\s*\((.*)\))?\s*$", re.I)
_LD_HEADER = re.compile(r"^\s*(?:logical\s*drive:?)\s+(\d+)(?:\s*\((.*)\))?\s*$", re.I)
_PD_HEADER = re.compile(r"^\s*physicaldrive\s+(\S+)(?:\s*\((.*)\))?\s*$", re.I)
_IGNORE_HEADER = re.compile(r"^\s*(Port Name|Internal Drive Cage|External Drive Cage|Drive Cage|Expander|"
                            r"Enclosure|SEP|Physical Drives|Mirror Group|Parity Group)\b", re.I)
_UNASSIGNED_HEADER = re.compile(r"^\s*(Unassigned|HBA Drives|unassigned)\s*$", re.I)
_PROGRESS = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _ssacli_controller_from_header(model: str, slot: str, note: str) -> RaidController:
    controller = RaidController(vendor="ssacli", model=_collapse(model), slot=slot.strip())
    if note:
        # ``show config``: "(Embedded)    (sn: PDNLH0BRH7V3XP)"
        serial = re.search(r"sn:\s*([A-Za-z0-9-]+)", note)
        if serial:
            controller.serial = serial.group(1)
        controller.extra["Slot note"] = _collapse(re.sub(r"\(?sn:\s*[A-Za-z0-9-]+\)?", "", note).strip(") "))
    return controller


def _apply_ssacli_controller(controller: RaidController, key: str, value: str) -> None:
    mapping = {
        "Serial Number": "serial", "Firmware Version": "firmware", "Controller Status": "status",
        "Cache Status": "cache_status", "Cache Ratio": "cache_ratio",
        "Battery/Capacitor Status": "battery_status", "Rebuild Priority": "rebuild_priority",
        "Surface Scan Mode": "surface_scan", "Encryption": "encryption",
        "Drive Write Cache": "drive_write_cache", "No-Battery Write Cache": "no_battery_write_cache",
        "Controller Mode": "mode", "PCI Address (Domain:Bus:Device.Function)": "pci_address",
    }
    if key in mapping:
        setattr(controller, mapping[key], value)
    elif key == "Slot" and not controller.slot:
        controller.slot = value
    elif key == "Cache Board Present":
        controller.cache_present = _bool(value)
    elif key == "Total Cache Size":
        number = _float(value)
        controller.cache_size = (f"{number:g} GB" if number is not None and "B" not in value.upper() else value)
    elif key == "Battery/Capacitor Count":
        controller.battery_count = _int(value)
    elif key == "Controller Temperature (C)":
        controller.temperature = _int(value)
    elif key == "Cache Module Temperature (C)":
        controller.cache_temperature = _int(value)
    elif key == "Capacitor Temperature (C)":
        controller.battery_temperature = _int(value)
    elif key == "Driver Name":
        controller.driver = value
    elif key == "Driver Version":
        controller.driver = f"{controller.driver} {value}".strip()
    else:
        controller.extra.setdefault(key, value)


def _apply_ssacli_ld(drive: LogicalDrive, key: str, value: str) -> None:
    if key == "Size":
        drive.size = value
    elif key == "Fault Tolerance":
        drive.raid_level = value if value.upper().startswith("RAID") else f"RAID {value}"
    elif key == "Status":
        drive.status = value
        progress = _PROGRESS.search(value)
        if progress:
            drive.progress_percent = float(progress.group(1))
    elif key == "Strip Size":
        drive.stripe_size = value
    elif key == "Disk Name":
        drive.os_device = value
    elif key == "Parity Initialization Status":
        drive.parity_init = value
        progress = _PROGRESS.search(value)
        if progress and drive.progress_percent is None:
            drive.progress_percent = float(progress.group(1))
    elif key == "Unrecoverable Media Errors":
        drive.media_errors = 0 if value.lower() in {"none", "0"} else _int(value)
    elif key == "Caching":
        drive.caching = value
    else:
        drive.extra.setdefault(key, value)


def _apply_ssacli_pd(drive: PhysicalDrive, key: str, value: str) -> None:
    if key == "Port":
        drive.port = value
    elif key == "Box":
        drive.box = value
    elif key == "Bay":
        drive.bay = value
    elif key == "Status":
        drive.status = value
        if "predictive" in value.lower():
            drive.predictive_failure = True
    elif key == "Interface Type":
        drive.drive_type = _drive_type(value, drive.extra.get("Drive Type", ""))
    elif key == "Drive Type":
        drive.extra["Drive Type"] = value
    elif key == "Size":
        drive.size = value
    elif key == "Firmware Revision":
        drive.firmware = value
    elif key == "Serial Number":
        drive.serial = value
    elif key == "Model":
        drive.model = _collapse(value)
    elif key == "Current Temperature (C)":
        drive.temperature = _int(value)
    elif key == "Maximum Temperature (C)":
        drive.temperature_max = _int(value)
    elif key == "Power On Hours":
        drive.power_on_hours = _int(value)
    elif key in {"Usage remaining", "SSD Usage Remaining", "Usage Remaining"}:
        drive.ssd_life_percent = _percent(value) if "%" in value else _float(value)
    elif key == "SSD Smart Trip Wearout":
        drive.wear_trip = _bool(value)
    elif key == "PHY Transfer Rate":
        drive.phy_rate = value
    elif key == "Drive exposed to OS":
        drive.exposed_to_os = _bool(value)
    elif key in {"Medium Error Count", "Media Error Count"}:
        drive.media_errors = _int(value)
    else:
        drive.extra.setdefault(key, value)


def _parse_pd_summary(drive: PhysicalDrive, summary: str) -> None:
    """``port 1I:box 1:bay 1, SAS HDD, 300 GB, OK`` aus ``show config``."""
    parts = [p.strip() for p in summary.split(",")]
    for part in parts:
        lower = part.lower()
        if lower.startswith("port "):
            for token in part.split(":"):
                name, _, number = token.strip().partition(" ")
                if name.lower() == "port":
                    drive.port = number
                elif name.lower() == "box":
                    drive.box = number
                elif name.lower() == "bay":
                    drive.bay = number
        elif re.search(r"\b(sas|sata|nvme|ssd|hdd)\b", lower) and not drive.drive_type:
            drive.drive_type = _drive_type(part)
        elif re.search(r"\d\s*(GB|TB|MB)\b", part) and not drive.size:
            drive.size = part
        elif part and part is parts[-1] and not drive.status:
            drive.status = part
            if "predictive" in lower:
                drive.predictive_failure = True


def _parse_ld_summary(drive: LogicalDrive, summary: str) -> None:
    """``279.4 GB, RAID 1, OK`` bzw. ``279.4 GB, RAID 1, Recovering, 37% complete``."""
    parts = [p.strip() for p in summary.split(",")]
    rest: list[str] = []
    for part in parts:
        if re.search(r"\d\s*(GB|TB|MB)\b", part) and not drive.size:
            drive.size = part
        elif part.upper().startswith("RAID") and not drive.raid_level:
            drive.raid_level = part
        elif part:
            rest.append(part)
    if rest and not drive.status:
        drive.status = ", ".join(rest)
        progress = _PROGRESS.search(drive.status)
        if progress:
            drive.progress_percent = float(progress.group(1))


def parse_ssacli_controllers(text: str) -> list[RaidController]:
    """``ssacli ctrl all show config detail`` / ``show status`` / ``show config`` / ``pd|ld all show detail``.

    Blöcke werden an ihren Überschriften erkannt (Controller ``… in Slot N``, ``Array: A``,
    ``Logical Drive: 1`` bzw. ``logicaldrive 1 (…)``, ``physicaldrive 1I:1:1``); ``Key: Value``-
    Zeilen gehören zum zuletzt begonnenen Block.  Physische Laufwerke, die mehrfach vorkommen
    (Mirror-Group-Liste und Detailblock), werden zusammengeführt.
    """
    controllers: list[RaidController] = []
    controller: Optional[RaidController] = None
    array = ""
    target: object = None  # RaidController | LogicalDrive | PhysicalDrive | None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        header = _CTRL_HEADER.match(line)
        if header and not line.startswith(" ") and ":" not in header.group(1):
            controller = _ssacli_controller_from_header(*header.groups(default=""))
            controllers.append(controller)
            array = ""
            target = controller
            continue
        if controller is None:
            continue
        if _UNASSIGNED_HEADER.match(line):
            array = ""
            target = None
            continue
        match = _ARRAY_HEADER.match(line)
        if match:
            array = match.group(1)
            target = None
            continue
        match = _LD_HEADER.match(line)
        if match:
            number = match.group(1)
            drive = controller.find_ld(number)
            if drive is None:
                drive = LogicalDrive(number=number, array=array)
                controller.logical_drives.append(drive)
            if match.group(2):
                _parse_ld_summary(drive, match.group(2))
            target = drive
            continue
        match = _PD_HEADER.match(line)
        if match:
            location = match.group(1)
            drive = controller.find_pd(location)
            if drive is None:
                drive = PhysicalDrive(location=location, array=array)
                controller.physical_drives.append(drive)
            if not drive.array and array:
                drive.array = array
            if match.group(2):
                _parse_pd_summary(drive, match.group(2))
            else:
                target = drive
            continue
        if _IGNORE_HEADER.match(line):
            # SEP-/Expander-/Drive-Cage-Blöcke gehören zu keinem Laufwerk – Kontext verwerfen.
            target = None
            continue
        key, sep, value = line.partition(": ")
        if not sep:
            if line.strip().endswith(":"):
                continue
            key, sep, value = line.partition(":")
            if not sep:
                continue
        key = _collapse(key)
        value = value.strip()
        if not key or not value:
            continue
        if isinstance(target, PhysicalDrive):
            _apply_ssacli_pd(target, key, value)
        elif isinstance(target, LogicalDrive):
            _apply_ssacli_ld(target, key, value)
        elif target is controller:
            _apply_ssacli_controller(controller, key, value)
    for item in controllers:
        for drive in item.physical_drives:
            if not drive.port and drive.location.count(":") == 2:
                drive.port, drive.box, drive.bay = drive.location.split(":")
    return controllers


def merge_ssacli(base: list[RaidController], extra: list[RaidController]) -> list[RaidController]:
    """Ergänzt Controller aus ``pd/ld all show detail`` in die Liste aus ``show config detail``."""
    for addition in extra:
        existing = next((c for c in base if c.slot == addition.slot and c.vendor == addition.vendor), None)
        if existing is None:
            base.append(addition)
            continue
        for key, value in addition.extra.items():
            existing.extra.setdefault(key, value)
        for name in ("serial", "firmware", "status"):
            if not getattr(existing, name):
                setattr(existing, name, getattr(addition, name))
        for ld in addition.logical_drives:
            current = existing.find_ld(ld.number)
            if current is None:
                existing.logical_drives.append(ld)
                continue
            for name in ("raid_level", "size", "status", "stripe_size", "os_device", "parity_init", "caching"):
                if not getattr(current, name):
                    setattr(current, name, getattr(ld, name))
            if current.progress_percent is None:
                current.progress_percent = ld.progress_percent
            if current.media_errors is None:
                current.media_errors = ld.media_errors
        for pd in addition.physical_drives:
            current = existing.find_pd(pd.location)
            if current is None:
                existing.physical_drives.append(pd)
                continue
            for name in ("drive_type", "size", "status", "model", "serial", "firmware", "phy_rate", "array"):
                if not getattr(current, name):
                    setattr(current, name, getattr(pd, name))
            for name in ("temperature", "temperature_max", "power_on_hours", "ssd_life_percent", "wear_trip",
                         "exposed_to_os", "media_errors"):
                if getattr(current, name) is None:
                    setattr(current, name, getattr(pd, name))
            current.predictive_failure = current.predictive_failure or pd.predictive_failure
            for key, value in pd.extra.items():
                current.extra.setdefault(key, value)
    return base


# ---------------------------------------------------------------------------
# storcli (Broadcom MegaRAID, HPE MR-Controller)
# ---------------------------------------------------------------------------

_STORCLI_PD_STATE = {
    "onln": "Online", "offln": "Offline", "ugood": "Unconfigured Good", "ubad": "Unconfigured Bad",
    "ghs": "Global Hot Spare", "dhs": "Dedicated Hot Spare", "rbld": "Rebuilding", "jbod": "JBOD",
    "failed": "Failed", "msng": "Missing", "cpybck": "Copyback", "ubunsp": "Unconfigured Bad Unsupported",
    "ugunsp": "Unconfigured Good Unsupported",
}
_STORCLI_VD_STATE = {
    "optl": "Optimal", "dgrd": "Degraded", "pdgd": "Partially Degraded", "ofln": "Offline",
    "rec": "Recovering", "cac": "CacheCade",
}


def _json_load(text: str) -> object:
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("keine JSON-Antwort")
    data, _end = json.JSONDecoder().raw_decode(text[start:])
    return data


def _storcli_controllers(text: str) -> list[tuple[dict, dict]]:
    """Liefert ``(Command Status, Response Data)`` je Controller."""
    data = _json_load(text)
    if not isinstance(data, dict):
        raise ValueError("storcli: unerwartetes JSON")
    result = []
    for entry in data.get("Controllers") or []:
        if not isinstance(entry, dict):
            continue
        status = entry.get("Command Status") or {}
        response = entry.get("Response Data") or {}
        if not isinstance(response, dict):
            response = {}
        result.append((status, response))
    return result


def _str(value: object) -> str:
    return "" if value is None else str(value).strip()


def _temp_c(value: object) -> Optional[int]:
    text = _str(value)
    match = re.search(r"(-?\d+)\s*C\b", text)
    if match:
        return int(match.group(1))
    return _int(text) if re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*", text) else None


def _find_key(data: dict, *fragments: str) -> Optional[str]:
    """Erster Schlüssel, dessen Kleinschreibung alle Fragmente enthält."""
    for key in data:
        lower = str(key).lower()
        if all(fragment in lower for fragment in fragments):
            return key
    return None


def _storcli_vd(entry: dict) -> LogicalDrive:
    dg_vd = _str(entry.get("DG/VD"))
    dg, _, vd = dg_vd.partition("/")
    state = _str(entry.get("State"))
    drive = LogicalDrive(
        number=vd or dg_vd, array=f"DG {dg}" if dg else "",
        raid_level=_str(entry.get("TYPE") or entry.get("Type")),
        size=_str(entry.get("Size")),
        status=_STORCLI_VD_STATE.get(state.lower(), state),
        caching=_str(entry.get("Cache")),
    )
    name = _str(entry.get("Name"))
    if name:
        drive.extra["Name"] = name
    for key in ("Access", "Consist", "sCC", "Strip Size", "OS Drive Name"):
        if key in entry and _str(entry[key]):
            if key == "Strip Size":
                drive.stripe_size = _str(entry[key])
            elif key == "OS Drive Name":
                drive.os_device = _str(entry[key])
            else:
                drive.extra[key] = _str(entry[key])
    return drive


def _storcli_pd(entry: dict) -> PhysicalDrive:
    location = _str(entry.get("EID:Slt") or entry.get("EID:Slot"))
    enclosure, _, slot = location.partition(":")
    state = _str(entry.get("State"))
    drive = PhysicalDrive(
        location=location, port=enclosure, bay=slot,
        array=f"DG {entry.get('DG')}" if _str(entry.get("DG")) not in {"", "-"} else "",
        drive_type=_drive_type(_str(entry.get("Intf")), _str(entry.get("Med"))),
        size=_str(entry.get("Size")),
        status=_STORCLI_PD_STATE.get(state.lower(), state),
        model=_collapse(_str(entry.get("Model"))),
    )
    if _str(entry.get("DID")):
        drive.extra["DID"] = _str(entry.get("DID"))
    return drive


def _apply_storcli_pd_details(drive: PhysicalDrive, details: dict) -> None:
    for section_name, section in details.items():
        if not isinstance(section, dict):
            continue
        lower = str(section_name).lower()
        if lower.endswith(" state"):
            drive.media_errors = _int(_str(section.get("Media Error Count"))) if "Media Error Count" in section else drive.media_errors
            drive.other_errors = _int(_str(section.get("Other Error Count"))) if "Other Error Count" in section else drive.other_errors
            if "Drive Temperature" in section:
                drive.temperature = _temp_c(section["Drive Temperature"])
            pf_count = _int(_str(section.get("Predictive Failure Count", "0")))
            smart = _str(section.get("S.M.A.R.T alert flagged by drive")).lower()
            if (pf_count or 0) > 0 or smart == "yes":
                drive.predictive_failure = True
            if _str(section.get("Shield Counter")):
                drive.extra["Shield Counter"] = _str(section["Shield Counter"])
        elif "device attributes" in lower:
            drive.serial = _str(section.get("SN")) or drive.serial
            drive.model = _collapse(_str(section.get("Model Number"))) or drive.model
            drive.firmware = _str(section.get("Firmware Revision")) or drive.firmware
            drive.phy_rate = _str(section.get("Link Speed") or section.get("Device Speed")) or drive.phy_rate
            if _str(section.get("WWN")):
                drive.extra["WWN"] = _str(section["WWN"])
            life_key = _find_key(section, "life") or _find_key(section, "wear")
            if life_key is not None:
                value = _str(section[life_key])
                drive.ssd_life_percent = _percent(value) if "%" in value else _float(value)
        elif "policies" in lower or "settings" in lower:
            if _str(section.get("Drive position")):
                drive.extra["Drive position"] = _str(section["Drive position"])
            if _str(section.get("Last Predictive Failure Event Sequence Number")) not in {"", "0"}:
                drive.predictive_failure = True


def parse_storcli(text: str, details_text: Optional[str] = None) -> list[RaidController]:
    """``storcli64 /call show all J`` (+ optional ``/call/eall/sall show all J``) → Controller.

    Fehlende Felder sind erlaubt; storcli-Versionen unterscheiden sich im Umfang.
    Ungültiges JSON liefert eine leere Liste (der Rohtext bleibt im Bericht sichtbar).
    """
    controllers: list[RaidController] = []
    try:
        entries = _storcli_controllers(text)
    except ValueError:
        return controllers
    for status, response in entries:
        if _str(status.get("Status")).lower() not in {"", "success"} and not response:
            continue
        basics = response.get("Basics") or {}
        version = response.get("Version") or {}
        state = response.get("Status") or {}
        hw = response.get("HwCfg") or {}
        controller = RaidController(
            vendor="storcli",
            model=_collapse(_str(basics.get("Model") or basics.get("Product Name"))),
            slot=_str(basics.get("Controller", status.get("Controller"))),
            pci_address=_str(basics.get("PCI Address")),
            serial=_str(basics.get("Serial Number")),
            firmware=_str(version.get("Firmware Version") or version.get("Firmware Package Build")),
            status=_str(state.get("Controller Status")),
            cache_size=_str(hw.get("On Board Memory Size")),
            mode=_str(state.get("Current Personality")),
            driver=_collapse(f"{_str(version.get('Driver Name'))} {_str(version.get('Driver Version'))}"),
        )
        if _str(version.get("Firmware Package Build")):
            controller.extra["Firmware Package Build"] = _str(version["Firmware Package Build"])
        bbu = _str(hw.get("BBU"))
        cachevault = response.get("Cachevault_Info") or response.get("CacheVault_Info") or []
        bbu_info = response.get("BBU_Info") or []
        backup = (cachevault or bbu_info)
        if backup and isinstance(backup, list) and isinstance(backup[0], dict):
            unit = backup[0]
            controller.battery_count = len(backup)
            controller.battery_status = _str(unit.get("State"))
            controller.battery_temperature = _temp_c(unit.get("Temp"))
            controller.cache_present = True
            if _str(unit.get("Model")):
                controller.extra["Cache backup"] = _str(unit["Model"])
        elif bbu:
            controller.battery_status = bbu
            controller.battery_count = 0 if bbu.lower() == "absent" else None
        if controller.cache_present is None:
            controller.cache_present = bool(controller.cache_size) and controller.cache_size.lower() not in {"0", "0mb", "n/a"}
        roc_key = _find_key(hw, "roc temperature")
        if roc_key is not None:
            controller.temperature = _temp_c(hw[roc_key])
        mem_ce = _int(_str(state.get("Memory Correctable Errors", "0")))
        mem_ue = _int(_str(state.get("Memory Uncorrectable Errors", "0")))
        if mem_ce:
            controller.extra["Cache memory correctable errors"] = str(mem_ce)
        if mem_ue:
            controller.extra["Cache memory uncorrectable errors"] = str(mem_ue)
        controller.cache_status = "OK" if not mem_ue else "Uncorrectable memory errors"
        preserved = _str(state.get("Any Offline VD Cache Preserved"))
        if preserved.lower() == "yes":
            controller.extra["Preserved cache"] = preserved
        for policy_key in ("Policies", "Defaults"):
            policies = response.get(policy_key) or {}
            if isinstance(policies, dict):
                wc = _find_key(policies, "write cache")
                if wc is not None and not controller.drive_write_cache:
                    controller.drive_write_cache = _str(policies[wc])
                rp = _find_key(policies, "rebuild rate")
                if rp is not None and not controller.rebuild_priority:
                    controller.rebuild_priority = _str(policies[rp])
                pr = _find_key(policies, "patrol read")
                if pr is not None and not controller.surface_scan:
                    controller.surface_scan = f"Patrol Read {_str(policies[pr])}"
        for entry in response.get("VD LIST") or []:
            if isinstance(entry, dict):
                controller.logical_drives.append(_storcli_vd(entry))
        for entry in response.get("PD LIST") or []:
            if isinstance(entry, dict):
                controller.physical_drives.append(_storcli_pd(entry))
        controllers.append(controller)

    if details_text:
        try:
            detail_sets = _storcli_controllers(details_text)
        except ValueError:
            detail_sets = []
        for index, (_status, response) in enumerate(detail_sets):
            controller = controllers[index] if index < len(controllers) else None
            if controller is None:
                continue
            for key, value in response.items():
                match = re.match(r"Drive /c\d+/e(\d+)/s(\d+)\s*$", str(key))
                if match and isinstance(value, list) and value and isinstance(value[0], dict):
                    location = f"{match.group(1)}:{match.group(2)}"
                    drive = controller.find_pd(location)
                    if drive is None:
                        drive = _storcli_pd(value[0])
                        drive.location = location
                        controller.physical_drives.append(drive)
                    continue
                match = re.match(r"Drive /c\d+/e(\d+)/s(\d+) - Detailed Information", str(key))
                if match and isinstance(value, dict):
                    location = f"{match.group(1)}:{match.group(2)}"
                    drive = controller.find_pd(location)
                    if drive is None:
                        drive = PhysicalDrive(location=location, port=match.group(1), bay=match.group(2))
                        controller.physical_drives.append(drive)
                    _apply_storcli_pd_details(drive, value)
    return controllers


# ---------------------------------------------------------------------------
# Bewertung
# ---------------------------------------------------------------------------

_OK_STATES = {"ok", "optimal", "online", "onln", "optl", "good", "unconfigured good", "ugood", "jbod",
              "global hot spare", "dedicated hot spare", "ghs", "dhs", "hba mode", ""}
_LD_WARN = re.compile(r"recover|rebuild|interim|degraded|transforming|expanding|migrat|queued|ready for recovery|"
                      r"partially|not configured|unknown", re.I)
_PD_REBUILD = re.compile(r"rebuild|copyback|replacement|erase|recover", re.I)


def _rate_pd(drive: PhysicalDrive) -> None:
    drive.reasons = []
    status = drive.status.lower()
    if drive.predictive_failure:
        drive.reasons.append(Finding("error", "Predictive Failure – Laufwerk vorsorglich tauschen.",
                                     "Predictive failure – replace the drive proactively.", drive.label))
    elif re.search(r"fail|offline|offln|missing|msng|bad|ubad|lost|erro", status):
        drive.reasons.append(Finding("error", f"Status „{drive.status}“.", f"Status “{drive.status}”.", drive.label))
    elif _PD_REBUILD.search(status):
        drive.reasons.append(Finding("warn", f"Laufwerk in „{drive.status}“.", f"Drive is “{drive.status}”.",
                                     drive.label))
    elif status not in _OK_STATES:
        drive.reasons.append(Finding("warn", f"Ungewöhnlicher Status „{drive.status}“.",
                                     f"Unusual status “{drive.status}”.", drive.label))
    if drive.wear_trip:
        drive.reasons.append(Finding("error", "SSD Smart Trip Wearout ausgelöst – SSD ist verschlissen.",
                                     "SSD Smart Trip Wearout triggered – SSD is worn out.", drive.label))
    if drive.ssd_life_percent is not None:
        if drive.ssd_life_percent < SSD_LIFE_ERROR_PERCENT:
            drive.reasons.append(Finding("error", f"SSD-Restlebensdauer nur noch {drive.ssd_life_percent:g} %.",
                                         f"SSD life remaining only {drive.ssd_life_percent:g} %.", drive.label))
        elif drive.ssd_life_percent < SSD_LIFE_WARN_PERCENT:
            drive.reasons.append(Finding("warn", f"SSD-Restlebensdauer {drive.ssd_life_percent:g} %.",
                                         f"SSD life remaining {drive.ssd_life_percent:g} %.", drive.label))
    if drive.temperature is not None and drive.temperature >= PD_TEMPERATURE_WARN:
        drive.reasons.append(Finding("warn", f"Temperatur {drive.temperature} °C.",
                                     f"Temperature {drive.temperature} °C.", drive.label))
    if drive.media_errors:
        drive.reasons.append(Finding("warn", f"{drive.media_errors} Medium-Fehler gezählt.",
                                     f"{drive.media_errors} medium errors counted.", drive.label))
    drive.severity = worst("ok", *(reason.severity for reason in drive.reasons))


def _rate_ld(drive: LogicalDrive) -> None:
    drive.reasons = []
    status = drive.status.lower()
    percent = f" ({drive.progress_percent:g} %)" if drive.progress_percent is not None else ""
    if re.search(r"fail|offline|ofln|lost|unavailable", status):
        drive.reasons.append(Finding("error", f"Status „{drive.status}“ – Daten gefährdet.",
                                     f"Status “{drive.status}” – data at risk.", drive.label))
    elif _LD_WARN.search(status):
        drive.reasons.append(Finding("warn", f"Status „{drive.status}“{percent}.",
                                     f"Status “{drive.status}”{percent}.", drive.label))
    elif status not in _OK_STATES:
        drive.reasons.append(Finding("warn", f"Ungewöhnlicher Status „{drive.status}“.",
                                     f"Unusual status “{drive.status}”.", drive.label))
    if drive.parity_init and re.search(r"progress|queued|not", drive.parity_init, re.I):
        drive.reasons.append(Finding("info", f"Paritätsinitialisierung: {drive.parity_init}.",
                                     f"Parity initialization: {drive.parity_init}.", drive.label))
    if drive.media_errors:
        drive.reasons.append(Finding("error", f"{drive.media_errors} nicht korrigierbare Medienfehler.",
                                     f"{drive.media_errors} unrecoverable media errors.", drive.label))
    drive.severity = worst("ok", *(reason.severity for reason in drive.reasons))


def rate_controller(controller: RaidController) -> tuple[str, list[Finding]]:
    """Bewertet Controller, logische und physische Laufwerke; setzt ``severity`` überall."""
    findings: list[Finding] = []
    status = controller.status.lower()
    if status and status not in _OK_STATES:
        severity = "warn" if re.search(r"attention|degraded|warn", status) else "error"
        findings.append(Finding(severity, f"Controller-Status „{controller.status}“.",
                                f"Controller status “{controller.status}”.", "Controller"))
    battery = controller.battery_status.lower()
    has_cache = controller.cache_present is True or bool(controller.cache_size)
    if battery and re.search(r"fail|not present|absent|missing|recharg|low|replace|degrad|non-?optimal", battery):
        severity = "error" if has_cache and re.search(r"fail|not present|absent|missing|replace", battery) else "warn"
        findings.append(Finding(severity, f"Akku/Kondensator: „{controller.battery_status}“.",
                                f"Battery/capacitor: “{controller.battery_status}”.", "Controller"))
    elif has_cache and controller.battery_count == 0:
        findings.append(Finding("warn", "Cache vorhanden, aber kein Akku/Kondensator – Write-Back-Cache ungesichert.",
                                "Cache present but no battery/capacitor – write-back cache is unprotected.",
                                "Controller"))
    cache = controller.cache_status.lower()
    if cache and cache not in _OK_STATES:
        findings.append(Finding("warn", f"Cache-Status „{controller.cache_status}“.",
                                f"Cache status “{controller.cache_status}”.", "Controller"))
    if controller.temperature is not None and controller.temperature >= CONTROLLER_TEMPERATURE_WARN:
        findings.append(Finding("warn", f"Controller-Temperatur {controller.temperature} °C.",
                                f"Controller temperature {controller.temperature} °C.", "Controller"))
    if controller.no_battery_write_cache.lower() == "enabled":
        findings.append(Finding("warn", "No-Battery Write Cache ist aktiviert – Datenverlust bei Stromausfall möglich.",
                                "No-battery write cache is enabled – data loss possible on power failure.",
                                "Controller"))
    if controller.drive_write_cache.lower() in {"enabled", "on", "wb"} and (
            not has_cache or re.search(r"fail|not present|absent", battery) or controller.battery_count == 0):
        findings.append(Finding("warn", "Laufwerks-Schreibcache aktiv ohne gesicherten Controller-Cache.",
                                "Drive write cache enabled without protected controller cache.", "Controller"))
    if controller.extra.get("Preserved cache", "").lower() == "yes":
        findings.append(Finding("warn", "Controller hält Cache eines ausgefallenen logischen Laufwerks zurück.",
                                "Controller preserves cache of an offline virtual drive.", "Controller"))
    if controller.extra.get("Cache memory uncorrectable errors"):
        findings.append(Finding("error", "Unkorrigierbare Fehler im Controller-Cache-Speicher.",
                                "Uncorrectable errors in controller cache memory.", "Controller"))
    if not controller.logical_drives and not controller.physical_drives and controller.mode.lower() != "hba":
        findings.append(Finding("info", "Keine Laufwerke am Controller konfiguriert.",
                                "No drives configured on the controller.", "Controller"))
    controller.warnings = findings
    for ld in controller.logical_drives:
        _rate_ld(ld)
    for pd in controller.physical_drives:
        _rate_pd(pd)
    controller.severity = worst("ok", *(f.severity for f in findings),
                                *(ld.severity for ld in controller.logical_drives),
                                *(pd.severity for pd in controller.physical_drives))
    return controller.severity, controller.all_findings()


def rate_report(report: RaidReport) -> RaidReport:
    for controller in report.controllers:
        rate_controller(controller)
    return report


# ---------------------------------------------------------------------------
# Werkzeugsuche und Sammeln
# ---------------------------------------------------------------------------

def _locate(names: tuple[str, ...], extra_dirs: tuple[str, ...]) -> Optional[str]:
    """Sucht Werkzeuge nur nach Existenz, nicht nach Ausführbarkeit: HPE installiert
    ssacli/ssaducli mit Modus 0500 (nur root), ausgeführt wird ohnehin in der Root-Sitzung."""
    for name in names:
        path = shutil.which(name, mode=os.F_OK)
        if path:
            return path
    for directory in (*_PATH_FALLBACK_DIRS, *extra_dirs):
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_file():
                return str(candidate)
    return None


def find_tools() -> dict[str, str]:
    """``{"ssacli": Pfad, "storcli": Pfad, "ssaducli": Pfad}`` – nur gefundene Werkzeuge."""
    tools: dict[str, str] = {}
    ssacli = _locate(SSACLI_NAMES, SSACLI_DIRS)
    if ssacli:
        tools["ssacli"] = ssacli
    storcli = _locate(STORCLI_NAMES, STORCLI_DIRS)
    if storcli:
        tools["storcli"] = storcli
    ssaducli = _locate(("ssaducli",), SSADUCLI_DIRS)
    if ssaducli:
        tools["ssaducli"] = ssaducli
    return tools


def tools_available() -> bool:
    return any(key in find_tools() for key in ("ssacli", "storcli"))


def command_name(path: str) -> str:
    """Name statt Pfad für Werkzeuge in Standardverzeichnissen (Root-PATH ist fest)."""
    directory, name = os.path.split(path)
    return name if directory in _STANDARD_DIRS else path


def _run_text(run: Runner, cmd: list[str], timeout: float) -> tuple[Optional[str], str]:
    try:
        result = run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return None, str(exc)
    output = result.stdout or ""
    if result.returncode and not output.strip():
        return None, (result.stderr or "").strip() or f"Exit {result.returncode}"
    return output, ""


_SLOT_LINE = re.compile(r"\bin Slot (\d+)")
# ssacli ohne Smart-Array-Controller: "Error: No controllers detected." mit Exit 1 – kein Fehler.
_NO_CONTROLLER = re.compile(r"no controllers? (detected|found)", re.IGNORECASE)


def collect_raid(run: Optional[Runner], tools: Optional[dict[str, str]] = None) -> RaidReport:
    """Liest alle RAID-Controller über **einen** Root-Runner. Ohne Werkzeug: ``available=False``."""
    report = RaidReport(tools=find_tools() if tools is None else dict(tools))
    if "ssacli" not in report.tools and "storcli" not in report.tools:
        report.hint = ("Kein RAID-Werkzeug gefunden (ssacli/storcli64) / "
                       "no RAID tool found (ssacli/storcli64)")
        return report
    report.available = True
    if run is None:
        report.errors.append("Keine Root-Rechte / no root privileges")
        return report

    if "ssacli" in report.tools:
        tool = command_name(report.tools["ssacli"])
        text, error = _run_text(run, [tool, "ctrl", "all", "show", "config", "detail"], SSACLI_TIMEOUT)
        if text is None:
            if _NO_CONTROLLER.search(error):
                report.raw_outputs["ssacli ctrl all show config detail"] = error
            else:
                report.errors.append(f"ssacli: {error}")
        else:
            report.raw_outputs["ssacli ctrl all show config detail"] = text
            try:
                controllers = parse_ssacli_controllers(text)
            except Exception as exc:  # Parser darf das Panel nie abstürzen lassen
                controllers = []
                report.errors.append(f"ssacli parse: {exc}")
            # "show status" liefert den Controller-/Cache-/Akkustatus auch dann, wenn
            # "config detail" einzelne Werte auslässt (ältere Controller, HBA-Modus).
            status_text, _error = _run_text(run, [tool, "ctrl", "all", "show", "status"], SSACLI_TIMEOUT)
            if status_text:
                report.raw_outputs["ssacli ctrl all show status"] = status_text
                merge_ssacli(controllers, parse_ssacli_controllers(status_text))
            for controller in controllers:
                if not controller.physical_drives and controller.slot.isdigit():
                    pd_text, _error = _run_text(
                        run, [tool, "ctrl", f"slot={controller.slot}", "pd", "all", "show", "detail"], SSACLI_TIMEOUT)
                    if pd_text:
                        report.raw_outputs[f"ssacli ctrl slot={controller.slot} pd all show detail"] = pd_text
                        merge_ssacli(controllers, parse_ssacli_controllers(pd_text))
            report.controllers.extend(controllers)

    if "storcli" in report.tools:
        tool = command_name(report.tools["storcli"])
        text, error = _run_text(run, [tool, "/call", "show", "all", "J"], STORCLI_TIMEOUT)
        if text is None:
            report.errors.append(f"storcli: {error}")
        else:
            report.raw_outputs["storcli /call show all J"] = text
            details, _error = _run_text(run, [tool, "/call/eall/sall", "show", "all", "J"], STORCLI_TIMEOUT)
            if details:
                report.raw_outputs["storcli /call/eall/sall show all J"] = details
            try:
                report.controllers.extend(parse_storcli(text, details))
            except Exception as exc:
                report.errors.append(f"storcli parse: {exc}")
    # TODO: smartctl -d cciss,N /dev/sgX für Laufwerke ohne "Drive exposed to OS" als Zusatz.
    return rate_report(report)


def save_ssaducli_report(run: Runner, target_zip: str, tools: Optional[dict[str, str]] = None) -> tuple[bool, str]:
    """``ssaducli -f <zip>`` – vollständiger HPE-Diagnosebericht (Smart Storage Diagnostics)."""
    tools = find_tools() if tools is None else tools
    if "ssaducli" not in tools:
        return False, "ssaducli nicht gefunden / not found"
    target = str(target_zip)
    if not target.endswith(".zip") or not os.path.isabs(target) or ".." in target or any(ch.isspace() for ch in target):
        return False, "Zielpfad muss absolut sein und auf .zip enden (ohne Leerzeichen)"
    try:
        result = run([command_name(tools["ssaducli"]), "-f", target], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return False, str(exc)
    if result.returncode:
        return False, (result.stderr or result.stdout or "").strip() or f"Exit {result.returncode}"
    return True, target


# ---------------------------------------------------------------------------
# Vergleich zweier Berichte (Gesamttest: vorher/nachher)
# ---------------------------------------------------------------------------

def diff_reports(before: Optional[RaidReport], after: Optional[RaidReport], language: str = "de") -> list[str]:
    """Neue Fehler/Warnungen, Statuswechsel und neue Medium-Fehler zwischen zwei Berichten."""
    if before is None or after is None:
        return []
    en = language == "en"
    lines: list[str] = []
    before_map = {c.key: c for c in before.controllers}
    for controller in after.controllers:
        old = before_map.get(controller.key)
        name = controller.label
        if old is None:
            lines.append(f"{name}: " + ("newly detected controller" if en else "neu erkannter Controller"))
            continue
        if old.status != controller.status:
            lines.append(f"{name}: " + (f"controller status {old.status or '?'} → {controller.status or '?'}" if en
                                        else f"Controller-Status {old.status or '?'} → {controller.status or '?'}"))
        if old.battery_status != controller.battery_status:
            lines.append(f"{name}: " + (f"battery/capacitor {old.battery_status or '?'} → {controller.battery_status or '?'}"
                                        if en else
                                        f"Akku/Kondensator {old.battery_status or '?'} → {controller.battery_status or '?'}"))
        for ld in controller.logical_drives:
            old_ld = old.find_ld(ld.number)
            if old_ld is not None and old_ld.status != ld.status:
                lines.append(f"{name} {ld.label}: " + (f"status {old_ld.status} → {ld.status}" if en
                                                       else f"Status {old_ld.status} → {ld.status}"))
        for pd in controller.physical_drives:
            old_pd = old.find_pd(pd.location)
            if old_pd is None:
                continue
            if old_pd.status != pd.status:
                lines.append(f"{name} {pd.label}: " + (f"status {old_pd.status} → {pd.status}" if en
                                                       else f"Status {old_pd.status} → {pd.status}"))
            if (pd.media_errors or 0) > (old_pd.media_errors or 0):
                lines.append(f"{name} {pd.label}: " + (
                    f"medium errors {old_pd.media_errors or 0} → {pd.media_errors}" if en
                    else f"Medium-Fehler {old_pd.media_errors or 0} → {pd.media_errors}"))
            if pd.predictive_failure and not old_pd.predictive_failure:
                lines.append(f"{name} {pd.label}: " + ("new predictive failure" if en else "neuer Predictive Failure"))
        old_texts = {(f.severity, f.text(language)) for f in old.all_findings()}
        for finding in controller.all_findings():
            if finding.severity in {"warn", "error"} and (finding.severity, finding.text(language)) not in old_texts:
                label = ("ERROR" if finding.severity == "error" else "WARNING") if en else (
                    "FEHLER" if finding.severity == "error" else "WARNUNG")
                lines.append(f"{name}: [{label}] {finding.text(language)}")
    # Doppelte Zeilen (z. B. Statuswechsel + gleichlautender neuer Befund) nur einmal nennen.
    seen: set[str] = set()
    unique = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique


def summary_line(report: RaidReport, language: str = "de") -> str:
    """Kurzfassung für Übersichten: Anzahl Controller/LD/PD mit Fehlern/Warnungen."""
    en = language == "en"
    if not report.available:
        return report.hint or ("No RAID tool" if en else "Kein RAID-Werkzeug")
    if not report.controllers:
        if report.errors:
            return ("RAID: " + "; ".join(report.errors))
        return "RAID: " + ("no controller found" if en else "kein Controller gefunden")
    findings = report.findings()
    errors = sum(f.severity == "error" for f in findings)
    warns = sum(f.severity == "warn" for f in findings)
    lds = sum(len(c.logical_drives) for c in report.controllers)
    pds = sum(len(c.physical_drives) for c in report.controllers)
    if en:
        return (f"RAID: {len(report.controllers)} controller(s), {lds} logical, {pds} physical drives "
                f"({errors} errors, {warns} warnings)")
    return (f"RAID: {len(report.controllers)} Controller, {lds} logische, {pds} physische Laufwerke "
            f"({errors} Fehler, {warns} Warnungen)")
