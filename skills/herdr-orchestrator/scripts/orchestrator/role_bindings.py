from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat as stat_module
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from orchestrator.identity import GLOBAL_SCOPE_ID, is_dir_scope_id, is_valid_uuid_v4
from orchestrator.paths import roles_dir
from orchestrator.registry.schema import STANDARD_ROLES, normalize_role_name

ROLE_BINDINGS_SCHEMA_VERSION = 1
_DIR_MODE = 0o755
_FILE_MODE = 0o644
_PANE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9:_-]{0,63}$")


class RoleBindingError(Exception):
    def __init__(self, code: str, *, message: str | None = None) -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_chmod(path: Path, mode: int) -> None:
    if not path.exists():
        return
    try:
        is_owner = path.stat().st_uid == os.getuid()
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        if is_owner or is_root:
            cur_mode = stat_module.S_IMODE(path.stat().st_mode)
            if cur_mode != mode:
                os.chmod(path, mode)
    except (PermissionError, OSError):
        pass


def _assert_safe_path(path: Path, *, must_exist: bool = False) -> None:
    if path.is_symlink():
        raise RoleBindingError("role_binding_unsafe_path", message=f"symlink rejected: {path!s}")
    if must_exist and not path.exists():
        raise RoleBindingError("role_binding_unsafe_path", message=f"missing path: {path!s}")


def _ensure_roles_dir() -> Path:
    root = roles_dir()
    orchestrator_root = root.parent
    state_parent = orchestrator_root.parent

    _assert_safe_path(state_parent, must_exist=False)
    if orchestrator_root.exists():
        _assert_safe_path(orchestrator_root, must_exist=True)
        if not orchestrator_root.is_dir():
            raise RoleBindingError(
                "role_binding_unsafe_path",
                message=f"not a directory: {orchestrator_root!s}",
            )
    else:
        try:
            orchestrator_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RoleBindingError(
                "role_binding_write_failed",
                message=f"cannot create directory: {orchestrator_root} ({exc.strerror or exc})",
            ) from exc
    _safe_chmod(orchestrator_root, _DIR_MODE)

    if root.exists():
        _assert_safe_path(root, must_exist=True)
        if not root.is_dir():
            raise RoleBindingError(
                "role_binding_unsafe_path",
                message=f"not a directory: {root!s}",
            )
    else:
        try:
            root.mkdir(mode=_DIR_MODE, exist_ok=True)
        except OSError as exc:
            raise RoleBindingError(
                "role_binding_write_failed",
                message=f"cannot create roles directory: {root} ({exc.strerror or exc})",
            ) from exc
    _safe_chmod(root, _DIR_MODE)
    return root


def _repo_file_stem(repository_id: str) -> str:
    if not isinstance(repository_id, str) or not repository_id.strip():
        raise RoleBindingError("invalid_input", message="repository_id must be a non-empty string")
    norm = repository_id.strip().lower()
    if norm == GLOBAL_SCOPE_ID:
        return GLOBAL_SCOPE_ID
    if is_valid_uuid_v4(norm) or is_dir_scope_id(norm):
        return norm
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]
    return f"repo_{digest}"


def role_bindings_file_path(repository_id: str) -> Path:
    stem = _repo_file_stem(repository_id)
    return roles_dir() / f"{stem}.json"


def _role_lock_path(repository_id: str) -> Path:
    stem = _repo_file_stem(repository_id)
    return roles_dir() / f"{stem}.json.lock"


def validate_role_name(role: str) -> str:
    try:
        return normalize_role_name(role, allow_unbound=False)
    except ValueError as exc:
        raise RoleBindingError("invalid_input", message=str(exc)) from exc


def validate_display_name(label: str | None, *, default_from_role_arg: str) -> str:
    if label is not None:
        if not isinstance(label, str) or not label.strip():
            raise RoleBindingError("invalid_input", message="--label must be a non-empty string")
        text = label.strip()
    else:
        text = default_from_role_arg.strip() if isinstance(default_from_role_arg, str) else ""
    # 先頭の '--' を除去（Herdr CLI のオプション引数誤認を全般的に防止）
    while text.startswith("--"):
        text = text[2:].strip()
    if not text or len(text) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise RoleBindingError("invalid_input", message=f"invalid display_name: {text!r}")
    return text


def is_target_meta_valid(meta: Any) -> bool:
    if not isinstance(meta, dict):
        return False
    cwd = meta.get("cwd")
    agent = meta.get("agent")
    if not isinstance(cwd, str) or not cwd.strip() or not Path(cwd).is_absolute():
        return False
    if not isinstance(agent, str) or not agent.strip():
        return False
    return True


