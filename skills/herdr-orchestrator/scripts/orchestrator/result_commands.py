from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from orchestrator.completion_commands import best_effort_completion_after_result
from orchestrator.identity import GLOBAL_SCOPE_ID, is_valid_uuid_v4
from orchestrator.live_agent import (
    LiveAgentError,
    get_caller_live_agent,
    is_path_within_worktree,
    verify_worktree_affinity,
)
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.paths_safe import require_result_artifact, resolve_result_ref
from orchestrator.registry.task_id import validate_task_id
from orchestrator.role_bindings import RoleBindingError, resolve_role_by_pane


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


def _lookup_all_bound_roles(repo_id: str, pane_id: str | None) -> dict[str, str]:
    """Return dict of scope -> bound_role for the pane (e.g., {'local': 'レビュワー', 'global': '調査担当'})."""
    roles: dict[str, str] = {}
    if not pane_id:
        return roles
    try:
        lr = resolve_role_by_pane(repo_id, pane_id)
        if lr:
            roles["local"] = lr
    except RoleBindingError:
        pass
    try:
        gr = resolve_role_by_pane(GLOBAL_SCOPE_ID, pane_id)
        if gr:
            roles["global"] = gr
    except RoleBindingError:
        pass
    return roles


def _lookup_bound_role(repo_id: str, pane_id: str | None) -> str | None:
    if not pane_id:
        return None
    try:
        local_r = resolve_role_by_pane(repo_id, pane_id)
        if local_r is not None:
            return local_r
    except RoleBindingError:
        pass
    try:
        return resolve_role_by_pane(GLOBAL_SCOPE_ID, pane_id)
    except RoleBindingError:
        return None


def _check_party_cwd_matches(
    live_cwd: Path,
    *,
    expected_cwd: Path,
    worktree_path: Path,
    repository_identity: str,
) -> bool:
    if is_valid_uuid_v4(repository_identity) and is_path_within_worktree(expected_cwd, worktree_path):
        try:
            verify_worktree_affinity(
                live_cwd,
                expected_worktree_path=worktree_path,
                expected_repository_identity=repository_identity,
            )
            return True
        except LiveAgentError:
            return False
    return live_cwd.resolve() == expected_cwd.resolve()


def build_recipient_verification(doc: dict[str, Any]) -> dict[str, Any]:
    worktree_path = Path(doc["context"]["worktree"]["path"]).resolve()
    repo_id = doc["context"]["repository"]["identity"]
    worker_info = doc["worker"]
    requester_info = doc["requester"]

    worker_pane = worker_info.get("pane_id") or worker_info.get("agent_name")
    worker_agent_name = worker_info.get("agent_name")
    worker_role = worker_info.get("role")
    worker_cwd = Path(worker_info.get("cwd") or worktree_path).resolve()

    requester_pane = requester_info.get("pane_id") or requester_info.get("agent_name")
    requester_agent_name = requester_info.get("agent_name")
    requester_role = requester_info.get("role")

    expected_worker_summary = {
        "role": worker_role,
        "pane_id": worker_pane,
        "cwd": str(worker_cwd),
    }

    try:
        caller = get_caller_live_agent()
    except LiveAgentError as exc:
        return {
            "verified": False,
            "caller_role_in_task": "other",
            "reason": "caller_live_agent_unavailable",
            "action_required": "STOP_AND_REPORT_TO_HUMAN",
            "expected_worker": expected_worker_summary,
            "message": f"呼び出し元ペインの状態を確認できませんでした（{exc}）。作業を行わず人間へチャットで報告して停止してください。",
        }

    caller_pane = (caller.pane_id or caller.name or "").strip()
    caller_cwd = caller.foreground_cwd.resolve()
    bound_roles = _lookup_all_bound_roles(repo_id, caller_pane)
    bound_role = bound_roles.get("local") or bound_roles.get("global")

    is_worker_caller = caller_pane == worker_pane or caller.name == worker_agent_name
    is_requester_caller = caller_pane == requester_pane or caller.name == requester_agent_name

    if is_worker_caller:
        cwd_ok = _check_party_cwd_matches(
            caller_cwd,
            expected_cwd=worker_cwd,
            worktree_path=worktree_path,
            repository_identity=repo_id,
        )
        if not bound_roles:
            role_ok = True
        else:
            role_ok = (worker_role in bound_roles.values()) if worker_role else True

        if cwd_ok and role_ok:
            caller_payload = {
                "bound_role": worker_role if (worker_role and bound_roles and worker_role in bound_roles.values()) else (bound_role or worker_role),
                "pane_id": caller_pane,
                "cwd": str(caller_cwd),
            }
            if bound_roles:
                caller_payload["bound_roles"] = bound_roles
            return {
                "verified": True,
                "caller_role_in_task": "worker",
                "expected_worker": expected_worker_summary,
                "actual_caller": caller_payload,
            }
        return {
            "verified": False,
            "caller_role_in_task": "worker",
            "reason": "recipient_role_or_cwd_mismatch",
            "action_required": "STOP_AND_REPORT_TO_HUMAN",
            "expected_worker": expected_worker_summary,
            "actual_caller": {
                "bound_role": bound_role,
                "bound_roles": bound_roles,
                "pane_id": caller_pane,
                "cwd": str(caller_cwd),
            },
            "message": "このタスクの宛先情報と現在のペインの状態が一致しません。作業を一切行わず、人間へチャットで報告して停止してください。",
        }

    if is_requester_caller:
        cwd_ok = _check_party_cwd_matches(
            caller_cwd,
            expected_cwd=worktree_path,
            worktree_path=worktree_path,
            repository_identity=repo_id,
        )
        if cwd_ok:
            return {
                "verified": True,
                "caller_role_in_task": "requester",
                "expected_requester": {
                    "role": requester_role,
                    "pane_id": requester_pane,
                    "cwd": str(worktree_path),
                },
                "actual_caller": {
                    "bound_role": bound_role or requester_role,
                    "pane_id": caller_pane,
                    "cwd": str(caller_cwd),
                },
            }
        return {
            "verified": False,
            "caller_role_in_task": "requester",
            "reason": "requester_cwd_mismatch",
            "action_required": "STOP_AND_REPORT_TO_HUMAN",
            "expected_requester": {
                "role": requester_role,
                "pane_id": requester_pane,
                "cwd": str(worktree_path),
            },
            "actual_caller": {
                "bound_role": bound_role,
                "pane_id": caller_pane,
                "cwd": str(caller_cwd),
            },
            "message": "このタスクの依頼元情報と現在のペインの状態が一致しません。作業を行わず人間へチャットで報告して停止してください。",
        }

    return {
        "verified": False,
        "caller_role_in_task": "other",
        "reason": "caller_not_task_participant",
        "action_required": "STOP_AND_REPORT_TO_HUMAN",
        "expected_worker": expected_worker_summary,
        "actual_caller": {
            "bound_role": bound_role,
            "pane_id": caller_pane,
            "cwd": str(caller_cwd),
        },
        "message": "このペインは当タスクの宛先（Worker）でも依頼元（Requester）でもありません。誤配送の可能性があるため、作業を一切行わず人間へチャットで報告して停止してください。",
    }


