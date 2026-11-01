# TODO Hardwaretest / Live-ISO

## A) GPU-Lasttest und Überwachung für AMD-, Intel- und NVIDIA-Grafik (Vulkan)

Umgesetzt (0.2.57): Tab „GPU (Vulkan)“, Worker
`hardwaretest/tests/vulkan_stress_script.py` (ctypes, SPIR-V aus
`tests/vulkan_shaders`, erzeugt mit `scripts/compile_vulkan_shaders.sh`),
sysfs-Überwachung `core/gpu_sysfs.py`, Hilfe DE/EN, Unit-Tests.
Praxistest Intel Raptor Lake iGPU: bestanden, Gegenprobe mit falschem Muster erkennt alle Fehler.
Live-ISO: firmware-amd/intel-graphics, mesa-vulkan-drivers, vulkan-tools, python3;
Einheitliches Image: NVIDIA-Enterprise-Treiber 595 + DCGM immer enthalten, nouveau/NVK über Bootmenü „ohne NVIDIA-Treiber“.

Offen:
- [ ] Praxistest auf AMD-Karte und Intel Arc (Hardware nötig), Gegenprobe NVIDIA über NVK
- [x] ISO gebaut (2026-11-01, 1,8 GB); Boot mit AMD/Intel-Grafik auf echter Hardware noch offen
- [x] Optional: Schritt „GPU (Vulkan)“ im Gesamttestplan
- [x] Intel-iGPU: Leistung über RAPL (uncore) und Auslastung (fdinfo) ergänzen; ISO: udev-Regel macht `energy_uj` lesbar
- [x] THIRD-PARTY-LICENSES der ISO ergänzen (Mesa: MIT)
- [ ] NVIDIA-Treiber + DCGM in ein eigenes Teil-Abbild (squashfs) auslagern, das im
      nouveau-Modus nicht in den RAM geladen wird (`toram`) – spart wirklich RAM,
      macht den Bau aber deutlich aufwendiger

## B) Zusätzliche CPU-Tests (Desktop und Server)

### OpenDCDiag (Intel, Apache 2.0) – Priorität 1
- [x] Erkennt Silent Data Errors (stille Rechenfehler) pro Kern; Intel und AMD
- [x] Binary bauen: `scripts/build_opendcdiag.sh` (fester Commit, AVX2- und generische Variante),
      Lizenz in `vendor/opendcdiag/` und README
- [x] Runner: Dauer, Kernauswahl (`--cpuset`), YAML auswerten → fehlerhafte Kerne benennen
      (`hardwaretest/tests/opendcdiag.py`)
- [x] Tab „OpenDCDiag“ + optionaler Schritt im Testplan
- [x] In Standard- und freies ISO aufnehmen (kommt über das `.deb`)
- [x] Praxistest auf ProLiant (Gen8 ohne AVX2 → generische Variante, mit root) – erfolgreich

### rasdaemon – Priorität 2
- [x] Machine-Check-/Cache-/Speicherfehler (MCE) während aller Tests mitschreiben
      (`core/ras.py`, zentral in `BaseTestRunner`; liest die rasdaemon-SQLite-DB)
- [x] Anzeige neuer Ereignisse seit Teststart im Ergebnis; unkorrigierbar → FEHLGESCHLAGEN,
      korrigiert → Warnung (Protokoll, Bericht Gesamttest)
- [x] Paket `rasdaemon` ins ISO, Dienst aktivieren (war schon enthalten und aktiv)
- [x] Praxistest auf dem ProLiant (`ras-mc-ctl --errors` gegen Testergebnis) – erfolgreich

### stress-ng mit Verifikation – Priorität 3
- [x] Zusätzliche Lastarten mit `--verify` (cpu, matrix, matrix-3d, fma, fp, vecmath, vecwide, vecfp, trig, l1cache, zlib) – 0.2.56
- [x] Auswahl „Mischlast“ im Gesamttest; Fehlerzeilen erkennen – 0.2.56
- [x] Paket `stress-ng` ins ISO (schon in `30-hwtools.list.chroot`)

### Intel In-Field Scan (IFS) – Priorität 4 (ProLiant Gen11/Gen12 mit Intel-CPU)
Betrifft Gen11 (Xeon 4./5. Gen, Sapphire/Emerald Rapids) und Gen12 (Xeon 6). AMD-Modelle
(DL325/345/365/385) haben kein IFS. Doku: https://docs.kernel.org/arch/x86/ifs.html
- OpenDCDiag ruft IFS schon selbst auf (Tests `ifs`, `ifs_array_bist`) → kein eigener Testcode nötig
- [ ] Auf Gen11/Gen12 erheben: `grep -m1 -E "model|stepping" /proc/cpuinfo; lsmod | grep ifs`
- [ ] Kernel-Treiber `intel_ifs` im Debian-ISO-Kernel vorhanden? Im ISO per `modprobe intel_ifs` laden
- [ ] sysfs: `/sys/devices/virtual/misc/intel_ifs_<n>/` (run_test, status, details, current_batch)
- [ ] Scan-Images von Intel-Webseite („In-Field Scan Test Images Download“), je Family-Model-Stepping,
      Ablage `/lib/firmware/intel/ifs_0/ff-mm-ss-NN.scan`
