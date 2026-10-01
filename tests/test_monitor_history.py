import math
import xml.etree.ElementTree as ET

import pytest
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from hardwaretest.core import monitoring
from hardwaretest.core.monitor_history import Bucket, MetricHistory, render_chart, validated_sensor_limits
from hardwaretest.ui import sensor_limits
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.monitoring_panel import MonitoringPanel


_APP = QApplication.instance() or QApplication([])


class Journal:
    available = True

    def poll(self):
        return []


def test_history_stays_bounded_and_preserves_peak_and_weighted_mean():
    history = MetricHistory(capacity=40)
    values = [1000 if index == 127 else 20 + index % 10 for index in range(20_000)]
    for index, value in enumerate(values):
        history.add(index * 10, value)
        assert len(history.buckets) <= 40
    assert min(item.minimum for item in history.buckets) == min(values)
    assert max(item.maximum for item in history.buckets) == 1000
    assert sum(item.count for item in history.buckets) == len(values)
    assert sum(item.total for item in history.buckets) == sum(values)
    assert history.buckets[0].start == 0
    assert history.buckets[-1].end == 199990


def test_missing_data_are_gaps_not_zeroes_and_do_not_join_lines():
    history = MetricHistory()
    for index, value in enumerate([50, None, 60]):
        history.add(index, value)
    svg = render_chart("temperature.cpu", history.buckets, 80, "en")
    assert 'data-mean="1"' not in svg
    assert history.buckets[1].count == 0
    assert history.buckets[1].minimum is None
    merged = history.buckets[0].merge(history.buckets[1])
    assert merged.gap and merged.count == 1 and merged.minimum == 50


def test_svg_is_self_contained_escaped_and_renders_in_qt():
    history = MetricHistory()
    history.add(0, 40)
    history.add(10, 70)
    svg = render_chart('temperature.<svg>&"cpu', history.buckets, 65, "en")
    ET.fromstring(svg)
    assert "&lt;svg&gt;" in svg and "&amp;" in svg
    assert "Limit: 65" in svg and "10.00 s" in svg
    assert "http" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert QSvgRenderer(QByteArray(svg.encode())).isValid()


@pytest.mark.parametrize("limits", [{"memory.percent": 60}, {"temperature.cpu": 121},
                                    {"temperature.cpu": math.nan}, {"temperature.cpu": True},
                                    {"temperature.cpu": "65"}, {"temperature.cpu": 29}])
def test_invalid_sensor_limits_rejected(limits):
    with pytest.raises(ValueError):
        validated_sensor_limits(limits)


def test_individual_limits_override_default_and_report_unmatched_profile(tmp_path):
    values = {"temperature.cpu": 70, "temperature.ssd": 66}
    session = monitoring.MonitorSession(tmp_path, 90, "en", collect=lambda: (values.copy(), []), journal=Journal())
    session.set_sensor_limits({"temperature.cpu": 85, "temperature.ssd": 65, "gpu.missing.temperature": 75})
    snapshot = session.sample()
    assert snapshot["critical"]
    assert any("temperature.ssd: 66 °C >= 65" in alert for alert in snapshot["alerts"])
    assert not any("temperature.cpu" in alert for alert in snapshot["alerts"])
    assert "gpu.missing.temperature" in session.summary()
    with pytest.raises(RuntimeError):
        session.set_sensor_limits({})
    txt, html = session.close()
    assert "Individual temperature limits" in txt.read_text()
    assert "Limit: 65" in html.read_text()


def test_higher_override_is_not_shadowed_by_default_limit(tmp_path):
    session = monitoring.MonitorSession(tmp_path, 60, collect=lambda: ({"temperature.cpu": 70}, []), journal=Journal())
    session.set_sensor_limits({"temperature.cpu": 85})
    assert not session.sample()["critical"]
    session.close()


def test_unconfigured_sensors_still_use_default_limit(tmp_path):
    session = monitoring.MonitorSession(tmp_path, 65, collect=lambda: ({"temperature.cpu": 70}, []), journal=Journal())
    assert session.sample()["critical"]
    session.close()


def test_reports_cap_charts_and_history_but_keep_all_csv_metrics(tmp_path):
    metrics = {f"temperature.sensor{index}": 40 for index in range(270)}
    session = monitoring.MonitorSession(tmp_path, collect=lambda: (metrics, []), journal=Journal())
    session.sample()
    assert len(session.history) == 256 and len(session.stats) == 270
    _, html = session.close()
    assert html.read_text().count("<svg ") == 24
    assert len(session.csv_path.read_text().splitlines()) == 271


def test_sensor_profile_roundtrip_and_cancel_do_not_write(monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(sensor_limits, "profile_settings", lambda: settings)
    sensor_limits.save_sensor_limits({"temperature.cpu": 80})
    assert sensor_limits.load_sensor_limits() == {"temperature.cpu": 80}
    dialog = sensor_limits.SensorLimitsDialog(90, sensor_limits.load_sensor_limits(), scan=False)
    dialog.populate({"temperature.cpu": 40, "temperature.ssd": 35})
    enabled, spin = dialog._rows["temperature.ssd"]
    enabled.setChecked(True)
    spin.setValue(65)
    assert dialog.selected_limits() == {"temperature.cpu": 80, "temperature.ssd": 65}
    dialog.reject()
    assert sensor_limits.load_sensor_limits() == {"temperature.cpu": 80}
    dialog.accept()
    assert sensor_limits.load_sensor_limits() == {"temperature.cpu": 80, "temperature.ssd": 65}
    dialog.deleteLater()


def test_corrupt_profile_does_not_silently_remove_limits(monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.Format.IniFormat)
    settings.setValue("monitoring/temperature_limits", "not json")
    monkeypatch.setattr(sensor_limits, "profile_settings", lambda: settings)
    with pytest.raises(ValueError):
        sensor_limits.load_sensor_limits()


def test_monitor_snapshot_chart_switch_and_language(monkeypatch):
    panel = MonitoringPanel()
    data = {"temperature.cpu": (Bucket(0, 0, 40, 40, 40, 1),),
            "temperature.ssd": (Bucket(0, 0, 30, 30, 30, 1),)}
    panel.show_snapshot(dict(history=data, limits={"temperature.cpu": 80}, metrics={},
                             timestamp="now", samples=1, csv="test.csv", alerts=[],
                             critical=False, alert_count=0))
    assert panel.chart_box.count() == 2
    panel.chart_box.setCurrentText("temperature.ssd")
    assert panel.chart.renderer().isValid()
    monkeypatch.setattr(language_manager, "_language", "en")
    panel.retranslate()
    assert panel.sensor_limits_btn.text() == "Individual temperature limits …"
    assert "Monitoring running" in panel.status.text()
    panel.deleteLater()
