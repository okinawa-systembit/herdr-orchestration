from __future__ import annotations

import hashlib
import re

STANDARD_ROLES = ("design", "reviewer", "implementer", "orchestrator")

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SEP_RE = re.compile(r"[-_]+")


def hash16_from_repository_identity(canonical_identity: str) -> str:
    """Compute 16-hex-char (64-bit) SHA-256 digest of normalized repository identity (UUID v4)."""
    norm = canonical_identity.strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def expected_agent_name(canonical_identity: str, role: str) -> str:
    """
    Pattern A (§6.2.1): deterministic agent.name: r-<hash16>-<role>.
    Guarantees Herdr regex ^[a-z][a-z0-9_-]{0,31}$ and length <= 31 chars (orchestrator is 31).
    Totally independent of directory name, worktree path, or remote URL.
    """
    if role not in STANDARD_ROLES:
        raise ValueError(f"unsupported role: {role}")
    hash16 = hash16_from_repository_identity(canonical_identity)
    name = f"r-{hash16}-{role}"
    if not _NAME_RE.match(name):
        raise ValueError(f"agent.name fails Herdr regex: {name!r}")
    if len(name) > 32:
        raise ValueError(f"agent.name exceeds 32 chars: {name!r} ({len(name)})")
    return name


def derive_requester_role(canonical_identity: str, live_agent_name: str) -> str:
    """§8.5 / §6.2.1: map live agent.name to exactly one standard role."""
    matches = [
        role
        for role in STANDARD_ROLES
        if expected_agent_name(canonical_identity, role) == live_agent_name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"requester role ambiguous or unknown for agent.name={live_agent_name!r}"
        )
    return matches[0]


def project_slug_from_repository_identity(canonical_identity: str) -> str:
    """Legacy helper for slug conversion."""
    s = canonical_identity.lower()
    s = re.sub(r"[^a-z0-9_-]", "-", s)
    s = _SEP_RE.sub("-", s).strip("-")
    if not s:
        s = "repo"
    if not re.match(r"^[a-z]", s):
        s = f"r-{s}"
    return s
