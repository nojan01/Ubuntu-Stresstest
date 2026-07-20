# Hardwaretest – Deployment-Anleitung

Anleitung zum Erstellen einer bootfähigen Testumgebung für HPE ProLiant Server.
Drei Varianten: USB-SSD, ISO-Image für iLO, und automatisiertes Massen-Deployment.

---

## Inhaltsverzeichnis

1. [Variante A: USB-SSD (physisch vor Ort)](#variante-a-usb-ssd)
2. [Variante B: Autoinstall-ISO für iLO (Remote)](#variante-b-autoinstall-iso-für-ilo)
3. [Variante C: Massen-Deployment (NFS + iLO API)](#variante-c-massen-deployment)
4. [Anhang: Tipps & Troubleshooting](#anhang)

---

## Voraussetzungen

| Komponente | Details |
|---|---|
| **Basis-OS** | Ubuntu Server 24.04 LTS |
| **Desktop** | XFCE4 (leichtgewichtig, ~300 MB RAM) |
| **USB-SSD** | Min. 16 GB, empfohlen 32 GB, USB 3.0+ |
| **Entwicklungsrechner** | Ubuntu/Debian mit `xorriso`, `grub`, `mtools` |

---

## Variante A: USB-SSD

Für einzelne Server, bei denen man physisch Zugang hat.

### Schritt 1: Ubuntu Server auf USB-SSD installieren

1. Ubuntu Server 24.04 LTS ISO herunterladen:
   ```bash
   wget https://releases.ubuntu.com/24.04/ubuntu-24.04-live-server-amd64.iso
   ```

2. Bootfähigen USB-Stick erstellen (temporär, zum Installieren):
   ```bash
   sudo dd if=ubuntu-24.04-live-server-amd64.iso of=/dev/sdX bs=4M status=progress
   sync
   ```

3. Vom USB-Stick booten und Ubuntu Server **auf die USB-SSD** installieren:
   - Minimale Installation wählen
   - OpenSSH-Server mitinstallieren
   - Ziel-Laufwerk = die USB-SSD (nicht die internen Platten!)

### Schritt 2: Desktop + Hardwaretest installieren

Nach dem Neustart von der USB-SSD:

```bash
# System aktualisieren
sudo apt update && sudo apt upgrade -y

# XFCE Desktop (leichtgewichtig)
sudo apt install -y xfce4 xfce4-terminal lightdm --no-install-recommends

# Hardwaretest entpacken
sudo mkdir -p /opt/Hardwaretest
sudo unzip /pfad/zu/Hardwaretest-v0.2.0.zip -d /opt/Hardwaretest
cd /opt/Hardwaretest

# Installer ausführen (installiert alle Abhängigkeiten)
sudo bash scripts/install_hardwaretest.sh
```

### Schritt 3: Auto-Login einrichten (optional)

Für eine Teststation ohne manuelle Anmeldung:

```bash
sudo tee /etc/lightdm/lightdm.conf.d/50-autologin.conf <<EOF
[Seat:*]
autologin-user=testuser
autologin-user-timeout=0
EOF
```

### Schritt 4: Hardwaretest automatisch starten (optional)

```bash
mkdir -p ~/.config/autostart
cp /usr/share/applications/hardwaretest.desktop ~/.config/autostart/
```

### Schritt 5: HPE Tools installieren (optional)

SSACLI wird für die RAID-Controller-Diagnose auf HPE ProLiant Servern benötigt.
Doku: https://downloads.linux.hpe.com/SDR/project/mcp/

```bash
# 1. HPE GPG-Schlüssel importieren
curl -fsSL https://downloads.linux.hpe.com/SDR/hpPublicKey2048_key1.pub | \
  gpg --dearmor | sudo tee /usr/share/keyrings/hpePublicKey.gpg > /dev/null
curl -fsSL https://downloads.linux.hpe.com/SDR/hpePublicKey2048_key1.pub | \
  gpg --dearmor | sudo tee -a /usr/share/keyrings/hpePublicKey.gpg > /dev/null
curl -fsSL https://downloads.linux.hpe.com/SDR/hpePublicKey2048_key2.pub | \
  gpg --dearmor | sudo tee -a /usr/share/keyrings/hpePublicKey.gpg > /dev/null

# 2. HPE MCP Repository hinzufügen (noble = Ubuntu 24.04, jammy = 22.04)
echo "deb [signed-by=/usr/share/keyrings/hpePublicKey.gpg] https://downloads.linux.hpe.com/SDR/repo/mcp noble/current non-free" | \
  sudo tee /etc/apt/sources.list.d/hpe-mcp.list

# 3. Installieren
sudo apt update
sudo apt install -y ssacli
```

---

## Variante B: Autoinstall-ISO für iLO (Remote)

Für Remote-Zugriff über HPE iLO Virtual Media. Statt ein riesiges Disk-Image
der USB-SSD zu erstellen (~500 GB), nutzen wir das offizielle Ubuntu Server ISO
(~2.5 GB) mit eingebetteter Autoinstall-Konfiguration. Die Installation läuft
**vollautomatisch** – ohne manuelle Eingaben.

**Ergebnis:** Ein ~2.6 GB ISO, das per iLO gemountet wird und automatisch
Ubuntu Server + XFCE4 + Hardwaretest installiert.

### Schritt 1: Voraussetzungen installieren

```bash
sudo apt install -y p7zip-full xorriso wget
```

### Schritt 2: Ubuntu Server ISO herunterladen

```bash
wget https://releases.ubuntu.com/24.04/ubuntu-24.04-live-server-amd64.iso
```

### Schritt 3: Autoinstall-Konfiguration anpassen (optional)

Die Konfiguration liegt unter `autoinstall/user-data`. Standardwerte:

| Einstellung | Wert |
|---|---|
| **Hostname** | `hardwaretest` |
| **Benutzer** | `testuser` |
| **Passwort** | `hardwaretest` |
| **Tastatur** | Deutsch (de) |
| **Netzwerk** | DHCP auf allen NICs |
| **Desktop** | XFCE4 + LightDM |
| **Auto-Login** | Ja (testuser) |
| **SSH** | Aktiviert |

Passwort ändern (optional):
```bash
# Neuen Hash generieren
openssl passwd -6 MeinNeuesPasswort
# Hash in autoinstall/user-data bei "password:" eintragen
```

### Schritt 4: ISO bauen

```bash
sudo bash scripts/build_autoinstall_iso.sh ubuntu-24.04-live-server-amd64.iso
```

Das Script:
1. Entpackt die Ubuntu Server ISO
2. Bettet die Autoinstall-Konfiguration ein
3. Bettet das Hardwaretest-ZIP ein
4. Erstellt ein BIOS+UEFI-bootfähiges ISO (~2.6 GB)

Ausgabe: `hardwaretest-autoinstall.iso`

### Schritt 5: ISO in iLO mounten

**Über die iLO Web-GUI:**

1. iLO Web-Interface öffnen (https://ilo-ip/)
2. → **Virtual Media** → **Connect Virtual Media**
3. → **CD/DVD** → **Image File** → `hardwaretest-autoinstall.iso` auswählen
4. → **Boot Order** → Virtual CD/DVD an erste Stelle
5. → Server neu starten
6. → Installation läuft vollautomatisch (~10-15 Minuten)

**Über iLO Netzwerkfreigabe (schneller):**

1. ISO auf einen HTTP- oder NFS-Share legen:
   ```bash
   # Beispiel: NFS-Share einrichten
   sudo apt install -y nfs-kernel-server
   sudo mkdir -p /srv/iso
   cp hardwaretest-autoinstall.iso /srv/iso/
   echo "/srv/iso *(ro,sync,no_subtree_check)" | sudo tee -a /etc/exports
   sudo exportfs -ra
   ```

2. In iLO: **Virtual Media** → **Scripted Media URL**:
   ```
   nfs://192.168.1.100/srv/iso/hardwaretest-autoinstall.iso
   ```

### Was passiert beim Booten?

1. Server bootet von der ISO (GRUB-Timeout: 5 Sekunden)
2. Ubuntu-Installer startet automatisch (kein Menü)
3. Partitioniert die erste Festplatte, installiert Ubuntu Server
4. Installiert XFCE4, stress-ng, fio, und alle Abhängigkeiten
5. Entpackt und installiert Hardwaretest
6. Richtet Auto-Login für `testuser` ein
7. Startet neu → Desktop mit Hardwaretest erscheint

---

## Variante C: Massen-Deployment

Für viele HPE ProLiant Server gleichzeitig über die iLO RESTful API.

### Schritt 1: ISO auf NFS/HTTP-Server bereitstellen

```bash
# HTTP-Server (einfachste Variante)
sudo apt install -y nginx
sudo cp hardwaretest-autoinstall.iso /var/www/html/
# Erreichbar unter: http://192.168.1.100/hardwaretest-autoinstall.iso
```

### Schritt 2: iLO REST API – ISO mounten & booten

```bash
# Variablen anpassen
ILO_IP="192.168.1.10"
ILO_USER="admin"
ILO_PASS="password"
ISO_URL="http://192.168.1.100/hardwaretest-autoinstall.iso"

# 1. ISO als Virtual Media mounten
curl -sk -u "${ILO_USER}:${ILO_PASS}" \
  -X PATCH "https://${ILO_IP}/redfish/v1/Managers/1/VirtualMedia/2" \
  -H "Content-Type: application/json" \
  -d "{
    \"Image\": \"${ISO_URL}\",
    \"Oem\": {
      \"Hpe\": {
        \"BootOnNextServerReset\": true
      }
    }
  }"

# 2. Server neu starten
curl -sk -u "${ILO_USER}:${ILO_PASS}" \
  -X POST "https://${ILO_IP}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "ForceRestart"}'

echo "Server $ILO_IP wird mit ISO gebootet..."
```

### Schritt 3: Batch-Script für mehrere Server

```bash
#!/usr/bin/env bash
# deploy_hardwaretest.sh – ISO auf mehrere Server mounten
ISO_URL="http://192.168.1.100/hardwaretest-autoinstall.iso"
ILO_USER="admin"
ILO_PASS="password"

# Liste der iLO-IP-Adressen
ILO_HOSTS=(
    192.168.1.10
    192.168.1.11
    192.168.1.12
    192.168.1.13
)

for ilo in "${ILO_HOSTS[@]}"; do
    echo "── Deploying to iLO: $ilo ──"

    # ISO mounten
    curl -sk -u "${ILO_USER}:${ILO_PASS}" \
      -X PATCH "https://${ilo}/redfish/v1/Managers/1/VirtualMedia/2" \
      -H "Content-Type: application/json" \
      -d "{\"Image\": \"${ISO_URL}\", \"Oem\": {\"Hpe\": {\"BootOnNextServerReset\": true}}}" \
      && echo "  ✓ ISO gemountet"

    # Neustart
    curl -sk -u "${ILO_USER}:${ILO_PASS}" \
      -X POST "https://${ilo}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" \
      -H "Content-Type: application/json" \
      -d '{"ResetType": "ForceRestart"}' \
      && echo "  ✓ Neustart ausgelöst"

    echo ""
done

echo "Alle Server werden gebootet. Prüfe Status über iLO Remote Console."
```

### Schritt 4: Virtual Media wieder trennen (nach dem Test)

```bash
curl -sk -u "${ILO_USER}:${ILO_PASS}" \
  -X PATCH "https://${ILO_IP}/redfish/v1/Managers/1/VirtualMedia/2" \
  -H "Content-Type: application/json" \
  -d '{"Image": null}'
```

---

## Anhang

### Tipps

- **USB-SSD klonen:** Eine fertige USB-SSD lässt sich mit `dd` auf weitere SSDs duplizieren:
  ```bash
  sudo dd if=/dev/sdQUELLE of=/dev/sdZIEL bs=4M status=progress
  ```

- **SSH-Zugang:** Über SSH können Tests auch remote gestartet werden:
  ```bash
  ssh testuser@server-ip "hardwaretest"
  ```

- **Swap deaktivieren:** Für RAM-Tests unbedingt vor dem Test Swap abschalten:
  ```bash
  sudo swapoff -a
  ```

- **iLO Remote Console:** Über die iLO-Weboberfläche hast du eine grafische
  Konsole zum Server – damit kannst du die Hardwaretest-GUI remote bedienen.

### Troubleshooting

| Problem | Lösung |
|---|---|
| USB-SSD bootet nicht | BIOS: UEFI aktivieren, Secure Boot ggf. deaktivieren |
| ISO bootet nicht in iLO | ISO mit `build_autoinstall_iso.sh` neu erstellen, UEFI-Support prüfen |
| XFCE startet nicht | `sudo dpkg-reconfigure lightdm` |
| Kein Netzwerk nach Boot | `sudo dhclient` oder `sudo netplan apply` |
| iLO Virtual Media langsam | ISO auf lokalen NFS/HTTP-Server statt über Internet |
| Prime95 startet nicht | Prüfe: `vendor/prime95/mprime -v` |
| HPE RAID-Info fehlt | HPE SDR Repository + `ssacli` installieren (siehe Schritt 5) |

### Dateigröße-Richtwerte

| Artefakt | Größe |
|---|---|
| USB-SSD Image (dd, sparse) | 4–8 GB |
| Autoinstall-ISO | ~2.6 GB |
| Hardwaretest ZIP | ~75 KB |
