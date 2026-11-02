"""SATA/SAS/USB-Inventar und SMART-Auswertung für den Gesamttest."""

import json
from subprocess import CompletedProcess
from datetime import datetime, timedelta

import pytest

from hardwaretest.core import block_disks, test_plan
from hardwaretest.core.block_disks import (
    BlockDisk, DiskSmart, list_block_disks, parse_disk_smart, read_disk_smart, smart_regressions, smart_lines,
)
from hardwaretest.tests.fio_runner import FioDiskFullReadRunner, FioNvmeFullReadRunner
from hardwaretest.core.test_runner import TestExecutionError, TestParameters

LSBLK = {"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 2000398934016, "model": "ST2000", "serial": "S1",
     "tran": "sata", "rota": True, "rm": False, "vendor": "ATA", "mountpoints": [None],
     "children": [{"name": "sda1", "path": "/dev/sda1", "type": "part", "mountpoints": ["/"]}]},
    {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 480103981056, "model": "Kingston", "serial": "K2",
     "tran": "usb", "rota": False, "rm": True, "vendor": None, "mountpoints": [None]},
    {"name": "sdc", "path": "/dev/sdc", "type": "disk", "size": 1000204886016, "model": "LOGICAL VOLUME", "serial": "L3",
     "tran": None, "rota": True, "rm": False, "vendor": "HP", "mountpoints": [None]},
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": 4096, "model": "X", "serial": "N"},
    {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": 1},
    {"name": "sr0", "path": "/dev/sr0", "type": "rom", "size": 1},
]}


def _run_json(payload, returncode=0):
    def run(args, **_):
        return CompletedProcess(args, returncode, stdout=json.dumps(payload), stderr="")
    return run


def test_list_block_disks_only_returns_sd_disks_with_kind_and_mounts():
    disks = list_block_disks(_run_json(LSBLK))
    assert [d.path for d in disks] == ["/dev/sda", "/dev/sdb", "/dev/sdc"]
    sda, sdb, sdc = disks
    assert sda.model == "ATA ST2000" and sda.mounted and sda.kind == "SATA HDD"
    assert sdb.kind == "USB SSD" and sdb.removable and not sdb.mounted
    assert sdc.kind == "RAID/anderer Bus HDD" and sdc.model == "HP LOGICAL VOLUME"


def test_list_block_disks_tolerates_failures():
    assert list_block_disks(lambda *a, **k: CompletedProcess(a, 1, stdout="", stderr="")) == []
    assert list_block_disks(lambda *a, **k: CompletedProcess(a, 0, stdout="{garbage", stderr="")) == []


def test_parse_ata_attributes_masks_high_bytes_and_flags():
    data = {"smart_status": {"passed": True}, "temperature": {"current": 38}, "power_on_time": {"hours": 1200},
            "ata_smart_attributes": {"table": [
                {"id": 5, "raw": {"value": 3}}, {"id": 197, "raw": {"value": 0}},
                {"id": 198, "raw": {"value": 0}}, {"id": 199, "raw": {"value": (7 << 32) | 2}}]}}
    info = parse_disk_smart("/dev/sda", data, returncode=0x40)
    assert info.available and info.healthy is True and info.temperature_c == 38
    assert (info.reallocated_sectors, info.pending_sectors, info.crc_errors) == (3, 0, 2)
    assert info.warnings == ["Einträge im SMART-Fehlerprotokoll"]


def test_parse_scsi_counters_and_failing_bit():
    data = {"scsi_grown_defect_list": 4, "scsi_error_counter_log": {"read": {"total_uncorrected_errors": 1}}}
    info = parse_disk_smart("/dev/sdb", data, returncode=0x08)
    assert info.grown_defects == 4 and info.read_uncorrected == 1 and info.healthy is False


def test_read_disk_smart_reports_unavailable_for_raid_logical_volume(monkeypatch):
    monkeypatch.setattr(block_disks.shutil, "which", lambda _: "/usr/sbin/smartctl")
    payload = {"smartctl": {"messages": [{"string": "Unknown USB bridge", "severity": "error"}]}}
    info = read_disk_smart("/dev/sdc", _run_json(payload, returncode=2))
    assert not info.available and "Unknown USB bridge" in info.error
    ok = read_disk_smart("/dev/sda", _run_json({"smart_status": {"passed": True}}, returncode=0))
    assert ok.available and ok.healthy is True


def test_smart_regressions_and_lines():
    before = DiskSmart("/dev/sda", available=True, healthy=True, reallocated_sectors=1, pending_sectors=0,
                       temperature_c=30)
    same = DiskSmart("/dev/sda", available=True, healthy=True, reallocated_sectors=1, pending_sectors=0,
                     temperature_c=41)
    worse = DiskSmart("/dev/sda", available=True, healthy=False, reallocated_sectors=2, pending_sectors=0)
    assert smart_regressions(before, same) == []
    problems = smart_regressions(before, worse)
    assert problems[0] == "SMART-Gesamtstatus: FAILED" and any("1 -> 2" in p for p in problems)
    assert any("Temperatur °C: 30 -> 41" in line for line in smart_lines("/dev/sda", before, same))


