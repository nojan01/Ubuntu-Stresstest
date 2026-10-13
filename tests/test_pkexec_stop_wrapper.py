import os
import subprocess
import time

from hardwaretest.tests.fio_runner import PKEXEC_STOP_WRAPPER


def _start(tmp_path, sleep_seconds):
	fake = tmp_path / "fio"
	fake.write_text(f"#!/bin/sh\nsleep {sleep_seconds}\n")
	fake.chmod(0o755)
	env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
	return subprocess.Popen(
		["sh", "-c", PKEXEC_STOP_WRAPPER, "hardwaretest-fio", "job.fio"],
		stdin=subprocess.PIPE,
		env=env,
	)


class Test_PkexecStopWrapper:
	def test_runs_to_completion_while_stdin_open(self, tmp_path):
		proc = _start(tmp_path, 1)
		start = time.monotonic()
		assert proc.wait(timeout=10) == 0
		assert time.monotonic() - start >= 0.9
		proc.stdin.close()

	def test_stops_on_stdin_eof(self, tmp_path):
		proc = _start(tmp_path, 30)
		time.sleep(0.3)
		proc.stdin.close()
		assert proc.wait(timeout=5) != 0


def test_wrapper_announces_authorization_before_fio(tmp_path):
    import subprocess

    from hardwaretest.tests.fio_runner import AUTHORIZED_MARKER, PKEXEC_STOP_WRAPPER

    fake = tmp_path / "fio"
    fake.write_text("#!/bin/sh\necho fio-output\n")
    fake.chmod(0o755)
    proc = subprocess.Popen(
        ["sh", "-c", PKEXEC_STOP_WRAPPER, "x"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, text=True, env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
    )
    assert proc.wait(timeout=10) == 0
    out = proc.stdout.read().splitlines()
    proc.stdin.close()
    assert out[0] == AUTHORIZED_MARKER
    assert "fio-output" in out
