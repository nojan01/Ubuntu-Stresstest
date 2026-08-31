#!/usr/bin/env bash
# =============================================================================
#  build_deb.sh – Erstellt ein installierbares Debian-Paket (hardwaretest_*.deb)
#
#  Das Paket installiert die Anwendung nach /opt/hardwaretest und richtet im
#  postinst-Schritt automatisch ein:
#    * Python-venv unter /opt/hardwaretest/.venv (mit PySide6, psutil, ...)
#    * CLI-Starter /usr/bin/hardwaretest
#    * Desktop-Eintrag + Icon (System-weit)
#
#  Die System-Abhaengigkeiten (stress-ng, fio, libxcb-*, python3-venv, ...)
#  werden ueber das Feld "Depends" von apt automatisch mitinstalliert.
#
#  Verwendung:
#    bash scripts/build_deb.sh
#    sudo apt install ./hardwaretest_<version>_amd64.deb
#
#  Voraussetzungen (nur zum Bauen):
#    sudo apt install -y dpkg-dev          # liefert dpkg-deb
# =============================================================================
set -euo pipefail

# ── Farben ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

command -v dpkg-deb >/dev/null 2>&1 || fail "dpkg-deb nicht gefunden. Bitte installieren: sudo apt install dpkg-dev"

# ── Pfade & Metadaten ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

VERSION="$(grep -m1 -E '^version' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')"
[[ -n "$VERSION" ]] || fail "Konnte Version nicht aus pyproject.toml lesen."

PKG_NAME="hardwaretest"
# vendor/prime95/mprime ist ein amd64-ELF-Binary -> Architektur amd64.
ARCH="amd64"
MAINTAINER="Norbert Jander <n.jander@posteo.de>"
INSTALL_DIR="/opt/hardwaretest"

BUILD_ROOT="$(mktemp -d /tmp/hardwaretest-deb.XXXXXX)"
trap 'rm -rf "$BUILD_ROOT"' EXIT
PKG_ROOT="$BUILD_ROOT/pkg"

OUTPUT="$REPO_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"

info "Repo:    $REPO_DIR"
info "Version: $VERSION"
info "Ziel:    $OUTPUT"

# ── 1. Anwendungsdateien nach /opt/hardwaretest kopieren ─────────────────────
APP_DEST="$PKG_ROOT$INSTALL_DIR"
mkdir -p "$APP_DEST"

COPY_PATHS=(
    hardwaretest assets autoinstall docs profiles scripts tests
    vendor LICENSE README.md INSTALL.md pyproject.toml
)
for path in "${COPY_PATHS[@]}"; do
    [[ -e "$path" ]] || continue
    cp -a "$path" "$APP_DEST/"
done

if [[ ! -f "$APP_DEST/vendor/prime95/mprime" ]]; then
    warn "vendor/prime95/mprime fehlt – Paket wird ohne gebuendeltes Prime95 gebaut."
fi

