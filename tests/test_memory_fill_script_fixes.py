"""Regressionstests zu den Review-Befunden im RAM-Fuelltest-Skript."""

import mmap

import pytest

from hardwaretest.tests import memory_fill_script as mfs


def _chunks(n, mb=1):
    return [(mmap.mmap(-1, mb << 20, flags=mmap.MAP_SHARED | mmap.MAP_ANONYMOUS),
             mb << 20) for _ in range(n)]


def _failing_fill(exc_factory, bad_chunk=2):
    original = mfs._fill_segments

    def fill(chunks, builder, segs, deadline=float("inf"), on_progress=None, dropped=None):
        if any(seg[0] == bad_chunk for seg in segs):
            raise exc_factory()
        return original(chunks, builder, segs, deadline, on_progress, dropped)
    return fill


# --- Befund 1: abgebrochene Fill-Worker --------------------------------------

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_failed_fill_worker_fails_instead_of_partial_coverage(monkeypatch):
    chunks = _chunks(4)
    builder = mfs._const_byte_builder(0xA5)
    segs = mfs._segments(chunks, None)
    monkeypatch.setattr(mfs, "_fill_segments", _failing_fill(lambda: RuntimeError("Schreibfehler simuliert")))
    with pytest.raises(mfs.WorkerFailure) as info:
        mfs._run_parallel("fill", chunks, builder, segs, workers=4)
    assert not info.value.inconclusive
    assert len(info.value.messages) == 1
    assert info.value.messages[0].startswith("FEHLER: Prozess ")
    assert "Schreibfehler simuliert" in info.value.messages[0]
    mfs._free_chunks(chunks)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_fill_worker_out_of_memory_is_inconclusive(monkeypatch):
    chunks = _chunks(4)
    builder = mfs._const_byte_builder(0xA5)
    segs = mfs._segments(chunks, None)
    monkeypatch.setattr(mfs, "_fill_segments", _failing_fill(MemoryError))
    with pytest.raises(mfs.WorkerFailure) as info:
        mfs._run_parallel("fill", chunks, builder, segs, workers=2)
    assert info.value.inconclusive
    assert "Speichermangel" in info.value.messages[0]
    mfs._free_chunks(chunks)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_failed_verify_worker_reports_reason(monkeypatch):
    chunks = _chunks(4)
    builder = mfs._const_byte_builder(0x5A)
    segs = mfs._segments(chunks, None)
    mfs._fill(chunks, builder)

    def check(chunks, builder, segs, on_progress=None, dropped=None, invert=False):
        if any(seg[0] == 1 for seg in segs):
            raise ValueError("Pruefabbruch simuliert")
        return []
    monkeypatch.setattr(mfs, "_check_segments", check)
    errs = mfs._run_parallel("verify", chunks, builder, segs, workers=4)
    assert errs[0] == "FEHLER: 1 Pruef-Prozess(e) unerwartet beendet"
    assert "Pruefabbruch simuliert" in errs[1]
    mfs._free_chunks(chunks)


def test_worker_exit_reason_classification():
    assert mfs._worker_exit_reason(0) == (None, "")
    assert mfs._worker_exit_reason(mfs._WORKER_EXIT_ENV)[0] == "env"
    assert mfs._worker_exit_reason(-9)[0] == "env"
    assert mfs._worker_exit_reason(-7)[0] == "fail"
    assert mfs._worker_exit_reason(1) == ("fail", "Exit-Code 1")
    assert mfs._classify_worker_exception(MemoryError())[0] == mfs._WORKER_EXIT_ENV
    assert mfs._classify_worker_exception(OSError(12, "Cannot allocate memory"))[0] == mfs._WORKER_EXIT_ENV
    code, text = mfs._classify_worker_exception(ValueError("x"))
    assert code == 1 and "ValueError: x" in text


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_run_cycles_is_inconclusive_after_fill_worker_out_of_memory(monkeypatch, capsys):
    monkeypatch.setattr(mfs, "_usable_cpus", lambda: 2)
    monkeypatch.setattr(mfs, "_min_reserve_mb", lambda: 0)
    monkeypatch.setattr(mfs, "_fill_segments", _failing_fill(MemoryError, bad_chunk=3))
    rc = mfs.run_cycles(duration_seconds=1, memory_mb=4, reserve_mb=0, chunk_mb=1,
                        quick=True, pause_seconds=0)
    out = capsys.readouterr().out
    assert rc == 2
    assert "Speichermangel" in out
    assert "ERGEBNIS: NICHT AUSSAGEKRÄFTIG" in out
    assert "BESTANDEN" not in out


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_run_cycles_fails_after_fill_worker_crash(monkeypatch, capsys):
    monkeypatch.setattr(mfs, "_usable_cpus", lambda: 2)
    monkeypatch.setattr(mfs, "_min_reserve_mb", lambda: 0)
    monkeypatch.setattr(mfs, "_fill_segments", _failing_fill(lambda: RuntimeError("kaputt"), bad_chunk=3))
    rc = mfs.run_cycles(duration_seconds=1, memory_mb=4, reserve_mb=0, chunk_mb=1,
                        quick=True, pause_seconds=0)
    out = capsys.readouterr().out
    assert rc == 1
    assert "kaputt" in out and "ERGEBNIS: FEHLER GEFUNDEN" in out


