# Hardwaretest – Installationsanleitung

Diese Anleitung beschreibt die Installation der **Hardwaretest GUI** auf
Ubuntu/Debian-Derivaten (Ubuntu 22.04 / 24.04, FunOS 24.04.4). Das beigelegte
Installationsscript erledigt alle Schritte automatisch und fragt vor jeder
Aktion nach.

---

## 1. Voraussetzungen

| Anforderung           | Wert                                                |
|-----------------------|-----------------------------------------------------|
| Betriebssystem        | Ubuntu 22.04 / 24.04, Debian, FunOS 24.04.4         |
| Architektur           | x86_64 (64-Bit)                                     |
| Rechte                | `sudo` (für APT-Pakete und Systemintegration)       |
| Python                | 3.10 – 3.13 (wird vom Script geprüft)               |
| Internetverbindung    | Nur für APT-Pakete erforderlich                     |
| Plattenplatz          | ca. 250 MB (inkl. venv und Prime95)                 |

> **Hinweis:** Die `mprime`-Binary (Prime95) ist im Zip enthalten und muss
> **nicht** nachgeladen werden. Eine Internetverbindung ist nur nötig, falls
> APT-Pakete (z. B. `stress-ng`, `fio`, `python3-venv`) noch fehlen.

---

## 2. Installation per .deb-Paket (empfohlen)

Am einfachsten ist die Installation über das fertige Debian-Paket. APT zieht
dabei alle System-Abhängigkeiten (stress-ng, fio, libxcb-*, python3-venv …)
automatisch mit:

```bash
sudo apt install ./hardwaretest_0.2.1_amd64.deb
```

Beim Setup wird unter `/opt/hardwaretest/.venv` automatisch eine Python-Umgebung
mit PySide6 angelegt, der Befehl `hardwaretest` sowie ein Desktop-Eintrag
eingerichtet. Danach Start über `hardwaretest` oder das Anwendungsmenü.

Deinstallation:

```bash
sudo apt remove hardwaretest      # entfernt App + venv
```

> Das `.deb` wird aus dem Repo mit `bash scripts/build_deb.sh` erzeugt
> (benötigt `dpkg-dev`).

---

## 3. Installation per Zip + Script

### Schritt 1 – Zip-Datei nach `/opt/hardwaretest` entpacken

```bash
sudo mkdir -p /opt/hardwaretest
sudo unzip Hardwaretest-v0.2.1.zip -d /opt/hardwaretest
```

### Schritt 2 – In das Projektverzeichnis wechseln

```bash
cd /opt/hardwaretest
```

### Schritt 3 – Installationsscript ausführen

```bash
sudo bash scripts/install_hardwaretest.sh
```

Das Script führt **6 Schritte** aus und fragt vor jedem nach:

| Schritt | Beschreibung                                                    |
|---------|-----------------------------------------------------------------|
| 1 / 6   | APT-Pflicht- und optionale Pakete prüfen / installieren         |
| 2 / 6   | Prime95 (mprime) prüfen – verwendet die mitgelieferte Binary    |
| 3 / 6   | Python venv `.venv/` anlegen, PySide6 + Abhängigkeiten installieren |
| 4 / 6   | CLI-Starter `/usr/local/bin/hardwaretest` einrichten            |
| 5 / 6   | Desktop-Starter (`.desktop` + SVG-Icon + Desktop-Verknüpfung)   |
| 6 / 6   | Zusammenfassung mit HPE `ssacli`-Hinweis                        |

#### Unbeaufsichtigte Installation

```bash
sudo bash scripts/install_hardwaretest.sh --yes
```

---

## 3. Programm starten

Nach erfolgreicher Installation:

- **Terminal:**
  ```bash
  hardwaretest
  ```
- **Desktop:** Klick auf **„Hardwaretest"** im Anwendungsmenü oder auf das
  Icon auf dem Desktop.

> Tipp: Falls der Befehl nicht gefunden wird, ein neues Terminal öffnen –
> das Script ergänzt `~/.profile` / `~/.bashrc` automatisch um
> `export PATH="$HOME/.local/bin:$PATH"`.

---

## 4. Inhalt des Installations-Zips

```
Hardwaretest-v0.2.1.zip
├── INSTALL.md                       ← diese Anleitung
├── README.md                        ← Feature-Übersicht
├── pyproject.toml                   ← Python-Projektdefinition
├── .gitignore
├── assets/
│   └── hardwaretest.svg             ← Anwendungs-Icon
├── autoinstall/                     ← Ubuntu-Autoinstall-Templates
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DEPLOYMENT.md
│   └── NETZWERK_DHCP.md
├── hardwaretest/                    ← Anwendungs-Quellcode (Python)
│   ├── core/                        ← system_info, test_runner
│   ├── tests/                       ← stress_ng, mprime, fio, memory_fill …
│   └── ui/                          ← Hauptfenster + Widgets
├── profiles/
│   └── default.yaml
├── scripts/
│   ├── install_hardwaretest.sh      ← Hauptinstaller
│   ├── collect_system_report.sh
│   └── build_autoinstall_iso.sh
├── tests/                           ← Pytest-Tests
└── vendor/
    └── prime95/                     ← mprime-Binary + libgmp + Doku
```

---

## 5. Manuelle Installation (für Entwickler)

Wenn das Installationsscript nicht genutzt werden soll:

```bash
sudo apt install python3 python3-venv python3-pip python3-dev build-essential \
    libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-shape0 libxcb-xfixes0 libxkbcommon-x11-0 \
    stress-ng fio lshw pciutils smartmontools jq

cd /opt/hardwaretest
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e .
python -m hardwaretest
```

Empfohlene Zusatzpakete: `btop`, `lm-sensors`, `edac-utils`, `nvme-cli`.
Auf HPE ProLiant zusätzlich `ssacli` aus dem
[HPE SDR Repository](https://downloads.linux.hpe.com/SDR/project/mcp/).

---

## 6. Deinstallation

```bash
sudo rm -f /usr/local/bin/hardwaretest
sudo rm -f /usr/share/applications/hardwaretest.desktop
sudo rm -f /usr/share/icons/hicolor/scalable/apps/hardwaretest.svg
rm -f "$HOME/Schreibtisch/Hardwaretest.desktop" \
      "$HOME/Desktop/Hardwaretest.desktop" 2>/dev/null
sudo rm -rf /opt/hardwaretest
```

---

## 7. Fehlerbehebung

| Problem                                       | Lösung                                                  |
|-----------------------------------------------|----------------------------------------------------------|
| `hardwaretest: command not found`             | Neues Terminal öffnen oder `source ~/.profile`           |
| Qt/XCB-Fehler beim Start                      | `sudo apt install libxcb-cursor0 libxkbcommon-x11-0`     |
| `mprime` startet nicht                        | `chmod +x /opt/hardwaretest/vendor/prime95/mprime`       |
| Keine SMART-Werte sichtbar                    | `sudo apt install smartmontools` und mit `sudo` starten  |
| Keine HPE-RAID-Infos                          | `ssacli` aus HPE SDR Repo installieren                   |
| ECC/EDAC zeigt nichts                         | `sudo apt install edac-utils` (nur bei ECC-RAM relevant) |

Weitere Details in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) und
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
