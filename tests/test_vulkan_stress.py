import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from hardwaretest.core import gpu_sysfs
from hardwaretest.core.test_runner import TestExecutionError, TestParameters
from hardwaretest.tests import vulkan_runner, vulkan_stress_script
from hardwaretest.tests.vulkan_runner import VulkanStressRunner
from hardwaretest.tests.vulkan_spirv import SPIRV


def test_command_contains_gpus_duration_and_memory():
    runner = VulkanStressRunner(TestParameters(duration_seconds=600), [1, 0, 1], memory_percent=70)
    with mock.patch.object(vulkan_runner, "vulkan_loader_available", return_value=True):
        cmd = runner.build_command()
    assert cmd[:2] == [sys.executable, str(Path(vulkan_runner.__file__).with_name("vulkan_stress_script.py"))]
    assert cmd[-6:] == ["--gpus", "0,1", "--duration", "595", "--memory-percent", "70"]


def test_frozen_worker_uses_system_python_without_bundle_libraries(tmp_path):
    script = tmp_path / "vulkan_stress_script.py"
    script.write_text("")
    which = {"python3": "/usr/bin/python3", "env": "/usr/bin/env"}
    with mock.patch.object(sys, "frozen", True, create=True), \
            mock.patch.object(vulkan_runner, "_script_path", return_value=script), \
            mock.patch.object(vulkan_runner.shutil, "which", side_effect=lambda name, path=None: which[name]):
        cmd = vulkan_runner.worker_command()
    assert cmd == ["/usr/bin/env", "-u", "LD_LIBRARY_PATH", "-u", "PYTHONHOME", "-u", "PYTHONPATH",
                   "/usr/bin/python3", "-I", str(script)]


def test_frozen_worker_falls_back_to_bundle_without_system_python(tmp_path):
    with mock.patch.object(sys, "frozen", True, create=True), \
            mock.patch.object(vulkan_runner, "_script_path", return_value=tmp_path / "fehlt.py"), \
            mock.patch.object(vulkan_runner.shutil, "which", return_value=None):
        cmd = vulkan_runner.worker_command()
    assert cmd == [sys.executable, "--vulkan-stress-worker"]


def test_missing_loader_fails():
    runner = VulkanStressRunner(TestParameters(duration_seconds=60), [0])
    with mock.patch.object(vulkan_runner, "vulkan_loader_available", return_value=False), \
            pytest.raises(TestExecutionError):
        runner.build_command()


def test_error_lines_are_collected():
    runner = VulkanStressRunner(TestParameters(duration_seconds=60), [0])
    runner._check_line_for_errors("GPU 0: Zyklus 3, 12 GiB geprüft, Fehler 0")
    runner._check_line_for_errors("GPU 0: FEHLER: 3 Speicherfehler (Zyklus 4)")
    assert runner._collected_errors == ["GPU 0: FEHLER: 3 Speicherfehler (Zyklus 4)"]


def test_worker_reports_failure():
    with mock.patch.object(vulkan_stress_script, "run_gpu", return_value=2):
        assert vulkan_stress_script.main(["--gpus", "0", "--duration", "1"]) == 1


def test_test_size_limits_integrated_gpus():
    gib = 2**30
    assert vulkan_stress_script.test_size(10 * gib, 12 * gib, False, 80) == 8 * gib
    assert vulkan_stress_script.test_size(20 * gib, 24 * gib, True, 80) == 6 * gib
    assert vulkan_stress_script.test_size(0, 24 * gib, True, 80) == 0


def test_spirv_modules_are_valid():
    for name in ("fill", "verify", "compute"):
        code = base64.b64decode("".join(SPIRV[name]))
        assert len(code) % 4 == 0
        assert int.from_bytes(code[:4], "little") == 0x07230203


