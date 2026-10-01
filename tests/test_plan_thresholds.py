from types import SimpleNamespace
from pathlib import Path

import pytest

from hardwaretest.core import test_plan
from hardwaretest.core.test_runner import TestParameters, TestResult
from hardwaretest.tests.network import NetworkRunner


def run_network(summary, mode="ping", **limits):
    config = test_plan.TestPlanConfig(output_dir=Path("/tmp"), **limits)
    executor = test_plan.TestPlanExecutor(config)
    runner = NetworkRunner(TestParameters(1), "localhost", mode)
    runner.start = lambda: None
    runner.is_running = lambda: False
    runner.get_result = lambda: TestResult(True, [], 1, 0)
    runner.summary = lambda: summary
    runner.compact_report_lines = lambda language: []
    return executor._run_process_step(mode, runner, structured_summary=True)


PING = dict(packet_loss_percent=0, latency_avg_ms=3, jitter_ms=1)


@pytest.mark.parametrize("summary,limits,expected", [
    (PING, {}, "passed"),
    ({**PING, "packet_loss_percent": 1}, {}, "failed"),
    ({**PING, "packet_loss_percent": 1}, {"max_packet_loss_percent": 2}, "passed"),
    (PING, {"max_latency_avg_ms": 2}, "failed"),
    (PING, {"max_jitter_ms": 0.5}, "failed"),
    ({"packet_loss_percent": 0}, {}, "failed"),
    ({**PING, "jitter_ms": float("nan")}, {}, "failed"),
    ({**PING, "jitter_ms": None}, {}, "failed"),
])
def test_ping_thresholds_and_missing_values(summary, limits, expected):
    result = run_network(summary, **limits)
    assert result.status == expected
    assert any("Limits:" in line for line in result.details)


def test_iperf_minimum_disabled_and_enforced():
    assert run_network(dict(throughput_mbit_s=50), "iperf3").status == "passed"
    result = run_network(dict(throughput_mbit_s=50), "iperf3", min_throughput_mbit_s=100)
    assert result.status == "failed"
    assert any("throughput: 50 / 100" in line for line in result.details)
    assert run_network(dict(throughput_mbit_s=100), "iperf3", min_throughput_mbit_s=100).status == "passed"


def test_exact_ping_thresholds_pass_and_null_latency_fails():
    assert run_network(PING, max_latency_avg_ms=3, max_jitter_ms=1).status == "passed"
    assert run_network({**PING, "latency_avg_ms": None}).status == "failed"


@pytest.mark.parametrize("limits", [{"max_packet_loss_percent": -1}, {"max_packet_loss_percent": 101},
                                    {"max_jitter_ms": float("nan")}, {"min_throughput_mbit_s": -1}])
def test_invalid_network_configuration_rejected(tmp_path, limits):
    with pytest.raises(ValueError):
        test_plan.TestPlanExecutor(test_plan.TestPlanConfig(output_dir=tmp_path, **limits)).execute()


def test_individual_temperature_limits_reach_plan_guard(tmp_path, monkeypatch):
    from hardwaretest.core.monitoring import MonitorSession

    monkeypatch.setattr(test_plan, "MonitorSession", lambda *args: MonitorSession(*args,
        collect=lambda: ({"temperature.ssd": 66}, []), journal=SimpleNamespace(available=True, poll=lambda: [])))
    config = test_plan.TestPlanConfig(output_dir=tmp_path, monitoring_enabled=True,
        cpu_enabled=True, ram_enabled=False, nvme_enabled=False, nvidia_enabled=False,
        sensor_temperature_limits=(("temperature.ssd", 65),))
    executor = test_plan.TestPlanExecutor(config)
    monkeypatch.setattr(executor, "_run_cpu", lambda: (_ for _ in ()).throw(AssertionError("must not start")))
    result = executor.execute()
    assert result.status == "failed" and [step.key for step in result.steps] == ["monitoring"]
