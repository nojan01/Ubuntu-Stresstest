"""smartctl: unbekannte USB-Bridge (-d sat-Retry) und harmloses Exitcode-Bit 2 bei NVMe."""

import json
import subprocess

from hardwaretest.core import block_disks, system_info

USB_FAIL = json.dumps({"smartctl": {"messages": [
    {"string": "/dev/sda: Unknown USB bridge [0x17ef:0x235c (0x100)]", "severity": "error"}],
    "exit_status": 1}})
SAT_OK = json.dumps({"model_name": "USB-SSD", "smart_status": {"passed": True}})
NVME_SELFTEST = json.dumps({"model_name": "WD SN5000S", "smart_status": {"passed": True},
                            "smartctl": {"messages": [
                                {"string": "Read Self-test Log failed: Invalid Field in Command (0x4002)",
                                 "severity": "error"}], "exit_status": 4}})


def _patch(monkeypatch, responses):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        rc, out = responses["sat" if "sat" in cmd else "plain"]
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")

    monkeypatch.setattr(system_info.shutil, "which", lambda n: "/usr/sbin/smartctl")
    monkeypatch.setattr(system_info.subprocess, "run", fake_run)
    monkeypatch.setattr(block_disks.shutil, "which", lambda n: "/usr/sbin/smartctl")
    return calls, fake_run


def test_unknown_usb_bridge_retries_with_sat(monkeypatch):
    calls, fake_run = _patch(monkeypatch, {"plain": (1, USB_FAIL), "sat": (0, SAT_OK)})
    info = system_info.read_smart_info("/dev/sda")
    assert info.healthy is True and info.model == "USB-SSD" and not info.error
    assert any("-d" in c and "sat" in c for c in calls)
    disk = block_disks.read_disk_smart("/dev/sda", run=fake_run)
    assert disk.available and disk.healthy is True


def test_unknown_usb_bridge_sat_also_fails(monkeypatch):
    calls, fake_run = _patch(monkeypatch, {"plain": (1, USB_FAIL), "sat": (2, USB_FAIL)})
    info = system_info.read_smart_info("/dev/sda")
    assert info.healthy is None
    assert "USB-Bridge" in info.error and "sat" in info.error
    disk = block_disks.read_disk_smart("/dev/sda", run=fake_run)
    assert not disk.available and "USB bridge" in disk.error


def test_nvme_missing_selftest_log_is_not_a_warning(monkeypatch):
    _patch(monkeypatch, {"plain": (4, NVME_SELFTEST), "sat": (4, NVME_SELFTEST)})
    info = system_info.read_smart_info("/dev/nvme0n1")
    assert info.healthy is True
    assert info.warnings == []
    assert info.exit_status == 4


def test_real_cmd_failure_still_warns(monkeypatch):
    payload = json.dumps({"smart_status": {"passed": True}, "smartctl": {"messages": [
        {"string": "Read SMART Data failed: scsi error", "severity": "error"}]}})
    _patch(monkeypatch, {"plain": (4, payload), "sat": (4, payload)})
    info = system_info.read_smart_info("/dev/sdb")
    assert any("fehlgeschlagen" in w for w in info.warnings)
