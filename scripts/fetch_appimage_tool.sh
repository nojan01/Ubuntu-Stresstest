#!/usr/bin/env bash
# Lädt das offizielle appimagetool und den x86_64-Laufzeitstub. Beide Dateien
# werden lokal zwischengespeichert, damit wiederholte Builds offline arbeiten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOL_DIR="${HARDWARETEST_BUILD_TOOLS_DIR:-$REPO_DIR/.build-tools}"
TOOL="$TOOL_DIR/appimagetool-x86_64.AppImage"
RUNTIME="$TOOL_DIR/runtime-x86_64"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"

mkdir -p "$TOOL_DIR"
if [[ ! -s "$TOOL" ]]; then
    command -v curl >/dev/null 2>&1 || {
        echo "FEHLER: curl wird zum Laden von appimagetool benötigt." >&2
        exit 1
    }
    curl --fail --location --retry 3 --output "$TOOL" "$TOOL_URL"
fi
chmod 0755 "$TOOL"
if [[ ! -s "$RUNTIME" ]]; then
    command -v curl >/dev/null 2>&1 || {
        echo "FEHLER: curl wird zum Laden der AppImage-Laufzeit benötigt." >&2
        exit 1
    }
    curl --fail --location --retry 3 --output "$RUNTIME" "$RUNTIME_URL"
fi
chmod 0755 "$RUNTIME"
printf '%s\n%s\n' "$TOOL" "$RUNTIME"
