"""Conservative memory budgeting; pure calculations, no allocations or signals."""
from typing import NamedTuple

DEFAULT_RESERVE_MB = 2048
MAX_WORKERS = 32
WORKER_SCRATCH_MB = 64


def minimum_reserve_mb(total_mb: int) -> int:
    return max(DEFAULT_RESERVE_MB, min(16384, max(0, total_mb) // 50))


class MemoryBudget(NamedTuple):
    target_mb: int
    workers: int
    scratch_mb: int
    page_tables_mb: int


def memory_budget(available_mb: int, reserve_mb: int, worker_limit: int,
                  requested_mb: int = 0, page_size: int = 4096) -> MemoryBudget:
    """Budget OS reserve, mutable worker buffers and forked page tables.

    mmap+mlock faults pages in the parent before verification. Each fork may
    duplicate its page tables. Charge 16 bytes per page per process (including
    parent), conservatively allowing for upper-level tables. This is an estimate,
    not a measured resident-memory figure; runtime pressure is monitored too.
    """
    available_mb = max(0, available_mb)
    reserve_mb = max(0, reserve_mb)
    workers = max(1, min(MAX_WORKERS, worker_limit,
                         max(1, (available_mb - reserve_mb) // 256)))
    scratch = workers * WORKER_SCRATCH_MB
    page_size = max(4096, page_size)
    table_bytes = 16 * (workers + 1)
    target = max(0, (available_mb - reserve_mb - scratch) * page_size
                 // (page_size + table_bytes))
    if requested_mb > 0:
        target = min(target, requested_mb)
    tables = (target * table_bytes + page_size - 1) // page_size
    return MemoryBudget(target, workers, scratch, tables)
