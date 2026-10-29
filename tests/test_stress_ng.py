import shutil

import pytest

from hardwaretest.core import test_plan
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.stress_ng import MIX_STRESSORS, StressNgRunner, mix_worker_counts


def test_mix_worker_counts_cover_all_stressors():
    counts = mix_worker_counts(4)
    assert [name for name, _ in counts] == list(MIX_STRESSORS)
    assert all(n >= 1 for _, n in counts)
    counts = mix_worker_counts(25)
    assert sum(n for _, n in counts) == 25
    assert max(n for _, n in counts) - min(n for _, n in counts) <= 1


def test_mix_command_verifies_without_quiet():
    cmd = StressNgRunner(TestParameters(30, cpu_cores=22, nice_level=0), mode="mix").build_command()
    assert cmd[:3] == ["stress-ng", "-t", "30s"]
    assert "-q" not in cmd and "--verify" in cmd
    assert cmd[cmd.index("--cpu") + 1] == "2"
    assert cmd[cmd.index("--cpu-method") + 1] == "all"
    for name in MIX_STRESSORS:
        assert f"--{name}" in cmd


def test_cpu_mode_now_verifies():
    cmd = StressNgRunner(TestParameters(30, cpu_cores=4), mode="cpu").build_command()
    assert "-q" in cmd and "--verify" in cmd


def test_mix_error_lines():
    runner = StressNgRunner(TestParameters(30, cpu_cores=4), mode="mix")
    for line in (
        "stress-ng: info:  [1] failed: 0",
        "stress-ng: info:  [1] passed: 11: cpu (1) matrix (1)",
        "stress-ng: info:  [1] metrics untrustworthy: 0",
        "stress-ng: info:  [2] zlib: zlib checksum values matches 0x1/0x1 (deflate/inflate)",
    ):
        runner._check_line_for_errors(line)
    assert runner._collected_errors == []
    runner._check_line_for_errors("stress-ng: fail:  [7] fma: fma verification failed")
    runner._check_line_for_errors("stress-ng: info:  [1] failed: 1: fma (1)")
    assert runner._collected_errors == [
        "stress-ng: fail:  [7] fma: fma verification failed",
        "stress-ng: Ergebnisprüfung fehlgeschlagen: fma (1)",
    ]


def test_plan_uses_selected_cpu_mode(monkeypatch, tmp_path):
    seen = {}

    def fake_step(self, key, runner, **kwargs):
        seen["mode"] = runner.mode
        return None

    monkeypatch.setattr(test_plan.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(test_plan.TestPlanExecutor, "_run_process_step", fake_step)
    for mode, expected in (("mix", "mix"), ("cpu", "cpu"), ("bogus", "cpu")):
        executor = test_plan.TestPlanExecutor(test_plan.TestPlanConfig(output_dir=tmp_path, cpu_mode=mode))
        executor._run_cpu()
        assert seen["mode"] == expected


@pytest.mark.skipif(not shutil.which("stress-ng"), reason="stress-ng fehlt")
def test_mix_stressors_exist_and_verify():
    import subprocess

    out = subprocess.run(["stress-ng", "--verifiable"], capture_output=True, text=True).stdout.split()
    assert set(MIX_STRESSORS) <= set(out)


def test_nice_level_uses_nice_command_not_stressor(monkeypatch):
    from hardwaretest.tests.memory_controller import MemoryControllerRunner

    monkeypatch.setattr("hardwaretest.tests.stress_ng.shutil.which", lambda name: f"/usr/bin/{name}")
    for runner in (StressNgRunner(TestParameters(30, cpu_cores=4), mode="cpu"),
                   MemoryControllerRunner(TestParameters(30, cpu_cores=4))):
        cmd = runner.build_command()
        assert cmd[:4] == ["/usr/bin/nice", "-n", "19", "stress-ng"]
        assert "--nice" not in cmd
