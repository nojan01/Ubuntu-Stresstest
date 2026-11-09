"""Lebenszyklus-Regressionen der Qt-Oberfläche (Worker, QProcess, closeEvent, Swap-Ablauf)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QProcess, QThread, Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from hardwaretest.core import test_runner
from hardwaretest.core.system_info import SystemInfo
from hardwaretest.ui import i18n, report_html
from hardwaretest.ui.widgets import (
    gpu_panel,
    info_panel,
    memory_fill_panel,
    nvidia_panel,
    nvme_panel,
    system_panel,
    temperature_widget,
    worker_lifecycle,
)
import hardwaretest.ui.main_window as mw

_APP = QApplication.instance() or QApplication([])


def _pump(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.001)
    assert predicate(), "Bedingung nicht innerhalb der Wartezeit erfüllt"


def _running_threads(widget) -> list[QThread]:
    return [t for t in widget.findChildren(QThread) if t.isRunning()]


def _settle(widget) -> None:
    _pump(lambda: not _running_threads(widget))
    for _ in range(3):
        _APP.sendPostedEvents(None, 0)
        _APP.processEvents()


@pytest.fixture
def keep():
    """Hält Panels bis nach dem Abarbeiten ausstehender deleteLater()-Ereignisse am Leben.

    Lambdas in Signalverbindungen referenzieren das Panel; würde das Panel erst beim
    Löschen seines letzten Kind-QProcess freigegeben, stürzt Qt im Destruktor ab.
    """
    panels: list = []
    yield panels.append
    for panel in panels:
        _settle(panel)
    panels.clear()


@pytest.fixture(autouse=True)
def _quiet_widgets(monkeypatch):
    monkeypatch.setattr(temperature_widget.TemperatureWidget, "_refresh", lambda self: None)
    monkeypatch.setattr(temperature_widget.TemperatureWidget, "start_monitoring", lambda self: None)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


# --------------------------------------------------------------------------- Befund 1: closeEvent


class _FakeRunner:
    """Runner, dessen stdout-Thread nach stop() noch kurz weiter loggt."""

    def __init__(self, log_fn):
        self._log = log_fn
        self._stop = threading.Event()
        self.waited_with = None
        self._thread = threading.Thread(target=self._stream, daemon=True)
        self._thread.start()

    def _stream(self):
        while not self._stop.wait(0.001):
            self._log("tick")
        for _ in range(50):
            self._log("nachlauf")
            time.sleep(0.0005)

    def is_running(self):
        return self._thread.is_alive()

    def stop(self, aborted: bool = False):
        self._stop.set()

    def wait_finished(self, timeout=None):
        self.waited_with = timeout
        self._thread.join(timeout)
        return not self._thread.is_alive()


class _FakeLegacyRunner:
    """Alter Core ohne wait_finished(): Fallback joint _stdout_thread direkt."""

    def __init__(self, log_fn):
        self._inner = _FakeRunner(log_fn)
        self._stdout_thread = self._inner._thread
        self._watchdog_thread = None

    def is_running(self):
        return self._inner.is_running()

    def stop(self, aborted: bool = False):
        self._inner.stop(aborted)


class _Panel(QObject):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self.runner = None


class _FakeEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True

    def accept(self):
        pass


def _bare_window(panels) -> mw.MainWindow:
    window = mw.MainWindow.__new__(mw.MainWindow)
    QMainWindow.__init__(window)
    window._test_panels = panels
    window.filesystem_panel = mock.Mock(is_busy=lambda: False)
    window.zfs_panel = mock.Mock(is_busy=lambda: False)
    window.monitoring_panel = mock.Mock(is_busy=lambda: False)
    window.proliant_panel = QObject()
    window.system_panel = QObject()
    return window


def test_close_event_joins_runner_threads_before_window_closes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    panel = _Panel()
    panel.runner = _FakeRunner(panel.lines.append)
    window = _bare_window([panel])
    event = _FakeEvent()
    with mock.patch.object(QMainWindow, "closeEvent") as super_close:
        window.closeEvent(event)
    assert not event.ignored
    assert super_close.called
    assert panel.runner.waited_with == mw.MainWindow._RUNNER_JOIN_TIMEOUT
    assert not panel.runner.is_running(), "stdout-Thread lief beim Schließen noch"
    assert "nachlauf" in panel.lines


def test_close_event_falls_back_without_wait_finished(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    panel = _Panel()
    panel.runner = _FakeLegacyRunner(panel.lines.append)
    assert not hasattr(panel.runner, "wait_finished")
    window = _bare_window([panel])
    event = _FakeEvent()
    with mock.patch.object(QMainWindow, "closeEvent"):
        window.closeEvent(event)
    assert not event.ignored
    assert not panel.runner.is_running()


def test_close_event_keeps_runner_alive_when_user_declines(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    panel = _Panel()
    panel.runner = _FakeRunner(panel.lines.append)
    window = _bare_window([panel])
    event = _FakeEvent()
    window.closeEvent(event)
    assert event.ignored
    assert panel.runner.is_running()
    panel.runner.stop()
    panel.runner.wait_finished(2)


def test_close_event_refuses_while_password_prompt_is_active():
    window = _bare_window([])
    window.system_panel = mock.Mock(is_busy=lambda: True)
    event = _FakeEvent()
    with mock.patch.object(QMainWindow, "closeEvent") as super_close:
        window.closeEvent(event)
    assert event.ignored
    assert not super_close.called


def test_real_runner_exposes_wait_finished_contract():
    # Schnittstellen-Vertrag mit dem Core: stdout- und Watchdog-Thread werden gejoint.
    assert callable(getattr(test_runner.BaseTestRunner, "wait_finished", None))


# --------------------------------------------------------------------------- Befund 2: Swap asynchron


class _InstantSwapWorker(QThread):
    finished_ok = Signal()
    failed = Signal(str)
    outcome = "ok"

    def __init__(self, password=None, parent=None):
        super().__init__(parent)

    def run(self):
        if self.outcome == "ok":
            self.finished_ok.emit()
        else:
            self.failed.emit("swapoff: verweigert")


def _memory_fill_panel(monkeypatch, swap_enabled: bool):
    info = SystemInfo(16 << 30, 12 << 30, swap_enabled, 8, 4, swap_used_bytes=1 << 20, swap_total_bytes=1 << 30)
    monkeypatch.setattr(memory_fill_panel, "check_swapoff_safe", lambda _info: (True, ""))
    monkeypatch.setattr(memory_fill_panel, "_SwapWorker", _InstantSwapWorker)
    blocking = mock.Mock(side_effect=AssertionError("disable_swap darf nicht im GUI-Thread laufen"))
    monkeypatch.setattr(memory_fill_panel, "disable_swap", blocking)
    panel = memory_fill_panel.MemoryFillPanel(system_info_provider=lambda: info)
    panel.duration_minutes.setValue(1) if hasattr(panel, "duration_minutes") else None
    launched = []
    monkeypatch.setattr(panel, "_launch_runner", lambda: launched.append(True))
    return panel, launched


def test_start_test_disables_swap_asynchronously_then_launches(monkeypatch):
    panel, launched = _memory_fill_panel(monkeypatch, swap_enabled=True)
    _InstantSwapWorker.outcome = "ok"
    panel.start_test()
    assert panel._start_after_swap is True
    assert not panel.start_btn.isEnabled()
    assert "Swap wird deaktiviert" in panel.progress.text()
    _settle(panel)
    assert launched == [True]
    assert panel._start_after_swap is False
    assert panel._swap_worker is None
    assert panel.start_btn.isEnabled()
    # Zweiter Start während laufender Deaktivierung wird ignoriert.
    panel._start_after_swap = True
    panel.start_test()
    assert launched == [True]


def test_start_test_resets_controls_when_swap_disable_fails(monkeypatch):
    panel, launched = _memory_fill_panel(monkeypatch, swap_enabled=True)
    _InstantSwapWorker.outcome = "fail"
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    panel.start_test()
    _settle(panel)
    assert launched == []
    assert panel._start_after_swap is False
    assert panel.start_btn.isEnabled()
    assert panel.progress.text() == ""
    assert panel._swap_worker is None


def test_start_test_without_swap_launches_directly(monkeypatch):
    panel, launched = _memory_fill_panel(monkeypatch, swap_enabled=False)
    panel.start_test()
    assert launched == [True]
    assert panel.findChildren(QThread) == []


# --------------------------------------------------------------------------- Befund 3/4: Worker-Lebenszyklus


def test_release_worker_only_clears_matching_reference():
    owner = QObject()
    first = QThread(owner)
    second = QThread(owner)
    owner._w = second
    assert worker_lifecycle.release_worker(owner, "_w", first) is False
    assert owner._w is second
    assert worker_lifecycle.release_worker(owner, "_w", second) is True
    assert owner._w is None
    assert worker_lifecycle.release_worker(owner, "_w", None) is False


def test_nvidia_telemetry_does_not_accumulate_dead_threads(monkeypatch):
    monkeypatch.setattr(nvidia_panel, "list_nvidia_gpus", lambda: [])
    panel = nvidia_panel.NvidiaPanel()
    panel._monitoring = True
    for _ in range(50):
        panel._request_telemetry()
        assert panel._telemetry_worker is not None
        # Überlappung: kein zweiter Worker, solange der erste läuft.
        current = panel._telemetry_worker
        panel._request_telemetry()
        assert panel._telemetry_worker is current
        _pump(lambda: panel._telemetry_worker is None)
    _settle(panel)
    assert len(panel.findChildren(QThread)) == 0


def test_nvidia_scan_worker_is_released(monkeypatch):
    monkeypatch.setattr(nvidia_panel, "list_nvidia_gpus", lambda: [])
    monkeypatch.setattr(
        nvidia_panel, "inspect_linux_nvidia",
        lambda: mock.Mock(driver_ready=True, hardware_present=True, messages=[], hints=[]),
    )
    panel = nvidia_panel.NvidiaPanel()
    monkeypatch.setattr(panel, "_show_linux_status", lambda status: None)
    for _ in range(20):
        panel._scan()
        _pump(lambda: panel._scan_worker is None)
    _settle(panel)
    assert panel.findChildren(QThread) == []
    assert panel.scan_btn.isEnabled()


def test_gpu_scan_worker_is_released(monkeypatch):
    monkeypatch.setattr(gpu_panel, "list_vulkan_gpus", lambda: [], raising=False)
    panel = gpu_panel.GpuPanel()
    for _ in range(20):
        panel._scan()
        _pump(lambda: panel._scan_worker is None)
    _settle(panel)
    assert panel.findChildren(QThread) == []


def test_nvme_worker_cleanup_happens_only_on_finished(monkeypatch):
    monkeypatch.setattr(nvme_panel, "nvme_available", lambda: False)
    panel = nvme_panel.NvmePanel()
    panel.device_box.addItem("/dev/nvme0n1", userData="/dev/nvme0n1")
    results = []

    def slow_failure():
        time.sleep(0.02)
        raise RuntimeError("kaputt")

    assert panel._run_worker(slow_failure, results.append) is True
    worker = panel._worker
    # Während der Thread läuft, darf kein zweiter gestartet werden.
    assert panel._run_worker(lambda: None, results.append) is False
    assert panel._worker is worker
    # failed-Signal verarbeitet nur Nutzdaten – Referenz bleibt, bis finished kommt.
    panel._worker_failed("x", show_failure=False)
    assert panel._worker is worker
    _pump(lambda: panel._worker is None)
    _settle(panel)
    assert panel.findChildren(QThread) == []
    assert panel.health_btn.isEnabled()
    for _ in range(50):
        assert panel._run_worker(lambda: 1, results.append)
        _pump(lambda: panel._worker is None)
    _settle(panel)
    assert results.count(1) == 50
    assert panel.findChildren(QThread) == []


# --------------------------------------------------------------------------- Befund 5: SystemPanel QProcess


def test_system_panel_recovers_from_failed_to_start(monkeypatch, keep):
    monkeypatch.setattr(system_panel.privilege, "tool", lambda: "/nonexistent/sudo-xyz")
    monkeypatch.setattr(system_panel.privilege, "command_prefix", lambda reason="": ["/nonexistent/sudo-xyz"])
    monkeypatch.setattr(system_panel.inv, "dmidecode_command", lambda: ["--version"])
    panel = system_panel.SystemPanel()
    keep(panel)
    panel._read_with_password()
    assert panel.is_busy()
    assert not panel.password_button.isEnabled()
    _pump(lambda: not panel.is_busy())
    assert panel.password_button.isEnabled()
    assert panel._memory_error
    panel.close_privileged_session()  # idempotent ohne Prozess


def test_system_panel_close_kills_running_prompt(monkeypatch, keep):
    monkeypatch.setattr(system_panel.privilege, "tool", lambda: "/bin/sleep")
    monkeypatch.setattr(system_panel.privilege, "command_prefix", lambda reason="": ["/bin/sleep"])
    monkeypatch.setattr(system_panel.inv, "dmidecode_command", lambda: ["30"])
    panel = system_panel.SystemPanel()
    keep(panel)
    panel._read_with_password()
    assert panel.is_busy()
    panel.close_privileged_session()
    assert not panel.is_busy()
    assert panel.password_button.isEnabled()


# --------------------------------------------------------------------------- Befund 6: fastfetch / Aushängen asynchron


def test_fastfetch_runs_asynchronously_with_timeout(monkeypatch, keep):
    monkeypatch.setattr(info_panel.shutil, "which", lambda name: "/bin/sleep" if name == "fastfetch" else None)
    monkeypatch.setattr(info_panel.InfoPanel, "FASTFETCH_TIMEOUT_MS", 50)
    started = []
    real_start = QProcess.start

    def fake_start(self, program, args=None, *rest):
        started.append((program, list(args or [])))
        return real_start(self, "/bin/sleep", ["5"])

    monkeypatch.setattr(QProcess, "start", fake_start)
    info = SystemInfo(16 << 30, 12 << 30, False, 8, 4)
    panel = info_panel.InfoPanel(system_info_provider=lambda: info)
    keep(panel)
    assert panel._fastfetch_process is not None, "fastfetch muss asynchron laufen"
    assert started and started[0][0] == "fastfetch"
    _pump(lambda: panel._fastfetch_process is None, timeout=3)
    assert "Zeitüberschreitung" in panel.fastfetch_view.toPlainText()


def test_fastfetch_output_is_shown(monkeypatch, keep):
    monkeypatch.setattr(info_panel.shutil, "which", lambda name: "/bin/echo" if name == "fastfetch" else None)
    real_start = QProcess.start
    monkeypatch.setattr(QProcess, "start", lambda self, program, args=None, *r: real_start(self, "/bin/echo", ["hallo"]))
    info = SystemInfo(16 << 30, 12 << 30, False, 8, 4)
    panel = info_panel.InfoPanel(system_info_provider=lambda: info)
    keep(panel)
    _pump(lambda: panel._fastfetch_process is None)
    assert panel.fastfetch_view.toPlainText() == "hallo"


def test_unmount_runs_asynchronously_and_reenables_controls(monkeypatch, keep):
    from hardwaretest.ui.widgets import disk_panel

    monkeypatch.setattr(disk_panel.DeviceDiskPanel, "_scan_block_devices", lambda self: [])
    panel = disk_panel.DeviceDiskPanel()
    keep(panel)
    lsblk_json = json.dumps({"blockdevices": [
        {"name": "sdz", "type": "disk", "mountpoints": [None],
         "children": [{"name": "sdz1", "type": "part", "mountpoints": ["/mnt/a"]},
                      {"name": "sdz2", "type": "part", "mountpoints": ["/mnt/b"]}]},
    ]})
    monkeypatch.setattr(disk_panel.subprocess, "run", lambda *a, **k: mock.Mock(stdout=lsblk_json, returncode=0))
    monkeypatch.setattr(disk_panel.QInputDialog, "getItem", staticmethod(lambda parent, title, label, items, *a, **k: (items[0], True)))
    monkeypatch.setattr(disk_panel.shutil, "which", lambda name: None)
    monkeypatch.setattr(disk_panel.privilege, "command_prefix", lambda reason="": ["pkexec"])
    calls = []
    real_start = QProcess.start

    def fake_start(self, program, args=None, *rest):
        calls.append((program, list(args or [])))
        return real_start(self, "/bin/true" if len(calls) == 1 else "/bin/false", [])

    monkeypatch.setattr(QProcess, "start", fake_start)
    refreshed = []
    monkeypatch.setattr(panel, "_refresh_device_list", lambda: refreshed.append(True))
    panel._unmount_device_dialog()
    assert panel._unmount_process is not None
    assert not panel.unmount_btn.isEnabled()
    _pump(lambda: panel._unmount_process is None and refreshed)
    assert [c[0] for c in calls] == ["pkexec", "pkexec"]
    assert sorted(c[1][-1] for c in calls) == ["/dev/sdz1", "/dev/sdz2"]
    assert panel.unmount_btn.isEnabled()
    assert len(panel._unmount_errors) == 1
    assert panel.findChildren(QProcess) == [] or all(
        p.state() == QProcess.ProcessState.NotRunning for p in panel.findChildren(QProcess)
    )


# --------------------------------------------------------------------------- Befund 7: report_html


def test_report_anchor_ids_are_sanitised(tmp_path: Path):
    evil = '"><script>alert(1)</script>'
    report = {"meta": {"hostname": "h"}, "commands": [
        {"id": evil, "command": "x", "output": "a"},
        {"id": 42, "command": "y", "output": "b"},
        {"id": "lscpu", "command": "lscpu", "output": "c"},
    ]}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    html = report_html.generate_html_report(path)
    assert "<script>" not in html
    assert 'href="#sec-0-script-alert-1-script"' in html
    assert 'id="sec-0-script-alert-1-script"' in html
    assert 'id="sec-1-42"' in html
    assert 'id="sec-2-lscpu"' in html
    assert "&quot;&gt;&lt;script&gt;" in html


def test_anchor_id_whitelist():
    assert report_html._anchor_id("a b/c", 3) == "sec-3-a-b-c"
    assert report_html._anchor_id("///", 1) == "sec-1"
    assert report_html._anchor_id("ok_ID-1", 0) == "sec-0-ok_ID-1"


# --------------------------------------------------------------------------- Befund 9: dynamische ComboBoxen


def test_dynamic_combo_is_not_overwritten_on_language_switch(monkeypatch):
    from PySide6.QtWidgets import QComboBox, QWidget

    manager = i18n.LanguageManager()
    monkeypatch.setattr(manager, "_language", "de")
    root = QWidget()
    static = QComboBox(root)
    static.addItem("Sequenzielles Lesen")
    dynamic = QComboBox(root)
    dynamic.setProperty("hardwaretest_dynamic", True)
    dynamic.addItem("/dev/nvme0n1 – alt")
    manager.retranslate_widget_tree(root)
    dynamic.clear()
    dynamic.addItem("/dev/nvme1n1 – neu")
    monkeypatch.setattr(manager, "_language", "en")
    manager.retranslate_widget_tree(root)
    assert static.itemText(0) == "Sequential read"
    assert dynamic.itemText(0) == "/dev/nvme1n1 – neu"


def test_combo_cache_invalidated_when_item_count_changes(monkeypatch):
    from PySide6.QtWidgets import QComboBox

    manager = i18n.LanguageManager()
    monkeypatch.setattr(manager, "_language", "en")
    combo = QComboBox()
    combo.addItem("Sequenzielles Lesen")
    manager.retranslate_widget_tree(combo)
    combo.clear()
    combo.addItem("Sequenzielles Lesen")
    combo.addItem("Zufälliges Lesen")
    manager.retranslate_widget_tree(combo)
    assert [combo.itemText(i) for i in range(2)] == ["Sequential read", "Random read"]


def test_nvme_device_box_marked_dynamic(monkeypatch):
    monkeypatch.setattr(nvme_panel, "nvme_available", lambda: False)
    panel = nvme_panel.NvmePanel()
    assert panel.device_box.property("hardwaretest_dynamic") is True


def test_hidden_proliant_tab_is_inserted_before_system_when_raid_is_requested():
    from PySide6.QtWidgets import QTabWidget, QWidget

    window = _bare_window([])
    window._tabs = QTabWidget()
    window.proliant_panel = QWidget()
    window.proliant_panel.show_raid_tab = mock.Mock()
    window.system_panel = QWidget()
    window._retranslate = mock.Mock()
    window._tabs.addTab(QWidget(), "Gesamttest")
    window._tabs.addTab(window.system_panel, "System")
    assert window._tabs.indexOf(window.proliant_panel) < 0

    window._show_raid_tab()

    assert window._tabs.indexOf(window.proliant_panel) == 1
    assert window._tabs.tabText(1) == "ProLiant Info"
    assert window._tabs.currentWidget() is window.proliant_panel
    window.proliant_panel.show_raid_tab.assert_called_once()
    window._retranslate.assert_called_once()

    window._show_raid_tab()
    assert window._tabs.count() == 3


def test_main_window_adds_proliant_tab_only_on_hpe_servers():
    import inspect

    source = inspect.getsource(mw.MainWindow.__init__)
    assert 'if ipmi.is_proliant():\n            self._tabs.addTab(self.proliant_panel, "ProLiant Info")' in source
