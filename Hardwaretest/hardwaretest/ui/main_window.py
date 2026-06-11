"""Main window wiring together all tabs."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMainWindow, QTabWidget

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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hardware Test Launcher")
        tabs = QTabWidget()
        screen = QGuiApplication.primaryScreen()
        available_geom = screen.availableGeometry() if screen else None
        compact_mode = self._should_use_compact_mode(available_geom)

        ram_panel = TestPanel(title="stress-ng")
        tabs.addTab(ram_panel, "stress-ng")

        prime_panel = MprimePanel()
        tabs.addTab(prime_panel, "Prime95")

        mem_ctrl_panel = MemoryControllerPanel()
        tabs.addTab(mem_ctrl_panel, "Speichercontroller")

        mem_fill_panel = MemoryFillPanel()
        tabs.addTab(mem_fill_panel, "RAM-Fuelltest")

        file_disk_panel = FileDiskPanel()
        tabs.addTab(file_disk_panel, "Disk (Datei)")

        device_disk_panel = DeviceDiskPanel()
        tabs.addTab(device_disk_panel, "Disk (Geräte)")

        destructive_disk_panel = DestructiveDiskPanel()
        tabs.addTab(destructive_disk_panel, "Disk (Destruktiv)")

        info_panel = InfoPanel(compact_mode=compact_mode)
        tabs.addTab(info_panel, "Informationen")

        help_panel = HelpPanel()
        tabs.addTab(help_panel, "Hilfe / Help")

        self.setCentralWidget(tabs)
        self._apply_initial_geometry(compact_mode, available_geom)

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
