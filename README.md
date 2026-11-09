# Hardwaretest GUI

PySide6-Anwendung zum Starten von Hardware-Stresstests für **PCs, Workstations und Server**. Nutzt bewährte Werkzeuge: `stress-ng`, `Prime95 (mprime)`, `OpenDCDiag`, `fio`. Zielplattformen sind **Ubuntu 24.04 und 26.04 (amd64)** sowie **AlmaLinux/RHEL/Rocky 9 und 10 (x86_64, RPM)**. Andere Distributionen werden nicht zugesichert. Optionale HPE-Smart-Array-Funktionen sind ausdrücklich als **HPE-spezifisch** gekennzeichnet und für normale PCs nicht erforderlich.

### Neu in 0.3.9
- **Hersteller-Diagnose für Marvell/QLogic FastLinQ (QL41000/QL45000, Treiber `qede`):** nutzt die QLogic Control Suite CLI (`QCScli`, Paket `QCS-CLI-40.x`, Installationspfad `/opt/QLogic_Corporation/QCS`) – Adapterliste, Geräteinfo und Statistik des gewählten Adapters (Ziel über MAC). Textfehler des Tools (Exitcode bleibt 0) werden als fehlgeschlagener Schritt gewertet. `qlnxcli` bleibt als Ersatz.

### Neu in 0.3.8

- **ProLiant Info** erscheint nur noch auf HPE ProLiant/Synergy (DMI); auf anderen Rechnern ist der Tab ausgeblendet. Der RAID-Button der Disk-Tabs blendet ihn bei Bedarf ein (z. B. MegaRAID).
- **Gesamttest:** neuer Teiltest **„RAM-Fülltest (gesamter freier Speicher)“** (Dauer, Reserve). Aktiver Swap wird vor dem Schritt über die Root-Sitzung des Gesamttests deaktiviert (`swapoff -a`) und nach dem Gesamttest wieder aktiviert; ist das Abschalten unsicher (zu viel belegter Swap), wird der Schritt übersprungen.
- Root-Sitzung: `swapoff -a` und `swapon` sind in der Befehlsliste freigegeben (nötig für den RAM-Fülltest im Gesamttest).

### Neu in 0.3.7

- **Hersteller-Diagnose (Netzwerk):** Intel führt jetzt `nvmupdate64e -i` **und** `epct64e -devices`/`-nic=N -get` nacheinander aus; bei mehreren installierten NVM-Update-Paketen wird die zur Adapterfamilie passende Variante gewählt (700er für `i40e`, E810 für `ice`) und `nvmupdate64e` im Paketordner gestartet (benötigt `nvmupdate.cfg`). Solarflare/AMD nutzt ersatzweise `sfreport -m -` (Textbericht), wenn `sfupdate`/`sfboot` fehlen.

### Neu in 0.3.6

- **Netzwerk-Tab: Hersteller-Diagnose.** Neuer Button, sichtbar wenn für den Treiber des gewählten Adapters ein proprietäres Herstellerwerkzeug installiert ist (im Live-ISO nur in der internen Ausprägung): Broadcom NetXtreme-E (`bnxt_en` → `niccli --list`, `-i N show`, `show --pkg_ver`, `link -s`, `cable -m --show`; ersatzweise `bnxtnvm device_info`), Intel (`i40e`/`ice`/`ixgbe`/`igb` → `nvmupdate64e -i`, `epct64e -devices` + `-nic=N -get`), Marvell/QLogic FastLinQ (`qede`/`bnx2x` → QLogic Control Suite CLI `QCScli -protocol local "list -f BDF NDIS"`, `-t NDIS -f MAC -i <mac> info`/`stats`; ersatzweise `qlnxcli -i`; `qaucli` 3.x ist reines FC-Werkzeug), Mellanox/NVIDIA MFT (`mlxfwmanager --query`, `mlxconfig query`, `mlxlink`) und Solarflare/AMD (`sfc` → `sfupdate`, `sfboot`). Nur lesende Abfragen, Ausgabe im Log und als HTML-Bericht; Werkzeuge werden in `$PATH`, `/opt/vendor-tools` und den üblichen Herstellerpfaden gesucht (`core/nic_vendor.py`).

### Neu in 0.3.5

- **SMART-Info / SATA-Tab:** USB-SSDs mit unbekannter USB-SATA-Bridge (z. B. Lenovo `0x17ef:0x235c`) werden automatisch mit `smartctl -d sat` erneut abgefragt statt als „Kommandozeilenfehler“ zu erscheinen.
- **SMART-Info:** NVMe-Laufwerke ohne Selbsttest-Protokoll („Read Self-test Log failed: Invalid Field in Command“) erhalten keine Warnung „SMART-Kommando fehlgeschlagen“ mehr – der Zustand bleibt ✓ OK.

### Neu in 0.3.4

- **System-Tab:** neue Gruppe **„Sensoren (iLO/IPMI): Temperaturen, Lüfter, Leistung“** – alle IPMI-Messwerte mit Status (farbig), leere Steckplätze ausgeblendet, auch im HTML-Bericht. Auf ProLiants mit laufendem `amsd` erscheinen deutlich mehr Sensoren.

### Neu in 0.3.3

- **System-Tab:** „In Zwischenablage kopieren“ ersetzt durch **„Bericht erstellen“** – alle Inventartabellen (CPU, Cache, Takt, Mainboard/BIOS, RAM, PCIe, Netzwerk, Grafik, Controller, Datenträger, USB, Thunderbolt, FC, Netzteile) als HTML-Bericht `~/Downloads/hwtest-system-*.html`.
- **Netzwerk-Tab:** ethtool-Selbsttest, SFP-Modul (`ethtool -m`) und Mellanox `mstlink` schreiben jetzt ebenfalls HTML-Berichte.
- RPM: `fio-engine-libaio` als Abhängigkeit (auf EL9 ist die libaio-Engine ein eigenes Paket; ohne sie scheiterten alle fio-Disk-Tests).

### Neu in 0.3.2

- Terminalstart: `xfce4-terminal`/`mate-terminal` werden mit `-x` aufgerufen. Mit `-e bash -lc …` starteten sie gar nicht („Unknown option -lc“) – Systemreport und die lshw/lspci/dmesg-Fenster im Informationen-Tab blieben auf Systemen ohne `x-terminal-emulator` (z. B. AlmaLinux-Live mit Openbox) aus.

### Neu in 0.3.1

- **AlmaLinux/RHEL/Rocky 9+:** Neues RPM-Paket (`scripts/build_rpm.sh`) mit sudoers-Regeln für die Gruppe `wheel`, Desktop-Eintrag und allen Abhängigkeiten (`stress-ng`, `fio`, `btop`, … – EPEL vorher aktivieren).
- Bundle, OpenDCDiag, RPM und AppImage werden jetzt in einem AlmaLinux-9-Container gebaut (`scripts/build_in_container.sh`, glibc 2.34). Damit laufen RPM und AppImage auf EL 9/10, Debian 12+ und Ubuntu 22.04+; zuvor verlangte das AppImage glibc 2.38.
- Installationshinweise sind distributionsabhängig (`sudo apt install …` / `sudo dnf install …`), Paketprüfung per `dpkg` oder `rpm`; Update-Prüfung kennt `.rpm`.
- stress-ng-Tab meldet ein fehlendes `stress-ng` vor dem Start mit Installationshinweis statt „Exit-Code 127“.
- Informationen-Tab: „Report öffnen“ zeigt den JSON-Systemreport im Browser (Firefox-JSON-Ansicht), wenn kein grafischer JSON-Viewer installiert ist; HTML-Report ebenfalls mit bereinigter Umgebung.

### Neu in 0.3.0

- **Stabile Version** nach ausführlichen Tests auf ProLiant-Servern, Workstations und im Live-ISO.
- Gesamttest-Tab: gesamter Tab scrollbar, Gruppe „Ablauf und Protokoll“ wird nicht mehr gestaucht.
- „Berichtsordner“ öffnet ~/Downloads zuverlässig im Dateimanager (bereinigte Umgebung).
- Doku: Portierungsanalyse für AlmaLinux/RHEL (`docs/PORTIERUNG_ALMALINUX.md`).

### Neu in 0.2.69

- **Einheitliche HTML-Berichte:** Jeder Test-Tab (stress-ng, Prime95, OpenDCDiag,
  Speichercontroller, RAM-Fülltest, Disk, NVMe, Netzwerk, NVIDIA GPU, GPU Vulkan,
  Dateisystem, ZFS, Monitoring, ProLiant, Gesamttest) schreibt nach jedem Test automatisch
  einen Bericht nach `~/Downloads/hwtest-<test>-JJJJMMTT-HHMMSS.html` mit gleichem Aufbau
  (Status, Zusammenfassung, Parameter, Abschnitte, Hinweise, Rohausgabe) und bietet
  „Bericht öffnen“. Gesamttest-CSV und Systemreport (`hwtest-systemreport-…`) folgen dem
  gleichen Namensschema.

