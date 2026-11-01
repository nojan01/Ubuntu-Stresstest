"""Regressionstests für den Runner-Lebenszyklus (Befunde 2, 3 und 5 des Code-Reviews)."""

import subprocess
import threading
import time

from hardwaretest.core import ras, test_runner
from hardwaretest.core.test_runner import RAS_UNREADABLE, BaseTestRunner, TestParameters


class _ShellRunner(BaseTestRunner):
    def __init__(self, script, params=None, **kwargs):
        super().__init__(params or TestParameters(0), **kwargs)
        self._script = script
        self.check_ras = False

    def build_command(self):
        return ["sh", "-c", self._script]


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


# ----------------------------------------------------------------- Befund 2: Finalisierung


def test_stop_waits_for_buffered_output_before_finalizing():
    # Der Prozess gibt beim Beenden noch eine Fehlerzeile aus; sie muss im Ergebnis
    # landen, obwohl stop() direkt nach Prozessende aufgerufen wird.
    runner = _ShellRunner('trap "echo FATAL: late failure; exit 0" TERM; echo start; '
                          'while :; do sleep 0.1; done')
    runner.start()
    assert _wait(lambda: runner._process is not None and runner._process.poll() is None)
    time.sleep(0.3)
    runner.stop(aborted=True)
    result = runner.get_result()
    assert result is not None
    assert any("late failure" in line for line in result.errors)
    assert not runner.is_running()
    assert runner.wait_finished(timeout=5)


