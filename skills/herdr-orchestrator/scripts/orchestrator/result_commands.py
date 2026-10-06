from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from orchestrator.completion_commands import best_effort_completion_after_result
from orchestrator.live_agent import LiveAgentError, get_caller_live_agent, verify_worktree_affinity
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.paths_safe import require_result_artifact, resolve_result_ref
from orchestrator.registry.task_id import validate_task_id


class ResultCommandError(Exception):
    def __init__(
        self,
        code: str,
        *,
        message: str | None = None,
        retryable: bool = False,
        task_id: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.code = code
        self.message = message or code
        self.retryable = retryable
        self.task_id = task_id
        self.hint = hint
        super().__init__(self.message)


def _registry_to_public(exc: RegistryError) -> ResultCommandError:
    hint = None
    if exc.code == "dispatch_not_ready":
        hint = "dispatch が terminal になるまで同一 payload で再試行する"
    return ResultCommandError(
        exc.code,
        message=exc.message,
        retryable=exc.retryable,
        task_id=exc.task_id,
        hint=hint,
    )


def task_status(task_id: str) -> dict[str, Any]:
    validate_task_id(task_id)
    try:
        doc = registry_ops.get_task(task_id)
    except RegistryError as exc:
        raise _registry_to_public(exc) from exc
    return {"command": "task-status", "task": copy.deepcopy(doc)}


def task_result(task_id: str) -> dict[str, Any]:
    validate_task_id(task_id)
    try:
        doc = registry_ops.get_task(task_id)
    except RegistryError as exc:
        raise _registry_to_public(exc) from exc

    worktree = Path(doc["context"]["worktree"]["path"])
    result = copy.deepcopy(doc["result"])
    ref = result.get("ref")
    locator: dict[str, Any] = {
        "task_id": task_id,
        "task_status": doc["task"]["status"],
        "result": result,
        "ref_resolved_path": None,
    }
    status = result.get("status")
    if status in ("succeeded", "failed") and ref:
        try:
            resolved = require_result_artifact(worktree, ref)
            locator["ref_resolved_path"] = str(resolved)
        except RegistryError as exc:
            raise _registry_to_public(exc) from exc
    elif ref:
        try:
            resolved = resolve_result_ref(worktree, ref)
            locator["ref_resolved_path"] = str(resolved) if resolved else None
        except RegistryError as exc:
            raise _registry_to_public(exc) from exc
    return {"command": "task-result", **locator}


def verify_worker_submit_context(
    *,
    expected_worker_agent: str,
    expected_worktree_path: Path,
    expected_repository_identity: str,
) -> None:
    try:
        caller = get_caller_live_agent()
    except LiveAgentError as exc:
        raise ResultCommandError(
            "worker_context_unverified",
            message=str(exc),
            retryable=False,
        ) from exc
    if caller.name != expected_worker_agent:
        raise ResultCommandError(
            "worker_agent_mismatch",
            message="caller agent does not match Registry worker.agent_name",
            retryable=False,
            task_id=None,
        )
    try:
        verify_worktree_affinity(
            caller.foreground_cwd,
            expected_worktree_path=expected_worktree_path,
            expected_repository_identity=expected_repository_identity,
        )
    except LiveAgentError as exc:
        raise ResultCommandError(
            "worker_context_unverified",
            message=str(exc),
            retryable=False,
        ) from exc


def result_submit(
    task_id: str,
    *,
    result_status: str,
    result_ref: str | None,
    summary: str | None,
) -> dict[str, Any]:
    validate_task_id(task_id)
    if result_status not in ("succeeded", "failed", "unavailable"):
        raise ResultCommandError("invalid_input", message="invalid result.status")

    try:
        doc = registry_ops.get_task(task_id)
    except RegistryError as exc:
        raise _registry_to_public(exc) from exc

    verify_worker_submit_context(
        expected_worker_agent=doc["worker"]["agent_name"],
        expected_worktree_path=Path(doc["context"]["worktree"]["path"]),
        expected_repository_identity=doc["context"]["repository"]["identity"],
    )

    before_result = copy.deepcopy(doc["result"])
    before_updated_at = doc["timestamps"]["updated_at"]
    try:
        updated = registry_ops.update_result(
            task_id,
            result_status=result_status,
            result_ref=result_ref,
            summary=summary,
        )
    except RegistryError as exc:
        raise _registry_to_public(exc) from exc

    replay = (
        before_result["status"] in ("succeeded", "failed", "unavailable")
        and updated["timestamps"]["updated_at"] == before_updated_at
    )

    payload: dict[str, Any] = {
        "command": "result-submit",
        "task_id": task_id,
        "idempotent_replay": replay,
        "task": {
            "id": updated["task"]["id"],
            "status": updated["task"]["status"],
            "type": updated["task"]["type"],
            "mode": updated["task"]["mode"],
        },
        "dispatch": {"status": updated["dispatch"]["status"]},
        "result": copy.deepcopy(updated["result"]),
    }
    if not replay:
        chain = best_effort_completion_after_result(task_id)
        if chain.get("attempted"):
            payload["completion_chain"] = chain
    return payload
