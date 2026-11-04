"""Berichte der Test-Tabs: nach Testende liegt hwtest-<test>-…html in ~/Downloads."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from hardwaretest.core.system_info import SystemInfo  # noqa: E402
from hardwaretest.core.test_runner import TestResult  # noqa: E402
from hardwaretest.ui.widgets.test_panel import TestPanel  # noqa: E402


@pytest.fixture(autouse=True)
def _app_and_home(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    yield tmp_path


def _system_info():
    return SystemInfo(16 << 30, 12 << 30, False, 8, 4)


class _FakeRunner:
    def __init__(self, result):
        self._result = result
        self._running = True
        self.stopped = False

    def is_running(self):
        return self._running

    def progress(self):
        return 1.0 if not self._running else 0.5

    def get_result(self):
        return self._result

    def stop(self):
        self.stopped = True
        self._running = False


def _finish(panel, result):
    panel.runner = _FakeRunner(result)
    panel._run_params = [("Testmodus", "Kombiniert")]
    panel.runner._running = False
    panel._update_progress()


def test_stress_panel_writes_report_after_finish(tmp_path):
    panel = TestPanel(title="stress-ng", system_info_provider=_system_info)
    panel.log_view.append("stress-ng: info: zeile")
    _finish(panel, TestResult(passed=False, errors=["MCE: bank 3"], duration_actual=12.0, exit_code=2, warnings=["korrigiert"]))
    files = list((tmp_path / "Downloads").glob("hwtest-stress-ng-*.html"))
    assert len(files) == 1
    html = files[0].read_text(encoding="utf-8")
    assert "FEHLGESCHLAGEN" in html and "MCE: bank 3" in html and "korrigiert" in html
    assert "stress-ng: info: zeile" in html and "Kombiniert" in html
    assert panel.report_bar.last_path == files[0]
    assert panel.report_bar.open_btn.isEnabled()


def test_stress_panel_report_on_manual_stop(tmp_path):
    panel = TestPanel(title="stress-ng", system_info_provider=_system_info)
    panel.runner = _FakeRunner(TestResult(passed=True, errors=[], duration_actual=3.0, exit_code=None))
    panel.stop_test()
    files = list((tmp_path / "Downloads").glob("hwtest-stress-ng-*.html"))
    assert len(files) == 1 and "ABGEBROCHEN" in files[0].read_text(encoding="utf-8")


def test_mprime_and_opendcdiag_panels_write_reports(tmp_path, monkeypatch):
    from hardwaretest.ui.widgets.mprime_panel import MprimePanel
    from hardwaretest.ui.widgets.opendcdiag_panel import OpenDcDiagPanel

    mprime = MprimePanel(system_info_provider=_system_info)
    _finish(mprime, TestResult(passed=True, errors=[], duration_actual=60.0, exit_code=0))
    dcdiag = OpenDcDiagPanel()
    _finish(dcdiag, TestResult(passed=True, errors=[], duration_actual=5.0, exit_code=0))
    names = sorted(p.name for p in (tmp_path / "Downloads").glob("hwtest-*.html"))
    assert len(names) == 2
    assert names[0].startswith("hwtest-opendcdiag-") and names[1].startswith("hwtest-prime95-")


def test_memory_panels_write_reports(tmp_path):
    from hardwaretest.ui.widgets.memory_controller_panel import MemoryControllerPanel
    from hardwaretest.ui.widgets.memory_fill_panel import MemoryFillPanel

    mc = MemoryControllerPanel(system_info_provider=_system_info)
    _finish(mc, TestResult(passed=True, errors=[], duration_actual=60.0, exit_code=0))
    fill = MemoryFillPanel(system_info_provider=_system_info)
    _finish(fill, TestResult(passed=False, errors=["Bitfehler bei 0x10"], duration_actual=5.0, exit_code=1))
    names = sorted(p.name for p in (tmp_path / "Downloads").glob("hwtest-*.html"))
    assert len(names) == 2
    assert names[0].startswith("hwtest-ram-fuelltest-") and names[1].startswith("hwtest-speichercontroller-")
