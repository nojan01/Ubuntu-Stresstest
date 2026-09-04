"""Read-only Linux network-adapter discovery."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
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
