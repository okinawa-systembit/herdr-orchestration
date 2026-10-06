from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.registry.errors import RegistryError
from orchestrator.registry.task_id import validate_task_id

SCHEMA_VERSION = 1

FORBIDDEN_FLAT_KEYS = frozenset(
    {"task_id", "task_type", "task_status", "result_ref"},
)

TASK_STATUSES = frozenset(
    {"created", "in_progress", "succeeded", "failed", "cancelled", "unknown"}
)
DISPATCH_STATUSES = frozenset({"pending", "sent", "failed", "uncertain", "skipped"})
RESULT_STATUSES = frozenset({"pending", "succeeded", "failed", "unavailable"})
COMPLETION_STATUSES = frozenset(
    {"not_applicable", "pending", "processing", "sent", "failed", "uncertain", "skipped"}
)
MODES = frozenset({"sync", "async"})
COMPLETION_ACTIONS = frozenset({"none", "resume_requester"})
STANDARD_ROLES = frozenset({"design", "reviewer", "implementer", "orchestrator"})


def _require_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("invalid_schema", message=f"{label} must be an object")
    return value


def _require_str(value: Any, *, label: str, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise RegistryError("invalid_schema", message=f"{label} must be a string")
    if nonempty and not value.strip():
        raise RegistryError("invalid_schema", message=f"{label} must be non-empty")
    return value


def _optional_str_or_null(value: Any, *, label: str) -> None:
    if value is not None and not isinstance(value, str):
        raise RegistryError("invalid_schema", message=f"{label} must be string or null")


def reject_flat_legacy_keys(doc: dict[str, Any]) -> None:
    for key in FORBIDDEN_FLAT_KEYS:
        if key in doc:
            raise RegistryError("invalid_schema", message=f"legacy flat key {key!r} forbidden")


def _validate_party(party: Any, *, label: str) -> None:
    obj = _require_dict(party, label=label)
    role = obj.get("role")
    if role not in STANDARD_ROLES:
        raise RegistryError("invalid_schema", message=f"{label}.role invalid")
    _require_str(obj.get("agent_name"), label=f"{label}.agent_name")
    _require_str(obj.get("pane_id"), label=f"{label}.pane_id")


def _validate_context(context: Any) -> None:
    ctx = _require_dict(context, label="context")
    repo = _require_dict(ctx.get("repository"), label="context.repository")
    _require_str(repo.get("identity"), label="context.repository.identity")
    wt = _require_dict(ctx.get("worktree"), label="context.worktree")
    path_str = _require_str(wt.get("path"), label="context.worktree.path")
    wt_path = Path(path_str)
    if not wt_path.is_absolute():
        raise RegistryError("invalid_schema", message="context.worktree.path must be absolute")
    _optional_str_or_null(wt.get("branch"), label="context.worktree.branch")
    _optional_str_or_null(wt.get("commit"), label="context.worktree.commit")


def _validate_timestamps(ts: Any) -> None:
    obj = _require_dict(ts, label="timestamps")
    _require_str(obj.get("created_at"), label="timestamps.created_at")
    _require_str(obj.get("updated_at"), label="timestamps.updated_at")


def validate_mode_action_consistency(mode: str, completion_action: str, completion_status: str) -> None:
    if mode == "sync" and completion_action != "none":
        raise RegistryError("invalid_schema", message="sync task requires completion.action=none")
    if mode == "sync" and completion_status != "not_applicable":
        raise RegistryError("invalid_schema", message="sync task requires completion.status=not_applicable")
    if completion_action == "none" and completion_status not in ("not_applicable",):
        raise RegistryError("invalid_schema", message="completion.action=none requires not_applicable status")


def validate_loaded_task(doc: dict[str, Any]) -> None:
    reject_flat_legacy_keys(doc)
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise RegistryError("invalid_schema", message="unsupported schema_version")

    task = _require_dict(doc.get("task"), label="task")
    task_id = _require_str(task.get("id"), label="task.id")
    validate_task_id(task_id)
    _require_str(task.get("type"), label="task.type")
    mode = task.get("mode")
    if mode not in MODES:
        raise RegistryError("invalid_schema", message="invalid task.mode")
    _require_str(task.get("instruction"), label="task.instruction")
    if task.get("status") not in TASK_STATUSES:
        raise RegistryError("invalid_schema", message="invalid task.status")

    _validate_context(doc.get("context"))
    _validate_party(doc.get("requester"), label="requester")
    _validate_party(doc.get("worker"), label="worker")

    dispatch = _require_dict(doc.get("dispatch"), label="dispatch")
    if dispatch.get("status") not in DISPATCH_STATUSES:
        raise RegistryError("invalid_schema", message="invalid dispatch.status")
    _optional_str_or_null(dispatch.get("reason"), label="dispatch.reason")
    started = dispatch.get("herdr_prompt_started_at")
    if started is not None and not isinstance(started, str):
        raise RegistryError("invalid_schema", message="dispatch.herdr_prompt_started_at invalid")

    result = _require_dict(doc.get("result"), label="result")
    if result.get("status") not in RESULT_STATUSES:
        raise RegistryError("invalid_schema", message="invalid result.status")
    _optional_str_or_null(result.get("ref"), label="result.ref")
    _optional_str_or_null(result.get("summary"), label="result.summary")

    completion = _require_dict(doc.get("completion"), label="completion")
    action = completion.get("action")
    if action not in COMPLETION_ACTIONS:
        raise RegistryError("invalid_schema", message="invalid completion.action")
    if completion.get("status") not in COMPLETION_STATUSES:
        raise RegistryError("invalid_schema", message="invalid completion.status")
    _optional_str_or_null(completion.get("reason"), label="completion.reason")
    rc = completion.get("recovery_count")
    if not isinstance(rc, int) or rc < 0:
        raise RegistryError("invalid_schema", message="completion.recovery_count invalid")

    validate_mode_action_consistency(mode, action, completion["status"])

    handoff_ex = doc.get("handoff_execution")
    if handoff_ex is not None:
        hx = _require_dict(handoff_ex, label="handoff_execution")
        pid = hx.get("lock_owner_pid")
        if pid is not None and not isinstance(pid, int):
            raise RegistryError("invalid_schema", message="handoff_execution.lock_owner_pid invalid")
        _optional_str_or_null(hx.get("locked_at"), label="handoff_execution.locked_at")

    _validate_timestamps(doc.get("timestamps"))


def validate_create_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise RegistryError("invalid_input", message="payload must be object")
    task_in = _require_dict(payload.get("task"), label="task")
    mode = task_in.get("mode")
    if mode not in MODES:
        raise RegistryError("invalid_input", message="task.mode must be sync or async")
    _require_str(task_in.get("type"), label="task.type")
    _require_str(task_in.get("instruction"), label="task.instruction")

    _validate_context(payload.get("context"))
    _validate_party(payload.get("requester"), label="requester")
    _validate_party(payload.get("worker"), label="worker")

    completion = _require_dict(payload.get("completion"), label="completion")
    action = completion.get("action")
    if action not in COMPLETION_ACTIONS:
        raise RegistryError("invalid_input", message="completion.action invalid")
    if mode == "sync" and action != "none":
        raise RegistryError("invalid_input", message="sync tasks require completion.action=none")


def initial_completion_status(mode: str, completion_action: str) -> str:
    if mode == "sync" or completion_action == "none":
        return "not_applicable"
    return "pending"
