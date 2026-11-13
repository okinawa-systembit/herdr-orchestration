from __future__ import annotations

import copy
import fcntl
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestrator.paths import handoff_lock_path, tasks_dir
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import io
from orchestrator.registry.paths_safe import require_result_artifact, validate_result_ref
from orchestrator.registry.schema import (
    SCHEMA_VERSION,
    initial_completion_status,
    validate_create_payload,
    validate_loaded_task,
)
from orchestrator.registry.task_id import generate_task_id, validate_task_id

_RETENTION_DAYS = 30


def _is_initial_dispatch_failure_terminal(doc: dict[str, Any]) -> bool:
    return (
        doc["dispatch"]["status"] in ("failed", "skipped")
        and doc["task"]["status"] == "failed"
        and doc["result"]["status"] == "unavailable"
    )


def _task_eligible_for_cleanup(doc: Any, cutoff: datetime) -> bool:
    if not isinstance(doc, dict):
        return False
    try:
        task = doc.get("task")
        if not isinstance(task, dict):
            return False
        task_status = task.get("status")
        completion = doc.get("completion")
        if not isinstance(completion, dict):
            return False
        completion_status = completion.get("status")

        if task_status not in ("succeeded", "failed", "cancelled"):
            return False
        if completion_status not in ("not_applicable", "sent", "failed", "skipped"):
            return False

        timestamps = doc.get("timestamps")
        if not isinstance(timestamps, dict):
            return False
        created = timestamps.get("created_at")
        if not isinstance(created, str):
            return False
        created_dt = datetime.fromisoformat(created)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        return created_dt <= cutoff
    except (KeyError, TypeError, ValueError):
        return False


def _extract_repository_identity(doc: Any) -> str:
    if not isinstance(doc, dict):
        return "unknown"
    ctx = doc.get("context")
    if not isinstance(ctx, dict):
        return "unknown"
    repo = ctx.get("repository")
    if not isinstance(repo, dict):
        return "unknown"
    ident = repo.get("identity")
    if ident:
        return str(ident)
    return "unknown"


def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    validate_create_payload(payload)
    task_id = generate_task_id()
    now = io.utc_now_iso()
    task_in = payload["task"]
    mode = task_in["mode"]
    completion_action = payload["completion"]["action"]
    worker_doc = copy.deepcopy(payload["worker"])
    if not worker_doc.get("cwd"):
        worker_doc["cwd"] = str(Path(payload["context"]["worktree"]["path"]).resolve())
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task": {
            "id": task_id,
            "type": task_in["type"],
            "mode": mode,
            "instruction": task_in["instruction"],
            "status": "created",
        },
        "context": copy.deepcopy(payload["context"]),
        "requester": copy.deepcopy(payload["requester"]),
        "worker": worker_doc,
        "dispatch": {
            "status": "pending",
            "reason": None,
            "herdr_prompt_started_at": None,
        },
        "result": {"status": "pending", "ref": None, "summary": None},
        "completion": {
            "action": completion_action,
            "status": initial_completion_status(mode, completion_action),
            "reason": None,
            "recovery_count": 0,
        },
        "handoff_execution": {"lock_owner_pid": None, "locked_at": None},
        "timestamps": {"created_at": now, "updated_at": now},
    }
    validate_loaded_task(doc)
    io.create_task_document(doc)
    return doc


def get_task(task_id: str) -> dict[str, Any]:
    return io.read_task_document(task_id)


