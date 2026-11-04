"""Tests für die strukturierte RAID-Auswertung (ssacli/storcli), Bewertung, Vergleich und UI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from hardwaretest.core import hpe_raid, ipmi, proliant_check
from hardwaretest.core.root_broker import RootBroker

FIXTURES = Path(__file__).parent / "fixtures" / "hpe"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- ssacli


def test_parse_ssacli_p440ar_config_detail():
    controllers = hpe_raid.parse_ssacli_controllers(_fx("ssacli_p440ar_config_detail.txt"))
    assert len(controllers) == 1
    c = controllers[0]
    assert c.vendor == "ssacli"
    assert c.model == "Smart Array P440ar"
    assert c.slot == "0"
    assert c.serial == "PDNLH0BRH7V3XP"
    assert c.firmware == "6.60"
    assert c.status == "OK"
    assert c.cache_status == "OK"
    assert c.cache_size.startswith("2")
    assert c.battery_status == "OK"
    assert c.battery_count == 1
    assert c.temperature == 50
    assert c.cache_temperature == 38
    assert c.battery_temperature == 29
    assert c.rebuild_priority
    assert c.surface_scan
    assert c.encryption

    assert [ld.number for ld in c.logical_drives] == ["1", "2"]
    ld1 = c.find_ld("1")
    assert ld1.raid_level == "RAID 1+0"
    assert ld1.size == "200.0 GB"
    assert ld1.status == "OK"
    assert ld1.os_device == "/dev/sda"
    assert ld1.stripe_size
    assert c.find_ld("2").os_device == "/dev/sdb"

    assert [pd.location for pd in c.physical_drives] == ["1I:1:1", "1I:1:2", "1I:1:3", "1I:1:4"]
    pd2 = c.find_pd("1I:1:2")
    assert pd2.status == "Predictive Failure"
    assert pd2.predictive_failure
    assert pd2.drive_type == "SAS SSD"
    assert pd2.model == "HP MO0800JEFPB"
    assert pd2.serial == "0QV1A2B3C4E6"
    pd4 = c.find_pd("1I:1:4")
    assert pd4.ssd_life_percent == pytest.approx(8.0)
    assert pd4.temperature == 62
    assert pd4.firmware == "HPD7"
    assert pd4.model == "HP MO0800JEFPB"  # SEP-Block danach darf das Modell nicht überschreiben
    assert pd4.power_on_hours is not None
    assert pd4.phy_rate
    assert all(pd.size == "800 GB" for pd in c.physical_drives)


def test_rate_p440ar_predictive_failure_and_ssd_wearout():
    c = hpe_raid.parse_ssacli_controllers(_fx("ssacli_p440ar_config_detail.txt"))[0]
    severity, findings = hpe_raid.rate_controller(c)
    assert severity == "error"
    assert c.severity == "error"
    texts = [f.text("de") for f in findings]
    assert any("1I:1:2" in t and "Predictive" in t for t in texts)
    assert any("1I:1:4" in t and "8 %" in t for t in texts)
    assert c.find_pd("1I:1:2").severity == "error"
    assert c.find_pd("1I:1:4").severity == "error"
    temp_warn = [f for f in c.find_pd("1I:1:4").reasons if f.severity == "warn"]
    assert temp_warn and "62" in temp_warn[0].text("en")
    assert c.find_pd("1I:1:1").severity == "ok"
    assert all(ld.severity == "ok" for ld in c.logical_drives)
    # Englische Texte vorhanden
    assert all(f.text("en") for f in findings)


def test_parse_ssacli_p816i_rebuild():
    c = hpe_raid.parse_ssacli_controllers(_fx("ssacli_p816i_config_detail.txt"))[0]
    assert "P816i-a" in c.model
    assert c.battery_status.startswith("Failed")
    assert c.cache_status == "Temporarily Disabled"
    ld = c.find_ld("1")
    assert ld.raid_level == "RAID 5"
    assert ld.progress_percent == pytest.approx(37.0)
    assert c.find_pd("1I:3:3").status == "Rebuilding"
    assert c.find_pd("1I:3:3").model == "HP EG001200JWJNQ"
    assert all(pd.drive_type == "SAS HDD" for pd in c.physical_drives)
    severity, findings = hpe_raid.rate_controller(c)
    assert severity == "error"  # Akku defekt bei vorhandenem Cache
    assert ld.severity == "warn"
    assert c.find_pd("1I:3:3").severity == "warn"
    assert {f.severity for f in findings if f.target == "Controller"} == {"error", "warn"}
    assert any(f.severity == "warn" and "37" in f.text("de") for f in findings)


def test_parse_ssacli_show_config_and_status():
    config = hpe_raid.parse_ssacli_controllers(_fx("ssacli_p440ar_config.txt"))
    assert len(config) == 1
    assert config[0].serial == "PDNLH0BRH7V3XP"
    assert len(config[0].logical_drives) == 2
    assert len(config[0].physical_drives) == 4
    assert config[0].find_pd("1I:1:2").status == "Predictive Failure"
    assert config[0].find_ld("2").raid_level == "RAID 1+0"
    status = hpe_raid.parse_ssacli_controllers(_fx("ssacli_p440ar_status.txt"))
    assert status[0].status == "OK"
    assert status[0].cache_status == "OK"
    assert status[0].battery_status == "OK"
    merged = hpe_raid.merge_ssacli(config, status)
    assert merged[0].status == "OK" and len(merged[0].physical_drives) == 4


def test_parse_ssacli_empty_and_garbage():
    assert hpe_raid.parse_ssacli_controllers("") == []
    assert hpe_raid.parse_ssacli_controllers("Error: No controllers detected.\n") == []


# ----------------------------------------------------------------------------- storcli


def test_parse_storcli_mr416i():
    controllers = hpe_raid.parse_storcli(_fx("storcli_mr416i_show_all.json"), _fx("storcli_mr416i_eall_sall.json"))
    assert len(controllers) == 1
    c = controllers[0]
    assert c.vendor == "storcli"
    assert c.model == "HPE MR416i-a Gen10+"
    assert c.serial == "SKC1234567"
    assert c.firmware == "5.240.02-3728"
    assert c.status == "Needs Attention"
    assert c.cache_size == "4096MB"
    assert c.battery_status == "Optimal"
    assert c.battery_temperature == 25
    assert c.temperature == 62
    assert c.pci_address == "00:c3:00:00"
    assert len(c.logical_drives) == 1
    vd = c.logical_drives[0]
    assert vd.raid_level == "RAID1"
    assert vd.status == "Degraded"
    assert vd.size == "1.745 TB"
    assert [pd.location for pd in c.physical_drives] == ["252:0", "252:1", "252:2"]
    offline = c.find_pd("252:1")
    assert offline.status == "Offline"
    assert offline.media_errors == 17
    assert offline.other_errors == 3
    assert offline.temperature == 38
    assert offline.serial == "S4YNNA0R123457"
    assert offline.firmware == "HPD3"
    assert offline.ssd_life_percent == pytest.approx(91)
    assert offline.phy_rate == "12.0Gb/s"
    assert offline.drive_type == "SAS SSD"
    assert c.find_pd("252:2").status == "Unconfigured Good"


def test_rate_storcli_offline_pd():
    c = hpe_raid.parse_storcli(_fx("storcli_mr416i_show_all.json"), _fx("storcli_mr416i_eall_sall.json"))[0]
    severity, findings = hpe_raid.rate_controller(c)
    assert severity == "error"
    assert c.find_pd("252:1").severity == "error"
    assert c.logical_drives[0].severity == "warn"
    assert any("Offline" in f.text("en") for f in findings if f.severity == "error")
    assert any("17" in f.text("en") for f in findings if f.severity == "warn")


def test_parse_storcli_without_details_and_missing_fields():
    data = {"Controllers": [{"Command Status": {"Status": "Success"},
                             "Response Data": {"Basics": {"Model": "MR216i-p"}, "Status": {"Controller Status": "Optimal"},
                                               "VD LIST": [{"DG/VD": "0/0", "TYPE": "RAID10", "State": "Optl"}],
                                               "PD LIST": [{"EID:Slt": "8:0", "State": "Onln"}]}}]}
    controllers = hpe_raid.parse_storcli(json.dumps(data))
    assert len(controllers) == 1
    c = controllers[0]
    assert c.model == "MR216i-p"
    assert c.logical_drives[0].status == "Optimal"
    assert c.physical_drives[0].location == "8:0"
    assert hpe_raid.rate_controller(c)[0] == "ok"
    assert hpe_raid.parse_storcli("not json") == []
    assert hpe_raid.parse_storcli(json.dumps({"Controllers": [{"Command Status": {"Status": "Failure"}}]})) == []


# ----------------------------------------------------------------------------- Bewertung allgemein


def _simple_controller(**kwargs) -> hpe_raid.RaidController:
    c = hpe_raid.RaidController(model="X", slot="1", status="OK", cache_size="1 GB", battery_status="OK")
    for key, value in kwargs.items():
        setattr(c, key, value)
    return c


def test_rate_thresholds():
    c = _simple_controller(temperature=85)
    assert hpe_raid.rate_controller(c)[0] == "warn"
    c = _simple_controller(status="Failed")
    assert hpe_raid.rate_controller(c)[0] == "error"
    c = _simple_controller(battery_status="Not Present")
    assert hpe_raid.rate_controller(c)[0] == "error"
    c = _simple_controller(cache_status="Temporarily Disabled")
    assert hpe_raid.rate_controller(c)[0] == "warn"
    c = _simple_controller(no_battery_write_cache="Enabled")
    assert hpe_raid.rate_controller(c)[0] == "warn"
    c = _simple_controller()
    c.physical_drives.append(hpe_raid.PhysicalDrive(location="1I:1:1", status="OK", ssd_life_percent=20))
    assert hpe_raid.rate_controller(c)[0] == "warn"
    c.physical_drives[0].ssd_life_percent = 9
    assert hpe_raid.rate_controller(c)[0] == "error"
    c = _simple_controller()
    c.physical_drives.append(hpe_raid.PhysicalDrive(location="1I:1:1", status="OK", temperature=59))
    assert hpe_raid.rate_controller(c)[0] == "ok"
    c.physical_drives[0].temperature = 60
    assert hpe_raid.rate_controller(c)[0] == "warn"
    c = _simple_controller()
    c.logical_drives.append(hpe_raid.LogicalDrive(number="1", status="Failed"))
    assert hpe_raid.rate_controller(c)[0] == "error"
    c = _simple_controller()
    c.logical_drives.append(hpe_raid.LogicalDrive(number="1", status="Interim Recovery Mode"))
    assert hpe_raid.rate_controller(c)[0] == "warn"


def test_worst_and_report_severity():
    assert hpe_raid.worst("ok", "warn", "info") == "warn"
    assert hpe_raid.worst("error", "warn") == "error"
    assert hpe_raid.worst() == "ok"
    report = hpe_raid.RaidReport()
    assert report.severity == "info"
    report = hpe_raid.RaidReport(available=True, errors=["x"])
    assert report.severity == "error"
    report = hpe_raid.RaidReport(available=True, controllers=[_simple_controller()])
    hpe_raid.rate_report(report)
    assert report.severity == "ok"
    assert "1 Controller" in hpe_raid.summary_line(report, "de")
    assert "1 controller" in hpe_raid.summary_line(report, "en")


# ----------------------------------------------------------------------------- Diff


def test_diff_reports_detects_new_errors():
    before = hpe_raid.RaidReport(available=True, controllers=hpe_raid.parse_ssacli_controllers(
        _fx("ssacli_p440ar_config_detail.txt")))
    after = hpe_raid.RaidReport(available=True, controllers=hpe_raid.parse_ssacli_controllers(
        _fx("ssacli_p440ar_config_detail.txt")))
    hpe_raid.rate_report(before)
    hpe_raid.rate_report(after)
    assert hpe_raid.diff_reports(before, after) == []
    pd = after.controllers[0].find_pd("1I:1:3")
    pd.status = "Failed"
    pd.media_errors = 5
    after.controllers[0].find_ld("1").status = "Interim Recovery Mode"
    hpe_raid.rate_report(after)
    lines = hpe_raid.diff_reports(before, after, "de")
    assert any("1I:1:3" in line and "OK → Failed" in line for line in lines)
    assert any("Medium-Fehler 0 → 5" in line for line in lines)
    assert any("LD 1" in line and "Interim Recovery Mode" in line for line in lines)
    assert any("[FEHLER]" in line for line in lines)
    lines_en = hpe_raid.diff_reports(before, after, "en")
    assert any("[ERROR]" in line for line in lines_en)
    assert hpe_raid.diff_reports(None, after) == []


# ----------------------------------------------------------------------------- collect_raid mit gemocktem Runner


class _FakeRun:
    def __init__(self, outputs: dict[str, str], fail: tuple[str, ...] = ()):
        self.outputs = outputs
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout=None, **kwargs):
        self.calls.append(list(cmd))
        key = " ".join(cmd[1:])
        if key in self.fail:
            raise subprocess.TimeoutExpired(cmd, timeout or 0)
        output = self.outputs.get(key)
        if output is None:
            return subprocess.CompletedProcess(cmd, 1, "", "Error: unknown command")
        return subprocess.CompletedProcess(cmd, 0, output, "")


def test_collect_raid_ssacli_and_storcli():
    run = _FakeRun({
        "ctrl all show config detail": _fx("ssacli_p440ar_config_detail.txt"),
        "ctrl all show status": _fx("ssacli_p440ar_status.txt"),
        "/call show all J": _fx("storcli_mr416i_show_all.json"),
        "/call/eall/sall show all J": _fx("storcli_mr416i_eall_sall.json"),
    })
    tools = {"ssacli": "/usr/sbin/ssacli", "storcli": "/opt/MegaRAID/storcli/storcli64", "ssaducli": "/usr/sbin/ssaducli"}
    report = hpe_raid.collect_raid(run, tools)
    assert report.available
    assert report.severity == "error"
    assert [c.vendor for c in report.controllers] == ["ssacli", "storcli"]
    assert report.errors == []
    assert set(report.raw_outputs) == {"ssacli ctrl all show config detail", "ssacli ctrl all show status",
                                       "storcli /call show all J", "storcli /call/eall/sall show all J"}
    assert run.calls[0][0] == "ssacli"  # Standardverzeichnis → Name
    assert run.calls[2][0] == "/opt/MegaRAID/storcli/storcli64"  # sonst absoluter Pfad
    assert all(call[0] != "pkexec" for call in run.calls)
    # pd all show detail nur, wenn config detail keine PDs enthielt
    assert not any("pd" in call for call in run.calls)
    assert "=== ssacli ctrl all show config detail ===" in report.raw_text()


def test_collect_raid_fetches_pd_detail_when_missing():
    run = _FakeRun({
        "ctrl all show config detail": _fx("ssacli_p440ar_status.txt"),
        "ctrl all show status": _fx("ssacli_p440ar_status.txt"),
        "ctrl slot=0 pd all show detail": _fx("ssacli_p440ar_config_detail.txt"),
    })
    report = hpe_raid.collect_raid(run, {"ssacli": "/usr/sbin/ssacli"})
    assert len(report.controllers) == 1
    assert len(report.controllers[0].physical_drives) == 4
    assert ["ssacli", "ctrl", "slot=0", "pd", "all", "show", "detail"] in run.calls


def test_collect_raid_handles_errors_and_missing_tools():
    report = hpe_raid.collect_raid(_FakeRun({}), {})
    assert not report.available and report.hint
    assert report.severity == "info"
    report = hpe_raid.collect_raid(None, {"ssacli": "/usr/sbin/ssacli"})
    assert report.available and report.errors and not report.controllers
    run = _FakeRun({}, fail=("ctrl all show config detail",))
    report = hpe_raid.collect_raid(run, {"ssacli": "/usr/sbin/ssacli"})
    assert report.errors and "ssacli" in report.errors[0]
    assert report.severity == "error"


def test_find_tools_and_command_name(tmp_path, monkeypatch):
    monkeypatch.setattr(hpe_raid.shutil, "which", lambda name, mode=None: None)
    monkeypatch.setattr(hpe_raid, "_PATH_FALLBACK_DIRS", (str(tmp_path / "usr-local-sbin"),))
    monkeypatch.setattr(hpe_raid, "SSACLI_DIRS", (str(tmp_path / "ssa"),))
    monkeypatch.setattr(hpe_raid, "STORCLI_DIRS", (str(tmp_path / "mr"),))
    monkeypatch.setattr(hpe_raid, "SSADUCLI_DIRS", (str(tmp_path / "du"),))
    assert hpe_raid.find_tools() == {}
    assert not hpe_raid.tools_available()
    for sub, name in (("ssa", "ssacli"), ("mr", "storcli64")):
        (tmp_path / sub).mkdir()
        tool = tmp_path / sub / name
        tool.write_text("#!/bin/sh\n")
        # HPE liefert ssacli/ssaducli mit 0500 aus: fuer normale Benutzer nicht ausfuehrbar,
        # muss trotzdem gefunden werden (Ausfuehrung erfolgt als root).
        tool.chmod(0o500 if name == "ssacli" else 0o755)
    tools = hpe_raid.find_tools()
    assert tools["ssacli"].endswith("/ssa/ssacli")
    assert tools["storcli"].endswith("/mr/storcli64")
    assert "ssaducli" not in tools
    assert hpe_raid.tools_available()
    # Live-ISO: Symlink in /usr/local/sbin hat Vorrang vor /opt und wird als Pfad uebergeben.
    local = tmp_path / "usr-local-sbin"
    local.mkdir()
    (local / "storcli64").symlink_to(tmp_path / "mr" / "storcli64")
    assert hpe_raid.find_tools()["storcli"] == str(local / "storcli64")
    assert hpe_raid.command_name("/usr/sbin/ssacli") == "ssacli"
    assert hpe_raid.command_name("/opt/MegaRAID/storcli/storcli64") == "/opt/MegaRAID/storcli/storcli64"
    assert hpe_raid.command_name("/usr/local/sbin/storcli64") == "/usr/local/sbin/storcli64"


def test_ssacli_without_controllers_is_not_an_error():
    class Run:
        def __call__(self, cmd, **kwargs):
            import subprocess
            if cmd[0] == "ssacli":
                return subprocess.CompletedProcess(cmd, 1, "", "Error: No controllers detected.\n")
            return subprocess.CompletedProcess(
                cmd, 0, '{"Controllers":[{"Command Status":{"Status":"Failure",'
                '"Description":"No Controller found"}}]}', "")

    report = hpe_raid.collect_raid(Run(), tools={"ssacli": "/usr/sbin/ssacli",
                                                 "storcli": "/usr/local/sbin/storcli64"})
    assert report.available and report.errors == [] and report.controllers == []
    assert report.severity == "info"
    assert "kein Controller gefunden" in hpe_raid.summary_line(report)


def test_save_ssaducli_report_validation():
    run = _FakeRun({"-f /x/report.zip": "ok"})
    assert hpe_raid.save_ssaducli_report(run, "/x/report.zip", {})[0] is False
    tools = {"ssaducli": "/usr/sbin/ssaducli"}
    assert hpe_raid.save_ssaducli_report(run, "relative.zip", tools)[0] is False
    assert hpe_raid.save_ssaducli_report(run, "/x/../report.zip", tools)[0] is False
    assert hpe_raid.save_ssaducli_report(run, "/x/report.txt", tools)[0] is False
    assert hpe_raid.save_ssaducli_report(run, "/x/my report.zip", tools)[0] is False
    ok, message = hpe_raid.save_ssaducli_report(run, "/x/report.zip", tools)
    assert ok and message == "/x/report.zip"
    assert run.calls[-1] == ["ssaducli", "-f", "/x/report.zip"]
    ok, message = hpe_raid.save_ssaducli_report(run, "/x/other.zip", tools)
    assert not ok and "unknown command" in message


# ----------------------------------------------------------------------------- Root-Broker-Whitelist


def _broker(tmp_path):
    for name in ("ssacli", "storcli64", "ssaducli"):
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\necho "%s $*"\n[ "$1" = -f ] && echo zip > "$2"\nexit 0\n' % name)
        tool.chmod(0o755)
    fake = tmp_path / "fakepkexec"
    fake.write_text(f'#!/bin/sh\nPATH="{tmp_path}:$PATH" exec "$@"\n')
    fake.chmod(0o755)
    broker = RootBroker(str(fake))
    broker.start()
    return broker


@pytest.mark.skipif(not os.path.exists("/bin/sh"), reason="sh fehlt")
def test_root_broker_allows_only_readonly_raid_commands(tmp_path):
    broker = _broker(tmp_path)
    try:
        allowed = [
            ["ssacli", "ctrl", "all", "show", "config", "detail"],
            ["ssacli", "ctrl", "all", "show", "status"],
            ["ssacli", "ctrl", "slot=0", "pd", "all", "show", "detail"],
            ["ssacli", "ctrl", "slot=12", "ld", "all", "show", "detail"],
            ["ssacli", "version"],
            ["storcli64", "/call", "show", "all", "J"],
            ["storcli64", "/call/eall/sall", "show", "all", "J"],
        ]
        for cmd in allowed:
            result = broker.run(cmd, timeout=15)
            assert result.returncode == 0, cmd
            assert result.stdout.strip() == " ".join(cmd), cmd
        rejected = [
            ["ssacli", "ctrl", "all", "delete"],
            ["ssacli", "ctrl", "slot=0", "ld", "1", "delete"],
            ["ssacli", "ctrl", "slot=x", "pd", "all", "show", "detail"],
            ["ssacli", "ctrl", "all", "show", "config", "detail", "forced"],
            ["storcli64", "/c0", "set", "jbod=on"],
            ["storcli64", "/call", "show", "all"],
            ["ssaducli", "-f", "relative.zip"],
            ["ssaducli", "-f", str(tmp_path / "a.txt")],
            ["ssaducli", "-f", str(tmp_path / ".." / "a.zip")],
            ["ssaducli", "-f", str(tmp_path / "a.zip"), "-v"],
        ]
        for cmd in rejected:
            with pytest.raises(RuntimeError):
                broker.run(cmd, timeout=15)
        target = tmp_path / "report.zip"
        result = broker.run(["ssaducli", "-f", str(target)], timeout=15)
        assert result.returncode == 0
        assert target.read_text().strip() == "zip"
        with pytest.raises(RuntimeError):  # vorhandene Datei darf nicht überschrieben werden
            broker.run(["ssaducli", "-f", str(target)], timeout=15)
    finally:
        broker.close()


# ----------------------------------------------------------------------------- proliant_check (Gesamttest)


def _report(fixture: str) -> hpe_raid.RaidReport:
    report = hpe_raid.RaidReport(available=True, tools={"ssacli": "/usr/sbin/ssacli"},
                                 controllers=hpe_raid.parse_ssacli_controllers(_fx(fixture)))
    return hpe_raid.rate_report(report)


def test_proliant_check_evaluate_fails_only_on_new_raid_errors():
    before = proliant_check.ProliantSnapshot(proliant=True, raid=_report("ssacli_p440ar_config_detail.txt"),
                                             iml=[], sensors=ipmi.parse_sensors("Fan 1 | 30h | ok | 29.1 | 30 percent"))
    after_same = proliant_check.ProliantSnapshot(proliant=True, raid=_report("ssacli_p440ar_config_detail.txt"),
                                                 iml=[], sensors=before.sensors)
    failed, summary, details = proliant_check.evaluate(before, after_same, "de")
    assert not failed  # Fehler bestanden bereits vorher
    assert "bereits vor dem Test" in summary
    assert "IML: 0 neue" in summary
    assert any("[FEHLER] Smart Array P440ar" in line for line in details)

    after = proliant_check.ProliantSnapshot(proliant=True, raid=_report("ssacli_p440ar_config_detail.txt"))
    after.raid.controllers[0].find_pd("1I:1:1").status = "Failed"
    hpe_raid.rate_report(after.raid)
    after.iml = ipmi.parse_iml(
        '{"Members": [{"Id": "9", "Created": "2026-11-02T10:00:00Z", "Severity": "Critical", '
        '"Message": "Drive Array Physical Drive failure (Port 1I Box 1 Bay 1)", "Oem": {"Hpe": {"Count": 1, '
        '"Repaired": false, "Class": 11, "Code": 1}}}]}')
    after.sensors = ipmi.parse_sensors("Fan 1 | 30h | cr | 29.1 | 0 percent\nTemp 1 | 31h | ok | 7.1 | 40 degrees C")
    failed, summary, details = proliant_check.evaluate(before, after, "en")
    assert failed
    assert "new RAID error" in summary
    assert "IML: 1 new entries (1 critical" in summary
    assert "Sensors: 1 new warnings" in summary
    assert any("1I:1:1" in line and "OK → Failed" in line for line in details)
    assert any("Physical Drive failure" in line for line in details)
    assert any("NEW Fan 1" in line for line in details)


def test_proliant_check_without_raid_tool():
    after = proliant_check.ProliantSnapshot(proliant=True, raid=None, iml=None, iml_error="ilorest fehlt", sensors=None,
                                            sensors_error="kein BMC")
    failed, summary, details = proliant_check.evaluate(None, after, "de")
    assert not failed
    assert "Keine ProLiant-/RAID-Daten" in summary
    assert "IML: ilorest fehlt" in details
    assert any("Keine Momentaufnahme" in line for line in details)
    assert proliant_check.evaluate(None, None, "en") == (False, "No data recorded.", [])


def test_proliant_check_snapshot_uses_single_runner(monkeypatch):
    run = _FakeRun({"ctrl all show config detail": _fx("ssacli_p816i_config_detail.txt"),
                    "ctrl all show status": "", "sdr elist": "Fan 1 | 30h | ok | 29.1 | 30 percent"})
    monkeypatch.setattr(hpe_raid, "find_tools", lambda: {"ssacli": "/usr/sbin/ssacli"})
    monkeypatch.setattr(ipmi, "ipmi_available", lambda: True)
    monkeypatch.setattr(ipmi, "ensure_ipmi_driver", lambda run: True)
    monkeypatch.setattr(ipmi, "read_iml", lambda run: ([], ""))
    snap = proliant_check.snapshot(run, proliant=True)
    assert snap.raid is not None and snap.raid.controllers[0].severity == "error"
    assert snap.iml == []
    assert snap.sensors and snap.sensors[0].name == "Fan 1"
    assert proliant_check.relevant() is True


def test_new_iml_entries_and_sensor_warnings():
    old = ipmi.parse_iml('{"Members": [{"Id": "1", "Created": "2026-01-01T00:00:00Z", "Severity": "Warning", '
                         '"Message": "Fan degraded", "Oem": {"Hpe": {"Count": 1, "Repaired": false}}}]}')
    new = ipmi.parse_iml('{"Members": [{"Id": "1", "Created": "2026-01-01T00:00:00Z", "Severity": "Warning", '
                         '"Message": "Fan degraded", "Oem": {"Hpe": {"Count": 3, "Repaired": false}}}, '
                         '{"Id": "2", "Created": "2026-01-02T00:00:00Z", "Severity": "OK", '
                         '"Message": "POST", "Oem": {"Hpe": {"Count": 1, "Repaired": false}}}]}')
    fresh = proliant_check.new_iml_entries(old, new)
    assert [e.number for e in fresh] == ["1", "2"]
    assert proliant_check.new_iml_entries(new, new) == []
    assert proliant_check.new_iml_entries(None, None) == []
    before = ipmi.parse_sensors("Fan 1 | 30h | ok | 29.1 | 30 percent\nPS 2 | 32h | cr | 10.1 | Failure detected")
    after = ipmi.parse_sensors("Fan 1 | 30h | cr | 29.1 | 0 percent\nPS 2 | 32h | cr | 10.1 | Failure detected")
    assert [s.name for s in proliant_check.new_sensor_warnings(before, after)] == ["Fan 1"]


# ----------------------------------------------------------------------------- UI


def test_proliant_panel_raid_tab(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from datetime import datetime
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hardwaretest.ui.widgets import proliant_panel
    monkeypatch.setattr(proliant_panel.ipmi, "is_proliant", lambda: False)
    monkeypatch.setattr(proliant_panel.ipmi, "ipmi_available", lambda: False)
    panel = proliant_panel.ProliantPanel()
    assert panel.pages.count() == 6
    assert panel.pages.tabText(5) == "RAID"
    assert not panel.ssaducli_btn.isEnabled()
    report = hpe_raid.RaidReport(available=True, tools={"ssacli": "/usr/sbin/ssacli", "ssaducli": "/usr/sbin/ssaducli"})
    report.controllers = (hpe_raid.parse_ssacli_controllers(_fx("ssacli_p440ar_config_detail.txt"))
                          + hpe_raid.parse_storcli(_fx("storcli_mr416i_show_all.json"),
                                                   _fx("storcli_mr416i_eall_sall.json")))
    report.raw_outputs["ssacli ctrl all show config detail"] = "RAW-TEXT"
    hpe_raid.rate_report(report)
    data = {"identity": {"vendor": "HPE", "product": "ProLiant DL380 Gen10"}, "proliant": True,
            "time": datetime.now(), "dmi_system": {}, "bmc": {}, "bmc_error": "", "sel_info": {}, "sensors": [],
            "sensors_error": "", "events": [], "events_error": "", "ecc": ("ok", "ECC ok", ""), "iml": None,
            "iml_error": "", "raid": report}
    panel.show_result(data)
    assert panel.raid_controllers.rowCount() == 2
    assert panel.raid_lds.rowCount() == 3
    assert panel.raid_pds.rowCount() == 7
    assert panel.ssaducli_btn.isEnabled()
    assert "RAID: 2 Controller" in panel.summary.text()
    assert "Fehler" in panel.raid_summary.text()
    assert "RAW-TEXT" in panel.raid_raw.toPlainText()
    assert "Predictive" in panel.raid_findings.toPlainText()
    statuses = [panel.raid_pds.item(r, 4).text() for r in range(panel.raid_pds.rowCount())]
    assert "Predictive Failure" in statuses and "Offline" in statuses
    html = panel.html_report()
    assert "RAID-Controller" in html and "0QV1A2B3C4E6" in html and "RAW-TEXT" in html
    assert any("== RAID-Controller ==" in line for line in panel._overview_lines(data))
    # Übersicht ohne "raid"-Schlüssel (ältere Datenstruktur) darf nicht abstürzen
    data_old = dict(data)
    del data_old["raid"]
    panel.show_result(data_old)
    assert "noch nicht" in panel.raid_summary.text()
    panel.show_raid_tab()
    assert panel.pages.currentIndex() == 5
    panel.close_privileged_session()


def test_disk_panel_raid_button_emits_signal(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hardwaretest.ui.widgets import disk_panel
    monkeypatch.setattr(disk_panel.hpe_raid, "find_tools", lambda: {"ssacli": "/usr/sbin/ssacli"})
    panel = disk_panel.DeviceDiskPanel()
    received = []
    panel.raid_tab_requested.connect(lambda: received.append(True))
    panel._show_raid_info()
    assert received == [True]
    assert "ProLiant Info" in panel.raid_btn.text()
    monkeypatch.setattr(disk_panel.hpe_raid, "find_tools", lambda: {})
    monkeypatch.setattr(disk_panel, "_show_info_dialog", lambda parent, title, content: received.append(content))
    panel._show_raid_info()
    assert len(received) == 2 and "ssacli" in received[1]


def test_disabled_cache_is_a_setting_not_a_warning():
    for status in ("Not Configured", "Disabled", "Not Enabled"):
        c = _simple_controller(cache_status=status)
        severity, findings = hpe_raid.rate_controller(c)
        assert severity == "ok", status
        assert any(f.severity == "info" and "Einstellung" in f.de for f in findings), status
    for status in ("Temporarily Disabled", "Permanently Disabled", "Disabled - low battery"):
        c = _simple_controller(cache_status=status)
        assert hpe_raid.rate_controller(c)[0] == "warn", status
