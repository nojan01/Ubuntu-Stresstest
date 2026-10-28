import mmap
import time

import pytest

from hardwaretest.tests import memory_fill_script as mfs
from hardwaretest.tests.memory_fill import MemoryFillRunner


def test_fill_stops_at_deadline_and_verifies_written_part():
    chunks = [(mmap.mmap(-1, 1 << 20), 1 << 20) for _ in range(4)]
    builder = mfs._const_byte_builder(0xAA)
    assert mfs._fill(chunks, builder, time.monotonic() - 1) == 0
    written = mfs._fill(chunks, builder)
    assert written == 4 << 20
    chunks[3][0][100] = 0x00
    assert mfs._verify(chunks, builder, 3 << 20) == []
    assert mfs._verify(chunks, builder)
    mfs._free_chunks(chunks)


def test_memory_fill_runner_has_long_terminate_grace():
    assert MemoryFillRunner.terminate_grace_seconds >= 120


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_verify_parallel_finds_error():
    chunks = [(mmap.mmap(-1, 1 << 20), 1 << 20) for _ in range(6)]
    builder = mfs._const_byte_builder(0x55)
    mfs._fill(chunks, builder)
    assert mfs._verify_parallel(chunks, builder, workers=3) == []
    chunks[4][0][7] = 0
    errs = mfs._verify_parallel(chunks, builder, workers=3)
    assert len(errs) == 1 and "Chunk 4" in errs[0]
    mfs._free_chunks(chunks)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_parallel_fill_is_visible_in_parent_and_rotates():
    chunks = mfs._allocate_chunks(8, 1)
    builder = mfs._const_byte_builder(0x3C)
    segs = mfs._segments(chunks, None)
    for rotation in (0, 3):
        written = mfs._run_parallel("fill", chunks, builder, segs, workers=4,
                                    rotation=rotation)
        assert sum(s[2] for s in written) == 8 << 20
        assert chunks[5][0][123] == 0x3C
        assert mfs._run_parallel("verify", chunks, builder, written,
                                 workers=4, rotation=rotation + 2) == []
    mfs._free_chunks(chunks)


def test_memory_fill_grace_scales_with_ram():
    from hardwaretest.tests.memory_fill import _terminate_grace_for
    assert _terminate_grace_for(16 * 1024) == 300
    assert _terminate_grace_for(2 * 1024 * 1024) == 1024
    assert _terminate_grace_for(4 * 1024 * 1024) == 2048
