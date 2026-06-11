#!/usr/bin/env bash
# =============================================================================
#  build_autoinstall_iso.sh – Erstellt ein vollautomatisches Installations-ISO
#
#  Nimmt das offizielle Ubuntu Server 24.04 ISO und bettet die Autoinstall-
#  Konfiguration + Hardwaretest-ZIP ein. Ergebnis: ~2.6 GB ISO für iLO.
#
#  Verwendung:
#    sudo bash scripts/build_autoinstall_iso.sh [ubuntu-24.04-live-server-amd64.iso]
#
#  Voraussetzungen:
#    sudo apt install -y p7zip-full xorriso wget
# =============================================================================
set -euo pipefail

# ── Farben ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── Root-Check ────────────────────────────────────────────────────────────────
if [[ $(id -u) -ne 0 ]]; then
    fail "Dieses Script muss mit sudo ausgeführt werden."
fi

# ── Pfade ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AUTOINSTALL_DIR="$REPO_DIR/autoinstall"
BUILD_DIR="/tmp/hardwaretest-iso-build"
OUTPUT_ISO="$REPO_DIR/hardwaretest-autoinstall.iso"

# ── Ubuntu ISO ermitteln ──────────────────────────────────────────────────────
UBUNTU_ISO="${1:-}"
if [[ -z "$UBUNTU_ISO" ]]; then
    # Im Repo-Verzeichnis oder /tmp suchen
    for candidate in \
        "$REPO_DIR"/ubuntu-*-live-server-amd64.iso \
        /tmp/ubuntu-*-live-server-amd64.iso \
        "$HOME"/Downloads/ubuntu-*-live-server-amd64.iso; do
        if [[ -f "$candidate" ]]; then
            UBUNTU_ISO="$candidate"
            break
        fi
    done
fi

if [[ -z "$UBUNTU_ISO" || ! -f "$UBUNTU_ISO" ]]; then
    echo ""
    echo -e "${BOLD}Verwendung:${NC}"
    echo "  sudo bash $0 <pfad-zur-ubuntu-server-iso>"
    echo ""
    echo "Ubuntu Server 24.04 LTS ISO herunterladen:"
    echo "  wget https://releases.ubuntu.com/24.04/ubuntu-24.04-live-server-amd64.iso"
    echo ""
    fail "Ubuntu Server ISO nicht gefunden."
fi

info "Ubuntu ISO:     $UBUNTU_ISO"
info "Autoinstall:    $AUTOINSTALL_DIR/user-data"
info "Ausgabe:        $OUTPUT_ISO"
echo ""

# ── Voraussetzungen prüfen ───────────────────────────────────────────────────
for cmd in xorriso 7z; do
    if ! command -v "$cmd" &>/dev/null; then
        fail "'$cmd' nicht gefunden. Installieren mit: sudo apt install -y p7zip-full xorriso"
    fi
done

# ── Prüfe Autoinstall-Dateien ────────────────────────────────────────────────
if [[ ! -f "$AUTOINSTALL_DIR/user-data" ]]; then
    fail "Autoinstall-Konfiguration nicht gefunden: $AUTOINSTALL_DIR/user-data"
fi

# ── Hardwaretest-ZIP erstellen (falls noch nicht vorhanden) ──────────────────
HARDWARETEST_ZIP="$REPO_DIR/Hardwaretest-v0.1.0.zip"
if [[ ! -f "$HARDWARETEST_ZIP" ]]; then
    info "Erstelle Hardwaretest-ZIP..."
    cd "$REPO_DIR"
    zip -r "$HARDWARETEST_ZIP" . \
        -x ".venv/*" -x "__pycache__/*" -x "*/__pycache__/*" -x "*.pyc" \
        -x ".git/*" -x ".idea/*" -x ".vscode/*" \
        -x ".mypy_cache/*" -x ".pytest_cache/*" -x ".ruff_cache/*" \
        -x "poetry.lock" \
        -x "vendor/prime95/mprime" -x "vendor/prime95/libgmp.so*" \
        -x "vendor/prime95/*.txt" \
        -x "*.bak" -x "*.hwbackup" \
        -x "autoinstall/*" -x "*.iso"
    ok "ZIP erstellt: $HARDWARETEST_ZIP"
fi

# ── Build-Verzeichnis vorbereiten ────────────────────────────────────────────
info "Räume Build-Verzeichnis auf..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# ── ISO entpacken ────────────────────────────────────────────────────────────
info "Entpacke Ubuntu ISO (das dauert ~1 Minute)..."
7z x -o"$BUILD_DIR/iso" "$UBUNTU_ISO" >/dev/null 2>&1
ok "ISO entpackt"

# Schreibrechte setzen (7z entpackt manchmal read-only)
chmod -R u+w "$BUILD_DIR/iso"

