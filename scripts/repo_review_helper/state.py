"""Auxiliary state management for repository review helper."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STATE_DIR_NAME = "herdr-review"
STATE_FILE_NAME = "state.json"


class StateError(Exception):
    """Base exception for state management errors."""


class SecurityPermissionError(StateError):
    """Raised when file or directory permissions/ownership violate security policies."""


class CorruptedStateError(StateError):
    """Raised when state data is corrupted or violates schema."""


class WorktreeMismatchError(StateError):
    """Raised when the executing worktree does not match the registered worktree."""


def get_git_common_dir(target_dir: str | Path | None = None) -> Path:
    """Resolve the git common directory using git rev-parse --git-common-dir."""
    cwd = Path(target_dir).resolve() if target_dir else Path.cwd().resolve()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        common_dir = res.stdout.strip()
        p = Path(common_dir)
        if not p.is_absolute():
            p = (cwd / p).resolve()
        return p
    except Exception as e:
        raise StateError(f"Failed to resolve git common directory from {cwd}: {e}") from e


def get_state_dir(git_common_dir: str | Path) -> Path:
    return Path(git_common_dir).resolve() / STATE_DIR_NAME


def get_state_file_path(git_common_dir: str | Path) -> Path:
    return get_state_dir(git_common_dir) / STATE_FILE_NAME


def generate_instance_id() -> str:
    """Generate instance ID: inst_ + 12 lowercase hex characters."""
    return f"inst_{secrets.token_hex(6)}"


def check_and_enforce_permissions(
    path: Path,
    expected_mode: int,
    is_dir: bool = False,
    base_dir: Path | None = None,
) -> None:
    """
    Validate that path is not a symlink, UID ownership matches current user,
    and strict permission mode matches expected_mode.
    Optionally verifies that path resolves within base_dir.
    Raises SecurityPermissionError on mismatch.
    """
    # Check symlink on the path itself
    if os.path.islink(str(path)):
        raise SecurityPermissionError(f"Security error: {path} is a symlink")

    if not path.exists():
        return

    st = path.lstat()
    current_uid = os.getuid()
    if st.st_uid != current_uid:
        raise SecurityPermissionError(
            f"Security error: {path} is owned by UID {st.st_uid}, expected {current_uid}"
        )
    actual_mode = st.st_mode & 0o777
    if actual_mode != expected_mode:
        raise SecurityPermissionError(
            f"Security error: {path} permissions are {oct(actual_mode)}, expected {oct(expected_mode)}. "
            f"Hint: Run 'chmod {oct(expected_mode)[2:]} {path}' to fix permissions."
        )

    if base_dir is not None:
        norm_base = Path(base_dir).resolve()
        norm_path = path.resolve()
        try:
            norm_path.relative_to(norm_base)
        except ValueError:
            raise SecurityPermissionError(f"Security error: {path} resolves outside base directory {base_dir}")


def safe_ensure_dir(path: Path, mode: int = 0o700, base_dir: Path | None = None) -> None:
    """
    Safely create or verify a directory and its ancestors under base_dir with strict permissions and no symlinks.
    """
    if os.path.islink(str(path)):
        raise SecurityPermissionError(f"Security error: {path} is a symlink")

    # If base_dir provided, ensure all components between base_dir and path are created with mode and checked
    parts_to_create: list[Path] = []
    curr = path
    while not curr.exists():
        if os.path.islink(str(curr)):
            raise SecurityPermissionError(f"Security error: {curr} is a symlink")
        parts_to_create.append(curr)
        curr = curr.parent

    # Create missing components in top-down order
    for p in reversed(parts_to_create):
        if os.path.islink(str(p)):
            raise SecurityPermissionError(f"Security error: {p} is a symlink")
        p.mkdir(mode=mode, exist_ok=False)
        os.chmod(str(p), mode)

    # Check permissions and base_dir
    check_and_enforce_permissions(path, mode, is_dir=True, base_dir=base_dir)


def validate_state_dict(data: Any) -> dict[str, Any]:
    """Validate schema and types of state dictionary."""
    if not isinstance(data, dict):
        raise CorruptedStateError("State root must be a JSON object")

    for key in ("version", "instance_id", "worktree_path", "counter"):
        if key not in data:
            raise CorruptedStateError(f"Missing required state key: '{key}'")

    if not isinstance(data["version"], int) or data["version"] != SCHEMA_VERSION:
        raise CorruptedStateError(f"Unsupported 'version': {data['version']!r}, expected {SCHEMA_VERSION}")

    if not isinstance(data["instance_id"], str) or not data["instance_id"].startswith("inst_"):
        raise CorruptedStateError(f"Invalid 'instance_id': {data['instance_id']!r}")

    if not isinstance(data["worktree_path"], str) or not data["worktree_path"].strip():
        raise CorruptedStateError(f"Invalid 'worktree_path': {data['worktree_path']!r}")

    if not isinstance(data["counter"], int) or data["counter"] < 0:
        raise CorruptedStateError(f"Invalid 'counter': {data['counter']!r}")

    return data


def load_state(git_common_dir: str | Path) -> dict[str, Any] | None:
    """
    Load and validate state.json.
    Returns dict if valid, None if file does not exist.
    Raises SecurityPermissionError on permission/ownership violation.
    Raises CorruptedStateError on JSON decode or schema error.
    """
    state_dir = get_state_dir(git_common_dir)
    state_path = get_state_file_path(git_common_dir)

    if not state_dir.exists():
        return None

    check_and_enforce_permissions(state_dir, 0o700, is_dir=True)

    if not state_path.exists():
        return None

    check_and_enforce_permissions(state_path, 0o600, is_dir=False)

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise CorruptedStateError(f"Cannot read state file {state_path}: {e}") from e

    try:
        raw_json = json.loads(content)
    except json.JSONDecodeError as e:
        raise CorruptedStateError(f"Failed to parse JSON in {state_path}: {e}") from e

    return validate_state_dict(raw_json)


def save_state(git_common_dir: str | Path, state: dict[str, Any]) -> None:
    """
    Atomically persist state dict to state.json using a temp file and fsync.
    Enforces 0700 on directory and 0600 on file.
    """
    validated = validate_state_dict(state)
    state_dir = get_state_dir(git_common_dir)
    safe_ensure_dir(state_dir, 0o700, base_dir=git_common_dir)

    token = secrets.token_hex(4)
    tmp_path = state_dir / f".state.json.tmp.{os.getpid()}.{token}"
    state_file = get_state_file_path(git_common_dir)

    payload = json.dumps(validated, indent=2, ensure_ascii=False) + "\n"

    # Open with O_CREAT | O_WRONLY | O_TRUNC, mode 0600
    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)

    os.replace(str(tmp_path), str(state_file))
    os.chmod(str(state_file), 0o600)


def create_initial_state(
    instance_id: str,
    worktree_path: str | Path,
    counter: int = 0,
) -> dict[str, Any]:
    """Create a new initial state dictionary."""
    now = datetime.now(timezone.utc).isoformat()
    norm_worktree = str(Path(worktree_path).resolve())
    return {
        "version": SCHEMA_VERSION,
        "instance_id": instance_id,
        "worktree_path": norm_worktree,
        "counter": counter,
        "created_at": now,
        "updated_at": now,
    }
