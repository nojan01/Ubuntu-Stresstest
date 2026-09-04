"""Adapters for non-destructive network reachability and throughput tests."""

from __future__ import annotations

import json
import re
from typing import List, Optional

from hardwaretest.core.test_runner import BaseTestRunner, TestParameters


class NetworkRunner(BaseTestRunner):
    """Run either a bounded ping test or an iperf3 client test.

    The target is deliberately supplied by the operator.  The application
    never starts an iperf server and never sends traffic to a hard-coded
    Internet host.
    """

    def __init__(
        self,
        params: TestParameters,
        target: str,
        mode: str,
        ping_count: int = 10,
        iperf_seconds: Optional[int] = None,
        interface: str = "",
        source_address: str = "",
        **kwargs,
    ) -> None:
        super().__init__(params, **kwargs)
        if mode not in {"ping", "iperf3"}:
            raise ValueError(f"Unsupported network test mode: {mode}")
        self.target = target.strip()
        self.mode = mode
        self.ping_count = max(1, ping_count)
        self.iperf_seconds = max(1, iperf_seconds or params.duration_seconds)
        self.interface = interface.strip()
        self.source_address = source_address.split("/", maxsplit=1)[0].strip()
        self.output_lines: List[str] = []
        self.completion_warning = ""

    def build_command(self) -> List[str]:
        if not self.target:
            raise ValueError("Ein Zielhost muss angegeben werden.")
        if self.target.startswith("-"):
            raise ValueError("Der Zielhost darf nicht mit einem Bindestrich beginnen.")
        if self.mode == "ping":
            command = ["env", "LC_ALL=C", "ping", "-c", str(self.ping_count), "-W", "2"]
            if self.interface:
                command.extend(["-I", self.interface])
            command.extend(["--", self.target])
            return command
        command = ["iperf3", "-c", self.target, "-t", str(self.iperf_seconds), "-J"]
        if self.source_address:
            command.extend(["-B", self.source_address])
        return command

    def _stream_output(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            stripped = line.rstrip()
            self.output_lines.append(stripped)
            self._log(stripped)
            self._check_line_for_errors(stripped)
        self._process.wait()
        self._finalize_result()
        if self._result:
            self._log(f"Test beendet – Ergebnis: {self._result.status_text}")

    def summary(self) -> Optional[dict[str, float]]:
        """Return parsed, locale-independent result values where possible."""
        output = "\n".join(self.output_lines)
        if self.mode == "ping":
            loss = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", output)
            rtt = re.search(
                r"(?:rtt|round-trip).*?=\s*([\d.]+)/([\d.]+)/([\d.]+)(?:/([\d.]+))?\s*ms",
                output,
            )
            result: dict[str, float] = {}
            if loss:
                result["packet_loss_percent"] = float(loss.group(1))
            if rtt:
                result.update({
                    "latency_min_ms": float(rtt.group(1)),
                    "latency_avg_ms": float(rtt.group(2)),
                    "latency_max_ms": float(rtt.group(3)),
                })
                if rtt.group(4):
                    result["jitter_ms"] = float(rtt.group(4))
            return result or None

        try:
            payload = json.loads(output)
            end = payload.get("end", {})
            received = end.get("sum_received") or end.get("sum") or {}
            sent = end.get("sum_sent") or {}
            if "bits_per_second" in received:
                return {
                    "throughput_mbit_s": float(received["bits_per_second"]) / 1_000_000,
                    "retransmits": float(
                        sent.get("retransmits", received.get("retransmits", 0))
                    ),
                    "measurement_seconds": float(
                        received.get("seconds", sent.get("seconds", self.iperf_seconds))
                    ),
                }

            # iperf3 3.16 can occasionally terminate during final JSON cleanup
            # after all data intervals were received.  In that case ``end``
            # has no sum, but the completed interval records still contain a
            # valid measurement.  Aggregate bytes instead of averaging the
            # per-interval bitrates so differently-sized final intervals are
            # weighted correctly.
            interval_sums = [
                interval.get("sum", {})
                for interval in payload.get("intervals", [])
                if isinstance(interval, dict)
            ]
            interval_sums = [
                item for item in interval_sums
                if isinstance(item, dict) and not item.get("omitted", False)
            ]
            total_bytes = sum(float(item.get("bytes", 0)) for item in interval_sums)
            total_seconds = sum(float(item.get("seconds", 0)) for item in interval_sums)
            if total_bytes <= 0 or total_seconds <= 0:
                return None
            return {
                "throughput_mbit_s": total_bytes * 8 / total_seconds / 1_000_000,
                "retransmits": sum(
                    float(item.get("retransmits", 0)) for item in interval_sums
                ),
                "measurement_seconds": total_seconds,
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _finalize_result(self) -> None:
        """Accept a near-complete iperf3 measurement despite its cleanup bug."""
        super()._finalize_result()
        self.completion_warning = ""
        if self.mode != "iperf3" or not self._result or self._result.passed or self._aborted:
            return

        output = "\n".join(self.output_lines)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return
        error = str(payload.get("error", "")).strip().casefold()
        if error != "interrupt - the client has terminated":
            return
        summary = self.summary()
        if not summary:
            return
        measured = summary.get("measurement_seconds", 0.0)
        if measured < max(1.0, self.iperf_seconds * 0.9):
            return

        # The requested transfer has effectively completed.  Preserve the
        # warning for the UI/log but do not report the network itself as bad.
        self.completion_warning = "iperf3_cleanup_error"
        self._result.passed = True
        self._result.errors = []

    def failure(self) -> Optional[tuple[str, str]]:
        """Classify expected connection failures and retain the raw message."""
        output = "\n".join(self.output_lines).strip()
        if not output:
            return None
        message = output
        if self.mode == "iperf3":
            try:
                payload = json.loads(output)
                if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                    message = payload["error"].strip()
            except json.JSONDecodeError:
                pass

        normalized = message.casefold()
        patterns = (
            ("connection_refused", ("connection refused", "verbindung abgelehnt")),
            ("dns_failed", (
                "name or service not known", "temporary failure in name resolution",
                "unknown host", "nodename nor servname provided",
            )),
            ("no_route", ("no route to host",)),
            ("network_unreachable", ("network is unreachable",)),
            ("timeout", ("timed out", "timeout")),
            ("permission_denied", ("permission denied", "operation not permitted")),
            ("connection_failed", ("unable to connect to server", "cannot connect to server")),
        )
        for code, needles in patterns:
            if any(needle in normalized for needle in needles):
                return code, message
        return "unknown", message

    def compact_report_lines(self, language: str = "de") -> list[str]:
        """Return bounded aggregate data suitable for long-term reports."""
        summary = self.summary() or {}
        if self.mode == "ping":
            output = "\n".join(self.output_lines)
            packets = re.search(
                r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",
                output,
            )
            sent = int(packets.group(1)) if packets else 0
            received = int(packets.group(2)) if packets else 0
            loss = summary.get("packet_loss_percent", 0.0)
            if language == "en":
                return [
                    f"Packets: sent {sent} | received {received} | loss {loss:.1f} %",
                    "Latency: "
                    f"min {summary.get('latency_min_ms', 0):.3f} ms | "
                    f"avg {summary.get('latency_avg_ms', 0):.3f} ms | "
                    f"max {summary.get('latency_max_ms', 0):.3f} ms | "
                    f"jitter {summary.get('jitter_ms', 0):.3f} ms",
                ]
            return [
                f"Pakete: gesendet {sent} | empfangen {received} | Verlust {loss:.1f} %",
                "Latenz: "
                f"Min {summary.get('latency_min_ms', 0):.3f} ms | "
                f"Ø {summary.get('latency_avg_ms', 0):.3f} ms | "
                f"Max {summary.get('latency_max_ms', 0):.3f} ms | "
                f"Jitter {summary.get('jitter_ms', 0):.3f} ms",
            ]

        try:
            payload = json.loads("\n".join(self.output_lines))
        except json.JSONDecodeError:
            payload = {}
        end = payload.get("end", {}) if isinstance(payload, dict) else {}
        received = end.get("sum_received", {}) if isinstance(end, dict) else {}
        sent = end.get("sum_sent", {}) if isinstance(end, dict) else {}
        cpu = end.get("cpu_utilization_percent", {}) if isinstance(end, dict) else {}
        received = received if isinstance(received, dict) else {}
        sent = sent if isinstance(sent, dict) else {}
        cpu = cpu if isinstance(cpu, dict) else {}
        duration = float(
            received.get("seconds", sent.get("seconds", summary.get("measurement_seconds", 0)))
            or 0
        )
        received_bytes = float(received.get("bytes", 0) or 0)
        sent_bytes = float(sent.get("bytes", 0) or 0)
        received_rate = float(
            received.get("bits_per_second", summary.get("throughput_mbit_s", 0) * 1_000_000)
            or 0
        ) / 1_000_000
        sent_rate = float(sent.get("bits_per_second", 0) or 0) / 1_000_000
        retransmits = float(sent.get("retransmits", summary.get("retransmits", 0)) or 0)
        if language == "en":
            lines = [
                f"Measurement duration: {duration:.1f} s",
                f"Received: {_format_data_size(received_bytes)} | {received_rate:.1f} Mbit/s",
                f"Sent: {_format_data_size(sent_bytes)} | {sent_rate:.1f} Mbit/s | "
                f"retransmits {retransmits:.0f}",
            ]
            if cpu:
                lines.append(
                    f"CPU utilization: local {float(cpu.get('host_total', 0) or 0):.1f} % | "
                    f"remote {float(cpu.get('remote_total', 0) or 0):.1f} %"
                )
            congestion = end.get("sender_tcp_congestion") if isinstance(end, dict) else None
            if congestion:
                lines.append(f"TCP congestion control: {congestion}")
            return lines

        lines = [
            f"Messdauer: {duration:.1f} s",
            f"Empfangen: {_format_data_size(received_bytes)} | {received_rate:.1f} Mbit/s",
            f"Gesendet: {_format_data_size(sent_bytes)} | {sent_rate:.1f} Mbit/s | "
            f"Retransmits {retransmits:.0f}",
        ]
        if cpu:
            lines.append(
                f"CPU-Auslastung: lokal {float(cpu.get('host_total', 0) or 0):.1f} % | "
                f"Gegenseite {float(cpu.get('remote_total', 0) or 0):.1f} %"
            )
        congestion = end.get("sender_tcp_congestion") if isinstance(end, dict) else None
        if congestion:
            lines.append(f"TCP-Staukontrolle: {congestion}")
        return lines


def threshold_failures(
    summary: Optional[dict[str, float]],
    *,
    max_packet_loss_percent: float,
    max_latency_avg_ms: float,
    max_jitter_ms: float,
    min_throughput_mbit_s: float,
) -> list[tuple[str, float, float]]:
    """Return stable failure codes plus actual and configured values."""
    if not summary:
        return [("missing_summary", 0.0, 0.0)]
    failures: list[tuple[str, float, float]] = []
    checks = (
        ("packet_loss", "packet_loss_percent", max_packet_loss_percent, lambda a, b: a > b),
        ("latency", "latency_avg_ms", max_latency_avg_ms, lambda a, b: a > b),
        ("jitter", "jitter_ms", max_jitter_ms, lambda a, b: a > b),
        ("throughput", "throughput_mbit_s", min_throughput_mbit_s, lambda a, b: b > 0 and a < b),
    )
    for code, key, limit, failed in checks:
        if key in summary and failed(summary[key], limit):
            failures.append((code, summary[key], limit))
    return failures


def _format_data_size(value: float) -> str:
    if value >= 1024 ** 3:
        return f"{value / 1024 ** 3:.2f} GiB"
    if value >= 1024 ** 2:
        return f"{value / 1024 ** 2:.1f} MiB"
    if value >= 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value:.0f} B"