### Neu in 0.2.68

- **NVIDIA GPU – Datenblatt:** Kennt der Treiber ein Abfragefeld von `nvidia-smi` nicht, wird es
  weggelassen und die Abfrage wiederholt, statt ein leeres Datenblatt zu liefern. Fehler von
  `nvidia-smi` erscheinen jetzt in der Statuszeile und als Hinweis im HTML-Bericht.

### Neu in 0.2.67

- **NVIDIA GPU – HTML-Bericht:** Nach jeder DCGM-Diagnose wird ein HTML-Bericht in
  `~/Downloads` (`dcgm-diagnose-DATUM.html`) abgelegt: Gesamtergebnis, Datenblatt aller
  geprüften GPUs (Modell, Seriennummer, UUID, VBIOS, PCIe-Generation/-Breite, ECC,
  Retired/Remapped Pages, Leistungsgrenzen, Takte, Temperatur) sowie die Ergebnistabelle
  pro Test und GPU. „HTML-Bericht öffnen“ zeigt ihn im Browser.
- **NVIDIA GPU – Datenblatt:** Neuer Knopf „GPU-Datenblatt (HTML)“ erzeugt dasselbe
  Datenblatt ohne Diagnose (`gpu-datenblatt-DATUM.html`).
- **DCGM-Diagnose:** „PCIe-Peer-to-Peer-Test überspringen“ ist nicht mehr auf HPE Synergy
  vorbelegt; der P2P-Test läuft standardmäßig mit (mit `iommu=pt` funktionsfähig).
- **ProLiant Info – RAID:** Ein nicht aktivierter Controller-Cache („Not Configured“,
  „Disabled“) wird nur noch als Hinweis gemeldet, nicht mehr als Warnung – das ist eine
  Einstellung, kein Fehler. „Temporarily/Permanently Disabled“ bleibt eine Warnung.

### Neu in 0.2.66

- **DCGM-Diagnose:** Die Option „PCIe-Peer-to-Peer-Test überspringen“ verwendet jetzt den
  korrekten DCGM-Parameter `pcie.test_p2p_on=false` (DCGM 4.x kennt kein `test_p2p`) und
  wird nur noch ab Stufe 2 übergeben – zuvor brach `dcgmi` mit „Invalid Parameter String“ ab.
- **DCGM-Diagnose:** Ein `runtime_error` von DCGM wird jetzt als Fehlermeldung im Bericht und
  in der Statuszeile angezeigt statt als „0 fehlgeschlagene Prüfung(en)“.
- **Netzwerk-Selbsttest (ethtool):** Setzt der Treiber nur das FAIL-Flag, ohne dass ein
  Einzeltest ≠ 0 ist (z. B. bnxt_en auf NPAR/geteilter PF oder mit aktiven VFs), gilt das
  Ergebnis als „nicht aussagekräftig“ (gelb) statt als Fehler.

### Neu in 0.2.65

- **ethtool-Selbsttest (Tab Netzwerk):** `ethtool -t` online/offline für physische Adapter über den Root-Broker; Fehlerzähler aus `ethtool -S` (CRC, rx/tx errors, dropped) werden vor/nach dem Test verglichen und Zuwächse als Warnung gemeldet. „SFP-Modul lesen“ (`ethtool -m`) zeigt Hersteller, Typ, Seriennummer, Temperatur, Spannung, Rx-/Tx-Leistung. **Mellanox Link-Diagnose** (`mstlink` aus `mstflint`, nur bei mlx4/mlx5-Adaptern und installiertem Paket sichtbar): Link-Zustand, FEC, Empfehlung, physische Fehler/BER, Link-Down-Zähler, Moduldaten mit Grenzwertprüfung. **Hersteller-Diagnose** (ab 0.3.6): lesende Inventar-/Link-/Transceiver-Abfragen mit proprietären Werkzeugen (Broadcom `niccli`, Intel `nvmupdate64e`/`epct64e`, Marvell `QCScli`/`qlnxcli`, NVIDIA MFT, Solarflare `sfupdate`/`sfboot`/`sfreport`), sofern installiert.
- **Fibre-Channel-HBAs (Tab System):** Übersicht aus `/sys/class/fc_host` (Port-Zustand, Geschwindigkeit, WWPN/WWNN, Fabric, Firmware, Treiber, Remote-Ports) mit Fehlerzählern; rot bei Zählern > 0 oder Linkdown. Auch im Gesamttest-Bericht.
- **Netzteile / Redundanz (Tab System):** DMI-Typ 39 (Einbauort, Hersteller, Modell, Seriennummer, Maximalleistung, Status, bestückt/hot-plug) plus IPMI-Netzteilsensoren und Redundanzstatus (`ipmitool sdr type 0x08/0x09`) inkl. Anleitung für den Redundanztest.
- **Sensoren (Tab System):** Temperaturen, Lüfter und Leistungsaufnahme aus dem iLO/BMC (`ipmitool sdr elist`); auf ProLiants mit laufendem `amsd` deutlich mehr Sensoren. Vollständige Liste im Tab ProLiant-Info.
- **DCGM-Diagnose (Tab NVIDIA):** Bei DCGM 4 zeigt der Bericht neben den GPU-Einzelergebnissen den Gesamtstatus je Test („Gesamt“) samt Fehlermeldung, wenn er schlechter ausfällt – bisher wurde ein Fail gezählt, war im Klartext aber unsichtbar. Die Statuszeile nennt die fehlgeschlagenen Prüfungen (z. B. „pcie“). Neue Option **„PCIe-Peer-to-Peer-Test überspringen“** (`pcie.test_p2p=false`): Der P2P-Teil scheitert bei aktivem IOMMU/VT-d mit PCIe-ACS oder GPUs hinter PCIe-Switches (HPE Synergy Grafikmodul) mit „buffer mismatch“ (ID 86/87) – kein GPU-Defekt. Auf HPE Synergy vorbelegt, auf ProLiant DL aus. Scheitert pcie an P2P, hängt der Bericht einen Hinweisblock mit Abhilfen an (Checkbox, `iommu=pt`, BIOS-ACS, `ForceP2P=1`).
- **Netzteile:** DMI-Platzhalter ohne Daten (Desktop-BIOS mit „Default string“, externes Netzteil) werden als „Platzhalter (keine Daten)“ angezeigt und bei der Redundanzbewertung ignoriert; Zeilen ohne Namen heißen „Netzteil N“.
- **Fix:** Die sudoers-Regel des .deb erlaubte nur `dmidecode -t 1 -t 4 -t 16 -t 17`; mit den Netzteilen (`-t 39`) griff sie nicht mehr und der System-Tab verlangte ein Passwort. Regel ergänzt, Schaltfläche heißt jetzt „Speichermodule und Netzteile mit Passwort auslesen“.
- **Fix:** Der Root-Broker lehnte `dmidecode -t 1 -t 4 -t 16 -t 17` im Gesamttest ab (Allowlist erlaubte nur einen `-t`-Parameter) – Systeminformationen im Bericht fehlten dadurch ohne root.

### Neu in 0.2.64

- **Fix Root-Sitzung (seit 0.2.62):** Alle Root-Aktionen über `sudo -A` – NVMe-Selbsttests, SMART-/Gesundheitsdaten, ProLiant Info (iLO, IML, RAID), fio-Lesetests, DCGM, Dateisystemprüfung – scheiterten mit `cat: /tmp/hardwaretest-root.…/out: Keine Berechtigung`. Der Root-Helfer übereignete die Ausgabedateien nur unter pkexec (`PKEXEC_UID`) dem Benutzer; unter sudo blieben sie root:0600. Jetzt wird auch `SUDO_UID` ausgewertet.

### Neu in 0.2.63

- **Gesamttest:** Kein Teiltest ist mehr vorausgewählt (nur das Monitoring bleibt aktiv) – bisher waren CPU, RAM, NVMe und NVIDIA vorgewählt, obwohl viele Rechner keine NVIDIA-Karte haben.
- **Temperaturgrenze Standard 100 °C** (statt 90 °C) im Langzeitmonitoring und Gesamttest; 90 °C führten auf ProLiants und aktuellen Desktop-CPUs regelmäßig zu Fehlalarmen.
- **Langzeitmonitoring: Lüfter per ACPI.** Rechner ohne hwmon-Lüftersensor (z. B. Lenovo IdeaCentre) liefern jetzt den ACPI-Lüfterzustand (`fan.acpi.N.percent`) als Fallback.
- **ProLiant Info:** Neuer Button **„HTML-Bericht anzeigen“** (wie im Langzeitmonitoring) – bisher ließ sich der Bericht nur exportieren.
- **Disk (Datei):** Auf dem Live-ISO wird der Schreib-/Lesetest auf RAM-Dateisystemen (tmpfs, overlay, squashfs …) abgelehnt, damit nicht versehentlich der Arbeitsspeicher statt eines Datenträgers getestet wird. Hinweistext klargestellt.
- **Disk (Destruktiv):** Zweite Sicherheitsabfrage („Wollen Sie das wirklich?“) vor dem Überschreiben; Standard ist Abbrechen.
- **OpenDCDiag:** Die CPU-Auswahl ist jetzt ein Auswahlmenü (Alle CPUs / Nur Sockel N / Benutzerdefiniert) statt eines freien cpuset-Feldes.
- **Tableiste:** Breitere Scroll-Pfeile bei kleinen Auflösungen (iLO-Konsole).
- **Hilfe** vollständig überprüft und aktualisiert (NVMe- und Gesamttest-Abschnitt ergänzt, veraltete Angaben korrigiert, alle Änderungen dieser Version eingearbeitet, DE/EN).
- Tests lesen und ändern die Spracheinstellung des Benutzers nicht mehr.

