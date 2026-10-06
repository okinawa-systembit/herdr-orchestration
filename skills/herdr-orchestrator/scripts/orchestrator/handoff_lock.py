from __future__ import annotations

import fcntl
import os

from orchestrator.paths import handoff_lock_path
from orchestrator.registry.io import _FILE_MODE, _assert_safe_registry_path, ensure_registry_layout
from orchestrator.registry.task_id import validate_task_id


class HandoffLockSession:
    """§6.6: wrapper process holds handoff flock fd until release()."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self._fd: int | None = None

    def acquire(self) -> None:
        validate_task_id(self.task_id)
        ensure_registry_layout()
        lock_path = handoff_lock_path(self.task_id)
        _assert_safe_registry_path(lock_path, must_exist=False)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, _FILE_MODE)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None

    def __enter__(self) -> HandoffLockSession:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
