from __future__ import annotations

import errno
import json
import os
import stat
from typing import Any

from orchestrator.paths import tasks_dir
from orchestrator.registry.errors import RegistryError
from orchestrator.registry.io import _assert_private_directory
from orchestrator.registry.schema import validate_loaded_task
from orchestrator.registry.task_id import validate_task_id

MAX_TASK_FILE_BYTES = 1024 * 1024  # 1 MB limit (R62-09)


def read_task_candidates() -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Open tasks directory, verify safety using opened file descriptors, and read task candidates
    relative to the verified directory descriptor (dir_fd).
    This eliminates TOCTOU races between directory verification, directory scanning, and task file reading (R62-08).

    Returns: (registry_exists, valid_docs, anomalies, concurrent_changes)
    Raises:
      RegistryError("registry_unsafe_path") if directory or parent is unsafe (symlink, not a dir, owner mismatch, group/world writable).
      RegistryError("registry_unreadable") if directory scanning or access permissions fail.
    """
    root = tasks_dir()
    orchestrator_root = root.parent
    state_parent = orchestrator_root.parent

    # Check state_parent if it exists
    _assert_private_directory(state_parent, must_exist=False)

    dir_open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    unsafe_errnos = (getattr(errno, "ELOOP", -1), errno.ENOTDIR)

    # 1. Open and verify orchestrator_root
    try:
        parent_fd = os.open(orchestrator_root, dir_open_flags)
    except FileNotFoundError:
        return False, [], [], []
    except OSError as exc:
        if exc.errno in unsafe_errnos:
            raise RegistryError("registry_unsafe_path", message=f"cannot access parent: {exc.strerror or exc}")
        raise RegistryError("registry_unreadable", message=f"cannot read parent directory: {exc.strerror or exc}")

    try:
        st_parent = os.fstat(parent_fd)
        if not stat.S_ISDIR(st_parent.st_mode):
            raise RegistryError("registry_unsafe_path", message=f"parent is not a directory: {orchestrator_root}")

        # 2. Open tasks directory relative to parent_fd using dir_fd
        try:
            tasks_fd = os.open(root.name, dir_open_flags, dir_fd=parent_fd)
        except FileNotFoundError:
            return False, [], [], []
        except OSError as exc:
            if exc.errno in unsafe_errnos:
                raise RegistryError("registry_unsafe_path", message=f"cannot access tasks dir: {exc.strerror or exc}")
            raise RegistryError("registry_unreadable", message=f"cannot read tasks directory: {exc.strerror or exc}")
    finally:
        os.close(parent_fd)

    # 3. Verify tasks directory inode
    try:
        st_tasks = os.fstat(tasks_fd)
        if not stat.S_ISDIR(st_tasks.st_mode):
            raise RegistryError("registry_unsafe_path", message=f"tasks root is not a directory: {root}")

        # 4. Scan entries directly from verified tasks_fd
        try:
            with os.scandir(tasks_fd) as it:
                scanned_entries = list(it)
        except PermissionError as exc:
            raise RegistryError("registry_unreadable", message=f"Permission denied: {exc}")
        except OSError as exc:
            raise RegistryError("registry_unreadable", message=f"Failed to scan task directory: {exc}")

        valid_docs: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = []
        concurrent_changes: list[dict[str, Any]] = []

        for entry in scanned_entries:
            name = entry.name
            # Ignore auxiliary files per §5.1
            if name.startswith("."):
                continue
            if name in (
                "cleanup.stamp",
                "stale-dispatch.log",
                "last_cleanup_at",
                "cleanup.run.lock",
            ):
                continue
            if name.endswith(".json.lock") or name.endswith(".handoff.lock"):
                continue
            # Candidate selection: MUST be task_*.json
            if not (name.startswith("task_") and name.endswith(".json")):
                continue

            try:
                if entry.is_dir(follow_symlinks=False):
                    anomalies.append({
                        "file": name,
                        "task_id": None,
                        "code": "unsafe_entry",
                        "message": "Candidate is a directory, not a regular file",
                    })
                    continue
            except OSError as exc:
                anomalies.append({
                    "file": name,
                    "task_id": None,
                    "code": "unsafe_entry",
                    "message": f"Failed to stat candidate: {exc.strerror or exc}",
                })
                continue

            stem = name[:-5]
            task_id: str | None = None
            try:
                validate_task_id(stem)
                task_id = stem
            except RegistryError:
                anomalies.append({
                    "file": name,
                    "task_id": None,
                    "code": "invalid_filename",
                    "message": f"File name stem does not match task_<uuidv4>: {name}",
                })
                continue

            # Step 1: Open fd safely relative to tasks_fd using dir_fd
            file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            if hasattr(os, "O_NONBLOCK"):
                file_flags |= os.O_NONBLOCK

            fd: int = -1
            try:
                fd = os.open(name, file_flags, dir_fd=tasks_fd)
            except FileNotFoundError:
                concurrent_changes.append({
                    "file": name,
                    "task_id": task_id,
                    "change": "removed",
                })
                continue
            except OSError as exc:
                eloop = getattr(errno, "ELOOP", -1)
                if exc.errno == eloop:
                    anomalies.append({
                        "file": name,
                        "task_id": task_id,
                        "code": "unsafe_entry",
                        "message": "Symlink rejected in registry",
                    })
                    continue
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "read_error",
                    "message": exc.strerror or str(exc),
                })
                continue

            # Step 2: Validate inode via fstat
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    anomalies.append({
                        "file": name,
                        "task_id": task_id,
                        "code": "unsafe_entry",
                        "message": "Not a regular file",
                    })
                    continue

                # R62-09: file size check against maximum limit
                if st.st_size > MAX_TASK_FILE_BYTES:
                    anomalies.append({
                        "file": name,
                        "task_id": task_id,
                        "code": "read_error",
                        "message": f"file size exceeds maximum allowed limit ({MAX_TASK_FILE_BYTES} bytes)",
                    })
                    continue

                # Step 3: Read bytes with size limit
                with os.fdopen(fd, "rb") as f:
                    fd = -1  # f owns and will close fd
                    raw_bytes = f.read(MAX_TASK_FILE_BYTES + 1)

                if len(raw_bytes) > MAX_TASK_FILE_BYTES:
                    anomalies.append({
                        "file": name,
                        "task_id": task_id,
                        "code": "read_error",
                        "message": f"file size exceeds maximum allowed limit ({MAX_TASK_FILE_BYTES} bytes)",
                    })
                    continue
            except OSError as exc:
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "read_error",
                    "message": exc.strerror or str(exc),
                })
                continue
            finally:
                if fd >= 0:
                    os.close(fd)

            # Step 4: Decode UTF-8
            try:
                raw_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "invalid_encoding",
                    "message": exc.reason or "Invalid UTF-8 encoding",
                })
                continue

            # Step 5: JSON parse
            try:
                doc = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "invalid_json",
                    "message": exc.msg or "JSON syntax error",
                })
                continue

            # Step 6: Root element must be an object
            if not isinstance(doc, dict):
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "invalid_json",
                    "message": "Root JSON element must be an object",
                })
                continue

            # Step 7: Schema version check
            if doc.get("schema_version") != 1:
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "unsupported_schema",
                    "message": "unsupported schema_version",
                })
                continue

            # Step 8: Canonical schema validation (must validate structure before checking fields)
            try:
                validate_loaded_task(doc)
            except RegistryError as exc:
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "invalid_schema",
                    "message": exc.message,
                })
                continue
            except Exception:
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "invalid_schema",
                    "message": "schema validation failed",
                })
                continue

            # Step 9: task.id must match filename stem
            task_obj = doc.get("task")
            if not isinstance(task_obj, dict) or task_obj.get("id") != task_id:
                anomalies.append({
                    "file": name,
                    "task_id": task_id,
                    "code": "task_id_mismatch",
                    "message": f"task.id in file does not match filename {name}",
                })
                continue

            valid_docs.append(doc)

        return True, valid_docs, anomalies, concurrent_changes
    finally:
        os.close(tasks_fd)