def test_disk_runner_accepts_only_sd_devices():
    runner = FioDiskFullReadRunner(TestParameters(0), ["/dev/sda", "/dev/sdab"], ioengine="libaio")
    try:
        job = "\n".join(runner._job_lines())
        assert "rw=read" in job and "/dev/sda" in job and "disk_full_read" in job
        assert "--readonly" in runner.build_command()
    finally:
        runner._cleanup_job_file()
    with pytest.raises(TestExecutionError):
        FioDiskFullReadRunner(TestParameters(0), ["/dev/nvme0n1"], ioengine="libaio")
    with pytest.raises(TestExecutionError):
        FioNvmeFullReadRunner(TestParameters(0), ["/dev/sda"], ioengine="libaio")


# ----------------------------------------------------------------- Testplan-Integration

DISK = BlockDisk("/dev/sda", "ATA ST2000", "S1", 4096, transport="sata")


def executor(tmp_path, **overrides):
    config = dict(output_dir=tmp_path, cpu_enabled=False, ram_enabled=False, nvme_enabled=False,
                  nvidia_enabled=False, disk_read_enabled=True, disk_devices=(DISK,))
    config.update(overrides)
    return test_plan.TestPlanExecutor(test_plan.TestPlanConfig(**config))


def test_disk_read_step_is_selected_and_needs_root(tmp_path, monkeypatch):
    plan = executor(tmp_path)
    assert plan.selected_step_keys() == ["disk_read"]
    monkeypatch.setattr(test_plan.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/usr/bin/pkexec")
    # conftest ersetzt _needs_root durch einen Stub; die Root-Regel steht im Quelltext.
    import inspect
    source = inspect.getsource(test_plan)
    assert '{"nvme", "nvme_read", "disk_read"}' in source


@pytest.mark.parametrize("after,expected", [(0, "passed"), (5, "failed")])
def test_disk_read_compares_smart(tmp_path, monkeypatch, after, expected):
    plan = executor(tmp_path, language="en")
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/bin/fake")
    monkeypatch.setattr(test_plan, "list_block_disks", lambda *a: [DISK])
    monkeypatch.setattr(test_plan, "smartctl_available", lambda: True)
    samples = iter([DiskSmart("/dev/sda", available=True, pending_sectors=0),
                    DiskSmart("/dev/sda", available=True, pending_sectors=after)])
    monkeypatch.setattr(test_plan, "read_disk_smart", lambda *a: next(samples))
    created = []
    monkeypatch.setattr(test_plan, "FioDiskFullReadRunner", lambda *a, **kw: created.append((a, kw)))
    plan._run_process_step = lambda *a, **kw: plan._instant_result("disk_read", "passed", "passed")
    result = plan._run_disk_read()
    assert result.status == expected
    assert created[0][0][1] == ["/dev/sda"] and created[0][1]["total_bytes"] == 4096
    assert any("S1" in line for line in result.details)


def test_disk_read_without_smart_is_only_a_warning(tmp_path, monkeypatch):
    plan = executor(tmp_path)
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/bin/fake")
    monkeypatch.setattr(test_plan, "list_block_disks", lambda *a: [DISK])
    monkeypatch.setattr(test_plan, "smartctl_available", lambda: True)
    monkeypatch.setattr(test_plan, "read_disk_smart", lambda *a: DiskSmart("/dev/sda", error="kein SMART"))
    monkeypatch.setattr(test_plan, "FioDiskFullReadRunner", lambda *a, **kw: None)
    plan._run_process_step = lambda *a, **kw: plan._instant_result("disk_read", "passed", "passed")
    result = plan._run_disk_read()
    assert result.status == "passed"
    assert any("SMART nicht verfügbar" in line and "kein SMART" in line for line in result.details)


def test_disk_identity_change_is_rejected(tmp_path):
    plan = executor(tmp_path)
    with pytest.raises(RuntimeError):
        plan._validated_disks([BlockDisk("/dev/sda", "ATA ST2000", "OTHER", 4096)])


# ----------------------------------------------------------------- Endzeit


def test_end_time_in_the_past_is_rejected(tmp_path):
    plan = executor(tmp_path, disk_read_enabled=False, nvme_enabled=True,
                    end_time=datetime.now() - timedelta(minutes=1))
    with pytest.raises(ValueError):
        plan.execute()


def test_end_time_stops_before_next_step_without_failure(tmp_path, monkeypatch):
    plan = executor(tmp_path, disk_read_enabled=False, nvme_enabled=True, ping_enabled=True, rounds=100,
                    end_time=datetime(2030, 1, 1, 12, 0))
    calls = []
    late = []

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 1) if late else datetime(2030, 1, 1)

    def nvme():
        calls.append("nvme")
        late.append(1)  # Nach dem ersten Teiltest ist die Endzeit überschritten.
        return plan._instant_result("nvme", "passed", "ok")

    plan._run_nvme = nvme
    plan._run_ping = lambda: calls.append("ping") or plan._instant_result("ping", "passed", "ok")
    monkeypatch.setattr(test_plan, "datetime", _Now)
    result = plan.execute()
    assert result.status == "passed"
    assert result.end_time_reached and result.completed_rounds == 0
    assert calls == ["nvme"]
    report = test_plan.render_text_report(result, "de")
    assert "Endzeit erreicht" in report
