#!/usr/bin/env python3
"""Zyklischer RAM-Fuelltest (eigenstaendiges Skript).

Ablauf pro Zyklus:
  1. Verfuegbaren Speicher ermitteln (abzueglich Reserve).
  2. Speicher in Chunks allokieren (mmap).
  3. Mehrere Bitmuster nacheinander schreiben + verifizieren:
       - 0xAA, 0x55, 0xFF, 0x00 (Stuck-Bit-Erkennung)
       - Walking-Ones / Walking-Zeros (Bit-Aliasing)
       - Address-in-Data, auch invertiert (Adress-Decoder-Fehler)
       - Pseudo-Random         (mit Seed, reproduzierbar)
     Der geschriebene Speicher wird per mlock gegen Auslagern gesperrt.
     Im Vollmodus folgt bei genug Restzeit eine Moving Inversion (lesen,
     vergleichen, invertiert schreiben, erneut pruefen).
  4. Alle Chunks freigeben (munmap).
  5. Naechster Zyklus, bis die Dauer abgelaufen ist.

Zusaetzlich (aus adaptive-ramtest uebernommen):
  - Speicherdruck-Ueberwachung: wird der freie RAM knapp, werden Bloecke vom
    Ende her verworfen statt das System einfrieren zu lassen.
  - Datenleitungstest (Vollmodus, einmal je Musterrunde): 64-Bit
    Walking-One/Zero ueber 128 MB, meldet die betroffenen Datenbits.
  - Physische Adresse bei Fehlern (/proc/self/pagemap, nur als root).
  - oom_score_adj 500: bei Speichermangel wird zuerst der Test beendet.

Exit-Codes:
  0 – Kein Fehler erkannt.
  1 – Verifikations-Fehler (moeglicher RAM-Defekt).
  2 – Sonstiger Fehler (z.B. Allokationsfehler, unerwartete Exception).
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import gc
import mmap
import os
import random
import signal
import struct
import sys
import time
import traceback
from typing import Callable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

_stop = False
# Verifizieren ist etwa doppelt so schnell wie Schreiben (gemessen 4300 vs.
# 1800 MB/s). Damit das Pruefen des Geschriebenen noch in die Testzeit passt,
# endet das Schreiben nach diesem Anteil der Restzeit.
_WRITE_SHARE = 1 / 1.6
# Abstand der Fortschrittsmeldungen beim Schreiben/Verifizieren.
_PROGRESS_INTERVAL = 10.0


def _handle_signal(signum, _frame):
    global _stop
    _stop = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_meminfo_mb() -> int:
    """Verfuegbaren RAM ueber /proc/meminfo lesen (Linux)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except OSError:
        pass
    try:
        import psutil
        return int(psutil.virtual_memory().available // (1024 * 1024))
    except Exception:
        return 1024


def _read_cgroup_available_mb() -> int:
    """Verfuegbaren RAM in einer cgroup v2 lesen.

    Gibt ``0`` zurueck, wenn keine cgroup-Begrenzung existiert oder die
    Dateien nicht lesbar sind. Andernfalls ``memory.max - memory.current``
    in MiB.
    """
    base = "/sys/fs/cgroup"
    try:
        with open(f"{base}/memory.max") as f:
            raw = f.read().strip()
        if raw == "max":
            return 0
        max_bytes = int(raw)
        with open(f"{base}/memory.current") as f:
            cur_bytes = int(f.read().strip())
    except (OSError, ValueError):
        return 0
    free = max(0, max_bytes - cur_bytes)
    return free // (1024 * 1024)


def _available_memory_mb() -> int:
    """Verfuegbarer RAM, begrenzt durch cgroup v2 falls aktiv."""
    sys_mb = _read_meminfo_mb()
    cg_mb = _read_cgroup_available_mb()
    if cg_mb > 0:
        return min(sys_mb, cg_mb)
    return sys_mb


# Typ-Alias: (mmap-Objekt, Groesse in Bytes)
Chunk = Tuple[mmap.mmap, int]


def _allocate_chunks(total_mb: int, chunk_mb: int) -> List[Chunk]:
    """Allokiert *total_mb* in Stuecken von *chunk_mb* via anonymem mmap."""
    chunks: List[Chunk] = []
    remaining = total_mb
    while remaining > 0 and not _stop:
        size_mb = min(chunk_mb, remaining)
        size_bytes = size_mb * 1024 * 1024
        try:
            m = mmap.mmap(-1, size_bytes,
                          flags=mmap.MAP_SHARED | mmap.MAP_ANONYMOUS,
                          prot=mmap.PROT_READ | mmap.PROT_WRITE)
            chunks.append((m, size_bytes))
            remaining -= size_mb
        except (OSError, OverflowError, ValueError):
            break
    return chunks


def _free_chunks(chunks: List[Chunk]) -> None:
    """Gibt alle Chunks frei und stellt sicher, dass der RAM ans OS zurueck geht.

    ``madvise(MADV_DONTNEED)`` gibt die physischen Seiten sofort frei (auch
    wenn ein Buffer noch exportiert ist), anschliessend schliesst ``close()``
    das Mapping. Fehler werden gemeldet statt stillschweigend verschluckt,
    damit ein haengender Buffer nicht unbemerkt RAM belegt laesst.
    """
    failed = 0
    for m, size in chunks:
        # Gesperrte Bereiche lassen sich nicht per madvise freigeben.
        with contextlib.suppress(Exception):
            _munlock(m, size)
        # 1. Physische Seiten sofort zurueckgeben.
        if hasattr(m, "madvise") and hasattr(mmap, "MADV_DONTNEED"):
            with contextlib.suppress(OSError, ValueError, BufferError):
                m.madvise(mmap.MADV_DONTNEED, 0, size)
        # 2. Mapping schliessen (munmap).
        try:
            m.close()
        except BufferError:
            failed += 1
        except Exception:
            failed += 1
    chunks.clear()
    # Python-Objekte aufraeumen, damit keine Referenzen die Mappings halten.
    gc.collect()
    if failed:
        print(f"  WARNUNG: {failed} Speicherblock/-bloecke konnten nicht "
              f"geschlossen werden (Buffer noch in Benutzung).", flush=True)


# ---------------------------------------------------------------------------
# mlock, physische Adressen, OOM-Schutz, Speicherdruck
# ---------------------------------------------------------------------------

_LIBC = None


def _libc():
    global _LIBC
    if _LIBC is None:
        try:
            lib = ctypes.CDLL(None, use_errno=True)
            for fn in (lib.mlock, lib.munlock):
                fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
                fn.restype = ctypes.c_int
            _LIBC = lib
        except Exception:
            _LIBC = False
    return _LIBC or None


def _chunk_addr(m: mmap.mmap) -> Optional[int]:
    """Virtuelle Startadresse eines mmap (Buffer-Export sofort wieder freigeben)."""
    try:
        obj = ctypes.c_char.from_buffer(m)
    except (TypeError, ValueError, BufferError):
        return None
    addr = ctypes.addressof(obj)
    del obj
    return addr


def _mlock(m: mmap.mmap, length: int) -> int:
    """Sperrt die ersten *length* Bytes gegen Auslagern; liefert 0 oder errno."""
    lib = _libc()
    addr = _chunk_addr(m)
    if lib is None or addr is None:
        return errno.ENOSYS
    if lib.mlock(addr, length) != 0:
        return ctypes.get_errno() or errno.EPERM
    return 0


def _munlock(m: mmap.mmap, length: int) -> None:
    lib = _libc()
    addr = _chunk_addr(m)
    if lib is not None and addr is not None:
        lib.munlock(addr, length)


def _lock_segments(chunks: List[Chunk], segs: List["Segment"]) -> Tuple[int, int]:
    """Sperrt die beschriebenen Segmente; liefert (gesperrte Bytes, errno)."""
    locked = 0
    for idx, _off, length in segs:
        if _stop:
            break
        err = _mlock(chunks[idx][0], length)
        if err:
            return locked, err
        locked += length
    return locked, 0


def _raise_memlock_limit() -> None:
    """RLIMIT_MEMLOCK anheben (als root unbegrenzt, sonst bis zum Hard-Limit)."""
    with contextlib.suppress(Exception):
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
        if os.geteuid() == 0:
            hard = resource.RLIM_INFINITY
        if soft != hard:
            resource.setrlimit(resource.RLIMIT_MEMLOCK, (hard, hard))


def _swap_active() -> bool:
    try:
        with open("/proc/swaps", encoding="utf-8") as f:
            return len(f.read().strip().splitlines()) > 1
    except OSError:
        return False


def _set_oom_score_adj(value: int = 500) -> None:
    """Bei Speichermangel soll der Kernel zuerst den Test beenden, nicht OS/GUI."""
    with contextlib.suppress(OSError, ValueError), \
            open("/proc/self/oom_score_adj", "r+", encoding="ascii") as f:
        if int(f.read().strip() or 0) < value:
            f.seek(0)
            f.write(str(value))


def _phys_addr(vaddr: int) -> Optional[int]:
    """Physische Adresse ueber /proc/self/pagemap (PFN nur mit CAP_SYS_ADMIN)."""
    page = mmap.PAGESIZE
    try:
        with open("/proc/self/pagemap", "rb") as f:
            f.seek((vaddr // page) * 8)
            raw = f.read(8)
    except OSError:
        return None
    if len(raw) != 8:
        return None
    entry = int.from_bytes(raw, "little")
    pfn = entry & ((1 << 55) - 1)
    if not (entry >> 63) & 1 or pfn == 0:
        return None
    return pfn * page + vaddr % page


def _phys_suffix(m: mmap.mmap, offset: int) -> str:
    addr = _chunk_addr(m)
    phys = _phys_addr(addr + offset) if addr is not None else None
    return f", phys. Adresse 0x{phys:X}" if phys is not None else ""


class _Pressure:
    """Ueberwacht den freien RAM und verwirft bei Knappheit Bloecke vom Ende her.

    Die Kennzeichen liegen in einem geteilten mmap, damit geforkte Worker
    verworfene Bloecke ueberspringen. Verworfene Seiten (MADV_REMOVE) lesen
    sich als Nullen und duerfen daher nicht als Fehler zaehlen.
    """

    def __init__(self, chunks: List[Chunk], reserve_mb: int,
                 interval: float = 0.2) -> None:
        self.chunks = chunks
        self.reserve_mb = reserve_mb
        self.interval = interval
        self.flags = mmap.mmap(-1, max(1, len(chunks)),
                               flags=mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
        self.dropped_bytes = 0
        self.events = 0
        self._tail = len(chunks)
        self._next = 0.0

    def is_dropped(self, idx: int) -> bool:
        return self.flags[idx] != 0

    def drop(self, idx: int) -> None:
        m, size = self.chunks[idx]
        self.flags[idx] = 1
        with contextlib.suppress(Exception):
            _munlock(m, size)
        if hasattr(mmap, "MADV_REMOVE"):
            with contextlib.suppress(OSError, ValueError):
                m.madvise(mmap.MADV_REMOVE, 0, size)
        self.dropped_bytes += size

    def poll(self) -> None:
        now = time.monotonic()
        if now < self._next:
            return
        self._next = now + self.interval
        avail = _available_memory_mb()
        if avail >= self.reserve_mb // 2 or self._tail == 0:
            return
        before = self.dropped_bytes
        while avail < self.reserve_mb and self._tail > 0:
            self._tail -= 1
            self.drop(self._tail)
            avail = _available_memory_mb()
        self.events += 1
        print(f"    WARNUNG: Speicher knapp – {(self.dropped_bytes - before) >> 20} MB "
              f"verworfen (jetzt verfuegbar {avail} MB, Reserve "
              f"{self.reserve_mb} MB).", flush=True)

    def reapply(self) -> None:
        """Seiten verworfener Bloecke erneut freigeben (Worker-Nachzuegler)."""
        if not hasattr(mmap, "MADV_REMOVE"):
            return
        for idx in range(self._tail, len(self.chunks)):
            m, size = self.chunks[idx]
            with contextlib.suppress(OSError, ValueError):
                m.madvise(mmap.MADV_REMOVE, 0, size)

    def keep(self, segs: List["Segment"]) -> List["Segment"]:
        return [s for s in segs if not self.is_dropped(s[0])]

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.flags.close()


# ---------------------------------------------------------------------------
# Pattern-Generatoren
# ---------------------------------------------------------------------------

_BUF_SIZE = 4 * 1024 * 1024  # 4 MiB Schreib-/Lesepuffer


# builder(global_offset:int, size:int) -> bytes  (len == size)
PatternBuilder = Callable[[int, int], bytes]


def _const_byte_builder(byte_value: int) -> PatternBuilder:
    full = bytes([byte_value & 0xFF]) * _BUF_SIZE

    def build(_offset: int, size: int) -> bytes:
        return full if size == _BUF_SIZE else full[:size]
    return build


def _const_word_builder(word_value: int) -> PatternBuilder:
    word = word_value & 0xFFFFFFFFFFFFFFFF
    packed = struct.pack("<Q", word)
    full = packed * (_BUF_SIZE // 8)

    def build(_offset: int, size: int) -> bytes:
        if size == _BUF_SIZE:
            return full
        rep = (size + 7) // 8
        return (packed * rep)[:size]
    return build


def _random_builder(seed: int) -> PatternBuilder:
    rng = random.Random(seed)
    full = rng.randbytes(_BUF_SIZE)

    def build(_offset: int, size: int) -> bytes:
        return full if size == _BUF_SIZE else full[:size]
    return build


def _address_builder() -> PatternBuilder:
    """Adress-Muster mit Page-Granularitaet (4 KiB).

    Jede 4-KiB-Page wird mit einem konstanten 64-Bit-Wert gefuellt, der
    der Startadresse der Page entspricht. Erkennt Adress-Decoder-Fehler
    auf Page-Ebene und ist wesentlich schneller als ein byte-genaues
    Muster (ein C-Aufruf pro Page statt pro Qword).
    """
    PAGE = 4096
    QWORDS_PER_PAGE = PAGE // 8

    def build(offset: int, size: int) -> bytes:
        out = bytearray(size)
        # Erste Page kann teilweise sein, falls offset nicht 4K-aligned ist
        page_start = (offset // PAGE) * PAGE
        in_page = offset - page_start
        pos = 0
        while pos < size:
            value = page_start & 0xFFFFFFFFFFFFFFFF
            page_buf = struct.pack("<Q", value) * QWORDS_PER_PAGE
            take = min(PAGE - in_page, size - pos)
            out[pos:pos + take] = page_buf[in_page:in_page + take]
            pos += take
            page_start += PAGE
            in_page = 0
        return bytes(out)
    return build


_INVERT_TABLE = bytes(255 - i for i in range(256))


def _inverted_builder(builder: PatternBuilder) -> PatternBuilder:
    """Bitweise invertierte Variante eines Musters."""
    def build(offset: int, size: int) -> bytes:
        return builder(offset, size).translate(_INVERT_TABLE)
    return build


def _data_line_patterns() -> List[Tuple[str, int]]:
    """64-Bit Walking-One/Zero: jede Datenleitung einmal allein 1 bzw. 0."""
    mask = (1 << 64) - 1
    pats = [(f"WalkingOne64 Bit {b}", 1 << b) for b in range(64)]
    pats += [(f"WalkingZero64 Bit {b}", ~(1 << b) & mask) for b in range(64)]
    return pats


def _build_pattern_plan(
    quick: bool, cycle: int = 1
) -> List[Tuple[str, PatternBuilder]]:
    """Liefert die Liste (name, builder) der Muster pro Zyklus.

    Die Muster wechseln von Zyklus zu Zyklus: in jedem Zyklus werden frische
    Zufallsmuster mit einem zyklus-abhaengigen Seed erzeugt, sodass der
    Speicher bei jeder Wiederholung mit anderen Daten beschrieben wird.
    """
    # Pro Zyklus reproduzierbarer, aber wechselnder Seed.
    cycle_rng = random.Random(0xC0FFEE ^ (cycle * 0x9E3779B1))

    if quick:
        rseed = cycle_rng.getrandbits(32)
        return [
            ("0xAA", _const_byte_builder(0xAA)),
            ("0x55", _const_byte_builder(0x55)),
            ("Address", _address_builder()),
            (f"Random seed=0x{rseed:08X}", _random_builder(rseed)),
        ]
    plan: List[Tuple[str, PatternBuilder]] = [
        ("0xAA", _const_byte_builder(0xAA)),
        ("0x55", _const_byte_builder(0x55)),
        ("0xFF", _const_byte_builder(0xFF)),
        ("0x00", _const_byte_builder(0x00)),
    ]
    # Walking-Ones / Walking-Zeros
    for bit in range(8):
        val = 1 << bit
        plan.append((f"WalkingOne 0x{val:02X}", _const_byte_builder(val)))
    for bit in range(8):
        val = (~(1 << bit)) & 0xFF
        plan.append((f"WalkingZero 0x{val:02X}", _const_byte_builder(val)))
    # Zyklus-abhaengige Wort- und Zufallsmuster -> wechselnde Muster.
    word_a = cycle_rng.getrandbits(64)
    word_b = cycle_rng.getrandbits(64)
    rseed1 = cycle_rng.getrandbits(32)
    rseed2 = cycle_rng.getrandbits(32)
    plan += [
        (f"Word 0x{word_a:016X}", _const_word_builder(word_a)),
        (f"Word 0x{word_b:016X}", _const_word_builder(word_b)),
        ("Address", _address_builder()),
        ("Address invertiert", _inverted_builder(_address_builder())),
        (f"Random seed=0x{rseed1:08X}", _random_builder(rseed1)),
        (f"Random seed=0x{rseed2:08X}", _random_builder(rseed2)),
    ]
    return plan


# ---------------------------------------------------------------------------
# Fill / Verify
# ---------------------------------------------------------------------------

def _fill(chunks: List[Chunk], builder: PatternBuilder, deadline: float = float("inf")) -> int:
    """Schreibt das Muster bis *deadline* (monotonic); liefert die geschriebenen Bytes.

    Bei sehr grossem RAM passt ein ganzer Zyklus nicht in die Testzeit; dann
    wird nur ein Teil beschrieben und genau dieser Teil verifiziert.
    """
    def _halt() -> bool:
        return _stop or time.monotonic() >= deadline

    grand_total = sum(size for _, size in chunks)
    reporter = _ProgressReporter("geschrieben", grand_total)
    global_offset = 0
    for m, total in chunks:
        reporter.update(global_offset)
        if _halt():
            return global_offset
        m.seek(0)
        pos = 0
        while pos < total:
            if _halt():
                return global_offset + pos
            piece = min(_BUF_SIZE, total - pos)
            m.write(builder(global_offset + pos, piece))
            pos += piece
        global_offset += total
    return global_offset


class _ProgressReporter:
    """Gibt alle ``_PROGRESS_INTERVAL`` Sekunden den Fortschritt aus."""

    def __init__(self, verb: str, total: int) -> None:
        self.verb = verb
        self.total = max(1, total)
        self.start = time.monotonic()
        self.next = self.start + _PROGRESS_INTERVAL

    def update(self, done: int) -> None:
        now = time.monotonic()
        if now < self.next:
            return
        self.next = now + _PROGRESS_INTERVAL
        mb = done // (1024 * 1024)
        speed = mb / max(1e-6, now - self.start)
        print(f"    ... {mb} / {self.total // (1024 * 1024)} MB {self.verb} "
              f"({100 * done / self.total:.0f}%, {speed:.0f} MB/s)", flush=True)


def _usable_cpus() -> int:
    """Anzahl der Worker: nutzbare logische CPUs, zwei bleiben fuer OS/GUI frei."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    return max(1, n - 2 if n > 4 else n)


def _min_reserve_mb() -> int:
    """Mindestreserve: 1 % des RAM, 1-8 GB.

    Ohne Swap kann der Kernel den Testspeicher nicht auslagern; bei zu kleiner
    Reserve verdraengt er die Programmdateien von OS/GUI und das System friert ein.
    """
    return max(1024, min(8192, _read_meminfo_total_mb() // 100))


def _read_meminfo_total_mb() -> int:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


Segment = Tuple[int, int, int]  # (Chunk-Index, globaler Offset, Laenge)


def _segments(chunks: List[Chunk], limit: Optional[int]) -> List[Segment]:
    segs: List[Segment] = []
    offset = 0
    for idx, (_m, total) in enumerate(chunks):
        if limit is not None:
            if offset >= limit:
                break
            total = min(total, limit - offset)
        segs.append((idx, offset, total))
        offset += total
    return segs


def _mismatch_msg(m: mmap.mmap, idx: int, global_offset: int, pos: int,
                  actual: bytes, expected: bytes) -> Optional[str]:
    for byte_idx, (got, want) in enumerate(zip(actual, expected, strict=False)):
        if got != want:
            return (f"MISMATCH bei Offset {global_offset + pos + byte_idx} "
                    f"(Chunk {idx}): erwartet 0x{want:02X}, gelesen 0x{got:02X}"
                    f"{_phys_suffix(m, pos + byte_idx)}")
    return None


def _check_segments(chunks: List[Chunk], builder: PatternBuilder,
                    segs: List[Segment],
                    on_progress: Optional[Callable[[int], None]] = None,
                    dropped=None, invert: bool = False) -> List[str]:
    """Prueft *segs*; mit *invert* wird danach das invertierte Muster geschrieben."""
    errors: List[str] = []
    done = 0
    for idx, global_offset, total in segs:
        if _stop:
            break
        m = chunks[idx][0]
        pos = 0
        while pos < total:
            if _stop or (dropped is not None and dropped[idx]):
                break
            piece = min(_BUF_SIZE, total - pos)
            expected = builder(global_offset + pos, piece)
            actual = m[pos:pos + piece]
            if actual != expected and not (dropped is not None and dropped[idx]):
                msg = _mismatch_msg(m, idx, global_offset, pos, actual, expected)
                if msg:
                    errors.append(msg)
                if len(errors) >= 50:
                    errors.append(
                        "... weitere Fehler unterdrueckt (>50 Treffer)"
                    )
                    return errors
            if invert:
                m[pos:pos + piece] = expected.translate(_INVERT_TABLE)
            pos += piece
            done += piece
            if on_progress is not None:
                on_progress(done)
    return errors


def _verify_segments(chunks: List[Chunk], builder: PatternBuilder,
                     segs: List[Segment],
                     on_progress: Optional[Callable[[int], None]] = None,
                     dropped=None) -> List[str]:
    return _check_segments(chunks, builder, segs, on_progress, dropped)


def _verify(chunks: List[Chunk], builder: PatternBuilder, limit: Optional[int] = None) -> List[str]:
    """Vergleicht den Inhalt aller Chunks (bis *limit* Bytes) mit dem Muster.

    ``mmap``-Slicing liefert ``bytes``; der Vergleich laeuft per memcmp.
    """
    segs = _segments(chunks, limit)
    reporter = _ProgressReporter("geprueft", sum(s[2] for s in segs))
    return _verify_segments(chunks, builder, segs, reporter.update)


def _fill_segments(chunks: List[Chunk], builder: PatternBuilder,
                   segs: List[Segment], deadline: float = float("inf"),
                   on_progress: Optional[Callable[[int], None]] = None,
                   dropped=None) -> List[Segment]:
    """Schreibt das Muster in *segs* bis *deadline*; liefert die beschriebenen Teile."""
    written: List[Segment] = []
    done = 0
    for idx, global_offset, total in segs:
        if dropped is not None and dropped[idx]:
            continue
        m = chunks[idx][0]
        pos = 0
        while pos < total and not _stop and time.monotonic() < deadline:
            if dropped is not None and dropped[idx]:
                break
            piece = min(_BUF_SIZE, total - pos)
            m[pos:pos + piece] = builder(global_offset + pos, piece)
            pos += piece
            done += piece
            if on_progress is not None:
                on_progress(done)
        if dropped is not None and dropped[idx]:
            continue
        if pos:
            written.append((idx, global_offset, pos))
        if pos < total:
            break
    return written


def _all_cpus() -> List[int]:
    if hasattr(os, "sched_getaffinity"):
        return sorted(os.sched_getaffinity(0))
    return []


def _spare_cpus(cpus: List[int], workers: int, cpu_offset: int) -> List[int]:
    """CPUs, die bei *cpu_offset* keinen Worker bekommen (frei fuer OS/GUI)."""
    used = {cpus[(w + cpu_offset) % len(cpus)] for w in range(workers)} if cpus else set()
    return [c for c in cpus if c not in used]


def _set_parent_death_signal() -> None:
    """Kindprozess beenden, falls der Hauptprozess hart beendet wird."""
    with contextlib.suppress(Exception):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


def _run_parallel(kind: str, chunks: List[Chunk], builder: PatternBuilder,
                  segs: List[Segment], workers: Optional[int] = None,
                  rotation: int = 0, deadline: float = float("inf"),
                  pressure: Optional[_Pressure] = None, cpu_offset: int = 0):
    """Fuehrt Schreiben (``kind='fill'``), Pruefen (``'verify'``) oder Pruefen
    mit anschliessendem invertiertem Schreiben (``'invert'``) parallel aus.

    Der Speicher ist ``MAP_SHARED``; geforkte Worker schreiben/lesen daher
    direkt im selben RAM. Jeder Worker ist an eine CPU gebunden. Ueber
    *rotation* bekommen die Bloecke je Zyklus andere CPUs (und damit auf
    Mehrsockel-Systemen wechselnd lokale und entfernte Speicherzugriffe).
    Mit *pressure* ueberwacht der Hauptprozess den freien RAM und verwirft
    bei Knappheit Bloecke; die Worker ueberspringen diese.
    *cpu_offset* verschiebt die genutzten CPUs, damit die fuer OS/GUI frei
    gelassenen Kerne von Zyklus zu Zyklus wechseln und alle Kerne testen.

    Liefert bei ``fill`` die beschriebenen Segmente, sonst die Fehler.
    """
    cpus = _all_cpus()
    if workers is None:
        workers = _usable_cpus()
    workers = max(1, min(workers, len(segs)))
    parts: List[List[Segment]] = [[] for _ in range(workers)]
    for j, seg in enumerate(segs):
        parts[(j + rotation) % workers].append(seg)
    grand_total = sum(s[2] for s in segs)
    verb = {"fill": "geschrieben", "invert": "invertiert"}.get(kind, "geprueft")
    dropped = pressure.flags if pressure is not None else None

    def _work(part: List[Segment], progress: Callable[[int], None]):
        if kind == "fill":
            return _fill_segments(chunks, builder, part, deadline, progress, dropped)
        return _check_segments(chunks, builder, part, progress, dropped,
                               invert=kind == "invert")

    if workers == 1 or not hasattr(os, "fork"):
        reporter = _ProgressReporter(verb, grand_total)

        def _progress(done: int) -> None:
            reporter.update(done)
            if pressure is not None:
                pressure.poll()
        result = _work(segs, _progress)
        if pressure is not None:
            pressure.reapply()
        return result

    print(f"    {workers} Prozesse auf CPUs, Rotation {rotation % workers} ...",
          flush=True)
    counters = mmap.mmap(-1, 8 * workers,
                         flags=mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
    procs: List[Tuple[int, int]] = []
    for w in range(workers):
        r, wfd = os.pipe()
        pid = os.fork()
        if pid == 0:
            code = 0
            try:
                os.close(r)
                _set_parent_death_signal()
                with contextlib.suppress(OSError):
                    os.nice(10)
                if cpus:
                    with contextlib.suppress(OSError):
                        os.sched_setaffinity(
                            0, {cpus[(w + cpu_offset) % len(cpus)]})

                def _report(done: int, _w: int = w) -> None:
                    struct.pack_into("<Q", counters, 8 * _w, done)
                res = _work(parts[w], _report)
                lines = ([f"{i} {o} {n}" for i, o, n in res]
                         if kind == "fill" else res)
                with os.fdopen(wfd, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
            except BaseException:
                code = 1
            finally:
                os._exit(code)
        os.close(wfd)
        procs.append((pid, r))

    reporter = _ProgressReporter(verb, grand_total)
    pending = {pid for pid, _ in procs}
    failed = 0
    try:
        while pending:
            for pid in list(pending):
                done_pid, status = os.waitpid(pid, os.WNOHANG)
                if done_pid:
                    pending.discard(pid)
                    if os.waitstatus_to_exitcode(status) != 0:
                        failed += 1
            if _stop:
                for pid in pending:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGTERM)
            if pending:
                time.sleep(0.2)
                reporter.update(sum(
                    struct.unpack_from("<Q", counters, 8 * i)[0]
                    for i in range(workers)))
                if pressure is not None:
                    pressure.poll()
    finally:
        outputs = []
        for _pid, r in procs:
            with os.fdopen(r, "r", encoding="utf-8") as f:
                outputs.append(f.read())
        counters.close()
        if pressure is not None:
            pressure.reapply()

    lines = [ln for out in outputs for ln in out.splitlines() if ln]
    if kind == "fill":
        written = [tuple(int(x) for x in ln.split()) for ln in lines]
        return sorted(written, key=lambda s: s[1])
    if failed and not _stop:
        lines.append(f"FEHLER: {failed} Pruef-Prozess(e) unerwartet beendet")
    return lines


def _verify_parallel(chunks: List[Chunk], builder: PatternBuilder,
                     limit: Optional[int] = None,
                     workers: Optional[int] = None) -> List[str]:
    return _run_parallel("verify", chunks, builder, _segments(chunks, limit), workers)


_DATA_LINE_MB = 128  # groesser als typische L3-Caches -> echte DRAM-Zugriffe


def _data_line_test(size_mb: int = _DATA_LINE_MB,
                    deadline: float = float("inf"),
                    patterns: Optional[List[Tuple[str, int]]] = None,
                    corrupt: Optional[Callable[[mmap.mmap], None]] = None
                    ) -> Tuple[int, List[str]]:
    """64-Bit Walking-One/Zero ueber einen kleinen Bereich.

    Liefert (geprufte Muster, Fehler). Fehler nennen die betroffenen
    Datenbits (XOR von erwartetem und gelesenem 64-Bit-Wort). *corrupt*
    dient nur Tests (wird nach jedem Schreiben aufgerufen).
    """
    size = size_mb * 1024 * 1024
    try:
        m = mmap.mmap(-1, size, flags=mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
    except (OSError, ValueError):
        return 0, []
    _mlock(m, size)
    errors: List[str] = []
    done = 0
    try:
        for name, word in patterns or _data_line_patterns():
            if _stop or time.monotonic() >= deadline:
                break
            builder = _const_word_builder(word)
            for pos in range(0, size, _BUF_SIZE):
                piece = min(_BUF_SIZE, size - pos)
                m[pos:pos + piece] = builder(pos, piece)
            if corrupt is not None:
                corrupt(m)
            for pos in range(0, size, _BUF_SIZE):
                piece = min(_BUF_SIZE, size - pos)
                actual = m[pos:pos + piece]
                if actual == builder(pos, piece):
                    continue
                for q in range(0, piece, 8):
                    got = int.from_bytes(actual[q:q + 8], "little")
                    if got == word:
                        continue
                    diff = got ^ word
                    bits = ",".join(str(b) for b in range(64) if diff >> b & 1)
                    errors.append(
                        f"MISMATCH bei Offset {pos + q} (Datenleitungstest, "
                        f"{name}): erwartet 0x{word:016X}, gelesen 0x{got:016X}, "
                        f"Datenbit(s) {bits}{_phys_suffix(m, pos + q)}")
                    break
                if len(errors) >= 50:
                    errors.append("... weitere Fehler unterdrueckt (>50 Treffer)")
                    return done, errors
            done += 1
    finally:
        _free_chunks([(m, size)])
    return done, errors


def _moving_inversion(chunks: List[Chunk], builder: PatternBuilder,
                      segs: List[Segment], rotation: int, half: int,
                      pressure: Optional[_Pressure] = None,
                      workers: Optional[int] = None,
                      cpu_offset: int = 0) -> List[str]:
    """Lesen + vergleichen + invertiert schreiben, danach das Invertierte pruefen.

    Erkennt Bits, die zwischen Schreiben und erneutem Lesen kippen
    (Haltefehler) und Kopplungen beim Umschalten aller Bits.
    """
    print("  Moving Inversion: pruefen und invertieren ...", flush=True)
    t0 = time.monotonic()
    errs = _run_parallel("invert", chunks, builder, segs, workers=workers,
                         rotation=rotation + 1, pressure=pressure,
                         cpu_offset=cpu_offset)
    if not errs and not _stop:
        if pressure is not None:
            segs = pressure.keep(segs)
        errs = _run_parallel("verify", chunks, _inverted_builder(builder), segs,
                             workers=workers, rotation=rotation + 1 + half,
                             pressure=pressure, cpu_offset=cpu_offset)
    secs = time.monotonic() - t0
    if errs:
        print(f"  Moving Inversion: FEHLER ({len(errs)} Treffer, {secs:.1f}s)",
              flush=True)
        for e in errs:
            print(f"    *** {e}", flush=True)
    else:
        print(f"  Moving Inversion: OK ({secs:.1f}s)", flush=True)
    return errs


# ---------------------------------------------------------------------------
# Hauptschleife
# ---------------------------------------------------------------------------

def run_cycles(
    duration_seconds: int,
    memory_mb: int = 0,
    reserve_mb: int = 512,
    chunk_mb: int = 256,
    quick: bool = False,
    pause_seconds: float = 3.0,
) -> int:
    start = time.monotonic()
    end = start + duration_seconds
    cycle = 0
    total_errors = 0
    verified_cycles = 0
    verified_mb = 0

    print("=== Zyklischer RAM-Fuelltest gestartet ===")
    if reserve_mb < _min_reserve_mb():
        reserve_mb = _min_reserve_mb()
    print(f"Dauer: {duration_seconds}s | Reserve: {reserve_mb} MB | "
          f"Chunk: {chunk_mb} MB | Pause: {pause_seconds:.0f}s")
    print("Ablauf je Zyklus: allokieren -> Muster schreiben -> verifizieren "
          "-> freigeben -> Pause (Speicher sichtbar leer).")
    _set_oom_score_adj(500)
    _raise_memlock_limit()
    print(flush=True)

    lock_warned = False
    lock_max_mb = 0
    dropped_total_mb = 0
    pressure_events = 0
    inversion_done = 0
    inversion_skipped = 0
    data_line_runs = 0
    data_line_errors = 0

    # Muster-Plan; wird nach jedem Durchlauf mit neuem Seed neu erzeugt, damit
    # die Muster von Runde zu Runde wechseln.
    plan_round = 1
    plan = _build_pattern_plan(quick=quick, cycle=plan_round)
    plan_idx = 0

    while not _stop:
        elapsed = time.monotonic() - start
        if elapsed >= duration_seconds:
            break

        # Naechstes Muster waehlen; Plan bei Bedarf mit neuem Seed neu bauen.
        if plan_idx >= len(plan):
            plan_round += 1
            plan = _build_pattern_plan(quick=quick, cycle=plan_round)
            plan_idx = 0

        # Datenleitungstest einmal je Musterrunde (nur Vollmodus).
        if plan_idx == 0 and not quick:
            print(f"--- Datenleitungstest: 64-Bit Walking-One/Zero ueber "
                  f"{_DATA_LINE_MB} MB ---", flush=True)
            t0 = time.monotonic()
            n_pat, dl_errs = _data_line_test(deadline=end)
            if n_pat:
                data_line_runs += 1
            data_line_errors += len(dl_errs)
            total_errors += len(dl_errs)
            for e in dl_errs:
                print(f"    *** {e}", flush=True)
            print(f"  Datenleitungstest: {'FEHLER' if dl_errs else 'OK'} "
                  f"({n_pat}/128 Muster, {time.monotonic() - t0:.1f}s)", flush=True)
            print(flush=True)
            if _stop or time.monotonic() >= end:
                break

        name, builder = plan[plan_idx]
        plan_idx += 1

        cycle += 1
        remaining_time = duration_seconds - elapsed

        if memory_mb > 0:
            avail = _available_memory_mb()
            target_mb = max(0, min(memory_mb, avail - reserve_mb))
            if target_mb < memory_mb:
                print(
                    f"Begrenze angeforderte {memory_mb} MB auf {target_mb} MB "
                    f"(verfügbar {avail} MB, Reserve {reserve_mb} MB).",
                    flush=True,
                )
        else:
            avail = _available_memory_mb()
            target_mb = max(64, avail - reserve_mb)

        print(f"--- Zyklus {cycle}: Muster {name} "
              f"(verbleibend: {remaining_time:.0f}s) ---")
        print(f"Allokiere {target_mb} MB in {chunk_mb}-MB-Bloecken ...",
              end="", flush=True)

        t0 = time.monotonic()
        chunks = _allocate_chunks(target_mb, chunk_mb)
        actual_mb = sum(size for _, size in chunks) // (1024 * 1024)
        alloc_time = time.monotonic() - t0
        print(f" {actual_mb} MB / {len(chunks)} Bloecke ({alloc_time:.1f}s)",
              flush=True)

        if actual_mb == 0:
            print("WARNUNG: Konnte keinen Speicher allokieren!", flush=True)
            time.sleep(1)
            continue

        cycle_errors: List[str] = []
        written_mb = 0
        written_segs: List[Segment] = []
        rotation = 0
        half = _usable_cpus() // 2
        # Die fuer OS/GUI frei gelassenen Kerne wechseln je Zyklus.
        all_cpus = _all_cpus()
        cpu_offset = (cycle - 1) * max(1, len(all_cpus) - _usable_cpus())
        pressure = _Pressure(chunks, reserve_mb)
        try:
            if not _stop:
                print(f"  Schreibe {name} ({actual_mb} MB) ...", flush=True)
                spare = _spare_cpus(all_cpus, min(_usable_cpus(), len(chunks)),
                                    cpu_offset)
                if spare and len(chunks) >= _usable_cpus():
                    print(f"    Frei fuer OS/GUI: CPU {', '.join(map(str, spare))}",
                          flush=True)
                t0 = time.monotonic()
                rotation = cycle * (half + 1)
                written_segs = pressure.keep(_run_parallel(
                    "fill", chunks, builder, _segments(chunks, None),
                    rotation=rotation,
                    deadline=t0 + max(0.0, end - t0) * _WRITE_SHARE,
                    pressure=pressure, cpu_offset=cpu_offset))
                written_mb = sum(s[2] for s in written_segs) // (1024 * 1024)
                write_time = time.monotonic() - t0
                speed = written_mb / write_time if write_time > 0 else 0
                print(f"  Schreiben fertig: {write_time:.1f}s ({speed:.0f} MB/s)", flush=True)
                if not _stop and written_mb < actual_mb and not pressure.dropped_bytes:
                    print(f"  Testzeit reicht nicht fuer den ganzen Zyklus – "
                          f"pruefe die geschriebenen {written_mb} MB.", flush=True)

            if not _stop and written_mb > 0:
                t0 = time.monotonic()
                locked, err = _lock_segments(chunks, written_segs)
                lock_max_mb = max(lock_max_mb, locked >> 20)
                if err and not lock_warned:
                    lock_warned = True
                    print(f"  WARNUNG: Speicher nur teilweise gegen Auslagern "
                          f"gesperrt ({locked >> 20} von {written_mb} MB, mlock: "
                          f"{os.strerror(err)}). Als root ausfuehren fuer volle "
                          f"Sperre.", flush=True)
                    if _swap_active():
                        print("  WARNUNG: Swap ist aktiv – ungesperrter Testspeicher "
                              "kann ausgelagert werden; der Test prueft dann "
                              "teilweise die Auslagerung statt des RAM.", flush=True)
                elif not err:
                    print(f"  Gesperrt (mlock): {locked >> 20} MB "
                          f"({time.monotonic() - t0:.1f}s)", flush=True)

            if not _stop and written_mb > 0:
                print(f"  Verifiziere {name} ({written_mb} MB) ...", flush=True)
                t0 = time.monotonic()
                # Pruefen durch andere CPUs als beim Schreiben (halbe Runde versetzt).
                errs = _run_parallel("verify", chunks, builder, written_segs,
                                     rotation=rotation + half, pressure=pressure,
                                     cpu_offset=cpu_offset)
                verify_time = time.monotonic() - t0
                written_segs = pressure.keep(written_segs)
                written_mb = sum(s[2] for s in written_segs) // (1024 * 1024)
                speed = written_mb / verify_time if verify_time > 0 else 0

                if errs:
                    print(f"  Verifizieren: FEHLER ({len(errs)} Treffer, "
                          f"{verify_time:.1f}s)", flush=True)
                    for e in errs:
                        print(f"    *** {e}", flush=True)
                    cycle_errors.extend(errs)
                elif written_mb > 0:
                    print(f"  Verifizieren: OK ({verify_time:.1f}s, {speed:.0f} MB/s)",
                          flush=True)
                    verified_cycles += 1
                    verified_mb += written_mb
                    if not quick and not _stop:
                        need = (write_time + 2 * verify_time) * 1.1
                        if end - time.monotonic() < need:
                            inversion_skipped += 1
                            print(f"  Moving Inversion uebersprungen (Restzeit < "
                                  f"{need:.0f}s).", flush=True)
                        else:
                            errs = _moving_inversion(
                                chunks, builder, written_segs, rotation, half,
                                pressure, cpu_offset=cpu_offset)
                            cycle_errors.extend(errs)
                            if not _stop:
                                inversion_done += 1
        finally:
            dropped_total_mb += pressure.dropped_bytes >> 20
            pressure_events += pressure.events
            pressure.close()
            print(f"  Freigabe {actual_mb} MB ...", end="", flush=True)
            t0 = time.monotonic()
            _free_chunks(chunks)
            free_time = time.monotonic() - t0
            print(f" {free_time:.1f}s", flush=True)
            if pressure.dropped_bytes:
                print(f"  Wegen Speicherdruck verworfen (nicht geprueft): "
                      f"{pressure.dropped_bytes >> 20} MB", flush=True)

        total_errors += len(cycle_errors)
        elapsed = time.monotonic() - start
        print(
            f"Zyklus {cycle} abgeschlossen: "
            f"{'FEHLER' if cycle_errors else 'OK'} | "
            f"Laufzeit: {elapsed:.0f}s / {duration_seconds}s | "
            f"Gesamtfehler: {total_errors}",
            flush=True,
        )

        # Pause zwischen den Zyklen: Speicher bleibt freigegeben, damit der
        # Rueckgang in Monitoren wie btop sichtbar wird. Nur pausieren, wenn
        # noch genug Zeit fuer einen weiteren Zyklus bleibt.
        elapsed = time.monotonic() - start
        if pause_seconds > 0 and not _stop and elapsed < duration_seconds:
            remaining = duration_seconds - elapsed
            wait = min(pause_seconds, remaining)
            print(f"  Pause {wait:.0f}s (Speicher freigegeben) ...",
                  flush=True)
            pause_end = time.monotonic() + wait
            while not _stop and time.monotonic() < pause_end:
                time.sleep(min(0.2, pause_end - time.monotonic()))

        print(flush=True)

    total_time = time.monotonic() - start
    print("=== Fuelltest beendet ===")
    print(f"Zyklen: {cycle} | Dauer: {total_time:.0f}s | "
          f"Fehler gesamt: {total_errors} | Verifiziert: "
          f"{verified_cycles} Zyklen / {verified_mb} MB")
    if lock_warned:
        print(f"Speichersperre (mlock): teilweise, max. {lock_max_mb} MB")
    elif lock_max_mb:
        print(f"Speichersperre (mlock): aktiv, max. {lock_max_mb} MB")
    if dropped_total_mb:
        print(f"Speicherdruck: {pressure_events} Mal, {dropped_total_mb} MB "
              f"verworfen")
    if not quick:
        print(f"Moving Inversion: {inversion_done} Durchlaeufe, "
              f"{inversion_skipped} uebersprungen (Zeit)")
        print(f"Datenleitungstest: {data_line_runs} Durchlaeufe, "
              f"{data_line_errors} Fehler")
    if total_errors > 0:
        print(f"ERGEBNIS: FEHLER GEFUNDEN – {total_errors} Verifikationsfehler")
        return 1
    if verified_cycles == 0 or verified_mb == 0:
        print("ERGEBNIS: NICHT AUSSAGEKRÄFTIG – keine verifizierten Zyklen")
        return 2
    print("ERGEBNIS: BESTANDEN – Keine Fehler gefunden")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Zyklischer RAM-Fuelltest: vollschreiben, verifizieren, "
                    "freigeben, wiederholen.",
    )
    parser.add_argument(
        "--duration", type=int, default=300,
        help="Testdauer in Sekunden (Standard: 300)",
    )
    parser.add_argument(
        "--memory-mb", type=int, default=0,
        help="Zu allokierender Speicher in MB "
             "(Standard: automatisch = verfuegbar - reserve)",
    )
    parser.add_argument(
        "--reserve-mb", type=int, default=512,
        help="Reserve fuer OS/GUI in MB (Standard: 512)",
    )
    parser.add_argument(
        "--chunk-mb", type=int, default=256,
        help="Allokations-Blockgroesse in MB (Standard: 256)",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Schnellmodus: nur 0xAA, 0x55 und Address-in-Data Muster.",
    )
    parser.add_argument(
        "--pause-seconds", type=float, default=3.0,
        help="Pause zwischen den Zyklen in Sekunden, waehrend der der "
             "Speicher freigegeben bleibt (Standard: 3). 0 = keine Pause.",
    )
    args = parser.parse_args(argv)
    try:
        return run_cycles(
            duration_seconds=args.duration,
            memory_mb=args.memory_mb,
            reserve_mb=args.reserve_mb,
            chunk_mb=args.chunk_mb,
            quick=args.quick,
            pause_seconds=args.pause_seconds,
        )
    except KeyboardInterrupt:
        print("Abbruch durch Benutzer (SIGINT).", flush=True)
        return 0
    except Exception as exc:
        print(f"FEHLER: Unerwartete Ausnahme: {exc}", flush=True)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
