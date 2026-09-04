import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace

from hardwaretest.core.network import list_network_adapters
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.network import NetworkRunner, threshold_failures


def test_ping_command_uses_operator_target_and_count():
    runner = NetworkRunner(
        TestParameters(duration_seconds=30),
        target="192.168.10.20",
        mode="ping",
        ping_count=7,
    )
    assert runner.build_command() == [
        "env", "LC_ALL=C", "ping", "-c", "7", "-W", "2", "--", "192.168.10.20"
    ]


def test_ping_and_iperf_can_bind_one_selected_adapter():
    ping = NetworkRunner(
        TestParameters(duration_seconds=30), "192.168.10.20", "ping",
        interface="eno2",
    )
    assert ping.build_command()[-4:] == ["-I", "eno2", "--", "192.168.10.20"]
    iperf = NetworkRunner(
        TestParameters(duration_seconds=30), "192.168.10.20", "iperf3",
        iperf_seconds=15, source_address="192.168.10.4/24",
    )
    assert iperf.build_command()[-2:] == ["-B", "192.168.10.4"]


def test_iperf_command_uses_configured_duration_not_watchdog_time():
    runner = NetworkRunner(
        TestParameters(duration_seconds=35),
        target="iperf.example.test",
        mode="iperf3",
        iperf_seconds=30,
    )
    assert runner.build_command() == ["iperf3", "-c", "iperf.example.test", "-t", "30", "-J"]


def test_ping_summary_parses_loss_and_latency():
    runner = NetworkRunner(TestParameters(duration_seconds=30), "host", "ping")
    runner.output_lines = [
        "10 packets transmitted, 10 received, 0% packet loss, time 9011ms",
        "rtt min/avg/max/mdev = 0.200/0.400/0.800/0.100 ms",
    ]
    assert runner.summary() == {
        "packet_loss_percent": 0.0,
        "latency_min_ms": 0.2,
        "latency_avg_ms": 0.4,
        "latency_max_ms": 0.8,
        "jitter_ms": 0.1,
    }


def test_ping_compact_report_omits_individual_packets():
    runner = NetworkRunner(TestParameters(duration_seconds=30), "host", "ping")
    runner.output_lines = [
        "PING host (192.0.2.1) 56(84) bytes of data.",
        *[
            f"64 bytes from 192.0.2.1: icmp_seq={number} ttl=64 time=0.400 ms"
            for number in range(1, 101)
        ],
        "100 packets transmitted, 100 received, 0% packet loss, time 99000ms",
        "rtt min/avg/max/mdev = 0.200/0.400/0.800/0.100 ms",
    ]
    lines = runner.compact_report_lines("de")
    assert len(lines) == 2
    assert "gesendet 100" in lines[0]
    assert "Ø 0.400 ms" in lines[1]
    assert "icmp_seq" not in "\n".join(lines)


def test_iperf_summary_parses_json_output():
    runner = NetworkRunner(TestParameters(duration_seconds=30), "host", "iperf3")
    runner.output_lines = [json.dumps({
        "end": {
            "sum_received": {"bits_per_second": 250000000},
            "sum_sent": {"retransmits": 3},
        }
    })]
    assert runner.summary() == {
        "throughput_mbit_s": 250.0,
        "retransmits": 3.0,
        "measurement_seconds": 30.0,
    }


def test_iperf_compact_report_uses_only_final_aggregates():
    runner = NetworkRunner(TestParameters(duration_seconds=35), "host", "iperf3")
    runner.output_lines = [json.dumps({
        "intervals": [
            {"sum": {"seconds": 1, "bytes": 125_000_000, "bits_per_second": 1e9}}
            for _ in range(3_600)
        ],
        "end": {
            "sum_sent": {
                "seconds": 3600,
                "bytes": 450_000_000_000,
                "bits_per_second": 1_000_000_000,
                "retransmits": 12,
            },
            "sum_received": {
                "seconds": 3600,
                "bytes": 449_000_000_000,
                "bits_per_second": 997_777_777,
            },
            "cpu_utilization_percent": {"host_total": 4.2, "remote_total": 5.1},
            "sender_tcp_congestion": "cubic",
        },
    })]
    lines = runner.compact_report_lines("en")
    assert len(lines) == 5
    assert "3600.0 s" in lines[0]
    assert "997.8 Mbit/s" in lines[1]
    assert "retransmits 12" in lines[2]
    assert "intervals" not in "\n".join(lines)


