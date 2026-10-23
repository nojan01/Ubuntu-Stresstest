# TODO: GPU-Lasttest und Überwachung für AMD- und Intel-Grafik (Vulkan)

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
