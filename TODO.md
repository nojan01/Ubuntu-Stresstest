# Hardwaretest – offene Punkte

Umgesetzte Funktionen stehen im README („Neu in …“) und in der Hilfe der App.
Die Reihenfolge entspricht der derzeitigen Priorität.

## Hohe Priorität

### Netzwerk-Test

- ethtool-Fehlerzähler (`ethtool -S`: CRC, rx/tx errors, dropped) automatisch vor und
  nach dem iperf3-/Ping-Lauf vergleichen und Zuwächse als Fehler werten.
- Proprietäre Herstellertools (seit 0.3.6/0.3.7 „Hersteller-Diagnose“, nur internes
  ISO über `--vendor-tools`): Syntax von `bnxtnvm`, `qlnxcli`, `sfupdate`/`sfboot`
  ist unbelegt – mit echten Binaries prüfen; Marvell `qlnxcli` (FastLinQ) und
  Solarflare `sfutils` (nur Source-RPM) fehlen im internen ISO. Marvell
  QConvergeConsoleCLI 3.2.2 (`qaucli`) ist im ISO, bedient aber nur FC-HBAs
  (`-pr nic` → „Unsupported command line option“) – deshalb seit 0.3.8 nicht mehr
  für Ethernet-Adapter verwendet.
- Realtek (`r8169`/`r8125`/`r8168`, 0.3.8): Realtek liefert für Linux kein
  Diagnose-CLI (nur Windows-Utility; `rtnicpg` ist ein EEPROM-Programmiertool) –
  alle Prüfungen laufen über `ethtool`. Geplant: Hinweis im Netzwerk-Tab und
  Bericht, wenn ein 2,5/5-GbE-Realtek-Adapter (RTL8125/8126) mit dem Kernel-`r8169`
  statt dem Realtek-`r8125`-Treiber läuft (bekannte Ursache für Link-Flaps/
  Durchsatzprobleme); Treiber + Version (`ethtool -i`) und PHY-Statistiken
  (`ethtool --phy-statistics`) in den Bericht aufnehmen; Hilfe/README ergänzen.

### Fibre Channel (zurückgestellt – keine FC-Hardware vorhanden)

- FC-Lasttest: fio lesend auf sichtbarer LUN (`/dev/mapper/mpath*` bzw. `/dev/sdX`)
  mit Vergleich der Fehlerzähler aus `/sys/class/fc_host/hostN/statistics/`
  (`link_failure_count`, `loss_of_sync_count`, `loss_of_signal_count`,
  `invalid_crc_count`, `error_frames`) vorher/nachher; Pfadstatus per `multipath -ll`.
- Ohne Fabric: Loopback-Stecker am SFP → `port_state` Online, Zähler ruhig.
- Herstellertools (proprietär, nur internes ISO): QLogic `qaucli` (QConvergeConsoleCLI
  3.2.2 ist im internen ISO; belegte Lese-Syntax: `qaucli -pr fc -g`, `-i [<hba>|all]`,
  `-i <hba> vpd`, `-t <hba>` Targets, `-l <hba>` LUNs, `-fg <hba> view` Flash-Gruppen;
  Loopback-/Diagnoseoptionen noch aus `qaucli -h` ermitteln) und Emulex `hbacmd`
  (hp-elxocmcore) mit `LoopBackTest` und SFP-Daten (`GetXcvrData`).

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

- ~~Tab „ProLiant Info“ automatisch ausblenden, wenn kein HPE ProLiant/Synergy~~
  erledigt (0.3.8): Tab nur auf ProLiant/Synergy; RAID-Button blendet ihn bei Bedarf ein.
- HPE iLO/Redfish, ProLiant-Health und Smart-Array-Prüfungen immer ausdrücklich als
  HPE-spezifisch markieren; fehlende HPE-Hardware niemals als PC-Fehler werten.
- Netzteil-/Redundanzdaten über `lm-sensors` oder HPE-Werkzeuge ergänzen (DMI und
  IPMI sind umgesetzt); Bewertung der Sensorwerte während CPU-, GPU- und
  Datenträgerlast – kein künstlicher Netzteil-Lasttest.
- Netzteile/Redundanz am ProLiant mit 2 und 4 Netzteilen real verifizieren
  (DMI Typ 39, IPMI 0x08/0x09, Netzkabel ziehen → „Redundancy Lost“).
- mstlink-Parser gegen echte Mellanox-Ausgabe verifizieren.

### AMD Instinct (zurückgestellt – bei Bedarf)

- Gegenstück zu DCGM für AMD-Datacenter-GPUs: **ROCm Validation Suite** (`rvs`: GST, IET,
  PEBB/PBQT, MEM), **RDC** (`rdci`) und `amd-smi`/`rocm-smi`. Setzt ROCm voraus (nur
  Instinct/wenige Radeon Pro; ISO +2–3 GB) → eigene ISO-Variante `rvs` und Tab „AMD RVS“
  analog zu OpenDCDiag/DCGM. Für Consumer-Radeons bleibt Vulkan-Lasttest + amdgpu-Sensoren.

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
