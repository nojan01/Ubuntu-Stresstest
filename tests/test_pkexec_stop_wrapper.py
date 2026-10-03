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
