"""Zyklischer RAM-Fuelltest: Speicher vollschreiben, verifizieren, freigeben, wiederholen.

Dieses Modul enthaelt:
- ``MemoryFillRunner`` – ``BaseTestRunner``-Adapter fuer die GUI.
- ``__main__``-Block – eigenstaendiges Skript, das vom Runner per
  ``subprocess`` aufgerufen wird.

Der Test allokiert den verfuegbaren Speicher in grossen Bloecken (mmap),
schreibt Bitmuster (0xAA / 0x55), verifiziert, gibt alles frei und
wiederholt den Zyklus bis die eingestellte Dauer abgelaufen ist.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from hardwaretest.core.test_runner import BaseTestRunner, TestParameters


class MemoryFillRunner(BaseTestRunner):
    """Runner der den zyklischen RAM-Fuelltest als Subprozess startet."""

    def __init__(
        self,
        params: TestParameters,
        memory_mb: int = 0,
        reserve_mb: int = 512,
        chunk_mb: int = 256,
        **kwargs,
    ) -> None:
        super().__init__(params, **kwargs)
        self.memory_mb = memory_mb
        self.reserve_mb = reserve_mb
        self.chunk_mb = chunk_mb

    def build_command(self) -> List[str]:
        script = str(Path(__file__).with_name("memory_fill_script.py"))
        cmd = [
            sys.executable, script,
            "--duration", str(self.params.duration_seconds),
            "--reserve-mb", str(self.reserve_mb),
            "--chunk-mb", str(self.chunk_mb),
        ]
        if self.memory_mb > 0:
            cmd += ["--memory-mb", str(self.memory_mb)]
        return cmd
