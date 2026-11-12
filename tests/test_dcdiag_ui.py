"""Harmless foreground tools, real broker lifecycle, and offscreen Qt controls."""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QWidget

from hardwaretest.core import root_broker
from hardwaretest.tests.dcdiag import DcDiagRunner
from hardwaretest.ui import main_window
from hardwaretest.ui.widgets import dcdiag_panel, temperature_widget


_APP = QApplication.instance() or QApplication([])


def pump(predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    assert predicate()
    _APP.processEvents()


@pytest.fixture
def manual_tool(tmp_path, monkeypatch):
    tool = tmp_path / "dcdiag"
    tool.write_text("#!/bin/sh\nexec sleep 30\n")
    tool.chmod(0o755)
    # This fixture never elevates privileges. Allow only its harmless fake tool;
    # bypass ownership checks for this user-owned temporary test tree.
    script = root_broker.ROOT_BROKER_SCRIPT.replace(
        "/usr/bin/dcdiag|/bin/dcdiag)", f"{tool})"
    ).replace('find -L "$p" -maxdepth 0 -uid 0 ! -perm /022', 'find -L "$p" -maxdepth 0')
    monkeypatch.setattr(root_broker, "ROOT_BROKER_SCRIPT", script)
    broker = root_broker.RootBroker(prefix=[])
    yield tool, broker
    broker.close()


def test_broker_cancels_foreground_process_group(manual_tool, tmp_path):
    tool, broker = manual_tool
    child_pid = tmp_path / "child.pid"
    tool.write_text(f'#!/bin/sh\nsleep 30 &\necho "$!" > "{child_pid}"\nwait\n')
    runner = DcDiagRunner(str(tool))
    runner.root_broker = broker
    runner.start()
    pump(child_pid.exists)
    pid = int(child_pid.read_text())
    assert Path(f"/proc/{pid}").exists()
    runner.stop(aborted=True)
    assert runner.wait_finished(5)
    assert runner.get_result().outcome == "cancelled"
    # A reparented zombie may briefly remain, but must no longer execute.
    stat = Path(f"/proc/{pid}/stat")
    assert not stat.exists() or stat.read_text().split()[2] == "Z"


@pytest.mark.parametrize("tail", [
    ["--service"], ["-s"], ["--query"], ["--help"],
    ["-T", "10s"], ["--strict-runtime"], ["-o", "/tmp/result"],
])
def test_broker_rejects_all_dcdiag_options(tail):
    broker = root_broker.RootBroker(prefix=[])
    try:
        with pytest.raises(RuntimeError, match="Befehl nicht erlaubt"):
            broker.submit(["/usr/bin/dcdiag", *tail])
    finally:
        broker.close()


def test_broker_rejects_untrusted_paths(tmp_path, monkeypatch):
    tool = tmp_path / "dcdiag"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o777)
    # Exercise the unchanged trust guard with a caller-writable candidate.
    monkeypatch.setattr(root_broker, "ROOT_BROKER_SCRIPT",
                        root_broker.ROOT_BROKER_SCRIPT.replace(
                            "/usr/bin/dcdiag|/bin/dcdiag)", f"{tool})"))
    broker = root_broker.RootBroker(prefix=[])
    try:
        with pytest.raises(RuntimeError, match="Befehl nicht erlaubt"):
            broker.submit([str(tool)])
        with pytest.raises(RuntimeError, match="Befehl nicht erlaubt"):
            broker.submit(["dcdiag"])
    finally:
        broker.close()


@pytest.mark.parametrize("binary", ["/usr/bin/true", "/bin/true"])
def test_broker_accepts_trusted_foreground_only(monkeypatch, binary):
    # Exercise the original ownership/ancestor checks with a real system binary,
    # without running Intel's workload or requiring root.
    monkeypatch.setattr(root_broker, "ROOT_BROKER_SCRIPT",
                        root_broker.ROOT_BROKER_SCRIPT.replace(
                            "/usr/bin/dcdiag|/bin/dcdiag)", f"{binary})"))
    broker = root_broker.RootBroker(prefix=[])
    try:
        assert broker.run([binary], timeout=5).returncode == 0
    finally:
        broker.close()


@pytest.fixture
def panel(manual_tool, monkeypatch):
    tool, broker = manual_tool
    monkeypatch.setattr(dcdiag_panel, "available_dcdiag_path", lambda: str(tool))
    monkeypatch.setattr(dcdiag_panel, "RootBroker", lambda **kwargs: broker)
    monkeypatch.setattr(temperature_widget.TemperatureWidget, "_refresh", lambda self: None)
    monkeypatch.setattr(temperature_widget.TemperatureWidget, "start_monitoring", lambda self: None)
    widget = dcdiag_panel.DcDiagPanel()
    reports = []
    monkeypatch.setattr(widget.report_bar, "publish", reports.append)
    yield widget, reports
    if widget._worker is not None:
        widget.stop_test()
        pump(lambda: widget._worker is None)
    if widget.runner is not None and widget.runner.is_running():
        widget.stop_test()
    if widget.runner is not None:
        assert widget.runner.wait_finished(5)
    widget.close_privileged_session()
    widget.timer.stop()
    widget.deleteLater()
    _APP.processEvents()


