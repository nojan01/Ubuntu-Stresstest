"""Manual Intel Data Center Diagnostic Tool, separate from OpenDCDiag."""

from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import threading

from hardwaretest.core.hardware_inventory import read_cpu_packages
from hardwaretest.core.test_runner import BaseTestRunner, LogCallback, TestParameters, TestResult


def available_dcdiag_path() -> str | None:
    """Offer the installed tool only with positive Intel Xeon inventory evidence."""
    packages = read_cpu_packages()
    if not any(
        package.vendor == "GenuineIntel" and re.search(r"\bXeon\b", package.model_name)
        for package in packages
    ):
        return None
    return shutil.which("dcdiag")


@dataclass
class DcDiagResult(TestResult):
    outcome: str = "unknown"

    @property
    def status_text(self) -> str:
        # Do not let the generic runner label an inconclusive result a hardware failure.
        return f"DCDiag: {self.outcome} (Exit-Code {self.exit_code})"


class DcDiagRunner(BaseTestRunner):
    """Run without CLI options; Intel documents messages, not exit-code semantics."""

    timeout_seconds = 90 * 60
    _use_watchdog = False

    def __init__(self, binary_path: str, log_fn: LogCallback | None = None) -> None:
        super().__init__(TestParameters(self.timeout_seconds), log_fn=log_fn)
        self.binary_path = binary_path
        self._messages: set[str] = set()
        self._timed_out = threading.Event()

    def build_command(self) -> list[str]:
        return [self.binary_path]

    def get_result(self) -> DcDiagResult | None:
        return self._result if isinstance(self._result, DcDiagResult) else None

    def start(self) -> None:
        self._messages.clear()
        self._timed_out.clear()
        super().start()
        # A very short run can finalize before BaseTestRunner.start clears its
        # cancellation event. Only create a watchdog while the process is alive.
        if self._process is not None and self._process.poll() is None:
            self._watchdog_thread = threading.Thread(target=self._watchdog_timer, daemon=True)
            self._watchdog_thread.start()

    def _check_line_for_errors(self, line: str) -> None:
        text = line.strip()
        # README.md in Intel package 693 and support article 000098270.
        if text == "Test completed successfully. No issues detected.":
            self._messages.add("passed")
        elif text == "This processor is not supported by this version of the tool.":
            self._messages.add("unsupported")
        elif text.startswith((
            "Test completed. Results are inconclusive",
            "Test completed successfully. One or more machine check errors occurred",
        )):
            self._messages.add("inconclusive")
        elif text.startswith(("Test failed.", "Test failed (")):
            self._messages.add("failed")

    def _watchdog_timer(self) -> None:
        if self._watchdog_cancel.wait(self.params.duration_seconds):
            return
        if self._process and self._process.poll() is None:
            self._timed_out.set()
            self._log("DCDiag: Sicherheitslimit erreicht; kein vollstaendiges Ergebnis.")
            self._terminate_process(self.terminate_grace_seconds)

    def _build_result(self) -> None:
        self._watchdog_cancel.set()
        super()._build_result()
        result = self._result
        assert result is not None
        # Inconclusive MCE output may also contain "Test failed (#...)".
        outcome = next((name for name in ("inconclusive", "failed", "unsupported", "passed")
                        if name in self._messages), "unknown")
        if self._aborted:
            outcome = "cancelled"
        elif self._timed_out.is_set():
            outcome = "timeout"
        elif outcome == "passed" and (result.exit_code != 0 or result.errors):
            outcome = "unknown"
        self._result = DcDiagResult(
            passed=outcome == "passed",
            errors=result.errors,
            warnings=result.warnings,
            duration_actual=result.duration_actual,
            exit_code=result.exit_code,
            outcome=outcome,
        )
