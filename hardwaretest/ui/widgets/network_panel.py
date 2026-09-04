"""User interface for non-destructive network tests and adapter diagnostics."""

from __future__ import annotations

import shutil
from typing import Optional

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hardwaretest.core.network import NetworkAdapter, list_network_adapters
from hardwaretest.core.test_runner import TestParameters, TestResult
from hardwaretest.tests.network import NetworkRunner, threshold_failures
from hardwaretest.ui.i18n import language_manager


class NetworkPanel(QWidget):
    """Run bounded ping/iperf3 tests, optionally bound to one adapter."""

    log_signal = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.runner: Optional[NetworkRunner] = None
        self._adapters: dict[str, NetworkAdapter] = {}
        self._last_result: Optional[TestResult] = None
        self._last_summary: Optional[dict[str, float]] = None
        self._last_failure: Optional[tuple[str, str]] = None
        self._last_completion_warning = ""
        self._last_aborted = False

        self.adapter_box = QComboBox()
        self.adapter_box._hardwaretest_skip_tree = True
        self.adapter_scan_btn = QPushButton("Netzwerkadapter aktualisieren")
        self.adapter_info = QTextEdit()
        self.adapter_info.setReadOnly(True)
        self.adapter_info.setMaximumHeight(135)
        self.adapter_info.setStyleSheet("font-family: monospace; font-size: 11px;")

        adapter_header = QHBoxLayout()
        adapter_header.addWidget(self.adapter_box, 1)
        adapter_header.addWidget(self.adapter_scan_btn)
        adapter_layout = QVBoxLayout()
        adapter_layout.addLayout(adapter_header)
        adapter_layout.addWidget(self.adapter_info)
        adapter_group = QGroupBox("Netzwerkadapter")
        adapter_group.setLayout(adapter_layout)

        self.target = QLineEdit()
        self.target.setPlaceholderText("z. B. 192.168.1.10 oder iperf.example.net")
        self.target.setClearButtonEnabled(True)

        self.ping_count = QSpinBox()
        self.ping_count.setRange(1, 10_000)
        self.ping_count.setValue(20)

        self.iperf_duration = QSpinBox()
        self.iperf_duration.setRange(5, 86_400)
        self.iperf_duration.setValue(30)
        self.iperf_duration.setSuffix(" s")

        self.max_loss = self._threshold_spin(0.0, 100.0, 0.0, " %")
        self.max_latency = self._threshold_spin(0.0, 60_000.0, 100.0, " ms")
        self.max_jitter = self._threshold_spin(0.0, 60_000.0, 20.0, " ms")
        self.min_throughput = self._threshold_spin(0.0, 1_000_000.0, 0.0, " Mbit/s")

        threshold_form = QFormLayout()
        threshold_form.addRow("Maximaler Paketverlust", self.max_loss)
        threshold_form.addRow("Maximale mittlere Latenz", self.max_latency)
        threshold_form.addRow("Maximaler Jitter", self.max_jitter)
        threshold_form.addRow("Minimaler Durchsatz (0 = aus)", self.min_throughput)
        threshold_group = QGroupBox("Pass/Fail-Grenzwerte")
        threshold_group.setLayout(threshold_form)

        self.hint = QLabel(
            "Ping misst Erreichbarkeit, Latenz, Jitter und Paketverlust. "
            "Für den Durchsatztest muss auf dem Ziel ein iperf3-Server laufen."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #bbbbbb; font-style: italic;")

        self.start_ping = QPushButton("Ping-Test starten")
        self.start_iperf = QPushButton("iperf3-Durchsatztest starten")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)

        self.result = QLabel("")
        self.result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel("Bereit")
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        test_form = QFormLayout()
        test_form.addRow("Zielhost oder IP-Adresse", self.target)
        test_form.addRow("Ping-Pakete", self.ping_count)
        test_form.addRow("iperf3-Dauer", self.iperf_duration)

        buttons = QHBoxLayout()
        buttons.addWidget(self.start_ping)
        buttons.addWidget(self.start_iperf)
        buttons.addWidget(self.stop_btn)

        options = QHBoxLayout()
        options.addLayout(test_form, 1)
        options.addWidget(threshold_group, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(adapter_group)
        layout.addLayout(options)
        layout.addLayout(buttons)
        layout.addWidget(self.result)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.log_view, 1)

        self.adapter_scan_btn.clicked.connect(self._scan_adapters)
        self.adapter_box.currentIndexChanged.connect(self._show_selected_adapter)
        self.start_ping.clicked.connect(lambda: self._start("ping"))
        self.start_iperf.clicked.connect(lambda: self._start("iperf3"))
        self.stop_btn.clicked.connect(self._stop)
        self.log_signal.connect(self.log_view.append)
        language_manager.language_changed.connect(self._retranslate_runtime)

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)
        self._scan_adapters()

    @staticmethod
    def _threshold_spin(minimum: float, maximum: float, value: float, suffix: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(1)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    def _set_runtime_text(self, widget: QLabel, source: str, **values: object) -> None:
        widget._hardwaretest_runtime_text = (source, values)
        widget.setText(language_manager.tr(source, **values))

    def _scan_adapters(self) -> None:
        selected = str(self.adapter_box.currentData() or "")
        self._adapters = {adapter.name: adapter for adapter in list_network_adapters()}
        blocker = QSignalBlocker(self.adapter_box)
        self.adapter_box.clear()
        self.adapter_box.addItem(language_manager.tr("Automatisch (Routing)"), userData="")
        for adapter in self._adapters.values():
            self.adapter_box.addItem(self._adapter_label(adapter), userData=adapter.name)
        index = self.adapter_box.findData(selected)
        self.adapter_box.setCurrentIndex(max(0, index))
        del blocker
        self._show_selected_adapter()

    def _adapter_label(self, adapter: NetworkAdapter) -> str:
        state = self._state_text(adapter.operstate)
        speed = (
            language_manager.tr("{speed} Mbit/s", speed=adapter.speed_mbps)
            if adapter.speed_mbps else language_manager.tr("Geschwindigkeit unbekannt")
        )
        return f"{adapter.name} – {state} – {speed} – {adapter.driver}"

    def _show_selected_adapter(self) -> None:
        selected = str(self.adapter_box.currentData() or "")
        adapters = [self._adapters[selected]] if selected in self._adapters else list(self._adapters.values())
        if not adapters:
            self.adapter_info.setPlainText(language_manager.tr("Keine Netzwerkadapter gefunden."))
            return
        self.adapter_info.setPlainText("\n\n".join(self._format_adapter(item) for item in adapters))

    @staticmethod
    def _format_adapter(adapter: NetworkAdapter) -> str:
        carrier = "ja" if adapter.carrier is True else "nein" if adapter.carrier is False else "unbekannt"
        speed = str(adapter.speed_mbps) if adapter.speed_mbps else "–"
        return "\n".join([
            language_manager.tr("Adapter: {name}", name=adapter.name),
            language_manager.tr(
                "Status: {state} | Verbindung: {carrier}",
                state=NetworkPanel._state_text(adapter.operstate),
                carrier=language_manager.tr(carrier),
            ),
            language_manager.tr(
                "Treiber: {driver} | Bus: {bus}", driver=adapter.driver, bus=adapter.bus_address
            ),
            language_manager.tr(
                "Geschwindigkeit: {speed} Mbit/s | Duplex: {duplex} | MTU: {mtu}",
                speed=speed,
                duplex=NetworkPanel._duplex_text(adapter.duplex),
                mtu=adapter.mtu or "–",
            ),
            language_manager.tr("MAC: {value}", value=adapter.mac_address),
            language_manager.tr("IPv4: {value}", value=", ".join(adapter.ipv4_addresses) or "–"),
            language_manager.tr("IPv6: {value}", value=", ".join(adapter.ipv6_addresses) or "–"),
        ])

    @staticmethod
    def _state_text(state: str) -> str:
        source = {
            "up": "verbunden",
            "down": "getrennt",
            "dormant": "wartend",
            "lowerlayerdown": "untergeordnete Verbindung inaktiv",
            "unknown": "unbekannt",
        }.get(state.casefold(), state)
        return language_manager.tr(source)

    @staticmethod
    def _duplex_text(duplex: str) -> str:
        source = {"full": "Vollduplex", "half": "Halbduplex", "unknown": "unbekannt"}.get(
            duplex.casefold(), duplex
        )
        return language_manager.tr(source)

    def _start(self, mode: str) -> None:
        if self.runner and self.runner.is_running():
            return
        target = self.target.text().strip()
        if not target:
            QMessageBox.warning(
                self,
                language_manager.tr("Zielhost fehlt"),
                language_manager.tr("Bitte einen Zielhost oder eine IP-Adresse angeben."),
            )
            return
        binary = "ping" if mode == "ping" else "iperf3"
        if shutil.which(binary) is None:
            package = "iputils-ping" if mode == "ping" else "iperf3"
            QMessageBox.warning(
                self,
                language_manager.tr("Werkzeug nicht installiert"),
                language_manager.tr(
                    "{binary} ist nicht installiert. Bitte das Paket {package} installieren.",
                    binary=binary,
                    package=package,
                ),
            )
            return

        adapter = self._adapters.get(str(self.adapter_box.currentData() or ""))
        if mode == "iperf3" and adapter is not None and not adapter.primary_ipv4:
            QMessageBox.warning(
                self,
                language_manager.tr("Keine IPv4-Adresse"),
                language_manager.tr(
                    "Der ausgewählte Adapter besitzt keine IPv4-Adresse für den iperf3-Test."
                ),
            )
            return

        if mode == "iperf3":
            answer = QMessageBox.question(
                self,
                language_manager.tr("iperf3-Server auf dem Ziel erforderlich"),
                language_manager.tr(
                    "Auf dem Gegensystem muss vor dem Test ein iperf3-Server laufen:\n\n"
                    "  iperf3 -s\n\n"
                    "macOS (falls iperf3 fehlt): brew install iperf3\n"
                    "Ubuntu (falls iperf3 fehlt): sudo apt install iperf3\n\n"
                    "Der Standardport ist TCP 5201 und muss von der Firewall zugelassen werden. "
                    "Durchsatztest jetzt starten?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        duration = max(5, self.iperf_duration.value()) if mode == "iperf3" else self.ping_count.value() * 3
        self.runner = NetworkRunner(
            TestParameters(duration_seconds=duration + 5),
            target=target,
            mode=mode,
            ping_count=self.ping_count.value(),
            iperf_seconds=self.iperf_duration.value(),
            interface=adapter.name if adapter else "",
            source_address=adapter.primary_ipv4 if adapter else "",
            log_fn=self.log_signal.emit,
        )
        try:
            self.runner.start()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, language_manager.tr("Netzwerktest konnte nicht starten"), str(exc)
            )
            self.runner = None
            return

        self._last_result = None
        self._last_summary = None
        self._last_failure = None
        self._last_completion_warning = ""
        self._last_aborted = False
        self.result.setText("")
        self.result._hardwaretest_runtime_text = None
        self.result.setStyleSheet("")
        self.log_view.clear()
        self.progress.setValue(0)
        self._set_runtime_text(self.status, "Netzwerktest läuft …")
        self._set_running(True)
        self._timer.start()

    def _stop(self) -> None:
        if self.runner:
            self._last_aborted = True
            self.runner.stop(aborted=True)
        self._finish()

    def _poll(self) -> None:
        if not self.runner:
            return
        self.progress.setValue(int(self.runner.progress() * 100))
        if not self.runner.is_running():
            self._finish()

    def _finish(self) -> None:
        self._timer.stop()
        if not self.runner:
            return
        self._last_result = self.runner.get_result()
        self._last_summary = self.runner.summary()
        self._last_failure = self.runner.failure()
        self._last_completion_warning = self.runner.completion_warning
        self._render_result()
        self._set_runtime_text(self.status, "Fertig")
        self.progress.setValue(100)
        self._set_running(False)
        self.runner = None

    def _render_result(self) -> None:
        result = self._last_result
        summary = self._last_summary
        if self._last_aborted:
            self._set_runtime_text(self.result, "? ABGEBROCHEN – Netzwerktest manuell beendet")
            self.result.setStyleSheet(self._result_style("#ffaa00"))
            return
        if result is None:
            return
        if not result.passed:
            if self._last_failure is not None:
                code, _raw_message = self._last_failure
                source = {
                    "connection_refused": (
                        "✗ iperf3-Server nicht erreichbar – Verbindung abgelehnt. "
                        "Auf dem Ziel muss „iperf3 -s“ laufen; Port 5201 und Firewall prüfen."
                    ),
                    "dns_failed": "✗ Zielname konnte nicht aufgelöst werden. Hostname oder DNS prüfen.",
                    "no_route": "✗ Keine Route zum Ziel. IP-Adresse, Adapterauswahl und Routing prüfen.",
                    "network_unreachable": "✗ Netzwerk nicht erreichbar. Linkstatus und IP-Konfiguration prüfen.",
                    "timeout": "✗ Zeitüberschreitung. Zielsystem, Firewall und Netzwerkverbindung prüfen.",
                    "permission_denied": "✗ Netzwerkzugriff nicht erlaubt. Berechtigungen des Werkzeugs prüfen.",
                    "connection_failed": (
                        "✗ Verbindung zum iperf3-Server fehlgeschlagen. Auf dem Ziel „iperf3 -s“ "
                        "sowie Port 5201 und Firewall prüfen."
                    ),
                }.get(code)
                if source:
                    self._set_runtime_text(self.result, source)
                    self.result.setStyleSheet(self._result_style("#ff4444"))
                    return
            details = "; ".join(result.errors[:2]) or language_manager.tr("Unbekannter Fehler")
            self._set_runtime_text(self.result, "✗ FEHLER – {details}", details=details)
            self.result.setStyleSheet(self._result_style("#ff4444"))
            return

        failures = threshold_failures(
            summary,
            max_packet_loss_percent=self.max_loss.value(),
            max_latency_avg_ms=self.max_latency.value(),
            max_jitter_ms=self.max_jitter.value(),
            min_throughput_mbit_s=self.min_throughput.value(),
        )
        if failures:
            details = "; ".join(self._format_threshold_failure(*failure) for failure in failures)
            self._set_runtime_text(self.result, "✗ GRENZWERT ÜBERSCHRITTEN – {details}", details=details)
            self.result.setStyleSheet(self._result_style("#ff4444"))
            return

        if summary and "throughput_mbit_s" in summary:
            source = (
                "✓ BESTANDEN – Durchsatz: {throughput:.1f} Mbit/s | "
                "Retransmits: {retransmits:.0f} | Messung vollständig; "
                "iperf3 meldete beim Beenden eine interne Warnung"
                if self._last_completion_warning == "iperf3_cleanup_error"
                else "✓ BESTANDEN – Durchsatz: {throughput:.1f} Mbit/s | "
                "Retransmits: {retransmits:.0f}"
            )
            self._set_runtime_text(
                self.result,
                source,
                throughput=summary["throughput_mbit_s"],
                retransmits=summary.get("retransmits", 0),
            )
        elif summary and "latency_avg_ms" in summary:
            self._set_runtime_text(
                self.result,
                "✓ BESTANDEN – Paketverlust: {loss:.1f}% | Latenz Ø: {average:.2f} ms "
                "(Min {minimum:.2f} / Max {maximum:.2f}) | Jitter: {jitter:.2f} ms",
                loss=summary.get("packet_loss_percent", 0),
                average=summary["latency_avg_ms"],
                minimum=summary.get("latency_min_ms", 0),
                maximum=summary.get("latency_max_ms", 0),
                jitter=summary.get("jitter_ms", 0),
            )
        else:
            self._set_runtime_text(self.result, "✓ BESTANDEN – Netzwerktest ohne Fehler abgeschlossen")
        self.result.setStyleSheet(self._result_style("#44ff44"))

    @staticmethod
    def _format_threshold_failure(code: str, actual: float, limit: float) -> str:
        sources = {
            "packet_loss": "Paketverlust {actual:.1f}% > {limit:.1f}%",
            "latency": "Mittlere Latenz {actual:.2f} ms > {limit:.2f} ms",
            "jitter": "Jitter {actual:.2f} ms > {limit:.2f} ms",
            "throughput": "Durchsatz {actual:.1f} Mbit/s < {limit:.1f} Mbit/s",
            "missing_summary": "Keine auswertbare Ergebniszusammenfassung",
        }
        return language_manager.tr(sources[code], actual=actual, limit=limit)

    @staticmethod
    def _result_style(color: str) -> str:
        return f"color: {color}; font-size: 14px; font-weight: bold; padding: 4px;"

    def _set_running(self, running: bool) -> None:
        self.start_ping.setEnabled(not running)
        self.start_iperf.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.adapter_box.setEnabled(not running)
        self.adapter_scan_btn.setEnabled(not running)

    def _retranslate_runtime(self, _language: str | None = None) -> None:
        selected = str(self.adapter_box.currentData() or "")
        blocker = QSignalBlocker(self.adapter_box)
        self.adapter_box.setItemText(0, language_manager.tr("Automatisch (Routing)"))
        for index in range(1, self.adapter_box.count()):
            adapter = self._adapters.get(str(self.adapter_box.itemData(index) or ""))
            if adapter:
                self.adapter_box.setItemText(index, self._adapter_label(adapter))
        selected_index = self.adapter_box.findData(selected)
        self.adapter_box.setCurrentIndex(max(0, selected_index))
        del blocker
        self._show_selected_adapter()
        state = getattr(self.status, "_hardwaretest_runtime_text", None)
        if state:
            source, values = state
            self.status.setText(language_manager.tr(source, **values))
        if self._last_result is not None or self._last_aborted:
            self._render_result()
