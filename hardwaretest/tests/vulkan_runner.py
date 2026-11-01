"""Herstellerneutraler GPU-Lasttest (Vulkan) für AMD-, Intel- und NVIDIA-Karten."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence

from hardwaretest.core.test_runner import BaseTestRunner, TestExecutionError, TestParameters

SCRIPT = "vulkan_stress_script.py"
_SANITIZE = ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH")


def _script_path() -> Path:
    if getattr(sys, "frozen", False):
        from hardwaretest.core.paths import resource_path

        return Path(resource_path("hardwaretest", "tests", SCRIPT))
    return Path(__file__).with_name(SCRIPT)


def worker_command() -> List[str]:
    """Befehl für den Vulkan-Worker.

    Im gebündelten Programm erben Kindprozesse ``LD_LIBRARY_PATH`` mit dessen
    (älteren) Bibliotheken; die Mesa-Treiber des Systems würden sie mitladen.
    Deshalb läuft der Worker dort bevorzugt mit dem System-Python und
    bereinigter Umgebung; das Skript nutzt nur die Standardbibliothek.
    """
    script = _script_path()
    if not getattr(sys, "frozen", False):
        return [sys.executable, str(script)]
    python = shutil.which("python3", path="/usr/bin:/bin")
    env = shutil.which("env", path="/usr/bin:/bin")
    if python and env and script.is_file():
        unset = [arg for name in _SANITIZE for arg in ("-u", name)]
        return [env, *unset, python, "-I", str(script)]
    return [sys.executable, "--vulkan-stress-worker"]


def vulkan_loader_available() -> bool:
    import ctypes

    try:
        ctypes.CDLL("libvulkan.so.1")
    except OSError:
        return False
    return True


def list_vulkan_gpus(timeout: float = 30) -> List[dict]:
    """GPUs laut Vulkan (ohne Software-Renderer). Wirft TestExecutionError bei Fehlern."""
    env = {key: value for key, value in os.environ.items() if key not in _SANITIZE}
    try:
        completed = subprocess.run(
            [*worker_command(), "--list"], capture_output=True, text=True, timeout=timeout, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TestExecutionError(f"Vulkan-Abfrage fehlgeschlagen: {exc}") from exc
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        raise TestExecutionError(f"Vulkan nicht verfügbar: {payload['error']}")
    if not isinstance(payload, list):
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:] or ["keine Ausgabe"]
        raise TestExecutionError(f"Vulkan-Abfrage fehlgeschlagen: {detail[0]}")
    return payload


class VulkanStressRunner(BaseTestRunner):
    """GPU-Lasttest (Rechenlast + Grafikspeicher-Muster) über Vulkan."""

    def __init__(
        self,
        params: TestParameters,
        gpu_indices: Sequence[int],
        memory_percent: int = 80,
        log_fn=None,
    ) -> None:
        if not gpu_indices:
            raise TestExecutionError("Keine GPU ausgewählt")
        super().__init__(params, log_fn=log_fn)
        self.gpu_indices = sorted(set(int(index) for index in gpu_indices))
        self.memory_percent = memory_percent

    def build_command(self) -> list[str]:
        if not vulkan_loader_available():
            raise TestExecutionError("libvulkan.so.1 nicht gefunden – Vulkan-Loader (libvulkan1) nötig")
        duration = self.params.duration_seconds
        script_duration = duration - 5 if duration > 10 else duration
        return [*worker_command(), "--gpus", ",".join(str(index) for index in self.gpu_indices),
                "--duration", str(script_duration), "--memory-percent", str(self.memory_percent)]

    def _check_line_for_errors(self, line: str) -> None:
        if "FEHLER" in line:
            self._remember_error(line.strip())
            return
        super()._check_line_for_errors(line)
