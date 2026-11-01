"""GPU-Messwerte aus sysfs für AMD (amdgpu), Intel (i915/xe) und NVIDIA (nouveau).

Nur lesend und ohne Root-Rechte. Nicht jeder Treiber liefert jeden Wert;
fehlende Werte bleiben ``None``. Für den proprietären NVIDIA-Treiber gibt es
den Tab „NVIDIA GPU“ (nvidia-smi).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

DRM = Path("/sys/class/drm")
_CARD = re.compile(r"card\d+$")


@dataclass
class DrmGpu:
    card: str
    pci: str
    driver: str
    vendor_id: int
    device_id: int
    temperature_c: Optional[float] = None
    power_w: Optional[float] = None
    fan_rpm: Optional[int] = None
    busy_percent: Optional[int] = None
    clock_mhz: Optional[int] = None
    memory_clock_mhz: Optional[int] = None
    vram_used: Optional[int] = None
    vram_total: Optional[int] = None


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def _int(path: Path, base: int = 10) -> Optional[int]:
    text = _read(path)
    if text is None:
        return None
    try:
        return int(text, base)
    except ValueError:
        return None


def _active_level_mhz(path: Path) -> Optional[int]:
    """Aktive Stufe aus amdgpu ``pp_dpm_sclk``/``pp_dpm_mclk`` (Zeile mit ``*``)."""
    text = _read(path) or ""
    for line in text.splitlines():
        if line.rstrip().endswith("*"):
            match = re.search(r"(\d+)\s*[Mm]hz", line)
            if match:
                return int(match.group(1))
    return None


def _hwmon(device: Path) -> Optional[Path]:
    hwmons = sorted((device / "hwmon").glob("hwmon*"))
    return hwmons[0] if hwmons else None


def read_gpu(card: Path) -> Optional[DrmGpu]:
    device = card / "device"
    vendor = _int(device / "vendor", 16)
    if vendor is None:
        return None
    try:
        driver = (device / "driver").resolve().name
        pci = device.resolve().name
    except OSError:
        return None
    gpu = DrmGpu(card=card.name, pci=pci, driver=driver, vendor_id=vendor,
                 device_id=_int(device / "device", 16) or 0)

    hwmon = _hwmon(device)
    if hwmon is not None:
        temp = _int(hwmon / "temp1_input")
        gpu.temperature_c = temp / 1000 if temp is not None else None
        power = _int(hwmon / "power1_average")
        if power is None:
            power = _int(hwmon / "power1_input")
        gpu.power_w = power / 1_000_000 if power is not None else None
        gpu.fan_rpm = _int(hwmon / "fan1_input")

    if driver == "amdgpu":
        gpu.busy_percent = _int(device / "gpu_busy_percent")
        gpu.clock_mhz = _active_level_mhz(device / "pp_dpm_sclk")
        gpu.memory_clock_mhz = _active_level_mhz(device / "pp_dpm_mclk")
        gpu.vram_used = _int(device / "mem_info_vram_used")
        gpu.vram_total = _int(device / "mem_info_vram_total")
    elif driver == "i915":
        gpu.clock_mhz = _int(card / "gt_act_freq_mhz")
        if gpu.clock_mhz is None:
            gpu.clock_mhz = _int(card / "gt_cur_freq_mhz")
    elif driver == "xe":
        for freq in sorted(device.glob("tile*/gt*/freq0")):
            gpu.clock_mhz = _int(freq / "act_freq")
            if gpu.clock_mhz is not None:
                break
    return gpu


def list_drm_gpus(root: Path = DRM) -> List[DrmGpu]:
    """Alle Grafikkarten mit DRM-Treiber (ohne Anschlüsse wie ``card0-DP-1``)."""
    if not root.is_dir():
        return []
    cards = sorted((path for path in root.iterdir() if _CARD.match(path.name)),
                   key=lambda path: int(path.name[4:]))
    return [gpu for gpu in (read_gpu(card) for card in cards) if gpu is not None]


def match_drm(vulkan_gpus: List[dict], drm_gpus: List[DrmGpu]) -> dict:
    """Ordnet Vulkan-Geräte über Hersteller- und Geräte-ID den DRM-Karten zu (Reihenfolge bei Gleichstand)."""
    free = list(drm_gpus)
    result = {}
    for gpu in vulkan_gpus:
        for candidate in free:
            if candidate.vendor_id == gpu.get("vendor_id") and candidate.device_id == gpu.get("device_id"):
                result[gpu["index"]] = candidate
                free.remove(candidate)
                break
    return result
