#!/usr/bin/env bash
# Erstellt ein eigenständiges PyInstaller-Verzeichnis mit Python, PySide6 und
# allen Python-Abhängigkeiten. Auf dem Zielsystem werden weder Python noch eine
# venv oder pip benötigt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_ROOT="${HARDWARETEST_PORTABLE_BUILD_ROOT:-$REPO_DIR/build/portable}"
PYTHON="${PYINSTALLER_PYTHON:-$REPO_DIR/.venv/bin/python}"
DIST_ROOT="$BUILD_ROOT/pyinstaller"
BUNDLE_DIR="$DIST_ROOT/hardwaretest"

if [[ ! -x "$PYTHON" ]]; then
    echo "FEHLER: Build-Python nicht gefunden: $PYTHON" >&2
    echo "Bitte zuerst eine Build-venv erstellen und requirements-build.txt installieren." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import PyInstaller, PySide6, psutil' >/dev/null 2>&1; then
    echo "FEHLER: PyInstaller, PySide6 oder psutil fehlen in der Build-Umgebung." >&2
    echo "Aufruf: $PYTHON -m pip install -r $REPO_DIR/requirements-build.txt -e $REPO_DIR" >&2
    exit 1
fi

rm -rf "$DIST_ROOT" "$BUILD_ROOT/work" "$BUILD_ROOT/spec"
mkdir -p "$DIST_ROOT" "$BUILD_ROOT/work" "$BUILD_ROOT/spec"

"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --onedir \
    --contents-directory _internal \
    --name hardwaretest \
    --paths "$REPO_DIR" \
    --distpath "$DIST_ROOT" \
    --workpath "$BUILD_ROOT/work" \
    --specpath "$BUILD_ROOT/spec" \
    --add-data "$REPO_DIR/assets:assets" \
    --add-data "$REPO_DIR/docs:docs" \
    --add-data "$REPO_DIR/profiles:profiles" \
    --add-data "$REPO_DIR/scripts:scripts" \
    --add-data "$REPO_DIR/vendor:vendor" \
    --add-data "$REPO_DIR/LICENSE:." \
    --add-data "$REPO_DIR/README.md:." \
    --add-data "$REPO_DIR/hardwaretest/tests/memory_fill_script.py:hardwaretest/tests" \
    "$REPO_DIR/scripts/portable_entry.py"

[[ -x "$BUNDLE_DIR/hardwaretest" ]] || {
    echo "FEHLER: PyInstaller-Binary wurde nicht erzeugt." >&2
    exit 1
}

if [[ -f "$BUNDLE_DIR/_internal/vendor/prime95/mprime" ]]; then
    chmod 0755 "$BUNDLE_DIR/_internal/vendor/prime95/mprime"
fi
find "$BUNDLE_DIR/_internal/scripts" -type f -name '*.sh' -exec chmod 0755 {} +

VERSION="$($BUNDLE_DIR/hardwaretest --version)"
$BUNDLE_DIR/hardwaretest --self-check
echo "Portable Anwendung erstellt: $BUNDLE_DIR"
echo "$VERSION"
