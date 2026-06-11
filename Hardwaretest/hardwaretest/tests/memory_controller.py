"""Stress-ng basierte Speichercontroller- und Cache-Tests.

Nutzt Stressoren die tatsaechlich den Speichercontroller,
Cache-Kohaerenz und Speicherbus belasten (statt Socket-Syscalls).
"""

from __future__ import annotations

from typing import List, Optional

from hardwaretest.core.test_runner import BaseTestRunner, TestParameters


# Verfuegbare Speichercontroller-Stressoren
MC_STRESSORS = {
    "cache": "Cache-Line-Stress (L1/L2/L3)",
    "membarrier": "Memory-Barrier-Operationen",
    "atomic": "Atomare Speicheroperationen",
    "tlb-shootdown": "TLB-Shootdown zwischen Kernen",
    "numa": "NUMA-Speicherzugriffe (Multi-Socket)",
    "lockbus": "Bus-Lock-Operationen",
    "mcontend": "Speicher-Contention zwischen Kernen",
}


class MemoryControllerRunner(BaseTestRunner):
    def __init__(
        self,
        params: TestParameters,
        stressors: Optional[List[str]] = None,
        operations: Optional[int] = None,
        **kwargs,
    ) -> None:
        super().__init__(params, **kwargs)
        self.operations = max(0, operations or 0)
        # Standard: cache + atomic + mcontend fuer gute Abdeckung
        default_stressors = ["cache", "atomic", "mcontend"]
        self.stressors = stressors or default_stressors

    def build_command(self) -> List[str]:
        params = self.params
        workers = params.cpu_cores or 1
        cmd = [
            "stress-ng",
            "-t", f"{params.duration_seconds}s",
        ]
        if params.nice_level:
            cmd += ["--nice", str(params.nice_level)]

        for stressor in self.stressors:
            if stressor in MC_STRESSORS:
                cmd += [f"--{stressor}", str(workers)]

        if self.operations > 0:
            # Ops-Limit auf den ersten Stressor anwenden
            if self.stressors:
                cmd += [f"--{self.stressors[0]}-ops", str(self.operations)]

        return cmd
