"""Swap-Umschaltung in einem Tab aktualisiert die Anzeige der anderen Tabs."""
from PySide6.QtWidgets import QApplication

from hardwaretest.ui.system_events import system_events
from hardwaretest.ui.widgets import memory_fill_panel, test_panel
from hardwaretest.ui.widgets.memory_controller_panel import MemoryControllerPanel
from hardwaretest.ui.widgets.mprime_panel import MprimePanel

_APP = QApplication.instance() or QApplication([])


def test_swap_done_notifies_other_panels(monkeypatch):
    received = []
    listener = lambda: received.append(True)  # noqa: E731
    system_events.swap_changed.connect(listener)
    try:
        for cls in (test_panel.TestPanel, memory_fill_panel.MemoryFillPanel):
            panel = cls()
            monkeypatch.setattr(panel, "_refresh_system_info", lambda *a, **k: None)
            received.clear()
            panel._on_swap_done()
            assert received == [True]
            panel._on_swap_on_done()
            assert received == [True, True]
            panel.deleteLater()
    finally:
        system_events.swap_changed.disconnect(listener)


def test_other_panels_refresh_on_swap_change(monkeypatch):
    panels = [MemoryControllerPanel(), MprimePanel()]
    counts = {}
    for panel in panels:
        monkeypatch.setattr(panel, "_refresh_system_info",
                            lambda *a, _p=panel, **k: counts.__setitem__(id(_p), counts.get(id(_p), 0) + 1))
    system_events.swap_changed.emit()
    assert len(counts) == len(panels)
    for panel in panels:
        panel.deleteLater()
