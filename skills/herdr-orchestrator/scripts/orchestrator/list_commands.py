from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.errors import emit_error
from orchestrator.registry.errors import RegistryError
from orchestrator.registry.listing import read_task_candidates


def _parse_registry_timestamp(raw: str) -> datetime | None:
    """Parse ISO-8601 timestamp string. Return timezone-aware datetime or None if invalid."""
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _check_inconsistent_state_combination(doc: dict[str, Any]) -> bool:
    """
    Check if the 4-axis status combination violates invariants I1 through I7.
    Returns True if an invariant is violated (inconsistent), False otherwise.
    Source of truth: docs/herdr-orchestrator/03-task-registry.md §6.3,
                     docs/herdr-orchestrator/05-completion-and-recovery.md §9.13, §9.14.
    """
    task_st = doc["task"]["status"]
    disp_st = doc["dispatch"]["status"]
    res_st = doc["result"]["status"]
    comp_st = doc["completion"]["status"]

    # I1: dispatch.status = pending <=> task.status = created
    if (disp_st == "pending") != (task_st == "created"):
        return True

    # I2: task.status in {created, in_progress, unknown} => result.status = pending
    if task_st in {"created", "in_progress", "unknown"} and res_st != "pending":
        return True

    # I3: task.status = succeeded <=> result.status = succeeded
    if (task_st == "succeeded") != (res_st == "succeeded"):
        return True
    # I3: task.status = failed <=> result.status in {failed, unavailable}
    if (task_st == "failed") != (res_st in {"failed", "unavailable"}):
        return True

    # I4: task.status = in_progress => dispatch.status = sent
    if task_st == "in_progress" and disp_st != "sent":
        return True
    # I4: task.status = unknown => dispatch.status = uncertain
    if task_st == "unknown" and disp_st != "uncertain":
        return True

    # I5: dispatch.status in {failed, skipped} =>
    #     task.status = failed and result.status = unavailable and completion.status in {skipped, not_applicable}
    if disp_st in {"failed", "skipped"}:
        if not (task_st == "failed" and res_st == "unavailable" and comp_st in {"skipped", "not_applicable"}):
            return True

    # I6: completion.status in {processing, sent, failed, uncertain} =>
    #     result.status in {succeeded, failed, unavailable} and dispatch.status in {sent, uncertain}
    if comp_st in {"processing", "sent", "failed", "uncertain"}:
        if not (res_st in {"succeeded", "failed", "unavailable"} and disp_st in {"sent", "uncertain"}):
            return True

    # I7: task.status = cancelled is inactive in MVP
    if task_st == "cancelled":
        return True

    return False


def _calculate_overview_state(doc: dict[str, Any]) -> str:
    """
    Deterministically derive overview.state from 4 status axes in priority order per §4.4:
    1: uncertain: (disp_st == "uncertain" and res_st == "pending") or comp_st == "uncertain" or task_st == "unknown"
    2: open: task_st in {created, in_progress} or res_st == "pending" or comp_st in {pending, processing, failed}
    3: closed: task_st in {succeeded, failed, cancelled} and res_st in {succeeded, failed, unavailable} and comp_st in {not_applicable, sent, skipped}
    """
    task_st = doc["task"]["status"]
    disp_st = doc["dispatch"]["status"]
    res_st = doc["result"]["status"]
    comp_st = doc["completion"]["status"]

    # Priority 1: uncertain
    if (
        task_st == "unknown"
        or (disp_st == "uncertain" and res_st == "pending")
        or comp_st == "uncertain"
    ):
        return "uncertain"

    # Priority 2: open
    if (
        task_st in {"created", "in_progress"}
        or res_st == "pending"
        or comp_st in {"pending", "processing", "failed"}
    ):
        return "open"

    # Priority 3: closed
    if (
        task_st in {"succeeded", "failed", "cancelled"}
        and res_st in {"succeeded", "failed", "unavailable"}
        and comp_st in {"not_applicable", "sent", "skipped"}
    ):
        return "closed"

    # Fallback to uncertain for safety
    return "uncertain"


