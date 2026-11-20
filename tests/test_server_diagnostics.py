import subprocess

import pytest

from hardwaretest.core.error_snapshot import differences
from hardwaretest.core.gpu_comparison import VideoComparison
from hardwaretest.core.nvme import NvmeSelfTestUnsupported, start_nvme_self_test


def test_oacs_without_selftest_does_not_send_opcode():
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"oacs":"0x8"}', '')
    with pytest.raises(NvmeSelfTestUnsupported):
        start_nvme_self_test('/dev/nvme2n1', 'short', run=run)
    assert calls == [['nvme', 'id-ctrl', '/dev/nvme2', '-o', 'json']]


def test_error_deltas_preserve_unknown_and_reset():
    lines = differences({'mc0/ue_count': 2, 'GPU 1/ECC': 9},
                        {'mc0/ue_count': 5, 'GPU 1/ECC': 0})
    assert 'mc0/ue_count: 2 → 5 (+3)' in lines
    assert 'GPU 1/ECC: Δ n/v (9 → 0)' in lines
    assert 'PCIe AER: n/v' in lines


def test_compare_only_matching_model_and_stream_batch():
    compare = VideoComparison()
    for gpu, fps, case in [('A', 100, 'decode'), ('B', 50, 'decode'), ('C', 12, 'encode')]:
        compare.add({'gpu': gpu, 'streams': [{'case': case, 'fps': fps}],
                     'values': {'width': '16x', 'temperature': '52 C'}})
    rows = list(compare.rows(dict.fromkeys('ABC', 'T4')))
    assert rows[1][-1].endswith('auffällig')
    assert rows[0][-2] == '16.0'
    assert rows[2][-1] == '–'


def test_ilo_drive_inventory_preserves_identity_and_health():
    from hardwaretest.ui.widgets.proliant_panel import ProliantPanel
    drive = {'Id': '1', 'SerialNumber': 'SSD123', 'Model': 'NVMe', '_storage': 'NS204i-u',
             'PhysicalLocation': {'PartLocation': {'ServiceLabel': 'Bay 2'}},
             'Status': {'Health': 'Critical'}}
    _, _, rows = ProliantPanel._raid_rows(None, [drive])
    cells, severity = rows[0]
    assert (cells[0], cells[1], cells[6], severity) == ('NS204i-u', 'Bay 2', 'SSD123', 'error')


def test_port_profile_persistence_and_link_assessment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtCore import QSettings
    from hardwaretest.ui.widgets import network_panel

    def settings(*args):
        return QSettings(str(tmp_path / 'ports.ini'), QSettings.Format.IniFormat)

    monkeypatch.setattr(network_panel, 'QSettings', settings)
    assess = network_panel.NetworkPanel._assessment
    panel = SimpleNamespace(_settings=lambda adapter: network_panel.NetworkPanel._settings(None, adapter))
    adapter = SimpleNamespace(mac_address='02:00:00:00:00:01', carrier=False, speed_mbps=None)
    config = panel._settings(adapter)
    assert assess(panel, adapter) == 'Optional · kein Link'
    config.setValue('role', 1)
    config.setValue('minimum', 25000)
    config.sync()
    assert 'erwarteter Link fehlt' in assess(panel, adapter)
    adapter.carrier = True
    adapter.speed_mbps = 10000
    assert 'unter 25000' in assess(panel, adapter)
    adapter.speed_mbps = 25000
    assert assess(panel, adapter) == 'Link OK'
