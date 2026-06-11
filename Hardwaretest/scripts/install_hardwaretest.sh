#!/usr/bin/env bash
# =============================================================================
#  Hardwaretest – Installationsscript für Ubuntu/Debian-Derivate
#
#  Prüft alle Abhängigkeiten, zeigt fehlende Pakete an, installiert auf
#  Wunsch und legt einen Desktop-Starter (.desktop) mit Icon an.
#
#  Verwendung:
#    sudo bash scripts/install_hardwaretest.sh          # Interaktiv
#    sudo bash scripts/install_hardwaretest.sh --yes    # Alles ohne Rückfrage
# =============================================================================
# KEIN "set -e" – das Script handhabt Fehler selbst, da set -e
# bei harmlosen Ausdruecken wie ((count++)) mit Wert 0 abbricht.
set -uo pipefail

# ── Farben ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Argumente ─────────────────────────────────────────────────────────────────
AUTO_YES=false
for arg in "$@"; do
    case "$arg" in
        --yes|-y) AUTO_YES=true ;;
        --help|-h)
            echo "Verwendung: sudo $0 [--yes|-y]"
            echo "  --yes, -y   Alle Aktionen ohne Rückfrage ausführen"
            exit 0
            ;;
    esac
done

# ── Helfer ────────────────────────────────────────────────────────────────────
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
header(){ echo -e "\n${BOLD}═══ $* ═══${NC}"; }

confirm() {
    if $AUTO_YES; then return 0; fi
    local msg="${1:-Fortfahren?}"
    echo -en "${YELLOW}${msg} [J/n]${NC} "
    read -r answer
    case "${answer,,}" in
        j|ja|y|yes|"") return 0 ;;
        *) return 1 ;;
    esac
}

# ── Root-Check ────────────────────────────────────────────────────────────────
if [[ $(id -u) -ne 0 ]]; then
    fail "Dieses Script muss mit sudo ausgeführt werden."
    echo "  Beispiel: sudo bash $0"
    exit 1
fi

# ── Ubuntu/Debian-Check ──────────────────────────────────────────────────────
if ! command -v apt-get &>/dev/null; then
    fail "Dieses Script funktioniert nur auf Ubuntu/Debian-Derivaten (apt-get nicht gefunden)."
    exit 1
fi

# ── Pfade ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
TARGET_HOME=$(eval echo "~${TARGET_USER}")
ICON_SOURCE="$REPO_DIR/assets/hardwaretest.svg"

header "Hardwaretest Installer"
info "Projektverzeichnis: $REPO_DIR"
info "Zielbenutzer:       $TARGET_USER ($TARGET_HOME)"

# ── Helfer: als Zielbenutzer ausführen ────────────────────────────────────────
run_as_user() {
    sudo -H -u "$TARGET_USER" bash -c "$1"
}

# ── Temporäres Verzeichnis ────────────────────────────────────────────────────
TMP_DIR=""
cleanup_tmp() {
    [[ -n "${TMP_DIR:-}" && -d "${TMP_DIR:-}" ]] && rm -rf "$TMP_DIR"
}
trap cleanup_tmp EXIT

ensure_tmp() {
    if [[ -z "$TMP_DIR" ]]; then
        TMP_DIR=$(mktemp -d)
    fi
}

# =============================================================================
#  1. APT-Pakete prüfen
# =============================================================================
header "1/7 – APT-Abhängigkeiten prüfen"

# Pflichtpakete (ohne diese funktioniert die App nicht)
# PolicyKit-Paketname erkennen (Ubuntu 22.04+ → polkitd, älter → policykit-1)
if apt-cache show polkitd &>/dev/null; then
    _POLKIT_PKG="polkitd"
elif apt-cache show policykit-1 &>/dev/null; then
    _POLKIT_PKG="policykit-1"
else
    _POLKIT_PKG="pkexec"  # Minimaler Fallback
fi

REQUIRED_PKG_NAMES=(
    python3
    python3-venv
    python3-pip
    python3-dev
    build-essential
    curl
    wget
    tar
    unzip
    git
    "$_POLKIT_PKG"
    stress-ng
    fio
    jq
    libxcb-cursor0
    libxcb-icccm4
    libxcb-keysyms1
    libxcb-render-util0
    libxcb-shape0
    libxcb-xfixes0
    libxkbcommon-x11-0
)
REQUIRED_PKG_DESC=(
    "Python 3 Interpreter"
    "Python venv-Modul"
    "Python pip"
    "Python Entwicklungsheader"
    "C-Compiler (für Erweiterungen)"
    "Download-Tool (curl)"
    "Download-Tool (wget)"
    "Archiv-Tool"
    "ZIP-Entpacker"
    "Versionsverwaltung"
    "PolicyKit (pkexec für Root-Zugriffe)"
    "CPU/RAM-Stresstest"
    "Disk I/O Benchmark"
    "JSON-Verarbeitung"
    "Qt6 XCB-Abhängigkeit"
    "Qt6 XCB-Abhängigkeit"
    "Qt6 XCB-Abhängigkeit"
    "Qt6 XCB-Abhängigkeit"
    "Qt6 XCB-Abhängigkeit"
    "Qt6 XCB-Abhängigkeit"
    "Qt6 XCB-Abhängigkeit"
)