### Neu in 0.2.62

- **Einheitlicher Passwortdialog.** Alle Root-Aktionen (Swap aus/ein, fio-Lesetest und destruktiver Test, NVMe/SMART, Root-Sitzung des Gesamttests, DCGM, Dateisystem- und ZFS-Prüfung, Speichermodule per dmidecode, Aushängen, IPMI, ProLiant/RAID) nutzen jetzt `sudo -A` mit dem eigenen Dialog der App („sudo-Passwort eingeben:“, dunkles Design, `hardwaretest --askpass`). Der große Polkit-Dialog erscheint nur noch als Fallback, wenn `sudo` fehlt. sudo merkt sich die Freigabe (Standard 15 Minuten), daher wird innerhalb einer Sitzung meist nur einmal gefragt; mit NOPASSWD-Regel (Live-ISO) oder als root entfällt die Abfrage ganz. Die Tabs fragen das Passwort nicht mehr selbst ab.

### Neu in 0.2.61

> **Stand 0.2.61:** Der Funktionsumfang ist vorläufig eingefroren – weitere Releases enthalten nur Fehlerbehebungen und Optimierungen.

- **System-Tab: Thunderbolt / USB4.** Neue Tabelle mit Host-Router und angeschlossenen Geräten als Kette (Route), Generation (Thunderbolt 1–4/USB4), ausgehandelter Linkrate × Lanes, Freigabestatus, NVM-Firmware, boltctl-Richtlinie und UUID; Hinweiszeile mit Sicherheitsstufe der Domäne und IOMMU-DMA-Schutz. Orange: Gerät läuft unter der üblichen Rate (Kabel/Port), rot: gesperrt, grau: in boltctl gespeichert, aber nicht angeschlossen. Auch im Textbericht enthalten.
- **Swap-Zustand tab-übergreifend:** Wird der Swap in einem Tab deaktiviert oder wieder aktiviert, aktualisieren alle anderen Tabs mit Swap-Anzeige bzw. -Button (stress-ng, RAM-Fülltest, mprime, Speichercontroller, Disk-Tabs, Systeminfo) ihren Zustand sofort.
- **RAID-Controller mit Ampel-Bewertung:** Neue Unterseite **ProLiant Info → RAID**. HPE Smart Array (`ssacli`/`hpssacli`) und Broadcom MegaRAID „MR“ (`storcli64`/`storcli2`) werden in **einer** Root-Sitzung strukturiert ausgewertet statt als Rohtext: Controller (Modell, Slot, Firmware, S/N, Status, Cache, Akku/Kondensator, Temperatur), logische Laufwerke (RAID-Level, Status, Rebuild-%, OS-Gerät) und physische Laufwerke (Position, Typ, Größe, Status, Modell, S/N, Firmware, Temperatur, Betriebsstunden, SSD-Rest, Link). Rot bei Ausfall, Predictive Failure, SSD-Rest < 10 % oder defektem/fehlendem Akku bei Cache; orange bei Rebuild, Cache deaktiviert, Medium-Fehlern, hoher Temperatur oder SSD-Rest < 25 %. Rohausgabe bleibt einsehbar; Button **Diagnosebericht (ssaducli) speichern**. RAID-Ampel in Übersicht, Zusammenfassung und HTML-Export.
- **Gesamttest:** Neuer Schritt **„ProLiant / RAID-Controller“** – Momentaufnahme von RAID, IML und Sensoren vor und nach den Tests; neue RAID-Fehler → Schritt FEHLGESCHLAGEN, neue IML-Einträge und Sensorwarnungen werden als Warnung aufgeführt. Ohne Root-Sitzung nur Hinweiszeile.
- Der Button „HPE RAID-Info“ in den Disk-Tabs heißt jetzt **„RAID-Controller-Status“** und springt zur neuen Unterseite (keine doppelte Passwortabfrage mehr). `scripts/collect_hpe_samples.sh` sammelt auf einem ProLiant echte Tool-Ausgaben als Test-Fixtures.
- **Fix NVIDIA-GPU-Lasttest (CUDA):** Seit 0.2.60 brach der Test auf jeder NVIDIA-GPU mit `cuModuleLoadData fehlgeschlagen (218) … PTX JIT compilation failed` ab – ein Kommentar im PTX-Kernel enthielt ein Nicht-ASCII-Zeichen, das `ptxas` ablehnt. Der Kernel wird jetzt im Test mit `ptxas` (falls installiert) assembliert und auf reines ASCII geprüft; bei JIT-Fehlern erscheint künftig die Fehlermeldung des Assemblers im Protokoll.
- **Fix RAID-Werkzeugsuche:** HPE liefert `ssacli`/`ssaducli` mit Modus 0500 (nur root) aus – sie wurden als normaler Benutzer nicht gefunden; `storcli64` aus `/usr/local/sbin` (Live-ISO) wurde in der Root-Sitzung nicht aufgelöst („Programm nicht gefunden“). Beides behoben; `ssacli` ohne Smart-Array-Controller („No controllers detected“) gilt nicht mehr als Fehler, sondern als „kein Controller gefunden“.
- **DCGM-Diagnose lesbar:** Das Ergebnis wird nicht mehr als JSON-Rohtext angezeigt, sondern als Textbericht (Treiber, Seriennummern, je Kategorie und Test ✓/✗/!/– mit GPU, Warnungen und Infos; DE/EN). Die JSON-Rohausgabe steht darunter weiterhin zur Verfügung; der Gesamttest-Bericht enthält nur die Textfassung. Der Formatierer erkennt jetzt auch das Schema von DCGM 4.x (`Metadata`, `entity_group`/`entity_id`, `test_summary`) sowie zusätzliche Top-Level-Schlüssel – zuvor fiel er dabei auf die JSON-Ausgabe zurück.
- **ProLiant Info → RAID scrollbar:** Der RAID-Reiter scrollt jetzt als Ganzes (wie der System-Tab); Tabellen passen ihre Höhe dem Inhalt an, die Rohausgabe hat deutlich mehr Platz.
- **Gesamttest: SATA/SAS/USB-Lesetest.** Neuer optionaler Schritt **„SATA/SAS/USB – vollständiger Lesetest“**: Ausgewählte `/dev/sd*`-Datenträger werden pro Durchlauf komplett gelesen (fio, `--readonly`, keine Schreibzugriffe); SMART-Zähler (Reallocated/Pending/Uncorrectable/CRC bzw. SAS-Defektliste und unkorrigierte Lesefehler) werden vorher und nachher verglichen – jede Verschlechterung → FEHLGESCHLAGEN. Die Laufwerksliste im Gesamttest zeigt jetzt NVMe und SATA/SAS/USB gemeinsam (mit Bus-Typ, HDD/SSD, „eingehängt“). Logische Laufwerke hinter RAID-Controllern liefern ohne Controller-Durchgriff kein SMART – der Lesetest läuft trotzdem, der Vergleich wird nur als Hinweis ausgelassen.
- **Gesamttest: Endzeit.** Neben den Durchläufen lässt sich eine **Endzeit** setzen: Der Plan wiederholt sich, startet nach Erreichen der Endzeit aber keinen Teiltest mehr (ein laufender Teiltest wird zu Ende geführt). Der Bericht nennt die Endzeit und ob sie erreicht wurde; das Stoppen zählt nicht als Fehler.
- **System-Tab: USB-Übersicht.** Neue Tabelle mit allen USB-Controllern, Hubs und Geräten als Baum (Bus → Port), inkl. Hersteller/Produkt, ID, Geräteklasse, ausgehandelter Geschwindigkeit (1,5 Mbit/s … 20 Gbit/s), unterstützter USB-Version, Treiber und Seriennummer. Geräte, die USB 3.x beherrschen, aber nur mit USB-2.0-Geschwindigkeit laufen (falscher Port, Kabel, Hub), werden orange markiert.
- **Dateisystemprüfung: Btrfs.** Btrfs-Dateisysteme lassen sich ausgehängt lesend prüfen (`btrfs check --readonly --check-data-csum`, inkl. Datenprüfsummen); für eingehängte Btrfs zeigt die Statusdiagnose die Fehlerzähler je Gerät (`btrfs device stats`) – Zähler > 0 gilt als Fehler.
- **RAM-Fülltest: Passwortabfrage für Swap repariert.** Die Prüfung „Passwort nötig?“ verließ sich auf `sudo -n -l swapoff`, das bei aktuellen sudo-Versionen Erfolg meldet, obwohl ein Passwort verlangt wird – der Dialog erschien nicht und `swapoff` scheiterte. Jetzt wird geprüft, ob sudo tatsächlich passwortfrei ausführen darf (gültiger Zeitstempel oder NOPASSWD-Regel für `swapoff`/ALL).
- **Dateisystemprüfung: exFAT.** Ausgehängte exFAT-Dateisysteme werden lesend mit `fsck.exfat -n` (exfatprogs) vollständig geprüft.
- **Dateisystemprüfung: NTFS-Basisprüfung.** Ausgehängte NTFS-Volumes lassen sich lesend mit `ntfsfix -n` prüfen (Bootsektor, MFT/MFT-Spiegel, Dirty-Flag). Ein gesetztes Dirty-Flag oder gemeldete Korruption zählt als Befund. Hinweis im Protokoll und in der Hilfe: Eine vollständige NTFS-Strukturprüfung gibt es unter Linux nicht – dafür unter Windows `chkdsk /f`.
- **Dateisystemreparatur entfernt.** Der Button **„Reparieren …“** und der Reparaturpfad des Root-Hilfsprogramms sind entfallen; der Dateisystem-Tab prüft ausschließlich lesend (`e2fsck -n`, `xfs_repair -n`, `btrfs check --readonly`, `fsck.fat -n`, `unsquashfs`, `fsck.vmfs`). Reparaturen verändern Metadaten und können Daten entfernen – sie bleiben eine bewusste Administrator-Entscheidung auf der Kommandozeile. Das HPE-Live-ISO liefert dafür alle Werkzeuge mit (`e2fsprogs`, `xfsprogs`, `btrfs-progs`, `dosfstools`, `ntfs-3g`, `exfatprogs`, `zfsutils-linux`, `gdisk`, `testdisk`); die Hilfe nennt das passende Kommando je Dateisystem.
- **Gesamttest: ZFS-Scrub.** Neuer optionaler Schritt **„ZFS-Scrub (Prüfsummen aller Pools)“** (nur sichtbar, wenn `zpool` installiert ist): startet `zpool scrub` für alle importierten Pools (ONLINE/DEGRADED, nicht schreibgeschützt), zeigt den Fortschritt und wartet auf das Ergebnis; Prüfsummen- oder Gerätefehler → FEHLGESCHLAGEN. Pools im Resilver werden übersprungen, bei Abbruch wird der Scrub gestoppt. Die Root-Sitzung erlaubt dafür ausschließlich `zpool scrub [-s] POOL`.
- **Fix OpenDCDiag im Gesamttest:** Über die Root-Sitzung schrieb OpenDCDiag sein YAML-Protokoll wegen der strengen umask des Brokers als `root:0600` – das Programm konnte es nicht lesen und meldete immer „Protokoll fehlt oder ist nicht lesbar“ (FEHLGESCHLAGEN), obwohl der Lauf im Tab fehlerfrei war. Der Broker übereignet das Protokoll jetzt nach dem Lauf dem Aufrufer.

