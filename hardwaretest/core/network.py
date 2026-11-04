"""Read-only Linux network-adapter discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Optional


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class NetworkAdapter:
    name: str
    operstate: str
    carrier: Optional[bool]
    mac_address: str
    mtu: Optional[int]
    speed_mbps: Optional[int]
    duplex: str
    driver: str
    bus_address: str
    ipv4_addresses: tuple[str, ...] = ()
    ipv6_addresses: tuple[str, ...] = ()

    @property
    def primary_ipv4(self) -> str:
        return self.ipv4_addresses[0] if self.ipv4_addresses else ""


def list_network_adapters(
    sysfs_root: Path = Path("/sys/class/net"),
    run: RunCommand = subprocess.run,
) -> list[NetworkAdapter]:
    """Return physical and virtual non-loopback interfaces without privileges."""
    addresses = _read_ip_addresses(run)
    try:
        entries = sorted(sysfs_root.iterdir(), key=lambda item: item.name)
    except OSError:
        entries = []

    adapters: list[NetworkAdapter] = []
    for entry in entries:
        if entry.name == "lo":
            continue
        device = entry / "device"
        adapters.append(NetworkAdapter(
            name=entry.name,
            operstate=_read_text(entry / "operstate") or "unknown",
            carrier=_read_bool(entry / "carrier"),
            mac_address=_read_text(entry / "address") or "–",
            mtu=_read_int(entry / "mtu"),
            speed_mbps=_positive_int(entry / "speed"),
            duplex=_read_text(entry / "duplex") or "unknown",
            driver=_resolved_name(device / "driver") or "unknown",
            bus_address=_resolved_name(device) or "virtual",
            ipv4_addresses=tuple(addresses.get(entry.name, {}).get("ipv4", ())),
            ipv6_addresses=tuple(addresses.get(entry.name, {}).get("ipv6", ())),
        ))
    return adapters


# ---------------------------------------------------------------------------
# ethtool: Fehlerzähler, Selbsttest, SFP-Modul
# ---------------------------------------------------------------------------

# Statistiknamen (ethtool -S), deren Zuwachs auf Kabel-, Link- oder Treiberprobleme hindeutet.
_ERROR_STAT = re.compile(r"err|drop|discard|crc|fcs|miss|collision|align|symbol|jabber|runt|fragment|"
                         r"length|overrun|underrun|fifo|carrier|timeout|reset|bad|invalid|lost|link_down", re.I)
# Zähler, die trotz passendem Wortteil normal wachsen oder keine Fehler sind.
_STAT_IGNORE = re.compile(r"^(rx|tx)_(no_)?buffer|_ok$|good|queue_\d+_(bytes|packets)|_min_|_max_|link_up|"
                          r"rx_length_?(64|65|128|256|512|1024|1519)|frames_(64|65|128|256|512|1024|1519)|"
                          r"tx_timeout_count$|_bytes$|_packets$|^(rx|tx)_(unicast|multicast|broadcast)", re.I)
_SELFTEST_RESULT = re.compile(r"^The test result is (PASS|FAIL)", re.M)
_SELFTEST_ENTRY = re.compile(r"^\s*(\S.*?)\s{2,}(-?\d+)\s*$", re.M)


def read_ethtool_stats(interface: str, run: RunCommand = subprocess.run) -> dict[str, int]:
    """``ethtool -S <iface>`` als {Name: Wert}; leer, wenn ethtool fehlt oder der Treiber nichts liefert."""
    if not interface or (run is subprocess.run and shutil.which("ethtool") is None):
        return {}
    try:
        completed = run(["ethtool", "-S", interface], check=False, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return {}
    if completed.returncode != 0:
        return {}
    return parse_ethtool_stats(completed.stdout)


def parse_ethtool_stats(text: str) -> dict[str, int]:
    stats: dict[str, int] = {}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if not sep or not name or name.startswith("NIC statistics"):
            continue
        try:
            stats[name] = int(value, 0)
        except ValueError:
            continue
    return stats


def error_counter_deltas(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Nur Fehler-/Verlustzähler, die zwischen zwei ``ethtool -S``-Abfragen gewachsen sind."""
    deltas: dict[str, int] = {}
    for name, value in after.items():
        if name not in before or not _ERROR_STAT.search(name) or _STAT_IGNORE.search(name):
            continue
        delta = value - before[name]
        if delta > 0:
            deltas[name] = delta
    return deltas


