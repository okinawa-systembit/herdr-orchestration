from __future__ import annotations

import fcntl
import re
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse


class RepositoryIdentityError(Exception):
    pass


class GitRepositoryNotInitializedError(RepositoryIdentityError):
    pass


_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_SCP_RE = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")
CONFIG_KEY = "herdr.repository-id"
CONFIG_FILE_NAME = "config"
LOCK_FILE_NAME = "herdr-identity.lock"


def is_valid_uuid_v4(val: str) -> bool:
    """Validate RFC 4122 / RFC 9562 UUID v4 (lowercase hexadecimal, 36 chars with hyphens)."""
    if not isinstance(val, str):
        return False
    return bool(_UUID_V4_RE.match(val))


def is_not_git_repository_error(stderr: str) -> bool:
    """Check if git error output indicates that the directory is not inside a git repository."""
    s = stderr.lower()
    return "not a git repository" in s


def resolve_git_common_dir(worktree: Path) -> Path:
    """
    Resolve the git common directory for worktree.
    Raise GitRepositoryNotInitializedError only if explicitly not inside a git repository.
    Raise RepositoryIdentityError for other git errors (corrupted repo, permission denied, etc.).
    """
    if not worktree.exists():
        raise RepositoryIdentityError(f"worktree path does not exist: {worktree}")

    proc = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip()
        if is_not_git_repository_error(err):
            raise GitRepositoryNotInitializedError(
                err or f"{worktree} is not inside a git repository"
            )
        raise RepositoryIdentityError(
            f"git rev-parse --is-inside-work-tree failed: {err or f'exit code {proc.returncode}'}"
        )

    proc_common = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc_common.returncode != 0:
        err_common = proc_common.stderr.strip()
        if is_not_git_repository_error(err_common):
            raise GitRepositoryNotInitializedError(err_common)
        raise RepositoryIdentityError(
            f"git rev-parse --git-common-dir failed: {err_common}"
        )
    common_dir_str = proc_common.stdout.strip()
    if not common_dir_str:
        raise RepositoryIdentityError("git rev-parse --git-common-dir returned empty path")
    common_path = Path(common_dir_str)
    if not common_path.is_absolute():
        common_path = (worktree / common_path).resolve()
    return common_path


@contextmanager
def _acquire_identity_lock(git_common_dir: Path):
    """Acquire exclusive flock on <git-common-dir>/herdr-identity.lock."""
    lock_file = git_common_dir / LOCK_FILE_NAME
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_file, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        f.close()


