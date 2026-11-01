"""Regressionstests gegen „False-OK“-Pfade: fehlende oder unlesbare
Diagnosedaten duerfen nie als gesund/bestanden gewertet werden."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from hardwaretest.core import system_info


# ---------------------------------------------------------------------------
# 1) SMART (smartctl)
# ---------------------------------------------------------------------------

def _fake_smartctl(monkeypatch, stdout: str, returncode: int, text_stdout: str = "", text_rc: int = 0):
    """Ersetzt shutil.which/subprocess.run im system_info-Modul durch Fakes."""
    calls = []

    def fake_which(name):
        return {"smartctl": "/usr/sbin/smartctl"}.get(name)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "-j" in cmd:
            return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(cmd, text_rc, stdout=text_stdout, stderr="")

    monkeypatch.setattr(system_info.shutil, "which", fake_which)
    monkeypatch.setattr(system_info.subprocess, "run", fake_run)
    return calls


def test_smart_json_without_smart_status_is_unknown_not_ok(monkeypatch):
    payload = json.dumps({"model_name": "FakeDisk", "serial_number": "X1"})
    _fake_smartctl(monkeypatch, payload, returncode=0)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is None
    assert info.error
    summary = system_info.format_smart_summary(info)
    assert "OK" not in summary
    assert "UNBEKANNT" in summary


def test_smart_exit_bit2_device_open_failure_is_unknown(monkeypatch):
    payload = json.dumps({"smart_status": {"passed": True}})
    _fake_smartctl(monkeypatch, payload, returncode=4 | 2)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is None
    assert "geoeffnet" in info.error
    assert "OK" not in system_info.format_smart_summary(info)


def test_smart_exit_bit3_disk_failing_overrides_passed(monkeypatch):
    payload = json.dumps({"smart_status": {"passed": True}})
    _fake_smartctl(monkeypatch, payload, returncode=8)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is False
    assert any("DISK FAILING" in w for w in info.warnings)
    assert "FEHLER" in system_info.format_smart_summary(info)


def test_smart_exit_bit4_is_warning_but_still_ok(monkeypatch):
    payload = json.dumps({"smart_status": {"passed": True}})
    _fake_smartctl(monkeypatch, payload, returncode=4)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is True
    assert info.warnings
    summary = system_info.format_smart_summary(info)
    assert "✓ OK" in summary
    assert "⚠" in summary


def test_smart_exit_bits_4_to_7_passed_with_warnings(monkeypatch):
    payload = json.dumps({"smart_status": {"passed": True}})
    _fake_smartctl(monkeypatch, payload, returncode=0x10 | 0x40)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is True
    assert len(info.warnings) == 2


def test_smart_json_passed_true_exit_zero_is_ok(monkeypatch):
    payload = json.dumps({"smart_status": {"passed": True}, "model_name": "Good"})
    _fake_smartctl(monkeypatch, payload, returncode=0)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is True
    assert not info.error
    assert "✓ OK" in system_info.format_smart_summary(info)


def test_smart_json_passed_false_is_failure(monkeypatch):
    payload = json.dumps({"smart_status": {"passed": False}})
    _fake_smartctl(monkeypatch, payload, returncode=8)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is False


def test_smart_text_fallback_without_health_line_is_unknown(monkeypatch):
    text = "Device Model:     FakeDisk\nSerial Number:    X1\n"
    _fake_smartctl(monkeypatch, "not json", returncode=0, text_stdout=text, text_rc=0)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is None
    assert info.model == "FakeDisk"
    assert "OK" not in system_info.format_smart_summary(info)


def test_smart_text_fallback_passed_line_is_ok(monkeypatch):
    text = "SMART overall-health self-assessment test result: PASSED\n"
    _fake_smartctl(monkeypatch, "not json", returncode=0, text_stdout=text, text_rc=0)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is True


def test_smart_text_fallback_failed_line_is_failure(monkeypatch):
    text = "SMART overall-health self-assessment test result: FAILED!\n"
    _fake_smartctl(monkeypatch, "not json", returncode=0, text_stdout=text, text_rc=8)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is False


def test_smart_missing_tool_is_unknown(monkeypatch):
    monkeypatch.setattr(system_info.shutil, "which", lambda name: None)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is None
    assert "nicht gefunden" in info.error


@pytest.mark.parametrize("rc", [1, 2, 3])
def test_smart_exit_bits_0_1_never_ok(monkeypatch, rc):
    payload = json.dumps({"smart_status": {"passed": True}})
    _fake_smartctl(monkeypatch, payload, returncode=rc)
    info = system_info.read_smart_info("/dev/sdz")
    assert info.healthy is None


# ---------------------------------------------------------------------------
# 2) NVMe smart-log ohne Pflichtfelder
# ---------------------------------------------------------------------------

from hardwaretest.core import test_plan  # noqa: E402
from hardwaretest.core.nvme import NvmeDevice, NvmeHealth, parse_smart_log  # noqa: E402

_DRIVE = NvmeDevice("/dev/nvme0n1", "/dev/nvme0", "Model", "SERIAL", "FW", 4096)


def _plan(tmp_path, **overrides):
    config = dict(output_dir=tmp_path, cpu_enabled=False, ram_enabled=False,
                  nvme_enabled=True, nvidia_enabled=False)
    config.update(overrides)
    return test_plan.TestPlanExecutor(test_plan.TestPlanConfig(**config))


def test_nvme_smart_log_without_critical_warning_is_unknown():
    health = parse_smart_log("/dev/nvme0n1", json.dumps({"temperature": 300, "media_errors": 0}))
    assert health.critical_warning is None
    assert health.healthy is None
    assert not health.health_known
    assert health.missing_fields == ("critical_warning",)


def test_nvme_smart_log_without_media_errors_is_unknown():
    health = parse_smart_log("/dev/nvme0n1", json.dumps({"critical_warning": 0}))
    assert health.healthy is None
    assert health.missing_fields == ("media_errors",)


def test_nvme_smart_log_unparseable_critical_warning_is_unknown():
    health = parse_smart_log("/dev/nvme0n1", json.dumps({"critical_warning": "n/a", "media_errors": 0}))
    assert health.healthy is None


def test_nvme_smart_log_complete_and_clean_is_healthy():
    health = parse_smart_log("/dev/nvme0n1", json.dumps({"critical_warning": 0, "media_errors": 0}))
    assert health.healthy is True


def test_nvme_smart_log_critical_warning_set_is_unhealthy():
    health = parse_smart_log("/dev/nvme0n1", json.dumps({"critical_warning": 4, "media_errors": 0}))
    assert health.healthy is False


def test_plan_nvme_step_fails_when_health_unknown(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/bin/fake")
    monkeypatch.setattr(test_plan, "list_nvme_devices", lambda: [_DRIVE])
    monkeypatch.setattr(test_plan, "read_nvme_smart",
                        lambda controller, *_: NvmeHealth(controller, media_errors=0))
    result = plan._run_nvme()
    assert result.status == test_plan.STATUS_FAILED
    assert any("critical_warning" in line and "nicht bestimmbar" in line for line in result.details)
    assert "1 mit Fehler/Warnung" in result.summary


def test_plan_nvme_step_passes_only_with_both_fields(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/bin/fake")
    monkeypatch.setattr(test_plan, "list_nvme_devices", lambda: [_DRIVE])
    monkeypatch.setattr(test_plan, "read_nvme_smart",
                        lambda controller, *_: NvmeHealth(controller, critical_warning=0, media_errors=0))
    assert plan._run_nvme().status == test_plan.STATUS_PASSED


def test_plan_nvme_read_rejects_unknown_initial_health(tmp_path, monkeypatch):
    plan = _plan(tmp_path, nvme_devices=(_DRIVE,), nvme_read_enabled=True)
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/bin/fake")
    monkeypatch.setattr(test_plan, "list_nvme_devices", lambda: [_DRIVE])
    monkeypatch.setattr(test_plan, "read_nvme_smart",
                        lambda controller, *_: NvmeHealth(controller, media_errors=0))
    with pytest.raises(RuntimeError):
        plan._run_nvme_read()


# ---------------------------------------------------------------------------
# 3) DCGM ohne auswertbares JSON
# ---------------------------------------------------------------------------

from hardwaretest.core.nvidia import LinuxNvidiaStatus  # noqa: E402
from hardwaretest.core.test_runner import TestParameters  # noqa: E402
from hardwaretest.tests.nvidia_runner import DcgmDiagnosticRunner  # noqa: E402


def _dcgm_runner(lines):
    runner = DcgmDiagnosticRunner(TestParameters(0), [0], 1)
    runner.output_lines = list(lines)
    return runner


def test_dcgm_runner_without_json_is_not_evaluable():
    runner = _dcgm_runner(["Successfully ran diagnostic for group.", "+----+"])
    assert runner.status_counts() == {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
    assert runner.evaluation_error()


def test_dcgm_runner_json_without_status_is_not_evaluable():
    runner = _dcgm_runner(['{"version": "3.3", "tests": []}'])
    assert runner.evaluation_error()


def test_dcgm_runner_with_statuses_is_evaluable():
    runner = _dcgm_runner(['{"tests":[{"status":"Pass"}]}'])
    assert runner.evaluation_error() is None


def _dcgm_plan(tmp_path, monkeypatch, lines):
    plan = _plan(tmp_path, nvme_enabled=False, dcgm_enabled=True)
    monkeypatch.setattr(test_plan, "dcgmi_available", lambda: True)
    monkeypatch.setattr(test_plan, "list_nvidia_gpus", lambda: [SimpleNamespace(index=0)])

    def fake_process_step(key, runner, **_kw):
        runner.output_lines = list(lines)
        return plan._instant_result(key, test_plan.STATUS_PASSED, "BESTANDEN | Exit-Code: 0")

    plan._run_process_step = fake_process_step
    return plan


def test_plan_dcgm_exit_zero_without_json_fails(tmp_path, monkeypatch):
    plan = _dcgm_plan(tmp_path, monkeypatch, ["Successfully ran diagnostic for group."])
    result = plan._run_dcgm()
    assert result.status == test_plan.STATUS_FAILED
    assert "nicht auswertbar" in result.summary


def test_plan_dcgm_exit_zero_with_pass_status_passes(tmp_path, monkeypatch):
    plan = _dcgm_plan(tmp_path, monkeypatch, ['{"tests":[{"status":"Pass"},{"status":"Skip"}]}'])
    result = plan._run_dcgm()
    assert result.status == test_plan.STATUS_PASSED
    assert "1 bestanden" in result.summary


def test_plan_dcgm_fail_status_still_fails(tmp_path, monkeypatch):
    plan = _dcgm_plan(tmp_path, monkeypatch, ['{"tests":[{"status":"Fail"}]}'])
    assert plan._run_dcgm().status == test_plan.STATUS_FAILED


# ---------------------------------------------------------------------------
# 4) NVIDIA-Inventar: Hardware vorhanden, nvidia-smi liefert keine GPU
# ---------------------------------------------------------------------------

_READY_STACK = LinuxNvidiaStatus(
    ("0000:41:00.0",), ("nvidia",), ("nvidia", "nvidia_uvm"),
    ("nvidia0", "nvidiactl"), True, True,
)


def test_plan_nvidia_inventory_empty_smi_output_fails(tmp_path, monkeypatch):
    plan = _plan(tmp_path, nvme_enabled=False, nvidia_enabled=True)
    monkeypatch.setattr(test_plan, "inspect_linux_nvidia", lambda: _READY_STACK)
    monkeypatch.setattr(test_plan, "list_nvidia_gpus", lambda: [])
    result = plan._run_nvidia()
    assert result.status == test_plan.STATUS_FAILED
    assert "0 NVIDIA-GPU(s)" not in result.summary
    assert "nvidia-smi" in result.summary


def test_plan_nvidia_inventory_smi_exception_fails(tmp_path, monkeypatch):
    plan = _plan(tmp_path, nvme_enabled=False, nvidia_enabled=True)
    monkeypatch.setattr(test_plan, "inspect_linux_nvidia", lambda: _READY_STACK)

    def boom():
        raise RuntimeError("Failed to parse nvidia-smi output")

    monkeypatch.setattr(test_plan, "list_nvidia_gpus", boom)
    result = plan._run_nvidia()
    assert result.status == test_plan.STATUS_FAILED
    assert any("Failed to parse" in line for line in result.details)


def test_plan_nvidia_inventory_with_gpu_passes(tmp_path, monkeypatch):
    plan = _plan(tmp_path, nvme_enabled=False, nvidia_enabled=True)
    monkeypatch.setattr(test_plan, "inspect_linux_nvidia", lambda: _READY_STACK)
    gpu = SimpleNamespace(index=0, model="RTX", uuid="GPU-1", memory_used_mib=1, memory_total_mib=2,
                          temperature_c=40, power_draw_w=20.0, pcie_generation_current=4,
                          pcie_width_current=16, ecc_uncorrected=0)
    monkeypatch.setattr(test_plan, "list_nvidia_gpus", lambda: [gpu])
    result = plan._run_nvidia()
    assert result.status == test_plan.STATUS_PASSED
    assert "1 NVIDIA-GPU(s)" in result.summary


# ---------------------------------------------------------------------------
# 6) Netzwerk: ping-Ausgabe ohne rtt-Zeile darf nicht bestehen
# ---------------------------------------------------------------------------

from hardwaretest.tests.network import NetworkRunner, invalid_measurements, threshold_failures  # noqa: E402

_PING_LIMITS = dict(max_packet_loss_percent=0.0, max_latency_avg_ms=100.0,
                    max_jitter_ms=20.0, min_throughput_mbit_s=0.0)

_PING_WITHOUT_RTT = """PING 10.0.0.1 (10.0.0.1) 56(84) bytes of data.
64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.212 ms

