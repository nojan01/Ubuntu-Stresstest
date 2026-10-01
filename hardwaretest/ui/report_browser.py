"""Open HTML reports in a browser, independently of the text/html association."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, QUrl
from PySide6.QtWidgets import QMessageBox

from hardwaretest.ui.i18n import language_manager


BROWSERS = ("firefox", "google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge", "vivaldi")


def browser_candidates():
    preferred = ""
    try:
        result = subprocess.run(["xdg-settings", "get", "default-web-browser"],
                                capture_output=True, text=True, timeout=2)
        desktop = result.stdout.strip().lower()
        preferred = next((name for name in BROWSERS if desktop in {
            name + ".desktop", "org.mozilla.firefox.desktop" if name == "firefox" else name + ".desktop",
            "org.chromium.chromium.desktop" if name == "chromium" else name + ".desktop",
        }), "")
    except (OSError, subprocess.SubprocessError):
        pass
    return tuple(dict.fromkeys(([preferred] if preferred else []) + list(BROWSERS)))


def browser_environment():
    # PyInstaller's bundled Qt libraries must not be injected into host browsers.
    env = os.environ.copy()
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        env.pop(name, None)
    return env


def start_browser(executable, url, directory):
    process = QProcess()
    process.setProgram(executable)
    process.setArguments([url])
    process.setWorkingDirectory(str(directory))
    env = QProcessEnvironment()
    for key, value in browser_environment().items():
        env.insert(key, value)
    process.setProcessEnvironment(env)
    result = process.startDetached()
    return bool(result[0]) if isinstance(result, tuple) else bool(result)


def open_html_report(path, parent=None):
    path = Path(path).expanduser().resolve()
    if path.is_file():
        url = QUrl.fromLocalFile(str(path)).toString()
        for browser in browser_candidates():
            executable = shutil.which(browser)
            if executable and start_browser(executable, url, path.parent):
                return True
    title = "HTML-Protokoll konnte nicht geöffnet werden"
    message = ("Bitte die Datei manuell in einem Browser öffnen:\n{path}" if path.is_file()
               else "HTML-Protokoll nicht gefunden")
    QMessageBox.warning(parent, language_manager.tr(title), language_manager.tr(message, path=path))
    return False