# Optionale Pakete (nützlich, aber nicht zwingend)
OPTIONAL_PKG_NAMES=(
    lshw
    pciutils
    smartmontools
    btop
    terminology
    gnome-text-editor
    dmidecode
    nvme-cli
    hdparm
    lm-sensors
    edac-utils
    memtest86+
)
OPTIONAL_PKG_DESC=(
    "Hardware-Informationen (lshw)"
    "PCI-Geräte auslesen (lspci)"
    "SMART Disk-Diagnose (smartctl)"
    "Interaktiver Systemmonitor"
    "Terminal-Emulator (Fallback)"
    "Text-Editor"
    "BIOS/DMI-Informationen"
    "NVMe-Verwaltung"
    "HDD-Parameter auslesen"
    "Hardware-Sensoren (sensors)"
    "ECC-Speicherfehler anzeigen"
    "RAM-Test (Reboot nötig)"
)

# Prüfe welche Pakete fehlen
missing_required=()
missing_optional=()
installed_count=0
total_count=$(( ${#REQUIRED_PKG_NAMES[@]} + ${#OPTIONAL_PKG_NAMES[@]} ))

for pkg in "${REQUIRED_PKG_NAMES[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        installed_count=$((installed_count + 1))
    else
        missing_required+=("$pkg")
    fi
done

for pkg in "${OPTIONAL_PKG_NAMES[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        installed_count=$((installed_count + 1))
    else
        missing_optional+=("$pkg")
    fi
done

ok "$installed_count / $total_count Pakete bereits installiert"

# ── Fehlende Pflichtpakete ────────────────────────────────────────────────────
if [[ ${#missing_required[@]} -gt 0 ]]; then
    echo ""
    fail "${#missing_required[@]} Pflichtpaket(e) fehlen:"
    for i in "${!REQUIRED_PKG_NAMES[@]}"; do
        pkg="${REQUIRED_PKG_NAMES[$i]}"
        for m in "${missing_required[@]}"; do
            if [[ "$m" == "$pkg" ]]; then
                echo -e "  ${RED}✗${NC} $pkg – ${REQUIRED_PKG_DESC[$i]}"
                break
            fi
        done
    done
    echo ""
    if confirm "Pflichtpakete jetzt installieren?"; then
        info "Aktualisiere Paketlisten..."
        apt-get update -qq
        info "Installiere ${#missing_required[@]} Pflichtpaket(e)..."
        _install_fail=0
        for pkg in "${missing_required[@]}"; do
            if DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" &>/dev/null; then
                ok "  $pkg installiert"
            else
                # Versuche alternative Paketnamen (z.B. libxcb-* Varianten)
                warn "  $pkg nicht verfügbar – übersprungen"
                _install_fail=$((_install_fail + 1))
            fi
        done
        if [[ $_install_fail -gt 0 ]]; then
            warn "$_install_fail Paket(e) konnten nicht installiert werden."
            warn "Die App funktioniert möglicherweise eingeschränkt."
        else
            ok "Alle Pflichtpakete installiert."
        fi
    else
        fail "Pflichtpakete werden benötigt. Abbruch."
        exit 1
    fi
else
    ok "Alle Pflichtpakete sind installiert."
fi

# ── Fehlende optionale Pakete ─────────────────────────────────────────────────
if [[ ${#missing_optional[@]} -gt 0 ]]; then
    echo ""
    warn "${#missing_optional[@]} optionale(s) Paket(e) nicht installiert:"
    for i in "${!OPTIONAL_PKG_NAMES[@]}"; do
        pkg="${OPTIONAL_PKG_NAMES[$i]}"
        for m in "${missing_optional[@]}"; do
            if [[ "$m" == "$pkg" ]]; then
                echo -e "  ${YELLOW}○${NC} $pkg – ${OPTIONAL_PKG_DESC[$i]}"
                break
            fi
        done
    done
    echo ""
    if confirm "Optionale Pakete jetzt installieren? (empfohlen für HPE ProLiant)"; then
        # Paketlisten ggf. nochmal aktualisieren, falls oben übersprungen
        if [[ ${#missing_required[@]} -eq 0 ]]; then
            apt-get update -qq
        fi
        # Einzeln installieren – so blockiert ein fehlendes Paket nicht die anderen
        for pkg in "${missing_optional[@]}"; do
            if DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" &>/dev/null; then
                ok "  $pkg installiert"
            else
                warn "  $pkg nicht verfügbar – übersprungen"
            fi
        done
    else
        info "Optionale Pakete übersprungen."
    fi
else
    ok "Alle optionalen Pakete sind installiert."
fi

# ── PolicyKit-Authentifizierungsagent ─────────────────────────────────────────
# polkitd/policykit-1 (installiert oben) ist nur das Backend.  Damit pkexec
# einen grafischen Passwort-Dialog zeigen kann, braucht man zusätzlich einen
# "Authentication Agent", der zur Desktop-Umgebung passt.  Ohne diesen Agent
# hängt pkexec endlos → Swap-Deaktivierung friert das System ein.

_polkit_agent_running() {
    # 1. Desktop-Umgebungen mit EINGEBAUTEM Polkit-Agent erkennen.
    #    GNOME Shell, Cinnamon und KDE Plasma bringen ihren eigenen Agent mit –
    #    dieser laeuft im Hauptprozess und taucht NICHT als separater Prozess auf.
    #    XFCE 4.14+ (Ubuntu 22.04+) integriert den Agent in xfce4-session.
    local args_list
    args_list=$(ps -eo args 2>/dev/null) || true
    local args_lower="${args_list,,}"

    # Desktops mit integriertem Polkit-Agent
    for desktop_proc in \
        "cinnamon --" \
        "gnome-shell" \
        "plasmashell" \
        "xfce4-session" \
        "budgie-wm" \
        "ukui-session" \
        "deepin-session"; do
        [[ "$args_lower" == *"$desktop_proc"* ]] && return 0
    done

    # 2. Separate Polkit-Agent-Prozesse erkennen.
    #    WICHTIG: ps -eo args (volle Kommandozeile) statt ps -eo comm verwenden,
    #    da comm auf 15 Zeichen gekuerzt wird (TASK_COMM_LEN).
    local agent_patterns=(
        polkit-gnome-authentication-agent
        polkit-kde-authentication-agent
        polkit-mate-authentication-agent
        xfce-polkit
        lxpolkit
        lxqt-policykit-agent
    )
    for pattern in "${agent_patterns[@]}"; do
        [[ "$args_lower" == *"$pattern"* ]] && return 0
    done

    # 3. Autostart-Dateien pruefen (systemweit + benutzerspezifisch).
    #    Wenn ein Agent konfiguriert ist, wird er beim naechsten Login gestartet.
    for d in /etc/xdg/autostart "$TARGET_HOME/.config/autostart"; do
        for f in "$d"/*polkit*.desktop "$d"/*policykit*.desktop; do
            [[ -f "$f" ]] && return 0
        done
    done

    # 4. Letzter Fallback: polkitd laeuft UND ein Agent-Binary existiert.
    #    Wenn polkitd aktiv ist und ein bekanntes Agent-Binary auf der Platte
    #    liegt, ist die Wahrscheinlichkeit hoch, dass der Agent funktioniert
    #    (z.B. wenn er von der Session gestartet wird, ohne eigenen Prozess).
    if [[ "$args_lower" == *"polkitd"* ]]; then
        for agent_bin in \
            /usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1 \
            /usr/lib/x86_64-linux-gnu/libexec/polkit-gnome-authentication-agent-1 \
            /usr/libexec/polkit-gnome-authentication-agent-1 \
            /usr/lib/xfce-polkit/xfce-polkit \
            /usr/libexec/polkit-mate-authentication-agent-1 \
            /usr/bin/lxpolkit \
            /usr/bin/lxqt-policykit-agent; do
            [[ -x "$agent_bin" ]] && return 0
        done
    fi

    return 1
}

_detect_desktop() {
    # Versuche die Desktop-Umgebung zu erkennen
    local de="${XDG_CURRENT_DESKTOP:-}"
    [[ -z "$de" ]] && de="${DESKTOP_SESSION:-}"
    echo "${de,,}"
}

_install_polkit_agent() {
    local de
    de=$(_detect_desktop)

    local agent_pkg=""
    local agent_exec=""
    local agent_name=""

    case "$de" in
        *xfce*)
            # xfce4-session liefert xfce-polkit mit seit Ubuntu 22.04+
            if apt-cache show xfce4-session &>/dev/null; then
                agent_pkg="xfce4-session"
                agent_name="xfce-polkit"
            fi
            ;;
        *mate*)
            agent_pkg="mate-polkit"
            agent_name="polkit-mate-authentication-agent"
            ;;
        *lxqt*)
            agent_pkg="lxqt-policykit"
            agent_name="lxqt-policykit-agent"
            ;;
        *lxde*)
            agent_pkg="lxpolkit"
            agent_name="lxpolkit"
            ;;
    esac

    # Fallback: policykit-1-gnome funktioniert auf fast allen DEs
    if [[ -z "$agent_pkg" ]] || ! apt-cache show "$agent_pkg" &>/dev/null; then
        agent_pkg="policykit-1-gnome"
        agent_name="polkit-gnome-authentication-agent"
        agent_exec="/usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1"
    fi

    info "Desktop-Umgebung erkannt: ${de:-unbekannt}"
    info "Benoetigter Polkit-Agent: $agent_pkg ($agent_name)"

    if dpkg -s "$agent_pkg" &>/dev/null; then
        ok "$agent_pkg ist bereits installiert."
    else
        if confirm "$agent_pkg installieren? (noetig fuer grafische Passwort-Abfrage)"; then
            apt-get update -qq 2>/dev/null || true
            if DEBIAN_FRONTEND=noninteractive apt-get install -y "$agent_pkg" &>/dev/null; then
                ok "$agent_pkg installiert."
            else
                warn "$agent_pkg konnte nicht installiert werden."
                warn "Die App fragt ersatzweise im eigenen Dialog nach dem Passwort."
                return
            fi
        else
            info "Polkit-Agent uebersprungen – die App nutzt ihren eigenen Passwort-Dialog."
            return
        fi
    fi

    # ── Autostart sicherstellen ──────────────────────────────────────────────
    # Manche Desktop-Umgebungen starten den Agent automatisch über
    # /etc/xdg/autostart.  Falls nicht, legen wir einen Eintrag an.
    local autostart_dir="$TARGET_HOME/.config/autostart"
    local autostart_file="$autostart_dir/polkit-agent.desktop"

    # Prüfe ob bereits ein systemweiter Autostart existiert
    local has_autostart=false
    for f in /etc/xdg/autostart/*polkit* /etc/xdg/autostart/*policykit*; do
        [[ -f "$f" ]] && { has_autostart=true; break; }
    done

    if $has_autostart; then
        ok "Polkit-Agent Autostart bereits in /etc/xdg/autostart/ vorhanden."
    else
        # Agent-Executable finden
        if [[ -z "${agent_exec:-}" ]]; then
            for candidate in \
                /usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1 \
                /usr/lib/x86_64-linux-gnu/libexec/polkit-gnome-authentication-agent-1 \
                /usr/libexec/polkit-gnome-authentication-agent-1 \
                /usr/lib/xfce-polkit/xfce-polkit \
                /usr/lib/mate-polkit/polkit-mate-authentication-agent-1 \
                /usr/bin/lxpolkit \
                /usr/bin/lxqt-policykit-agent; do
                if [[ -x "$candidate" ]]; then
                    agent_exec="$candidate"
                    break
                fi
            done
        fi

        if [[ -n "${agent_exec:-}" && -x "${agent_exec:-}" ]]; then
            run_as_user "mkdir -p '$autostart_dir'"
            cat <<AUTOSTART >"$autostart_file"
[Desktop Entry]
Type=Application
Name=PolicyKit Authentication Agent
Comment=Installiert von Hardwaretest-Installer
Exec=$agent_exec
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
AUTOSTART
            chown "$TARGET_USER":"$TARGET_USER" "$autostart_file"
            chmod 644 "$autostart_file"
            ok "Autostart angelegt: $autostart_file"

            # Agent gleich jetzt starten, falls nicht bereits laufend
            if ! _polkit_agent_running; then
                info "Starte Polkit-Agent fuer aktuelle Sitzung..."
                sudo -u "$TARGET_USER" nohup "$agent_exec" >/dev/null 2>&1 &
                sleep 1
                if _polkit_agent_running; then
                    ok "Polkit-Agent laeuft."
                else
                    warn "Polkit-Agent konnte nicht gestartet werden."
                    warn "Er wird nach dem naechsten Login automatisch starten."
                fi
            fi
        else
            warn "Polkit-Agent installiert, aber Executable nicht gefunden."
            warn "Bitte nach dem naechsten Login pruefen."
        fi
    fi
}

if _polkit_agent_running; then
    ok "PolicyKit-Authentifizierungsagent laeuft bereits."
else
    warn "Kein PolicyKit-Authentifizierungsagent erkannt."
    info "Ohne Agent kann pkexec keinen Passwort-Dialog anzeigen"
    info "(haeufig auf Ubuntu Server + XFCE4)."
    _install_polkit_agent
fi

# =============================================================================
#  2. Prime95 (mprime) – im Projektverzeichnis gebündelt
# =============================================================================
header "2/7 – Prime95 (mprime)"

PRIME_URL="https://www.mersenne.org/ftp_root/gimps/p95v308b17.linux64.tar.gz"
PRIME_DIR="$REPO_DIR/vendor/prime95"
PRIME_BIN="$PRIME_DIR/mprime"

# Auch Legacy-Pfad prüfen
LEGACY_PRIME_DIR="$TARGET_HOME/Prime95"
LEGACY_PRIME_BIN="$LEGACY_PRIME_DIR/mprime"

if [[ -x "$PRIME_BIN" ]]; then
    ok "Prime95 im Projekt vorhanden: $PRIME_BIN"
    version_info=$("$PRIME_BIN" -v 2>&1 | head -1) || true
    [[ -n "$version_info" ]] && info "  Version: $version_info"
elif [[ -x "$LEGACY_PRIME_BIN" ]]; then
    info "Prime95 in Legacy-Pfad gefunden: $LEGACY_PRIME_BIN"
    if confirm "Nach $PRIME_DIR verschieben (empfohlen)?"; then
        mkdir -p "$PRIME_DIR"
        cp -a "$LEGACY_PRIME_DIR"/* "$PRIME_DIR/"
        chown -R "$TARGET_USER":"$TARGET_USER" "$PRIME_DIR"
        chmod +x "$PRIME_BIN"
        ok "Prime95 in Projektverzeichnis verschoben."
        info "Legacy-Ordner $LEGACY_PRIME_DIR kann manuell gelöscht werden."
    else
        info "Prime95 wird weiterhin aus $LEGACY_PRIME_BIN geladen."
    fi
elif command -v mprime &>/dev/null; then
    ok "mprime im PATH gefunden: $(command -v mprime)"
else
    warn "Prime95 (mprime) nicht gefunden."
    if confirm "Prime95 jetzt herunterladen und ins Projekt integrieren?"; then
        ensure_tmp
        cd "$TMP_DIR"
        info "Lade Prime95 herunter..."
        wget -q --show-progress "$PRIME_URL"
        mkdir -p "$PRIME_DIR"
        info "Entpacke nach $PRIME_DIR..."
        tar -xzf "${PRIME_URL##*/}" -C "$PRIME_DIR" --strip-components=1 2>/dev/null || \
            tar -xzf "${PRIME_URL##*/}" -C "$PRIME_DIR"
        chown -R "$TARGET_USER":"$TARGET_USER" "$PRIME_DIR"
        chmod +x "$PRIME_BIN"
        ok "Prime95 ins Projekt integriert."
    else
        warn "Prime95 übersprungen – Blend-Tests stehen nicht zur Verfügung."
    fi
fi

# Symlink in /usr/local/bin (falls Prime95 vorhanden)
if [[ -x "$PRIME_BIN" ]]; then
    ln -sf "$PRIME_BIN" /usr/local/bin/mprime 2>/dev/null || true
fi

# =============================================================================
#  3. Fastfetch & JSON-Reader (jless)
# =============================================================================
header "3/7 – Fastfetch & JSON-Reader"

# ── Fastfetch ─────────────────────────────────────────────────────────────────
# Fastfetch wird im Info-Panel für die Systemübersicht genutzt.
# Es ist nicht in den Ubuntu/Debian-Standard-Repos enthalten, daher wird
# das .deb-Paket direkt von GitHub heruntergeladen.

if command -v fastfetch &>/dev/null; then
    ok "Fastfetch bereits installiert: $(command -v fastfetch)"
else
    warn "Fastfetch nicht gefunden."
    if confirm "Fastfetch jetzt installieren? (Systeminformationen im Info-Panel)"; then
        ensure_tmp
        FASTFETCH_DL_URL=""
        # GitHub API: neuestes Release von fastfetch-cli/fastfetch
        if command -v curl &>/dev/null && command -v jq &>/dev/null; then
            info "Ermittle aktuelle Fastfetch-Version von GitHub..."
            FASTFETCH_DL_URL=$(curl -fsSL \
                "https://api.github.com/repos/fastfetch-cli/fastfetch/releases/latest" 2>/dev/null \
                | jq -r '.assets[] | select(.name | test("linux-amd64\\.deb$")) | .browser_download_url' 2>/dev/null \
                | head -1) || true
        fi

        if [[ -n "$FASTFETCH_DL_URL" ]]; then
            info "Lade Fastfetch herunter..."
            cd "$TMP_DIR"
            if curl -fsSL -o fastfetch.deb "$FASTFETCH_DL_URL" 2>/dev/null; then
                if dpkg -i fastfetch.deb &>/dev/null; then
                    ok "Fastfetch installiert (via GitHub .deb)."
                else
                    # Fehlende Abhängigkeiten nachziehen
                    apt-get install -f -y &>/dev/null || true
                    if command -v fastfetch &>/dev/null; then
                        ok "Fastfetch installiert (via GitHub .deb, Abhängigkeiten nachinstalliert)."
                    else
                        warn "Fastfetch .deb konnte nicht installiert werden."
                        info "Manuell: https://github.com/fastfetch-cli/fastfetch/releases"
                    fi
                fi
            else
                warn "Download von Fastfetch fehlgeschlagen."
                info "Manuell: https://github.com/fastfetch-cli/fastfetch/releases"
            fi
            cd "$REPO_DIR"
        else
            warn "Konnte aktuelle Fastfetch-Version nicht ermitteln (GitHub API)."
            info "Manuell herunterladen: https://github.com/fastfetch-cli/fastfetch/releases"
            info "  Dann installieren:   sudo dpkg -i fastfetch-linux-amd64.deb"
        fi
    else
        info "Fastfetch übersprungen – Info-Panel zeigt Hinweis statt Systeminformationen."
    fi
fi

# ── JSON-Reader (jless) ──────────────────────────────────────────────────────
# jless ist der bevorzugte interaktive JSON-Viewer für Systemreports.
# Da jless nicht in den APT-Repos verfügbar ist, wird das Binary direkt
# von GitHub heruntergeladen.

JLESS_BIN="/usr/local/bin/jless"

if command -v jless &>/dev/null; then
    ok "JSON-Reader (jless) bereits installiert: $(command -v jless)"
else
    # Prüfe ob ein alternativer JSON-Viewer vorhanden ist
    _alt_viewer=""
    for _candidate in fx gnome-text-editor gedit xed kate; do
        if command -v "$_candidate" &>/dev/null; then
            _alt_viewer="$_candidate"
            break
        fi
    done
    if [[ -n "$_alt_viewer" ]]; then
        info "Alternativer Viewer verfügbar: $_alt_viewer"
    fi

    if confirm "jless jetzt installieren? (empfohlener JSON-Reader für Systemreports)"; then
        ensure_tmp
        JLESS_DL_URL=""
        # GitHub API: neuestes Release von PaulJuliusMartinez/jless
        if command -v curl &>/dev/null && command -v jq &>/dev/null; then
            info "Ermittle aktuelle jless-Version von GitHub..."
            JLESS_DL_URL=$(curl -fsSL \
                "https://api.github.com/repos/PaulJuliusMartinez/jless/releases/latest" 2>/dev/null \
                | jq -r '.assets[] | select(.name | test("x86_64.*linux")) | .browser_download_url' 2>/dev/null \
                | head -1) || true
        fi

        if [[ -n "$JLESS_DL_URL" ]]; then
            info "Lade jless herunter..."
            cd "$TMP_DIR"
            if curl -fsSL -o jless-release.zip "$JLESS_DL_URL" 2>/dev/null; then
                mkdir -p jless-extract
                unzip -o jless-release.zip -d jless-extract &>/dev/null 2>&1 || \
                    tar -xzf jless-release.zip -C jless-extract 2>/dev/null || true
                # Binary finden (kann direkt oder in Unterordner liegen)
                _JLESS_FOUND=$(find "$TMP_DIR/jless-extract" -name "jless" -type f 2>/dev/null | head -1)
                if [[ -z "$_JLESS_FOUND" ]]; then
                    # Manche Releases packen das Binary direkt ohne Ordner
                    _JLESS_FOUND=$(find "$TMP_DIR" -name "jless" -type f ! -path "*/jless-extract/*" 2>/dev/null | head -1)
                fi
                if [[ -n "$_JLESS_FOUND" && -f "$_JLESS_FOUND" ]]; then
                    cp "$_JLESS_FOUND" "$JLESS_BIN"
                    chmod +x "$JLESS_BIN"
                    ok "jless installiert: $JLESS_BIN"
                else
                    warn "jless-Binary nicht im Archiv gefunden."
                    info "Manuell installieren: https://github.com/PaulJuliusMartinez/jless/releases"
                fi
            else
                warn "Download von jless fehlgeschlagen."
                info "Manuell: https://github.com/PaulJuliusMartinez/jless/releases"
            fi
            cd "$REPO_DIR"
        else
            warn "Konnte aktuelle jless-Version nicht ermitteln (GitHub API)."
            info "Manuell installieren: https://github.com/PaulJuliusMartinez/jless/releases"
            info "  Oder via Cargo:     cargo install jless"
        fi
    else
        if [[ -n "${_alt_viewer:-}" ]]; then
            info "jless übersprungen – '$_alt_viewer' wird als Fallback verwendet."
        else
            info "jless übersprungen – JSON-Reports können mit 'jq' angezeigt werden."
        fi
    fi
fi

# =============================================================================
#  4. Python-Umgebung und Projektabhängigkeiten
# =============================================================================
header "4/7 – Python-Umgebung"

# PATH sicherstellen
ensure_local_bin_on_path() {
    local snippet='export PATH="$HOME/.local/bin:$PATH"'
    local files=("$TARGET_HOME/.profile" "$TARGET_HOME/.bashrc")
    for file in "${files[@]}"; do
        if [[ -f "$file" ]] && grep -Fq "$snippet" "$file"; then
            continue
        fi
        [[ ! -f "$file" ]] && { touch "$file"; chown "$TARGET_USER":"$TARGET_USER" "$file"; }
        printf '\n# Added by hardwaretest installer\n%s\n' "$snippet" >>"$file"
        chown "$TARGET_USER":"$TARGET_USER" "$file"
    done
}

# Sicherstellen, dass der Zielbenutzer Schreibrechte im Projektverzeichnis hat
# (nötig wenn das Projekt z.B. unter /opt/ liegt und root gehört)
if [[ ! -w "$REPO_DIR" ]] || ! sudo -u "$TARGET_USER" test -w "$REPO_DIR" 2>/dev/null; then
    info "Setze Eigentümer von $REPO_DIR auf $TARGET_USER..."
    chown -R "$TARGET_USER":"$TARGET_USER" "$REPO_DIR"
    ok "Verzeichnisrechte angepasst."
fi

# venv erstellen/aktualisieren
if [[ ! -d "$REPO_DIR/.venv" ]]; then
    info "Erstelle virtuelle Python-Umgebung..."
    if ! run_as_user "python3 -m venv '$REPO_DIR/.venv'"; then
        # Fallback: als root erstellen und Eigentümer setzen
        warn "venv-Erstellung als $TARGET_USER fehlgeschlagen – versuche als root..."
        python3 -m venv "$REPO_DIR/.venv"
        chown -R "$TARGET_USER":"$TARGET_USER" "$REPO_DIR/.venv"
    fi
    # Prüfen ob venv tatsächlich existiert
    if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
        fail "Virtuelle Umgebung konnte nicht erstellt werden!"
        fail "Bitte prüfen: python3 -m venv '$REPO_DIR/.venv'"
        exit 1
    fi
    ok "venv erstellt."
else
    ok "venv bereits vorhanden."
    # Eigentümer sicherstellen
    if ! sudo -u "$TARGET_USER" test -w "$REPO_DIR/.venv" 2>/dev/null; then
        info "Korrigiere Eigentümer der bestehenden venv..."
        chown -R "$TARGET_USER":"$TARGET_USER" "$REPO_DIR/.venv"
    fi
fi

info "Aktualisiere pip + wheel..."
if ! run_as_user "'$REPO_DIR/.venv/bin/python' -m pip install --upgrade pip wheel -q"; then
    warn "pip-Upgrade als $TARGET_USER fehlgeschlagen – versuche als root..."
    HOME="$TARGET_HOME" "$REPO_DIR/.venv/bin/python" -m pip install --upgrade pip wheel -q
    chown -R "$TARGET_USER":"$TARGET_USER" "$REPO_DIR/.venv"
fi

info "Installiere Projektabhängigkeiten (PySide6, psutil, ...)..."
if ! run_as_user "cd '$REPO_DIR' && '$REPO_DIR/.venv/bin/pip' install -e '$REPO_DIR' -q"; then
    warn "Paketinstallation als $TARGET_USER fehlgeschlagen – versuche als root..."
    HOME="$TARGET_HOME" "$REPO_DIR/.venv/bin/pip" install -e "$REPO_DIR" -q
    chown -R "$TARGET_USER":"$TARGET_USER" "$REPO_DIR/.venv"
fi
ok "Python-Abhängigkeiten installiert."

ensure_local_bin_on_path

# =============================================================================
#  4. CLI-Launcher
# =============================================================================
header "5/7 – CLI-Starter"

LAUNCHER_PATH="/usr/local/bin/hardwaretest"
cat <<LAUNCHER >"$LAUNCHER_PATH"
#!/usr/bin/env bash
# Hardwaretest GUI Launcher – generiert von install_hardwaretest.sh
set -euo pipefail
REPO_DIR="$REPO_DIR"
VENV="\$REPO_DIR/.venv/bin/python"
if [[ ! -x "\$VENV" ]]; then
    echo "Fehler: Virtuelle Umgebung fehlt (\$VENV)." >&2
    echo "Bitte erneut ausführen: sudo bash \$REPO_DIR/scripts/install_hardwaretest.sh" >&2
    exit 1
fi
exec "\$VENV" -m hardwaretest "\$@"
LAUNCHER
chmod +x "$LAUNCHER_PATH"
ok "CLI-Starter: $LAUNCHER_PATH"

# Legacy-Name beibehalten
ln -sf "$LAUNCHER_PATH" /usr/local/bin/hardwaretest-launcher 2>/dev/null || true

# =============================================================================
#  5. Desktop-Starter + Icon
# =============================================================================
header "6/7 – Desktop-Starter & Icon"

# ── Icon installieren ─────────────────────────────────────────────────────────
ICON_DEST_DIR="/usr/share/icons/hicolor/scalable/apps"
ICON_DEST="$ICON_DEST_DIR/hardwaretest.svg"
PIXMAP_DEST="/usr/share/pixmaps/hardwaretest.svg"

if [[ -f "$ICON_SOURCE" ]]; then
    mkdir -p "$ICON_DEST_DIR"
    cp "$ICON_SOURCE" "$ICON_DEST"
    cp "$ICON_SOURCE" "$PIXMAP_DEST"
    chmod 644 "$ICON_DEST" "$PIXMAP_DEST"
    ok "Icon installiert: $ICON_DEST"
else
    warn "Icon-Datei nicht gefunden: $ICON_SOURCE"
    warn "Verwende Fallback-Icon 'utilities-system-monitor'."
fi

# ── Icon-Name bestimmen ──────────────────────────────────────────────────────
if [[ -f "$ICON_DEST" ]]; then
    ICON_NAME="hardwaretest"
else
    ICON_NAME="utilities-system-monitor"
fi

# ── .desktop-Datei (systemweit) ──────────────────────────────────────────────
DESKTOP_FILE="/usr/share/applications/hardwaretest.desktop"
cat <<DESKTOP >"$DESKTOP_FILE"
[Desktop Entry]
Version=1.5
Type=Application
Name=Hardwaretest
Name[de]=Hardwaretest
GenericName=Hardware Stress Test
GenericName[de]=Hardware-Stresstest
Comment=Stress tests for CPU, RAM and Disk (stress-ng, Prime95, fio)
Comment[de]=Stresstests für CPU, RAM und Festplatten (stress-ng, Prime95, fio)
Exec=$LAUNCHER_PATH
Icon=$ICON_NAME
Terminal=false
Categories=System;Utility;Monitor;
Keywords=stress;test;cpu;ram;disk;fio;prime95;hardware;benchmark;
StartupNotify=true
StartupWMClass=hardwaretest
DESKTOP
chmod 644 "$DESKTOP_FILE"
ok "Desktop-Datei: $DESKTOP_FILE"

# ── Desktop-Verknüpfung auf dem Benutzer-Desktop ────────────────────────────
install_desktop_shortcut() {
    # Finde den Desktop-Ordner des Benutzers
    local desktop_dir=""

    # 1. xdg-user-dir (GNOME, KDE, XFCE, Cinnamon, MATE, Budgie, ...)
    if command -v xdg-user-dir &>/dev/null; then
        desktop_dir=$(sudo -u "$TARGET_USER" xdg-user-dir DESKTOP 2>/dev/null || true)
    fi

    # 2. Fallback: typische lokalisierte Desktop-Ordnernamen
    if [[ -z "$desktop_dir" || ! -d "$desktop_dir" ]]; then
        for candidate in \
            "$TARGET_HOME/Desktop" \
            "$TARGET_HOME/Schreibtisch" \
            "$TARGET_HOME/Bureau" \
            "$TARGET_HOME/Escritorio" \
            "$TARGET_HOME/Área de Trabalho" \
            "$TARGET_HOME/Scrivania"; do
            if [[ -d "$candidate" ]]; then
                desktop_dir="$candidate"
                break
            fi
        done
    fi

    if [[ -z "$desktop_dir" || ! -d "$desktop_dir" ]]; then
        warn "Desktop-Ordner nicht gefunden – überspringe Desktop-Verknüpfung."
        info "Die Anwendung ist trotzdem über das Anwendungsmenü erreichbar."
        return
    fi

    local shortcut="$desktop_dir/hardwaretest.desktop"
    cp "$DESKTOP_FILE" "$shortcut"
    chown "$TARGET_USER":"$TARGET_USER" "$shortcut"
    chmod 755 "$shortcut"

    # ── Desktop-spezifische Vertrauenseinstellungen ──────────────────────────

    # GNOME (ab 3.x / 40+): Datei als „vertrauenswürdig" markieren
    if command -v gio &>/dev/null; then
        sudo -u "$TARGET_USER" gio set "$shortcut" \
            metadata::trusted true 2>/dev/null || true
    fi

    # Cinnamon / Nemo: gleiche gio-Methode, bereits oben abgedeckt

    # KDE Plasma: chmod 755 reicht – keine spezielle Markierung nötig

    # XFCE / Thunar: .desktop-Dateien werden automatisch erkannt

    # MATE / Caja: ebenfalls automatisch

    # Moksha / Enlightenment (Bodhi Linux): .desktop wird im PCManFM
    # oder Thunar auf dem Desktop erkannt. chmod 755 reicht.
    # efreetd aktualisieren falls vorhanden (Moksha Desktop-Cache)
    if command -v efreetd &>/dev/null; then
        sudo -u "$TARGET_USER" efreetd --restart 2>/dev/null || true
    fi

    ok "Desktop-Verknüpfung: $shortcut"
}

install_desktop_shortcut

# ── Icon-Cache und Desktop-Datenbank aktualisieren ───────────────────────────
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi
# KDE-spezifisch: kbuildsycoca5 aktualisiert den Anwendungskatalog
if command -v kbuildsycoca5 &>/dev/null; then
    sudo -u "$TARGET_USER" kbuildsycoca5 2>/dev/null || true
fi

# =============================================================================
#  6. Zusammenfassung
# =============================================================================
header "7/7 – Zusammenfassung"

echo ""
echo -e "${BOLD}Installierte Komponenten:${NC}"
echo -e "  ${GREEN}✓${NC} Python-Umgebung:   $REPO_DIR/.venv"
echo -e "  ${GREEN}✓${NC} CLI-Starter:       hardwaretest  (oder hardwaretest-launcher)"
echo -e "  ${GREEN}✓${NC} Desktop-Starter:   $DESKTOP_FILE"

if [[ -x "$PRIME_BIN" ]]; then
    echo -e "  ${GREEN}✓${NC} Prime95:           $PRIME_BIN (im Projekt)"
elif [[ -x "$LEGACY_PRIME_BIN" ]]; then
    echo -e "  ${GREEN}✓${NC} Prime95:           $LEGACY_PRIME_BIN (Legacy-Pfad)"
elif command -v mprime &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Prime95:           $(command -v mprime)"
else
    echo -e "  ${YELLOW}○${NC} Prime95:           nicht installiert"
fi

if [[ -f "$ICON_DEST" ]]; then
    echo -e "  ${GREEN}✓${NC} Icon:              $ICON_DEST"
fi

if command -v fastfetch &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Fastfetch:         $(command -v fastfetch)"
else
    echo -e "  ${YELLOW}○${NC} Fastfetch:         nicht installiert"
fi

if command -v jless &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} JSON-Reader:       jless ($(command -v jless))"
else
    _summary_viewer=""
    for _c in fx gnome-text-editor gedit xed kate; do
        if command -v "$_c" &>/dev/null; then _summary_viewer="$_c"; break; fi
    done
    if [[ -n "$_summary_viewer" ]]; then
        echo -e "  ${YELLOW}○${NC} JSON-Reader:       $_summary_viewer (Fallback)"
    else
        echo -e "  ${YELLOW}○${NC} JSON-Reader:       nur jq (Minimal-Viewer)"
    fi
fi

if _polkit_agent_running; then
    echo -e "  ${GREEN}✓${NC} Polkit-Agent:      aktiv (pkexec zeigt Passwort-Dialog)"
else
    echo -e "  ${YELLOW}○${NC} Polkit-Agent:      nicht aktiv (App nutzt eigenen Passwort-Dialog)"
fi

echo ""
echo -e "${BOLD}Starten:${NC}"
echo -e "  • Terminal:          ${CYAN}hardwaretest${NC}"
echo -e "  • Anwendungsmenü:    ${CYAN}Hardwaretest${NC}  (mit Icon)"
echo ""

# ── HPE-spezifische Hinweise ──────────────────────────────────────────────────
if command -v ssacli &>/dev/null || command -v hpssacli &>/dev/null; then
    ok "HPE SmartArray Tool erkannt – RAID-Info in der App verfügbar."
elif [[ -f /opt/smartstorageadmin/ssacli/bin/ssacli ]]; then
    ok "HPE ssacli erkannt unter /opt/smartstorageadmin/"
else
    echo ""
    info "HPE-Hinweis: ssacli/hpssacli nicht gefunden."
    info "  Auf HPE ProLiant für RAID-Info installieren:"
    info "    1. GPG-Schlüssel importieren:"
    info "       curl https://downloads.linux.hpe.com/SDR/hpPublicKey2048_key1.pub | gpg --dearmor | sudo tee /usr/share/keyrings/hpePublicKey.gpg > /dev/null"
    info "       curl https://downloads.linux.hpe.com/SDR/hpePublicKey2048_key1.pub | gpg --dearmor | sudo tee -a /usr/share/keyrings/hpePublicKey.gpg > /dev/null"
    info "       curl https://downloads.linux.hpe.com/SDR/hpePublicKey2048_key2.pub | gpg --dearmor | sudo tee -a /usr/share/keyrings/hpePublicKey.gpg > /dev/null"
    info "    2. Repository hinzufügen (Ubuntu 24.04):"
    info "       echo 'deb [signed-by=/usr/share/keyrings/hpePublicKey.gpg] https://downloads.linux.hpe.com/SDR/repo/mcp noble/current non-free' | sudo tee /etc/apt/sources.list.d/hpe-mcp.list"
    info "    3. sudo apt update && sudo apt install ssacli"
    info "    Doku: https://downloads.linux.hpe.com/SDR/project/mcp/"
fi

echo ""
ok "Installation abgeschlossen!"