def _project_task(doc: dict[str, Any], *, overview_state: str, warnings: list[str]) -> dict[str, Any]:
    """Project a canonical registry task document into the task-list item schema."""
    return {
        "task": {
            "id": doc["task"]["id"],
            "type": doc["task"]["type"],
            "mode": doc["task"]["mode"],
            "status": doc["task"]["status"],
        },
        "context": {
            "repository": {
                "identity": doc["context"]["repository"]["identity"],
            },
            "worktree": {
                "path": doc["context"]["worktree"]["path"],
                "branch": doc["context"]["worktree"].get("branch"),
            },
        },
        "requester": {
            "role": doc["requester"]["role"],
            "agent_name": doc["requester"]["agent_name"],
        },
        "worker": {
            "role": doc["worker"]["role"],
            "agent_name": doc["worker"]["agent_name"],
            "pane_id": doc["worker"]["pane_id"],
        },
        "dispatch": {
            "status": doc["dispatch"]["status"],
            "herdr_prompt_started_at": doc["dispatch"].get("herdr_prompt_started_at"),
        },
        "result": {
            "status": doc["result"]["status"],
        },
        "completion": {
            "action": doc["completion"]["action"],
            "status": doc["completion"]["status"],
            "recovery_count": doc["completion"]["recovery_count"],
        },
        "timestamps": {
            "created_at": doc["timestamps"]["created_at"],
            "updated_at": doc["timestamps"]["updated_at"],
        },
        "overview": {
            "state": overview_state,
            "warnings": warnings,
        },
    }


def run_task_list(
    *,
    repository: str | None = None,
    worktree: str | None = None,
    type: str | None = None,
    statuses: list[str] | None = None,
    limit: int = 100,
) -> tuple[dict[str, Any], int]:
    """
    List tasks from Task Registry in read-only manner.
    Returns (payload_dict, exit_code).
    """
    # 1. Semantic validation of worktree
    normalized_worktree: str | None = None
    if worktree is not None:
        wt_path = Path(worktree)
        if not wt_path.is_absolute():
            emit_error("invalid_input", retryable=False, message="--worktree must be an absolute path")
        normalized_worktree = str(wt_path.resolve(strict=False))

    listed_at = datetime.now(timezone.utc).isoformat()

    # 2. Open verified tasks directory and read candidates via dir_fd (R62-08)
    try:
        registry_exists, valid_docs, anomalies, concurrent_changes = read_task_candidates()
    except RegistryError as exc:
        emit_error(exc.code, retryable=False, message=exc.message)

    if not registry_exists:
        payload: dict[str, Any] = {
            "command": "task-list",
            "list_format_version": 1,
            "listed_at": listed_at,
            "registry": {
                "exists": False,
            },
            "filters": {
                "repository": repository,
                "worktree": normalized_worktree,
                "type": type,
                "status": statuses or [],
            },
            "sort": "created_at_desc",
            "limit": limit,
            "complete": True,
            "total_matched": 0,
            "returned": 0,
            "truncated": False,
            "tasks": [],
            "anomalies": [],
            "concurrent_changes": [],
        }
        return payload, 0

    matched_tasks: list[tuple[datetime | None, str, dict[str, Any]]] = []

    # 4. Filter and project valid tasks
    for doc in valid_docs:
        task_id = doc["task"]["id"]
        warnings: list[str] = []
        created_at_raw = doc["timestamps"]["created_at"]
        parsed_dt = _parse_registry_timestamp(created_at_raw)
        if parsed_dt is None:
            warnings.append("created_at_unparseable")

        if _check_inconsistent_state_combination(doc):
            warnings.append("inconsistent_state_combination")

        overview_state = _calculate_overview_state(doc)
        projected = _project_task(doc, overview_state=overview_state, warnings=warnings)

        # Filters
        if repository is not None and doc["context"]["repository"]["identity"] != repository:
            continue
        if normalized_worktree is not None and doc["context"]["worktree"]["path"] != normalized_worktree:
            continue
        if type is not None and doc["task"]["type"] != type:
            continue
        if statuses and doc["task"]["status"] not in statuses:
            continue

        matched_tasks.append((parsed_dt, task_id, projected))

    # 5. Sort:
    # 1. Primary: created_at descending (latest first). Unparseable (None) placed at the end.
    # 2. Secondary: task.id ascending (deterministic).
    matched_tasks.sort(key=lambda item: item[1])  # task.id asc
    matched_tasks.sort(
        key=lambda item: (item[0] is not None, item[0].timestamp() if item[0] is not None else -math.inf),
        reverse=True,
    )

    total_matched = len(matched_tasks)
    returned_tasks = [item[2] for item in matched_tasks[:limit]]
    returned = len(returned_tasks)
    truncated = total_matched > returned
    complete = len(anomalies) == 0
    exit_code = 0 if complete else 3

    payload = {
        "command": "task-list",
        "list_format_version": 1,
        "listed_at": listed_at,
        "registry": {
            "exists": True,
        },
        "filters": {
            "repository": repository,
            "worktree": normalized_worktree,
            "type": type,
            "status": statuses or [],
        },
        "sort": "created_at_desc",
        "limit": limit,
        "complete": complete,
        "total_matched": total_matched,
        "returned": returned,
        "truncated": truncated,
        "tasks": returned_tasks,
        "anomalies": anomalies,
        "concurrent_changes": concurrent_changes,
    }
    return payload, exit_code
