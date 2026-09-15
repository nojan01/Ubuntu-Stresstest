"""Laufzeitpfade für Quelltext-, DEB- und AppImage-Ausführung."""

from __future__ import annotations

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
