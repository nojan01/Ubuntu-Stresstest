# Hardwaretest – Entwicklungs-Roadmap

Diese Liste dokumentiert die gewünschten Erweiterungen. Die Reihenfolge
entspricht der derzeitigen Priorität.

## Hohe Priorität

### Netzwerk-Test

- Status: zweite Ausbaustufe umgesetzt
- `iperf3`-Client für Durchsatztests zu einem frei wählbaren Server – umgesetzt
- Ping-, Latenz-, Jitter- und Paketverlustmessung – umgesetzt
- Pass/Fail-Grenzwerte für Paketverlust, Latenz, Jitter und Durchsatz – umgesetzt
- Adapterauswahl und Bindung des Tests an eine konkrete Schnittstelle – umgesetzt
- Wiederholte Ping-/iperf3-Läufe im Gesamttestplan mit kompakter Zusammenfassung und CSV – umgesetzt in 0.2.29.
- Frei konfigurierbare Netzwerk-Grenzwerte auch im Gesamttestplan – umgesetzt in 0.2.30.
- Übersicht der Netzwerkadapter, Link-Geschwindigkeit und Treiber – umgesetzt

### NVMe-Test und -Diagnose

- Status: umgesetzt: Erkennung mit Modell, Seriennummer und Firmware,
  SMART-/Gesundheitsdaten, Selbsttest-Protokoll mit bestanden/fehlgeschlagen,
  zeitbegrenzter Lese-Benchmark sowie vollständiger nicht-destruktiver Lese-Test.
- Vollständige NVMe-Lesetests mit Auswahl und SMART-Vergleich vorher/nachher im
  Gesamttestplan und gemeinsamen Text-/HTML-/CSV-Bericht – umgesetzt in 0.2.29.
- Noch offen: zeitlicher SMART-Verlauf auch im eigenständigen Langzeitmonitoring.

### GPU-Test – NVIDIA für KI, AI und Virtual Desktop

- Status: erste Ausbaustufe umgesetzt: Mehrfachauswahl, Erkennung über
  `nvidia-smi`, Treiber/CUDA-Kompatibilität, VRAM, Temperatur, Leistung und ECC
  sowie optionale DCGM-Diagnosestufen 1–4 mit Pass/Fail-Protokoll.
- Taktraten sowie aktuelle/maximale PCIe-Generation und Linkbreite – umgesetzt
- Auslastungs-, Temperatur-, Leistungs-, VRAM- und Taktmonitor während des
  Tests mit konfigurierbarem Messintervall – umgesetzt
- CUDA-Prüfung (Treiber-/CUDA-Version, GPUs, Rechentest) – umgesetzt
- VRAM-Stresstest mit Fehlerprüfung (Vulkan für alle Hersteller, CUDA für NVIDIA,
  Muster und Zähler über 32 Bit hinaus) – umgesetzt in 0.2.5x/0.2.60
- GPU-Lasttest für KI-/VDI-Szenarien mit frei wählbarer Dauer (Vulkan/CUDA, auch
  im Gesamttestplan) – umgesetzt
- Protokollierung von Xid-Meldungen und Kernel-Logs sowie Telemetrie während des
  Tests – umgesetzt
- DCGM-Diagnose als lesbarer Textbericht (DCGM 3.x und 4.x) – umgesetzt in 0.2.61
- Noch offen: Lasttest auf AMD-/Intel-GPUs über ROCm/oneAPI (derzeit nur Vulkan).

### Gesamttestplan

- Status: erste Ausbaustufe umgesetzt
- Automatischer Ablauf aus CPU-, RAM-, NVMe-Gesundheits-, Netzwerk- und optional
  NVIDIA-/DCGM-Prüfungen – umgesetzt
- Dauer und Ein-/Ausschluss jedes Teiltests konfigurierbar – umgesetzt
- Abbruch beim ersten fehlgeschlagenen Teiltest oder manueller Abbruch – umgesetzt
- Zusammenfassender Klartext- und HTML-Report mit Ergebnissen, Details und
  Zeitstempeln – umgesetzt
- Begleitmonitoring mit Temperatur-/ECC-Sicherheitsabbruch und CSV-/Text-/HTML-Bericht – umgesetzt in 0.2.26
- Vollständige NVMe-Lesetests sowie 1–10.000 Wiederholungen des gesamten Plans mit
  kompaktem Bericht und fortlaufender CSV – umgesetzt in 0.2.29.
