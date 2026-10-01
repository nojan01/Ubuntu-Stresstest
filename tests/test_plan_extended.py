import csv
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from hardwaretest.core import test_plan
from hardwaretest.core.nvme import NvmeDevice, NvmeHealth
from hardwaretest.core.test_runner import TestResult
from hardwaretest.tests.fio_runner import FioNvmeFullReadRunner
from hardwaretest.core.test_runner import TestParameters, TestExecutionError
from hardwaretest.ui.i18n import language_manager
from hardwaretest.ui.widgets.test_plan_panel import TestPlanPanel


_APP = QApplication.instance() or QApplication([])
DRIVE = NvmeDevice("/dev/nvme0n1", "/dev/nvme0", "Model", "SERIAL", "FW", 4096)


def executor(tmp_path, **overrides):
    config = dict(output_dir=tmp_path, cpu_enabled=False, ram_enabled=False,
                  nvme_enabled=True, nvidia_enabled=False)
    config.update(overrides)
    return test_plan.TestPlanExecutor(test_plan.TestPlanConfig(**config))


def test_repeated_plan_aggregates_and_streams_one_csv_row_per_step(tmp_path):
    plan = executor(tmp_path, rounds=50)
    call = []

    def step():
        call.append(1)
        return plan._instant_result("nvme", "failed" if len(call) == 2 else "passed",
                                    f"round-{len(call)}", [f"detail-{len(call)}"])

    plan._run_nvme = step
    result = plan.execute()
    assert result.status == "failed"
    assert result.completed_rounds == 50
    assert len(result.steps) == 1
    assert "FEHLGESCHLAGEN: 1" in result.steps[0].summary
    assert "BESTANDEN: 49" in result.steps[0].summary
    assert "detail-2" in result.steps[0].details
    assert "detail-50" in result.steps[0].details
    assert "detail-20" not in result.steps[0].details
    with result.csv_report.open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 50 and rows[-1]["round"] == "50"
    assert result.html_report.stat().st_size < 10_000


def test_first_failure_stops_all_remaining_rounds(tmp_path):
    plan = executor(tmp_path, rounds=100, stop_on_error=True)
    plan._run_nvme = lambda: plan._instant_result("nvme", "failed", "failed")
    result = plan.execute()
    assert result.status == "failed"
    assert result.completed_rounds == 1
    assert len(result.csv_report.read_text().splitlines()) == 2


def test_exception_stops_leftover_runner_before_next_round(tmp_path):
    plan = executor(tmp_path, rounds=2)
    stopped = []
    starts = []

    def step():
        starts.append(len(stopped))
        plan._active_runner = SimpleNamespace(is_running=lambda: True,
                                              stop=lambda **kw: stopped.append(kw))
        raise RuntimeError("unexpected")

    plan._run_nvme = step
    result = plan.execute()
    assert starts == [0, 1] and len(stopped) == 2
    assert result.status == "failed"


def test_failure_mid_round_is_not_a_completed_round(tmp_path):
    plan = executor(tmp_path, rounds=100, stop_on_error=True, ping_enabled=True)
    plan._run_nvme = lambda: plan._instant_result("nvme", "failed", "failed")
    result = plan.execute()
    assert result.completed_rounds == 0
    assert len(result.steps) == 1


def test_cancel_retains_partial_results_and_reports(tmp_path):
    plan = executor(tmp_path, rounds=100)

    def step():
        plan.cancel()
        return plan._instant_result("nvme", "cancelled", "cancelled")

    plan._run_nvme = step
    result = plan.execute()
    assert result.status == "cancelled" and result.completed_rounds == 0
    assert result.text_report.exists() and result.csv_report.exists()


@pytest.mark.parametrize("rounds", [0, -1, 10001])
def test_invalid_rounds_are_rejected(tmp_path, rounds):
    with pytest.raises(ValueError):
        executor(tmp_path, rounds=rounds).execute()


def test_full_read_step_order_and_explicit_drive_selection(tmp_path):
    plan = executor(tmp_path, nvme_read_enabled=True, ping_enabled=True)
    assert plan.selected_step_keys() == ["nvme", "nvme_read", "ping"]
    assert plan._run_nvme_read().status in ("failed", "skipped")


@pytest.mark.parametrize("change", [None, {"serial": "OTHER"}, {"size_bytes": 8192}, {"model": "OTHER"}])
def test_selected_identity_cannot_be_replaced(tmp_path, change):
    plan = executor(tmp_path, nvme_devices=(DRIVE,))
    detected = [] if change is None else [replace(DRIVE, **change)]
    with pytest.raises(RuntimeError):
        plan._validated_nvme_devices(detected)


