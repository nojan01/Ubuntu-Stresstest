import subprocess
import time

import pytest

from hardwaretest.core.root_broker import ROOT_BROKER_SCRIPT, RootAuthorizationError, RootBroker
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters


def _broker(tmp_path):
    for name, body in {
        "nvme": 'echo "{\\"args\\": \\"$*\\"}"; echo warn >&2; exit 3',
        "fio": 'trap "exit 143" TERM; echo start; while :; do sleep 0.1; done',
        "dcgmi": 'case "$1" in diag) echo "dcgm $*"; exit 0;; esac; '
                 'trap "exit 143" TERM; echo start; while :; do sleep 0.1; done',
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
    # fio ist als freier RUN-Befehl nicht mehr erlaubt (nur noch als geprüfter FIOJOB).
    for command in (["nvme", "format", "/dev/nvme0"], ["sh", "-c", "id"], ["fio", "a;b"],
                    ["fio", "job.fio"]):
        with pytest.raises(RuntimeError):
            broker.run(command)
    with pytest.raises(RuntimeError):
        broker.submit(["fio", "two words"])
    broker.close()


def test_cancelled_authorization_raises(tmp_path):
    with pytest.raises(RootAuthorizationError):
        RootBroker("/bin/false").start()


class _LongRunner(BaseTestRunner):
    def build_command(self):
        return ["dcgmi", "stress"]


def test_runner_streams_and_stops_through_broker(tmp_path):
    broker = _broker(tmp_path)
    runner = _LongRunner(TestParameters(0))
    runner.root_broker = broker
    runner.start()
    time.sleep(0.5)
    assert runner.is_running()
    runner.stop(aborted=True)
    assert not runner.is_running()
    broker.close()


def test_dmidecode_and_ethtool_allowlist(tmp_path):
    for name in ("dmidecode", "ethtool", "mstlink"):
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\necho "$*"\n')
        tool.chmod(0o755)
    broker = _broker(tmp_path)
    broker.start()
    try:
        # Mehrere -t-Paare (Systemtab und Gesamttest lesen Typ 1, 4, 16, 17 und 39 in einem Aufruf).
        assert broker.run(["dmidecode", "-t", "1", "-t", "4", "-t", "16", "-t", "17", "-t", "39"],
                          timeout=10).stdout.strip() == "-t 1 -t 4 -t 16 -t 17 -t 39"
        assert broker.run(["dmidecode", "-t", "memory"], timeout=10).returncode == 0
        for bad in (["dmidecode", "-t"], ["dmidecode", "-t", "0"], ["dmidecode", "--dump-bin", "/tmp/x"],
                    ["dmidecode", "-t", "1", "-s", "system-serial-number"]):
            with pytest.raises(RuntimeError):
                broker.run(bad, timeout=10)
        assert broker.run(["ethtool", "-t", "eth0"], timeout=10).stdout.strip() == "-t eth0"
        assert broker.run(["ethtool", "-t", "ens1f0", "online"], timeout=10).returncode == 0
        assert broker.run(["ethtool", "-m", "ens1f0"], timeout=10).returncode == 0
        for bad in (["ethtool", "-t"], ["ethtool", "-t", "eth0", "loop"], ["ethtool", "-s", "eth0", "speed", "10"],
                    ["ethtool", "-t", "-m"], ["ethtool", "-m", "../x"], ["ethtool", "-t", "a" * 16]):
            with pytest.raises(RuntimeError):
                broker.run(bad, timeout=10)
        assert broker.run(["mstlink", "-d", "0000:2e:00.0", "--show_module", "--show_counters"],
                          timeout=10).stdout.strip() == "-d 0000:2e:00.0 --show_module --show_counters"
        assert broker.run(["mstlink", "-d", "2e:00.1"], timeout=10).returncode == 0
        for bad in (["mstlink", "-d"], ["mstlink", "-d", "/dev/mst/x"], ["mstlink", "-d", "2e:00.0", "--port_type", "PCIE"],
                    ["mstlink", "-d", "2e:00.0", "--set_speed", "25G"], ["mstlink", "-d", "0000:2e:00"]):
            with pytest.raises(RuntimeError):
                broker.run(bad, timeout=10)
    finally:
        broker.close()