### Neu in 0.2.60

Ergebnis eines vollständigen Code-Reviews – Sicherheit, Zuverlässigkeit und „falsch-positive“ Ergebnisse:

- **Root-Sitzung (Gesamttest) gehärtet:** `fio` läuft nicht mehr mit beliebigen Argumenten als root, sondern über eine geprüfte Job-Beschreibung mit fester Schlüsselmenge (Schreibzugriffe nur auf `/dev/*`). Alle Root-Jobs starten in eigener Prozessgruppe; **Stopp und Watchdog beenden jetzt den Root-Prozess selbst**, nicht nur das Relay. Autorisierungs- und Antwort-Timeouts statt blockierendem Warten; Arbeitsverzeichnis und FIFOs nur noch für den Aufrufer lesbar. RAS-Abfragen über die Root-Sitzung funktionieren jetzt (vorher stillschweigend ohne Ergebnis); unlesbare RAS-Daten werden als Warnung gemeldet.
- **Testläufe:** Ergebnisse werden genau einmal finalisiert, spät gepufferte Fehlerzeilen gehen beim Stoppen nicht mehr verloren. fio-Watchdog mit Reserve für die Verify-Phase; bei `numjobs > 1` disjunkte Bereiche je Job. pkexec-Fallback beendet fio zuverlässig.
- **Keine falschen „BESTANDEN“ mehr:** SMART ohne Gesamtstatus oder mit smartctl-Fehlerbits → UNBEKANNT/FEHLER statt gesund; NVMe ohne `critical_warning` → nicht bestimmbar; DCGM ohne auswertbares JSON und NVIDIA-Inventar ohne GPU trotz erkannter Hardware → FEHLGESCHLAGEN; Vulkan-Test ohne tatsächlich geprüften Speicher → NICHT AUSSAGEKRÄFTIG; Netzwerktest meldet fehlende Pflichtmesswerte. Kernel-Journal wird vollständig paginiert (vorher konnten bei Fehler-Bursts Einträge verloren gehen).
- **RAM-Test:** Abgestürzte oder vom OOM-Killer beendete Fill-Prozesse werden gemeldet (Umgebungsproblem → NICHT AUSSAGEKRÄFTIG, sonst FEHLER); cgroup-Speicherlimits werden berücksichtigt; unter Speicherdruck verworfene Bereiche zählen zur Abdeckung (unter 90 % → nicht aussagekräftig).
- **GPU-Tests:** Muster und Fehlerzähler sind nicht mehr auf 32 Bit begrenzt (Aliasing ab 16 GiB); Rechenlast wird gegen eine CPU-Referenz geprüft statt nur Threads untereinander.
- **Oberfläche:** Fenster schließen wartet auf laufende Test-Threads; swapoff, fastfetch, lsblk und Aushängen blockieren die Oberfläche nicht mehr; NVIDIA-Telemetrie erzeugt keine Thread-Lecks mehr; Passwortabfrage im Tab System erholt sich von Startfehlern; HTML-Bericht escaped Anker-IDs; polkit-Agent-Erkennung repariert (lange Prozessnamen wurden nie erkannt). 18 fehlende englische Übersetzungen ergänzt; ein Test erzwingt künftig vollständige Übersetzungen.
- Manuell abgebrochene Tests werden **bis zum Abbruch bewertet** („keine Fehler erkannt“ bzw. gefundene Probleme) statt nur „abgebrochen“ zu melden – wie bisher schon beim Festplattentest: NVMe-Lese-Test, GPU-Lasttest (Vulkan und CUDA) sowie DCGM-Diagnose (Teilergebnis der bereits abgeschlossenen Prüfungen).

### Neu in 0.2.59

- Korrigiert: Hardwaretest konnte wenige Sekunden nach dem Start abstürzen (SIGSEGV), meist beim ersten Start nach der Installation. Ursache war die automatische Update-Prüfung im Hintergrund: Ihr Worker-Objekt wurde von Qt und Python doppelt gelöscht. Der Update-Thread gehört jetzt allein Qt und wird erst nach seinem Ende freigegeben.

### Neu in 0.2.58

- Tab **System** erweitert (ohne root): **PCIe-Verbindungen** mit aktueller und möglicher Generation/Breite (rot: Breite reduziert, orange: Generation reduziert – im Leerlauf oft normal), **Netzwerk** (Modell, Treiber, Firmware, MAC, Zustand, Geschwindigkeit), **Grafik** (Treiber, VRAM, VBIOS), **Speicher-Controller** (mit Firmware) und **Datenträger** (Modell, Größe, HDD/SSD, Seriennummer, Firmware, Sektorgröße). Auch in Zwischenablage und Gesamttest-Bericht.
- Tab **GPU (Vulkan)**: Fortschrittsbalken läuft zeitbasiert; Abbruch beendet auch die automatisch gestartete Liveüberwachung und wird protokolliert; Liveüberwachung zeigt Uhrzeit, Maximaltakt und bei integrierten GPUs die CPU-Pakettemperatur; „Überwachung stoppen“ zeigt den Zeitpunkt. CUDA-Lasttest mit zeitbasiertem Fortschritt.
- Integrierte **Intel-GPUs** (i915/xe): Liveüberwachung zeigt jetzt die **Auslastung** (aus `/proc/*/fdinfo` wie `gputop`, ohne root für Prozesse des eigenen Benutzers) und die **Leistung** über den RAPL-Zähler „uncore“. RAPL ist meist nur für root lesbar – dann erscheint „Leistung nur mit root“; im HPE-Live-ISO gibt eine udev-Regel die Zähler frei.
- **Gesamttest**: optionaler Schritt **GPU-Lasttest (Vulkan)** mit einstellbarer Dauer (Standard 300 s) auf allen Vulkan-GPUs; ohne Vulkan-Loader deaktiviert, ohne Vulkan-GPU übersprungen.
- Ist `dcgmi` nicht installiert, werden der Bereich **DCGM-Diagnose** im Tab NVIDIA GPU und die DCGM-Option im Gesamttest ausgeblendet statt nur deaktiviert.

