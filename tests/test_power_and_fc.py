"""Netzteile (dmidecode Typ 39, IPMI) und Fibre-Channel-HBAs (/sys/class/fc_host)."""

from pathlib import Path

from hardwaretest.core import device_inventory as dev
from hardwaretest.core import hardware_inventory as inv
from hardwaretest.core import ipmi

DMI_POWER = """\
# dmidecode 3.4
Handle 0x0030, DMI type 39, 22 bytes
System Power Supply
\tPower Unit Group: 1
\tLocation: Bay 1
\tName: Power Supply 1
\tManufacturer: HPE
\tSerial Number: 5WBXK0A4D8S1Z1
\tAsset Tag: Not Specified
\tModel Part Number: 865408-B21
\tRevision: 1.0
\tMax Power Capacity: 500 W
\tStatus: Present, OK
\tType: Switching
\tInput Voltage Range Switching: Auto-switch
\tPlugged: Yes
\tHot Replaceable: Yes

Handle 0x0031, DMI type 39, 22 bytes
System Power Supply
\tPower Unit Group: 1
\tLocation: Bay 2
\tName: Power Supply 2
\tManufacturer: HPE
\tSerial Number: 5WBXK0A4D8S1Z2
\tModel Part Number: 865408-B21
\tMax Power Capacity: 500 W
\tStatus: Present, Critical
\tType: Switching
\tPlugged: Yes
\tHot Replaceable: Yes

Handle 0x0032, DMI type 39, 22 bytes
System Power Supply
\tLocation: Bay 3
\tName: Power Supply 3
\tMax Power Capacity: Unknown
\tStatus: Not Present
\tPlugged: No
\tHot Replaceable: Yes

Handle 0x0040, DMI type 17, 40 bytes
Memory Device
\tSize: 16 GB
\tLocator: PROC 1 DIMM 1
\tType: DDR4
"""


def test_parse_dmidecode_power_supplies():
    memory = inv.parse_dmidecode(DMI_POWER)
    assert len(memory.devices) == 1  # Speichermodule weiterhin erkannt
    supplies = memory.power_supplies
    assert [psu.location for psu in supplies] == ["Bay 1", "Bay 2", "Bay 3"]
    assert supplies[0].model == "865408-B21" and supplies[0].max_power == "500 W"
    assert supplies[0].hot_replaceable is True and supplies[0].present
    assert [psu.severity for psu in supplies] == ["ok", "error", "info"]
    assert not supplies[2].present and supplies[2].max_power == ""
    assert inv.power_status_text("de", supplies[1]) == "Fehler"
    assert inv.power_status_text("en", supplies[2]) == "not present"
    assert inv.power_summary("de", supplies) == "2/3 bestückt, keine Redundanz, 1 mit Störung"
    assert "39" in inv.dmidecode_command()


def test_power_summary_redundant():
    supplies = [inv.PowerSupply(location=f"Bay {i}", status="Present, OK") for i in (1, 2)]
    assert inv.power_summary("en", supplies) == "2/2 present, redundant"
    lines = inv.inventory_lines("de", [], [], inv.BoardInfo(), inv.MemoryInventory(power_supplies=supplies))
    assert any(line.startswith("Netzteile (DMI): 2/2 bestückt, redundant") for line in lines)


def test_power_summary_four_supplies_one_failed():
    supplies = [inv.PowerSupply(location=f"Bay {i}", status="Present, OK") for i in (1, 2, 3)]
    supplies.append(inv.PowerSupply(location="Bay 4", status="Present, Critical"))
    assert inv.power_summary("de", supplies) == "4/4 bestückt, redundant, 1 mit Störung"
    assert [psu.severity for psu in supplies] == ["ok", "ok", "ok", "error"]


def test_power_supply_placeholders_are_ignored():
    """Desktop-BIOS: Typ 39 mit „Default string“ überall darf nicht als redundante Netzteile zählen."""
    text = ("Handle 0x0030, DMI type 39, 22 bytes\nSystem Power Supply\n\tLocation: Default string\n"
            "\tName: Default string\n\tManufacturer: Default string\n\tSerial Number: Default string\n"
            "\tModel Part Number: Default string\n\tMax Power Capacity: Unknown\n\tStatus: Present, OK\n"
            "\tType: Switching\n\tPlugged: Yes\n\tHot Replaceable: No\n\n") * 3
    supplies = inv.parse_dmidecode(text).power_supplies
    assert len(supplies) == 3
    assert all(psu.placeholder and psu.severity == "info" and psu.manufacturer == "" for psu in supplies)
    assert inv.power_status_text("de", supplies[0]) == "Platzhalter (keine Daten)"
    assert inv.power_summary("de", supplies).startswith("3 Einträge ohne Daten")
    mixed = supplies + [inv.PowerSupply(location="Bay 1", status="Present, OK")]
    assert inv.power_summary("en", mixed) == "1/1 present, 3 placeholders ignored"


SDR_PSU = ("Power Supply 1   | 9Eh | ok  | 10.1 | Presence detected\n"
           "Power Supply 2   | 9Fh | ok  | 10.2 | Presence detected, Failure detected\n")
SDR_UNIT = "Power Supplies   | A4h | ok  | 21.1 | Redundancy Lost\n"


