"""Technische Übersicht: Prozessoren, Caches, Takt, Mainboard/BIOS und Speichermodule.

CPU-, Cache- und Mainboard-Daten kommen ohne root aus /proc, /sys und ``lscpu``.
Die Speichermodule liefert ``dmidecode`` (root nötig: direkt, per ``sudo -n`` oder pkexec).
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
from typing import Callable, Optional

CPU_SYSFS = Path("/sys/devices/system/cpu")
HYBRID_SYSFS = Path("/sys/devices")
DMI_SYSFS = Path("/sys/class/dmi/id")
CPUINFO = Path("/proc/cpuinfo")
DMIDECODE_ARGS = ["-t", "1", "-t", "4", "-t", "16", "-t", "17", "-t", "39"]

# Für Stresstests aussagekräftige Befehlssätze, in Anzeigereihenfolge.
NOTABLE_FLAGS = (
    ("sse4_2", "SSE4.2"), ("avx", "AVX"), ("avx2", "AVX2"), ("fma", "FMA"), ("avx512f", "AVX-512"),
    ("avx512_vnni", "AVX-512 VNNI"), ("avx512_bf16", "AVX-512 BF16"), ("avx512_fp16", "AVX-512 FP16"),
    ("avx_vnni", "AVX-VNNI"), ("amx_tile", "AMX"), ("aes", "AES-NI"), ("sha_ni", "SHA"),
    ("vmx", "VT-x"), ("svm", "AMD-V"), ("rdrand", "RDRAND"),
)


@dataclass
class CpuPackage:
    socket: str
    model_name: str = "?"
    vendor: str = "?"
    family: str = "?"
    model: str = "?"
    stepping: str = "?"
    microcode: str = "?"
    cpus: list[int] = field(default_factory=list)
    cores: set = field(default_factory=set)
    flags: list[str] = field(default_factory=list)
    bugs: list[str] = field(default_factory=list)
    # Hybrid-CPUs (Intel ab Alder Lake): Kern-IDs der P- bzw. E-Kerne
    p_cores: set = field(default_factory=set)
    e_cores: set = field(default_factory=set)

    @property
    def core_text(self) -> str:
        if self.p_cores and self.e_cores:
            return f"{self.core_count} ({len(self.p_cores)} P + {len(self.e_cores)} E)"
        return str(self.core_count)

    @property
    def threads(self) -> int:
        return len(self.cpus)

    @property
    def core_count(self) -> int:
        return len(self.cores) or self.threads

    @property
    def notable_flags(self) -> list[str]:
        present = set(self.flags)
        return [label for flag, label in NOTABLE_FLAGS if flag in present]


@dataclass
class CpuClock:
    cpu: int
    socket: str
    core: str
    core_type: str
    current_mhz: Optional[float]
    min_mhz: Optional[float]
    max_mhz: Optional[float]
    # False: kein cpufreq-Treiber (Takt regelt das BIOS, z. B. HPE Power Regulator) → Ist-Takt aus /proc/cpuinfo
    cpufreq: bool = True


@dataclass
class CacheLevel:
    name: str
    level: int
    kind: str
    one_size: str
    all_size: str
    ways: Optional[int]
    sets: Optional[int]


@dataclass
class MemoryDevice:
    locator: str
    bank: str = ""
    size: str = ""
    kind: str = ""
    type_detail: str = ""
    form_factor: str = ""
    speed: str = ""
    configured_speed: str = ""
    rank: str = ""
    total_width: str = ""
    data_width: str = ""
    voltage: str = ""
    manufacturer: str = ""
    part_number: str = ""
    serial: str = ""

    @property
    def installed(self) -> bool:
        return bool(self.size) and self.size.lower() not in {"no module installed", "0", "not installed", "unknown"}

    @property
    def ecc(self) -> Optional[bool]:
        total, data = _bits(self.total_width), _bits(self.data_width)
        if total is None or data is None:
            return None
        return total > data

    @property
    def size_mib(self) -> int:
        match = re.match(r"(\d+)\s*([KMGT])B", self.size, re.I)
        if not match:
            return 0
        factor = {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}[match.group(2).upper()]
        return int(int(match.group(1)) * factor)


@dataclass
class DmiProcessor:
    """DMI-Typ 4 (ein bestückter Sockel)."""

    designation: str = ""
    manufacturer: str = ""
    version: str = ""
    max_speed: str = ""
    current_speed: str = ""
    external_clock: str = ""
    voltage: str = ""
    core_count: str = ""
    core_enabled: str = ""
    thread_count: str = ""
    status: str = ""
    upgrade: str = ""
    part_number: str = ""
    serial: str = ""

    @property
    def max_mhz(self) -> Optional[float]:
        return _speed_mhz(self.max_speed)

    @property
    def base_mhz(self) -> Optional[float]:
        return _speed_mhz(self.current_speed)


@dataclass
class PowerSupply:
    """DMI-Typ 39 „System Power Supply“ (dmidecode -t 39)."""

    location: str = ""
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    revision: str = ""
    max_power: str = ""          # „500 W“ oder leer (Unknown)
    status: str = ""             # „Present, OK“, „Present, Critical“, „Not Present“ …
    kind: str = ""               # Switching, Linear, Battery …
    hot_replaceable: Optional[bool] = None
    plugged: Optional[bool] = None
    unplugged: Optional[bool] = None

    @property
    def placeholder(self) -> bool:
        """BIOS-Platzhalter ohne jede Angabe (Desktop-Boards melden oft Typ 39 mit „Default string“)."""
        return not any((self.location, self.name, self.manufacturer, self.model, self.serial, self.max_power))

    @property
    def present(self) -> bool:
        return "not present" not in self.status.lower() and self.unplugged is not True

    @property
    def severity(self) -> str:
        """ok | warn | error | info – für die Ampel im System-Tab."""
        status = self.status.lower()
        if not self.present or self.placeholder:
            return "info"
        if "critical" in status or "non-recoverable" in status:
            return "error"
        if "non-critical" in status or "warn" in status or self.plugged is False or self.unplugged is True:
            return "warn"
        if "ok" in status:
            return "ok"
        return "info"


@dataclass
class MemoryInventory:
    devices: list[MemoryDevice] = field(default_factory=list)
    processors: list[DmiProcessor] = field(default_factory=list)
    error_correction: list[str] = field(default_factory=list)
    max_capacity: list[str] = field(default_factory=list)
    slots_declared: int = 0
    system_serial: str = ""
    power_supplies: list[PowerSupply] = field(default_factory=list)

    @property
    def installed(self) -> list[MemoryDevice]:
        return [device for device in self.devices if device.installed]

    @property
    def slot_count(self) -> int:
        return max(len(self.devices), self.slots_declared)

    @property
    def total_mib(self) -> int:
        return sum(device.size_mib for device in self.installed)


@dataclass
class BoardInfo:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return self.values.get(key, "")


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

def parse_cpuinfo(text: str) -> list[CpuPackage]:
    packages: dict[str, CpuPackage] = {}
    for block in re.split(r"\n\s*\n", text.strip()):
        values = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                values[key.strip()] = value.strip()
        if "processor" not in values:
            continue
        socket = values.get("physical id", "0")
        package = packages.get(socket)
        if package is None:
            package = packages[socket] = CpuPackage(
                socket=socket,
                model_name=values.get("model name", "?"),
                vendor=values.get("vendor_id", "?"),
                family=values.get("cpu family", "?"),
                model=values.get("model", "?"),
                stepping=values.get("stepping", "?"),
                microcode=values.get("microcode", "?"),
                flags=values.get("flags", "").split(),
                bugs=values.get("bugs", "").split(),
            )
        if values["processor"].isdigit():
            package.cpus.append(int(values["processor"]))
        if "core id" in values:
            package.cores.add(values["core id"])
    return sorted(packages.values(), key=lambda item: _int_or(item.socket, 0))


def read_cpu_packages(sysfs: Path = CPU_SYSFS, hybrid_sysfs: Path = HYBRID_SYSFS) -> list[CpuPackage]:
    try:
        packages = parse_cpuinfo(Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    types = read_core_types(hybrid_sysfs)
    if types:
        for package in packages:
            for cpu in package.cpus:
                core = _read(sysfs / f"cpu{cpu}/topology/core_id")
                target = package.p_cores if types.get(cpu) == "P" else package.e_cores if types.get(cpu) == "E" else None
                if core and target is not None:
                    target.add(core)
    return packages


def parse_cpulist(text: str) -> set[int]:
    cpus: set[int] = set()
    for part in text.strip().split(","):
        start, _, end = part.partition("-")
        if start.strip().isdigit():
            cpus.update(range(int(start), int(end if end.strip().isdigit() else start) + 1))
    return cpus


def read_core_types(sysfs: Path = HYBRID_SYSFS) -> dict[int, str]:
    """Logische CPU → "P"/"E" bei Hybrid-CPUs, sonst leer."""
    types: dict[int, str] = {}
    for name, label in (("cpu_core", "P"), ("cpu_atom", "E")):
        for cpu in parse_cpulist(_read(sysfs / name / "cpus")):
            types[cpu] = label
    return types if len(set(types.values())) > 1 else {}


def parse_cpuinfo_mhz(text: str) -> dict[int, float]:
    """Logische CPU → „cpu MHz“ aus /proc/cpuinfo (x86, auch ohne cpufreq-Treiber)."""
    result: dict[int, float] = {}
    cpu: Optional[int] = None
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        if key == "processor":
            cpu = _int_or(value, None)
        elif key == "cpu MHz" and cpu is not None:
            with contextlib.suppress(ValueError):
                result[cpu] = float(value.strip())
    return result


def read_cpu_clocks(
    packages: Optional[list[CpuPackage]] = None, sysfs: Path = CPU_SYSFS, hybrid_sysfs: Path = HYBRID_SYSFS,
    cpuinfo: Path = CPUINFO,
) -> list[CpuClock]:
    owners: dict[int, str] = {}
    for package in packages or []:
        for cpu in package.cpus:
            owners[cpu] = package.socket
    types = read_core_types(hybrid_sysfs)
    clocks: list[CpuClock] = []
    fallback: Optional[dict[int, float]] = None
    for path in sorted(sysfs.glob("cpu[0-9]*"), key=lambda item: int(item.name[3:])):
        cpu = int(path.name[3:])
        freq = path / "cpufreq"
        current = _khz_to_mhz(_read(freq / "scaling_cur_freq"))
        has_cpufreq = current is not None
        if not has_cpufreq:
            if fallback is None:
                fallback = parse_cpuinfo_mhz(_read(cpuinfo))
            current = fallback.get(cpu)
        clocks.append(CpuClock(
            cpu=cpu,
            socket=_read(path / "topology/physical_package_id") or owners.get(cpu, "?"),
            core=_read(path / "topology/core_id") or "?",
            core_type=types.get(cpu, ""),
            current_mhz=current,
            min_mhz=_khz_to_mhz(_read(freq / "cpuinfo_min_freq")),
            max_mhz=_khz_to_mhz(_read(freq / "cpuinfo_max_freq")),
            cpufreq=has_cpufreq,
        ))
    return clocks


def dmi_processor_map(
    packages: list[CpuPackage], memory: Optional[MemoryInventory],
) -> dict[str, DmiProcessor]:
    """Ordnet die bestückten DMI-Prozessoren den Sockeln in Reihenfolge zu."""
    if memory is None:
        return {}
    return {package.socket: proc for package, proc in zip(packages, memory.processors, strict=False)}


def parse_lscpu_caches(text: str) -> list[CacheLevel]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    caches = []
    for item in data.get("caches", []):
        caches.append(CacheLevel(
            name=str(item.get("name", "?")),
            level=_int_or(item.get("level"), 0),
            kind=str(item.get("type", "")),
            one_size=str(item.get("one-size", "")),
            all_size=str(item.get("all-size", "")),
            ways=_int_or(item.get("ways"), None),
            sets=_int_or(item.get("sets"), None),
        ))
    return sorted(caches, key=lambda cache: (cache.level, cache.name))


def read_caches() -> list[CacheLevel]:
    if not shutil.which("lscpu"):
        return []
    try:
        result = subprocess.run(["lscpu", "-C", "-J", "-B"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    caches = parse_lscpu_caches(result.stdout) if result.returncode == 0 else []
    for cache in caches:
        cache.one_size, cache.all_size = _human_bytes(cache.one_size), _human_bytes(cache.all_size)
    return caches


# ---------------------------------------------------------------------------
# Mainboard / BIOS
# ---------------------------------------------------------------------------

BOARD_KEYS = (
    "sys_vendor", "product_name", "product_version", "product_family", "product_serial",
    "board_vendor", "board_name", "board_version", "bios_vendor", "bios_version", "bios_date", "bios_release",
)


def read_board_info(sysfs: Path = DMI_SYSFS) -> BoardInfo:
    values = {}
    for key in BOARD_KEYS:
        value = _read(sysfs / key)
        if value and value.lower() not in {
            "to be filled by o.e.m.", "default string", "not specified", "not defined", "none", "system product name",
            "system manufacturer", "system version", "0123456789",
        }:
            values[key] = value
    return BoardInfo(values)


# ---------------------------------------------------------------------------
# Speichermodule
# ---------------------------------------------------------------------------

def parse_dmidecode(text: str) -> MemoryInventory:
    inventory = MemoryInventory()
    for block in re.split(r"\n\s*\n", text):
        values = {key.strip(): value.strip() for key, value in re.findall(r"^\t([^:\n\t]+):[ \t]*(.*)$", block, re.M)}
        if "\nSystem Information" in "\n" + block:
            serial = values.get("Serial Number", "")
            if serial and serial.lower() not in {"not specified", "to be filled by o.e.m.", "default string"}:
                inventory.system_serial = serial
        elif "\nPhysical Memory Array" in "\n" + block:
            if values.get("Use", "System Memory") != "System Memory":
                continue
            if values.get("Error Correction Type"):
                inventory.error_correction.append(values["Error Correction Type"])
            if values.get("Maximum Capacity"):
                inventory.max_capacity.append(values["Maximum Capacity"])
            inventory.slots_declared += _int_or(values.get("Number Of Devices"), 0)
        elif "\nProcessor Information" in "\n" + block:
            status = values.get("Status", "")
            if "unpopulated" in status.lower():
                continue
            inventory.processors.append(DmiProcessor(
                designation=_clean(values.get("Socket Designation", "")),
                manufacturer=_clean(values.get("Manufacturer", "")),
                version=_clean(values.get("Version", "")),
                max_speed=_clean(values.get("Max Speed", "")),
                current_speed=_clean(values.get("Current Speed", "")),
                external_clock=_clean(values.get("External Clock", "")),
                voltage=_clean(values.get("Voltage", "")),
                core_count=_clean(values.get("Core Count", "")),
                core_enabled=_clean(values.get("Core Enabled", "")),
                thread_count=_clean(values.get("Thread Count", "")),
                status=_clean(status),
                upgrade=_clean(values.get("Upgrade", "")),
                part_number=_clean(values.get("Part Number", "")),
                serial=_clean(values.get("Serial Number", "")),
            ))
        elif "\nSystem Power Supply" in "\n" + block:
            inventory.power_supplies.append(PowerSupply(
                location=_clean(values.get("Location", "")),
                name=_clean(values.get("Name", "")),
                manufacturer=_clean(values.get("Manufacturer", "")),
                model=_clean(values.get("Model Part Number", "")),
                serial=_clean(values.get("Serial Number", "")),
                revision=_clean(values.get("Revision", "")),
                max_power=_clean(values.get("Max Power Capacity", "")),
                status=_clean(values.get("Status", "")),
                kind=_clean(values.get("Type", "")),
                hot_replaceable=_yes_no(values.get("Hot Replaceable")),
                plugged=_yes_no(values.get("Plugged")),
                unplugged=_yes_no(values.get("Unplugged")),
            ))
        elif "\nMemory Device" in "\n" + block:
            inventory.devices.append(MemoryDevice(
                locator=values.get("Locator", "?"),
                bank=_clean(values.get("Bank Locator", "")),
                size=values.get("Size", ""),
                kind=_clean(values.get("Type", "")),
                type_detail=_clean(values.get("Type Detail", "")),
                form_factor=_clean(values.get("Form Factor", "")),
                speed=_clean(values.get("Speed", "")),
                configured_speed=_clean(values.get("Configured Memory Speed", values.get("Configured Clock Speed", ""))),
                rank=_clean(values.get("Rank", "")),
                total_width=_clean(values.get("Total Width", "")),
                data_width=_clean(values.get("Data Width", "")),
                voltage=_clean(values.get("Configured Voltage", "")),
                manufacturer=_clean(values.get("Manufacturer", "")),
                part_number=_clean(values.get("Part Number", "")),
                serial=_clean(values.get("Serial Number", "")),
            ))
    return inventory


def dmidecode_command() -> list[str]:
    return ["dmidecode", *DMIDECODE_ARGS]


def read_memory_inventory(run: Optional[Callable] = None) -> tuple[Optional[MemoryInventory], str]:
    """Liest die Speichermodule ohne Passwortabfrage: als root direkt, sonst ``sudo -n``.

    ``run`` ersetzt den Aufruf (z. B. Root-Sitzung des Gesamttests).
    """
    if run is None:
        if os.geteuid() == 0:
            run = subprocess.run
        elif shutil.which("sudo"):
            def run(cmd, **kwargs):
                return subprocess.run(["sudo", "-n", *cmd], **kwargs)
        else:
            return None, "root"
    try:
        result = run(dmidecode_command(), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return None, str(exc)
    if result.returncode:
        return None, (result.stderr or result.stdout).strip() or f"Exit {result.returncode}"
    return parse_dmidecode(result.stdout), ""


# ---------------------------------------------------------------------------
# Textfassung (Bericht, Zwischenablage)
# ---------------------------------------------------------------------------

def _tt(language: str, de: str, en: str) -> str:
    return en if language == "en" else de


def inventory_lines(
    language: str = "de",
    packages: Optional[list[CpuPackage]] = None,
    caches: Optional[list[CacheLevel]] = None,
    board: Optional[BoardInfo] = None,
    memory: Optional[MemoryInventory] = None,
    memory_error: str = "",
) -> list[str]:
    packages = read_cpu_packages() if packages is None else packages
    caches = read_caches() if caches is None else caches
    board = read_board_info() if board is None else board
    lines: list[str] = []

    system = " ".join(filter(None, (board.get("sys_vendor"), board.get("product_name"))))
    version = board.get("product_version")
    if system and version and version not in system:
        system += f" ({version})"
    serial = board.get("product_serial") or (memory.system_serial if memory else "")
    if system:
        lines.append(_tt(language, "System: ", "System: ") + system + (f" (S/N {serial})" if serial else ""))
    mainboard = " ".join(filter(None, (board.get("board_vendor"), board.get("board_name"), board.get("board_version"))))
    if mainboard:
        lines.append("Mainboard: " + mainboard)
    bios = " ".join(filter(None, (board.get("bios_vendor"), board.get("bios_version"))))
    if bios:
        lines.append("BIOS: " + bios + (f" ({board.get('bios_date')})" if board.get("bios_date") else ""))

    dmi = dmi_processor_map(packages, memory)
    for package in packages:
        lines.append(
            f"CPU {_tt(language, 'Sockel', 'socket')} {package.socket}: {package.model_name} – "
            f"{package.core_text} {_tt(language, 'Kerne', 'cores')}, {package.threads} Threads, "
            f"Family {package.family} Model {package.model} Stepping {package.stepping}, Microcode {package.microcode}"
        )
        proc = dmi.get(package.socket)
        if proc:
            lines.append("  DMI: " + " · ".join(filter(None, (
                proc.designation,
                f"{_tt(language, 'Basistakt', 'base clock')} {proc.current_speed}" if proc.current_speed else "",
                f"{_tt(language, 'max.', 'max')} {proc.max_speed}" if proc.max_speed else "",
                f"{_tt(language, 'ext. Takt', 'ext. clock')} {proc.external_clock}" if proc.external_clock else "",
                proc.voltage,
                f"{_tt(language, 'Kerne aktiv', 'cores enabled')} {proc.core_enabled}/{proc.core_count}"
                if proc.core_enabled and proc.core_count else "",
                proc.part_number, f"S/N {proc.serial}" if proc.serial else "",
            ))))
        if package.notable_flags:
            lines.append(f"  {_tt(language, 'Befehlssätze', 'Instruction sets')}: {', '.join(package.notable_flags)}")
    if caches:
        lines.append("Cache: " + ", ".join(f"{cache.name} {cache.all_size}" for cache in caches))

    if memory is None:
        if memory_error:
            lines.append(_tt(language, "Speichermodule: nicht lesbar (", "Memory modules: unavailable (")
                         + memory_error.splitlines()[0][:120] + ")")
    else:
        installed = memory.installed
        lines.append(
            _tt(language, "Arbeitsspeicher: ", "Memory: ")
            + f"{_format_mib(memory.total_mib)}, {len(installed)}/{memory.slot_count} "
            + _tt(language, "Steckplätze belegt", "slots populated")
            + (f", {_tt(language, 'Fehlerkorrektur', 'error correction')}: {', '.join(sorted(set(memory.error_correction)))}"
               if memory.error_correction else "")
        )
        for device in installed:
            lines.append("  " + " · ".join(filter(None, (
                device.locator, device.bank, device.size, device.kind, device.form_factor,
                device.configured_speed or device.speed, f"Rank {device.rank}" if device.rank else "",
                "ECC" if device.ecc else "", device.manufacturer, device.part_number,
                f"S/N {device.serial}" if device.serial else "",
            ))))
        if memory.power_supplies:
            lines.append(_tt(language, "Netzteile (DMI): ", "Power supplies (DMI): ") + power_summary(language, memory.power_supplies))
            for psu in memory.power_supplies:
                lines.append("  " + " · ".join(filter(None, (
                    psu.location or psu.name, psu.name if psu.location and psu.name != psu.location else "",
                    psu.manufacturer, psu.model, psu.max_power, power_status_text(language, psu),
                    _tt(language, "Hot-Plug", "hot-plug") if psu.hot_replaceable else "",
                    f"S/N {psu.serial}" if psu.serial else "",
                ))))
    return lines


def power_status_text(language: str, psu: PowerSupply) -> str:
    status = psu.status.lower()
    if psu.placeholder:
        return _tt(language, "Platzhalter (keine Daten)", "placeholder (no data)")
    if not psu.present:
        return _tt(language, "nicht bestückt", "not present")
    if psu.plugged is False or psu.unplugged is True:
        return _tt(language, "vorhanden, nicht eingesteckt", "present, unplugged")
    if "critical" in status or "non-recoverable" in status:
        return _tt(language, "Fehler", "failed")
    if "non-critical" in status or "warn" in status:
        return _tt(language, "Warnung", "warning")
    if "ok" in status:
        return "OK"
    return psu.status or _tt(language, "unbekannt", "unknown")


def power_summary(language: str, supplies: list[PowerSupply]) -> str:
    """„2/2 bestückt, redundant“ bzw. Hinweis auf fehlende Redundanz."""
    placeholders = [psu for psu in supplies if psu.placeholder]
    supplies = [psu for psu in supplies if not psu.placeholder]
    if not supplies:
        return _tt(language, f"{len(placeholders)} Einträge ohne Daten (BIOS-Platzhalter, keine Aussage möglich)",
                   f"{len(placeholders)} entries without data (BIOS placeholders, no assessment possible)")
    present = [psu for psu in supplies if psu.present]
    healthy = [psu for psu in present if psu.severity == "ok"]
    text = _tt(language, f"{len(present)}/{len(supplies)} bestückt", f"{len(present)}/{len(supplies)} present")
    if len(healthy) >= 2:
        text += _tt(language, ", redundant", ", redundant")
    elif len(supplies) >= 2:
        text += _tt(language, ", keine Redundanz", ", no redundancy")
    if len(healthy) < len(present):
        text += _tt(language, f", {len(present) - len(healthy)} mit Störung", f", {len(present) - len(healthy)} with fault")
    if placeholders:
        text += _tt(language, f", {len(placeholders)} Platzhalter ignoriert", f", {len(placeholders)} placeholders ignored")
    return text


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _int_or(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _khz_to_mhz(value: str) -> Optional[float]:
    number = _int_or(value, None)
    return None if number is None else number / 1000


def _speed_mhz(value: str) -> Optional[float]:
    match = re.match(r"(\d+)\s*MHz", value or "", re.I)
    return float(match.group(1)) if match and int(match.group(1)) > 0 else None


def _bits(value: str) -> Optional[int]:
    match = re.match(r"(\d+)\s*bits", value or "")
    return int(match.group(1)) if match else None


def _yes_no(value) -> Optional[bool]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return True if text == "yes" else False if text == "no" else None


def _clean(value: str) -> str:
    value = (value or "").strip()
    if value.lower() in {"not specified", "unknown", "none", "not provided", "no dimm", "undefined",
                         "default string", "to be filled by o.e.m.", "to be filled by oem"}:
        return ""
    return value


def _human_bytes(value: str) -> str:
    number = _int_or(value, None)
    if number is None:
        return value
    for unit, size in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if number >= size:
            amount = number / size
            return f"{amount:.0f} {unit}" if amount == int(amount) else f"{amount:.2f} {unit}".replace(".00", "")
    return f"{number} B"


def _format_mib(mib: int) -> str:
    if mib >= 1024 * 1024:
        return f"{mib / (1024 * 1024):.2f} TiB".replace(".00", "")
    if mib >= 1024:
        return f"{mib / 1024:.0f} GiB" if mib % 1024 == 0 else f"{mib / 1024:.1f} GiB"
    return f"{mib} MiB"
