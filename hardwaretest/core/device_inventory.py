"""Weitere Komponenten für den Tab „System“: PCIe-Links, Netzwerk, Grafik, Speicher-Controller, Datenträger, USB.

Alles ohne root aus ``/sys`` sowie ``lspci``, ``ethtool``, ``lsblk``, ``lsusb`` und – falls vorhanden – ``nvidia-smi``.
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
# USB-Geschwindigkeiten laut sysfs ``speed`` (Mbit/s) → Bezeichnung
_USB_SPEEDS = {1: "Low Speed (1,5 Mbit/s)", 12: "Full Speed (12 Mbit/s)", 480: "High Speed (480 Mbit/s)",
               5000: "SuperSpeed (5 Gbit/s)", 10000: "SuperSpeed+ (10 Gbit/s)", 20000: "SuperSpeed+ (20 Gbit/s)",
               40000: "USB4 (40 Gbit/s)"}
_USB_CLASSES = {0x01: "Audio", 0x02: "Kommunikation", 0x03: "HID", 0x05: "Physical", 0x06: "Bild", 0x07: "Drucker",
                0x08: "Massenspeicher", 0x09: "Hub", 0x0a: "CDC-Daten", 0x0b: "Chipkarte", 0x0d: "Content Security",
                0x0e: "Video", 0x0f: "Gesundheit", 0x10: "Audio/Video", 0xdc: "Diagnose", 0xe0: "Funk (Bluetooth/WLAN)",
                0xef: "Verschiedenes", 0xfe: "Anwendungsspezifisch", 0xff: "Herstellerspezifisch"}

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
class UsbDevice:
    port: str                 # sysfs-Name, z. B. "usb3" (Root-Hub) oder "3-10.2"
    bus: int
    device: int
    vendor_id: int
    product_id: int
    manufacturer: str = ""
    product: str = ""
    serial: str = ""
    speed_mbps: int = 0       # ausgehandelte Geschwindigkeit
    usb_version: str = ""     # vom Gerät gemeldete USB-Version (bcdUSB)
    device_class: int = 0
    interface_classes: tuple[int, ...] = ()
    drivers: tuple[str, ...] = ()
    max_children: int = 0
    depth: int = 0            # 0 = Root-Hub, 1 = direkt am Root-Hub, …
    pci: str = ""             # Host-Controller (nur Root-Hubs)

    @property
    def is_root_hub(self) -> bool:
        return self.port.startswith("usb")

    @property
    def is_hub(self) -> bool:
        return self.device_class == 0x09 or self.max_children > 0

    @property
    def model(self) -> str:
        if self.is_root_hub and self.product:
            # Hersteller ist beim Root-Hub nur „Linux <Kernel> xhci-hcd“.
            driver = self.manufacturer.rsplit(" ", 1)[-1] if self.manufacturer else ""
            return self.product + (f" ({driver})" if driver else "")
        name = " ".join(dict.fromkeys(filter(None, (self.manufacturer, self.product)))).strip()
        return name or f"{self.vendor_id:04x}:{self.product_id:04x}"

    @property
    def kind(self) -> str:
        if self.is_root_hub:
            return "Root-Hub"
        classes = [self.device_class] if self.device_class not in (0, 0xef) else list(self.interface_classes)
        labels = list(dict.fromkeys(_USB_CLASSES.get(c, f"0x{c:02x}") for c in classes if c))
        return ", ".join(labels) or _USB_CLASSES[0xef]


_TB_SECURITY = {
    "none": ("Keine – alle Geräte automatisch", "None – all devices auto-authorized"),
    "user": ("Benutzer – Freigabe nötig", "User – authorization required"),
    "secure": ("Sicher – Freigabe mit Schlüssel", "Secure – key-based authorization"),
    "dponly": ("Nur DisplayPort", "DisplayPort only"),
    "usbonly": ("Nur USB/DisplayPort (kein PCIe)", "USB/DisplayPort only (no PCIe)"),
    "nopcie": ("Kein PCIe-Tunnel", "No PCIe tunneling"),
}


@dataclass
class ThunderboltDevice:
    route: str                 # sysfs-Name, z. B. "0-0" (Host-Router) oder "0-1", "0-301"
    domain: int = 0
    name: str = ""
    vendor: str = ""
    vendor_id: int = 0
    device_id: int = 0
    unique_id: str = ""
    generation: int = 0        # 1–3 = Thunderbolt 1–3, 4 = USB4/Thunderbolt 4
    nvm_version: str = ""
    authorized: Optional[int] = None   # 0 = gesperrt, 1 = freigegeben, 2 = mit Schlüssel
    rx_speed: str = ""         # z. B. "20.0 Gb/s"
    tx_speed: str = ""
    rx_lanes: int = 0
    tx_lanes: int = 0
    connected: bool = True     # False = nur in boltctl gespeichert
    policy: str = ""           # boltctl: auto / manual / iommu
    security: str = ""         # Sicherheitsstufe der Domäne (nur Host-Router)
    iommu: Optional[bool] = None       # IOMMU-DMA-Schutz der Domäne (nur Host-Router)

    @property
    def is_host(self) -> bool:
        return self.route.endswith("-0")

    @property
    def depth(self) -> int:
        hops = self.route.partition("-")[2]
        return 0 if hops in ("", "0") else (len(hops) + 1) // 2

    @property
    def model(self) -> str:
        return " ".join(dict.fromkeys(filter(None, (self.vendor, self.name)))).strip() or self.unique_id

    @property
    def generation_text(self) -> str:
        return {1: "Thunderbolt 1", 2: "Thunderbolt 2", 3: "Thunderbolt 3", 4: "USB4 / Thunderbolt 4"}.get(
            self.generation, "")

    @property
    def speed_gbps(self) -> float:
        try:
            return float((self.rx_speed or self.tx_speed).split()[0])
        except (ValueError, IndexError):
            return 0.0


# Fehlerzähler aus /sys/class/fc_host/hostN/statistics – jeder Zuwachs ist auffällig.
FC_ERROR_COUNTERS = ("link_failure_count", "loss_of_sync_count", "loss_of_signal_count",
                     "prim_seq_protocol_err_count", "invalid_tx_word_count", "invalid_crc_count",
                     "error_frames", "dumped_frames", "fcp_frame_alloc_failures")
_FC_UNSUPPORTED = 0xFFFFFFFFFFFFFFFF


@dataclass
class FcHost:
    """Fibre-Channel-HBA-Port aus ``/sys/class/fc_host`` (ohne Root lesbar)."""

    host: str                 # host3
    port_name: str = ""       # WWPN 0x10000090fa…
    node_name: str = ""       # WWNN
    port_state: str = ""      # Online, Linkdown, Offline …
    port_type: str = ""       # NPort (fabric via point-to-point), Loop …
    speed: str = ""           # „16 Gbit“ oder „unknown“
    supported_speeds: str = ""
    fabric_name: str = ""
    symbolic_name: str = ""
    model: str = ""
    model_description: str = ""
    firmware: str = ""
    driver: str = ""
    driver_version: str = ""
    serial: str = ""
    pci: str = ""
    remote_ports: int = 0
    statistics: dict[str, int] = field(default_factory=dict)

    @property
    def online(self) -> bool:
        return self.port_state.lower() == "online"

    @property
    def error_counters(self) -> dict[str, int]:
        return {key: value for key, value in self.statistics.items() if key in FC_ERROR_COUNTERS and value > 0}

    @property
    def severity(self) -> str:
        """ok | warn | error – Linkdown ist ein Fehler, Fehlerzähler > 0 eine Warnung."""
        if not self.online:
            return "error"
        return "warn" if self.error_counters else "ok"


@dataclass
class DeviceInventory:
    pci: list[PciDevice] = field(default_factory=list)
    network: list[NetInterface] = field(default_factory=list)
    graphics: list[GraphicsDevice] = field(default_factory=list)
    controllers: list[StorageController] = field(default_factory=list)
    disks: list[Disk] = field(default_factory=list)
    usb: list[UsbDevice] = field(default_factory=list)
    thunderbolt: list[ThunderboltDevice] = field(default_factory=list)
    fc_hosts: list[FcHost] = field(default_factory=list)

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


def parse_lsusb(text: str) -> dict[tuple[int, int], str]:
    """``lsusb``-Zeilen → {(Vendor, Produkt): Name aus der usb.ids-Datenbank}."""
    names = {}
    for line in text.splitlines():
        match = re.match(r"Bus \d+ Device \d+: ID ([0-9a-f]{4}):([0-9a-f]{4})\s*(.*)", line.strip(), re.I)
        if match and match[3].strip():
            names[(int(match[1], 16), int(match[2], 16))] = match[3].strip()
    return names


def _children(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except OSError:
        return []


def _usb_port_sort_key(port: str) -> tuple:
    if port.startswith("usb"):
        return (_int_or_none(port[3:]) or 0, ())
    bus, _, rest = port.partition("-")
    return (_int_or_none(bus) or 0, tuple(_int_or_none(p) or 0 for p in rest.split(".")))


def read_usb(sysfs: Path = SYSFS, names: Optional[dict[tuple[int, int], str]] = None) -> list[UsbDevice]:
    """USB-Topologie aus ``/sys/bus/usb/devices`` (Geräte, keine Interfaces)."""
    base = sysfs / "bus" / "usb" / "devices"
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    devices: list[UsbDevice] = []
    for entry in entries:
        name = entry.name
        if ":" in name or not (entry / "idVendor").exists():
            continue
        interface_classes, drivers = [], []
        # Interfaces liegen im Gerätverzeichnis und zusätzlich als Link in devices/.
        interfaces = sorted({p.name: p for p in (*_children(entry), *entries) if p.name.startswith(name + ":")}.values(),
                            key=lambda p: p.name)
        for interface in interfaces:
            cls = _hex(interface / "bInterfaceClass")
            if cls:
                interface_classes.append(cls)
            driver = _link_name(interface / "driver")
            if driver:
                drivers.append(driver)
        vendor, product = _hex(entry / "idVendor"), _hex(entry / "idProduct")
        manufacturer, product_name = _read(entry / "manufacturer"), _read(entry / "product")
        if names and not (manufacturer or product_name):
            product_name = names.get((vendor, product), "")
        root = name.startswith("usb")
        devices.append(UsbDevice(
            port=name, bus=_int(entry / "busnum"), device=_int(entry / "devnum"),
            vendor_id=vendor, product_id=product, manufacturer=manufacturer, product=product_name,
            serial=_read(entry / "serial") if not root else "",
            speed_mbps=_int_or_none(_read(entry / "speed").split(".")[0]) or 0,
            usb_version=_read(entry / "version"), device_class=_hex(entry / "bDeviceClass"),
            interface_classes=tuple(dict.fromkeys(interface_classes)), drivers=tuple(dict.fromkeys(drivers)),
            max_children=_int(entry / "maxchild"),
            depth=0 if root else name.partition("-")[2].count(".") + 1,
            pci=_read(entry / "serial") if root else "",
        ))
    return sorted(devices, key=lambda d: _usb_port_sort_key(d.port))


def parse_boltctl(text: str) -> list[dict[str, str]]:
    """``boltctl list`` → Liste von Geräten mit name/uuid/status/policy/type/vendor/generation."""
    devices: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[:1] in ("●", "○", "*", "o") and line[1:2] == " ":
            devices.append({"name": line[2:].strip()})
            continue
        if not devices or line[:1] not in "|`├└":
            continue
        key, sep, value = line.lstrip("|`-─├└ ").partition(":")
        if sep and key.strip() and (key.strip() == "name" or key.strip() not in devices[-1]):
            devices[-1][key.strip()] = value.strip()
    return devices


def _tb_route_sort_key(route: str) -> tuple:
    domain, _, hops = route.partition("-")
    try:
        return (int(domain), int(hops, 16) if hops else 0)
    except ValueError:
        return (0, 0)


def read_thunderbolt(sysfs: Path = SYSFS, boltctl: Optional[list[dict[str, str]]] = None) -> list[ThunderboltDevice]:
    """Thunderbolt-/USB4-Router aus ``/sys/bus/thunderbolt/devices`` (ohne Retimer/Tunnel)."""
    base = sysfs / "bus" / "thunderbolt" / "devices"
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    domains: dict[int, tuple[str, Optional[bool]]] = {}
    for entry in entries:
        if entry.name.startswith("domain"):
            protection = _read(entry / "iommu_dma_protection")
            domains[_int_or_none(entry.name[6:]) or 0] = (
                _read(entry / "security"), None if protection == "" else protection == "1")
    devices: list[ThunderboltDevice] = []
    for entry in entries:
        name = entry.name
        if ":" in name or "-" not in name or not (entry / "unique_id").exists():
            continue
        domain = _int_or_none(name.partition("-")[0]) or 0
        security, iommu = domains.get(domain, ("", None))
        authorized = _read(entry / "authorized")
        device = ThunderboltDevice(
            route=name, domain=domain, name=_read(entry / "device_name"), vendor=_read(entry / "vendor_name"),
            vendor_id=_hex(entry / "vendor"), device_id=_hex(entry / "device"), unique_id=_read(entry / "unique_id"),
            generation=_int(entry / "generation"), nvm_version=_read(entry / "nvm_version"),
            authorized=_int_or_none(authorized), rx_speed=_read(entry / "rx_speed"), tx_speed=_read(entry / "tx_speed"),
            rx_lanes=_int(entry / "rx_lanes"), tx_lanes=_int(entry / "tx_lanes"),
        )
        if device.is_host:
            device.security, device.iommu = security, iommu
        devices.append(device)
    devices.sort(key=lambda d: _tb_route_sort_key(d.route))
    if boltctl:
        by_uuid = {d.unique_id: d for d in devices}
        for stored in boltctl:
            uuid = stored.get("uuid", "")
            if uuid in by_uuid:
                by_uuid[uuid].policy = stored.get("policy", "")
            elif uuid and stored.get("type", "") != "host":
                devices.append(ThunderboltDevice(
                    route="", name=stored.get("name", ""), vendor=stored.get("vendor", ""), unique_id=uuid,
                    generation={"Thunderbolt 1": 1, "Thunderbolt 2": 2, "Thunderbolt 3": 3, "USB4": 4}.get(
                        stored.get("generation", ""), 0),
                    connected=False, policy=stored.get("policy", ""),
                ))
    return devices


def read_fc_hosts(sysfs: Path = SYSFS) -> list[FcHost]:
    """FC-HBA-Ports aus ``/sys/class/fc_host`` samt Modell/Firmware aus ``scsi_host`` und Statistik."""
    base = sysfs / "class" / "fc_host"
    try:
        entries = sorted(base.iterdir(), key=lambda p: (len(p.name), p.name))
    except OSError:
        return []
    hosts: list[FcHost] = []
    for entry in entries:
        scsi = sysfs / "class" / "scsi_host" / entry.name
        device = entry / "device"
        pci, driver = "", ""
        try:
            resolved = device.resolve()
            for parent in (resolved, *resolved.parents):
                if _PCI_ADDRESS.match(parent.name):
                    pci, driver = parent.name, _link_name(parent / "driver")
                    break
        except OSError:
            pass
        statistics: dict[str, int] = {}
        stats_dir = entry / "statistics"
        try:
            stat_files = sorted(stats_dir.iterdir())
        except OSError:
            stat_files = []
        for stat in stat_files:
            raw = _read(stat)
            try:
                value = int(raw, 0)
            except ValueError:
                continue
            if value != _FC_UNSUPPORTED and value >= 0:
                statistics[stat.name] = value
        try:
            remote_ports = sum(1 for child in device.iterdir() if child.name.startswith("rport-"))
        except OSError:
            remote_ports = 0
        hosts.append(FcHost(
            host=entry.name, port_name=_read(entry / "port_name"), node_name=_read(entry / "node_name"),
            port_state=_read(entry / "port_state"), port_type=_read(entry / "port_type"),
            speed=_read(entry / "speed"), supported_speeds=_read(entry / "supported_speeds"),
            fabric_name=_read(entry / "fabric_name"), symbolic_name=_read(entry / "symbolic_name"),
            model=_read(scsi / "model_name"), model_description=_read(scsi / "model_desc"),
            firmware=_read(scsi / "fw_version") or _read(scsi / "fwrev"),
            driver=driver or _read(scsi / "driver_name"), driver_version=_read(scsi / "driver_version") or _read(scsi / "lpfc_drvr_version"),
            serial=_read(scsi / "serial_num"), pci=pci, remote_ports=remote_ports, statistics=statistics,
        ))
    return hosts


def read_device_inventory(sysfs: Path = SYSFS, run: Optional[Callable] = None) -> DeviceInventory:
    run = run or subprocess.run
    lspci = _tool("lspci")
    names = parse_lspci(_run(run, [lspci, "-vmm", "-D"])) if lspci else {}
    devices = read_pci_devices(sysfs, names)
    by_address = {device.address: device for device in devices}
    lsusb = _tool("lsusb")
    usb_names = parse_lsusb(_run(run, [lsusb])) if lsusb else {}
    bolt = _tool("boltctl")
    bolt_devices = parse_boltctl(_run(run, [bolt, "list"])) if bolt else []
    return DeviceInventory(
        pci=devices,
        network=read_network(sysfs, by_address, run),
        graphics=read_graphics(sysfs, devices, run),
        controllers=read_controllers(sysfs, devices),
        disks=read_disks(run),
        usb=read_usb(sysfs, usb_names),
        thunderbolt=read_thunderbolt(sysfs, bolt_devices),
        fc_hosts=read_fc_hosts(sysfs),
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


def usb_speed_text(language: str, device: UsbDevice) -> str:
    label = _USB_SPEEDS.get(device.speed_mbps)
    if label is None:
        label = f"{device.speed_mbps} Mbit/s" if device.speed_mbps else ""
    if language == "en":
        label = label.replace("1,5 Mbit", "1.5 Mbit")
    return label


def usb_kind_text(language: str, device: UsbDevice) -> str:
    kind = device.kind
    if language == "en":
        for de, en in (("Kommunikation", "Communications"), ("Bild", "Imaging"), ("Drucker", "Printer"),
                       ("Massenspeicher", "Mass storage"), ("Chipkarte", "Smart card"), ("Gesundheit", "Health"),
                       ("Diagnose", "Diagnostic"), ("Funk (Bluetooth/WLAN)", "Wireless (Bluetooth/Wi-Fi)"),
                       ("Verschiedenes", "Miscellaneous"), ("Anwendungsspezifisch", "Application specific"),
                       ("Herstellerspezifisch", "Vendor specific"), ("Root-Hub", "Root hub")):
            kind = kind.replace(de, en)
    return kind


def usb_downgraded(device: UsbDevice) -> bool:
    """USB-3-Gerät (bcdUSB ≥ 3.00), das nur mit 480 Mbit/s oder weniger verbunden ist."""
    try:
        version = float(device.usb_version)
    except ValueError:
        return False
    return version >= 3.0 and 0 < device.speed_mbps < 5000


def usb_tree_label(device: UsbDevice) -> str:
    """Port mit Einrückung für die Topologie (Root-Hub → Hub → Gerät)."""
    if device.is_root_hub:
        return f"Bus {device.bus:03d} ({device.port})"
    return "    " * (device.depth - 1) + "└ " + device.port


def tb_tree_label(language: str, device: ThunderboltDevice) -> str:
    if not device.connected:
        return "–"
    if device.is_host:
        return _tt(language, f"Domäne {device.domain} ({device.route})", f"Domain {device.domain} ({device.route})")
    return "    " * (device.depth - 1) + "└ " + device.route


def tb_link_text(language: str, device: ThunderboltDevice) -> str:
    if not device.connected:
        return _tt(language, "nicht verbunden (gespeichert)", "not connected (stored)")
    if not device.rx_speed and not device.tx_speed:
        return ""
    lanes = device.rx_lanes or device.tx_lanes
    speed = device.rx_speed if device.rx_speed == device.tx_speed else f"{device.rx_speed} ↓ / {device.tx_speed} ↑"
    speed = speed.replace("Gb/s", "Gbit/s")
    return f"{speed} × {lanes}" if lanes else speed


def tb_downgraded(device: ThunderboltDevice) -> bool:
    """Verbundenes Gerät läuft unter der für seine Generation üblichen Linkrate (20 Gbit/s × 2 ab TB3)."""
    if not device.connected or device.is_host or not device.speed_gbps:
        return False
    expected = 20.0 if device.generation >= 3 else 10.0
    lanes = device.rx_lanes or device.tx_lanes
    return device.speed_gbps < expected or (lanes and lanes < 2)


def tb_authorized_text(language: str, device: ThunderboltDevice) -> str:
    if not device.connected or device.authorized is None:
        return ""
    return {0: _tt(language, "gesperrt", "blocked"), 1: _tt(language, "freigegeben", "authorized"),
            2: _tt(language, "freigegeben (Schlüssel)", "authorized (key)")}.get(
        device.authorized, str(device.authorized))


def tb_security_text(language: str, level: str) -> str:
    texts = _TB_SECURITY.get(level)
    return _tt(language, *texts) if texts else level


def disk_type_text(disk: Disk) -> str:
    return " ".join(filter(None, (disk.transport.upper() if disk.transport else "", disk.media)))


_FC_STATES = {"online": ("Online", "online"), "linkdown": ("Link unterbrochen", "link down"),
              "offline": ("Offline", "offline"), "blocked": ("blockiert", "blocked"),
              "bypassed": ("umgangen", "bypassed"), "diagnostics": ("Diagnose", "diagnostics"),
              "not present": ("nicht vorhanden", "not present"), "error": ("Fehler", "error"),
              "loopback": ("Loopback", "loopback"), "deleted": ("gelöscht", "deleted"), "unknown": ("unbekannt", "unknown")}


def fc_state_text(language: str, host: FcHost) -> str:
    texts = _FC_STATES.get(host.port_state.lower())
    return _tt(language, *texts) if texts else host.port_state


def fc_speed_text(language: str, host: FcHost) -> str:
    speed = host.speed.replace("Gbit", "Gbit/s")
    if not speed or speed.lower() == "unknown":
        return _tt(language, "unbekannt", "unknown") if host.online else "–"
    return speed


def fc_wwn_text(value: str) -> str:
    """0x10000090fa123456 → 10:00:00:90:fa:12:34:56"""
    digits = value.lower().removeprefix("0x")
    if len(digits) != 16 or not re.fullmatch(r"[0-9a-f]{16}", digits):
        return value
    return ":".join(digits[i:i + 2] for i in range(0, 16, 2))


def fc_errors_text(language: str, host: FcHost) -> str:
    errors = host.error_counters
    if not errors:
        return "0" if host.statistics else "–"
    return ", ".join(f"{key.removesuffix('_count')}={value}" for key, value in errors.items())


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
    for device in inventory.usb:
        lines.append("USB " + usb_tree_label(device) + ": " + " · ".join(filter(None, (
            device.model, f"{device.vendor_id:04x}:{device.product_id:04x}", usb_kind_text(language, device),
            usb_speed_text(language, device), f"USB {device.usb_version}" if device.usb_version else "",
            ", ".join(device.drivers), f"S/N {device.serial}" if device.serial else "",
            _tt(language, f"{device.max_children} Ports", f"{device.max_children} ports") if device.is_hub and device.max_children else "",
            device.pci,
        ))))
    for device in inventory.thunderbolt:
        lines.append("Thunderbolt " + tb_tree_label(language, device) + ": " + " · ".join(filter(None, (
            device.model, device.generation_text, tb_link_text(language, device),
            tb_authorized_text(language, device),
            f"NVM {device.nvm_version}" if device.nvm_version else "",
            _tt(language, f"Richtlinie {device.policy}", f"policy {device.policy}") if device.policy else "",
            _tt(language, f"Sicherheit: {tb_security_text(language, device.security)}",
                f"security: {tb_security_text(language, device.security)}") if device.security else "",
            "" if device.iommu is None else _tt(language, f"IOMMU-DMA-Schutz {'ja' if device.iommu else 'nein'}",
                                                f"IOMMU DMA protection {'yes' if device.iommu else 'no'}"),
        ))))
    for host in inventory.fc_hosts:
        lines.append(f"FC-HBA {host.host}: " + " · ".join(filter(None, (
            host.model or host.model_description, f"WWPN {fc_wwn_text(host.port_name)}" if host.port_name else "",
            fc_state_text(language, host), fc_speed_text(language, host) if host.online else "",
            host.port_type, f"Fabric {fc_wwn_text(host.fabric_name)}" if host.online and host.fabric_name else "",
            _tt(language, f"{host.remote_ports} Remote-Ports", f"{host.remote_ports} remote ports") if host.remote_ports else "",
            f"FW {host.firmware}" if host.firmware else "", host.driver, host.pci,
            _tt(language, f"Fehlerzähler: {fc_errors_text(language, host)}", f"error counters: {fc_errors_text(language, host)}")
            if host.error_counters else "",
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
