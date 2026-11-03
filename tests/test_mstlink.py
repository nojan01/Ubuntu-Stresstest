"""mstlink (mstflint): Parser und Bewertung der Mellanox-Link-Diagnose."""

import subprocess

from hardwaretest.core import network

MSTLINK_OK = """
Operational Info
----------------
State                           : Active
Physical state                  : LinkUp
Speed                           : 25G
Width                           : 1x
FEC                             : Firecode FEC
Loopback Mode                   : No Loopback
Auto Negotiation                : ON

Supported Info
--------------
Enabled Link Speed (Ext.)       : 0x00000800 (25G)
Supported Cable Speed (Ext.)    : 0x00000800 (25G)

Troubleshooting Info
--------------------
Status Opcode                   : 0
Group Opcode                    : N/A
Recommendation                  : No issue was observed

Tool Information
----------------
Firmware Version                : 16.35.2000
amBER Version                   : 2.17
MFT Version                     : mstflint 4.26.0

Module Info
-----------
Identifier                      : SFP/SFP+/SFP28
Compliance                      : 25GBASE-SR
Cable Technology                : 850 nm VCSEL
Cable Type                      : Optical Module (separated)
OUI                             : Mellanox
Vendor Name                     : Mellanox
Vendor Part Number              : MMA2P00-AS
Vendor Serial Number            : MT1234X56789
Rev                             : A2
Wavelength [nm]                 : 850
Transfer Distance [m]           : 100
Digital Diagnostic Monitoring   : Yes
Power Class                     : 1.5 W max
Temperature [C]                 : 38 [-10..80]
Voltage [mV]                    : 3296 [2900..3600]
Bias Current [mA]               : 7.27 [2..11]
Rx Power Current [dBm]          : -1.9 [-13.3..2.4]
Tx Power Current [dBm]          : -1.1 [-8..2.4]

Physical Counters and BER Info
------------------------------
Time Since Last Clear [Min]     : 1234.5
Effective Physical Errors       : 0
Effective Physical BER          : 15E-255
Raw Physical Errors Per Lane    : 0
Raw Physical BER                : 15E-255
Link Down Counter               : 0
Link Error Recovery Counter     : 0
"""


def _completed(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(["mstlink"], rc, stdout=stdout, stderr=stderr)


def test_parse_mstlink_ok():
    result = network.parse_mstlink(MSTLINK_OK)
    assert result.link_up is True
    assert result.speed == "25G"
    assert result.fec == "Firecode FEC"
    assert result.recommendation == "No issue was observed"
    assert result.effective_errors == 0
    assert result.raw_ber == "15E-255"
    assert result.module["Vendor Part Number"] == "MMA2P00-AS"
    assert result.module["Rx Power Current [dBm]"] == "-1.9 [-13.3..2.4]"
    assert "Wavelength [nm]" not in result.module
    assert result.warnings == []
    assert result.severity == "ok"


def test_parse_mstlink_warnings():
    text = (MSTLINK_OK.replace("Recommendation                  : No issue was observed",
                               "Recommendation                  : Cable/module is not supported")
            .replace("Effective Physical Errors       : 0", "Effective Physical Errors       : 17")
            .replace("Link Down Counter               : 0", "Link Down Counter               : 3")
            .replace("Rx Power Current [dBm]          : -1.9 [-13.3..2.4]",
                     "Rx Power Current [dBm]          : -15.2 [-13.3..2.4]"))
    result = network.parse_mstlink(text)
    assert result.severity == "warn"
    assert any("Cable/module is not supported" in w for w in result.warnings)
    assert any("Effective Physical Errors: 17" in w for w in result.warnings)
    assert any("Link Down Counter: 3" in w for w in result.warnings)
    assert any("Rx Power Current [dBm] -15.2" in w for w in result.warnings)


def test_parse_mstlink_link_down():
    text = (MSTLINK_OK.replace("State                           : Active", "State                           : Disable")
            .replace("Physical state                  : LinkUp", "Physical state                  : Disabled"))
    result = network.parse_mstlink(text)
    assert result.link_up is False
    assert result.severity == "error"


def test_run_mstlink_device_error():
    def run(cmd, **kwargs):
        assert cmd == ["mstlink", "-d", "0000:2e:00.0", "--show_module", "--show_counters"]
        return _completed(stderr="-E- Failed to open device: 0000:2e:00.0 (No such device)\n", rc=1)
    result = network.run_mstlink("0000:2e:00.0", run=run)
    assert result.error.startswith("-E- Failed to open device")
    assert result.severity == "error"
    assert network.run_mstlink("/dev/mst/mt4119", run=run).error.startswith("ungültige PCI-Adresse")


def test_run_mstlink_warning_on_nonzero_with_data():
    def run(cmd, **kwargs):
        return _completed(stdout=MSTLINK_OK, stderr="-W- Module info not available\n", rc=1)
    result = network.run_mstlink("2e:00.0", run=run)
    assert not result.error
    assert result.severity == "warn"
    assert "-W- Module info not available" in result.warnings


def test_is_mellanox():
    mlx = network.NetworkAdapter("ens1f0", "up", True, "aa", 1500, 25000, "full", "mlx5_core", "0000:2e:00.0")
    rtl = network.NetworkAdapter("enp46s0", "up", True, "aa", 1500, 1000, "full", "r8169", "0000:2e:00.0")
    virt = network.NetworkAdapter("docker0", "up", None, "aa", 1500, None, "", "mlx5_core", "virtual")
    assert network.is_mellanox(mlx)
    assert not network.is_mellanox(rtl)
    assert not network.is_mellanox(virt)