def test_iperf_cleanup_error_after_near_complete_transfer_is_accepted():
    runner = NetworkRunner(
        TestParameters(duration_seconds=35), "host", "iperf3", iperf_seconds=30
    )
    runner.output_lines = [json.dumps({
        "intervals": [
            {
                "sum": {
                    "seconds": 1.0,
                    "bytes": 112_500_000,
                    "bits_per_second": 900_000_000,
                    "retransmits": 0,
                    "omitted": False,
                }
            }
            for _ in range(29)
        ],
        "end": {"streams": []},
        "error": "interrupt - the client has terminated",
    })]
    runner._process = SimpleNamespace(returncode=1)
    runner._start_time = time.time() - 29

    runner._finalize_result()

    assert runner.get_result().passed is True
    assert runner.completion_warning == "iperf3_cleanup_error"
    assert runner.summary() == {
        "throughput_mbit_s": 900.0,
        "retransmits": 0.0,
        "measurement_seconds": 29.0,
    }


def test_iperf_cleanup_error_after_short_transfer_remains_failure():
    runner = NetworkRunner(
        TestParameters(duration_seconds=35), "host", "iperf3", iperf_seconds=30
    )
    runner.output_lines = [json.dumps({
        "intervals": [{"sum": {"seconds": 1.0, "bytes": 1_000_000}} for _ in range(5)],
        "error": "interrupt - the client has terminated",
    })]
    runner._process = SimpleNamespace(returncode=1)
    runner._start_time = time.time() - 5

    runner._finalize_result()

    assert runner.get_result().passed is False
    assert runner.completion_warning == ""


def test_iperf_connection_refused_is_classified_from_json():
    runner = NetworkRunner(TestParameters(duration_seconds=30), "host", "iperf3")
    runner.output_lines = [json.dumps({
        "start": {"connected": []},
        "error": "unable to connect to server: Connection refused",
    })]
    assert runner.failure() == (
        "connection_refused", "unable to connect to server: Connection refused"
    )


def test_ping_dns_and_routing_errors_are_classified():
    runner = NetworkRunner(TestParameters(duration_seconds=30), "host", "ping")
    runner.output_lines = ["ping: host: Name or service not known"]
    assert runner.failure()[0] == "dns_failed"
    runner.output_lines = ["ping: connect: Network is unreachable"]
    assert runner.failure()[0] == "network_unreachable"


def test_thresholds_flag_loss_latency_jitter_and_low_throughput():
    ping_failures = threshold_failures(
        {"packet_loss_percent": 1.0, "latency_avg_ms": 21.0, "jitter_ms": 6.0},
        max_packet_loss_percent=0.0,
        max_latency_avg_ms=20.0,
        max_jitter_ms=5.0,
        min_throughput_mbit_s=0.0,
    )
    assert [item[0] for item in ping_failures] == ["packet_loss", "latency", "jitter"]
    throughput_failures = threshold_failures(
        {"throughput_mbit_s": 900.0},
        max_packet_loss_percent=0.0,
        max_latency_avg_ms=100.0,
        max_jitter_ms=20.0,
        min_throughput_mbit_s=1000.0,
    )
    assert [item[0] for item in throughput_failures] == ["throughput"]


def test_network_adapter_discovery_reads_sysfs_and_ip_json():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sysfs = root / "net"
        interface = sysfs / "eno1"
        pci_device = root / "devices" / "0000:01:00.0"
        driver = root / "drivers" / "ice"
        interface.mkdir(parents=True)
        pci_device.mkdir(parents=True)
        driver.mkdir(parents=True)
        (interface / "device").symlink_to(pci_device, target_is_directory=True)
        (pci_device / "driver").symlink_to(driver, target_is_directory=True)
        for name, value in {
            "operstate": "up", "carrier": "1", "address": "00:11:22:33:44:55",
            "mtu": "9000", "speed": "25000", "duplex": "full",
        }.items():
            (interface / name).write_text(value)

        def fake_run(command, **_kwargs):
            payload = [{
                "ifname": "eno1",
                "addr_info": [
                    {"family": "inet", "local": "192.168.8.20", "prefixlen": 24},
                    {"family": "inet6", "local": "fe80::1", "prefixlen": 64},
                ],
            }]
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        adapters = list_network_adapters(sysfs, run=fake_run)
        assert len(adapters) == 1
        adapter = adapters[0]
        assert adapter.name == "eno1"
        assert adapter.driver == "ice"
        assert adapter.bus_address == "0000:01:00.0"
        assert adapter.speed_mbps == 25000
        assert adapter.primary_ipv4 == "192.168.8.20/24"
