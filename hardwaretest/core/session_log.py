"""Sitzungsprotokoll („Flugschreiber“): eine fortlaufende Zeitachse aller Tests.

Jede Zeile hat Zeitstempel und Quelle: ``2026-11-13T18:16:04.123+01:00 | stress-ng | text``.
Testausgaben, Start/Ende, Kernel-Fehler und alle 5 s eine Zustandszeile (Last,
Temperaturen, Lüfter, ECC, laufende Tests) landen in stündlich wechselnden
Dateien unter ``~/Downloads/hwtest-protokoll``. Wichtige Zeilen werden sofort,
alle übrigen spätestens nach einer Sekunde per ``fsync`` gesichert – friert der
Rechner ein, zeigt die letzte Zeile den Zustand kurz davor. Ohne ``start()``
(z. B. in Unit-Tests) sind alle Funktionen wirkungslos.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Iterator, Optional

import psutil

from hardwaretest import __version__

SESSION = "SITZUNG"
STATE = "ZUSTAND"
KERNEL = "KERNEL"
GPU = "GPU"
SENSORS = "SENSOREN"
PREFIX = "hwtest-protokoll-"
MIN_RETENTION_HOURS = 24
DEFAULT_RETENTION_HOURS = 72
_SEPARATOR = " | "
_EDAC = Path("/sys/devices/system/edac/mc")


def default_directory() -> Path:
    return Path.home() / "Downloads" / "hwtest-protokoll"


def retention_hours() -> int:
    """Vorhaltezeit; ``HARDWARETEST_LOG_HOURS`` überschreibt, nie unter 24 h."""
    try:
        value = int(os.environ.get("HARDWARETEST_LOG_HOURS", DEFAULT_RETENTION_HOURS))
    except ValueError:
        value = DEFAULT_RETENTION_HOURS
    return max(MIN_RETENTION_HOURS, value)


@dataclass
class LogLine:
    time: datetime
    source: str
    text: str


@dataclass
class UncleanSession:
    """Vorherige Sitzung ohne sauberes Ende (Absturz, Einfrieren, Ausschalten)."""

    last_time: datetime
    active_tests: str
    last_lines: list[str] = field(default_factory=list)
    file: Optional[Path] = None


def parse_line(raw: str) -> Optional[LogLine]:
    parts = raw.rstrip("\n").split(_SEPARATOR, 2)
    if len(parts) != 3:
        return None
    try:
        return LogLine(datetime.fromisoformat(parts[0]), parts[1], parts[2])
    except ValueError:
        return None


def log_files(directory: Path) -> list[Path]:
    return sorted(Path(directory).glob(PREFIX + "*.log"))


def read_lines(directory: Path, since: datetime) -> Iterator[tuple[LogLine, str]]:
    """Zeilen ab ``since`` aus allen in Frage kommenden Stundendateien."""
    first = PREFIX + (since - timedelta(hours=1)).strftime("%Y%m%d-%H") + ".log"
    for path in log_files(directory):
        if path.name < first:
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                for raw in stream:
                    line = parse_line(raw)
                    if line is not None and line.time >= since:
                        yield line, raw.rstrip("\n")
        except OSError:
            continue


def find_unclean(directory: Path) -> Optional[UncleanSession]:
    """Prüft, ob die letzte Sitzung ohne ``SITZUNG | ENDE`` aufhörte."""
    files = log_files(directory)
    if not files:
        return None
    try:
        raws = files[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    parsed = [(parse_line(raw), raw) for raw in raws]
    parsed = [(line, raw) for line, raw in parsed if line is not None]
    if not parsed:
        return None
    last = parsed[-1][0]
    if last.source == SESSION and last.text.startswith("ENDE"):
        return None
    active = ""
    for line, _raw in reversed(parsed):
        if line.source == STATE:
            match = re.search(r"aktiv=\[([^\]]*)\]", line.text)
            active = match.group(1) if match else ""
            break
        if line.source == SESSION:
            break  # Sitzungsbeginn erreicht: noch keine Zustandszeile geschrieben
    tail = [raw for line, raw in parsed if line.source != STATE][-8:]
    return UncleanSession(last.time, active, tail, files[-1])


def _edac_counts() -> Optional[tuple[int, int]]:
    ce = ue = 0
    found = False
    for controller in _EDAC.glob("mc[0-9]*"):
        try:
            ce += int((controller / "ce_count").read_text())
            ue += int((controller / "ue_count").read_text())
            found = True
        except (OSError, ValueError):
            continue
    return (ce, ue) if found else None


def collect_state() -> str:
    """Billige Momentaufnahme ohne Root-Rechte (psutil, sysfs)."""
    parts = [f"cpu={psutil.cpu_percent(interval=None):.0f}%"]
    with contextlib.suppress(OSError, AttributeError):
        parts.append("last=" + "/".join(f"{value:.1f}" for value in os.getloadavg()))
    with contextlib.suppress(Exception):
        memory = psutil.virtual_memory()
        parts.append(f"ram={memory.percent:.0f}%")
        swap = psutil.swap_memory()
        if swap.total:
            parts.append(f"swap={swap.percent:.0f}%")
    with contextlib.suppress(Exception):
        frequency = psutil.cpu_freq()
        if frequency and frequency.current:
            parts.append(f"takt={frequency.current:.0f}MHz")
    with contextlib.suppress(Exception):
        for chip, sensors in sorted(psutil.sensors_temperatures().items()):
            values = [sensor.current for sensor in sensors if sensor.current is not None]
            if values:
                parts.append(f"temp[{chip}]={max(values):.0f}C")
    with contextlib.suppress(Exception):
        for chip, sensors in sorted(psutil.sensors_fans().items()):
            for index, sensor in enumerate(sensors):
                parts.append(f"fan[{chip}.{sensor.label or index}]={sensor.current:.0f}rpm")
    counts = _edac_counts()
    if counts is not None:
        parts.append(f"ecc_ce={counts[0]} ecc_ue={counts[1]}")
    return " ".join(parts)


class _IpmiSampler:
    """iLO-/BMC-Lüfter und höchste BMC-Temperatur; langsam, daher eigener Takt."""

    interval = 30.0
    retry_after_failure = 300.0

    def __init__(self) -> None:
        self.text = ""
        self.note = ""
        self.temperatures: list[str] = []
        self.fans: list[str] = []
        self.other: list[str] = []
        self._next = 0.0

    def due(self) -> bool:
        return time.monotonic() >= self._next

    def sample(self) -> None:
        from hardwaretest.core import ipmi

        self._next = time.monotonic() + self.interval
        if not ipmi.ipmi_available():
            self._next = float("inf")
            return
        run = ipmi._noninteractive_runner()
        sensors, error = ipmi.read_sensors(run, timeout=60) if run is not None else (None, "kein Root-Zugriff")
        if sensors is None:
            self.text = ""
            self.temperatures, self.fans, self.other = [], [], []
            self.note = "BMC-Sensoren nicht lesbar (ohne Passwort): " + " ".join(error.split())[:120]
            self._next = time.monotonic() + self.retry_after_failure
            return
        self.note = ""
        parts = []
        hottest: Optional[tuple[float, str]] = None
        for sensor in ipmi.environment_sensors(sensors):
            reading = ipmi._numeric_sdr_reading(sensor.reading)
            if reading is None:
                continue
            value, unit = reading
            kind = ipmi.sensor_kind(sensor)
            if kind == "fan" and unit in {"%", "rpm"}:
                parts.append(f"bmc_fan[{sensor.name}]={value:g}{unit}")
            elif kind == "temperature" and (hottest is None or value > hottest[0]):
                hottest = (value, sensor.name)
        if hottest is not None:
            parts.append(f"bmc_temp_max[{hottest[1]}]={hottest[0]:g}C")
        self.text = " ".join(parts)
        # Vollständige Liste für die Sensorzeile: alle BMC-Temperaturen, Lüfter und Leistungswerte.
        self.temperatures, self.fans, self.other = [], [], []
        for sensor in ipmi.environment_sensors(sensors):
            reading = ipmi._numeric_sdr_reading(sensor.reading)
            if reading is None:
                continue
            value, unit = reading
            entry = f"bmc[{sensor.name}]={value:g}{unit.replace('°', '').replace('watts', 'W')}"
            kind = ipmi.sensor_kind(sensor)
            (self.temperatures if kind == "temperature" else self.fans if kind == "fan" else self.other).append(entry)


def collect_sensor_details() -> tuple[list[str], list[str]]:
    """Alle einzelnen Temperaturen und Lüfter aus hwmon (psutil), ersatzweise ACPI-Lüfterstufen."""
    temperatures: list[str] = []
    fans: list[str] = []
    with contextlib.suppress(Exception):
        for chip, sensors in sorted(psutil.sensors_temperatures().items()):
            for index, sensor in enumerate(sensors):
                if sensor.current is not None:
                    temperatures.append(f"temp[{chip}.{sensor.label or index}]={sensor.current:.0f}C")
    with contextlib.suppress(Exception):
        for chip, sensors in sorted(psutil.sensors_fans().items()):
            for index, sensor in enumerate(sensors):
                fans.append(f"fan[{chip}.{sensor.label or index}]={sensor.current:.0f}rpm")
    if not fans:
        with contextlib.suppress(Exception):
            from hardwaretest.core.monitoring import acpi_fan_metrics

            fans = [f"fan[acpi.{key.split('.')[2]}]={value:g}%" for key, value in sorted(acpi_fan_metrics().items())]
    return temperatures, fans


def _mib(value) -> Optional[int]:
    return round(value / (1024 * 1024)) if isinstance(value, (int, float)) else None


def format_gpu_snapshot(data: dict) -> tuple[str, dict]:
    """Kompakte Protokollzeile aus ``gputaskman --json`` und die Merkmale für Ereignisse."""
    device = data.get("device") or {}
    identity = data.get("gpu_identity") or {}
    index = identity.get("index", device.get("index", "?"))
    parts = [f"gpu{index}[{device.get('name') or data.get('gpu') or '?'}]"]

    def add(label: str, key: str, unit: str = "") -> None:
        value = device.get(key)
        if isinstance(value, (int, float)):
            parts.append(f"{label}={value:g}{unit}")

    add("last", "util_gpu", "%")
    add("speicherlast", "util_mem", "%")
    used = _mib(device.get("mem_used") if device.get("mem_used") is not None else data.get("vram_used"))
    total = _mib(device.get("mem_total") if device.get("mem_total") is not None else data.get("vram_total"))
    if used is not None:
        parts.append(f"vram={used}" + (f"/{total}" if total is not None else "") + "MiB")
    add("temp", "temp", "C")
    power, limit = device.get("power_w"), device.get("power_limit_w")
    if isinstance(power, (int, float)):
        parts.append(f"leistung={power:g}" + (f"/{limit:g}" if isinstance(limit, (int, float)) else "") + "W")
    add("fan", "fan", "%")
    add("takt", "gfx_clock", "MHz")
    link = None
    if device.get("pcie_gen") is not None or device.get("pcie_width") is not None:
        link = f"gen{device.get('pcie_gen', '?')}x{device.get('pcie_width', '?')}"
        parts.append(f"pcie={link}")
    throttle = [str(item) for item in device.get("throttle") or []]
    if throttle:
        parts.append("drossel=[" + ", ".join(throttle) + "]")
    top = sorted((p for p in data.get("processes") or [] if isinstance(p, dict)),
                 key=lambda p: (p.get("vram_bytes") or 0, p.get("gpu_pct") or 0), reverse=True)[:3]
    if top:
        parts.append("top=[" + "; ".join(
            f"{p.get('name', '?')}"
            + (f" {_mib(p['vram_bytes'])}MiB" if p.get("vram_bytes") is not None else "")
            + (f" {p['gpu_pct']:.0f}%" if isinstance(p.get("gpu_pct"), (int, float)) else "")
            for p in top) + "]")
    return " ".join(parts), {"throttle": tuple(throttle), "link": link}


def _gpu_sampler_binary() -> Optional[str]:
    # Installed launchers can invoke pkexec. Automatic logging must only use
    # the known direct collectors, with the current user's existing privileges.
    for candidate in ('/opt/gputaskman-qt6/collector/gputaskman-collector',
                      '/opt/gputaskman/gputaskman'):
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


class _GpuSampler:
    """GPU-Werte über direkte Collector-Binaries, ohne automatische Rechteabfrage."""

    interval = 30.0
    retry_after_failure = 300.0

    def __init__(self, write: Callable[..., None], binary: Optional[str] = None) -> None:
        self._write = write
        self.binary = str(binary) if binary is not None else _gpu_sampler_binary()
        self._qt_collector = bool(self.binary and Path(self.binary).name == "gputaskman-collector")
        self._indexes: Optional[list[str]] = None
        self._previous: dict[str, dict] = {}
        self._next = 0.0 if self.binary else float("inf")
        self._noted = ""

    def due(self) -> bool:
        return time.monotonic() >= self._next

    def _run(self, *args: str) -> str:
        env = {key: value for key, value in os.environ.items()
               if key not in {"LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH"}}  # PyInstaller-Umgebung
        result = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=45,
                                stdin=subprocess.DEVNULL, env=env)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip()[:160] or f"Exit {result.returncode}")
        return result.stdout

    def sample(self) -> None:
        self._next = time.monotonic() + self.interval
        try:
            if self._qt_collector:
                frame = json.loads(self._run("--once"))
                if not isinstance(frame, dict) or frame.get("protocol") != 2 or not isinstance(frame.get("snapshots"), list):
                    raise ValueError("Ungültige Collector-Antwort")
                readings = []
                for snapshot in frame["snapshots"]:
                    if not isinstance(snapshot, dict):
                        raise ValueError("Ungültiger GPU-Datensatz")
                    identity = snapshot.get("gpu_identity") or {}
                    index = identity.get("index", "?")
                    key = snapshot.get("gpu_id") or identity.get("id") or str(index)
                    readings.append((str(key), str(index), snapshot))
                if not readings:
                    raise RuntimeError("Keine GPU-Messwerte mit aktuellen Benutzerrechten verfügbar")
            else:
                if self._indexes is None:
                    self._indexes = re.findall(r"^\s*(\d+):", self._run("--list-gpus"), re.M)
                    if not self._indexes:
                        self._next = float("inf")
                        return
                readings = [(index, index, json.loads(self._run("--json", "--no-system", "--gpu", index)))
                            for index in self._indexes]
            for key, index, snapshot in readings:
                line, facts = format_gpu_snapshot(snapshot)
                self._write(GPU, line, important=True)
                before = self._previous.get(key)
                if facts["throttle"] and (before is None or before["throttle"] != facts["throttle"]):
                    self._write(GPU, f"EREIGNIS: gpu{index} gedrosselt: " + ", ".join(facts["throttle"]), important=True)
                elif before is not None and before["throttle"] and not facts["throttle"]:
                    self._write(GPU, f"EREIGNIS: gpu{index} Drosselung beendet", important=True)
                if before is not None and before["link"] != facts["link"]:
                    self._write(GPU, f"EREIGNIS: gpu{index} PCIe-Verbindung geändert: {before['link']} -> {facts['link']}",
                                important=True)
                self._previous[key] = facts
            self._noted = ""
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            note = "gputaskman nicht auswertbar: " + " ".join(str(exc).split())[:160]
            if note != self._noted:
                self._write(GPU, note)
            self._noted = note
            self._next = time.monotonic() + self.retry_after_failure


class SessionLog:
    def __init__(self, directory: Path, *, retention: Optional[int] = None, state_interval: float = 5.0,
                 sync_interval: float = 1.0, clock: Callable[[], datetime] = datetime.now,
                 sample_hardware: bool = True, sensor_interval: float = 30.0) -> None:
        self.directory = Path(directory)
        self.retention = retention if retention is not None else retention_hours()
        self.state_interval = state_interval
        self.sensor_interval = max(sensor_interval, state_interval)
        self.sync_interval = sync_interval
        self._clock = clock
        self._sample_hardware = sample_hardware
        self._lock = threading.RLock()
        self._file = None
        self._file_key = ""
        self._dirty = False
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._active: dict[str, str] = {}
        self.unclean: Optional[UncleanSession] = None

    # ----- Lebenszyklus -----

    def start(self) -> Optional[UncleanSession]:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.unclean = find_unclean(self.directory)
        self.purge()
        self.write(SESSION, f"START Hardwaretest {__version__} auf {platform.node() or '?'} "
                            f"(Kernel {platform.release()}, PID {os.getpid()})", important=True)
        if self.unclean is not None:
            self.write(SESSION, "Vorherige Sitzung endete ohne sauberes Ende um "
                       + self.unclean.last_time.isoformat(timespec="seconds")
                       + (f"; aktiv: {self.unclean.active_tests}" if self.unclean.active_tests else ""),
                       important=True)
        for target in (self._sync_loop, self._state_loop):
            thread = threading.Thread(target=target, daemon=True, name="hwtest-" + target.__name__)
            thread.start()
            self._threads.append(thread)
        return self.unclean

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=10)
        self._threads = []
        self.write(SESSION, "ENDE", important=True)
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def purge(self) -> None:
        limit = PREFIX + (self._clock() - timedelta(hours=self.retention)).strftime("%Y%m%d-%H") + ".log"
        for path in log_files(self.directory):
            if path.name < limit:
                with contextlib.suppress(OSError):
                    path.unlink()

    # ----- Schreiben -----

    def _open(self, key: str) -> None:
        if self._file is not None:
            with contextlib.suppress(OSError):
                self._file.flush()
                os.fsync(self._file.fileno())
            self._file.close()
        self._file = (self.directory / f"{PREFIX}{key}.log").open("a", encoding="utf-8")
        self._file_key = key
        # Verzeichniseintrag sichern, sonst fehlt nach einem Absturz die ganze Datei.
        with contextlib.suppress(OSError):
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.purge()

    def write(self, source: str, text: str, important: bool = False) -> None:
        now = self._clock().astimezone()
        stamp = now.isoformat(timespec="milliseconds")
        source = source.replace("|", "/").strip() or "?"
        try:
            with self._lock:
                key = now.strftime("%Y%m%d-%H")
                if self._file is None or key != self._file_key:
                    self._open(key)
                for part in str(text).splitlines() or [""]:
                    self._file.write(f"{stamp}{_SEPARATOR}{source}{_SEPARATOR}{part}\n")
                self._file.flush()
                if important:
                    os.fsync(self._file.fileno())
                    self._dirty = False
                else:
                    self._dirty = True
        except (OSError, ValueError):
            pass  # Das Protokoll darf einen Test nie stören (Datenträger voll, Ordner weg).

    def test_started(self, key: str, name: str, detail: str = "") -> None:
        with self._lock:
            self._active[key] = name
        self.write(name, "TEST START" + (f": {detail}" if detail else ""), important=True)

    def test_finished(self, key: str, name: str, detail: str = "") -> None:
        with self._lock:
            self._active.pop(key, None)
        self.write(name, "TEST ENDE" + (f": {detail}" if detail else ""), important=True)

    def active_tests(self) -> list[str]:
        with self._lock:
            return sorted(self._active.values())

    # ----- Hintergrund -----

    def _sync_loop(self) -> None:
        while not self._stop.wait(self.sync_interval):
            with self._lock:
                if self._dirty and self._file is not None:
                    with contextlib.suppress(OSError, ValueError):
                        os.fsync(self._file.fileno())
                    self._dirty = False

    def _state_loop(self) -> None:
        journal = None
        bmc = _IpmiSampler()
        bmc_thread: Optional[threading.Thread] = None
        bmc_note = ""
        gpu = _GpuSampler(self.write) if self._sample_hardware else None
        gpu_thread: Optional[threading.Thread] = None
        next_sensors = time.monotonic() + self.state_interval  # erste Sensorzeile nach der ersten BMC-Abfrage
        if self._sample_hardware:
            psutil.cpu_percent(interval=None)  # erster Aufruf liefert sonst 0
            try:
                from hardwaretest.core.monitoring import KernelEvents

                journal = KernelEvents()
                if not journal.available:
                    self.write(KERNEL, "Kernel-Journal nicht lesbar; Kernel-Fehler fehlen im Protokoll.")
                    journal = None
            except Exception:
                journal = None
        while not self._stop.wait(self.state_interval):
            try:
                state = collect_state() if self._sample_hardware else ""
                if self._sample_hardware:
                    if bmc.due() and (bmc_thread is None or not bmc_thread.is_alive()):
                        bmc_thread = threading.Thread(target=bmc.sample, daemon=True, name="hwtest-bmc")
                        bmc_thread.start()
                    if bmc.note and bmc.note != bmc_note:
                        self.write(STATE, bmc.note)
                    bmc_note = bmc.note
                    if bmc.text:
                        state += " " + bmc.text
                self.write(STATE, f"{state} aktiv=[{', '.join(self.active_tests())}]".strip(), important=True)
                if self._sample_hardware and time.monotonic() >= next_sensors:
                    # Alle Einzelsensoren; fehlende Werte stehen ausdrücklich da (z. B. in einer VM).
                    next_sensors = time.monotonic() + self.sensor_interval
                    temperatures, fans = collect_sensor_details()
                    temperatures += bmc.temperatures
                    fans += bmc.fans
                    self.write(SENSORS, " ".join([
                        *(temperatures or ["temperaturen=keine_lesbar"]),
                        *(fans or ["luefter=keine_lesbar"]), *bmc.other]), important=True)
                if gpu is not None and gpu.due() and (gpu_thread is None or not gpu_thread.is_alive()):
                    gpu_thread = threading.Thread(target=gpu.sample, daemon=True, name="hwtest-gpu")
                    gpu_thread.start()
                if journal is not None:
                    for message in journal.poll():
                        self.write(KERNEL, message, important=True)
                    if not journal.available:
                        self.write(KERNEL, "Kernel-Journal nicht mehr lesbar.")
                        journal = None
            except Exception as exc:  # Sampler darf nie die App beenden
                self.write(STATE, f"Zustand nicht lesbar: {exc}")

    # ----- Lesen -----

    def excerpt(self, minutes_before: float = 10, max_lines: int = 1500) -> str:
        with self._lock:
            if self._file is not None:
                with contextlib.suppress(OSError, ValueError):
                    self._file.flush()
        since = self._clock().astimezone() - timedelta(minutes=minutes_before)
        lines = [raw for _line, raw in read_lines(self.directory, since)]
        if len(lines) > max_lines:
            head = max_lines // 5
            skipped = len(lines) - max_lines
            lines = [*lines[:head], f"… {skipped} Zeilen ausgelassen (vollständig im Protokollordner) …",
                     *lines[-(max_lines - head):]]
        return "\n".join(lines)


# ----- Modulweite Sitzung -----

_session: Optional[SessionLog] = None
_local = threading.local()


def start(directory: Optional[Path] = None, **options) -> Optional[UncleanSession]:
    """Startet das Protokoll der App; liefert Angaben zu einer unsauber beendeten Vorsitzung."""
    global _session
    if _session is not None or os.environ.get("HARDWARETEST_NO_SESSION_LOG"):
        return None
    session = SessionLog(directory or default_directory(), **options)
    try:
        unclean = session.start()
    except OSError as exc:
        print(f"Hardwaretest: Sitzungsprotokoll nicht möglich: {exc}", file=sys.stderr)
        return None
    _session = session
    return unclean


def stop() -> None:
    global _session
    session, _session = _session, None
    if session is not None:
        session.stop()


def active() -> bool:
    return _session is not None


def directory() -> Path:
    return _session.directory if _session is not None else default_directory()


@contextlib.contextmanager
def quiet():
    """Unterdrückt ``write`` im aktuellen Thread (Zeile wurde schon von der Quelle protokolliert)."""
    previous = getattr(_local, "quiet", False)
    _local.quiet = True
    try:
        yield
    finally:
        _local.quiet = previous


def write(source: str, text: str, important: bool = False) -> None:
    if _session is not None and not getattr(_local, "quiet", False):
        _session.write(source, text, important)


def test_started(key: str, name: str, detail: str = "") -> None:
    if _session is not None:
        _session.test_started(key, name, detail)


def test_finished(key: str, name: str, detail: str = "") -> None:
    if _session is not None:
        _session.test_finished(key, name, detail)


def excerpt(minutes_before: float = 10) -> str:
    return _session.excerpt(minutes_before) if _session is not None else ""