def test_manual_start_stop_and_cancelled_report(panel):
    widget, reports = panel
    widget.start_test()
    pump(lambda: not widget._launching)
    assert widget.runner.is_running()
    assert not widget.start_btn.isEnabled()
    assert widget.stop_btn.isEnabled()
    assert widget.progress.maximum() == 0  # No fabricated percentage.
    widget.stop_test()
    pump(lambda: widget._worker is None)
    assert widget.start_btn.isEnabled()
    assert not widget.stop_btn.isEnabled()
    assert reports[-1].status == "cancelled"
    assert "Exit-Code" in reports[-1].summary
    assert not widget._broker
    widget._update_progress()
    assert len(reports) == 1


@pytest.mark.parametrize("message,status", [
    ("Test completed successfully. No issues detected.", "passed"),
    ("unknown output", "warning"),
    ("This processor is not supported by this version of the tool.", "skipped"),
])
def test_completion_reports_keep_original_output(panel, manual_tool, message, status):
    widget, reports = panel
    tool, _ = manual_tool
    tool.write_text(f"#!/bin/sh\necho '{message}'\n")
    widget.start_test()
    pump(lambda: widget._worker is None)
    pump(lambda: widget.runner.get_result() is not None)
    widget._update_progress()
    assert reports[-1].status == status
    assert message in reports[-1].raw_output
    assert widget.start_btn.isEnabled()


def test_authorization_failure_is_visible(panel, manual_tool, monkeypatch):
    widget, reports = panel
    _, broker = manual_tool

    def denied():
        raise root_broker.RootAuthorizationError("Test authorization denied")

    monkeypatch.setattr(broker, "start", denied)
    widget.start_test()
    pump(lambda: widget._worker is None)
    assert "Test authorization denied" in widget.result_label.text()
    assert widget.start_btn.isEnabled()
    assert not widget.stop_btn.isEnabled()
    assert not reports


def test_stop_during_authorization_does_not_launch_test(panel, manual_tool, monkeypatch):
    widget, reports = panel
    _, broker = manual_tool
    import threading

    waiting = threading.Event()
    release = threading.Event()
    original = broker.start

    def authorize():
        waiting.set()
        assert release.wait(5)
        original()

    monkeypatch.setattr(broker, "start", authorize)
    widget.start_test()
    pump(waiting.is_set)
    widget.stop_test()
    release.set()
    pump(lambda: widget._worker is None)
    assert widget.runner._process is None
    assert not widget.runner.is_running()
    assert "Abgebrochen" in widget.result_label.text()
    assert not reports


class _OtherPanel(QWidget):
    busy_changed = Signal(bool)
    safety_stop = Signal()
    guard_released = Signal()
    raid_tab_requested = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__()

    def stop_test(self):
        pass

    _stop_io_test = stop_test
    _stop_diagnostic = stop_test
    _stop = stop_test

    def is_busy(self):
        return False


@pytest.mark.parametrize("binary", [None, "/usr/bin/dcdiag"])
def test_main_window_conditional_tab_and_safety_registration(monkeypatch, binary):
    for name in (
        "TestPanel", "MprimePanel", "OpenDcDiagPanel", "MemoryControllerPanel",
        "MemoryFillPanel", "FileDiskPanel", "DeviceDiskPanel", "DestructiveDiskPanel",
        "NvmePanel", "NvidiaPanel", "GpuPanel", "NetworkPanel", "TestPlanPanel",
        "FilesystemPanel", "ZfsPanel", "MonitoringPanel", "ProliantPanel",
        "SystemPanel", "InfoPanel", "HelpPanel",
    ):
        monkeypatch.setattr(main_window, name, _OtherPanel)
    monkeypatch.setattr(main_window, "available_dcdiag_path", lambda: binary)
    monkeypatch.setattr(main_window, "update_check_disabled", lambda: True)
    monkeypatch.setattr(main_window.ipmi, "is_proliant", lambda: False)
    monkeypatch.setattr(temperature_widget.TemperatureWidget, "_refresh", lambda self: None)
    window = main_window.MainWindow()
    titles = [window._tabs.tabText(index) for index in range(window._tabs.count())]
    assert "OpenDCDiag" in titles
    assert ("Intel DCDiag" in titles) == bool(binary)
    panels = [p for p in window._test_panels if isinstance(p, dcdiag_panel.DcDiagPanel)]
    if binary:
        assert len(panels) == 1
        assert panels[0].stop_test in window._stop_callbacks
        window._safety_stop()
        assert not panels[0].isEnabled()
    else:
        assert not panels
    window.close()
    window.deleteLater()
    _APP.processEvents()
