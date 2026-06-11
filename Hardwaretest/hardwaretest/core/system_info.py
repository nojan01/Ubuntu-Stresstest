"""Helpers to inspect memory, CPU, temperature and ECC information."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psutil


# ---------------------------------------------------------------------------
# Temperature helpers
# ---------------------------------------------------------------------------

@dataclass
class CpuTemperature:
    """Snapshot of CPU temperature readings."""
    current: float = 0.0
    high: float = 0.0
    critical: float = 0.0
    label: str = ""


def read_cpu_temperatures() -> List[CpuTemperature]:
    """Read CPU temperatures via psutil or fallback to sysfs."""
    results: List[CpuTemperature] = []
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, RuntimeError):
        temps = {}

    # psutil liefert z.B. {"coretemp": [...], "k10temp": [...]}
    for chip_name in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        if chip_name in temps:
            for entry in temps[chip_name]:
                results.append(CpuTemperature(
                    current=entry.current or 0.0,
                    high=entry.high or 0.0,
                    critical=entry.critical or 0.0,
                    label=entry.label or chip_name,
                ))
    if not results and temps:
        # Fallback: erste verfuegbare Sensor-Gruppe
        for chip_name, entries in temps.items():
            for entry in entries:
                results.append(CpuTemperature(
                    current=entry.current or 0.0,
                    high=entry.high or 0.0,
                    critical=entry.critical or 0.0,
                    label=entry.label or chip_name,
                ))
            break
    return results


@dataclass
class CpuChipSummary:
    """Aggregierte Temperatur-Zusammenfassung pro CPU-Chip/Socket."""
    chip_name: str
    core_count: int = 0
    temp_min: float = 0.0
    temp_avg: float = 0.0
    temp_max: float = 0.0
    hottest_core_label: str = ""
    high: float = 0.0
    critical: float = 0.0
    per_core: List[CpuTemperature] = field(default_factory=list)


def summarize_cpu_temperatures(
    temps: List[CpuTemperature],
) -> List[CpuChipSummary]:
    """Gruppiert Core-Temperaturen nach Chip/Socket und aggregiert Min/Avg/Max.

    Bei Dual-Socket-Systemen mit >90 Cores wird so eine kompakte
    Uebersicht moeglich statt 90+ Einzelzeilen.

    Strategie:
    1. Wenn "Package id N" Eintraege vorhanden sind, werden die
       nachfolgenden "Core X" Eintraege dem jeweiligen Package zugeordnet.
    2. Sonst wird nach Label-Praefix gruppiert (Tdie, k10temp etc.).
    """
    if not temps:
        return []

    from collections import OrderedDict

    # Schritt 1: Pruefen ob Package-Eintraege vorhanden (Intel coretemp)
    package_indices: List[int] = []
    for i, t in enumerate(temps):
        if (t.label or "").lower().startswith("package id"):
            package_indices.append(i)

    chips: Dict[str, List[CpuTemperature]] = OrderedDict()

    if package_indices:
        # Intel-Stil: "Package id 0", "Core 0", ..., "Package id 1", "Core 48", ...
        # Jedes Package bekommt die nachfolgenden Cores bis zum naechsten Package
        for pkg_pos, pkg_idx in enumerate(package_indices):
            pkg_temp = temps[pkg_idx]
            label = pkg_temp.label or ""
            parts = label.split()
            socket_id = parts[-1] if len(parts) >= 3 and parts[-1].isdigit() else str(pkg_pos)
            chip_key = f"CPU {socket_id} (Socket {socket_id})"

            # Bestimme den Bereich der zugehoerigen Cores
            next_pkg_idx = (
                package_indices[pkg_pos + 1]
                if pkg_pos + 1 < len(package_indices)
                else len(temps)
            )

            # Package-Temp selbst als ersten Eintrag (wird getrennt dargestellt)
            core_list: List[CpuTemperature] = [pkg_temp]
            for j in range(pkg_idx + 1, next_pkg_idx):
                core_list.append(temps[j])

            chips[chip_key] = core_list

        # Eintraege die VOR dem ersten Package stehen (selten, aber moeglich)
        if package_indices[0] > 0:
            pre = temps[: package_indices[0]]
            if pre:
                chips.setdefault("Sonstige", []).extend(pre)
    else:
        # Kein Package: nach Label-Praefix gruppieren (AMD, ARM, etc.)
        for t in temps:
            chip_key = _chip_key_for_temp(t)
            chips.setdefault(chip_key, []).append(t)

    summaries: List[CpuChipSummary] = []
    for chip_key, cores in chips.items():
        current_values = [c.current for c in cores if c.current > 0]
        if not current_values:
            continue
        hottest = max(cores, key=lambda c: c.current)
        high_vals = [c.high for c in cores if c.high > 0]
        crit_vals = [c.critical for c in cores if c.critical > 0]
        summaries.append(CpuChipSummary(
            chip_name=chip_key,
            core_count=len(current_values),
            temp_min=min(current_values),
            temp_avg=sum(current_values) / len(current_values),
            temp_max=max(current_values),
            hottest_core_label=hottest.label or chip_key,
            high=min(high_vals) if high_vals else 0.0,
            critical=min(crit_vals) if crit_vals else 0.0,
            per_core=cores,
        ))
    return summaries


def _chip_key_for_temp(t: CpuTemperature) -> str:
    """Leitet einen Chip/Socket-Schluessel aus dem Label ab.

    Beispiele:
      "Package id 0"  → "CPU 0"
      "Package id 1"  → "CPU 1"
      "Core 47"       → "CPU 0" (Core < 64 heuristisch)
      "Tdie"          → Label selbst
    """
    label = (t.label or "").strip()

    # "Package id N" direkt verwenden
    if label.lower().startswith("package id"):
        parts = label.split()
        if len(parts) >= 3 and parts[-1].isdigit():
            return f"CPU {parts[-1]} (Socket {parts[-1]})"
        return label

    # "Core N" – Versuche Socket-Zuordnung ueber Core-Index
    if label.lower().startswith("core "):
        parts = label.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            # Ohne Package-Info koennen wir nicht sicher zuordnen,
            # daher geben wir einen generischen Chip-Key zurueck.
            # Die Package-Temperaturen werden separat geliefert.
            return "Cores"
        return "Cores"

    # Tdie, Tctl, k10temp etc. → eigene Gruppe
    if label:
        return label
    return "CPU"


def format_temperatures(temps: List[CpuTemperature]) -> str:
    """Menschenlesbare Zusammenfassung der CPU-Temperaturen."""
    if not temps:
        return "Keine Temperatursensoren gefunden"
    lines: List[str] = []
    for t in temps:
        parts = [f"{t.label}: {t.current:.0f}°C"]
        if t.high > 0:
            parts.append(f"max {t.high:.0f}°C")
        if t.critical > 0:
            parts.append(f"krit {t.critical:.0f}°C")
        lines.append(" / ".join(parts))
    return " | ".join(lines)


# ---------------------------------------------------------------------------
# EDAC / ECC helpers
# ---------------------------------------------------------------------------

@dataclass
class EdacInfo:
    """ECC/EDAC error counters from sysfs."""
    correctable_errors: int = 0
    uncorrectable_errors: int = 0
    available: bool = False
    controllers: List[str] = field(default_factory=list)


def read_edac_info() -> EdacInfo:
    """Read ECC error counters from /sys/devices/system/edac/."""
    edac_base = Path("/sys/devices/system/edac/mc")
    if not edac_base.exists():
        return EdacInfo(available=False)

    info = EdacInfo(available=True)
    for mc_dir in sorted(edac_base.parent.glob("mc/mc*")):
        info.controllers.append(mc_dir.name)
        ce_file = mc_dir / "ce_count"
        ue_file = mc_dir / "ue_count"
        if ce_file.exists():
            try:
                info.correctable_errors += int(ce_file.read_text().strip())
            except (ValueError, OSError):
                pass
        if ue_file.exists():
            try:
                info.uncorrectable_errors += int(ue_file.read_text().strip())
            except (ValueError, OSError):
                pass
    return info


def format_edac_info(edac: EdacInfo) -> str:
    """Menschenlesbare EDAC-Zusammenfassung."""
    if not edac.available:
        return "ECC/EDAC: nicht verfuegbar (kein ECC-RAM oder Kernel-Modul nicht geladen)"
    parts = [
        f"ECC Controller: {len(edac.controllers)}",
        f"Korrigierbare Fehler: {edac.correctable_errors}",
        f"Unkorrigierbare Fehler: {edac.uncorrectable_errors}",
    ]
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------

@dataclass
class SystemInfo:
    """Lightweight snapshot of relevant system stats."""

    total_memory_bytes: int
    available_memory_bytes: int
    swap_enabled: bool
    logical_cpu_cores: int
    physical_cpu_cores: int
    swap_used_bytes: int = 0
    swap_total_bytes: int = 0

    @property
    def available_memory_mb(self) -> int:
        return self.available_memory_bytes // (1024 * 1024)

    @property
    def cpu_cores(self) -> int:
        return self.logical_cpu_cores

    def reserved_core_limit(self) -> int:
        base = self.physical_cpu_cores or self.logical_cpu_cores or 1
        if base <= 1:
            return 1
        return base - 1


def read_system_info() -> SystemInfo:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    logical = psutil.cpu_count(logical=True) or 1
    physical = psutil.cpu_count(logical=False) or 0
    return SystemInfo(
        total_memory_bytes=int(vm.total),
        available_memory_bytes=int(vm.available),
        swap_enabled=swap.total > 0 and swap.percent < 100,
        logical_cpu_cores=logical,
        physical_cpu_cores=physical,
        swap_used_bytes=int(swap.used),
        swap_total_bytes=int(swap.total),
    )


def _is_polkit_agent_running() -> bool:
    """Check whether a polkit authentication agent is active.

    Without a running agent, ``pkexec`` blocks forever waiting for a dialog
    that will never appear (common on Ubuntu Server + XFCE4).
    """
    agent_names = (
        "polkit-gnome-authentication-agent",
        "polkit-kde-authentication-agent",
        "xfce-polkit",
        "lxpolkit",
        "lxqt-policykit-agent",
        "polkitd",
        "polkit-mate-authentication-agent",
    )
    try:
        result = subprocess.run(
            ["ps", "-eo", "comm"],
            capture_output=True, text=True, timeout=5,
        )
        running = result.stdout.lower()
        return any(name in running for name in agent_names)
    except Exception:
        return False


# Timeout (seconds) for privilege-escalation subprocess calls so the
# application can never hang indefinitely.
_PKEXEC_TIMEOUT = 120


def check_swapoff_safe(info: "SystemInfo") -> Tuple[bool, str]:
    """Check whether ``swapoff -a`` can run without freezing the system.

    Returns ``(safe, message)``.  *safe* is ``False`` when the amount of
    data currently stored in swap exceeds the available RAM – in that
    scenario the kernel would have to evict caches or OOM-kill processes
    which typically freezes Ubuntu Server 24.04 + XFCE4 desktops.
    """
    if info.swap_used_bytes == 0:
        return True, "Swap ist leer – kann sicher deaktiviert werden."

    swap_used_mb = info.swap_used_bytes / (1024 * 1024)
    avail_mb = info.available_memory_bytes / (1024 * 1024)
    # Leave a safety margin of 15 % so the desktop doesn't starve.
    safe_headroom = int(avail_mb * 0.85)

    if swap_used_mb > safe_headroom:
        return False, (
            f"Swap enthaelt {swap_used_mb:.0f} MB Daten, aber nur "
            f"{avail_mb:.0f} MB RAM sind frei (sicherer Spielraum: "
            f"{safe_headroom} MB).\n"
            "Das Deaktivieren wuerde das System einfrieren.\n\n"
            "Bitte zuerst Anwendungen beenden um RAM freizugeben,\n"
            "oder manuell in einem TTY ausfuehren: sudo swapoff -a"
        )

    return True, (
        f"Swap enthaelt {swap_used_mb:.0f} MB – genug freier RAM "
        f"({avail_mb:.0f} MB) verfuegbar."
    )


def needs_password_for_swap() -> bool:
    """Return ``True`` when the user must supply a password for swapoff.

    A password is NOT needed when:
    * a polkit authentication agent is running (pkexec will show its own
      graphical dialog), **or**
    * the user has a NOPASSWD sudo rule for swapoff.

    In all other cases (typical Ubuntu Server 24.04 + XFCE4 without a
    polkit agent) the application must ask the user for a password and
    pipe it to ``sudo -S``.
    """
    pkexec_path: Optional[str] = shutil.which("pkexec")
    if pkexec_path and _is_polkit_agent_running():
        return False

    # Check whether ``sudo -n swapoff -a`` would succeed (NOPASSWD rule).
    sudo_path: Optional[str] = shutil.which("sudo")
    swapoff_path: Optional[str] = shutil.which("swapoff")
    if sudo_path and swapoff_path:
        try:
            # -n = non-interactive; --validate only checks auth, doesn't
            # actually run the command.
            result = subprocess.run(
                [sudo_path, "-n", "-l", swapoff_path],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return False  # NOPASSWD is set up
        except Exception:
            pass

    return True


def _build_swapoff_cmd(*, password: Optional[str] = None
                       ) -> Tuple[List[str], Optional[str]]:
    """Build the swapoff command list and optional stdin input.

    Returns ``(cmd, stdin_data)``.
    """
    swapoff_path: Optional[str] = shutil.which("swapoff")
    if not swapoff_path:
        raise FileNotFoundError("swapoff nicht gefunden.")

    if password is not None:
        sudo_path: Optional[str] = shutil.which("sudo")
        if not sudo_path:
            raise FileNotFoundError("sudo nicht gefunden.")
        return [sudo_path, "-S", swapoff_path, "-a"], password + "\n"

    pkexec_path: Optional[str] = shutil.which("pkexec")
    if pkexec_path and _is_polkit_agent_running():
        return [pkexec_path, swapoff_path, "-a"], None

    sudo_path = shutil.which("sudo")
    cmd = [sudo_path, swapoff_path, "-a"] if sudo_path else [swapoff_path, "-a"]
    return cmd, None


def disable_swap(*, password: Optional[str] = None) -> None:
    """Disable swap – runs **synchronously** (call from a worker thread!).

    When *password* is given it is piped to ``sudo -S``.  Otherwise the
    function tries ``pkexec`` (if a polkit agent is running) or plain
    ``sudo`` as a fallback.

    .. warning::
       This blocks until ``swapoff -a`` finishes which can take a very
       long time when lots of data is in swap.  **Never call this on the
       main / GUI thread.**
    """
    cmd, stdin_data = _build_swapoff_cmd(password=password)
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=_PKEXEC_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Swap-Deaktivierung: Zeitlimit ueberschritten. "
            "Kein Polkit-Agent aktiv? Bitte manuell ausfuehren: sudo swapoff -a"
        ) from exc

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(
            f"swapoff fehlgeschlagen (exit {proc.returncode}): {stderr}"
        )


def disable_swap_with_password(password: str) -> None:
    """Convenience wrapper kept for backwards compatibility."""
    disable_swap(password=password)


def check_memtest86_installed() -> Tuple[bool, str]:
    """Prueft ob memtest86+ installiert und als GRUB-Eintrag verfuegbar ist."""
    memtest_paths = [
        Path("/boot/memtest86+.bin"),
        Path("/boot/memtest86+x64.bin"),
        Path("/boot/memtest86+x64.efi"),
        Path("/boot/memtest86+ia32.bin"),
        Path("/boot/memtest86+ia32.efi"),
    ]
    found = [p for p in memtest_paths if p.exists()]
    if found:
        return True, f"memtest86+ gefunden: {found[0].name}"

    # Check ob Package installiert ist
    dpkg = shutil.which("dpkg")
    if dpkg:
        try:
            result = subprocess.run(
                [dpkg, "-s", "memtest86+"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and "Status: install ok installed" in result.stdout:
                return True, "memtest86+ Paket installiert"
        except (subprocess.SubprocessError, OSError):
            pass
    return False, "memtest86+ nicht installiert (sudo apt install memtest86+)"


# ---------------------------------------------------------------------------
# SMART disk health (smartctl)
# ---------------------------------------------------------------------------

@dataclass
class SmartInfo:
    """SMART-Zusammenfassung eines Datentraegers."""
    device: str
    healthy: bool
    model: str = ""
    serial: str = ""
    temperature_c: int = 0
    power_on_hours: int = 0
    reallocated_sectors: int = 0
    pending_sectors: int = 0
    uncorrectable_sectors: int = 0
    raw_output: str = ""
    error: str = ""


def read_smart_info(device: str) -> SmartInfo:
    """Liest SMART-Daten eines Datentraegers via smartctl.

    Benoetigt root/sudo fuer direkte Geraetezugriffe.
    """
    smartctl = shutil.which("smartctl")
    if not smartctl:
        return SmartInfo(device=device, healthy=False,
                         error="smartctl nicht gefunden (sudo apt install smartmontools)")
    # Versuche mit pkexec oder sudo
    prefix: List[str] = []
    pkexec = shutil.which("pkexec")
    sudo = shutil.which("sudo")
    if pkexec:
        prefix = [pkexec]
    elif sudo:
        prefix = [sudo]

    try:
        result = subprocess.run(
            [*prefix, smartctl, "-a", "-j", device],
            capture_output=True, text=True, timeout=30,
        )
        raw = result.stdout or ""
    except (subprocess.SubprocessError, OSError) as exc:
        return SmartInfo(device=device, healthy=False, error=str(exc))

    # JSON-Parsing (smartctl -j gibt JSON aus)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Fallback: Textausgabe ohne -j
        return _parse_smart_text(device, prefix, smartctl)

    info = SmartInfo(device=device, healthy=True, raw_output=raw)
    info.model = data.get("model_name", "")
    info.serial = data.get("serial_number", "")

    # SMART overall health
    smart_status = data.get("smart_status", {})
    info.healthy = smart_status.get("passed", True)

    # Temperatur
    temp_data = data.get("temperature", {})
    info.temperature_c = temp_data.get("current", 0)

    # Power-on hours
    poh = data.get("power_on_time", {})
    info.power_on_hours = poh.get("hours", 0)

    # SMART-Attribute (ID 5, 197, 198)
    for attr in data.get("ata_smart_attributes", {}).get("table", []):
        attr_id = attr.get("id", 0)
        raw_val = attr.get("raw", {}).get("value", 0)
        if attr_id == 5:
            info.reallocated_sectors = raw_val
        elif attr_id == 197:
            info.pending_sectors = raw_val
        elif attr_id == 198:
            info.uncorrectable_sectors = raw_val

    return info


def _parse_smart_text(device: str, prefix: List[str], smartctl: str) -> SmartInfo:
    """Fallback-Parser fuer smartctl ohne JSON."""
    try:
        result = subprocess.run(
            [*prefix, smartctl, "-a", device],
            capture_output=True, text=True, timeout=30,
        )
        raw = result.stdout or ""
    except (subprocess.SubprocessError, OSError) as exc:
        return SmartInfo(device=device, healthy=False, error=str(exc))

    info = SmartInfo(device=device, healthy=True, raw_output=raw)
    for line in raw.splitlines():
        low = line.lower()
        if "overall-health" in low:
            info.healthy = "passed" in low
        elif "device model" in low or "model number" in low:
            info.model = line.split(":", 1)[-1].strip()
        elif "serial number" in low:
            info.serial = line.split(":", 1)[-1].strip()
        elif "temperature_celsius" in low or "194 " in line:
            parts = line.split()
            for p in reversed(parts):
                if p.isdigit():
                    info.temperature_c = int(p)
                    break
        elif "reallocated_sector" in low or "  5 " in line:
            parts = line.split()
            for p in reversed(parts):
                if p.isdigit():
                    info.reallocated_sectors = int(p)
                    break
        elif "current_pending_sector" in low or "197 " in line:
            parts = line.split()
            for p in reversed(parts):
                if p.isdigit():
                    info.pending_sectors = int(p)
                    break
    return info


def format_smart_summary(info: SmartInfo) -> str:
    """Menschenlesbare SMART-Zusammenfassung."""
    if info.error:
        return f"{info.device}: {info.error}"
    status = "✓ OK" if info.healthy else "✗ FEHLER"
    parts = [f"{info.device}: {status}"]
    if info.model:
        parts.append(f"Modell: {info.model}")
    if info.temperature_c > 0:
        parts.append(f"Temp: {info.temperature_c}°C")
    if info.power_on_hours > 0:
        parts.append(f"Betriebsstunden: {info.power_on_hours:,}")
    if info.reallocated_sectors > 0:
        parts.append(f"⚠ Realloc: {info.reallocated_sectors}")
    if info.pending_sectors > 0:
        parts.append(f"⚠ Pending: {info.pending_sectors}")
    if info.uncorrectable_sectors > 0:
        parts.append(f"⚠ Uncorr: {info.uncorrectable_sectors}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# HPE SmartArray RAID controller info (ssacli / hpssacli)
# ---------------------------------------------------------------------------

@dataclass
class HpeRaidInfo:
    """HPE SmartArray RAID-Controller-Zusammenfassung."""
    available: bool = False
    tool_name: str = ""
    controllers: List[str] = field(default_factory=list)
    raw_output: str = ""
    error: str = ""


def read_hpe_raid_info() -> HpeRaidInfo:
    """Liest HPE SmartArray RAID-Informationen via ssacli oder hpssacli.

    Typisch auf HPE ProLiant DL/ML/BL Servern mit SmartArray Controllern.
    """
    # Suche nach ssacli (aktuell) oder hpssacli (legacy)
    tool: Optional[str] = None
    tool_name = ""
    for name in ("ssacli", "hpssacli", "hpacucli"):
        path = shutil.which(name)
        if path:
            tool = path
            tool_name = name
            break
    # Auch in typischen HPE-Pfaden suchen
    if not tool:
        for name in ("ssacli", "hpssacli"):
            for search_dir in ("/opt/hp/hpssacli/bld", "/opt/smartstorageadmin/ssacli/bin",
                                "/usr/sbin", "/opt/compaq/hpacucli/bld"):
                candidate = Path(search_dir) / name
                if candidate.exists() and candidate.is_file():
                    tool = str(candidate)
                    tool_name = name
                    break
            if tool:
                break

    if not tool:
        return HpeRaidInfo(available=False,
                           error="Kein HPE RAID-Tool gefunden (ssacli/hpssacli nicht installiert)")

    prefix: List[str] = []
    pkexec = shutil.which("pkexec")
    sudo = shutil.which("sudo")
    if pkexec:
        prefix = [pkexec]
    elif sudo:
        prefix = [sudo]

    try:
        result = subprocess.run(
            [*prefix, tool, "ctrl", "all", "show", "status"],
            capture_output=True, text=True, timeout=15,
        )
        ctrl_output = result.stdout or ""
    except (subprocess.SubprocessError, OSError) as exc:
        return HpeRaidInfo(available=True, tool_name=tool_name, error=str(exc))

    # Logische und physische Laufwerke dazu
    try:
        result2 = subprocess.run(
            [*prefix, tool, "ctrl", "all", "show", "config"],
            capture_output=True, text=True, timeout=15,
        )
        config_output = result2.stdout or ""
    except (subprocess.SubprocessError, OSError):
        config_output = ""

    full_output = f"=== Controller Status ===\n{ctrl_output}\n=== Configuration ===\n{config_output}"

    controllers: List[str] = []
    for line in ctrl_output.splitlines():
        stripped = line.strip()
        if stripped and ("Smart Array" in stripped or "Controller" in stripped):
            controllers.append(stripped)

    return HpeRaidInfo(
        available=True,
        tool_name=tool_name,
        controllers=controllers,
        raw_output=full_output,
    )
