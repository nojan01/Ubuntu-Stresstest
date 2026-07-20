"""Fio-based disk workload runner.

Optimiert fuer HPE ProLiant Server mit RAID-Controllern und
NVMe-Laufwerken.  Unterstuetzt io_uring (Gen10+) und libaio.
Alle Schreib-Workloads nutzen --verify fuer Datenintegritaet.
"""

from __future__ import annotations

import contextlib
import functools
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from hardwaretest.core.test_runner import BaseTestRunner, TestExecutionError, TestParameters


@functools.lru_cache(maxsize=1)
def _best_ioengine() -> str:
    """Waehlt die beste verfuegbare IO-Engine.

    Ergebnis wird gecached, damit nicht bei jeder Runner-Instanziierung
    ein ``fio --enghelp``-Subprozess gestartet wird.

    io_uring ist ab Linux 5.1+ verfuegbar und bietet weniger Overhead
    als libaio, was auf HPE ProLiant Gen10+ mit NVMe vorteilhaft ist.
    """
    try:
        result = subprocess.run(
            ["fio", "--enghelp"],
            capture_output=True, text=True, timeout=5,
        )
        if "io_uring" in (result.stdout or ""):
            return "io_uring"
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return "libaio"


def _mounted_devices() -> set[str]:
    """Liefert die Menge der aktuell eingehaengten Block-Devices.

    Liest /proc/mounts und expandiert symbolische Links, sodass
    ``/dev/sda1`` und ``/dev/disk/by-uuid/...`` korrekt erkannt werden.
    """
    mounts: set[str] = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                dev = parts[0]
                if not dev.startswith("/dev/"):
                    continue
                mounts.add(dev)
                try:
                    real = os.path.realpath(dev)
                    mounts.add(real)
                except OSError:
                    pass
    except OSError:
        pass
    return mounts


def _check_devices_not_mounted(devices: List[str]) -> None:
    """Wirft TestExecutionError, falls eines der *devices* eingehaengt ist.

    Schuetzt vor versehentlichem Ueberschreiben aktiver Filesysteme im
    destruktiven Modus.
    """
    mounted = _mounted_devices()
    blocked: List[str] = []
    for dev in devices:
        candidates = {dev}
        with contextlib.suppress(OSError):
            candidates.add(os.path.realpath(dev))
        # Auch Partitionen pruefen: /dev/sda blockiert, falls /dev/sda1 mounted
        dev_name = os.path.basename(dev)
        for m in mounted:
            m_name = os.path.basename(m)
            if m_name.startswith(dev_name) and m_name != dev_name:
                blocked.append(f"{dev} (Partition {m} eingehaengt)")
                break
        else:
            if candidates & mounted:
                blocked.append(dev)
    if blocked:
        raise TestExecutionError(
            "Destruktiver Test abgebrochen: folgende Datentraeger sind "
            "eingehaengt und wuerden zerstoert werden:\n  - "
            + "\n  - ".join(blocked)
            + "\nBitte erst aushaengen (umount)."
        )


class FioRunner(BaseTestRunner):
    """Dateibasierter fio-Test mit optionaler Datenverifikation.

    Schreib-Workloads (write, randwrite, randrw) verifizieren die
    geschriebenen Daten automatisch mit CRC32C.
    """

    def __init__(
        self,
        params: TestParameters,
        filename: str,
        rw: str,
        block_size: str,
        size_mb: int,
        io_depth: int,
        num_jobs: int,
        direct: bool = True,
        ioengine: Optional[str] = None,
        verify: bool = True,
        log_fn=None,
    ) -> None:
        super().__init__(params, log_fn=log_fn)
        self.filename = str(Path(filename).expanduser())
        self.rw = (rw or "read").lower()
        self.block_size = block_size or "128k"
        self.size_mb = max(1, size_mb)
        self.io_depth = max(1, io_depth)
        self.num_jobs = max(1, num_jobs)
        self.direct = direct
        self.ioengine = ioengine or _best_ioengine()
        # Verify nur bei Workloads die schreiben
        self._has_writes = self.rw in ("write", "randwrite", "randrw", "readwrite", "rw")
        self.verify = verify and self._has_writes

    def build_command(self) -> List[str]:
        duration = self.params.duration_seconds
        cmd = [
            "fio",
            "--name=hardwaretest",
            f"--filename={self.filename}",
            f"--size={self.size_mb}M",
            f"--rw={self.rw}",
            f"--bs={self.block_size}",
            f"--runtime={duration}",
            "--time_based=1",
            f"--ioengine={self.ioengine}",
            f"--iodepth={self.io_depth}",
            f"--numjobs={self.num_jobs}",
            "--direct=1" if self.direct else "--direct=0",
            "--group_reporting",
            "--eta=always",
            "--unlink=1",
            "--create_on_open=1",
        ]
        if self.verify:
            cmd += [
                "--verify=crc32c",
                "--do_verify=1",
                "--verify_fatal=1",
            ]
        return cmd


