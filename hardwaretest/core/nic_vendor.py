"""Hersteller-Diagnose für Ethernet-Adapter mit proprietären NIC-Werkzeugen.

Die Werkzeuge (Broadcom ``niccli``/``bnxtnvm``, Intel ``nvmupdate64e``/``epct64e``,
Marvell/QLogic ``qaucli``/``qlnxcli``, Mellanox/NVIDIA MFT ``mlxfwmanager``/``mlxconfig``/
``mlxlink``, Solarflare/AMD ``sfupdate``/``sfboot``) sind nicht frei weitergebbar und nur im
internen Live-ISO (``--vendor-tools``) enthalten. Hier werden ausschließlich lesende
Inventar- und Diagnosebefehle ausgeführt; Firmware wird nie geschrieben.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Optional, Sequence

from .network import NetworkAdapter, _BDF

RunCommand = Callable[..., subprocess.CompletedProcess[str]]

# Zusätzliche Suchpfade neben $PATH (Installationsorte der Herstellerpakete).
TOOL_DIRS = (
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/sbin", "/usr/bin",
    "/opt/vendor-tools/bin", "/opt/mellanox/mft/bin", "/opt/Broadcom", "/opt/broadcom",
    "/opt/QLogic_Corporation/QConvergeConsoleCLI", "/opt/marvell", "/opt/marvell/qlnxcli/bin",
    "/opt/intel", "/opt/solarflare", "/usr/share/vendor-tools",
)
# Verzeichnisse, die rekursiv (begrenzt) durchsucht werden – dort entpackt das ISO Archive.
TOOL_TREES = ("/opt/vendor-tools", "/opt/Broadcom", "/opt/broadcom", "/opt/marvell", "/opt/intel")
_SKIP_DIRS = re.compile(r"(^|/)(win|Win|windows|Windows|arm|aarch64|i386|32|esx|ESX|vmware|freebsd|FreeBSD|EFI|efi)[^/]*(/|$)")


@dataclass(frozen=True)
class VendorTool:
    vendor: str
    drivers: tuple[str, ...]
    binaries: tuple[str, ...]   # Reihenfolge = Priorität; das erste gefundene gewinnt
    note: str = ""              # Hinweis zur Belegbarkeit/Besonderheit (für Hilfe/Log)


VENDOR_TOOLS: tuple[VendorTool, ...] = (
    VendorTool("Broadcom NetXtreme-E", ("bnxt_en", "bnxt_re"), ("niccli", "bnxtnvm")),
    VendorTool("Intel Ethernet", ("i40e", "ice", "ixgbe", "igb", "igc", "e1000e", "iavf", "idpf"),
               ("nvmupdate64e", "epct64e", "epct")),
    VendorTool("Marvell/QLogic FastLinQ", ("qede", "qed", "bnx2x", "bnx2", "qlcnic", "netxen_nic"),
               ("qaucli", "qlnxcli")),
    VendorTool("Mellanox/NVIDIA", ("mlx5_core", "mlx5_en", "mlx4_en", "mlx4_core", "mlxsw_spectrum"),
               ("mlxfwmanager", "mlxconfig", "mlxlink")),
    VendorTool("Solarflare/Xilinx/AMD", ("sfc", "sfc_ef100"), ("sfupdate", "sfboot")),
)

_WHICH_CACHE: dict[str, Optional[str]] = {}


def clear_cache() -> None:
    _WHICH_CACHE.clear()


def find_tool(name: str) -> Optional[str]:
    """Pfad zu einem Hersteller-Kommando (PATH, bekannte /opt-Pfade, /opt/vendor-tools rekursiv)."""
    if name in _WHICH_CACHE:
        return _WHICH_CACHE[name]
    found = shutil.which(name)
    if found is None:
        for directory in TOOL_DIRS:
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                found = str(candidate)
                break
    if found is None:
        for tree in TOOL_TREES:
            found = _search_tree(Path(tree), name)
            if found:
                break
    _WHICH_CACHE[name] = found
    return found


def _search_tree(root: Path, name: str, max_depth: int = 5) -> Optional[str]:
    if not root.is_dir():
        return None
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if rel != "." and _SKIP_DIRS.search(rel + "/"):
            dirnames[:] = []
            continue
        if len(Path(dirpath).parts) - base_depth >= max_depth:
            dirnames[:] = []
        if name in filenames:
            candidate = Path(dirpath) / name
            if os.access(candidate, os.X_OK):
                return str(candidate)
        dirnames.sort()
    return None


def tool_for_adapter(adapter: NetworkAdapter) -> Optional[VendorTool]:
    for tool in VENDOR_TOOLS:
        if adapter.driver in tool.drivers:
            return tool
    return None


def available_tool(adapter: NetworkAdapter) -> Optional[tuple[VendorTool, str, str]]:
    """``(VendorTool, Kommandoname, Pfad)`` des ersten installierten Werkzeugs für den Adapter."""
    tool = tool_for_adapter(adapter)
    if tool is None or adapter.bus_address == "virtual":
        return None
    for binary in tool.binaries:
        path = find_tool(binary)
        if path:
            return tool, binary, path
    return None


@dataclass
class VendorStep:
    title: str
    command: list[str]
    returncode: Optional[int] = None
    output: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.error


@dataclass
class VendorDiagResult:
    vendor: str = ""
    tool: str = ""
    steps: list[VendorStep] = field(default_factory=list)
    rows: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def severity(self) -> str:
        """``error`` (kein Werkzeug / alles fehlgeschlagen), ``warn`` (Auffälligkeiten) oder ``ok``."""
        if self.error:
            return "error"
        if self.steps and not any(step.ok for step in self.steps):
            return "error"
        return "warn" if self.warnings else "ok"

    @property
    def summary(self) -> str:
        done = sum(1 for step in self.steps if step.ok)
        parts = [f"{self.tool}: {done}/{len(self.steps)} Abfragen erfolgreich"]
        for key in ("Firmware", "Package Version", "Device Name", "Link Status", "Speed", "NVM Version", "FW Version"):
            for row_key, value in self.rows:
                if row_key.split("] ", 1)[-1].lower() == key.lower() and value:
                    parts.append(f"{key} {value}")
                    break
        return " | ".join(parts)


_KEY_VALUE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _./()\[\]-]{1,48}?)\s*[:=]\s*(\S.*?)\s*$")
_FAIL_WORDS = re.compile(r"\b(fail(?:ed|ure)?|error|not supported|unknown device|no (?:adapters?|devices?) found|timeout)\b", re.I)
_BENIGN = re.compile(r"\b(0 errors?|no errors?|errors?\s*[:=]\s*0\b|error_?count\s*[:=]\s*0\b)", re.I)


def _short_bdf(bus_address: str) -> str:
    return bus_address.split(":", 1)[1] if bus_address.count(":") == 2 else bus_address


def _niccli_index(listing: str, bus_address: str) -> Optional[str]:
    """Index des Adapters aus ``niccli --list`` anhand der PCI-Adresse (Spalte „PCIAddr“/„Bus“)."""
    short = _short_bdf(bus_address).lower()
    full = bus_address.lower()
    for line in listing.splitlines():
        low = line.lower()
        if short not in low and full not in low:
            continue
        match = re.match(r"\s*(\d+)\b", line)
        if match:
            return match.group(1)
    return None


def _epct_index(listing: str, bus_address: str) -> Optional[str]:
    """Index aus ``epct -devices`` (Spalte ``NIC``; Bus-Angabe als ``SS:BBB`` oder ``bus:dev.fn``)."""
    short = _short_bdf(bus_address).lower()
    bus = short.split(":", 1)[0]
    for line in listing.splitlines():
        low = line.lower()
        match = re.match(r"\s*(\d+)\s", line)
        if not match:
            continue
        if short in low or re.search(rf"\b0+:0*{re.escape(bus)}\b", low):
            return match.group(1)
    return None


def plan_commands(binary: str, adapter: NetworkAdapter) -> list[VendorStep]:
    """Lesende Befehle für das Werkzeug; Index-abhängige Schritte werden nachträglich ergänzt."""
    name, bdf = adapter.name, adapter.bus_address
    if binary == "niccli":
        return [VendorStep("Adapterliste", ["niccli", "--list"])]
    if binary == "bnxtnvm":
        return [VendorStep("Geräteinfo", ["bnxtnvm", f"-dev={name}", "device_info"])]
    if binary == "nvmupdate64e":
        return [VendorStep("NVM-Inventar", ["nvmupdate64e", "-i"])]
    if binary in {"epct64e", "epct"}:
        return [VendorStep("Geräteliste", [binary, "-devices"])]
    if binary == "qaucli":
        return [VendorStep("Adapterübersicht", ["qaucli", "-pr", "nic", "-i"]),
                VendorStep("Allgemeine Informationen", ["qaucli", "-pr", "nic", "-g"])]
    if binary == "qlnxcli":
        return [VendorStep("Adapterübersicht", ["qlnxcli", "-i"])]
    if binary in {"mlxfwmanager", "mlxconfig", "mlxlink"}:
        steps = []
        if find_tool("mlxfwmanager"):
            steps.append(VendorStep("Firmware-Inventar", ["mlxfwmanager", "--query", "-d", bdf]))
        if find_tool("mlxconfig"):
            steps.append(VendorStep("Konfiguration", ["mlxconfig", "-d", bdf, "query"]))
        if find_tool("mlxlink"):
            steps.append(VendorStep("Link-Diagnose", ["mlxlink", "-d", bdf, "--show_module", "--show_counters"]))
        return steps
    if binary == "sfupdate":
        steps = [VendorStep("Firmware-Inventar", ["sfupdate"])]
        if find_tool("sfboot"):
            steps.append(VendorStep("Adapterkonfiguration", ["sfboot"]))
        return steps
    if binary == "sfboot":
        return [VendorStep("Adapterkonfiguration", ["sfboot"])]
    return []


def _follow_up(binary: str, step: VendorStep, adapter: NetworkAdapter) -> list[VendorStep]:
    """Schritte, die erst nach dem Inventar möglich sind (Adapterindex bekannt)."""
    if not step.ok:
        return []
    if binary == "niccli" and step.title == "Adapterliste":
        index = _niccli_index(step.output, adapter.bus_address)
        if index is None:
            return []
        return [VendorStep("Geräteinfo", ["niccli", "-i", index, "show"]),
                VendorStep("Firmware-Paket", ["niccli", "-i", index, "show", "--pkg_ver"]),
                VendorStep("Link-Status", ["niccli", "-i", index, "link", "-s"]),
                VendorStep("Transceiver", ["niccli", "-i", index, "cable", "-m", "--show"])]
    if binary in {"epct64e", "epct"} and step.title == "Geräteliste":
        index = _epct_index(step.output, adapter.bus_address)
        if index is None:
            return []
        return [VendorStep("Port-Konfiguration", [binary, f"-nic={index}", "-get"])]
    return []


def _clean_env() -> dict[str, str]:
    # Hardwaretest (PyInstaller) vererbt LD_LIBRARY_PATH; Herstellerbinaries brechen damit.
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    return env


def _execute(step: VendorStep, path: str, run: RunCommand, timeout: int) -> None:
    command = [path] + step.command[1:]
    try:
        completed = run(command, check=False, capture_output=True, text=True, timeout=timeout, env=_clean_env())
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        step.error = str(exc)
        return
    step.returncode = completed.returncode
    step.output = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
    if completed.returncode != 0:
        tail = [line.strip() for line in step.output.splitlines() if line.strip()]
        step.error = (tail[-1] if tail else "") or f"Exitcode {completed.returncode}"


def _collect_rows(result: VendorDiagResult, step: VendorStep) -> None:
    seen: set[str] = set()
    for raw in step.output.splitlines():
        line = raw.rstrip()
        if not line.strip() or set(line.strip()) <= {"-", "=", "+", "|"}:
            continue
        match = _KEY_VALUE.match(line)
        if match:
            key, value = match.group(1).strip(), match.group(2).strip()
            if key.lower() in seen or len(value) > 160:
                continue
            seen.add(key.lower())
            result.rows.append((f"[{step.title}] {key}", value))
        if _FAIL_WORDS.search(line) and not _BENIGN.search(line):
            text = line.strip()
            if len(text) <= 200:
                result.warnings.append(f"{step.title}: {text}")


def run_vendor_diagnostics(adapter: NetworkAdapter, run: RunCommand = subprocess.run,
                           timeout: int = 120, tool: Optional[tuple[VendorTool, str, str]] = None) -> VendorDiagResult:
    """Alle lesenden Abfragen des passenden Herstellerwerkzeugs ausführen und auswerten."""
    selected = tool or available_tool(adapter)
    if selected is None:
        vendor = tool_for_adapter(adapter)
        name = vendor.vendor if vendor else adapter.driver
        return VendorDiagResult(vendor=name, error=f"kein Herstellerwerkzeug für {name} ({adapter.driver}) installiert")
    vendor, binary, path = selected
    if not _BDF.match(adapter.bus_address):
        return VendorDiagResult(vendor=vendor.vendor, tool=binary,
                                error=f"ungültige PCI-Adresse {adapter.bus_address!r}")
    result = VendorDiagResult(vendor=vendor.vendor, tool=binary)
    queue = plan_commands(binary, adapter)
    if not queue:
        result.error = f"keine Abfragen für {binary} definiert"
        return result
    while queue:
        step = queue.pop(0)
        tool_path = path if step.command[0] == binary else (find_tool(step.command[0]) or step.command[0])
        _execute(step, tool_path, run, timeout)
        result.steps.append(step)
        if step.ok:
            _collect_rows(result, step)
        else:
            result.warnings.append(f"{step.title} ({' '.join(step.command)}): {step.error}")
        queue = _follow_up(binary, step, adapter) + queue
    if binary == "niccli" and len(result.steps) == 1 and result.steps[0].ok:
        result.warnings.append(f"Adapter {adapter.bus_address} nicht in 'niccli --list' gefunden")
    # Doppelte Warnungen zusammenfassen, Reihenfolge erhalten
    result.warnings = list(dict.fromkeys(result.warnings))
    return result


def describe_tools(adapters: Sequence[NetworkAdapter]) -> list[str]:
    """Kurzliste „Hersteller: Kommando (Pfad)“ für Log/Systeminfo."""
    lines: list[str] = []
    for adapter in adapters:
        found = available_tool(adapter)
        if found:
            vendor, binary, path = found
            text = f"{adapter.name}: {vendor.vendor} → {binary} ({path})"
            if text not in lines:
                lines.append(text)
    return lines
