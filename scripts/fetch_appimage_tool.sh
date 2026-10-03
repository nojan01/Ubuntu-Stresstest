#!/usr/bin/env bash
# Lädt das offizielle appimagetool und den x86_64-Laufzeitstub. Beide Dateien
# werden lokal zwischengespeichert, damit wiederholte Builds offline arbeiten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOL_DIR="${HARDWARETEST_BUILD_TOOLS_DIR:-$REPO_DIR/.build-tools}"
TOOL="$TOOL_DIR/appimagetool-x86_64.AppImage"
RUNTIME="$TOOL_DIR/runtime-x86_64"
APPIMAGETOOL_VERSION="1.9.1"
RUNTIME_VERSION="20251108"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/${RUNTIME_VERSION}/runtime-x86_64"
TOOL_SHA256="${APPIMAGETOOL_SHA256:-ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0}"
RUNTIME_SHA256="${APPIMAGE_RUNTIME_SHA256:-2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d}"

sha_ok() {
    printf '%s  %s\n' "$2" "$1" | sha256sum -c --status -
}

# Laedt eine Datei, falls sie fehlt oder eine abweichende Pruefsumme hat
# (z. B. ein alter Continuous-Download im Cache), und bricht ab, wenn auch
# der frische Download nicht zur festen SHA256 passt.
fetch_verified() {
    local file="$1" url="$2" expected="$3" label="$4"
    if [[ -s "$file" ]] && sha_ok "$file" "$expected"; then
        return 0
    fi
    command -v curl >/dev/null 2>&1 || {
        echo "FEHLER: curl wird zum Laden von $label benötigt." >&2
        exit 1
    }
    rm -f "$file"
    curl --fail --location --retry 3 --output "$file.part" "$url"
    if ! sha_ok "$file.part" "$expected"; then
        rm -f "$file.part"
        echo "FEHLER: SHA256 von $label stimmt nicht ($url)." >&2
        exit 1
    fi
    mv "$file.part" "$file"
}

mkdir -p "$TOOL_DIR"
fetch_verified "$TOOL" "$TOOL_URL" "$TOOL_SHA256" "appimagetool $APPIMAGETOOL_VERSION"
chmod 0755 "$TOOL"
fetch_verified "$RUNTIME" "$RUNTIME_URL" "$RUNTIME_SHA256" "AppImage-Runtime $RUNTIME_VERSION"
chmod 0755 "$RUNTIME"
printf '%s\n%s\n' "$TOOL" "$RUNTIME"
