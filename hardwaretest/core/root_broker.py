"""Langlebige Root-Sitzung über eine einzige pkexec-Autorisierung.

Der Root-Prozess nimmt zeilenweise nur freigegebene Werkzeuge (fio, nvme mit
lesenden/Selbsttest-Unterbefehlen, dcgmi, lesendes ipmitool, dmidecode, IML per ilorest) entgegen.  Jeder Auftrag schreibt
in eine root-eigene FIFO; ein unprivilegierter Relay-Prozess liest sie und
liefert den Exit-Code des Werkzeugs.  Schliesst die App die Sitzung (oder
stirbt sie), beendet der Root-Prozess alle laufenden Aufträge.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
from typing import Optional, Sequence

READY_MARKER = "@@HARDWARETEST-ROOT-READY@@"

ROOT_BROKER_SCRIPT = r"""
umask 022
jobs=""
cleanup() {
  for d in $jobs; do [ -f "$d/pid" ] && kill -TERM "$(cat "$d/pid")" 2>/dev/null; done
  for d in $jobs; do rm -rf "$d"; done
}
trap cleanup EXIT
echo "@@HARDWARETEST-ROOT-READY@@"
while IFS= read -r line; do
  set -f; set -- $line; set +f
  op=${1:-}; [ $# -gt 0 ] && shift
  case "$op" in
  RUN)
    ok=1
    case "${1:-}" in
      fio|dcgmi) ;;
      nvme) case "${2:-}" in smart-log|self-test-log|device-self-test) ;; *) ok=0;; esac ;;
      ipmitool) case "${2:-} ${3:-}" in "sdr type"|"sdr elist"|"sel list"|"sel elist"|"sel info"|"mc info") ;; *) ok=0;; esac ;;
      dmidecode) [ $# -eq 3 ] && [ "$2" = "-t" ] && case "$3" in memory|system) true;; *) false;; esac || ok=0 ;;
      ilorest) [ "$*" = "ilorest --nologo serverlogs --selectlog=IML --json" ] || ok=0 ;;
      modprobe) [ $# -eq 3 ] && [ "$2" = "-q" ] && case "$3" in ipmi_si|ipmi_devintf) true;; *) false;; esac || ok=0 ;;
      *) ok=0 ;;
    esac
    for a in "$@"; do case "$a" in *[!A-Za-z0-9/_=.,:%+-]*) ok=0;; esac; done
    if [ "$ok" != 1 ]; then echo "@@ERR Befehl nicht erlaubt"; continue; fi
    d=$(mktemp -d /tmp/hardwaretest-root.XXXXXXXX) || { echo "@@ERR mktemp"; continue; }
    chmod 711 "$d"; mkfifo -m 644 "$d/out"; : >"$d/err"
    jobs="$jobs $d"
    ( exec >"$d/out" 2>"$d/err" </dev/null; "$@" & p=$!; echo "$p" >"$d/pid"; wait "$p"; echo "$?" >"$d/status" ) &
    echo "@@JOB $d"
    ;;
  KILL)
    case " $jobs " in *" ${1:-} "*) [ -f "$1/pid" ] && kill -TERM "$(cat "$1/pid")" 2>/dev/null ;; esac ;;
  DONE)
    case " $jobs " in
      *" ${1:-} "*)
        rm -rf "$1"
        rest=""; for d in $jobs; do [ "$d" = "$1" ] || rest="$rest $d"; done; jobs=$rest ;;
    esac ;;
  esac
done
"""

_RELAY_SCRIPT = (
    'cat "$1/out"; cat "$1/err" >&2; '
    'rc=$(cat "$1/status" 2>/dev/null); exit "${rc:-1}"'
)


class RootAuthorizationError(RuntimeError):
    """Die pkexec-Autorisierung wurde abgebrochen oder abgelehnt."""


class RootBroker:
    def __init__(self, pkexec_path: str = "pkexec") -> None:
        self._pkexec_path = pkexec_path
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()

    def is_active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Fragt einmal das Passwort ab; blockiert bis zur Autorisierung."""
        with self._lock:
            if self.is_active():
                return
            self._proc = subprocess.Popen(
                [self._pkexec_path, "sh", "-c", ROOT_BROKER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            assert self._proc.stdout is not None
            line = self._proc.stdout.readline()
            if line.strip() != READY_MARKER:
                self._close_locked()
                raise RootAuthorizationError(
                    "Root-Berechtigung wurde abgebrochen oder abgelehnt."
                )

    def _send(self, text: str) -> str:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None or proc.stdout is None:
            raise RootAuthorizationError("Root-Sitzung ist nicht aktiv.")
        try:
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RootAuthorizationError("Root-Sitzung wurde beendet.") from exc
        return ""

    def submit(self, command: Sequence[str]) -> str:
        args = [str(item) for item in command]
        if not args or any(not item or any(ch.isspace() for ch in item) for item in args):
            raise RuntimeError("Ungültiges Argument für die Root-Sitzung")
        self.start()
        with self._lock:
            self._send("RUN " + " ".join(args))
            assert self._proc is not None and self._proc.stdout is not None
            reply = self._proc.stdout.readline().strip()
        if reply.startswith("@@JOB "):
            return reply[len("@@JOB "):]
        if not reply:
            raise RootAuthorizationError("Root-Sitzung wurde beendet.")
        raise RuntimeError(reply.removeprefix("@@ERR ").strip() or "Root-Auftrag fehlgeschlagen")

    @staticmethod
    def relay_command(job: str) -> list[str]:
        return ["sh", "-c", _RELAY_SCRIPT, "hardwaretest-relay", job]

    def kill(self, job: str) -> None:
        with self._lock, contextlib.suppress(RootAuthorizationError):
            self._send(f"KILL {job}")

    def done(self, job: str) -> None:
        with self._lock, contextlib.suppress(RootAuthorizationError):
            self._send(f"DONE {job}")

    def run(self, command: Sequence[str], timeout: Optional[float] = None,
            **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """subprocess.run-kompatibel (capture_output/text) über die Root-Sitzung."""
        job = self.submit(command)
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

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
