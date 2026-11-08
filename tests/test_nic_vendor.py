"""Hersteller-NIC-Diagnose (niccli, nvmupdate64e/epct, MFT, qaucli, sfupdate): Planung, Index-Auflösung, Bewertung."""

import os
import stat
import subprocess

import pytest

from hardwaretest.core import nic_vendor
from hardwaretest.core.network import NetworkAdapter


def adapter(driver: str, bdf: str = "0000:5e:00.0", name: str = "ens1f0") -> NetworkAdapter:
    return NetworkAdapter(name=name, operstate="up", carrier=True, mac_address="aa:bb:cc:dd:ee:ff", mtu=1500,
                          speed_mbps=25000, duplex="full", driver=driver, bus_address=bdf)


NICCLI_LIST = """
Scrutiny NIC CLI v227.0.130.0

Index  Name                          PCIAddr          Bus Address
-----  ----------------------------  ---------------  -----------
1      BCM57414 NetXtreme-E 25Gb     0000:5e:00.0     5e:00.0
2      BCM57414 NetXtreme-E 25Gb     0000:5e:00.1     5e:00.1
"""

NICCLI_SHOW = """
Device Name        : BCM57414
Firmware           : 227.0.130.0
Package Version    : 227.1.111.0
Link Status        : Up
Speed              : 25 Gbps
"""


class FakeRun:
    """Liefert vorbereitete Ausgaben pro Kommandoende (ohne Pfad) und protokolliert alle Aufrufe."""

    def __init__(self, outputs: dict[str, tuple[int, str]]):
        self.outputs = outputs
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        self.kwargs.append(kwargs)
        key = " ".join([os.path.basename(command[0])] + list(command[1:]))
        rc, out = self.outputs.get(key, (1, f"unknown command {key}"))
        return subprocess.CompletedProcess(command, rc, out if rc == 0 else "", "" if rc == 0 else out)


@pytest.fixture(autouse=True)
def _clear_cache():
    nic_vendor.clear_cache()
    yield
    nic_vendor.clear_cache()


def _make_tool(tmp_path, relative: str) -> str:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_tool_for_adapter_mapping():
    assert nic_vendor.tool_for_adapter(adapter("bnxt_en")).vendor.startswith("Broadcom")
    assert nic_vendor.tool_for_adapter(adapter("ice")).binaries[0] == "nvmupdate64e"
    assert nic_vendor.tool_for_adapter(adapter("qede")).binaries == ("qaucli", "qlnxcli")
    assert nic_vendor.tool_for_adapter(adapter("mlx5_core")).vendor.startswith("Mellanox")
    assert nic_vendor.tool_for_adapter(adapter("sfc")).binaries[0] == "sfupdate"
    assert nic_vendor.tool_for_adapter(adapter("cxgb4")) is None   # Chelsio bewusst nicht integriert
    assert nic_vendor.tool_for_adapter(adapter("r8169")) is None


def test_find_tool_in_opt_tree_skips_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(nic_vendor.shutil, "which", lambda name: None)
    monkeypatch.setattr(nic_vendor, "TOOL_DIRS", ())
    monkeypatch.setattr(nic_vendor, "TOOL_TREES", (str(tmp_path / "opt" / "vendor-tools"),))
    _make_tool(tmp_path, "opt/vendor-tools/nvm/Windows_x64/nvmupdate64e")
    linux = _make_tool(tmp_path, "opt/vendor-tools/nvm/Linux_x64/nvmupdate64e")
    assert nic_vendor.find_tool("nvmupdate64e") == linux
    assert nic_vendor.find_tool("niccli") is None
    assert nic_vendor.available_tool(adapter("ice"))[1] == "nvmupdate64e"
    assert nic_vendor.available_tool(adapter("bnxt_en")) is None
    assert nic_vendor.available_tool(adapter("ice", bdf="virtual")) is None


def test_niccli_index_from_list():
    assert nic_vendor._niccli_index(NICCLI_LIST, "0000:5e:00.1") == "2"
    assert nic_vendor._niccli_index(NICCLI_LIST, "0000:af:00.0") is None


def test_niccli_full_run(monkeypatch):
    monkeypatch.setattr(nic_vendor, "find_tool", lambda name: "/opt/vendor-tools/bin/niccli" if name == "niccli" else None)
    run = FakeRun({
        "niccli --list": (0, NICCLI_LIST),
        "niccli -i 1 show": (0, NICCLI_SHOW),
        "niccli -i 1 show --pkg_ver": (0, "Package Version : 227.1.111.0\n"),
        "niccli -i 1 link -s": (0, "Link Status : Up\nLink Speed : 25G\n"),
        "niccli -i 1 cable -m --show": (0, "Vendor Name : HPE\nModule Type : SFP28\n"),
    })
    result = nic_vendor.run_vendor_diagnostics(adapter("bnxt_en"), run=run)
    assert result.tool == "niccli" and result.error == ""
    assert [step.title for step in result.steps] == ["Adapterliste", "Geräteinfo", "Firmware-Paket", "Link-Status", "Transceiver"]
    assert all(step.ok for step in result.steps)
    assert result.severity == "ok"
    assert run.calls[0][0] == "/opt/vendor-tools/bin/niccli"
    assert ("[Geräteinfo] Firmware", "227.0.130.0") in result.rows
    assert ("[Transceiver] Vendor Name", "HPE") in result.rows
    assert "Firmware 227.0.130.0" in result.summary
    assert "LD_LIBRARY_PATH" not in run.kwargs[0]["env"]


