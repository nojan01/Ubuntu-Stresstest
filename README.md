# Hardwaretest GUI

PySide6-Anwendung zum Starten von Hardware-Stresstests für **CPU, RAM und Festplatten** – optimiert für **HPE ProLiant Server**. Nutzt bewährte Werkzeuge: `stress-ng`, `Prime95 (mprime)`, `fio`. Getestet unter Ubuntu 22.04 / 24.04 und FunOS 24.04.4 (X11/Wayland, GNOME, KDE Plasma, XFCE, Cinnamon, MATE, FVWM).

## Installation

### Variante A – Debian-Paket (empfohlen)

```bash
sudo apt install ./hardwaretest_0.2.1_amd64.deb
```

APT installiert alle System-Abhängigkeiten automatisch. Das Setup legt unter
`/opt/hardwaretest/.venv` eine Python-Umgebung mit PySide6 an und richtet den
Befehl `hardwaretest` sowie einen Desktop-Eintrag ein. Deinstallation mit
`sudo apt remove hardwaretest`.

> Das Paket wird aus dem Repo mit `bash scripts/build_deb.sh` erzeugt
> (benötigt `dpkg-dev`).

### Variante B – Zip + Installationsscript

#### 1. Zip entpacken nach `/opt/hardwaretest`

```bash
sudo unzip Hardwaretest-v0.2.1.zip -d /opt/hardwaretest
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
| **3/6** | **Python venv** – erstellt `.venv/`, installiert PySide6, psutil etc. |
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
- **HPE RAID-Info** – SmartArray-Controller-Status via `ssacli` / `hpssacli`.

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
