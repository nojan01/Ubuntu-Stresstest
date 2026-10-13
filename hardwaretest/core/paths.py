"""Laufzeitpfade für Quelltext-, DEB- und AppImage-Ausführung."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def application_root() -> Path:
    """Liefert den Root für mitgelieferte Ressourcen.

    PyInstaller legt Daten im Ein-Verzeichnis-Build unter ``sys._MEIPASS`` ab.
    Im Entwicklungsbetrieb ist der Repository-Root zwei Ebenen oberhalb dieses
    Moduls der entsprechende Ressourcen-Root.
    """
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """Liefert einen absoluten Pfad innerhalb der mitgelieferten Ressourcen."""
    return application_root().joinpath(*parts)


def self_command_for_root() -> list[str]:
    """Programmaufruf für Worker, die per pkexec als root laufen.

    Ein AppImage ist per FUSE nur für den startenden Benutzer eingehängt; root
    kann darin nichts lesen.  Deshalb startet root das AppImage selbst über
    ``--appimage-extract-and-run`` statt der Binärdatei im FUSE-Mount.
    """
    appimage = os.environ.get("APPIMAGE")
    if getattr(sys, "frozen", False) and appimage and os.path.isfile(appimage):
        return [appimage, "--appimage-extract-and-run"]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "hardwaretest"]
