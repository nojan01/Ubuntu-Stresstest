"""Tests für die technische Übersicht (Tab „System“)."""

from __future__ import annotations

import json
import subprocess

from hardwaretest.core import hardware_inventory as inv

CPUINFO = """\
processor\t: 0
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 85
model name\t: Intel(R) Xeon(R) Gold 6230 CPU @ 2.10GHz
stepping\t: 7
microcode\t: 0x5003604
physical id\t: 0
core id\t\t: 0
flags\t\t: fpu sse4_2 avx avx2 fma avx512f aes vmx
bugs\t\t: spectre_v1 mds

processor\t: 1
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 85
model name\t: Intel(R) Xeon(R) Gold 6230 CPU @ 2.10GHz
stepping\t: 7
microcode\t: 0x5003604
physical id\t: 0
core id\t\t: 0
flags\t\t: fpu sse4_2 avx avx2 fma avx512f aes vmx
bugs\t\t: spectre_v1 mds

processor\t: 2
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 85
model name\t: Intel(R) Xeon(R) Gold 6230 CPU @ 2.10GHz
stepping\t: 7
microcode\t: 0x5003604
physical id\t: 1
core id\t\t: 0
flags\t\t: fpu sse4_2 avx avx2 fma avx512f aes vmx
bugs\t\t: spectre_v1 mds
"""

DMIDECODE = """\
# dmidecode 3.5
Handle 0x0001, DMI type 1, 27 bytes
System Information
\tManufacturer: HPE
\tProduct Name: ProLiant DL380 Gen10
\tSerial Number: CZJ1234567

Handle 0x1000, DMI type 16, 23 bytes
Physical Memory Array
\tLocation: System Board Or Motherboard
\tUse: System Memory
\tError Correction Type: Multi-bit ECC
\tMaximum Capacity: 1536 GB
\tNumber Of Devices: 12

Handle 0x1001, DMI type 16, 23 bytes
Physical Memory Array
\tLocation: System Board Or Motherboard
\tUse: Flash Memory
\tError Correction Type: None
\tNumber Of Devices: 1

Handle 0x1100, DMI type 17, 84 bytes
Memory Device
\tTotal Width: 72 bits
\tData Width: 64 bits
\tSize: 32 GB
\tForm Factor: DIMM
\tLocator: PROC 1 DIMM 1
\tBank Locator: Not Specified
\tType: DDR4
\tType Detail: Synchronous Registered (Buffered)
\tSpeed: 2933 MT/s
\tManufacturer: HPE
\tSerial Number: Not Specified
\tPart Number: P00924-B21
\tRank: 2
\tConfigured Memory Speed: 2666 MT/s
\tConfigured Voltage: 1.2 V

Handle 0x1101, DMI type 17, 84 bytes
Memory Device
\tTotal Width: Unknown
\tData Width: Unknown
\tSize: No Module Installed
\tForm Factor: DIMM
\tLocator: PROC 1 DIMM 2
\tType: Unknown
"""


def test_parse_cpuinfo_groups_sockets() -> None:
    packages = inv.parse_cpuinfo(CPUINFO)
    assert [p.socket for p in packages] == ["0", "1"]
    first = packages[0]
    assert first.threads == 2 and first.core_count == 1
    assert first.microcode == "0x5003604" and first.family == "6" and first.model == "85"
    assert first.notable_flags == ["SSE4.2", "AVX", "AVX2", "FMA", "AVX-512", "AES-NI", "VT-x"]
    assert first.bugs == ["spectre_v1", "mds"]


def test_hybrid_core_types_and_clocks(tmp_path) -> None:
    cpu_root, hybrid = tmp_path / "cpu", tmp_path / "devices"
    for cpu, core, cur in ((0, 0, 3000000), (1, 0, 3100000), (2, 8, 1200000)):
        base = cpu_root / f"cpu{cpu}"
        (base / "topology").mkdir(parents=True)
        (base / "cpufreq").mkdir()
        (base / "topology/core_id").write_text(f"{core}\n")
        (base / "cpufreq/scaling_cur_freq").write_text(f"{cur}\n")
        (base / "cpufreq/cpuinfo_min_freq").write_text("400000\n")
        (base / "cpufreq/cpuinfo_max_freq").write_text("5000000\n")
    (hybrid / "cpu_core").mkdir(parents=True)
    (hybrid / "cpu_atom").mkdir()
    (hybrid / "cpu_core/cpus").write_text("0-1\n")
    (hybrid / "cpu_atom/cpus").write_text("2\n")

    assert inv.parse_cpulist("0-3,8,10-11") == {0, 1, 2, 3, 8, 10, 11}
    assert inv.read_core_types(hybrid) == {0: "P", 1: "P", 2: "E"}
    clocks = inv.read_cpu_clocks(sysfs=cpu_root, hybrid_sysfs=hybrid)
    assert [(c.cpu, c.core_type, c.current_mhz, c.max_mhz) for c in clocks] == [
        (0, "P", 3000.0, 5000.0), (1, "P", 3100.0, 5000.0), (2, "E", 1200.0, 5000.0),
    ]
    # Nur ein Kerntyp vorhanden → keine Hybrid-CPU
    (hybrid / "cpu_atom/cpus").write_text("")
    assert inv.read_core_types(hybrid) == {}

    package = inv.CpuPackage("0", cores={"0", "8"}, p_cores={"0"}, e_cores={"8"})
    assert package.core_text == "2 (1 P + 1 E)"


