import pytest

from hardwaretest.core import ipmi


def test_parse_sdr_fans():
    text = "Fan 1            | 30h | ok  | 29.1 | 30.58 percent\nFan 2            | 31h | ns  | 29.2 | No Reading\n"
    readings = ipmi.parse_sdr(text)
    assert len(readings) == 1
    assert readings[0].name == "Fan 1" and readings[0].unit == "%" and readings[0].value == 30.58


def test_parse_sel_memory():
    text = ("1 | 01/01/2025 | 10:00:00 | Memory #0x01 | Correctable ECC | Asserted\n"
            "2 | 01/01/2025 | 10:01:00 | Memory #0x01 | Uncorrectable ECC | Asserted\n"
            "3 | 01/01/2025 | 10:02:00 | Power Supply #0x1 | Failure detected | Asserted\n")
    events = ipmi.parse_sel_memory(text)
    assert [e.uncorrectable for e in events] == [False, True]


def test_parse_dmidecode_memory():
    text = ("Physical Memory Array\n\tError Correction Type: Multi-bit ECC\n\n"
            "Memory Device\n\tSize: 32 GB\n\tLocator: PROC 1 DIMM 1\n\tType: DDR4\n\tSpeed: 2933 MT/s\n"
            "\tManufacturer: HPE\n\tPart Number: P00924-B21\n\n"
            "Memory Device\n\tSize: No Module Installed\n\tLocator: PROC 1 DIMM 2\n")
    dmi = ipmi.parse_dmidecode_memory(text)
    assert dmi.ecc is True
    assert len(dmi.modules) == 1 and dmi.modules[0].locator == "PROC 1 DIMM 1"


def test_sel_counts_are_remembered():
    import subprocess
    text = ("1 | 01/01/2025 | 10:00:00 | Memory #0x01 | Correctable ECC | Asserted\n"
            "2 | 01/01/2025 | 10:01:00 | Memory #0x01 | Uncorrectable ECC | Asserted\n")

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, text, "")
    ipmi.sel_ecc_metrics(run)
    assert (ipmi.last_sel_counts.correctable, ipmi.last_sel_counts.uncorrectable) == (1, 1)


SEL = """   1 | 10/15/2026 | 08:00:00 | Memory #0x01 | Correctable ECC (@DIMM 3) | Asserted
   2 | 10/15/2026 | 08:05:00 | Power Supply #0x02 | Failure detected | Asserted
   3 | 10/15/2026 | 08:06:00 | Power Supply #0x02 | Failure detected | Deasserted
   4 | 10/15/2026 | 08:07:00 | System Event #0x10 | Timestamp Clock Sync | Asserted
"""


def test_parse_event_log_severity():
    events = ipmi.parse_event_log(SEL)
    assert [e.severity for e in events] == ["warn", "error", "info", "info"]
    assert events[0].memory and not events[1].memory


def test_parse_sensors():
    text = ("01-Inlet Ambient  | 01h | ok  |  7.1 | 22 degrees C\n"
            "Fan 1            | 30h | ok  | 29.1 | 30.58 percent\n"
            "Power Supply 2   | 51h | cr  | 10.2 | Failure detected\n"
            "PCI 3            | 40h | ns  | 11.3 | Disabled\n")
    sensors = ipmi.parse_sensors(text)
    assert [s.severity for s in sensors] == ["ok", "ok", "error", "info"]