def _parse_registry_timestamp(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def find_stale_dispatch_candidates(
    *,
    min_age_sec: float,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    candidates: list[dict[str, Any]] = []
    root = tasks_dir()
    if not root.exists() or not root.is_dir():
        return []
    for path in sorted(root.glob("task_*.json")):
        if path.name.endswith(".json.lock"):
            continue
        try:
            doc = io.read_task_document(path.stem)
            task = doc["task"]
            dispatch = doc["dispatch"]
            if task["status"] != "created" or dispatch["status"] != "pending":
                continue
            updated = _parse_registry_timestamp(doc["timestamps"]["updated_at"])
        except Exception:
            continue
        age_sec = (current - updated).total_seconds()
        if age_sec < min_age_sec:
            continue
        scenario = "B" if dispatch.get("herdr_prompt_started_at") else "A"
        candidates.append(
            {
                "task_id": doc["task"]["id"],
                "scenario": scenario,
                "updated_at": doc["timestamps"]["updated_at"],
                "age_sec": round(age_sec, 3),
                "worker_agent_name": doc["worker"]["agent_name"],
            }
        )
    return candidates


_PANE_ID_RE = re.compile(r"^w[0-9A-Za-z]+:p[0-9A-Za-z]+$")


def _evaluate_worker_pane_status(
    *,
    worker_pane_id: str | None,
    worker_agent_name: str | None,
    target_worker_path: str | None,
    worker_cwd_raw: str | None,
    worktree_path_raw: str | None,
    by_pane_id: dict[str, dict[str, Any]] | None,
    by_name: dict[str, dict[str, Any]] | None,
) -> tuple[str, list[str]]:
    if by_pane_id is None or by_name is None:
        return "unavailable", []

    lookup_pane_id = worker_pane_id
    if not lookup_pane_id and isinstance(worker_agent_name, str) and _PANE_ID_RE.match(worker_agent_name):
        lookup_pane_id = worker_agent_name

    matched_pane: dict[str, Any] | None = None
    if lookup_pane_id:
        matched_pane = by_pane_id.get(lookup_pane_id)
    elif isinstance(worker_agent_name, str):
        matched_pane = by_name.get(worker_agent_name)

    if matched_pane is None:
        return "missing", ["worker_pane_missing"]

    if not matched_pane.get("agent") or matched_pane.get("agent_status") == "none":
        return "agent_not_running", ["worker_agent_not_running"]

    live_cwd_raw = matched_pane.get("cwd")
    if isinstance(live_cwd_raw, str) and live_cwd_raw and target_worker_path:
        from orchestrator.live_agent import is_path_within_worktree

        live_cwd = Path(live_cwd_raw)
        target_path = Path(target_worker_path)
        in_target = is_path_within_worktree(live_cwd, target_path)
        in_same_worktree = False
        if (
            not in_target
            and isinstance(worker_cwd_raw, str)
            and worker_cwd_raw
            and isinstance(worktree_path_raw, str)
            and worktree_path_raw
        ):
            wt_path = Path(worktree_path_raw)
            if is_path_within_worktree(Path(worker_cwd_raw), wt_path) and is_path_within_worktree(
                live_cwd, wt_path
            ):
                in_same_worktree = True
        if not in_target and not in_same_worktree:
            return "context_changed", ["worker_pane_context_changed"]

    return "live", []


def find_stale_in_progress_candidates(
    *,
    min_age_sec: float,
    live_panes: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Detect stale in_progress / unknown tasks using 2-tier signals (Issue #174).
    Completely read-only: does not create directories, acquire locks, or modify files.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    root = tasks_dir()
    if not root.exists() or not root.is_dir():
        return []

    by_pane_id: dict[str, dict[str, Any]] | None = None
    by_name: dict[str, dict[str, Any]] | None = None
    if live_panes is not None:
        by_pane_id = {}
        by_name = {}
        for pane in live_panes:
            if not isinstance(pane, dict):
                continue
            pid = pane.get("pane_id")
            if isinstance(pid, str) and pid:
                by_pane_id[pid] = pane
            pname = pane.get("name")
            if isinstance(pname, str) and pname:
                by_name[pname] = pane

    candidates: list[dict[str, Any]] = []
    for path in sorted(root.glob("task_*.json")):
        if path.name.endswith(".json.lock"):
            continue
        try:
            doc = io.read_task_document(path.stem)
            task = doc["task"]
            result = doc["result"]
            if task["status"] not in ("in_progress", "unknown") or result["status"] != "pending":
                continue
            updated_at_raw = doc["timestamps"]["updated_at"]
            updated = _parse_registry_timestamp(updated_at_raw)
        except Exception:
            continue

        age_sec = (current - updated).total_seconds()
        worktree_path_raw = doc["context"]["worktree"].get("path")
        worker_cwd_raw = doc["worker"].get("cwd")
        worktree_exists: bool | None = (
            Path(worktree_path_raw).is_dir()
            if isinstance(worktree_path_raw, str) and worktree_path_raw
            else None
        )
        target_worker_path: str | None = (
            worker_cwd_raw
            if isinstance(worker_cwd_raw, str) and worker_cwd_raw
            else (
                worktree_path_raw
                if isinstance(worktree_path_raw, str) and worktree_path_raw
                else None
            )
        )
        worker_cwd_exists: bool | None = (
            Path(target_worker_path).is_dir() if target_worker_path is not None else None
        )

        reasons: list[str] = []
        if target_worker_path is not None and not worker_cwd_exists:
            reasons.append("worktree_missing")

        worker_pane_id = doc["worker"].get("pane_id")
        worker_agent_name = doc["worker"].get("agent_name")
        worker_pane_status, pane_reasons = _evaluate_worker_pane_status(
            worker_pane_id=worker_pane_id,
            worker_agent_name=worker_agent_name,
            target_worker_path=target_worker_path,
            worker_cwd_raw=worker_cwd_raw,
            worktree_path_raw=worktree_path_raw,
            by_pane_id=by_pane_id,
            by_name=by_name,
        )
        reasons.extend(pane_reasons)

        has_strong = len(reasons) > 0
        is_age_stale = age_sec >= min_age_sec
        if is_age_stale:
            reasons.append("updated_at_stale")

        if not has_strong and not is_age_stale:
            continue

        signal_strength = "strong" if has_strong else "weak"
        if any(
            r in reasons
            for r in ("worktree_missing", "worker_pane_missing", "worker_pane_context_changed")
        ):
            suggested_action = "cancel-task"
        elif "worker_agent_not_running" in reasons:
            suggested_action = "recover_or_cancel"
        else:
            suggested_action = "verify_live_task"

        candidates.append(
            {
                "task_id": doc["task"]["id"],
                "task_status": task["status"],
                "dispatch_status": doc["dispatch"]["status"],
                "result_status": result["status"],
                "signal_strength": signal_strength,
                "reasons": reasons,
                "updated_at": updated_at_raw,
                "age_sec": round(age_sec, 3),
                "repository_identity": doc["context"]["repository"]["identity"],
                "worktree_path": worktree_path_raw,
                "worktree_exists": worktree_exists,
                "worker_role": doc["worker"].get("role"),
                "worker_cwd": target_worker_path,
                "worker_cwd_exists": worker_cwd_exists,
                "worker_pane_id": worker_pane_id,
                "worker_agent_name": worker_agent_name,
                "worker_pane_status": worker_pane_status,
                "suggested_action": suggested_action,
            }
        )
    return candidates


def repair_missing_prompt_started_at(task_id: str) -> dict[str, Any]:
    """§6.13 B': record herdr_prompt_started_at under Registry lock when worker trace exists."""

    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        task = doc["task"]
        dispatch = doc["dispatch"]
        if task["status"] != "created" or dispatch["status"] != "pending":
            raise RegistryError("registry_conflict", task_id=task_id)
        if dispatch.get("herdr_prompt_started_at"):
            return doc
        out = copy.deepcopy(doc)
        out["dispatch"]["herdr_prompt_started_at"] = io.utc_now_iso()
        return out

    return io.mutate_task(task_id, mutator)


def assert_handoff_continuable(task_id: str) -> dict[str, Any]:
    from orchestrator.live_agent import LiveAgentError, verify_live_party_cwd

    def verify_registry_state(doc: dict[str, Any]) -> dict[str, Any]:
        task = doc["task"]
        dispatch = doc["dispatch"]
        if task["status"] != "created" or dispatch["status"] != "pending":
            raise RegistryError("registry_conflict", task_id=task_id)
        if not dispatch.get("herdr_prompt_started_at"):
            raise RegistryError(
                "registry_conflict",
                task_id=task_id,
                message="herdr_prompt_started_at required before assert",
            )
        return doc

    doc = io.mutate_task(task_id, verify_registry_state)
    worker_name = doc["worker"]["agent_name"]
    worktree_path = Path(doc["context"]["worktree"]["path"])
    worker_cwd = Path(doc["worker"].get("cwd") or worktree_path)
    try:
        verify_live_party_cwd(
            worker_name,
            expected_cwd=worker_cwd,
            worktree_path=worktree_path,
            repository_identity=doc["context"]["repository"]["identity"],
            require_promptable=True,
        )
    except LiveAgentError as exc:
        raise RegistryError(
            "handoff_not_continuable",
            task_id=task_id,
            message=str(exc),
        ) from exc
    return {"task_id": task_id, "continuable": True}


def record_prompt_start(task_id: str, *, started_at: str | None = None) -> dict[str, Any]:
    ts = started_at or io.utc_now_iso()

    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        task = doc["task"]
        dispatch = doc["dispatch"]
        if task["status"] != "created" or dispatch["status"] != "pending":
            raise RegistryError("registry_conflict", task_id=task_id)
        if dispatch.get("herdr_prompt_started_at"):
            raise RegistryError("registry_conflict", task_id=task_id)
        if task["status"] in ("succeeded", "failed", "cancelled"):
            raise RegistryError("registry_conflict", task_id=task_id)
        out = copy.deepcopy(doc)
        out["dispatch"]["herdr_prompt_started_at"] = ts
        return out

    return io.mutate_task(task_id, mutator)


def _completion_on_dispatch_failure(doc: dict[str, Any]) -> str:
    action = doc["completion"]["action"]
    if action == "resume_requester":
        return "skipped"
    return "not_applicable"


def mark_dispatch(
    task_id: str,
    *,
    outcome: str,
    reason: str | None = None,
) -> dict[str, Any]:
    if outcome not in ("sent", "failed", "skipped", "uncertain"):
        raise RegistryError("invalid_input", message="invalid dispatch outcome")

    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        task = doc["task"]
        dispatch = doc["dispatch"]
        if task["status"] != "created" or dispatch["status"] != "pending":
            raise RegistryError("registry_conflict", task_id=task_id)
        if not dispatch.get("herdr_prompt_started_at"):
            raise RegistryError("registry_conflict", task_id=task_id)
        if dispatch["status"] in ("sent", "failed", "uncertain", "skipped"):
            raise RegistryError("registry_conflict", task_id=task_id)

        out = copy.deepcopy(doc)
        out["dispatch"]["status"] = outcome
        if reason is not None:
            out["dispatch"]["reason"] = reason

        if outcome == "sent":
            out["task"]["status"] = "in_progress"
        elif outcome in ("failed", "skipped"):
            out["task"]["status"] = "failed"
            out["result"]["status"] = "unavailable"
            out["completion"]["status"] = _completion_on_dispatch_failure(doc)
        elif outcome == "uncertain":
            out["task"]["status"] = "unknown"
            if doc["completion"]["action"] == "resume_requester":
                out["completion"]["status"] = "pending"
            else:
                out["completion"]["status"] = "not_applicable"
        return out

    return io.mutate_task(task_id, mutator)


def update_result(
    task_id: str,
    *,
    result_status: str,
    result_ref: str | None,
    summary: str | None,
) -> dict[str, Any]:
    if result_status not in ("succeeded", "failed", "unavailable"):
        raise RegistryError("invalid_input", message="invalid result.status")

    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        task = doc["task"]
        dispatch = doc["dispatch"]
        result = doc["result"]
        if dispatch["status"] == "pending":
            raise RegistryError(
                "dispatch_not_ready",
                retryable=True,
                task_id=task_id,
            )
        if dispatch["status"] not in ("sent", "failed", "uncertain", "skipped"):
            raise RegistryError("registry_conflict", task_id=task_id)

        if _is_initial_dispatch_failure_terminal(doc):
            raise RegistryError(
                "registry_conflict",
                task_id=task_id,
                message="result submit not allowed after initial dispatch failure",
            )

        canonical = {
            "status": result_status,
            "ref": result_ref,
            "summary": summary,
        }
        if result["status"] in ("succeeded", "failed", "unavailable"):
            existing = {
                "status": result["status"],
                "ref": result.get("ref"),
                "summary": result.get("summary"),
            }
            if existing == canonical:
                return doc
            raise RegistryError("registry_conflict", task_id=task_id)

        if task["status"] not in ("in_progress", "unknown"):
            raise RegistryError("registry_conflict", task_id=task_id)

        worktree = Path(doc["context"]["worktree"]["path"])
        if result_status == "unavailable":
            if result_ref is not None:
                raise RegistryError("invalid_input", message="unavailable result must have ref=null")
        else:
            if result_ref is not None:
                require_result_artifact(worktree, result_ref)
            else:
                if not isinstance(summary, str) or not summary.strip():
                    raise RegistryError(
                        "invalid_input",
                        message="result.ref or non-empty summary is required for succeeded/failed result",
                    )
                if len(summary) > 2000:
                    raise RegistryError(
                        "invalid_input",
                        message="summary exceeds maximum length of 2000 characters",
                    )

        out = copy.deepcopy(doc)
        out["result"]["status"] = result_status
        out["result"]["ref"] = result_ref
        out["result"]["summary"] = summary
        if result_status == "succeeded":
            out["task"]["status"] = "succeeded"
        else:
            out["task"]["status"] = "failed"
        return out

    return io.mutate_task(task_id, mutator)


def assert_completion_claim_eligible(doc: dict[str, Any]) -> None:
    task_id = doc["task"]["id"]
    completion = doc["completion"]
    if completion["action"] != "resume_requester":
        raise RegistryError("registry_conflict", task_id=task_id)
    if completion["status"] != "pending":
        raise RegistryError("registry_conflict", task_id=task_id)
    if doc["dispatch"]["status"] not in ("sent", "uncertain"):
        raise RegistryError("registry_conflict", task_id=task_id)
    if doc["result"]["status"] not in ("succeeded", "failed", "unavailable"):
        raise RegistryError("registry_conflict", task_id=task_id)
    if doc["task"]["status"] not in ("succeeded", "failed"):
        raise RegistryError("registry_conflict", task_id=task_id)
    if _is_initial_dispatch_failure_terminal(doc):
        raise RegistryError(
            "registry_conflict",
            task_id=task_id,
            message="completion claim not allowed after initial dispatch failure",
        )


def claim_completion(task_id: str) -> dict[str, Any]:
    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        assert_completion_claim_eligible(doc)
        out = copy.deepcopy(doc)
        out["completion"]["status"] = "processing"
        return out

    return io.mutate_task(task_id, mutator)


def finish_completion(
    task_id: str,
    *,
    outcome: str,
    reason: str | None = None,
) -> dict[str, Any]:
    if outcome not in ("sent", "failed", "uncertain", "skipped"):
        raise RegistryError("invalid_input", message="invalid completion outcome")

    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        completion = doc["completion"]
        if completion["status"] != "processing":
            raise RegistryError("registry_conflict", task_id=task_id)
        out = copy.deepcopy(doc)
        out["completion"]["status"] = outcome
        if reason is not None:
            out["completion"]["reason"] = reason
        return out

    return io.mutate_task(task_id, mutator)


def reset_completion(task_id: str) -> dict[str, Any]:
    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        if doc["task"]["status"] == "cancelled":
            raise RegistryError(
                "registry_conflict",
                task_id=task_id,
                message="cannot reset completion of cancelled task",
            )
        completion = doc["completion"]
        if completion["status"] not in ("processing", "failed", "uncertain", "skipped"):
            raise RegistryError("registry_conflict", task_id=task_id)
        if _is_initial_dispatch_failure_terminal(doc):
            raise RegistryError("registry_conflict", task_id=task_id)
        out = copy.deepcopy(doc)
        out["completion"]["status"] = "pending"
        out["completion"]["recovery_count"] = int(completion.get("recovery_count") or 0) + 1
        return out

    return io.mutate_task(task_id, mutator)


def cancel_task(
    task_id: str,
    *,
    reason: str,
    cancelled_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transition an in_progress or unknown task to cancelled (Issue #173)."""
    validate_task_id(task_id)
    if not isinstance(reason, str):
        raise RegistryError("invalid_input", message="--reason must be a string")
    clean_reason = reason.strip()
    if not clean_reason:
        raise RegistryError("invalid_input", message="--reason must not be empty")
    if len(clean_reason) > 2000:
        raise RegistryError("invalid_input", message="--reason exceeds 2000 characters")

    if cancelled_by is not None:
        role_val = cancelled_by.get("role")
        actor = {
            "role": str(role_val).strip() if isinstance(role_val, str) and role_val.strip() else "unbound",
            "pane_id": cancelled_by.get("pane_id") if isinstance(cancelled_by.get("pane_id"), str) else None,
            "agent_name": cancelled_by.get("agent_name") if isinstance(cancelled_by.get("agent_name"), str) else None,
        }
    else:
        actor = {"role": "unbound", "pane_id": None, "agent_name": None}

    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        task_status = doc["task"]["status"]
        if task_status == "created":
            raise RegistryError(
                "registry_conflict",
                task_id=task_id,
                message="cannot cancel task in 'created' status; use reconcile-dispatch instead",
            )
        if task_status not in ("in_progress", "unknown"):
            raise RegistryError(
                "registry_conflict",
                task_id=task_id,
                message=f"cannot cancel task in terminal status {task_status!r}",
            )
        completion = doc["completion"]
        if completion["status"] == "processing":
            raise RegistryError(
                "registry_conflict",
                task_id=task_id,
                message="cannot cancel task while completion.status is 'processing'",
            )

        out = copy.deepcopy(doc)
        out["task"]["status"] = "cancelled"
        out["task"]["cancel_reason"] = clean_reason
        out["task"]["cancelled_by"] = copy.deepcopy(actor)
        if completion["action"] == "resume_requester" and completion["status"] == "pending":
            out["completion"]["status"] = "skipped"
        return out

    return io.mutate_task(task_id, mutator)


def inspect_completion(task_id: str) -> dict[str, Any]:
    doc = get_task(task_id)
    return {
        "task_id": task_id,
        "completion": copy.deepcopy(doc["completion"]),
        "result_status": doc["result"]["status"],
        "task_status": doc["task"]["status"],
    }


def _apply_scenario_a_terminal(
    task_id: str,
    *,
    dispatch_outcome: str,
    dispatch_reason: str | None,
) -> dict[str, Any]:
    def mutator(doc: dict[str, Any]) -> dict[str, Any]:
        dispatch = doc["dispatch"]
        task = doc["task"]
        if dispatch["status"] != "pending" or task["status"] != "created":
            raise RegistryError("registry_conflict", task_id=task_id)
        if dispatch.get("herdr_prompt_started_at"):
            raise RegistryError("registry_conflict", task_id=task_id)
        out = copy.deepcopy(doc)
        out["dispatch"]["status"] = dispatch_outcome
        if dispatch_reason is not None:
            out["dispatch"]["reason"] = dispatch_reason
        if dispatch_outcome == "uncertain":
            out["task"]["status"] = "unknown"
            out["dispatch"]["reason"] = dispatch_reason or "reconcile_indeterminate_no_worker_trace"
            if doc["completion"]["action"] == "resume_requester":
                out["completion"]["status"] = "pending"
            else:
                out["completion"]["status"] = "not_applicable"
        elif dispatch_outcome == "failed":
            out["task"]["status"] = "failed"
            out["result"]["status"] = "unavailable"
            out["completion"]["status"] = _completion_on_dispatch_failure(doc)
        else:
            raise RegistryError("invalid_input", message="unsupported dispatch-outcome")
        return out

    return io.mutate_task(task_id, mutator)


def reconcile_dispatch(
    task_id: str,
    *,
    dispatch_outcome: str | None = None,
    dispatch_reason: str | None = None,
) -> dict[str, Any]:
    from orchestrator.worker_trace import probe_worker_task_trace

    with io.handoff_lock_exclusive(task_id, nonblocking=True) as acquired:
        if not acquired:
            raise RegistryError("registry_conflict", task_id=task_id)

        doc = get_task(task_id)
        dispatch = doc["dispatch"]
        task = doc["task"]
        if dispatch["status"] != "pending":
            raise RegistryError("registry_conflict", task_id=task_id)

        scenario = "B" if dispatch.get("herdr_prompt_started_at") else "A"
        repaired_b_prime = False
        if scenario == "A":
            worker_name = doc["worker"]["agent_name"]
            if probe_worker_task_trace(task_id, worker_name):
                doc = repair_missing_prompt_started_at(task_id)
                scenario = "B"
                repaired_b_prime = True

        if scenario == "A" and dispatch_outcome is None:
            return {
                "task_id": task_id,
                "updated": False,
                "scenario": "A",
                "action": "await_human",
                "worker_trace_observed": False,
            }

        if dispatch_outcome is None:
            raise RegistryError("invalid_input", message="--dispatch-outcome required")

        if dispatch_outcome == "failed" and not dispatch_reason:
            raise RegistryError("invalid_input", message="dispatch reason required for failed outcome")

        if task["status"] != "created":
            raise RegistryError("registry_conflict", task_id=task_id)

        if scenario == "A":
            updated = _apply_scenario_a_terminal(
                task_id,
                dispatch_outcome=dispatch_outcome,
                dispatch_reason=dispatch_reason,
            )
        elif dispatch_outcome == "uncertain":
            updated = mark_dispatch(task_id, outcome="uncertain")
        elif dispatch_outcome == "failed":
            updated = mark_dispatch(task_id, outcome="failed", reason=dispatch_reason)
        else:
            raise RegistryError("invalid_input", message="unsupported dispatch-outcome")

        needs_result = updated["result"]["status"] == "pending"
        result: dict[str, Any] = {
            "task_id": task_id,
            "updated": True,
            "scenario": scenario,
            "task": updated["task"],
            "dispatch": updated["dispatch"],
            "needs_result_reregistration": needs_result,
        }
        if repaired_b_prime:
            result["repaired_b_prime"] = True
        return result


def _try_delete_task_if_eligible(
    task_id: str,
    cutoff: datetime,
    *,
    out_repo_id: list[str | None] | None = None,
) -> bool:
    """
    2-stage deletion of eligible tasks (docs/herdr-orchestrator-task-registry-cleanup.md §6.1).
    Phase 1: Read without lock to filter out ineligible / active / corrupted tasks.
    Phase 2: Acquire task_mutation_lock, re-verify eligibility, and delete.
    Returns True if deleted, False otherwise.
    If out_repo_id is provided, appends the repository identity or 'unknown'.
    """
    path = io.task_file_path(task_id)
    if not path.exists():
        return False

    # Phase 1: Lock-free preliminary filter
    try:
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
        if not _task_eligible_for_cleanup(doc, cutoff):
            return False
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False

    # Phase 2: Lock-protected re-verification and deletion
    repo_id: str | None = None
    with io.task_mutation_lock(task_id):
        if not path.exists():
            return False
        try:
            doc = io.read_task_document_unlocked(path)
        except RegistryError:
            return False
        if not _task_eligible_for_cleanup(doc, cutoff):
            return False

        repo_id = _extract_repository_identity(doc)

        path.unlink(missing_ok=True)
        handoff_lock_path(task_id).unlink(missing_ok=True)

    # Post-lock cleanup of task_<id>.json.lock (§6.2)
    if not path.exists():
        io.registry_lock_path(task_id).unlink(missing_ok=True)

    if out_repo_id is not None:
        out_repo_id.append(repo_id)

    return True


def _sweep_orphan_locks(root: Path, *, dry_run: bool = False) -> int:
    """
    Sweep orphan lock files (task_*.json.lock and task_*.handoff.lock without corresponding task_*.json).
    Uses non-blocking flock to ensure safety (§6.2).
    Returns count of orphan locks deleted (or detected in dry-run).
    """
    orphan_count = 0
    if not root.exists() or not root.is_dir():
        return 0

    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        name = entry.name
        task_id: str | None = None
        if name.startswith("task_") and name.endswith(".json.lock"):
            task_id = name[:-len(".json.lock")]
        elif name.startswith("task_") and name.endswith(".handoff.lock"):
            task_id = name[:-len(".handoff.lock")]

        if not task_id:
            continue

        task_json = io.task_file_path(task_id)
        if task_json.exists():
            continue

        # Found orphan lock file
        if dry_run:
            orphan_count += 1
            continue

        try:
            fd = os.open(str(entry), os.O_RDWR)
        except OSError:
            continue

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            os.close(fd)
            continue

        try:
            entry.unlink(missing_ok=True)
            orphan_count += 1
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    return orphan_count


def inspect_cleanup_status(*, now: datetime | None = None) -> dict[str, Any]:
    """
    Completely read-only status inspection for doctor and diagnostics (docs/herdr-orchestrator-task-registry-cleanup.md §4.1).
    Does NOT create directories, acquire locks, or modify any files.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    root = tasks_dir()
    if not root.exists() or not root.is_dir():
        return {
            "registry_path": str(root),
            "shared_across_repositories": True,
            "retention_days": _RETENTION_DAYS,
            "total_tasks": 0,
            "eligible_count": 0,
            "eligible_by_repository": {},
            "orphan_lock_files_count": 0,
            "last_cleanup_at": None,
        }

    total_tasks = 0
    eligible_count = 0
    eligible_by_repository: dict[str, int] = {}
    cutoff = current - timedelta(days=_RETENTION_DAYS)

    task_files = sorted(root.glob("task_*.json"))
    for path in task_files:
        if path.name.endswith(".json.lock"):
            continue
        total_tasks += 1
        try:
            raw = path.read_text(encoding="utf-8")
            doc = json.loads(raw)
            if _task_eligible_for_cleanup(doc, cutoff):
                eligible_count += 1
                repo_id = _extract_repository_identity(doc)
                eligible_by_repository[repo_id] = eligible_by_repository.get(repo_id, 0) + 1
        except Exception:
            continue

    orphan_lock_files_count = _sweep_orphan_locks(root, dry_run=True)
    stamp_raw = io.read_cleanup_stamp(root)

    return {
        "registry_path": str(root),
        "shared_across_repositories": True,
        "retention_days": _RETENTION_DAYS,
        "total_tasks": total_tasks,
        "eligible_count": eligible_count,
        "eligible_by_repository": eligible_by_repository,
        "orphan_lock_files_count": orphan_lock_files_count,
        "last_cleanup_at": stamp_raw,
    }


def cleanup_tasks(
    *,
    now: datetime | None = None,
    nonblocking: bool = False,
    ignore_rate_limit: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Task Registry cleanup (docs/herdr-orchestrator-task-registry-cleanup.md §6.3, §6.8).
    Supports dry-run, non-blocking lock, and rate-limit bypass.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    cutoff = current - timedelta(days=_RETENTION_DAYS)
    root = tasks_dir()

    if dry_run:
        if not root.exists() or not root.is_dir():
            return {
                "deleted": [],
                "deleted_count": 0,
                "deleted_by_repository": {},
                "orphan_locks_deleted": 0,
                "last_cleanup_at": None,
                "skipped_lock": False,
                "skipped_rate_limit": False,
            }
        deleted_eligible: list[str] = []
        deleted_by_repo: dict[str, int] = {}
        for path in sorted(root.glob("task_*.json")):
            if path.name.endswith(".json.lock"):
                continue
            task_id = path.stem
            try:
                raw = path.read_text(encoding="utf-8")
                doc = json.loads(raw)
                if _task_eligible_for_cleanup(doc, cutoff):
                    deleted_eligible.append(task_id)
                    repo_id = _extract_repository_identity(doc)
                    deleted_by_repo[repo_id] = deleted_by_repo.get(repo_id, 0) + 1
            except Exception:
                continue

        orphan_count = _sweep_orphan_locks(root, dry_run=True)
        stamp_raw = io.read_cleanup_stamp(root)
        return {
            "deleted": sorted(deleted_eligible),
            "deleted_count": len(deleted_eligible),
            "deleted_by_repository": deleted_by_repo,
            "orphan_locks_deleted": orphan_count,
            "last_cleanup_at": stamp_raw,
            "skipped_lock": False,
            "skipped_rate_limit": False,
        }

    # Real cleanup with lock
    with io.cleanup_run_lock(nonblocking=nonblocking) as acquired:
        stamp_raw = io.read_cleanup_stamp(root) if (root.exists() and root.is_dir()) else None
        if not acquired:
            return {
                "deleted": [],
                "deleted_count": 0,
                "deleted_by_repository": {},
                "orphan_locks_deleted": 0,
                "last_cleanup_at": stamp_raw,
                "skipped_lock": True,
                "skipped_rate_limit": False,
            }

        layout_root = io.ensure_registry_layout()
        stamp_raw = io.read_cleanup_stamp(layout_root)
        if not ignore_rate_limit and stamp_raw:
            try:
                last = datetime.fromisoformat(stamp_raw)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if current - last < timedelta(days=1):
                    return {
                        "deleted": [],
                        "deleted_count": 0,
                        "deleted_by_repository": {},
                        "orphan_locks_deleted": 0,
                        "last_cleanup_at": stamp_raw,
                        "skipped_lock": False,
                        "skipped_rate_limit": True,
                    }
            except ValueError:
                pass

        deleted: list[str] = []
        deleted_by_repo = {}
        for path in sorted(layout_root.glob("task_*.json")):
            if path.name.endswith(".json.lock"):
                continue
            task_id = path.stem
            try:
                repo_holder: list[str | None] = []
                was_deleted = _try_delete_task_if_eligible(task_id, cutoff, out_repo_id=repo_holder)
                if was_deleted:
                    deleted.append(task_id)
                    r_id = repo_holder[0] if repo_holder else "unknown"
                    deleted_by_repo[r_id] = deleted_by_repo.get(r_id, 0) + 1
            except RegistryError:
                continue

        orphan_deleted = _sweep_orphan_locks(layout_root, dry_run=False)
        new_stamp = current.replace(microsecond=0).isoformat()
        io.write_cleanup_stamp(layout_root, new_stamp)

        return {
            "deleted": sorted(deleted),
            "deleted_count": len(deleted),
            "deleted_by_repository": deleted_by_repo,
            "orphan_locks_deleted": orphan_deleted,
            "last_cleanup_at": new_stamp,
            "skipped_lock": False,
            "skipped_rate_limit": False,
        }
