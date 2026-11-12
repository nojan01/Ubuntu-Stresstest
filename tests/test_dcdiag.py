import pytest

from hardwaretest.core.hardware_inventory import CpuPackage, parse_cpuinfo
from hardwaretest.core.test_plan import TestPlanConfig, TestPlanExecutor
from hardwaretest.tests import dcdiag


SUCCESS = "Test completed successfully. No issues detected."


@pytest.mark.parametrize("vendor,model,expected", [
    ("GenuineIntel", "Intel(R) Xeon(R) Gold 6130", True),
    ("GenuineIntel", "Intel Xeon CPU E5-2680 v4", True),
    ("GenuineIntel", "Intel Core i9", False),
    ("AuthenticAMD", "AMD EPYC", False),
    ("AuthenticAMD", "Xeon", False),
    ("?", "Intel Xeon", False),
    ("GenuineIntel", "?", False),
    ("GenuineIntel", "FakeXeon CPU", False),
    ("GenuineIntel", "Xeonish", False),
])
def test_inventory_gate(monkeypatch, vendor, model, expected):
    packages = parse_cpuinfo(
        f"processor : 0\nvendor_id : {vendor}\nmodel name : {model}\n"
    )
    monkeypatch.setattr(dcdiag, "read_cpu_packages", lambda: packages)
    monkeypatch.setattr(dcdiag.shutil, "which", lambda name: "/usr/bin/dcdiag")
    assert dcdiag.available_dcdiag_path() == ("/usr/bin/dcdiag" if expected else None)


def test_missing_inventory_binary_and_cross_socket_evidence(monkeypatch):
    monkeypatch.setattr(dcdiag.shutil, "which", lambda name: "/usr/bin/dcdiag")
    monkeypatch.setattr(dcdiag, "read_cpu_packages", lambda: [])
    assert dcdiag.available_dcdiag_path() is None
    monkeypatch.setattr(dcdiag, "read_cpu_packages", lambda: [
        CpuPackage("0", vendor="GenuineIntel", model_name="Core"),
        CpuPackage("1", vendor="?", model_name="Xeon"),
    ])
    assert dcdiag.available_dcdiag_path() is None
    monkeypatch.setattr(dcdiag, "read_cpu_packages", lambda: [
        CpuPackage("0", vendor="GenuineIntel", model_name="Intel Xeon"),
    ])
    monkeypatch.setattr(dcdiag.shutil, "which", lambda name: None)
    assert dcdiag.available_dcdiag_path() is None


def make_tool(tmp_path, body):
    tool = tmp_path / "dcdiag"
    tool.write_text("#!/bin/sh\n" + body)
    tool.chmod(0o755)
    return str(tool)


def finish(runner):
    runner._process.wait(timeout=5)
    assert runner.wait_finished(5)
    return runner.get_result()


@pytest.mark.parametrize("message,code,outcome", [
    (SUCCESS, 0, "passed"),
    ("", 0, "unknown"),
    ("unrecognized new result", 0, "unknown"),
    (SUCCESS, 7, "unknown"),
    ("This processor is not supported by this version of the tool.", 0, "unsupported"),
    ("Test failed (#b5693ba).", 0, "failed"),
    ("Test failed. Contact your system manufacturer.", 1, "failed"),
    ("Test completed. Results are inconclusive due to an outdated version of microcode.", 0, "inconclusive"),
    ("Test completed. Results are inconclusive due to the system exceeding\ntemperature limits.", 0, "inconclusive"),
    ("Test failed (#b5693ba).\nTest completed. Results are inconclusive, one or more machine check errors occurred.", 1, "inconclusive"),
    ("Test completed successfully. One or more machine check errors occurred, please check the system logs.", 0, "inconclusive"),
])
def test_documented_output_not_exit_code_defines_result(tmp_path, message, code, outcome):
    tool = make_tool(tmp_path, f"cat <<'OUTPUT'\n{message}\nOUTPUT\nexit {code}\n")
    runner = dcdiag.DcDiagRunner(tool)
    assert runner.build_command() == [tool]
    runner.start()
    result = finish(runner)
    assert result.outcome == outcome
    assert result.passed == (outcome == "passed")
    assert result.exit_code == code
    if outcome in {"unknown", "inconclusive", "unsupported"}:
        assert "FEHLER GEFUNDEN" not in result.status_text


def test_stop_and_timeout_do_not_pass_even_after_success_line(tmp_path):
    tool = make_tool(tmp_path, f"echo '{SUCCESS}'\nexec sleep 30\n")
    runner = dcdiag.DcDiagRunner(tool)
    runner.stop_grace_seconds = 1
    runner.start()
    runner.stop(aborted=True)
    assert runner.wait_finished(5)
    assert runner.get_result().outcome == "cancelled"
    assert not runner.get_result().passed

    runner = dcdiag.DcDiagRunner(tool)
    runner.params.duration_seconds = 0.1
    runner.terminate_grace_seconds = 1
    runner.start()
    result = finish(runner)
    assert result.outcome == "timeout"
    assert not result.passed


def test_missing_executable_surfaces_start_error(tmp_path):
    runner = dcdiag.DcDiagRunner(str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        runner.start()
    assert runner.get_result() is None


def test_never_automatically_selected(tmp_path):
    executor = TestPlanExecutor(TestPlanConfig(output_dir=tmp_path))
    assert "dcdiag" not in executor.selected_step_keys()
    assert not hasattr(executor.config, "dcdiag_enabled")