class _FioJobFileRunner(BaseTestRunner):
    """Helper base class for runners that create temporary fio job files."""

    def __init__(
        self,
        params: TestParameters,
        log_fn=None,
        use_pkexec: bool = False,
        ioengine: Optional[str] = None,
    ) -> None:
        super().__init__(params, log_fn=log_fn)
        self._job_file: Optional[str] = None
        self._command_prefix: List[str] = []
        self._pkexec_path: Optional[str] = None
        self.ioengine = ioengine or _best_ioengine()
        if use_pkexec:
            # Skip pkexec when already running as root (e.g. Puppy Linux /
            # TrixiePup64 where the entire session runs as root).
            if os.geteuid() == 0:
                self._command_prefix = []
                self._pkexec_path = None
            else:
                pkexec_path = shutil.which("pkexec")
                if not pkexec_path:
                    raise TestExecutionError("pkexec nicht gefunden. Bitte polkit installieren.")
                self._command_prefix = [pkexec_path]
                self._pkexec_path = pkexec_path

    def build_command(self) -> List[str]:
        lines = self._job_lines()
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            prefix="hardwaretest-fio-job-",
            suffix=".fio",
        ) as job_file:
            job_file.write("\n".join(lines))
            self._job_file = job_file.name
        return [*self._command_prefix, "fio", self._job_file]

    def _job_lines(self) -> List[str]:  # pragma: no cover - abstract helper
        raise NotImplementedError

    def stop(self, aborted: bool = False) -> None:
        if aborted:
            self._aborted = True
        if self._pkexec_path:
            self._stop_with_pkexec()
        else:
            super().stop(aborted=aborted)
        self._cleanup_job_file()

    def _stream_output(self) -> None:
        super()._stream_output()
        self._cleanup_job_file()

    def _cleanup_job_file(self) -> None:
        if self._job_file:
            with contextlib.suppress(OSError):
                os.unlink(self._job_file)
            self._job_file = None

    def _stop_with_pkexec(self) -> None:
        if not self._process or self._process.poll() is not None:
            self._process = None
            self._start_time = None
            return
        self._log("Stoppe Test (pkexec)...")
        kill_cmd = [
            self._pkexec_path or "pkexec",
            "/bin/kill",
            "-TERM",
            str(self._process.pid),
        ]
        try:
            subprocess.run(kill_cmd, check=False, capture_output=True, text=True)
        except Exception as exc:  # pragma: no cover - system dependent
            self._log(f"pkexec Kill-Aufruf fehlgeschlagen: {exc}")
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._log("pkexec Kill-Timeout, versuche SIGKILL...")
            kill_cmd[-2] = "-KILL"
            try:
                subprocess.run(kill_cmd, check=False, capture_output=True, text=True)
            except Exception as exc:  # pragma: no cover
                self._log(f"pkexec Kill -KILL fehlgeschlagen: {exc}")
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._log("Prozess reagiert nicht auf Kill. Bitte ggf. manuell stoppen.")
        # Auf den Ausgabe-Thread warten, damit das Ergebnis (inkl. der bis zum
        # Abbruch gesammelten Fehler) vollstaendig ausgewertet ist, bevor die
        # GUI es abfragt.
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=5)
        if self._result is None:
            self._finalize_result()
        self._process = None
        self._start_time = None


