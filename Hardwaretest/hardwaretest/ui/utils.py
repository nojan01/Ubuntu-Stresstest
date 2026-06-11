"""Utility helpers for UI actions."""

from __future__ import annotations

import shutil
from typing import Sequence, Tuple

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
    ("xterm", ("-e",)),
)


def launch_command_in_terminal(command: Sequence[str], hold: bool = False) -> bool:
    """Try to run command inside a fresh terminal window."""

    for binary, args in TERMINAL_CANDIDATES:
        resolved = shutil.which(binary)
        if not resolved:
            continue
        terminal_args = list(args)
        if hold and binary in {
            "x-terminal-emulator",
            "gnome-terminal",
            "kgx",
            "xfce4-terminal",
            "mate-terminal",
            "konsole",
            "tilix",
            "terminology",
        }:
            if binary in {"x-terminal-emulator", "terminology"}:
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
