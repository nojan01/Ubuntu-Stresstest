from pathlib import Path
import json

from hardwaretest.ui.widgets.disk_panel import find_unmountable_disks


class Test_FindUnmountableDisks:
	def test_excludes_system_disk_and_unmounted(self):
		data = {"blockdevices": [
			{"name": "sda", "type": "disk", "mountpoints": [None], "children": [
				{"name": "sda1", "type": "part", "mountpoints": [None]},
				{"name": "sda3", "type": "part", "mountpoints": ["/media/u/STICK"]},
			]},
			{"name": "nvme0n1", "type": "disk", "mountpoints": [None], "children": [
				{"name": "nvme0n1p1", "type": "part", "mountpoints": ["/boot/efi"]},
				{"name": "nvme0n1p2", "type": "part", "mountpoints": ["/"]},
			]},
			{"name": "sdb", "type": "disk", "mountpoints": [None]},
		]}
		assert find_unmountable_disks(json.dumps(data)) == [
			("/dev/sda", [("/dev/sda3", "/media/u/STICK")])
		]

	def test_invalid_json(self):
		assert find_unmountable_disks("kaputt") == []


def test_mount_fstype_prefers_longest_mountpoint(tmp_path):
	mounts = tmp_path / "mounts"
	mounts.write_text(
	    "overlay / overlay rw 0 0\n"
	    "tmpfs /tmp tmpfs rw 0 0\n"
	    "/dev/sdb1 /media/user/HW\\040Test ext4 rw 0 0\n"
	    "/dev/sda1 /mnt ext4 rw 0 0\n"
	)
	from hardwaretest.ui.widgets.disk_panel import is_ram_backed, mount_fstype
	assert mount_fstype(Path("/tmp/x"), str(mounts)) == "tmpfs"
	assert mount_fstype(Path("/home/user/test.bin"), str(mounts)) == "overlay"
	assert mount_fstype(Path("/mnt/data/test.bin"), str(mounts)) == "ext4"
	assert mount_fstype(Path("/media/user/HW Test/t.bin"), str(mounts)) == "ext4"
	assert is_ram_backed(Path("/home/user"), str(mounts))
	assert not is_ram_backed(Path("/mnt"), str(mounts))
