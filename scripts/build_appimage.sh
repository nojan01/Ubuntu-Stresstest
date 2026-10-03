#!/usr/bin/env bash
# Baut Hardwaretest als eigenständige x86_64-AppImage.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_ROOT="${HARDWARETEST_PORTABLE_BUILD_ROOT:-$REPO_DIR/build/portable}"
VERSION="$(grep -m1 -E '^version' "$REPO_DIR/pyproject.toml" | sed -E 's/.*"([^"]+)".*/\1/')"
BUNDLE_DIR="$BUILD_ROOT/pyinstaller/hardwaretest"
APPDIR="$BUILD_ROOT/Hardwaretest.AppDir"
OUTPUT="$REPO_DIR/Hardwaretest-$VERSION-x86_64.AppImage"

if [[ "${HARDWARETEST_REUSE_BUNDLE:-0}" != "1" ]]; then
    "$SCRIPT_DIR/build_portable_bundle.sh"
fi
[[ -x "$BUNDLE_DIR/hardwaretest" ]] || {
    echo "FEHLER: Portables Programmbündel fehlt: $BUNDLE_DIR" >&2
    exit 1
}
rm -rf "$APPDIR"
mkdir -p \
    "$APPDIR/usr/lib/hardwaretest" \
    "$APPDIR/usr/bin" \
    "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a "$BUNDLE_DIR/." "$APPDIR/usr/lib/hardwaretest/"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="${APPDIR:-$(dirname "$(readlink -f "$0")")}"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/lib/hardwaretest/hardwaretest" "$@"
APPRUN
chmod 0755 "$APPDIR/AppRun"
ln -s ../lib/hardwaretest/hardwaretest "$APPDIR/usr/bin/hardwaretest"

cat > "$APPDIR/hardwaretest.desktop" <<DESKTOP
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
X-AppImage-Version=$VERSION
DESKTOP
cp "$APPDIR/hardwaretest.desktop" "$APPDIR/usr/share/applications/hardwaretest.desktop"
cp "$REPO_DIR/assets/hardwaretest.svg" "$APPDIR/hardwaretest.svg"
cp "$REPO_DIR/assets/hardwaretest.svg" \
    "$APPDIR/usr/share/icons/hicolor/scalable/apps/hardwaretest.svg"

CUSTOM_TOOLS="${APPIMAGETOOL:-}${APPIMAGE_RUNTIME_FILE:-}"
APPIMAGETOOL="${APPIMAGETOOL:-$REPO_DIR/.build-tools/appimagetool-x86_64.AppImage}"
RUNTIME_FILE="${APPIMAGE_RUNTIME_FILE:-$REPO_DIR/.build-tools/runtime-x86_64}"
# Standard-Cache immer gegen die festen SHA256 pruefen lassen.
if [[ -z "$CUSTOM_TOOLS" ]] \
    || [[ ! -x "$APPIMAGETOOL" || ! -s "$RUNTIME_FILE" ]]; then
    "$SCRIPT_DIR/fetch_appimage_tool.sh" >/dev/null
fi
[[ -x "$APPIMAGETOOL" ]] || {
    echo "FEHLER: appimagetool nicht gefunden: $APPIMAGETOOL" >&2
    exit 1
}
[[ -s "$RUNTIME_FILE" ]] || {
    echo "FEHLER: AppImage-Laufzeit fehlt: $RUNTIME_FILE" >&2
    exit 1
}

rm -f "$OUTPUT"
ARCH=x86_64 VERSION="$VERSION" "$APPIMAGETOOL" --appimage-extract-and-run \
    --runtime-file "$RUNTIME_FILE" \
    "$APPDIR" "$OUTPUT"
chmod 0755 "$OUTPUT"
echo "AppImage erstellt: $OUTPUT"
