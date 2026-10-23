from unittest import mock

from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests import gpu_stress_script, nvidia_runner
from hardwaretest.tests.nvidia_runner import GpuStressRunner


def test_command_contains_gpus_duration_and_memory():
    runner = GpuStressRunner(TestParameters(duration_seconds=600), [1, 0, 1], memory_percent=70)
    with mock.patch.object(nvidia_runner, "cuda_driver_available", return_value=True):
        cmd = runner.build_command()
    assert cmd[-6:] == ["--gpus", "0,1", "--duration", "595", "--memory-percent", "70"]


def test_error_lines_are_collected():
    runner = GpuStressRunner(TestParameters(duration_seconds=60), [0])
    runner._check_line_for_errors("GPU 0: 1200 MB VRAM geprüft")
    runner._check_line_for_errors("GPU 0: FEHLER: 3 Speicherfehler")
    assert runner._collected_errors == ["GPU 0: FEHLER: 3 Speicherfehler"]


def test_missing_libcuda_fails(capsys):
    with mock.patch.object(gpu_stress_script, "run_gpu", return_value=1):
        rc = gpu_stress_script.main(["--gpus", "0", "--duration", "1"])
    assert rc == 1
