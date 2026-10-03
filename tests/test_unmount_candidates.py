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
