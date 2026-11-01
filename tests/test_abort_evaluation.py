"""Manuell abgebrochene Tests werden bis zum Abbruch bewertet (GPU, NVIDIA, DCGM)."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from hardwaretest.core.test_runner import TestResult
from hardwaretest.tests.nvidia_runner import GpuStressRunner
from hardwaretest.ui.widgets import gpu_panel, nvidia_panel

_APP = QApplication.instance() or QApplication([])


def _settle(widget) -> None:
    """Scan-Threads enden lassen und deleteLater abarbeiten, bevor Python das Panel freigibt."""
    deadline = time.monotonic() + 5.0
    # Bis die finished-Slots gelaufen sind und deleteLater alle Worker entfernt hat.
    while widget.findChildren(QThread) and time.monotonic() < deadline:
        _APP.sendPostedEvents(None, 0)
        _APP.processEvents()
        time.sleep(0.001)
    for _ in range(3):
        _APP.sendPostedEvents(None, 0)
        _APP.processEvents()


@pytest.fixture
def keep():
    panels: list = []
    yield panels.append
    for panel in panels:
        _settle(panel)
    panels.clear()


def _result(passed: bool, errors=(), seconds: float = 30.0) -> TestResult:
    return TestResult(passed=passed, errors=list(errors), duration_actual=seconds, exit_code=143 if passed else 1)


class _Runner:
    def __init__(self, result):
        self._result = result
        self._ui_log_lines = ["Zyklus 3, 12 GiB geprüft, Fehler 0"]

    def stop(self, aborted=False):
        pass

    def is_running(self):
        return False

    def get_result(self):
        return self._result


class _FakeGpuStress(_Runner, GpuStressRunner):
    """Erbt nur den Typ für die isinstance-Prüfung im NVIDIA-Panel."""

    def __init__(self, result):
        _Runner.__init__(self, result)


class _FakeDcgm(_Runner):
    output_lines = ("dcgmi output",)

    def __init__(self, result, counts):
        super().__init__(result)
        self._counts = counts

    def status_counts(self):
        return self._counts

    def evaluation_error(self):
        return None


def test_vulkan_abort_without_errors_is_green(monkeypatch, keep):
    monkeypatch.setattr(gpu_panel, "list_vulkan_gpus", lambda: [], raising=False)
    panel = gpu_panel.GpuPanel()
    keep(panel)
    panel._runner = _Runner(_result(True, seconds=31.2))
    panel._finish(aborted=True)
    assert "ABGEBROCHEN" in panel.result.text() and "keine Fehler" in panel.result.text() and "31s" in panel.result.text()
    assert "#44ff44" in panel.result.styleSheet()


def test_vulkan_abort_with_errors_is_red(monkeypatch, keep):
    monkeypatch.setattr(gpu_panel, "list_vulkan_gpus", lambda: [], raising=False)
    panel = gpu_panel.GpuPanel()
    keep(panel)
    panel._runner = _Runner(_result(False, ["GPU 0: FEHLER: 3 Speicherfehler"]))
    panel._finish(aborted=True)
    assert "1 Fehlermeldung" in panel.result.text()
    assert "#ff4444" in panel.result.styleSheet()


def test_cuda_abort_is_evaluated(monkeypatch, keep):
    monkeypatch.setattr(nvidia_panel, "list_nvidia_gpus", lambda: [])
    panel = nvidia_panel.NvidiaPanel()
    keep(panel)
    panel._runner = _FakeGpuStress(_result(True))
    panel._finish_diagnostic(aborted=True)
    assert "ABGEBROCHEN" in panel.result.text() and "keine Fehler" in panel.result.text()
    panel._runner = _FakeGpuStress(_result(False, ["FEHLER: Rechenergebnis weicht ab"]))
    panel._finish_diagnostic(aborted=True)
    assert "1 Fehlermeldung" in panel.result.text()


def test_dcgm_abort_uses_partial_counts(monkeypatch, keep):
    monkeypatch.setattr(nvidia_panel, "list_nvidia_gpus", lambda: [])
    panel = nvidia_panel.NvidiaPanel()
    keep(panel)
    panel._runner = _FakeDcgm(_result(True), {"pass": 4, "fail": 0, "warn": 1, "skip": 0})
    panel._finish_diagnostic(aborted=True)
    assert "4 Prüfung(en)" in panel.result.text() and "#44ff44" in panel.result.styleSheet()
    panel._runner = _FakeDcgm(_result(True), {"pass": 2, "fail": 1, "warn": 0, "skip": 0})
    panel._finish_diagnostic(aborted=True)
    assert "1 fehlgeschlagene" in panel.result.text() and "#ff4444" in panel.result.styleSheet()
    panel._runner = _FakeDcgm(_result(True), {"pass": 0, "fail": 0, "warn": 0, "skip": 0})
    panel._finish_diagnostic(aborted=True)
    assert panel.result.text().startswith("?") and "kein auswertbares Teilergebnis" in panel.diag_status.text()
