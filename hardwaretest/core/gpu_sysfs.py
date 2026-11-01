"""GPU-Messwerte aus sysfs für AMD (amdgpu), Intel (i915/xe) und NVIDIA (nouveau).

Nur lesend und ohne Root-Rechte. Nicht jeder Treiber liefert jeden Wert;
fehlende Werte bleiben ``None``. Für den proprietären NVIDIA-Treiber gibt es
den Tab „NVIDIA GPU“ (nvidia-smi).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

DRM = Path("/sys/class/drm")
HWMON = Path("/sys/class/hwmon")
POWERCAP = Path("/sys/class/powercap")
PROC = Path("/proc")
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
    max_clock_mhz: Optional[int] = None
    memory_clock_mhz: Optional[int] = None
    vram_used: Optional[int] = None
    vram_total: Optional[int] = None
    # True: Temperatur des CPU-Pakets (integrierte Intel-GPU ohne eigenen Sensor)
    package_temperature: bool = False
    # Leistung der integrierten Intel-GPU aus RAPL „uncore“; ohne Leserecht (nur root) True
    power_rapl: bool = False
    power_needs_root: bool = False


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


def _highest_level_mhz(path: Path) -> Optional[int]:
    levels = [int(match) for match in re.findall(r"(\d+)\s*[Mm]hz", _read(path) or "")]
    return max(levels) if levels else None


def cpu_package_temperature(root: Optional[Path] = None) -> Optional[float]:
    """Temperatur „Package id 0“ von coretemp (die integrierte GPU sitzt im selben Chip)."""
    for hwmon in sorted((root or HWMON).glob("hwmon*")):
        if _read(hwmon / "name") != "coretemp":
            continue
        for label in sorted(hwmon.glob("temp*_label")):
            if _read(label) == "Package id 0":
                value = _int(label.with_name(label.name.replace("_label", "_input")))
                return value / 1000 if value is not None else None
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
        gpu.max_clock_mhz = _highest_level_mhz(device / "pp_dpm_sclk")
        gpu.memory_clock_mhz = _active_level_mhz(device / "pp_dpm_mclk")
        gpu.vram_used = _int(device / "mem_info_vram_used")
        gpu.vram_total = _int(device / "mem_info_vram_total")
    elif driver == "i915":
        gpu.clock_mhz = _int(card / "gt_act_freq_mhz")
        if gpu.clock_mhz is None:
            gpu.clock_mhz = _int(card / "gt_cur_freq_mhz")
        gpu.max_clock_mhz = _int(card / "gt_RP0_freq_mhz") or _int(card / "gt_max_freq_mhz")
    elif driver == "xe":
        for freq in sorted(device.glob("tile*/gt*/freq0")):
            gpu.clock_mhz = _int(freq / "act_freq")
            gpu.max_clock_mhz = _int(freq / "rp0_freq") or _int(freq / "max_freq")
            if gpu.clock_mhz is not None:
                break
    if driver in ("i915", "xe") and gpu.temperature_c is None and pci.startswith("0000:00:"):
        gpu.temperature_c = cpu_package_temperature()
        gpu.package_temperature = gpu.temperature_c is not None
    return gpu


def list_drm_gpus(root: Path = DRM) -> List[DrmGpu]:
    """Alle Grafikkarten mit DRM-Treiber (ohne Anschlüsse wie ``card0-DP-1``)."""
    if not root.is_dir():
        return []
    cards = sorted((path for path in root.iterdir() if _CARD.match(path.name)),
                   key=lambda path: int(path.name[4:]))
    return [gpu for gpu in (read_gpu(card) for card in cards) if gpu is not None]


def _parse_fdinfo(text: str) -> Dict[str, str]:
    result = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.startswith("drm-"):
            result[key.strip()] = value.strip()
    return result


def _leading_int(value: str) -> Optional[int]:
    match = re.match(r"\d+", value)
    return int(match.group()) if match else None


def read_drm_clients(pci: str, proc: Path = PROC) -> Dict[str, Dict[str, int]]:
    """Zähler aller lesbaren DRM-Clients einer GPU aus ``/proc/*/fdinfo`` (wie ``gputop``).

    Ohne root nur Prozesse des eigenen Benutzers – dazu gehört der Vulkan-Lasttest.
    i915: ``drm-engine-<name>`` (ns belegt); xe: ``drm-cycles-<klasse>`` und ``drm-total-cycles-<klasse>``.
    """
    clients: Dict[str, Dict[str, int]] = {}
    try:
        pids = [entry for entry in proc.iterdir() if entry.name.isdigit()]
    except OSError:
        return clients
    for pid in pids:
        try:
            fds = list((pid / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                if not os.readlink(fd).startswith("/dev/dri/"):
                    continue
                info = _parse_fdinfo((pid / "fdinfo" / fd.name).read_text())
            except OSError:
                continue
            if info.get("drm-pdev") != pci or "drm-client-id" not in info:
                continue
            counters = {}
            for key, value in info.items():
                if key.startswith(("drm-engine-", "drm-cycles-", "drm-total-cycles-")) \
                        and not key.startswith("drm-engine-capacity-"):
                    number = _leading_int(value)
                    if number is not None:
                        counters[key] = number
            clients[info["drm-client-id"]] = counters
    return clients


def busy_percent(previous: Dict[str, Dict[str, int]], current: Dict[str, Dict[str, int]],
                 elapsed_ns: int) -> Optional[int]:
    """Auslastung der am stärksten belegten Engine zwischen zwei Abfragen (0–100)."""
    busy: Dict[str, int] = {}
    total: Dict[str, int] = {}
    for client, counters in current.items():
        before = previous.get(client, {})
        for key, value in counters.items():
            delta = max(0, value - before.get(key, 0))
            if key.startswith("drm-total-cycles-"):
                cls = key[len("drm-total-cycles-"):]
                total[cls] = max(total.get(cls, 0), delta)
            elif key.startswith("drm-cycles-"):
                cls = key[len("drm-cycles-"):]
                busy[cls] = busy.get(cls, 0) + delta
            else:
                cls = key[len("drm-engine-"):]
                busy[cls] = busy.get(cls, 0) + delta
    values = []
    for cls, amount in busy.items():
        if cls in total:
            if total[cls]:
                values.append(amount / total[cls])
        elif elapsed_ns > 0:
            values.append(amount / elapsed_ns)
    if not values:
        return 0 if current or previous else None
    return round(min(1.0, max(values)) * 100)


def rapl_uncore_zone(root: Path = POWERCAP) -> Optional[Path]:
    """RAPL-Zone „uncore“ (PP1, integrierte Grafik) des ersten Pakets."""
    for zone in sorted(root.glob("intel-rapl:0:*")):
        if _read(zone / "name") == "uncore":
            return zone
    return None


class IntelGpuSampler:
    """Ergänzt bei Intel-GPUs (i915/xe) Auslastung und – integriert – Leistung über zwei Abfragen."""

    def __init__(self, proc: Path = PROC, powercap: Path = POWERCAP,
                 clock: Callable[[], int] = time.monotonic_ns) -> None:
        self._proc = proc
        self._powercap = powercap
        self._clock = clock
        self._previous: Dict[str, Tuple[int, Dict[str, Dict[str, int]], Optional[int]]] = {}

    def reset(self) -> None:
        self._previous.clear()

    def update(self, gpu: DrmGpu) -> None:
        if gpu.driver not in ("i915", "xe"):
            return
        now = self._clock()
        clients = read_drm_clients(gpu.pci, self._proc)
        energy = None
        zone = None
        if gpu.power_w is None and gpu.pci.startswith("0000:00:"):
            zone = rapl_uncore_zone(self._powercap)
            if zone is not None:
                energy = _int(zone / "energy_uj")
                gpu.power_needs_root = energy is None
        previous = self._previous.get(gpu.pci)
        self._previous[gpu.pci] = (now, clients, energy)
        if previous is None:
            return
        before, old_clients, old_energy = previous
        elapsed = now - before
        if gpu.busy_percent is None:
            gpu.busy_percent = busy_percent(old_clients, clients, elapsed)
        if energy is not None and old_energy is not None and elapsed > 0:
            delta = energy - old_energy
            if delta < 0:
                delta += _int(zone / "max_energy_range_uj") or 0
            if delta >= 0:
                gpu.power_w = delta * 1000 / elapsed  # µJ/ns → W
                gpu.power_rapl = True


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
