import sys

from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.memory_fill import MemoryFillRunner


def test_frozen_memory_fill_uses_internal_worker(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    runner = MemoryFillRunner(TestParameters(duration_seconds=20), memory_mb=16)

    command = runner.build_command()

    assert command[:2] == [sys.executable, "--memory-fill-worker"]
    assert command[command.index("--duration") + 1] == "15"
    assert command[command.index("--memory-mb") + 1] == "16"


def test_source_memory_fill_uses_python_script(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    runner = MemoryFillRunner(TestParameters(duration_seconds=5), memory_mb=1)

    command = runner.build_command()

    assert command[0] == sys.executable
    assert command[1].endswith("memory_fill_script.py")