### Neu in 0.2.57

- Neuer Tab **GPU (Vulkan)**: herstellerneutraler GPU-Lasttest für AMD, Intel und NVIDIA über Vulkan Compute (Mesa RADV/ANV/NVK oder Herstellertreiber) – VRAM-Mustertest mit Fehlerzählung auf der GPU und Rechenlast mit Ergebnisvergleich; bei integrierten GPUs höchstens 25 % des gemeinsamen Speichers. Liveüberwachung aus sysfs (Temperatur, Leistung, Auslastung, Takte, VRAM, Lüfter). Empfohlen: `libvulkan1`, `mesa-vulkan-drivers`, `python3`.
- Tab **System**: Fehlt ein cpufreq-Treiber (Takt regelt das BIOS, z. B. HPE Power Regulator), kommen Ist-Takt aus `/proc/cpuinfo` sowie Nenn- und Maximaltakt aus DMI Typ 4. Die sudoers-Regel des `.deb` erlaubt dafür jetzt `dmidecode -t 1 -t 4 -t 16 -t 17`.
- Korrigiert: Nach Abbruch eines GPU-Lasttests (CUDA) lief der Kindprozess je GPU weiter. Signale werden jetzt weitergereicht, Kindprozesse enden auch beim harten Beenden des Workers.

### Neu in 0.2.56

- Neuer Tab **System**: Prozessor je Sockel (Family/Model/Stepping, Microcode, Kerne mit P/E-Aufteilung bei Hybrid-CPUs, Threads, Taktbereich, Befehlssätze, CPU-Fehler laut Kernel, alle Flags), Caches, Takt je logischer CPU, Mainboard/BIOS und Speichermodule je Steckplatz (Größe, Typ, Nenn-/Ist-Takt, Rank, ECC, Spannung, Hersteller, Teile- und Seriennummer). Kopierbar als Text. Das `.deb` installiert `/etc/sudoers.d/hardwaretest-dmidecode`: Mitglieder der Gruppe `sudo` dürfen genau `dmidecode -t 1 -t 16 -t 17` ohne Passwort ausführen; sonst „Speichermodule mit Passwort auslesen“ (pkexec). Neue Abhängigkeit: `dmidecode`.
- Gesamttest: Text- und HTML-Bericht beginnen mit einem Abschnitt **System** (CPU, Mainboard/BIOS, Speichermodule).
- Gesamttest: neue **CPU-Lastart „Mischlast mit Ergebnisprüfung“** (stress-ng cpu, matrix, matrix-3d, fma, fp, vecmath, vecwide, vecfp, trig, l1cache, zlib mit `--verify`). Die Volllast prüft jetzt ebenfalls die Ergebnisse. Falsche Ergebnisse („fail:“ / „failed: N“) lassen den Schritt mit Nennung der Lastart fehlschlagen.
- Korrigiert: Die niedrige Priorität im Tab stress-ng und im Speichercontroller-Test wurde mit `stress-ng --nice 19` gesetzt – das startet aber 19 zusätzliche „nice“-Worker. Jetzt per `nice -n 19`.

### Neu in 0.2.55

- OpenDCDiag funktioniert auch mit der privilegierten Sitzung des Gesamttests: Beide mitgelieferten CPU-Varianten sind mit eng begrenzten Diagnoseparametern freigegeben. Bisher konnte der Schritt mit „Befehl nicht erlaubt“ sofort scheitern, sobald etwa NVMe oder das Monitoring eine gemeinsame Root-Sitzung benötigte. Das temporäre Protokollverzeichnis bleibt jetzt benutzerprivat statt für alle Benutzer beschreibbar. Leere oder unvollständige YAML-Protokolle gelten nicht mehr als bestanden.
- CPU-Anzeige unterscheidet physische Kerne, logische CPUs/Threads und Kern-Temperatursensoren ausdrücklich (z. B. 32 Kerne / 64 logische CPUs / 32 Kernsensoren bei Hyperthreading).
- stress-ng zeigt **CPU-Worker (Threads)** statt „CPU-Kerne“. Die GUI-Reserve ist als Minimum in logischen Threads beschriftet; die tatsächliche Reserve inklusive HT-Partner steht oben. Bei 64 logischen CPUs und einer Mindestreserve von 1 sind normalerweise 62 Test-Threads verfügbar, nicht 63. Worker-Limit und Start berücksichtigen die tatsächlich erlaubte CPU-Maske.
- Beschriftungen, Tooltips und Hilfe in Deutsch und Englisch aktualisiert. Fehlende Sensoren werden nicht als fehlende CPU-Kerne dargestellt.

### Neu in 0.2.54

- Neuer Tab **OpenDCDiag** (Intel, Apache 2.0): sucht stille Datenfehler je CPU-Kern und nennt fehlerhafte Kerne mit Sockel, Kern und CPU-Nummer. Auch als optionaler Schritt im Gesamttest (läuft dort über die Root-Sitzung). Mitgeliefert in `.deb` und AppImage, für CPUs ohne AVX2 zusätzlich als generische Variante; gebaut mit `scripts/build_opendcdiag.sh`. Das `.deb` installiert `/etc/sudoers.d/hardwaretest-opendcdiag`: Mitglieder der Gruppe `sudo` starten OpenDCDiag damit ohne Passwort als root (MSR-Tests, Intel In-Field Scan); Tab und Gesamttest nutzen das automatisch per `sudo -n`.

### Neu in 0.2.53

- Alle Tests werten neue Hardwarefehler aus **rasdaemon** aus (CPU-Machine-Checks, EDAC-Speicherfehler, PCIe-AER, aussortierte Speicherseiten), die während des Tests protokolliert wurden: unkorrigierbare Fehler → Test fehlgeschlagen, korrigierte → Warnung im Protokoll und im Bericht des Gesamttests. Ohne rasdaemon ändert sich nichts; die Datenbank wird bei Bedarf über root (`sudo -n` bzw. die Root-Sitzung des Testplans) gelesen.

### Neu in 0.2.52

- Temperaturen: Bei mehreren Sockeln tragen alle Core-Namen das Sockel-Präfix (S0/S1). Bisher fehlte es bei Core-IDs, die nur auf einem Sockel vorkommen (lückenhafte IDs bei Xeon, z. B. „Core 8“), teils mit falscher Sockelzuordnung.
- RAM-Fülltest: Testspeicher wird zuverlässig freigegeben. Er liegt als Shmem vor (in btop/free „Cached“) und blieb auf großen Servern teils komplett belegt; jetzt wird er per `MADV_REMOVE` sofort ausgestanzt, auch wenn noch eine Referenz hängt oder Teile ausgelagert sind.
- RAM-Fülltest: Nach der Freigabe wartet der Test, bis der Kernel den Speicher wieder als verfügbar meldet (bei TB-RAM dauert das; ca. 1 s je 8 GB, max. 10 min). Wartezeit bzw. Shmem/Mlocked-Werte stehen im Protokoll.
- RAM-Fülltest: Die Abbruchfrist nach Testende wächst mit der RAM-Größe (2 TB: ca. 17 min), damit große Server nicht vor Ende der Freigabe hart beendet werden.

### Neu in 0.2.51

- ProLiant Info: HPE Synergy-Compute-Module (z. B. Synergy 480/660 Gen10) werden erkannt; Sensoren, Ereignisprotokoll und IML werden automatisch ausgelesen.
- Dateisystem-Tab: VMware-ESXi-Datenspeicher (VMFS3/5/6) werden lesend geprüft (`fsck.vmfs6`/`fsck.vmfs`, nur Metadaten; Reparatur nur mit VMware VOMA unter ESXi).
- Dateisystem-Tab: Das Label im Bootsektor der ESXi-Bootbanks (vfat) wird nicht mehr als Fehler gemeldet und bei einer Reparatur nicht gelöscht.

### Neu in 0.2.50

- RAM-Fülltest erweitert (Ideen aus adaptive-ramtest): Speicher wird per mlock gegen Auslagern gesperrt, Moving Inversion, 64-Bit-Datenleitungstest, Muster „Address invertiert“, Verwerfen von Blöcken bei Speicherdruck statt Einfrieren, OOM-Schutz für OS/GUI, physische Adresse bei Fehlern (als root).
- RAM-Fülltest: Die zwei für OS/GUI frei gelassenen Kerne wechseln jeden Zyklus, sodass alle Kerne testen.
- Hilfe: Funktionsweise und Grenzen des RAM-Fülltests ausführlich erklärt.

