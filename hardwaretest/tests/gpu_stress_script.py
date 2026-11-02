"""NVIDIA-GPU-Lasttest ohne CUDA-Toolkit.

Nutzt nur die Treiberbibliothek ``libcuda.so.1`` (ctypes). Die Kernel liegen
als PTX vor und werden vom Treiber zur Laufzeit übersetzt. Je GPU läuft ein
eigener Prozess, der abwechselnd

* eine Rechenlast (FMA-Ketten, jedes Ergebnis muss identisch sein) und
* einen Speichertest (Muster schreiben und auf der GPU prüfen)

ausführt. Kernel bleiben kurz (< 1 s), damit der Display-Watchdog bei
Grafikkarten mit angeschlossenem Monitor nicht greift.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import os
import signal
import struct
import sys
import threading
import time
from typing import Dict, List, Optional

# --- CPU-Referenz der Rechenlast ----------------------------------------------
FMA_A = 0.999999
FMA_B = 1e-6
FMA_START = (0.0, 0.5, 1.0, 2.0)
FMA_TOLERANCE = 1e-5  # ~20 ulp bei Ergebnissen um 4.0


def _round_f32(values):
    return struct.unpack("<%df" % len(values), struct.pack("<%df" % len(values), *values))


class FmaReference:
    """Unabhängig auf der CPU berechnete Referenz der FMA-Ketten des GPU-Kernels.

    Die Kette ``x = fma(x, a, b)`` wird exakt in fp32 nachgebildet: Produkt und
    Summe passen in ein fp64, das Ergebnis wird je Schritt auf fp32 gerundet.
    Zusätzlich wird die nicht verschmolzene Variante ``round(round(x*a)+b)``
    geführt, weil manche Treiber ohne FMA rechnen. Beide Ketten erreichen nach
    wenigen Millionen Schritten einen Fixpunkt, danach ist das Ergebnis für
    jede größere Iterationszahl konstant. Die Berechnung läuft im Hintergrund;
    solange die Referenz für eine Iterationszahl fehlt, wird der Vergleich
    übersprungen (``None``).
    """

    def __init__(self, start: bool = True) -> None:
        self.a, self.b = _round_f32((FMA_A, FMA_B))
        self._lock = threading.Lock()
        self._sums: Dict[int, tuple] = {}
        self._final: Optional[tuple] = None
        self._thread = threading.Thread(target=self.compute_all, daemon=True) if start else None
        if self._thread is not None:
            self._thread.start()

    @staticmethod
    def _sum(values) -> float:
        left, right = _round_f32((values[0] + values[1], values[2] + values[3]))
        return _round_f32((left + right,))[0]

    def compute_all(self, limit: int = 1 << 25) -> None:
        a, b = self.a, self.b
        fused = list(_round_f32(FMA_START))
        plain = list(fused)
        n, checkpoint = 0, 1
        fused_done = plain_done = False
        while n < limit and not (fused_done and plain_done):
            if not fused_done:
                new = list(_round_f32([x * a + b for x in fused]))
                fused_done = new == fused
                fused = new
            if not plain_done:
                new = list(_round_f32([x + b for x in _round_f32([x * a for x in plain])]))
                plain_done = new == plain
                plain = new
            n += 1
            if n == checkpoint:
                with self._lock:
                    self._sums[n] = (self._sum(fused), self._sum(plain))
                checkpoint *= 2
        if fused_done and plain_done:
            # Fixpunkt erreicht: ab hier ist das Ergebnis fuer jede Iterationszahl gleich.
            with self._lock:
                self._final = (self._sum(fused), self._sum(plain))

    def lookup(self, iterations: int) -> Optional[tuple]:
        """(verschmolzen, unverschmolzen) für ``iterations`` Schritte oder None, falls noch nicht berechnet."""
        with self._lock:
            if iterations in self._sums:
                return self._sums[iterations]
            if self._final is not None and iterations >= max(self._sums, default=0):
                return self._final
        return None

    def check(self, value: float, iterations: int) -> Optional[str]:
        """None = passt (oder keine Referenz), sonst Fehlertext."""
        expected = self.lookup(iterations)
        if expected is None:
            return None
        if any(abs(value - ref) <= FMA_TOLERANCE for ref in expected):
            return None
        return (f"Rechenergebnis {value!r} weicht von CPU-Referenz {expected[0]!r} ab "
                f"({iterations} Iterationen)")


_A = FMA_A
_B = FMA_B


def _f32(value: float) -> str:
    return "0f%08X" % struct.unpack("<I", struct.pack("<f", value))[0]


PTX = r"""
.version 6.4
.target sm_52
.address_size 64