- [ ] Weitergabe/Lizenz der Images klären → zunächst nur im internen Image
- [ ] ISO-Build: Option `--ifs-images <ordner>`
- [ ] BIOS prüfen: muss IFS/„Scan at Field“ freigeschaltet werden?
- [ ] Tab „System“ und OpenDCDiag-Tab: Anzeige IFS unterstützt / Treiber geladen / Images vorhanden
- [ ] Hinweis: jeder Kern ist pro Batch bis ~200 ms offline (unkritisch im Testbetrieb)
- [ ] Praxistest auf Gen11 und Gen12

### Weitere Kandidaten (später prüfen)
- [ ] HPL/Linpack (`hpcc`, BSD): maximale FP-Last, Mehrsockel
- [ ] y-cruncher: Weitergabe unklar → nur bei Erlaubnis

## C) Ein einheitliches Image für alle
Ziel: alle Images identisch, einziger Unterschied ist MemTest86 Pro (PassMark, nur intern).
Entscheidung 29.10.2026: künftig **2 Images** – ein freies (öffentlich) und ein internes
(zusätzlich MemTest86 Pro, Intel-IFS-Images, ggf. weitere nicht frei weitergebbare Teile).
- [ ] Alle GPUs unterstützt: NVIDIA (Consumer + Enterprise, Treiber + DCGM), AMD, Intel
- [ ] DCGM-Weitergabe im öffentlichen ISO klären (DCGM Apache 2.0; mitgelieferte
      CUDA-Bibliotheken: NVIDIA-Lizenz, Weitergabe prüfen) → THIRD-PARTY ergänzen
- [ ] Hardwaretest: nur noch eine Version (freie Variante mit DCGM-Patches entfällt)
- [ ] Zwei Größen, sonst identisch:
  - **Klein:** NVIDIA-Treiber + GPU-Lasttest, AMD/Intel-Firmware + Vulkan, ohne DCGM
  - **Groß:** wie klein, zusätzlich NVIDIA DCGM
- [ ] Build-Skript vereinfachen: Optionen nur noch `--dcgm` (groß) und `--memtest86` (intern)
- [x] Hardwaretest: DCGM-Bereich automatisch ausblenden, wenn `dcgmi` fehlt (statt nur deaktivieren)
- [ ] Einheitliche Hintergründe (textfrei, dezentes Copyright/MIT) für alle Images
- [ ] Größe prüfen (klein/groß + ca. 60–80 MB AMD/Intel)
- [ ] Releases: öffentlich `nojan01/Hardwaretest-Live-ISO`, intern mit MemTest86 Pro
      in `nojan01/HPE-Live-ISO`; README/THIRD-PARTY anpassen

## E) Tab „System“ erweitern: weitere Komponenten
Gleicher Stil wie CPU/RAM/Board, möglichst ohne root; auch im System-Abschnitt des Gesamttest-Berichts.
- [x] **PCIe-Links** (`/sys/bus/pci/devices/*/{current,max}_link_{speed,width}`): aktuelle vs. maximale
      Breite/Generation, Abweichung rot markieren (schlechter Sitz, Riser defekt, BIOS) – Priorität 1
- [x] **Netzwerk** (`/sys/class/net`, `ethtool -i`, `lspci`): Modell, Treiber, Firmware, MAC, Link/Speed, Steckplatz
- [x] **Grafik** (`lspci`, `/sys/class/drm`, `nvidia-smi`): Modell, Treiber, VRAM, VBIOS
- [x] **RAID-/HBA-Controller** (`lspci`, `/sys/class/scsi_host`): Modell, Treiber, Firmware
- [ ] Array-Details per HPE `ssacli`/`storcli` (proprietär → nur internes Image)
- [x] **Datenträger** (`lsblk`, ohne `smartctl`): Modell, S/N, Firmware, Größe,
      SSD/HDD, NVMe/SATA/SAS, Sektorgröße
- [x] Tests mit Beispieldaten (sysfs in tmp_path, lspci/ethtool/lsblk-Ausgaben), Hilfe DE/EN

