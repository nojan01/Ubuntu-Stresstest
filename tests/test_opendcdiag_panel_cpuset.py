from pathlib import Path

from PySide6.QtWidgets import QApplication

from hardwaretest.core import system_info
from hardwaretest.ui.widgets import opendcdiag_panel


def test_cpu_sockets_reads_sysfs(tmp_path):
    for cpu, socket in ((0, 0), (1, 0), (2, 1), (3, 1)):
        topo = tmp_path / f"cpu{cpu}" / "topology"
        topo.mkdir(parents=True)
        (topo / "physical_package_id").write_text(f"{socket}\n")
    assert system_info.cpu_sockets(tmp_path) == [0, 1]


def test_cpu_scope_combo_controls_cpuset(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(opendcdiag_panel, "cpu_sockets", lambda: [0, 1])
    panel = opendcdiag_panel.OpenDcDiagPanel()
    assert panel.selected_cpuset() == ""
    assert not panel.cpuset.isEnabled()
    panel.cpu_scope.setCurrentIndex(2)
    assert panel.selected_cpuset() == "p1"
    panel.cpu_scope.setCurrentIndex(panel.cpu_scope.count() - 1)
    assert panel.cpuset.isEnabled()
    panel.cpuset.setText("0-3")
    assert panel.selected_cpuset() == "0-3"
