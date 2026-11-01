"""Regressionstests für den fio-Runner (Befunde 1, 6 und 7 des Code-Reviews)."""

import os
import signal
import stat
import subprocess
import time

import pytest

from hardwaretest.core.root_broker import RootBroker
from hardwaretest.core.test_runner import TestExecutionError, TestParameters
from hardwaretest.tests import fio_runner
from hardwaretest.tests.fio_runner import (
    AUTHORIZED_MARKER,
    PKEXEC_STOP_WRAPPER,
    FioDeviceSweepRunner,
    FioDestructiveRunner,
    FioNvmeFullReadRunner,
    FioRunner,
    _job_tokens,
)


def _runner(rw="randwrite", size_mb=1024, num_jobs=4, duration=120, verify=True):
    return FioRunner(TestParameters(duration), "/mnt/test/fio.bin", rw, "128k", size_mb, 16,
                     num_jobs, ioengine="psync", verify=verify)


# ----------------------------------------------------------------- Befund 6: Watchdog-Puffer


def test_watchdog_slack_exceeds_runtime_buffer_and_verify_estimate():
    runner = _runner(duration=120)
    slack = runner.watchdog_slack_seconds
    assert slack >= 60 + runner.estimated_verify_seconds()
    assert runner.estimated_verify_seconds() > 0
    long_runner = _runner(duration=3600, verify=False)
    assert long_runner.estimated_verify_seconds() == 0
    assert long_runner.watchdog_slack_seconds == pytest.approx(900)


def test_watchdog_waits_longer_than_fio_runtime(monkeypatch):
    waited = []

    class Cancel:
        def wait(self, timeout=None):
            waited.append(timeout)
            return True  # abgebrochen → kein Kill

        def set(self):
            pass

        def clear(self):
            pass

    runner = _runner(duration=10)
    runner._watchdog_cancel = Cancel()
    runner._watchdog_timer()
    assert waited and waited[0] > 10 + 60


def test_read_only_runner_has_no_verify_but_still_slack():
    runner = _runner(rw="randread", duration=60)
    assert not runner.verify
    assert runner.watchdog_slack_seconds == 60


# ----------------------------------------------------------------- Befund 6: numjobs-Überlappung


def test_verifying_jobs_get_disjoint_ranges():
    runner = _runner(size_mb=1024, num_jobs=4)
    cmd = runner.build_command()
    assert "--numjobs=4" in cmd
    assert "--size=256M" in cmd
    assert "--offset_increment=256M" in cmd
    assert runner.total_size_mb <= 1024
    assert "--verify=crc32c" in cmd


def test_non_verifying_jobs_keep_full_size_and_no_offset():
    runner = _runner(rw="randread", size_mb=1024, num_jobs=4)
    cmd = runner.build_command()
    assert "--size=1024M" in cmd
    assert not any(arg.startswith("--offset_increment") for arg in cmd)
    assert runner.total_size_mb == 1024


def test_single_verifying_job_unchanged():
    runner = _runner(size_mb=512, num_jobs=1)
    cmd = runner.build_command()
    assert "--size=512M" in cmd and "--numjobs=1" in cmd
    assert not any(arg.startswith("--offset_increment") for arg in cmd)


def test_more_jobs_than_megabytes_is_clamped():
    runner = _runner(size_mb=2, num_jobs=8)
    assert runner.num_jobs == 2 and runner.job_size_mb == 1
    assert runner.total_size_mb == 2


# ----------------------------------------------------------------- Befund 1: Broker-Tokens


class _RecordingBroker:
    def __init__(self):
        self.tokens = None
        self.runs = []

    def submit_fio_job(self, tokens):
        self.tokens = list(tokens)
        return "job-x"

    def submit(self, command):  # pragma: no cover - darf nicht genutzt werden
        raise AssertionError("fio darf nicht als freier RUN-Befehl laufen")


def test_fio_runner_submits_checked_tokens_instead_of_cli(monkeypatch):
    broker = _RecordingBroker()
    runner = _runner(size_mb=256, num_jobs=2, duration=30)
    runner.root_broker = broker
    job = runner._submit_to_broker(runner.build_command())
    assert job == "job-x"
    assert broker.tokens[0] == "--eta=always"
    assert "@hardwaretest" in broker.tokens
    assert "filename=/mnt/test/fio.bin" in broker.tokens
    assert "offset_increment=128M" in broker.tokens
    assert "group_reporting=1" in broker.tokens
    assert all(not tok.startswith("--") or tok.startswith("--eta=") for tok in broker.tokens)


def test_fio_runner_rejects_unknown_cli_arguments_for_broker():
    runner = _runner()
    runner.root_broker = _RecordingBroker()
    with pytest.raises(TestExecutionError):
        runner._submit_to_broker(["fio", "--name=x", "--output=/etc/passwd", "job.fio"])


def test_job_tokens_translate_sections_and_reject_whitespace():
    assert _job_tokens(["[global]", "rw=read", "", "[nvme0n1]", "filename=/dev/nvme0n1"]) == [
        "@global", "rw=read", "@nvme0n1", "filename=/dev/nvme0n1",
    ]
    with pytest.raises(TestExecutionError):
        _job_tokens(["exec_prerun=/bin/sh -c id"])


def test_jobfile_runner_uses_broker_tokens_and_writes_no_local_file(tmp_path):
    broker = _RecordingBroker()
    runner = FioDeviceSweepRunner(TestParameters(0), ["/dev/sda", "/dev/sdb"], "1m", 32,
                                  ioengine="psync")
    runner.root_broker = broker
    cmd = runner.build_command()
    assert cmd[-1] == fio_runner._BROKER_JOB_PLACEHOLDER
    assert runner._job_file is None and runner._job_dir is None
    runner._submit_to_broker(cmd)
    assert broker.tokens[:2] == ["--eta=always", "@global"]
    assert "filename=/dev/sda" in broker.tokens and "filename=/dev/sdb" in broker.tokens
    assert "continue_on_error=read" in broker.tokens


