"""Entry point for running the Qt application."""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from hardwaretest.ui.main_window import MainWindow


def run() -> int:
    _sanitize_qt_environment()
    app = QApplication(sys.argv)
    _apply_dark_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


def _apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    base = QColor(30, 30, 30)
    alt_base = QColor(45, 45, 45)
    text = QColor(220, 220, 220)
    highlight = QColor(64, 128, 255)
    palette.setColor(QPalette.ColorRole.Window, base)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, alt_base)
    palette.setColor(QPalette.ColorRole.AlternateBase, base.darker(115))
    palette.setColor(QPalette.ColorRole.ToolTipBase, text)
    palette.setColor(QPalette.ColorRole.ToolTipText, base)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, alt_base)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Link, highlight)
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 128, 128))
    app.setPalette(palette)


def _sanitize_qt_environment() -> None:
    # Some desktop environments export overrides like QT_STYLE_OVERRIDE=gtk2, which
    # modern Qt builds no longer provide. Removing them avoids noisy warnings.
    for var in ("QT_STYLE_OVERRIDE", "QT_QPA_PLATFORMTHEME"):
        if os.environ.get(var):
            os.environ.pop(var)

    # Ensure a sensible QT_QPA_PLATFORM default.
    # On Wayland sessions (WAYLAND_DISPLAY set) Qt6 auto-detects the backend.
    # On pure X11 sessions (common on Puppy Linux / TrixiePup64 Retro, JWM,
    # FVWM, etc.) we pin to "xcb" so that PySide6 doesn't attempt to load
    # a Wayland plugin that may not be installed.
    if not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("WAYLAND_DISPLAY"):
            # Wayland session – let Qt try wayland first, fall back to xcb
            os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
        else:
            os.environ["QT_QPA_PLATFORM"] = "xcb"


if __name__ == "__main__":
    raise SystemExit(run())
