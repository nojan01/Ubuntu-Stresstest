"""Exercise the real broker protocol and runner, using harmless fake tools."""

import csv
import tempfile
from pathlib import Path

import pytest

from hardwaretest.core import root_broker, test_plan
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests import opendcdiag


PASS_REPORT = "tests:\n- {test: eigen_gemm, result: pass}\nexit: pass\n"
FAIL_REPORT = """tests:
- test: eigen_gemm
  result: fail
  threads:
  - id: {logical: 35, package: 1, core: 3}
    state: failed
exit: fail
"""


@pytest.fixture
def diagnostic_broker(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    tools = tmp_path / "bin"
    tools.mkdir()
    nvme = tools / "nvme"
    nvme.write_text('#!/bin/sh\nprintf \'{"critical_warning":0}\\n\'\n')
    nvme.chmod(0o755)
    fake_pkexec = tmp_path / "fake-pkexec"
    fake_pkexec.write_text(f'#!/bin/sh\nPATH="{tools}:$PATH" exec "$@"\n')
    fake_pkexec.chmod(0o755)
    monkeypatch.setattr(root_broker, "resource_path", lambda *parts: bundle.joinpath(*parts))
    monkeypatch.setattr(opendcdiag, "resource_path", lambda *parts: bundle.joinpath(*parts))
    broker = root_broker.RootBroker(str(fake_pkexec))

    def configure(variant="opendcdiag", report=PASS_REPORT, exit_code=0):
        for name in ("opendcdiag", "opendcdiag-generic"):
            binary = bundle / "vendor" / "opendcdiag" / name
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text(
                '#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\n'
                f'cat > "$2" <<\'REPORT\'\n{report}REPORT\nexit {exit_code}\n'
            )
            binary.chmod(0o755)
        flags = opendcdiag._HASWELL_FLAGS if variant == "opendcdiag" else set()
        monkeypatch.setattr(opendcdiag, "cpu_flags", lambda: flags)
        return opendcdiag.default_opendcdiag_path()

    yield broker, configure
    broker.close()


@pytest.mark.parametrize("variant", ["opendcdiag", "opendcdiag-generic"])
def test_combined_plan_runs_both_variants_in_existing_session(diagnostic_broker, monkeypatch,
                                                              tmp_path, variant):
    broker, configure = diagnostic_broker
    binary = configure(variant)
    broker.start()
    session_pid = broker._proc.pid
    seen_sessions = []
    logs = []
    config = test_plan.TestPlanConfig(
        output_dir=tmp_path / "reports", cpu_enabled=False, ram_enabled=False,
        nvidia_enabled=False, nvme_enabled=True, opendcdiag_enabled=True,
        opendcdiag_seconds=1, rounds=2,
    )
    executor = test_plan.TestPlanExecutor(config, log_fn=logs.append)
    executor._root = broker

    def nvme_step():
        seen_sessions.append(broker._proc.pid)
        reply = broker.run(["nvme", "smart-log", "/dev/nvme0", "-o", "json"], timeout=5)
        assert reply.returncode == 0
        assert "critical_warning" in reply.stdout
        return executor._instant_result("nvme", test_plan.STATUS_PASSED, "Mock SMART OK")

    monkeypatch.setattr(executor, "_run_nvme", nvme_step)
    result = executor.execute()
    assert result.status == test_plan.STATUS_PASSED
    assert result.completed_rounds == 2
    assert [step.key for step in result.steps] == ["opendcdiag", "nvme"]
    assert all(step.status == test_plan.STATUS_PASSED for step in result.steps)
    assert seen_sessions == [session_pid, session_pid]
    assert sum(f"Starte (root): {binary} " in line for line in logs) == 2
    assert "Befehl nicht erlaubt" not in "\n".join(logs)
    assert "OpenDCDiag" in result.text_report.read_text()
    assert "OpenDCDiag" in result.html_report.read_text()
    with result.csv_report.open() as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 5
    assert not broker.is_active()


@pytest.mark.parametrize("report,exit_code,expected", [
    (FAIL_REPORT, 1, "S1 Core 3 (CPU 35)"),
    ("", 0, "Protokoll fehlt"),
])
def test_broker_preserves_hardware_failures_and_missing_report_errors(
    diagnostic_broker, tmp_path, report, exit_code, expected,
):
    broker, configure = diagnostic_broker
    configure(report=report, exit_code=exit_code)
    executor = test_plan.TestPlanExecutor(test_plan.TestPlanConfig(output_dir=tmp_path))
    executor._root = broker
    result = executor._run_opendcdiag()
    assert result.status == test_plan.STATUS_FAILED
    assert any(expected in line for line in result.details)
    assert "Befehl nicht erlaubt" not in result.summary


def test_runner_uses_private_report_directory_with_broker(diagnostic_broker):
    broker, configure = diagnostic_broker
    runner = opendcdiag.OpenDcDiagRunner(TestParameters(1), binary_path=configure())
    runner.root_broker = broker
    # Keep the harmless tool running briefly so we can inspect its directory.
    binary = Path(runner.binary_path)
    binary.write_text(binary.read_text().replace("#!/bin/sh\n", "#!/bin/sh\nsleep 0.1\n", 1))
    runner.start()
    directory = runner.yaml_path.parent
    assert directory.stat().st_mode & 0o777 == 0o700
    runner._process.wait(timeout=5)
    runner._stdout_thread.join(timeout=5)
    assert runner.get_result().passed
    assert not directory.exists()


def test_broker_rejects_arbitrary_executables_flags_and_report_paths(diagnostic_broker, tmp_path):
    broker, configure = diagnostic_broker
    binary = configure()
    with tempfile.TemporaryDirectory(prefix="hardwaretest-opendcdiag-") as directory:
        output = str(Path(directory) / "opendcdiag.yaml")
        tail = ["-T", "1s", "--strict-runtime", "-o", output]
        invalid = [
            [str(tmp_path / "opendcdiag"), *tail],
            ["opendcdiag", *tail],
            [binary, *tail, "--other-option"],
            [binary, *tail[:3], "-o", str(tmp_path / "outside.yaml")],
            [binary, "-T", "infinite", *tail[2:]],
            [binary, "-T", "0s", *tail[2:]],
            [binary, "--load", "plugin.so"],
            [binary, *tail, "--cpuset="],
            [binary, *tail, "--cpuset=p1;id"],
            [binary, *tail[:3], "-o", directory + "/../hardwaretest-opendcdiag-x/opendcdiag.yaml"],
        ]
        for command in invalid:
            with pytest.raises(RuntimeError, match="Befehl nicht erlaubt"):
                broker.submit(command)
        Path(output).symlink_to(tmp_path / "victim")
        with pytest.raises(RuntimeError, match="Befehl nicht erlaubt"):
            broker.submit([binary, *tail])


def test_broker_accepts_cpuset_and_configured_temp_root(diagnostic_broker, monkeypatch, tmp_path):
    broker, configure = diagnostic_broker
    binary = configure()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with tempfile.TemporaryDirectory(prefix="hardwaretest-opendcdiag-") as directory:
        output = Path(directory) / "opendcdiag.yaml"
        reply = broker.run([binary, "-T", "1s", "--strict-runtime", "-o", str(output),
                            "--cpuset=p1"], timeout=5)
        assert reply.returncode == 0
        assert output.read_text() == PASS_REPORT
