"""Main window wiring together all tabs."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, QThread
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from hardwaretest import __version__
from hardwaretest.ui.widgets.test_panel import TestPanel
from hardwaretest.ui.widgets.mprime_panel import MprimePanel
from hardwaretest.ui.widgets.memory_controller_panel import MemoryControllerPanel
from hardwaretest.ui.widgets.memory_fill_panel import MemoryFillPanel
from hardwaretest.ui.widgets.info_panel import InfoPanel
from hardwaretest.ui.widgets.disk_panel import (
    DestructiveDiskPanel,
    DeviceDiskPanel,
    FileDiskPanel,
)
from hardwaretest.ui.widgets.help_panel import HelpPanel
from hardwaretest.ui.widgets.network_panel import NetworkPanel
from hardwaretest.ui.widgets.nvme_panel import NvmePanel
from hardwaretest.ui.widgets.nvidia_panel import NvidiaPanel
from hardwaretest.ui.widgets.test_plan_panel import TestPlanPanel
from hardwaretest.ui.widgets.filesystem_panel import FilesystemPanel
from hardwaretest.ui.widgets.monitoring_panel import MonitoringPanel
from hardwaretest.ui.widgets.zfs_panel import ZfsPanel
from hardwaretest.ui.i18n import language_manager


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._tabs = QTabWidget()
        screen = QGuiApplication.primaryScreen()
        available_geom = screen.availableGeometry() if screen else None
        compact_mode = self._should_use_compact_mode(available_geom)

        ram_panel = TestPanel(title="stress-ng")
        self._tabs.addTab(ram_panel, "stress-ng")

        prime_panel = MprimePanel()
        self._tabs.addTab(prime_panel, "Prime95")

        mem_ctrl_panel = MemoryControllerPanel()
        self._tabs.addTab(mem_ctrl_panel, "Speichercontroller")

        mem_fill_panel = MemoryFillPanel()
        self._tabs.addTab(mem_fill_panel, "RAM-Fuelltest")

        file_disk_panel = FileDiskPanel()
        self._tabs.addTab(file_disk_panel, "Disk (Datei)")

        device_disk_panel = DeviceDiskPanel()
        self._tabs.addTab(device_disk_panel, "Disk (Geräte)")

        destructive_disk_panel = DestructiveDiskPanel()
        self._tabs.addTab(destructive_disk_panel, "Disk (Destruktiv)")

        nvme_panel = NvmePanel()
        self._tabs.addTab(nvme_panel, "NVMe")

        nvidia_panel = NvidiaPanel()
        self._tabs.addTab(nvidia_panel, "NVIDIA GPU")

        network_panel = NetworkPanel()
        self._tabs.addTab(network_panel, "Netzwerk")

        test_plan_panel = TestPlanPanel()
        self._tabs.addTab(test_plan_panel, "Gesamttest")

        self.filesystem_panel = FilesystemPanel()
        self.zfs_panel = ZfsPanel()
        filesystem_tabs = QTabWidget()
        filesystem_tabs.addTab(self.filesystem_panel, "Partitionen")
        filesystem_tabs.addTab(self.zfs_panel, "ZFS-Pools")
        self._tabs.addTab(filesystem_tabs, "Dateisystem")
        self.monitoring_panel = MonitoringPanel()
        self._tabs.addTab(self.monitoring_panel, "Langzeitmonitoring")
        self._test_panels = [ram_panel, prime_panel, mem_ctrl_panel, mem_fill_panel,
                             file_disk_panel, device_disk_panel, destructive_disk_panel,
                             nvme_panel, nvidia_panel, network_panel, test_plan_panel]
        self._stop_callbacks = [panel.stop_test for panel in self._test_panels[:7]] + [
            nvme_panel._stop_io_test, nvidia_panel._stop_diagnostic,
            network_panel._stop, test_plan_panel._stop,
        ]
        self._filesystem_active = False
        self._temperature_lock = False
        self.filesystem_panel.can_start = self._can_check_filesystem
        self.zfs_panel.can_start = self._can_check_filesystem
        self.filesystem_panel.busy_changed.connect(self._filesystem_busy)
        self.zfs_panel.busy_changed.connect(self._filesystem_busy)
        self.monitoring_panel.safety_stop.connect(self._safety_stop)
        self.monitoring_panel.guard_released.connect(self._release_temperature_lock)

        info_panel = InfoPanel(compact_mode=compact_mode)
        self._tabs.addTab(info_panel, "Informationen")

        help_panel = HelpPanel()
        self._tabs.addTab(help_panel, "Hilfe / Help")

        self.setCentralWidget(self._tabs)
        self._add_menus()
        language_manager.language_changed.connect(self._retranslate)
        self._retranslate()
        self._apply_initial_geometry(compact_mode, available_geom)

    def _can_check_filesystem(self) -> bool:
        if self.filesystem_panel.is_busy() or self.zfs_panel.is_busy() or self._temperature_lock:
            return False
        for panel in self._test_panels:
            for name in ("runner", "_runner", "_io_runner"):
                runner = getattr(panel, name, None)
                if runner is not None and runner.is_running():
                    return False
            if getattr(panel, "_self_test_active", False):
                return False
            if any(thread.isRunning() for thread in panel.findChildren(QThread)):
                return False
        return True

    def _filesystem_busy(self, busy: bool) -> None:
        self._filesystem_active = self.filesystem_panel.is_busy() or self.zfs_panel.is_busy()
        self.filesystem_panel.setEnabled(not self.zfs_panel.is_busy())
        self.zfs_panel.setEnabled(not self.filesystem_panel.is_busy())
        self._update_test_lock()

    def _update_test_lock(self) -> None:
        for panel in self._test_panels:
            panel.setEnabled(not (self._filesystem_active or self._temperature_lock))

    def _safety_stop(self) -> None:
        self._temperature_lock = True
        self._update_test_lock()
        for callback in self._stop_callbacks:
            callback()
        # Firmware NVMe self-tests cannot be stopped by killing a user process;
        # filesystem repair must never be interrupted by this thermal guard.

    def _release_temperature_lock(self) -> None:
        self._temperature_lock = False
        self._update_test_lock()

    def closeEvent(self, event) -> None:
        if self.filesystem_panel.is_busy() or self.zfs_panel.is_busy() or self.monitoring_panel.is_busy():
            QMessageBox.information(self, language_manager.tr("Aktion läuft"), language_manager.tr(
                "Bitte das Monitoring zuerst stoppen und laufende Dateisystem-Aktionen abschließen lassen."
            ))
            event.ignore()
            return
        # Test plan/scan workers must not be destroyed while their thread runs.
        if any(thread.isRunning() for thread in self.findChildren(QThread)):
            QMessageBox.information(self, language_manager.tr("Aktion läuft"), language_manager.tr(
                "Bitte laufende Tests beenden und warten, bis die Hintergrundaktion abgeschlossen ist."
            ))
            event.ignore()
            return
        super().closeEvent(event)

    def _add_menus(self) -> None:
        """Add the global language selector and the application notice."""
        self._language_menu = self.menuBar().addMenu("Sprache")
        action_group = QActionGroup(self)
        action_group.setExclusive(True)
        self._german_action = QAction("Deutsch", self, checkable=True)
        self._english_action = QAction("English", self, checkable=True)
        for action, code in ((self._german_action, "de"), (self._english_action, "en")):
            action.setActionGroup(action_group)
            action.triggered.connect(lambda checked=False, language=code: language_manager.set_language(language))
            self._language_menu.addAction(action)

        self._help_menu = self.menuBar().addMenu("Hilfe / Help")
        self._about_action = QAction("Über Hardwaretest / About", self)
        self._about_action.triggered.connect(self._show_about)
        self._help_menu.addAction(self._about_action)

    def _retranslate(self, _language: str | None = None) -> None:
        language_manager.retranslate_widget_tree(self)
        if language_manager.language == "en":
            self.setWindowTitle(f"Hardware Test Launcher v{__version__}")
        else:
            self.setWindowTitle(f"Hardwaretest-Starter v{__version__}")
        self._german_action.setChecked(language_manager.language == "de")
        self._english_action.setChecked(language_manager.language == "en")

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            language_manager.tr("Über Hardwaretest"),
            f"<h3>Hardwaretest {__version__}</h3>"
            + ("<p>GUI for CPU, RAM and disk stress tests.</p>"
               "<p>This software is released under the <b>MIT License</b>. "
               "The complete license text is available in <code>LICENSE</code>.</p>"
               if language_manager.language == "en" else
               "<p>GUI für CPU-, RAM- und Festplatten-Stresstests.</p>"
               "<p>Diese Software steht unter der <b>MIT-Lizenz</b>. "
               "Den vollständigen Lizenztext finden Sie in der Datei <code>LICENSE</code>.</p>")
            + "<p>© 2026 Norbert Jander</p>",
        )

    @staticmethod
    def _should_use_compact_mode(available_geom: Optional[QRect]) -> bool:
        if available_geom is None:
            return False
        return available_geom.width() <= 1280 or available_geom.height() <= 800

    def _apply_initial_geometry(self, compact_mode: bool, available_geom: Optional[QRect]) -> None:
        default_width = 900 if not compact_mode else 820
        default_height = 750 if not compact_mode else 680
        min_width = 800 if not compact_mode else 760
        min_height = 680 if not compact_mode else 620
        if available_geom is not None:
            margin = 80
            width = min(default_width, max(min_width, available_geom.width() - margin))
            height = min(default_height, max(min_height, available_geom.height() - margin))
        else:
            width = default_width
            height = default_height
        self.setMinimumSize(min_width, min_height)
        self.resize(width, height)
