import subprocess

from hardwaretest.core.nvidia import (
    LinuxNvidiaStatus,
    list_nvidia_gpus,
    parse_cuda_version,
    parse_nvidia_gpu_csv,
)
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.nvidia_runner import DcgmDiagnosticRunner


def test_nvidia_csv_parser_includes_ecc_and_cuda():
    devices = parse_nvidia_gpu_csv(
        "0, GPU-abc, NVIDIA L40S, 570.1, 46068, 1024, 41, 70.5, 350.0, "
        "82, 1980, 1980, 8001, 4, 4, 16, 16\n",
        cuda_version="12.8",
        ecc_csv="0, Enabled, 2, 0\n",
    )
    assert len(devices) == 1
    gpu = devices[0]
    assert gpu.model == "NVIDIA L40S"
    assert gpu.cuda_version == "12.8"
    assert gpu.memory_total_mib == 46068
    assert gpu.ecc_mode == "Enabled"
    assert gpu.ecc_corrected == 2
    assert gpu.ecc_uncorrected == 0
    assert gpu.utilization_gpu_percent == 82
    assert gpu.clock_graphics_mhz == 1980
    assert gpu.clock_sm_mhz == 1980
    assert gpu.clock_memory_mhz == 8001
    assert gpu.pcie_generation_current == 4
    assert gpu.pcie_generation_max == 4
    assert gpu.pcie_width_current == 16
    assert gpu.pcie_width_max == 16


def test_nvidia_csv_parser_accepts_unsupported_telemetry_fields():
    devices = parse_nvidia_gpu_csv(
        "1, GPU-old, NVIDIA Tesla, 535.1, 16384, 0, 35, N/A, N/A, "
        "N/A, N/A, N/A, N/A, N/A, N/A, N/A, N/A\n"
    )
    assert len(devices) == 1
    assert devices[0].power_draw_w is None
    assert devices[0].utilization_gpu_percent is None
    assert devices[0].pcie_width_current is None


def test_cuda_version_parser():
    assert parse_cuda_version("NVIDIA-SMI 570.1  CUDA Version: 12.8") == "12.8"
    assert parse_cuda_version("driver unavailable") == "N/A"


def test_nvidia_discovery_builds_compatible_queries(monkeypatch=None):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command == ["nvidia-smi"]:
            return subprocess.CompletedProcess(command, 0, "CUDA Version: 12.8", "")
        if "ecc.mode.current" in command[1]:
            return subprocess.CompletedProcess(command, 0, "0, Enabled, 0, 0\n", "")
        return subprocess.CompletedProcess(
            command, 0,
            "0, GPU-a, NVIDIA A100, 570.1, 40960, 0, 35, 45, 250, "
            "10, 1410, 1410, 1215, 4, 4, 16, 16\n",
            "",
        )

    devices = list_nvidia_gpus(run=fake_run)
    assert len(devices) == 1
    assert len(commands) == 3
    assert "utilization.gpu" in commands[0][1]
    assert "pcie.link.width.current" in commands[0][1]


def test_dcgm_command_targets_every_selected_gpu(monkeypatch=None):
    runner = DcgmDiagnosticRunner(TestParameters(0), [3, 0, 1], 2)
    # Avoid making the unit test depend on an installed DCGM package.
    import hardwaretest.tests.nvidia_runner as module
    original = module.dcgmi_available
    module.dcgmi_available = lambda: True
    try:
        assert runner.build_command() == [
            "dcgmi", "diag", "--run", "2", "--entity-id", "gpu:0,gpu:1,gpu:3", "--json"
        ]
    finally:
        module.dcgmi_available = original


def test_dcgm_json_statuses_are_counted():
    runner = DcgmDiagnosticRunner(TestParameters(0), [0], 1)
    runner.output_lines = [
        'prefix {"tests":[{"status":"Pass"},{"status":"Fail"},{"status":"Skip"}]} suffix'
    ]
    assert runner.status_counts() == {"pass": 1, "fail": 1, "warn": 0, "skip": 1}


def test_linux_driver_check_requires_complete_working_stack():
    ready = LinuxNvidiaStatus(
        ("0000:41:00.0",), ("nvidia",), ("nvidia", "nvidia_uvm"),
        ("nvidia0", "nvidiactl"), True, True,
    )
    missing_module = LinuxNvidiaStatus(
        ("0000:41:00.0",), ("nouveau",), (), (), False, False,
    )
    assert ready.hardware_present and ready.driver_ready
    assert missing_module.hardware_present and not missing_module.driver_ready
