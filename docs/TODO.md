# TODO Hardwaretest / Live-ISO

## A) GPU-Lasttest und Überwachung für AMD- und Intel-Grafik (Vulkan)

Ziel: Gleichwertiger Test wie der NVIDIA-GPU-Lasttest (0.2.48), aber herstellerneutral
über Vulkan Compute – für AMD (Radeon RX 400 … RX 9000, Instinct) und Intel
(UHD/Iris Xe, z. B. Raptor Lake iGPU, Arc A/B-Serie). Kein ROCm, kein oneAPI.

## Grundlagen
- [ ] Laufzeitvoraussetzung: `libvulkan1` + `mesa-vulkan-drivers` (RADV für AMD, ANV für Intel)
- [ ] Erkennung: Vulkan-Geräte auflisten (Name, Hersteller-ID 0x1002 AMD / 0x8086 Intel,
      Treiber, VRAM-Heaps, iGPU vs. dGPU), Software-Renderer (llvmpipe) ausblenden
- [ ] Fehlermeldung, wenn Vulkan/ICD fehlt

## Lasttest-Worker (`hardwaretest/tests/vulkan_stress_script.py`)
- [ ] Vulkan-Anbindung per ctypes an `libvulkan.so.1` (Instance, Device, Queue,
      Buffer, Memory, Pipeline, Command-Buffer, Fence)
- [ ] Vorkompilierte SPIR-V-Shader mitliefern (aus GLSL, Quellen im Repo):
  - [ ] `fill`/`verify`: Muster `(i*0x9E3779B1) ^ pat`, Fehlerzähler per atomicAdd
  - [ ] `compute`: FMA-Ketten, alle Invocations müssen identisches Ergebnis liefern
- [ ] Iterationen so regeln, dass ein Dispatch 0,1–0,4 s dauert (GPU-Hang-Watchdog,
      besonders bei iGPU mit Desktop)
- [ ] Speicheranteil: dGPU = % des freien VRAM (VK_EXT_memory_budget),
      iGPU = begrenzter Anteil des Systems-RAM (z. B. max. 25 %), damit das System bedienbar bleibt
- [ ] Gleiche Ausgabe wie CUDA-Worker: „GPU n: …“, „FEHLER: …“,
      „ERGEBNIS: BESTANDEN/FEHLGESCHLAGEN“, Exit 1 bei Fehlern
- [ ] Ein Prozess pro GPU, Schalter `--vulkan-stress-worker` in `__main__.py`,
      PyInstaller-Ressource in `build_portable_bundle.sh`

## Überwachung (ohne Zusatztools, sysfs)
- [ ] AMD (`amdgpu`): Temperatur (edge/junction/mem), Leistung, GPU-/Speichertakt,
      Auslastung (`gpu_busy_percent`), VRAM belegt/gesamt, Lüfter, PCIe-Link
- [ ] Intel (`i915`/`xe`): Takt (`gt_cur_freq_mhz`), Leistung über RAPL/hwmon,
      Temperatur (Arc via hwmon; iGPU über Package-Temperatur), Auslastung wenn verfügbar
- [ ] Liveüberwachung analog NVIDIA-Tab

## Oberfläche
- [ ] Neuer Tab „GPU (AMD/Intel)“ oder Erweiterung zu allgemeinem GPU-Tab:
      Inventar, Auswahl, Liveüberwachung, Lasttest (Dauer, Speicheranteil, Start/Stop),
      Ergebnisbereich
- [ ] Optional: Schritt im Testplan
- [ ] Hilfe DE/EN, Übersetzungen in `i18n.py`

## Tests
- [ ] Unit-Tests: Kommandozeile, Fehlererkennung, sysfs-Parser mit Beispieldateien
- [ ] SPIR-V mit `spirv-val` prüfen
- [ ] Praxistest auf Intel Raptor Lake iGPU (dieser PC)
- [ ] Praxistest auf AMD-Karte (Hardware nötig) und Gegenprobe NVIDIA über Vulkan

## Live-ISO (alle Varianten: Standard, NVIDIA/DCGM, frei)
Stand 2026-10-23: NVIDIA vollständig; AMD/Intel nur Kernel-Treiber + Mesa-OpenGL.
- [ ] `firmware-amd-graphics` aufnehmen (ohne startet `amdgpu` nicht → nur Notfall-Bildschirmtreiber)
- [ ] `firmware-intel-graphics` aufnehmen (GuC/HuC/DMC; Pflicht für Arc, nötig für neuere iGPUs)
- [ ] `mesa-vulkan-drivers` aufnehmen (RADV/ANV für Vulkan-Lasttest; `libvulkan1` ist bereits drin)
- [ ] ca. 60–80 MB Mehrgröße prüfen, Verify-Hook um Prüfung der Firmware ergänzen
- [ ] Test: Boot mit AMD-Karte und Intel-iGPU/Arc, `amdgpu`/`i915`/`xe` geladen, Sensoren sichtbar
- [ ] THIRD-PARTY-LICENSES ergänzen (Mesa: MIT)

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
- [ ] Hardwaretest: DCGM-Bereich automatisch ausblenden, wenn `dcgmi` fehlt (statt nur deaktivieren)
- [ ] Einheitliche Hintergründe (textfrei, dezentes Copyright/MIT) für alle Images
- [ ] Größe prüfen (klein/groß + ca. 60–80 MB AMD/Intel)
- [ ] Releases: öffentlich `nojan01/Hardwaretest-Live-ISO`, intern mit MemTest86 Pro
      in `nojan01/HPE-Live-ISO`; README/THIRD-PARTY anpassen

## E) Tab „System“ erweitern: weitere Komponenten
Gleicher Stil wie CPU/RAM/Board, möglichst ohne root; auch im System-Abschnitt des Gesamttest-Berichts.
- [ ] **PCIe-Links** (`/sys/bus/pci/devices/*/{current,max}_link_{speed,width}`): aktuelle vs. maximale
      Breite/Generation, Abweichung rot markieren (schlechter Sitz, Riser defekt, BIOS) – Priorität 1
- [ ] **Netzwerk** (`/sys/class/net`, `ethtool -i`, `lspci`): Modell, Treiber, Firmware, MAC, Link/Speed, Steckplatz
- [ ] **Grafik** (`lspci`, `/sys/class/drm`, `nvidia-smi`): Modell, Treiber, VRAM, VBIOS
- [ ] **RAID-/HBA-Controller** (`lspci`, `/sys/class/scsi_host`): Modell, Treiber, Firmware;
      Array-Details per HPE `ssacli`/`storcli` (proprietär → nur internes Image)
- [ ] **Datenträger** (`lsblk -J -O`, `/sys/block`, `smartctl -i`): Modell, S/N, Firmware, Größe,
      SSD/HDD, NVMe/SATA/SAS, Sektorgröße
- [ ] Tests mit Beispieldaten (sysfs in tmp_path, lspci/ethtool/lsblk-Ausgaben), Hilfe DE/EN

## F) HPE RAID-Controller: Button „HPE Smart Array“ (Disk Geräte) ausbauen
Heute: `ssacli ctrl all show status` + `show config`, nur Rohtext (`system_info.read_hpe_raid_info`).
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

## D) Sonstiges
- [ ] Update-Prüfung im ISO abgeschaltet (`HARDWARETEST_NO_UPDATE_CHECK=1`, Menüpunkt ausgeblendet) – umgesetzt, mit nächstem Release/ISO veröffentlichen
