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
- Dauerlauf mit Protokoll und Pass/Fail-Grenzwerten
- Übersicht der Netzwerkadapter, Link-Geschwindigkeit und Treiber – umgesetzt

### NVMe-Test und -Diagnose

- Status: umgesetzt: Erkennung mit Modell, Seriennummer und Firmware,
  SMART-/Gesundheitsdaten, Selbsttest-Protokoll mit bestanden/fehlgeschlagen,
  zeitbegrenzter Lese-Benchmark sowie vollständiger nicht-destruktiver Lese-Test.
- Noch offen: Integration der Ergebnisse in den gemeinsamen Langzeit- und
  Systemreport.

### GPU-Test – NVIDIA für KI, AI und Virtual Desktop

- Status: erste Ausbaustufe umgesetzt: Mehrfachauswahl, Erkennung über
  `nvidia-smi`, Treiber/CUDA-Kompatibilität, VRAM, Temperatur, Leistung und ECC
  sowie optionale DCGM-Diagnosestufen 1–4 mit Pass/Fail-Protokoll.
- Taktraten sowie aktuelle/maximale PCIe-Generation und Linkbreite – umgesetzt
- Auslastungs-, Temperatur-, Leistungs-, VRAM- und Taktmonitor während des
  Tests mit konfigurierbarem Messintervall – umgesetzt
- CUDA-Prüfung: verfügbare CUDA-Version, erkannte GPUs und einfacher
  Rechentest, sofern CUDA installiert ist
- VRAM-Stresstest und Fehlerprüfung mit einem etablierten, optionalen Tool
- Optionaler GPU-Lasttest für KI-/VDI-Szenarien mit frei wählbarer Dauer
- Protokollierung von GPU-Fehlern, Xid-Meldungen und relevanten Kernel-Logs

### Gesamttestplan

- Status: erste Ausbaustufe umgesetzt
- Automatischer Ablauf aus CPU-, RAM-, NVMe-Gesundheits-, Netzwerk- und optional
  NVIDIA-/DCGM-Prüfungen – umgesetzt
- Dauer und Ein-/Ausschluss jedes Teiltests konfigurierbar – umgesetzt
- Abbruch beim ersten fehlgeschlagenen Teiltest oder manueller Abbruch – umgesetzt
- Zusammenfassender Klartext- und HTML-Report mit Ergebnissen, Details und
  Zeitstempeln – umgesetzt
- Noch offen: gemeinsamer Grenzwertwächter für kritische Temperaturen und die
  Einbindung vollständiger Laufwerks-Lesetests in mehrstündige Testpläne

### Langzeitmonitoring

- Zeitverlauf für Temperatur, Lüfter, ECC/EDAC, SMART und GPU-Werte
- Erfassung relevanter Kernel- und Hardwarefehler
- Konfigurierbare Messintervalle und Laufzeit
- Export als CSV und Einbindung in den HTML-Systemreport

## Nachrangig / bei passendem System sinnvoll

### Netzteil- und Server-Hardware-Test

- Prüfung verfügbarer Netzteil-, Spannungs- und Redundanzdaten über
  IPMI, `lm-sensors` oder HPE-Werkzeuge
- Speziell für HPE ProLiant: Erkennung von redundanten Netzteilen,
  Health-Status und Warnungen, sofern das System die Werte bereitstellt
- Kein künstlicher Lasttest des Netzteils; stattdessen Bewertung der
  Sensorwerte während CPU-, GPU- und Datenträgerlast

### USB-Test

- Übersicht erkannter USB-Geräte und USB-Geschwindigkeiten
- Nicht-destruktiver Lese-/Schreibtest auf ausdrücklich ausgewählten
  Testmedien
- Optionaler Test von Kamera, Audio und weiteren angeschlossenen Geräten

## Grundsätze

- Destruktive Tests bleiben klar getrennt und erfordern weiterhin eine
  eindeutige Bestätigung.
- Optionale Werkzeuge werden erkannt und mit einer verständlichen
  Installationshilfe angezeigt, statt zwingende Abhängigkeiten zu werden.
- Alle neuen Tests liefern strukturierte Ergebnisse für den vorhandenen
  Systemreport und den geplanten Gesamttestplan.
