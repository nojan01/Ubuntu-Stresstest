"""Gemeinsames Lebenszyklus-Muster für QThread-Worker der Panels.

Referenzmuster (siehe ``main_window._clear_update_thread``): Der Worker hat das
Panel als Eltern, Ergebnis-/Fehlerslots verarbeiten nur Nutzdaten; erst der an
``QThread.finished`` gebundene Cleanup wartet den Thread ab (``wait()``), gibt
das Qt-Objekt per ``deleteLater()`` frei und löscht die Panel-Referenz – aber
nur, wenn sie noch auf genau diesen Worker zeigt.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread


def connect_finished(worker: QThread, slot: Callable[[QThread], Any]) -> None:
    """``finished`` so verbinden, dass der Slot den konkreten Worker erhält."""
    worker.finished.connect(lambda _worker=worker: slot(_worker))


def release_worker(owner: object, attr: str, worker: QThread | None) -> bool:
    """Aus dem ``finished``-Slot aufrufen.

    Joint den Thread, ruft ``deleteLater()`` auf und setzt ``owner.<attr>`` auf
    None, falls dort noch dieser Worker hängt. Gibt zurück, ob die Referenz
    gelöscht wurde (False bei einem bereits ersetzten oder fremden Worker).
    """
    if worker is None:
        worker = getattr(owner, attr, None)
    if worker is None:
        return False
    # finished wird noch aus dem Thread gesendet; erst nach wait() ist er beendet.
    worker.wait()
    # Vom Panel lösen, bevor deleteLater() greift: Wird das Panel zerstört, solange
    # das DeferredDelete-Ereignis noch aussteht, löscht Qt den Worker als Kind
    # mit und PySide stürzt beim späteren Abarbeiten des Ereignisses ab.
    worker.setParent(None)
    worker.deleteLater()
    if getattr(owner, attr, None) is worker:
        setattr(owner, attr, None)
        return True
    return False