.visible .entry ht_fill(.param .u64 p_buf, .param .u64 p_n, .param .u32 p_pat)
{
    .reg .pred %p<2>;
    .reg .b32 %r<10>;
    .reg .b64 %rd<10>;
    ld.param.u64 %rd1, [p_buf];
    ld.param.u64 %rd2, [p_n];
    ld.param.u32 %r1, [p_pat];
    mov.u32 %r2, %ctaid.x;
    mov.u32 %r3, %ntid.x;
    mov.u32 %r4, %tid.x;
    mov.u32 %r5, %nctaid.x;
    mul.wide.u32 %rd3, %r2, %r3;
    cvt.u64.u32 %rd4, %r4;
    add.u64 %rd3, %rd3, %rd4;
    mul.wide.u32 %rd5, %r5, %r3;
FILL_LOOP:
    setp.ge.u64 %p1, %rd3, %rd2;
    @%p1 bra FILL_DONE;
    // Muster aus allen 64 Indexbits: lo*K1 ^ (hi+1)*K2 ^ pat
    cvt.u32.u64 %r6, %rd3;
    mul.lo.u32 %r6, %r6, -1640531535;
    shr.u64 %rd9, %rd3, 32;
    cvt.u32.u64 %r8, %rd9;
    add.u32 %r8, %r8, 1;
    mul.lo.u32 %r8, %r8, -2048144789;
    xor.b32 %r6, %r6, %r8;
    xor.b32 %r7, %r6, %r1;
    shl.b64 %rd6, %rd3, 2;
    add.u64 %rd7, %rd1, %rd6;
    st.global.u32 [%rd7], %r7;
    add.u64 %rd3, %rd3, %rd5;
    bra FILL_LOOP;
FILL_DONE:
    ret;
}

.visible .entry ht_verify(.param .u64 p_buf, .param .u64 p_n, .param .u32 p_pat, .param .u64 p_err)
{
    .reg .pred %p<3>;
    .reg .b32 %r<12>;
    .reg .b64 %rd<13>;
    ld.param.u64 %rd1, [p_buf];
    ld.param.u64 %rd2, [p_n];
    ld.param.u32 %r1, [p_pat];
    ld.param.u64 %rd8, [p_err];
    add.u64 %rd10, %rd8, 8;
    mov.u32 %r2, %ctaid.x;
    mov.u32 %r3, %ntid.x;
    mov.u32 %r4, %tid.x;
    mov.u32 %r5, %nctaid.x;
    mul.wide.u32 %rd3, %r2, %r3;
    cvt.u64.u32 %rd4, %r4;
    add.u64 %rd3, %rd3, %rd4;
    mul.wide.u32 %rd5, %r5, %r3;
VERIFY_LOOP:
    setp.ge.u64 %p1, %rd3, %rd2;
    @%p1 bra VERIFY_DONE;
    cvt.u32.u64 %r6, %rd3;
    mul.lo.u32 %r6, %r6, -1640531535;
    shr.u64 %rd9, %rd3, 32;
    cvt.u32.u64 %r10, %rd9;
    add.u32 %r10, %r10, 1;
    mul.lo.u32 %r10, %r10, -2048144789;
    xor.b32 %r6, %r6, %r10;
    xor.b32 %r7, %r6, %r1;
    shl.b64 %rd6, %rd3, 2;
    add.u64 %rd7, %rd1, %rd6;
    ld.global.u32 %r8, [%rd7];
    setp.ne.u32 %p2, %r8, %r7;
    // 64-Bit-Zaehler (Anzeige) und 32-Bit-Flag (Pass/Fail) - kein Ueberlauf moeglich
    @%p2 atom.global.add.u64 %rd11, [%rd8], 1;
    @%p2 atom.global.or.b32 %r9, [%rd10], 1;
    add.u64 %rd3, %rd3, %rd5;
    bra VERIFY_LOOP;
VERIFY_DONE:
    ret;
}

