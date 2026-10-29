import subprocess
from pathlib import Path

from hardwaretest.core import test_plan
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests import opendcdiag
from hardwaretest.tests.opendcdiag import OpenDcDiagRunner, load_report, parse_report

PASS_YAML = """\
command-line: 'opendcdiag -T 10s -o x.yaml'
version: opendcdiag-b26f647e28a6
cpu-info:
- { logical: 0, package: 0, core: 0, thread: 0 }   # 0
tests:
- test: eigen_gemm
  result: pass
  time-at-end: { elapsed: 1.0, now: !!timestamp '2026-10-28T14:53:13Z' }
- test: eigen_gemm
  result: pass
- test: ifs
  result: skip
  skip-category: RuntimeSkipCategory
  skip-reason: 'IFS not supported'
- test: ifs
  result: skip
  skip-reason: 'IFS not supported'
exit: pass
"""

FAIL_YAML = """\
tests:
- test: zstd
  result: fail
  threads:
  - thread: 3
    id: { logical: 35, package: 1, core: 3, thread: 1 }
    state: failed
    messages:
    - { level: info, text: 'start' }
    - level: error
      text: 'E> Data miscompare'
      data-miscompare: { description: 'output buffer', type: uint8_t, offset: [12],
                         address: '0x1', actual: '0x0f', expected: '0x0e', mask: '0x01' }
  - thread: 4
    id: { logical: 36, package: 1, core: 4, thread: 0 }
    messages: []
  fail: { cpu-mask: 'x', seed: 'AES:1', time-to-fail: 2.5 }
- test: eigen_svd
  result: crash
  result-details: { crashed: true, core-dump: false, code: 11, reason: 'Segmentation fault' }
- test: zlib
  result: pass
exit: fail
"""


def _report(text: str, tmp_path: Path):
    path = tmp_path / "log.yaml"
    path.write_text(text)
    return load_report(path)


def test_parse_pass_counts_and_skips(tmp_path):
    report = _report(PASS_YAML, tmp_path)
    assert report.exit == "pass"
    assert report.counts["pass"] == 2
    assert report.failures == []
    assert report.skipped == {"ifs": "IFS not supported"}


def test_parse_failure_names_socket_and_core(tmp_path):
    report = _report(FAIL_YAML, tmp_path)
    assert report.failures[0].startswith("OpenDCDiag fail: zstd – S1 Core 3 (CPU 35): E> Data miscompare")
    assert "expected=0x0e, actual=0x0f" in report.failures[0]
    assert len([line for line in report.failures if "zstd" in line]) == 1
    assert report.failures[1] == "OpenDCDiag crash: eigen_svd – CPU ?: Segmentation fault"
    assert report.failed_cpus == ["S1 Core 3 (CPU 35)"]


def test_parse_exit_fail_without_details():
    assert parse_report({"tests": [], "exit": "invalid"}).failures == ["OpenDCDiag exit: invalid"]
    assert parse_report(None).failures == []


def test_unreadable_log_returns_none(tmp_path):
    assert load_report(tmp_path / "missing.yaml") is None
    assert _report("tests: [unclosed", tmp_path) is None
    assert _report("", tmp_path) is None
    assert _report("[]", tmp_path) is None
    assert _report("tests: []", tmp_path) is None


def test_binary_selection_by_cpu_flags(monkeypatch, tmp_path):
    for name in ("opendcdiag", "opendcdiag-generic"):
        (tmp_path / name).write_text("")
    monkeypatch.setattr(opendcdiag, "resource_path", lambda *parts: tmp_path / parts[-1])
    full = {"avx2", "bmi2", "fma", "movbe", "f16c", "sse4_2"}
    assert opendcdiag.default_opendcdiag_path(full).endswith("/opendcdiag")
    assert opendcdiag.default_opendcdiag_path({"avx", "sse4_2"}).endswith("/opendcdiag-generic")


def test_cpu_flags_reads_first_flags_line(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nflags\t\t: fpu avx2 bmi2\n")
    assert opendcdiag.cpu_flags(str(cpuinfo)) == {"fpu", "avx2", "bmi2"}
    assert opendcdiag.cpu_flags(str(tmp_path / "none")) == set()


def test_command_uses_strict_runtime_and_cpuset():
    runner = OpenDcDiagRunner(TestParameters(90), binary_path="/bin/odcd", cpuset=" p1 ")
    cmd = runner.build_command()
    assert cmd[:4] == ["/bin/odcd", "-T", "90s", "--strict-runtime"]
    assert cmd[-1] == "--cpuset=p1"
    assert runner.watchdog_slack_seconds > 0


def test_stop_without_start_keeps_temp_directory(tmp_path):
    runner = OpenDcDiagRunner(TestParameters(5), binary_path="/bin/true")
    runner.stop()
    assert Path(opendcdiag.tempfile.gettempdir()).is_dir()
    assert runner.get_result() is not None


def test_runner_evaluates_yaml_and_cleans_up(tmp_path):
    script = tmp_path / "fake-odcd"
    script.write_text(
        "#!/bin/sh\nwhile [ \"$1\" != -o ]; do shift; done\n"
        f"cat > \"$2\" <<'EOF'\n{FAIL_YAML}EOF\necho 'exit: fail'\nexit 1\n"
    )
    script.chmod(0o755)
    runner = OpenDcDiagRunner(TestParameters(5), binary_path=str(script))
    runner.check_ras = False
    runner.use_sudo = False
    runner.start()
    tmp_dir = runner.yaml_path.parent  # _tmp_dir ist nach schnellem Ende evtl. schon None
    runner._process.wait(timeout=10)
    runner._stdout_thread.join(timeout=10)
    result = runner.get_result()
    assert result is not None and not result.passed
    assert any("S1 Core 3" in line for line in result.errors)
    assert not tmp_dir.exists()
    assert "Kerne: S1 Core 3 (CPU 35)" in runner.summary_text()
    assert "Cores: S1 Core 3" in runner.summary_text("en")


def test_plan_step_is_optional_and_skipped_without_binary(monkeypatch, tmp_path):
    config = test_plan.TestPlanConfig(output_dir=tmp_path)
    assert "opendcdiag" not in test_plan.TestPlanExecutor(config).selected_step_keys()
    config = test_plan.TestPlanConfig(output_dir=tmp_path, opendcdiag_enabled=True, language="en")
    executor = test_plan.TestPlanExecutor(config)
    assert "opendcdiag" in executor.selected_step_keys()
    monkeypatch.setattr(test_plan, "opendcdiag_available", lambda: False)
    result = executor._run_opendcdiag()
    assert result.status == test_plan.STATUS_SKIPPED
    assert result.summary == "OpenDCDiag is not installed."
    assert result.name == "OpenDCDiag – silent data errors per core"


def test_runner_prefixes_sudo_only_when_allowed(monkeypatch, tmp_path):
    from hardwaretest.tests import opendcdiag as mod

    runner = OpenDcDiagRunner(TestParameters(5), binary_path="/x/opendcdiag")
    runner._work_dir = str(tmp_path)
    assert runner.build_command()[0] == "/x/opendcdiag"
    runner._sudo = True
    assert runner.build_command()[:3] == ["sudo", "-n", "/x/opendcdiag"]

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(mod.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert not mod.sudo_allowed("/x/opendcdiag")
    assert calls == [["sudo", "-n", "/x/opendcdiag", "--version"]]
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    assert mod.runs_as_root("/x/opendcdiag")
