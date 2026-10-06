from __future__ import annotations

import re
from pathlib import Path

from orchestrator.registry.errors import RegistryError

_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _reject_uri_like_ref(ref: str) -> None:
    if "://" in ref or _URI_SCHEME_RE.match(ref):
        raise RegistryError(
            "invalid_result_ref",
            message="result.ref must be a worktree-relative path, not a URL",
        )


def resolve_result_ref(worktree_path: Path, ref: str | None) -> Path | None:
    """Validate ref and return resolved path under worktree, or None when ref is null."""
    if ref is None:
        return None
    validate_result_ref(worktree_path, ref)
    root = worktree_path.resolve()
    return (root / ref).resolve()


def validate_result_ref(worktree_path: Path, ref: str | None) -> None:
    if ref is None:
        return
    if not isinstance(ref, str) or not ref.strip():
        raise RegistryError("invalid_result_ref", message="result.ref must be non-empty when set")
    _reject_uri_like_ref(ref)
    if ref.startswith(("/", "~")):
        raise RegistryError("invalid_result_ref", message="result.ref must be worktree-relative")
    parts = Path(ref).parts
    if ".." in parts:
        raise RegistryError("invalid_result_ref", message="result.ref must not contain ..")
    root = worktree_path.resolve()
    candidate = (root / ref).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RegistryError(
            "invalid_result_ref",
            message="result.ref resolves outside worktree",
        ) from exc
    if candidate.is_symlink():
        raise RegistryError("invalid_result_ref", message="result.ref must not be a symlink")


def require_result_artifact(worktree_path: Path, ref: str) -> Path:
    resolved = resolve_result_ref(worktree_path, ref)
    assert resolved is not None
    if not resolved.is_file():
        raise RegistryError(
            "artifact_missing",
            message="result artifact file does not exist",
        )
    return resolved
