import csv
import subprocess
from types import SimpleNamespace

from hardwaretest.core import monitoring
from hardwaretest.core import test_plan


class Journal:
    available = True

    def poll(self):
        return []


def test_threshold_baseline_and_error_deltas(tmp_path):
    metrics = {"temperature.cpu": 40, "ecc.mc0.ue_count": 2}
    session = monitoring.MonitorSession(tmp_path, collect=lambda: (metrics.copy(), []), journal=Journal())
    assert not session.sample()["critical"]  # Historical errors aren't new errors.
    assert session.alert_count == 1
    session.sample()
    assert session.alert_count == 1
    metrics["ecc.mc0.ue_count"] = 3
    assert session.sample()["critical"]
    metrics["temperature.cpu"] = 95
    assert session.sample()["critical"]
    count = session.alert_count
    session.sample()
    assert session.alert_count == count  # Do not repeat the same temperature alert.
    session.close()


def test_missing_sensors_are_not_a_hardware_failure(tmp_path):
    session = monitoring.MonitorSession(tmp_path, collect=lambda: ({"memory.percent": 10}, ["temperature", "ecc"]), journal=Journal())
    assert not session.sample()["critical"]
    assert session.alert_count == 0
    assert "temperature" in session.summary()
    session.close()


def test_reports_bounded_escaped_and_csv_complete(tmp_path):
    metrics = {"ecc.<controller>.ce_count": 0, "temperature.cpu": float("nan")}
    session = monitoring.MonitorSession(tmp_path, language="en", collect=lambda: (metrics.copy(), []), journal=Journal())
    for count in range(220):
        metrics["ecc.<controller>.ce_count"] = count
        session.sample()
    txt, html = session.close()
    assert session.alert_count == 219
    assert len(session.alerts) == 100
    assert "&lt;controller&gt;" in html.read_text()
    assert "<controller>" not in html.read_text()
    assert "temperature.cpu" not in session.stats
    assert "temperature.cpu" in session.unavailable
    assert len(txt.read_text()) < 25000
    with session.csv_path.open() as file:
        rows = list(csv.reader(file))
    assert sum(row[1] == "event" for row in rows) == 219


def test_guard_fails_closed_on_collector_failure(tmp_path):
    def fail():
        raise OSError("sensor failed")
    session = monitoring.MonitorSession(tmp_path, collect=fail, journal=Journal())
    guard = monitoring.MonitoringGuard(session)
    guard.start()
    assert guard.tripped.is_set()
    guard.finish()
    assert "sensor failed" in guard.error


def test_plan_hot_at_start_does_not_launch_load(monkeypatch, tmp_path):
    factory = monitoring.MonitorSession
    monkeypatch.setattr(test_plan, "MonitorSession", lambda *a: factory(*a, collect=lambda: ({"temperature.cpu": 105}, []), journal=Journal()))
    config = test_plan.TestPlanConfig(output_dir=tmp_path, monitoring_enabled=True, ram_enabled=False, nvme_enabled=False, nvidia_enabled=False)
    executor = test_plan.TestPlanExecutor(config)
    monkeypatch.setattr(executor, "_run_cpu", lambda: (_ for _ in ()).throw(AssertionError("must not start")))
    result = executor.execute()
    assert result.status == "failed"
    assert [s.key for s in result.steps] == ["monitoring"]
    assert "Sicherheitsabbruch" in result.steps[0].summary
    assert result.html_report.exists()


def test_plan_stops_active_runner_on_critical_signal(tmp_path):
    executor = test_plan.TestPlanExecutor(test_plan.TestPlanConfig(output_dir=tmp_path))
    stopped = []
    executor._monitor = SimpleNamespace(tripped=SimpleNamespace(is_set=lambda: True))
    runner = SimpleNamespace(build_command=lambda: ["fake"], start=lambda: None,
                             is_running=lambda: True, stop=lambda **kw: stopped.append(kw))
    result = executor._run_process_step("cpu", runner)
    assert stopped == [{"aborted": True}]
    assert result.status == "failed"


def test_acpi_fan_metrics_reports_state_percent(tmp_path):
    for name, kind, cur, maximum in (("cooling_device3", "Processor", 2, 10), ("cooling_device4", "Fan", 1, 1),
                                     ("cooling_device5", "Fan", 1, 4), ("cooling_device6", "Fan", 0, 0)):
        device = tmp_path / name
        device.mkdir()
        (device / "type").write_text(kind + "\n")
        (device / "cur_state").write_text(f"{cur}\n")
        (device / "max_state").write_text(f"{maximum}\n")
    assert monitoring.acpi_fan_metrics(tmp_path) == {"fan.acpi.4.percent": 100, "fan.acpi.5.percent": 25}


def test_ipmi_fans_are_recorded_alongside_hwmon_fans(monkeypatch, tmp_path):
    monkeypatch.setattr(monitoring.psutil, "virtual_memory", lambda: SimpleNamespace(percent=30))
    monkeypatch.setattr(monitoring.psutil, "sensors_temperatures", lambda: {})
    monkeypatch.setattr(monitoring.psutil, "sensors_fans",
                        lambda: {"hwmon": [SimpleNamespace(label="CPU", current=900)]})
    monkeypatch.setattr(monitoring.ipmi, "ipmi_available", lambda: True)
    monkeypatch.setattr(monitoring.ipmi, "sel_ecc_metrics", lambda run: ({"ecc.ipmi_sel.ue_count": 0}, []))
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: None)
    value = [30.5]

    def run(command, **kwargs):
        assert command == ["ipmitool", "sdr", "elist"]
        reading = "No Reading" if value[0] is None else f"{value[0]} percent"
        return subprocess.CompletedProcess(command, 0, f"Fan 1 | 30h | ok | 29.1 | {reading}\n", "")

    session = monitoring.MonitorSession(tmp_path, collect=lambda: monitoring.collect_metrics(root_run=run),
                                        journal=Journal())
    try:
        first = session.sample()
        value[0] = 45.0
        second = session.sample()
        value[0] = None
        missing = session.sample()
        history = session.chart_snapshot()["history"]
    finally:
        _txt, html = session.close()

    key = "fan.ipmi.Fan 1 (%)"
    assert first["metrics"][key] == 30.5 and second["metrics"][key] == 45.0
    assert second["metrics"]["fan.hwmon.0.CPU"] == 900
    assert "fan.ipmi" not in second["unavailable"]
    assert key not in missing["metrics"] and "fan.ipmi" in missing["unavailable"]
    assert [bucket.maximum for bucket in history[key]] == [30.5, 45.0, None]
    with session.csv_path.open() as file:
        rows = list(csv.reader(file))
    assert [row[2] for row in rows if row[1] == key] == ["30.5", "45.0"]
    assert key in html.read_text()


def test_unavailable_ipmi_fans_are_reported_even_with_hwmon_fans(monkeypatch):
    monkeypatch.setattr(monitoring.psutil, "virtual_memory", lambda: SimpleNamespace(percent=30))
    monkeypatch.setattr(monitoring.psutil, "sensors_temperatures", lambda: {})
    monkeypatch.setattr(monitoring.psutil, "sensors_fans",
                        lambda: {"hwmon": [SimpleNamespace(label="CPU", current=900)]})
    monkeypatch.setattr(monitoring.ipmi, "ipmi_available", lambda: True)
    monkeypatch.setattr(monitoring.ipmi, "default_runner", lambda: None)
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: None)
    metrics, unavailable = monitoring.collect_metrics()
    assert metrics["fan.hwmon.0.CPU"] == 900
    assert "fan.ipmi" in unavailable
