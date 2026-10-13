import subprocess
import time

import pytest

from hardwaretest.core.root_broker import ROOT_BROKER_SCRIPT, RootAuthorizationError, RootBroker
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters


def _broker(tmp_path):
    for name, body in {
        "nvme": 'echo "{\\"args\\": \\"$*\\"}"; echo warn >&2; exit 3',
        "fio": 'trap "exit 143" TERM; echo start; while :; do sleep 0.1; done',
        "dcgmi": 'echo "dcgm $*"; exit 0',
    }.items():
        tool = tmp_path / name
        tool.write_text("#!/bin/sh\n" + body + "\n")
        tool.chmod(0o755)
    fake = tmp_path / "fakepkexec"
    fake.write_text(f'#!/bin/sh\nPATH="{tmp_path}:$PATH" exec "$@"\n')
    fake.chmod(0o755)
    return RootBroker(str(fake))


def test_script_is_valid_sh():
    assert subprocess.run(["sh", "-n", "-c", ROOT_BROKER_SCRIPT]).returncode == 0


def test_run_returns_stdout_stderr_and_exit_code_in_one_session(tmp_path):
    broker = _broker(tmp_path)
    broker.start()
    pid = broker._proc.pid
    first = broker.run(["nvme", "smart-log", "/dev/nvme0", "-o", "json"], timeout=10)
    second = broker.run(["dcgmi", "diag", "--run", "1", "--entity-id", "gpu:0,gpu:1"], timeout=10)
    assert broker._proc.pid == pid
    assert first.returncode == 3
    assert first.stdout.strip() == '{"args": "smart-log /dev/nvme0 -o json"}'
    assert first.stderr.strip() == "warn"
    assert second.stdout.strip() == "dcgm diag --run 1 --entity-id gpu:0,gpu:1"
    broker.close()


def test_rejects_unlisted_commands(tmp_path):
    broker = _broker(tmp_path)
    for command in (["nvme", "format", "/dev/nvme0"], ["sh", "-c", "id"], ["fio", "a;b"]):
        with pytest.raises(RuntimeError):
            broker.run(command)
    with pytest.raises(RuntimeError):
        broker.submit(["fio", "two words"])
    broker.close()


def test_cancelled_authorization_raises(tmp_path):
    with pytest.raises(RootAuthorizationError):
        RootBroker("/bin/false").start()


class _FioRunner(BaseTestRunner):
    def build_command(self):
        return ["fio", "job.fio"]


def test_runner_streams_and_stops_through_broker(tmp_path):
    broker = _broker(tmp_path)
    runner = _FioRunner(TestParameters(0))
    runner.root_broker = broker
    runner.start()
    time.sleep(0.5)
    assert runner.is_running()
    runner.stop(aborted=True)
    assert not runner.is_running()
    broker.close()