def _fake_fio_broker(tmp_path):
    fio = tmp_path / "fio"
    fio.write_text('#!/bin/sh\nfor a in "$@"; do last=$a; done\necho "ARGS: $*"\ncat "$last"\n')
    fio.chmod(0o755)
    fake = tmp_path / "fakepkexec"
    fake.write_text(f'#!/bin/sh\nPATH="{tmp_path}:$PATH" exec "$@"\n')
    fake.chmod(0o755)
    return RootBroker(str(fake))


def test_nvme_full_read_runner_end_to_end_through_real_broker_script(tmp_path):
    broker = _fake_fio_broker(tmp_path)
    lines = []
    runner = FioNvmeFullReadRunner(TestParameters(0), "/dev/nvme0n1", ioengine="psync",
                                   log_fn=lines.append)
    runner.check_ras = False
    runner.root_broker = broker
    runner.start()
    assert runner.wait_finished(timeout=15)
    result = runner.get_result()
    broker.close()
    assert result is not None and result.passed, lines
    text = "\n".join(lines)
    assert "ARGS: --readonly --eta=always --eta-interval=1000 --status-interval=1" in text
    assert "[global]" in text and "filename=/dev/nvme0n1" in text and "rw=read" in text
    assert runner._job_file is None


def test_destructive_runner_tokens_are_accepted_by_broker_script(tmp_path, monkeypatch):
    monkeypatch.setattr(fio_runner, "_check_devices_not_mounted", lambda devices, **kw: None)
    broker = _fake_fio_broker(tmp_path)
    runner = FioDestructiveRunner(TestParameters(0), ["/dev/sdz"], "1m", 32, passes=2,
                                  ioengine="libaio")
    runner.root_broker = broker
    job = runner._submit_to_broker(runner.build_command())
    completed = broker._run_job(job, ["fio"], timeout=10)
    broker.close()
    assert completed.returncode == 0
    assert "verify=crc32c" in completed.stdout and "loops=2" in completed.stdout
    assert "[sdz]" in completed.stdout


def test_destructive_runner_on_regular_file_is_rejected_by_broker(tmp_path, monkeypatch):
    monkeypatch.setattr(fio_runner, "_check_devices_not_mounted", lambda devices, **kw: None)
    broker = _fake_fio_broker(tmp_path)
    runner = FioDestructiveRunner(TestParameters(0), [str(tmp_path / "victim")], "1m", 32,
                                  ioengine="libaio")
    runner.root_broker = broker
    with pytest.raises(RuntimeError):
        runner._submit_to_broker(runner.build_command())
    broker.close()


# ----------------------------------------------------------------- Befund 1: lokale Jobdatei


def test_local_job_file_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(fio_runner.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    runner = FioDeviceSweepRunner(TestParameters(0), ["/dev/sda"], "1m", 32, ioengine="psync")
    cmd = runner.build_command()
    job_file = cmd[-1]
    assert job_file == runner._job_file
    assert stat.S_IMODE(os.stat(job_file).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(runner._job_dir).st_mode) == 0o700
    assert os.path.dirname(job_file) == runner._job_dir
    runner._cleanup_job_file()
    assert not os.path.exists(job_file) and not os.path.exists(os.path.dirname(job_file))
    assert runner._job_file is None and runner._job_dir is None


# ----------------------------------------------------------------- Befund 7: pkexec-Wrapper


_FAKE_FIO_WITH_CHILD = (
    "#!/bin/sh\n"
    "sh -c 'trap \"\" TERM; while :; do sleep 0.1; done' &\n"
    'echo "CHILD $!"\n'
    'trap "exit 143" TERM\n'
    "while :; do sleep 0.1; done\n"
)


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _start_wrapper(tmp_path):
    fake = tmp_path / "fio"
    fake.write_text(_FAKE_FIO_WITH_CHILD)
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    proc = subprocess.Popen(
        ["sh", "-c", PKEXEC_STOP_WRAPPER, "hardwaretest-fio", "job.fio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
    )
    assert proc.stdout.readline().strip() == AUTHORIZED_MARKER
    child = int(proc.stdout.readline().split()[1])
    assert _pid_alive(child)
    return proc, child


def _wait_dead(pid, timeout=12):
    deadline = time.monotonic() + timeout
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _pid_alive(pid)


def test_wrapper_kills_fio_group_on_stdin_eof(tmp_path):
    proc, child = _start_wrapper(tmp_path)
    proc.stdin.close()
    assert proc.wait(timeout=15) != 0
    assert _wait_dead(child), "Enkelprozess muss mit der Gruppe beendet werden"


def test_wrapper_forwards_sigterm_to_fio_group(tmp_path):
    proc, child = _start_wrapper(tmp_path)
    os.kill(proc.pid, signal.SIGTERM)
    assert proc.wait(timeout=15) != 0
    assert _wait_dead(child)
    proc.stdin.close()


def test_wrapper_still_exits_zero_on_normal_completion(tmp_path):
    fake = tmp_path / "fio"
    fake.write_text("#!/bin/sh\necho done\nexit 0\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    # stdin bleibt offen (wie im Betrieb) – nur EOF bedeutet "stoppen".
    proc = subprocess.Popen(["sh", "-c", PKEXEC_STOP_WRAPPER, "x", "a", "b"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    assert proc.wait(timeout=15) == 0
    assert proc.stdout.read().splitlines() == [AUTHORIZED_MARKER, "done"]
    proc.stdin.close()
