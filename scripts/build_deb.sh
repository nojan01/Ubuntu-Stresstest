#!/usr/bin/env bash
# Erstellt ein venv-freies Debian-Paket aus dem PyInstaller-Bundle.
# Python, PySide6 und alle Python-Module sind Bestandteil des Pakets. APT darf
# weiterhin die nativen Hardwarewerkzeuge und Linux-Bibliotheken installieren.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_ROOT="${HARDWARETEST_PORTABLE_BUILD_ROOT:-$REPO_DIR/build/portable}"
VERSION="$(grep -m1 -E '^version' "$REPO_DIR/pyproject.toml" | sed -E 's/.*"([^"]+)".*/\1/')"
PKG_NAME="hardwaretest"
ARCH="amd64"
MAINTAINER="Norbert Jander <n.jander@posteo.de>"
INSTALL_DIR="/opt/hardwaretest"
BUNDLE_DIR="$BUILD_ROOT/pyinstaller/hardwaretest"
OUTPUT="$REPO_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"

command -v dpkg-deb >/dev/null 2>&1 || {
    echo "FEHLER: dpkg-deb fehlt. Installation: sudo apt install dpkg-dev" >&2
    exit 1
}

if [[ "${HARDWARETEST_REUSE_BUNDLE:-0}" != "1" ]]; then
    "$SCRIPT_DIR/build_portable_bundle.sh"
fi
[[ -x "$BUNDLE_DIR/hardwaretest" ]] || {
    echo "FEHLER: Portables Programmbündel fehlt: $BUNDLE_DIR" >&2
    exit 1
}

BUILD_STAGE="$(mktemp -d /tmp/hardwaretest-deb.XXXXXX)"
trap 'rm -rf "$BUILD_STAGE"' EXIT
PKG_ROOT="$BUILD_STAGE/pkg"
APP_DEST="$PKG_ROOT$INSTALL_DIR"
mkdir -p \
    "$APP_DEST" \
    "$PKG_ROOT/usr/bin" \
    "$PKG_ROOT/usr/share/applications" \
    "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps" \
    "$PKG_ROOT/usr/share/doc/$PKG_NAME" \
    "$PKG_ROOT/etc/sudoers.d" \
    "$PKG_ROOT/DEBIAN"

cp -a "$BUNDLE_DIR/." "$APP_DEST/"
cp "$REPO_DIR/LICENSE" "$PKG_ROOT/usr/share/doc/$PKG_NAME/copyright"
cp "$REPO_DIR/assets/hardwaretest.svg" \
    "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps/hardwaretest.svg"

# OpenDCDiag braucht root für MSR-Zugriff und Intel In-Field Scan. Mitglieder der
# Gruppe sudo dürfen nur diese beiden Programme ohne Passwort als root starten.
ODCD_DIR="$INSTALL_DIR/_internal/vendor/opendcdiag"
cat > "$PKG_ROOT/etc/sudoers.d/hardwaretest-opendcdiag" <<SUDOERS
# Hardwaretest: OpenDCDiag ohne Passwortabfrage als root (MSR, In-Field Scan)
%sudo ALL=(root) NOPASSWD: $ODCD_DIR/opendcdiag, $ODCD_DIR/opendcdiag-generic
SUDOERS
chmod 0440 "$PKG_ROOT/etc/sudoers.d/hardwaretest-opendcdiag"
if command -v visudo >/dev/null 2>&1; then
    visudo -c -q -f "$PKG_ROOT/etc/sudoers.d/hardwaretest-opendcdiag"
fi

# Tab „System“: Speichermodule (DMI) lesen – nur genau diese dmidecode-Argumente.
cat > "$PKG_ROOT/etc/sudoers.d/hardwaretest-dmidecode" <<'SUDOERS'
# Hardwaretest: Speichermodule und Seriennummer ohne Passwortabfrage lesen
%sudo ALL=(root) NOPASSWD: /usr/sbin/dmidecode -t 1 -t 16 -t 17
SUDOERS
chmod 0440 "$PKG_ROOT/etc/sudoers.d/hardwaretest-dmidecode"
if command -v visudo >/dev/null 2>&1; then
    visudo -c -q -f "$PKG_ROOT/etc/sudoers.d/hardwaretest-dmidecode"
fi
printf '%s\n' /etc/sudoers.d/hardwaretest-opendcdiag /etc/sudoers.d/hardwaretest-dmidecode > "$PKG_ROOT/DEBIAN/conffiles"

cat > "$PKG_ROOT/usr/bin/hardwaretest" <<'LAUNCHER'
#!/bin/sh
exec /opt/hardwaretest/hardwaretest "$@"
LAUNCHER
chmod 0755 "$PKG_ROOT/usr/bin/hardwaretest"

cat > "$PKG_ROOT/usr/share/applications/hardwaretest.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Hardwaretest
Comment=GUI für CPU-, RAM-, Datenträger-, Netzwerk- und GPU-Tests
Comment[en]=GUI for CPU, RAM, storage, network and GPU tests
Exec=hardwaretest
Icon=hardwaretest
Terminal=false
Categories=System;Utility;
Keywords=stress;memory;cpu;disk;benchmark;hardware;nvme;gpu;network;
StartupNotify=true
DESKTOP

INSTALLED_SIZE_KB="$(du -sk "$PKG_ROOT" | cut -f1)"
cat > "$PKG_ROOT/DEBIAN/control" <<CONTROL
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_SIZE_KB
Depends: stress-ng, fio, btop, dmidecode, libzstd1, zlib1g, e2fsprogs, util-linux, pkexec, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxcb-xfixes0, libxkbcommon-x11-0, libgl1, libegl1
Recommends: smartmontools, jq, curl, lshw, pciutils, iperf3, iputils-ping, nvme-cli, polkitd | policykit-1, vmfs6-tools, vmfs-tools
Suggests: lm-sensors, edac-utils, zfsutils-linux, datacenter-gpu-manager-4-cuda12 | datacenter-gpu-manager-4-cuda13
Description: Eigenständige GUI für Hardware-Stresstests
 Enthält Python, PySide6 und alle Python-Module bereits vollständig. Bei der
 Installation werden weder eine Python-venv angelegt noch Pakete mit pip aus
 dem Internet geladen. Native Testwerkzeuge werden von APT bereitgestellt.
CONTROL

cat > "$PKG_ROOT/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
# Alte Installationen über das Legacy-ZIP legten einen nicht paketverwalteten
# venv-Starter unter /usr/local/bin ab. Er steht üblicherweise vor /usr/bin.
# Nur den eindeutig von Hardwaretest erzeugten Starter entfernen; eine fremde
# Datei gleichen Namens bleibt unangetastet.
legacy_launcher=/usr/local/bin/hardwaretest
if [ -f "$legacy_launcher" ] \
    && grep -Fq 'generiert von install_hardwaretest.sh' "$legacy_launcher" \
    && grep -Fq 'REPO_DIR="/opt/hardwaretest"' "$legacy_launcher" \
    && grep -Fq 'VENV="$REPO_DIR/.venv/bin/python"' "$legacy_launcher"; then
    rm -f "$legacy_launcher"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
POSTINST

cat > "$PKG_ROOT/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
exit 0
POSTRM

chmod 0755 "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/postrm"
find "$PKG_ROOT" -type d -exec chmod 0755 {} +
[[ -f "$APP_DEST/_internal/vendor/prime95/mprime" ]] && \
    chmod 0755 "$APP_DEST/_internal/vendor/prime95/mprime"
for odcd in "$APP_DEST"/_internal/vendor/opendcdiag/opendcdiag "$APP_DEST"/_internal/vendor/opendcdiag/opendcdiag-generic; do
    if [[ -f "$odcd" ]]; then chmod 0755 "$odcd"; fi
done
find "$APP_DEST/_internal/scripts" -type f -name '*.sh' -exec chmod 0755 {} +

rm -f "$OUTPUT"
dpkg-deb --root-owner-group --build -Zxz "$PKG_ROOT" "$OUTPUT" >/dev/null

if dpkg-deb --ctrl-tarfile "$OUTPUT" | tar -xOf - ./control \
    | grep '^Depends:' | grep -Eq 'python3|venv|pip'; then
    echo "FEHLER: Unerwartete Python-Laufzeitabhängigkeit im Paket." >&2
    exit 1
fi
echo "Venv-freies DEB erstellt: $OUTPUT"
