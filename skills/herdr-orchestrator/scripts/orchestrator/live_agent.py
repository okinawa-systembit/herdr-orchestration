from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.env import HerdrEnvError, require_herdr_env
from orchestrator.herdr_cli import HerdrCliError, run_herdr
from orchestrator.identity import RepositoryIdentityError, derive_repository_identity
from orchestrator.worktree import WorktreeError, resolve_worktree_root

PROMPTABLE_AGENT_STATES = frozenset({"idle", "done"})


class LiveAgentError(Exception):
    pass


@dataclass(frozen=True)
class LiveAgentSnapshot:
    name: str
    agent_status: str
    foreground_cwd: Path
    pane_id: str | None
    raw: dict[str, Any]


def _require_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveAgentError(f"herdr payload invalid: {label} is not an object")
    return value


def _parse_foreground_cwd(agent: dict[str, Any]) -> Path:
    fg = agent.get("foreground_cwd")
    if fg is None or (isinstance(fg, str) and not fg.strip()):
        raise LiveAgentError("live agent foreground_cwd unavailable")
    if not isinstance(fg, str):
        raise LiveAgentError("live agent foreground_cwd has invalid type")
    path = Path(fg)
    if not path.is_absolute():
        raise LiveAgentError("live agent foreground_cwd must be an absolute path")
    return path


def _agent_from_herdr_payload(payload: dict[str, Any]) -> LiveAgentSnapshot:
    root = _require_dict(payload, label="root")
    if "error" in root:
        err = root["error"]
        code = err.get("code", "herdr_error") if isinstance(err, dict) else "herdr_error"
        raise LiveAgentError(f"herdr agent get failed: {code}")
    result = _require_dict(root.get("result"), label="result")
    agent = _require_dict(result.get("agent"), label="agent")
    name = agent.get("name")
    if not isinstance(name, str) or not name:
        raise LiveAgentError("live agent has no name")
    fg_path = _parse_foreground_cwd(agent)
    status = agent.get("agent_status")
    if not isinstance(status, str) or not status:
        status = "unknown"
    pane_id = agent.get("pane_id")
    if pane_id is not None and not isinstance(pane_id, str):
        raise LiveAgentError("live agent pane_id has invalid type")
    return LiveAgentSnapshot(
        name=name,
        agent_status=status,
        foreground_cwd=fg_path,
        pane_id=pane_id,
        raw=agent,
    )


def get_caller_live_agent() -> LiveAgentSnapshot:
    """Resolve the Agent in the caller pane (HERDR_PANE_ID → agent get)."""
    pane_id = (os.environ.get("HERDR_PANE_ID") or "").strip()
    if not pane_id:
        raise LiveAgentError("caller HERDR_PANE_ID unavailable")
    return get_live_agent(pane_id)


def get_live_agent(agent_name: str) -> LiveAgentSnapshot:
    """Resolve live Agent by unique name or pane id hosting an agent."""
    try:
        require_herdr_env()
    except HerdrEnvError as exc:
        raise LiveAgentError(exc.message) from exc
    try:
        payload = run_herdr(["agent", "get", agent_name])
    except HerdrCliError as exc:
        raise LiveAgentError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise LiveAgentError("herdr agent get returned non-object payload")
    return _agent_from_herdr_payload(payload)


def assert_promptable_state(snapshot: LiveAgentSnapshot) -> None:
    if snapshot.agent_status not in PROMPTABLE_AGENT_STATES:
        raise LiveAgentError(
            f"agent {snapshot.name!r} state {snapshot.agent_status!r} not promptable"
        )


def verify_worktree_affinity(
    foreground_cwd: Path,
    *,
    expected_worktree_path: Path,
    expected_repository_identity: str,
) -> None:
    """
    §11.0 steps 1–5 (library): resolve git root + repository identity, compare to expected.
    """
    try:
        resolved = resolve_worktree_root(foreground_cwd)
    except WorktreeError as exc:
        raise LiveAgentError("worktree root resolution failed") from exc

    expected_root = expected_worktree_path.resolve()
    if resolved != expected_root:
        raise LiveAgentError(
            f"worktree mismatch: resolved={resolved!s} expected={expected_root!s}"
        )

    try:
        derived = derive_repository_identity(resolved)
    except RepositoryIdentityError as exc:
        raise LiveAgentError("repository identity derivation failed") from exc

    if derived != expected_repository_identity:
        raise LiveAgentError(
            f"repository identity mismatch: derived={derived!r} expected={expected_repository_identity!r}"
        )


def verify_live_agent_context(
    agent_name: str,
    *,
    expected_worktree_path: Path,
    expected_repository_identity: str,
    require_promptable: bool = True,
) -> LiveAgentSnapshot:
    snap = get_live_agent(agent_name)
    if snap.name != agent_name:
        raise LiveAgentError("agent.name mismatch after get")
    if require_promptable:
        assert_promptable_state(snap)
    verify_worktree_affinity(
        snap.foreground_cwd,
        expected_worktree_path=expected_worktree_path,
        expected_repository_identity=expected_repository_identity,
    )
    return snap
