from pathlib import Path
import sys

from hardwaretest.core.paths import application_root, resource_path


def test_source_application_root_contains_project_files():
    assert (application_root() / "hardwaretest").is_dir()
    assert resource_path("LICENSE").is_file()


def test_frozen_application_root_uses_meipass(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert application_root() == tmp_path
    assert resource_path("vendor", "prime95") == tmp_path / "vendor" / "prime95"