def task_status(task_id: str) -> dict[str, Any]:
    validate_task_id(task_id)
    try:
        doc = registry_ops.get_task(task_id)
    except RegistryError as exc:
        raise _registry_to_public(exc) from exc
    return {
        "command": "task-status",
        "recipient_verification": build_recipient_verification(doc),
        "task": copy.deepcopy(doc),
    }


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
        "recipient_verification": build_recipient_verification(doc),
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
    expected_worker_pane: str | None = None,
    expected_worker_role: str | None = None,
    expected_worker_cwd: Path | None = None,
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
    if caller.name != expected_worker_agent and caller.pane_id != expected_worker_agent:
        raise ResultCommandError(
            "worker_agent_mismatch",
            message="caller agent does not match Registry worker.agent_name",
            retryable=False,
            task_id=None,
        )
    if expected_worker_role is not None and caller.pane_id:
        bound_roles = _lookup_all_bound_roles(expected_repository_identity, caller.pane_id)
        if bound_roles and expected_worker_role not in bound_roles.values():
            raise ResultCommandError(
                "worker_context_unverified",
                message=(
                    f"caller pane {caller.pane_id!r} is not bound to expected role {expected_worker_role!r} "
                    f"(bound roles: {bound_roles})"
                ),
                retryable=False,
            )
    target_cwd = (expected_worker_cwd or expected_worktree_path).resolve()
    if is_valid_uuid_v4(expected_repository_identity) and is_path_within_worktree(target_cwd, expected_worktree_path):
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
    else:
        if caller.foreground_cwd.resolve() != target_cwd:
            raise ResultCommandError(
                "worker_context_unverified",
                message=(
                    f"worker cwd mismatch: resolved={caller.foreground_cwd.resolve()!s} "
                    f"expected={target_cwd!s}"
                ),
                retryable=False,
            )


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

    worktree_path = Path(doc["context"]["worktree"]["path"])
    worker_cwd = Path(doc["worker"].get("cwd") or worktree_path)
    verify_worker_submit_context(
        expected_worker_agent=doc["worker"]["agent_name"],
        expected_worker_pane=doc["worker"].get("pane_id"),
        expected_worker_role=doc["worker"].get("role"),
        expected_worker_cwd=worker_cwd,
        expected_worktree_path=worktree_path,
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
