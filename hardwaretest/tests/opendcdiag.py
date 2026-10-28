"""Adapter für OpenDCDiag (Intel, Apache 2.0) – sucht stille Datenfehler je CPU-Kern."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from hardwaretest.core.paths import resource_path
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters

# -march=haswell setzt diese Befehlssätze voraus; sonst die generische Variante.
_HASWELL_FLAGS = {"avx2", "bmi2", "fma", "movbe", "f16c"}
_FAILED_RESULTS = {"fail", "crash", "timed out", "operating system error"}


def cpu_flags(cpuinfo: str = "/proc/cpuinfo") -> set[str]:
    try:
        text = Path(cpuinfo).read_text(errors="replace")
    except OSError:
        return set()
    for line in text.splitlines():
        if line.startswith("flags"):
            return set(line.split(":", 1)[1].split())
    return set()


def default_opendcdiag_path(flags: Optional[set[str]] = None) -> str:
    """Gebündeltes Binary passend zur CPU, sonst ``opendcdiag`` im PATH."""
    flags = cpu_flags() if flags is None else flags
    names = ["opendcdiag", "opendcdiag-generic"] if flags >= _HASWELL_FLAGS else ["opendcdiag-generic"]
    for name in names:
        bundled = resource_path("vendor", "opendcdiag", name)
        if bundled.is_file():
            return str(bundled)
    found = shutil.which("opendcdiag")
    return found or str(resource_path("vendor", "opendcdiag", names[0]))


def opendcdiag_available() -> bool:
    return Path(default_opendcdiag_path()).is_file()


def sudo_allowed(binary: str) -> bool:
    """True, wenn ``sudo -n`` das Binary ohne Passwort als root starten darf.

    Das .deb liefert dafür /etc/sudoers.d/hardwaretest-opendcdiag mit. ``sudo -n -l``
    taugt nicht als Prüfung: Es meldet Erfolg, obwohl ein Passwort nötig wäre.
    """
    if os.geteuid() == 0 or not shutil.which("sudo"):
        return False
    try:
        return subprocess.run(
            ["sudo", "-n", binary, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def runs_as_root(binary: str) -> bool:
    return os.geteuid() == 0 or sudo_allowed(binary)


@dataclass
class OpenDcDiagReport:
    exit: str = ""
    counts: Counter = field(default_factory=Counter)
    failures: List[str] = field(default_factory=list)
    failed_cpus: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)


def _cpu_label(cpu: object) -> str:
    if not isinstance(cpu, dict):
        return "CPU ?"
    return f"S{cpu.get('package', '?')} Core {cpu.get('core', '?')} (CPU {cpu.get('logical', '?')})"


def _thread_message(thread: dict) -> str:
    for message in thread.get("messages") or []:
        if not isinstance(message, dict) or message.get("level") != "error":
            continue
        text = str(message.get("text") or "").strip()
        miscompare = message.get("data-miscompare")
        if isinstance(miscompare, dict):
            text = (f"{text} {miscompare.get('description', '')} "
                    f"(expected={miscompare.get('expected')}, actual={miscompare.get('actual')})").strip()
        if text:
            return " ".join(text.split())[:300]
    return ""


def parse_report(data: object) -> OpenDcDiagReport:
    """Wertet das YAML-Protokoll (``-o``) aus."""
    report = OpenDcDiagReport()
    if not isinstance(data, dict):
        return report
    report.exit = str(data.get("exit") or "")
    seen: set[str] = set()
    cpus: set[str] = set()
    for test in data.get("tests") or []:
        if not isinstance(test, dict):
            continue
        name = str(test.get("test", "?"))
        result = str(test.get("result", "?"))
        report.counts[result] += 1
        if result == "skip":
            report.skipped.setdefault(name, str(test.get("skip-reason") or "").strip())
            continue
        if result not in _FAILED_RESULTS:
            continue
        threads = [t for t in test.get("threads") or [] if isinstance(t, dict)]
        failed_threads = [t for t in threads if t.get("state") == "failed"] or threads
        if not failed_threads:
            reason = (test.get("result-details") or {}).get("reason", "") if isinstance(
                test.get("result-details"), dict) else ""
            failed_threads = [{"id": None, "messages": [{"level": "error", "text": reason}]}]
        for thread in failed_threads:
            cpu = _cpu_label(thread.get("id")) if thread.get("id") is not None else "CPU ?"
            line = f"OpenDCDiag {result}: {name} – {cpu}"
            message = _thread_message(thread)
            if message:
                line += f": {message}"
            if line not in seen:
                seen.add(line)
                report.failures.append(line)
            if cpu != "CPU ?":
                cpus.add(cpu)
    report.failed_cpus = sorted(cpus)
    if report.exit in {"fail", "invalid"} and not report.failures:
        report.failures.append(f"OpenDCDiag exit: {report.exit}")
    return report


def load_report(path: Path) -> Optional[OpenDcDiagReport]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
            return parse_report(yaml.load(handle, Loader=loader))
    except (OSError, yaml.YAMLError):
        return None


class OpenDcDiagRunner(BaseTestRunner):
    # OpenDCDiag beendet sich mit --strict-runtime selbst; der Watchdog greift nur bei Hängern.
    watchdog_slack_seconds = 120

    def __init__(
        self,
        params: TestParameters,
        binary_path: Optional[str] = None,
        cpuset: str = "",
        **kwargs,
    ) -> None:
        self.binary_path = str(Path(binary_path or default_opendcdiag_path()).expanduser())
        self._tmp_dir: Optional[Path] = None
        self._report_loaded = False
        self.yaml_path = Path(tempfile.gettempdir()) / "opendcdiag.yaml"
        super().__init__(params, **kwargs)
        self.cpuset = cpuset.strip()
        self.report: Optional[OpenDcDiagReport] = None
        # Ohne Root-Sitzung root per "sudo -n" nutzen, sofern ohne Passwort erlaubt.
        self.use_sudo = True
        self._sudo = False

    def build_command(self) -> List[str]:
        cmd = [
            *(["sudo", "-n"] if self._sudo else []),
            self.binary_path,
            "-T", f"{max(1, int(self.params.duration_seconds))}s",
            "--strict-runtime",
            "-o", str(self.yaml_path),
        ]
        if self.cpuset:
            cmd.append(f"--cpuset={self.cpuset}")
        return cmd

    def start(self) -> None:
        self.report = None
        self._report_loaded = False
        # Beschreibbar auch für einen per Root-Sitzung gestarteten Prozess.
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="hardwaretest-opendcdiag-"))
        self._tmp_dir.chmod(0o777)
        self.yaml_path = self._tmp_dir / "opendcdiag.yaml"
        self._work_dir = str(self._tmp_dir)
        self._sudo = self.use_sudo and self.root_broker is None and sudo_allowed(self.binary_path)
        try:
            super().start()
        except Exception:
            self._remove_tmp_dir()
            raise

    def _check_line_for_errors(self, line: str) -> None:
        # Bewertet wird das YAML-Protokoll; die Konsolenausgabe enthält nur Kopfzeilen.
        return

    def _finalize_result(self) -> None:
        finished = self._process is None or self._process.poll() is not None
        if finished and not self._report_loaded:
            self._report_loaded = True
            self.report = load_report(self.yaml_path) if self._tmp_dir is not None else None
            self._remove_tmp_dir()
            if self.report is not None:
                for line in self.report.failures:
                    self._remember_error(line)
                self._log("OpenDCDiag: " + self.summary_text())
                for name, reason in self.report.skipped.items():
                    self._log(f"  übersprungen: {name}: {reason}")
            elif not self._aborted:
                self._remember_error("OpenDCDiag-Protokoll fehlt oder ist nicht lesbar.")
        super()._finalize_result()

    def _remove_tmp_dir(self) -> None:
        tmp_dir, self._tmp_dir = self._tmp_dir, None
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def summary_text(self, language: str = "de") -> str:
        if self.report is None:
            return ""
        counts = self.report.counts
        failed = sum(counts[r] for r in _FAILED_RESULTS)
        skipped = len(self.report.skipped)
        if language == "en":
            text = f"{counts['pass']} passed, {failed} failed, {skipped} tests skipped"
            label = "Cores"
        else:
            text = f"{counts['pass']} bestanden, {failed} fehlgeschlagen, {skipped} Tests übersprungen"
            label = "Kerne"
        if self.report.failed_cpus:
            text += f" | {label}: " + ", ".join(self.report.failed_cpus)
        return text
