"""Tab „System“: Prozessor, Caches, Takt je Kern, Mainboard/BIOS, Speichermodule und weitere Komponenten."""

from __future__ import annotations


from PySide6.QtCore import QProcess, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from hardwaretest.core import privilege
from hardwaretest.core import device_inventory as dev
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


_LINK_COLORS = {dev.LINK_WIDTH: QColor("#c62828"), dev.LINK_SPEED: QColor("#ef6c00")}


def _fill(table: QTableWidget, headers: list[str], rows: list[list], colors: dict | None = None,
          max_height: int | None = 420) -> None:
    """``colors``: {(Zeile, Spalte): QColor} für Statuszellen; ``max_height=None``: alle Zeilen ohne eigene Scrollleiste."""
    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            item = _item(value)
            color = (colors or {}).get((row, column))
            if color is not None:
                item.setBackground(color)
                item.setForeground(QColor("white"))
            table.setItem(row, column, item)
    table.resizeColumnsToContents()
    height = table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(len(rows))) + 4
    if table.horizontalScrollBar().isVisible() or max_height is None:
        height += table.horizontalScrollBar().sizeHint().height()
    height = max(height, 60)
    table.setFixedHeight(height if max_height is None else min(height, max_height))


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
        self._devices = dev.DeviceInventory()

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
        self.clock_note = QLabel()
        self.clock_note.setWordWrap(True)
        self.clock_note.setVisible(False)
        clock_layout.addWidget(self.clock_note)
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

        self.pci_group, self.pci_table = self._add_table_group(layout)
        self.pci_note = QLabel()
        self.pci_note.setWordWrap(True)
        self.pci_group.layout().insertWidget(0, self.pci_note)
        self.network_group, self.network_table = self._add_table_group(layout)
        self.graphics_group, self.graphics_table = self._add_table_group(layout)
        self.controller_group, self.controller_table = self._add_table_group(layout)
        self.disk_group, self.disk_table = self._add_table_group(layout)
        self.usb_group, self.usb_table = self._add_table_group(layout)
        self.usb_note = QLabel()
        self.usb_note.setWordWrap(True)
        self.usb_group.layout().insertWidget(0, self.usb_note)
        self.tb_group, self.tb_table = self._add_table_group(layout)
        self.tb_note = QLabel()
        self.tb_note.setWordWrap(True)
        self.tb_group.layout().insertWidget(0, self.tb_note)
        layout.addStretch(1)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(self.CLOCK_INTERVAL_MS)
        self._clock_timer.timeout.connect(self._update_clocks)

        language_manager.language_changed.connect(self.retranslate)
        self.refresh()

    @staticmethod
    def _add_table_group(layout: QVBoxLayout) -> tuple[QGroupBox, QTableWidget]:
        group = QGroupBox()
        table = _table()
        QVBoxLayout(group).addWidget(table)
        layout.addWidget(group)
        return group, table

    # ------------------------------------------------------------------ Daten
    def refresh(self) -> None:
        self._devices = dev.read_device_inventory()
        self._packages = inv.read_cpu_packages()
        self._caches = inv.read_caches()
        self._board = inv.read_board_info()
        self._memory, self._memory_error = inv.read_memory_inventory()
        self.retranslate()

    def _read_with_password(self) -> None:
        if privilege.tool() is None or self._process is not None:
            return
        program, *args = [*privilege.command_prefix(_t("Speichermodule auslesen", "Read memory modules")),
                          *inv.dmidecode_command()]
        process = QProcess(self)
        self._process = process
        process.finished.connect(lambda code, _status, p=process: self._password_done(p, code))
        process.errorOccurred.connect(lambda error, p=process: self._password_error(p, error))
        self.password_button.setEnabled(False)
        process.start(program, args)

    def is_busy(self) -> bool:
        """True, solange die Passwortabfrage bzw. dmidecode läuft."""
        return self._process is not None

    def _password_error(self, process: QProcess, error) -> None:
        # Bei „Crashed“ folgt noch finished(); nur FailedToStart bleibt sonst ohne Abschluss.
        if error == QProcess.ProcessError.FailedToStart:
            self._password_done(process, -1, _t("Rechteerhöhung konnte nicht gestartet werden", "Privilege escalation could not be started"))

    def _password_done(self, process: QProcess, code: int, fallback_error: str = "") -> None:
        # Idempotent: finished und errorOccurred können beide eintreffen.
        if self._process is not process:
            return
        self._process = None
        output = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        error = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
        process.deleteLater()
        if code == 0 and output:
            self._memory, self._memory_error = inv.parse_dmidecode(output), ""
        else:
            self._memory_error = error or fallback_error or _t("Abgebrochen", "Cancelled")
        self.retranslate()

    def text_lines(self) -> list[str]:
        return inv.inventory_lines(
            language_manager.language, self._packages, self._caches, self._board, self._memory, self._memory_error,
        ) + dev.device_lines(language_manager.language, self._devices)

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
        dmi = inv.dmi_processor_map(self._packages, self._memory)
        hybrid = any(clock.core_type for clock in clocks)
        headers = ["CPU", _t("Sockel", "Socket"), _t("Kern", "Core")]
        headers += [_t("Typ", "Type")] if hybrid else []
        headers += [_t("Aktuell", "Current"), "Min", "Max"]
        rows = []
        for clock in clocks:
            row = [clock.cpu, clock.socket, clock.core] + ([clock.core_type] if hybrid else [])
            proc = dmi.get(clock.socket)
            maximum = clock.max_mhz or (proc.max_mhz if proc else None)
            rows.append([*row, _mhz(clock.current_mhz), _mhz(clock.min_mhz), _mhz(maximum)])
        self.clock_note.setVisible(bool(clocks) and not any(clock.cpufreq for clock in clocks))
        self.clock_note.setText(_t(
            "Kein cpufreq-Treiber: Die Taktsteuerung liegt beim BIOS (z. B. HPE Power Regulator nicht auf "
            "„OS Control“). Aktueller Takt aus /proc/cpuinfo, Max aus DMI (dmidecode), Min nicht verfügbar.",
            "No cpufreq driver: clock control is handled by the BIOS (e.g. HPE Power Regulator not set to "
            "“OS Control”). Current clock from /proc/cpuinfo, max from DMI (dmidecode), min unavailable.",
        ))
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
            self._memory is None and not privilege.is_root() and privilege.tool() is not None
        )
        self.password_button.setEnabled(self._process is None)
        self.cpu_group.setTitle(_t("Prozessor", "Processor"))
        self.cache_group.setTitle("Cache")
        self.clock_group.setTitle(_t("Takt je logischer CPU", "Clock per logical CPU"))
        self.board_group.setTitle("Mainboard / BIOS")
        self.memory_group.setTitle(_t("Arbeitsspeicher", "Memory"))
        self.pci_group.setTitle(_t("PCIe-Verbindungen", "PCIe links"))
        self.network_group.setTitle(_t("Netzwerk", "Network"))
        self.graphics_group.setTitle(_t("Grafik", "Graphics"))
        self.controller_group.setTitle(_t("Speicher-Controller", "Storage controllers"))
        self.disk_group.setTitle(_t("Datenträger", "Disks"))
        self.usb_group.setTitle("USB")
        self.tb_group.setTitle("Thunderbolt / USB4")
        self._show_cpu()
        self._show_caches()
        self._show_board()
        self._show_memory()
        self._show_devices()
        self._update_clocks()

    def _show_cpu(self) -> None:
        clocks = inv.read_cpu_clocks(self._packages)
        dmi = inv.dmi_processor_map(self._packages, self._memory)

        def dmi_field(attr: str):
            return lambda p: getattr(dmi[p.socket], attr) if p.socket in dmi else ""

        def cores_enabled(p):
            proc = dmi.get(p.socket)
            if not proc or not proc.core_enabled:
                return ""
            return f"{proc.core_enabled} / {proc.core_count}" if proc.core_count else proc.core_enabled

        fields = [
            (_t("Modell", "Model"), lambda p: p.model_name),
            (_t("Hersteller", "Vendor"), lambda p: p.vendor),
            ("Family / Model / Stepping", lambda p: f"{p.family} / {p.model} / {p.stepping}"),
            ("Microcode", lambda p: p.microcode),
            (_t("Kerne", "Cores"), lambda p: p.core_text),
            ("Threads", lambda p: p.threads),
            (_t("Takt min / max", "Clock min / max"), lambda p: self._clock_range(p, clocks, dmi.get(p.socket))),
            (_t("Befehlssätze", "Instruction sets"), lambda p: ", ".join(p.notable_flags)),
            (_t("Bekannte CPU-Fehler (Kernel)", "Known CPU bugs (kernel)"), lambda p: " ".join(p.bugs)),
        ]
        if dmi:
            fields += [
                (_t("Steckplatz (DMI)", "Socket (DMI)"), dmi_field("designation")),
                (_t("Basistakt (DMI)", "Base clock (DMI)"), dmi_field("current_speed")),
                (_t("Max. Takt (DMI)", "Max clock (DMI)"), dmi_field("max_speed")),
                (_t("Externer Takt", "External clock"), dmi_field("external_clock")),
                (_t("Spannung", "Voltage"), dmi_field("voltage")),
                (_t("Kerne aktiv / vorhanden", "Cores enabled / present"), cores_enabled),
                (_t("Sockeltyp", "Socket type"), dmi_field("upgrade")),
                ("Status", dmi_field("status")),
                (_t("Teilenummer", "Part number"), dmi_field("part_number")),
                (_t("Seriennummer", "Serial number"), dmi_field("serial")),
            ]
        headers = [""] + [f"{_t('Sockel', 'Socket')} {p.socket}" for p in self._packages]
        rows = [[label] + [getter(p) for p in self._packages] for label, getter in fields]
        rows = [row for row in rows if any(value not in (None, "") for value in row[1:])]
        _fill(self.cpu_table, headers, rows, max_height=None)
        flags = sorted({flag for p in self._packages for flag in p.flags})
        self.flags_label.setText(_t("Alle CPU-Flags: ", "All CPU flags: ") + (" ".join(flags) or "–"))

    @staticmethod
    def _clock_range(package: inv.CpuPackage, clocks: list[inv.CpuClock], proc: inv.DmiProcessor | None = None) -> str:
        own = [c for c in clocks if c.socket == package.socket]
        lows = [c.min_mhz for c in own if c.min_mhz]
        highs = [c.max_mhz for c in own if c.max_mhz]
        high = max(highs) if highs else (proc.max_mhz if proc else None)
        return f"{_mhz(min(lows) if lows else None)} / {_mhz(high)}"

    def _show_caches(self) -> None:
        headers = ["Cache", _t("Art", "Type"), _t("Je Kern/Cluster", "Per instance"), _t("Gesamt", "Total"),
                   _t("Assoziativität", "Ways"), "Sets"]
        rows = [[c.name, c.kind, c.one_size, c.all_size, c.ways, c.sets] for c in self._caches]
        _fill(self.cache_table, headers, rows, max_height=None)

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
        _fill(self.board_table, [_t("Eigenschaft", "Property"), _t("Wert", "Value")], rows, max_height=None)

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
        _fill(self.memory_table, headers, rows, max_height=None)

    def _show_devices(self) -> None:
        language = language_manager.language
        devices = self._devices
        self.pci_note.setText(_t(
            "Rot: weniger Lanes als möglich (Steckplatz, Riser, Sitz der Karte prüfen). Orange: niedrigere "
            "Generation – im Leerlauf durch Energiesparen (ASPM) oft normal, unter Last erneut prüfen.",
            "Red: fewer lanes than possible (check slot, riser, card seating). Orange: lower generation – often "
            "normal at idle due to power saving (ASPM); check again under load.",
        ))
        rows, colors = [], {}
        for index, device in enumerate(devices.links):
            rows.append([device.address, device.model, device.driver,
                         dev.link_text(device.current_speed, device.current_width),
                         f"Gen{device.possible_generation} x{device.possible_width}",
                         dev.link_status_text(language, device)])
            if device.link_state in _LINK_COLORS:
                colors[(index, 5)] = _LINK_COLORS[device.link_state]
        _fill(self.pci_table, [_t("Adresse", "Address"), _t("Gerät", "Device"), _t("Treiber", "Driver"),
                               _t("Aktuell", "Current"), _t("Möglich", "Possible"), "Status"], rows, colors)
        _fill(self.network_table, ["Interface", _t("Modell", "Model"), _t("Treiber", "Driver"), "Firmware", "MAC",
                                   _t("Zustand", "State"), _t("Geschwindigkeit", "Speed"), "PCI"],
              [[n.name, n.model, n.driver, n.firmware, n.mac, n.state, dev.speed_text(language, n.speed_mbps), n.pci]
               for n in devices.network])
        _fill(self.graphics_table, ["PCI", _t("Modell", "Model"), _t("Treiber", "Driver"), "VRAM", "VBIOS"],
              [[g.pci, g.model, g.driver, dev.vram_text(language, g.vram), g.vbios] for g in devices.graphics])
        _fill(self.controller_table, ["PCI", _t("Art", "Type"), _t("Modell", "Model"), _t("Treiber", "Driver"),
                                      "Firmware"],
              [[c.pci, c.kind, c.model, c.driver, c.firmware] for c in devices.controllers])
        _fill(self.disk_table, [_t("Gerät", "Device"), _t("Modell", "Model"), _t("Größe", "Size"), _t("Art", "Type"),
                                _t("Seriennummer", "Serial number"), "Firmware", _t("Sektor log./phys.", "Sector log./phys.")],
              [[d.name, d.model, dev._human(d.size) if d.size else "", dev.disk_type_text(d), d.serial, d.firmware,
                dev.sector_text(d)] for d in devices.disks])
        self.usb_note.setText(_t(
            "Topologie je Bus: Root-Hub → Hub → Gerät. Geschwindigkeit = ausgehandelt; liegt sie unter der "
            "USB-Version des Geräts, prüfen Sie Port, Kabel und Hub. Orange: USB-3-Gerät läuft nur mit High Speed.",
            "Topology per bus: root hub → hub → device. Speed = negotiated; if below the device's USB version, "
            "check port, cable and hub. Orange: USB 3 device running at High Speed only.",
        ))
        rows, colors = [], {}
        for index, device in enumerate(devices.usb):
            rows.append([dev.usb_tree_label(device), device.model, f"{device.vendor_id:04x}:{device.product_id:04x}",
                         dev.usb_kind_text(language, device), dev.usb_speed_text(language, device),
                         device.usb_version, ", ".join(device.drivers), device.serial or device.pci,
                         str(device.max_children) if device.is_hub and device.max_children else ""])
            if not device.is_root_hub and dev.usb_downgraded(device):
                colors[(index, 4)] = _LINK_COLORS[dev.LINK_SPEED]
        _fill(self.usb_table, ["Port", _t("Gerät", "Device"), "ID", _t("Art", "Type"), _t("Geschwindigkeit", "Speed"),
                               "USB", _t("Treiber", "Driver"), _t("Seriennummer / Controller", "Serial / controller"),
                               "Ports"], rows, colors)
        self._show_thunderbolt(language, devices)

    def _show_thunderbolt(self, language: str, devices: dev.DeviceInventory) -> None:
        if not devices.thunderbolt:
            self.tb_note.setText(_t("Kein Thunderbolt-/USB4-Controller gefunden.", "No Thunderbolt/USB4 controller found."))
            _fill(self.tb_table, [_t("Gerät", "Device")], [])
            return
        hosts = [d for d in devices.thunderbolt if d.is_host]
        host_info = []
        for host in hosts:
            parts = [_t(f"Domäne {host.domain}", f"Domain {host.domain}")]
            if host.security:
                parts.append(_t("Sicherheitsstufe: ", "Security level: ") + dev.tb_security_text(language, host.security))
            if host.iommu is not None:
                parts.append(_t("IOMMU-DMA-Schutz: ", "IOMMU DMA protection: ") + _t("ja", "yes") if host.iommu
                             else _t("IOMMU-DMA-Schutz: nein", "IOMMU DMA protection: no"))
            host_info.append(" · ".join(parts))
        self.tb_note.setText("\n".join(host_info) + "\n" + _t(
            "Kette je Domäne: Host-Router → Gerät → nachgeschaltetes Gerät. Link = ausgehandelte Rate × Lanes "
            "(üblich 20 Gbit/s × 2 ab Thunderbolt 3). Orange: Gerät läuft langsamer – Kabel (aktiv/40 Gbit/s), Port "
            "und Kette prüfen. Rot: Gerät gesperrt (Freigabe über boltctl/Systemeinstellungen nötig). "
            "Graue Zeilen: in boltctl gespeichert, aktuell nicht angeschlossen.",
            "Chain per domain: host router → device → downstream device. Link = negotiated rate × lanes "
            "(typically 20 Gbit/s × 2 from Thunderbolt 3 on). Orange: device runs slower – check cable (active/40 Gbit/s), "
            "port and chain. Red: device blocked (authorize via boltctl/system settings). "
            "Grey rows: stored in boltctl, currently not connected.",
        ))
        rows, colors = [], {}
        grey = QColor("#9e9e9e")
        for index, device in enumerate(devices.thunderbolt):
            rows.append([dev.tb_tree_label(language, device), device.model, device.generation_text,
                         dev.tb_link_text(language, device), dev.tb_authorized_text(language, device),
                         device.nvm_version, device.policy, device.unique_id])
            if not device.connected:
                for col in range(8):
                    colors[(index, col)] = grey
            elif device.authorized == 0:
                colors[(index, 4)] = _LINK_COLORS[dev.LINK_WIDTH]
            elif dev.tb_downgraded(device):
                colors[(index, 3)] = _LINK_COLORS[dev.LINK_SPEED]
        _fill(self.tb_table, [_t("Route", "Route"), _t("Gerät", "Device"), _t("Generation", "Generation"), "Link",
                              _t("Freigabe", "Authorization"), "NVM", _t("Richtlinie", "Policy"), "UUID"], rows, colors)

    def close_privileged_session(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.kill()
            process.waitForFinished(2000)
            process.deleteLater()
            self.password_button.setEnabled(True)
