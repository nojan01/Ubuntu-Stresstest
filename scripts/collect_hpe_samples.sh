#!/bin/bash
# Sammelt Rohausgaben der HPE-/MegaRAID-Werkzeuge auf einem ProLiant als Test-Fixtures
# für hardwaretest/core/hpe_raid.py.  Als root ausführen (z. B. im HPE-Live-ISO).
# Jeder Aufruf hat ein Zeitlimit und darf fehlen; Fehler landen in <name>.err.
set -u

TIMEOUT=${TIMEOUT:-180}
OUT="hpe-samples-$(hostname)-$(date +%Y%m%d-%H%M)"
mkdir -p "$OUT" || exit 1

log() { printf '%s\n' "$*"; }

# capture <datei-stamm> <kommando …>
capture() {
  local name=$1; shift
  if ! command -v "$1" >/dev/null 2>&1 && [ ! -x "$1" ]; then
    log "  - $name: $1 nicht gefunden"
    printf 'not found: %s\n' "$1" >"$OUT/$name.err"
    return 1
  fi
  log "  * $name: $*"
  if timeout "$TIMEOUT" "$@" >"$OUT/$name.txt" 2>"$OUT/$name.err"; then
    [ -s "$OUT/$name.err" ] || rm -f "$OUT/$name.err"
    return 0
  fi
  printf 'exit=%s\n' "$?" >>"$OUT/$name.err"
  return 1
}

find_tool() {
  # find_tool name [verzeichnisse …] → Pfad oder leer
  local name=$1; shift
  local p
  p=$(command -v "$name" 2>/dev/null) && { printf '%s' "$p"; return 0; }
  for d in "$@"; do [ -x "$d/$name" ] && { printf '%s' "$d/$name"; return 0; }; done
  return 1
}

log "== Sammle nach $OUT =="
{
  echo "hostname: $(hostname)"
  echo "date: $(date -Is)"
  echo "kernel: $(uname -r)"
  echo "user: $(id -un) ($(id -u))"
} >"$OUT/meta.txt"
[ "$(id -u)" = 0 ] || log "WARNUNG: nicht als root – viele Ausgaben werden fehlen."

# --- Smart Array (ssacli / hpssacli) -------------------------------------------
SSACLI=$(find_tool ssacli /opt/smartstorageadmin/ssacli/bin /opt/smartstorageadmin/ssacli/bld) \
  || SSACLI=$(find_tool hpssacli /opt/hp/hpssacli/bld) || SSACLI=""
if [ -n "$SSACLI" ]; then
  capture ssacli_version "$SSACLI" version
  capture ssacli_ctrl_all_show "$SSACLI" ctrl all show
  capture ssacli_ctrl_all_show_status "$SSACLI" ctrl all show status
  capture ssacli_ctrl_all_show_config "$SSACLI" ctrl all show config
  capture ssacli_ctrl_all_show_config_detail "$SSACLI" ctrl all show config detail
  capture ssacli_ctrl_all_show_detail "$SSACLI" ctrl all show detail
  if [ -s "$OUT/ssacli_ctrl_all_show.txt" ]; then
    grep -oE 'in Slot [0-9]+' "$OUT/ssacli_ctrl_all_show.txt" | awk '{print $3}' | sort -u | while read -r slot; do
      capture "ssacli_slot${slot}_pd_all_show_detail" "$SSACLI" ctrl slot="$slot" pd all show detail
      capture "ssacli_slot${slot}_ld_all_show_detail" "$SSACLI" ctrl slot="$slot" ld all show detail
      capture "ssacli_slot${slot}_show_detail" "$SSACLI" ctrl slot="$slot" show detail
    done
  fi
else
  log "  - ssacli/hpssacli nicht gefunden"
fi
SSADUCLI=$(find_tool ssaducli /opt/smartstorageadmin/ssaducli/bin /opt/smartstorageadmin/ssaducli/bld) || SSADUCLI=""
if [ -n "$SSADUCLI" ]; then
  log "  * ssaducli -f $OUT/ssaducli-report.zip"
  timeout 600 "$SSADUCLI" -f "$PWD/$OUT/ssaducli-report.zip" >"$OUT/ssaducli.txt" 2>"$OUT/ssaducli.err" \
    || printf 'exit=%s\n' "$?" >>"$OUT/ssaducli.err"
fi

# --- MegaRAID (storcli64 / storcli2) ----------------------------------------------
for tool in storcli64 storcli2 storcli; do
  BIN=$(find_tool "$tool" /opt/MegaRAID/storcli /opt/MegaRAID/storcli2 /usr/local/sbin) || continue
  capture "${tool}_call_show_all" "$BIN" /call show all J
  capture "${tool}_call_show" "$BIN" /call show J
  capture "${tool}_call_eall_sall_show_all" "$BIN" /call/eall/sall show all J
  capture "${tool}_call_vall_show_all" "$BIN" /call/vall show all J
  capture "${tool}_call_cv_show_all" "$BIN" /call/cv show all J
  capture "${tool}_call_bbu_show_all" "$BIN" /call/bbu show all J
  capture "${tool}_call_show_events" "$BIN" /call show events type=latest=200 filter=warning,critical,fatal J
  capture "${tool}_call_show_all_text" "$BIN" /call show all
done

# --- iLO / IPMI --------------------------------------------------------------------
modprobe -q ipmi_si 2>/dev/null; modprobe -q ipmi_devintf 2>/dev/null
capture ipmitool_mc_info ipmitool mc info
capture ipmitool_sdr_elist ipmitool sdr elist
capture ipmitool_sdr_type_temperature ipmitool sdr type Temperature
capture ipmitool_sel_info ipmitool sel info
capture ipmitool_sel_elist ipmitool sel elist
# Nur die Konfiguration ohne Benutzer/Passwörter (hponcfg -g); kein -w iLO.xml.
capture hponcfg_g hponcfg -g
if command -v ilorest >/dev/null 2>&1; then
  capture ilorest_iml ilorest --nologo serverlogs --selectlog=IML --json
  ilorest --nologo logout >/dev/null 2>&1
fi

# --- System ------------------------------------------------------------------------
capture lspci_nn sh -c "lspci -nn | grep -iE 'raid|sas|scsi|storage'"
capture lspci_vv_storage sh -c "lspci -vv -d ::0104; lspci -vv -d ::0107; lspci -vv -d ::0100"
capture lsblk_O lsblk -O -J
capture lsscsi lsscsi -g
capture dmidecode_system dmidecode -t system -t baseboard
capture dmidecode_bios dmidecode -t bios
capture ls_dev_sg sh -c "ls -l /dev/sg* /dev/cciss 2>/dev/null"
capture dmesg_storage sh -c "dmesg | grep -iE 'hpsa|smartpqi|megaraid|mpt3sas|scsi|sd[a-z]' | tail -500"
capture smartctl_scan smartctl --scan-open

# Leere .err-Dateien entfernen
find "$OUT" -name '*.err' -size 0 -delete 2>/dev/null

TAR="$OUT.tar.gz"
if tar czf "$TAR" "$OUT"; then
  log ""
  log "== Fertig: $TAR =="
  log "Bitte das Archiv in das Repository nach tests/fixtures/hpe/ kopieren (Seriennummern werden dort anonymisiert)."
  log "Übersicht:"
  ls -la "$OUT" | sed 's/^/  /'
else
  log "tar fehlgeschlagen – Ordner $OUT bleibt bestehen."
fi
