"""Prozessweite Benachrichtigungen über Änderungen am Systemzustand (z. B. Swap).

Mehrere Tabs zeigen den Swap-Zustand an oder bieten „Swap deaktivieren“ an.
Schaltet ein Tab den Swap um, erfahren die anderen das über ``swap_changed``
und lesen ihre Systemwerte neu ein.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class _SystemEvents(QObject):
    swap_changed = Signal()


system_events = _SystemEvents()
