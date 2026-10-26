"""Tests fuer die aus adaptive-ramtest uebernommenen Funktionen des Fuelltests."""

import mmap

import pytest

from hardwaretest.tests import memory_fill_script as mfs


def _chunks(n, mb=1):
    return [(mmap.mmap(-1, mb << 20, flags=mmap.MAP_SHARED | mmap.MAP_ANONYMOUS),
             mb << 20) for _ in range(n)]


def test_inverted_builder_flips_all_bits():
    base = mfs._address_builder()
    inv = mfs._inverted_builder(base)
    a, b = base(8192, 4096), inv(8192, 4096)
    assert all(x ^ y == 0xFF for x, y in zip(a, b, strict=True))


def test_full_plan_has_inverted_address_quick_unchanged():
    assert "Address invertiert" in [n for n, _ in mfs._build_pattern_plan(False)]
    assert len(mfs._build_pattern_plan(True)) == 4


def test_data_line_patterns_cover_all_64_bits():
    pats = mfs._data_line_patterns()
    assert len(pats) == 128
    ones = [w for n, w in pats if n.startswith("WalkingOne64")]
    assert sorted(ones) == [1 << b for b in range(64)]


def test_data_line_test_ok_and_reports_bit():
    done, errs = mfs._data_line_test(size_mb=4, patterns=mfs._data_line_patterns()[:3])
    assert done == 3 and errs == []

    def corrupt(m):
        m[16] ^= 0x04  # Bit 2 im dritten Byte -> Datenbit 2 des Worts

    done, errs = mfs._data_line_test(size_mb=4, patterns=[("x", 0)], corrupt=corrupt)
    assert len(errs) == 1
    assert "MISMATCH bei Offset 16" in errs[0] and "Datenbit(s) 2" in errs[0]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("workers", [1, 3])
def test_moving_inversion_passes_and_finds_flip(workers):
    chunks = _chunks(4)
    builder = mfs._const_byte_builder(0xA5)
    segs = mfs._segments(chunks, None)
    mfs._fill(chunks, builder)
    assert mfs._moving_inversion(chunks, builder, segs, 0, 1, workers=workers) == []
    assert chunks[2][0][10] == 0x5A
    # Inhalt ist jetzt invertiert; ein gekipptes Bit muss auffallen.
    inv = mfs._inverted_builder(builder)
    chunks[1][0][99] ^= 0x01
    errs = mfs._moving_inversion(chunks, inv, segs, 0, 1, workers=workers)
    assert len(errs) == 1 and "Chunk 1" in errs[0]
    mfs._free_chunks(chunks)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("workers", [1, 3])
def test_dropped_chunks_are_skipped_without_false_errors(workers):
    chunks = _chunks(4)
    builder = mfs._const_byte_builder(0x3C)
    pressure = mfs._Pressure(chunks, reserve_mb=0)
    segs = mfs._segments(chunks, None)
    written = mfs._run_parallel("fill", chunks, builder, segs, workers=workers,
                                pressure=pressure)
    pressure.drop(3)
    assert chunks[3][0][0] == 0  # MADV_REMOVE -> liest Nullen
    assert pressure.keep(written) == written[:3]
    errs = mfs._run_parallel("verify", chunks, builder, written, workers=workers,
                             pressure=pressure)
    assert errs == []
    assert pressure.dropped_bytes == 1 << 20
    # Schreiben ueberspringt verworfene Bloecke.
    again = mfs._run_parallel("fill", chunks, builder, segs, workers=workers,
                              pressure=pressure)
    assert [s[0] for s in again] == [0, 1, 2]
    pressure.close()
    mfs._free_chunks(chunks)


def test_pressure_poll_drops_from_tail(monkeypatch):
    chunks = _chunks(4)
    pressure = mfs._Pressure(chunks, reserve_mb=1000)
    avail = iter([100, 300, 600, 1200])
    monkeypatch.setattr(mfs, "_available_memory_mb", lambda: next(avail))
    pressure.poll()
    assert [pressure.is_dropped(i) for i in range(4)] == [False, True, True, True]
    assert pressure.events == 1
    pressure.close()
    mfs._free_chunks(chunks)


def test_pressure_poll_idle_when_enough_memory(monkeypatch):
    chunks = _chunks(2)
    pressure = mfs._Pressure(chunks, reserve_mb=1000)
    monkeypatch.setattr(mfs, "_available_memory_mb", lambda: 600)
    pressure.poll()
    assert pressure.dropped_bytes == 0
    pressure.close()
    mfs._free_chunks(chunks)


def test_mlock_and_free_locked_chunk():
    chunks = _chunks(1)
    m, size = chunks[0]
    err = mfs._mlock(m, size)
    assert err in (0, 1, 11, 12)  # ok, EPERM, EAGAIN, ENOMEM
    locked, err2 = mfs._lock_segments(chunks, mfs._segments(chunks, None))
    assert (locked == size) == (err2 == 0)
    mfs._free_chunks(chunks)  # darf trotz Sperre keinen BufferError liefern
    assert m.closed


def test_lock_failure_is_reported(monkeypatch):
    chunks = _chunks(2)
    monkeypatch.setattr(mfs, "_mlock", lambda m, n: 12)
    assert mfs._lock_segments(chunks, mfs._segments(chunks, None)) == (0, 12)
    mfs._free_chunks(chunks)


def test_phys_addr_without_privileges_is_none_or_int():
    m = mmap.mmap(-1, 4096)
    m[0] = 1
    phys = mfs._phys_addr(mfs._chunk_addr(m))
    assert phys is None or phys > 0
    suffix = mfs._phys_suffix(m, 0)
    assert suffix == "" or suffix.startswith(", phys. Adresse 0x")
    m.close()


def test_oom_score_adj_raised():
    import subprocess
    import sys
    code = ("from hardwaretest.tests import memory_fill_script as m;"
            "m._set_oom_score_adj(500);"
            "print(open('/proc/self/oom_score_adj').read().strip())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert int(out) >= 500


def test_spare_cpus_rotate_so_every_cpu_is_tested():
    cpus = list(range(16))
    workers, spare_n = 14, 2
    seen_spare = set()
    for cycle in range(1, 9):
        spare = mfs._spare_cpus(cpus, workers, (cycle - 1) * spare_n)
        assert len(spare) == spare_n
        seen_spare.update(spare)
    # Jeder Kern war einmal frei -> jeder Kern hat in den anderen Zyklen getestet.
    assert seen_spare == set(cpus)
    assert mfs._spare_cpus(cpus, workers, 0) == [14, 15]
    assert mfs._spare_cpus(cpus, workers, 2) == [0, 1]
