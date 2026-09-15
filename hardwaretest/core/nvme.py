"""Safe NVMe discovery, health parsing and self-test command helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shutil
import subprocess
from typing import Callable, Optional, Sequence


@dataclass(frozen=True)
class NvmeDevice:
    namespace_path: str
    controller_path: str
    model: str
    serial: str
    firmware: str
    size_bytes: int


@dataclass(frozen=True)
class NvmeHealth:
    device: str
    critical_warning: int = 0
    temperature_c: Optional[int] = None
    available_spare: Optional[int] = None
    percentage_used: Optional[int] = None
    power_on_hours: Optional[int] = None
    unsafe_shutdowns: Optional[int] = None
    media_errors: Optional[int] = None
    error_log_entries: Optional[int] = None
    data_units_read: Optional[int] = None
    data_units_written: Optional[int] = None

    @property
    def healthy(self) -> bool:
        return self.critical_warning == 0 and (self.media_errors or 0) == 0


@dataclass(frozen=True)
class NvmeSelfTestStatus:
    """Current non-destructive device self-test state reported by nvme-cli."""
    active: bool
    completion_percent: Optional[int] = None
    result_code: Optional[int] = None
    raw_log: str = ""

    @property
    def passed(self) -> Optional[bool]:
        if self.result_code is None or self.result_code == 0xF:
            return None
        return self.result_code == 0


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def nvme_available() -> bool:
    return shutil.which("nvme") is not None


def controller_path(path: str) -> str:
    """Return ``/dev/nvmeX`` for a namespace or controller path."""
    match = re.fullmatch(r"(/dev/nvme\d+)(?:n\d+)?", path.strip())
    if not match:
        raise ValueError(f"Kein gültiger NVMe-Gerätepfad: {path}")
    return match.group(1)


def list_nvme_devices(run: RunCommand = subprocess.run) -> list[NvmeDevice]:
    """Discover NVMe namespaces without requiring privileged access."""
    if not nvme_available():
        return []
    try:
        completed = run(
            ["nvme", "list", "-o", "json"], check=False, capture_output=True, text=True, timeout=15
        )
        if completed.returncode != 0:
            return []
        payload = json.loads(completed.stdout or "{}")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return []

    devices: list[NvmeDevice] = []
    for entry in payload.get("Devices", []):
        path = str(entry.get("DevicePath", "")).strip()
        try:
            ctrl = controller_path(path)
        except ValueError:
            continue
        devices.append(
            NvmeDevice(
                namespace_path=path,
                controller_path=ctrl,
                model=str(entry.get("ModelNumber", "")).strip(),
                serial=str(entry.get("SerialNumber", "")).strip(),
                firmware=str(entry.get("Firmware", "")).strip(),
                size_bytes=int(entry.get("PhysicalSize") or entry.get("UsedBytes") or 0),
            )
        )
    return devices


def parse_smart_log(device: str, raw_json: str) -> NvmeHealth:
    """Parse ``nvme smart-log -o json`` output without executing commands."""
    data = json.loads(raw_json)
    temperature = _as_int(data.get("temperature"))
    # nvme-cli reports a Kelvin value on many controllers, but some modern
    # versions already emit Celsius.  Values over 200 are safely Kelvin.
    if temperature is not None and temperature > 200:
        temperature -= 273
    return NvmeHealth(
        device=controller_path(device),
        critical_warning=_as_int(data.get("critical_warning")) or 0,
        temperature_c=temperature,
        available_spare=_as_int(data.get("avail_spare")),
        percentage_used=_as_int(data.get("percentage_used")),
        power_on_hours=_as_int(data.get("power_on_hours")),
        unsafe_shutdowns=_as_int(data.get("unsafe_shutdowns")),
        media_errors=_as_int(data.get("media_errors")),
        error_log_entries=_as_int(data.get("num_err_log_entries")),
        data_units_read=_as_int(data.get("data_units_read")),
        data_units_written=_as_int(data.get("data_units_written")),
    )


def read_nvme_smart(
    device: str,
    command_prefix: Sequence[str] = (),
    run: RunCommand = subprocess.run,
) -> NvmeHealth:
    """Read SMART/health data. ``command_prefix`` can be ``("pkexec",)``."""
    ctrl = controller_path(device)
    completed = run(
        [*command_prefix, "nvme", "smart-log", ctrl, "-o", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "nvme smart-log failed").strip()
        raise RuntimeError(message)
    return parse_smart_log(ctrl, completed.stdout)


def start_nvme_self_test(
    device: str,
    test_type: str,
    command_prefix: Sequence[str] = (),
    run: RunCommand = subprocess.run,
) -> None:
    """Start a non-destructive NVMe device self-test (short or extended)."""
    if test_type not in {"short", "extended"}:
        raise ValueError(f"Unbekannter NVMe-Selbsttest: {test_type}")
    selector = "1" if test_type == "short" else "2"
    completed = run(
        [*command_prefix, "nvme", "device-self-test", controller_path(device), "-s", selector],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "nvme device-self-test failed").strip()
        raise RuntimeError(message)


def is_self_test_in_progress_error(message: str) -> bool:
    """Recognise wording variants emitted by different nvme-cli versions."""
    normalized = message.casefold().replace("-", " ").replace("_", " ")
    return "in progress" in normalized and (
        "self test" in normalized
        or ("device" in normalized and "test" in normalized)
    )


def parse_self_test_status(raw_json: str) -> NvmeSelfTestStatus:
    """Parse current progress and the newest entry from ``self-test-log`` JSON."""
    data = json.loads(raw_json)
    values = {_normalize_key(key): value for key, value in data.items()}
    operation = _as_int(_find_value(values, "currentselftestoperation")) or 0
    completion = _as_int(_find_value(values, "currentselftestcompletion"))
    if completion is not None:
        completion = max(0, min(100, completion))
    # 0 means no operation.  1 and 2 are short/extended; vendor-specific
    # operations are also treated as active so the UI never offers a second
    # test while the controller is busy.
    return NvmeSelfTestStatus(
        active=operation != 0,
        completion_percent=completion,
        result_code=_latest_self_test_result(data),
        raw_log=json.dumps(data, ensure_ascii=False, indent=2),
    )


def read_nvme_self_test_status(
    device: str,
    command_prefix: Sequence[str] = (),
    run: RunCommand = subprocess.run,
) -> NvmeSelfTestStatus:
    """Read current self-test progress without starting a new test."""
    ctrl = controller_path(device)
    completed = run(
        [*command_prefix, "nvme", "self-test-log", ctrl, "-o", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "nvme self-test log failed").strip()
        raise RuntimeError(message)
    return parse_self_test_status(completed.stdout)


def _as_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None


def _normalize_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _find_value(values: dict[str, object], needle: str) -> object:
    """Find a JSON value across nvme-cli's legacy and current key names."""
    for key, value in values.items():
        normalized = key.replace("device", "")
        if normalized.endswith(needle) or needle in normalized:
            return value
    return None


def _latest_self_test_result(data: object) -> Optional[int]:
    """Find the newest self-test result across nvme-cli JSON format versions."""
    if isinstance(data, dict):
        normalized = {_normalize_key(key): value for key, value in data.items()}
        result = _find_value(normalized, "selftestresult")
        if result is not None and not isinstance(result, (list, dict)):
            return _as_int(result)
        # Some nvme-cli versions call an entry's result ``operation_result``.
        # Accept that only inside a dictionary that is clearly self-test data.
        if any("selftest" in key for key in normalized):
            result = _find_value(normalized, "operationresult")
            if result is not None and not isinstance(result, (list, dict)):
                return _as_int(result)
        for value in data.values():
            found = _latest_self_test_result(value)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _latest_self_test_result(value)
            if found is not None:
                return found
    return None
