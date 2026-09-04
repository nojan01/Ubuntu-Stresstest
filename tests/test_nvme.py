import json
import subprocess

from hardwaretest.core.nvme import (
    controller_path,
    is_self_test_in_progress_error,
    parse_self_test_status,
    parse_smart_log,
    read_nvme_self_test_status,
)
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.fio_runner import FioNvmeFullReadRunner, FioNvmeReadBenchmarkRunner


def test_controller_path_accepts_controller_and_namespace():
    assert controller_path("/dev/nvme0") == "/dev/nvme0"
    assert controller_path("/dev/nvme12n3") == "/dev/nvme12"


def test_smart_log_parses_health_and_converts_kelvin():
    raw = json.dumps({
        "critical_warning": 0,
        "temperature": 303,
        "avail_spare": 98,
        "percentage_used": 4,
        "power_on_hours": 1200,
        "unsafe_shutdowns": 2,
        "media_errors": 0,
        "num_err_log_entries": 1,
        "data_units_read": 1234,
        "data_units_written": 5678,
    })
    health = parse_smart_log("/dev/nvme0n1", raw)
    assert health.device == "/dev/nvme0"
    assert health.temperature_c == 30
    assert health.available_spare == 98
    assert health.percentage_used == 4
    assert health.healthy


def test_self_test_status_parses_current_operation_and_completion():
    status = parse_self_test_status(json.dumps({
        "Current Device Self-Test Operation": "0x2",
        "Current Device Self-Test Completion": 37,
    }))
    assert status.active
    assert status.completion_percent == 37


def test_self_test_status_reads_completed_result_from_log_entry():
    status = parse_self_test_status(json.dumps({
        "current_self_test_operation": 0,
        "self_test_results": [{"self_test_result": 0, "self_test_code": 2}],
    }))
    assert not status.active
    assert status.result_code == 0
    assert status.passed is True


def test_self_test_in_progress_message_variants_are_detected():
    assert is_self_test_in_progress_error(
        "NVMe status: Device Self-Test In Progress"
    )
    assert is_self_test_in_progress_error(
        "device self test operation in progress"
    )


def test_self_test_status_uses_self_test_log_command():
    seen = []

    def fake_run(command, **_kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(
            command, 0,
            stdout='{"current_self_test_operation": 1, "current_self_test_completion": 42}',
            stderr="",
        )

    status = read_nvme_self_test_status("/dev/nvme0n1", run=fake_run)
    assert status.active and status.completion_percent == 42
    assert seen == [["nvme", "self-test-log", "/dev/nvme0", "-o", "json"]]


def test_nvme_read_benchmark_is_strictly_read_only_and_parses_metrics():
    runner = FioNvmeReadBenchmarkRunner(
        TestParameters(duration_seconds=30), "/dev/nvme0n1", rw="randread", block_size="4k", ioengine="libaio"
    )
    assert "rw=randread" in runner._job_lines()
    assert "filename=/dev/nvme0n1" in runner._job_lines()
    runner.output_lines = [
        '{"jobs": [{"read": {"bw_bytes": 1048576000, "iops": 250000, "lat_ns": {"mean": 400000}}}]}'
    ]
    assert runner.summary() == {"throughput_mib_s": 1000.0, "iops": 250000.0, "latency_ms": 0.4}


def test_nvme_full_read_has_no_write_or_verification_options():
    runner = FioNvmeFullReadRunner(
        TestParameters(duration_seconds=0), "/dev/nvme0n1", ioengine="libaio"
    )
    lines = runner._job_lines()
    assert "rw=read" in lines
    assert "filename=/dev/nvme0n1" in lines
    assert not any(line.startswith("verify=") for line in lines)
    assert not any(line.startswith("do_verify=") for line in lines)
    command = runner.build_command()
    try:
        assert "--eta=always" in command
        assert "--eta-interval=1000" in command
        assert "--status-interval=1" in command
    finally:
        runner._cleanup_job_file()


def test_nvme_full_read_parses_live_fio_progress():
    runner = FioNvmeFullReadRunner(
        TestParameters(duration_seconds=0), "/dev/nvme0n1", ioengine="libaio",
        total_bytes=4 * 1024 * 1024,
    )
    runner._record_progress("nvme_full_read: (groupid=0, jobs=1): err=0")
    runner._record_progress("  read: IOPS=256, BW=1024KiB/s (1049kB/s)(1MiB/1000msec)")
    assert runner.progress() == 0.25
    runner._record_progress("  read: IOPS=256, BW=1024KiB/s (1049kB/s)(2MiB/2000msec)")
    assert runner.progress() == 0.5
    runner._record_progress("Jobs: 1 (f=1): [R(1)][75.0% done][r=1200MiB/s]")
    assert runner.progress() == 0.75

    parallel = FioNvmeFullReadRunner(
        TestParameters(duration_seconds=0), ["/dev/nvme0n1", "/dev/nvme1n1"],
        ioengine="libaio", total_bytes=8 * 1024 * 1024,
    )
    parallel._record_progress("nvme0: (groupid=0, jobs=1): err=0")
    parallel._record_progress("  read: IOPS=1, BW=1MiB/s (1MB/s)(2MiB/2000msec)")
    parallel._record_progress("nvme1: (groupid=1, jobs=1): err=0")
    parallel._record_progress("  read: IOPS=1, BW=1MiB/s (1MB/s)(1MiB/1000msec)")
    assert parallel.progress() == 0.375


def test_nvme_multi_drive_jobs_run_all_selected_namespaces():
    devices = ["/dev/nvme0n1", "/dev/nvme1n1", "/dev/nvme2n1"]
    benchmark = FioNvmeReadBenchmarkRunner(
        TestParameters(duration_seconds=30), devices, ioengine="libaio"
    )
    full_read = FioNvmeFullReadRunner(
        TestParameters(duration_seconds=0), devices, ioengine="libaio"
    )
    for runner in (benchmark, full_read):
        lines = runner._job_lines()
        assert [line for line in lines if line.startswith("filename=")] == [
            f"filename={device}" for device in devices
        ]
        assert "group_reporting=0" in lines
        assert "rw=read" in lines
