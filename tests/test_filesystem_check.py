from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from types import SimpleNamespace

import pytest

from hardwaretest.core import filesystem_check as fs


DEVICE = fs.FilesystemDevice("/dev/test0", "ext4", "abc", "8:1", (), "part")


def setup_device(monkeypatch, device=DEVICE, mounts="", holders=()):
    monkeypatch.setattr(fs, "list_filesystems", lambda: [device])
    real_stat = os.stat
    monkeypatch.setattr(fs.os, "stat", lambda path, *a, **kw: (
        SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 1))
        if str(path) == DEVICE.path else real_stat(path, *a, **kw)
    ))
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: (
        mounts if str(path) in {"/proc/self/mountinfo", "/proc/1/mountinfo"} else
        "Filename Type Size Used Priority\n" if str(path) == "/proc/swaps" else real_read(path, *a, **kw)
    ))
    real_iter = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda path: (
        iter(holders) if str(path) == "/sys/dev/block/8:1/holders" else real_iter(path)
    ))


def test_offline_identity_validated(monkeypatch):
    setup_device(monkeypatch)
    assert fs.validate_device(DEVICE) == DEVICE


@pytest.mark.parametrize("change", [dict(uuid="changed"), dict(number="8:2"), dict(fstype="xfs")])
def test_changed_device_rejected(monkeypatch, change):
    setup_device(monkeypatch, replace(DEVICE, **change))
    with pytest.raises(ValueError, match="Device changed"):
        fs.validate_device(DEVICE)


@pytest.mark.parametrize("device,mounts,holders", [
    (replace(DEVICE, mounts=("/",)), "", ()),
    (DEVICE, "42 1 8:1 /subdir /bind ro - ext4 /dev/test0 ro", ()),
    (replace(DEVICE, children=True), "", ()),
    (DEVICE, "", (Path("dm-0"),)),
])
def test_mount_bind_or_active_layer_rejected(monkeypatch, device, mounts, holders):
    setup_device(monkeypatch, device, mounts, holders)
    with pytest.raises(ValueError, match="mounted or in use"):
        fs.validate_device(DEVICE)


def test_unreadable_mount_state_fails_closed(monkeypatch):
    setup_device(monkeypatch)
    monkeypatch.setattr(Path, "read_text", lambda *_: (_ for _ in ()).throw(PermissionError("no mount info")))
    with pytest.raises(PermissionError):
        fs.validate_device(DEVICE)


def test_worker_never_runs_checker_after_rejected_validation(monkeypatch):
    monkeypatch.setattr(fs.os, "geteuid", lambda: 0)
    monkeypatch.setattr(fs, "validate_device", lambda _: (_ for _ in ()).throw(ValueError("mounted")))
    monkeypatch.setattr(fs, "run_checker", lambda *_a, **_k: pytest.fail("must not run"))
    assert fs.worker_main(["repair", "--device", "/dev/test0", "--uuid", "abc", "--number", "8:1", "--fstype", "ext4"]) == 8


def test_commands_and_exit_codes_are_conservative():
    assert fs.checker_command("check", DEVICE.path) == ["/usr/sbin/e2fsck", "-f", "-n", DEVICE.path]
    assert fs.checker_command("repair", DEVICE.path)[2] == "-p"
    assert "corrected" in fs.result_text(1, "repair", "en")
    assert "Reboot" in fs.result_text(2, "repair", "en")
    assert "remain" in fs.result_text(4, "check", "en")
    assert "unknown" in fs.result_text(8, "check", "en")
    assert "unknown" in fs.result_text(126, "check", "en")
    assert "unknown" in fs.result_text(1, "check", "en")


def test_inventory_includes_mounted_and_unsupported_for_explanation(monkeypatch):
    rows = {"blockdevices": [{"name": "/dev/test", "children": [
        {"name": "/dev/test0", "type": "part", "fstype": "ext4", "uuid": "abc", "maj:min": "8:1", "mountpoints": ["/"]},
        {"name": "/dev/test1", "type": "part", "fstype": "xfs", "uuid": "xyz", "maj:min": "8:2", "mountpoints": [None]},
    ]}]}
    monkeypatch.setattr(fs.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(stdout=json.dumps(rows)))
    devices = fs.list_filesystems()
    assert devices[0].mounts == ("/",)
    assert not devices[1].supported


