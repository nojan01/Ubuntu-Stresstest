"""Utility helpers for UI actions."""

from __future__ import annotations

import os
import shutil
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QProcess

TerminalCandidate = Tuple[str, Sequence[str]]

TERMINAL_CANDIDATES: Sequence[TerminalCandidate] = (
    ("x-terminal-emulator", ("-e",)),
    ("gnome-terminal", ("--",)),
    ("kgx", ("--",)),
    ("konsole", ("-e",)),
    ("xfce4-terminal", ("-e",)),
    ("mate-terminal", ("-e",)),
    ("tilix", ("-e",)),
    ("terminology", ("-e",)),
    # Puppy Linux / leichtgewichtige Desktops
    ("urxvt", ("-e",)),
    ("rxvt-unicode", ("-e",)),
    ("rxvt", ("-e",)),
    ("lxterminal", ("-e",)),
    ("sakura", ("-e",)),
    ("st", ("-e",)),
    ("xterm", ("-e",)),
)


def launch_command_in_terminal(
    command: Sequence[str],
    hold: bool = False,
    geometry: Optional[Tuple[int, int]] = None,
) -> bool:
    """Try to run command inside a fresh terminal window.

    *geometry* ist ein optionales ``(columns, rows)``-Tupel, das die
    gewuenschte Fenstergroesse in Zeichen vorgibt (z.B. ``(110, 44)``).
    Nicht jedes Terminal unterstuetzt das; dort wird die Angabe ignoriert.
    """

    candidates: List[TerminalCandidate] = list(TERMINAL_CANDIDATES)
    if geometry is not None:
        # ``x-terminal-emulator`` ist nur ein update-alternatives-Alias. Sein
        # Wrapper reicht ``--geometry`` nicht zuverlaessig an das echte Terminal
        # weiter, daher ans Ende stellen und konkrete Terminals (gnome-terminal,
        # xterm, ...) bevorzugen, die die Geometrie korrekt umsetzen.
        candidates.sort(key=lambda c: c[0] == "x-terminal-emulator")

    for binary, args in candidates:
        resolved = shutil.which(binary)
        if not resolved:
            continue
        terminal_args: List[str] = []
        if geometry is not None:
            terminal_args += _geometry_args(binary, geometry[0], geometry[1])
        terminal_args += list(args)
        if hold and binary in {
            "x-terminal-emulator",
            "gnome-terminal",
            "kgx",
            "xfce4-terminal",
            "mate-terminal",
            "konsole",
            "tilix",
            "terminology",
            "urxvt",
            "rxvt-unicode",
            "rxvt",
            "lxterminal",
            "sakura",
            "st",
        }:
            if binary in {"x-terminal-emulator", "terminology", "rxvt", "rxvt-unicode", "urxvt", "st"}:
                terminal_args += [_wrap_hold_single_string(command)]
            elif "gnome" in binary or binary in {"kgx"}:
                terminal_args += ["--", "bash", "-lc", _wrap_hold_command(command)]
            else:
                terminal_args += ["bash", "-lc", _wrap_hold_command(command)]
        else:
            terminal_args += list(command)
        if QProcess.startDetached(resolved, terminal_args):
            return True
    return False


def _geometry_args(binary: str, cols: int, rows: int) -> List[str]:
    """Liefert die terminal-spezifischen Argumente fuer eine Fenstergroesse.

    Die Groesse wird in Zeichen (Spalten x Zeilen) angegeben. Terminals, die
    keine zeichenbasierte Geometrie kennen, liefern eine leere Liste – die
    Groessenvorgabe wird dann ignoriert.
    """
    geo = f"{cols}x{rows}"
    # GTK-basierte Terminals: --geometry=COLSxROWS
    if binary in {
        "gnome-terminal", "xfce4-terminal", "mate-terminal",
        "lxterminal", "tilix",
    }:
        return [f"--geometry={geo}"]
    # X11-Klassiker: -geometry COLSxROWS
    if binary in {"xterm", "urxvt", "rxvt-unicode", "rxvt"}:
        return ["-geometry", geo]
    # st / terminology: -g COLSxROWS
    if binary in {"st", "terminology"}:
        return ["-g", geo]
    # sakura nutzt getrennte Optionen
    if binary == "sakura":
        return [f"--columns={cols}", f"--rows={rows}"]
    # konsole/kgx/x-terminal-emulator: keine zeichenbasierte Geometrie
    return []


def _wrap_hold_command(command: Sequence[str]) -> str:
    cmd = " ".join(shlex_quote(part) for part in command)
    return f"{cmd}; echo '--- Vorgang beendet, Fenster mit Enter schliessen ---'; read"


def _wrap_hold_single_string(command: Sequence[str]) -> str:
    inner = _wrap_hold_command(command).replace('"', '\"')
    return f"bash -lc \"{inner}\""


def shlex_quote(value: str) -> str:
    if not value:
        return "''"
    if all(c.isalnum() or c in "@%+=:,./-" for c in value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


# ── Kernel-Log Helpers ────────────────────────────────────────────────────
# Puppy Linux (und andere Nicht-systemd-Distros) haben kein journalctl.
# Fallback-Kette:
#   1. journalctl -kf          (systemd)
#   2. dmesg -w                (Linux >=3.5, kein systemd noetig)
#   3. tail -f /var/log/kern.log
#   4. tail -f /var/log/messages

_KLOG_PATHS = ["/var/log/messages", "/var/log/kern.log"]


def _has_journalctl() -> bool:
    """Return True if journalctl is usable (systemd as init AND journal files exist)."""
    if shutil.which("journalctl") is None:
        return False
    # journalctl binary may exist on Debian-based Puppy Linux but
    # systemd-journald is not running → "No journal files were found".
    # Quick check: is PID 1 actually systemd?
    try:
        pid1 = os.path.basename(os.readlink("/proc/1/exe"))
        if "systemd" not in pid1:
            return False
    except (OSError, PermissionError):
        pass
    # Extra safety: check that journal directories exist
    return os.path.isdir("/run/log/journal") or os.path.isdir("/var/log/journal")


def build_klog_command() -> List[str]:
    """Return the best available command to follow kernel logs live."""
    if _has_journalctl():
        return ["journalctl", "-kf"]
    # dmesg -w (follow mode) – verfuegbar ab Linux 3.5
    if shutil.which("dmesg"):
        return ["dmesg", "-w"]
    # Fallback: tail -f auf Log-Datei
    for logpath in _KLOG_PATHS:
        if os.path.isfile(logpath):
            return ["tail", "-f", logpath]
    # Absoluter Fallback
    return ["dmesg"]


def build_mcelog_command() -> List[str]:
    """Return the best available command to follow MCE kernel messages live."""
    if _has_journalctl():
        return ["journalctl", "-kf", "-g", "MCE"]
    # Ohne systemd: dmesg -w | grep oder tail -f | grep
    # Muss als bash -c laufen wegen der Pipe
    if shutil.which("dmesg"):
        return ["bash", "-c", "dmesg -w | grep --line-buffered -i MCE"]
    for logpath in _KLOG_PATHS:
        if os.path.isfile(logpath):
            return ["bash", "-c", f"tail -f {shlex_quote(logpath)} | grep --line-buffered -i MCE"]
    return ["bash", "-c", "dmesg | grep -i MCE"]
