import subprocess

from hardwaretest.core.nvidia import (
    LinuxNvidiaStatus,
    list_nvidia_gpus,
    parse_cuda_version,
    parse_nvidia_gpu_csv,
)
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.nvidia_runner import DcgmDiagnosticRunner


def test_nvidia_csv_parser_includes_ecc_and_cuda():
    devices = parse_nvidia_gpu_csv(
        "0, GPU-abc, NVIDIA L40S, 570.1, 46068, 1024, 41, 70.5, 350.0, "
        "82, 1980, 1980, 8001, 4, 4, 16, 16\n",
        cuda_version="12.8",
        ecc_csv="0, Enabled, 2, 0\n",
    )
    assert len(devices) == 1
    gpu = devices[0]
    assert gpu.model == "NVIDIA L40S"
    assert gpu.cuda_version == "12.8"
    assert gpu.memory_total_mib == 46068
    assert gpu.ecc_mode == "Enabled"
    assert gpu.ecc_corrected == 2
    assert gpu.ecc_uncorrected == 0
    assert gpu.utilization_gpu_percent == 82
    assert gpu.clock_graphics_mhz == 1980
    assert gpu.clock_sm_mhz == 1980
    assert gpu.clock_memory_mhz == 8001
    assert gpu.pcie_generation_current == 4
    assert gpu.pcie_generation_max == 4
    assert gpu.pcie_width_current == 16
    assert gpu.pcie_width_max == 16


def test_nvidia_csv_parser_accepts_unsupported_telemetry_fields():
    devices = parse_nvidia_gpu_csv(
        "1, GPU-old, NVIDIA Tesla, 535.1, 16384, 0, 35, N/A, N/A, "
        "N/A, N/A, N/A, N/A, N/A, N/A, N/A, N/A\n"
    )
    assert len(devices) == 1
    assert devices[0].power_draw_w is None
    assert devices[0].utilization_gpu_percent is None
    assert devices[0].pcie_width_current is None


def test_cuda_version_parser():
    assert parse_cuda_version("NVIDIA-SMI 570.1  CUDA Version: 12.8") == "12.8"
    assert parse_cuda_version("driver unavailable") == "N/A"


def test_nvidia_discovery_builds_compatible_queries(monkeypatch=None):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command == ["nvidia-smi"]:
            return subprocess.CompletedProcess(command, 0, "CUDA Version: 12.8", "")
        if "ecc.mode.current" in command[1]:
            return subprocess.CompletedProcess(command, 0, "0, Enabled, 0, 0\n", "")
        return subprocess.CompletedProcess(
            command, 0,
            "0, GPU-a, NVIDIA A100, 570.1, 40960, 0, 35, 45, 250, "
            "10, 1410, 1410, 1215, 4, 4, 16, 16\n",
            "",
        )

    devices = list_nvidia_gpus(run=fake_run)
    assert len(devices) == 1
    assert len(commands) == 3
    assert "utilization.gpu" in commands[0][1]
    assert "pcie.link.width.current" in commands[0][1]


def test_dcgm_command_targets_every_selected_gpu(monkeypatch=None):
    runner = DcgmDiagnosticRunner(TestParameters(0), [3, 0, 1], 2)
    # Avoid making the unit test depend on an installed DCGM package.
    import hardwaretest.tests.nvidia_runner as module
    original = module.dcgmi_available
    module.dcgmi_available = lambda: True
    try:
        assert runner.build_command() == [
            "dcgmi", "diag", "--run", "2", "--entity-id", "gpu:0,gpu:1,gpu:3", "--json"
        ]
        assert DcgmDiagnosticRunner(TestParameters(0), [0], 2, skip_p2p=True).build_command() == [
            "dcgmi", "diag", "--run", "2", "--entity-id", "gpu:0", "--parameters", "pcie.test_p2p_on=false", "--json"
        ]
        assert DcgmDiagnosticRunner(TestParameters(0), [0], 1, skip_p2p=True).build_command() == [
            "dcgmi", "diag", "--run", "1", "--entity-id", "gpu:0", "--json"
        ]
    finally:
        module.dcgmi_available = original


def test_dcgm_json_statuses_are_counted():
    runner = DcgmDiagnosticRunner(TestParameters(0), [0], 1)
    runner.output_lines = [
        'prefix {"tests":[{"status":"Pass"},{"status":"Fail"},{"status":"Skip"}]} suffix'
    ]
    assert runner.status_counts() == {"pass": 1, "fail": 1, "warn": 0, "skip": 1}


