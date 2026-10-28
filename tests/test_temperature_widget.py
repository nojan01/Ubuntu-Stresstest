from PySide6.QtWidgets import QApplication

from hardwaretest.core.system_info import CpuTemperature
from hardwaretest.ui.widgets.temperature_widget import _CollapsibleCoreDetail


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_many_core_temperatures_are_paginated_and_bounded():
    _app()
    detail = _CollapsibleCoreDetail()
    temperatures = [
        CpuTemperature(current=35 + index % 15, label=f"Core {index}", chip_name="coretemp")
        for index in range(130)
    ]

    detail.update_cores(temperatures)
    detail._toggle_btn.setChecked(True)

    assert len(detail._core_labels) == detail.PAGE_SIZE == 24
    assert detail._content.maximumHeight() == 165
    assert not detail._page_widget.isHidden()
    assert "1/6" in detail._page_label.text()

    detail._change_page(1)
    assert "2/6" in detail._page_label.text()
    assert "Core 24" in detail._core_labels[0].text()


def test_repeated_core_names_from_two_sockets_are_not_dropped():
    _app()
    detail = _CollapsibleCoreDetail()
    temperatures = [
        CpuTemperature(current=40, label=f"Core {index}", chip_name="coretemp")
        for index in range(24)
    ] + [
        CpuTemperature(current=45, label=f"Core {index}", chip_name="coretemp")
        for index in range(24)
    ]

    detail.update_cores(temperatures)
    detail._change_page(1)

    assert len(detail._temps) == 48
    assert len(detail._core_labels) == 24
    assert all("45°C" in label.text() for label in detail._core_labels)


def test_page_limit_is_visible_even_on_small_cpu():
    _app()
    detail = _CollapsibleCoreDetail()
    detail.update_cores([
        CpuTemperature(current=40, label=f"Core {index}", chip_name="coretemp")
        for index in range(10)
    ])

    assert "(10)" in detail._toggle_btn.text()
    detail._toggle_btn.setChecked(True)
    assert not detail._page_widget.isHidden()
    assert "1/1" in detail._page_label.text()
    assert "10" in detail._page_label.text()
    assert not detail._previous_btn.isEnabled()
    assert not detail._next_btn.isEnabled()


def test_duplicate_core_names_get_socket_prefix():
    _app()
    detail = _CollapsibleCoreDetail()
    detail.update_cores([
        CpuTemperature(current=40, label=f"Core {index % 2}", chip_name="coretemp")
        for index in range(4)
    ])
    assert detail._display_names() == ["S0 Core 0", "S0 Core 1", "S1 Core 0", "S1 Core 1"]


def test_page_survives_updates_and_resizes():
    _app()
    detail = _CollapsibleCoreDetail()
    temps = [CpuTemperature(current=40, label=f"Core {i}", chip_name="coretemp") for i in range(80)]
    detail.update_cores(temps)
    detail._toggle_btn.setChecked(True)
    detail._fit_page_size(10)  # 1 Zeile = 4 Cores
    for _ in range(5):
        detail._change_page(1)
    assert detail._page_index == 5
    detail._fit_page_size(500)
    detail._fit_page_size(10)
    detail.update_cores(temps)
    assert detail._page_index == 5


def test_all_cores_get_socket_prefix_with_sparse_core_ids():
    # Xeon: Core-IDs lueckenhaft und je Sockel verschieden (z. B. Core 8 nur
    # auf Sockel 0, Core 10 nur auf Sockel 1).
    _app()
    detail = _CollapsibleCoreDetail()
    detail.update_cores([
        CpuTemperature(current=40, label="Core 0", chip_name="coretemp", socket=0),
        CpuTemperature(current=40, label="Core 8", chip_name="coretemp", socket=0),
        CpuTemperature(current=40, label="Core 0", chip_name="coretemp", socket=1),
        CpuTemperature(current=40, label="Core 10", chip_name="coretemp", socket=1),
    ])
    assert detail._display_names() == [
        "S0 Core 0", "S0 Core 8", "S1 Core 0", "S1 Core 10"]


def test_single_socket_keeps_plain_names():
    _app()
    detail = _CollapsibleCoreDetail()
    detail.update_cores([
        CpuTemperature(current=40, label=f"Core {i}", chip_name="coretemp", socket=0)
        for i in range(2)
    ])
    assert detail._display_names() == ["Core 0", "Core 1"]


def test_read_cpu_temperatures_assigns_socket(monkeypatch):
    from types import SimpleNamespace as NS
    from hardwaretest.core import system_info

    def entry(label):
        return NS(label=label, current=40.0, high=80.0, critical=90.0)
    monkeypatch.setattr(system_info.psutil, "sensors_temperatures", lambda: {
        "coretemp": [entry("Package id 0"), entry("Core 0"), entry("Core 8"),
                     entry("Package id 1"), entry("Core 0"), entry("Core 10")],
    })
    temps = system_info.read_cpu_temperatures()
    assert [(t.label, t.socket) for t in temps] == [
        ("Package id 0", 0), ("Core 0", 0), ("Core 8", 0),
        ("Package id 1", 1), ("Core 0", 1), ("Core 10", 1)]
