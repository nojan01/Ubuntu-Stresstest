"""Optional NVIDIA DCGM diagnostic runner."""

from __future__ import annotations

import json
import os
import shutil
from typing import Optional, Sequence

from hardwaretest.core.test_runner import BaseTestRunner, TestExecutionError, TestParameters


def dcgmi_available() -> bool:
    return shutil.which("dcgmi") is not None


class DcgmDiagnosticRunner(BaseTestRunner):
    def __init__(
        self,
        params: TestParameters,
        gpu_indices: Sequence[int],
        run_level: int,
        log_fn=None,
        use_pkexec: bool = False,
    ) -> None:
        if not gpu_indices:
            raise TestExecutionError("Keine NVIDIA-GPU ausgewählt")
        if run_level not in {1, 2, 3, 4}:
            raise TestExecutionError("Ungültige DCGM-Diagnosestufe")
        super().__init__(params, log_fn=log_fn)
        self.gpu_indices = sorted(set(int(index) for index in gpu_indices))
        self.run_level = run_level
        self.output_lines: list[str] = []
        self._command_prefix: list[str] = []
        if use_pkexec and os.geteuid() != 0:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                raise TestExecutionError("pkexec nicht gefunden. Bitte polkit installieren.")
            self._command_prefix = [pkexec]

    def build_command(self) -> list[str]:
        if not dcgmi_available():
            raise TestExecutionError("dcgmi ist nicht installiert")
        entities = ",".join(f"gpu:{index}" for index in self.gpu_indices)
        return [
            *self._command_prefix,
            "dcgmi",
            "diag",
            "--run",
            str(self.run_level),
            "--entity-id",
            entities,
            "--json",
        ]

    def _stream_output(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            stripped = line.rstrip()
            self.output_lines.append(stripped)
            self._log(stripped)
            self._check_line_for_errors(stripped)
        self._process.wait()
        self._finalize_result()

    def status_counts(self) -> dict[str, int]:
        """Count DCGM result states without relying on one schema version."""
        payload = _extract_json("\n".join(self.output_lines))
        counts = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}

        def visit(value: object) -> None:
            if isinstance(value, dict):
                status = value.get("status")
                if isinstance(status, str):
                    normalized = status.casefold()
                    for key in counts:
                        if normalized.startswith(key):
                            counts[key] += 1
                            break
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        if payload is not None:
            visit(payload)
        return counts


def _extract_json(output: str) -> Optional[object]:
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(output[start:end + 1])
    except json.JSONDecodeError:
        return None
