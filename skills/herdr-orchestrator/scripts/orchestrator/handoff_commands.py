from __future__ import annotations

import copy
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.handoff_lock import HandoffLockSession
from orchestrator.herdr_cli import HerdrCliError, run_herdr
from orchestrator.identity import RepositoryIdentityError, derive_repository_identity
from orchestrator.live_agent import (
    LiveAgentError,
    get_caller_live_agent,
    get_live_agent,
    verify_live_agent_context,
)
from orchestrator.naming import derive_requester_role, expected_agent_name
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.schema import MODES, STANDARD_ROLES
from orchestrator.worktree import WorktreeError, resolve_worktree_root

DEFAULT_RESULT_WAIT_SEC = 30 * 60
RESULT_POLL_INTERVAL_SEC = float(
    os.environ.get("HERDR_ORCHESTRATOR_RESULT_POLL_SEC", "2.0")
)
TERMINAL_RESULTS = frozenset({"succeeded", "failed", "unavailable"})


class HandoffCommandError(Exception):
    def __init__(
        self,
        code: str,
        *,
        message: str | None = None,
        retryable: bool = False,
        task_id: str | None = None,
    ) -> None:
        self.code = code
        self.message = message or code
        self.retryable = retryable
        self.task_id = task_id
        super().__init__(self.message)


def _registry_to_handoff(exc: RegistryError) -> HandoffCommandError:
    return HandoffCommandError(
        exc.code,
        message=exc.message,
        retryable=exc.retryable,
        task_id=exc.task_id,
    )


def _git_branch(worktree: Path) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    return branch or None


def build_task_envelope(task_id: str) -> str:
    return (
        f"{task_id}\n"
        "herdr-orchestrator task-status / task-result を実行し Registry を読んでから作業すること"
    )


def _herdr_error_code(exc: HerdrCliError) -> str:
    payload = exc.payload or {}
    herdr_err = payload.get("herdr") if isinstance(payload.get("herdr"), dict) else {}
    inner = herdr_err.get("error") if isinstance(herdr_err.get("error"), dict) else {}
    if isinstance(inner.get("code"), str):
        return inner["code"]
    return "herdr_prompt_failed"


def _dispatch_outcome_from_prompt_error(exc: HerdrCliError) -> tuple[str, str]:
    code = _herdr_error_code(exc)
    if code in ("agent_prompt_stalled",):
        return "uncertain", code
    if code in (
        "agent_not_found",
        "agent_not_ready",
        "agent_blocked",
        "herdr_prompt_failed",
    ):
        return "failed", code
    return "uncertain", code


def _require_live_pane_id(snap: Any, *, party: str) -> str:
    pane_id = snap.pane_id
    if not isinstance(pane_id, str) or not pane_id.strip():
        raise HandoffCommandError(
            "live_pane_id_missing",
            message=f"{party} live agent has no pane_id",
        )
    return pane_id


@dataclass(frozen=True)
class HandoffInput:
    mode: str
    worker_role: str
    task_type: str
    instruction: str
    result_wait_timeout_sec: int


def _resolve_parties(*, worker_role: str) -> tuple[Any, Any, Path, str, str]:
    if worker_role not in STANDARD_ROLES:
        raise HandoffCommandError("invalid_input", message="invalid worker role")
    try:
        requester = get_caller_live_agent()
    except LiveAgentError as exc:
        raise HandoffCommandError("requester_context_unverified", message=str(exc)) from exc
    try:
        worktree = resolve_worktree_root(requester.foreground_cwd)
    except WorktreeError as exc:
        raise HandoffCommandError("requester_context_unverified", message=str(exc)) from exc
    try:
        identity = derive_repository_identity(worktree)
    except RepositoryIdentityError as exc:
        raise HandoffCommandError("requester_context_unverified", message=str(exc)) from exc
    try:
        requester_role = derive_requester_role(identity, requester.name)
    except ValueError as exc:
        raise HandoffCommandError("requester_context_unverified", message=str(exc)) from exc
    worker_name = expected_agent_name(identity, worker_role)
    try:
        worker = verify_live_agent_context(
            worker_name,
            expected_worktree_path=worktree,
            expected_repository_identity=identity,
            require_promptable=True,
        )
    except LiveAgentError as exc:
        raise HandoffCommandError("worker_unavailable", message=str(exc)) from exc
    _require_live_pane_id(requester, party="requester")
    _require_live_pane_id(worker, party="worker")
    return requester, worker, worktree, identity, requester_role


