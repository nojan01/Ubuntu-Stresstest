"""ethtool: Fehlerzähler (-S), Selbsttest (-t) und Modulinfo (-m)."""

import subprocess

from hardwaretest.core import network

STATS_BEFORE = """NIC statistics:
     rx_packets: 1000
     tx_packets: 900
     rx_bytes: 123456
     rx_crc_errors: 0
     rx_missed_errors: 4
     tx_timeout_count: 0
     rx_length_errors: 0
     rx_no_buffer_count: 7
     rx_queue_0_packets: 500
     tx_errors: 0
     link_down_events: 1
     rx_fifo_errors: 0
"""
STATS_AFTER = STATS_BEFORE.replace("rx_crc_errors: 0", "rx_crc_errors: 3").replace(
    "rx_missed_errors: 4", "rx_missed_errors: 9").replace("rx_no_buffer_count: 7", "rx_no_buffer_count: 70").replace(
    "rx_packets: 1000", "rx_packets: 5000").replace("link_down_events: 1", "link_down_events: 2")


def test_parse_stats_and_deltas():
    before = network.parse_ethtool_stats(STATS_BEFORE)
    after = network.parse_ethtool_stats(STATS_AFTER)
    assert before["rx_packets"] == 1000 and "NIC statistics" not in before
    assert network.error_counter_deltas(before, after) == {"rx_crc_errors": 3, "rx_missed_errors": 5,
                                                            "link_down_events": 1}
    assert network.error_counters(before) == {"rx_missed_errors": 4, "link_down_events": 1}
    assert network.error_counter_deltas(before, before) == {}


def test_read_stats_uses_runner_and_tolerates_failure():
    def run(command, **_kwargs):
        assert command == ["ethtool", "-S", "eth0"]
        return subprocess.CompletedProcess(command, 0, STATS_BEFORE, "")

    assert network.read_ethtool_stats("eth0", run)["tx_packets"] == 900

    def failing(command, **_kwargs):
        return subprocess.CompletedProcess(command, 94, "", "no stats available")

    assert network.read_ethtool_stats("eth0", failing) == {}
    assert network.read_ethtool_stats("", run) == {}


SELFTEST_PASS = """The test result is PASS
The test extra info:
Register test  (offline)	 0
Eeprom test    (offline)	 0
Interrupt test (offline)	 0
Loopback test  (offline)	 0
Link test   (on/offline)	 0
"""
SELFTEST_FAIL = SELFTEST_PASS.replace("PASS", "FAIL").replace("Loopback test  (offline)\t 0", "Loopback test  (offline)\t 13")


def test_parse_selftest():
    passed = network.parse_ethtool_selftest(SELFTEST_PASS)
    assert passed.passed is True and len(passed.entries) == 5 and passed.failed_entries == []
    assert passed.entries[0] == ("Register test  (offline)", 0)
    failed = network.parse_ethtool_selftest(SELFTEST_FAIL)
    assert failed.passed is False and failed.failed_entries == [("Loopback test  (offline)", 13)]
    unsupported = network.parse_ethtool_selftest("", 1, "Cannot test: Operation not supported")
    assert unsupported.passed is None and unsupported.error == "Cannot test: Operation not supported"


def test_selftest_fail_flag_without_failed_entries_is_inconclusive():
    # bnxt_en auf NPAR-/geteilter PF: Kernel setzt nur das FAIL-Flag, alle Einzeltests bleiben 0
    text = ("The test result is FAIL\n"
            "NVM test (online)         0\n"
            "Link test (online)        0\n"
            "Register test (offline)   0\n"
            "Ext loopback test (offline) 0\n")
    result = network.parse_ethtool_selftest(text, returncode=1)
    assert result.passed is False and not result.failed_entries
    assert result.inconclusive
    failed = network.parse_ethtool_selftest("The test result is FAIL\nLink test (online)   1\n", 1)
    assert failed.inconclusive is False
    assert network.parse_ethtool_selftest("The test result is PASS\nLink test (online)   0\n").inconclusive is False


def test_run_selftest_command():
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, SELFTEST_PASS, "")

    assert network.run_ethtool_selftest("ens1f0", run=run).passed is True
    assert network.run_ethtool_selftest("ens1f0", offline=True, run=run).passed is True
    assert calls == [["ethtool", "-t", "ens1f0", "online"], ["ethtool", "-t", "ens1f0", "offline"]]

    def broken(command, **_kwargs):
        raise OSError("ethtool fehlt")

    assert network.run_ethtool_selftest("eth0", run=broken).error == "ethtool fehlt"


MODULE = """	Identifier                                : 0x03 (SFP)
	Extended identifier                       : 0x04 (GBIC/SFP defined by 2-wire interface ID)
	Connector                                 : 0x07 (LC)
	Transceiver type                          : 10G Ethernet: 10G Base-SR
	Encoding                                  : 0x06 (64B/66B)
	BR, Nominal                               : 10300MBd
	Length (SMF)                              : 0km
	Length (OM3 50um)                         : 300m
	Laser wavelength                          : 850nm
	Vendor name                               : FINISAR CORP.
	Vendor PN                                 : FTLX8571D3BCL
	Vendor rev                                : A
	Vendor SN                                 : AP123456
	Module temperature                        : 38.12 degrees C / 100.62 degrees F
	Module voltage                            : 3.3012 V
	Laser bias current                        : 6.234 mA
	Laser output power                        : 0.5912 mW / -2.28 dBm
	Receiver signal average optical power     : 0.0001 mW / -40.00 dBm
	Laser bias current high alarm             : Off
	Laser bias current low alarm              : Off
	Laser rx power low alarm                  : On
	Laser rx power low warning                : On
"""


def test_parse_module_and_alarms():
    values = network.parse_ethtool_module(MODULE)
    assert values["Vendor name"] == "FINISAR CORP." and values["Vendor PN"] == "FTLX8571D3BCL"
    assert values["Transceiver type"] == "10G Ethernet: 10G Base-SR"
    assert values["Module temperature"] == "38.12 degrees C / 100.62 degrees F"
    assert values["Alarms"] == "Laser rx power low alarm, Laser rx power low warning"
    assert "Extended identifier" not in values


def test_read_module_without_module_is_not_an_error():
    def run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "Cannot get module EEPROM information: No such device")

    assert network.read_ethtool_module("eth0", run) == ({}, "")

    def other(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "netlink error: Operation not permitted")

    assert network.read_ethtool_module("eth0", other) == ({}, "netlink error: Operation not permitted")