def test_niccli_adapter_not_listed_warns(monkeypatch):
    monkeypatch.setattr(nic_vendor, "find_tool", lambda name: "/usr/local/sbin/niccli" if name == "niccli" else None)
    run = FakeRun({"niccli --list": (0, NICCLI_LIST)})
    result = nic_vendor.run_vendor_diagnostics(adapter("bnxt_en", bdf="0000:af:00.0"), run=run)
    assert len(result.steps) == 1
    assert result.severity == "warn"
    assert any("nicht in 'niccli --list'" in w for w in result.warnings)


def test_failed_step_marks_warning_and_all_failed_is_error(monkeypatch):
    monkeypatch.setattr(nic_vendor, "find_tool", lambda name: "/usr/sbin/sfupdate" if name == "sfupdate" else None)
    run = FakeRun({"sfupdate": (2, "sfupdate: No Solarflare adapters found")})
    result = nic_vendor.run_vendor_diagnostics(adapter("sfc"), run=run)
    assert result.severity == "error"
    assert result.steps[0].error == "sfupdate: No Solarflare adapters found"
    assert result.warnings[0].startswith("Firmware-Inventar (sfupdate)")


def test_epct_follow_up_uses_index(monkeypatch):
    monkeypatch.setattr(nic_vendor, "find_tool", lambda name: "/opt/vendor-tools/epct/epct64e" if name == "epct64e" else None)
    listing = ("NIC  Seg:Bus  Ven-Dev   Mode   Adapter Name\n"
               "===  =======  ========  =====  ============\n"
               " 1   00:094   8086-1593  4x25  Intel(R) Ethernet Network Adapter E810\n")
    run = FakeRun({
        "epct64e -devices": (0, listing),
        "epct64e -nic=1 -get": (0, "Current Port Option : 4x25G\n"),
    })
    result = nic_vendor.run_vendor_diagnostics(adapter("ice", bdf="0000:94:00.0"), run=run)
    assert [step.title for step in result.steps] == ["Geräteliste", "Port-Konfiguration"]
    assert ("[Port-Konfiguration] Current Port Option", "4x25G") in result.rows
    assert result.severity == "ok"


def test_mft_runs_only_installed_tools(monkeypatch):
    present = {"mlxfwmanager": "/usr/bin/mlxfwmanager", "mlxlink": "/usr/bin/mlxlink"}
    monkeypatch.setattr(nic_vendor, "find_tool", lambda name: present.get(name))
    run = FakeRun({
        "mlxfwmanager --query -d 0000:5e:00.0": (0, "Device Type: ConnectX5\nFW 16.35.2000\n  Status: Up to date\n"),
        "mlxlink -d 0000:5e:00.0 --show_module --show_counters": (0, "State : Active\nRaw Physical BER : 1E-255\n"),
    })
    result = nic_vendor.run_vendor_diagnostics(adapter("mlx5_core"), run=run)
    assert [step.command[0] for step in result.steps] == ["mlxfwmanager", "mlxlink"]
    assert result.severity == "ok"
    assert ("[Link-Diagnose] State", "Active") in result.rows


def test_no_tool_installed(monkeypatch):
    monkeypatch.setattr(nic_vendor, "find_tool", lambda name: None)
    result = nic_vendor.run_vendor_diagnostics(adapter("qede"), run=FakeRun({}))
    assert result.severity == "error"
    assert "kein Herstellerwerkzeug" in result.error and "qede" in result.error


def test_failure_words_in_output_become_warnings(monkeypatch):
    monkeypatch.setattr(nic_vendor, "find_tool", lambda name: "/usr/sbin/bnxtnvm" if name == "bnxtnvm" else None)
    run = FakeRun({"bnxtnvm -dev=ens1f0 device_info": (0, "Device: BCM57414\nNVM Version: 1.2.3\nSelftest: FAILED\nErrors: 0\n")})
    result = nic_vendor.run_vendor_diagnostics(adapter("bnxt_en"), run=run)
    assert result.tool == "bnxtnvm"
    assert result.severity == "warn"
    assert result.warnings == ["Geräteinfo: Selftest: FAILED"]