def validate_pane_id(pane_id: str) -> str:
    if not isinstance(pane_id, str):
        raise RoleBindingError("invalid_input", message="pane_id must be a string")
    cleaned = pane_id.strip()
    if not cleaned or not _PANE_ID_RE.match(cleaned):
        raise RoleBindingError("invalid_input", message=f"invalid pane_id: {pane_id!r}")
    return cleaned


@contextmanager
def _role_mutation_lock(repository_id: str) -> Iterator[None]:
    _ensure_roles_dir()
    lpath = _role_lock_path(repository_id)
    _assert_safe_path(lpath, must_exist=False)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(lpath), flags, _FILE_MODE)
    except OSError as exc:
        raise RoleBindingError(
            "role_binding_write_failed",
            message=f"cannot open lock {lpath.name}: {exc.strerror or exc}",
        ) from exc
    _safe_chmod(lpath, _FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_doc_unlocked(repository_id: str) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    path = role_bindings_file_path(repository_id)
    if not path.exists():
        return {}, {}
    _assert_safe_path(path, must_exist=True)
    try:
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RoleBindingError(
            "role_binding_corrupted",
            message=f"cannot read role bindings {path.name}: {exc}",
        ) from exc
    if not isinstance(doc, dict):
        raise RoleBindingError("role_binding_corrupted", message="role bindings root must be object")
    if doc.get("schema_version") != ROLE_BINDINGS_SCHEMA_VERSION:
        raise RoleBindingError("role_binding_corrupted", message="unsupported schema_version")
    roles_raw = doc.get("roles")
    if not isinstance(roles_raw, dict):
        raise RoleBindingError("role_binding_corrupted", message="roles must be object")
    roles: dict[str, str] = {}
    for k, v in roles_raw.items():
        if not isinstance(k, str) or not isinstance(v, str) or not v.strip():
            continue
        try:
            norm_k = validate_role_name(k)
        except RoleBindingError:
            continue
        roles[norm_k] = v.strip()

    targets_raw = doc.get("targets")
    targets: dict[str, dict[str, Any]] = {}
    if isinstance(targets_raw, dict):
        for k, meta in targets_raw.items():
            if not isinstance(k, str) or not isinstance(meta, dict):
                continue
            try:
                norm_k = validate_role_name(k)
            except RoleBindingError:
                continue
            if norm_k in roles:
                targets[norm_k] = dict(meta)
    return roles, targets


def _read_bindings_unlocked(repository_id: str) -> dict[str, str]:
    roles, _ = _read_doc_unlocked(repository_id)
    return roles


def _write_doc_unlocked(
    repository_id: str,
    roles: dict[str, str],
    targets: dict[str, dict[str, Any]],
) -> None:
    root = _ensure_roles_dir()
    dest = role_bindings_file_path(repository_id)
    _assert_safe_path(dest, must_exist=False)
    norm_id = repository_id.strip().lower()
    repo_val = (
        norm_id
        if (norm_id == GLOBAL_SCOPE_ID or is_valid_uuid_v4(norm_id) or is_dir_scope_id(norm_id))
        else repository_id.strip()
    )
    clean_targets = {
        r: targets[r]
        for r in sorted(roles.keys())
        if r in targets and isinstance(targets[r], dict)
    }
    doc: dict[str, Any] = {
        "schema_version": ROLE_BINDINGS_SCHEMA_VERSION,
        "repository_id": repo_val,
        "roles": {role: roles[role] for role in sorted(roles.keys())},
        "targets": clean_targets,
        "updated_at": _utc_now_iso(),
    }
    encoded = (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    tmp_name = f".role_{dest.stem}.{secrets.token_hex(8)}.tmp"
    tmp_path = root / tmp_name
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(tmp_path), flags, _FILE_MODE)
    except OSError as exc:
        raise RoleBindingError(
            "role_binding_write_failed",
            message=f"temp open failed: {exc.strerror or exc}",
        ) from exc
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(fd, encoded[offset:])
            if written <= 0:
                raise OSError("incomplete write to role bindings temp file")
            offset += written
        os.fsync(fd)
    except Exception as exc:
        os.close(fd)
        tmp_path.unlink(missing_ok=True)
        raise RoleBindingError(
            "role_binding_write_failed",
            message=f"write failed: {exc}",
        ) from exc
    os.close(fd)
    _safe_chmod(tmp_path, _FILE_MODE)
    try:
        os.replace(tmp_path, dest)
        _safe_chmod(dest, _FILE_MODE)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise RoleBindingError(
            "role_binding_write_failed",
            message=f"atomic replace failed: {exc.strerror or exc}",
        ) from exc


def get_role_bindings(repository_id: str) -> dict[str, str]:
    """Return mapping {role: pane_id} for repository_id."""
    if not roles_dir().exists():
        return {}
    return _read_bindings_unlocked(repository_id)


def get_role_binding_document(repository_id: str) -> dict[str, Any]:
    """Return full binding info including roles, targets, and outdated_roles."""
    if not roles_dir().exists():
        return {
            "repository_id": repository_id,
            "roles": {},
            "targets": {},
            "outdated_roles": [],
        }
    roles, targets = _read_doc_unlocked(repository_id)
    outdated = sorted(r for r in roles if not is_target_meta_valid(targets.get(r)))
    return {
        "repository_id": repository_id,
        "roles": roles,
        "targets": targets,
        "outdated_roles": outdated,
    }


def set_role_binding(
    repository_id: str,
    role: str,
    pane_id: str,
    *,
    target_meta: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Bind role -> pane_id for repository_id.
    Enforces a strict 1-to-1 bijection between role and pane_id within the same repository:
    if another role was previously bound to the same pane_id, that stale binding is removed.
    """
    valid_role = validate_role_name(role)
    valid_pane = validate_pane_id(pane_id)
    with _role_mutation_lock(repository_id):
        current_roles, current_targets = _read_doc_unlocked(repository_id)
        updated_roles = {
            r: p
            for r, p in current_roles.items()
            if p != valid_pane and r != valid_role
        }
        updated_targets = {
            r: m
            for r, m in current_targets.items()
            if r in updated_roles
        }
        updated_roles[valid_role] = valid_pane
        if target_meta is not None:
            meta_copy = dict(target_meta)
            meta_copy["pane_id"] = valid_pane
            updated_targets[valid_role] = meta_copy
        _write_doc_unlocked(repository_id, updated_roles, updated_targets)
        return dict(sorted(updated_roles.items()))


def compare_and_update_role_binding(
    repository_id: str,
    role: str,
    *,
    expected_previous_pane_id: str,
    new_pane_id: str,
    target_meta: dict[str, Any],
) -> bool:
    """
    Atomically update role -> new_pane_id and target_meta under lock ONLY IF
    the role is currently still bound to expected_previous_pane_id (prevents lost updates),
    and ONLY write to disk if the pane_id or target_meta actually changed.
    """
    valid_role = validate_role_name(role)
    valid_pane = validate_pane_id(new_pane_id)
    with _role_mutation_lock(repository_id):
        current_roles, current_targets = _read_doc_unlocked(repository_id)
        if current_roles.get(valid_role) != expected_previous_pane_id:
            return False
        meta_copy = dict(target_meta)
        meta_copy["pane_id"] = valid_pane
        if valid_pane == expected_previous_pane_id and current_targets.get(valid_role) == meta_copy:
            return True
        updated_roles = {
            r: p
            for r, p in current_roles.items()
            if p != valid_pane and r != valid_role
        }
        updated_targets = {
            r: m
            for r, m in current_targets.items()
            if r in updated_roles
        }
        updated_roles[valid_role] = valid_pane
        updated_targets[valid_role] = meta_copy
        _write_doc_unlocked(repository_id, updated_roles, updated_targets)
        return True


def clear_role_binding(
    repository_id: str,
    *,
    role: str | None = None,
    clear_all: bool = False,
) -> dict[str, str]:
    """Clear a specific role binding or all role bindings for repository_id."""
    if clear_all and role is not None:
        raise RoleBindingError("invalid_input", message="cannot specify both role and --all")
    if not clear_all and role is None:
        raise RoleBindingError("invalid_input", message="specify a role or --all")
    valid_role = validate_role_name(role) if role is not None else None
    with _role_mutation_lock(repository_id):
        current_roles, current_targets = _read_doc_unlocked(repository_id)
        if clear_all:
            updated_roles: dict[str, str] = {}
            updated_targets: dict[str, dict[str, Any]] = {}
        else:
            assert valid_role is not None
            updated_roles = {r: p for r, p in current_roles.items() if r != valid_role}
            updated_targets = {r: m for r, m in current_targets.items() if r != valid_role}
        _write_doc_unlocked(repository_id, updated_roles, updated_targets)
        return dict(sorted(updated_roles.items()))


def resolve_pane_by_role(repository_id: str, role: str) -> str | None:
    """Return bound pane_id for role in repository_id, or None if unbound."""
    valid_role = validate_role_name(role)
    bindings = get_role_bindings(repository_id)
    return bindings.get(valid_role)


def resolve_role_by_pane(repository_id: str, pane_id: str) -> str | None:
    """Return bound role for pane_id in repository_id, or None if unbound."""
    if not isinstance(pane_id, str) or not pane_id.strip():
        return None
    cleaned = pane_id.strip()
    bindings = get_role_bindings(repository_id)
    matches = [r for r, p in bindings.items() if p == cleaned]
    if len(matches) == 1:
        return matches[0]
    return None
