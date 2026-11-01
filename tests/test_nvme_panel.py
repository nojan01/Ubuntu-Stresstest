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
    assert "#44ff44" in panel.io_result.styleSheet()


def test_aborted_read_test_with_errors_shows_them(monkeypatch, keep):
    panel, _ = _panel_with_runner(
        monkeypatch,
        dict(passed=False, errors=["verify: bad magic header", "io_u error"], duration_actual=5.0, exit_code=1),
        keep,
    )
    panel._stop_io_test()
    assert "2 Problem(e)" in panel.io_result.text()
    assert "#ff4444" in panel.io_result.styleSheet()
    assert "bad magic header" in panel.io_status.text()