def test_linux_driver_check_requires_complete_working_stack():
    ready = LinuxNvidiaStatus(
        ("0000:41:00.0",), ("nvidia",), ("nvidia", "nvidia_uvm"),
        ("nvidia0", "nvidiactl"), True, True,
    )
    missing_module = LinuxNvidiaStatus(
        ("0000:41:00.0",), ("nouveau",), (), (), False, False,
    )
    assert ready.hardware_present and ready.driver_ready
    assert missing_module.hardware_present and not missing_module.driver_ready


_DCGM_PAYLOAD = {
    "DCGM Diagnostic": {
        "version": "3.3.9",
        "Driver Version Detected": "570.86",
        "GPU Device IDs": ["2331"],
        "GPU Device Serials": {"0": "1234567890"},
        "test_categories": [
            {"category": "Deployment", "tests": [
                {"name": "software", "results": [
                    {"gpu_id": "0", "status": "PASS", "info": ["Driver OK"]}]},
            ]},
            {"category": "Hardware", "tests": [
                {"name": "GPU Memory", "results": [
                    {"gpu_id": "0", "status": "FAIL",
                     "warnings": [{"warning": "ECC error detected", "error_id": 42}]}]},
                {"name": "Diagnostic", "results": [{"gpu_id": "0", "status": "SKIP"}]},
            ]},
        ],
    }
}


def test_dcgm_report_is_readable_text():
    import json

    runner = DcgmDiagnosticRunner(TestParameters(0), [0], 2)
    runner.output_lines = json.dumps(_DCGM_PAYLOAD, indent=2).splitlines()
    text = "\n".join(runner.report_lines("de"))
    assert text.startswith("DCGM-Diagnose (DCGM 3.3.9)")
    assert "Treiber: 570.86" in text
    assert "Seriennummern: GPU 0: 1234567890" in text
    assert "[Deployment]" in text and "[Hardware]" in text
    assert "✓ software – GPU 0: bestanden" in text
    assert "Info: Driver OK" in text
    assert "✗ GPU Memory – GPU 0: FEHLGESCHLAGEN" in text
    assert "Warnung: ECC error detected (ID 42)" in text
    assert "– Diagnostic – GPU 0: übersprungen" in text
    assert "{" not in text
    english = "\n".join(runner.report_lines("en"))
    assert "GPU Memory – GPU 0: FAILED" in english and "Driver: 570.86" in english


def test_dcgm_report_falls_back_to_raw_output_without_json():
    runner = DcgmDiagnosticRunner(TestParameters(0), [0], 1)
    runner.output_lines = ["Error: unable to establish a connection to the host engine"]
    assert runner.report_lines() == runner.output_lines


def test_dcgm_report_handles_legacy_schema():
    import json

    payload = {"DCGM GPU Diagnostic": {"test_categories": [
        {"category": "Integration", "tests": [
            {"name": "PCIe", "results": [{"gpu_ids": "0,1", "status": "Pass", "warnings": "Low bandwidth"}]}]}]}}
    runner = DcgmDiagnosticRunner(TestParameters(0), [0, 1], 2)
    runner.output_lines = json.dumps(payload).splitlines()
    text = "\n".join(runner.report_lines("de"))
    assert "✓ PCIe – GPU 0,1: bestanden" in text and "Warnung: Low bandwidth" in text


def test_dcgm_report_handles_dcgm4_schema_and_extra_top_level_keys():
    import json

    payload = {
        "DCGM Diagnostic": {
            "Metadata": {"DCGM Version": "4.2.3", "Driver Version Detected": "580.65"},
            "test_categories": [
                {"category": "Integration", "tests": [
                    {"name": "pcie", "test_summary": {"status": "Pass"}, "results": [
                        {"entity_group": "GPU", "entity_group_id": 1, "entity_id": 0,
                         "status": "Pass", "info": ["GPU to Host bandwidth:\t13.53 GB/s"]}]},
                    {"name": "memory", "test_summary": {"status": "Skip"}, "results": []},
                ]},
            ],
        },
        "Other": {"ignored": True},
    }
    runner = DcgmDiagnosticRunner(TestParameters(0), [0], 2)
    runner.output_lines = json.dumps(payload, indent=1).splitlines()
    text = "\n".join(runner.report_lines("de"))
    assert "DCGM-Diagnose (DCGM 4.2.3)" in text and "Treiber: 580.65" in text
    assert "✓ pcie – GPU 0: bestanden" in text
    assert "Info: GPU to Host bandwidth: 13.53 GB/s" in text
    assert "– memory: übersprungen" in text
    assert "{" not in text


