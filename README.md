# Hardwaretest GUI

PySide6-Anwendung zum Starten von Hardware-Stresstests für **PCs, Workstations und Server**. Nutzt bewährte Werkzeuge: `stress-ng`, `Prime95 (mprime)`, `fio`. Zielplattformen sind **Ubuntu 24.04 und 26.04 (amd64)**. Andere Distributionen werden nicht zugesichert. Optionale HPE-Smart-Array-Funktionen sind ausdrücklich als **HPE-spezifisch** gekennzeichnet und für normale PCs nicht erforderlich.

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

## Installation

### Variante A – Debian-Paket (empfohlen)

Für die Installation wird nur die Datei `hardwaretest_0.2.30_amd64.deb` benötigt.

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

### Variante B – AppImage

Das AppImage enthält ebenfalls Python, PySide6 und alle Python-Module. Es kann
ohne Installation einer Python-Laufzeit gestartet werden:

```bash
chmod +x Hardwaretest-0.2.30-x86_64.AppImage
./Hardwaretest-0.2.30-x86_64.AppImage
```

Native Diagnoseprogramme müssen auf der jeweiligen Distribution weiterhin über
deren Paketverwaltung installiert werden. Das AppImage wird mit
`bash scripts/build_appimage.sh` erzeugt.

### Variante C – Zip + Installationsscript (Legacy)

#### 1. Zip entpacken nach `/opt/hardwaretest`

```bash
sudo unzip Hardwaretest-v0.2.30.zip -d /opt/hardwaretest
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
- **Netzwerkdiagnose** – Zeigt Adapter, Treiber, PCI-Adresse, Linkstatus/-geschwindigkeit, Duplex, MTU und IP-Adressen. Ping ermittelt Paketverlust, Latenz und Jitter; iperf3 misst Durchsatz und Retransmits. Alle Messwerte können gegen Pass/Fail-Grenzen geprüft werden.
- **Gesamttestplan** – Führt frei auswählbare CPU-, RAM-, NVMe-, Netzwerk- und NVIDIA/DCGM-Prüfungen automatisch nacheinander aus. Jeder Lauf erzeugt ein Klartext- und ein druckoptimiertes HTML-Protokoll; der Browser kann dieses zusätzlich als PDF speichern. Netzwerk-Dauerläufe werden kompakt mit Summenwerten statt einzelner Ping-Pakete oder iperf3-Intervalle protokolliert.
- **HPE RAID-Info** – SmartArray-Controller-Status via `ssacli` / `hpssacli`.

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
