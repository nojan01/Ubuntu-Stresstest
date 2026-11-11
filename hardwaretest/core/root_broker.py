"""Langlebige Root-Sitzung über eine einzige Autorisierung (sudo -A, Fallback pkexec).

Der Root-Prozess nimmt zeilenweise nur freigegebene Aufträge entgegen: nvme mit
lesenden/Selbsttest-Unterbefehlen, dcgmi, mitgeliefertes OpenDCDiag, lesendes
ipmitool, dmidecode, IML per ilorest, das Löschen von SEL/IML/iLO Event Log
(ProLiant Info, nach Rückfrage), lesende RAID-Abfragen (ssacli/hpssacli,
storcli64/storcli2) sowie ein ssaducli-Diagnosebericht in eine neue .zip-Datei,
ein eng geprüfter fio-Auftrag
(``FIOJOB``; die Jobdatei schreibt der Root-Prozess selbst aus einzeln
geprüften Schlüssel/Wert-Paaren) sowie eine feste rasdaemon-Abfrage
(``RASQUERY``).  Jeder Auftrag läuft in einer eigenen Prozessgruppe und
schreibt in eine FIFO, die nur der aufrufende Benutzer lesen kann; ein
unprivilegierter Relay-Prozess liest sie und liefert den Exit-Code.  ``KILL``
beendet die Prozessgruppe als root und wartet auf ihr Ende.  Schliesst die App
die Sitzung (oder stirbt sie), beendet der Root-Prozess alle Aufträge.
"""

from __future__ import annotations

import contextlib
import json
import os
import selectors
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence

from hardwaretest.core import privilege, ras
from hardwaretest.core.paths import resource_path

READY_MARKER = "@@HARDWARETEST-ROOT-READY@@"

# Jobdatei-Schlüssel, die der Root-Prozess in eine fio-Jobdatei übernimmt. Alles
# andere (exec_prerun/exec_postrun, ioengine=exec, Ausgabepfade, ...) wird abgelehnt.
FIO_JOB_KEYS = frozenset({
    "rw", "bs", "direct", "ioengine", "iodepth", "numjobs", "runtime", "time_based",
    "size", "offset", "offset_increment", "group_reporting", "continue_on_error",
    "error_dump", "filename", "allow_file_create", "verify", "do_verify", "verify_fatal",
    "verify_dump", "verify_backlog", "verify_backlog_batch", "loops", "sync", "unlink",
    "create_on_open", "name",
})
FIO_CLI_FLAGS = frozenset({
    "--readonly", "--eta=always", "--eta=never", "--eta=auto", "--eta-interval",
    "--status-interval", "--output-format=json", "--output-format=normal", "--group_reporting",
})

