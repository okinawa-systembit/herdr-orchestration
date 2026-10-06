from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat as stat_module
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from orchestrator.paths import handoff_lock_path, task_json_path, tasks_dir
from orchestrator.registry.errors import RegistryError
from orchestrator.registry.schema import validate_loaded_task
from orchestrator.registry.task_id import validate_task_id

_DIR_MODE = 0o700
_FILE_MODE = 0o600


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _assert_safe_registry_path(path: Path, *, must_exist: bool = False) -> None:
    if path.is_symlink():
        raise RegistryError("registry_unsafe_path", message=f"symlink rejected: {path!s}")
    if must_exist and not path.exists():
        raise RegistryError("registry_unsafe_path", message=f"missing path: {path!s}")
    if path.exists() and path.stat().st_uid != os.getuid():
        raise RegistryError("registry_unsafe_path", message=f"unexpected owner: {path!s}")


def _assert_private_directory(path: Path, *, must_exist: bool = False) -> None:
    """Reject group/world-writable directories (§6.9 shared writable)."""
    _assert_safe_registry_path(path, must_exist=must_exist)
    if not path.exists():
        return
    if not path.is_dir():
        raise RegistryError("registry_unsafe_path", message=f"not a directory: {path!s}")
    mode = stat_module.S_IMODE(path.stat().st_mode)
    if mode & (stat_module.S_IWGRP | stat_module.S_IWOTH):
        raise RegistryError(
            "registry_unsafe_path",
            message=f"shared writable directory rejected: {path!s}",
        )


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("incomplete write to registry temp file")
        offset += written


def _normalize_file_mode(path: Path) -> None:
    if not path.exists():
        return
    mode = stat_module.S_IMODE(path.stat().st_mode)
    if mode != _FILE_MODE:
        os.chmod(path, _FILE_MODE)


def ensure_registry_layout() -> Path:
    root = tasks_dir()
    orchestrator_root = root.parent
    state_parent = orchestrator_root.parent

    _assert_private_directory(state_parent, must_exist=False)
    if orchestrator_root.exists():
        _assert_private_directory(orchestrator_root, must_exist=True)
    else:
        orchestrator_root.mkdir(parents=True, exist_ok=True)
    os.chmod(orchestrator_root, _DIR_MODE)

    if root.exists():
        _assert_private_directory(root, must_exist=True)
    else:
        root.mkdir(mode=_DIR_MODE)
    os.chmod(root, _DIR_MODE)
    return root


def registry_lock_path(task_id: str) -> Path:
    validate_task_id(task_id)
    return task_json_path(task_id).with_suffix(".json.lock")


def task_file_path(task_id: str) -> Path:
    validate_task_id(task_id)
    ensure_registry_layout()
    path = task_json_path(task_id)
    _assert_safe_registry_path(path, must_exist=False)
    return path


def read_task_document_unlocked(path: Path) -> dict[str, Any]:
    _assert_safe_registry_path(path, must_exist=True)
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise RegistryError("invalid_schema", message="task json root must be object")
    validate_loaded_task(doc)
    return doc


def read_task_document(task_id: str) -> dict[str, Any]:
    path = task_file_path(task_id)
    if not path.exists():
        raise RegistryError("task_not_found", task_id=task_id)
    return read_task_document_unlocked(path)


def _atomic_write_json(path: Path, doc: dict[str, Any]) -> None:
    root = ensure_registry_layout()
    _assert_safe_registry_path(path, must_exist=False)
    tmp_name = f".{path.name}.tmp.{secrets.token_hex(8)}"
    tmp = root / tmp_name
    if tmp.exists() or tmp.is_symlink():
        raise RegistryError("registry_unsafe_path", message="temporary file path unsafe")
    payload = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    encoded = payload.encode("utf-8")
    fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE)
    try:
        _write_all(fd, encoded)
        os.fsync(fd)
    except OSError:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise RegistryError("registry_write_failed", message="atomic write failed")
    else:
        os.close(fd)
    try:
        _assert_safe_registry_path(path, must_exist=False)
        os.replace(tmp, path)
        os.chmod(path, _FILE_MODE)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise RegistryError("registry_write_failed", message="atomic replace failed") from exc


