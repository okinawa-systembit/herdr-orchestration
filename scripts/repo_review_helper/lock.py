"""Shared file lock mechanism for repository review helper."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import time
from pathlib import Path
from types import TracebackType
from typing import Optional, Type


class LockError(Exception):
    """Base error for lock operations."""


class LockAcquisitionError(LockError):
    """Raised when the lock cannot be acquired."""


class ReviewLock:
    """
    Exclusive flock-based lock bound to a fixed file in the git common directory.

    Guarantees TOCTOU safety via fstat/stat inode and device comparison,
    retrying if the file was replaced during open/flock.
    Never unlinks the lock file.
    """

    LOCK_FILENAME = "herdr-review.lock"
    MAX_RETRIES = 5
    RETRY_DELAY_SEC = 0.05

    def __init__(self, git_common_dir: str | Path, *, non_blocking: bool = False, timeout: float | None = None) -> None:
        self.git_common_dir = Path(git_common_dir).resolve()
        self.lock_path = self.git_common_dir / self.LOCK_FILENAME
        self.non_blocking = non_blocking
        self.timeout = timeout
        self.fd: int | None = None

    def acquire(self) -> None:
        if self.fd is not None:
            return  # Already acquired

        self.git_common_dir.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()

        attempts = 0
        open_flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW

        while attempts < self.MAX_RETRIES:
            attempts += 1
            try:
                fd = os.open(str(self.lock_path), open_flags, 0o600)
            except OSError as e:
                if e.errno == errno.ELOOP or os.path.islink(str(self.lock_path)):
                    raise LockAcquisitionError(
                        f"Security error: lock file {self.lock_path} is a symlink"
                    ) from e
                if e.errno == errno.EISDIR or os.path.isdir(str(self.lock_path)):
                    raise LockAcquisitionError(
                        f"Security error: lock file {self.lock_path} is a directory, not a regular file"
                    ) from e
                raise LockAcquisitionError(
                    f"Failed to open lock file {self.lock_path}: {e}"
                ) from e

            acquired = False
            try:
                # Check symlink (double defense)
                if os.path.islink(str(self.lock_path)):
                    raise LockAcquisitionError(
                        f"Security error: lock file {self.lock_path} is a symlink"
                    )

                # Ensure opened descriptor is a regular file
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    raise LockAcquisitionError(
                        f"Security error: lock file {self.lock_path} is not a regular file"
                    )

                # Ensure permissions are strictly 0600 and owned by current user
                if st.st_uid != os.getuid():
                    raise LockAcquisitionError(
                        f"Security error: lock file {self.lock_path} is owned by UID {st.st_uid}, expected {os.getuid()}"
                    )
                if (st.st_mode & 0o777) != 0o600:
                    raise LockAcquisitionError(
                        f"Security error: lock file {self.lock_path} permissions are {oct(st.st_mode & 0o777)}, expected 0o600. "
                        f"Automatic modification is prohibited. Hint: Run 'chmod 600 {self.lock_path}' to fix permissions."
                    )

                # Acquire flock
                flags = fcntl.LOCK_EX
                if self.non_blocking:
                    flags |= fcntl.LOCK_NB

                if self.timeout is not None:
                    # Timeout-based loop with non-blocking flock
                    nb_flags = fcntl.LOCK_EX | fcntl.LOCK_NB
                    while True:
                        try:
                            fcntl.flock(fd, nb_flags)
                            break
                        except (BlockingIOError, OSError):
                            if time.monotonic() - start_time >= self.timeout:
                                raise LockAcquisitionError(f"Timed out acquiring lock on {self.lock_path}")
                            time.sleep(self.RETRY_DELAY_SEC)
                else:
                    fcntl.flock(fd, flags)

                # Step 4: inode verification (TOCTOU protection)
                fstat_res = os.fstat(fd)
                try:
                    stat_res = os.stat(str(self.lock_path))
                except FileNotFoundError:
                    # File was unlinked between open and stat
                    time.sleep(self.RETRY_DELAY_SEC)
                    continue

                if fstat_res.st_ino != stat_res.st_ino or fstat_res.st_dev != stat_res.st_dev:
                    # File was replaced by another process
                    time.sleep(self.RETRY_DELAY_SEC)
                    continue

                # Successfully acquired and verified
                self.fd = fd
                acquired = True
                return
            except (BlockingIOError, OSError) as e:
                if self.non_blocking and isinstance(e, BlockingIOError):
                    raise LockAcquisitionError(f"Could not acquire lock immediately on {self.lock_path}") from e
                if attempts >= self.MAX_RETRIES:
                    raise LockAcquisitionError(
                        f"Failed to acquire lock after {self.MAX_RETRIES} attempts on {self.lock_path}: {e}"
                    ) from e
                time.sleep(self.RETRY_DELAY_SEC)
            finally:
                if not acquired:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

        raise LockAcquisitionError(f"Exceeded max retries acquiring lock on {self.lock_path}")

    def release(self) -> None:
        if self.fd is None:
            return
        fd = self.fd
        self.fd = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> ReviewLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.release()