def _completed(stdout, returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_list_vulkan_gpus_parses_json():
    gpus = [{"index": 1, "name": "AMD Radeon RX 7600 (RADV NAVI33)", "type": "discrete"}]
    with mock.patch.object(vulkan_runner.subprocess, "run", return_value=_completed(json.dumps(gpus))) as run:
        assert vulkan_runner.list_vulkan_gpus() == gpus
    env = run.call_args.kwargs["env"]
    assert "LD_LIBRARY_PATH" not in env


def test_list_vulkan_gpus_reports_errors():
    with mock.patch.object(vulkan_runner.subprocess, "run",
                           return_value=_completed(json.dumps({"error": "libvulkan.so.1 fehlt"}), 1)), \
            pytest.raises(TestExecutionError, match="libvulkan"):
        vulkan_runner.list_vulkan_gpus()
    with mock.patch.object(vulkan_runner.subprocess, "run", return_value=_completed("Segmentation fault", 139)), \
            pytest.raises(TestExecutionError, match="Segmentation"):
        vulkan_runner.list_vulkan_gpus()


def _card(root: Path, number: int, pci: str, driver: str, vendor: int, device: int, files: dict) -> None:
    pci_dir = root / "devices" / pci
    (root / "drivers" / driver).mkdir(parents=True, exist_ok=True)
    pci_dir.mkdir(parents=True)
    (pci_dir / "driver").symlink_to(root / "drivers" / driver)
    (pci_dir / "vendor").write_text(f"0x{vendor:04x}\n")
    (pci_dir / "device").write_text(f"0x{device:04x}\n")
    card = root / "drm" / f"card{number}"
    card.mkdir(parents=True)
    (card / "device").symlink_to(pci_dir)
    (root / "drm" / f"card{number}-DP-1").mkdir()
    for name, value in files.items():
        target = card / name[5:] if name.startswith("card/") else pci_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)


def test_sysfs_reads_amd_and_intel(tmp_path):
    _card(tmp_path, 1, "0000:03:00.0", "amdgpu", 0x1002, 0x7480, {
        "hwmon/hwmon4/temp1_input": "61000\n",
        "hwmon/hwmon4/power1_average": "152000000\n",
        "hwmon/hwmon4/fan1_input": "1450\n",
        "gpu_busy_percent": "99\n",
        "pp_dpm_sclk": "0: 500Mhz\n1: 2655Mhz *\n",
        "pp_dpm_mclk": "0: 96Mhz\n3: 1124Mhz *\n",
        "mem_info_vram_used": str(6 * 2**30),
        "mem_info_vram_total": str(8 * 2**30),
    })
    _card(tmp_path, 0, "0000:00:02.0", "i915", 0x8086, 0xA7A0, {"card/gt_act_freq_mhz": "1300\n"})
    gpus = gpu_sysfs.list_drm_gpus(tmp_path / "drm")
    assert [gpu.card for gpu in gpus] == ["card0", "card1"]
    intel, amd = gpus
    assert (intel.driver, intel.clock_mhz, intel.temperature_c) == ("i915", 1300, None)
    assert amd.pci == "0000:03:00.0"
    assert (amd.temperature_c, amd.power_w, amd.fan_rpm, amd.busy_percent) == (61.0, 152.0, 1450, 99)
    assert (amd.clock_mhz, amd.memory_clock_mhz, amd.vram_used, amd.vram_total) == (2655, 1124, 6 * 2**30, 8 * 2**30)

    vulkan = [{"index": 0, "vendor_id": 0x1002, "device_id": 0x7480},
              {"index": 1, "vendor_id": 0x8086, "device_id": 0xA7A0},
              {"index": 2, "vendor_id": 0x10DE, "device_id": 0x2684}]
    assert gpu_sysfs.match_drm(vulkan, gpus) == {0: amd, 1: intel}


def test_sysfs_without_drm_directory(tmp_path):
    assert gpu_sysfs.list_drm_gpus(tmp_path / "fehlt") == []


def test_gpu_panel_lists_gpus_and_telemetry():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from hardwaretest.ui.widgets.gpu_panel import GpuPanel

    app = QApplication.instance() or QApplication([])
    card = gpu_sysfs.DrmGpu("card1", "0000:03:00.0", "amdgpu", 0x1002, 0x7480, temperature_c=55.0,
                            busy_percent=97, vram_used=2**30, vram_total=8 * 2**30)
    with mock.patch("hardwaretest.ui.widgets.gpu_panel.list_drm_gpus", return_value=[card]):
        panel = GpuPanel()
        panel._show_gpus([{"index": 0, "name": "AMD Radeon RX 7600", "vendor": "AMD", "type": "discrete",
                           "vendor_id": 0x1002, "device_id": 0x7480, "memory": 8 * 2**30, "api": "1.4.318"}])
    assert panel._selected_indices() == [0]
    assert panel.stress_start_btn.isEnabled()
    assert "AMD Radeon RX 7600 (AMD, dediziert, 8.0 GiB, Vulkan 1.4.318)" in panel._gpu_checks[0].text()
    text = panel.telemetry.toPlainText()
    assert "[amdgpu 0000:03:00.0]" in text and "55.0 °C" in text and "1.0/8.0 GiB" in text
    panel._show_gpus([])
    assert not panel.stress_start_btn.isEnabled()
    panel.deleteLater()
    app.processEvents()