def test_read_power_sensors_and_redundancy():
    calls = []

    class _Done:
        def __init__(self, text):
            self.returncode, self.stdout, self.stderr = 0, text, ""

    def run(cmd, **_kwargs):
        calls.append(cmd)
        return _Done(SDR_PSU if cmd[-1] == "0x08" else SDR_UNIT)

    sensors, error = ipmi.read_power_sensors(run)
    assert error == "" and [c[1:] for c in calls] == [["sdr", "type", "0x08"], ["sdr", "type", "0x09"]]
    assert [s.name for s in sensors] == ["Power Supply 1", "Power Supply 2", "Power Supplies"]
    assert sensors[1].severity == "error"
    assert sensors[2].severity == "error"  # „Redundancy Lost“ gilt als Fehler
    assert ipmi.power_redundancy(sensors) == "lost"
    assert ipmi.power_redundancy(ipmi.parse_sensors("Power Supplies | A4h | ok | 21.1 | Fully Redundant")) == "redundant"
    assert ipmi.power_redundancy([]) is None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


def _fake_fc(root: Path, host: str, pci: str, state: str, stats: dict[str, str]) -> None:
    pci_dir = root / "devices" / "pci0000:00" / "0000:00:03.0" / pci
    _write(pci_dir / "vendor", "0x10df")
    (pci_dir / "driver").symlink_to(root / "bus" / "pci" / "drivers" / "lpfc")
    scsi_dev = pci_dir / host
    scsi_dev.mkdir(parents=True)
    (scsi_dev / "rport-3:0-1").mkdir()
    (scsi_dev / "rport-3:0-2").mkdir()
    fc = root / "class" / "fc_host" / host
    fc.mkdir(parents=True)
    (fc / "device").symlink_to(scsi_dev)
    for key, value in {"port_name": "0x10000090fa1b2c3d", "node_name": "0x20000090fa1b2c3d", "port_state": state,
                       "port_type": "NPort (fabric via point-to-point)", "speed": "16 Gbit",
                       "supported_speeds": "4 Gbit, 8 Gbit, 16 Gbit", "fabric_name": "0x100000051e0a1b2c",
                       "symbolic_name": "Emulex LPe16002B-M6 FV12.8.542.0 DV14.2.0.5"}.items():
        _write(fc / key, value)
    for key, value in stats.items():
        _write(fc / "statistics" / key, value)
    scsi = root / "class" / "scsi_host" / host
    _write(scsi / "model_name", "LPe16002B-M6")
    _write(scsi / "model_desc", "Emulex LightPulse 16Gb FC HBA")
    _write(scsi / "fw_version", "12.8.542.0")
    _write(scsi / "serial_num", "FC12345678")


def test_read_fc_hosts(tmp_path):
    (tmp_path / "bus" / "pci" / "drivers" / "lpfc").mkdir(parents=True)
    _fake_fc(tmp_path, "host3", "0000:04:00.0", "Online",
             {"link_failure_count": "0x0", "loss_of_sync_count": "0x3", "invalid_crc_count": "0x0",
              "tx_frames": "0x1234", "fcp_input_megabytes": "0xffffffffffffffff", "rx_words": "garbage"})
    _fake_fc(tmp_path, "host4", "0000:04:00.1", "Linkdown",
             {"link_failure_count": "0x0", "loss_of_sync_count": "0x0"})
    hosts = dev.read_fc_hosts(tmp_path)
    assert [h.host for h in hosts] == ["host3", "host4"]
    first, second = hosts
    assert first.model == "LPe16002B-M6" and first.firmware == "12.8.542.0" and first.driver == "lpfc"
    assert first.pci == "0000:04:00.0" and first.remote_ports == 2 and first.serial == "FC12345678"
    assert first.online and first.statistics == {"link_failure_count": 0, "loss_of_sync_count": 3,
                                                 "invalid_crc_count": 0, "tx_frames": 0x1234}
    assert first.error_counters == {"loss_of_sync_count": 3} and first.severity == "warn"
    assert not second.online and second.severity == "error" and second.error_counters == {}
    assert dev.fc_wwn_text(first.port_name) == "10:00:00:90:fa:1b:2c:3d"
    assert dev.fc_wwn_text("Emulex") == "Emulex"
    assert dev.fc_state_text("de", second) == "Link unterbrochen" and dev.fc_state_text("en", first) == "online"
    assert dev.fc_speed_text("de", first) == "16 Gbit/s" and dev.fc_speed_text("de", second) == "16 Gbit/s"
    assert dev.fc_errors_text("de", first) == "loss_of_sync=3" and dev.fc_errors_text("de", second) == "0"
    lines = dev.device_lines("de", dev.DeviceInventory(fc_hosts=hosts))
    assert lines[0].startswith("FC-HBA host3: LPe16002B-M6 · WWPN 10:00:00:90:fa:1b:2c:3d · Online · 16 Gbit/s")
    assert "Fehlerzähler: loss_of_sync=3" in lines[0]
    assert "Link unterbrochen" in lines[1] and "Fabric" not in lines[1]


def test_read_fc_hosts_without_sysfs(tmp_path):
    assert dev.read_fc_hosts(tmp_path) == []
    assert dev.read_device_inventory(tmp_path).fc_hosts == []


def test_is_synergy_reads_dmi_product(monkeypatch):
    monkeypatch.setattr(ipmi, "_read", lambda path: "Synergy 480 Gen10" if path.endswith("product_name") else "")
    assert ipmi.is_synergy()
    monkeypatch.setattr(ipmi, "_read", lambda path: "ProLiant DL380 Gen10" if path.endswith("product_name") else "")
    assert not ipmi.is_synergy()
