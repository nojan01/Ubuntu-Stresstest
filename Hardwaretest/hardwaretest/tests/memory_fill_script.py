#!/usr/bin/env python3
"""Zyklischer RAM-Fuelltest (eigenstaendiges Skript).

Ablauf pro Zyklus:
  1. Verfuegbaren Speicher ermitteln (abzueglich Reserve).
  2. Speicher in Chunks allokieren (mmap).
  3. Muster 0xAA in alle Chunks schreiben.
  4. Alle Chunks verifizieren.
  5. Muster 0x55 schreiben + verifizieren.
  6. Alle Chunks freigeben (munmap).
  7. Naechster Zyklus, bis die Dauer abgelaufen ist.

Exit-Codes:
  0 – Kein Fehler erkannt.
  1 – Verifikations-Fehler (moeglicher RAM-Defekt).
  2 – Sonstiger Fehler.
"""

from __future__ import annotations

import argparse
import mmap
import os
import signal
import sys
import time
from typing import List, Tuple

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

def _available_memory_mb() -> int:
    """Verfuegbaren RAM ueber /proc/meminfo lesen (Linux)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except OSError:
        pass
    # Fallback: psutil
    try:
        import psutil
        return int(psutil.virtual_memory().available // (1024 * 1024))
    except Exception:
        return 1024  # Minimalwert


# Typ-Alias: (mmap-Objekt, Groesse in Bytes)
Chunk = Tuple[mmap.mmap, int]


def _allocate_chunks(total_mb: int, chunk_mb: int) -> List[Chunk]:
    """Allokiert *total_mb* in Stuecken von *chunk_mb* via anonymem mmap.

    Gibt eine Liste von (mmap, size_bytes)-Tupeln zurueck.
    Die Groesse wird separat gespeichert, damit kein .size()-Syscall
    auf dem fd noetig ist (vermeidet 'Bad file descriptor' bei vielen Chunks).
    """
    chunks: List[Chunk] = []
    remaining = total_mb
    while remaining > 0 and not _stop:
        size_mb = min(chunk_mb, remaining)
        size_bytes = size_mb * 1024 * 1024
        try:
            m = mmap.mmap(-1, size_bytes, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS,
                          prot=mmap.PROT_READ | mmap.PROT_WRITE)
            chunks.append((m, size_bytes))
            remaining -= size_mb
        except (OSError, OverflowError, ValueError):
            # Nicht genug Speicher – mit dem bisher Allozierten weiterarbeiten
            break
    return chunks


def _fill_pattern(chunks: List[Chunk], pattern: int) -> None:
    """Schreibt *pattern* (0-255) in alle Chunks."""
    buf_size = 4 * 1024 * 1024  # 4 MB Schreibblock
    fill = bytes([pattern]) * buf_size
    for m, total in chunks:
        if _stop:
            return
        m.seek(0)
        written = 0
        while written < total:
            if _stop:
                return
            piece = min(buf_size, total - written)
            if piece < buf_size:
                m.write(bytes([pattern]) * piece)
            else:
                m.write(fill)
            written += piece


def _verify_pattern(chunks: List[Chunk], pattern: int) -> List[str]:
    """Verifiziert den Inhalt aller Chunks. Gibt Fehlerliste zurueck."""
    errors: List[str] = []
    read_size = 4 * 1024 * 1024
    expected = bytes([pattern]) * read_size
    chunk_offset_mb = 0
    for idx, (m, total) in enumerate(chunks):
        if _stop:
            break
        m.seek(0)
        pos = 0
        while pos < total:
            if _stop:
                break
            piece_len = min(read_size, total - pos)
            data = m.read(piece_len)
            if piece_len < read_size:
                exp = bytes([pattern]) * piece_len
            else:
                exp = expected
            if data != exp:
                # Genaue Byte-Position finden
                for byte_idx, (got, want) in enumerate(zip(data, exp)):
                    if got != want:
                        abs_offset = chunk_offset_mb * 1024 * 1024 + pos + byte_idx
                        errors.append(
                            f"MISMATCH bei Offset {abs_offset} (Chunk {idx}): "
                            f"erwartet 0x{want:02X}, gelesen 0x{got:02X}"
                        )
                        break
                if len(errors) >= 50:
                    errors.append("... weitere Fehler unterdrueckt (>50 Treffer)")
                    return errors
            pos += piece_len
        chunk_offset_mb += total // (1024 * 1024)
    return errors


def _free_chunks(chunks: List[Chunk]) -> None:
    """Gibt alle Chunks frei."""
    for m, _size in chunks:
        try:
            m.close()
        except Exception:
            pass
    chunks.clear()


# ---------------------------------------------------------------------------
# Hauptschleife
# ---------------------------------------------------------------------------

def run_cycles(
    duration_seconds: int,
    memory_mb: int = 0,
    reserve_mb: int = 512,
    chunk_mb: int = 256,
) -> int:
    """Fuehrt die Fuell-/Freigabe-Zyklen durch. Gibt Exit-Code zurueck."""
    global _stop

    start = time.monotonic()
    cycle = 0
    total_errors = 0
    patterns = [0xAA, 0x55, 0xFF, 0x00]

    print(f"=== Zyklischer RAM-Fuelltest gestartet ===")
    print(f"Dauer: {duration_seconds}s | Reserve: {reserve_mb} MB | Chunk: {chunk_mb} MB")
    print(flush=True)

    while not _stop:
        elapsed = time.monotonic() - start
        if elapsed >= duration_seconds:
            break

        cycle += 1
        remaining_time = duration_seconds - elapsed

        # Verfuegbaren Speicher ermitteln
        if memory_mb > 0:
            target_mb = memory_mb
        else:
            avail = _available_memory_mb()
            target_mb = max(64, avail - reserve_mb)

        print(f"--- Zyklus {cycle} (verbleibend: {remaining_time:.0f}s) ---")
        print(f"Allokiere {target_mb} MB in {chunk_mb}-MB-Bloecken ...", flush=True)

        # 1. Allokieren
        t0 = time.monotonic()
        chunks = _allocate_chunks(target_mb, chunk_mb)
        actual_mb = sum(size for _, size in chunks) // (1024 * 1024)
        alloc_time = time.monotonic() - t0
        print(f"Allokiert: {actual_mb} MB in {len(chunks)} Bloecken ({alloc_time:.1f}s)", flush=True)

        if actual_mb == 0:
            print("WARNUNG: Konnte keinen Speicher allokieren!", flush=True)
            time.sleep(1)
            continue

        # 2. Muster schreiben + verifizieren
        cycle_errors: List[str] = []
        for pattern in patterns:
            if _stop:
                break
            elapsed = time.monotonic() - start
            if elapsed >= duration_seconds:
                break

            print(f"  Schreibe Muster 0x{pattern:02X} ({actual_mb} MB) ...", end="", flush=True)
            t0 = time.monotonic()
            _fill_pattern(chunks, pattern)
            write_time = time.monotonic() - t0
            speed = actual_mb / write_time if write_time > 0 else 0
            print(f" {write_time:.1f}s ({speed:.0f} MB/s)", flush=True)

            if _stop:
                break

            print(f"  Verifiziere Muster 0x{pattern:02X} ...", end="", flush=True)
            t0 = time.monotonic()
            errs = _verify_pattern(chunks, pattern)
            verify_time = time.monotonic() - t0
            speed = actual_mb / verify_time if verify_time > 0 else 0

            if errs:
                print(f" FEHLER ({len(errs)} Treffer, {verify_time:.1f}s)", flush=True)
                for e in errs:
                    print(f"    *** {e}", flush=True)
                cycle_errors.extend(errs)
            else:
                print(f" OK ({verify_time:.1f}s, {speed:.0f} MB/s)", flush=True)

        # 3. Freigeben
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
        print(flush=True)

    # Zusammenfassung
    total_time = time.monotonic() - start
    print(f"=== Fuelltest beendet ===")
    print(f"Zyklen: {cycle} | Dauer: {total_time:.0f}s | Fehler gesamt: {total_errors}")
    if total_errors > 0:
        print(f"ERGEBNIS: FEHLER GEFUNDEN – {total_errors} Verifikationsfehler")
        return 1
    else:
        print(f"ERGEBNIS: BESTANDEN – Keine Fehler gefunden")
        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zyklischer RAM-Fuelltest: vollschreiben, verifizieren, freigeben, wiederholen."
    )
    parser.add_argument(
        "--duration", type=int, default=300,
        help="Testdauer in Sekunden (Standard: 300)",
    )
    parser.add_argument(
        "--memory-mb", type=int, default=0,
        help="Zu allokierender Speicher in MB (Standard: automatisch = verfuegbar - reserve)",
    )
    parser.add_argument(
        "--reserve-mb", type=int, default=512,
        help="Reserve fuer OS/GUI in MB (Standard: 512)",
    )
    parser.add_argument(
        "--chunk-mb", type=int, default=256,
        help="Allokations-Blockgroesse in MB (Standard: 256)",
    )
    args = parser.parse_args()
    exit_code = run_cycles(
        duration_seconds=args.duration,
        memory_mb=args.memory_mb,
        reserve_mb=args.reserve_mb,
        chunk_mb=args.chunk_mb,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