# ── Autoinstall-Konfiguration einbetten ──────────────────────────────────────
info "Bette Autoinstall-Konfiguration ein..."

# Erstelle Verzeichnis für Autoinstall-Dateien
mkdir -p "$BUILD_DIR/iso/autoinstall"
cp "$AUTOINSTALL_DIR/user-data" "$BUILD_DIR/iso/autoinstall/"
cp "$AUTOINSTALL_DIR/meta-data" "$BUILD_DIR/iso/autoinstall/"

# Hardwaretest-ZIP einbetten (wird von late-commands entpackt)
mkdir -p "$BUILD_DIR/iso/hardwaretest"
cp "$HARDWARETEST_ZIP" "$BUILD_DIR/iso/hardwaretest/"
ok "Autoinstall + Hardwaretest-ZIP eingebettet"

# ── GRUB-Konfiguration anpassen (autoinstall ohne Rückfrage) ─────────────────
info "Passe GRUB-Konfiguration an..."

GRUB_CFG="$BUILD_DIR/iso/boot/grub/grub.cfg"
if [[ -f "$GRUB_CFG" ]]; then
    # Füge autoinstall-Parameter zum ersten Menüeintrag hinzu
    # Ersetze den Standard-linux-Befehl um autoinstall hinzuzufügen
    sed -i 's|linux\s\+/casper/vmlinuz ---|linux /casper/vmlinuz autoinstall ds=nocloud;s=/cdrom/autoinstall/ ---|' "$GRUB_CFG"
    # Timeout auf 5 Sekunden setzen (statt 30)
    sed -i 's/set timeout=.*/set timeout=5/' "$GRUB_CFG"
    ok "GRUB konfiguriert (autoinstall + 5s Timeout)"
else
    warn "grub.cfg nicht gefunden – möglicherweise andere ISO-Struktur"
fi

# ── Neues ISO bauen ──────────────────────────────────────────────────────────
info "Erstelle neues ISO-Image (das dauert ~2 Minuten)..."

# MBR aus dem Original-ISO extrahieren (für BIOS-Boot)
dd if="$UBUNTU_ISO" bs=1 count=446 of="$BUILD_DIR/mbr.bin" 2>/dev/null

# EFI-Partition extrahieren
EFI_IMG=""
if [[ -f "$BUILD_DIR/iso/boot/grub/efi.img" ]]; then
    EFI_IMG="$BUILD_DIR/iso/boot/grub/efi.img"
elif [[ -f "$BUILD_DIR/iso/EFI/BOOT/efi.img" ]]; then
    EFI_IMG="$BUILD_DIR/iso/EFI/BOOT/efi.img"
fi

# ISO mit xorriso erstellen (BIOS + UEFI bootfähig)
xorriso -as mkisofs \
    -r -V "HARDWARETEST-AUTOINSTALL" \
    --modification-date="$(date +%Y%m%d%H%M%S00)" \
    -o "$OUTPUT_ISO" \
    --grub2-mbr "$BUILD_DIR/mbr.bin" \
    --protective-msdos-label \
    -partition_cyl_align off \
    -partition_offset 16 \
    --mbr-force-bootable \
    -append_partition 2 28732ac11ff8d211ba4b00a0c93ec93b \
    ${EFI_IMG:+"$EFI_IMG"} \
    -appended_part_as_gpt \
    -iso_mbr_part_type a2a0d0ebe5b9334487c068b6b72699c7 \
    -c '/boot.catalog' \
    -b '/boot/grub/i386-pc/eltorito.img' \
    -no-emul-boot \
    -boot-load-size 4 \
    -boot-info-table \
    --grub2-boot-info \
    -eltorito-alt-boot \
    -e '--interval:appended_partition_2:::' \
    -no-emul-boot \
    "$BUILD_DIR/iso" 2>/dev/null

ok "ISO erstellt: $OUTPUT_ISO"

# ── Aufräumen ────────────────────────────────────────────────────────────────
info "Räume Build-Verzeichnis auf..."
rm -rf "$BUILD_DIR"

# ── Zusammenfassung ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══ Fertig ═══${NC}"
ISO_SIZE=$(du -h "$OUTPUT_ISO" | cut -f1)
echo -e "  ISO:   ${GREEN}$OUTPUT_ISO${NC}"
echo -e "  Größe: ${GREEN}$ISO_SIZE${NC}"
echo ""
echo -e "${CYAN}Verwendung mit iLO:${NC}"
echo "  1. ISO auf HTTP/NFS-Server kopieren"
echo "  2. iLO → Virtual Media → ISO URL eingeben"
echo "  3. Server booten → Installation läuft vollautomatisch"
echo ""
echo -e "${CYAN}Oder direkt testen (KVM/QEMU):${NC}"
echo "  qemu-system-x86_64 -m 4096 -cdrom $OUTPUT_ISO -boot d"
echo ""
