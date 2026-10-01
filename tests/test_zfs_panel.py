from PySide6.QtWidgets import QApplication, QMessageBox

from hardwaretest.core.zfs import ZfsPool, parse_status
from hardwaretest.ui.widgets.zfs_panel import ZfsPanel


_APP = QApplication.instance() or QApplication([])
POOL = ZfsPool("tank", "12345", "ONLINE", 100, 20, 80)


def status(scan):
    return parse_status(f"pool: tank\nstate: ONLINE\nscan: {scan}\nconfig:\nerrors: No known data errors\n")


def panel_with_pool(tmp_path):
    panel = ZfsPanel()
    panel.directory.setText(str(tmp_path))
    panel.scanned([POOL])
    return panel


def test_cancel_confirmation_never_starts_scrub(monkeypatch, tmp_path):
    panel = panel_with_pool(tmp_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    panel.control("start")
    assert panel._process is None
    assert not panel._monitoring


def test_existing_scrub_monitored_until_finished(tmp_path):
    panel = panel_with_pool(tmp_path)
    panel.show_status(status("scrub in progress since today, 33.0% done"))
    assert panel._monitoring
    assert panel.progress.value() == 33
    assert not panel.start_btn.isEnabled()
    assert panel.stop_btn.isEnabled()
    panel.show_status(status("scrub repaired 0B in 00:10:00 with 0 errors on today"))
    assert not panel._monitoring
    assert panel.progress.value() == 100
    assert panel._html.exists()
    assert len(list(tmp_path.glob("*.csv"))) == 1


def test_detach_does_not_stop_scrub_and_report_not_passed(monkeypatch, tmp_path):
    panel = panel_with_pool(tmp_path)
    panel.show_status(status("scrub in progress since today, 20.0% done"))
    monkeypatch.setattr(panel, "control", lambda *_: (_ for _ in ()).throw(AssertionError("must not stop pool")))
    panel.detach.click()
    assert not panel._monitoring
    assert panel._html.exists()
    assert "kein abschließendes Testergebnis" in panel._html.read_text()


def test_old_scrub_result_does_not_complete_new_monitoring(tmp_path):
    panel = panel_with_pool(tmp_path)
    old = status("scrub repaired 0B in 00:10:00 with 0 errors on yesterday")
    panel._last = old
    panel.begin_monitoring(old.raw)
    panel.show_status(old)
    assert panel._monitoring
    panel.finish_monitoring("incomplete")


def test_paused_scrub_can_resume_after_detach(tmp_path):
    panel = panel_with_pool(tmp_path)
    panel.show_status(status("scrub paused since yesterday"))
    panel.finish_monitoring("detached")
    assert panel.start_btn.isEnabled()


def test_csv_close_error_does_not_leave_monitoring_locked(tmp_path):
    panel = panel_with_pool(tmp_path)
    panel.show_status(status("scrub in progress since today, 20.0% done"))
    panel._csv_file.close()
    class FailedFile:
        def close(self):
            raise OSError("disk full")
    panel._csv_file = FailedFile()
    panel.finish_monitoring("incomplete")
    assert not panel._monitoring
    assert "CSV unvollständig" in panel.status.text()
