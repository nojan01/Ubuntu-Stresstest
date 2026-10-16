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
