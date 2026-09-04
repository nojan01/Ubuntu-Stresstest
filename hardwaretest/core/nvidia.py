"""NVIDIA GPU discovery and health-data parsing via nvidia-smi."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class NvidiaGpu:
    index: int
    uuid: str
    model: str
    driver_version: str
    cuda_version: str
    memory_total_mib: Optional[int]
    memory_used_mib: Optional[int]
    temperature_c: Optional[int]
    power_draw_w: Optional[float]
    power_limit_w: Optional[float]
    ecc_mode: str = "N/A"
    ecc_corrected: Optional[int] = None
    ecc_uncorrected: Optional[int] = None
    utilization_gpu_percent: Optional[int] = None
    clock_graphics_mhz: Optional[int] = None
    clock_sm_mhz: Optional[int] = None
    clock_memory_mhz: Optional[int] = None
    pcie_generation_current: Optional[int] = None
    pcie_generation_max: Optional[int] = None
    pcie_width_current: Optional[int] = None
    pcie_width_max: Optional[int] = None


@dataclass(frozen=True)
class LinuxNvidiaStatus:
    pci_devices: tuple[str, ...]
    bound_drivers: tuple[str, ...]
    kernel_modules: tuple[str, ...]
    device_nodes: tuple[str, ...]
    nvidia_smi_installed: bool
    nvidia_smi_works: bool
    nvidia_smi_error: str = ""

    @property
    def hardware_present(self) -> bool:
        return bool(self.pci_devices)

    @property
    def driver_ready(self) -> bool:
        return (
            self.hardware_present
            and self.nvidia_smi_installed
            and self.nvidia_smi_works
            and "nvidia" in self.kernel_modules
            and bool(self.device_nodes)
        )


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def nvidia_smi_available() -> bool:
    return shutil.which("nvidia-smi") is not None


def inspect_linux_nvidia(
    sysfs_root: Path = Path("/sys/bus/pci/devices"),
    dev_root: Path = Path("/dev"),
    proc_modules: Path = Path("/proc/modules"),
    run: RunCommand = subprocess.run,
) -> LinuxNvidiaStatus:
    """Check PCI visibility, kernel binding, device nodes and nvidia-smi."""
    pci_devices: list[str] = []
    drivers: list[str] = []
    try:
        entries = sorted(sysfs_root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            vendor = (entry / "vendor").read_text().strip().casefold()
            device_class = (entry / "class").read_text().strip().casefold()
        except OSError:
            continue
        if vendor != "0x10de" or not device_class.startswith("0x03"):
            continue
        pci_devices.append(entry.name)
        try:
            drivers.append((entry / "driver").resolve(strict=True).name)
        except OSError:
            drivers.append("ungebunden")

    modules: list[str] = []
    try:
        for line in proc_modules.read_text().splitlines():
            name = line.split(maxsplit=1)[0] if line else ""
            if name.startswith("nvidia"):
                modules.append(name)
    except OSError:
        pass
    try:
        nodes = sorted(path.name for path in dev_root.glob("nvidia*") if path.exists())
    except OSError:
        nodes = []

    installed = nvidia_smi_available() if run is subprocess.run else True
    works = False
    error = ""
    if installed:
        try:
            completed = run(
                ["nvidia-smi", "-L"], check=False, capture_output=True, text=True, timeout=20
            )
            works = completed.returncode == 0
            if not works:
                error = (completed.stderr or completed.stdout).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
    return LinuxNvidiaStatus(
        pci_devices=tuple(pci_devices),
        bound_drivers=tuple(drivers),
        kernel_modules=tuple(modules),
        device_nodes=tuple(nodes),
        nvidia_smi_installed=installed,
        nvidia_smi_works=works,
        nvidia_smi_error=error,
    )


def parse_cuda_version(output: str) -> str:
    match = re.search(r"CUDA Version:\s*([0-9.]+|N/A)", output, re.IGNORECASE)
    return match.group(1) if match else "N/A"


def parse_nvidia_gpu_csv(
    gpu_csv: str,
    cuda_version: str = "N/A",
    ecc_csv: str = "",
) -> list[NvidiaGpu]:
    ecc_by_index: dict[int, tuple[str, Optional[int], Optional[int]]] = {}
    for row in csv.reader(ecc_csv.splitlines(), skipinitialspace=True):
        if len(row) < 4:
            continue
        index = _optional_int(row[0])
        if index is None:
            continue
        ecc_by_index[index] = (
            _clean(row[1]),
            _optional_int(row[2]),
            _optional_int(row[3]),
        )

    devices: list[NvidiaGpu] = []
    for row in csv.reader(gpu_csv.splitlines(), skipinitialspace=True):
        if len(row) < 9:
            continue
        index = _optional_int(row[0])
        if index is None:
            continue
        ecc_mode, corrected, uncorrected = ecc_by_index.get(index, ("N/A", None, None))
        devices.append(NvidiaGpu(
            index=index,
            uuid=_clean(row[1]),
            model=_clean(row[2]),
            driver_version=_clean(row[3]),
            cuda_version=cuda_version,
            memory_total_mib=_optional_int(row[4]),
            memory_used_mib=_optional_int(row[5]),
            temperature_c=_optional_int(row[6]),
            power_draw_w=_optional_float(row[7]),
            power_limit_w=_optional_float(row[8]),
            ecc_mode=ecc_mode,
            ecc_corrected=corrected,
            ecc_uncorrected=uncorrected,
            utilization_gpu_percent=_row_optional_int(row, 9),
            clock_graphics_mhz=_row_optional_int(row, 10),
            clock_sm_mhz=_row_optional_int(row, 11),
            clock_memory_mhz=_row_optional_int(row, 12),
            pcie_generation_current=_row_optional_int(row, 13),
            pcie_generation_max=_row_optional_int(row, 14),
            pcie_width_current=_row_optional_int(row, 15),
            pcie_width_max=_row_optional_int(row, 16),
        ))
    return devices


def list_nvidia_gpus(run: RunCommand = subprocess.run) -> list[NvidiaGpu]:
    """Return all GPUs visible to the installed NVIDIA driver."""
    if run is subprocess.run and not nvidia_smi_available():
        return []
    fields = (
        "index,uuid,name,driver_version,memory.total,memory.used,"
        "temperature.gpu,power.draw,power.limit,utilization.gpu,"
        "clocks.current.graphics,clocks.current.sm,clocks.current.memory,"
        "pcie.link.gen.current,pcie.link.gen.max,"
        "pcie.link.width.current,pcie.link.width.max"
    )
    completed = run(
        ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "nvidia-smi failed").strip()
        raise RuntimeError(message)

    overview = run(
        ["nvidia-smi"], check=False, capture_output=True, text=True, timeout=20
    )
    cuda_version = parse_cuda_version(overview.stdout) if overview.returncode == 0 else "N/A"

    ecc_fields = (
        "index,ecc.mode.current,ecc.errors.corrected.volatile.total,"
        "ecc.errors.uncorrected.volatile.total"
    )
    ecc = run(
        ["nvidia-smi", f"--query-gpu={ecc_fields}", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return parse_nvidia_gpu_csv(
        completed.stdout,
        cuda_version=cuda_version,
        ecc_csv=ecc.stdout if ecc.returncode == 0 else "",
    )


def _clean(value: object) -> str:
    text = str(value).strip()
    return "N/A" if not text or text.casefold() in {"n/a", "[n/a]", "not supported"} else text


def _optional_int(value: object) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _row_optional_int(row: list[str], index: int) -> Optional[int]:
    return _optional_int(row[index]) if index < len(row) else None
