"""Fio-based disk workload runner.

Optimiert fuer HPE ProLiant Server mit RAID-Controllern und
NVMe-Laufwerken.  Unterstuetzt io_uring (Gen10+) und libaio.
Alle Schreib-Workloads nutzen --verify fuer Datenintegritaet.
"""

from __future__ import annotations

import contextlib
from collections import deque
import errno
import functools
import os
import shutil
import subprocess
import tempfile
import time
import json
from pathlib import Path
import re
from typing import List, Optional, Sequence

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


_BUSY_FSTYPES = {"zfs_member", "linux_raid_member", "LVM2_member", "crypto_LUKS", "swap"}


def _sys_block_name(path: str, *, sys_root: str = "/sys") -> str:
    name = os.path.basename(os.path.realpath(path))
    parent = os.path.join(sys_root, "class", "block", name, "..")
    with contextlib.suppress(OSError):
        parent_name = os.path.basename(os.path.realpath(parent))
        if parent_name and parent_name != "block":
            return parent_name
    return name


def _add_with_parents(devices: set[str], dev: str, *, sys_root: str = "/sys") -> None:
    if not dev:
        return
    for candidate in {dev, os.path.realpath(dev)}:
        devices.add(candidate)
        if candidate.startswith("/dev/"):
            parent = _sys_block_name(candidate, sys_root=sys_root)
            devices.add(f"/dev/{parent}")


def _add_holders(devices: set[str], name: str, *, sys_root: str = "/sys") -> None:
    holder_dir = os.path.join(sys_root, "class", "block", name, "holders")
    with contextlib.suppress(OSError):
        for holder in os.listdir(holder_dir):
            devices.add(f"/dev/{name}")
            holder_path = f"/dev/{holder}"
            _add_with_parents(devices, holder_path, sys_root=sys_root)
            _add_holders(devices, holder, sys_root=sys_root)


def _walk_lsblk_busy(
    node: dict, devices: set[str], *, sys_root: str = "/sys", parents: tuple[str, ...] = ()
) -> None:
    name = node.get("name") or ""
    path = f"/dev/{name}" if name else ""
    mountpoints = node.get("mountpoints") or []
    if isinstance(mountpoints, str):
        mountpoints = [mountpoints]
    if path and ((node.get("fstype") in _BUSY_FSTYPES) or any(mountpoints)):
        _add_with_parents(devices, path, sys_root=sys_root)
        devices.update(parents)
    for child in node.get("children", []) or []:
        _walk_lsblk_busy(child, devices, sys_root=sys_root, parents=(*parents, path) if path else parents)


def _mounted_devices(*, proc_root: str = "/proc", sys_root: str = "/sys") -> set[str]:
    """Return block devices that are mounted, swapped or otherwise in use."""
    mounts: set[str] = set()
    try:
        with open(os.path.join(proc_root, "mounts")) as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                dev = parts[0]
                if not dev.startswith("/dev/"):
                    continue
                _add_with_parents(mounts, dev, sys_root=sys_root)
    except OSError:
        pass
    try:
        with open(os.path.join(proc_root, "swaps")) as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if parts and parts[0].startswith("/dev/"):
                    _add_with_parents(mounts, parts[0], sys_root=sys_root)
    except OSError:
        pass
    with contextlib.suppress(OSError):
        for name in os.listdir(os.path.join(sys_root, "block")):
            _add_holders(mounts, name, sys_root=sys_root)
            class_dir = os.path.join(sys_root, "class", "block")
            with contextlib.suppress(OSError):
                for child in os.listdir(os.path.join(class_dir, name)):
                    if child.startswith(name):
                        _add_holders(mounts, child, sys_root=sys_root)
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,MOUNTPOINTS,FSTYPE"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        data = json.loads(result.stdout or "{}")
        for node in data.get("blockdevices", []) or []:
            _walk_lsblk_busy(node, mounts, sys_root=sys_root)
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return mounts


def _device_busy_by_exclusive_open(dev: str) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_EXCL", 0)
    try:
        fd = os.open(dev, flags)
    except OSError as exc:
        return exc.errno == errno.EBUSY
    else:
        os.close(fd)
    return False


