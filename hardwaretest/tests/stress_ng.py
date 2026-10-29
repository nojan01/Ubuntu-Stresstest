"""Adapter für stress-ng mit dedizierten CPU- und RAM-Testmodi."""

from __future__ import annotations

from typing import List, Optional
import re
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
    "mix": {
        "label": "CPU-Mischlast mit Ergebnisprüfung",
        "description": "Ganzzahl-, Gleitkomma-, FMA-, Vektor-, Matrix-, L1-Cache- und Kompressionslast "
                       "gleichzeitig; jede Lastart prüft ihre Ergebnisse (--verify).",
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


# Lastarten der Mischlast: alle unterstützen --verify (stress-ng --verifiable) und
# sind ab stress-ng 0.17 (Ubuntu 24.04) vorhanden. "hash" fehlt bewusst, weil es
# lange Tabellen ausgibt.
MIX_STRESSORS = (
    "cpu", "matrix", "matrix-3d", "fma", "fp", "vecmath", "vecwide", "vecfp", "trig", "l1cache", "zlib",
)

_FAIL_LINE = re.compile(r"stress-ng:\s+fail:", re.IGNORECASE)
_FAILED_SUMMARY = re.compile(r"\bfailed:\s*[1-9]\d*:\s*(.+)$")


def with_nice(command: List[str], level: int) -> List[str]:
    """Priorität per ``nice -n`` senken.

    Nicht ``stress-ng --nice N``: das startet N zusätzliche Worker, die ihre Priorität zufällig ändern.
    """
    nice = shutil.which("nice") if level else None
    return [nice, "-n", str(level), *command] if nice else list(command)


def mix_worker_counts(total_workers: int) -> List[tuple]:
    """Verteilt die Worker gleichmäßig auf die Lastarten der Mischlast (mind. 1 je Lastart)."""
    total = max(1, total_workers)
    n = len(MIX_STRESSORS)
    return [(name, max(1, total // n + (1 if i < total % n else 0))) for i, name in enumerate(MIX_STRESSORS)]


class StressNgRunner(BaseTestRunner):
    """Konfigurierbarer stress-ng Runner mit Testmodus-Auswahl."""

    def __init__(
        self,
        params: TestParameters,
        mode: str = "combined",
        vm_workers: Optional[int] = None,
        sched_idle: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(params, **kwargs)
        self.mode = mode if mode in STRESS_NG_MODES else "combined"
        # VM-Worker: Standard = Anzahl CPU-Kerne fuer maximale Abdeckung
        self.vm_workers = vm_workers or (params.cpu_cores or 1)
        # SCHED_IDLE (chrt --idle): laeuft nur wenn das System sonst nichts
        # zu tun hat. Standardmaessig AUS, weil es auf belasteten Systemen
        # zu einem effektiv inaktiven Test fuehrt (false-OK).
        self.sched_idle = sched_idle

    def build_command(self) -> List[str]:
        params = self.params
        cmd = ["stress-ng"]
        # -q unterdrückt auch die "fail:"-Meldungen; die Mischlast soll benennen, was falsch rechnete.
        if self.mode != "mix":
            cmd.append("-q")
        cmd += ["-t", f"{params.duration_seconds}s"]

        # Modus-spezifische Stressoren
        if self.mode == "cpu":
            cmd += self._build_cpu_args(params)
        elif self.mode == "ram":
            cmd += self._build_ram_args(params)
        elif self.mode == "ram_bandwidth":
            cmd += self._build_ram_bandwidth_args(params)
        elif self.mode == "cache":
            cmd += self._build_cache_args(params)
        elif self.mode == "mix":
            cmd += self._build_mix_args(params)
        else:  # combined
            cmd += self._build_combined_args(params)

        final_cmd = with_nice(cmd, params.nice_level)
        if params.cpu_mask:
            taskset = shutil.which("taskset")
            if taskset:
                final_cmd = [taskset, params.cpu_mask, *final_cmd]
            else:
                self._log("taskset nicht gefunden - starte ohne CPU-Affinitaet.")
        if self.sched_idle:
            chrt = shutil.which("chrt")
            if chrt:
                final_cmd = [chrt, "--idle", "0", *final_cmd]
            else:
                self._log("chrt nicht gefunden - starte ohne SCHED_IDLE.")
        return final_cmd

    def _build_cpu_args(self, params: TestParameters) -> List[str]:
        """Maximale CPU-Hitze: matrix + cpu-Methoden."""
        args: List[str] = []
        if params.cpu_cores:
            args += ["-c", str(params.cpu_cores)]
        args += ["--cpu-method", "all", "--matrix", "1", "--verify"]
        return args

    def _build_mix_args(self, params: TestParameters) -> List[str]:
        """Viele Rechenwerke gleichzeitig, jede Lastart mit Ergebnisprüfung."""
        args: List[str] = []
        for name, workers in mix_worker_counts(params.cpu_cores or 1):
            args += [f"--{name}", str(workers)]
            if name == "cpu":
                args += ["--cpu-method", "all"]
        args.append("--verify")
        return args

    def _check_line_for_errors(self, line: str) -> None:
        if _FAIL_LINE.search(line):
            self._remember_error(line.strip())
            return
        match = _FAILED_SUMMARY.search(line)
        if match:
            self._remember_error(f"stress-ng: Ergebnisprüfung fehlgeschlagen: {match.group(1).strip()}")
            return
        super()._check_line_for_errors(line)

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