def test_finalize_result_is_idempotent_across_threads():
    runner = _ShellRunner("exit 0")
    runner.start()
    assert runner.wait_finished(timeout=5)
    calls = []
    original = runner._build_result

    def counting():
        calls.append(1)
        original()

    runner._build_result = counting
    threads = [threading.Thread(target=runner._finalize_result) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # Der Ausgabe-Thread hat bereits finalisiert – weitere Aufrufe sind no-ops.
    assert calls == []
    assert runner.get_result() is not None
    runner.stop()
    assert runner.get_result() is not None


def test_stream_output_does_not_refinalize_after_stop():
    runner = _ShellRunner('echo start; while :; do sleep 0.1; done')
    runner.start()
    time.sleep(0.3)
    runner.stop(aborted=True)
    first = runner.get_result()
    assert runner.wait_finished(timeout=5)
    assert runner.get_result() is first


# ----------------------------------------------------------------- Vertrag (a)/(b)/(c)


def test_is_running_stays_true_until_result_exists():
    gate = threading.Event()
    runner = _ShellRunner("exit 0")
    original = runner._build_result

    def slow_build():
        gate.wait(5)
        original()

    runner._build_result = slow_build
    runner.start()
    assert _wait(lambda: runner._process is not None and runner._process.poll() is not None)
    # Prozess ist beendet, Ergebnis aber noch nicht da → weiterhin "läuft".
    assert runner.is_running()
    assert runner.get_result() is None
    gate.set()
    assert runner.wait_finished(timeout=5)
    assert runner.get_result() is not None
    assert not runner.is_running()


def test_wait_finished_without_start_returns_true_immediately():
    runner = _ShellRunner("exit 0")
    started = time.monotonic()
    assert runner.wait_finished(timeout=5) is True
    assert time.monotonic() - started < 1


def test_wait_finished_respects_timeout_and_joins_watchdog():
    runner = _ShellRunner('while :; do sleep 0.1; done', params=TestParameters(60))
    runner.start()
    assert runner._watchdog_thread is not None and runner._watchdog_thread.is_alive()
    assert runner.wait_finished(timeout=0.2) is False
    runner.stop(aborted=True)
    assert runner.wait_finished(timeout=5) is True
    assert not runner._watchdog_thread.is_alive()


def test_public_signatures_are_compatible():
    import inspect

    assert list(inspect.signature(BaseTestRunner.start).parameters) == ["self"]
    assert list(inspect.signature(BaseTestRunner.stop).parameters) == ["self", "aborted"]
    assert list(inspect.signature(BaseTestRunner.get_result).parameters) == ["self"]
    assert list(inspect.signature(BaseTestRunner.is_running).parameters) == ["self"]
    params = inspect.signature(BaseTestRunner.wait_finished).parameters
    assert list(params) == ["self", "timeout"] and params["timeout"].default is None


# ----------------------------------------------------------------- Befund 3: Broker-Kill


class _FakeBroker:
    """Simuliert die Root-Sitzung: KILL beendet den "Root-Prozess" über eine Flag-Datei."""

    def __init__(self, flag_dir):
        self.flag = flag_dir / "killed"
        self.killed = []
        self.done_jobs = []
        self.submitted = []

    def submit(self, cmd):
        self.submitted.append(list(cmd))
        return "job-1"

    def relay_command(self, job):
        return ["sh", "-c", f'echo relay; while [ ! -e "{self.flag}" ]; do sleep 0.05; done; exit 143']

    def kill(self, job, grace_seconds=10):
        self.killed.append((job, grace_seconds))
        self.flag.touch()

    def done(self, job):
        self.done_jobs.append(job)


def test_watchdog_uses_broker_kill_for_root_jobs(tmp_path):
    broker = _FakeBroker(tmp_path)
    runner = _ShellRunner("unused", params=TestParameters(1))
    runner.terminate_grace_seconds = 1
    runner.root_broker = broker
    runner.start()
    assert broker.submitted == [["sh", "-c", "unused"]]
    assert _wait(lambda: broker.killed, timeout=5)
    assert broker.killed[0] == ("job-1", 1)
    assert runner.wait_finished(timeout=10)
    assert broker.done_jobs == ["job-1"]
    assert runner.get_result() is not None


def test_stop_uses_broker_kill_for_root_jobs(tmp_path):
    broker = _FakeBroker(tmp_path)
    runner = _ShellRunner("unused")
    runner.root_broker = broker
    runner.start()
    time.sleep(0.2)
    runner.stop(aborted=True)
    assert broker.killed and broker.killed[0][0] == "job-1"
    assert broker.done_jobs == ["job-1"]
    # Exit 143 (SIGTERM) nach bewusstem Abbruch ist kein Fehler.
    assert runner.get_result() is not None and runner.get_result().passed


def test_stop_kills_relay_when_broker_kill_does_not_end_root_job(tmp_path):
    broker = _FakeBroker(tmp_path)
    broker.kill = lambda job, grace_seconds=10: None  # Root-Prozess "überlebt"
    runner = _ShellRunner("unused")
    runner.stop_grace_seconds = 1
    runner.root_broker = broker
    runner.start()
    time.sleep(0.2)
    started = time.monotonic()
    runner.stop(aborted=True)
    assert time.monotonic() - started < 15
    assert runner._process is None and runner.get_result() is not None


# ----------------------------------------------------------------- Befund 5: RAS_UNREADABLE


def test_unreadable_ras_baseline_adds_warning(monkeypatch):
    monkeypatch.setattr(ras, "snapshot", lambda run=None, db_path=ras.DB_PATH: None)
    monkeypatch.setattr(ras, "rasdaemon_installed", lambda: True)
    runner = _ShellRunner("exit 0")
    runner.check_ras = True
    runner.start()
    assert runner.wait_finished(timeout=5)
    result = runner.get_result()
    assert result is not None and result.passed
    assert RAS_UNREADABLE in result.warnings


def test_no_ras_warning_when_rasdaemon_is_absent(monkeypatch):
    monkeypatch.setattr(ras, "snapshot", lambda run=None, db_path=ras.DB_PATH: None)
    monkeypatch.setattr(ras, "rasdaemon_installed", lambda: False)
    runner = _ShellRunner("exit 0")
    runner.check_ras = True
    runner.start()
    assert runner.wait_finished(timeout=5)
    assert runner.get_result().warnings == []


def test_ras_runner_prefers_broker_ras_query():
    class Broker:
        def __init__(self):
            self.calls = []

        def run(self, cmd, **kwargs):  # pragma: no cover - darf nicht genutzt werden
            raise AssertionError("python -c darf nicht über den Broker laufen")

        def ras_query(self, baseline, db_path, limit):
            self.calls.append((baseline, db_path, limit))
            return {"ids": {"mc_event": 7}, "events": []}

    broker = Broker()
    data = ras._run_query(None, broker.run, "/nonexistent/ras.db")
    assert data == {"ids": {"mc_event": 7}, "events": []}
    assert broker.calls == [(None, "/nonexistent/ras.db", ras.MAX_EVENTS)]


def test_subprocess_exit_code_is_still_reported():
    runner = _ShellRunner("exit 3")
    runner.start()
    assert runner.wait_finished(timeout=5)
    result = runner.get_result()
    assert not result.passed and result.exit_code == 3
    assert isinstance(runner._process, subprocess.Popen)
    runner.stop()
    assert runner._process is None
    assert test_runner.RAS_UNREADABLE not in result.warnings
