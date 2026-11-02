"""Zentrale Rechteerhöhung mit einheitlichem Passwortdialog.

Alle Root-Aktionen der App laufen über ``sudo -A``: sudo ruft den eigenen
Askpass-Helfer (``hardwaretest --askpass``) auf, der denselben Qt-Dialog wie
„Swap deaktivieren“ zeigt. sudo merkt sich die Freigabe (Standard 15 min), so
dass nicht jede Aktion erneut fragt. Ohne sudo bleibt ``pkexec`` als Fallback;
als root ist kein Präfix nötig.
"""
from __future__ import annotations

import os
import shlex
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Optional

from hardwaretest.core.paths import self_command_for_user

PROMPT_PREFIX = "Hardwaretest: "
_ASKPASS_NAME = "hardwaretest-askpass"


def is_root() -> bool:
    return os.geteuid() == 0


def tool() -> Optional[str]:
    """Pfad des Werkzeugs für die Rechteerhöhung (sudo bevorzugt), ``None`` wenn keins vorhanden."""
    return shutil.which("sudo") or shutil.which("pkexec")


def available() -> bool:
    """True, wenn Root-Aktionen möglich sind (root, sudo oder pkexec)."""
    return is_root() or tool() is not None


def missing_text(language: str = "de") -> str:
    if language == "en":
        return "Neither sudo nor pkexec found; administrator authentication unavailable."
    return "Weder sudo noch pkexec gefunden; Administratoranmeldung nicht verfügbar."


def _runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime)
    return Path(tempfile.gettempdir())


def askpass_path() -> Path:
    """Schreibt (einmalig) das Askpass-Skript und liefert seinen Pfad.

    ``SUDO_ASKPASS`` erlaubt nur einen Programmpfad ohne Argumente, daher ein
    kleines Shell-Skript, das die App im Askpass-Modus startet.
    """
    path = _runtime_dir() / f"{_ASKPASS_NAME}-{os.getuid()}.sh"
    command = " ".join(shlex.quote(part) for part in self_command_for_user())
    content = f'#!/bin/sh\nexec {command} --askpass "$@"\n'
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
        path.chmod(stat.S_IRWXU)
    except OSError:
        pass
    return path


def ensure_askpass_env() -> None:
    """Setzt ``SUDO_ASKPASS`` für alle Kindprozesse auf den eigenen Dialog."""
    os.environ["SUDO_ASKPASS"] = str(askpass_path())


def prompt_text(reason: str) -> str:
    # sudo interpretiert % in der Eingabeaufforderung als Platzhalter.
    return (PROMPT_PREFIX + reason.replace("%", "%%")) if reason else PROMPT_PREFIX.rstrip(": ")


def command_prefix(reason: str = "") -> list[str]:
    """Präfix für einen Root-Befehl: ``[]`` als root, sonst sudo -A (Fallback pkexec).

    ``reason`` erscheint als Titel des Passwortdialogs (z. B. „Root-Sitzung starten“).
    """
    if is_root():
        return []
    sudo = shutil.which("sudo")
    if sudo:
        ensure_askpass_env()
        return [sudo, "-A", "-p", prompt_text(reason)]
    pkexec = shutil.which("pkexec")
    if pkexec:
        return [pkexec]
    return []


def wrap(command: list[str], reason: str = "") -> list[str]:
    return [*command_prefix(reason), *command]


def askpass_main(argv: list[str]) -> int:
    """``hardwaretest --askpass [PROMPT]``: Passwortdialog für sudo, Passwort auf stdout."""
    import sys

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit

    from hardwaretest.main import _apply_dark_theme, _sanitize_qt_environment
    from hardwaretest.ui.i18n import language_manager

    _sanitize_qt_environment()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _apply_dark_theme(app)
    prompt = argv[0] if argv else ""
    title = prompt[len(PROMPT_PREFIX):] if prompt.startswith(PROMPT_PREFIX) else (prompt or "Hardwaretest")
    label = language_manager.tr("sudo-Passwort eingeben:")
    dialog = QInputDialog()
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    if dialog.exec() != QInputDialog.DialogCode.Accepted or not dialog.textValue():
        return 1
    sys.stdout.write(dialog.textValue() + "\n")
    sys.stdout.flush()
    return 0