--- 10.0.0.1 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3052ms
"""

_PING_WITH_RTT = _PING_WITHOUT_RTT + "rtt min/avg/max/mdev = 0.201/0.230/0.260/0.021 ms\n"


def test_ping_summary_without_rtt_line_fails_thresholds():
    runner = NetworkRunner(TestParameters(10), "10.0.0.1", "ping")
    runner.output_lines = _PING_WITHOUT_RTT.splitlines()
    summary = runner.summary()
    assert summary == {"packet_loss_percent": 0.0}
    assert invalid_measurements(summary, "ping") == ["latency_avg_ms", "jitter_ms"]
    failures = threshold_failures(summary, **_PING_LIMITS)
    assert failures
    assert all(code == "missing_summary" for code, _a, _l in failures)


def test_ping_summary_with_rtt_line_passes_thresholds():
    runner = NetworkRunner(TestParameters(10), "10.0.0.1", "ping")
    runner.output_lines = _PING_WITH_RTT.splitlines()
    summary = runner.summary()
    assert invalid_measurements(summary, "ping") == []
    assert threshold_failures(summary, **_PING_LIMITS) == []


def test_ping_summary_mode_inferred_without_explicit_mode():
    assert threshold_failures({"packet_loss_percent": 0.0}, **_PING_LIMITS)
    assert threshold_failures({"throughput_mbit_s": 500.0}, **_PING_LIMITS) == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, None, "3"])
def test_ping_invalid_measurement_values_fail(bad):
    summary = {"packet_loss_percent": 0.0, "latency_avg_ms": bad, "jitter_ms": 1.0}
    assert "latency_avg_ms" in invalid_measurements(summary, "ping")
    assert threshold_failures(summary, **_PING_LIMITS)


def test_iperf_summary_without_throughput_fails():
    assert threshold_failures({"retransmits": 0.0}, **_PING_LIMITS, mode="iperf3")


def test_threshold_failure_codes_known_to_ui():
    # network_panel._format_threshold_failure kennt nur diese Codes.
    known = {"packet_loss", "latency", "jitter", "throughput", "missing_summary"}
    summary = {"packet_loss_percent": 5.0, "latency_avg_ms": float("nan")}
    for code, _a, _l in threshold_failures(summary, **_PING_LIMITS):
        assert code in known


def test_plan_ping_without_rtt_reports_missing_fields(tmp_path):
    from hardwaretest.core.test_runner import TestResult
    plan = _plan(tmp_path, nvme_enabled=False, ping_enabled=True)
    runner = NetworkRunner(TestParameters(1), "localhost", "ping")
    runner.start = lambda: None
    runner.is_running = lambda: False
    runner.get_result = lambda: TestResult(True, [], 1, 0)
    runner.output_lines = _PING_WITHOUT_RTT.splitlines()
    result = plan._run_process_step("ping", runner, structured_summary=True)
    assert result.status == test_plan.STATUS_FAILED
    assert any("latency_avg_ms" in line and "jitter_ms" in line for line in result.details)
    # Keine Doppelmeldung: Grenzwertzeilen nur, wenn Messwerte vollständig sind
    assert not any("Grenzwert überschritten" in line for line in result.details)


# ---------------------------------------------------------------------------
# 5) Kernel-Journal: Bursts > Seitengröße dürfen keine Einträge verlieren
# ---------------------------------------------------------------------------

from hardwaretest.core import monitoring  # noqa: E402


class _FakeJournal:
    """Simuliert journalctl --after-cursor: liefert Einträge nach dem Cursor, ältester zuerst."""

    def __init__(self, entries):
        self.entries = entries  # Liste von (cursor, message)
        self.calls = []

    def read_page(self, cursor, limit):
        self.calls.append((cursor, limit))
        index = 0
        if cursor:
            index = next(i for i, (c, _m) in enumerate(self.entries) if c == cursor) + 1
        return [json.dumps({"__CURSOR": c, "MESSAGE": m}) for c, m in self.entries[index:index + limit]]


def _kernel_events(monkeypatch, fake, page_size=10, max_per_poll=25):
    monkeypatch.setattr(monitoring.KernelEvents, "__init__", lambda self: None)
    events = monitoring.KernelEvents()
    events.cursor = "c0"
    events.available = True
    events.backlog = False
    events.page_size = page_size
    events.max_entries_per_poll = max_per_poll
    monkeypatch.setattr(events, "_read_page", fake.read_page)
    return events


def test_kernel_events_burst_larger_than_page_is_fully_inspected(monkeypatch):
    entries = [("c0", "boot")]
    for i in range(1, 24):
        entries.append((f"c{i}", f"noise {i}"))
    entries.insert(3, ("cE1", "mce: [Hardware Error]: CPU 0: Machine Check"))
    entries.append(("cE2", "nvme0: I/O error on sector 42"))
    fake = _FakeJournal(entries)
    events = _kernel_events(monkeypatch, fake, page_size=10, max_per_poll=100)
    found = events.poll()
    assert any("Hardware Error" in e for e in found)
    assert any("I/O error" in e for e in found)
    assert events.cursor == "cE2"
    assert not events.backlog
    assert len(fake.calls) >= 3
    assert events.available


def test_kernel_events_cursor_only_advances_to_processed_entry(monkeypatch):
    entries = [("c0", "boot")] + [(f"c{i}", f"noise {i}") for i in range(1, 60)]
    entries.append(("cE", "EXT4-fs error (device sda1): corrupted"))
    fake = _FakeJournal(entries)
    events = _kernel_events(monkeypatch, fake, page_size=10, max_per_poll=30)
    first = events.poll()
    assert events.backlog
    assert any("log burst" in e for e in first)
    assert events.cursor == "c30"
    second = events.poll()
    assert events.cursor == "cE"
    assert any("EXT4-fs error" in e for e in second)
    assert not events.backlog


def test_kernel_events_exactly_one_page_without_backlog(monkeypatch):
    entries = [("c0", "boot")] + [(f"c{i}", f"noise {i}") for i in range(1, 11)]
    fake = _FakeJournal(entries)
    events = _kernel_events(monkeypatch, fake, page_size=10, max_per_poll=100)
    assert events.poll() == []
    assert events.cursor == "c10"
    assert not events.backlog


def test_kernel_events_read_failure_marks_unavailable(monkeypatch):
    def boom(cursor, limit):
        raise subprocess.SubprocessError("journalctl exit 1")

    monkeypatch.setattr(monitoring.KernelEvents, "__init__", lambda self: None)
    events = monitoring.KernelEvents()
    events.cursor, events.available, events.backlog = "c0", True, False
    monkeypatch.setattr(events, "_read_page", boom)
    assert events.poll() == []
    assert not events.available


def test_monitor_session_reports_journal_backlog(tmp_path):
    class Journal:
        available = True
        backlog = True

        def poll(self):
            return ["kernel: log burst; deferred"]

    session = monitoring.MonitorSession(tmp_path, collect=lambda: ({"memory.percent": 10}, []), journal=Journal())
    sample = session.sample()
    assert "kernel journal (backlog)" in sample["unavailable"]
    session.close()


# ---------------------------------------------------------------------------
# 7) polkitd ist kein Authentifizierungsagent
# ---------------------------------------------------------------------------

_PS_SERVER_NO_AGENT = """\
    0 /sbin/init
    0 /usr/lib/polkit-1/polkitd --no-debug
    0 /usr/lib/polkit-1/polkit-agent-helper-1 user
 1000 xfce4-session
 1000 /usr/bin/python3 -m hardwaretest
