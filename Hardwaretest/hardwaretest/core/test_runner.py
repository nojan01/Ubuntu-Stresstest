"""Generic helpers for launching long-running stress tools."""

from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Union


LogCallback = Callable[[str], None]


# ---------------------------------------------------------------------------
# Fehler-Keywords fuer automatische Ergebnisbewertung
# ---------------------------------------------------------------------------

_FAILURE_PATTERNS = [
    re.compile(r"FATAL", re.IGNORECASE),
    re.compile(r"hardware\s+error", re.IGNORECASE),
    re.compile(r"hardware\s+failure", re.IGNORECASE),
    re.compile(r"rounding\s+(was\s+)?error", re.IGNORECASE),
    re.compile(r"ILLEGAL SUMOUT", re.IGNORECASE),
    re.compile(r"ECC\s+error", re.IGNORECASE),
    re.compile(r"mismatch", re.IGNORECASE),
    re.compile(r"failed(?!\s*:\s*0\b)", re.IGNORECASE),
    re.compile(r"SIGKILL|SIGSEGV|SIGBUS", re.IGNORECASE),
    re.compile(r"out\s+of\s+memory", re.IGNORECASE),
    re.compile(r"machine\s+check", re.IGNORECASE),
    # fio-spezifische Fehlermeldungen
    re.compile(r"verify:\s*bad\s+header", re.IGNORECASE),
    re.compile(r"verify\s+failed", re.IGNORECASE),
    re.compile(r"crc\d*[a-z]*:\s*verify", re.IGNORECASE),
    re.compile(r"io_u\s+error", re.IGNORECASE),
    re.compile(r"verify_header", re.IGNORECASE),
    re.compile(r"data\s+direction\s+mismatch", re.IGNORECASE),
    re.compile(r"short\s+read", re.IGNORECASE),
    re.compile(r"I/O\s+error", re.IGNORECASE),
    re.compile(r"medium\s+error", re.IGNORECASE),
    re.compile(r"unrecovered\s+read\s+error", re.IGNORECASE),
    # HPE RAID-Controller / SCSI-Fehler
    re.compile(r"sense\s+key.*(?:HARDWARE|MEDIUM|ABORTED)", re.IGNORECASE),
    re.compile(r"SCSI\s+error", re.IGNORECASE),
    re.compile(r"drive\s+(?:fault|failure)", re.IGNORECASE),
    re.compile(r"predictive\s+failure", re.IGNORECASE),
]


@dataclass
class TestResult:
    """Ergebnis eines abgeschlossenen Tests."""
    passed: bool
    errors: List[str]
    duration_actual: float
    exit_code: Optional[int]

    @property
    def status_text(self) -> str:
        if self.passed:
            return "BESTANDEN ✓"
        return f"FEHLER GEFUNDEN ✗ ({len(self.errors)} Treffer)"

    @property
    def status_emoji(self) -> str:
        return "✓" if self.passed else "✗"


@dataclass
class TestParameters:
    duration_seconds: int
    cpu_cores: Optional[int] = None
    memory_bytes: Optional[int] = None
    nice_level: int = 19
    cpu_mask: Optional[str] = None


class TestExecutionError(RuntimeError):
    pass


PathLike = Union[str, Path]


class BaseTestRunner:
    """Provides lifecycle, stdout/stderr collection and result evaluation for CLI tools."""

    def __init__(
        self,
        params: TestParameters,
        log_fn: Optional[LogCallback] = None,
        work_dir: Optional[PathLike] = None,
    ) -> None:
        self.params = params
        self._log = log_fn or (lambda msg: None)
        self._process: Optional[subprocess.Popen[str]] = None
        self._start_time: Optional[float] = None
        self._stdout_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_cancel = threading.Event()
        self._work_dir = str(work_dir) if work_dir else None
        self._collected_errors: List[str] = []
        self._result: Optional[TestResult] = None

    def build_command(self) -> List[str]:  # pragma: no cover - abstract hook
        raise NotImplementedError

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            raise TestExecutionError("Test läuft bereits")
        self._collected_errors = []
        self._result = None
        cmd = self.build_command()
        self._log(f"Starte: {' '.join(cmd)}")
        self._start_time = time.time()
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=self._work_dir,
        )
        self._stdout_thread = threading.Thread(target=self._stream_output, daemon=True)
        self._stdout_thread.start()
        # Watchdog: beendet den Prozess automatisch nach der eingestellten Dauer
        self._watchdog_cancel.clear()
        if self.params.duration_seconds > 0:
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_timer, daemon=True
            )
            self._watchdog_thread.start()

    def stop(self) -> None:
        self._watchdog_cancel.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread = None
        if self._process and self._process.poll() is None:
            self._log("Stoppe Test...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._finalize_result()
        self._process = None
        self._start_time = None

    def is_running(self) -> bool:
        return bool(self._process and self._process.poll() is None)

    def progress(self) -> float:
        if not self._start_time or self.params.duration_seconds <= 0:
            return 0.0
        elapsed = time.time() - self._start_time
        return min(1.0, elapsed / self.params.duration_seconds)

    def get_result(self) -> Optional[TestResult]:
        """Gibt das Testergebnis zurueck (nur verfuegbar nach Testende)."""
        return self._result

    def _check_line_for_errors(self, line: str) -> None:
        """Prueft eine Ausgabezeile auf bekannte Fehler-Keywords."""
        for pattern in _FAILURE_PATTERNS:
            if pattern.search(line):
                self._collected_errors.append(line.strip())
                break

    def _finalize_result(self) -> None:
        """Erstellt das TestResult nach Abschluss des Tests."""
        elapsed = 0.0
        if self._start_time:
            elapsed = time.time() - self._start_time
        exit_code = self._process.returncode if self._process else None
        # Nicht-Null Exit-Code als Fehler werten
        non_zero_exit = exit_code is not None and exit_code != 0
        has_errors = len(self._collected_errors) > 0 or non_zero_exit
        if non_zero_exit and not self._collected_errors:
            self._collected_errors.append(f"Prozess beendet mit Exit-Code {exit_code}")
        self._result = TestResult(
            passed=not has_errors,
            errors=list(self._collected_errors),
            duration_actual=elapsed,
            exit_code=exit_code,
        )

    def _watchdog_timer(self) -> None:
        """Wartet die eingestellte Testdauer ab und beendet den Prozess automatisch."""
        duration = self.params.duration_seconds
        if self._watchdog_cancel.wait(timeout=duration):
            return
        if self._process and self._process.poll() is None:
            self._log(
                f"Testzeit ({duration}s) abgelaufen – Prozess wird automatisch beendet."
            )
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._log("Prozess reagiert nicht auf SIGTERM – sende SIGKILL.")
                self._process.kill()

    def _stream_output(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            stripped = line.rstrip()
            self._log(stripped)
            self._check_line_for_errors(stripped)
        self._process.wait()
        self._finalize_result()
        if self._result:
            self._log(f"Test beendet – Ergebnis: {self._result.status_text}")
        else:
            self._log("Test beendet.")