def _read_uuid_from_git_config(git_common_dir: Path) -> str | None:
    """Read herdr.repository-id directly from <git-common-dir>/config."""
    config_file = git_common_dir / CONFIG_FILE_NAME
    proc = subprocess.run(
        ["git", "config", "--file", str(config_file), "--get", CONFIG_KEY],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        val = proc.stdout.strip()
        return val if val else None
    if proc.returncode == 1:
        return None
    raise RepositoryIdentityError(f"failed to read git config: {proc.stderr.strip()}")


def _write_uuid_to_git_config(git_common_dir: Path, uuid_str: str) -> None:
    """Write herdr.repository-id directly to <git-common-dir>/config."""
    config_file = git_common_dir / CONFIG_FILE_NAME
    proc = subprocess.run(
        ["git", "config", "--file", str(config_file), CONFIG_KEY, uuid_str],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RepositoryIdentityError(f"failed to write git config: {proc.stderr.strip()}")


def get_or_create_repository_identity(worktree: Path) -> str:
    """
    Get existing UUID v4 from common git config, or generate and persist a new one.
    Guarantees idempotency and concurrency safety via flock on <git-common-dir>/herdr-identity.lock.
    Aborts without returning on write/verify failure.
    """
    common_dir = resolve_git_common_dir(worktree)
    with _acquire_identity_lock(common_dir):
        existing = _read_uuid_from_git_config(common_dir)
        if existing is not None:
            if not is_valid_uuid_v4(existing):
                raise RepositoryIdentityError(
                    f"invalid repository identity format in git config: {existing!r}"
                )
            return existing

        new_uuid = str(uuid.uuid4()).lower()
        _write_uuid_to_git_config(common_dir, new_uuid)

        readback = _read_uuid_from_git_config(common_dir)
        if readback != new_uuid:
            raise RepositoryIdentityError(
                f"repository identity write verification failed: expected={new_uuid!r}, read={readback!r}"
            )
        return new_uuid


def get_repository_identity(worktree: Path) -> str:
    """
    Read-only retrieval of UUID v4 from common git config.
    Does not generate, alter, or repair UUIDs.
    """
    common_dir = resolve_git_common_dir(worktree)
    existing = _read_uuid_from_git_config(common_dir)
    if existing is None:
        raise RepositoryIdentityError("repository identity not initialized")
    if not is_valid_uuid_v4(existing):
        raise RepositoryIdentityError(
            f"invalid repository identity format in git config: {existing!r}"
        )
    return existing


def derive_repository_identity(worktree: Path) -> str:
    """Read-only alias for get_repository_identity(worktree)."""
    return get_repository_identity(worktree)


# Legacy / URL helper routines preserved for utility and backward test compatibility
def _strip_git_suffix(path: str) -> str:
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return path


def _parsed_hostname(parsed) -> str:
    try:
        hostname = parsed.hostname
    except ValueError as exc:
        raise RepositoryIdentityError("invalid host in remote url") from exc
    host = (hostname or "").lower()
    if not host:
        raise RepositoryIdentityError("missing host")
    return host


def _parsed_port(parsed) -> int | None:
    try:
        port = parsed.port
    except ValueError as exc:
        raise RepositoryIdentityError("invalid port in remote url") from exc
    if port is not None and not (1 <= port <= 65535):
        raise RepositoryIdentityError("port out of range in remote url")
    return port


def _canonical_host_path(host: str, port: int | None, path: str, *, default_port: int) -> str:
    if not path:
        raise RepositoryIdentityError("repository path must be non-empty")
    if port is None or port == default_port:
        return f"{host}/{path}"
    return f"{host}:{port}/{path}"


def normalize_remote_url(url: str) -> str:
    raw = url.strip()
    if not raw:
        raise RepositoryIdentityError("empty remote url")

    if "://" not in raw:
        m = _SCP_RE.match(raw)
        if m:
            user = m.group("user")
            host_part = m.group("host")
            if "/" in user or "/" in host_part:
                raise RepositoryIdentityError("unsupported scp-style remote url")
            host = host_part.lower()
            path = _strip_git_suffix(m.group("path"))
            return _canonical_host_path(host, None, path, default_port=22)
        raise RepositoryIdentityError(f"unsupported remote url (missing scheme): {url!r}")

    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise RepositoryIdentityError("invalid remote url") from exc
    scheme = parsed.scheme.lower()
    if scheme not in ("https", "ssh"):
        raise RepositoryIdentityError(f"unsupported scheme: {scheme}")

    host = _parsed_hostname(parsed)
    path = _strip_git_suffix(parsed.path or "")
    port = _parsed_port(parsed)
    if scheme == "https":
        return _canonical_host_path(host, port, path, default_port=443)
    return _canonical_host_path(host, port, path, default_port=22)


def git_remote_fetch_urls(worktree: Path, remote: str = "origin") -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "remote", "get-url", "--all", remote],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RepositoryIdentityError(f"git remote get-url failed: {proc.stderr.strip()}")
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RepositoryIdentityError(f"remote {remote!r} has no fetch urls")
    return lines


def identity_from_origin_fetch_urls(urls: list[str]) -> str:
    identities = {normalize_remote_url(u) for u in urls}
    if len(identities) != 1:
        raise RepositoryIdentityError(
            f"origin fetch urls normalize to multiple identities: {sorted(identities)}"
        )
    return identities.pop()


def list_remotes(worktree: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "remote"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RepositoryIdentityError(proc.stderr.strip() or "git remote failed")
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
