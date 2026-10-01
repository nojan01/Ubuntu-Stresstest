from __future__ import annotations

import sys
from pathlib import Path

from hardwaretest import __version__


def _portable_self_check() -> int:
    """Prüft die eingebettete Python-Laufzeit und wichtige Ressourcen."""
    import PySide6
    import psutil
    from PySide6 import QtCore

    from hardwaretest.core.paths import resource_path

    resources = {
        "Hilfedateien": resource_path("docs"),
        "Profile": resource_path("profiles"),
        "Systembericht": resource_path("scripts", "collect_system_report.sh"),
        "RAM-Testhelfer": resource_path("hardwaretest", "tests", "memory_fill_script.py"),
    }
    failed = False
    print(f"Hardwaretest {__version__}")
    print(f"Python: {sys.version.split()[0]} (eingebettet: {'ja' if getattr(sys, 'frozen', False) else 'nein'})")
    print(f"PySide6/Qt: {PySide6.__version__} / {QtCore.qVersion()}")
    print(f"psutil: {psutil.__version__}")
    for label, path in resources.items():
        present = Path(path).exists()
        failed |= not present
        print(f"{label}: {'OK' if present else 'FEHLT'} ({path})")
    return 1 if failed else 0


def main() -> int:
    if sys.argv[1:2] == ["--zfs-worker"]:
        from hardwaretest.core.zfs import worker_main

        return worker_main(sys.argv[2:])
    if sys.argv[1:2] == ["--filesystem-worker"]:
        from hardwaretest.core.filesystem_check import worker_main

        return worker_main(sys.argv[2:])
    if sys.argv[1:2] == ["--memory-fill-worker"]:
        from hardwaretest.tests.memory_fill_script import main as memory_fill_main

        return memory_fill_main(sys.argv[2:])
    if "--version" in sys.argv[1:]:
        print(f"Hardwaretest {__version__}")
        return 0
    if "--self-check" in sys.argv[1:]:
        return _portable_self_check()

    # Der verzögerte Import hält den Versionscheck auch im gebündelten Build
    # klein und testet trotzdem, dass das eigentliche GUI-Modul enthalten ist.
    from hardwaretest.main import run

    return run()

if __name__ == "__main__":
    raise SystemExit(main())
