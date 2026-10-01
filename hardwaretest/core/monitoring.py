"""Bounded, incremental telemetry recording for PCs and servers (no stress load)."""

from __future__ import annotations

import csv
from datetime import datetime
from html import escape
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

import psutil

from hardwaretest.core.monitor_history import MetricHistory, is_temperature, render_chart, validated_sensor_limits


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def collect_metrics() -> tuple[dict[str, float], list[str]]:
    metrics: dict[str, float] = {"memory.percent": psutil.virtual_memory().percent}
    unavailable: list[str] = []
    try:
        temperatures = psutil.sensors_temperatures()
        for chip, sensors in temperatures.items():
            for index, sensor in enumerate(sensors):
                metrics[f"temperature.{chip}.{index}.{sensor.label}"] = sensor.current
        if not temperatures:
            unavailable.append("temperature")
    except (OSError, AttributeError, RuntimeError):
        unavailable.append("temperature")
    try:
        for chip, sensors in psutil.sensors_fans().items():
            for index, sensor in enumerate(sensors):
                metrics[f"fan.{chip}.{index}.{sensor.label}"] = sensor.current
    except (OSError, AttributeError, RuntimeError):
        unavailable.append("fan")
    if not any(key.startswith("fan.") for key in metrics) and "fan" not in unavailable:
        unavailable.append("fan")
    for controller in Path("/sys/devices/system/edac/mc").glob("mc[0-9]*"):
        for field in ("ce_count", "ue_count"):
            try:
                metrics[f"ecc.{controller.name}.{field}"] = int((controller / field).read_text())
            except (ValueError, OSError):
                unavailable.append(f"ecc.{controller.name}.{field}")
    if not any(key.startswith("ecc.") for key in metrics):
        unavailable.append("ecc")
    # Ext4 exposes recorded filesystem errors without running fsck on a mount.
    for counter in Path("/sys/fs/ext4").glob("*/errors_count"):
        try:
            metrics[f"filesystem.{counter.parent.name}.errors"] = int(counter.read_text())
        except (ValueError, OSError):
            unavailable.append(f"filesystem.{counter.parent.name}")
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=uuid,temperature.gpu,power.draw,utilization.gpu,ecc.errors.uncorrected.volatile.total",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode:
                unavailable.append("nvidia-smi")
            else:
                for row in csv.reader(result.stdout.splitlines()):
                    if len(row) != 5:
                        continue
                    for name, value in zip(("temperature", "power", "utilization", "ue_count"), row[1:], strict=True):
                        try:
                            metrics[f"gpu.{row[0].strip()}.{name}"] = float(value.strip())
                        except ValueError:
                            unavailable.append(f"gpu.{row[0].strip()}.{name}")
        except (OSError, subprocess.SubprocessError):
            unavailable.append("nvidia-smi")
    return metrics, unavailable


class KernelEvents:
    """Read new kernel errors only; never silently call missing access 'healthy'."""

    pattern = re.compile(
        r"hardware error|machine check|uncorrect(?:ed|able)|NVRM: Xid|"
        r"I/O error|EXT4-fs error|BTRFS.*(?:error|corrupt)|XFS.*(?:error|corrupt)|"
        r"PCIe Bus Error|AER:.*(?:error|fatal)", re.I,
    )

    def __init__(self) -> None:
        self.cursor = ""
        self.available = False
        try:
            result = self._run(["-n", "0", "--show-cursor"])
            match = re.search(r"-- cursor: (.+)", result.stdout)
            if result.returncode == 0 and match:
                self.cursor = match.group(1).strip()
                self.available = True
        except (OSError, subprocess.SubprocessError):
            pass

    @staticmethod
    def _run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["journalctl", "-k", "-b", "--no-pager", *args],
            capture_output=True, text=True, timeout=5,
        )

    def poll(self) -> list[str]:
        if not self.available:
            return []
        try:
            result = self._run(["--after-cursor", self.cursor, "-n", "500", "-o", "json"])
            if result.returncode:
                self.available = False
                return []
            lines = result.stdout.splitlines()
            events = []
            if len(lines) >= 500:
                events.append("kernel: log burst; limited to 500 entries per sample")
            for line in lines:
                entry = json.loads(line)
                self.cursor = entry.get("__CURSOR", self.cursor)
                message = entry.get("MESSAGE", "")
                if isinstance(message, str) and self.pattern.search(message):
                    events.append(message[:2000])
            return events
        except (OSError, ValueError, subprocess.SubprocessError):
            self.available = False
            return []


