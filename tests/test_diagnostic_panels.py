from PySide6.QtWidgets import QApplication, QMessageBox

from hardwaretest.core.filesystem_check import FilesystemDevice
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.filesystem_panel import FilesystemPanel
from hardwaretest.ui.widgets.monitoring_panel import MonitoringPanel


_APP = QApplication.instance() or QApplication([])


def test_filesystem_controls_block_mounted_and_unsupported():
    panel = FilesystemPanel()
    panel.scanned([
        FilesystemDevice("/dev/a", "ext4", "a", "8:1", ("/",), "part"),
        FilesystemDevice("/dev/b", "xfs", "b", "8:2", (), "part"),
        FilesystemDevice("/dev/c", "ext4", "c", "8:3", (), "part"),
    ])
    assert not panel.repair.isEnabled()
    assert not panel.check.isEnabled()
    assert panel.online.isEnabled()
    panel.devices.setCurrentIndex(1)
    assert not panel.repair.isEnabled()
    panel.devices.setCurrentIndex(2)
    assert panel.repair.isEnabled()
    assert panel.check.isEnabled()
    panel.deleteLater()


def test_cancel_repair_confirmation_never_starts_process(monkeypatch):
    panel = FilesystemPanel()
    panel.scanned([FilesystemDevice("/dev/c", "ext4", "c", "8:3", (), "part")])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    panel.start("repair")
    assert panel._process is None
    assert panel._report is None
    panel.deleteLater()


def test_english_german_switch_retained_after_global_translation(monkeypatch):
    panel = FilesystemPanel()
    monitor = MonitoringPanel()
    monkeypatch.setattr(language_manager, "_language", "en")
    panel.retranslate()
    monitor.retranslate()
    language_manager.retranslate_widget_tree(panel)
    assert panel.check.text() == "Check integrity (offline)"
    assert monitor.start_btn.text() == "Start monitoring"
    monkeypatch.setattr(language_manager, "_language", "de")
    panel.retranslate()
    monitor.retranslate()
    language_manager.retranslate_widget_tree(panel)
    assert panel.check.text() == "Struktur prüfen (offline)"
    panel.deleteLater()
    monitor.deleteLater()


def test_monitor_emits_one_safety_stop_but_keeps_recording():
    panel = MonitoringPanel()
    stops = []
    panel.safety_stop.connect(lambda: stops.append(True))
    snapshot = dict(metrics={"temperature.cpu": 95}, timestamp="now", samples=1,
                    alert_count=1, csv="test.csv", alerts=["hot"], critical=True)
    panel.show_snapshot(snapshot)
    panel.show_snapshot(snapshot)
    assert stops == [True]
    assert "95" in panel.metrics.toPlainText()
    panel.deleteLater()
