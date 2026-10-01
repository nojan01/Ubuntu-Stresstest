from dataclasses import replace

import pytest

from hardwaretest.core import zfs


POOL = zfs.ZfsPool("tank", "12345", "ONLINE", 100, 20, 80)


def status_text(scan="none requested", state="ONLINE", counter=0, errors="No known data errors"):
    return f"""  pool: tank
 state: {state}
  scan: {scan}
config:
        NAME          STATE     READ WRITE CKSUM
        tank          {state}   0    0     0
          /dev/test0  ONLINE     0    0     {counter}
errors: {errors}
"""


def test_pool_inventory_uses_imported_pools_not_disk_signatures(monkeypatch):
    monkeypatch.setattr(zfs, "run_zpool", lambda args: "tank\t12345\tONLINE\t100\t20\t80\n")
    assert zfs.list_pools() == [POOL]
    monkeypatch.setattr(zfs, "run_zpool", lambda args: "")
    assert zfs.list_pools() == []


@pytest.mark.parametrize("scan,result,progress", [
    ("none requested", "none", None),
    ("scrub in progress since yesterday\n 10G / 30G scanned, 0B repaired, 33.25% done, 01:00:00 to go", "running", 33.25),
    ("scrub paused since yesterday", "paused", None),
    ("resilver in progress since yesterday, 20.0% done", "resilver", 20),
    ("scrub canceled on yesterday", "cancelled", None),
    ("scrub repaired 0B in 00:10:00 with 0 errors on yesterday", "passed", None),
    ("scrub repaired 100B in 00:10:00 with 2 errors on yesterday", "failed", None),
])
def test_scrub_status_parsing(scan, result, progress):
    parsed = zfs.parse_status(status_text(scan))
    assert parsed.scan_result == result
    assert parsed.progress == progress


@pytest.mark.parametrize("changes", [dict(state="DEGRADED"), dict(counter=1), dict(errors="Permanent errors have been detected")])
def test_device_or_pool_errors_not_reported_healthy(changes):
    assert zfs.parse_status(status_text(**changes)).has_errors


def mock_control(monkeypatch, scan="none requested", readonly="off", state="ONLINE"):
    commands = []
    monkeypatch.setattr(zfs, "list_pools", lambda: [POOL])
    monkeypatch.setattr(zfs, "read_status", lambda _: zfs.parse_status(status_text(scan, state)))
    def run(args):
        commands.append(args)
        return readonly if args[0] == "get" else ""
    monkeypatch.setattr(zfs, "run_zpool", run)
    return commands


@pytest.mark.parametrize("scan", ["scrub in progress since today", "resilver in progress since today"])
def test_duplicate_scrub_or_resilver_blocks_start(monkeypatch, scan):
    commands = mock_control(monkeypatch, scan=scan)
    with pytest.raises(ValueError, match="already running"):
        zfs.control_pool("start", POOL.guid)
    assert not commands


def test_readonly_pool_cannot_start_scrub(monkeypatch):
    commands = mock_control(monkeypatch, readonly="on")
    with pytest.raises(ValueError, match="Read-only"):
        zfs.control_pool("start", POOL.guid)
    assert not any(args[0] == "scrub" for args in commands)


def test_actions_only_target_selected_guid(monkeypatch):
    commands = mock_control(monkeypatch)
    zfs.control_pool("start", POOL.guid)
    assert commands[-1] == ["scrub", "tank"]
    commands = mock_control(monkeypatch, scan="scrub paused since yesterday")
    zfs.control_pool("stop", POOL.guid)
    assert commands == [["scrub", "-s", "tank"]]


def test_no_import_or_unrelated_pool_mutation(monkeypatch):
    commands = mock_control(monkeypatch)
    with pytest.raises(ValueError, match="not imported"):
        zfs.control_pool("start", "999")
    with pytest.raises(ValueError, match="Invalid"):
        zfs.control_pool("import", POOL.guid)
    assert not commands


def test_changed_pool_identity_rejected(monkeypatch):
    monkeypatch.setattr(zfs, "list_pools", lambda: [replace(POOL, guid="67890")])
    with pytest.raises(ValueError, match="changed"):
        zfs.read_status(POOL)


def test_result_missing_scan_data_never_passes():
    assert zfs.parse_status("state: UNKNOWN\n").scan_result != "passed"
    assert zfs.parse_status("state: ONLINE\nscan: scrub repaired 0B in 00:00:01 with 0 errors on today\n").has_errors
