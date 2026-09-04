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

    assert "24" in detail._toggle_btn.text()
    detail._toggle_btn.setChecked(True)
    assert not detail._page_widget.isHidden()
    assert "1/1" in detail._page_label.text()
    assert "10" in detail._page_label.text()
    assert not detail._previous_btn.isEnabled()
    assert not detail._next_btn.isEnabled()