def test_dcgm_report_shows_failed_test_summary_and_names_failed_checks():
    """DCGM 4: GPU-Ergebnisse Pass, test_summary Fail -> Fail muss im Klartext erscheinen."""
    import json

    from hardwaretest.tests.nvidia_runner import failed_checks

    payload = {"DCGM Diagnostic": {"test_categories": [
        {"category": "Integration", "tests": [
            {"name": "pcie", "test_summary": {"status": "Fail", "errors": [
                {"error_id": 86, "warning": "P2P write verification failed"}]},
             "results": [{"entity_id": 0, "status": "Pass", "warnings": ["P2P write verification failed"]},
                         {"entity_id": 1, "status": "Pass"}]}]},
        {"category": "Hardware", "tests": [
            {"name": "memory", "test_summary": {"status": "Pass"},
             "results": [{"entity_id": 0, "status": "Pass"}, {"entity_id": 1, "status": "Fail",
                                                                "errors": ["ECC error"]}]}]},
    ]}}
    runner = DcgmDiagnosticRunner(TestParameters(0), [0, 1], 2)
    runner.output_lines = json.dumps(payload, indent=1).splitlines()
    text = "\n".join(runner.report_lines("de"))
    assert "✓ pcie – GPU 0: bestanden" in text
    assert "✗ pcie – Gesamt: FEHLGESCHLAGEN" in text and "Fehler: P2P write verification failed (ID 86)" in text
    assert "✗ memory – GPU 1: FEHLGESCHLAGEN" in text
    assert "memory – Gesamt" not in text
    assert runner.status_counts()["fail"] == 2
    assert failed_checks(runner.parsed_payload()) == ["pcie", "memory (GPU 1)"]


def test_p2p_hint_only_without_skip_and_with_p2p_failure():
    from hardwaretest.ui.widgets.nvidia_panel import NvidiaPanel

    class Runner:
        def __init__(self, skip_p2p, lines):
            self.skip_p2p = skip_p2p
            self.output_lines = lines

    p2p = ['{"error_id": 86, "message": "P2P write verification failed: buffer mismatch"}']
    assert NvidiaPanel._p2p_failure(Runner(False, p2p), "pcie")
    assert not NvidiaPanel._p2p_failure(Runner(True, p2p), "pcie")
    assert not NvidiaPanel._p2p_failure(Runner(False, p2p), "memory (GPU 1)")
    assert not NvidiaPanel._p2p_failure(Runner(False, ['{"error_id": 12}']), "pcie")
    hints = NvidiaPanel._p2p_hint_lines()
    assert any("iommu=pt" in line for line in hints)
    assert any("ForceP2P=1" in line for line in hints)


def test_dcgm_runtime_error_is_reported_as_evaluation_error():
    runner = DcgmDiagnosticRunner(TestParameters(0), [0], 1)
    runner.output_lines = [
        '{ "DCGM Diagnostic": { "runtime_error": "Error: Unable to complete diagnostic for entities gpu:0.'
        ' Return: (-30)\\nInvalid Parameter String: test \'pcie\' has no parameter \'test_p2p\'.\\n",'
        ' "version": "4.7.1" } }'
    ]
    error = runner.evaluation_error()
    assert error is not None and "has no parameter" in error and "\n" not in error
    report = "\n".join(runner.report_lines("de"))
    assert "DCGM-Fehler:" in report and "Unbekanntes Ergebnisformat" not in report


# --- HTML-Bericht / Datenblatt -------------------------------------------------

from hardwaretest.core import nvidia_report  # noqa: E402

_SPECS_CSV = (
    "0, NVIDIA A100-PCIE-40GB, 1234567890, GPU-abc, 92.00.45.00.03, 00000000:17:00.0, 0x20F110DE, 0x145F10DE, "
    "595.10, 8.0, 40960 MiB, Enabled, Enabled, 0, 0, No, 4, 4, 16, 16, 250.00 W, 250.00 W, 250.00 W, 1410 MHz, "
    "1215 MHz, 1410 MHz, 45, [N/A], G001.0000.03.02, G001.0000.03.02, G001.0000.03.02, Enabled, Default\n"
    "1, NVIDIA A100-PCIE-40GB, 1234567891, GPU-def, 92.00.45.00.03, 00000000:65:00.0, 0x20F110DE, 0x145F10DE, "
    "595.10, 8.0, 40960 MiB, Enabled, Enabled, 0, 0, No, 4, 4, 16, 16, 250.00 W, 250.00 W, 250.00 W, 1410 MHz, "
    "1215 MHz, 1410 MHz, 47, [N/A], G001.0000.03.02, G001.0000.03.02, G001.0000.03.02, Enabled, Default\n"
)
_REMAPPED_CSV = "00000000:17:00.0, 0, 0, No, No\n00000000:65:00.0, 2, 1, Yes, No\n"


def _fake_run(stdout_by_arg):
    def run(cmd, **_kwargs):
        for key, out in stdout_by_arg.items():
            if any(key in part for part in cmd):
                return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
    return run