def _build_create_payload(
    *,
    handoff: HandoffInput,
    worktree: Path,
    identity: str,
    requester_role: str,
    requester_snap: Any,
    worker_snap: Any,
) -> dict[str, Any]:
    completion_action = "none" if handoff.mode == "sync" else "resume_requester"
    return {
        "task": {
            "type": handoff.task_type,
            "mode": handoff.mode,
            "instruction": handoff.instruction,
        },
        "context": {
            "repository": {"identity": identity},
            "worktree": {
                "path": str(worktree.resolve()),
                "branch": _git_branch(worktree),
                "commit": None,
            },
        },
        "requester": {
            "role": requester_role,
            "agent_name": requester_snap.name,
            "pane_id": _require_live_pane_id(requester_snap, party="requester"),
        },
        "worker": {
            "role": handoff.worker_role,
            "agent_name": worker_snap.name,
            "pane_id": _require_live_pane_id(worker_snap, party="worker"),
        },
        "completion": {"action": completion_action},
    }


def _observe_worker_during_result_wait(doc: dict[str, Any]) -> None:
    worker_name = doc["worker"]["agent_name"]
    dispatch_status = doc["dispatch"]["status"]
    try:
        snap = get_live_agent(worker_name)
    except LiveAgentError as exc:
        if dispatch_status == "sent":
            raise HandoffCommandError(
                "worker_unavailable",
                retryable=False,
                task_id=doc["task"]["id"],
                message=str(exc),
            ) from exc
        return
    if snap.agent_status == "blocked":
        raise HandoffCommandError(
            "worker_blocked",
            retryable=False,
            task_id=doc["task"]["id"],
            message="worker is blocked during result wait",
        )


def _wait_for_result_terminal(task_id: str, *, timeout_sec: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        doc = registry_ops.get_task(task_id)
        if doc["result"]["status"] in TERMINAL_RESULTS:
            return doc
        if doc["dispatch"]["status"] == "pending":
            raise HandoffCommandError(
                "dispatch_not_ready",
                retryable=True,
                task_id=task_id,
                message="mark-dispatch incomplete; cannot poll result",
            )
        _observe_worker_during_result_wait(doc)
        time.sleep(RESULT_POLL_INTERVAL_SEC)
    raise HandoffCommandError(
        "result_wait_timeout",
        retryable=True,
        task_id=task_id,
        message="result.status did not reach terminal before timeout",
    )


def run_handoff(handoff: HandoffInput) -> dict[str, Any]:
    if handoff.mode not in MODES:
        raise HandoffCommandError("invalid_input", message="mode must be sync or async")

    requester, worker, worktree, identity, requester_role = _resolve_parties(
        worker_role=handoff.worker_role
    )
    create_payload = _build_create_payload(
        handoff=handoff,
        worktree=worktree,
        identity=identity,
        requester_role=requester_role,
        requester_snap=requester,
        worker_snap=worker,
    )

    try:
        doc = registry_ops.create_task(create_payload)
    except RegistryError as exc:
        raise _registry_to_handoff(exc) from exc

    task_id = doc["task"]["id"]
    prompt_sent = False
    dispatch_outcome: str | None = None
    dispatch_reason: str | None = None

    with HandoffLockSession(task_id):
        try:
            doc = registry_ops.record_prompt_start(task_id)
        except RegistryError as exc:
            raise _registry_to_handoff(exc) from exc

        try:
            registry_ops.assert_handoff_continuable(task_id)
        except RegistryError as exc:
            raise _registry_to_handoff(exc) from exc

        try:
            run_herdr(["agent", "prompt", worker.name, build_task_envelope(task_id)])
            prompt_sent = True
            dispatch_outcome, dispatch_reason = "sent", None
        except HerdrCliError as exc:
            prompt_sent = False
            dispatch_outcome, dispatch_reason = _dispatch_outcome_from_prompt_error(exc)

        try:
            doc = registry_ops.mark_dispatch(
                task_id,
                outcome=dispatch_outcome,
                reason=dispatch_reason,
            )
        except RegistryError as exc:
            raise _registry_to_handoff(exc) from exc

        result_wait: dict[str, Any] | None = None
        if handoff.mode == "sync" and dispatch_outcome == "sent":
            try:
                result_wait = _wait_for_result_terminal(
                    task_id,
                    timeout_sec=handoff.result_wait_timeout_sec,
                )
                doc = result_wait
            except HandoffCommandError:
                raise
            except RegistryError as exc:
                raise _registry_to_handoff(exc) from exc

    out: dict[str, Any] = {
        "command": "handoff",
        "task_id": task_id,
        "mode": handoff.mode,
        "prompt_sent": prompt_sent,
        "task": copy.deepcopy(doc["task"]),
        "dispatch": copy.deepcopy(doc["dispatch"]),
        "result": copy.deepcopy(doc["result"]),
        "completion": copy.deepcopy(doc["completion"]),
    }
    if result_wait is not None:
        out["result_wait"] = {
            "completed": True,
            "result_status": result_wait["result"]["status"],
            "task_status": result_wait["task"]["status"],
        }
    elif handoff.mode == "sync" and dispatch_outcome == "sent":
        out["result_wait"] = {"completed": False}
    return out
