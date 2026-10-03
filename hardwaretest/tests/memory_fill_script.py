#!/usr/bin/env python3
"""Zyklischer RAM-Fuelltest (eigenstaendiges Skript).

Ablauf pro Zyklus:
  1. Verfuegbaren Speicher ermitteln (abzueglich Reserve).
  2. Speicher in Chunks allokieren (mmap).
  3. Mehrere Bitmuster nacheinander schreiben + verifizieren:
       - 0xAA, 0x55, 0xFF, 0x00 (Stuck-Bit-Erkennung)
       - Walking-Ones / Walking-Zeros (Bit-Aliasing)
       - Address-in-Data       (Adress-Decoder-Fehler)
       - Pseudo-Random         (mit Seed, reproduzierbar)
  4. Alle Chunks freigeben (munmap).
  5. Naechster Zyklus, bis die Dauer abgelaufen ist.

Exit-Codes:
  0 – Kein Fehler erkannt.
  1 – Verifikations-Fehler (moeglicher RAM-Defekt).
  2 – Sonstiger Fehler (z.B. Allokationsfehler, unerwartete Exception).
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import mmap
import random
import signal
import struct
import sys
import time
import traceback
from typing import Callable, List, Tuple

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

_stop = False


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
                          flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS,
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
        (f"Random seed=0x{rseed1:08X}", _random_builder(rseed1)),
        (f"Random seed=0x{rseed2:08X}", _random_builder(rseed2)),
    ]
    return plan


# ---------------------------------------------------------------------------
# Fill / Verify
# ---------------------------------------------------------------------------

def _fill(chunks: List[Chunk], builder: PatternBuilder) -> None:
    """Schreibt das vom *builder* erzeugte Muster in alle Chunks."""
    global_offset = 0
    for m, total in chunks:
        if _stop:
            return
        m.seek(0)
        pos = 0
        while pos < total:
            if _stop:
                return
            piece = min(_BUF_SIZE, total - pos)
            m.write(builder(global_offset + pos, piece))
            pos += piece
        global_offset += total


def _verify(chunks: List[Chunk], builder: PatternBuilder) -> List[str]:
    """Vergleicht den Inhalt aller Chunks mit dem erwarteten Muster.

    Nutzt ``mmap.read`` + ``bytes``-Vergleich (memcmp): das ist ~14x schneller
    als der Vergleich von ``memoryview``-Slices, der in CPython byteweise
    statt per memcmp ausgewertet wird und die Verifikation auf ~500 MB/s
    bremste.
    """
    errors: List[str] = []
    global_offset = 0
    for idx, (m, total) in enumerate(chunks):
        if _stop:
            break
        m.seek(0)
        pos = 0
        while pos < total:
            if _stop:
                break
            piece = min(_BUF_SIZE, total - pos)
            expected = builder(global_offset + pos, piece)
            actual = m.read(piece)
            if actual != expected:
                for byte_idx, (got, want) in enumerate(
                    zip(actual, expected, strict=False)
                ):
                    if got != want:
                        abs_offset = global_offset + pos + byte_idx
                        errors.append(
                            f"MISMATCH bei Offset {abs_offset} "
                            f"(Chunk {idx}): erwartet 0x{want:02X}, "
                            f"gelesen 0x{got:02X}"
                        )
                        break
                if len(errors) >= 50:
                    errors.append(
                        "... weitere Fehler unterdrueckt (>50 Treffer)"
                    )
                    return errors
            pos += piece
        global_offset += total
    return errors


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
    cycle = 0
    total_errors = 0
    verified_cycles = 0
    verified_mb = 0

    print("=== Zyklischer RAM-Fuelltest gestartet ===")
    print(f"Dauer: {duration_seconds}s | Reserve: {reserve_mb} MB | "
          f"Chunk: {chunk_mb} MB | Pause: {pause_seconds:.0f}s")
    print("Ablauf je Zyklus: allokieren -> Muster schreiben -> verifizieren "
          "-> freigeben -> Pause (Speicher sichtbar leer).")
    print(flush=True)

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
        try:
            if not _stop:
                print(f"  Schreibe {name} ({actual_mb} MB) ...",
                      end="", flush=True)
                t0 = time.monotonic()
                _fill(chunks, builder)
                write_time = time.monotonic() - t0
                speed = actual_mb / write_time if write_time > 0 else 0
                print(f" {write_time:.1f}s ({speed:.0f} MB/s)", flush=True)

            if not _stop:
                print(f"  Verifiziere {name} ...", end="", flush=True)
                t0 = time.monotonic()
                errs = _verify(chunks, builder)
                verify_time = time.monotonic() - t0
                speed = actual_mb / verify_time if verify_time > 0 else 0

                if errs:
                    print(f" FEHLER ({len(errs)} Treffer, "
                          f"{verify_time:.1f}s)", flush=True)
                    for e in errs:
                        print(f"    *** {e}", flush=True)
                    cycle_errors.extend(errs)
                else:
                    print(f" OK ({verify_time:.1f}s, {speed:.0f} MB/s)",
                          flush=True)
                    verified_cycles += 1
                    verified_mb += actual_mb
        finally:
            print(f"  Freigabe {actual_mb} MB ...", end="", flush=True)
            t0 = time.monotonic()
            _free_chunks(chunks)
            free_time = time.monotonic() - t0
            print(f" {free_time:.1f}s", flush=True)

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