.visible .entry ht_compute(.param .u64 p_out, .param .u32 p_iters)
{
    .reg .pred %p<2>;
    .reg .b32 %r<8>;
    .reg .f32 %f<8>;
    .reg .b64 %rd<4>;
    ld.param.u64 %rd1, [p_out];
    ld.param.u32 %r1, [p_iters];
    mov.u32 %r2, %ctaid.x;
    mov.u32 %r3, %ntid.x;
    mov.u32 %r4, %tid.x;
    mad.lo.u32 %r5, %r2, %r3, %r4;
    mov.f32 %f1, 0f00000000;
    mov.f32 %f2, 0f3F000000;
    mov.f32 %f3, 0f3F800000;
    mov.f32 %f4, 0f40000000;
    mov.f32 %f5, @A@;
    mov.f32 %f6, @B@;
    mov.u32 %r6, 0;
COMPUTE_LOOP:
    setp.ge.u32 %p1, %r6, %r1;
    @%p1 bra COMPUTE_DONE;
    fma.rn.f32 %f1, %f1, %f5, %f6;
    fma.rn.f32 %f2, %f2, %f5, %f6;
    fma.rn.f32 %f3, %f3, %f5, %f6;
    fma.rn.f32 %f4, %f4, %f5, %f6;
    add.u32 %r6, %r6, 1;
    bra COMPUTE_LOOP;
COMPUTE_DONE:
    add.f32 %f1, %f1, %f2;
    add.f32 %f3, %f3, %f4;
    add.f32 %f1, %f1, %f3;
    mul.wide.u32 %rd2, %r5, 4;
    add.u64 %rd3, %rd1, %rd2;
    st.global.f32 [%rd3], %f1;
    ret;
}
""".replace("@A@", _f32(_A)).replace("@B@", _f32(_B))

BLOCK = 256
COMPUTE_BLOCKS = 1024
MAX_MEMORY_BLOCKS = 65535
MIN_TEST_BYTES = 64 << 20  # darunter gilt "kein Grafikspeicher testbar"
ERRORS_BYTES = 16  # u64 Zaehler + u32 Flag + Fuellung
EXIT_OK, EXIT_FAILED, EXIT_INCONCLUSIVE = 0, 1, 2


class ErrorCounter(ctypes.Structure):
    """Host-Abbild des Fehlerpuffers von ``ht_verify``."""

    _fields_ = [("count", ctypes.c_uint64), ("flag", ctypes.c_uint32), ("_pad", ctypes.c_uint32)]


def expected_word(index: int, pattern: int) -> int:
    """CPU-Nachbildung des Fuellmusters von ``ht_fill`` fuer den 64-Bit-Wortindex."""
    lo = (index & 0xFFFFFFFF) * 0x9E3779B1
    hi = (((index >> 32) & 0xFFFFFFFF) + 1) * 0x85EBCA6B
    return (lo ^ hi ^ pattern) & 0xFFFFFFFF


class EnvironmentProblem(RuntimeError):
    """Umgebungsproblem (nicht aussagekraeftig), kein Hardwarefehler."""


class CudaError(RuntimeError):
    pass


class Cuda:
    """Minimaler ctypes-Wrapper um die CUDA-Driver-API."""

    def __init__(self, lib=None) -> None:
        self.lib = lib if lib is not None else ctypes.CDLL("libcuda.so.1")

    def call(self, name: str, *args) -> None:
        result = getattr(self.lib, name)(*args)
        if result != 0:
            text = ctypes.c_char_p()
            with contextlib.suppress(Exception):
                self.lib.cuGetErrorString(result, ctypes.byref(text))
            message = text.value.decode() if text.value else ""
            raise CudaError(f"{name} fehlgeschlagen ({result}) {message}".strip())

    def load_ptx(self, ptx: str) -> ctypes.c_void_p:
        """Laedt PTX per JIT; bei Fehlern wird das ptxas-Fehlerprotokoll mitgeliefert."""
        module = ctypes.c_void_p()
        log = ctypes.create_string_buffer(8192)
        # CU_JIT_ERROR_LOG_BUFFER = 5, CU_JIT_ERROR_LOG_BUFFER_SIZE_BYTES = 6
        options = (ctypes.c_int * 2)(5, 6)
        values = (ctypes.c_void_p * 2)(
            ctypes.cast(log, ctypes.c_void_p), ctypes.c_void_p(ctypes.sizeof(log))
        )
        try:
            self.call("cuModuleLoadDataEx", ctypes.byref(module), ctypes.c_char_p(ptx.encode()),
                      ctypes.c_uint(2), options, values)
        except CudaError as exc:
            detail = log.value.decode(errors="replace").strip()
            raise CudaError(f"{exc}: {detail}" if detail else str(exc)) from None
        return module

    def launch(self, function, grid: int, block: int, *args) -> None:
        pointers = (ctypes.c_void_p * len(args))(
            *[ctypes.cast(ctypes.pointer(arg), ctypes.c_void_p) for arg in args]
        )
        self.call(
            "cuLaunchKernel", function,
            ctypes.c_uint(grid), ctypes.c_uint(1), ctypes.c_uint(1),
            ctypes.c_uint(block), ctypes.c_uint(1), ctypes.c_uint(1),
            ctypes.c_uint(0), None, pointers, None,
        )
        self.call("cuCtxSynchronize")


def _say(gpu: int, text: str) -> None:
    sys.stdout.write(f"GPU {gpu}: {text}\n")
    sys.stdout.flush()


def run_gpu(gpu: int, duration: float, memory_percent: int, cuda: Optional[Cuda] = None) -> int:
    """Belastet eine GPU für ``duration`` Sekunden. Rückgabe: Anzahl Fehler."""
    cuda = cuda or Cuda()
    cuda.call("cuInit", ctypes.c_uint(0))
    device = ctypes.c_int()
    cuda.call("cuDeviceGet", ctypes.byref(device), ctypes.c_int(gpu))
    name = ctypes.create_string_buffer(256)
    cuda.call("cuDeviceGetName", name, ctypes.c_int(256), device)
    context = ctypes.c_void_p()
    cuda.call("cuCtxCreate_v2", ctypes.byref(context), ctypes.c_uint(0), device)
    module = cuda.load_ptx(PTX)
    kernels = {}
    for kernel in ("ht_fill", "ht_verify", "ht_compute"):
        handle = ctypes.c_void_p()
        cuda.call("cuModuleGetFunction", ctypes.byref(handle), module, kernel.encode())
        kernels[kernel] = handle

    free = ctypes.c_size_t()
    total = ctypes.c_size_t()
    cuda.call("cuMemGetInfo_v2", ctypes.byref(free), ctypes.byref(total))
    size = (free.value * memory_percent // 100) & ~(1024 * 1024 - 1)
    words = size // 4
    _say(gpu, f"{name.value.decode(errors='replace')}, teste {size / 2**30:.1f} GiB "
              f"von {total.value / 2**30:.1f} GiB")
    if size < MIN_TEST_BYTES:
        cuda.lib.cuCtxDestroy_v2(context)
        raise EnvironmentProblem(
            f"kein Grafikspeicher testbar: {size / 2**20:.0f} MiB belegbar "
            f"(frei gemeldet {free.value / 2**20:.0f} MiB), mindestens {MIN_TEST_BYTES >> 20} MiB nötig")

    buffer = ctypes.c_uint64()
    errors_dev = ctypes.c_uint64()
    out_dev = ctypes.c_uint64()
    threads = COMPUTE_BLOCKS * BLOCK
    cuda.call("cuMemAlloc_v2", ctypes.byref(buffer), ctypes.c_size_t(size))
    cuda.call("cuMemAlloc_v2", ctypes.byref(errors_dev), ctypes.c_size_t(ERRORS_BYTES))
    cuda.call("cuMemAlloc_v2", ctypes.byref(out_dev), ctypes.c_size_t(threads * 4))
    host_out = (ctypes.c_float * threads)()
    host_err = ErrorCounter()

    memory_blocks = min(MAX_MEMORY_BLOCKS, max(1, (words + BLOCK - 1) // BLOCK))
    iterations = 1 << 16
    errors = 0
    cycle = 0
    checked = 0
    cpu_checks = 0
    cpu_reference = FmaReference()
    deadline = time.monotonic() + duration
    next_report = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            cycle += 1
            # Rechenlast: Laufzeit je Kernel auf ca. 0,2 s einregeln.
            start = time.monotonic()
            used_iterations = iterations
            cuda.launch(kernels["ht_compute"], COMPUTE_BLOCKS, BLOCK,
                        out_dev, ctypes.c_uint32(iterations))
            elapsed = time.monotonic() - start
            if elapsed < 0.1 and iterations < (1 << 30):
                iterations *= 2
            elif elapsed > 0.4 and iterations > 1024:
                iterations //= 2
            cuda.call("cuMemcpyDtoH_v2", host_out, out_dev, ctypes.c_size_t(threads * 4))
            reference = host_out[0]
            wrong = sum(1 for value in host_out if value != reference)
            if wrong:
                errors += wrong
                _say(gpu, f"FEHLER: Rechenfehler in {wrong} von {threads} Threads (Zyklus {cycle})")
            mismatch = cpu_reference.check(reference, used_iterations)
            if mismatch:
                errors += 1
                _say(gpu, f"FEHLER: {mismatch} (Zyklus {cycle})")
            elif cpu_reference.lookup(used_iterations) is not None:
                cpu_checks += 1

            # Speichertest mit wechselndem Muster.
            pattern = ctypes.c_uint32((cycle * 0x5BD1E995) & 0xFFFFFFFF)
            cuda.call("cuMemsetD32_v2", errors_dev, ctypes.c_uint32(0), ctypes.c_size_t(ERRORS_BYTES // 4))
            cuda.launch(kernels["ht_fill"], memory_blocks, BLOCK,
                        buffer, ctypes.c_uint64(words), pattern)
            cuda.launch(kernels["ht_verify"], memory_blocks, BLOCK,
                        buffer, ctypes.c_uint64(words), pattern, errors_dev)
            cuda.call("cuMemcpyDtoH_v2", ctypes.byref(host_err), errors_dev, ctypes.c_size_t(ERRORS_BYTES))
            checked += size
            if host_err.flag or host_err.count:
                # Das Flag entscheidet; der Zaehler dient nur der Anzeige.
                bad = max(1, host_err.count)
                errors += bad
                _say(gpu, f"FEHLER: {bad} Speicherfehler (Zyklus {cycle})")

            if time.monotonic() >= next_report:
                next_report += 10
                _say(gpu, f"Zyklus {cycle}, {checked / 2**30:.0f} GiB geprüft, Fehler {errors}")
    finally:
        for pointer in (buffer, errors_dev, out_dev):
            if pointer.value:
                cuda.lib.cuMemFree_v2(pointer)
        cuda.lib.cuCtxDestroy_v2(context)
    _say(gpu, f"Ende nach {cycle} Zyklen, {checked / 2**30:.0f} GiB geprüft, Fehler {errors}")
    _say(gpu, f"Rechenergebnis gegen CPU-Referenz geprüft: {cpu_checks} von {cycle} Zyklen")
    return errors


def worker_exit_code(gpu: int, duration: float, memory_percent: int) -> int:
    """Führt den Test einer GPU aus und liefert den Exit-Code des Kindprozesses."""
    try:
        errors = run_gpu(gpu, duration, memory_percent)
    except EnvironmentProblem as exc:
        _say(gpu, f"FEHLER: nicht aussagekräftig – {exc}")
        return EXIT_INCONCLUSIVE
    except Exception as exc:
        _say(gpu, f"FEHLER: {exc}")
        return EXIT_INCONCLUSIVE
    return EXIT_FAILED if errors else EXIT_OK


def summarize(results: dict) -> int:
    """Gibt die ERGEBNIS-Zeile für die GPU-Exit-Codes aus und liefert den Gesamt-Exit-Code."""
    failed = sorted(gpu for gpu, code in results.items() if code == EXIT_FAILED)
    inconclusive = sorted(gpu for gpu, code in results.items() if code not in (EXIT_OK, EXIT_FAILED))
    if failed:
        print(f"ERGEBNIS: FEHLGESCHLAGEN (GPU {', '.join(map(str, failed))})", flush=True)
        return EXIT_FAILED
    if inconclusive or not results:
        print(f"ERGEBNIS: NICHT AUSSAGEKRÄFTIG (GPU {', '.join(map(str, inconclusive))})", flush=True)
        return EXIT_INCONCLUSIVE
    print("ERGEBNIS: BESTANDEN", flush=True)
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpus", required=True, help="GPU-Indizes, z. B. 0,1")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--memory-percent", type=int, default=80)
    args = parser.parse_args(argv)
    gpus = [int(part) for part in args.gpus.split(",") if part.strip()]
    percent = max(10, min(95, args.memory_percent))

    # Je GPU ein Prozess; fork vor cuInit ist mit CUDA unproblematisch.
    children: dict[int, int] = {}
    parent = os.getpid()

    def forward(signum, _frame):
        for child in children:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGTERM)
        os._exit(128 + signum)

    for gpu in gpus:
        pid = os.fork()
        if pid == 0:
            code = EXIT_INCONCLUSIVE
            try:
                # Endet der Elternprozess (z. B. per SIGKILL), soll die GPU-Last mitenden.
                with contextlib.suppress(OSError, AttributeError):
                    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
                if os.getppid() != parent:
                    os._exit(EXIT_INCONCLUSIVE)
                code = worker_exit_code(gpu, args.duration, percent)
            except Exception as exc:
                _say(gpu, f"FEHLER: {exc}")
            finally:
                sys.stdout.flush()
                os._exit(code)
        children[pid] = gpu
    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    results: dict = {}
    for pid, gpu in children.items():
        _, status = os.waitpid(pid, 0)
        results[gpu] = os.waitstatus_to_exitcode(status)
    return summarize(results)


if __name__ == "__main__":
    sys.exit(main())
