"""Cross-platform file-system durability helpers.

Two POSIX facilities have no direct Windows equivalent and must be degraded
explicitly rather than crashing:

* ``flock`` advisory locks -> ``msvcrt.locking`` on a single byte.
* ``fsync`` of a directory -> not applicable; Windows has no way to open a
  directory for ``fsync``, and ``os.replace`` is already atomic on NTFS.

On POSIX both keep their original behaviour, so the Unix path stays identical.
"""
import errno
import os
import time

# Windows lock polling: short interval, generous deadline (flock waits forever).
_WINDOWS_LOCK_POLL = 0.005
_WINDOWS_LOCK_TIMEOUT = 300.0

# Windows rename retry: a concurrent reader holds the destination open briefly.
_REPLACE_ATTEMPTS = 60
_REPLACE_BACKOFF = 0.005
_REPLACE_MAX_BACKOFF = 0.05


def _blocking():
    return BlockingIOError(errno.EACCES, 'lock is held by another handle')


class _Windows:
    def __init__(self):
        import msvcrt
        self._msvcrt = msvcrt

    def _try(self, handle, mode):
        # msvcrt locks from the current position, so always lock byte 0 of the file.
        try:
            handle.seek(0)
        except (OSError, ValueError):
            pass
        try:
            self._msvcrt.locking(handle.fileno(), mode, 1)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EDEADLK, errno.EAGAIN):
                raise _blocking() from None
            raise

    def acquire(self, handle, exclusive=True, blocking=True):
        # ``LK_LOCK`` is not an unbounded wait: msvcrt retries ten times and then
        # gives up, which breaks many-thread callers that hold the lock briefly.
        # Poll explicitly so the Windows path matches flock's wait-until-free
        # behaviour, with a deadline only to avoid an infinite hang.
        deadline = time.monotonic() + _WINDOWS_LOCK_TIMEOUT
        while True:
            try:
                self._try(handle, self._msvcrt.LK_NBLCK)
                return
            except BlockingIOError:
                if not blocking or time.monotonic() >= deadline:
                    raise
                time.sleep(_WINDOWS_LOCK_POLL)

    def release(self, handle):
        try:
            handle.seek(0)
        except (OSError, ValueError):
            pass
        try:
            self._msvcrt.locking(handle.fileno(), self._msvcrt.LK_UNLCK, 1)
        except OSError:
            # Closing the handle releases the byte range anyway.
            pass


class _Posix:
    def __init__(self):
        import fcntl
        self._fcntl = fcntl

    def acquire(self, handle, exclusive=True, blocking=True):
        flag = self._fcntl.LOCK_EX if exclusive else self._fcntl.LOCK_SH
        if not blocking:
            flag |= self._fcntl.LOCK_NB
        try:
            self._fcntl.flock(handle, flag)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise _blocking() from None
            raise

    def release(self, handle):
        try:
            self._fcntl.flock(handle, self._fcntl.LOCK_UN)
        except OSError:
            pass


try:  # pragma: no cover - platform selection
    _impl = _Posix()
except (ImportError, AttributeError):  # pragma: no cover
    _impl = _Windows()


def acquire(handle, *, exclusive=True, blocking=True):
    """Take the lock, raising ``BlockingIOError`` when ``blocking`` is false."""
    _impl.acquire(handle, exclusive=exclusive, blocking=blocking)


def release(handle):
    """Release a lock previously taken on ``handle``."""
    _impl.release(handle)


def sync_directory(path):
    """Flush a directory entry so a rename survives a crash.

    POSIX only. On Windows the directory handle cannot be opened for ``fsync``,
    and ``os.replace`` is already atomic on NTFS, so this is a deliberate no-op
    instead of a failure.
    """
    if os.name == 'nt':
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(source, destination, *, attempts=_REPLACE_ATTEMPTS):
    """Rename ``source`` over ``destination``, tolerating a transient sharing error.

    ``os.replace`` is unconditionally atomic on POSIX, which lets the reader open
    the destination while it is replaced. Windows instead fails the rename with a
    sharing violation whenever another handle currently has the destination open,
    so a reader polling a state file can abort a writer. Retry briefly to close
    that window; POSIX never takes the retry path.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(_REPLACE_BACKOFF * (attempt + 1), _REPLACE_MAX_BACKOFF))


def read_text(path, *, attempts=_REPLACE_ATTEMPTS):
    """Read a UTF-8 text file, tolerating a rename happening at the same moment.

    The mirror image of :func:`replace`: a reader that opens the destination while
    a writer replaces it also hits a sharing violation on Windows. Retrying keeps
    the HTTP polling path from returning 500 during a state update.
    """
    for attempt in range(attempts):
        try:
            return path.read_text(encoding='utf-8')
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(_REPLACE_BACKOFF * (attempt + 1), _REPLACE_MAX_BACKOFF))
