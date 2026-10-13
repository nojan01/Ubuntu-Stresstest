import sys

from hardwaretest.core import paths


def test_self_command_uses_appimage_when_frozen(monkeypatch, tmp_path):
    image = tmp_path / "x.AppImage"
    image.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPIMAGE", str(image))
    assert paths.self_command_for_root() == [str(image), "--appimage-extract-and-run"]


def test_self_command_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert paths.self_command_for_root()[1:] == ["-m", "hardwaretest"]
