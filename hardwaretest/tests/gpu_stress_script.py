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
import struct
import sys
import time
from typing import List, Optional

_A = 0.999999
_B = 1e-6


def _f32(value: float) -> str:
    return "0f%08X" % struct.unpack("<I", struct.pack("<f", value))[0]


PTX = r"""
.version 6.4
.target sm_52
.address_size 64

.visible .entry ht_fill(.param .u64 p_buf, .param .u64 p_n, .param .u32 p_pat)
{
    .reg .pred %p<2>;
    .reg .b32 %r<8>;
    .reg .b64 %rd<8>;
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
    cvt.u32.u64 %r6, %rd3;
    mul.lo.u32 %r6, %r6, -1640531535;
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
    .reg .b32 %r<10>;
    .reg .b64 %rd<9>;
    ld.param.u64 %rd1, [p_buf];
    ld.param.u64 %rd2, [p_n];
    ld.param.u32 %r1, [p_pat];
    ld.param.u64 %rd8, [p_err];
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
    xor.b32 %r7, %r6, %r1;
    shl.b64 %rd6, %rd3, 2;
    add.u64 %rd7, %rd1, %rd6;
    ld.global.u32 %r8, [%rd7];
    setp.ne.u32 %p2, %r8, %r7;
    @%p2 atom.global.add.u32 %r9, [%rd8], 1;
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
    module = ctypes.c_void_p()
    cuda.call("cuModuleLoadData", ctypes.byref(module), ctypes.c_char_p(PTX.encode()))
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

    buffer = ctypes.c_uint64()
    errors_dev = ctypes.c_uint64()
    out_dev = ctypes.c_uint64()
    threads = COMPUTE_BLOCKS * BLOCK
    cuda.call("cuMemAlloc_v2", ctypes.byref(buffer), ctypes.c_size_t(size))
    cuda.call("cuMemAlloc_v2", ctypes.byref(errors_dev), ctypes.c_size_t(4))
    cuda.call("cuMemAlloc_v2", ctypes.byref(out_dev), ctypes.c_size_t(threads * 4))
    host_out = (ctypes.c_float * threads)()
    host_err = ctypes.c_uint32()

    memory_blocks = min(MAX_MEMORY_BLOCKS, max(1, (words + BLOCK - 1) // BLOCK))
    iterations = 1 << 16
    errors = 0
    cycle = 0
    checked = 0
    deadline = time.monotonic() + duration
    next_report = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            cycle += 1
            # Rechenlast: Laufzeit je Kernel auf ca. 0,2 s einregeln.
            start = time.monotonic()
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

            # Speichertest mit wechselndem Muster.
            pattern = ctypes.c_uint32((cycle * 0x5BD1E995) & 0xFFFFFFFF)
            cuda.call("cuMemsetD32_v2", errors_dev, ctypes.c_uint32(0), ctypes.c_size_t(1))
            cuda.launch(kernels["ht_fill"], memory_blocks, BLOCK,
                        buffer, ctypes.c_uint64(words), pattern)
            cuda.launch(kernels["ht_verify"], memory_blocks, BLOCK,
                        buffer, ctypes.c_uint64(words), pattern, errors_dev)
            cuda.call("cuMemcpyDtoH_v2", ctypes.byref(host_err), errors_dev, ctypes.c_size_t(4))
            checked += size
            if host_err.value:
                errors += host_err.value
                _say(gpu, f"FEHLER: {host_err.value} Speicherfehler (Zyklus {cycle})")

            if time.monotonic() >= next_report:
                next_report += 10
                _say(gpu, f"Zyklus {cycle}, {checked / 2**30:.0f} GiB geprüft, Fehler {errors}")
    finally:
        for pointer in (buffer, errors_dev, out_dev):
            if pointer.value:
                cuda.lib.cuMemFree_v2(pointer)
        cuda.lib.cuCtxDestroy_v2(context)
    _say(gpu, f"Ende nach {cycle} Zyklen, {checked / 2**30:.0f} GiB geprüft, Fehler {errors}")
    return errors


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpus", required=True, help="GPU-Indizes, z. B. 0,1")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--memory-percent", type=int, default=80)
    args = parser.parse_args(argv)
    gpus = [int(part) for part in args.gpus.split(",") if part.strip()]
    percent = max(10, min(95, args.memory_percent))

    # Je GPU ein Prozess; fork vor cuInit ist mit CUDA unproblematisch.
    children = {}
    for gpu in gpus:
        pid = os.fork()
        if pid == 0:
            code = 2
            try:
                code = 1 if run_gpu(gpu, args.duration, percent) else 0
            except Exception as exc:
                _say(gpu, f"FEHLER: {exc}")
            finally:
                sys.stdout.flush()
                os._exit(code)
        children[pid] = gpu

    failed = []
    for pid, gpu in children.items():
        _, status = os.waitpid(pid, 0)
        if os.waitstatus_to_exitcode(status) != 0:
            failed.append(gpu)
    if failed:
        print(f"ERGEBNIS: FEHLGESCHLAGEN (GPU {', '.join(map(str, failed))})", flush=True)
        return 1
    print("ERGEBNIS: BESTANDEN", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