def test_proliant_panel_renders(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from datetime import datetime
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hardwaretest.ui.widgets import proliant_panel
    monkeypatch.setattr(proliant_panel.ipmi, "is_proliant", lambda: False)
    monkeypatch.setattr(proliant_panel.ipmi, "ipmi_available", lambda: False)
    panel = proliant_panel.ProliantPanel()
    assert not panel.notice.isHidden()
    data = {"identity": {"vendor": "HPE", "product": "ProLiant DL380 Gen10"}, "proliant": True,
            "time": datetime.now(), "dmi_system": {"Serial Number": "CZ123"}, "bmc": {"Firmware Revision": "2.90"},
            "bmc_error": "", "sel_info": {"Entries": "4"}, "sensors": ipmi.parse_sensors("Fan 1 | 30h | ok | 29.1 | 30 percent"),
            "sensors_error": "", "events": ipmi.parse_event_log(SEL), "events_error": "",
            "ecc": ("warn", "ECC", "details"), "iml": ipmi.parse_iml(IML), "iml_error": ""}
    panel.show_result(data)
    assert panel.events.rowCount() == 4
    panel.event_filter.setCurrentIndex(2)
    assert panel.events.rowCount() == 1
    assert "CZ123" in panel.html_report()
    assert panel.iml.rowCount() == 3
    panel.iml_filter.setCurrentIndex(2)
    assert panel.iml.rowCount() == 1
    assert "Integrated Management Log" in panel.html_report()


IML = """Discovering data...Done
{"@odata.id": "/redfish/v1/Systems/1/LogServices/IML/Entries", "Members": [
 {"Id": "2", "Created": "2026-10-18T08:05:00Z", "Severity": "Critical",
  "Message": "Uncorrectable Memory Error detected (Processor 1, DIMM 3)",
  "Oem": {"Hpe": {"Class": 33, "Count": 1, "Repaired": false, "Updated": "2026-10-18T08:05:00Z",
                  "Categories": ["Hardware", "Memory"]}}},
 {"Id": "1", "Created": "2026-10-17T07:00:00Z", "Severity": "Caution",
  "Message": "Power Supply Failure (Bay 2)", "Oem": {"Hpe": {"Class": 32, "Count": 3, "Repaired": true}}},
 {"Id": "3", "Created": "2026-10-18T09:00:00Z", "Severity": "OK", "Message": "Firmware flashed"}
]}
"""


def test_parse_iml():
    entries = ipmi.parse_iml(IML)
    assert [e.number for e in entries] == ["1", "2", "3"]
    assert [e.severity for e in entries] == ["info", "error", "info"]
    assert entries[0].repaired and entries[0].category == "32" and entries[0].count == "3"
    assert entries[1].memory and entries[1].category == "Hardware, Memory"
    assert entries[1].created == "2026-10-18 08:05:00"


def test_parse_iml_rejects_garbage():
    import pytest
    with pytest.raises(ValueError):
        ipmi.parse_iml("Error: no iLO")


def test_root_broker_allows_only_fixed_ilorest_command():
    from hardwaretest.core import root_broker
    assert " ".join(ipmi.ILOREST_IML_CMD) in root_broker.ROOT_BROKER_SCRIPT


def test_iml_error_text_chif():
    err = ("ERROR   : BlobStore2.initializecreds raised exception: Failed to load dynlib/dll 'ilorest_chif.so'.\n"
           "ERROR   : CHIF session initialization failed - BlobStore2 instance is None\n")
    text = ipmi._iml_error_text(err)
    assert text.startswith("BlobStore2.initializecreds")
    assert "CHIF session initialization failed" in text
    assert "Require Host Authentication" in text


def test_parse_iml_serverlogs_list():
    from hardwaretest.core.ipmi import parse_iml
    text = 'Discovering data...Done\n[{"Id": "2", "Created": "2026-10-01T10:00:00Z", "Severity": "Critical", ' \
           '"Message": "Memory error", "Oem": {"Hpe": {"Count": 1, "Repaired": false, "Categories": ["Memory"]}}}]'
    entries = parse_iml(text)
    assert len(entries) == 1 and entries[0].severity == "error" and entries[0].category == "Memory"


def test_parse_iml_gen10_event():
    from hardwaretest.core.ipmi import parse_iml
    text = ('[{"Created": "2026-10-19T09:29:39Z", "Id": "178", "Message": "HPE Eth 10/25Gb Connectivity status '
            'changed to OK", "Oem": {"Hpe": {"Categories": ["Hardware"], "Class": 17, "ClassDescription": "Network", '
            '"Count": 1, "Repaired": true, "Severity": "Repaired", "Updated": "2026-10-19T09:29:39Z"}}, '
            '"Severity": "OK"}]')
    (e,) = parse_iml(text)
    assert e.number == "178" and e.repaired and e.severity == "info"
    assert e.category == "Hardware / Network" and e.count == "1"


def test_sel_counts_only_new_events_since_start(monkeypatch):
    monkeypatch.setattr(ipmi, "_sel_baseline", None)
    old = ipmi.SelEvent("1 | Memory | Uncorrectable ECC | Asserted", True)
    ipmi._remember([old])
    assert ipmi.last_sel_counts.new_uncorrectable == 0
    new = ipmi.SelEvent("2 | Memory | Correctable ECC | Asserted", False)
    ipmi._remember([old, new])
    c = ipmi.last_sel_counts
    assert (c.uncorrectable, c.new_correctable, c.new_uncorrectable) == (1, 1, 0)


def test_sel_baseline_matches_list_and_elist_formats(monkeypatch):
    monkeypatch.setattr(ipmi, "_sel_baseline", None)
    ipmi._remember([ipmi.SelEvent("  1a | 10/15/2026 | 12:00:00 | Memory #0x01 | Uncorrectable ECC | Asserted", True)])
    ipmi._remember([ipmi.SelEvent("1a | Pre-Init |0000001234| Memory #0x01 | Uncorrectable ECC | Asserted", True)])
    assert ipmi.last_sel_counts.new_uncorrectable == 0



@pytest.mark.parametrize(("vendor", "product", "expected"), [
    ("HPE", "ProLiant DL380 Gen10", True),
    ("HPE", "Synergy 480 Gen10 w/ PCIe Exp Mod", True),
    ("Hewlett Packard Enterprise", "Synergy 660 Gen10", True),
    ("HP", "ProLiant ML350 Gen9", True),
    ("Dell Inc.", "PowerEdge R740", False),
    ("Lenovo", "ThinkSystem Synergy", False),
    ("HPE", "EliteDesk 800", False),
])
def test_is_proliant_recognizes_hpe_ilo_servers(monkeypatch, vendor, product, expected):
    values = {"/sys/class/dmi/id/sys_vendor": vendor, "/sys/class/dmi/id/product_name": product}
    monkeypatch.setattr(ipmi, "_read", lambda path, *a, **k: values.get(path, ""))
    assert ipmi.is_proliant() is expected


def test_environment_sensors_filters_and_sorts():
    text = "\n".join([
        "Fan 1             | 30h | ok  | 29.1 | 30.58 percent",
        "Fan 2             | 31h | ns  | 29.2 | No Reading",
        "01-Inlet Ambient  | 01h | ok  | 64.1 | 21 degrees C",
        "Power Meter       | 50h | ok  | 7.1  | 180 Watts",
        "Power Supply 1    | 9Eh | ok  | 10.1 | Presence detected",
        "Memory            | 20h | ok  | 7.1  | Presence Detected",
    ])
    picked = ipmi.environment_sensors(ipmi.parse_sensors(text))
    assert [s.name for s in picked] == ["01-Inlet Ambient", "Fan 1", "Power Meter"]
    assert [ipmi.sensor_kind(s) for s in picked] == ["temperature", "fan", "power"]


# ----- Protokolle löschen (ProLiant Info) -----


class _LogRun:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, cmd, **kwargs):
        import subprocess
        self.calls.append(" ".join(cmd))
        if any(word in self.calls[-1] for word in self.fail):
            return subprocess.CompletedProcess(cmd, 1, "", "ERROR   : Chif driver not found")
        out = '{"Members": []}' if "--json" in cmd else "Clearing log...\nThe operation completed successfully.\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_clear_log_runs_fixed_commands_and_resets_sel_baseline(monkeypatch):
    monkeypatch.setattr(ipmi, "_sel_baseline", {"1", "2"})
    run = _LogRun()
    assert ipmi.clear_log(run, "IML") == (True, "The operation completed successfully.")
    assert ipmi._sel_baseline == {"1", "2"}
    assert ipmi.clear_log(run, "SEL")[0] is True
    assert ipmi._sel_baseline is None          # Eintragsnummern beginnen neu
    assert run.calls == ["ilorest --nologo serverlogs --selectlog=IML --clearlog", "ipmitool sel clear"]
    ok, message = ipmi.clear_log(_LogRun(fail=["IEL"]), "IEL")
    assert not ok and "Chif driver not found" in message


