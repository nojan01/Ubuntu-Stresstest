"""Advisory RAM-test gate for controlled test/application start points (Linux)."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat


def lock_path() -> Path:
    return Path('/tmp/hardwaretest-ram-fill.lock')


class RamFillBusy(RuntimeError):
    pass


def _open_gate() -> int:
    path = lock_path()
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o644)
        os.fchmod(fd, 0o644)  # Only a newly-created empty file; never chmod an existing path.
    except FileExistsError:
        fd = os.open(path, flags)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError('RAM-test gate is not a regular file')
    return fd


@contextmanager
def ram_fill_lock():
    fd = _open_gate()
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RamFillBusy('RAM-Fuelltest laeuft bereits; Start gesperrt.') from exc
        yield
    finally:
        os.close(fd)  # OS releases the lock even after a crash; no stale PID flag.


def ram_fill_active() -> bool:
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    try:
        fd = os.open(lock_path(), flags)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)
