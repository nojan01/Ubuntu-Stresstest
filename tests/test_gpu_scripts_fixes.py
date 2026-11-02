"""Regressionstests zu den Review-Befunden in den GPU-Lasttest-Skripten."""

import ctypes
from unittest import mock

import pytest

from hardwaretest.tests import gpu_stress_script as gss
from hardwaretest.tests import vulkan_stress_script as vss


class _FakeGpuTest:
    """Ersetzt GpuTest: liefert konfigurierbare Allokation, keine echte GPU."""

    def __init__(self, allocated: int, free: int = 8 << 30, heap: int = 8 << 30):
        self.info = {"name": "Fake GPU", "vendor": "Test", "api": "1.3.0", "type": "discrete"}
        self._allocated = allocated
        self._free, self._heap = free, heap
        self.chunks = [(1, allocated // 4, 1)] if allocated else []
        self.threads = 4
        self.closed = False

    def free_memory(self):
        return self._free, self._heap

    def allocate(self, wanted):
        return self._allocated

    def compute(self, iterations):
        return 0

    def memory_test(self, pattern):
        return 0

    def close(self):
        self.closed = True


def test_run_gpu_rejects_empty_allocation(capsys):
    fake = _FakeGpuTest(allocated=0, free=0)
    with mock.patch.object(vss, "GpuTest", return_value=fake), \
            pytest.raises(vss.EnvironmentProblem, match="kein Grafikspeicher testbar"):
        vss.run_gpu(0, 0.1, 80, vk=object())
    assert fake.closed


def test_run_gpu_rejects_allocation_below_minimum():
    fake = _FakeGpuTest(allocated=vss.MIN_TEST_BYTES - (1 << 20))
    with mock.patch.object(vss, "GpuTest", return_value=fake), \
            pytest.raises(vss.EnvironmentProblem, match="mindestens 64 MiB"):
        vss.run_gpu(0, 0.1, 80, vk=object())


def test_worker_exit_code_distinguishes_environment_from_gpu_failure(capsys):
    with mock.patch.object(vss, "run_gpu", side_effect=vss.EnvironmentProblem("nichts testbar")):
        assert vss.worker_exit_code(0, 1, 80) == vss.EXIT_INCONCLUSIVE
    assert "FEHLER: nicht aussagekräftig – nichts testbar" in capsys.readouterr().out
    with mock.patch.object(vss, "run_gpu", side_effect=vss.VulkanError("vkQueueSubmit", vss.VK_ERROR_DEVICE_LOST)):
        assert vss.worker_exit_code(0, 1, 80) == vss.EXIT_FAILED
    with mock.patch.object(vss, "run_gpu", side_effect=vss.VulkanError("vkCreateInstance", -9)):
        assert vss.worker_exit_code(0, 1, 80) == vss.EXIT_INCONCLUSIVE
    with mock.patch.object(vss, "run_gpu", return_value=3):
        assert vss.worker_exit_code(0, 1, 80) == vss.EXIT_FAILED
    with mock.patch.object(vss, "run_gpu", return_value=0):
        assert vss.worker_exit_code(0, 1, 80) == vss.EXIT_OK


def test_summarize_never_passes_without_checked_memory(capsys):
    assert vss.summarize({0: vss.EXIT_INCONCLUSIVE}) == vss.EXIT_INCONCLUSIVE
    assert "ERGEBNIS: NICHT AUSSAGEKRÄFTIG (GPU 0)" in capsys.readouterr().out
    assert vss.summarize({0: vss.EXIT_OK, 1: vss.EXIT_INCONCLUSIVE}) == vss.EXIT_INCONCLUSIVE
    assert vss.summarize({0: vss.EXIT_FAILED, 1: vss.EXIT_INCONCLUSIVE}) == vss.EXIT_FAILED
    assert "FEHLGESCHLAGEN (GPU 0)" in capsys.readouterr().out
    assert vss.summarize({0: vss.EXIT_OK}) == vss.EXIT_OK
    assert "ERGEBNIS: BESTANDEN" in capsys.readouterr().out


def test_main_reports_inconclusive_when_nothing_testable(capsys):
    with mock.patch.object(vss, "worker_exit_code", return_value=vss.EXIT_INCONCLUSIVE):
        assert vss.main(["--gpus", "0", "--duration", "1"]) == vss.EXIT_INCONCLUSIVE
    assert "ERGEBNIS: BESTANDEN" not in capsys.readouterr().out


# --- Befund 5: Muster über 16 GiB eindeutig, Fehlerzähler ohne Überlauf -------

def test_vulkan_pattern_differs_for_chunks_aliasing_modulo_2_32():
    words_per_chunk = vss.CHUNK_BYTES // 4
    pattern = 0x5BD1E995
    # Block 0 und der Block, dessen base genau um 2^32 Wörter (16 GiB) versetzt ist.
    alias_index = (1 << 32) // words_per_chunk
    seen = set()
    for chunk_index in (0, alias_index, 2 * alias_index, 7, alias_index + 7):
        base = chunk_index * words_per_chunk
        for offset in (0, 1, 12345, words_per_chunk - 1):
            word = vss.expected_word(pattern, chunk_index, base, offset)
            assert (chunk_index, offset) not in seen
            seen.add((chunk_index, offset))
            # Ohne Block-Seed waeren diese Woerter identisch:
            if chunk_index:
                other = vss.expected_word(pattern, 0, 0, offset)
                assert word != other
    seeds = {vss.chunk_pattern(pattern, i, i * words_per_chunk) for i in range(4096)}
    assert len(seeds) == 4096


def test_vulkan_memory_test_reads_counter_per_chunk(monkeypatch):
    test = vss.GpuTest.__new__(vss.GpuTest)
    counter = vss.c_u32(0)
    test.errors_ptr = ctypes.addressof(counter)
    test.chunks = [(1, 1024, 11), (2, 1024, 12), (3, 1024, 13)]
    calls = []

    def fake_dispatch(step, descriptor_set, groups, push):
        calls.append((step, descriptor_set, push.pat, push.base))
        if step == "verify":
            assert counter.value == 0, "Zaehler muss je Block genullt werden"
            counter.value = 0xFFFFFFFF if descriptor_set == 12 else 5

    test.dispatch = fake_dispatch
    assert test.memory_test(0x1234) == 0xFFFFFFFF + 10  # kein 32-Bit-Ueberlauf
    assert len(calls) == 6
    pats = {pat for step, _set, pat, _base in calls if step == "fill"}
    assert len(pats) == 3
    assert [c[3] for c in calls[:3]] == [0, 1024, 2048]


# --- Befund 5 (CUDA): 64-Bit-Index im Muster, Flag statt überlaufendem Zähler --




def test_cuda_pattern_mixes_high_index_bits():
    pattern = 0x5BD1E995
    for index in (0, 1, 0xDEADBEEF, 0xFFFFFFFF):
        words = {gss.expected_word(index + (k << 32), pattern) for k in range(64)}
        assert len(words) == 64, "Muster darf sich nicht alle 16 GiB wiederholen"
    assert gss.expected_word(5, 1) != gss.expected_word(5, 2)
    # PTX verwendet dieselben Konstanten und die hohen 32 Bit des Index.
    for kernel in ("ht_fill", "ht_verify"):
        body = gss.PTX.split(f".entry {kernel}")[1].split(".entry")[0]
        assert "shr.u64" in body and "-1640531535" in body and "-2048144789" in body
    verify = gss.PTX.split(".entry ht_verify")[1]
    assert "atom.global.add.u64" in verify and "atom.global.or.b32" in verify
    assert ctypes.sizeof(gss.ErrorCounter) == gss.ERRORS_BYTES
    assert gss.ErrorCounter.flag.offset == 8


class _FakeCuda:
    """Spielt die CUDA-Driver-API nach; Fehlerpuffer und Rechenergebnis sind vorgebbar."""

    def __init__(self, free: int, count: int = 0, flag: int = 0, compute_out=None):
        self.free = free
        self.count, self.flag = count, flag
        self.compute_out = compute_out
        self.lib = mock.MagicMock()
        self.launches = []
        self.memsets = []

    def call(self, name, *args):
        if name == "cuMemGetInfo_v2":
            args[0]._obj.value = self.free
            args[1]._obj.value = max(self.free, 8 << 30)
        elif name == "cuMemAlloc_v2":
            args[0]._obj.value = 0x1000 + len(self.memsets) + len(self.launches)
        elif name == "cuMemsetD32_v2":
            self.memsets.append(args[2].value)
        elif name == "cuMemcpyDtoH_v2":
            if args[2].value == gss.ERRORS_BYTES:
                counter = args[0]._obj
                counter.count, counter.flag = self.count, self.flag
            elif self.compute_out is not None:
                for i, value in enumerate(self.compute_out):
                    args[0][i] = value
        return 0

    def load_ptx(self, ptx):
        assert ptx.isascii()
        return ctypes.c_void_p(0x2000)

    def launch(self, function, grid, block, *args):
        self.launches.append((grid, block, args))


def test_cuda_run_gpu_rejects_too_small_allocation():
    cuda = _FakeCuda(free=32 << 20)
    with pytest.raises(gss.EnvironmentProblem, match="kein Grafikspeicher testbar"):
        gss.run_gpu(0, 0.2, 80, cuda=cuda)
    assert cuda.launches == []
    assert cuda.lib.cuCtxDestroy_v2.called


def test_cuda_error_flag_fails_even_if_counter_wrapped_to_zero(capsys, reference):
    cuda = _FakeCuda(free=1 << 30, count=0, flag=1)
    with mock.patch.object(gss, "FmaReference", return_value=reference):
        errors = gss.run_gpu(0, 0.05, 80, cuda=cuda)
    out = capsys.readouterr().out
    assert errors >= 1
    assert "FEHLER: 1 Speicherfehler" in out
    # Zaehler und Flag werden vor jedem Zyklus komplett genullt (4 Woerter).
    assert cuda.memsets and all(words == gss.ERRORS_BYTES // 4 for words in cuda.memsets)


def test_cuda_large_counter_is_reported_without_overflow(capsys, reference):
    cuda = _FakeCuda(free=1 << 30, count=(1 << 32) + 7, flag=1)
    with mock.patch.object(gss, "FmaReference", return_value=reference):
        errors = gss.run_gpu(0, 0.05, 80, cuda=cuda)
    assert errors >= (1 << 32) + 7
    assert f"FEHLER: {(1 << 32) + 7} Speicherfehler" in capsys.readouterr().out


def test_cuda_clean_run_passes(capsys, reference):
    good = reference.lookup(1 << 16)[0]
    cuda = _FakeCuda(free=1 << 30, compute_out=[good] * (gss.COMPUTE_BLOCKS * gss.BLOCK))
    with mock.patch.object(gss, "FmaReference", return_value=reference):
        assert gss.run_gpu(0, 0.05, 80, cuda=cuda) == 0
    assert "FEHLER" not in capsys.readouterr().out


def test_cuda_main_reports_inconclusive_for_environment_problem(capsys):
    with mock.patch.object(gss, "run_gpu", side_effect=gss.EnvironmentProblem("nichts testbar")):
        rc = gss.main(["--gpus", "0", "--duration", "1"])
    out = capsys.readouterr().out
    assert rc == gss.EXIT_INCONCLUSIVE
    assert "ERGEBNIS: NICHT AUSSAGEKRÄFTIG (GPU 0)" in out and "BESTANDEN" not in out


# --- Befund 6: unabhängige CPU-Referenz für die FMA-Kette ---------------------

def _exact_chain_sum(iterations: int) -> float:
    """Oracle mit exakter Bruchrechnung (unabhängig von der Implementierung im Skript)."""
    import struct as _struct
    from fractions import Fraction

    def r32(x):
        return _struct.unpack("<f", _struct.pack("<f", float(x)))[0]

    a, b = Fraction(r32(0.999999)), Fraction(r32(1e-6))
    xs = [Fraction(r32(v)) for v in (0.0, 0.5, 1.0, 2.0)]
    for _ in range(iterations):
        xs = [Fraction(r32(x * a + b)) for x in xs]
    return r32(Fraction(r32(xs[0] + xs[1])) + Fraction(r32(xs[2] + xs[3])))


@pytest.fixture(scope="module")
def reference():
    ref = gss.FmaReference(start=False)
    ref.compute_all(limit=1 << 16)  # deckt die Start-Iterationszahlen beider Skripte ab
    return ref


def test_fma_reference_matches_exact_arithmetic(reference):
    for n in (1, 1024, 4096):
        assert reference.lookup(n)[0] == _exact_chain_sum(n)
    assert reference.lookup(1 << 11)[0] != reference.lookup(1 << 12)[0]


def test_fma_reference_check_accepts_fused_and_plain_but_not_wrong(reference):
    fused, plain = reference.lookup(4096)
    assert reference.check(fused, 4096) is None
    assert reference.check(plain, 4096) is None
    assert reference.check(fused + 1e-3, 4096) and "CPU-Referenz" in reference.check(fused + 1e-3, 4096)
    assert reference.check(0.0, 4096)
    assert reference.check(123.0, 12345) is None, "ohne Referenz kein Urteil"
    assert reference.lookup(1 << 16) is not None and reference.lookup(1 << 17) is None


def test_vulkan_and_cuda_share_identical_reference():
    assert vss.FmaReference(start=False).a == gss.FmaReference(start=False).a
    assert vss.FMA_TOLERANCE == gss.FMA_TOLERANCE


def test_cuda_uniform_but_wrong_result_is_detected(capsys, reference):
    cuda = _FakeCuda(free=1 << 30, compute_out=[1.234] * (gss.COMPUTE_BLOCKS * gss.BLOCK))
    with mock.patch.object(gss, "FmaReference", return_value=reference):
        errors = gss.run_gpu(0, 0.05, 80, cuda=cuda)
    out = capsys.readouterr().out
    assert errors >= 1
    assert "weicht von CPU-Referenz" in out and "Rechenfehler in" not in out


def test_vulkan_uniform_but_wrong_result_is_detected(capsys, reference):
    fake = _FakeGpuTest(allocated=vss.MIN_TEST_BYTES)
    fake.last_value = 1.234
    with mock.patch.object(vss, "GpuTest", return_value=fake), \
            mock.patch.object(vss, "FmaReference", return_value=reference):
        errors = vss.run_gpu(0, 0.05, 80, vk=object())
    out = capsys.readouterr().out
    assert errors >= 1 and "weicht von CPU-Referenz" in out
    assert "Rechenergebnis gegen CPU-Referenz geprüft" in out


def test_vulkan_correct_uniform_result_passes(capsys, reference):
    fake = _FakeGpuTest(allocated=vss.MIN_TEST_BYTES)

    def compute(iterations):
        # Liefert fuer bekannte Iterationszahlen das korrekte, sonst ein beliebiges Ergebnis.
        known = reference.lookup(iterations)
        fake.last_value = known[0] if known else 7.0
        return 0

    fake.compute = compute
    with mock.patch.object(vss, "GpuTest", return_value=fake), \
            mock.patch.object(vss, "FmaReference", return_value=reference):
        assert vss.run_gpu(0, 0.05, 80, vk=object()) == 0
    assert "FEHLER" not in capsys.readouterr().out
