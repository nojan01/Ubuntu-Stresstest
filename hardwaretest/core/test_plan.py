"""Sequential, non-destructive hardware test plan and report generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape
import os
from pathlib import Path
import platform
import shutil
import threading
import time
from typing import Callable, Optional

from hardwaretest import __version__
from hardwaretest.core.nvidia import inspect_linux_nvidia, list_nvidia_gpus
from hardwaretest.core.nvme import list_nvme_devices, read_nvme_smart
from hardwaretest.core.system_info import read_system_info
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters
from hardwaretest.tests.network import NetworkRunner
from hardwaretest.tests.nvidia_runner import DcgmDiagnosticRunner, dcgmi_available
from hardwaretest.tests.stress_ng import StressNgRunner


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
    ram_enabled: bool = True
    ram_seconds: int = 60
    ram_percent: int = 70
    nvme_enabled: bool = True
    ping_enabled: bool = False
    network_target: str = ""
    ping_count: int = 20
    iperf_enabled: bool = False
    iperf_seconds: int = 30
    nvidia_enabled: bool = True
    dcgm_enabled: bool = False
    dcgm_level: int = 1


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


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]


class TestPlanExecutor:
    """Execute selected tests one after another in a worker thread."""

    def __init__(
        self,
        config: TestPlanConfig,
        log_fn: Optional[LogCallback] = None,
        progress_fn: Optional[ProgressCallback] = None,
    ) -> None:
        self.config = config
        self._log = log_fn or (lambda _message: None)
        self._progress = progress_fn or (lambda _done, _total, _name: None)
        self._cancel = threading.Event()
        self._active_runner: Optional[BaseTestRunner] = None

    def cancel(self) -> None:
        self._cancel.set()

    def selected_step_keys(self) -> list[str]:
        config = self.config
        steps: list[str] = []
        if config.cpu_enabled:
            steps.append("cpu")
        if config.ram_enabled:
            steps.append("ram")
        if config.nvme_enabled:
            steps.append("nvme")
        if config.ping_enabled:
            steps.append("ping")
        if config.iperf_enabled:
            steps.append("iperf3")
        if config.nvidia_enabled:
            steps.append("nvidia")
        if config.dcgm_enabled:
            steps.append("dcgm")
        return steps

    def execute(self) -> TestPlanResult:
        started = _now()
        results: list[TestPlanStepResult] = []
        step_keys = self.selected_step_keys()
        handlers = {
            "cpu": self._run_cpu,
            "ram": self._run_ram,
            "nvme": self._run_nvme,
            "ping": self._run_ping,
            "iperf3": self._run_iperf,
            "nvidia": self._run_nvidia,
            "dcgm": self._run_dcgm,
        }

        for index, key in enumerate(step_keys):
            if self._cancel.is_set():
                break
            name = _step_name(key, self.config.language)
            self._progress(index, len(step_keys), name)
            self._log(f"=== {name} ===")
            try:
                result = handlers[key]()
            except Exception as exc:  # defensive boundary for long unattended plans
                result = self._instant_result(key, STATUS_FAILED, str(exc), [repr(exc)])
            results.append(result)
            self._log(f"{name}: {_status_label(result.status, self.config.language)}")
            if result.status == STATUS_FAILED and self.config.stop_on_error:
                break

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
        )
        text_path, html_path = write_test_plan_reports(
            plan_result, self.config.output_dir, self.config.language
        )
        plan_result.text_report = text_path
        plan_result.html_report = html_path
        self._progress(len(results), len(step_keys), "")
        return plan_result

    def _run_cpu(self) -> TestPlanStepResult:
        if not shutil.which("stress-ng"):
            return self._skipped("cpu", "stress-ng ist nicht installiert.")
        info = read_system_info()
        cores = max(1, info.logical_cpu_cores - 1)
        runner = StressNgRunner(
            TestParameters(self.config.cpu_seconds, cpu_cores=cores, nice_level=0),
            mode="cpu",
            log_fn=self._log,
        )
        return self._run_process_step("cpu", runner)

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
        if not devices:
            return self._skipped("nvme", "Keine NVMe-Laufwerke gefunden.")
        prefix = self._privilege_prefix()
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
                health = read_nvme_smart(device.controller_path, prefix)
                details.append(
                    f"  Status: {'OK' if health.healthy else 'WARNUNG'} | "
                    f"Temperatur: {_value(health.temperature_c)} °C | "
                    f"Verschleiß: {_value(health.percentage_used)} % | "
                    f"Medienfehler: {_value(health.media_errors)} | "
                    f"Kritische Warnungen: {health.critical_warning}"
                )
                if not health.healthy:
                    failed += 1
            except Exception as exc:
                failed += 1
                details.append(f"  FEHLER: {exc}")
        status = STATUS_FAILED if failed else STATUS_PASSED
        summary = f"{len(devices)} NVMe-Laufwerk(e), {failed} mit Fehler/Warnung"
        return self._timed_result("nvme", status, started, started_at, summary, details)

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
        gpus = list_nvidia_gpus()
        for gpu in gpus:
            details.append(
                f"GPU {gpu.index}: {gpu.model} | {gpu.uuid} | "
                f"VRAM {_value(gpu.memory_used_mib)}/{_value(gpu.memory_total_mib)} MiB | "
                f"{_value(gpu.temperature_c)} °C | {_value(gpu.power_draw_w)} W | "
                f"PCIe Gen {_value(gpu.pcie_generation_current)} "
                f"x{_value(gpu.pcie_width_current)} | ECC unkorrigiert "
                f"{_value(gpu.ecc_uncorrected)}"
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
            use_pkexec=os.geteuid() != 0 and shutil.which("pkexec") is not None,
        )
        result = self._run_process_step("dcgm", runner)
        counts = runner.status_counts()
        result.summary += (
            f" | {counts['pass']} bestanden, {counts['fail']} fehlgeschlagen, "
            f"{counts['warn']} Warnungen, {counts['skip']} übersprungen"
        )
        if counts["fail"]:
            result.status = STATUS_FAILED
        return result

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
        command = runner.build_command()
        details.append("$ " + " ".join(command))
        # build_command can allocate resources (fio job files), therefore start
        # normally and keep the preview only for the report where possible.
        runner.start()
        self._active_runner = runner
        while runner.is_running():
            if self._cancel.wait(0.25):
                runner.stop(aborted=True)
                self._active_runner = None
                return self._timed_result(
                    key, STATUS_CANCELLED, started, started_at,
                    _text("Teiltest wurde abgebrochen.", self.config.language), details,
                )
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
        summary_parts = [
            _status_label(status, self.config.language),
            f"Exit-Code: {result.exit_code}",
        ]
        if summary_extra:
            summary_parts.append(summary_extra)
        if structured_summary and isinstance(runner, NetworkRunner):
            values = runner.summary() or {}
            if "throughput_mbit_s" in values:
                summary_parts.append(f"{values['throughput_mbit_s']:.1f} Mbit/s")
                summary_parts.append(f"Retransmits: {values.get('retransmits', 0):.0f}")
            if "latency_avg_ms" in values:
                summary_parts.append(f"Latenz Ø: {values['latency_avg_ms']:.2f} ms")
                summary_parts.append(
                    f"Paketverlust: {values.get('packet_loss_percent', 0):.1f} %"
                )
        details.extend(result.errors)
        output_lines = getattr(runner, "output_lines", None)
        if structured_summary and isinstance(runner, NetworkRunner):
            details.extend(runner.compact_report_lines(self.config.language))
            if status == STATUS_FAILED:
                failure = runner.failure()
                if failure and failure[1]:
                    details.append(f"Fehler: {' '.join(failure[1].split())[:1000]}")
        elif isinstance(output_lines, list):
            details.extend(str(line) for line in output_lines)
        return self._timed_result(
            key, status, started, started_at, " | ".join(summary_parts),
            _bounded_details(details, language=self.config.language),
        )

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
    stem = f"hardwaretest-{safe_host}-{timestamp}"
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
        "",
    ]
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
.step.passed{{border-color:var(--ok)}} .step.failed{{border-color:var(--bad)}} .step.cancelled{{border-color:var(--bad)}}
.step h2{{font-size:18px;margin:0 0 5px}} .step p{{margin:8px 0}} details{{margin-top:10px}} summary{{cursor:pointer;color:var(--accent)}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#111318;padding:12px;border-radius:6px;color:#d9e1ea}}
@media print{{:root{{--bg:#fff;--panel:#fff;--text:#111;--dim:#555}} main{{padding:0}} header,.step{{border:1px solid #bbb;border-left-width:6px}} details{{display:block}} details>summary{{display:none}}}}
</style></head><body><main><header><h1>{escape(title)}</h1>
<div>{escape(result.hostname)} · Hardwaretest {escape(__version__)} · Kernel {escape(platform.release())}</div>
<div class="meta">{escape(result.started_at)} – {escape(result.finished_at)}</div>
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


def _step_name(key: str, language: str) -> str:
    names = {
        "cpu": ("CPU-Stresstest", "CPU stress test"),
        "ram": ("RAM-Verifikation", "RAM verification"),
        "nvme": ("NVMe-/Laufwerksgesundheit", "NVMe / drive health"),
        "ping": ("Netzwerk-Ping", "Network ping"),
        "iperf3": ("Netzwerk-Durchsatz (iperf3)", "Network throughput (iperf3)"),
        "nvidia": ("NVIDIA-Treiber und GPU-Inventar", "NVIDIA driver and GPU inventory"),
        "dcgm": ("NVIDIA-DCGM-Diagnose", "NVIDIA DCGM diagnostics"),
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
        "Beginn": "Start",
        "Ende": "End",
        "Gesamtergebnis": "Overall result",
        "Zeitraum": "Time",
        "Dauer": "Duration",
        "Zusammenfassung": "Summary",
        "Details": "Details",
        "Testplan wurde manuell abgebrochen.": "The test plan was cancelled manually.",
        "Teiltest wurde abgebrochen.": "The test step was cancelled.",
        "stress-ng ist nicht installiert.": "stress-ng is not installed.",
        "nvme-cli ist nicht installiert.": "nvme-cli is not installed.",
        "Keine NVMe-Laufwerke gefunden.": "No NVMe drives found.",
        "Kein Netzwerkziel angegeben.": "No network target was entered.",
        "ping ist nicht installiert.": "ping is not installed.",
        "iperf3 ist nicht installiert.": "iperf3 is not installed.",
        "NVIDIA DCGM ist nicht installiert.": "NVIDIA DCGM is not installed.",
        "Keine NVIDIA-GPU für DCGM gefunden.": "No NVIDIA GPU was found for DCGM.",
    }
    return translations.get(source, source) if language == "en" else source