def _check_devices_not_mounted(devices: List[str], *, proc_root: str = "/proc", sys_root: str = "/sys") -> None:
    """Wirft TestExecutionError, falls eines der *devices* eingehaengt ist.

    Schuetzt vor versehentlichem Ueberschreiben aktiver Filesysteme im
    destruktiven Modus.
    """
    try:
        mounted = _mounted_devices(proc_root=proc_root, sys_root=sys_root)
    except TypeError:
        mounted = _mounted_devices()
    blocked: List[str] = []
    for dev in devices:
        candidates = {dev, f"/dev/{_sys_block_name(dev, sys_root=sys_root)}"}
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
        if _device_busy_by_exclusive_open(dev):
            blocked.append(f"{dev} (Kernel meldet busy)")
    if blocked:
        raise TestExecutionError(
            "Destruktiver Test abgebrochen: folgende Datentraeger sind "
            "eingehaengt und wuerden zerstoert werden:\n  - "
            + "\n  - ".join(blocked)
            + "\nBitte erst aushaengen (umount)."
        )


# Startet fio als root und beendet es, sobald die stdin-Pipe der App schliesst
# (Stopp ohne erneute pkexec-Autorisierung). sh leitet stdin von
# Hintergrundjobs nach /dev/null um; deshalb die Pipe vorher auf fd 3 sichern.
_FIO_ETA_PERCENT = re.compile(r"^Jobs:.*?\[(\d+(?:\.\d+)?)%\]")

# Wird nach erfolgreicher pkexec-Autorisierung ausgegeben, damit die App
# Laufzeit und Fortschritt erst ab dann zaehlt.
AUTHORIZED_MARKER = "@@HARDWARETEST-AUTHORIZED@@"

# fio laeuft in einer eigenen Prozessgruppe (setsid); Stopp per stdin-EOF oder
# SIGTERM an den Wrapper beendet die ganze Gruppe, nach Frist mit SIGKILL.
PKEXEC_STOP_WRAPPER = (
    'echo "' + AUTHORIZED_MARKER + '"; exec 3<&0; '
    'if command -v setsid >/dev/null 2>&1; then setsid fio "$@" </dev/null & '
    'else fio "$@" </dev/null & fi; pid=$!; '
    'stop() { kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null; '
    '(sleep 8; kill -KILL "-$pid" 2>/dev/null; kill -KILL "$pid" 2>/dev/null) & }; '
    'trap stop TERM INT HUP; '
    '(read _ <&3; kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null) & stopper=$!; '
    'while :; do wait "$pid"; status=$?; kill -0 "$pid" 2>/dev/null || break; done; '
    'kill -KILL "-$pid" 2>/dev/null; kill "$stopper" 2>/dev/null; exit "$status"'
)

# Platzhalter statt lokaler Jobdatei, wenn die Root-Sitzung die Jobdatei selbst schreibt.
_BROKER_JOB_PLACEHOLDER = "@@broker-job@@"