### Neu in 0.2.49

- NVIDIA-Tab übersichtlicher: Liste der unterstützten GPUs in die Hilfe verschoben, Tab scrollt bei kleinen Fenstern statt Elemente zu stauchen.

### Neu in 0.2.48

- NVIDIA-Tab: neuer **GPU-Lasttest** für alle NVIDIA-GPUs ab Turing (auch GeForce), ohne DCGM und ohne CUDA-Toolkit – Rechenlast mit Ergebnisvergleich und VRAM-Mustertest, nur der NVIDIA-Treiber (libcuda) wird benötigt.

### Neu in 0.2.47

- Core-Details: Die gewählte Seite bleibt bei Aktualisierungen erhalten (sprang vorher nach ca. 2 s auf Seite 1 zurück).
- Hilfe: Erklärung der Core-Nummerierung (Hardware-Core-ID, Lücken bei Hybrid-CPUs/Hyperthreading).

### Neu in 0.2.46

- ECC-Anzeige: Alte BMC-Einträge werden zuverlässig als „älter“ erkannt; die Anzeige springt nicht mehr zwischen Grün und Rot.
- Core-Details: Seitengröße passt sich dem sichtbaren Platz an, Blättern funktioniert auch bei 40/80 Cores; doppelte Core-Namen erhalten Socket-Präfix (S0/S1); Punkt statt Emoji.

### Neu in 0.2.45

- ECC-Anzeige: BMC-Protokoll (iLO/IPMI) wertet nur Ereignisse, die seit App-Start neu hinzukommen; ältere Einträge werden nur gezählt (Details im ProLiant-Info-Tab).
- Core-Details: Blättern-Buttons sitzen jetzt in der Kopfzeile und sind auch auf kleinen Bildschirmen (z. B. iLO-Konsole 1024×768) erreichbar.
- Hilfe: Abschnitt zum RAM-Fülltest mit Hinweis auf ausreichend freien Speicher fürs OS bei manueller Speichergröße.

### Neu in 0.2.44

- RAM-Fülltest friert große Server nicht mehr ein: Mindestreserve 1 % des RAM (1–8 GB), zwei Kerne bleiben frei, Worker laufen mit niedriger Priorität.

### Neu in 0.2.43

- RAM-Fülltest schreibt parallel auf allen nutzbaren CPU-Kernen (geteilter Speicher, je Prozess an eine CPU gebunden).
- Kern-Rotation: Die Speicherblöcke wechseln je Zyklus die CPU; geprüft wird von anderen CPUs als geschrieben – auf Mehrsockel-Servern abwechselnd lokale und entfernte Zugriffe.

### Neu in 0.2.42

- RAM-Fülltest: Verifizieren läuft parallel auf allen nutzbaren CPU-Kernen (automatisch ermittelt).
- Fortschrittsanzeige alle 10 s beim Schreiben und Verifizieren.

### Neu in 0.2.41

- RAM-Fülltest: Reicht die Testzeit nicht für einen vollen Zyklus, wird nur ein Teil geschrieben und dieser geprüft – kein Abbruch mit Exit-Code -9 mehr auf Servern mit viel RAM.
- Nach Ablauf der Testzeit bleiben bis zu 300 s zum Freigeben des Speichers.

### Neu in 0.2.40

- NVIDIA-Tab zeigt die unterstützten GPU-Typen (Treiber 595, Open-Kernelmodul ab Turing).

### Neu in 0.2.39

- IML wird über `ilorest serverlogs --selectlog=IML --json` gelesen – funktioniert ohne iLO-Zugangsdaten auch im iLO-Sicherheitsmodus.
- Kategorie zeigt zusätzlich die IML-Klasse (z. B. „Hardware / Network“).

### Neu in 0.2.38

- ProLiant Info → IML: Fehlermeldungen von iLOrest werden gekürzt in einer eigenen Zeile angezeigt (statt das Suchfeld zu verdrängen), mit Hinweis bei CHIF-Fehlern (fehlende `ilorest_chif.so` bzw. „Require Host Authentication“ im iLO).

### Neu in 0.2.37

- ProLiant Info: neue Unterseite **Integrated Management Log (IML)** – liest das HPE-IML lokal über iLOrest (ohne iLO-Passwort, Redfish über den CHIF-Kanal) mit HPE-Klartextmeldungen, Bewertung, „repariert“-Kennzeichnung, Filter (Fehler/Warnungen, nur offene, nur Speicher), Suche und HTML-Export. IML-Fehler fließen in die Zusammenfassung ein.

### Neu in 0.2.36

- Tab „ECC / Speicher“ heißt jetzt **„ProLiant Info“**: liest per ipmitool vom HPE iLO Systemdaten (Modell, Seriennummer, BIOS, iLO-Firmware), alle Sensoren und das komplette Ereignisprotokoll (IPMI SEL) mit Bewertung, Filter, Suche und HTML-Export; Unterseite Speicher/ECC wie bisher.
- Automatisches Auslesen auf ProLiant beim Start und alle 5 Minuten; ohne ProLiant erscheint ein Hinweis, dass die iLO-Informationen nicht aktiv sind.

### Neu in 0.2.35

- ECC-Tab liest auf Servern mit BMC (IPMI) automatisch beim Start und alle 5 Minuten aus (einmalige Passwortabfrage).
- Die ECC-Anzeige in den Testtabs (stress-ng, Prime95 …) zeigt zusätzlich die Fehler aus dem BMC-Protokoll (z. B. HPE iLO).
- squashfs kann auch eingehängt geprüft werden (nur lesend, keine Reparatur).

### Neu in 0.2.34

- Neuer Tab **ECC / Speicher**: ECC-Typ und Module (dmidecode), Kernel-EDAC-Zähler und Speicherereignisse aus dem BMC-Protokoll (ipmitool, z. B. HPE iLO).
- Langzeitmonitoring und Gesamttest erfassen auf Servern mit BMC Lüfterdrehzahlen und ECC-Ereignisse per IPMI.
- Benötigt die Pakete `ipmitool` und `dmidecode`.

### Neu in 0.2.33

- **Dateisystem-Tab**: Strukturprüfung jetzt auch für XFS (`xfs_repair -n`), vfat (`fsck.fat -n`)
  und squashfs (`unsquashfs`, liest alle Datenblöcke); Reparatur für XFS (ohne `-L`) und vfat.
  squashfs ist schreibgeschützt und wird nur geprüft.
- **Disk (Geräte)**: Ein manueller Abbruch wird nicht mehr als Fehler protokolliert.

### Neu in 0.2.32

- **Einmalige Passwortabfrage**: Der Gesamttest fragt das Administrator-Passwort gleich
  zu Beginn ab (NVMe, NVMe-Lesetest, DCGM); danach kann man vom Rechner weggehen.
- **NVMe-Tab**: keine wiederholten Passwortabfragen mehr während laufender Tests;
  der NVMe-Lesetest schlägt nicht mehr durch den Watchdog während der Passworteingabe fehl
  und zeigt „Warte auf Passwort-Eingabe“ an.
- **AppImage-Fixes**: Systemreport (JSON) sowie Dateisystem- und ZFS-Prüfung funktionieren
  wieder mit Root-Rechten aus dem AppImage.

### Neu in 0.2.31

- **Sicherheit destruktiver Plattentest**: keine Vorauswahl, gesperrte Systemplatte und
  belegte Laufwerke (Mounts, Swap, LVM/LUKS, mdraid, ZFS), erneute Prüfung samt
  Seriennummer/WWN direkt vor dem Start; Button „Datenträger aushängen…“.
- **Stabilität**: Stopp und Temperatur-Notabschaltung ohne zweite Kennwortabfrage,
  kein Watchdog-Abbruch langer Plattentests, echter fio-Fortschrittsbalken, Tests werden
  beim Schließen beendet, Swap wird gezielt wieder aktiviert.
- **RAM-Fülltest**: Größe auf verfügbaren Speicher begrenzt, kein „bestanden“ ohne Prüfung.
- **Update-Prüfung** beim Start und über Hilfe → „Nach Updates suchen“.
- Feste Versionen und SHA-256-Prüfung der AppImage-Build-Werkzeuge.

### Neu in 0.2.30

- **Gesamttest**: Netzwerk-Grenzwerte für Paketverlust, mittlere Latenz, Jitter und
  Mindestdurchsatz; Auswertung in jedem Durchlauf, fehlende Werte sind kein „bestanden“.
- **Langzeitmonitoring**: gespeicherte individuelle Temperaturgrenzen, gemeinsam
  auch im Begleitmonitoring nutzbar. Allgemeiner Standardwert für übrige Sensoren.
