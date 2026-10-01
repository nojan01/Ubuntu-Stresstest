import csv
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
    monkeypatch.setattr(test_plan, "MonitorSession", lambda *a: factory(*a, collect=lambda: ({"temperature.cpu": 95}, []), journal=Journal()))
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
