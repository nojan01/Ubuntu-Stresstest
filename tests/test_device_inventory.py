import subprocess
from pathlib import Path

from hardwaretest.core import device_inventory as dev

LSPCI = """Slot:\t0000:00:01.0
Class:\tPCI bridge
Vendor:\tIntel Corporation
Device:\tRoot Port

Slot:\t0000:01:00.0
Class:\tEthernet controller
Vendor:\tIntel Corporation
Device:\tEthernet Controller X710
SVendor:\tHewlett Packard Enterprise

Slot:\t0000:02:00.0
Class:\tRAID bus controller
Vendor:\tBroadcom / LSI
Device:\tMegaRAID

Slot:\t0000:03:00.0
Class:\tVGA compatible controller
Vendor:\tNVIDIA Corporation
Device:\tGA102GL [RTX A6000]
"""

LSBLK = """{"blockdevices": [
 {"name":"sda","model":"MB4000GVYZK  ","vendor":"ATA","serial":"ZC1","rev":"HPG4","size":4000787030016,
  "rota":true,"tran":"sas","log-sec":512,"phy-sec":4096,"type":"disk"},
 {"name":"nvme0n1","model":"SAMSUNG MZQL2","vendor":null,"serial":"S6","rev":"GDC5","size":960197124096,
  "rota":"0","tran":"nvme","log-sec":512,"phy-sec":512,"type":"disk"},
 {"name":"zram0","model":null,"size":8000,"rota":false,"type":"disk"},
 {"name":"loop0","size":8000,"rota":false,"type":"loop"}
]}"""


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n")


def _pci(root: Path, chain: list[str], cls: str, vendor: str, device: str, link=None, driver=None) -> Path:
    """Legt ein Gerät unter devices/pci0000:00/<chain…> an und verlinkt es in bus/pci/devices."""
    real = root / "devices" / "pci0000:00"
    for part in chain:
        real = real / part
    _write(real / "class", cls)
    _write(real / "vendor", vendor)
    _write(real / "device", device)
    if link:
        for name, value in zip(("current_link_speed", "current_link_width", "max_link_speed", "max_link_width"), link, strict=True):
            _write(real / name, value)
    if driver:
        target = root / "bus" / "pci" / "drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (real / "driver").symlink_to(target)
    bus = root / "bus" / "pci" / "devices"
    bus.mkdir(parents=True, exist_ok=True)
    (bus / chain[-1]).symlink_to(real)
    return real


def _sysfs(tmp_path: Path) -> Path:
    root = tmp_path / "sys"
    _pci(root, ["0000:00:01.0"], "0x060400", "0x8086", "0x1234",
         ("8.0 GT/s PCIe", "16", "8.0 GT/s PCIe", "16"), "pcieport")
    nic = _pci(root, ["0000:00:01.0", "0000:01:00.0"], "0x020000", "0x8086", "0x1572",
               ("8.0 GT/s PCIe", "4", "8.0 GT/s PCIe", "8"), "i40e")
    raid = _pci(root, ["0000:00:01.0", "0000:02:00.0"], "0x010400", "0x1000", "0x10e2",
                ("8.0 GT/s PCIe", "8", "16.0 GT/s PCIe", "8"), "megaraid_sas")
    _write(raid / "host0" / "scsi_host" / "host0" / "fw_version", "52.26.0-5179")
    gpu = _pci(root, ["0000:00:01.0", "0000:03:00.0"], "0x030000", "0x10de", "0x2230",
               ("16.0 GT/s PCIe", "16", "16.0 GT/s PCIe", "16"), "amdgpu")
    _write(gpu / "mem_info_vram_total", str(48 * 10**9))
    _write(gpu / "vbios_version", "113-D1")
    net = root / "class" / "net"
    eth = net / "eno1"
    _write(eth / "address", "aa:bb:cc:dd:ee:ff")
    _write(eth / "operstate", "up")
    _write(eth / "speed", "10000")
    (eth / "device").symlink_to(nic)
    _write(net / "lo" / "address", "00:00:00:00:00:00")
    return root


class FakeRun:
    def __init__(self):
        self.commands = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        name = Path(command[0]).name
        out = {"lspci": LSPCI, "lsblk": LSBLK,
               "ethtool": "driver: i40e\nversion: 2.1\nfirmware-version: 9.20 0x8000d95e 1.3429.0\n"}.get(name, "")
        return subprocess.CompletedProcess(command, 0, out, "")


