#!/usr/bin/env bash
# =============================================================================
#  build_deb.sh – Erstellt ein installierbares Ubuntu/Debian-Paket (.deb)
#
#  Das Paket installiert Hardwaretest nach /opt/hardwaretest, legt einen
#  Launcher /usr/bin/hardwaretest, eine .desktop-Datei und ein Icon an und
#  deklariert alle benötigten APT-Abhängigkeiten (PySide6, psutil, …).
#
#  Verwendung:
#    bash scripts/build_deb.sh            # baut hardwaretest_<version>_all.deb
#    bash scripts/build_deb.sh /tmp/out   # Ausgabeverzeichnis angeben
#
#  Installation des fertigen Pakets:
#    sudo apt install ./hardwaretest_<version>_all.deb
#  (oder: sudo dpkg -i hardwaretest_<version>_all.deb && sudo apt -f install)
# =============================================================================
set -euo pipefail

# ── Pfade ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="${1:-$REPO_DIR/dist}"

# ── Metadaten ─────────────────────────────────────────────────────────────────
PKG_NAME="hardwaretest"
# Version aus pyproject.toml lesen (Fallback 0.1.0)
VERSION="$(grep -m1 '^version' "$REPO_DIR/pyproject.toml" | sed -E 's/.*"([^"]+)".*/\1/')"
VERSION="${VERSION:-0.1.0}"
MAINTAINER="Hardwaretest Maintainers <maintainers@example.com>"
ARCH="all"

# ── Voraussetzungen ──────────────────────────────────────────────────────────
command -v dpkg-deb >/dev/null 2>&1 || {
    echo "FEHLER: dpkg-deb nicht gefunden. Installieren mit: sudo apt install dpkg-dev" >&2
    exit 1
}

# ── Staging-Verzeichnis vorbereiten ──────────────────────────────────────────
STAGE="$(mktemp -d)"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

INSTALL_ROOT="/opt/$PKG_NAME"
PKG_INSTALL_DIR="$STAGE$INSTALL_ROOT"

echo "[INFO] Staging Anwendungsdateien nach $INSTALL_ROOT ..."
mkdir -p "$PKG_INSTALL_DIR"

# Projektdateien kopieren (ohne VCS-, Build- und Laufzeit-Artefakte).
# Der relative Layout (hardwaretest/, scripts/, vendor/, assets/) bleibt
# erhalten, da der Code Geschwister-Verzeichnisse über __file__ auflöst.
copy_if_exists() {
    local item="$1"
    [[ -e "$REPO_DIR/$item" ]] && cp -a "$REPO_DIR/$item" "$PKG_INSTALL_DIR/"
}
copy_if_exists hardwaretest
copy_if_exists scripts
copy_if_exists assets
copy_if_exists profiles
copy_if_exists vendor
copy_if_exists docs
copy_if_exists pyproject.toml
copy_if_exists README.md

# Aufräumen: Caches und Build-Reste, die cp -a evtl. mitgenommen hat.
find "$PKG_INSTALL_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$PKG_INSTALL_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
rm -rf "$PKG_INSTALL_DIR/.venv" "$PKG_INSTALL_DIR/build" "$PKG_INSTALL_DIR/dist" 2>/dev/null || true

# ── Launcher /usr/bin/hardwaretest ───────────────────────────────────────────
echo "[INFO] Erstelle Launcher /usr/bin/$PKG_NAME ..."
mkdir -p "$STAGE/usr/bin"
cat >"$STAGE/usr/bin/$PKG_NAME" <<LAUNCHER
#!/bin/sh
# Hardwaretest GUI Launcher (Debian-Paket)
exec env PYTHONPATH="$INSTALL_ROOT\${PYTHONPATH:+:\$PYTHONPATH}" python3 -m hardwaretest "\$@"
LAUNCHER
chmod 755 "$STAGE/usr/bin/$PKG_NAME"

# ── Desktop-Datei + Icons ─────────────────────────────────────────────────────
echo "[INFO] Installiere Desktop-Eintrag und Icon ..."
mkdir -p "$STAGE/usr/share/applications"
cp "$REPO_DIR/packaging/hardwaretest.desktop" "$STAGE/usr/share/applications/$PKG_NAME.desktop"

if [[ -f "$REPO_DIR/assets/hardwaretest.svg" ]]; then
    mkdir -p "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/pixmaps"
    cp "$REPO_DIR/assets/hardwaretest.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/$PKG_NAME.svg"
    cp "$REPO_DIR/assets/hardwaretest.svg" "$STAGE/usr/share/pixmaps/$PKG_NAME.svg"
fi

# ── Dokumentation (copyright + changelog) ────────────────────────────────────
DOC_DIR="$STAGE/usr/share/doc/$PKG_NAME"
mkdir -p "$DOC_DIR"
cat >"$DOC_DIR/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: hardwaretest

Files: *
Copyright: Hardwaretest Maintainers
License: MIT
COPYRIGHT

cat >"$DOC_DIR/changelog.Debian" <<CHANGELOG
$PKG_NAME ($VERSION) stable; urgency=medium

  * Paketierung als natives Debian-/Ubuntu-Paket.

 -- $MAINTAINER  $(date -R)
CHANGELOG
gzip -9n "$DOC_DIR/changelog.Debian"

# ── Installierte Größe berechnen (in KiB) ────────────────────────────────────
INSTALLED_SIZE="$(du -sk "$STAGE" | cut -f1)"

# ── DEBIAN/control + Maintainer-Skripte ──────────────────────────────────────
echo "[INFO] Schreibe DEBIAN/control ..."
mkdir -p "$STAGE/DEBIAN"
cat >"$STAGE/DEBIAN/control" <<CONTROL
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.10),
 python3-pyside6.qtwidgets,
 python3-pyside6.qtgui,
 python3-pyside6.qtcore,
 python3-psutil,
 python3-yaml
Recommends: stress-ng,
 fio,
 smartmontools,
 jq,
 lshw,
 pciutils,
 nvme-cli,
 hdparm,
 lm-sensors,
 util-linux,
 polkitd | policykit-1
Suggests: btop,
 fastfetch,
 edac-utils,
 memtest86+
Description: GUI für CPU-, RAM- und Festplatten-Stresstests
 PySide6-Anwendung zum Starten von Hardware-Stresstests für CPU, RAM und
 Festplatten - optimiert für HPE ProLiant Server. Nutzt bewährte Werkzeuge
 wie stress-ng, Prime95 (mprime) und fio.
 .
 Enthält Disk-Tests (Datei, Geräte, destruktiv), SMART-/EDAC-Auswertung,
 Temperatur-Monitoring und automatische Pass/Fail-Erkennung.
CONTROL

# Konfigurationsdateien als conffiles markieren? Keine in /etc -> entfällt.

# postinst: Desktop-Datenbank und Icon-Cache aktualisieren
cat >"$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
    fi
fi

exit 0
POSTINST
chmod 755 "$STAGE/DEBIAN/postinst"

# postrm: Caches nach Entfernung aktualisieren
cat >"$STAGE/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e

if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
    fi
fi

exit 0
POSTRM
chmod 755 "$STAGE/DEBIAN/postrm"

# ── Berechtigungen normalisieren ─────────────────────────────────────────────
# Alle Verzeichnisse auf 755 (mktemp-Root ist 700, umask kann 775 erzeugen).
chmod 755 "$STAGE"
find "$STAGE" -path "$STAGE/DEBIAN" -prune -o -type d -exec chmod 755 {} + 2>/dev/null || true
chmod 644 "$STAGE/usr/share/applications/$PKG_NAME.desktop"
# Shell-Skripte ausführbar lassen
find "$STAGE/opt/$PKG_NAME/scripts" -type f -name '*.sh' -exec chmod 755 {} + 2>/dev/null || true

# ── Paket bauen ──────────────────────────────────────────────────────────────
mkdir -p "$OUTPUT_DIR"
DEB_FILE="$OUTPUT_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"
echo "[INFO] Baue $DEB_FILE ..."
dpkg-deb --root-owner-group --build "$STAGE" "$DEB_FILE" >/dev/null

echo ""
echo "═══ Fertig ═══"
echo "  Paket: $DEB_FILE"
echo "  Größe: $(du -h "$DEB_FILE" | cut -f1)"
echo ""
echo "Installieren mit:"
echo "  sudo apt install $DEB_FILE"
