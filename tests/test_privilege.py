"""Tests für die zentrale Rechteerhöhung (sudo -A mit eigenem Askpass-Dialog)."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from hardwaretest.core import privilege


@pytest.fixture
def which(monkeypatch):
    table: dict[str, str] = {}
    monkeypatch.setattr(privilege.shutil, "which", lambda name: table.get(name))
    return table


def test_root_needs_no_prefix(monkeypatch, which):
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 0)
    assert privilege.command_prefix("x") == []
    assert privilege.available() is True


def test_sudo_preferred_with_askpass(monkeypatch, which, tmp_path):
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    which.update({"sudo": "/usr/bin/sudo", "pkexec": "/usr/bin/pkexec"})
    prefix = privilege.command_prefix("Swap 100% deaktivieren")
    assert prefix == ["/usr/bin/sudo", "-A", "-p", "Hardwaretest: Swap 100%% deaktivieren"]
    askpass = os.environ["SUDO_ASKPASS"]
    assert askpass.startswith(str(tmp_path))
    mode = os.stat(askpass).st_mode
    assert mode & stat.S_IXUSR and not mode & (stat.S_IRWXG | stat.S_IRWXO)
    content = Path(askpass).read_text(encoding="utf-8")
    assert content.startswith("#!/bin/sh\nexec ") and content.rstrip().endswith('--askpass "$@"')


def test_pkexec_fallback_without_sudo(monkeypatch, which):
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 1000)
    which["pkexec"] = "/usr/bin/pkexec"
    assert privilege.command_prefix("x") == ["/usr/bin/pkexec"]
    which.clear()
    assert privilege.command_prefix("x") == []
    assert privilege.available() is False
    assert "sudo" in privilege.missing_text() and "sudo" in privilege.missing_text("en")


def test_wrap_and_prompt_text(monkeypatch, which):
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 1000)
    which["pkexec"] = "/usr/bin/pkexec"
    assert privilege.wrap(["swapoff", "-a"], "Swap") == ["/usr/bin/pkexec", "swapoff", "-a"]
    assert privilege.prompt_text("") == "Hardwaretest"
    assert privilege.prompt_text("Test") == "Hardwaretest: Test"


def test_askpass_subcommand_cancel_returns_1():
    """``hardwaretest --askpass`` ohne Anzeige: Abbruch liefert Exit 1 und kein Passwort."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    code = (
        "import sys\n"
        "from PySide6.QtCore import QTimer\n"
        "from PySide6.QtWidgets import QApplication, QInputDialog\n"
        "app = QApplication([])\n"
        "def cancel():\n"
        "    for w in app.topLevelWidgets():\n"
        "        if isinstance(w, QInputDialog):\n"
        "            w.reject()\n"
        "QTimer.singleShot(300, cancel)\n"
        "from hardwaretest.core.privilege import askpass_main\n"
        "sys.exit(askpass_main(['Hardwaretest: Test']))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 1, result.stderr
    assert result.stdout == ""


def test_parent_geometry_roundtrip(monkeypatch):
    monkeypatch.delenv(privilege.PARENT_GEOMETRY_ENV, raising=False)
    assert privilege.parent_geometry() is None
    privilege.publish_parent_geometry(100, 200, 800, 600)
    assert privilege.parent_geometry() == (100, 200, 800, 600)
    assert privilege.parent_geometry({privilege.PARENT_GEOMETRY_ENV: "1,2,0,5"}) is None
    assert privilege.parent_geometry({privilege.PARENT_GEOMETRY_ENV: "kaputt"}) is None