# --- Befund 2: cgroup-Limit und Reserve ---------------------------------------

def _cgroup(tmp_path, path, memory_max, current, cgroup_line="0::"):
    root = tmp_path / "cgroup"
    directory = root.joinpath(*[p for p in path.split("/") if p])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "memory.max").write_text(f"{memory_max}\n")
    (directory / "memory.current").write_text(f"{current}\n")
    proc = tmp_path / "self_cgroup"
    proc.write_text(f"{cgroup_line}{path}\n")
    return str(root), str(proc)


def test_cgroup_without_limit_returns_none(tmp_path):
    root, proc = _cgroup(tmp_path, "/user.slice/app", "max", 0)
    (tmp_path / "cgroup" / "memory.max").write_text("max\n")
    (tmp_path / "cgroup" / "memory.current").write_text("123\n")
    assert mfs._read_cgroup_available_mb(root, proc) is None
    assert mfs._read_cgroup_available_mb(str(tmp_path / "fehlt"), proc) is None


def test_cgroup_exhausted_limit_returns_zero_not_fallback(tmp_path, monkeypatch):
    root, proc = _cgroup(tmp_path, "/user.slice/app", 2 << 30, 2 << 30)
    assert mfs._read_cgroup_available_mb(root, proc) == 0
    monkeypatch.setattr(mfs, "_read_meminfo_mb", lambda: 64000)
    monkeypatch.setattr(mfs, "_read_cgroup_available_mb", lambda: 0)
    assert mfs._available_memory_mb() == 0
    monkeypatch.setattr(mfs, "_read_cgroup_available_mb", lambda: None)
    assert mfs._available_memory_mb() == 64000


def test_cgroup_path_from_proc_is_resolved_and_parents_apply(tmp_path):
    root, proc = _cgroup(tmp_path, "/user.slice/app", 4 << 30, 1 << 30)
    assert mfs._read_cgroup_available_mb(root, proc) == 3 * 1024
    # Uebergeordnete Ebene mit kleinerem Rest begrenzt staerker.
    parent = tmp_path / "cgroup" / "user.slice"
    (parent / "memory.max").write_text(f"{3 << 30}\n")
    (parent / "memory.current").write_text(f"{2 << 30}\n")
    assert mfs._read_cgroup_available_mb(root, proc) == 1024
    # Wurzel-Fallback, wenn /proc/self/cgroup nicht lesbar ist.
    (tmp_path / "cgroup" / "memory.max").write_text(f"{1 << 30}\n")
    (tmp_path / "cgroup" / "memory.current").write_text("0\n")
    assert mfs._read_cgroup_available_mb(root, str(tmp_path / "fehlt")) == 1024


def test_cgroup_v1_lines_are_ignored(tmp_path):
    root, proc = _cgroup(tmp_path, "/app", 1 << 30, 0, cgroup_line="12:memory:")
    assert mfs._cgroup_v2_path(proc) is None
    assert mfs._read_cgroup_available_mb(root, proc) is None  # Wurzel ohne memory.max