class MonitorSession:
    """CSV on disk, bounded aggregates in memory. Reports do not expand with duration."""

    def __init__(self, directory: Path, temperature_limit: float = 90, language: str = "de",
                 collect=collect_metrics, journal=None) -> None:
        self.language = language
        self.limit = temperature_limit
        if not math.isfinite(self.limit) or not 30 <= self.limit <= 120:
            raise ValueError("Temperaturgrenze / Temperature limit: 30–120 °C")
        self.sensor_limits: dict[str, float] = {}
        self.history: dict[str, MetricHistory] = {}
        self.started_monotonic = time.monotonic()
        self.collect = collect
        self.journal = journal if journal is not None else KernelEvents()
        directory = Path(directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        self.base = directory / f"monitor-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        self.started = now()
        self.samples = 0
        self.alert_count = 0
        self.alerts: list[str] = []
        self.unavailable: set[str] = set()
        self.stats: dict[str, list[float]] = {}
        self.previous: dict[str, float] = {}
        self.active_alerts: set[str] = set()
        self.critical = False
        self.recording_error = ""
        self.csv_path = self.base.with_suffix(".csv")
        self._file = self.csv_path.open("x", encoding="utf-8", newline="")
        self._csv = csv.writer(self._file)
        self._csv.writerow(["timestamp", "metric", "value"])
        self._closed = False
        self.last_sample_time = 0.0

    def text(self, de: str, en: str) -> str:
        return en if self.language == "en" else de

    def set_sensor_limits(self, values: dict[str, float]) -> None:
        if self.samples:
            raise RuntimeError("Grenzen nur vor Start ändern / Change limits before starting")
        self.sensor_limits = validated_sensor_limits(values)

    def sample(self) -> dict:
        metrics, missing = self.collect()
        invalid = [key for key, value in metrics.items() if not isinstance(value, (float, int)) or not math.isfinite(value)]
        missing = [*missing, *invalid, *(set(self.previous) - set(metrics))]
        metrics = {key: value for key, value in metrics.items() if key not in invalid}
        self.last_sample_time = time.monotonic()
        self.samples += 1
        stamp = now()
        elapsed = max(0.0, time.monotonic() - self.started_monotonic)
        for key in sorted(metrics, key=lambda name: (not is_temperature(name), name)):
            if key not in self.history and len(self.history) < 256:
                self.history[key] = MetricHistory()
        for key, history in self.history.items():
            history.add(elapsed, metrics.get(key))
        self.unavailable.update(missing)
        alerts = []
        active = set()
        for key, value in metrics.items():
            self._csv.writerow([stamp, key, value])
            stat = self.stats.setdefault(key, [value, value, 0.0, 0])
            stat[:] = [min(stat[0], value), max(stat[1], value), stat[2] + value, stat[3] + 1]
            limit = self.sensor_limits.get(key, self.limit)
            if is_temperature(key) and value >= limit:
                self.critical = True
                active.add(key)
                if key not in self.active_alerts:
                    alerts.append(f"{key}: {value:g} °C >= {limit:g} °C")
            is_counter = key.startswith(("ecc.", "filesystem.")) or key.endswith(".ue_count")
            if is_counter:
                before = self.previous.get(key)
                if before is not None and value > before:
                    alerts.append(f"{key}: +{value - before:g} ({before:g} -> {value:g})")
                    if key.endswith("ue_count"):
                        self.critical = True
                elif before is not None and value < before:
                    alerts.append(f"{key}: " + self.text("Zähler zurückgesetzt", "counter reset") + f" ({before:g} -> {value:g})")
                elif before is None and value > 0:
                    alerts.append(f"{key}: {value:g} " + self.text("bereits bei Beginn", "at baseline"))
            self.previous[key] = value
        self.active_alerts = active
        alerts.extend(self.journal.poll())
        if not self.journal.available:
            self.unavailable.add("kernel journal")
        for alert in alerts:
            self.alert_count += 1
            if len(self.alerts) < 100:
                self.alerts.append(f"{stamp}: {alert}")
            self._csv.writerow([stamp, "event", alert])
        for missing_key in missing:
            self._csv.writerow([stamp, "unavailable", missing_key])
        self._file.flush()
        return {"timestamp": stamp, "metrics": metrics, "alerts": alerts,
                "critical": self.critical, "samples": self.samples,
                "unavailable": sorted(self.unavailable),
                "alert_count": self.alert_count, "csv": str(self.csv_path)}

    def chart_snapshot(self) -> dict:
        # Immutable bucket copies are safe to send from a worker to the Qt UI.
        return {"history": {key: tuple(history.buckets) for key, history in self.history.items()},
                "limits": {key: self.sensor_limits.get(key, self.limit) for key in self.history if is_temperature(key)}}

    def record_error(self, message: str) -> None:
        self.recording_error = message
        self.critical = True
        self.alert_count += 1
        if len(self.alerts) < 100:
            self.alerts.append(f"{now()}: MONITORING ERROR: {message}")

    def summary(self) -> str:
        return "\n".join([
            self.text("Langzeitmonitoring", "Long-term monitoring"),
            f"{self.started} – {now()}",
            self.text("Messungen", "Samples") + f": {self.samples}",
            self.text("Ereignisse", "Events") + f": {self.alert_count}",
            (self.text("Aufzeichnung unvollständig: ", "Recording incomplete: ") + self.recording_error
             if self.recording_error else ""),
            self.text("Temperaturgrenze", "Temperature limit") + f": {self.limit:g} °C",
            self.text("Individuelle Temperaturgrenzen", "Individual temperature limits") + ": "
            + ("; ".join(f"{key} = {value:g} °C" for key, value in sorted(self.sensor_limits.items())) or "–"),
            self.text("Konfigurierte Sensoren ohne Messwert", "Configured sensors with no measurements") + ": "
            + (", ".join(sorted(set(self.sensor_limits) - set(self.stats))) or "–"),
            self.text("Nicht verfügbar / zeitweise ausgefallen", "Unavailable / intermittent")
            + ": " + (", ".join(sorted(self.unavailable)) or "–"),
            self.text("Keine vollständige Hardwarefreigabe; nur erfasste Messwerte.",
                      "Not a complete hardware certification; recorded measurements only."),
            "", "Min / Max / Mean:",
            *[f"{key}: {s[0]:.2f} / {s[1]:.2f} / {s[2] / s[3]:.2f}"
              for key, s in sorted(self.stats.items())],
            "", *self.alerts,
            self.text("Ereignisdetails im Bericht auf 100 begrenzt; vollständig in CSV.",
                      "Report limited to 100 event details; complete data in CSV."),
            f"CSV: {self.csv_path.name}",
        ])

    def close(self) -> tuple[Path, Path]:
        if not self._closed:
            self._file.close()
            self._closed = True
        text = self.summary()
        txt, html = self.base.with_suffix(".txt"), self.base.with_suffix(".html")
        txt.write_text(text, encoding="utf-8")
        charts = self.html_charts()
        html.write_text(
            '<!doctype html><html lang="' + self.language + '"><meta charset="utf-8">'
            '<title>Hardwaretest Monitoring</title><style>body{font:16px sans-serif;'
            'max-width:1100px;margin:2em auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
            'svg{display:block;width:100%;height:auto;margin:1em 0}'
            '@media print{svg{break-inside:avoid}}'
            '</style><h1>Hardwaretest Monitoring</h1><pre>' + escape(text) + '</pre>' + charts + '</html>',
            encoding="utf-8",
        )
        return txt, html

    def html_charts(self) -> str:
        temperatures = sorted((key for key in self.history if is_temperature(key)),
            key=lambda key: ("core" in key.lower(), key))
        other = sorted(key for key in self.history if not is_temperature(key))
        keys = temperatures[:12] + other[:12]
        if len(keys) < 24:
            keys.extend(key for key in [*temperatures, *other] if key not in keys)
            keys = keys[:24]
        note = self.text("Verlaufsdiagramme (max. 24); verdichtete Zeitabschnitte, Min/Max bleiben erhalten. Vollständige Messungen in CSV.",
                         "History charts (max. 24); compacted time buckets preserve min/max. Full measurements in CSV.")
        return '<h2>' + self.text("Messverlauf", "Measurement history") + '</h2><p>' + note + '</p>' + "".join(
            render_chart(key, self.history[key].buckets,
                         self.sensor_limits.get(key, self.limit) if is_temperature(key) else None, self.language)
            for key in keys)


class MonitoringGuard:
    """One initial sample before load, then independent sampling during a plan."""

    def __init__(self, session: MonitorSession, interval: float = 10):
        self.session = session
        self.interval = max(1, interval)
        self.stop_event = threading.Event()
        self.tripped = threading.Event()
        self.error = ""
        self._thread = None

    def sample(self):
        try:
            if self.session.sample()["critical"]:
                self.tripped.set()
        except Exception as exc:
            self.error = str(exc)
            self.session.record_error(self.error)
            self.tripped.set()

    def start(self):
        self.sample()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self.stop_event.wait(self.interval):
            self.sample()
            if self.error:
                break

    def finish(self):
        self.stop_event.set()
        if self._thread:
            self._thread.join()
        if not self.error:
            self.sample()
        return self.session.close()
