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
            "ecc": ("warn", "ECC", "details")}
    panel.show_result(data)
    assert panel.events.rowCount() == 4
    panel.event_filter.setCurrentIndex(2)
    assert panel.events.rowCount() == 1
    assert "CZ123" in panel.html_report()
