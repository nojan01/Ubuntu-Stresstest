"""Separate panels for fio-based file tests and raw-device reads."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar, List, Optional, Tuple

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
	QComboBox,
	QDialog,
	QDialogButtonBox,
	QFileDialog,
	QFormLayout,
	QGroupBox,
	QHBoxLayout,
	QLabel,
	QListWidget,
	QListWidgetItem,
	QLineEdit,
	QMessageBox,
	QProgressBar,
	QPushButton,
	QSpinBox,
	QTextEdit,
	QVBoxLayout,
	QWidget,
)

from hardwaretest.core.system_info import (
	SystemInfo,
	format_smart_summary,
	read_hpe_raid_info,
	read_smart_info,
	read_system_info,
)
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters
from hardwaretest.tests.fio_runner import (
	FioDestructiveRunner,
	FioDeviceSweepRunner,
	FioRunner,
)
from hardwaretest.ui.utils import launch_command_in_terminal, build_klog_command, build_mcelog_command


@dataclass
class BlockDevice:
	name: str
	path: str
	size_bytes: int
	display_size: str
	model: str
	read_only: bool
	removable: bool


SystemInfoProvider = Callable[[], SystemInfo]


def _format_size(size_bytes: int) -> str:
	if size_bytes <= 0:
		return "unbekannt"
	units = ["B", "KB", "MB", "GB", "TB", "PB"]
	value = float(size_bytes)
	idx = 0
	while value >= 1024 and idx < len(units) - 1:
		value /= 1024
		idx += 1
	return f"{value:.1f} {units[idx]}"


def _show_info_dialog(parent: QWidget, title: str, content: str) -> None:
	"""Zeigt einen scrollbaren Text-Dialog mit Monospace-Ausgabe."""
	dlg = QDialog(parent)
	dlg.setWindowTitle(title)
	dlg.resize(720, 520)
	text_view = QTextEdit(dlg)
	text_view.setReadOnly(True)
	text_view.setPlainText(content)
	text_view.setStyleSheet("font-family: monospace; font-size: 11px;")
	btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
	btn_box.rejected.connect(dlg.close)
	layout = QVBoxLayout(dlg)
	layout.addWidget(text_view)
	layout.addWidget(btn_box)
	dlg.exec()


class DeviceSelectionMixin:
	"""Shared helpers for panels that operate on block devices."""

	def _init_device_selection(self) -> None:
		self._devices: List[BlockDevice] = []

	def _refresh_device_list(self) -> None:
		devices = self._scan_block_devices()
		self._devices = devices
		self.device_list.blockSignals(True)
		self.device_list.clear()
		for dev in devices:
			item = QListWidgetItem(self._format_device_entry(dev))
			item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
			item.setCheckState(Qt.CheckState.Checked)
			self.device_list.addItem(item)
		self.device_list.blockSignals(False)
		if devices:
			self.device_status.setText(f"{len(devices)} Datenträger gefunden.")
		else:
			self.device_status.setText("Keine geeigneten Datenträger gefunden.")
		self._update_device_controls_enabled()

	def _scan_block_devices(self) -> List[BlockDevice]:
		try:
			result = subprocess.run(
				["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,MODEL,RO,RM"],
				check=True,
				capture_output=True,
				text=True,
			)
		except FileNotFoundError:
			self._append_log("lsblk nicht gefunden. Bitte util-linux installieren.")
			return []
		except subprocess.CalledProcessError as exc:
			stderr = (exc.stderr or "").strip()
			self._append_log(f"lsblk fehlgeschlagen: {stderr or exc}")
			return []
		try:
			data = json.loads(result.stdout or "{}")
		except json.JSONDecodeError as exc:
			self._append_log(f"lsblk Ausgabe unverständlich: {exc}")
			return []
		devices: List[BlockDevice] = []
		for node in data.get("blockdevices", []) or []:
			self._collect_block_devices(node, devices)
		return devices

	def _collect_block_devices(self, node: dict, devices: List[BlockDevice]) -> None:
		node_type = (node.get("type") or "").lower()
		name = node.get("name") or ""
		if node_type == "disk" and name and not name.startswith(("loop", "ram", "sr")):
			size_raw = node.get("size") or "0"
			try:
				size_bytes = int(size_raw)
			except ValueError:
				size_bytes = 0
			devices.append(
				BlockDevice(
					name=name,
					path=f"/dev/{name}",
					size_bytes=size_bytes,
					display_size=_format_size(size_bytes),
					model=(node.get("model") or "").strip() or "unbekannt",
					read_only=str(node.get("ro", "0")) == "1",
					removable=str(node.get("rm", "0")) == "1",
				)
			)
		for child in node.get("children", []) or []:
			self._collect_block_devices(child, devices)

	def _format_device_entry(self, dev: BlockDevice) -> str:
		flags = []
		if dev.read_only:
			flags.append("RO")
		if dev.removable:
			flags.append("removable")
		flag_text = f" ({', '.join(flags)})" if flags else ""
		return f"{dev.path} – {dev.display_size} – {dev.model}{flag_text}"

	def _set_all_devices_checked(self, checked: bool) -> None:
		state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
		for idx in range(self.device_list.count()):
			item = self.device_list.item(idx)
			item.setCheckState(state)
		self._update_device_controls_enabled()

	def _selected_device_objects(self) -> List[BlockDevice]:
		selected: List[BlockDevice] = []
		for idx in range(self.device_list.count()):
			if idx >= len(self._devices):
				continue
			item = self.device_list.item(idx)
			if item.checkState() == Qt.CheckState.Checked:
				selected.append(self._devices[idx])
		return selected


class FileDiskPanel(QWidget):
	"""Runs fio against a temporary file."""

	log_signal = Signal(str)

	WORKLOADS: ClassVar[List[Tuple[str, str, str]]] = [
		("Seq Read", "read", "Sequenzieller Read-Test"),
		("Seq Write", "write", "Sequenzieller Write-Test"),
		("Rand Read", "randread", "Zufälliger Read-Test"),
		("Rand Write", "randwrite", "Zufälliger Write-Test"),
		("Rand RW", "randrw", "Gemischter Random R/W"),
	]

	BLOCK_SIZES: ClassVar[List[str]] = ["4k", "16k", "64k", "128k", "256k", "512k", "1m", "2m"]

	def __init__(
		self,
		parent: Optional[QWidget] = None,
		system_info_provider: SystemInfoProvider = read_system_info,
	) -> None:
		super().__init__(parent)
		self.system_info_provider = system_info_provider
		self.runner: Optional[BaseTestRunner] = None
		self._system_info = self.system_info_provider()
		self._reserved_core_limit = max(1, self._system_info.reserved_core_limit())

		self.info_label = QLabel("Systemdaten werden ermittelt...")
		self.refresh_btn = QPushButton("Systemwerte aktualisieren")
		self.refresh_btn.clicked.connect(self._refresh_system_info)

		self.duration_hours = QSpinBox()
		self.duration_hours.setRange(0, 240)
		self.duration_hours.setSuffix(" h")
		self.duration_minutes = QSpinBox()
		self.duration_minutes.setRange(0, 59)
		self.duration_minutes.setSuffix(" m")
		self.duration_seconds = QSpinBox()
		self.duration_seconds.setRange(0, 59)
		self.duration_seconds.setSuffix(" s")
		self._set_default_duration(300)

		default_path = Path.home() / "hardwaretest" / "fio-disk-test.dat"
		self.file_path = QLineEdit(str(default_path))
		self.file_path.setClearButtonEnabled(True)
		self.choose_btn = QPushButton("Pfad wählen")
		self.choose_btn.clicked.connect(self._choose_file_path)

		self.workload_box = QComboBox()
		for label, value, tooltip in self.WORKLOADS:
			self.workload_box.addItem(label, userData=value)
			idx = self.workload_box.count() - 1
			self.workload_box.setItemData(idx, tooltip, role=Qt.ItemDataRole.ToolTipRole)
		self.workload_box.setCurrentIndex(0)

		self.block_size_box = QComboBox()
		self.block_size_box.setEditable(True)
		for size in self.BLOCK_SIZES:
			self.block_size_box.addItem(size)
		self.block_size_box.setEditText("128k")

		self.file_size_mb = QSpinBox()
		self.file_size_mb.setRange(128, 8 * 1024 * 1024)
		self.file_size_mb.setValue(4096)
		self.file_size_mb.setSuffix(" MB")

		self.io_depth = QSpinBox()
		self.io_depth.setRange(1, 1024)
		self.io_depth.setValue(32)

		self.num_jobs = QSpinBox()
		self.num_jobs.setRange(1, self._system_info.cpu_cores)
		self.num_jobs.setValue(min(4, self._reserved_core_limit))

		self.progress_label = QLabel("Bereit")
		self.progress_bar = QProgressBar()
		self.progress_bar.setRange(0, 100)
		self.progress_bar.setValue(0)
		self.log_view = QTextEdit()
		self.log_view.setReadOnly(True)

		self.start_btn = QPushButton("fio starten")
		self.stop_btn = QPushButton("Stop")
		self.stop_btn.setEnabled(False)

		self.result_label = QLabel("")
		self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

		self.btop_btn = QPushButton("btop starten")
		self.klog_btn = QPushButton("Kernel-Logs")
		self.mce_btn = QPushButton("MCE-Logs")

		safety_label = QLabel(
			"fio erstellt/löscht ausschließlich die angegebene Datei. Keine bestehenden Daten werden verändert."
		)
		safety_label.setWordWrap(True)

		form = QFormLayout()
		form.addRow("Dauer", self._build_duration_widget())
		form.addRow("Testdatei", self._build_path_widget())
		form.addRow("Workload", self.workload_box)
		form.addRow("Blockgröße", self.block_size_box)
		form.addRow("Dateigröße", self.file_size_mb)
		form.addRow("iodepth", self.io_depth)
		form.addRow("Jobs", self.num_jobs)

		btn_row = QHBoxLayout()
		btn_row.addWidget(self.start_btn)
		btn_row.addWidget(self.stop_btn)
		btn_row.addStretch(1)

		group = QGroupBox("Dateibasierter Test (nicht-destruktiv)")
		group_layout = QVBoxLayout()
		group_layout.addLayout(form)
		group_layout.addWidget(safety_label)
		group_layout.addLayout(btn_row)
		group_layout.addWidget(self.progress_bar)
		group_layout.addWidget(self.progress_label)
		group.setLayout(group_layout)

		monitor_row = QHBoxLayout()
		monitor_row.addWidget(self.btop_btn)
		monitor_row.addWidget(self.klog_btn)
		monitor_row.addWidget(self.mce_btn)
		monitor_row.addStretch(1)

		layout = QVBoxLayout()
		layout.addWidget(self.info_label)
		layout.addWidget(self.refresh_btn)
		layout.addWidget(group)
		layout.addLayout(monitor_row)
		layout.addWidget(self.log_view)
		self.setLayout(layout)

		self.timer = QTimer(self)
		self.timer.setInterval(500)
		self.timer.timeout.connect(self._update_progress)

		self.start_btn.clicked.connect(self.start_test)
		self.stop_btn.clicked.connect(self.stop_test)
		self.btop_btn.clicked.connect(self._launch_btop)
		self.klog_btn.clicked.connect(self._launch_klogs)
		self.mce_btn.clicked.connect(self._launch_mcelog)
		self.log_signal.connect(self._append_log)

		self._refresh_system_info()

	def _build_duration_widget(self) -> QWidget:
		container = QWidget()
		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(4)
		layout.addWidget(self.duration_hours)
		layout.addWidget(self.duration_minutes)
		layout.addWidget(self.duration_seconds)
		container.setLayout(layout)
		return container

	def _build_path_widget(self) -> QWidget:
		container = QWidget()
		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(4)
		layout.addWidget(self.file_path)
		layout.addWidget(self.choose_btn)
		container.setLayout(layout)
		return container

	def start_test(self) -> None:
		if self.runner and self.runner.is_running():
			return
		duration_seconds = self._total_duration_seconds()
		if duration_seconds <= 0:
			self._append_log("Bitte eine Dauer größer als 0 Sekunden auswählen.")
			return
		path = Path(self.file_path.text().strip() or "").expanduser()
		if not path.name:
			self._append_log("Bitte einen gültigen Dateipfad angeben.")
			return
		try:
			path.parent.mkdir(parents=True, exist_ok=True)
		except Exception as exc:  # pragma: no cover - filesystem env specific
			self._append_log(f"Ordner konnte nicht erstellt werden: {exc}")
			return
		if path.exists() and not path.is_file():
			self._append_log("Der angegebene Pfad ist keine Datei.")
			return
		size_mb = self.file_size_mb.value()
		required_bytes = size_mb * 1024 * 1024
		try:
			free_bytes = shutil.disk_usage(str(path.parent)).free
		except FileNotFoundError:
			self._append_log("Zielordner konnte nicht gelesen werden.")
			return
		if required_bytes >= free_bytes:
			self._append_log("Nicht genug freier Speicherplatz für die Testdatei.")
			return
		block_size = self.block_size_box.currentText().strip().lower()
		if not block_size:
			self._append_log("Blockgröße darf nicht leer sein.")
			return
		workload = self.workload_box.currentData()
		usable_jobs = max(1, min(self.num_jobs.value(), self._reserved_core_limit))
		if usable_jobs != self.num_jobs.value():
			self._append_log(
				f"Jobs automatisch auf {usable_jobs} reduziert, damit die GUI responsiv bleibt."
			)
		params = TestParameters(duration_seconds=duration_seconds)
		self.runner = FioRunner(
			params,
			filename=str(path),
			rw=workload,
			block_size=block_size,
			size_mb=size_mb,
			io_depth=self.io_depth.value(),
			num_jobs=usable_jobs,
			log_fn=self._handle_runner_log,
		)
		try:
			self.runner.start()
		except Exception as exc:  # pragma: no cover - runtime safety
			self._append_log(f"Fehler beim Start: {exc}")
			self.runner = None
			return
		self.start_btn.setEnabled(False)
		self.stop_btn.setEnabled(True)
		self.progress_bar.setValue(0)
		self.progress_label.setText("Fortschritt: 0%")
		self.result_label.setText("")
		self.result_label.setStyleSheet("")
		self.timer.start()
		self._append_log("fio (Datei) gestartet...")

	def stop_test(self) -> None:
		if not self.runner:
			return
		self.runner.stop(aborted=True)
		self._append_log("Dateibasierter Test manuell gestoppt.")
		self._on_run_finished(manual=True)

	def _update_progress(self) -> None:
		if not self.runner:
			self.timer.stop()
			return
		progress = int(self.runner.progress() * 100)
		running = self.runner.is_running()
		if running:
			self.progress_bar.setValue(progress)
			self.progress_label.setText(f"Fortschritt: {progress}%")
		else:
			self._on_run_finished()

	def _on_run_finished(self, manual: bool = False) -> None:
		self.timer.stop()
		if manual:
			self.progress_label.setText("Abgebrochen")
			self._show_result(aborted=True)
		else:
			self.progress_label.setText("Test fertig")
			self.progress_bar.setValue(100)
			self._show_result()
		self.start_btn.setEnabled(True)
		self.stop_btn.setEnabled(False)
		self.runner = None

	def _show_result(self, aborted: bool = False) -> None:
		if not self.runner:
			return
		result = self.runner.get_result()
		if result is None:
			return
		if result.passed:
			if aborted:
				self.result_label.setText("✓ ABGEBROCHEN – Bis zum Abbruch keine Fehler erkannt")
			else:
				self.result_label.setText("✓ BESTANDEN – Keine Fehler erkannt")
			self.result_label.setStyleSheet(
				"color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;"
			)
		else:
			if aborted:
				self.result_label.setText(
					f"✗ ABGEBROCHEN – {len(result.errors)} Problem(e) bis zum Abbruch erkannt"
				)
			else:
				self.result_label.setText(
					f"✗ FEHLER – {len(result.errors)} Problem(e) erkannt"
				)
			self.result_label.setStyleSheet(
				"color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;"
			)

	def _append_log(self, text: str) -> None:
		self.log_view.append(text)

	def _handle_runner_log(self, text: str) -> None:
		self.log_signal.emit(text)

	def _refresh_system_info(self) -> None:
		info = self.system_info_provider()
		self._system_info = info
		swap_state = "aktiv" if info.swap_enabled else "deaktiviert"
		reserve_note = "mind. 1 Kern für GUI reserviert"
		if info.physical_cpu_cores > 0:
			core_text = (
				f"Kerne: {info.cpu_cores} logisch / {info.physical_cpu_cores} physisch"
			)
		else:
			core_text = f"Kerne: {info.cpu_cores} logisch"
		self.info_label.setText(
			f"Verfügbar: {info.available_memory_mb} MB | {core_text} ({reserve_note}) | Swap {swap_state}"
		)
		self._reserved_core_limit = max(1, info.reserved_core_limit())
		self.num_jobs.setMaximum(max(1, info.cpu_cores))
		if self.num_jobs.value() > self.num_jobs.maximum():
			self.num_jobs.setValue(self.num_jobs.maximum())

	def _total_duration_seconds(self) -> int:
		return (
			self.duration_hours.value() * 3600
			+ self.duration_minutes.value() * 60
			+ self.duration_seconds.value()
		)

	def _set_default_duration(self, seconds: int) -> None:
		hours = seconds // 3600
		minutes = (seconds % 3600) // 60
		secs = seconds % 60
		self.duration_hours.setValue(hours)
		self.duration_minutes.setValue(minutes)
		self.duration_seconds.setValue(secs)

	def _choose_file_path(self) -> None:
		selected, _ = QFileDialog.getSaveFileName(
			self,
			"Zieldatei für fio",
			self.file_path.text() or str(Path.home()),
		)
		if selected:
			self.file_path.setText(selected)

	def _launch_btop(self) -> None:
		if not launch_command_in_terminal(["btop"], geometry=(110, 44)):
			self._append_log("btop konnte nicht gestartet werden. Bitte Installation prüfen.")

	def _launch_klogs(self) -> None:
		if not launch_command_in_terminal(build_klog_command()):
			self._append_log("Kernel-Logs konnten nicht gestartet werden. Bitte Installation prüfen.")

	def _launch_mcelog(self) -> None:
		if not launch_command_in_terminal(build_mcelog_command()):
			self._append_log("MCE-Logs konnten nicht gestartet werden. Bitte Installation prüfen.")


class DeviceDiskPanel(DeviceSelectionMixin, QWidget):
	"""Runs heuristic fio read sweeps across entire block devices."""

	log_signal = Signal(str)

	def __init__(
		self,
		parent: Optional[QWidget] = None,
		system_info_provider: SystemInfoProvider = read_system_info,
	) -> None:
		super().__init__(parent)
		self.system_info_provider = system_info_provider
		self.runner: Optional[BaseTestRunner] = None
		self._system_info = self.system_info_provider()
		self._init_device_selection()

		self.info_label = QLabel("Systemdaten werden ermittelt...")
		self.refresh_btn = QPushButton("Systemwerte aktualisieren")
		self.refresh_btn.clicked.connect(self._refresh_system_info)

		self.device_status = QLabel("Noch keine Datenträger gescannt.")
		self.device_list = QListWidget()
		self.device_list.setAlternatingRowColors(True)
		self.device_list.setSelectionMode(QListWidget.NoSelection)
		self.device_list.itemChanged.connect(lambda _: self._update_device_controls_enabled())

		self.scan_btn = QPushButton("Datenträger scannen")
		self.scan_btn.clicked.connect(self._refresh_device_list)
		self.select_all_btn = QPushButton("Alle auswählen")
		self.select_all_btn.clicked.connect(lambda: self._set_all_devices_checked(True))
		self.select_none_btn = QPushButton("Alle abwählen")
		self.select_none_btn.clicked.connect(lambda: self._set_all_devices_checked(False))

		self.block_size_box = QComboBox()
		for size in ["512k", "1m", "2m", "4m"]:
			self.block_size_box.addItem(size)
		self.block_size_box.setEditable(True)
		self.block_size_box.setCurrentText("1m")

		self.io_depth = QSpinBox()
		self.io_depth.setRange(1, 2048)
		self.io_depth.setValue(64)

		self.progress_label = QLabel("Bereit (heuristisch)")
		self.progress_bar = QProgressBar()
		self.progress_bar.setRange(0, 100)
		self.progress_bar.setValue(0)
		self.log_view = QTextEdit()
		self.log_view.setReadOnly(True)

		self.start_btn = QPushButton("Rohgeräte-Read starten")
		self.stop_btn = QPushButton("Stop")
		self.stop_btn.setEnabled(False)

		self.result_label = QLabel("")
		self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

		self.btop_btn = QPushButton("btop starten")
		self.klog_btn = QPushButton("Kernel-Logs")
		self.mce_btn = QPushButton("MCE-Logs")
		self.smart_btn = QPushButton("SMART-Info")
		self.raid_btn = QPushButton("HPE RAID-Info")

		device_hint = QLabel(
			"Hinweis: Der Test liest alle Blöcke mit pkexec/fio, schreibt aber nichts. Passwortabfrage möglich."
		)
		device_hint.setWordWrap(True)

		list_controls = QHBoxLayout()
		list_controls.addWidget(self.scan_btn)
		list_controls.addWidget(self.select_all_btn)
		list_controls.addWidget(self.select_none_btn)
		list_controls.addStretch(1)

		form = QFormLayout()
		form.addRow("Blockgröße", self.block_size_box)
		form.addRow("iodepth", self.io_depth)

		btn_row = QHBoxLayout()
		btn_row.addWidget(self.start_btn)
		btn_row.addWidget(self.stop_btn)
		btn_row.addStretch(1)

		group = QGroupBox("Rohgeräte-Lesetest (heuristisch)")
		group_layout = QVBoxLayout()
		group_layout.addWidget(self.device_status)
		group_layout.addLayout(list_controls)
		group_layout.addWidget(self.device_list)
		group_layout.addLayout(form)
		group_layout.addLayout(btn_row)
		group_layout.addWidget(self.result_label)
		group_layout.addWidget(self.progress_bar)
		group_layout.addWidget(self.progress_label)
		group_layout.addWidget(device_hint)
		group.setLayout(group_layout)

		monitor_row = QHBoxLayout()
		monitor_row.addWidget(self.btop_btn)
		monitor_row.addWidget(self.klog_btn)
		monitor_row.addWidget(self.mce_btn)
		monitor_row.addWidget(self.smart_btn)
		monitor_row.addWidget(self.raid_btn)
		monitor_row.addStretch(1)

		layout = QVBoxLayout()
		layout.addWidget(self.info_label)
		layout.addWidget(self.refresh_btn)
		layout.addWidget(group)
		layout.addLayout(monitor_row)
		layout.addWidget(self.log_view)
		self.setLayout(layout)

		self.timer = QTimer(self)
		self.timer.setInterval(500)
		self.timer.timeout.connect(self._update_progress)

		self.start_btn.clicked.connect(self.start_test)
		self.stop_btn.clicked.connect(self.stop_test)
		self.btop_btn.clicked.connect(self._launch_btop)
		self.klog_btn.clicked.connect(self._launch_klogs)
		self.mce_btn.clicked.connect(self._launch_mcelog)
		self.smart_btn.clicked.connect(self._show_smart_info)
		self.raid_btn.clicked.connect(self._show_raid_info)
		self.log_signal.connect(self._append_log)

		self._refresh_system_info()
		self._refresh_device_list()

	def start_test(self) -> None:
		if self.runner and self.runner.is_running():
			return
		selected = self._selected_device_objects()
		if not selected:
			self._append_log("Bitte mindestens einen Datenträger auswählen.")
			return
		paths = [dev.path for dev in selected]
		block_size = self.block_size_box.currentText().strip().lower()
		if not block_size:
			self._append_log("Blockgröße darf nicht leer sein.")
			return
		estimated = self._estimate_runtime_seconds(selected)
		params = TestParameters(duration_seconds=max(1, estimated))
		self.runner = FioDeviceSweepRunner(
			params,
			devices=paths,
			block_size=block_size,
			io_depth=self.io_depth.value(),
			log_fn=self._handle_runner_log,
			use_pkexec=True,
		)
		try:
			self._append_log("pkexec wird gestartet – bitte Autorisierung bestätigen, falls angefordert.")
			self.runner.start()
		except Exception as exc:  # pragma: no cover - runtime safety
			self._append_log(f"Fehler beim Start: {exc}")
			self.runner = None
			return
		joined = ", ".join(paths)
		self._append_log(f"Rohgeräte-Read gestartet auf: {joined}")
		self.start_btn.setEnabled(False)
		self.stop_btn.setEnabled(True)
		self.progress_bar.setValue(0)
		self.progress_label.setText("Heuristischer Fortschritt: 0%")
		self.result_label.setText("")
		self.result_label.setStyleSheet("")
		self.timer.start()

	def stop_test(self) -> None:
		if not self.runner:
			return
		self.runner.stop(aborted=True)
		self._append_log("Rohgeräte-Test manuell gestoppt.")
		self._on_run_finished(manual=True)

	def _update_progress(self) -> None:
		if not self.runner:
			self.timer.stop()
			return
		progress = int(self.runner.progress() * 100)
		running = self.runner.is_running()
		display = min(progress, 99) if running else 100
		self.progress_bar.setValue(display)
		if running:
			suffix = " (läuft)" if display >= 99 else ""
			self.progress_label.setText(f"Heuristischer Fortschritt: {display}%{suffix}")
		else:
			self._on_run_finished()

	def _on_run_finished(self, manual: bool = False) -> None:
		self.timer.stop()
		if manual:
			self.progress_label.setText("Abgebrochen")
			self._show_result(aborted=True)
		else:
			self.progress_label.setText("Rohgeräte-Lesetest fertig (heuristisch)")
			self.progress_bar.setValue(100)
			self._show_result()
		self.start_btn.setEnabled(True)
		self.stop_btn.setEnabled(False)
		self.runner = None
		self._update_device_controls_enabled()

	def _show_result(self, aborted: bool = False) -> None:
		if not self.runner:
			return
		result = self.runner.get_result()
		if result is None:
			return
		if result.passed:
			if aborted:
				self.result_label.setText("✓ ABGEBROCHEN – Bis zum Abbruch alle Blöcke fehlerfrei gelesen")
			else:
				self.result_label.setText("✓ BESTANDEN – Alle Blöcke fehlerfrei gelesen")
			self.result_label.setStyleSheet(
				"color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;"
			)
		else:
			if aborted:
				self.result_label.setText(
					f"✗ ABGEBROCHEN – {len(result.errors)} I/O-Problem(e) bis zum Abbruch erkannt"
				)
			else:
				self.result_label.setText(
					f"✗ FEHLER – {len(result.errors)} I/O-Problem(e) erkannt"
				)
			self.result_label.setStyleSheet(
				"color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;"
			)

	def _append_log(self, text: str) -> None:
		self.log_view.append(text)

	def _handle_runner_log(self, text: str) -> None:
		self.log_signal.emit(text)

	def _refresh_system_info(self) -> None:
		info = self.system_info_provider()
		self._system_info = info
		self.info_label.setText(
			f"Kerne verfügbar: {info.cpu_cores} | RAM frei: {info.available_memory_mb} MB"
		)

	def _update_device_controls_enabled(self) -> None:
		running = bool(self.runner and self.runner.is_running())
		if running:
			self.start_btn.setEnabled(False)
			self.stop_btn.setEnabled(True)
			return
		has_selection = bool(self._selected_device_objects())
		self.start_btn.setEnabled(has_selection)
		self.stop_btn.setEnabled(False)

	def _estimate_runtime_seconds(self, devices: List[BlockDevice]) -> int:
		if not devices:
			return 60
		assumed_throughput = 250 * 1024 * 1024  # 250 MB/s gesamt
		total_bytes = sum(max(1, dev.size_bytes) for dev in devices)
		return max(60, int(total_bytes / assumed_throughput))

	def _launch_btop(self) -> None:
		if not launch_command_in_terminal(["btop"], geometry=(110, 44)):
			self._append_log("btop konnte nicht gestartet werden. Bitte Installation prüfen.")

	def _launch_klogs(self) -> None:
		if not launch_command_in_terminal(build_klog_command()):
			self._append_log("Kernel-Logs konnten nicht gestartet werden. Bitte Installation prüfen.")

	def _launch_mcelog(self) -> None:
		if not launch_command_in_terminal(build_mcelog_command()):
			self._append_log("MCE-Logs konnten nicht gestartet werden. Bitte Installation prüfen.")

	def _show_smart_info(self) -> None:
		"""Zeigt SMART-Daten fuer alle ausgewaehlten Geraete."""
		devices = self._selected_device_objects()
		if not devices:
			devices = self._devices
		if not devices:
			self._append_log("Keine Datenträger vorhanden – bitte zuerst scannen.")
			return
		lines: list[str] = []
		for dev in devices:
			info = read_smart_info(dev.path)
			lines.append(format_smart_summary(info))
			if info.raw_output:
				lines.append("")
				lines.append(info.raw_output)
			lines.append("─" * 60)
		_show_info_dialog(self, "SMART-Informationen", "\n".join(lines))

	def _show_raid_info(self) -> None:
		"""Zeigt HPE SmartArray RAID-Controller-Informationen."""
		info = read_hpe_raid_info()
		if info.error and not info.available:
			_show_info_dialog(self, "HPE RAID-Info", info.error)
			return
		title = f"HPE RAID-Info ({info.tool_name})" if info.tool_name else "HPE RAID-Info"
		content = info.raw_output if info.raw_output else (info.error or "Keine Ausgabe.")
		_show_info_dialog(self, title, content)


class DestructiveDiskPanel(DeviceSelectionMixin, QWidget):
	"""Runs destructive write/read/verify passes across block devices."""

	log_signal = Signal(str)

	def __init__(
		self,
		parent: Optional[QWidget] = None,
		system_info_provider: SystemInfoProvider = read_system_info,
	) -> None:
		super().__init__(parent)
		self.system_info_provider = system_info_provider
		self.runner: Optional[BaseTestRunner] = None
		self._system_info = self.system_info_provider()
		self._init_device_selection()

		self.info_label = QLabel("Systemdaten werden ermittelt...")
		self.refresh_btn = QPushButton("Systemwerte aktualisieren")
		self.refresh_btn.clicked.connect(self._refresh_system_info)

		self.danger_label = QLabel(
			"WARNUNG: Dieser Test überschreibt alle ausgewählten Datenträger vollständig und vergleicht die Daten erneut. Nur auf leeren Laufwerken verwenden!"
		)
		self.danger_label.setWordWrap(True)
		self.danger_label.setStyleSheet("color: #b00020; font-weight: bold;")

		self.device_status = QLabel("Noch keine Datenträger gescannt.")
		self.device_list = QListWidget()
		self.device_list.setAlternatingRowColors(True)
		self.device_list.setSelectionMode(QListWidget.NoSelection)
		self.device_list.itemChanged.connect(lambda _: self._update_device_controls_enabled())

		self.scan_btn = QPushButton("Datenträger scannen")
		self.scan_btn.clicked.connect(self._refresh_device_list)
		self.select_all_btn = QPushButton("Alle auswählen")
		self.select_all_btn.clicked.connect(lambda: self._set_all_devices_checked(True))
		self.select_none_btn = QPushButton("Alle abwählen")
		self.select_none_btn.clicked.connect(lambda: self._set_all_devices_checked(False))

		self.block_size_box = QComboBox()
		for size in ["512k", "1m", "2m", "4m"]:
			self.block_size_box.addItem(size)
		self.block_size_box.setEditable(True)
		self.block_size_box.setCurrentText("1m")

		self.io_depth = QSpinBox()
		self.io_depth.setRange(1, 4096)
		self.io_depth.setValue(64)

		self.passes_box = QSpinBox()
		self.passes_box.setRange(1, 10)
		self.passes_box.setValue(1)
		self.passes_box.setSuffix(" Durchläufe")

		self.progress_label = QLabel("Bereit (destruktiv)")
		self.progress_bar = QProgressBar()
		self.progress_bar.setRange(0, 100)
		self.progress_bar.setValue(0)
		self.log_view = QTextEdit()
		self.log_view.setReadOnly(True)

		self.start_btn = QPushButton("DESTRUKTIVEN Test starten")
		self.stop_btn = QPushButton("Stop")
		self.stop_btn.setEnabled(False)
		danger_style = "background-color: #b00020; color: white; font-weight: bold;"
		self.start_btn.setStyleSheet(danger_style)
		self.stop_btn.setStyleSheet("background-color: #7f0000; color: white; font-weight: bold;")

		self.result_label = QLabel("")
		self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

		self.btop_btn = QPushButton("btop starten")
		self.klog_btn = QPushButton("Kernel-Logs")
		self.mce_btn = QPushButton("MCE-Logs")
		self.smart_btn = QPushButton("SMART-Info")
		self.raid_btn = QPushButton("HPE RAID-Info")

		button_row = QHBoxLayout()
		button_row.addWidget(self.scan_btn)
		button_row.addWidget(self.select_all_btn)
		button_row.addWidget(self.select_none_btn)
		button_row.addStretch(1)

		form = QFormLayout()
		form.addRow("Blockgröße", self.block_size_box)
		form.addRow("iodepth", self.io_depth)
		form.addRow("Schreib-/Lesedurchläufe", self.passes_box)

		ctrl_row = QHBoxLayout()
		ctrl_row.addWidget(self.start_btn)
		ctrl_row.addWidget(self.stop_btn)
		ctrl_row.addStretch(1)

		group = QGroupBox("Destruktiver Schreib-/Lesetest (pkexec + verify)")
		group_layout = QVBoxLayout()
		group_layout.addWidget(self.danger_label)
		group_layout.addWidget(self.device_status)
		group_layout.addLayout(button_row)
		group_layout.addWidget(self.device_list)
		group_layout.addLayout(form)
		group_layout.addLayout(ctrl_row)
		group_layout.addWidget(self.result_label)
		group_layout.addWidget(self.progress_bar)
		group_layout.addWidget(self.progress_label)
		group.setLayout(group_layout)

		monitor_row = QHBoxLayout()
		monitor_row.addWidget(self.btop_btn)
		monitor_row.addWidget(self.klog_btn)
		monitor_row.addWidget(self.mce_btn)
		monitor_row.addWidget(self.smart_btn)
		monitor_row.addWidget(self.raid_btn)
		monitor_row.addStretch(1)

		layout = QVBoxLayout()
		layout.addWidget(self.info_label)
		layout.addWidget(self.refresh_btn)
		layout.addWidget(group)
		layout.addLayout(monitor_row)
		layout.addWidget(self.log_view)
		self.setLayout(layout)

		self.timer = QTimer(self)
		self.timer.setInterval(500)
		self.timer.timeout.connect(self._update_progress)

		self.start_btn.clicked.connect(self.start_test)
		self.stop_btn.clicked.connect(self.stop_test)
		self.btop_btn.clicked.connect(self._launch_btop)
		self.klog_btn.clicked.connect(self._launch_klogs)
		self.mce_btn.clicked.connect(self._launch_mcelog)
		self.smart_btn.clicked.connect(self._show_smart_info)
		self.raid_btn.clicked.connect(self._show_raid_info)
		self.log_signal.connect(self._append_log)

		self._refresh_system_info()
		self._refresh_device_list()

	def start_test(self) -> None:
		if self.runner and self.runner.is_running():
			return
		selected = self._selected_device_objects()
		if not selected:
			self._append_log("Bitte mindestens einen Datenträger auswählen.")
			return
		block_size = self.block_size_box.currentText().strip().lower()
		if not block_size:
			self._append_log("Blockgröße darf nicht leer sein.")
			return
		estimated = self._estimate_runtime_seconds(selected)
		preview = self._build_destructive_preview(
			selected,
			block_size,
			self.io_depth.value(),
			self.passes_box.value(),
			estimated,
		)
		message = QMessageBox(self)
		message.setIcon(QMessageBox.Warning)
		message.setWindowTitle("Destruktiver Test bestätigen")
		message.setText(
			"Dieser Test überschreibt ALLE ausgewählten Datenträger vollständig. Bitte Konfiguration prüfen."
		)
		message.setInformativeText(preview)
		message.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
		message.setDefaultButton(QMessageBox.StandardButton.No)
		if message.exec() != QMessageBox.StandardButton.Yes:
			self._append_log("Destruktiver Test abgebrochen – keine Bestätigung.")
			return
		paths = [dev.path for dev in selected]
		params = TestParameters(duration_seconds=max(1, estimated))
		self.runner = FioDestructiveRunner(
			params,
			devices=paths,
			block_size=block_size,
			io_depth=self.io_depth.value(),
			passes=self.passes_box.value(),
			log_fn=self._handle_runner_log,
			use_pkexec=True,
		)
		try:
			self._append_log("WARNUNG: Destruktiver Test startet jetzt über pkexec – Autorisierung bestätigen!")
			self.runner.start()
		except Exception as exc:  # pragma: no cover - runtime safety
			self._append_log(f"Fehler beim Start: {exc}")
			self.runner = None
			return
		joined = ", ".join(paths)
		self._append_log(f"Destruktiver Schreib-/Lesetest gestartet auf: {joined}")
		self.start_btn.setEnabled(False)
		self.stop_btn.setEnabled(True)
		self.progress_bar.setValue(0)
		self.progress_label.setText("Fortschritt (Write+Verify): 0%")
		self.result_label.setText("")
		self.result_label.setStyleSheet("")
		self.timer.start()

	def stop_test(self) -> None:
		if not self.runner:
			return
		self.runner.stop(aborted=True)
		self._append_log("Destruktiver Test manuell gestoppt.")
		self._on_run_finished(manual=True)

	def _update_progress(self) -> None:
		if not self.runner:
			self.timer.stop()
			return
		progress = int(self.runner.progress() * 100)
		running = self.runner.is_running()
		display = min(progress, 99) if running else 100
		self.progress_bar.setValue(display)
		if running:
			self.progress_label.setText(f"Fortschritt (Write+Verify): {display}%")
		else:
			self._on_run_finished()

	def _on_run_finished(self, manual: bool = False) -> None:
		self.timer.stop()
		if manual:
			self.progress_label.setText("Abgebrochen")
			self._show_result(aborted=True)
		else:
			self.progress_label.setText("Destruktiver Test fertig")
			self.progress_bar.setValue(100)
			self._show_result()
		self.start_btn.setEnabled(True)
		self.stop_btn.setEnabled(False)
		self.runner = None
		self._update_device_controls_enabled()

	def _show_result(self, aborted: bool = False) -> None:
		if not self.runner:
			return
		result = self.runner.get_result()
		if result is None:
			return
		if result.passed:
			if aborted:
				self.result_label.setText("✓ ABGEBROCHEN – Bis zum Abbruch keine Verifikationsfehler")
			else:
				self.result_label.setText("✓ BESTANDEN – Schreib-/Lese-Verifikation fehlerfrei")
			self.result_label.setStyleSheet(
				"color: #44ff44; font-size: 14px; font-weight: bold; padding: 4px;"
			)
		else:
			if aborted:
				self.result_label.setText(
					f"✗ ABGEBROCHEN – {len(result.errors)} Verifikations-/I/O-Problem(e) bis zum Abbruch"
				)
			else:
				self.result_label.setText(
					f"✗ FEHLER – {len(result.errors)} Verifikations-/I/O-Problem(e)"
				)
			self.result_label.setStyleSheet(
				"color: #ff4444; font-size: 14px; font-weight: bold; padding: 4px;"
			)

	def _append_log(self, text: str) -> None:
		self.log_view.append(text)

	def _handle_runner_log(self, text: str) -> None:
		self.log_signal.emit(text)

	def _refresh_system_info(self) -> None:
		info = self.system_info_provider()
		self._system_info = info
		self.info_label.setText(
			f"CPU: {info.cpu_cores} Kerne | RAM frei: {info.available_memory_mb} MB | Swap {'aktiv' if info.swap_enabled else 'deaktiviert'}"
		)

	def _update_device_controls_enabled(self) -> None:
		running = bool(self.runner and self.runner.is_running())
		if running:
			self.start_btn.setEnabled(False)
			self.stop_btn.setEnabled(True)
			return
		has_selection = bool(self._selected_device_objects())
		self.start_btn.setEnabled(has_selection)
		self.stop_btn.setEnabled(False)

	def _estimate_runtime_seconds(self, devices: List[BlockDevice]) -> int:
		if not devices:
			return 60
		total_bytes = sum(max(1, dev.size_bytes) for dev in devices)
		assumed_throughput = 200 * 1024 * 1024  # 200 MB/s pro Pass
		passes = max(1, self.passes_box.value())
		per_pass = max(60, int(total_bytes / assumed_throughput))
		return per_pass * passes * 2  # write + verify

	def _build_destructive_preview(
		self,
		devices: List[BlockDevice],
		block_size: str,
		io_depth: int,
		passes: int,
		estimated_seconds: int,
	) -> str:
		lines = ["Ausgewählte Datenträger:"]
		for dev in devices:
			lines.append(f"  - {dev.path} ({dev.display_size}, {dev.model})")
		lines.extend(
			[
				"",
				f"Blockgröße: {block_size}",
				f"iodepth: {io_depth}",
				f"Durchläufe: {passes}",
				f"Geschätzte Dauer: {self._format_duration(estimated_seconds)}",
			]
		)
		return "\n".join(lines)

	@staticmethod
	def _format_duration(seconds: int) -> str:
		seconds = max(0, int(seconds))
		minutes, sec = divmod(seconds, 60)
		hours, minutes = divmod(minutes, 60)
		parts = []
		if hours:
			parts.append(f"{hours} h")
		if minutes:
			parts.append(f"{minutes} m")
		if sec or not parts:
			parts.append(f"{sec} s")
		return " ".join(parts)

	def _launch_btop(self) -> None:
		if not launch_command_in_terminal(["btop"], geometry=(110, 44)):
			self._append_log("btop konnte nicht gestartet werden. Bitte Installation prüfen.")

	def _launch_klogs(self) -> None:
		if not launch_command_in_terminal(build_klog_command()):
			self._append_log("Kernel-Logs konnten nicht gestartet werden. Bitte Installation prüfen.")

	def _launch_mcelog(self) -> None:
		if not launch_command_in_terminal(build_mcelog_command()):
			self._append_log("MCE-Logs konnten nicht gestartet werden. Bitte Installation prüfen.")

	def _show_smart_info(self) -> None:
		"""Zeigt SMART-Daten fuer alle ausgewaehlten Geraete."""
		devices = self._selected_device_objects()
		if not devices:
			devices = self._devices
		if not devices:
			self._append_log("Keine Datenträger vorhanden – bitte zuerst scannen.")
			return
		lines: list[str] = []
		for dev in devices:
			info = read_smart_info(dev.path)
			lines.append(format_smart_summary(info))
			if info.raw_output:
				lines.append("")
				lines.append(info.raw_output)
			lines.append("─" * 60)
		_show_info_dialog(self, "SMART-Informationen", "\n".join(lines))

	def _show_raid_info(self) -> None:
		"""Zeigt HPE SmartArray RAID-Controller-Informationen."""
		info = read_hpe_raid_info()
		if info.error and not info.available:
			_show_info_dialog(self, "HPE RAID-Info", info.error)
			return
		title = f"HPE RAID-Info ({info.tool_name})" if info.tool_name else "HPE RAID-Info"
		content = info.raw_output if info.raw_output else (info.error or "Keine Ausgabe.")
		_show_info_dialog(self, title, content)