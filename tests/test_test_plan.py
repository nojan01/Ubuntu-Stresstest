from pathlib import Path

from hardwaretest.core.test_plan import (
    STATUS_FAILED,
    STATUS_PASSED,
    TestPlanConfig,
    TestPlanExecutor,
    TestPlanResult,
    TestPlanStepResult,
    render_html_report,
    render_text_report,
    write_test_plan_reports,
    _bounded_details,
)


def _result() -> TestPlanResult:
    return TestPlanResult(
        hostname="proliant-test",
        started_at="2026-09-04T10:00:00+02:00",
        finished_at="2026-09-04T10:02:00+02:00",
        status=STATUS_FAILED,
        steps=[
            TestPlanStepResult(
                key="cpu",
                name="CPU-Stresstest",
                status=STATUS_PASSED,
                started_at="2026-09-04T10:00:00+02:00",
                finished_at="2026-09-04T10:01:00+02:00",
                duration_seconds=60.0,
                summary="BESTANDEN | Exit-Code: 0",
                details=["stress-ng: successful run completed"],
            ),
            TestPlanStepResult(
                key="nvme",
                name="NVMe-/Laufwerksgesundheit",
                status=STATUS_FAILED,
                started_at="2026-09-04T10:01:00+02:00",
                finished_at="2026-09-04T10:02:00+02:00",
                duration_seconds=60.0,
                summary="1 Laufwerk mit Fehler",
                details=["Modell <kritisch> & Seriennummer"],
            ),
        ],
    )


def test_selected_plan_steps_follow_configuration(tmp_path: Path):
    config = TestPlanConfig(
        output_dir=tmp_path,
        cpu_enabled=True,
        ram_enabled=False,
        nvme_enabled=True,
        ping_enabled=True,
        iperf_enabled=False,
        nvidia_enabled=False,
        dcgm_enabled=True,
    )
    assert TestPlanExecutor(config).selected_step_keys() == ["cpu", "nvme", "ping", "dcgm"]


def test_text_report_contains_summary_and_raw_details():
    report = render_text_report(_result())
    assert "Hardwaretest – Gesamtprotokoll" in report
    assert "Gesamtergebnis: FEHLGESCHLAGEN" in report
    assert "CPU-Stresstest" in report
    assert "Modell <kritisch> & Seriennummer" in report


def test_html_report_is_self_contained_and_escapes_details():
    report = render_html_report(_result())
    assert "<!doctype html>" in report
    assert "@media print" in report
    assert "Modell &lt;kritisch&gt; &amp; Seriennummer" in report
    assert "class=\"step failed\"" in report


def test_report_writer_creates_text_and_html(tmp_path: Path):
    text_path, html_path = write_test_plan_reports(_result(), tmp_path)
    assert text_path.exists() and text_path.suffix == ".txt"
    assert html_path.exists() and html_path.suffix == ".html"
    assert "proliant-test" in text_path.name
    assert "Gesamtergebnis" in text_path.read_text(encoding="utf-8")


def test_raw_details_are_bounded_at_both_ends():
    lines = [f"line {number}" for number in range(1_000)]
    bounded = _bounded_details(lines, max_lines=20, max_characters=10_000)
    assert len(bounded) == 19
    assert bounded[0] == "line 0"
    assert bounded[-1] == "line 999"
    assert "982 Protokollzeilen" in bounded[9]
