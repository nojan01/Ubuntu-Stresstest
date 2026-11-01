"""Weitere Komponenten für den Tab „System“: PCIe-Links, Netzwerk, Grafik, Speicher-Controller, Datenträger.

Alles ohne root aus ``/sys`` sowie ``lspci``, ``ethtool``, ``lsblk`` und – falls vorhanden – ``nvidia-smi``.
Fehlende Programme oder Werte bleiben leer; das Einlesen bricht nie ab.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Optional

SYSFS = Path("/sys")
_PCI_ADDRESS = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
_TOOL_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
_TIMEOUT = 10
# GT/s je Lane → PCIe-Generation
_GENERATIONS = {"2.5": 1, "5.0": 2, "8.0": 3, "16.0": 4, "32.0": 5, "64.0": 6}
_SKIP_DISKS = ("loop", "ram", "zram", "fd", "sr")

LINK_OK = "ok"
LINK_WIDTH = "width"   # weniger Lanes als möglich: Sitz, Riser, Steckplatz – immer auffällig
LINK_SPEED = "speed"   # niedrigere Generation: im Leerlauf (Energiesparen) oft normal


@dataclass
class PciDevice:
    address: str
    class_code: int
    vendor_id: int = 0
    device_id: int = 0
    vendor: str = ""
    name: str = ""
    driver: str = ""
    current_speed: str = ""
    current_width: int = 0
    max_speed: str = ""
    max_width: int = 0
    port_max_speed: str = ""
    port_max_width: int = 0

    @property
    def model(self) -> str:
        return " ".join(filter(None, (self.vendor, self.name))) or f"{self.vendor_id:04x}:{self.device_id:04x}"

    @property
    def has_link(self) -> bool:
        return bool(generation(self.max_speed)) and 0 < self.max_width < 255

    @property
    def possible_generation(self) -> int:
        own = generation(self.max_speed)
        port = generation(self.port_max_speed)
        return min(own, port) if own and port else own

    @property
    def possible_width(self) -> int:
        if 0 < self.port_max_width < 255:
            return min(self.max_width, self.port_max_width)
        return self.max_width

    @property
    def link_state(self) -> str:
        if self.current_width and self.current_width < self.possible_width:
            return LINK_WIDTH
        current = generation(self.current_speed)
        if current and current < self.possible_generation:
            return LINK_SPEED
        return LINK_OK


@dataclass
class NetInterface:
    name: str
    pci: str = ""
    model: str = ""
    driver: str = ""
    firmware: str = ""
    mac: str = ""
    state: str = ""
    speed_mbps: Optional[int] = None


@dataclass
class GraphicsDevice:
    pci: str
    model: str
    driver: str = ""
    vram: str = ""
    vbios: str = ""


@dataclass
class StorageController:
    pci: str
    model: str
    driver: str = ""
    firmware: str = ""
    kind: str = ""


@dataclass
class Disk:
    name: str
    model: str = ""
    serial: str = ""
    firmware: str = ""
    size: int = 0
    rotational: Optional[bool] = None
    transport: str = ""
    logical_sector: Optional[int] = None
    physical_sector: Optional[int] = None

    @property
    def media(self) -> str:
        if self.rotational:
            return "HDD"
        if self.rotational is False:
            return "SSD"
        return ""


@dataclass
class DeviceInventory:
    pci: list[PciDevice] = field(default_factory=list)
    network: list[NetInterface] = field(default_factory=list)
    graphics: list[GraphicsDevice] = field(default_factory=list)
    controllers: list[StorageController] = field(default_factory=list)
    disks: list[Disk] = field(default_factory=list)

    @property
    def links(self) -> list[PciDevice]:
        """Endgeräte mit PCIe-Link (ohne Brücken/Root-Ports)."""
        return [device for device in self.pci if device.has_link and device.class_code != 0x0604]


# ---------------------------------------------------------------------------
# Einlesen
# ---------------------------------------------------------------------------

def generation(speed: str) -> int:
    match = re.match(r"\s*([\d.]+)\s*GT/s", speed or "")
    return _GENERATIONS.get(match.group(1), 0) if match else 0


def link_text(speed: str, width: int) -> str:
    gen = generation(speed)
    if not gen or not 0 < width < 255:
        return ""
    return f"Gen{gen} x{width}"


def parse_lspci(text: str) -> dict[str, dict[str, str]]:
    """``lspci -vmm -D`` → {Adresse: {Feld: Wert}}."""
    result: dict[str, dict[str, str]] = {}
    for block in text.strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(":")
            if value:
                fields.setdefault(key.strip(), value.strip())
        if "Slot" in fields:
            result[fields["Slot"].lower()] = fields
    return result


def _run(run: Callable, command: list[str]) -> str:
    try:
        completed = run(command, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _tool(name: str) -> Optional[str]:
    return shutil.which(name) or shutil.which(name, path=_TOOL_PATH)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _hex(path: Path) -> int:
    try:
        return int(_read(path), 16)
    except ValueError:
        return 0


def _int(path: Path) -> int:
    try:
        return int(_read(path))
    except ValueError:
        return 0


def _link_name(path: Path) -> str:
    try:
        return path.resolve().name if path.exists() else ""
    except OSError:
        return ""


def read_pci_devices(sysfs: Path = SYSFS, names: Optional[dict[str, dict[str, str]]] = None) -> list[PciDevice]:
    names = names or {}
    devices = []
    root = sysfs / "bus" / "pci" / "devices"
    if not root.is_dir():
        return []
    for entry in sorted(root.iterdir()):
        address = entry.name.lower()
        info = names.get(address, {})
        device = PciDevice(
            address=address, class_code=_hex(entry / "class") >> 8 if _read(entry / "class") else 0,
            vendor_id=_hex(entry / "vendor"), device_id=_hex(entry / "device"),
            vendor=info.get("Vendor", ""), name=info.get("Device", ""), driver=_link_name(entry / "driver"),
            current_speed=_read(entry / "current_link_speed"), current_width=_int(entry / "current_link_width"),
            max_speed=_read(entry / "max_link_speed"), max_width=_int(entry / "max_link_width"),
        )
        try:
            parent = entry.resolve().parent
        except OSError:
            parent = None
        if parent is not None and _PCI_ADDRESS.match(parent.name):
            device.port_max_speed = _read(parent / "max_link_speed")
            device.port_max_width = _int(parent / "max_link_width")
        devices.append(device)
    return devices


def _pci_of(path: Path) -> str:
    """PCI-Adresse eines ``…/device``-Links; leer bei Nicht-PCI-Geräten (z. B. USB)."""
    try:
        resolved = path.resolve()
    except OSError:
        return ""
    return resolved.name if _PCI_ADDRESS.match(resolved.name) else ""


def parse_ethtool_firmware(text: str) -> str:
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "firmware-version":
            value = value.strip()
            return "" if value.lower() in ("", "n/a") else value
    return ""


def read_network(sysfs: Path, pci: dict[str, PciDevice], run: Callable) -> list[NetInterface]:
    root = sysfs / "class" / "net"
    if not root.is_dir():
        return []
    ethtool = _tool("ethtool")
    interfaces = []
    for entry in sorted(root.iterdir()):
        if not (entry / "device").exists():
            continue  # lo, Bridges, VLANs, Bonds
        address = _pci_of(entry / "device")
        speed = _int(entry / "speed")
        interface = NetInterface(
            name=entry.name, pci=address, driver=_link_name(entry / "device" / "driver"),
            mac=_read(entry / "address"), state=_read(entry / "operstate"),
            speed_mbps=speed if speed > 0 else None,
            model=pci[address].model if address in pci else "",
        )
        if ethtool:
            interface.firmware = parse_ethtool_firmware(_run(run, [ethtool, "-i", entry.name]))
        interfaces.append(interface)
    return interfaces


def parse_nvidia_smi(text: str) -> dict[str, tuple[str, str]]:
    """``nvidia-smi --query-gpu=pci.bus_id,memory.total,vbios_version --format=csv,noheader``."""
    result = {}
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 3:
            result[parts[0].lower()[-12:]] = (parts[1], parts[2])
    return result


def read_graphics(sysfs: Path, devices: list[PciDevice], run: Callable) -> list[GraphicsDevice]:
    found = [device for device in devices if device.class_code >> 8 == 0x03]
    nvidia = {}
    smi = _tool("nvidia-smi")
    if smi and any(device.vendor_id == 0x10DE and device.driver == "nvidia" for device in found):
        nvidia = parse_nvidia_smi(_run(run, [smi, "--query-gpu=pci.bus_id,memory.total,vbios_version",
                                             "--format=csv,noheader"]))
    result = []
    for device in found:
        entry = sysfs / "bus" / "pci" / "devices" / device.address
        gpu = GraphicsDevice(pci=device.address, model=device.model, driver=device.driver)
        vram = _int(entry / "mem_info_vram_total")
        if vram:
            gpu.vram = _human(vram)
        gpu.vbios = _read(entry / "vbios_version")
        if device.address in nvidia:
            gpu.vram, gpu.vbios = nvidia[device.address]
        if not gpu.vram and device.address.startswith("0000:00:") and device.driver in ("i915", "xe", "amdgpu"):
            gpu.vram = "shared"
        result.append(gpu)
    return result


_FIRMWARE_ATTRS = ("firmware_revision", "fw_version", "firmware_version", "version_fw", "fwrev")
_CONTROLLER_KINDS = {0x00: "SCSI", 0x01: "IDE", 0x04: "RAID", 0x05: "ATA", 0x06: "SATA", 0x07: "SAS", 0x08: "NVMe"}


def read_controllers(sysfs: Path, devices: list[PciDevice]) -> list[StorageController]:
    result = []
    for device in devices:
        if device.class_code >> 8 != 0x01:
            continue
        entry = sysfs / "bus" / "pci" / "devices" / device.address
        firmware = ""
        for host in sorted(entry.glob("host*/scsi_host/host*")):
            for attr in _FIRMWARE_ATTRS:
                firmware = _read(host / attr)
                if firmware:
                    break
            if firmware:
                break
        if not firmware:
            for nvme in sorted(entry.glob("nvme/nvme*")):
                firmware = _read(nvme / "firmware_rev")
                if firmware:
                    break
        result.append(StorageController(
            pci=device.address, model=device.model, driver=device.driver, firmware=firmware,
            kind=_CONTROLLER_KINDS.get(device.class_code & 0xFF, ""),
        ))
    return result


def parse_lsblk(text: str) -> list[Disk]:
    try:
        devices = json.loads(text).get("blockdevices", [])
    except (ValueError, AttributeError):
        return []
    disks = []
    for item in devices:
        name = item.get("name") or ""
        if item.get("type") != "disk" or name.startswith(_SKIP_DISKS):
            continue
        rota = item.get("rota")
        if isinstance(rota, str):
            rota = rota.strip() == "1"
        disks.append(Disk(
            name=name, model=_clean(item.get("model")) or _clean(item.get("vendor")),
            serial=_clean(item.get("serial")), firmware=_clean(item.get("rev")),
            size=int(item.get("size") or 0), rotational=rota, transport=_clean(item.get("tran")),
            logical_sector=_int_or_none(item.get("log-sec")), physical_sector=_int_or_none(item.get("phy-sec")),
        ))
    return disks


def read_disks(run: Callable) -> list[Disk]:
    lsblk = _tool("lsblk")
    if not lsblk:
        return []
    return parse_lsblk(_run(run, [lsblk, "-J", "-d", "-b", "-o",
                                  "NAME,MODEL,VENDOR,SERIAL,REV,SIZE,ROTA,TRAN,LOG-SEC,PHY-SEC,TYPE"]))


def read_device_inventory(sysfs: Path = SYSFS, run: Optional[Callable] = None) -> DeviceInventory:
    run = run or subprocess.run
    lspci = _tool("lspci")
    names = parse_lspci(_run(run, [lspci, "-vmm", "-D"])) if lspci else {}
    devices = read_pci_devices(sysfs, names)
    by_address = {device.address: device for device in devices}
    return DeviceInventory(
        pci=devices,
        network=read_network(sysfs, by_address, run),
        graphics=read_graphics(sysfs, devices, run),
        controllers=read_controllers(sysfs, devices),
        disks=read_disks(run),
    )


# ---------------------------------------------------------------------------
# Textfassung (Bericht, Zwischenablage)
# ---------------------------------------------------------------------------

def _tt(language: str, de: str, en: str) -> str:
    return en if language == "en" else de


def link_status_text(language: str, device: PciDevice) -> str:
    state = device.link_state
    if state == LINK_WIDTH:
        return _tt(language, "Breite reduziert", "width reduced")
    if state == LINK_SPEED:
        return _tt(language, "Generation reduziert (im Leerlauf oft normal)", "generation reduced (often normal at idle)")
    return "OK"


def speed_text(language: str, mbps: Optional[int]) -> str:
    if not mbps:
        return ""
    return f"{mbps // 1000} Gbit/s" if mbps >= 1000 and mbps % 1000 == 0 else f"{mbps} Mbit/s"


def vram_text(language: str, value: str) -> str:
    return _tt(language, "gemeinsamer Arbeitsspeicher", "shared system memory") if value == "shared" else value


def disk_type_text(disk: Disk) -> str:
    return " ".join(filter(None, (disk.transport.upper() if disk.transport else "", disk.media)))


def device_lines(language: str, inventory: DeviceInventory) -> list[str]:
    lines: list[str] = []
    links = inventory.links
    if links:
        lines.append(_tt(language, "PCIe-Links (aktuell / möglich):", "PCIe links (current / possible):"))
        for device in links:
            possible = f"Gen{device.possible_generation} x{device.possible_width}"
            lines.append(f"  {device.address} {device.model}: {link_text(device.current_speed, device.current_width) or '–'}"
                         f" / {possible} – {link_status_text(language, device)}")
    for interface in inventory.network:
        lines.append(_tt(language, "Netzwerk ", "Network ") + f"{interface.name}: " + " · ".join(filter(None, (
            interface.model, interface.driver, f"Firmware {interface.firmware}" if interface.firmware else "",
            interface.mac, interface.state, speed_text(language, interface.speed_mbps), interface.pci,
        ))))
    for gpu in inventory.graphics:
        lines.append(_tt(language, "Grafik ", "Graphics ") + f"{gpu.pci}: " + " · ".join(filter(None, (
            gpu.model, gpu.driver, f"VRAM {vram_text(language, gpu.vram)}" if gpu.vram else "",
            f"VBIOS {gpu.vbios}" if gpu.vbios else "",
        ))))
    for controller in inventory.controllers:
        lines.append(_tt(language, "Speicher-Controller ", "Storage controller ") + f"{controller.pci}: "
                     + " · ".join(filter(None, (controller.kind, controller.model, controller.driver,
                                                f"Firmware {controller.firmware}" if controller.firmware else ""))))
    for disk in inventory.disks:
        lines.append(_tt(language, "Datenträger ", "Disk ") + f"{disk.name}: " + " · ".join(filter(None, (
            disk.model, _human(disk.size) if disk.size else "", disk_type_text(disk),
            f"S/N {disk.serial}" if disk.serial else "", f"Firmware {disk.firmware}" if disk.firmware else "",
            sector_text(disk),
        ))))
    return lines


def sector_text(disk: Disk) -> str:
    if not disk.logical_sector:
        return ""
    if disk.physical_sector and disk.physical_sector != disk.logical_sector:
        return f"{disk.logical_sector}/{disk.physical_sector} B"
    return f"{disk.logical_sector} B"


def _clean(value) -> str:
    return " ".join(str(value).split()) if value not in (None, "") else ""


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _human(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"
