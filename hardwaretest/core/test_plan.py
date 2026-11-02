"""Sequential, non-destructive hardware test plan and report generation."""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import functools
import math
from datetime import datetime
from html import escape
import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time
import uuid
from typing import Callable, Optional

from hardwaretest import __version__
from hardwaretest.core import device_inventory, hardware_inventory, ipmi, proliant_check, zfs
from hardwaretest.core.monitoring import MonitorSession, MonitoringGuard, collect_metrics
from hardwaretest.core.monitor_history import validated_sensor_limits
from hardwaretest.core.nvidia import inspect_linux_nvidia, list_nvidia_gpus
from hardwaretest.core.block_disks import (
    BlockDisk, list_block_disks, read_disk_smart, smart_lines, smart_regressions, smartctl_available,
)
from hardwaretest.core.nvme import NvmeDevice, list_nvme_devices, read_nvme_smart
from hardwaretest.core.root_broker import RootBroker
from hardwaretest.core.system_info import read_system_info
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters
from hardwaretest.tests.network import NetworkRunner, invalid_measurements, threshold_failures
from hardwaretest.tests.fio_runner import FioDiskFullReadRunner, FioNvmeFullReadRunner
from hardwaretest.tests.opendcdiag import (
    OpenDcDiagRunner,
    default_opendcdiag_path,
    opendcdiag_available,
    runs_as_root,
)
from hardwaretest.tests.nvidia_runner import DcgmDiagnosticRunner, dcgmi_available
from hardwaretest.tests.stress_ng import StressNgRunner
from hardwaretest.tests.vulkan_runner import VulkanStressRunner, list_vulkan_gpus, vulkan_loader_available


STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_CANCELLED = "cancelled"


@dataclass(frozen=True)
class TestPlanConfig:
    output_dir: Path
    language: str = "de"
    stop_on_error: bool = False
    cpu_enabled: bool = True
    cpu_seconds: int = 60
    cpu_mode: str = "cpu"
    opendcdiag_enabled: bool = False
    opendcdiag_seconds: int = 300
    ram_enabled: bool = True
    ram_seconds: int = 60
    ram_percent: int = 70
    nvme_enabled: bool = True
    nvme_read_enabled: bool = False
    nvme_devices: tuple[NvmeDevice, ...] = ()
    disk_read_enabled: bool = False
    disk_devices: tuple[BlockDisk, ...] = ()
    zfs_enabled: bool = False
    rounds: int = 1
    # Optionale Endzeit: Der Plan wiederholt sich (bis ``rounds``), startet aber keinen
    # weiteren Teiltest mehr, sobald diese Zeit erreicht ist.
    end_time: Optional[datetime] = None
    ping_enabled: bool = False
    network_target: str = ""
    ping_count: int = 20
    iperf_enabled: bool = False
    iperf_seconds: int = 30
    max_packet_loss_percent: float = 0.0
    max_latency_avg_ms: float = 100.0
    max_jitter_ms: float = 20.0
    min_throughput_mbit_s: float = 0.0
    nvidia_enabled: bool = True
    dcgm_enabled: bool = False
    dcgm_level: int = 1
    vulkan_enabled: bool = False
    vulkan_seconds: int = 300
    vulkan_memory_percent: int = 80
    monitoring_enabled: bool = False
    monitoring_interval: int = 10
    temperature_limit: int = 90
    sensor_temperature_limits: tuple[tuple[str, float], ...] = ()


@dataclass
class TestPlanStepResult:
    key: str
    name: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass
class TestPlanResult:
    hostname: str
    started_at: str
    finished_at: str
    status: str
    steps: list[TestPlanStepResult]
    text_report: Optional[Path] = None
    html_report: Optional[Path] = None
    csv_report: Optional[Path] = None
    completed_rounds: int = 1
    requested_rounds: int = 1
    end_time: Optional[datetime] = None
    end_time_reached: bool = False
    system_info: list[str] = field(default_factory=list)


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]