- Live-Verlaufsdiagramm mit Sensorauswahl sowie eingebettete Diagramme im HTML-Bericht;
  verdichtete Zeitabschnitte mit Min/Max/Mittelwert erhalten gemessene Spitzen.
  CSV bleibt vollständig, keine zusätzliche Chart- oder Python-Installation nötig.
- Zielplattformen bleiben Ubuntu 24.04/26.04, ohne venv auf dem Zielsystem.

### Neu in 0.2.29

- **Gesamttest**: 1–10.000 Durchläufe des gewählten Plans, getrennte Fortschrittsanzeige
  für Ablauf und aktuellen Teiltest; fortlaufende CSV mit einer Zeile je Teiltest.
- Optionaler vollständiger **NVMe-Lesetest** im Plan: Checkbox-Auswahl der Laufwerke,
  erneute Identitätsprüfung, paralleles Lesen der ausgewählten Namespaces, zusätzliche
  fio-Schreibsperre und SMART-Vergleich vorher/nachher.
- Kompakte Text-/HTML-Berichte mit Ergebniszählern, letztem Ergebnis und erstem Fehler;
  Bildschirmprotokoll und Lesetest-Ausgaben bleiben begrenzt.
- Keine unbeaufsichtigten Dateisystemreparaturen oder ZFS-Imports. Details und Grenzen:
  [Langzeittestpläne](docs/LONG_TEST_PLANS.md).

### Neu in 0.2.28

- **Dateisystem → ZFS-Pools**: Auswahl importierter Pools, Status mit Lese-/Schreib-/
  Prüfsummenfehlern, Scrub starten/fortsetzen/stoppen, Fortschritt und abschließendes
  Text-/HTML-Protokoll; Messverlauf als CSV.
- Scrubs prüfen Daten und Prüfsummen im laufenden Betrieb und können Schäden mit
  redundanten Kopien automatisch korrigieren. Optional: `zfsutils-linux` und passende
  ZFS-Kernelunterstützung. Es werden keine Pools importiert oder erzeugt.

### Neu in 0.2.27

- Eingehängte Dateisysteme, einschließlich ext4 und FAT/EFI, können jetzt eine
  lesende Statusdiagnose mit Speicherbelegung und verfügbaren Fehlerhinweisen ausführen.
  Vollständige Strukturprüfung und Reparatur bleiben Offline-Aktionen.
- HTML-Berichte öffnen einen installierten Webbrowser unabhängig von der
  HTML-Dateizuordnung (z.B. ChatGPT).

### Neu in 0.2.26

- Langzeitmonitoring mit fortlaufender CSV-Aufzeichnung, kompaktem Text-/HTML-Bericht,
  Temperaturgrenze und Erkennung neuer ECC-/Kernel-/ext4-Fehler; auch im Gesamttestplan.
- Optionaler Sicherheitsabbruch laufender App-Stresstests bei Temperaturgrenze,
  neuen unkorrigierbaren ECC-Fehlern oder Ausfall des Monitorings.
- Separater Dateisystem-Tab: Offline-Prüfung für ext2/ext3/ext4 sowie ausdrücklich
  bestätigte konservative Reparatur. Keine Reparatur eingehängter Dateisysteme,
  kein automatisches Aushängen und keine Reparatur im unbeaufsichtigten Testplan.
- Details, Grenzen und Vorgehen: [Dateisystem und Monitoring](docs/FILESYSTEM_MONITORING.md).

## Lizenz

Hardwaretest ist unter der [MIT-Lizenz](LICENSE) veröffentlicht. Der vollständige
Lizenztext befindet sich in der Datei `LICENSE`.

Mitgeliefertes OpenDCDiag: Apache License 2.0, siehe `vendor/opendcdiag/LICENSE` und
`vendor/opendcdiag/LICENSE.3rdparty` (im Paket unter `_internal/vendor/opendcdiag/`).

## Installation

### Variante A – Debian-Paket (empfohlen)

Für die Installation wird nur die Datei `hardwaretest_0.2.52_amd64.deb` benötigt.

1. Die DEB-Datei herunterladen.
2. Im Dateimanager doppelt anklicken und im Paketinstallationsprogramm
   **Installieren** wählen.
3. Das Administratorkennwort eingeben. Danach **Hardwaretest** im Anwendungsmenü starten.

Linux Mint hat dafür bereits ein Paketinstallationsprogramm. Unter Ubuntu muss
ein grafischer Installer für lokale DEB-Dateien vorhanden sein. Falls sich beim
Doppelklick ein Archivprogramm öffnet, über **Öffnen mit** den Paketinstaller wählen,
sofern er installiert ist.

APT installiert die nativen System-Abhängigkeiten wie `stress-ng`, `fio` und
`btop` automatisch aus den eingerichteten Ubuntu-Paketquellen. Python, PySide6
und die Python-Module sind bereits im Programm enthalten: Auf dem Zielsystem
werden weder eine venv angelegt noch `pip` oder eine bestimmte System-Python-
Version benötigt. Das Paket richtet den Befehl `hardwaretest` und einen
Desktop-Eintrag ein. Deinstallation mit `sudo apt remove hardwaretest`.

Ein zusätzliches Installationsskript gehört nicht zur DEB-Auslieferung.

> Das Paket wird aus dem Repo mit `bash scripts/build_deb.sh` erzeugt
> (benötigt `dpkg-dev`, PyInstaller und die Entwicklungsumgebung nur auf dem
> Build-System).

### Variante A2 – RPM-Paket (AlmaLinux, RHEL, Rocky 9+)

```bash
sudo dnf install -y epel-release          # stress-ng, btop, fastfetch liegen in EPEL
sudo dnf install -y ./hardwaretest-0.3.7-1.x86_64.rpm
```

dnf installiert die nativen Abhängigkeiten automatisch. Die sudoers-Regeln
gelten für Mitglieder der Gruppe `wheel`. Deinstallation mit
`sudo dnf remove hardwaretest`. Gebaut wird das RPM mit
`bash scripts/build_in_container.sh rpm` (benötigt podman oder docker; baut in
`almalinux:9`, damit die Binärdateien auf EL9 und neuer laufen).

### Variante B – AppImage

Das AppImage enthält ebenfalls Python, PySide6 und alle Python-Module. Es kann
ohne Installation einer Python-Laufzeit gestartet werden:

```bash
chmod +x Hardwaretest-0.2.52-x86_64.AppImage
./Hardwaretest-0.2.51-x86_64.AppImage
```

Native Diagnoseprogramme müssen auf der jeweiligen Distribution weiterhin über
deren Paketverwaltung installiert werden. Das AppImage wird mit
`bash scripts/build_appimage.sh` erzeugt.

### Variante C – Zip + Installationsscript (Legacy)

#### 1. Zip entpacken nach `/opt/hardwaretest`

```bash
sudo unzip Hardwaretest-v0.2.31.zip -d /opt/hardwaretest
```

### 2. In das Verzeichnis wechseln

```bash
cd /opt/hardwaretest
```

### 3. Installationsscript ausführen

```bash
sudo bash scripts/install_hardwaretest.sh
```

### Was passiert dabei?

Das Script durchläuft **6 Schritte** und fragt vor jeder Aktion nach:

| Schritt | Beschreibung |
|---|---|
| **1/6** | **APT-Pakete prüfen** – zeigt fehlende Pflicht- und optionale Pakete an, installiert auf Bestätigung |
| **2/6** | **Prime95 (mprime)** – prüft ob vorhanden, bietet Download an |
| **3/6** | **Python venv** – nur diese Legacy-Variante erstellt `.venv/` und installiert PySide6, psutil etc. |
| **4/6** | **CLI-Starter** – legt `/usr/local/bin/hardwaretest` an |
| **5/6** | **Desktop-Starter** – `.desktop`-Datei + SVG-Icon + Verknüpfung auf dem Desktop |
| **6/6** | **Zusammenfassung** mit HPE ssacli-Hinweis |

### Optionen

```bash
# Interaktiv (Standard) – fragt vor jeder Installation
sudo bash scripts/install_hardwaretest.sh

# Unbeaufsichtigt – alles ohne Rückfrage
sudo bash scripts/install_hardwaretest.sh --yes
```

### Nach der Installation starten

- **Terminal:** `hardwaretest`
- **Desktop:** Klick auf **Hardwaretest** im Anwendungsmenü (oder Desktop-Icon)

### Voraussetzungen

- **Ubuntu/Debian-Derivat** (apt-get muss vorhanden sein)
- **sudo-Berechtigung** (für Paketinstallation und pkexec-Setup)
- Internetverbindung (für Paket-Downloads und Prime95)

> **Hinweis:** Das Installationsscript ergänzt automatisch `.profile`/`.bashrc` um `export PATH="$HOME/.local/bin:$PATH"`. Öffne danach ein neues Terminal, damit alle Befehle gefunden werden.

## Features