def test_clear_logs_backs_up_iel_first_and_skips_clearing_when_backup_fails(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from hardwaretest.ui.widgets import proliant_panel
    target = tmp_path / "iel.json"
    monkeypatch.setattr(proliant_panel.hwreport, "report_path", lambda *a, **k: target)
    run = _LogRun()
    results = proliant_panel.clear_logs(run, ["SEL", "IEL"])
    assert [(log, ok) for log, ok, _m in results] == [("SEL", True), ("IEL", True)]
    assert target.read_text() == '{"Members": []}' and str(target) in results[1][2]
    assert run.calls == ["ipmitool sel clear", "ilorest --nologo serverlogs --selectlog=IEL --json",
                         "ilorest --nologo serverlogs --selectlog=IEL --clearlog"]
    failing = _LogRun(fail=["--json"])
    results = proliant_panel.clear_logs(failing, ["IEL"])
    assert results[0][1] is False and not any("--clearlog" in call for call in failing.calls)
    assert [ok for _l, ok, _m in proliant_panel.clear_logs(None, ["IML"])] == [False]


def test_clear_logs_dialog_requires_a_selection(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    QApplication.instance() or QApplication([])
    from hardwaretest.ui.widgets import proliant_panel
    dialog = proliant_panel.ClearLogsDialog(["SEL", "IML"])
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled() and dialog.selected() == []
    assert not dialog.boxes["IEL"].isEnabled()           # Werkzeug nicht verfügbar
    dialog.boxes["IML"].setChecked(True)
    assert ok.isEnabled() and dialog.selected() == ["IML"]


def test_proliant_panel_clears_logs_saves_report_first_and_rereads(tmp_path, monkeypatch):
    import os
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hardwaretest.ui.widgets import proliant_panel
    monkeypatch.setattr(proliant_panel.ipmi, "is_proliant", lambda: False)
    monkeypatch.setattr(proliant_panel.ipmi, "ipmi_available", lambda: False)
    monkeypatch.setattr(proliant_panel.ipmi, "clearable_logs", lambda: ["SEL", "IML"])
    panel = proliant_panel.ProliantPanel()
    run, order = _LogRun(fail=["IML"]), []
    monkeypatch.setattr(panel, "root_runner", lambda: run)
    monkeypatch.setattr(panel, "export", lambda: order.append("export"))
    monkeypatch.setattr(panel, "start", lambda: order.append("reread"))
    panel._start_clear(["SEL", "IML"])
    assert not panel.clear_btn.isEnabled()
    deadline = time.time() + 10
    while panel._clear_worker is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert order == ["export", "reread"]                      # Bericht vor dem Löschen, danach neu auslesen
    assert run.calls[0] == "ipmitool sel clear"
    text = panel.clear_status.text()
    assert "SEL: gelöscht" in text and "IML: FEHLER" in text and "Chif driver not found" in text
    assert panel.clear_btn.isEnabled()
