# Hardwaretest – offene Punkte

Umgesetzte Funktionen stehen im README („Neu in …“) und in der Hilfe der App.
Die Reihenfolge entspricht der derzeitigen Priorität.

## Hohe Priorität

### Netzwerk-Test

- ethtool-Fehlerzähler (`ethtool -S`: CRC, rx/tx errors, dropped) automatisch vor und
  nach dem iperf3-/Ping-Lauf vergleichen und Zuwächse als Fehler werten.
- Proprietäre Herstellertools (NVIDIA MFT `mlxlink`/`mlxcables`, Broadcom
  `bnxtnvm`/`bnxtmt`, Intel `nvmupdate`) sind nicht im externen Live-ISO enthalten –
  sie kämen ausschließlich über `--vendor-tools` ins interne ISO. Falls gewünscht:
  optionale Schaltflächen, nur sichtbar, wenn das Tool installiert ist
  (frei und immer verfügbar: `ethtool`, `mstflint`/`mstlink`).

### Fibre Channel (zurückgestellt – keine FC-Hardware vorhanden)

- FC-Lasttest: fio lesend auf sichtbarer LUN (`/dev/mapper/mpath*` bzw. `/dev/sdX`)
  mit Vergleich der Fehlerzähler aus `/sys/class/fc_host/hostN/statistics/`
  (`link_failure_count`, `loss_of_sync_count`, `loss_of_signal_count`,
  `invalid_crc_count`, `error_frames`) vorher/nachher; Pfadstatus per `multipath -ll`.
- Ohne Fabric: Loopback-Stecker am SFP → `port_state` Online, Zähler ruhig.
- Herstellertools (proprietär, nur internes ISO): QLogic `qaucli` (hp-qaucli) und
  Emulex `hbacmd` (hp-elxocmcore) mit `LoopBackTest` und SFP-Daten (`GetXcvrData`).

### NVMe / Langzeitmonitoring

- Zeitlicher SMART-Verlauf im eigenständigen Langzeitmonitoring.
- Grenzwerte für weitere Messgrößen und zusätzliche Diagrammauswahl im HTML-Bericht.

### GPU-Test

- Lasttest auf AMD-/Intel-GPUs über ROCm/oneAPI (derzeit nur Vulkan).

### Gesamttestplan

- SMART hinter RAID-Controllern (`smartctl -d cciss,N`/`megaraid,N`) für den
  Vorher/Nachher-Vergleich.

### Dateisystemprüfung

- Echte ZFS-Pool-Validierung auf Ubuntu 24.04/26.04.

## Nachrangig / bei passendem System sinnvoll

### Netzteil- und Server-Hardware

- HPE iLO/Redfish, ProLiant-Health und Smart-Array-Prüfungen immer ausdrücklich als
  HPE-spezifisch markieren; fehlende HPE-Hardware niemals als PC-Fehler werten.
- Netzteil-/Redundanzdaten über `lm-sensors` oder HPE-Werkzeuge ergänzen (DMI und
  IPMI sind umgesetzt); Bewertung der Sensorwerte während CPU-, GPU- und
  Datenträgerlast – kein künstlicher Netzteil-Lasttest.
- Netzteile/Redundanz am ProLiant mit 2 und 4 Netzteilen real verifizieren
  (DMI Typ 39, IPMI 0x08/0x09, Netzkabel ziehen → „Redundancy Lost“).
- mstlink-Parser gegen echte Mellanox-Ausgabe verifizieren.

### USB-Test

- Nicht-destruktiver Schreibtest auf ausdrücklich ausgewählten Testmedien.
- Optionaler Test von Kamera, Audio und weiteren angeschlossenen Geräten.

## Grundsätze

- Destruktive Tests bleiben klar getrennt und erfordern weiterhin eine
  eindeutige Bestätigung.
- Optionale Werkzeuge werden erkannt und mit einer verständlichen
  Installationshilfe angezeigt, statt zwingende Abhängigkeiten zu werden.
- Alle neuen Tests liefern strukturierte Ergebnisse für den vorhandenen
  Systemreport und den Gesamttestplan.
