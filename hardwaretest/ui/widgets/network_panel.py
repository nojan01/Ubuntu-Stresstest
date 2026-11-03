"""User interface for non-destructive network tests and adapter diagnostics."""

from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Optional

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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

from hardwaretest.core import network, privilege
from hardwaretest.core.network import NetworkAdapter, list_network_adapters
from hardwaretest.core.root_broker import RootBroker
from hardwaretest.core.test_runner import TestParameters, TestResult
from hardwaretest.tests.network import NetworkRunner, threshold_failures
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.worker_lifecycle import connect_finished, release_worker


class _EthtoolWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, action: Callable[[], object], parent: QWidget) -> None:
        super().__init__(parent)
        self._action = action

    def run(self) -> None:
        try:
            self.completed.emit(self._action())
        except Exception as exc:  # pragma: no cover - abhängig von Treiber und Rechteerhöhung
            self.failed.emit(str(exc))


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
        self._stats_before: dict[str, int] = {}
        self._stats_interface = ""
        self._counter_deltas: dict[str, int] = {}
        self._ethtool_worker: Optional[_EthtoolWorker] = None
        self._root_session: Optional[RootBroker] = None

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
        self.selftest_btn = QPushButton("ethtool-Selbsttest")
        self.selftest_offline = QCheckBox("Offline-Test (Link wird kurz unterbrochen)")
        self.module_btn = QPushButton("SFP-Modul auslesen (ethtool -m)")
        self.mstlink_btn = QPushButton("Mellanox Link-Diagnose (mstlink)")
        self.mstlink_btn.setVisible(False)
        self.ethtool_result = QLabel("")
        self.ethtool_result.setWordWrap(True)
        self.ethtool_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ethtool_result.setVisible(False)
        ethtool_row = QHBoxLayout()
        ethtool_row.addWidget(self.selftest_btn)
        ethtool_row.addWidget(self.selftest_offline)
        ethtool_row.addWidget(self.module_btn)
        ethtool_row.addWidget(self.mstlink_btn)
        ethtool_row.addStretch(1)
        adapter_layout = QVBoxLayout()
        adapter_layout.addLayout(adapter_header)
        adapter_layout.addWidget(self.adapter_info)
        adapter_layout.addLayout(ethtool_row)
        adapter_layout.addWidget(self.ethtool_result)
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
        self.log_view.document().setMaximumBlockCount(5000)

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
        self.selftest_btn.clicked.connect(self._start_selftest)
        self.module_btn.clicked.connect(self._read_module)
        self.mstlink_btn.clicked.connect(self._run_mstlink)
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
        self._update_ethtool_buttons()
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

        self._stats_interface = adapter.name if adapter else ""
        self._stats_before = network.read_ethtool_stats(self._stats_interface)
        self._counter_deltas = {}
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
        if self._stats_before:
            self._counter_deltas = network.error_counter_deltas(
                self._stats_before, network.read_ethtool_stats(self._stats_interface))
            if self._counter_deltas:
                self.log_signal.emit(language_manager.tr(
                    "Fehlerzähler ({interface}) während des Tests gewachsen: {counters}",
                    interface=self._stats_interface, counters=self._counters_text()))
            else:
                self.log_signal.emit(language_manager.tr(
                    "Fehlerzähler ({interface}) unverändert (ethtool -S).", interface=self._stats_interface))
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
        self._update_ethtool_buttons()

    # ------------------------------------------------------------------ ethtool
    def _counters_text(self) -> str:
        return ", ".join(f"{name} +{delta}" for name, delta in self._counter_deltas.items())

    def _append_counter_note(self) -> None:
        if not self._counter_deltas or self._last_aborted:
            return
        note = language_manager.tr("Fehlerzähler gewachsen: {counters}", counters=self._counters_text())
        self.result.setText(self.result.text() + "\n⚠ " + note)
        if self._last_result is not None and self._last_result.passed:
            self.result.setStyleSheet(self._result_style("#ffaa00"))

    def _selected_adapter(self) -> Optional[NetworkAdapter]:
        return self._adapters.get(str(self.adapter_box.currentData() or ""))

    def _update_ethtool_buttons(self) -> None:
        adapter = self._selected_adapter()
        idle = self._ethtool_worker is None and not (self.runner and self.runner.is_running())
        physical = adapter is not None and adapter.bus_address != "virtual"
        available = shutil.which("ethtool") is not None and (privilege.is_root() or privilege.tool() is not None)
        for widget in (self.selftest_btn, self.selftest_offline, self.module_btn):
            widget.setEnabled(idle and physical and available)
        tip = ""
        if shutil.which("ethtool") is None:
            tip = language_manager.tr("ethtool ist nicht installiert.")
        elif not physical:
            tip = language_manager.tr("Bitte einen physischen Adapter auswählen.")
        for widget in (self.selftest_btn, self.module_btn):
            widget.setToolTip(tip)
        # Nur bei Mellanox/NVIDIA-Adaptern und installiertem mstflint sichtbar – sonst kein Hinweis.
        mellanox = adapter is not None and network.is_mellanox(adapter) and network.mstlink_available()
        self.mstlink_btn.setVisible(mellanox)
        self.mstlink_btn.setEnabled(mellanox and idle and (privilege.is_root() or privilege.tool() is not None))

    def _ethtool_run(self, command, **kwargs):
        if privilege.is_root() or privilege.tool() is None:
            return subprocess.run(command, **kwargs)
        if self._root_session is None:
            self._root_session = RootBroker(reason=language_manager.tr("Netzwerkadapter prüfen (ethtool)"))
        return self._root_session.run(command, **kwargs)

    def _run_ethtool(self, action: Callable[[], object], on_success: Callable[[object], None]) -> None:
        if self._ethtool_worker is not None:
            return
        worker = _EthtoolWorker(action, self)
        self._ethtool_worker = worker
        worker.completed.connect(on_success)
        worker.failed.connect(self._ethtool_failed)
        connect_finished(worker, self._ethtool_finished)
        self._update_ethtool_buttons()
        worker.start()

    def _ethtool_finished(self, worker=None) -> None:
        if release_worker(self, "_ethtool_worker", worker):
            self._set_runtime_text(self.status, "Bereit")
            self._update_ethtool_buttons()

    def _ethtool_failed(self, message: str) -> None:
        self._show_ethtool("✗ ethtool fehlgeschlagen: {details}", "#ff5555", details=message)

    def _show_ethtool(self, source: str, color: str, **values: object) -> None:
        self.ethtool_result.setVisible(True)
        self.ethtool_result.setStyleSheet(f"color: {color}; font-weight: bold; padding: 2px;")
        self._set_runtime_text(self.ethtool_result, source, **values)

    def _start_selftest(self) -> None:
        adapter = self._selected_adapter()
        if adapter is None:
            return
        offline = self.selftest_offline.isChecked()
        if offline:
            answer = QMessageBox.question(
                self,
                language_manager.tr("Offline-Selbsttest"),
                language_manager.tr(
                    "Der Offline-Selbsttest unterbricht die Verbindung von {interface} für einige Sekunden "
                    "(Register-, EEPROM-, Interrupt- und Loopback-Test). Laufende Verbindungen über diesen "
                    "Adapter brechen ab. Fortfahren?", interface=adapter.name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._set_runtime_text(self.status, "ethtool-Selbsttest läuft auf {interface} …", interface=adapter.name)
        name = adapter.name
        self._run_ethtool(
            lambda: (name, network.run_ethtool_selftest(name, offline, run=self._ethtool_run)),
            self._show_selftest,
        )

    def _show_selftest(self, value: object) -> None:
        name, result = value
        if result.passed is None:
            self._show_ethtool("? Selbsttest auf {interface} ohne Ergebnis – {details}", "#ffaa00", interface=name,
                               details=result.error or language_manager.tr("Treiber unterstützt keinen Selbsttest"))
            return
        details = "; ".join(f"{test}: {code}" for test, code in result.entries)
        if result.passed:
            self._show_ethtool("✓ Selbsttest auf {interface} bestanden ({details})", "#55ff55", interface=name,
                               details=details or language_manager.tr("keine Einzelergebnisse"))
        else:
            failed = "; ".join(f"{test}: {code}" for test, code in result.failed_entries) or details
            self._show_ethtool("✗ Selbsttest auf {interface} fehlgeschlagen – {details}", "#ff5555", interface=name,
                               details=failed)
        for line in (details.split("; ") if details else []):
            self.log_signal.emit(f"ethtool -t {name}: {line}")

    def _read_module(self) -> None:
        adapter = self._selected_adapter()
        if adapter is None:
            return
        name = adapter.name
        self._set_runtime_text(self.status, "SFP-Modul von {interface} wird gelesen …", interface=name)
        self._run_ethtool(lambda: (name, network.read_ethtool_module(name, run=self._ethtool_run)), self._show_module)

    def _show_module(self, value: object) -> None:
        name, (values, error) = value
        if error:
            self._show_ethtool("✗ SFP-Modul auf {interface} nicht lesbar: {details}", "#ff5555", interface=name, details=error)
            return
        if not values:
            self._show_ethtool("Kein steckbares Modul (SFP/QSFP) auf {interface} oder keine Moduldaten.", "#bbbbbb", interface=name)
            return
        alarms = values.pop("Alarms", "")
        text = " | ".join(f"{key}: {val}" for key, val in values.items())
        if alarms:
            self._show_ethtool("⚠ SFP {interface}: {details} | Alarme: {alarms}", "#ffaa00", interface=name, details=text, alarms=alarms)
        else:
            self._show_ethtool("SFP {interface}: {details}", "#bbbbbb", interface=name, details=text)

    def _run_mstlink(self) -> None:
        adapter = self._selected_adapter()
        if adapter is None or not network.is_mellanox(adapter):
            return
        name, bus = adapter.name, adapter.bus_address
        self._set_runtime_text(self.status, "mstlink-Diagnose läuft auf {interface} …", interface=name)
        self._run_ethtool(lambda: (name, network.run_mstlink(bus, run=self._ethtool_run)), self._show_mstlink)

    def _show_mstlink(self, value: object) -> None:
        name, result = value
        if result.error:
            self._show_ethtool("✗ mstlink auf {interface} fehlgeschlagen: {details}", "#ff5555",
                               interface=name, details=result.error)
            return
        for section, values in result.sections.items():
            for key, val in values.items():
                self.log_signal.emit(f"mstlink {name} [{section}] {key}: {val}")
        parts = [part for part in (result.physical_state or result.state, result.speed, result.fec) if part]
        if result.effective_errors is not None:
            parts.append(f"Errors {result.effective_errors}")
        if result.raw_ber:
            parts.append(f"BER {result.raw_ber}")
        if result.recommendation:
            parts.append(result.recommendation)
        module = result.module
        if module:
            parts.append("Modul: " + ", ".join(f"{label} {module[key]}" for key, label in network._MSTLINK_MODULE_KEYS
                                               if key in module))
        details = " | ".join(parts)
        if result.severity == "error":
            self._show_ethtool("✗ mstlink {interface}: kein Link – {details}", "#ff5555", interface=name, details=details)
        elif result.severity == "warn":
            self._show_ethtool("⚠ mstlink {interface}: {details} | Auffällig: {warnings}", "#ffaa00", interface=name,
                               details=details, warnings="; ".join(result.warnings))
        else:
            self._show_ethtool("✓ mstlink {interface}: {details}", "#55ff55", interface=name, details=details)

    def close_privileged_session(self) -> None:
        if self._root_session is not None:
            self._root_session.close()
            self._root_session = None

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
            self._append_counter_note()
        if self.ethtool_result.isVisible():
            state = getattr(self.ethtool_result, "_hardwaretest_runtime_text", None)
            if state:
                source, values = state
                self.ethtool_result.setText(language_manager.tr(source, **values))
        self._update_ethtool_buttons()
