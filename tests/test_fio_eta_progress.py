import os
import subprocess

from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.fio_runner import _FioJobFileRunner


class _FakeEtaRunner(_FioJobFileRunner):
	_eta_progress = True

	def __init__(self, script, **kwargs):
		super().__init__(TestParameters(duration_seconds=0), **kwargs)
		self._script = script

	def build_command(self):
		return ["sh", "-c", self._script]


class Test_FioEtaProgress:
	def test_progress_from_eta_lines_not_logged(self):
		logs = []
		runner = _FakeEtaRunner(
			"printf 'Jobs: 1 (f=1): [W(1)][12.5%%][eta 00m:05s]\\rJobs: 1 (f=1), 0B/s-60.0MiB/s: [V(1)][62.0%%][eta 00m:02s]\\rfertig\\n'",
			log_fn=logs.append,
		)
		runner._process = subprocess.Popen(
			runner.build_command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
		)
		runner._start_time = 1.0
		runner._stream_output()
		assert runner._eta_fraction == 0.62
		assert "fertig" in logs
		assert not any(line.startswith("Jobs:") for line in logs)

	def test_wrapper_passes_all_arguments(self, tmp_path):
		from hardwaretest.tests.fio_runner import PKEXEC_STOP_WRAPPER

		fake = tmp_path / "fio"
		fake.write_text('#!/bin/sh\necho "$@"\n')
		fake.chmod(0o755)
		env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
		proc = subprocess.Popen(
			["sh", "-c", PKEXEC_STOP_WRAPPER, "hardwaretest-fio", "--eta=always", "job.fio"],
			stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
		)
		assert proc.wait(timeout=5) == 0
		assert proc.stdout.read().strip() == "--eta=always job.fio"
		proc.stdin.close()
		proc.stdout.close()
