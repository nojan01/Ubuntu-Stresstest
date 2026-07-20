"""Tests fuer mprime Konfigurations-Backup-Pfade."""

from __future__ import annotations

from pathlib import Path

from hardwaretest.tests.mprime import MprimeRunner


def test_load_config_uses_correct_backup_name(tmp_path: Path):
    # MprimeRunner._load_config soll prime.txt.bak (nicht prime.bak) suchen,
    # falls prime.txt fehlt.
    cfg = tmp_path / "prime.txt"
    sample = tmp_path / "prime.txt.bak"
    sample.write_text("StressTester=1\nUsePrimenet=0\n")
    assert not cfg.exists()
    data = MprimeRunner._load_config(cfg)
    # Beim Laden ohne existierende cfg: leeres Dict, aber Sample wird kopiert
    assert cfg.exists()
    assert data == {}


def test_load_config_creates_hwbackup(tmp_path: Path):
    cfg = tmp_path / "prime.txt"
    cfg.write_text("StressTester=1\nUsePrimenet=0\n")
    MprimeRunner._load_config(cfg)
    backup = tmp_path / "prime.txt.hwbackup"
    assert backup.exists()
    assert backup.read_text() == cfg.read_text()


def test_load_config_parses_keys(tmp_path: Path):
    cfg = tmp_path / "prime.txt"
    cfg.write_text("[Internals]\nStressTester=1\nTortureTest=2\n")
    data = MprimeRunner._load_config(cfg)
    assert data["StressTester"] == "1"
    assert data["TortureTest"] == "2"
    assert data["__section__Internals"] == "[Internals]"