def test_parse_specs_csv_cleans_unsupported_values():
    specs = nvidia_report.parse_specs_csv(_SPECS_CSV)
    assert len(specs) == 2
    assert specs[0]["name"] == "NVIDIA A100-PCIE-40GB"
    assert specs[0]["serial"] == "1234567890"
    assert specs[0]["fan.speed"] == "–"
    assert specs[1]["pci.bus_id"] == "00000000:65:00.0"


def test_merge_remapped_rows_by_bus_id():
    specs = nvidia_report.parse_specs_csv(_SPECS_CSV)
    nvidia_report.merge_remapped_rows(specs, _REMAPPED_CSV)
    assert specs[1]["remapped_rows.correctable"] == "2"
    assert specs[1]["remapped_rows.pending"] == "Yes"
    assert specs[0]["remapped_rows.uncorrectable"] == "0"


def test_collect_gpu_specs_filters_indices_and_survives_missing_tool():
    run = _fake_run({"--query-gpu": _SPECS_CSV, "--query-remapped-rows": _REMAPPED_CSV})
    specs = nvidia_report.collect_gpu_specs([1], run=run)
    assert [s["index"] for s in specs] == ["1"]
    assert specs[0]["remapped_rows.correctable"] == "2"

    def missing(cmd, **_kwargs):
        raise FileNotFoundError("nvidia-smi")

    assert nvidia_report.collect_gpu_specs(run=missing) == []


def test_render_html_contains_specs_results_and_no_json():
    specs = nvidia_report.parse_specs_csv(_SPECS_CSV)
    payload = {
        "DCGM Diagnostic": {
            "test_categories": [
                {"category": "Hardware", "tests": [
                    {"name": "memory", "results": [
                        {"gpu_id": "0", "status": "Pass"},
                        {"gpu_id": "1", "status": "Fail", "warnings": [{"warning": "ECC error"}]},
                    ]},
                ]},
            ],
            "runtime_error": "Unable to complete test",
        }
    }
    html = nvidia_report.render_html(
        specs, "de", payload=payload, title_suffix="DCGM-Diagnose Stufe 2",
        verdict="FEHLGESCHLAGEN", verdict_color="#c62828",
        details=["1 Test fehlgeschlagen"], hints=["Hinweis A"], meta=[("DCGM-Stufe", "2")])
    assert "<html" in html and "DCGM-Diagnose Stufe 2" in html
    assert "NVIDIA A100-PCIE-40GB" in html and "1234567891" in html
    assert "memory" in html and "ECC error" in html and "Unable to complete test" in html
    assert "FEHLGESCHLAGEN" in html and "Hinweis A" in html and "DCGM-Stufe" in html
    assert "test_categories" not in html and '"status"' not in html


def test_render_html_without_payload_is_a_datasheet():
    html = nvidia_report.render_html(nvidia_report.parse_specs_csv(_SPECS_CSV), "en", title_suffix="GPU data sheet")
    assert "GPU data sheet" in html and "GPU-abc" in html


def test_report_path_in_downloads(tmp_path):
    path = nvidia_report.report_path("dcgm-diagnose", downloads=tmp_path)
    assert path.parent == tmp_path
    assert path.name.startswith("dcgm-diagnose-") and path.suffix == ".html"


def test_collect_gpu_specs_drops_unknown_fields_and_reports_them():
    calls = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        query = next((c for c in cmd if c.startswith("--query-gpu=")), "")
        if "inforom.pwr" in query:
            return subprocess.CompletedProcess(cmd, 2, stdout="", stderr='Field "inforom.pwr" is not a valid field to query.\n')
        if query:
            fields = query.split("=", 1)[1].split(",")
            row = ", ".join("00000000:17:00.0" if f == "pci.bus_id" else ("0" if f == "index" else "x") for f in fields)
            return subprocess.CompletedProcess(cmd, 0, stdout=row + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    errors: list[str] = []
    specs = nvidia_report.collect_gpu_specs(run=run, errors=errors)
    assert len(specs) == 1 and specs[0]["index"] == "0"
    assert specs[0]["inforom.pwr"] == "–" and specs[0]["name"] == "x"
    assert any("inforom.pwr" in e for e in errors)
    assert sum(1 for c in calls if any(a.startswith("--query-gpu=") for a in c)) == 2


def test_collect_gpu_specs_reports_other_failures():
    def run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 9, stdout="", stderr="NVIDIA-SMI has failed")

    errors: list[str] = []
    assert nvidia_report.collect_gpu_specs(run=run, errors=errors) == []
    assert errors and "NVIDIA-SMI has failed" in errors[0]
