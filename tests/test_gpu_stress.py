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


def test_ptx_is_pure_ascii():
    # ptxas bricht bei Nicht-ASCII-Zeichen (auch in Kommentaren) mit
    # "Unexpected non-ASCII character" ab -> CUDA_ERROR_INVALID_PTX (218).
    bad = [(i, line) for i, line in enumerate(gpu_stress_script.PTX.splitlines(), 1)
           if not line.isascii()]
    assert bad == []


def test_ptx_assembles_with_ptxas(tmp_path):
    import shutil
    import subprocess

    import pytest

    ptxas = shutil.which("ptxas")
    if ptxas is None:
        pytest.skip("ptxas nicht installiert")
    src = tmp_path / "ht.ptx"
    src.write_text(gpu_stress_script.PTX)
    proc = subprocess.run([ptxas, "-arch=sm_52", "-o", str(tmp_path / "ht.cubin"), str(src)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_load_ptx_reports_jit_log():
    import ctypes

    class Lib:
        def cuModuleLoadDataEx(self, module, ptx, count, options, values):
            buf = ctypes.cast(values[0], ctypes.c_char_p)
            ctypes.memmove(buf, b"ptxas fatal : Zeile 7", 22)
            return 218

        def cuGetErrorString(self, code, out):
            return 0

    cuda = gpu_stress_script.Cuda(lib=Lib())
    with mock.patch.object(gpu_stress_script, "CudaError", gpu_stress_script.CudaError):
        try:
            cuda.load_ptx(".version 6.4")
        except gpu_stress_script.CudaError as exc:
            assert "218" in str(exc) and "ptxas fatal" in str(exc)
        else:
            raise AssertionError("CudaError erwartet")
