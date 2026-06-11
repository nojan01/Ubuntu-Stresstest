#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
    echo "Bitte mit sudo ausführen." >&2
    exit 1
fi

if [[ $# -ge 1 ]]; then
    OUTPUT_PATH="$1"
else
    TARGET_USER=${SUDO_USER:-$(logname 2>/dev/null || echo root)}
    TARGET_HOME=$(eval echo "~${TARGET_USER}")
    OUTPUT_PATH="$TARGET_HOME/Downloads/hardwaretest_system_report.json"
fi
OUTPUT_DIR="$(dirname "$OUTPUT_PATH")"
mkdir -p "$OUTPUT_DIR"

TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT
MANIFEST="$TMP_DIR/command_manifest.tsv"
: >"$MANIFEST"

capture_cmd() {
    local name="$1"
    local display="$2"
    shift 2
    local outfile="$TMP_DIR/${name}.txt"
    local binary="$1"
    if ! command -v "$binary" >/dev/null 2>&1; then
        printf '%s nicht gefunden\n' "$binary" >"$outfile"
    else
        if "$@" >"$outfile" 2>&1; then
            :
        else
            local rc=$?
            printf '\n[Exit-Code %s]\n' "$rc" >>"$outfile"
        fi
    fi
    printf '%s\t%s\t%s\n' "$name" "$display" "$outfile" >>"$MANIFEST"
    echo "$outfile"
}

capture_cmd fastfetch "fastfetch --logo none --pipe" fastfetch --logo none --pipe
capture_cmd lshw_cpu "sudo lshw -class cpu" lshw -class cpu
capture_cmd lshw_memory "sudo lshw -class memory" lshw -class memory
capture_cmd lshw_storage "sudo lshw -class storage" lshw -class storage
capture_cmd lshw_disk "sudo lshw -class disk" lshw -class disk
capture_cmd lshw_raid "sudo lshw -class raid" lshw -class raid
capture_cmd lshw_scsi "sudo lshw -class scsi" lshw -class scsi
capture_cmd lshw_display "sudo lshw -class display" lshw -class display
capture_cmd lshw_network "sudo lshw -class network" lshw -class network
capture_cmd lspci_vvv "sudo lspci -vvv" lspci -vvv
capture_cmd dmesg "sudo dmesg" dmesg
UNAME_FILE=$(capture_cmd uname "uname -a" uname -a)

export COMMAND_MANIFEST="$MANIFEST" UNAME_FILE

python3 - "$OUTPUT_PATH" <<'PY'
import json
import os
import pathlib
import platform
import sys
from datetime import datetime, timezone

def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except FileNotFoundError:
        return "(Datei nicht gefunden)"

manifest_path = pathlib.Path(os.environ["COMMAND_MANIFEST"])
commands = []
for line in manifest_path.read_text(encoding="utf-8").splitlines():
    parts = line.split("\t", 2)
    if len(parts) != 3:
        continue
    key, display, path = parts
    content = read_text(pathlib.Path(path))
    commands.append(
        {
            "id": key,
            "command": display,
            "output": content,
            "output_lines": content.splitlines(),
        }
    )

kernel_path = os.environ.get("UNAME_FILE")
kernel = read_text(pathlib.Path(kernel_path)) if kernel_path else "(keine Daten)"

payload = {
    "meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hostname": platform.node(),
        "kernel": kernel,
    },
    "commands": commands,
}

output_path = pathlib.Path(sys.argv[1])
output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(f"Report gespeichert: {output_path}")
PY

echo "Systemreport erfolgreich erzeugt: $OUTPUT_PATH"
