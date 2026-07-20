"""Tests fuer den Mount-Schutz im FioDestructiveRunner."""

from __future__ import annotations

import pytest

from hardwaretest.core.test_runner import TestExecutionError, TestParameters
from hardwaretest.tests import fio_runner


def test_mount_check_blocks_mounted_device(monkeypatch):
    monkeypatch.setattr(fio_runner, "_mounted_devices", lambda: {"/dev/sdz1"})
    with pytest.raises(TestExecutionError, match="eingehaengt"):
        fio_runner._check_devices_not_mounted(["/dev/sdz1"])


def test_mount_check_blocks_parent_when_partition_mounted(monkeypatch):
    monkeypatch.setattr(fio_runner, "_mounted_devices", lambda: {"/dev/sdz1"})
    with pytest.raises(TestExecutionError, match="Partition"):
        fio_runner._check_devices_not_mounted(["/dev/sdz"])


def test_mount_check_allows_unmounted_device(monkeypatch):
    monkeypatch.setattr(fio_runner, "_mounted_devices", lambda: {"/dev/sda1"})
    # Sollte nichts werfen
    fio_runner._check_devices_not_mounted(["/dev/sdz"])


def test_destructive_runner_rejects_mounted(monkeypatch):
    monkeypatch.setattr(fio_runner, "_mounted_devices", lambda: {"/dev/sdz1"})
    params = TestParameters(duration_seconds=10)
    with pytest.raises(TestExecutionError):
        fio_runner.FioDestructiveRunner(
            params=params,
            devices=["/dev/sdz"],
            block_size="1m",
            io_depth=1,
        )