_ROOT_BROKER_TEMPLATE = r"""
umask 077
# Unter pkexec (Root) nur Systempfade; sudo setzt secure_path; ohne Privilegien (Tests) bleibt PATH erhalten.
[ -z "${PKEXEC_UID:-}" ] || { PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH; }
# Exact bundled paths passed as shell arguments, not command names from PATH
# or interpolated shell source. Keep both CPU variants available.
odcd_avx2=${1:-}
odcd_generic=${2:-}
odcd_tmp_root=${3:-/tmp}
runtime_parent=${4:-}
ras_db=/var/lib/rasdaemon/ras-mc_event.db
# Aufrufender Benutzer: pkexec setzt PKEXEC_UID, sudo -A setzt SUDO_UID.
caller=${PKEXEC_UID:-${SUDO_UID:-}}
case "$caller" in ''|*[!0-9]*) caller=$(id -u);; esac
# Working directory: root-owned, traverse-only, random name; preferably inside
# the caller's private runtime directory (0700), otherwise /tmp.
parent=/tmp
if [ -n "$runtime_parent" ] && [ -d "$runtime_parent" ] && [ ! -L "$runtime_parent" ] \
   && [ "$(stat -c %u:%a "$runtime_parent" 2>/dev/null)" = "$caller:700" ]; then
  parent=$runtime_parent
fi
work=$(mktemp -d "$parent/hardwaretest-root.XXXXXXXX") || { echo "@@ERR mktemp"; exit 1; }
chmod 711 "$work"
mkdir -m 700 "$work/jobs" || exit 1
cat >"$work/jobs/rasquery.py" <<'HARDWARETEST_RAS_QUERY_PY'
@@RAS_QUERY_SCRIPT@@
HARDWARETEST_RAS_QUERY_PY
jobs=""
njob=0
kill_group() {
  # $1 job dir, $2 signal: ganze Prozessgruppe, sonst nur den Hauptprozess.
  [ -s "$1/pid" ] || return 0
  pid=$(cat "$1/pid")
  case "$pid" in ''|*[!0-9]*) return 0;; esac
  kill "-$2" "-$pid" 2>/dev/null || kill "-$2" "$pid" 2>/dev/null
}
wait_status() {
  # $1 job dir, $2 Zehntelsekunden
  n=0
  while [ ! -s "$1/status" ] && [ "$n" -lt "$2" ]; do sleep 0.1; n=$((n+1)); done
  [ -s "$1/status" ]
}
cleanup() {
  for d in $jobs; do kill_group "$d" TERM; done
  for d in $jobs; do wait_status "$d" 20; kill_group "$d" KILL; done
  rm -rf "$work"
}
trap cleanup EXIT
launch() {
  # Startet "$@" als eigene Sitzung/Prozessgruppe; Ausgabe über FIFO an den Relay.
  d=$(mktemp -d "$work/job.XXXXXXXX") || { echo "@@ERR mktemp"; return 1; }
  chmod 711 "$d"
  mkfifo -m 600 "$d/out" && : >"$d/err" && : >"$d/status" || { echo "@@ERR mkfifo"; rm -rf "$d"; return 1; }
  chmod 600 "$d/err" "$d/status"
  chown "$caller" "$d/out" "$d/err" "$d/status" 2>/dev/null
  jobs="$jobs $d"
  if command -v setsid >/dev/null 2>&1; then
    ( exec >"$d/out" 2>"$d/err" </dev/null; setsid "$@" & p=$!; echo "$p" >"$d/pid"; wait "$p"; echo "$?" >"$d/status" ) &
  else
    ( exec >"$d/out" 2>"$d/err" </dev/null; "$@" & p=$!; echo "$p" >"$d/pid"; wait "$p"; echo "$?" >"$d/status" ) &
  fi
  echo "@@JOB $d"
}
vendor_prefix_ok() { case "$1" in /usr/*|/opt/*) return 0 ;; *) return 1 ;; esac; }
vendor_path_ok() {
  # Hersteller-Programm mit Pfad: Der Aufrufer darf es nicht austauschen koennen. Herstellerpakete
  # liefern Dateien oft mit fremder UID (niccli: ccxswbuild) oder gruppenbeschreibbar (EPCT.zip) ->
  # gefordert: unter /usr oder /opt, Eintrag (Symlink), Ziel und dessen Ordner gehoeren nicht dem
  # Aufrufer und sind nicht fuer Andere beschreibbar; das Ziel ist eine ausfuehrbare Datei.
  vendor_prefix_ok "$1" || return 1
  [ -n "$(find "$1" -maxdepth 0 ! -uid "$caller" 2>/dev/null)" ] || return 1
  real=$(readlink -f "$1" 2>/dev/null) || return 1
  [ -n "$(find "$real" -maxdepth 0 -type f ! -uid "$caller" -perm -u+x ! -perm /002 2>/dev/null)" ] || return 1
  [ -n "$(find "${real%/*}" -maxdepth 0 -type d ! -uid "$caller" ! -perm /002 2>/dev/null)" ] || return 1
  if [ -n "$(find "$real" -maxdepth 0 -perm /020 2>/dev/null)" ]; then
    # gruppenbeschreibbar: nur zulassen, wenn der Aufrufer nicht in dieser Gruppe ist
    g=$(stat -c %g "$real" 2>/dev/null) || return 1
    for cg in $(id -G "$caller" 2>/dev/null); do [ "$cg" = "$g" ] && return 1; done
  fi
  return 0
}
echo "@@HARDWARETEST-ROOT-READY@@"
while IFS= read -r line; do
  set -f; set -- $line; set +f
  op=${1:-}; [ $# -gt 0 ] && shift
  case "$op" in
  RUN)
    ok=1; postchown=0; vcd=0; vjoin=0; vbdf=""
    case "${1:-}" in
      dcgmi) ;;
      nvme) case "${2:-}" in smart-log|self-test-log|device-self-test) ;; *) ok=0;; esac ;;
      smartctl|/usr/sbin/smartctl)
        # Nur lesende SMART-Abfrage ganzer Datentraeger im JSON-Format.
        [ $# -eq 4 ] && [ "$2" = "-a" ] && [ "$3" = "-j" ] || ok=0
        case "${4:-}" in /dev/sd[a-z]|/dev/sd[a-z][a-z]|/dev/sd[a-z][a-z][a-z]|/dev/nvme[0-9]|/dev/nvme[0-9][0-9]) ;; *) ok=0;; esac ;;
      ipmitool)
        case "${2:-} ${3:-}" in
          "sdr type"|"sdr elist"|"sel list"|"sel elist"|"sel info"|"mc info") ;;
          # Ereignisprotokoll loeschen (ProLiant Info, nach Rueckfrage)
          "sel clear") [ $# -eq 3 ] || ok=0 ;;
          *) ok=0 ;;
        esac ;;
      dmidecode)
        # Nur lesende Tabellen: -t <typ> beliebig oft (System, CPU, Speicher, Netzteile).
        [ $# -ge 3 ] && [ $(( ($# - 1) % 2 )) -eq 0 ] || ok=0
        i=0; expect_t=1
        for a in "$@"; do
          i=$((i+1)); [ "$i" -eq 1 ] && continue
          if [ "$expect_t" = 1 ]; then [ "$a" = "-t" ] || ok=0; expect_t=0
          else case "$a" in memory|system|processor|1|4|16|17|39) ;; *) ok=0;; esac; expect_t=1; fi
        done ;;
      ethtool)
        # Selbsttest (unterbricht bei offline kurz den Link) und SFP-Modulinfo eines Interfaces.
        case "${2:-}" in
          -t) { [ $# -eq 3 ] || { [ $# -eq 4 ] && case "$4" in online|offline) true;; *) false;; esac; }; } || ok=0 ;;
          -m|-S|--module-info) [ $# -eq 3 ] || ok=0 ;;
          *) ok=0 ;;
        esac
        case "${3:-}" in ''|-*|*/*|.|..) ok=0;; esac
        [ ${#3} -le 15 ] || ok=0 ;;
      mstlink)
        # Mellanox-Link-Diagnose (mstflint), nur lesend: -d <PCI-BDF> [--show_module] [--show_counters]
        [ $# -ge 3 ] && [ $# -le 5 ] && [ "$2" = "-d" ] || ok=0
        case "${3:-}" in
          [0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F].[0-7]) ;;
          [0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F].[0-7]) ;;
          *) ok=0 ;;
        esac
        i=0; for a in "$@"; do i=$((i+1)); [ "$i" -le 3 ] && continue
          case "$a" in --show_module|--show_counters) ;; *) ok=0;; esac; done ;;
      ilorest)
        # IML lesen; IML/iLO Event Log loeschen und IEL vorher sichern (ProLiant Info, nach Rueckfrage)
        case "$*" in
          "ilorest --nologo serverlogs --selectlog=IML --json"|"ilorest --nologo serverlogs --selectlog=IEL --json") ;;
          "ilorest --nologo serverlogs --selectlog=IML --clearlog"|"ilorest --nologo serverlogs --selectlog=IEL --clearlog") ;;
          *) ok=0 ;;
        esac ;;
      modprobe) [ $# -eq 3 ] && [ "$2" = "-q" ] && case "$3" in ipmi_si|ipmi_devintf) true;; *) false;; esac || ok=0 ;;
      swapoff) [ $# -eq 2 ] && [ "$2" = "-a" ] || ok=0 ;;
      swapon)
        # Gesamttest: Swap nach dem RAM-Fuelltest wieder aktivieren (alle oder ein Geraet/eine Datei).
        [ $# -eq 2 ] || ok=0
        case "${2:-}" in -a|/?*) ;; *) ok=0;; esac
        case "${2:-}" in *..*|*//*) ok=0;; esac ;;
      zpool)
        # Nur Scrub starten/abbrechen; Pool-Namen beginnen mit einem Buchstaben.
        case $# in 3) [ "$2" = "scrub" ] || ok=0; pool=$3;; 4) [ "$2 $3" = "scrub -s" ] || ok=0; pool=$4;; *) ok=0;; esac
        case "${pool:-}" in [A-Za-z]*) ;; *) ok=0;; esac
        case "${pool:-}" in *[!A-Za-z0-9_.:-]*) ok=0;; esac
        [ ${#pool} -le 64 ] || ok=0 ;;
      ssacli|hpssacli|hpacucli|/opt/smartstorageadmin/ssacli/bin/ssacli|/opt/smartstorageadmin/ssacli/bld/ssacli|/opt/hp/hpssacli/bld/hpssacli|/opt/compaq/hpacucli/bld/hpacucli)
        # Nur lesende Smart-Array-Abfragen (Status/Konfiguration/Laufwerksdetails).
        rest=""; i=0; for a in "$@"; do i=$((i+1)); [ "$i" -gt 1 ] && rest="$rest $a"; done; rest=${rest# }
        case "$rest" in
          version|"ctrl all show"|"ctrl all show status"|"ctrl all show config"|"ctrl all show config detail"|"ctrl all show detail") ;;
          "ctrl slot="*" pd all show detail"|"ctrl slot="*" ld all show detail"|"ctrl slot="*" show detail"|"ctrl slot="*" show config detail")
            slotarg=${3#slot=}; case "$slotarg" in ''|*[!0-9]*) ok=0;; esac ;;
          *) ok=0 ;;
        esac ;;
      storcli|storcli64|storcli2|/opt/MegaRAID/storcli/storcli64|/opt/MegaRAID/storcli2/storcli2|/opt/MegaRAID/storcli/storcli|/usr/local/sbin/storcli64|/usr/local/sbin/storcli2)
        # Nur lesende MegaRAID-Abfragen im JSON-Format.
        rest=""; i=0; for a in "$@"; do i=$((i+1)); [ "$i" -gt 1 ] && rest="$rest $a"; done; rest=${rest# }
        case "$rest" in
          "/call show all J"|"/call show J"|"/call/eall/sall show all J"|"/call/vall show all J"|"/call/cv show all J"|"/call/bbu show all J") ;;
          *) ok=0 ;;
        esac ;;
      ssaducli|/opt/smartstorageadmin/ssaducli/bin/ssaducli|/opt/smartstorageadmin/ssaducli/bld/ssaducli)
        # Diagnosebericht nur als neue .zip-Datei unter einem absoluten Pfad; danach
        # geht die Datei an den aufrufenden Benutzer über (postchown).
        [ $# -eq 3 ] && [ "$2" = "-f" ] || ok=0
        case "${3:-}" in /*.zip) ;; *) ok=0;; esac
        case "${3:-}" in *..*|*//*) ok=0;; esac
        [ ! -e "${3:-}" ] && [ ! -L "${3:-}" ] || ok=0
        postchown=1 ;;
      "$odcd_avx2"|"$odcd_generic")
        # Only the runner's time-limited diagnostic command, never arbitrary
        # executables, tool options or output paths supplied by a caller.
        { [ $# -eq 6 ] || [ $# -eq 7 ]; } || ok=0
        [ "${2:-}" = "-T" ] && [ "${4:-}" = "--strict-runtime" ] && [ "${5:-}" = "-o" ] || ok=0
        seconds=${3:-}; case "$seconds" in *s) seconds=${seconds%s};; *) ok=0;; esac
        case "$seconds" in ''|0*|*[!0-9]*) ok=0;; esac
        report=${6:-}
        case "$report" in "$odcd_tmp_root"/hardwaretest-opendcdiag-*/opendcdiag.yaml) ;; *) ok=0;; esac
        report_dir=${report%/opendcdiag.yaml}
        report_id=${report_dir#"$odcd_tmp_root"/hardwaretest-opendcdiag-}
        case "$report_id" in ''|*[!A-Za-z0-9_-]*) ok=0;; esac
        [ -d "$report_dir" ] && [ ! -L "$report_dir" ] && [ ! -e "$report" ] && [ ! -L "$report" ] || ok=0
        if [ $# -eq 7 ]; then case "$7" in --cpuset=?*) ;; *) ok=0;; esac; fi
        # Wegen umask 077 entsteht das Protokoll als root:0600; nach Abschluss dem Aufrufer uebereignen.
        postchown=2 ;;
      *)
        # Hersteller-NIC-Werkzeuge (core/nic_vendor.py): nur lesende Inventar-/Link-Abfragen,
        # Programm ueber Basisnamen; mit Pfad nur root-eigene, nicht fremdbeschreibbare Dateien.
        vb=${1##*/}
        rest=""; i=0; for a in "$@"; do i=$((i+1)); [ "$i" -gt 1 ] && rest="$rest $a"; done; rest=${rest# }
        case "$vb" in
          niccli)
            case "$#:$rest" in
              2:--list) ;;
              "4:-i $3 show"|"5:-i $3 show --pkg_ver"|"5:-i $3 link -s"|"6:-i $3 cable -m --show")
                case "$3" in ''|*[!0-9]*) ok=0;; esac ;;
              *) ok=0 ;;
            esac ;;
          bnxtnvm)
            [ $# -eq 3 ] && [ "$3" = device_info ] || ok=0
            case "${2:-}" in -dev=[A-Za-z]*) ;; *) ok=0;; esac
            [ ${#2} -le 20 ] || ok=0 ;;
          nvmupdate64e|nvmupdate64e-*)
            # Inventar; Intel erwartet nvmupdate.cfg im Programmordner -> dort starten.
            [ "$#:$rest" = "2:-i" ] || ok=0; vcd=1 ;;
          epct64e|epct)
            case "$#:$rest" in
              2:-devices) ;;
              "3:-nic=${2#-nic=} -get") case "${2#-nic=}" in ''|*[!0-9]*) ok=0;; esac ;;
              *) ok=0 ;;
            esac ;;
          qlnxcli) [ "$#:$rest" = "2:-i" ] || ok=0 ;;
          QCScli)
            # QLogic Control Suite CLI verlangt die Kommandozeichenkette als ein Argument;
            # sie kommt tokenweise an und wird nach der Pruefung wieder zusammengesetzt.
            case "$#:$rest" in
              "7:-protocol local list -f BDF NDIS") vjoin=1 ;;
              "10:-protocol local -t NDIS -f MAC -i $9 info"|"10:-protocol local -t NDIS -f MAC -i $9 stats")
                case "$9" in
                  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
                  *) ok=0 ;;
                esac ;;
              *) ok=0 ;;
            esac ;;
          mlxfwmanager) [ $# -eq 4 ] && [ "$2" = --query ] && [ "$3" = -d ] || ok=0; vbdf=${4:-} ;;
          mlxconfig) [ $# -eq 4 ] && [ "$2" = -d ] && [ "$4" = query ] || ok=0; vbdf=${3:-} ;;
          mlxlink)
            [ $# -eq 5 ] && [ "$2" = -d ] && [ "$4" = --show_module ] && [ "$5" = --show_counters ] || ok=0
            vbdf=${3:-} ;;
          sfupdate|sfboot) [ $# -eq 1 ] || ok=0 ;;
          sfreport) [ "$#:$rest" = "3:-m -" ] || ok=0 ;;
          *) ok=0 ;;
        esac
        if [ -n "$vbdf" ]; then
          case "$vbdf" in
            [0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F].[0-7]) ;;
            [0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F].[0-7]) ;;
            *) ok=0 ;;
          esac
        fi
        if [ "$ok" = 1 ]; then
          case "$1" in */*) vendor_path_ok "$1" || { echo "@@ERR Programm nicht vertrauenswuerdig (Rechte/Eigentuemer): $1"; continue; } ;; esac
        fi ;;
    esac
    for a in "$@"; do case "$a" in *[!A-Za-z0-9/_=.,:%+-]*) ok=0;; esac; done
    if [ "$ok" != 1 ]; then echo "@@ERR Befehl nicht erlaubt"; continue; fi
    if [ "$vjoin" = 1 ]; then set -- "$1" "$2" "$3" "$4 $5 $6 $7"; fi
    case "$1" in
      /*) ;;
      *) bin=$(command -v "$1" 2>/dev/null) || { echo "@@ERR Programm nicht gefunden: $1"; continue; }
         shift; set -- "$bin" "$@" ;;
    esac
    if [ "$vcd" = 1 ]; then
      set -- sh -c 'cd "$(dirname "$(readlink -f "$1")")" && shift && exec "$@"' hardwaretest-vendor "$1" "$@"
    fi
    if [ "$postchown" = 1 ]; then
      # ssaducli: Bericht nach Abschluss dem Aufrufer übereignen.
      set -- sh -c '"$1" -f "$2"; rc=$?; [ -e "$2" ] && chown "$3" "$2"; exit $rc' hardwaretest-ssaducli "$1" "$3" "$caller"
    elif [ "$postchown" = 2 ]; then
      # OpenDCDiag: YAML-Protokoll nach Abschluss dem Aufrufer uebereignen.
      set -- sh -c 'r=$1; u=$2; shift 2; "$@"; rc=$?; [ -e "$r" ] && [ ! -L "$r" ] && chown "$u" "$r"; exit $rc' hardwaretest-opendcdiag "$report" "$caller" "$@"
    fi
    launch "$@"
    ;;
  FIOJOB)
    # Tokens: --flag[=wert] (CLI), @name (neuer Abschnitt), schluessel=wert.
    ok=1; flags=""; body=""; section=0; nfile=0; writes=0; devonly=1
    for a in "$@"; do
      case "$a" in
        --readonly|--eta=always|--eta=never|--eta=auto|--output-format=json|--output-format=normal|--group_reporting)
          flags="$flags $a" ;;
        --eta-interval=*|--status-interval=*)
          case "${a#*=}" in ''|*[!0-9]*) ok=0;; esac; flags="$flags $a" ;;
        @*)
          name=${a#@}
          case "$name" in ''|*[!A-Za-z0-9_.-]*) ok=0;; esac
          body="$body
[$name]"; section=1 ;;
        *=*)
          [ "$section" = 1 ] || ok=0
          k=${a%%=*}; v=${a#*=}
          [ -n "$v" ] || ok=0
          case "$k" in
            rw) case "$v" in read|randread) ;; write|randwrite|randrw|readwrite|rw) writes=1;; *) ok=0;; esac ;;
            ioengine) case "$v" in libaio|io_uring|psync|sync|pvsync|posixaio) ;; *) ok=0;; esac ;;
            filename)
              case "$v" in /*) ;; *) ok=0;; esac
              case "$v" in *..*|*//*|*[!A-Za-z0-9/_.-]*) ok=0;; esac
              case "$v" in /dev/*) ;; *) devonly=0;; esac
              nfile=$((nfile+1)) ;;
            bs|size|offset|offset_increment) case "$v" in *[!0-9kKmMgG%]*) ok=0;; esac ;;
            iodepth|numjobs|runtime|loops|verify_backlog|verify_backlog_batch) case "$v" in *[!0-9]*) ok=0;; esac ;;
            direct|time_based|group_reporting|error_dump|allow_file_create|do_verify|verify_fatal|verify_dump|sync|unlink|create_on_open)
              case "$v" in 0|1) ;; *) ok=0;; esac ;;
            continue_on_error) case "$v" in none|read|write|io|verify|all) ;; *) ok=0;; esac ;;
            verify) case "$v" in ''|*[!a-z0-9-]*) ok=0;; esac ;;
            name) case "$v" in ''|*[!A-Za-z0-9_.-]*) ok=0;; esac ;;
            *) ok=0 ;;
          esac
          body="$body
$k=$v" ;;
        *) ok=0 ;;
      esac
    done
    [ "$nfile" -ge 1 ] || ok=0
    # Schreibende Aufträge als root nur auf Blockgeräte, nie auf beliebige Dateien.
    [ "$writes" = 0 ] || [ "$devonly" = 1 ] || ok=0
    if [ "$ok" != 1 ]; then echo "@@ERR Befehl nicht erlaubt"; continue; fi
    fio_bin=$(command -v fio 2>/dev/null) || { echo "@@ERR Programm nicht gefunden: fio"; continue; }
    njob=$((njob+1)); jobfile="$work/jobs/$njob.fio"
    printf '%s\n' "$body" >"$jobfile" || { echo "@@ERR Jobdatei"; continue; }
    set -f; set -- $flags; set +f
    launch "$fio_bin" "$@" "$jobfile"
    ;;
  RASQUERY)
    # Feste Abfrage der rasdaemon-Datenbank; Baseline ist kompaktes JSON oder null.
    baseline=${1:-null}
    case "$baseline" in *[!A-Za-z0-9_{}\":,-]*|'') echo "@@ERR Befehl nicht erlaubt"; continue;; esac
    py=$(command -v python3 2>/dev/null) || { echo "@@ERR Programm nicht gefunden: python3"; continue; }
    launch "$py" "$work/jobs/rasquery.py" "$ras_db" "$baseline" "${2:-50}"
    ;;
  KILL)
    # KILL <job> [frist-sekunden]: SIGTERM an die Gruppe, nach Frist SIGKILL, dann warten.
    grace=${2:-10}; case "$grace" in ''|*[!0-9]*) grace=10;; esac
    case " $jobs " in
      *" ${1:-} "*)
        kill_group "$1" TERM
        wait_status "$1" $((grace*10))
        # Nachzuegler der Gruppe (z. B. Kindprozesse, die SIGTERM ignorieren) hart beenden.
        kill_group "$1" KILL
        wait_status "$1" 50
        echo "@@OK" ;;
      *) echo "@@ERR Unbekannter Auftrag" ;;
    esac ;;
  DONE)
    case " $jobs " in
      *" ${1:-} "*)
        rm -rf "$1"
        rest=""; for d in $jobs; do [ "$d" = "$1" ] || rest="$rest $d"; done; jobs=$rest ;;
    esac
    echo "@@OK" ;;
  *) echo "@@ERR Unbekannte Operation" ;;
  esac
done
"""

