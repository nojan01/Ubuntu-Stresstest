"""SATA-/SAS-/USB-Datenträger (``/dev/sd*``): Inventar über lsblk und SMART-Vergleich über smartctl.

Nur lesende Zugriffe. Die Funktionen nehmen ein ``run``-Callable entgegen, damit sie
sowohl direkt als auch über die Root-Sitzung des Gesamttests laufen können.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional

RunCommand = Callable[..., subprocess.CompletedProcess]

DISK_PATTERN = re.compile(r"/dev/sd[a-z]+")
SMARTCTL_ARGS = ("-a", "-j")

_TRANSPORTS = {"sata": "SATA", "sas": "SAS", "usb": "USB", "ata": "ATA", "scsi": "SCSI",
               "fc": "FC", "iscsi": "iSCSI", "spi": "SPI", "sbp": "FireWire"}


@dataclass(frozen=True)
class BlockDisk:
    path: str
    model: str
    serial: str
    size_bytes: int
    transport: str = ""
    rotational: bool = True
    removable: bool = False
    mounted: bool = False

    @property
    def kind(self) -> str:
        label = _TRANSPORTS.get(self.transport.lower(), self.transport.upper() or "RAID/anderer Bus")
        return label + (" HDD" if self.rotational else " SSD")


@dataclass
class DiskSmart:
    """Für den Vorher/Nachher-Vergleich relevante SMART-Zähler (ATA und SAS/SCSI)."""
    device: str
    available: bool = False
    healthy: Optional[bool] = None
    temperature_c: Optional[int] = None
    power_on_hours: Optional[int] = None
    reallocated_sectors: Optional[int] = None
    pending_sectors: Optional[int] = None
    uncorrectable_sectors: Optional[int] = None
    crc_errors: Optional[int] = None
    grown_defects: Optional[int] = None
    read_uncorrected: Optional[int] = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    # (Feld, Beschriftung) – ein Anstieg gilt als Fehler nach dem Lesetest.
    COUNTERS = (
        ("reallocated_sectors", "Reallocated_Sector_Ct"),
        ("pending_sectors", "Current_Pending_Sector"),
        ("uncorrectable_sectors", "Offline_Uncorrectable"),
        ("crc_errors", "UDMA_CRC_Error_Count"),
        ("grown_defects", "Grown defects"),
        ("read_uncorrected", "Read uncorrected errors"),
    )


def smartctl_available() -> bool:
    return shutil.which("smartctl") is not None


def list_block_disks(run: RunCommand = subprocess.run) -> list[BlockDisk]:
    """Alle ``/dev/sd*``-Datenträger (ohne NVMe, Loop, zram) – ohne Root-Rechte."""
    lsblk = shutil.which("lsblk")
    if not lsblk:
        return []
    try:
        completed = run(
            [lsblk, "-J", "-b", "-o", "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,ROTA,RM,VENDOR,MOUNTPOINTS"],
            check=False, capture_output=True, text=True, timeout=15,
        )
        if completed.returncode != 0:
            return []
        payload = json.loads(completed.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    disks: list[BlockDisk] = []
    for node in payload.get("blockdevices") or []:
        if not isinstance(node, dict) or (node.get("type") or "").lower() != "disk":
            continue
        path = str(node.get("path") or f"/dev/{node.get('name', '')}")
        if not DISK_PATTERN.fullmatch(path):
            continue
        model = " ".join(str(node.get(key) or "").strip() for key in ("vendor", "model")).strip()
        disks.append(BlockDisk(
            path=path,
            model=model,
            serial=str(node.get("serial") or "").strip(),
            size_bytes=int(node.get("size") or 0),
            transport=str(node.get("tran") or ""),
            rotational=bool(node.get("rota", True)),
            removable=bool(node.get("rm", False)),
            mounted=_has_mounts(node),
        ))
    return disks


def _has_mounts(node: dict) -> bool:
    if any(node.get("mountpoints") or []):
        return True
    return any(_has_mounts(child) for child in node.get("children") or [] if isinstance(child, dict))


def read_disk_smart(device: str, run: RunCommand = subprocess.run) -> DiskSmart:
    """``smartctl -a -j`` auswerten (ATA-Attribute 5/197/198/199, SAS-Defektliste und Fehlerzähler)."""
    info = DiskSmart(device=device)
    smartctl = shutil.which("smartctl")
    if not smartctl:
        info.error = "smartctl nicht gefunden (smartmontools)"
        return info
    try:
        completed = run([smartctl, *SMARTCTL_ARGS, device], check=False, capture_output=True, text=True,
                        timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        info.error = str(exc)
        return info
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if completed.returncode & 0x01 and any("unknown usb bridge" in m.lower() for m in _messages(data)):
        # Unbekannte USB-SATA-Bridge: SAT-Passthrough erzwingen
        try:
            retry = run([smartctl, "-d", "sat", *SMARTCTL_ARGS, device], check=False, capture_output=True,
                        text=True, timeout=60)
            retry_data = json.loads(retry.stdout or "{}")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            retry, retry_data = None, None
        if retry is not None and isinstance(retry_data, dict) and retry_data and not retry.returncode & 0x03:
            completed, data = retry, retry_data
    returncode = completed.returncode
    if returncode & 0x03 or not data:
        info.error = "; ".join(_messages(data)) or f"smartctl Exit {returncode}"
        return info
    return parse_disk_smart(device, data, returncode)


def _messages(data: dict) -> List[str]:
    return [str(m.get("string", "")) for m in (data.get("smartctl") or {}).get("messages") or []
            if isinstance(m, dict) and m.get("string")]


def parse_disk_smart(device: str, data: dict, returncode: int = 0) -> DiskSmart:
    info = DiskSmart(device=device, available=True)
    status = data.get("smart_status")
    if isinstance(status, dict) and isinstance(status.get("passed"), bool):
        info.healthy = status["passed"]
    temperature = data.get("temperature")
    if isinstance(temperature, dict) and isinstance(temperature.get("current"), int):
        info.temperature_c = temperature["current"]
    power_on = data.get("power_on_time")
    if isinstance(power_on, dict) and isinstance(power_on.get("hours"), int):
        info.power_on_hours = power_on["hours"]
    attributes = {5: "reallocated_sectors", 197: "pending_sectors", 198: "uncorrectable_sectors",
                  199: "crc_errors"}
    for attr in (data.get("ata_smart_attributes") or {}).get("table") or []:
        if not isinstance(attr, dict):
            continue
        name = attributes.get(attr.get("id"))
        raw = (attr.get("raw") or {}).get("value")
        if name and isinstance(raw, int):
            # Bei einigen Attributen stecken in den oberen Bytes weitere Zähler.
            setattr(info, name, raw & 0xFFFFFFFF)
    grown = data.get("scsi_grown_defect_list")
    if isinstance(grown, int):
        info.grown_defects = grown
    counters = data.get("scsi_error_counter_log")
    if isinstance(counters, dict):
        read = counters.get("read")
        if isinstance(read, dict) and isinstance(read.get("total_uncorrected_errors"), int):
            info.read_uncorrected = read["total_uncorrected_errors"]
    if returncode & 0x08:
        info.healthy = False
        info.warnings.append("smartctl meldet DISK FAILING")
    if returncode & 0x10:
        info.warnings.append("Prefail-Attribut unter Schwellwert")
    if returncode & 0x40:
        info.warnings.append("Einträge im SMART-Fehlerprotokoll")
    if returncode & 0x80:
        info.warnings.append("Fehler im Selbsttest-Protokoll")
    return info


def smart_regressions(before: DiskSmart, after: DiskSmart) -> list[str]:
    """Verschlechterungen zwischen zwei SMART-Abfragen (leer = unauffällig)."""
    problems: list[str] = []
    if after.healthy is False:
        problems.append("SMART-Gesamtstatus: FAILED")
    for name, label in DiskSmart.COUNTERS:
        old, new = getattr(before, name), getattr(after, name)
        if old is not None and new is not None and new > old:
            problems.append(f"{label}: {old} -> {new}")
    return problems


def smart_lines(device: str, before: DiskSmart, after: DiskSmart) -> list[str]:
    lines = []
    for name, label in (("temperature_c", "Temperatur °C"), ("power_on_hours", "Betriebsstunden"),
                        *DiskSmart.COUNTERS):
        old, new = getattr(before, name), getattr(after, name)
        if old is None and new is None:
            continue
        lines.append(f"{device}: {label}: {_fmt(old)} -> {_fmt(new)}")
    return lines


def _fmt(value: object) -> str:
    return "–" if value is None else str(value)