def _inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "_tool", lambda name: f"/usr/bin/{name}" if name != "nvidia-smi" else None)
    return dev.read_device_inventory(_sysfs(tmp_path), FakeRun())


def test_generation_and_link_text():
    assert dev.generation("16.0 GT/s PCIe") == 4
    assert dev.generation("2.5 GT/s") == 1
    assert dev.generation("Unknown") == 0
    assert dev.link_text("32.0 GT/s PCIe", 16) == "Gen5 x16"
    assert dev.link_text("Unknown", 255) == ""


def test_pci_links_states_and_bridge_filter(tmp_path, monkeypatch):
    inventory = _inventory(tmp_path, monkeypatch)
    links = {device.address: device for device in inventory.links}
    assert "0000:00:01.0" not in links  # Root-Port
    nic, raid, gpu = links["0000:01:00.0"], links["0000:02:00.0"], links["0000:03:00.0"]
    assert nic.model == "Intel Corporation Ethernet Controller X710"
    assert nic.driver == "i40e"
    assert nic.link_state == dev.LINK_WIDTH  # x4 statt x8
    # Karte kann Gen4, der Port nur Gen3 → möglich ist Gen3, also OK
    assert raid.possible_generation == 3 and raid.link_state == dev.LINK_OK
    assert gpu.link_state == dev.LINK_OK
    gpu.current_speed = "2.5 GT/s PCIe"
    assert gpu.link_state == dev.LINK_SPEED
    assert "Leerlauf" in dev.link_status_text("de", gpu)


def test_network_graphics_controllers_disks(tmp_path, monkeypatch):
    inventory = _inventory(tmp_path, monkeypatch)
    assert [n.name for n in inventory.network] == ["eno1"]
    eth = inventory.network[0]
    assert (eth.pci, eth.driver, eth.mac, eth.state, eth.speed_mbps) == (
        "0000:01:00.0", "i40e", "aa:bb:cc:dd:ee:ff", "up", 10000)
    assert eth.firmware == "9.20 0x8000d95e 1.3429.0"
    assert dev.speed_text("de", eth.speed_mbps) == "10 Gbit/s"

    gpu = inventory.graphics[0]
    assert (gpu.vram, gpu.vbios) == ("48.0 GB", "113-D1")

    controller = inventory.controllers[0]
    assert (controller.kind, controller.firmware, controller.driver) == ("RAID", "52.26.0-5179", "megaraid_sas")

    assert [d.name for d in inventory.disks] == ["sda", "nvme0n1"]
    sda, nvme = inventory.disks
    assert sda.model == "MB4000GVYZK" and sda.media == "HDD" and dev.sector_text(sda) == "512/4096 B"
    assert nvme.media == "SSD" and dev.disk_type_text(nvme) == "NVME SSD"


def test_nvidia_smi_and_shared_igpu(tmp_path):
    parsed = dev.parse_nvidia_smi("00000000:03:00.0, 49140 MiB, 94.02.5C.00.02\n")
    assert parsed == {"0000:03:00.0": ("49140 MiB", "94.02.5C.00.02")}
    root = tmp_path / "sys"
    _pci(root, ["0000:00:02.0"], "0x030000", "0x8086", "0xa7a0", driver="i915")
    devices = dev.read_pci_devices(root)
    assert dev.read_graphics(root, devices, FakeRun())[0].vram == "shared"
    assert dev.vram_text("en", "shared") == "shared system memory"


def test_device_lines(tmp_path, monkeypatch):
    lines = dev.device_lines("en", _inventory(tmp_path, monkeypatch))
    text = "\n".join(lines)
    assert "PCIe links (current / possible):" in text
    assert "Gen3 x4 / Gen3 x8 – width reduced" in text
    assert "Network eno1:" in text and "10 Gbit/s" in text
    assert "Storage controller 0000:02:00.0: RAID" in text
    assert "Disk sda: MB4000GVYZK · 4.0 TB · SAS HDD" in text


def test_missing_tools_and_sysfs(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "_tool", lambda name: None)
    inventory = dev.read_device_inventory(tmp_path / "nothing", FakeRun())
    assert inventory == dev.DeviceInventory()
    assert dev.device_lines("de", inventory) == []
    assert dev.parse_lsblk("kaputt") == []