def error_counters(stats: dict[str, int]) -> dict[str, int]:
    """Fehlerzähler mit Wert > 0 (Stand seit Treiberstart)."""
    return {name: value for name, value in stats.items()
            if value > 0 and _ERROR_STAT.search(name) and not _STAT_IGNORE.search(name)}


@dataclass
class SelfTestResult:
    """Ergebnis von ``ethtool -t <iface> [online|offline]``."""

    passed: Optional[bool] = None       # None: kein Ergebnis (Treiber ohne Selbsttest, Fehler)
    entries: list[tuple[str, int]] = field(default_factory=list)   # (Testname, Code; 0 = bestanden)
    error: str = ""

    @property
    def failed_entries(self) -> list[tuple[str, int]]:
        return [(name, code) for name, code in self.entries if code != 0]

    @property
    def inconclusive(self) -> bool:
        """Treiber meldet FAIL, aber kein Einzeltest ist ungleich 0: Der Test konnte nicht laufen
        (bnxt_en: NPAR/geteilte PF oder aktive VFs, Schnittstelle nicht aktiv) – kein Hardwarefehler."""
        return self.passed is False and bool(self.entries) and not self.failed_entries


def parse_ethtool_selftest(text: str, returncode: int = 0, stderr: str = "") -> SelfTestResult:
    """Ausgabe wie ``The test result is PASS`` gefolgt von ``Register test  (offline)   0``-Zeilen."""
    result = SelfTestResult()
    match = _SELFTEST_RESULT.search(text)
    if match:
        result.passed = match.group(1) == "PASS"
    for name, code in _SELFTEST_ENTRY.findall(text):
        if name.lower().startswith("the test result"):
            continue
        result.entries.append((name.strip(), int(code)))
    if result.passed is None:
        if result.entries:
            result.passed = not result.failed_entries
        else:
            result.error = (stderr or text).strip() or (f"Exit {returncode}" if returncode else "")
    return result


def run_ethtool_selftest(interface: str, offline: bool = False, run: RunCommand = subprocess.run,
                         timeout: float = 180) -> SelfTestResult:
    """Treiber-Selbsttest; ``offline`` unterbricht den Link kurz und testet auch Loopback/Interrupts."""
    command = ["ethtool", "-t", interface, "offline" if offline else "online"]
    try:
        completed = run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return SelfTestResult(error=str(exc))
    return parse_ethtool_selftest(completed.stdout, completed.returncode, completed.stderr)


_SFP_KEYS = (("Identifier", "Typ"), ("Vendor name", "Hersteller"), ("Vendor PN", "Teilenummer"),
             ("Vendor SN", "Seriennummer"), ("Vendor rev", "Revision"), ("Transceiver type", "Transceiver"),
             ("Connector", "Anschluss"), ("Encoding", "Kodierung"), ("BR, Nominal", "Rate"),
             ("Length (SMF)", "Reichweite SMF"), ("Length (OM3 50um)", "Reichweite OM3"),
             ("Length (Copper or Active cable)", "Kabellänge"), ("Laser wavelength", "Wellenlänge"),
             ("Module temperature", "Modultemperatur"), ("Module voltage", "Modulspannung"),
             ("Laser bias current", "Laserstrom"), ("Laser output power", "Sendeleistung"),
             ("Receiver signal average optical power", "Empfangsleistung"))


def parse_ethtool_module(text: str) -> dict[str, str]:
    """``ethtool -m <iface>``: ausgewählte Felder als {Schlüssel: Wert}; Alarm-/Warnflags werden gesammelt."""
    values: dict[str, str] = {}
    alarms: list[str] = []
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if not sep or not value:
            continue
        if name.endswith(("alarm", "warning")) and value.lower() in {"on", "yes", "true"}:
            alarms.append(name)
            continue
        for key, _label in _SFP_KEYS:
            if name == key or name.startswith(key + " "):
                if key in values:
                    values[key] += f" / {value}" if value not in values[key] else ""
                else:
                    values[key] = value
                break
    if alarms:
        values["Alarms"] = ", ".join(alarms)
    return values