ROOT_BROKER_SCRIPT = _ROOT_BROKER_TEMPLATE.replace("@@RAS_QUERY_SCRIPT@@", ras.QUERY_SCRIPT.strip("\n"))

_RELAY_SCRIPT = (
    'cat "$1/out"; cat "$1/err" >&2; '
    'rc=$(cat "$1/status" 2>/dev/null); exit "${rc:-1}"'
)


class RootAuthorizationError(RuntimeError):
    """Die Root-Autorisierung wurde abgebrochen oder abgelehnt."""


class _LineReader:
    """Zeilenweises Lesen von einem Pipe-Deskriptor mit Timeout.

    ``wake()`` bricht ein laufendes ``readline`` aus einem anderen Thread ab
    (Ergebnis wie EOF) – auch wenn ein Enkelprozess die Pipe noch offen hält.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._buffer = b""
        self._eof = False
        self._wake_r, self._wake_w = os.pipe()

    def wake(self) -> None:
        self._eof = True
        with contextlib.suppress(OSError):
            os.write(self._wake_w, b"x")

    def close(self) -> None:
        for fd in (self._wake_r, self._wake_w):
            with contextlib.suppress(OSError):
                os.close(fd)

    def readline(self, timeout: Optional[float]) -> Optional[str]:
        """Zeile ohne Zeilenende; ``""`` bei EOF/Abbruch, ``None`` bei Timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while b"\n" not in self._buffer:
            if self._eof:
                data, self._buffer = self._buffer, b""
                return data.decode("utf-8", "replace")
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                return None
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(self._fd, selectors.EVENT_READ)
                    selector.register(self._wake_r, selectors.EVENT_READ)
                    ready = {key.fd for key, _ in selector.select(remaining)}
                if not ready:
                    return None
                if self._wake_r in ready:
                    self._eof = True
                    continue
                chunk = os.read(self._fd, 65536)
            except (OSError, ValueError):
                chunk = b""
            if not chunk:
                self._eof = True
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line.decode("utf-8", "replace")


