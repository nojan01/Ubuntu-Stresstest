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
- [ ] Erkennt Silent Data Errors (stille Rechenfehler) pro Kern; Intel und AMD
- [ ] Binary/Bundle beschaffen bzw. bauen (github.com/opendcdiag/opendcdiag), Lizenz in THIRD-PARTY
- [ ] Runner: Dauer, Kernauswahl (alle / einzelne), Ausgabe (YAML) auswerten → fehlerhafte Kerne benennen
- [ ] UI im CPU-Bereich + optional Schritt im Testplan
- [ ] In Standard- und freies ISO aufnehmen

### rasdaemon – Priorität 2
- [x] Machine-Check-/Cache-/Speicherfehler (MCE) während aller Tests mitschreiben
      (`core/ras.py`, zentral in `BaseTestRunner`; liest die rasdaemon-SQLite-DB)
- [x] Anzeige neuer Ereignisse seit Teststart im Ergebnis; unkorrigierbar → FEHLGESCHLAGEN,
      korrigiert → Warnung (Protokoll, Bericht Gesamttest)
- [x] Paket `rasdaemon` ins ISO, Dienst aktivieren (war schon enthalten und aktiv)
- [ ] Praxistest auf dem ProLiant (`ras-mc-ctl --errors` gegen Testergebnis)

### stress-ng mit Verifikation – Priorität 3
- [ ] Zusätzliche Lastarten mit `--verify` (cpu, matrix, fft, cache, branch, vecmath …)
- [ ] Auswahl „Mischlast“ neben Prime95; Fehlerzeilen erkennen
- [ ] Paket `stress-ng` ins ISO (prüfen, ob schon enthalten)

### Intel In-Field Scan (IFS) – optional
- [ ] Nur Xeon ab Sapphire Rapids: Treiber `intel_ifs`, sysfs `/sys/devices/virtual/ifs/`
- [ ] Scan-Images von Intel (`/lib/firmware/intel/ifs_0/`) – Weitergabe/Lizenz klären
- [ ] Nur anzeigen, wenn die CPU IFS unterstützt

### Weitere Kandidaten (später prüfen)
- [ ] HPL/Linpack (`hpcc`, BSD): maximale FP-Last, Mehrsockel
- [ ] y-cruncher: Weitergabe unklar → nur bei Erlaubnis

## C) Ein einheitliches Image für alle
Ziel: alle Images identisch, einziger Unterschied ist MemTest86 Pro (PassMark, nur intern).
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

## D) Sonstiges
- [ ] Update-Prüfung im ISO abgeschaltet (`HARDWARETEST_NO_UPDATE_CHECK=1`, Menüpunkt ausgeblendet) – umgesetzt, mit nächstem Release/ISO veröffentlichen
