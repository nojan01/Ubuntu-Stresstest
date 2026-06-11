"""Adapter für stress-ng mit dedizierten CPU- und RAM-Testmodi."""

from __future__ import annotations

from typing import List, Optional
import shutil

from hardwaretest.core.test_runner import BaseTestRunner, TestParameters


# ---------------------------------------------------------------------------
# Testmodus-Definitionen
# ---------------------------------------------------------------------------

STRESS_NG_MODES = {
    "cpu": {
        "label": "CPU-Last (maximale Hitze)",
        "description": "Maximale CPU-Belastung durch Berechnungen, wenig RAM-Nutzung.",
    },
    "ram": {
        "label": "RAM-Test (Fehler finden)",
        "description": "Aggressiver Speichertest mit Verifikation. Findet RAM-Fehler.",
    },
    "ram_bandwidth": {
        "label": "RAM-Bandbreite (Durchsatz)",
        "description": "Testet Speicherbandbreite mit memcpy/stream-Workloads.",
    },
    "combined": {
        "label": "CPU + RAM kombiniert",
        "description": "Gleichzeitige CPU-Berechnung und RAM-Verifizierung.",
    },
    "cache": {
        "label": "CPU-Cache-Stress",
        "description": "Cache-Line-Bouncing zwischen Kernen. Findet Inter-Core-Fehler.",
    },
}


class StressNgRunner(BaseTestRunner):
    """Konfigurierbarer stress-ng Runner mit Testmodus-Auswahl."""

    def __init__(
        self,
        params: TestParameters,
        mode: str = "combined",
        vm_workers: Optional[int] = None,
        **kwargs,
    ) -> None:
        super().__init__(params, **kwargs)
        self.mode = mode if mode in STRESS_NG_MODES else "combined"
        # VM-Worker: Standard = Anzahl CPU-Kerne fuer maximale Abdeckung
        self.vm_workers = vm_workers or (params.cpu_cores or 1)

    def build_command(self) -> List[str]:
        params = self.params
        cmd = [
            "stress-ng",
            "-q",
            "-t", f"{params.duration_seconds}s",
        ]

        if params.nice_level:
            cmd += ["--nice", str(params.nice_level)]

        # Modus-spezifische Stressoren
        if self.mode == "cpu":
            cmd += self._build_cpu_args(params)
        elif self.mode == "ram":
            cmd += self._build_ram_args(params)
        elif self.mode == "ram_bandwidth":
            cmd += self._build_ram_bandwidth_args(params)
        elif self.mode == "cache":
            cmd += self._build_cache_args(params)
        else:  # combined
            cmd += self._build_combined_args(params)

        final_cmd = cmd
        if params.cpu_mask:
            taskset = shutil.which("taskset")
            if taskset:
                final_cmd = [taskset, params.cpu_mask] + final_cmd
            else:
                self._log("taskset nicht gefunden - starte ohne CPU-Affinitaet.")
        chrt = shutil.which("chrt")
        if chrt:
            final_cmd = [chrt, "--idle", "0"] + final_cmd
        return final_cmd

    def _build_cpu_args(self, params: TestParameters) -> List[str]:
        """Maximale CPU-Hitze: matrix + cpu-Methoden."""
        args: List[str] = []
        if params.cpu_cores:
            args += ["-c", str(params.cpu_cores)]
        args += ["--cpu-method", "all", "--matrix", "1"]
        return args

    def _build_ram_args(self, params: TestParameters) -> List[str]:
        """Aggressiver RAM-Test mit Verifikation."""
        workers = self.vm_workers
        args = [
            "--vm", str(workers),
            "--vm-method", "all",
            "--verify",
        ]
        if params.memory_bytes:
            per_worker = max(1, params.memory_bytes // max(1, workers))
            args += ["--vm-bytes", str(per_worker)]
        args += [
            "--vm-hang", "0",
            "--vm-keep",
        ]
        return args

    def _build_ram_bandwidth_args(self, params: TestParameters) -> List[str]:
        """Speicherbandbreite: memcpy + stream."""
        workers = max(1, self.vm_workers // 2)
        args = [
            "--memcpy", str(workers),
            "--stream", str(workers),
        ]
        if params.memory_bytes:
            args += ["--stream-l3-size", str(min(params.memory_bytes, 64 * 1024 * 1024))]
        return args

    def _build_cache_args(self, params: TestParameters) -> List[str]:
        """Cache-Stress: findet Inter-Core-Fehler."""
        workers = max(1, params.cpu_cores or 1)
        args = [
            "--cache", str(workers),
            "--cache-level", "3",
        ]
        return args

    def _build_combined_args(self, params: TestParameters) -> List[str]:
        """CPU + RAM gemeinsam."""
        args: List[str] = []
        cpu_workers = max(1, (params.cpu_cores or 1) // 2)
        vm_workers = max(1, self.vm_workers - cpu_workers)
        # CPU-Teil
        args += ["-c", str(cpu_workers), "--cpu-method", "all"]
        # RAM-Teil mit Verifikation
        args += [
            "--vm", str(vm_workers),
            "--vm-method", "all",
            "--verify",
            "--vm-keep",
        ]
        if params.memory_bytes:
            per_worker = max(1, params.memory_bytes // max(1, vm_workers))
            args += ["--vm-bytes", str(per_worker)]
        # Matrix fuer zusaetzliche Last
        args += ["--matrix", "1"]
        return args