def test_selected_health_only_checks_selected_controller(tmp_path, monkeypatch):
    plan = executor(tmp_path, nvme_devices=(DRIVE,))
    other = replace(DRIVE, namespace_path="/dev/nvme1n1", controller_path="/dev/nvme1", serial="OTHER")
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/bin/fake")
    monkeypatch.setattr(test_plan, "list_nvme_devices", lambda: [DRIVE, other])
    reads = []
    monkeypatch.setattr(test_plan, "read_nvme_smart",
                        lambda controller, _: reads.append(controller) or NvmeHealth(controller, media_errors=0))
    assert plan._run_nvme().status == "passed"
    assert reads == ["/dev/nvme0"]


@pytest.mark.parametrize("after,expected", [(0, "passed"), (1, "failed"), (None, "failed")])
def test_full_read_compares_smart_and_records_identity(tmp_path, monkeypatch, after, expected):
    plan = executor(tmp_path, nvme_devices=(DRIVE,), nvme_read_enabled=True, language="en")
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/bin/fake")
    monkeypatch.setattr(test_plan, "list_nvme_devices", lambda: [DRIVE])
    samples = iter([NvmeHealth("/dev/nvme0", media_errors=0), NvmeHealth("/dev/nvme0", media_errors=after)])
    monkeypatch.setattr(test_plan, "read_nvme_smart", lambda *args: next(samples))
    commands = []
    monkeypatch.setattr(test_plan, "FioNvmeFullReadRunner", lambda *args, **kw: commands.append((args, kw)))
    plan._run_process_step = lambda *a, **kw: plan._instant_result("nvme_read", "passed", "passed")
    result = plan._run_nvme_read()
    assert result.status == expected
    assert any("SERIAL" in line and "FW" in line for line in result.details)
    assert any("media_errors: 0 ->" in line for line in result.details)
    assert commands[0][0][0].duration_seconds == 0
    assert commands[0][0][1] == ["/dev/nvme0n1"]


def test_cancelled_read_is_never_reported_as_passed(tmp_path, monkeypatch):
    plan = executor(tmp_path, nvme_devices=(DRIVE,), nvme_read_enabled=True)
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/bin/fake")
    monkeypatch.setattr(test_plan, "list_nvme_devices", lambda: [DRIVE])
    reads = []
    monkeypatch.setattr(test_plan, "read_nvme_smart", lambda *a: reads.append(1) or NvmeHealth("/dev/nvme0", media_errors=0))
    monkeypatch.setattr(test_plan, "FioNvmeFullReadRunner", lambda *a, **kw: None)
    plan._run_process_step = lambda *a, **kw: plan._instant_result("nvme_read", "cancelled", "cancelled")
    result = plan._run_nvme_read()
    assert result.status == "cancelled" and len(reads) == 1


def test_full_read_rejects_job_injection_and_enforces_readonly():
    with pytest.raises(TestExecutionError):
        FioNvmeFullReadRunner(TestParameters(0), "/dev/nvme0n1\nrw=write", ioengine="libaio")
    runner = FioNvmeFullReadRunner(TestParameters(0), DRIVE.namespace_path, ioengine="libaio")
    try:
        assert "--readonly" in runner.build_command()
        assert "allow_file_create=0" in runner._job_lines()
        assert runner.output_lines.maxlen == 80
        for index in range(1000):
            runner._check_line_for_errors(f"I/O error {index}")
        assert len(runner._collected_errors) == 200
        assert runner._collected_errors[0] == "I/O error 0"
        assert runner._collected_errors[-1] == "I/O error 999"
    finally:
        runner._cleanup_job_file()


def test_process_step_builds_job_only_once_and_emits_progress(tmp_path):
    plan = executor(tmp_path)
    updates = []
    plan._step_progress = updates.append
    running = iter([True, False])
    runner = SimpleNamespace(start=lambda: None, is_running=lambda: next(running), progress=lambda: 0.7,
                             get_result=lambda: TestResult(True, [], 1, 0), output_lines=[])
    result = plan._run_process_step("nvme_read", runner)
    assert result.status == "passed" and updates == [70, 100]


def test_plan_panel_selection_config_and_cancel_confirmation(monkeypatch):
    panel = TestPlanPanel()
    panel._scanned_drives([DRIVE])
    assert panel._selected_drives() == ()
    panel._select_all_drives()
    panel.rounds.setValue(3)
    panel.nvme_read_check.setChecked(True)
    assert panel._config().nvme_devices == (DRIVE,)
    assert panel._config().rounds == 3
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    panel._start()
    assert panel._worker is None
    monkeypatch.setattr(language_manager, "_language", "en")
    language_manager.retranslate_widget_tree(panel)
    panel._retranslate_runtime()
    assert panel.nvme_read_check.text() == "Full NVMe read test (optional)"
    assert panel.step_progress.format() == "Current test step: %p%"
    assert panel.log_view.document().maximumBlockCount() == 1000
    panel.deleteLater()