### CPU / RAM Tests
- **stress-ng** – 5 Modi: CPU, RAM, RAM-Bandbreite, Combined, Cache. RAM-Tests mit `--verify` für Datenintegrität.
- **Prime95 (mprime)** – Torture-Tests (Blend/SmallFFT/LargeFFT) mit Thread-/Modus-Wahl und Live-Logausgabe.
- **Speichercontroller** – 7 Stressoren: cache, membarrier, atomic, tlb-shootdown, numa, lockbus, mcontend. Besonders wichtig für Dual-Socket-Systeme.

### Festplatten Tests
- **Disk (Datei)** – Sichere fio-Workloads gegen temporäre Dateien. Bei Schreib-Workloads automatische CRC32c-Verifikation.
- **Disk (Geräte)** – Sektorweises Lesen aller Blöcke mit `continue_on_error=read` – erkennt defekte Sektoren nach RAID-Rebuilds.
- **Disk (Destruktiv)** – Write+Verify-Tests mit optimiertem `verify_backlog` für große RAID-Volumes. Deutliche Sicherheitsabfragen.
- **I/O-Engine** – Automatische Erkennung von `io_uring` (NVMe auf Gen10+) mit Fallback auf `libaio`.
- **SMART-Info** – Gesundheitsstatus, Temperatur, Betriebsstunden, Reallocated/Pending Sectors via `smartctl`.
- **NVMe-Diagnose** – Modell, Seriennummer, Firmware und NVMe-SMART-Werte; kurze oder erweiterte Selbsttests mit Protokollauswertung als bestanden/fehlgeschlagen.
- **NVMe-Lesetests** – Nicht-destruktiver sequenzieller oder zufälliger Lese-Benchmark sowie ein vollständiger Lese-Test über jedes Block des ausgewählten NVMe-Namespace.
- **NVIDIA-GPU-Diagnose** – Erkennt mehrere NVIDIA-GPUs, zeigt Treiber, CUDA-Kompatibilität, VRAM, Temperatur, Leistung, ECC, Taktraten und PCIe-Anbindung. Eine Liveüberwachung erfasst Auslastung, Temperatur, Leistung, VRAM und Taktraten auch während optionaler NVIDIA-DCGM-Diagnosen.
- **GPU-Lasttest (Vulkan)** – AMD-, Intel- und NVIDIA-Grafik ohne Herstellertools: VRAM-Mustertest, Rechenlast mit Ergebnisvergleich, Liveüberwachung aus sysfs.
- **Netzwerkdiagnose** – Zeigt Adapter, Treiber, PCI-Adresse, Linkstatus/-geschwindigkeit, Duplex, MTU und IP-Adressen. Ping ermittelt Paketverlust, Latenz und Jitter; iperf3 misst Durchsatz und Retransmits. Alle Messwerte können gegen Pass/Fail-Grenzen geprüft werden.
- **Gesamttestplan** – Führt frei auswählbare CPU-, RAM-, NVMe-, Netzwerk- und NVIDIA/DCGM-Prüfungen automatisch nacheinander aus. Jeder Lauf erzeugt ein Klartext- und ein druckoptimiertes HTML-Protokoll; der Browser kann dieses zusätzlich als PDF speichern. Netzwerk-Dauerläufe werden kompakt mit Summenwerten statt einzelner Ping-Pakete oder iperf3-Intervalle protokolliert.
- **ProLiant Info** – iLO-Systemdaten, Sensoren, Ereignisprotokoll, IML, ECC sowie **RAID-Controller** (HPE Smart Array via `ssacli`, MegaRAID via `storcli64`) mit Ampel-Bewertung und ssaducli-Diagnosebericht.

Für NVIDIA-Inventardaten muss ein geeigneter NVIDIA-Treiber installiert sein und
`nvidia-smi` funktionieren. Aktive GPU-Diagnosen benötigen zusätzlich NVIDIA DCGM
(`datacenter-gpu-manager-4-cuda12` bzw. `-cuda13`, passend zur von `nvidia-smi`
gemeldeten CUDA-Hauptversion) und den gestarteten Dienst `nvidia-dcgm`. CUDA Toolkit,
PyTorch und Docker werden nicht benötigt.
Eine vollständige Installations- und Prüfanleitung steht in
[`docs/NVIDIA_GPU.md`](docs/NVIDIA_GPU.md).

### Monitoring & Diagnose
- **Temperatur-Widget** – Pro-Socket-Zusammenfassung (Min/Ø/Max), kompakte Ansicht ab 8+ Sensoren, optimiert für Dual-Socket mit 90+ Cores.
- **ECC/EDAC-Anzeige** – Korrigierbare und unkorrigierbare Speicherfehler (bei ECC-RAM).
- **Pass/Fail-Erkennung** – Automatische Bewertung nach jedem Test anhand von 25+ Fehlermustern (fio-Verify, SCSI, HPE RAID).
- **Kernel-Logs, MCE-Logs, btop** – Direktstart aus der GUI.
- **Swap deaktivieren** – Ein-Klick-Funktion für echte RAM-Tests.

### Weitere Features
- **Info-Panel** – CPU/RAM-Zusammenfassung, Fastfetch, JSON-Export, lshw/lspci-Schnellzugriff.
- **Hilfe-Tab** – Zweisprachige Dokumentation (Deutsch/English) aller Tests und Optionen.
- **Reporting** – `scripts/collect_system_report.sh` erstellt strukturierte JSON-Dateien.
- **Desktop-Integration** – SVG-Icon, .desktop-Datei, Unterstützung für GNOME, KDE, XFCE, Cinnamon, MATE, FVWM (FunOS).

## Manuelles Setup für Entwicklung

Falls das Installationsscript nicht genutzt werden soll:

```bash
sudo apt install python3 python3-venv python3-pip python3-dev build-essential \
    libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-shape0 libxcb-xfixes0 libxkbcommon-x11-0 \
    stress-ng fio lshw pciutils smartmontools jq
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e .
python -m hardwaretest
```

Zusätzlich empfohlen: Prime95 (`~/Prime95/mprime`), btop, lm-sensors, edac-utils, nvme-cli. Auf HPE ProLiant: `ssacli` aus dem [HPE SDR Repository](https://downloads.linux.hpe.com/SDR/project/mcp/).

## Portabler Build ohne Python auf dem Zielsystem

Nur der Build-Rechner benötigt Python, eine venv und PyInstaller. Ein fertiges
DEB oder AppImage bringt seine eigene Python-Laufzeit und PySide6 mit. Die
Build-Werkzeuge werden einmalig mit `requirements-build.txt` ergänzt:

```bash
source .venv/bin/activate
python -m pip install -r requirements-build.txt -e .
bash scripts/build_deb.sh
bash scripts/build_appimage.sh
```

Der eingebaute Test `hardwaretest --self-check` zeigt die eingebetteten
Versionen und prüft, ob die wichtigen Ressourcen im Programmbündel vorhanden
sind. Native Hardwarewerkzeuge bleiben bewusst APT-/Distributionspakete, damit
Treiber und systemnahe Programme zur jeweiligen Linux-Version passen.

## Tests & Code-Qualität

```bash
source .venv/bin/activate
pip install pytest ruff        # Dev-Werkzeuge
python -m pytest -q            # Unit-Tests (tests/)
ruff check hardwaretest tests  # Linting (Konfiguration in pyproject.toml)
```

Die Ruff-Konfiguration (`[tool.ruff]` in `pyproject.toml`) prüft die Regelgruppen
`F, E, B, SIM, RUF` bei einer Zeilenlänge von 100. Bewusst deutschsprachige
Umlaute/Sonderzeichen (`RUF001–003`) sind ausgenommen.

## Release-ZIP bauen

Das Installations-ZIP (`Hardwaretest-v<version>.zip`) wird aus dem Repo erzeugt:

```bash
bash scripts/build_release_zip.sh
```

Das Skript liest die Version aus `pyproject.toml`, bündelt die Anwendung inkl.
`vendor/prime95/mprime` und schließt Caches, `.venv/` sowie Laufzeitdateien aus.
Das Ergebnis wird von `scripts/install_hardwaretest.sh`,
`scripts/build_autoinstall_iso.sh` und `autoinstall/user-data` als Quelle genutzt.

## Projektstruktur

```
hardwaretest/
├── core/           # system_info, test_runner (BaseTestRunner)
├── tests/          # stress_ng, mprime, memory_controller, fio_runner
├── ui/
│   ├── main_window.py
│   └── widgets/    # test_panel, disk_panel, info_panel, help_panel,
│                   # temperature_widget, memory_controller_panel, mprime_panel
scripts/
├── install_hardwaretest.sh
├── build_release_zip.sh
├── build_deb.sh
├── build_autoinstall_iso.sh
└── collect_system_report.sh
assets/
└── hardwaretest.svg
```

Weitere technische Details stehen in `docs/ARCHITECTURE.md`.
