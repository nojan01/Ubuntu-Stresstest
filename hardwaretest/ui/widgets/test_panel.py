"""Reusable panel to configure and run a stress test.

Erweitert um: Testmodus-Selektor, Swap-Deaktivierung, Temperatur-Monitor,
Pass/Fail-Bewertung und memtest86+-Hinweis.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
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

from hardwaretest.core.system_info import (
    SystemInfo,
    check_memtest86_installed,
    check_swapoff_safe,
    disable_swap,
    enable_swap,
    needs_password_for_swap,
    read_system_info,
)
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters
from hardwaretest.tests.stress_ng import STRESS_NG_MODES, StressNgRunner
from hardwaretest.ui.utils import launch_command_in_terminal, build_klog_command, build_mcelog_command
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.temperature_widget import TemperatureWidget


# ---------------------------------------------------------------------------
# Background worker so swapoff never blocks the Qt event loop
# ---------------------------------------------------------------------------

class _SwapWorker(QThread):
    """Runs ``swapoff -a`` in a background thread.

    Signals
    -------
    finished_ok : emitted when swap was disabled successfully.
    failed      : str – emitted with an error message on failure.
    """

    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, password: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._password = password

    def run(self) -> None:
        try:
            disable_swap(password=self._password)
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _SwapOnWorker(QThread):
    """Runs ``swapon -a`` in a background thread.

    Signals
    -------
    finished_ok : emitted when swap was enabled successfully.
    failed      : str – emitted with an error message on failure.
    """

    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, password: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._password = password

    def run(self) -> None:
        try:
            enable_swap(password=self._password)
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


SystemInfoProvider = Callable[[], SystemInfo]


class TestPanel(QWidget):
    log_signal = Signal(str)

    def __init__(
        self,
        title: str = "stress-ng",
        system_info_provider: SystemInfoProvider = read_system_info,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self.system_info_provider = system_info_provider
        self.runner: Optional[BaseTestRunner] = None
        self._system_info: SystemInfo = self.system_info_provider()

        # --- Testmodus-Auswahl ---
        self.mode_box = QComboBox()
        for key, info in STRESS_NG_MODES.items():
            self.mode_box.addItem(info["label"], userData=key)
            idx = self.mode_box.count() - 1
            self.mode_box.setItemData(idx, info["description"], role=Qt.ItemDataRole.ToolTipRole)
        # Standard: combined
        combined_idx = list(STRESS_NG_MODES.keys()).index("combined")
        self.mode_box.setCurrentIndex(combined_idx)

        self.duration_hours = QSpinBox()
        self.duration_hours.setRange(0, 240)
        self.duration_hours.setSuffix(" h")

        self.duration_minutes = QSpinBox()
        self.duration_minutes.setRange(0, 59)
        self.duration_minutes.setSuffix(" m")

        self.duration_seconds = QSpinBox()
        self.duration_seconds.setRange(0, 59)
        self.duration_seconds.setSuffix(" s")
        self._set_default_duration(seconds=120)

        self.memory_mb = QSpinBox()
        self.memory_mb.setRange(64, 1024 * 1024)

        self.info_label = QLabel("Systemdaten werden ermittelt...")
        self.refresh_btn = QPushButton("Systemwerte aktualisieren")
        self.refresh_btn.clicked.connect(lambda: self._refresh_system_info(update_memory_value=True))

        self.cores = QSpinBox()
        self.cores.setRange(1, self._system_info.cpu_cores)
        self.cores.setValue(max(1, self._system_info.cpu_cores - 1))

        self.reserve_cores = QSpinBox()
        self.reserve_cores.setRange(0, max(self._system_info.cpu_cores - 1, 0))
        self.reserve_cores.setValue(min(1, self.reserve_cores.maximum()))
        self.reserve_cores.valueChanged.connect(lambda _: self._update_core_limits())

        # --- Swap-Button ---
        self.swap_btn = QPushButton("Swap deaktivieren")
        self.swap_btn.setToolTip(
            "Deaktiviert Swap, damit RAM-Tests den physischen Speicher testen "
            "und nicht auf die Festplatte ausweichen."
        )
        self.swap_btn.clicked.connect(self._disable_swap)

        self.swap_on_btn = QPushButton("Swap aktivieren")
        self.swap_on_btn.setToolTip(
            "Aktiviert den Swap wieder (swapon -a), z.B. nachdem er fuer den "
            "RAM-Test deaktiviert wurde."
        )
        self.swap_on_btn.clicked.connect(self._enable_swap)

        # --- memtest86+ Hinweis ---
        self.memtest_btn = QPushButton("memtest86+ pruefen")
        self.memtest_btn.setToolTip(
            "Prueft ob memtest86+ installiert ist. Fuer gruendlichste RAM-Tests "
            "empfohlen (laeuft ausserhalb des Betriebssystems)."
        )
        self.memtest_btn.clicked.connect(self._check_memtest)

        # --- Ergebnis-Anzeige ---
        self.result_label = QLabel("")
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress = QLabel("Bereit")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(5000)

        self.start_btn = QPushButton(f"{title} starten")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.btop_btn = QPushButton("btop starten")
        self.klog_btn = QPushButton("Kernel-Logs")
        self.mce_btn = QPushButton("MCE-Logs")

        # --- Temperatur-Widget ---
        self.temp_widget = TemperatureWidget()

        duration_widget = QWidget()
        duration_layout = QHBoxLayout()
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.setSpacing(4)
        duration_layout.addWidget(self.duration_hours)
        duration_layout.addWidget(self.duration_minutes)
        duration_layout.addWidget(self.duration_seconds)
        duration_widget.setLayout(duration_layout)

        form = QFormLayout()
        form.addRow("Testmodus", self.mode_box)
        form.addRow("Dauer", duration_widget)
        form.addRow("RAM (MB)", self.memory_mb)
        form.addRow("CPU-Kerne", self.cores)
        form.addRow("GUI-Kerne freilassen", self.reserve_cores)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.btop_btn)
        btn_row.addWidget(self.klog_btn)
        btn_row.addWidget(self.mce_btn)

        util_row = QHBoxLayout()
        util_row.addWidget(self.swap_btn)
        util_row.addWidget(self.swap_on_btn)
        util_row.addWidget(self.memtest_btn)

        layout = QVBoxLayout()
        layout.addWidget(self.info_label)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.temp_widget)
        layout.addLayout(form)
        layout.addLayout(util_row)
        layout.addLayout(btn_row)
        layout.addWidget(self.result_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_view)
        self.setLayout(layout)

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_progress)

        self.start_btn.clicked.connect(self.start_test)
        self.stop_btn.clicked.connect(self.stop_test)
        self.btop_btn.clicked.connect(self._launch_btop)
        self.klog_btn.clicked.connect(self._launch_klogs)
        self.mce_btn.clicked.connect(self._launch_mcelog)

        self.log_signal.connect(self._append_log)
        self._refresh_system_info(update_memory_value=True)

    def start_test(self) -> None:
        if self.runner and self.runner.is_running():
            return
        self._refresh_system_info(update_memory_value=False)
        duration_seconds = self._total_duration_seconds()
        if duration_seconds <= 0:
            self._append_log("Bitte eine Dauer groesser als 0 Sekunden auswaehlen.")
            return
        mem_bytes = int(self.memory_mb.value() * 1024 * 1024)
        usable_cores = max(1, min(self.cores.value(), self._effective_core_limit()))
        if usable_cores != self.cores.value():
            self._append_log(
                f"CPU-Kerne automatisch auf {usable_cores} reduziert, damit die GUI responsiv bleibt."
            )
        cpu_mask = self._build_cpu_mask(usable_cores)
        if cpu_mask:
            try:
                allowed = bin(int(cpu_mask, 16)).count("1")
                total = self._system_info.cpu_cores
                self._append_log(
                    f"CPU-Affinitaet (taskset): {cpu_mask} – "
                    f"{allowed}/{total} logische CPUs erlaubt "
                    f"(reservierte Kerne inkl. HT-Geschwister freigehalten)"
                )
            except ValueError:
                self._append_log(f"CPU-Affinitaet (taskset): {cpu_mask}")
        params = TestParameters(
            duration_seconds=duration_seconds,
            cpu_cores=usable_cores,
            memory_bytes=mem_bytes,
            cpu_mask=cpu_mask,
        )
        mode = self.mode_box.currentData() or "combined"
        self._append_log(f"Testmodus: {STRESS_NG_MODES.get(mode, {}).get('label', mode)}")

        # Warnung bei aktivem Swap und RAM-intensivem Test
        if self._system_info.swap_enabled and mode in ("ram", "ram_bandwidth", "combined"):
            self._append_log(
                "⚠ WARNUNG: Swap ist aktiv! RAM-Test koennte auf die Festplatte "
                "ausweichen. Swap vorher deaktivieren fuer zuverlaessige Ergebnisse."
            )

        self.runner = StressNgRunner(
            params,
            mode=mode,
            vm_workers=usable_cores,
            log_fn=self._handle_runner_log,
        )
        try:
            self.runner.start()
        except Exception as exc:  # pragma: no cover - runtime safety
            self._handle_runner_log(f"Fehler beim Start: {exc}")
            self.runner = None
            return
        self.timer.start()
        self.temp_widget.start_monitoring()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress.setText(language_manager.tr("Fortschritt: 0%"))
        self.result_label.setText("")
        self.result_label.setStyleSheet("")
        self._append_log("Test gestartet...")

    def stop_test(self) -> None:
        if not self.runner:
            return
        self.runner.stop()
        self.timer.stop()
        self.temp_widget.stop_monitoring()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress.setText(language_manager.tr("Manuell gestoppt"))
        self._show_result()
        self._append_log("Test manuell gestoppt.")

    def _append_log(self, text: str) -> None:
        self.log_view.append(text)

    def _handle_runner_log(self, text: str) -> None:
        self.log_signal.emit(text)

    def _update_progress(self) -> None:
        if not self.runner:
            return
        progress = int(self.runner.progress() * 100)
        self.progress_bar.setValue(progress)
        self.progress.setText(language_manager.tr("Fortschritt: {progress}%", progress=progress))
        if not self.runner.is_running():
            self.timer.stop()
            self.temp_widget.stop_monitoring()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.progress_bar.setValue(100)
            self.progress.setText(language_manager.tr("Fertig"))
            self._show_result()

    def _show_result(self) -> None:
        """Zeigt das Pass/Fail-Ergebnis an."""
        if not self.runner:
            return
        result = self.runner.get_result()
        if result is None:
            return
        if result.passed:
            self.result_label.setText(language_manager.tr(
                "✓ BESTANDEN – Keine Fehler gefunden ({seconds:.0f}s)", seconds=result.duration_actual
            ))
            self.result_label.setStyleSheet(
                "color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;"
            )
        else:
            error_summary = "; ".join(result.errors[:3])
            if len(result.errors) > 3:
                error_summary += f" ... (+{len(result.errors) - 3} weitere)"
            self.result_label.setText(language_manager.tr("✗ FEHLER GEFUNDEN – {error}", error=error_summary))
            self.result_label.setStyleSheet(
                "color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;"
            )

    def _disable_swap(self) -> None:
        """Deaktiviert Swap – prueft Sicherheit, dann im Hintergrund-Thread."""
        # 0. Refresh system info so the safety check is up-to-date.
        self._refresh_system_info(update_memory_value=False)

        # 1. Safety check: is there enough free RAM to absorb swap?
        safe, msg = check_swapoff_safe(self._system_info)
        if not safe:
            self._append_log(f"⚠ {msg}")
            QMessageBox.warning(
                self,
                "Swap-Deaktivierung nicht sicher",
                msg,
            )
            return

        # 2. Determine whether we need a password.
        #    On Ubuntu Server 24.04 + XFCE4 there is normally no polkit
        #    agent, so pkexec would hang and plain sudo cannot prompt in
        #    a non-interactive subprocess.  We must ask the user upfront.
        password: Optional[str] = None
        if needs_password_for_swap():
            pw, ok = QInputDialog.getText(
                self,
                "Swap deaktivieren – Passwort erforderlich",
                "sudo-Passwort eingeben:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not pw:
                self._append_log("Swap-Deaktivierung abgebrochen (kein Passwort).")
                return
            password = pw

        # 3. Disable the button and show progress while the thread runs.
        self.swap_btn.setEnabled(False)
        self.swap_btn.setText(language_manager.tr("Swap wird deaktiviert …"))
        self._append_log("Swap-Deaktivierung laeuft (Hintergrund-Thread) …")

        worker = _SwapWorker(password=password, parent=self)
        # prevent garbage collection before the thread finishes
        self._swap_worker = worker

        worker.finished_ok.connect(self._on_swap_done)
        worker.failed.connect(self._on_swap_failed)
        worker.start()

    # -- slots for the background swap worker --------------------------------

    def _on_swap_done(self) -> None:
        """Called (on the main thread) when swapoff succeeded."""
        self._append_log("✓ Swap erfolgreich deaktiviert.")
        self._refresh_system_info(update_memory_value=False)
        self._swap_worker = None

    def _on_swap_failed(self, error_msg: str) -> None:
        """Called (on the main thread) when swapoff failed."""
        self._append_log(f"Swap-Deaktivierung fehlgeschlagen: {error_msg}")
        # Re-enable the button so the user can retry.
        self._refresh_system_info(update_memory_value=False)
        QMessageBox.warning(
            self,
            "Swap-Deaktivierung",
            f"Swap konnte nicht deaktiviert werden:\n{error_msg}\n\n"
            "Manuell ausfuehren: sudo swapoff -a",
        )
        self._swap_worker = None

    # -- slots for the background swap-on worker -----------------------------

    def _enable_swap(self) -> None:
        """Aktiviert Swap wieder – im Hintergrund-Thread."""
        password: Optional[str] = None
        if needs_password_for_swap():
            pw, ok = QInputDialog.getText(
                self,
                "Swap aktivieren – Passwort erforderlich",
                "sudo-Passwort eingeben:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not pw:
                self._append_log("Swap-Aktivierung abgebrochen (kein Passwort).")
                return
            password = pw

        self.swap_on_btn.setEnabled(False)
        self.swap_on_btn.setText(language_manager.tr("Swap wird aktiviert …"))
        self._append_log("Swap-Aktivierung laeuft (Hintergrund-Thread) …")

        worker = _SwapOnWorker(password=password, parent=self)
        self._swap_on_worker = worker
        worker.finished_ok.connect(self._on_swap_on_done)
        worker.failed.connect(self._on_swap_on_failed)
        worker.start()

    def _on_swap_on_done(self) -> None:
        """Called (on the main thread) when swapon succeeded."""
        self._append_log("✓ Swap erfolgreich aktiviert.")
        self._refresh_system_info(update_memory_value=False)
        self._swap_on_worker = None

    def _on_swap_on_failed(self, error_msg: str) -> None:
        """Called (on the main thread) when swapon failed."""
        self._append_log(f"Swap-Aktivierung fehlgeschlagen: {error_msg}")
        self._refresh_system_info(update_memory_value=False)
        QMessageBox.warning(
            self,
            "Swap-Aktivierung",
            f"Swap konnte nicht aktiviert werden:\n{error_msg}\n\n"
            "Manuell ausfuehren: sudo swapon -a",
        )
        self._swap_on_worker = None


    def _check_memtest(self) -> None:
        """Prueft ob memtest86+ installiert ist und zeigt Hinweis."""
        installed, message = check_memtest86_installed()
        if installed:
            QMessageBox.information(
                self,
                "memtest86+",
                f"✓ {message}\n\n"
                "Beim naechsten Neustart kann memtest86+ im GRUB-Bootmenue "
                "unter 'Erweiterte Optionen' gestartet werden.\n\n"
                "memtest86+ testet den RAM ausserhalb des Betriebssystems "
                "und findet Fehler, die Software-Tests nicht erkennen koennen.",
            )
        else:
            QMessageBox.information(
                self,
                "memtest86+",
                f"✗ {message}\n\n"
                "Installation:\n  sudo apt install memtest86+\n\n"
                "Nach der Installation erscheint memtest86+ im GRUB-Bootmenue "
                "unter 'Erweiterte Optionen'.",
            )
        self._append_log(f"memtest86+: {message}")

    def _refresh_system_info(self, update_memory_value: bool) -> None:
        info = self.system_info_provider()
        self._system_info = info
        swap_state = "aktiv" if info.swap_enabled else "deaktiviert"
        reserve_note = f"Reserviert fuer GUI: {self.reserve_cores.value()} Kern(e)"
        if info.physical_cpu_cores > 0:
            core_text = (
                f"Kerne: {info.cpu_cores} logisch / {info.physical_cpu_cores} physisch"
            )
        else:
            core_text = f"Kerne: {info.cpu_cores} logisch"
        self.info_label.setText(
            f"Verfuegbar: {info.available_memory_mb} MB | {core_text} ({reserve_note}) | Swap {swap_state}"
        )
        self.cores.setMaximum(info.cpu_cores)
        if self.cores.value() > info.cpu_cores:
            self.cores.setValue(info.cpu_cores)
        max_reserve = max(info.cpu_cores - 1, 0)
        self.reserve_cores.setMaximum(max_reserve)
        if self.reserve_cores.value() > max_reserve:
            self.reserve_cores.setValue(max_reserve)
        self._update_core_limits()
        self.memory_mb.setMaximum(max(info.available_memory_mb, self.memory_mb.minimum()))
        if update_memory_value:
            self.memory_mb.setValue(info.available_memory_mb)

        # Swap-Button Status
        self.swap_btn.setEnabled(info.swap_enabled)
        if not info.swap_enabled:
            self.swap_btn.setText(language_manager.tr("Swap bereits deaktiviert"))
        else:
            self.swap_btn.setText(language_manager.tr("Swap deaktivieren"))

        # Swap-aktivieren-Button: nur sinnvoll wenn Swap aktuell aus ist
        self.swap_on_btn.setEnabled(not info.swap_enabled)
        if info.swap_enabled:
            self.swap_on_btn.setText(language_manager.tr("Swap bereits aktiv"))
        else:
            self.swap_on_btn.setText(language_manager.tr("Swap aktivieren"))

    def _launch_btop(self) -> None:
        if not launch_command_in_terminal(["btop"], geometry=(110, 44)):
            self._append_log("btop konnte nicht gestartet werden. Bitte Installation pruefen.")

    def _launch_klogs(self) -> None:
        if not launch_command_in_terminal(build_klog_command()):
            self._append_log("Kernel-Logs konnten nicht gestartet werden. Bitte Installation pruefen.")

    def _launch_mcelog(self) -> None:
        if not launch_command_in_terminal(build_mcelog_command()):
            self._append_log("MCE-Logs konnten nicht gestartet werden. Bitte Installation pruefen.")

    def _total_duration_seconds(self) -> int:
        return (
            self.duration_hours.value() * 3600
            + self.duration_minutes.value() * 60
            + self.duration_seconds.value()
        )

    def _set_default_duration(self, seconds: int) -> None:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        self.duration_hours.setValue(hours)
        self.duration_minutes.setValue(minutes)
        self.duration_seconds.setValue(secs)

    def _build_cpu_mask(self, cpu_count: int) -> Optional[str]:
        """Bilde eine taskset-Maske, die ganze *physische* Kerne reserviert.

        Bei aktivem Hyperthreading reicht es nicht, nur einzelne logische
        CPUs aus der Maske zu nehmen – der jeweilige HT-Sibling liefe sonst
        weiter auf demselben physischen Kern. Diese Methode liest die HT-
        Topologie aus ``/sys/devices/system/cpu/cpuN/topology/thread_siblings_list``
        und entfernt komplette physische Kerne (inkl. aller Geschwister).
        """
        total = self._system_info.cpu_cores
        if cpu_count <= 0 or cpu_count >= total:
            return None

        cores = self._physical_core_groups(total)
        # Reservierte logische CPUs:
        reserved_logical_target = total - cpu_count
        # Physische Kerne von hinten reservieren, bis genug logische CPUs
        # entfernt sind. So ist mindestens ein voller Kern (alle HT-Threads)
        # garantiert frei.
        allowed_cores = list(cores)
        removed = 0
        # Mindestens ein physischer Kern muss erlaubt bleiben.
        while len(allowed_cores) > 1 and removed < reserved_logical_target:
            removed += len(allowed_cores.pop())
        if not allowed_cores:
            return None

        allowed_cpus = [cpu for group in allowed_cores for cpu in group]
        mask = 0
        for cpu in allowed_cpus:
            mask |= 1 << cpu
        if mask == 0:
            return None
        return hex(mask)

    def _physical_core_groups(self, total: int) -> list[tuple[int, ...]]:
        """Gibt sortierte Gruppen logischer CPUs pro physischem Kern zurück.

        Nutzt ``thread_siblings_list`` aus sysfs. Fällt auf eine 1-zu-1-
        Zuordnung zurück, falls die Topologie nicht lesbar ist (z. B.
        Container ohne sysfs-CPU-Mounts).
        """
        from pathlib import Path

        seen: set[int] = set()
        groups: list[tuple[int, ...]] = []
        for cpu in range(total):
            if cpu in seen:
                continue
            sibs_path = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
            siblings: tuple[int, ...]
            try:
                raw = sibs_path.read_text().strip()
                ids: set[int] = set()
                for part in raw.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if "-" in part:
                        a, b = part.split("-", 1)
                        ids.update(range(int(a), int(b) + 1))
                    else:
                        ids.add(int(part))
                # Auf vorhandene logische CPUs begrenzen
                ids = {i for i in ids if 0 <= i < total}
                if not ids:
                    ids = {cpu}
                siblings = tuple(sorted(ids))
            except OSError:
                siblings = (cpu,)
            seen.update(siblings)
            groups.append(siblings)
        # Sortierung: nach kleinster CPU-ID der Gruppe (deterministisch)
        groups.sort(key=lambda g: g[0])
        return groups

    def _effective_core_limit(self) -> int:
        total = max(1, self._system_info.cpu_cores)
        max_reserve = max(total - 1, 0)
        reserve = min(self.reserve_cores.value(), max_reserve)
        return max(1, total - reserve)

    def _update_core_limits(self) -> None:
        limit = self._effective_core_limit()
        if self.cores.value() > limit:
            self.cores.setValue(limit)
