"""Hardware test GUI package."""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("hardwaretest-gui")
except metadata.PackageNotFoundError:  # pragma: no cover - läuft aus Quelltext ohne Installation
    __version__ = "0.2.22"

__all__ = ["__version__"]
