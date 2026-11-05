"""Distributionserkennung für Installationshinweise und Paketabfragen (Debian/Ubuntu, RHEL/AlmaLinux, SUSE, Arch)."""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

# Debian-Paketname -> Name in anderen Familien (nur abweichende Namen).
_PACKAGE_NAMES = {
    "rhel": {"zfsutils-linux": "zfs", "iputils-ping": "iputils", "policykit-1": "polkit",
             "vmfs6-tools": "vmfs6-tools (Quellen: https://github.com/glandium/vmfs6-tools)",
             "vmfs-tools": "vmfs-tools (Quellen: https://github.com/glandium/vmfs-tools)"},
    "suse": {"zfsutils-linux": "zfs", "iputils-ping": "iputils", "policykit-1": "polkit"},
    "arch": {"zfsutils-linux": "zfs-utils", "iputils-ping": "iputils", "policykit-1": "polkit",
             "memtest86+": "memtest86+-efi"},
}
_INSTALL_COMMANDS = {"debian": "sudo apt install", "rhel": "sudo dnf install",
                     "suse": "sudo zypper install", "arch": "sudo pacman -S"}


def os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return data


@functools.lru_cache(maxsize=1)
def family() -> str:
    """Liefert "debian", "rhel", "suse", "arch" oder "unknown"."""
    info = os_release()
    ids = {info.get("ID", "")} | set(info.get("ID_LIKE", "").split())
    if ids & {"debian", "ubuntu", "linuxmint", "pop", "raspbian"}:
        return "debian"
    if ids & {"rhel", "fedora", "centos", "almalinux", "rocky", "ol", "amzn"}:
        return "rhel"
    if ids & {"suse", "opensuse", "sles", "opensuse-leap", "opensuse-tumbleweed"}:
        return "suse"
    if ids & {"arch", "manjaro", "endeavouros"}:
        return "arch"
    if shutil.which("apt-get"):
        return "debian"
    if shutil.which("dnf") or shutil.which("yum"):
        return "rhel"
    return "unknown"


def package_name(debian_name: str) -> str:
    return _PACKAGE_NAMES.get(family(), {}).get(debian_name, debian_name)


def install_command(*debian_names: str) -> str:
    """Installationsbefehl für die laufende Distribution, Paketnamen in Debian-Schreibweise."""
    prefix = _INSTALL_COMMANDS.get(family(), _INSTALL_COMMANDS["debian"])
    return prefix + " " + " ".join(package_name(name) for name in debian_names)


def package_installed(debian_name: str) -> bool:
    """Prüft über dpkg bzw. rpm, ob ein Paket installiert ist (False, wenn nicht feststellbar)."""
    name = package_name(debian_name)
    try:
        if shutil.which("dpkg"):
            result = subprocess.run(["dpkg", "-s", name], capture_output=True, text=True, timeout=5)
            return result.returncode == 0 and "Status: install ok installed" in result.stdout
        if shutil.which("rpm"):
            result = subprocess.run(["rpm", "-q", name], capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        if shutil.which("pacman"):
            return subprocess.run(["pacman", "-Q", name], capture_output=True, timeout=5).returncode == 0
    except (subprocess.SubprocessError, OSError):
        pass
    return False