def read_ethtool_module(interface: str, run: RunCommand = subprocess.run) -> tuple[dict[str, str], str]:
    """SFP-/QSFP-Daten (meist nur als root); ``({}, "")`` wenn kein Modul steckt."""
    try:
        completed = run(["ethtool", "-m", interface], check=False, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return {}, str(exc)
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout).strip()
        if re.search(r"no such device|not supported|invalid argument|no data available|Cannot get module", error, re.I):
            return {}, ""
        return {}, error
    return parse_ethtool_module(completed.stdout), ""


# ----------------------------------------------------------------- mstlink (Mellanox/NVIDIA)
MELLANOX_DRIVERS = ("mlx4_en", "mlx4_core", "mlx5_core", "mlx5_en", "mlxsw_spectrum")
_BDF = re.compile(r"^([0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
_RANGE_VALUE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*\[\s*(-?\d+(?:\.\d+)?)\s*\.\.\s*(-?\d+(?:\.\d+)?)\s*\]")
_MSTLINK_MODULE_KEYS = (("Identifier", "Typ"), ("Compliance", "Standard"), ("Cable Type", "Kabeltyp"),
                        ("Vendor Name", "Hersteller"), ("Vendor Part Number", "Teilenummer"),
                        ("Vendor Serial Number", "Seriennummer"), ("Transfer Distance [m]", "Reichweite m"),
                        ("Temperature [C]", "Temperatur °C"), ("Voltage [mV]", "Spannung mV"),
                        ("Bias Current [mA]", "Laserstrom mA"), ("Rx Power Current [dBm]", "Rx dBm"),
                        ("Tx Power Current [dBm]", "Tx dBm"))


def mstlink_available() -> bool:
    return shutil.which("mstlink") is not None


def is_mellanox(adapter: NetworkAdapter) -> bool:
    return adapter.driver in MELLANOX_DRIVERS and bool(_BDF.match(adapter.bus_address))


@dataclass
class MstlinkResult:
    """Ausgewertete Ausgabe von ``mstlink -d <PCI> --show_module --show_counters``."""

    sections: dict[str, dict[str, str]] = field(default_factory=dict)
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def get(self, section: str, key: str, default: str = "") -> str:
        return self.sections.get(section, {}).get(key, default)

    @property
    def state(self) -> str:
        return self.get("Operational Info", "State")

    @property
    def physical_state(self) -> str:
        return self.get("Operational Info", "Physical state")

    @property
    def link_up(self) -> Optional[bool]:
        phys = self.physical_state
        if not phys:
            return None
        return phys.lower().replace(" ", "") in {"linkup", "ethanfsmenable", "active"} or self.state.lower() == "active"

    @property
    def speed(self) -> str:
        speed = self.get("Operational Info", "Speed")
        width = self.get("Operational Info", "Width")
        return f"{speed} {width}".strip() if width and width not in {"N/A", "1x"} else speed

    @property
    def fec(self) -> str:
        return self.get("Operational Info", "FEC")

    @property
    def recommendation(self) -> str:
        return self.get("Troubleshooting Info", "Recommendation")

    @property
    def effective_errors(self) -> Optional[int]:
        return _first_int(self.get("Physical Counters and BER Info", "Effective Physical Errors"))

    @property
    def raw_ber(self) -> str:
        return self.get("Physical Counters and BER Info", "Raw Physical BER")

    @property
    def link_down_counter(self) -> Optional[int]:
        return _first_int(self.get("Physical Counters and BER Info", "Link Down Counter"))

    @property
    def module(self) -> dict[str, str]:
        values: dict[str, str] = {}
        section = self.sections.get("Module Info", {})
        for key, _label in _MSTLINK_MODULE_KEYS:
            value = section.get(key, "")
            if value and value != "N/A":
                values[key] = value
        return values

    @property
    def severity(self) -> str:
        """``error`` (Ausführung/Link), ``warn`` (Auffälligkeiten), ``ok`` oder ``info`` (keine Daten)."""
        if self.error:
            return "error"
        if not self.sections:
            return "info"
        if self.link_up is False:
            return "error"
        return "warn" if self.warnings else "ok"


def _first_int(text: str) -> Optional[int]:
    match = re.search(r"-?\d+", text or "")
    return int(match.group()) if match else None


def parse_mstlink(text: str) -> MstlinkResult:
    """Abschnitte („Titel“ + Unterstreichung) mit ``Schlüssel : Wert``-Zeilen einlesen und bewerten."""
    result = MstlinkResult()
    lines = text.splitlines()
    section = ""
    for index, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip():
            continue
        nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if nxt and set(nxt) <= {"-", "="} and ":" not in line:
            section = line.strip()
            result.sections.setdefault(section, {})
            continue
        if set(line.strip()) <= {"-", "="}:
            continue
        key, sep, value = line.partition(":")
        if not sep or not section:
            continue
        key, value = key.strip(), value.strip()
        if key:
            result.sections[section][key] = value
    _evaluate_mstlink(result)
    return result


def _evaluate_mstlink(result: MstlinkResult) -> None:
    recommendation = result.recommendation
    if recommendation and not re.search(r"no issue", recommendation, re.I):
        result.warnings.append(f"Empfehlung: {recommendation}")
    errors = result.effective_errors
    if errors:
        result.warnings.append(f"Effective Physical Errors: {errors}")
    downs = result.link_down_counter
    if downs:
        result.warnings.append(f"Link Down Counter: {downs}")
    module = result.sections.get("Module Info", {})
    for key in ("Temperature [C]", "Voltage [mV]", "Bias Current [mA]", "Rx Power Current [dBm]", "Tx Power Current [dBm]"):
        match = _RANGE_VALUE.match(module.get(key, ""))
        if not match:
            continue
        value, low, high = (float(group) for group in match.groups())
        if value < low or value > high:
            result.warnings.append(f"{key} {value:g} außerhalb [{low:g}..{high:g}]")
    for key, value in module.items():
        if key.endswith(("Alarm", "alarm")) and value.lower() not in {"n/a", "no", "off", "0", ""}:
            result.warnings.append(f"{key}: {value}")


def run_mstlink(bus_address: str, run: RunCommand = subprocess.run, timeout: int = 60) -> MstlinkResult:
    """``mstlink`` (mstflint, Root) für die PCI-Adresse ausführen; Fehler landen in ``result.error``."""
    if not _BDF.match(bus_address):
        return MstlinkResult(error=f"ungültige PCI-Adresse {bus_address!r}")
    command = ["mstlink", "-d", bus_address, "--show_module", "--show_counters"]
    try:
        completed = run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return MstlinkResult(error=str(exc))
    output = completed.stdout or ""
    result = parse_mstlink(output)
    messages = [line.strip() for line in completed.stderr.splitlines() if line.strip()]
    messages += [line.strip() for line in output.splitlines() if line.strip().startswith(("-E-", "-W-"))]
    message = "; ".join(dict.fromkeys(messages))
    if not result.sections:
        result.error = message or (completed.stderr or output).strip() or f"mstlink Exitcode {completed.returncode}"
    elif completed.returncode != 0 or message:
        result.warnings.append(message or f"mstlink Exitcode {completed.returncode}")
    return result


def _read_ip_addresses(run: RunCommand) -> dict[str, dict[str, list[str]]]:
    if run is subprocess.run and shutil.which("ip") is None:
        return {}
    try:
        completed = run(
            ["ip", "-j", "address", "show"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            return {}
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}

    result: dict[str, dict[str, list[str]]] = {}
    if not isinstance(payload, list):
        return result
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("ifname"), str):
            continue
        values = {"ipv4": [], "ipv6": []}
        for address in item.get("addr_info", []):
            if not isinstance(address, dict):
                continue
            local = address.get("local")
            prefix = address.get("prefixlen")
            family = address.get("family")
            if not isinstance(local, str) or not isinstance(prefix, int):
                continue
            value = f"{local}/{prefix}"
            if family == "inet":
                values["ipv4"].append(value)
            elif family == "inet6":
                values["ipv6"].append(value)
        result[item["ifname"]] = values
    return result


def _read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(_read_text(path))
    except ValueError:
        return None


def _positive_int(path: Path) -> Optional[int]:
    value = _read_int(path)
    return value if value is not None and value > 0 else None


def _read_bool(path: Path) -> Optional[bool]:
    value = _read_text(path)
    if value == "1":
        return True
    if value == "0":
        return False
    return None


def _resolved_name(path: Path) -> str:
    try:
        return path.resolve(strict=True).name
    except OSError:
        return ""