# Caches / Laufzeitmuell entfernen
find "$APP_DEST" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP_DEST" -type d -name '.pytest_cache' -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP_DEST" -type d -name '.ruff_cache' -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP_DEST" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
rm -f "$APP_DEST"/vendor/prime95/prime.txt \
      "$APP_DEST"/vendor/prime95/local.txt \
      "$APP_DEST"/vendor/prime95/results*.txt \
      "$APP_DEST"/vendor/prime95/worktodo.txt \
      "$APP_DEST"/vendor/prime95/*.hwbackup 2>/dev/null || true

# ── 2. Icon system-weit ablegen ──────────────────────────────────────────────
ICON_DEST="$PKG_ROOT/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$ICON_DEST"
if [[ -f assets/hardwaretest.svg ]]; then
    cp assets/hardwaretest.svg "$ICON_DEST/hardwaretest.svg"
fi

# ── 3. Desktop-Eintrag ───────────────────────────────────────────────────────
DESKTOP_DEST="$PKG_ROOT/usr/share/applications"
mkdir -p "$DESKTOP_DEST"
cat > "$DESKTOP_DEST/hardwaretest.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Hardwaretest
Comment=GUI für CPU/RAM/Disk-Stresstests (stress-ng, Prime95, fio)
Comment[en]=GUI launcher for CPU/RAM/disk stress tests
Exec=hardwaretest
Icon=hardwaretest
Terminal=false
Categories=System;Utility;
Keywords=stress;memory;cpu;disk;benchmark;hardware;
StartupNotify=true
DESKTOP

# ── 4. Paket-Metadaten berechnen ─────────────────────────────────────────────
INSTALLED_SIZE_KB="$(du -sk "$PKG_ROOT" | cut -f1)"

DEBIAN_DIR="$PKG_ROOT/DEBIAN"
mkdir -p "$DEBIAN_DIR"

cat > "$DEBIAN_DIR/control" <<CONTROL
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_SIZE_KB
Depends: python3, python3-venv, python3-pip, stress-ng, fio, btop, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxcb-xfixes0, libxkbcommon-x11-0, libgl1, libegl1
Recommends: smartmontools, jq, curl, lshw, pciutils, polkitd | policykit-1
Suggests: lm-sensors, edac-utils, nvme-cli
Description: GUI für Hardware-Stresstests (CPU, RAM, Festplatten)
 PySide6-Anwendung zum Starten von Hardware-Stresstests für CPU, RAM und
 Festplatten, optimiert für HPE ProLiant Server. Nutzt stress-ng, Prime95
 (mprime) und fio mit automatischer Pass/Fail-Erkennung, Temperatur- und
 ECC/EDAC-Überwachung sowie SMART-Diagnose.
 .
 Beim ersten Setup wird unter $INSTALL_DIR/.venv automatisch eine
 Python-Umgebung mit PySide6 angelegt. Start über den Befehl "hardwaretest"
 oder den Desktop-Eintrag.
CONTROL

# Konfigurationsdateien, die bei Updates nicht ueberschrieben werden sollen
cat > "$DEBIAN_DIR/conffiles" <<CONFFILES
$INSTALL_DIR/profiles/default.yaml
CONFFILES

# ── 5. Maintainer-Skripte ────────────────────────────────────────────────────
cat > "$DEBIAN_DIR/postinst" <<POSTINST
#!/bin/sh
set -e

INSTALL_DIR="$INSTALL_DIR"
VENV_DIR="\$INSTALL_DIR/.venv"
LAUNCHER="/usr/bin/hardwaretest"

case "\$1" in
    configure)
        echo "Richte Python-Umgebung unter \$VENV_DIR ein (kann einige Minuten dauern)..."
        if [ ! -x "\$VENV_DIR/bin/python" ]; then
            python3 -m venv "\$VENV_DIR"
        fi
        "\$VENV_DIR/bin/python" -m pip install --upgrade pip wheel >/dev/null 2>&1 || true
        if ! "\$VENV_DIR/bin/pip" install -e "\$INSTALL_DIR"; then
            echo "WARNUNG: pip-Installation fehlgeschlagen. Bitte Internetverbindung pruefen" >&2
            echo "         und manuell ausfuehren: \$VENV_DIR/bin/pip install -e \$INSTALL_DIR" >&2
        fi

        # CLI-Starter anlegen
        cat > "\$LAUNCHER" <<'LAUNCH'
#!/bin/sh
# Hardwaretest-Starter – nutzt die dedizierte venv unter /opt/hardwaretest.
exec /opt/hardwaretest/.venv/bin/python -m hardwaretest "\$@"
LAUNCH
        chmod 0755 "\$LAUNCHER"

        # Fastfetch wird im Info-Panel fuer die Systemuebersicht genutzt.
        # Installation ueber PPA – optional, darf postinst nicht abbrechen.
        # Da bei grafischer Installation (aptk, GNOME Software, ...) die
        # apt-Sperre waehrend des gesamten postinst gehalten wird, wird
        # ein vollstaendig entkoppelter Prozess (setsid) gestartet, der
        # auf die Freigabe wartet und dann fastfetch installiert.
        if command -v fastfetch >/dev/null 2>&1; then
            echo "Fastfetch bereits installiert."
        else
            echo "Fastfetch wird nach Freigabe der apt-Sperre im Hintergrund installiert..."
            echo "(Log: /var/log/hardwaretest-fastfetch-install.log)"
            FF_SCRIPT=\$(mktemp /tmp/hardwaretest-ff-XXXXXX.sh)
            cat > "\$FF_SCRIPT" <<'FFSCRIPT'
#!/bin/sh
LOG=/var/log/hardwaretest-fastfetch-install.log
echo "\$(date): Fastfetch-Hintergrundinstallation gestartet (PID \$\$)" >> "\$LOG"
# Warte bis apt-Sperre frei ist (max. 5 Minuten)
_tries=0
while fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; do
    if [ "\$_tries" -ge 150 ]; then
        echo "\$(date): Timeout – apt-Sperre nicht freigegeben." >> "\$LOG"
        rm -f "\$0"
        exit 1
    fi
    sleep 2
    _tries=\$((\$_tries + 1))
done
echo "\$(date): apt-Sperre frei, starte Installation..." >> "\$LOG"
if command -v add-apt-repository >/dev/null 2>&1; then
    add-apt-repository -y --no-update ppa:zhangsongcui3371/fastfetch >> "\$LOG" 2>&1
    apt-get update -qq >> "\$LOG" 2>&1
    apt-get install -y fastfetch >> "\$LOG" 2>&1
    if command -v fastfetch >/dev/null 2>&1; then
        echo "\$(date): Fastfetch erfolgreich installiert." >> "\$LOG"
    else
        echo "\$(date): Fastfetch Installation fehlgeschlagen." >> "\$LOG"
    fi
else
    echo "\$(date): add-apt-repository nicht verfuegbar." >> "\$LOG"
fi
rm -f "\$0"
FFSCRIPT
            chmod +x "\$FF_SCRIPT"
            setsid "\$FF_SCRIPT" </dev/null >/dev/null 2>&1 &
        fi

        # Icon-/Desktop-Caches aktualisieren (falls Tools vorhanden)
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database -q /usr/share/applications || true
        fi
        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
        fi

        echo "Hardwaretest installiert. Start mit: hardwaretest"
        ;;
esac

exit 0
POSTINST

cat > "$DEBIAN_DIR/prerm" <<PRERM
#!/bin/sh
set -e

INSTALL_DIR="$INSTALL_DIR"

case "\$1" in
    remove|upgrade|deconfigure)
        rm -f /usr/bin/hardwaretest
        # venv wird beim Entfernen geloescht (sie wurde im postinst erzeugt
        # und ist nicht Teil der Paketdateien).
        rm -rf "\$INSTALL_DIR/.venv"
        ;;
esac

exit 0
PRERM

cat > "$DEBIAN_DIR/postrm" <<POSTRM
#!/bin/sh
set -e

INSTALL_DIR="$INSTALL_DIR"

case "\$1" in
    purge|remove)
        # Bei vollstaendiger Deinstallation generierte Reste entfernen.
        rm -f /usr/bin/hardwaretest
        rm -rf "\$INSTALL_DIR/.venv"
        # Leeres Installationsverzeichnis aufraeumen (nur falls leer).
        rmdir "\$INSTALL_DIR" 2>/dev/null || true
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database -q /usr/share/applications || true
        fi
        ;;
esac

exit 0
POSTRM

chmod 0755 "$DEBIAN_DIR/postinst" "$DEBIAN_DIR/prerm" "$DEBIAN_DIR/postrm"

# ── 6. Rechte normalisieren & Paket bauen ────────────────────────────────────
find "$PKG_ROOT" -type d -exec chmod 0755 {} +
# Ausfuehrbare Dateien
[[ -f "$APP_DEST/vendor/prime95/mprime" ]] && chmod 0755 "$APP_DEST/vendor/prime95/mprime"
find "$APP_DEST/scripts" -type f -name '*.sh' -exec chmod 0755 {} + 2>/dev/null || true

rm -f "$OUTPUT"
dpkg-deb --root-owner-group --build "$PKG_ROOT" "$OUTPUT" >/dev/null

SIZE="$(du -h "$OUTPUT" | cut -f1)"
ok "Erstellt: $OUTPUT ($SIZE)"
echo ""
info "Installation:   sudo apt install $OUTPUT"
info "Deinstallation: sudo apt remove $PKG_NAME"