@pytest.mark.parametrize("fstype", ["ext4", "vfat", "btrfs", "xfs", "ntfs3", "ntfs", "exfat"])
def test_mounted_status_reads_telemetry_and_never_fsck(monkeypatch, fstype):
    device = replace(DEVICE, fstype=fstype, mounts=("/",))
    number = "0:55" if fstype in {"btrfs", "ntfs"} else "8:1"
    mount_type = "fuseblk" if fstype == "ntfs" else fstype
    setup_device(monkeypatch, device, f"42 1 {number} / / rw - {mount_type} /dev/test0 rw")
    original_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: (
        "0" if path.name == "errors_count" else original_read(path, *a, **kw)
    ))
    monkeypatch.setattr(fs.os, "statvfs", lambda _: SimpleNamespace(f_blocks=100, f_frsize=4096, f_bavail=50))
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="EXT4-fs (test0): mounted. Opts: errors=remount-ro\nEXT4-fs (other1): error\n", stderr="")
    monkeypatch.setattr(fs.subprocess, "run", run)
    report, code = fs.read_online_status(device, "en")
    assert code == 0
    assert "Not a full integrity check" in report
    assert "Capacity / available" in report
    assert all(command[0] == "journalctl" for command in commands)
    assert "other1" not in report


def test_online_counter_and_kernel_errors_are_reported(monkeypatch):
    setup_device(monkeypatch, replace(DEVICE, mounts=("/",)), "42 1 8:1 / / rw - ext4 /dev/test0 rw")
    original_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: (
        "2" if path.name == "errors_count" else original_read(path, *a, **kw)
    ))
    monkeypatch.setattr(fs.os, "statvfs", lambda _: SimpleNamespace(f_blocks=100, f_frsize=4096, f_bavail=50))
    monkeypatch.setattr(fs.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="EXT4-fs error (test0): bad inode", stderr=""))
    report, code = fs.read_online_status(DEVICE, "en")
    assert code == 4
    assert "recorded by the ext4 driver: 2" in report
    assert "bad inode" in report
    assert "error indications found" in fs.result_text(code, "status", "en")


def test_status_worker_allows_mount_but_never_runs_checker(monkeypatch, capsys):
    monkeypatch.setattr(fs.os, "geteuid", lambda: 0)
    monkeypatch.setattr(fs, "validate_identity", lambda _: replace(DEVICE, mounts=("/",)))
    monkeypatch.setattr(fs, "read_online_status", lambda *_: ("diagnostic report", 0))
    monkeypatch.setattr(fs, "run_checker", lambda *a: pytest.fail("must not run fsck"))
    assert fs.worker_main(["status", "--device", "/dev/test0", "--uuid", "abc", "--number", "8:1", "--fstype", "ext4"]) == 0
    assert "diagnostic report" in capsys.readouterr().out


@pytest.mark.skipif(not all(shutil.which(cmd) for cmd in ("mkfs.ext4", "debugfs", "e2fsck")), reason="ext tools missing")
def test_check_and_repair_real_temporary_image(tmp_path):
    # This is a regular file in pytest's temp directory, NEVER a user block device.
    image = tmp_path / "disposable-ext4.img"
    with image.open("wb") as file:
        file.truncate(32 * 1024 * 1024)
    subprocess.run(["mkfs.ext4", "-F", "-q", str(image)], check=True, capture_output=True)
    subprocess.run(["debugfs", "-w", "-R", "set_super_value free_blocks_count 0", str(image)], check=True, capture_output=True)
    before = image.read_bytes()
    assert fs.run_checker("check", str(image)) == 4
    assert image.read_bytes() == before  # -n must not change a byte.
    assert fs.run_checker("repair", str(image)) == 1
    assert fs.run_checker("check", str(image)) == 0
