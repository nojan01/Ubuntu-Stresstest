"""Tests fuer die Fehler-Pattern-Erkennung im BaseTestRunner."""

from __future__ import annotations

from hardwaretest.core import test_runner


def _matches(line: str) -> bool:
    return any(p.search(line) for p in test_runner._FAILURE_PATTERNS)


def test_english_keywords_match():
    assert _matches("FATAL: hardware error detected")
    assert _matches("crc32c: verify failed at offset 1234")
    assert _matches("I/O error on /dev/sda")
    assert _matches("Unrecovered read error")


def test_german_keywords_match():
    assert _matches("ERGEBNIS: FEHLER GEFUNDEN – 3 Verifikationsfehler")
    assert _matches("Verifikationsfehler an Offset 4096")
    assert _matches("Speicherfehler erkannt")


def test_passing_lines_do_not_match():
    assert not _matches("Verify OK")
    assert not _matches("=== Fuelltest beendet ===")
    assert not _matches("ERGEBNIS: BESTANDEN – Keine Fehler gefunden")
    assert not _matches("Zyklus 1 abgeschlossen: OK | Gesamtfehler: 0")


def test_terminated_exit_codes_constant():
    # Stelle sicher, dass typische Termination-Codes erkannt werden,
    # damit ein vom Benutzer gestoppter Test nicht als Fehler gilt.
    assert -15 in test_runner._TERMINATED_EXIT_CODES  # SIGTERM
    assert -9 in test_runner._TERMINATED_EXIT_CODES   # SIGKILL
    assert 143 in test_runner._TERMINATED_EXIT_CODES  # 128+15
    assert 137 in test_runner._TERMINATED_EXIT_CODES  # 128+9
