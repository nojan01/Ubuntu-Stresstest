"""CPU topology, temperature sensors and worker counts have different units."""

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from hardwaretest.core.system_info import CpuTemperature, SystemInfo
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets import temperature_widget, test_panel as panel_module


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def panel_factory(monkeypatch):
    monkeypatch.setattr(language_manager, "_language", "de")
    monkeypatch.setattr(temperature_widget.TemperatureWidget, "_refresh", lambda self: None)
    monkeypatch.setattr(temperature_widget.TemperatureWidget, "start_monitoring", lambda self: None)
    panels = []

    def make(logical=64, physical=32, groups=None):
        if groups is None:
            groups = [(i, i + physical) for i in range(physical)]
        monkeypatch.setattr(panel_module.TestPanel, "_physical_core_groups",
                            lambda self, total: groups)
        info = SystemInfo(16 << 30, 12 << 30, False, logical, physical)
        panel = panel_module.TestPanel(system_info_provider=lambda: info)
        panels.append(panel)
        return panel

    yield make
    for panel in panels:
        panel.timer.stop()
        panel.temp_widget._timer.stop()
        panel.close()
        panel.deleteLater()


def test_hyperthreading_reserves_whole_core_and_displays_units(panel_factory):
    panel = panel_factory()
    assert panel.cores.value() == panel.cores.maximum() == 62
    assert panel.reserve_cores.value() == 1
    assert "32 physische Kerne / 64 logische CPUs" in panel.info_label.text()
    assert "GUI-Reserve: 2 logische CPUs" in panel.info_label.text()
    assert any(label.text() == "CPU-Worker (Threads)" for label in panel.findChildren(QLabel))
    assert int(panel._build_cpu_mask(62), 16).bit_count() == 62


@pytest.mark.parametrize("logical,physical,siblings,expected", [
    (32, 32, [(i,) for i in range(32)], 31),
    (1, 1, [(0,)], 1),
    (8, 2, [(0, 1, 2, 3), (4, 5, 6, 7)], 4),
    (4, 0, [(i,) for i in range(4)], 3),
])
def test_worker_limit_without_smt_with_smt4_and_unknown_topology(
    panel_factory, logical, physical, siblings, expected,
):
    panel = panel_factory(logical, physical, siblings)
    assert panel.cores.value() == panel.cores.maximum() == expected
    if not physical:
        assert "physische Kerne unbekannt" in panel.info_label.text()


def test_changing_reserve_restores_maximum_and_updates_actual_count(panel_factory):
    panel = panel_factory()
    panel.reserve_cores.setValue(3)
    assert panel.cores.maximum() == 60
    assert "GUI-Reserve: 4 logische CPUs" in panel.info_label.text()
    panel.reserve_cores.setValue(0)
    assert panel.cores.maximum() == 64
    panel.cores.setValue(64)
    assert "GUI-Reserve: 0 logische CPUs" in panel.info_label.text()
    assert panel._build_cpu_mask(64) is None
    panel.reserve_cores.setValue(1)
    assert panel.cores.value() == panel.cores.maximum() == 62


def test_start_does_not_oversubscribe_after_whole_core_rounding(panel_factory, monkeypatch):
    class FakeRunner:
        def __init__(self, params, **kwargs):
            self.params = params
            self.vm_workers = kwargs["vm_workers"]

        def start(self):
            pass

    monkeypatch.setattr(panel_module, "StressNgRunner", FakeRunner)
    panel = panel_factory()
    panel.cores.setValue(61)
    panel.start_test()
    params = panel.runner.params
    assert params.cpu_cores == panel.runner.vm_workers == panel.cores.value() == 60
    assert int(params.cpu_mask, 16).bit_count() == 60
    assert "GUI-Reserve: 4 logische CPUs" in panel.info_label.text()
    assert "von 61 auf 60 begrenzt" in panel.log_view.toPlainText()


def test_cpu_info_and_form_labels_switch_languages_without_stale_counts(panel_factory, monkeypatch):
    panel = panel_factory()
    for language in ("en", "de", "en"):
        monkeypatch.setattr(language_manager, "_language", language)
        panel._update_cpu_info()
        language_manager.retranslate_widget_tree(panel)
        if language == "en":
            assert "32 physical cores / 64 logical CPUs" in panel.info_label.text()
            assert "GUI reserve: 2 logical CPUs" in panel.info_label.text()
            assert "Number of test workers" in panel.cores.toolTip()
            assert any(label.text() == "CPU workers (threads)" for label in panel.findChildren(QLabel))
        else:
            assert "32 physische Kerne / 64 logische CPUs" in panel.info_label.text()
    panel.reserve_cores.setValue(3)
    assert "GUI reserve: 4 logical CPUs" in panel.info_label.text()


def _dual_socket_temperatures():
    readings = []
    for socket in range(2):
        readings.append(CpuTemperature(current=40, label=f"Package id {socket}",
                                       chip_name="coretemp", socket=socket))
        readings.extend(CpuTemperature(current=38, label=f"Core {i}",
                                        chip_name="coretemp", socket=socket)
                        for i in range(16))
    readings.append(CpuTemperature(current=30, label="acpitz", chip_name="acpitz"))
    return readings


@pytest.mark.parametrize("language,header,toggle", [
    ("de", "Kernsensoren: 32", "Kernsensor-Details anzeigen (32)"),
    ("en", "Core sensors: 32", "Show core sensor details (32)"),
])
def test_temperature_counts_sensors_not_threads_or_packages(monkeypatch, language, header, toggle):
    monkeypatch.setattr(language_manager, "_language", language)
    monkeypatch.setattr(temperature_widget.ipmi, "refresh_sel_counts_async", lambda: None)
    monkeypatch.setattr(temperature_widget, "read_cpu_temperatures", _dual_socket_temperatures)
    widget = temperature_widget.TemperatureWidget()
    try:
        assert header in widget._temp_summary_label.text()
        assert "32 Cores" not in widget._temp_summary_label.text()
        assert widget._core_detail._toggle_btn.text() == toggle
        assert len(widget._core_detail._temps) == 32
        widget._core_detail._toggle_btn.setChecked(True)
        assert "(32)" in widget._core_detail._toggle_btn.text()
        assert len(widget._core_detail._core_labels) <= 24
        language_manager.retranslate_widget_tree(widget)
        assert header in widget._temp_summary_label.text()
    finally:
        widget.close()
        widget.deleteLater()


def test_incomplete_sensors_do_not_claim_total_physical_core_count(monkeypatch):
    monkeypatch.setattr(language_manager, "_language", "de")
    monkeypatch.setattr(temperature_widget.ipmi, "refresh_sel_counts_async", lambda: None)
    readings = _dual_socket_temperatures()[:17]
    monkeypatch.setattr(temperature_widget, "read_cpu_temperatures", lambda: readings)
    widget = temperature_widget.TemperatureWidget()
    try:
        assert "Kernsensoren: 16" in widget._temp_summary_label.text()
        assert "physische Kerne" not in widget._temp_summary_label.text()
    finally:
        widget.close()
        widget.deleteLater()