def _job_tokens(lines: Sequence[str]) -> List[str]:
    """Jobdatei-Zeilen in Broker-Tokens: ``[name]`` → ``@name``, ``key=value`` unverändert."""
    tokens: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            tokens.append("@" + line[1:-1])
        elif "=" in line and not any(ch.isspace() for ch in line):
            tokens.append(line)
        else:
            raise TestExecutionError(f"Ungültige fio-Jobzeile: {line!r}")
    return tokens


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
        # Verifizierende Jobs duerfen sich nicht ueberschreiben: disjunkte Bereiche
        # innerhalb der Gesamtgroesse (size_mb bleibt die belegte Gesamtmenge).
        if self.verify and self.num_jobs > 1:
            self.num_jobs = min(self.num_jobs, self.size_mb)
            self.job_size_mb = max(1, self.size_mb // self.num_jobs)
        else:
            self.job_size_mb = self.size_mb

    @property
    def total_size_mb(self) -> int:
        """Tatsaechlich belegte Dateigroesse ueber alle Jobs."""
        return self.job_size_mb * self.num_jobs if self.verify else self.size_mb

    def estimated_verify_seconds(self) -> float:
        """Grobe obere Schranke fuer die Verify-Phase (konservativ 20 MB/s)."""
        if not self.verify:
            return 0.0
        return max(30.0, self.total_size_mb / 20.0)

    @property
    def watchdog_slack_seconds(self) -> float:  # type: ignore[override]
        # fio begrenzt sich per --runtime selbst; der Watchdog ist nur Notbremse
        # und darf fio nicht kurz vor Abschluss oder waehrend des Verify beenden.
        duration = max(0, self.params.duration_seconds)
        return max(60.0, 0.25 * duration) + self.estimated_verify_seconds()

    def build_command(self) -> List[str]:
        duration = self.params.duration_seconds
        cmd = [
            "fio",
            "--name=hardwaretest",
            f"--filename={self.filename}",
            f"--size={self.job_size_mb}M",
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
            if self.num_jobs > 1:
                cmd.append(f"--offset_increment={self.job_size_mb}M")
            cmd += [
                "--verify=crc32c",
                "--do_verify=1",
                "--verify_fatal=1",
            ]
        return cmd

    def _submit_to_broker(self, cmd: List[str]) -> str:
        """CLI-Optionen in geprüfte Broker-Tokens umsetzen (kein freies ``fio``)."""
        name = "hardwaretest"
        flags: List[str] = []
        options: List[str] = []
        for arg in cmd[1:]:
            if arg.startswith("--name="):
                name = arg[len("--name="):]
            elif arg == "--group_reporting":
                options.append("group_reporting=1")
            elif arg.startswith("--eta="):
                flags.append(arg)
            elif arg.startswith("--") and "=" in arg:
                options.append(arg[2:])
            else:
                raise TestExecutionError(f"fio-Option nicht über die Root-Sitzung erlaubt: {arg}")
        return self.root_broker.submit_fio_job([*flags, f"@{name}", *options])


class _FioJobFileRunner(BaseTestRunner):
    """Helper base class for runners that create temporary fio job files."""

    # Fortschritt aus fio-ETA-Zeilen statt aus einer Zeitschaetzung ableiten.
    _eta_progress: bool = False
    # fio begrenzt die Laufzeit selbst (runtime/size); ein externer Watchdog
    # wuerde fio sonst vor der JSON-Ausgabe beenden – inklusive der Zeit,
    # in der pkexec noch auf das Kennwort wartet.
    _use_watchdog = False

    def __init__(
        self,
        params: TestParameters,
        log_fn=None,
        use_pkexec: bool = False,
        ioengine: Optional[str] = None,
    ) -> None:
        super().__init__(params, log_fn=log_fn)
        self._job_file: Optional[str] = None
        self._job_dir: Optional[str] = None
        self._eta_fraction = 0.0
        self._command_prefix: List[str] = []
        self._pkexec_path: Optional[str] = None
        self.authorized = True
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
        if self.root_broker is not None:
            # Die Root-Sitzung schreibt die Jobdatei selbst aus geprüften Tokens.
            return ["fio", *self._extra_fio_args(), _BROKER_JOB_PLACEHOLDER]
        lines = self._job_lines()
        self._cleanup_job_file()
        # Privates Verzeichnis (0700) und Datei (0600): kein Austausch der Jobdatei
        # durch andere Benutzer, bevor fio sie (ggf. als root) liest.
        self._job_dir = tempfile.mkdtemp(prefix="hardwaretest-fio-job-")
        self._job_file = os.path.join(self._job_dir, "job.fio")
        fd = os.open(self._job_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as job_file:
            job_file.write("\n".join(lines))
        command = ["fio", *self._extra_fio_args(), self._job_file]
        if self._pkexec_path:
            script = PKEXEC_STOP_WRAPPER
            return [self._pkexec_path, "sh", "-c", script, "hardwaretest-fio", *command[1:]]
        return [*self._command_prefix, *command]

    def _submit_to_broker(self, cmd: List[str]) -> str:
        if cmd[-1] != _BROKER_JOB_PLACEHOLDER or cmd[0] != "fio":
            raise TestExecutionError("fio-Befehl nicht für die Root-Sitzung geeignet")
        tokens = [*cmd[1:-1], *_job_tokens(self._job_lines())]
        return self.root_broker.submit_fio_job(tokens)

    def start(self) -> None:
        self.authorized = not self._pkexec_path
        super().start()

    def _handle_auth_marker(self, line: str) -> bool:
        if line.strip() != AUTHORIZED_MARKER:
            return False
        self.authorized = True
        self._start_time = time.time()
        return True

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

    def _extra_fio_args(self) -> List[str]:
        if not self._eta_progress:
            return []
        return ["--eta=always"]

    def progress(self) -> float:
        if not self._eta_progress:
            return super().progress()
        if self.get_result() is not None and not self.is_running():
            return 1.0
        return self._eta_fraction

    def _stream_output(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            for fragment in line.replace("\r", "\n").splitlines():
                stripped = fragment.rstrip()
                if not stripped or self._handle_auth_marker(stripped):
                    continue
                if self._eta_progress and stripped.startswith("Jobs:"):
                    # ETA-Zeilen nur fuer den Fortschritt auswerten, nicht loggen.
                    match = _FIO_ETA_PERCENT.search(stripped)
                    if match:
                        value = max(0.0, min(100.0, float(match.group(1)))) / 100.0
                        self._eta_fraction = max(self._eta_fraction, value)
                    continue
                self._log(stripped)
                self._check_line_for_errors(stripped)
        self._process.wait()
        self._finalize_result()
        if self._result:
            self._log(f"Test beendet – Ergebnis: {self._result.status_text}")
        self._cleanup_job_file()

    def _cleanup_job_file(self) -> None:
        if self._job_file:
            with contextlib.suppress(OSError):
                os.unlink(self._job_file)
            self._job_file = None
        if self._job_dir:
            with contextlib.suppress(OSError):
                os.rmdir(self._job_dir)
            self._job_dir = None

    def _stop_with_pkexec(self) -> None:
        if not self._process or self._process.poll() is not None:
            self._process = None
            self._start_time = None
            return
        if self._process.stdin:
            with contextlib.suppress(OSError):
                self._process.stdin.close()
            try:
                self._process.wait(timeout=10)
                if self._stdout_thread is not None:
                    self._stdout_thread.join(timeout=5)
                if self._result is None:
                    self._finalize_result()
                self._process = None
                self._start_time = None
                return
            except subprocess.TimeoutExpired:
                pass
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

    _eta_progress = True

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


class FioNvmeReadBenchmarkRunner(_FioJobFileRunner):
    """Timed, read-only fio benchmark for one NVMe namespace.

    It never writes to the drive.  The JSON result is retained so the UI can
    report throughput and IOPS instead of merely showing fio's raw log.
    """

    def __init__(
        self,
        params: TestParameters,
        device: str | Sequence[str],
        rw: str = "read",
        block_size: str = "1m",
        io_depth: int = 32,
        log_fn=None,
        use_pkexec: bool = False,
        ioengine: Optional[str] = None,
    ) -> None:
        if rw not in {"read", "randread"}:
            raise TestExecutionError("Nur lesende NVMe-Benchmarks sind erlaubt")
        raw_devices = [device] if isinstance(device, str) else list(device)
        if not raw_devices:
            raise TestExecutionError("Keine NVMe-Laufwerke ausgewählt")
        self.devices = [str(Path(item).expanduser()) for item in raw_devices]
        self.device = self.devices[0]
        self.rw = rw
        self.block_size = block_size or "1m"
        self.io_depth = max(1, io_depth)
        self.output_lines: List[str] = []
        super().__init__(params, log_fn=log_fn, use_pkexec=use_pkexec, ioengine=ioengine)

    def _job_lines(self) -> List[str]:
        lines = [
            "[global]",
            f"rw={self.rw}",
            f"bs={self.block_size}",
            "direct=1",
            f"ioengine={self.ioengine}",
            f"iodepth={self.io_depth}",
            "numjobs=1",
            "group_reporting=0",
            "time_based=1",
            f"runtime={max(1, self.params.duration_seconds)}",
            "size=100%",
            "continue_on_error=read",
            "error_dump=1",
        ]
        for index, device in enumerate(self.devices):
            lines.extend([
                "",
                f"[nvme_read_benchmark_{index + 1}_{Path(device).name}]",
                f"filename={device}",
            ])
        return lines

    def build_command(self) -> List[str]:
        """Request JSON through fio's CLI for fio 3.x compatibility."""
        command = super().build_command()
        return [*command[:-1], "--output-format=json", command[-1]]

    def _stream_output(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            stripped = line.rstrip()
            if self._handle_auth_marker(stripped):
                continue
            self.output_lines.append(stripped)
            self._log(stripped)
            self._check_line_for_errors(stripped)
        self._process.wait()
        self._finalize_result()
        self._cleanup_job_file()

    def summary(self) -> Optional[dict[str, float]]:
        summaries = self.device_summaries()
        if not summaries:
            return None
        total_ios = sum(item["total_ios"] for item in summaries)
        if total_ios:
            latency_ms = sum(
                item["latency_ms"] * item["total_ios"] for item in summaries
            ) / total_ios
        else:
            latency_ms = sum(item["latency_ms"] for item in summaries) / len(summaries)
        return {
            "throughput_mib_s": sum(item["throughput_mib_s"] for item in summaries),
            "iops": sum(item["iops"] for item in summaries),
            "latency_ms": latency_ms,
        }

    def device_summaries(self) -> list[dict[str, float | str]]:
        """Return one benchmark result per selected namespace."""
        try:
            payload = json.loads("\n".join(self.output_lines))
            summaries: list[dict[str, float | str]] = []
            for index, job in enumerate(payload["jobs"]):
                read = job["read"]
                device = self.devices[index] if index < len(self.devices) else str(job.get("jobname", "NVMe"))
                summaries.append({
                    "device": device,
                    "throughput_mib_s": float(read.get("bw_bytes", 0)) / (1024 * 1024),
                    "iops": float(read.get("iops", 0)),
                    "latency_ms": float(read.get("lat_ns", {}).get("mean", 0)) / 1_000_000,
                    "total_ios": float(read.get("total_ios", 0)),
                })
            return summaries
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return []


class FioNvmeFullReadRunner(_FioJobFileRunner):
    """Read every block of exactly one NVMe namespace, without writing data.

    This deliberately does not reuse a generic disk-workload runner.  Its job
    file has a fixed ``rw=read`` setting and contains no verification options,
    so a UI change cannot accidentally turn this into a write operation.
    """

    device_pattern = r"/dev/nvme\d+n\d+"
    job_prefix = "nvme_full_read"
    no_device_error = "Keine NVMe-Laufwerke ausgewählt"
    wrong_device_error = "Nur NVMe-Namespace-Geräte sind erlaubt"

    def __init__(
        self,
        params: TestParameters,
        device: str | Sequence[str],
        block_size: str = "1m",
        io_depth: int = 32,
        log_fn=None,
        use_pkexec: bool = False,
        ioengine: Optional[str] = None,
        total_bytes: int = 0,
    ) -> None:
        raw_devices = [device] if isinstance(device, str) else list(device)
        if not raw_devices:
            raise TestExecutionError(self.no_device_error)
        self.devices = [str(Path(item).expanduser()) for item in raw_devices]
        if any(not re.fullmatch(self.device_pattern, path) for path in self.devices):
            raise TestExecutionError(self.wrong_device_error)
        self.output_lines = deque(maxlen=80)
        self.device = self.devices[0]
        self.block_size = block_size or "1m"
        self.io_depth = max(1, io_depth)
        self._reported_progress = 0.0
        self._total_bytes = max(0, total_bytes)
        self._current_group = 0
        self._bytes_by_group: dict[int, int] = {}
        super().__init__(params, log_fn=log_fn, use_pkexec=use_pkexec, ioengine=ioengine)

    def build_command(self) -> List[str]:
        """Ask fio for periodic, newline-terminated progress reports."""
        command = super().build_command()
        return [
            *command[:-1],
            "--readonly",
            "--eta=always",
            "--eta-interval=1000",
            "--status-interval=1",
            command[-1],
        ]

    def progress(self) -> float:
        if self.get_result() is not None and not self.is_running():
            return 1.0
        return self._reported_progress

    def _check_line_for_errors(self, line: str) -> None:
        super()._check_line_for_errors(line)
        # Preserve both the first and most recent errors without keeping a line
        # for every failed block of a large, failing drive in memory.
        if len(self._collected_errors) > 200:
            del self._collected_errors[100]

    def _record_progress(self, output: str) -> None:
        """Extract progress from fio ETA or periodic per-job byte counters."""
        group = re.search(r"\bgroupid=(\d+)", output)
        if group:
            self._current_group = int(group.group(1))

        byte_values = re.findall(
            r"\((\d+(?:\.\d+)?)([KMGTPE]?i?[bB])/\d", output
        )
        if byte_values and self._total_bytes:
            value, unit = byte_values[-1]
            self._bytes_by_group[self._current_group] = _fio_size_to_bytes(float(value), unit)
            byte_progress = sum(self._bytes_by_group.values()) / self._total_bytes
            self._reported_progress = max(
                self._reported_progress, max(0.0, min(1.0, byte_progress))
            )

        for match in re.finditer(r"\[\s*(\d+(?:\.\d+)?)%\s*(?:done)?\]", output):
            value = max(0.0, min(100.0, float(match.group(1)))) / 100.0
            self._reported_progress = max(self._reported_progress, value)

    def _stream_output(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            # fio updates its ETA line with carriage returns.  Periodic status
            # reports add newlines, so both forms can arrive in one chunk.
            self._record_progress(line)
            for fragment in line.replace("\r", "\n").splitlines():
                stripped = fragment.rstrip()
                if stripped and not self._handle_auth_marker(stripped):
                    self.output_lines.append(stripped)
                    self._log(stripped)
                    self._check_line_for_errors(stripped)
        self._process.wait()
        self._finalize_result()
        if self._result and self._result.passed:
            self._reported_progress = 1.0
        self._cleanup_job_file()

    def _job_lines(self) -> List[str]:
        lines = [
            "[global]",
            "rw=read",
            "allow_file_create=0",
            f"bs={self.block_size}",
            "direct=1",
            f"ioengine={self.ioengine}",
            f"iodepth={self.io_depth}",
            "numjobs=1",
            "group_reporting=0",
            "time_based=0",
            "size=100%",
            "continue_on_error=read",
            "error_dump=1",
        ]
        for index, device in enumerate(self.devices):
            lines.extend([
                "",
                f"[{self.job_prefix}_{index + 1}_{Path(device).name}]",
                f"filename={device}",
            ])
        return lines


class FioDiskFullReadRunner(FioNvmeFullReadRunner):
    """Vollständiger Lesetest für SATA-/SAS-/USB-Datenträger (``/dev/sd*``), ohne Schreibzugriffe."""

    device_pattern = r"/dev/sd[a-z]+"
    job_prefix = "disk_full_read"
    no_device_error = "Keine Datenträger ausgewählt"
    wrong_device_error = "Nur ganze /dev/sd*-Datenträger sind erlaubt"


def _fio_size_to_bytes(value: float, unit: str) -> int:
    normalized = unit.lower()
    if normalized == "b":
        factor = 1
    else:
        exponent = "kmgtpe".index(normalized[0]) + 1
        factor = (1024 if "i" in normalized else 1000) ** exponent
    return int(value * factor)


class FioDestructiveRunner(_FioJobFileRunner):
    """Writes and verifies patterns across block devices (destructive).

    Schreibt Daten mit CRC32C-Checksumme und liest sie zur Verifikation
    zurueck.  Optimiert fuer HPE ProLiant Server mit grossen RAID-Arrays
    (grosser verify_backlog fuer viele GB pro Disk).
    """

    _eta_progress = True

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