## F) HPE RAID-Controller: Button „HPE Smart Array“ (Disk Geräte) ausbauen
Heute: `ssacli ctrl all show status` + `show config`, nur Rohtext (`system_info.read_hpe_raid_info`).
In Arbeit (0.2.61): Modul `core/hpe_raid.py` (ssacli + storcli, Ampel), Sub-Tab RAID im Tab ProLiant Info,
Gesamttest-Abschnitt ProLiant/RAID; `scripts/collect_hpe_samples.sh` sammelt am ProLiant echte Ausgaben als Fixtures.
Im ISO bereits vorhanden (HPE MCP): `ssacli`, `ssaducli`, `storcli64`, `storcli2`.
- [ ] Mängel beheben: nur **eine** Passwortabfrage (eine Root-Sitzung statt 2× pkexec), Timeout > 15 s
- [ ] Controller (`ssacli ctrl all show config detail`): Firmware, S/N, PCI-Slot, Cache (Größe, Status,
      Lese/Schreib-Verhältnis), Akku/Kondensator (Status, Temperatur), Controller-Temperatur,
      Rebuild-Priorität, Surface Scan, Verschlüsselung
- [ ] Physische Laufwerke (`ctrl slot=X pd all show detail`): Status, Bay/Box, Modell, S/N, Firmware,
      Temperatur akt./max, Betriebsstunden, SSD-Restlebensdauer %, PHY-Speed, Predictive Failure
- [ ] Logische Laufwerke (`ctrl slot=X ld all show detail`): RAID-Level, Status (OK / Interim Recovery /
      Rebuilding x %), Stripe-Größe, Zuordnung `/dev/sdX`
- [ ] SMART hinter dem Controller: `smartctl -d cciss,N /dev/sgX`
- [ ] Diagnosebericht `ssaducli -f <datei>.zip` speichern (für HPE-Support)
- [ ] **MR-Controller** (Broadcom, Gen10 Plus/Gen11/Gen12): `storcli64 /call show all` – bisher nicht unterstützt
- [ ] Auswertung mit Ampel statt Rohtext: Akku/Cache defekt, Laufwerk ausgefallen/Predictive Failure,
      Rebuild läuft, SSD-Verschleiß < 10 %, Temperatur hoch
- [ ] Gesamttest: Controller-Zustand vor/nach dem Test vergleichen; Abschnitt im Bericht
- [ ] Tests mit echten Beispielausgaben (ssacli/storcli vom ProLiant sammeln), Hilfe DE/EN

## G) Verbesserungsvorschläge (Review 01.11.2026)
Reihenfolge nach Nutzen für den Server-Einsatz; F (RAID) und die Gesamttest-Integration der
ProLiant-Daten (IML/Sensoren/RAID vor/nach dem Test) sind in Arbeit (0.2.61).

### Funktional
- [ ] **Burn-in-Profil**: zeitgesteuerter Mischlauf (z. B. 8 h: RAM → fio → GPU → stress-ng im Wechsel)
      mit periodischen SMART-/MCE-/Temperatur-Snapshots und Trendkurve im Bericht
- [ ] **Berichtsvergleich** (vor/nach Reparatur): Diff der Messwerte (Durchsatz, Fehlerzähler, Temperaturen)
- [ ] **PCIe-Fehlerzähler** (AER, `lspci -vv` CESta/UESta) im Tab System und Gesamttest – korreliert oft
      mit Link-Downgrades, die bereits angezeigt werden
- [ ] **Netzwerk**: optional iperf3-Durchsatz zu einer angegebenen Gegenstelle; Link-Flaps aus dem Journal zählen

### Bedienung / Bericht
- [ ] **Bericht als JSON** zusätzlich zu HTML (maschinenlesbar für Archiv/Ticketsystem); optional
      automatisch per scp auf einen Zielhost
- [ ] **Testprofile speichern/laden** (Gesamttest-Auswahl + Dauern), z. B. „Schnellcheck 15 min“, „Abnahme 4 h“
- [ ] **Hardware-Kennung im Bericht**: Seriennummer/Asset-Tag aus DMI prominent im Kopf, Dateiname mit Seriennummer

### Technik / Qualität
- [ ] **End-to-End-Smoke-Test im ISO-Build**: QEMU bootet das Image, startet Hardwaretest headless mit kurzem
      Profil und prüft, dass ein Bericht entsteht (fängt Paket-/Wrapper-Probleme vor dem ProLiant-Test ab)
- [ ] **Gesamttest robuster**: Gesamtlaufzeit-Limit; Bericht inkrementell schreiben, damit Teilergebnisse
      auch bei Absturz der App auf Platte liegen
- [ ] **Englische Oberfläche** einmal komplett durchklicken (18 fehlende Übersetzungen in 0.2.60 deuten auf
      seltene EN-Tests hin)
- [ ] **CUDA-PTX** (64-Bit-Muster/-Zähler seit 0.2.60) einmal real auf NVIDIA-GPU ausführen

## D) Sonstiges
- [x] Update-Prüfung im ISO abgeschaltet (`HARDWARETEST_NO_UPDATE_CHECK=1`, Menüpunkt ausgeblendet) – veröffentlicht mit 0.2.59/ISO 2026-11-01