class _StepAggregate:
    """Keep only the latest and first failed result, regardless of round count."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.latest: Optional[TestPlanStepResult] = None
        self.first_failure: Optional[TestPlanStepResult] = None
        self.started = ""
        self.seconds = 0.0

    def add(self, result: TestPlanStepResult) -> None:
        if not self.started:
            self.started = result.started_at
        self.latest = result
        self.seconds += result.duration_seconds
        self.counts[result.status] = self.counts.get(result.status, 0) + 1
        if result.status == STATUS_FAILED and self.first_failure is None:
            self.first_failure = result

    def result(self, language: str, repeated: bool) -> TestPlanStepResult:
        assert self.latest is not None
        latest = self.latest
        if not repeated:
            return latest
        status = next(key for key in (STATUS_FAILED, STATUS_CANCELLED, STATUS_PASSED, STATUS_SKIPPED)
                      if self.counts.get(key))
        counts = " | ".join(f"{_status_label(key, language)}: {self.counts.get(key, 0)}"
                            for key in (STATUS_PASSED, STATUS_FAILED, STATUS_SKIPPED, STATUS_CANCELLED))
        details = [counts, _text("Letztes Ergebnis", language) + ": " + latest.summary,
                   *latest.details]
        if self.first_failure is not None and self.first_failure is not latest:
            first = self.first_failure
            details.extend([_text("Erster Fehler", language) + f": {first.started_at} | {first.summary}",
                            *first.details])
        return TestPlanStepResult(latest.key, latest.name, status, self.started, latest.finished_at,
                                  self.seconds, counts, _bounded_details(details, language=language))


class TestPlanExecutor:
    """Execute selected tests one after another in a worker thread."""

    def __init__(
        self,
        config: TestPlanConfig,
        log_fn: Optional[LogCallback] = None,
        progress_fn: Optional[ProgressCallback] = None,
        step_progress_fn: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.config = config
        self._log = log_fn or (lambda _message: None)
        self._progress = progress_fn or (lambda _done, _total, _name: None)
        self._step_progress = step_progress_fn or (lambda _percent: None)
        self._zfs_poll_seconds = 5.0
        self._cancel = threading.Event()
        self._active_runner: Optional[BaseTestRunner] = None
        self._monitor: Optional[MonitoringGuard] = None
        self._root: Optional[RootBroker] = None

    def cancel(self) -> None:
        self._cancel.set()

    def selected_step_keys(self) -> list[str]:
        config = self.config
        steps: list[str] = []
        if config.cpu_enabled:
            steps.append("cpu")
        if config.opendcdiag_enabled:
            steps.append("opendcdiag")
        if config.ram_enabled:
            steps.append("ram")
        if config.nvme_enabled:
            steps.append("nvme")
        if config.nvme_read_enabled:
            steps.append("nvme_read")
        if config.disk_read_enabled:
            steps.append("disk_read")
        if config.zfs_enabled:
            steps.append("zfs_scrub")
        if config.ping_enabled:
            steps.append("ping")
        if config.iperf_enabled:
            steps.append("iperf3")
        if config.nvidia_enabled:
            steps.append("nvidia")
        if config.dcgm_enabled:
            steps.append("dcgm")
        if config.vulkan_enabled:
            steps.append("vulkan")
        return steps

    def execute(self) -> TestPlanResult:
        try:
            return self._execute()
        finally:
            if self._active_runner is not None:
                if self._active_runner.is_running():
                    self._active_runner.stop(aborted=True)
                self._active_runner = None
            # Also close the CSV if an unexpected reporting/progress error occurs.
            if self._monitor is not None:
                self._monitor.finish()
                self._monitor = None
            if self._root is not None:
                self._root.close()
                self._root = None

    def _execute(self) -> TestPlanResult:
        started = _now()
        results: list[TestPlanStepResult] = []
        step_keys = self.selected_step_keys()
        rounds = int(self.config.rounds)
        if not 1 <= rounds <= 10_000:
            raise ValueError("Durchläufe / Rounds: 1–10000")
        end_time = self.config.end_time
        if end_time is not None and end_time <= datetime.now():
            raise ValueError("Endzeit liegt in der Vergangenheit / End time is in the past")
        end_time_reached = False
        validated_sensor_limits(dict(self.config.sensor_temperature_limits))
        for value, maximum in ((self.config.max_packet_loss_percent, 100),
                               (self.config.max_latency_avg_ms, 60_000),
                               (self.config.max_jitter_ms, 60_000),
                               (self.config.min_throughput_mbit_s, 1_000_000)):
            if not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError("Ungültige Netzwerk-Grenzwerte / Invalid network thresholds")
        completed_rounds = 0
        aggregates: dict[str, _StepAggregate] = {}
        directory = self.config.output_dir.expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        csv_path = directory / f"testplan-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.csv"
        handlers = {
            "cpu": self._run_cpu,
            "opendcdiag": self._run_opendcdiag,
            "ram": self._run_ram,
            "nvme": self._run_nvme,
            "nvme_read": self._run_nvme_read,
            "disk_read": self._run_disk_read,
            "zfs_scrub": self._run_zfs_scrub,
            "ping": self._run_ping,
            "iperf3": self._run_iperf,
            "nvidia": self._run_nvidia,
            "dcgm": self._run_dcgm,
            "vulkan": self._run_vulkan,
        }

        # Passwort sofort abfragen, damit lange Testpläne unbeaufsichtigt laufen.
        self._authorize_root()
        system_info = self._collect_system_info()
        # ProLiant/RAID: Momentaufnahme vor den Tests (IML, Sensoren, RAID-Controller).
        proliant_before = self._proliant_snapshot()

        if self.config.monitoring_enabled:
            extra = {}
            if self._root is not None and ipmi.ipmi_available():
                ipmi.ensure_ipmi_driver(self._root.run)
                extra["collect"] = functools.partial(collect_metrics, root_run=self._root.run)
            session = MonitorSession(self.config.output_dir, self.config.temperature_limit, self.config.language,
                                     **extra)
            session.set_sensor_limits(dict(self.config.sensor_temperature_limits))
            self._monitor = MonitoringGuard(session, self.config.monitoring_interval)
            self._monitor.start()

        # One CSV row per test, not per packet/block. Only bounded aggregates stay
        # in memory; repeated plans therefore keep their TXT/HTML reports compact.
        with csv_path.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["round", "step", "status", "start", "end", "seconds", "summary"])
            for round_index in range(rounds):
                if self._cancel.is_set() or self._safety_triggered():
                    break
                round_complete = True
                stop = False
                for index, key in enumerate(step_keys):
                    if self._cancel.is_set() or self._safety_triggered():
                        round_complete = False
                        break
                    if end_time is not None and datetime.now() >= end_time:
                        end_time_reached = True
                        round_complete = index == 0 and round_index > 0
                        self._log(_text("Endzeit erreicht – keine weiteren Teiltests.", self.config.language))
                        break
                    name = _step_name(key, self.config.language)
                    label = (f"Durchlauf {round_index + 1}/{rounds}: {name}" if self.config.language == "de"
                             else f"Round {round_index + 1}/{rounds}: {name}")
                    self._progress(round_index * len(step_keys) + index, rounds * len(step_keys), label)
                    self._step_progress(0)
                    self._log(f"=== {label} ===")
                    try:
                        result = handlers[key]()
                    except Exception as exc:  # defensive boundary for unattended plans
                        if self._active_runner is not None:
                            if self._active_runner.is_running():
                                self._active_runner.stop(aborted=True)
                            self._active_runner = None
                        result = self._instant_result(key, STATUS_FAILED, str(exc), [repr(exc)])
                    writer.writerow([round_index + 1, key, result.status, result.started_at,
                                     result.finished_at, f"{result.duration_seconds:.3f}", result.summary])
                    stream.flush()
                    aggregate = aggregates.setdefault(key, _StepAggregate())
                    aggregate.add(result)
                    self._log(f"{name}: {_status_label(result.status, self.config.language)}")
                    if result.status == STATUS_CANCELLED or self._safety_triggered() or (
                            result.status == STATUS_FAILED and self.config.stop_on_error):
                        round_complete = index == len(step_keys) - 1 and result.status != STATUS_CANCELLED
                        stop = True
                        break
                if round_complete and not end_time_reached:
                    completed_rounds += 1
                if stop or not round_complete or not step_keys or end_time_reached:
                    break
        results = [item.result(self.config.language, rounds > 1) for item in aggregates.values()]

        if self._monitor is not None:
            monitor = self._monitor
            self._monitor = None
            try:
                paths = monitor.finish()
                session = monitor.session
                status = STATUS_FAILED if monitor.tripped.is_set() or session.alert_count else STATUS_PASSED
                summary = (f"{session.samples} Messungen, {session.alert_count} Ereignisse; nur verfügbare Sensoren."
                           if self.config.language == "de" else
                           f"{session.samples} samples, {session.alert_count} events; available sensors only.")
                if monitor.tripped.is_set():
                    summary = self._safety_message() + " " + summary
                results.append(self._instant_result("monitoring", status, summary,
                    [session.summary(), *map(str, paths), monitor.error]))
            except Exception as exc:
                results.append(self._instant_result("monitoring", STATUS_FAILED, str(exc)))

        if proliant_before is not None:
            results.append(self._proliant_step(proliant_before))

        cancelled = self._cancel.is_set()
        if cancelled and (not results or results[-1].status != STATUS_CANCELLED):
            results.append(self._instant_result(
                "cancelled", STATUS_CANCELLED,
                _text("Testplan wurde manuell abgebrochen.", self.config.language),
            ))
        status = _overall_status(results, cancelled)
        plan_result = TestPlanResult(
            hostname=platform.node() or "unknown",
            started_at=started,
            finished_at=_now(),
            status=status,
            steps=results,
            csv_report=csv_path,
            completed_rounds=completed_rounds,
            requested_rounds=rounds,
            end_time=end_time,
            end_time_reached=end_time_reached,
            system_info=system_info,
        )
        text_path, html_path = write_test_plan_reports(
            plan_result, self.config.output_dir, self.config.language
        )
        plan_result.text_report = text_path
        plan_result.html_report = html_path
        self._progress(completed_rounds * len(step_keys), rounds * len(step_keys), "")
        return plan_result

    def _validated_disks(self, detected: list[BlockDisk]) -> list[BlockDisk]:
        by_path = {disk.path: disk for disk in detected}
        selected = []
        for expected in self.config.disk_devices:
            actual = by_path.get(expected.path)
            if (actual is None or actual.serial != expected.serial or actual.model != expected.model
                    or actual.size_bytes != expected.size_bytes):
                message = ("Datenträger fehlt oder Identität hat sich geändert: " if self.config.language == "de"
                           else "Disk missing or identity changed: ")
                raise RuntimeError(message + expected.path)
            selected.append(actual)
        return selected

    def _run_disk_read(self) -> TestPlanStepResult:
        if not shutil.which("fio"):
            return self._skipped("disk_read", "fio ist nicht installiert.")
        if not self.config.disk_devices:
            return self._instant_result("disk_read", STATUS_FAILED,
                                        _text("Kein Datenträger für den Lesetest ausgewählt.", self.config.language))
        disks = self._validated_disks(list_block_disks())
        details = [f"{d.path} | {d.kind} | {d.model} | SN {d.serial} | {d.size_bytes} B" for d in disks]
        before = {}
        smart_missing: list[str] = []
        for disk in disks:
            if self._cancel.is_set() or self._safety_triggered():
                return self._instant_result("disk_read", STATUS_CANCELLED if self._cancel.is_set()
                                            else STATUS_FAILED, "Lesetest abgebrochen.", details)
            smart = read_disk_smart(disk.path, self._nvme_run) if smartctl_available() else None
            if smart is None or not smart.available:
                # Hinter RAID-Controllern liefern logische Laufwerke kein SMART; der
                # Lesetest läuft trotzdem, der Vergleich entfällt.
                smart_missing.append(disk.path + (f" ({smart.error})" if smart and smart.error else ""))
            else:
                before[disk.path] = smart
        if smart_missing:
            details.append(_text("SMART nicht verfügbar (kein Vorher/Nachher-Vergleich): ", self.config.language)
                           + ", ".join(smart_missing))
        self._validated_disks(list_block_disks())
        total = sum(d.size_bytes for d in disks)
        runner = FioDiskFullReadRunner(
            TestParameters(0), [d.path for d in disks], total_bytes=total,
            use_pkexec=False, log_fn=lambda _line: None,
        )
        self._attach_root(runner)
        result = self._run_process_step("disk_read", runner,
            summary_extra=f"{len(disks)} {_text('Datenträger', self.config.language)}; {total / 1024**3:.1f} GiB")
        details.extend(result.details)
        if result.status != STATUS_CANCELLED and not self._safety_triggered():
            for path, initial in before.items():
                try:
                    final = read_disk_smart(path, self._nvme_run)
                    if not final.available:
                        raise RuntimeError(final.error or "SMART")
                    details.extend(smart_lines(path, initial, final))
                    problems = smart_regressions(initial, final)
                    if problems:
                        result.status = STATUS_FAILED
                        details.append(_text("SMART-Warnung nach Lesetest.", self.config.language)
                                       + " " + "; ".join(problems))
                except Exception as exc:
                    result.status = STATUS_FAILED
                    details.append(f"SMART {path}: {exc}")
        result.summary = _status_label(result.status, self.config.language) + " | " + (
            "Vollständiger Lesetest, keine Schreibzugriffe" if self.config.language == "de"
            else "Full read test, no writes") + f" | {len(disks)} {_text('Datenträger', self.config.language)}"
        result.details = _bounded_details(details, language=self.config.language)
        return result

    def _run_zfs_scrub(self) -> TestPlanStepResult:
        """Scrub aller importierten Pools; prüft Prüfsummen aller Datenblöcke."""
        if not shutil.which("zpool"):
            return self._skipped("zfs_scrub", "zfsutils-linux ist nicht installiert.")
        started, started_at = time.monotonic(), _now()
        lang = self.config.language
        try:
            pools = zfs.list_pools()
        except (OSError, RuntimeError, ValueError) as exc:
            return self._timed_result("zfs_scrub", STATUS_FAILED, started, started_at,
                                      _text("ZFS-Pools nicht lesbar.", lang), [str(exc)])
        if not pools:
            return self._skipped("zfs_scrub", "Keine importierten ZFS-Pools.")
        details: list[str] = []
        active: list[zfs.ZfsPool] = []
        failed = False
        for pool in pools:
            try:
                status = zfs.read_status(pool)
                readonly = zfs.run_zpool(["get", "-H", "-o", "value", "readonly", pool.name]).strip()
            except (OSError, RuntimeError, ValueError) as exc:
                details.append(f"{pool.name}: {exc}")
                failed = True
                continue
            if status.state not in {"ONLINE", "DEGRADED"}:
                details.append(f"{pool.name}: {_text('Pool nicht verfügbar', lang)} ({status.state})")
                failed = True
            elif status.scan_result == "resilver":
                details.append(f"{pool.name}: {_text('Resilver läuft – Scrub übersprungen.', lang)}")
            elif readonly != "off":
                details.append(f"{pool.name}: {_text('schreibgeschützt – Scrub übersprungen.', lang)}")
            else:
                if status.scan_result == "paused":
                    self._nvme_run(["zpool", "scrub", "-s", pool.name], capture_output=True, text=True)
                completed = self._nvme_run(["zpool", "scrub", pool.name], capture_output=True, text=True)
                if completed.returncode:
                    details.append(f"{pool.name}: zpool scrub: {(completed.stderr or completed.stdout).strip()}")
                    failed = True
                else:
                    active.append(pool)
                    details.append(f"{pool.name}: {_text('Scrub gestartet', lang)} ({pool.health}, "
                                   f"{pool.size / 1024**3:.1f} GiB, {_text('belegt', lang)} {pool.allocated / 1024**3:.1f} GiB)")
        pending = list(active)
        cancelled = False
        while pending:
            if self._cancel.is_set() or self._safety_triggered():
                cancelled = True
                for pool in pending:
                    self._nvme_run(["zpool", "scrub", "-s", pool.name], capture_output=True, text=True)
                break
            progress: list[float] = []
            for pool in list(pending):
                try:
                    status = zfs.read_status(pool)
                except (OSError, RuntimeError, ValueError) as exc:
                    details.append(f"{pool.name}: {exc}")
                    failed = True
                    pending.remove(pool)
                    continue
                if status.scan_result == "running":
                    progress.append(status.progress or 0.0)
                    continue
                pending.remove(pool)
                progress.append(100.0)
                details.append(f"{pool.name}: {status.scan}")
                if status.has_errors or status.scan_result != "passed":
                    failed = True
                    details.append(f"{pool.name}: {_text('ZFS meldet Fehler', lang)} ({status.scan_result}, {status.state})")
                    details.extend("    " + line for line in status.raw.splitlines() if line.strip())
            if active:
                self._step_progress(min(99, int(sum(progress) / len(active))))
            if pending:
                self._cancel.wait(self._zfs_poll_seconds)
        if cancelled:
            return self._timed_result("zfs_scrub", STATUS_CANCELLED if self._cancel.is_set() else STATUS_FAILED,
                                      started, started_at, _text("Scrub abgebrochen.", lang), details)
        summary = f"{len(active)}/{len(pools)} {_text('Pools geprüft', lang)}"
        if failed:
            status_text = STATUS_FAILED
        elif not active:
            status_text = STATUS_SKIPPED
        else:
            status_text = STATUS_PASSED
        return self._timed_result("zfs_scrub", status_text, started, started_at, summary, details)

    def _safety_triggered(self) -> bool:
        return self._monitor is not None and self._monitor.tripped.is_set()

    def _safety_message(self) -> str:
        return ("Sicherheitsabbruch: Temperaturgrenze, neuer unkorrigierbarer ECC-Fehler oder Monitoring-Ausfall."
                if self.config.language == "de" else
                "Safety stop: temperature limit, new uncorrectable ECC error or monitoring failure.")

    def _run_cpu(self) -> TestPlanStepResult:
        if not shutil.which("stress-ng"):
            return self._skipped("cpu", "stress-ng ist nicht installiert.")
        info = read_system_info()
        cores = max(1, info.logical_cpu_cores - 1)
        runner = StressNgRunner(
            TestParameters(self.config.cpu_seconds, cpu_cores=cores, nice_level=0),
            mode=self.config.cpu_mode if self.config.cpu_mode in ("cpu", "mix") else "cpu",
            log_fn=self._log,
        )
        return self._run_process_step("cpu", runner)

    def _run_opendcdiag(self) -> TestPlanStepResult:
        if not opendcdiag_available():
            return self._skipped("opendcdiag", "OpenDCDiag ist nicht installiert.")
        runner = OpenDcDiagRunner(TestParameters(self.config.opendcdiag_seconds), log_fn=self._log)
        self._attach_root(runner)
        result = self._run_process_step("opendcdiag", runner)
        summary = runner.summary_text(self.config.language)
        if summary:
            result.summary += " | " + summary
        return result

    def _run_ram(self) -> TestPlanStepResult:
        if not shutil.which("stress-ng"):
            return self._skipped("ram", "stress-ng ist nicht installiert.")
        info = read_system_info()
        workers = max(1, min(info.logical_cpu_cores, 8))
        memory = max(
            64 * 1024 * 1024,
            int(info.available_memory_bytes * max(1, min(self.config.ram_percent, 90)) / 100),
        )
        runner = StressNgRunner(
            TestParameters(
                self.config.ram_seconds,
                cpu_cores=workers,
                memory_bytes=memory,
                nice_level=0,
            ),
            mode="ram",
            vm_workers=workers,
            log_fn=self._log,
        )
        return self._run_process_step(
            "ram", runner, summary_extra=f"RAM: {memory // (1024 * 1024)} MiB"
        )

    def _run_nvme(self) -> TestPlanStepResult:
        started = time.monotonic()
        started_at = _now()
        if not shutil.which("nvme"):
            return self._skipped("nvme", "nvme-cli ist nicht installiert.")
        devices = list_nvme_devices()
        if self.config.nvme_devices:
            devices = self._validated_nvme_devices(devices)
        if not devices:
            return self._skipped("nvme", "Keine NVMe-Laufwerke gefunden.")
        details: list[str] = []
        failed = 0
        for device in devices:
            if self._cancel.is_set():
                return self._timed_result(
                    "nvme", STATUS_CANCELLED, started, started_at,
                    "NVMe-Prüfung abgebrochen.", details,
                )
            details.append(
                f"{device.namespace_path} | {device.model} | SN {device.serial} | "
                f"FW {device.firmware}"
            )
            try:
                health = read_nvme_smart(device.controller_path, (), self._nvme_run)
                if health.healthy is True:
                    status_text = "OK"
                elif health.healthy is False:
                    status_text = _text("WARNUNG", self.config.language)
                else:
                    status_text = _text("UNBEKANNT", self.config.language)
                details.append(
                    f"  Status: {status_text} | "
                    f"temperature: {_value(health.temperature_c)} °C | "
                    f"percentage_used: {_value(health.percentage_used)} % | "
                    f"media_errors: {_value(health.media_errors)} | "
                    f"critical_warning: {_value(health.critical_warning)}"
                )
                if health.healthy is None:
                    failed += 1
                    details.append(
                        "  " + _text("Gesundheitsstatus nicht bestimmbar, fehlende SMART-Felder:",
                                     self.config.language)
                        + " " + ", ".join(health.missing_fields)
                    )
                elif not health.healthy:
                    failed += 1
            except Exception as exc:
                failed += 1
                details.append(f"  FEHLER: {exc}")
        status = STATUS_FAILED if failed else STATUS_PASSED
        summary = (f"{len(devices)} NVMe-Laufwerk(e), {failed} mit Fehler/Warnung" if self.config.language == "de"
                   else f"{len(devices)} NVMe drive(s), {failed} with errors/warnings")
        return self._timed_result("nvme", status, started, started_at, summary, details)

    def _validated_nvme_devices(self, detected: list[NvmeDevice]) -> list[NvmeDevice]:
        by_path = {device.namespace_path: device for device in detected}
        selected = []
        for expected in self.config.nvme_devices:
            actual = by_path.get(expected.namespace_path)
            if (actual is None or not expected.serial or actual.serial != expected.serial
                    or actual.model != expected.model or actual.size_bytes != expected.size_bytes):
                message = ("NVMe fehlt oder Identität hat sich geändert: " if self.config.language == "de"
                           else "NVMe missing or identity changed: ")
                raise RuntimeError(message + expected.namespace_path)
            selected.append(actual)
        return selected

    def _run_nvme_read(self) -> TestPlanStepResult:
        if not shutil.which("fio") or not shutil.which("nvme"):
            return self._skipped("nvme_read", "fio oder nvme-cli ist nicht installiert.")
        if not self.config.nvme_devices:
            return self._instant_result("nvme_read", STATUS_FAILED,
                                        "Kein NVMe-Laufwerk für den Lesetest ausgewählt.")
        devices = self._validated_nvme_devices(list_nvme_devices())
        details = [f"{d.namespace_path} | {d.model} | SN {d.serial} | FW {d.firmware} | {d.size_bytes} B"
                   for d in devices]
        before = {}
        for device in devices:
            if self._cancel.is_set() or self._safety_triggered():
                return self._instant_result("nvme_read", STATUS_CANCELLED if self._cancel.is_set()
                                            else STATUS_FAILED, "NVMe-Prüfung abgebrochen.", details)
            if device.controller_path not in before:
                before[device.controller_path] = read_nvme_smart(device.controller_path, (), self._nvme_run)
                if not before[device.controller_path].health_known:
                    raise RuntimeError(_text("SMART-Medienfehlerzähler nicht verfügbar.", self.config.language))
        # Refresh the identity again after authentication, before starting fio.
        self._validated_nvme_devices(list_nvme_devices())
        runner = FioNvmeFullReadRunner(
            TestParameters(0), [d.namespace_path for d in devices], total_bytes=sum(d.size_bytes for d in devices),
            use_pkexec=False, log_fn=lambda _line: None,
        )
        self._attach_root(runner)
        result = self._run_process_step("nvme_read", runner,
            summary_extra=f"{len(devices)} NVMe; {sum(d.size_bytes for d in devices) / 1024**3:.1f} GiB")
        details.extend(result.details)
        if result.status != STATUS_CANCELLED and not self._safety_triggered():
            for controller, initial in before.items():
                try:
                    final = read_nvme_smart(controller, (), self._nvme_run)
                    for field_name in ("temperature_c", "media_errors", "error_log_entries",
                                       "critical_warning", "percentage_used", "data_units_read"):
                        old, new = getattr(initial, field_name), getattr(final, field_name)
                        details.append(f"{controller}: {field_name}: {_value(old)} -> {_value(new)}")
                    if (not final.health_known or not final.healthy
                            or (initial.media_errors is not None and final.media_errors is not None
                                and final.media_errors > initial.media_errors)):
                        result.status = STATUS_FAILED
                        details.append(_text("SMART-Warnung nach Lesetest.", self.config.language))
                except Exception as exc:
                    result.status = STATUS_FAILED
                    details.append(f"SMART: {exc}")
        result.summary = _status_label(result.status, self.config.language) + " | " + (
            "Vollständiger Lesetest, keine Schreibzugriffe" if self.config.language == "de"
            else "Full read test, no writes") + f" | {len(devices)} NVMe"
        result.details = _bounded_details(details, language=self.config.language)
        return result

    def _run_ping(self) -> TestPlanStepResult:
        if not self.config.network_target.strip():
            return self._skipped("ping", "Kein Netzwerkziel angegeben.")
        if not shutil.which("ping"):
            return self._skipped("ping", "ping ist nicht installiert.")
        runner = NetworkRunner(
            TestParameters(max(10, self.config.ping_count * 3)),
            self.config.network_target,
            "ping",
            ping_count=self.config.ping_count,
            log_fn=self._log,
        )
        return self._run_process_step("ping", runner, structured_summary=True)

    def _run_iperf(self) -> TestPlanStepResult:
        if not self.config.network_target.strip():
            return self._skipped("iperf3", "Kein Netzwerkziel angegeben.")
        if not shutil.which("iperf3"):
            return self._skipped("iperf3", "iperf3 ist nicht installiert.")
        runner = NetworkRunner(
            TestParameters(self.config.iperf_seconds + 5),
            self.config.network_target,
            "iperf3",
            iperf_seconds=self.config.iperf_seconds,
            log_fn=self._log,
        )
        return self._run_process_step("iperf3", runner, structured_summary=True)

    def _run_nvidia(self) -> TestPlanStepResult:
        started = time.monotonic()
        started_at = _now()
        status = inspect_linux_nvidia()
        details = [
            f"PCIe-Geräte: {', '.join(status.pci_devices) or '–'}",
            f"Treiber: {', '.join(status.bound_drivers) or '–'}",
            f"Kernelmodule: {', '.join(status.kernel_modules) or '–'}",
            f"nvidia-smi: {'OK' if status.nvidia_smi_works else 'FEHLER'}",
        ]
        if not status.hardware_present:
            return self._timed_result(
                "nvidia", STATUS_SKIPPED, started, started_at,
                "Keine NVIDIA-GPU erkannt.", details,
            )
        if not status.driver_ready:
            if status.nvidia_smi_error:
                details.append(status.nvidia_smi_error)
            return self._timed_result(
                "nvidia", STATUS_FAILED, started, started_at,
                "NVIDIA-Treiberinstallation ist nicht betriebsbereit.", details,
            )
        try:
            gpus = list_nvidia_gpus()
        except Exception as exc:
            details.append(f"nvidia-smi: {exc}")
            return self._timed_result(
                "nvidia", STATUS_FAILED, started, started_at,
                _text("GPU-Inventar konnte nicht gelesen werden.", self.config.language), details,
            )
        for gpu in gpus:
            details.append(
                f"GPU {gpu.index}: {gpu.model} | {gpu.uuid} | "
                f"VRAM {_value(gpu.memory_used_mib)}/{_value(gpu.memory_total_mib)} MiB | "
                f"{_value(gpu.temperature_c)} °C | {_value(gpu.power_draw_w)} W | "
                f"PCIe Gen {_value(gpu.pcie_generation_current)} "
                f"x{_value(gpu.pcie_width_current)} | ECC unkorrigiert "
                f"{_value(gpu.ecc_uncorrected)}"
            )
        if not gpus:
            # NVIDIA-PCI-Hardware ist vorhanden, nvidia-smi liefert aber keine parsebare GPU.
            details.append(_text("nvidia-smi lieferte keine auswertbare GPU-Liste (leer oder inkompatibles Format).",
                                 self.config.language))
            return self._timed_result(
                "nvidia", STATUS_FAILED, started, started_at,
                _text("NVIDIA-Hardware erkannt, aber keine GPU über nvidia-smi auswertbar.",
                      self.config.language), details,
            )
        return self._timed_result(
            "nvidia", STATUS_PASSED, started, started_at,
            f"{len(gpus)} NVIDIA-GPU(s), Treiber und nvidia-smi betriebsbereit.", details,
        )

    def _run_dcgm(self) -> TestPlanStepResult:
        if not dcgmi_available():
            return self._skipped("dcgm", "NVIDIA DCGM ist nicht installiert.")
        try:
            gpus = list_nvidia_gpus()
        except Exception as exc:
            return self._instant_result("dcgm", STATUS_FAILED, str(exc))
        if not gpus:
            return self._skipped("dcgm", "Keine NVIDIA-GPU für DCGM gefunden.")
        runner = DcgmDiagnosticRunner(
            TestParameters(0),
            [gpu.index for gpu in gpus],
            self.config.dcgm_level,
            log_fn=self._log,
            use_pkexec=False,
            language=self.config.language,
        )
        self._attach_root(runner)
        result = self._run_process_step("dcgm", runner)
        counts = runner.status_counts()
        result.summary += (
            f" | {counts['pass']} bestanden, {counts['fail']} fehlgeschlagen, "
            f"{counts['warn']} Warnungen, {counts['skip']} übersprungen"
        )
        if counts["fail"]:
            result.status = STATUS_FAILED
        evaluation_error = runner.evaluation_error()
        if evaluation_error and result.status == STATUS_PASSED:
            result.status = STATUS_FAILED
            message = _text("DCGM-Ergebnis nicht auswertbar:", self.config.language) + " " + evaluation_error
            result.summary += " | " + message
            result.details = [*result.details, message]
        return result

    def _run_vulkan(self) -> TestPlanStepResult:
        if not vulkan_loader_available():
            return self._skipped("vulkan", "Vulkan-Loader (libvulkan1) ist nicht installiert.")
        try:
            gpus = list_vulkan_gpus()
        except Exception as exc:
            return self._skipped("vulkan", str(exc))
        if not gpus:
            return self._skipped("vulkan", "Keine Vulkan-GPU gefunden.")
        runner = VulkanStressRunner(
            TestParameters(self.config.vulkan_seconds), [gpu["index"] for gpu in gpus],
            memory_percent=self.config.vulkan_memory_percent, log_fn=self._log,
        )
        names = ", ".join(f"GPU {gpu['index']}: {gpu.get('name', '?')}" for gpu in gpus)
        return self._run_process_step("vulkan", runner, summary_extra=names)

    def _run_process_step(
        self,
        key: str,
        runner: BaseTestRunner,
        summary_extra: str = "",
        structured_summary: bool = False,
    ) -> TestPlanStepResult:
        started = time.monotonic()
        started_at = _now()
        details: list[str] = []
        try:
            runner.start()
        except Exception:
            cleanup = getattr(runner, "_cleanup_job_file", None)
            if cleanup is not None:
                cleanup()
            raise
        self._active_runner = runner
        process = getattr(runner, "_process", None)
        if process is not None and isinstance(process.args, list):
            details.append("$ " + " ".join(map(str, process.args)))
        while runner.is_running():
            if self._safety_triggered():
                runner.stop(aborted=True)
                self._active_runner = None
                return self._timed_result(key, STATUS_FAILED, started, started_at,
                                          self._safety_message(), details)
            if self._cancel.wait(0.25):
                runner.stop(aborted=True)
                self._active_runner = None
                return self._timed_result(
                    key, STATUS_CANCELLED, started, started_at,
                    _text("Teiltest wurde abgebrochen.", self.config.language), details,
                )
            self._step_progress(min(99, max(0, int(runner.progress() * 100))))
        # stdout completion and result finalization happen on a reader thread.
        output_thread = getattr(runner, "_stdout_thread", None)
        if output_thread is not None:
            output_thread.join(timeout=5)
        self._active_runner = None
        result = runner.get_result()
        if result is None:
            return self._timed_result(
                key, STATUS_FAILED, started, started_at,
                "Kein auswertbares Testergebnis.", details,
            )
        status = STATUS_PASSED if result.passed else STATUS_FAILED
        network_assessment = ""
        if structured_summary and isinstance(runner, NetworkRunner) and result.passed:
            values = runner.summary() or {}
            invalid = invalid_measurements(values, runner.mode)
            if invalid:
                status = STATUS_FAILED
                network_assessment = (_text("Netzwerk-Messwerte fehlen oder sind ungültig.", self.config.language)
                                      + " (" + ", ".join(invalid) + ")")
                details.append(network_assessment)
            else:
                failures = threshold_failures(values,
                    max_packet_loss_percent=self.config.max_packet_loss_percent,
                    max_latency_avg_ms=self.config.max_latency_avg_ms,
                    max_jitter_ms=self.config.max_jitter_ms,
                    min_throughput_mbit_s=self.config.min_throughput_mbit_s,
                    mode=runner.mode)
                for code, actual, limit in failures:
                    label = "Grenzwert überschritten" if self.config.language == "de" else "Threshold exceeded"
                    details.append(f"{label}: {code}: {actual:g} / {limit:g}")
                if failures:
                    status = STATUS_FAILED
                    network_assessment = ("Grenzwert überschritten: " if self.config.language == "de"
                                          else "Threshold exceeded: ") + "; ".join(
                        f"{code}: {actual:g} / {limit:g}" for code, actual, limit in failures)
            details.append(f"Limits: loss <= {self.config.max_packet_loss_percent:g}% | "
                           f"latency <= {self.config.max_latency_avg_ms:g} ms | jitter <= {self.config.max_jitter_ms:g} ms | "
                           f"throughput >= {self.config.min_throughput_mbit_s:g} Mbit/s (0 = off)")
        if result.passed:
            self._step_progress(100)
        summary_parts = [
            _status_label(status, self.config.language),
            f"Exit-Code: {result.exit_code}",
        ]
        if summary_extra:
            summary_parts.append(summary_extra)
        if network_assessment:
            summary_parts.append(network_assessment)
        if structured_summary and isinstance(runner, NetworkRunner):
            values = {key: value for key, value in (runner.summary() or {}).items()
                      if isinstance(value, (float, int)) and math.isfinite(value)}
            if "throughput_mbit_s" in values:
                summary_parts.append(f"{values['throughput_mbit_s']:.1f} Mbit/s")
                summary_parts.append(f"Retransmits: {values.get('retransmits', 0):.0f}")
            if "latency_avg_ms" in values:
                summary_parts.append(f"Latenz Ø: {values['latency_avg_ms']:.2f} ms")
                summary_parts.append(
                    f"Paketverlust: {values.get('packet_loss_percent', 0):.1f} %"
                )
        details.extend(_text(line, self.config.language) for line in result.errors)
        warnings = getattr(result, "warnings", None) or []
        corrected = sum(1 for line in warnings if line.startswith("rasdaemon CE"))
        if corrected:
            summary_parts.append(_text("rasdaemon: korrigierte Hardwarefehler", self.config.language)
                                 + f": {corrected}")
        details.extend(_text("WARNUNG", self.config.language) + ": " + _text(line, self.config.language)
                       for line in warnings)
        output_lines = getattr(runner, "output_lines", None)
        if structured_summary and isinstance(runner, NetworkRunner):
            details.extend(runner.compact_report_lines(self.config.language))
            if status == STATUS_FAILED:
                failure = runner.failure()
                if failure and failure[1]:
                    label = "Error" if self.config.language == "en" else "Fehler"
                    details.append(f"{label}: {' '.join(failure[1].split())[:1000]}")
        elif isinstance(runner, DcgmDiagnosticRunner):
            details.extend(runner.report_lines(self.config.language))
        elif output_lines is not None:
            details.extend(str(line) for line in output_lines)
        return self._timed_result(
            key, status, started, started_at, " | ".join(summary_parts),
            _bounded_details(details, language=self.config.language),
        )

    def _needs_root(self) -> bool:
        if os.geteuid() == 0 or not shutil.which("pkexec"):
            return False
        keys = set(self.selected_step_keys())
        return (bool(keys & {"nvme", "nvme_read", "disk_read"}) or ("dcgm" in keys and dcgmi_available())
                or ("zfs_scrub" in keys and shutil.which("zpool") is not None)
                or ("opendcdiag" in keys and opendcdiag_available()
                    and not runs_as_root(default_opendcdiag_path()))
                or (self.config.monitoring_enabled and ipmi.ipmi_available())
                or proliant_check.relevant())

    def _authorize_root(self) -> None:
        """Fragt das Passwort einmal zu Beginn ab – danach läuft der Plan unbeaufsichtigt."""
        if self._root is not None or not self._needs_root():
            return
        self._log(_text("Root-Berechtigung für den gesamten Testplan wird angefordert …",
                        self.config.language))
        broker = RootBroker(shutil.which("pkexec") or "pkexec")
        broker.start()
        self._root = broker

    def _collect_system_info(self) -> list[str]:
        """CPU, Mainboard und Speichermodule für den Bericht; Fehler stoppen den Plan nie."""
        try:
            run = self._root.run if self._root is not None else None
            memory, error = hardware_inventory.read_memory_inventory(run)
            lines = hardware_inventory.inventory_lines(self.config.language, memory=memory, memory_error=error)
        except Exception as exc:  # Bericht soll trotzdem entstehen
            return [f"System: {exc}"]
        try:
            lines = lines + device_inventory.device_lines(self.config.language, device_inventory.read_device_inventory())
        except Exception as exc:
            lines = [*lines, f"System: {exc}"]
        if self._privileged_run() is None and proliant_check.relevant():
            lines.append(_text("ProLiant/RAID: keine Root-Sitzung – IML, Sensoren und RAID-Controller wurden nicht geprüft.",
                               self.config.language))
        return lines

    def _privileged_run(self):
        """Root-Runner des Plans: Root-Sitzung, direkter Aufruf als root oder None."""
        if self._root is not None:
            return self._root.run
        if os.geteuid() == 0:
            return subprocess.run
        return None

    def _proliant_snapshot(self) -> Optional[proliant_check.ProliantSnapshot]:
        run = self._privileged_run()
        if run is None or not proliant_check.relevant():
            return None
        try:
            return proliant_check.snapshot(run)
        except Exception as exc:  # Bericht soll trotzdem entstehen
            self._log(f"ProLiant/RAID: {exc}")
            return None

    def _proliant_step(self, before: proliant_check.ProliantSnapshot) -> TestPlanStepResult:
        """Schritt „ProLiant / RAID-Controller“: zweite Momentaufnahme und Vergleich."""
        started = time.monotonic()
        started_at = _now()
        try:
            after = proliant_check.snapshot(self._privileged_run(), proliant=before.proliant)
            failed, summary, details = proliant_check.evaluate(before, after, self.config.language)
        except Exception as exc:
            return self._timed_result("proliant", STATUS_FAILED, started, started_at, str(exc), [repr(exc)])
        return self._timed_result("proliant", STATUS_FAILED if failed else STATUS_PASSED, started, started_at,
                                  summary, details)

    def _nvme_run(self, command, **kwargs):
        """SMART-Abfragen über die zu Beginn autorisierte Root-Sitzung."""
        if self._root is None:
            return subprocess.run(command, **kwargs)
        return self._root.run(command, **kwargs)

    def _attach_root(self, runner: BaseTestRunner) -> BaseTestRunner:
        if runner is not None and self._root is not None:
            runner.root_broker = self._root
        return runner

    def _privilege_prefix(self) -> tuple[str, ...]:
        if os.geteuid() == 0:
            return ()
        return ("pkexec",) if shutil.which("pkexec") else ()

    def _skipped(self, key: str, summary: str) -> TestPlanStepResult:
        return self._instant_result(key, STATUS_SKIPPED, summary)

    def _instant_result(
        self, key: str, status: str, summary: str, details: Optional[list[str]] = None
    ) -> TestPlanStepResult:
        now = _now()
        return TestPlanStepResult(
            key, _step_name(key, self.config.language), status, now, now, 0.0,
            _text(summary, self.config.language), details or [],
        )

    def _timed_result(
        self,
        key: str,
        status: str,
        started: float,
        started_at: str,
        summary: str,
        details: Optional[list[str]] = None,
    ) -> TestPlanStepResult:
        return TestPlanStepResult(
            key=key,
            name=_step_name(key, self.config.language),
            status=status,
            started_at=started_at,
            finished_at=_now(),
            duration_seconds=max(0.0, time.monotonic() - started),
            summary=_text(summary, self.config.language),
            details=details or [],
        )


def write_test_plan_reports(
    result: TestPlanResult, output_dir: Path, language: str = "de"
) -> tuple[Path, Path]:
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _filename_timestamp(result.started_at)
    safe_host = "".join(c if c.isalnum() or c in "-_" else "_" for c in result.hostname)
    stem = f"hardwaretest-{safe_host}-{timestamp}-{uuid.uuid4().hex[:8]}"
    text_path = output_dir / f"{stem}.txt"
    html_path = output_dir / f"{stem}.html"
    text_path.write_text(render_text_report(result, language), encoding="utf-8")
    html_path.write_text(render_html_report(result, language), encoding="utf-8")
    return text_path, html_path


def render_text_report(result: TestPlanResult, language: str = "de") -> str:
    title = "Hardwaretest – Gesamtprotokoll" if language == "de" else "Hardware Test – Full Report"
    lines = [
        title,
        "=" * len(title),
        f"Version: {__version__}",
        f"Hostname: {result.hostname}",
        f"Kernel: {platform.release()}",
        f"{_text('Beginn', language)}: {result.started_at}",
        f"{_text('Ende', language)}: {result.finished_at}",
        f"{_text('Gesamtergebnis', language)}: {_status_label(result.status, language)}",
        f"{_text('Durchläufe vollständig', language)}: {result.completed_rounds}/{result.requested_rounds}"
        + _end_time_note(result, language),
        f"CSV: {result.csv_report or '–'}",
        "",
    ]
    if result.system_info:
        lines.extend(["System", "-" * 6, *result.system_info, ""])
    for step in result.steps:
        lines.extend([
            f"[{_status_label(step.status, language)}] {step.name}",
            f"{_text('Zeitraum', language)}: {step.started_at} – {step.finished_at}",
            f"{_text('Dauer', language)}: {step.duration_seconds:.1f} s",
            f"{_text('Zusammenfassung', language)}: {step.summary}",
        ])
        if step.details:
            lines.append(f"{_text('Details', language)}:")
            lines.extend(f"  {line}" for line in step.details)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html_report(result: TestPlanResult, language: str = "de") -> str:
    title = "Hardwaretest – Gesamtprotokoll" if language == "de" else "Hardware Test – Full Report"
    cards: list[str] = []
    if result.system_info:
        cards.append(f'<section class="step system"><h2>System</h2><pre>{escape(chr(10).join(result.system_info))}</pre></section>')
    for step in result.steps:
        details = ""
        if step.details:
            details = f"<details open><summary>{escape(_text('Details', language))}</summary><pre>{escape(chr(10).join(step.details))}</pre></details>"
        cards.append(
            f'<section class="step {escape(step.status)}">'
            f'<h2><span>{escape(_status_symbol(step.status))}</span> {escape(step.name)}</h2>'
            f'<div class="meta">{escape(step.started_at)} – {escape(step.finished_at)} · '
            f'{step.duration_seconds:.1f} s</div>'
            f'<p>{escape(step.summary)}</p>{details}</section>'
        )
    return f"""<!doctype html>
