"""Tab „System“: Prozessor, Caches, Takt je Kern, Mainboard/BIOS und Speichermodule."""

from __future__ import annotations

import os
import shutil

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from hardwaretest.core import hardware_inventory as inv
from hardwaretest.ui.i18n import language_manager


def _t(de: str, en: str) -> str:
    return en if language_manager.language == "en" else de


def _mhz(value) -> str:
    return f"{value:.0f} MHz" if value else "–"


def _item(text) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text not in (None, "") else "–")
    item.setToolTip(item.text())
    return item


def _fill(table: QTableWidget, headers: list[str], rows: list[list]) -> None:
    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            table.setItem(row, column, _item(value))
    table.resizeColumnsToContents()
    height = table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(len(rows))) + 4
    table.setFixedHeight(min(max(height, 60), 420))


def _table() -> QTableWidget:
    table = QTableWidget()
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class SystemPanel(QWidget):
    CLOCK_INTERVAL_MS = 2000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._packages: list[inv.CpuPackage] = []
        self._caches: list[inv.CacheLevel] = []
        self._board = inv.BoardInfo()
        self._memory: inv.MemoryInventory | None = None
        self._memory_error = ""
        self._process: QProcess | None = None

        outer = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton()
        self.refresh_button.clicked.connect(self.refresh)
        self.password_button = QPushButton()
        self.password_button.clicked.connect(self._read_with_password)
        self.copy_button = QPushButton()
        self.copy_button.clicked.connect(self._copy)
        for button in (self.refresh_button, self.password_button, self.copy_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.cpu_group = QGroupBox()
        cpu_layout = QVBoxLayout(self.cpu_group)
        self.cpu_table = _table()
        self.flags_label = QLabel()
        self.flags_label.setWordWrap(True)
        cpu_layout.addWidget(self.cpu_table)
        cpu_layout.addWidget(self.flags_label)
        layout.addWidget(self.cpu_group)

        self.cache_group = QGroupBox()
        cache_layout = QVBoxLayout(self.cache_group)
        self.cache_table = _table()
        cache_layout.addWidget(self.cache_table)
        layout.addWidget(self.cache_group)

        self.clock_group = QGroupBox()
        clock_layout = QVBoxLayout(self.clock_group)
        self.clock_table = _table()
        clock_layout.addWidget(self.clock_table)
        layout.addWidget(self.clock_group)

        self.board_group = QGroupBox()
        board_layout = QVBoxLayout(self.board_group)
        self.board_table = _table()
        board_layout.addWidget(self.board_table)
        layout.addWidget(self.board_group)

        self.memory_group = QGroupBox()
        memory_layout = QVBoxLayout(self.memory_group)
        self.memory_summary = QLabel()
        self.memory_summary.setWordWrap(True)
        self.memory_table = _table()
        memory_layout.addWidget(self.memory_summary)
        memory_layout.addWidget(self.memory_table)
        layout.addWidget(self.memory_group)
        layout.addStretch(1)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(self.CLOCK_INTERVAL_MS)
        self._clock_timer.timeout.connect(self._update_clocks)

        language_manager.language_changed.connect(self.retranslate)
        self.refresh()

    # ------------------------------------------------------------------ Daten
    def refresh(self) -> None:
        self._packages = inv.read_cpu_packages()
        self._caches = inv.read_caches()
        self._board = inv.read_board_info()
        self._memory, self._memory_error = inv.read_memory_inventory()
        self.retranslate()

    def _read_with_password(self) -> None:
        pkexec = shutil.which("pkexec")
        if not pkexec or self._process is not None:
            return
        self._process = QProcess(self)
        self._process.finished.connect(self._password_finished)
        self.password_button.setEnabled(False)
        self._process.start(pkexec, inv.dmidecode_command())

    def _password_finished(self, code: int, _status) -> None:
        process, self._process = self._process, None
        output = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        error = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
        process.deleteLater()
        if code == 0 and output:
            self._memory, self._memory_error = inv.parse_dmidecode(output), ""
        else:
            self._memory_error = error or _t("Abgebrochen", "Cancelled")
        self.retranslate()

    def text_lines(self) -> list[str]:
        return inv.inventory_lines(
            language_manager.language, self._packages, self._caches, self._board, self._memory, self._memory_error,
        )

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(self.text_lines()))

    # ------------------------------------------------------------------ Takt
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_clocks()
        self._clock_timer.start()

    def hideEvent(self, event) -> None:
        self._clock_timer.stop()
        super().hideEvent(event)

    def _update_clocks(self) -> None:
        clocks = inv.read_cpu_clocks(self._packages)
        hybrid = any(clock.core_type for clock in clocks)
        headers = ["CPU", _t("Sockel", "Socket"), _t("Kern", "Core")]
        headers += [_t("Typ", "Type")] if hybrid else []
        headers += [_t("Aktuell", "Current"), "Min", "Max"]
        rows = []
        for clock in clocks:
            row = [clock.cpu, clock.socket, clock.core] + ([clock.core_type] if hybrid else [])
            rows.append([*row, _mhz(clock.current_mhz), _mhz(clock.min_mhz), _mhz(clock.max_mhz)])
        if self.clock_table.rowCount() == len(rows) and self.clock_table.columnCount() == len(headers):
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    self.clock_table.item(row, column).setText(str(value))
            self.clock_table.setHorizontalHeaderLabels(headers)
        else:
            _fill(self.clock_table, headers, rows)

    # ------------------------------------------------------------------ Anzeige
    def retranslate(self, *_args) -> None:
        self.refresh_button.setText(_t("Neu einlesen", "Refresh"))
        self.password_button.setText(_t("Speichermodule mit Passwort auslesen", "Read memory modules with password"))
        self.copy_button.setText(_t("In Zwischenablage kopieren", "Copy to clipboard"))
        self.password_button.setVisible(
            self._memory is None and os.geteuid() != 0 and shutil.which("pkexec") is not None
        )
        self.password_button.setEnabled(self._process is None)
        self.cpu_group.setTitle(_t("Prozessor", "Processor"))
        self.cache_group.setTitle("Cache")
        self.clock_group.setTitle(_t("Takt je logischer CPU", "Clock per logical CPU"))
        self.board_group.setTitle("Mainboard / BIOS")
        self.memory_group.setTitle(_t("Arbeitsspeicher", "Memory"))
        self._show_cpu()
        self._show_caches()
        self._show_board()
        self._show_memory()
        self._update_clocks()

    def _show_cpu(self) -> None:
        clocks = inv.read_cpu_clocks(self._packages)
        fields = [
            (_t("Modell", "Model"), lambda p: p.model_name),
            (_t("Hersteller", "Vendor"), lambda p: p.vendor),
            ("Family / Model / Stepping", lambda p: f"{p.family} / {p.model} / {p.stepping}"),
            ("Microcode", lambda p: p.microcode),
            (_t("Kerne", "Cores"), lambda p: p.core_text),
            ("Threads", lambda p: p.threads),
            (_t("Takt min / max", "Clock min / max"), lambda p: self._clock_range(p, clocks)),
            (_t("Befehlssätze", "Instruction sets"), lambda p: ", ".join(p.notable_flags)),
            (_t("Bekannte CPU-Fehler (Kernel)", "Known CPU bugs (kernel)"), lambda p: " ".join(p.bugs)),
        ]
        headers = [""] + [f"{_t('Sockel', 'Socket')} {p.socket}" for p in self._packages]
        rows = [[label] + [getter(p) for p in self._packages] for label, getter in fields]
        _fill(self.cpu_table, headers, rows)
        flags = sorted({flag for p in self._packages for flag in p.flags})
        self.flags_label.setText(_t("Alle CPU-Flags: ", "All CPU flags: ") + (" ".join(flags) or "–"))

    @staticmethod
    def _clock_range(package: inv.CpuPackage, clocks: list[inv.CpuClock]) -> str:
        own = [c for c in clocks if c.socket == package.socket]
        lows = [c.min_mhz for c in own if c.min_mhz]
        highs = [c.max_mhz for c in own if c.max_mhz]
        return f"{_mhz(min(lows) if lows else None)} / {_mhz(max(highs) if highs else None)}"

    def _show_caches(self) -> None:
        headers = ["Cache", _t("Art", "Type"), _t("Je Kern/Cluster", "Per instance"), _t("Gesamt", "Total"),
                   _t("Assoziativität", "Ways"), "Sets"]
        rows = [[c.name, c.kind, c.one_size, c.all_size, c.ways, c.sets] for c in self._caches]
        _fill(self.cache_table, headers, rows)

    def _show_board(self) -> None:
        board = self._board
        fields = [
            (_t("Systemhersteller", "System vendor"), "sys_vendor"),
            (_t("Produkt", "Product"), "product_name"),
            (_t("Produktversion", "Product version"), "product_version"),
            (_t("Seriennummer", "Serial number"), "product_serial"),
            (_t("Mainboard-Hersteller", "Board vendor"), "board_vendor"),
            ("Mainboard", "board_name"),
            (_t("Mainboard-Version", "Board version"), "board_version"),
            (_t("BIOS-Hersteller", "BIOS vendor"), "bios_vendor"),
            (_t("BIOS-Version", "BIOS version"), "bios_version"),
            (_t("BIOS-Datum", "BIOS date"), "bios_date"),
        ]
        rows = [[label, board.get(key)] for label, key in fields if board.get(key)]
        if not board.get("product_serial") and self._memory and self._memory.system_serial:
            rows.append([_t("Seriennummer", "Serial number"), self._memory.system_serial])
        _fill(self.board_table, [_t("Eigenschaft", "Property"), _t("Wert", "Value")], rows)

    def _show_memory(self) -> None:
        headers = [_t("Steckplatz", "Slot"), "Bank", _t("Größe", "Size"), _t("Typ", "Type"),
                   _t("Bauform", "Form factor"), _t("Nenntakt", "Rated speed"), _t("Ist-Takt", "Configured speed"),
                   "Rank", "ECC", _t("Spannung", "Voltage"), _t("Hersteller", "Manufacturer"),
                   _t("Teilenummer", "Part number"), _t("Seriennummer", "Serial number")]
        memory = self._memory
        if memory is None:
            self.memory_summary.setText(
                _t("Speichermodule benötigen Root-Rechte (dmidecode). ", "Memory modules need root (dmidecode). ")
                + (self._memory_error.splitlines()[0][:200] if self._memory_error else "")
            )
            _fill(self.memory_table, headers, [])
            return
        installed = memory.installed
        parts = [
            _t("Gesamt", "Total") + f": {inv._format_mib(memory.total_mib)}",
            _t("Belegt", "Populated") + f": {len(installed)} / {memory.slot_count}",
        ]
        if memory.error_correction:
            parts.append(_t("Fehlerkorrektur", "Error correction") + ": " + ", ".join(sorted(set(memory.error_correction))))
        if memory.max_capacity:
            parts.append(_t("Maximal", "Maximum") + ": " + ", ".join(memory.max_capacity))
        self.memory_summary.setText(" · ".join(parts))
        rows = []
        for device in memory.devices:
            if not device.installed:
                rows.append([device.locator, device.bank, _t("leer", "empty")] + [""] * (len(headers) - 3))
                continue
            ecc = "–" if device.ecc is None else (_t("ja", "yes") if device.ecc else _t("nein", "no"))
            rows.append([device.locator, device.bank, device.size, device.kind, device.form_factor, device.speed,
                         device.configured_speed, device.rank, ecc, device.voltage, device.manufacturer,
                         device.part_number, device.serial])
        _fill(self.memory_table, headers, rows)

    def close_privileged_session(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.waitForFinished(2000)