class FioDeviceSweepRunner(_FioJobFileRunner):
    """Runs a read-only fio job across multiple block devices in parallel.

    Liest alle Bloecke jedes Geraets vollstaendig und prueft auf
    I/O-Fehler.  Bei HPE ProLiant mit SmartArray RAID werden so
    defekte Sektoren und Parity-Fehler erkannt.
    """

    def __init__(
        self,
        params: TestParameters,
        devices: List[str],
        block_size: str,
        io_depth: int,
        log_fn=None,
        use_pkexec: bool = False,
        ioengine: Optional[str] = None,
    ) -> None:
        if not devices:
            raise TestExecutionError("Keine Datenträger ausgewählt")
        self.devices = [str(Path(dev).expanduser()) for dev in devices]
        self.block_size = block_size or "1m"
        self.io_depth = max(1, io_depth)
        super().__init__(params, log_fn=log_fn, use_pkexec=use_pkexec, ioengine=ioengine)

    def _job_lines(self) -> List[str]:
        lines = [
            "[global]",
            "rw=read",
            f"bs={self.block_size}",
            "direct=1",
            f"ioengine={self.ioengine}",
            f"iodepth={self.io_depth}",
            "group_reporting=1",
            "time_based=0",
            "size=100%",
            # Continue on error: zaehlt I/O-Fehler statt abzubrechen
            # damit alle Bloecke gelesen werden und ein vollstaendiger
            # Fehlerbericht entsteht (wichtig bei RAID-Rebuilds)
            "continue_on_error=read",
            "error_dump=1",
        ]
        for idx, dev in enumerate(self.devices):
            name = Path(dev).name or f"dev{idx}"
            lines.extend(
                [
                    "",
                    f"[{name}]",
                    f"filename={dev}",
                    "numjobs=1",
                ]
            )
        return lines


class FioDestructiveRunner(_FioJobFileRunner):
    """Writes and verifies patterns across block devices (destructive).

    Schreibt Daten mit CRC32C-Checksumme und liest sie zur Verifikation
    zurueck.  Optimiert fuer HPE ProLiant Server mit grossen RAID-Arrays
    (grosser verify_backlog fuer viele GB pro Disk).
    """

    def __init__(
        self,
        params: TestParameters,
        devices: List[str],
        block_size: str,
        io_depth: int,
        passes: int = 1,
        log_fn=None,
        use_pkexec: bool = False,
        ioengine: Optional[str] = None,
    ) -> None:
        if not devices:
            raise TestExecutionError("Keine Datenträger ausgewählt")
        self.devices = [str(Path(dev).expanduser()) for dev in devices]
        # Sicherheitsnetz: keine eingehaengten Devices destruktiv beschreiben
        _check_devices_not_mounted(self.devices)
        self.block_size = block_size or "1m"
        self.io_depth = max(1, io_depth)
        self.passes = max(1, passes)
        super().__init__(params, log_fn=log_fn, use_pkexec=use_pkexec, ioengine=ioengine)

    def _job_lines(self) -> List[str]:
        lines = [
            "[global]",
            "rw=write",
            f"bs={self.block_size}",
            "direct=1",
            f"ioengine={self.ioengine}",
            f"iodepth={self.io_depth}",
            "group_reporting=1",
            "time_based=0",
            # CRC32C ist schnell und zuverlaessig fuer Datenintegritaet
            "verify=crc32c",
            "do_verify=1",
            "verify_fatal=1",
            "verify_dump=1",
            # Grosser Backlog fuer HPE RAID-Arrays (8x 1.2TB SAS etc.)
            # 16384 Bloecke = bei 1MB Blockgroesse ca. 16GB im Backlog
            "verify_backlog=16384",
            "verify_backlog_batch=4096",
            f"loops={self.passes}",
            "size=100%",
            "numjobs=1",
            "sync=0",
        ]
        for idx, dev in enumerate(self.devices):
            name = Path(dev).name or f"dev{idx}"
            lines.extend(
                [
                    "",
                    f"[{name}]",
                    f"filename={dev}",
                    "numjobs=1",
                ]
            )
        return lines
