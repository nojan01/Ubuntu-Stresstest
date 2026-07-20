"""Tests fuer Pattern-Builder und Verify-Logik im memory_fill_script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Modul direkt aus Datei laden, da es als CLI-Skript existiert.
SCRIPT = Path(__file__).resolve().parent.parent / "hardwaretest" / "tests" / "memory_fill_script.py"
spec = importlib.util.spec_from_file_location("memfill_script", SCRIPT)
mfs = importlib.util.module_from_spec(spec)
sys.modules["memfill_script"] = mfs
assert spec.loader is not None
spec.loader.exec_module(mfs)


def test_const_byte_builder_full_size():
    b = mfs._const_byte_builder(0xAA)
    out = b(0, mfs._BUF_SIZE)
    assert len(out) == mfs._BUF_SIZE
    assert out[:4] == b"\xaa\xaa\xaa\xaa"
    assert out == bytes([0xAA]) * mfs._BUF_SIZE


def test_const_byte_builder_partial():
    b = mfs._const_byte_builder(0x55)
    out = b(0, 17)
    assert out == bytes([0x55]) * 17


def test_const_word_builder_endianness():
    b = mfs._const_word_builder(0x0123456789ABCDEF)
    out = b(0, 8)
    assert out == b"\xef\xcd\xab\x89\x67\x45\x23\x01"


def test_random_builder_reproducible():
    b1 = mfs._random_builder(42)
    b2 = mfs._random_builder(42)
    assert b1(0, 1024) == b2(0, 1024)


def test_address_builder_first_page_zero():
    b = mfs._address_builder()
    out = b(0, 16)
    # Erste Page ab Offset 0 -> Wert 0 in jedem Qword
    assert out == b"\x00" * 16


def test_address_builder_second_page_value():
    b = mfs._address_builder()
    # Page 1 startet bei Offset 4096 -> Wert 4096
    out = b(4096, 8)
    assert int.from_bytes(out, "little") == 4096


def test_address_builder_unaligned_offset():
    b = mfs._address_builder()
    # Offset 4090 liegt in Page 0 (Wert 0), letzte 6 Bytes der ersten Page
    # plus 2 Bytes der zweiten Page (Wert 4096 LE = 00 10 00 00 ...)
    out = b(4090, 8)
    assert len(out) == 8
    assert out[:6] == b"\x00" * 6
    # naechste 2 Bytes = ersten 2 Bytes von 4096-LE
    assert out[6:8] == (4096).to_bytes(8, "little")[:2]


def test_pattern_plan_quick_includes_fixed_and_random():
    plan = mfs._build_pattern_plan(quick=True)
    names = [n for n, _ in plan]
    assert "0xAA" in names
    assert "0x55" in names
    assert "Address" in names
    assert any("Random" in n for n in names)


def test_pattern_plan_full_includes_walking_and_random():
    plan = mfs._build_pattern_plan(quick=False)
    names = [n for n, _ in plan]
    assert any(n.startswith("WalkingOne") for n in names)
    assert any(n.startswith("WalkingZero") for n in names)
    assert any("Random" in n for n in names)
    assert any("Address" in n for n in names)


def test_pattern_plan_changes_between_cycles():
    """Muster muessen sich von Zyklus zu Zyklus aendern."""
    names1 = [n for n, _ in mfs._build_pattern_plan(quick=False, cycle=1)]
    names2 = [n for n, _ in mfs._build_pattern_plan(quick=False, cycle=2)]
    # Zufalls-/Wort-Muster tragen den Seed im Namen -> muessen abweichen.
    assert names1 != names2


def test_pattern_plan_random_data_differs_between_cycles():
    """Die geschriebenen Zufallsdaten unterscheiden sich je Zyklus."""
    plan1 = mfs._build_pattern_plan(quick=False, cycle=1)
    plan2 = mfs._build_pattern_plan(quick=False, cycle=2)
    rnd1 = next(b for n, b in plan1 if n.startswith("Random"))
    rnd2 = next(b for n, b in plan2 if n.startswith("Random"))
    assert rnd1(0, 1024) != rnd2(0, 1024)