def test_parse_lscpu_caches() -> None:
    text = json.dumps({"caches": [
        {"name": "L3", "one-size": "25165824", "all-size": "25165824", "ways": 12, "type": "Unified",
         "level": 3, "sets": 32768},
        {"name": "L1d", "one-size": "49152", "all-size": "425984", "ways": 12, "type": "Data", "level": 1,
         "sets": 64},
    ]})
    caches = inv.parse_lscpu_caches(text)
    assert [c.name for c in caches] == ["L1d", "L3"]
    assert caches[0].ways == 12 and caches[1].sets == 32768
    assert inv.parse_lscpu_caches("kein json") == []


def test_read_board_info_filters_placeholders(tmp_path) -> None:
    for key, value in (("sys_vendor", "HPE"), ("product_name", "ProLiant DL380 Gen10"),
                       ("board_version", "Not Defined"), ("product_serial", "To be filled by O.E.M."),
                       ("bios_version", "U30")):
        (tmp_path / key).write_text(value + "\n")
    board = inv.read_board_info(tmp_path)
    assert board.values == {"sys_vendor": "HPE", "product_name": "ProLiant DL380 Gen10", "bios_version": "U30"}


def test_parse_dmidecode_modules_and_ecc() -> None:
    memory = inv.parse_dmidecode(DMIDECODE)
    assert memory.system_serial == "CZJ1234567"
    assert memory.error_correction == ["Multi-bit ECC"]  # Flash-Array ignoriert
    assert memory.max_capacity == ["1536 GB"] and memory.slot_count == 12
    assert len(memory.devices) == 2 and len(memory.installed) == 1
    module = memory.installed[0]
    assert module.locator == "PROC 1 DIMM 1" and module.bank == ""
    assert module.ecc is True and module.size_mib == 32 * 1024
    assert module.configured_speed == "2666 MT/s" and module.rank == "2" and module.serial == ""
    assert memory.devices[1].ecc is None
    assert memory.total_mib == 32 * 1024


def test_read_memory_inventory_uses_runner() -> None:
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, DMIDECODE, "")

    memory, error = inv.read_memory_inventory(run)
    assert error == "" and memory is not None and len(memory.installed) == 1
    assert calls == [["dmidecode", "-t", "1", "-t", "16", "-t", "17"]]

    def denied(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "sudo: Ein Passwort ist notwendig\n")

    assert inv.read_memory_inventory(denied) == (None, "sudo: Ein Passwort ist notwendig")


def test_inventory_lines_text() -> None:
    packages = inv.parse_cpuinfo(CPUINFO)
    board = inv.BoardInfo({"sys_vendor": "HPE", "product_name": "ProLiant DL380 Gen10", "bios_version": "U30"})
    caches = [inv.CacheLevel("L3", 3, "Unified", "27.5 MiB", "55 MiB", 11, 40960)]
    lines = inv.inventory_lines("en", packages, caches, board, inv.parse_dmidecode(DMIDECODE))
    text = "\n".join(lines)
    assert "System: HPE ProLiant DL380 Gen10 (S/N CZJ1234567)" in text
    assert "CPU socket 1: Intel(R) Xeon(R) Gold 6230" in text
    assert "Cache: L3 55 MiB" in text
    assert "Memory: 32 GiB, 1/12 slots populated, error correction: Multi-bit ECC" in text
    assert "PROC 1 DIMM 1 · 32 GB · DDR4 · DIMM · 2666 MT/s · Rank 2 · ECC · HPE · P00924-B21" in text
    german = inv.inventory_lines("de", packages, [], board, None, "sudo: Ein Passwort ist notwendig")
    assert german[-1] == "Speichermodule: nicht lesbar (sudo: Ein Passwort ist notwendig)"


def test_system_panel_smoke(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication

    from hardwaretest.ui.i18n import language_manager
    from hardwaretest.ui.widgets import system_panel

    monkeypatch.setattr(system_panel.inv, "read_cpu_packages", lambda: inv.parse_cpuinfo(CPUINFO))
    monkeypatch.setattr(system_panel.inv, "read_caches", lambda: [])
    monkeypatch.setattr(system_panel.inv, "read_board_info", lambda: inv.BoardInfo({"sys_vendor": "HPE"}))
    monkeypatch.setattr(system_panel.inv, "read_memory_inventory",
                        lambda run=None: (inv.parse_dmidecode(DMIDECODE), ""))
    app = QApplication.instance() or QApplication([])
    previous = language_manager.language
    try:
        language_manager.set_language("de")
        panel = system_panel.SystemPanel()
        assert panel.cpu_table.columnCount() == 3  # Beschriftung + 2 Sockel
        assert panel.memory_table.rowCount() == 2
        assert panel.memory_table.item(1, 2).text() == "leer"
        assert "Belegt: 1 / 12" in panel.memory_summary.text()
        assert not panel.password_button.isVisibleTo(panel)
        language_manager.set_language("en")
        assert panel.memory_group.title() == "Memory"
        assert panel.memory_table.item(1, 2).text() == "empty"
        assert panel.memory_table.item(0, 8).text() == "yes"
        assert "Memory: 32 GiB" in "\n".join(panel.text_lines())
        panel.deleteLater()
        app.processEvents()
    finally:
        language_manager.set_language(previous)
