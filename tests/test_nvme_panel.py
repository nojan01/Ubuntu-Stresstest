from PySide6.QtWidgets import QApplication

from hardwaretest.core.nvme import NvmeSelfTestStatus
from hardwaretest.ui.widgets import nvme_panel


_APP = QApplication.instance() or QApplication([])


def test_self_test_log_is_rendered_in_details(monkeypatch):
    monkeypatch.setattr(nvme_panel, "nvme_available", lambda: False)
    panel = nvme_panel.NvmePanel()
    panel.device_box.addItem("/dev/nvme0n1", userData="/dev/nvme0n1")

    panel._show_self_test_status(
        NvmeSelfTestStatus(
            active=False,
            result_code=0,
            raw_log='{"self_test_result": 0}',
        )
    )

    output = panel.details.toPlainText()
    assert "NVMe-Selbsttest-Protokoll" in output
    assert "/dev/nvme0n1" in output
    assert "BESTANDEN" in output
    assert '"self_test_result": 0' in output