def test_run_cycles_aborts_when_available_not_above_reserve(monkeypatch, capsys):
    monkeypatch.setattr(mfs, "_available_memory_mb", lambda: 500)
    monkeypatch.setattr(mfs, "_min_reserve_mb", lambda: 1024)
    allocate = mfs._allocate_chunks
    calls = []
    monkeypatch.setattr(mfs, "_allocate_chunks", lambda *a: calls.append(a) or allocate(*a))
    rc = mfs.run_cycles(duration_seconds=5, memory_mb=0, reserve_mb=512, chunk_mb=1,
                        quick=True, pause_seconds=0)
    out = capsys.readouterr().out
    assert rc == 2 and calls == []
    assert "FEHLER: Verfuegbarer Speicher (500 MB) liegt nicht ueber der Reserve (1024 MB)" in out
    assert "ERGEBNIS: NICHT AUSSAGEKRÄFTIG" in out and "Zyklen: 0" in out


# --- Befund 3: madvise-Rueckgabe und Mindestabdeckung --------------------------

def _pressure_with_chunks(n: int, size: int = 1 << 20):
    chunks = mfs._allocate_chunks(n * size // (1 << 20), 1)
    assert len(chunks) == n
    return chunks, mfs._Pressure(chunks, reserve_mb=1)


def test_drop_counts_only_successful_madvise(monkeypatch):
    chunks, pressure = _pressure_with_chunks(2)
    try:
        assert pressure.drop(0) is True
        assert pressure.dropped_bytes == chunks[0][1]
        assert pressure.unreleased_bytes == 0

        def boom(m, size):
            raise OSError(22, "Invalid argument")

        monkeypatch.setattr(mfs, "_madv_remove", boom)
        assert pressure.drop(1) is False
        assert pressure.is_dropped(1)  # ausgeschlossen bleibt er trotzdem
        assert pressure.dropped_bytes == chunks[0][1]
        assert pressure.unreleased_bytes == chunks[1][1]
        assert pressure.excluded_bytes == chunks[0][1] + chunks[1][1]
    finally:
        pressure.close()
        mfs._free_chunks(chunks)


def _run_with_drops(monkeypatch, total_mb: int, drop: int):
    """Laesst einen Zyklus laufen, bei dem *drop* Chunks wegen Speicherdruck verworfen werden."""
    monkeypatch.setattr(mfs, "_usable_cpus", lambda: 2)
    monkeypatch.setattr(mfs, "_min_reserve_mb", lambda: 0)
    original_pressure = mfs._Pressure

    class Forced(original_pressure):
        instances = 0

        def __init__(self, chunks, reserve_mb):
            super().__init__(chunks, reserve_mb)
            Forced.instances += 1

        def poll(self):
            if self.events or Forced.instances > 1:  # nur im ersten Zyklus
                return
            self.events = 1
            for _ in range(drop):
                self._tail -= 1
                self.drop(self._tail)

    monkeypatch.setattr(mfs, "_Pressure", Forced)
    return mfs.run_cycles(duration_seconds=2, memory_mb=total_mb, reserve_mb=0,
                          chunk_mb=1, quick=True, pause_seconds=0)


def test_low_coverage_is_inconclusive(monkeypatch, capsys):
    rc = _run_with_drops(monkeypatch, total_mb=10, drop=4)
    out = capsys.readouterr().out
    assert rc == 2
    assert "Zyklus NICHT AUSSAGEKRAEFTIG: Abdeckung 6 von 10 MB geprueft (60 %" in out
    assert "Zyklus 1 abgeschlossen: NICHT AUSSAGEKRAEFTIG" in out
    assert "Wegen Speicherdruck verworfen (nicht geprueft): 4 MB" in out
    assert "Speicherdruck: 1 Mal, 4 MB verworfen" in out
    assert "ERGEBNIS: NICHT AUSSAGEKRÄFTIG" in out and "BESTANDEN" not in out


def test_sufficient_coverage_still_passes_and_reports_dropped(monkeypatch, capsys):
    rc = _run_with_drops(monkeypatch, total_mb=20, drop=1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Abdeckung: 19 von 20 MB geprueft (95 %)" in out
    assert "Wegen Speicherdruck verworfen (nicht geprueft): 1 MB" in out
    assert "ERGEBNIS: BESTANDEN" in out