@contextmanager
def task_mutation_lock(task_id: str) -> Iterator[int]:
    """Exclusive flock on `<task.id>.json.lock` (shared by mutate and cleanup)."""
    validate_task_id(task_id)
    ensure_registry_layout()
    lock_path = registry_lock_path(task_id)
    _assert_safe_registry_path(lock_path, must_exist=False)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, _FILE_MODE)
    _normalize_file_mode(lock_path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write_task_document(task_id: str, doc: dict[str, Any]) -> None:
    validate_loaded_task(doc)
    path = task_file_path(task_id)
    if doc.get("task", {}).get("id") != task_id:
        raise RegistryError("invalid_schema", message="task.id mismatch")
    _atomic_write_json(path, doc)


def create_task_document(doc: dict[str, Any]) -> None:
    task_id = doc["task"]["id"]
    validate_task_id(task_id)
    validate_loaded_task(doc)
    path = task_file_path(task_id)
    if path.exists():
        raise RegistryError("task_already_exists", task_id=task_id)
    _atomic_write_json(path, doc)


def mutate_task(
    task_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    validate_task_id(task_id)
    path = task_file_path(task_id)
    if not path.exists():
        raise RegistryError("task_not_found", task_id=task_id)

    with task_mutation_lock(task_id):
        current = read_task_document_unlocked(path)
        before = json.dumps(current, sort_keys=True)
        updated = mutator(current)
        if updated is current:
            return current
        validate_loaded_task(updated)
        if updated.get("task", {}).get("id") != task_id:
            raise RegistryError("invalid_schema", message="task.id mismatch on update")
        after = json.dumps(updated, sort_keys=True)
        if before == after:
            return current
        updated.setdefault("timestamps", {})
        updated["timestamps"]["updated_at"] = utc_now_iso()
        _atomic_write_json(path, updated)
        return updated


def write_cleanup_stamp(root: Path, content: str) -> None:
    stamp_path = root / "last_cleanup_at"
    _assert_safe_registry_path(stamp_path, must_exist=False)
    fd = os.open(str(stamp_path), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, _FILE_MODE)
    try:
        _write_all(fd, content.encode("utf-8"))
        os.fsync(fd)
    except OSError as exc:
        raise RegistryError("registry_write_failed", message="cleanup stamp write failed") from exc
    finally:
        os.close(fd)


def read_cleanup_stamp(root: Path) -> str | None:
    stamp_path = root / "last_cleanup_at"
    if not stamp_path.exists():
        return None
    _assert_safe_registry_path(stamp_path, must_exist=True)
    return stamp_path.read_text(encoding="utf-8").strip()


_CLEANUP_RUN_LOCK_NAME = "cleanup.run.lock"


@contextmanager
def cleanup_run_lock(*, nonblocking: bool = False) -> Iterator[bool]:
    """Serialize full cleanup runs (§6.8 at most once per day)."""
    root = ensure_registry_layout()
    lock_path = root / _CLEANUP_RUN_LOCK_NAME
    _assert_safe_registry_path(lock_path, must_exist=False)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, _FILE_MODE)
    _normalize_file_mode(lock_path)
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
    acquired = False
    try:
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError:
            yield False
            return
        acquired = True
        yield True
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def handoff_lock_exclusive(task_id: str, *, nonblocking: bool = False) -> Iterator[bool]:
    validate_task_id(task_id)
    ensure_registry_layout()
    lock_path = handoff_lock_path(task_id)
    _assert_safe_registry_path(lock_path, must_exist=False)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, _FILE_MODE)
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
    acquired = False
    try:
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError:
            yield False
            return
        acquired = True
        yield True
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
