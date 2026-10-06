from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse


class RepositoryIdentityError(Exception):
    pass


_SCP_RE = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")


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
    """
    Map one Git remote URL to canonical identity (§11.0).
    MVP: scp, https, ssh:// only.
    """
    raw = url.strip()
    if not raw:
        raise RepositoryIdentityError("empty remote url")

    m = _SCP_RE.match(raw)
    if m:
        user = m.group("user")
        host_part = m.group("host")
        if "/" in user or "/" in host_part:
            raise RepositoryIdentityError("unsupported scp-style remote url")
        host = host_part.lower()
        path = _strip_git_suffix(m.group("path"))
        return _canonical_host_path(host, None, path, default_port=22)

    if "://" not in raw:
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


def derive_repository_identity(worktree: Path) -> str:
    """
    §11.0: origin required; origin fetch URLs (all) → identity; every remote must match.
    """
    remotes = list_remotes(worktree)
    if "origin" not in remotes:
        raise RepositoryIdentityError("origin remote is required")

    origin_identity = identity_from_origin_fetch_urls(git_remote_fetch_urls(worktree, "origin"))

    for remote in remotes:
        if remote == "origin":
            continue
        remote_identity = identity_from_origin_fetch_urls(git_remote_fetch_urls(worktree, remote))
        if remote_identity != origin_identity:
            raise RepositoryIdentityError(
                f"remotes disagree on repository identity: "
                f"origin={origin_identity!r}, {remote}={remote_identity!r}"
            )

    return origin_identity
