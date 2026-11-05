#!/usr/bin/env bash
# Erstellt ein venv-freies RPM-Paket (RHEL/AlmaLinux/Rocky/Fedora) aus dem
# PyInstaller-Bundle – Gegenstück zu build_deb.sh. Python, PySide6 und alle
# Python-Module sind Bestandteil des Pakets; native Hardwarewerkzeuge kommen
# aus BaseOS/AppStream/EPEL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_ROOT="${HARDWARETEST_PORTABLE_BUILD_ROOT:-$REPO_DIR/build/portable}"
VERSION="$(grep -m1 -E '^version' "$REPO_DIR/pyproject.toml" | sed -E 's/.*"([^"]+)".*/\1/')"
PKG_NAME="hardwaretest"
ARCH="x86_64"
INSTALL_DIR="/opt/hardwaretest"
BUNDLE_DIR="$BUILD_ROOT/pyinstaller/hardwaretest"
OUTPUT="$REPO_DIR/${PKG_NAME}-${VERSION}-1.${ARCH}.rpm"

command -v rpmbuild >/dev/null 2>&1 || {
    echo "FEHLER: rpmbuild fehlt. Installation: sudo apt install rpm  bzw.  sudo dnf install rpm-build" >&2
    exit 1
}

if [[ "${HARDWARETEST_REUSE_BUNDLE:-0}" != "1" ]]; then
    "$SCRIPT_DIR/build_portable_bundle.sh"
fi
[[ -x "$BUNDLE_DIR/hardwaretest" ]] || {
    echo "FEHLER: Portables Programmbündel fehlt: $BUNDLE_DIR" >&2
    exit 1
}

BUILD_STAGE="$(mktemp -d /tmp/hardwaretest-rpm.XXXXXX)"
trap 'rm -rf "$BUILD_STAGE"' EXIT
TOPDIR="$BUILD_STAGE/rpmbuild"
PKG_ROOT="$BUILD_STAGE/buildroot"
APP_DEST="$PKG_ROOT$INSTALL_DIR"
mkdir -p \
    "$TOPDIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS} \
    "$APP_DEST" \
    "$PKG_ROOT/usr/bin" \
    "$PKG_ROOT/usr/share/applications" \
    "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps" \
    "$PKG_ROOT/usr/share/licenses/$PKG_NAME" \
    "$PKG_ROOT/etc/sudoers.d"

cp -a "$BUNDLE_DIR/." "$APP_DEST/"
cp "$REPO_DIR/LICENSE" "$PKG_ROOT/usr/share/licenses/$PKG_NAME/LICENSE"
cp "$REPO_DIR/assets/hardwaretest.svg" \
    "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps/hardwaretest.svg"

# Auf RHEL heißt die Administratorgruppe „wheel“ (Debian: „sudo“).
ODCD_DIR="$INSTALL_DIR/_internal/vendor/opendcdiag"
cat > "$PKG_ROOT/etc/sudoers.d/hardwaretest-opendcdiag" <<SUDOERS
# Hardwaretest: OpenDCDiag ohne Passwortabfrage als root (MSR, In-Field Scan)
%wheel ALL=(root) NOPASSWD: $ODCD_DIR/opendcdiag, $ODCD_DIR/opendcdiag-generic
SUDOERS
cat > "$PKG_ROOT/etc/sudoers.d/hardwaretest-dmidecode" <<'SUDOERS'
# Hardwaretest: System, CPU, Speichermodule und Netzteile ohne Passwortabfrage lesen
%wheel ALL=(root) NOPASSWD: /usr/sbin/dmidecode -t 1 -t 4 -t 16 -t 17
%wheel ALL=(root) NOPASSWD: /usr/sbin/dmidecode -t 1 -t 4 -t 16 -t 17 -t 39
SUDOERS
chmod 0440 "$PKG_ROOT/etc/sudoers.d/hardwaretest-"*
if command -v visudo >/dev/null 2>&1; then
    for f in "$PKG_ROOT/etc/sudoers.d/hardwaretest-"*; do visudo -c -q -f "$f"; done
fi

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

find "$PKG_ROOT" -type d -exec chmod 0755 {} +
[[ -f "$APP_DEST/_internal/vendor/prime95/mprime" ]] && \
    chmod 0755 "$APP_DEST/_internal/vendor/prime95/mprime"
for odcd in "$APP_DEST"/_internal/vendor/opendcdiag/opendcdiag "$APP_DEST"/_internal/vendor/opendcdiag/opendcdiag-generic; do
    if [[ -f "$odcd" ]]; then chmod 0755 "$odcd"; fi
done
find "$APP_DEST/_internal/scripts" -type f -name '*.sh' -exec chmod 0755 {} +

cat > "$TOPDIR/SPECS/$PKG_NAME.spec" <<SPEC
Name:           $PKG_NAME
Version:        $VERSION
Release:        1
Summary:        Eigenständige GUI für Hardware-Stresstests
License:        MIT
URL:            https://github.com/nojan01/Ubuntu-Stresstest
ExclusiveArch:  $ARCH
AutoReqProv:    no

# stress-ng und btop liegen in EPEL: vorher "sudo dnf install epel-release"
Requires:       stress-ng fio btop dmidecode libzstd zlib e2fsprogs util-linux polkit
Requires:       libxcb xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-renderutil libxkbcommon-x11 mesa-libGL mesa-libEGL
Recommends:     vulkan-loader mesa-vulkan-drivers python3 smartmontools jq curl lshw pciutils iperf3 iputils nvme-cli ipmitool
Suggests:       lm_sensors edac-utils ethtool mstflint zfs memtest86+ fastfetch

%description
Enthält Python, PySide6 und alle Python-Module bereits vollständig. Bei der
Installation werden weder eine Python-venv angelegt noch Pakete mit pip aus
dem Internet geladen. Native Testwerkzeuge werden von dnf bereitgestellt.
Voraussetzung auf RHEL/AlmaLinux/Rocky: EPEL aktivieren
(sudo dnf install epel-release), dort liegen stress-ng, btop und fastfetch.

%install
cp -a "$PKG_ROOT"/. %{buildroot}/

%post
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || :
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || :
fi

%postun
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || :
fi

%files
%license /usr/share/licenses/$PKG_NAME/LICENSE
%config(noreplace) %attr(0440,root,root) /etc/sudoers.d/hardwaretest-opendcdiag
%config(noreplace) %attr(0440,root,root) /etc/sudoers.d/hardwaretest-dmidecode
/usr/bin/hardwaretest
/usr/share/applications/hardwaretest.desktop
/usr/share/icons/hicolor/scalable/apps/hardwaretest.svg
$INSTALL_DIR
SPEC

rpmbuild -bb \
    --define "_topdir $TOPDIR" \
    --define "_build_id_links none" \
    --define "__brp_mangle_shebangs %{nil}" \
    --define "__brp_strip %{nil}" \
    --define "__brp_strip_static_archive %{nil}" \
    --define "__brp_check_rpaths %{nil}" \
    --define "__os_install_post %{nil}" \
    --define "debug_package %{nil}" \
    --quiet \
    "$TOPDIR/SPECS/$PKG_NAME.spec"

rm -f "$OUTPUT"
cp "$TOPDIR/RPMS/$ARCH/${PKG_NAME}-${VERSION}-1.${ARCH}.rpm" "$OUTPUT"
echo "Venv-freies RPM erstellt: $OUTPUT"
