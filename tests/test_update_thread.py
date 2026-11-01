"""Lebenszyklus des Update-Threads: früher Doppel-Löschung → SIGSEGV beim Start."""

from __future__ import annotations

import os
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMainWindow

import hardwaretest.ui.main_window as mw


def test_update_check_thread_repeats_without_crash():
    app = QApplication.instance() or QApplication([])
    window = mw.MainWindow.__new__(mw.MainWindow)
    QMainWindow.__init__(window)
    window._update_thread = None
    window._update_manual = False
    results = []
    window._on_update_result = lambda *args: results.append(args)

    def fake_fetch():
        time.sleep(0.002)
        return None

    with mock.patch.object(mw, "fetch_latest_release", side_effect=fake_fetch):
        for _ in range(100):
            window._check_for_updates(manual=False)
            deadline = time.monotonic() + 5
            while window._update_thread is not None and time.monotonic() < deadline:
                app.processEvents()
            assert window._update_thread is None
        QCoreApplication.sendPostedEvents(None, 0)
        app.processEvents()
    assert len(results) == 100
    window.deleteLater()