<html lang="{escape(language)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} – {escape(result.hostname)}</title>
<style>
:root{{--bg:#17191d;--panel:#24272d;--text:#edf1f5;--dim:#aeb6c2;--ok:#42d66b;--bad:#ff5c5c;--skip:#e5ad3d;--accent:#69a7ff}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Ubuntu,Arial,sans-serif}}
main{{max-width:1050px;margin:auto;padding:28px}} header{{padding:22px;background:var(--panel);border-radius:10px;margin-bottom:20px}}
h1{{margin:0 0 10px;color:var(--accent)}} .summary{{font-size:18px;font-weight:bold}} .meta{{color:var(--dim);font-size:13px;margin-bottom:8px}}
.step{{background:var(--panel);border-left:6px solid var(--skip);border-radius:8px;padding:14px 18px;margin:12px 0;break-inside:avoid}}
.step.system{{border-color:var(--accent)}} .step.passed{{border-color:var(--ok)}} .step.failed{{border-color:var(--bad)}} .step.cancelled{{border-color:var(--bad)}}
.step h2{{font-size:18px;margin:0 0 5px}} .step p{{margin:8px 0}} details{{margin-top:10px}} summary{{cursor:pointer;color:var(--accent)}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#111318;padding:12px;border-radius:6px;color:#d9e1ea}}
@media print{{:root{{--bg:#fff;--panel:#fff;--text:#111;--dim:#555}} main{{padding:0}} header,.step{{border:1px solid #bbb;border-left-width:6px}} details{{display:block}} details>summary{{display:none}}}}
</style></head><body><main><header><h1>{escape(title)}</h1>
<div>{escape(result.hostname)} · Hardwaretest {escape(__version__)} · Kernel {escape(platform.release())}</div>
<div class="meta">{escape(result.started_at)} – {escape(result.finished_at)}</div>
<div>{escape(_text('Durchläufe vollständig', language))}: {result.completed_rounds}/{result.requested_rounds}{escape(_end_time_note(result, language))}</div>
<div>CSV: {escape(str(result.csv_report or '–'))}</div>
<div class="summary">{escape(_text('Gesamtergebnis', language))}: {escape(_status_label(result.status, language))}</div>
</header>{''.join(cards)}</main></body></html>"""


def _overall_status(results: list[TestPlanStepResult], cancelled: bool) -> str:
    if cancelled or any(item.status == STATUS_CANCELLED for item in results):
        return STATUS_CANCELLED
    if any(item.status == STATUS_FAILED for item in results):
        return STATUS_FAILED
    if any(item.status == STATUS_PASSED for item in results):
        return STATUS_PASSED
    return STATUS_SKIPPED


def _end_time_note(result: TestPlanResult, language: str) -> str:
    if result.end_time is None:
        return ""
    stamp = result.end_time.strftime("%Y-%m-%d %H:%M")
    if result.end_time_reached:
        return f" ({_text('Endzeit erreicht', language)}: {stamp})"
    return f" ({_text('Endzeit', language)}: {stamp})"


def _step_name(key: str, language: str) -> str:
    names = {
        "monitoring": ("Langzeitmonitoring", "Long-term monitoring"),
        "proliant": ("ProLiant / RAID-Controller", "ProLiant / RAID controller"),
        "cpu": ("CPU-Stresstest", "CPU stress test"),
        "opendcdiag": ("OpenDCDiag – stille Datenfehler je Kern", "OpenDCDiag – silent data errors per core"),
        "ram": ("RAM-Verifikation", "RAM verification"),
        "nvme": ("NVMe-/Laufwerksgesundheit", "NVMe / drive health"),
        "nvme_read": ("NVMe – vollständiger Lesetest", "NVMe – full read test"),
        "disk_read": ("SATA/SAS/USB – vollständiger Lesetest", "SATA/SAS/USB – full read test"),
        "zfs_scrub": ("ZFS-Scrub (Prüfsummen aller Pools)", "ZFS scrub (checksums of all pools)"),
        "ping": ("Netzwerk-Ping", "Network ping"),
        "iperf3": ("Netzwerk-Durchsatz (iperf3)", "Network throughput (iperf3)"),
        "nvidia": ("NVIDIA-Treiber und GPU-Inventar", "NVIDIA driver and GPU inventory"),
        "dcgm": ("NVIDIA-DCGM-Diagnose", "NVIDIA DCGM diagnostics"),
        "vulkan": ("GPU-Lasttest (Vulkan)", "GPU stress test (Vulkan)"),
        "cancelled": ("Testplan", "Test plan"),
    }
    pair = names.get(key, (key, key))
    return pair[1] if language == "en" else pair[0]


def _status_label(status: str, language: str) -> str:
    labels = {
        STATUS_PASSED: ("BESTANDEN", "PASSED"),
        STATUS_FAILED: ("FEHLGESCHLAGEN", "FAILED"),
        STATUS_SKIPPED: ("ÜBERSPRUNGEN", "SKIPPED"),
        STATUS_CANCELLED: ("ABGEBROCHEN", "CANCELLED"),
    }
    pair = labels.get(status, (status.upper(), status.upper()))
    return pair[1] if language == "en" else pair[0]


def _status_symbol(status: str) -> str:
    return {STATUS_PASSED: "✓", STATUS_FAILED: "✗", STATUS_SKIPPED: "–", STATUS_CANCELLED: "?"}.get(status, "–")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _filename_timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y%m%d-%H%M%S")
    except ValueError:
        return datetime.now().strftime("%Y%m%d-%H%M%S")


def _value(value: object) -> str:
    return "N/A" if value is None else str(value)


def _bounded_details(
    values: list[str], max_lines: int = 200, max_characters: int = 24_000,
    language: str = "de",
) -> list[str]:
    """Bound raw diagnostic logs while preserving their beginning and end."""
    lines = [str(value) for value in values]
    omitted_lines = 0
    if len(lines) > max_lines:
        half = max(1, (max_lines - 1) // 2)
        omitted_lines = len(lines) - half * 2
        omission = (
            f"… {omitted_lines} log lines omitted …"
            if language == "en"
            else f"… {omitted_lines} Protokollzeilen ausgelassen …"
        )
        lines = [
            *lines[:half],
            omission,
            *lines[-half:],
        ]
    if sum(len(line) + 1 for line in lines) <= max_characters:
        return lines

    budget = max(100, (max_characters - 100) // 2)
    head: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        head.append(line)
        used += len(line) + 1
    tail: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) + 1 > budget:
            break
        tail.append(line)
        used += len(line) + 1
    hidden = max(0, len(lines) - len(head) - len(tail))
    omission = (
        f"… {hidden} additional log lines omitted due to size limit …"
        if language == "en"
        else f"… {hidden} weitere Protokollzeilen wegen Größenbegrenzung ausgelassen …"
    )
    return [
        *head,
        omission,
        *reversed(tail),
    ]


def _text(source: str, language: str) -> str:
    translations = {
        "Durchläufe vollständig": "Completed rounds",
        "Endzeit": "End time",
        "Endzeit erreicht": "End time reached",
        "Endzeit erreicht – keine weiteren Teiltests.": "End time reached – no further steps.",
        "Kein Datenträger für den Lesetest ausgewählt.": "No disk selected for the read test.",
        "SMART nicht verfügbar (kein Vorher/Nachher-Vergleich): ": "SMART unavailable (no before/after comparison): ",
        "Datenträger": "disks",
        "zfsutils-linux ist nicht installiert.": "zfsutils-linux is not installed.",
        "Keine importierten ZFS-Pools.": "No imported ZFS pools.",
        "ZFS-Pools nicht lesbar.": "ZFS pools unreadable.",
        "Pool nicht verfügbar": "Pool unavailable",
        "Resilver läuft – Scrub übersprungen.": "Resilver in progress – scrub skipped.",
        "schreibgeschützt – Scrub übersprungen.": "read-only – scrub skipped.",
        "Scrub gestartet": "Scrub started",
        "belegt": "used",
        "ZFS meldet Fehler": "ZFS reports errors",
        "Scrub abgebrochen.": "Scrub cancelled.",
        "Pools geprüft": "pools scrubbed",
        "Letztes Ergebnis": "Latest result",
        "Erster Fehler": "First failure",
        "fio oder nvme-cli ist nicht installiert.": "fio or nvme-cli is not installed.",
        "Kein NVMe-Laufwerk für den Lesetest ausgewählt.": "No NVMe drive selected for the read test.",
        "SMART-Warnung nach Lesetest.": "SMART warning after the read test.",
        "SMART-Medienfehlerzähler nicht verfügbar.": "SMART media error counter unavailable.",
        "NVMe-Prüfung abgebrochen.": "NVMe check cancelled.",
        "Kein auswertbares Testergebnis.": "No usable test result.",
        "WARNUNG": "WARNING",
        "UNBEKANNT": "UNKNOWN",
        "DCGM-Ergebnis nicht auswertbar:": "DCGM result not evaluable:",
        "GPU-Inventar konnte nicht gelesen werden.": "GPU inventory could not be read.",
        "nvidia-smi lieferte keine auswertbare GPU-Liste (leer oder inkompatibles Format).":
            "nvidia-smi returned no usable GPU list (empty or incompatible format).",
        "NVIDIA-Hardware erkannt, aber keine GPU über nvidia-smi auswertbar.":
            "NVIDIA hardware detected, but no GPU could be read via nvidia-smi.",
        "Gesundheitsstatus nicht bestimmbar, fehlende SMART-Felder:":
            "Health status cannot be determined, missing SMART fields:",
        "rasdaemon: korrigierte Hardwarefehler": "rasdaemon: corrected hardware errors",
        "rasdaemon: Ereignisse konnten nicht gelesen werden.": "rasdaemon: events could not be read.",
        "Netzwerk-Messwerte fehlen oder sind ungültig.": "Network measurements are missing or invalid.",
        "Beginn": "Start",
        "ProLiant/RAID: keine Root-Sitzung – IML, Sensoren und RAID-Controller wurden nicht geprüft.":
            "ProLiant/RAID: no root session – IML, sensors and RAID controllers were not checked.",
        "Ende": "End",
        "Gesamtergebnis": "Overall result",
        "Zeitraum": "Time",
        "Dauer": "Duration",
        "Zusammenfassung": "Summary",
        "Details": "Details",
        "Testplan wurde manuell abgebrochen.": "The test plan was cancelled manually.",
        "Root-Berechtigung für den gesamten Testplan wird angefordert …":
            "Requesting root permission for the whole test plan …",
        "Teiltest wurde abgebrochen.": "The test step was cancelled.",
        "stress-ng ist nicht installiert.": "stress-ng is not installed.",
        "nvme-cli ist nicht installiert.": "nvme-cli is not installed.",
        "Keine NVMe-Laufwerke gefunden.": "No NVMe drives found.",
        "Kein Netzwerkziel angegeben.": "No network target was entered.",
        "ping ist nicht installiert.": "ping is not installed.",
        "iperf3 ist nicht installiert.": "iperf3 is not installed.",
        "NVIDIA DCGM ist nicht installiert.": "NVIDIA DCGM is not installed.",
        "OpenDCDiag ist nicht installiert.": "OpenDCDiag is not installed.",
        "OpenDCDiag-Protokoll fehlt oder ist nicht lesbar.": "OpenDCDiag log is missing or unreadable.",
        "Keine NVIDIA-GPU für DCGM gefunden.": "No NVIDIA GPU was found for DCGM.",
        "Vulkan-Loader (libvulkan1) ist nicht installiert.": "Vulkan loader (libvulkan1) is not installed.",
        "Keine Vulkan-GPU gefunden.": "No Vulkan GPU found.",
    }
    if language == "en":
        return translations.get(source, source.replace("Prozess beendet mit Exit-Code", "Process exited with code"))
    return source
