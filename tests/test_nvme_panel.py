import pytest
from PySide6.QtWidgets import QApplication

from hardwaretest.core.nvme import NvmeSelfTestStatus
from hardwaretest.ui.widgets import nvme_panel


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def keep():
    panels: list = []
    yield panels.append
    for panel in panels:
        _settle(panel)
    panels.clear()


def test_self_test_log_is_rendered_in_details(monkeypatch):
    monkeypatch.setattr(nvme_panel, "nvme_available", lambda: False)
    panel = nvme_panel.NvmePanel()
    panel.device_box.addItem("/dev/nvme0n1", userData="/dev/nvme0n1")

    panel._show_self_test_status(
        NvmeSelfTestStatus(
            active=False,
            result_code=0,
            raw_log='{"self_test_result": 0}',
        )
    )

    output = panel.details.toPlainText()
    assert "NVMe-Selbsttest-Protokoll" in output
    assert "/dev/nvme0n1" in output
    assert "BESTANDEN" in output
    assert '"self_test_result": 0' in output


class _StoppedRunner:
    """Runner, der wie BaseTestRunner nach stop(aborted=True) ein Ergebnis liefert."""

    authorized = True

    def __init__(self, result):
        self._result = None
        self._pending = result
        self.stopped_with = None

    def stop(self, aborted=False):
        self.stopped_with = aborted
        self._result = self._pending

    def is_running(self):
        return False

    def get_result(self):
        return self._result


def _settle(widget) -> None:
    """Hintergrund-Threads enden lassen und deleteLater abarbeiten, bevor Python das Panel freigibt."""
    import time

    from PySide6.QtCore import QThread

    deadline = time.monotonic() + 5.0
    # Bis die finished-Slots gelaufen sind und deleteLater alle Worker entfernt hat.
    while widget.findChildren(QThread) and time.monotonic() < deadline:
        _APP.sendPostedEvents(None, 0)
        _APP.processEvents()
        time.sleep(0.001)
    for _ in range(3):
        _APP.sendPostedEvents(None, 0)
        _APP.processEvents()


def _panel_with_runner(monkeypatch, result, keep):
    from hardwaretest.core.test_runner import TestResult

    monkeypatch.setattr(nvme_panel, "nvme_available", lambda: False)
    panel = nvme_panel.NvmePanel()
    keep(panel)
    runner = _StoppedRunner(TestResult(**result))
    panel._io_runner = runner
    return panel, runner


def test_aborted_read_test_without_errors_is_rated_clean(monkeypatch, keep):
    panel, runner = _panel_with_runner(
        monkeypatch, dict(passed=True, errors=[], duration_actual=42.4, exit_code=None), keep
    )
    panel._stop_io_test()
    assert runner.stopped_with is True
    text = panel.io_result.text()
    assert "ABGEBROCHEN" in text and "keine Fehler" in text and "42s" in text
    assert "#39d353" in panel.io_result.styleSheet()


def test_aborted_read_test_with_errors_shows_them(monkeypatch, keep):
    panel, _ = _panel_with_runner(
        monkeypatch,
        dict(passed=False, errors=["verify: bad magic header", "io_u error"], duration_actual=5.0, exit_code=1),
        keep,
    )
    panel._stop_io_test()
    assert "2 Problem(e)" in panel.io_result.text()
    assert "#ff6257" in panel.io_result.styleSheet()
    assert "bad magic header" in panel.io_status.text()


def test_unsupported_self_test_shows_inline_notice(monkeypatch, keep):
    monkeypatch.setattr(nvme_panel, "nvme_available", lambda: False)
    panel = nvme_panel.NvmePanel()
    keep(panel)

    def unexpected_popup(*args):
        pytest.fail("Unsupported self-test should be reported inline")

    monkeypatch.setattr(nvme_panel.QMessageBox, "warning", unexpected_popup)
    panel._worker_failed(nvme_panel.NvmeSelfTestUnsupported("Invalid Command Opcode"))
    assert panel.result.text() == "NVMe-Selbsttestfunktion nicht unterstützt"
    assert not panel._self_test_active


@pytest.mark.parametrize("action", ["smart", "self-test"])
def test_nvme_actions_use_checked_controllers_and_continue_after_failure(monkeypatch, keep, action):
    devices = [
        nvme_panel.NvmeDevice(f"/dev/nvme{ctrl}n{ns}", f"/dev/nvme{ctrl}", "", "", "", 0)
        for ctrl, ns in [(0, 1), (0, 2), (1, 1), (2, 1)]
    ]
    monkeypatch.setattr(nvme_panel, "nvme_available", lambda: True)
    monkeypatch.setattr(nvme_panel, "list_nvme_devices", lambda: devices)
    panel = nvme_panel.NvmePanel()
    keep(panel)
    for device in devices[1:]:
        panel._io_drive_checks[device.namespace_path].setChecked(True)
    panel._io_drive_checks[devices[-1].namespace_path].setChecked(False)
    monkeypatch.setattr(panel, "_run_worker",
                        lambda run, callback, **kwargs: callback(run()))
    paths = []

    def run(path, *args, **kwargs):
        paths.append(path)
        if path == "/dev/nvme0n1":
            raise nvme_panel.NvmeSelfTestUnsupported("Invalid Command Opcode")
        if action == "smart":
            return nvme_panel.NvmeHealth(device="/dev/nvme1")

    if action == "smart":
        monkeypatch.setattr(nvme_panel, "read_nvme_smart", run)
        panel._read_health()
        assert "unvollständig" in panel.status.text()
    else:
        monkeypatch.setattr(nvme_panel, "start_nvme_self_test", run)
        monkeypatch.setattr(nvme_panel.QMessageBox, "question",
                            lambda *args: nvme_panel.QMessageBox.StandardButton.Yes)
        reports = []
        monkeypatch.setattr(panel.report_bar, "publish", reports.append)
        panel._start_self_test()
        assert panel._batch_pending == [devices[2]]
        monkeypatch.setattr(nvme_panel, "read_nvme_self_test_status",
                            lambda path, **kw: NvmeSelfTestStatus(active=False, result_code=0, raw_log="test log"))
        panel._poll_self_test_status()
        assert not panel._self_test_active
        assert reports[0].status == nvme_panel.STATUS_WARNING
    assert paths == ["/dev/nvme0n1", "/dev/nvme1n1"]
    assert "/dev/nvme0n1" in panel.details.toPlainText()