class RootBroker:
    # Wartezeit auf die Autorisierung bzw. auf eine Protokollantwort.
    auth_timeout: float = 120.0
    reply_timeout: float = 30.0

    def __init__(self, prefix: str | Sequence[str] | None = None, *, reason: str = "Root-Sitzung starten",
                 auth_timeout: Optional[float] = None, reply_timeout: Optional[float] = None) -> None:
        """``prefix``: Programm(pfad) oder Befehlspräfix für die Rechteerhöhung;
        ``None`` → zentral über :func:`privilege.command_prefix` (sudo -A mit eigenem Dialog)."""
        if prefix is None:
            self._prefix = privilege.command_prefix(reason)
        elif isinstance(prefix, str):
            self._prefix = [prefix]
        else:
            self._prefix = list(prefix)
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._reader: Optional[_LineReader] = None
        self._stderr_path: Optional[Path] = None
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._ready = False
        if auth_timeout is not None:
            self.auth_timeout = auth_timeout
        if reply_timeout is not None:
            self.reply_timeout = reply_timeout

    def is_active(self) -> bool:
        return self._ready and self._proc is not None and self._proc.poll() is None

    @staticmethod
    def _runtime_parent() -> str:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        if runtime and os.path.isdir(runtime):
            return runtime
        return tempfile.gettempdir()

    def start(self) -> None:
        """Fragt einmal das Passwort ab; blockiert (ohne Sperre) bis zur Autorisierung."""
        with self._start_lock:
            if self.is_active():
                return
            with self._lock:
                self._close_locked()
                fd, name = tempfile.mkstemp(prefix="hardwaretest-root-", suffix=".err")
                self._stderr_path = Path(name)
                try:
                    proc = subprocess.Popen(
                        [*self._prefix, "sh", "-c", ROOT_BROKER_SCRIPT, "hardwaretest-root",
                         str(resource_path("vendor", "opendcdiag", "opendcdiag")),
                         str(resource_path("vendor", "opendcdiag", "opendcdiag-generic")),
                         tempfile.gettempdir(), self._runtime_parent()],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=fd,
                        bufsize=0,
                    )
                finally:
                    os.close(fd)
                self._proc = proc
                assert proc.stdout is not None
                self._reader = _LineReader(proc.stdout.fileno())
            line = self._reader.readline(self.auth_timeout)
            with self._lock:
                if self._proc is not proc:
                    raise RootAuthorizationError("Root-Sitzung wurde geschlossen.")
                if line is None:
                    detail = self._stderr_tail()
                    self._close_locked(wait_seconds=2)
                    raise RootAuthorizationError(
                        "Root-Berechtigung: keine Antwort innerhalb von "
                        f"{int(self.auth_timeout)} s." + detail
                    )
                if line.strip() != READY_MARKER:
                    detail = self._stderr_tail()
                    self._close_locked()
                    raise RootAuthorizationError(
                        "Root-Berechtigung wurde abgebrochen oder abgelehnt." + detail
                    )
                self._ready = True

    def _stderr_tail(self, limit: int = 800) -> str:
        path = self._stderr_path
        if path is None:
            return ""
        try:
            text = path.read_text(errors="replace").strip()
        except OSError:
            return ""
        if not text:
            return ""
        return "\n" + text[-limit:]

    def _request_locked(self, text: str, timeout: Optional[float] = None) -> str:
        """Sendet eine Protokollzeile und wartet (mit Timeout) auf die Antwort."""
        proc, reader = self._proc, self._reader
        if not self._ready or proc is None or proc.poll() is not None or proc.stdin is None or reader is None:
            raise RootAuthorizationError("Root-Sitzung ist nicht aktiv.")
        try:
            proc.stdin.write((text + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._close_locked()
            raise RootAuthorizationError("Root-Sitzung wurde beendet.") from exc
        reply = reader.readline(self.reply_timeout if timeout is None else timeout)
        if reply is None:
            detail = self._stderr_tail()
            self._close_locked(wait_seconds=2)
            raise RootAuthorizationError("Root-Sitzung antwortet nicht – Sitzung beendet." + detail)
        if reply == "":
            detail = self._stderr_tail()
            self._close_locked()
            raise RootAuthorizationError("Root-Sitzung wurde beendet." + detail)
        return reply.strip()

    @staticmethod
    def _check_tokens(args: Iterable[str]) -> list[str]:
        tokens = [str(item) for item in args]
        if not tokens or any(not item or any(ch.isspace() for ch in item) for item in tokens):
            raise RuntimeError("Ungültiges Argument für die Root-Sitzung")
        return tokens

    def _submit_line(self, line: str) -> str:
        self.start()
        with self._lock:
            reply = self._request_locked(line)
        if reply.startswith("@@JOB "):
            return reply[len("@@JOB "):]
        raise RuntimeError(reply.removeprefix("@@ERR ").strip() or "Root-Auftrag fehlgeschlagen")

    def submit(self, command: Sequence[str]) -> str:
        tokens = [str(item) for item in command]
        if tokens and os.path.basename(tokens[0]) == "QCScli":
            # Kommandozeichenkette ("list -f BDF NDIS") tokenweise übertragen; das Root-Skript
            # setzt sie nach der Prüfung wieder zu einem Argument zusammen.
            tokens = [part for item in tokens for part in item.split(" ") if part]
        return self._submit_line("RUN " + " ".join(self._check_tokens(tokens)))

    def submit_fio_job(self, tokens: Sequence[str]) -> str:
        """fio-Auftrag aus geprüften Tokens (``--flag``, ``@abschnitt``, ``schlüssel=wert``)."""
        return self._submit_line("FIOJOB " + " ".join(self._check_tokens(tokens)))

    @staticmethod
    def relay_command(job: str) -> list[str]:
        return ["sh", "-c", _RELAY_SCRIPT, "hardwaretest-relay", job]

    def kill(self, job: str, grace_seconds: float = 10) -> None:
        """Beendet die Root-Prozessgruppe des Auftrags und wartet auf ihr Ende."""
        grace = max(1, int(grace_seconds))
        with self._lock, contextlib.suppress(RootAuthorizationError):
            self._request_locked(f"KILL {job} {grace}", timeout=grace + 10 + self.reply_timeout)

    def done(self, job: str) -> None:
        with self._lock, contextlib.suppress(RootAuthorizationError):
            self._request_locked(f"DONE {job}")

    def _run_job(self, job: str, command: Sequence[str],
                 timeout: Optional[float]) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                self.relay_command(job), capture_output=True, text=True, timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.kill(job)
            raise
        finally:
            self.done(job)
        return subprocess.CompletedProcess(
            list(command), completed.returncode, completed.stdout, completed.stderr
        )

    def run(self, command: Sequence[str], timeout: Optional[float] = None,
            **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """subprocess.run-kompatibel (capture_output/text) über die Root-Sitzung."""
        return self._run_job(self.submit(command), command, timeout)

    def ras_query(self, baseline: Optional[dict], db_path: str = ras.DB_PATH,
                  limit: int = ras.MAX_EVENTS, timeout: float = 20) -> Optional[dict]:
        """Liest die rasdaemon-Datenbank als root (fester Pfad im Root-Skript)."""
        if db_path != ras.DB_PATH:
            return None
        token = json.dumps(baseline, separators=(",", ":")) if baseline is not None else "null"
        if any(ch.isspace() for ch in token):
            return None
        try:
            job = self._submit_line(f"RASQUERY {token} {int(limit)}")
            completed = self._run_job(job, ["rasquery"], timeout)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            return None
        if completed.returncode:
            return None
        try:
            data = json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None
        return data if isinstance(data, dict) else None

    def close(self) -> None:
        """Beendet die Sitzung; bricht auch eine laufende Autorisierung ab."""
        with self._lock:
            self._close_locked()

    def _close_locked(self, wait_seconds: float = 10) -> None:
        proc, self._proc = self._proc, None
        was_ready, self._ready = self._ready, False
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.wake()
        stderr_path, self._stderr_path = self._stderr_path, None
        if proc is not None:
            with contextlib.suppress(OSError):
                if proc.stdin:
                    proc.stdin.close()
            if not was_ready:
                # Noch in der Autorisierung: sudo/pkexec sofort abbrechen statt auf EOF zu warten.
                with contextlib.suppress(OSError):
                    proc.terminate()
            # Ein Root-Prozess ist für den Benutzer nicht per Signal erreichbar;
            # das Skript endet regulär bei stdin-EOF. Signale sind nur Beste-Mühe.
            try:
                proc.wait(timeout=wait_seconds if was_ready else min(wait_seconds, 5))
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        proc.kill()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=3)
            with contextlib.suppress(OSError):
                if proc.stdout:
                    proc.stdout.close()
        if reader is not None:
            reader.close()
        if stderr_path is not None:
            with contextlib.suppress(OSError):
                stderr_path.unlink()