- OpenDCDiag, GPU-Lasttest (Vulkan/CUDA), ProLiant-/RAID-Momentaufnahme – umgesetzt
  in 0.2.5x–0.2.61.
- Vollständige SATA/SAS/USB-Lesetests (`/dev/sd*`, nur lesend) mit
  SMART-Vergleich vorher/nachher – umgesetzt in 0.2.61.
- Zeitgesteuerte Endzeit: nach Erreichen wird kein weiterer Teiltest gestartet –
  umgesetzt in 0.2.61.
- ZFS-Scrub aller importierten Pools als optionaler Schritt – umgesetzt in 0.2.61.
- Noch offen: SMART hinter RAID-Controllern (`smartctl -d cciss,N`/`megaraid,N`)
  für den Vorher/Nachher-Vergleich.

### Langzeitmonitoring

- Status: erste Ausbaustufe umgesetzt in 0.2.26 (Temperaturen, Lüfter, ECC/EDAC,
  ext4-Fehlerzähler, NVIDIA-Messwerte, neue Kernelmeldungen, CSV und kompakte
  Text-/HTML-Berichte; im Gesamttestplan integriert).
- Individuelle, gespeicherte Temperaturgrenzen sowie kompakte Live-/HTML-Diagramme
  mit Min/Max/Mittelwert und Messlücken – umgesetzt in 0.2.30, auch im Begleitmonitoring.
- Noch offen: zeitlicher SMART-Verlauf, Grenzwerte für weitere Messgrößen und
  zusätzliche Diagrammauswahl im HTML-Bericht.

### Dateisystemprüfung und Reparatur

- Offline-Prüfung ext2/ext3/ext4 und separat bestätigte konservative Reparatur – umgesetzt in 0.2.26.
- Eingehängte Dateisysteme bleiben gesperrt; Live-USB-Hinweise und erneute
  Geräte-/Mount-Prüfung vor dem privilegierten Start – umgesetzt.
- XFS (`xfs_repair -n`/`xfs_repair`), vfat, squashfs, VMFS – umgesetzt in 0.2.5x.
- Btrfs: lesende Prüfung (`btrfs check --readonly --check-data-csum`) und
  Fehlerzähler eingehängter Dateisysteme (`btrfs device stats`); bewusst keine
  automatische Reparatur – umgesetzt in 0.2.61.
- Noch offen: NTFS und exFAT (Statusdiagnose vorhanden, keine Struktur-Prüfung).
- ZFS-Poolstatus, geführter Scrub mit Fortschritt/Stop und Text-/HTML-/CSV-Protokoll – umgesetzt in 0.2.28.
- ZFS-Scrub im Gesamttestplan – umgesetzt in 0.2.61.
- Noch offen: echte ZFS-Pool-Validierung auf Ubuntu 24.04/26.04.

## Nachrangig / bei passendem System sinnvoll

### Netzteil- und Server-Hardware-Test

- HPE iLO/Redfish, ProLiant-Health und Smart-Array-Prüfungen immer ausdrücklich
  als HPE-spezifisch markieren; fehlende HPE-Hardware niemals als PC-Fehler werten.

- Prüfung verfügbarer Netzteil-, Spannungs- und Redundanzdaten über
  IPMI, `lm-sensors` oder HPE-Werkzeuge
- Speziell für HPE ProLiant: Erkennung von redundanten Netzteilen,
  Health-Status und Warnungen, sofern das System die Werte bereitstellt
- Kein künstlicher Lasttest des Netzteils; stattdessen Bewertung der
  Sensorwerte während CPU-, GPU- und Datenträgerlast

### USB-Test

- Übersicht erkannter USB-Geräte, Geschwindigkeiten und Hub-Topologie im
  System-Tab – umgesetzt in 0.2.61.
- Vollständiger Lesetest von USB-Datenträgern im Gesamttestplan – umgesetzt in 0.2.61
  (gemeinsam mit SATA/SAS).
- Nicht-destruktiver Schreibtest auf ausdrücklich ausgewählten Testmedien
- Optionaler Test von Kamera, Audio und weiteren angeschlossenen Geräten

## Grundsätze

- Destruktive Tests bleiben klar getrennt und erfordern weiterhin eine
  eindeutige Bestätigung.
- Optionale Werkzeuge werden erkannt und mit einer verständlichen
  Installationshilfe angezeigt, statt zwingende Abhängigkeiten zu werden.
- Alle neuen Tests liefern strukturierte Ergebnisse für den vorhandenen
  Systemreport und den geplanten Gesamttestplan.
