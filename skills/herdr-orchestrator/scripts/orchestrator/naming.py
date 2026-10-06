from __future__ import annotations

import hashlib
import re
STANDARD_ROLES = ("design", "reviewer", "implementer", "orchestrator")

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SEP_RE = re.compile(r"[-_]+")


def project_slug_from_repository_identity(canonical_identity: str) -> str:
    """§4.1 steps 1–6: identity canonical string → project-slug."""
    s = canonical_identity.lower()
    s = re.sub(r"[^a-z0-9_-]", "-", s)
    s = _SEP_RE.sub("-", s).strip("-")
    if not s:
        s = "repo"
    if not re.match(r"^[a-z]", s):
        s = f"r-{s}"
    return s


def expected_agent_name(canonical_identity: str, role: str) -> str:
    """§4.1 steps 7–11: deterministic agent.name for (identity, role)."""
    if role not in STANDARD_ROLES:
        raise ValueError(f"unsupported role: {role}")
    slug = project_slug_from_repository_identity(canonical_identity)
    candidate = f"{slug}-{role}"
    if len(candidate) <= 32:
        name = candidate
    else:
        hash4 = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()[:4]
        prefix_len = 32 - len(role) - 6
        if prefix_len <= 0:
            raise ValueError("agent name construction failed: prefix_len <= 0")
        prefix = slug[:prefix_len].rstrip("-")
        name = f"{prefix}-{hash4}-{role}"
    if not _NAME_RE.match(name):
        raise ValueError(f"agent.name fails Herdr regex: {name!r}")
    return name


def derive_requester_role(canonical_identity: str, live_agent_name: str) -> str:
    """§8.5: map live agent.name to exactly one standard role."""
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