"""

_PS_LIVE_ISO_LXPOLKIT = _PS_SERVER_NO_AGENT + " 1000 lxpolkit\n"
_PS_OTHER_USER_AGENT = _PS_SERVER_NO_AGENT + " 1001 /usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1\n"


def _fake_ps(monkeypatch, output: str, uid: int = 1000):
    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["ps", "-eo"]
        return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr="")

    monkeypatch.setattr(system_info.subprocess, "run", fake_run)
    monkeypatch.setattr(system_info.os, "getuid", lambda: uid)


def test_polkitd_alone_is_not_an_agent(monkeypatch):
    _fake_ps(monkeypatch, _PS_SERVER_NO_AGENT)
    assert system_info._is_polkit_agent_running() is False


def test_lxpolkit_in_live_iso_is_detected(monkeypatch):
    _fake_ps(monkeypatch, _PS_LIVE_ISO_LXPOLKIT)
    assert system_info._is_polkit_agent_running() is True


def test_agent_of_other_user_does_not_count(monkeypatch):
    _fake_ps(monkeypatch, _PS_OTHER_USER_AGENT, uid=1000)
    assert system_info._is_polkit_agent_running() is False
    _fake_ps(monkeypatch, _PS_OTHER_USER_AGENT, uid=1001)
    assert system_info._is_polkit_agent_running() is True


@pytest.mark.parametrize("command", [
    "/usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1",
    "/usr/lib/x86_64-linux-gnu/libexec/polkit-kde-authentication-agent-1",
    "/usr/lib/x86_64-linux-gnu/polkit-mate/polkit-mate-authentication-agent-1",
    "/usr/bin/xfce-polkit",
    "hyprpolkitagent",
    "/usr/bin/gnome-shell",
])
def test_known_agents_are_detected(monkeypatch, command):
    _fake_ps(monkeypatch, _PS_SERVER_NO_AGENT + f" 1000 {command} --flag\n")
    assert system_info._is_polkit_agent_running() is True


def test_ps_failure_means_no_agent(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("ps missing")

    monkeypatch.setattr(system_info.subprocess, "run", fake_run)
    assert system_info._is_polkit_agent_running() is False


def test_needs_password_without_agent_and_without_nopasswd(monkeypatch):
    monkeypatch.setattr(system_info, "is_running_as_root", lambda: False)
    monkeypatch.setattr(system_info.shutil, "which",
                        lambda name: {"pkexec": "/usr/bin/pkexec", "sudo": "/usr/bin/sudo",
                                      "swapoff": "/usr/sbin/swapoff"}.get(name))
    monkeypatch.setattr(system_info.os, "getuid", lambda: 1000)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["ps", "-eo"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=_PS_SERVER_NO_AGENT, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="a password is required")

    monkeypatch.setattr(system_info.subprocess, "run", fake_run)
    assert system_info.needs_password_for_swap() is True
