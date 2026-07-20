#!/usr/bin/env bash
# =============================================================================
#  build_release_zip.sh – Erstellt das Installations-ZIP (Hardwaretest-vX.Y.Z.zip)
#
#  Das ZIP enthaelt die komplette Anwendung inkl. gebuendeltem Prime95
#  (vendor/prime95/mprime) und wird von:
#    * scripts/install_hardwaretest.sh
#    * scripts/build_autoinstall_iso.sh
#    * autoinstall/user-data
#  als Quelle verwendet.
#
#  Verwendung:
#    bash scripts/build_release_zip.sh
#
#  Voraussetzungen:
#    sudo apt install -y zip
# =============================================================================
set -euo pipefail

# ── Farben ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

command -v zip >/dev/null 2>&1 || fail "zip nicht gefunden. Bitte installieren: sudo apt install zip"

# ── Pfade ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

# ── Version aus pyproject.toml lesen ─────────────────────────────────────────
VERSION="$(grep -m1 -E '^version' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')"
[[ -n "$VERSION" ]] || fail "Konnte Version nicht aus pyproject.toml lesen."
OUTPUT="Hardwaretest-v${VERSION}.zip"

info "Repo:    $REPO_DIR"
info "Version: $VERSION"
info "Ziel:    $OUTPUT"

# ── Prime95-Binary pruefen (optional, aber empfohlen) ────────────────────────
if [[ ! -f vendor/prime95/mprime ]]; then
    warn "vendor/prime95/mprime fehlt – das ZIP wird ohne gebuendeltes Prime95 erstellt."
    warn "Der Installer laedt mprime dann zur Laufzeit nach."
fi

# ── Bestehendes Artefakt entfernen ───────────────────────────────────────────
rm -f "$OUTPUT"

# ── Inhalte der Anwendung (nur existierende Pfade aufnehmen) ──────────────────
INCLUDE=(
    hardwaretest
    assets
    autoinstall
    docs
    profiles
    scripts
    tests
    vendor
    README.md
    INSTALL.md
    pyproject.toml
    .gitignore
)
PRESENT=()
for path in "${INCLUDE[@]}"; do
    [[ -e "$path" ]] && PRESENT+=("$path")
done

# ── ZIP erstellen (Caches, venv, Laufzeitdateien ausschliessen) ──────────────
zip -r -q "$OUTPUT" "${PRESENT[@]}" \
    -x '*/__pycache__/*' \
       '*.pyc' \
       '*.pyo' \
       '*/.pytest_cache/*' \
       '*/.ruff_cache/*' \
       '*/.mypy_cache/*' \
       'vendor/prime95/prime.txt' \
       'vendor/prime95/local.txt' \
       'vendor/prime95/results*.txt' \
       'vendor/prime95/worktodo.txt' \
       'vendor/prime95/*.hwbackup'

SIZE="$(du -h "$OUTPUT" | cut -f1)"
COUNT="$(unzip -l "$OUTPUT" | tail -1 | awk '{print $2}')"
ok "Erstellt: $OUTPUT ($SIZE, $COUNT Dateien)"
