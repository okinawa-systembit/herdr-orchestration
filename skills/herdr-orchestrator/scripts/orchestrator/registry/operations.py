from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestrator.paths import handoff_lock_path
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import io
from orchestrator.registry.paths_safe import require_result_artifact, validate_result_ref
from orchestrator.registry.schema import (
    SCHEMA_VERSION,
    initial_completion_status,
    validate_create_payload,
    validate_loaded_task,
)
from orchestrator.registry.task_id import generate_task_id

_RETENTION_DAYS = 30


def _is_initial_dispatch_failure_terminal(doc: dict[str, Any]) -> bool:
    return (
        doc["dispatch"]["status"] in ("failed", "skipped")
        and doc["task"]["status"] == "failed"
        and doc["result"]["status"] == "unavailable"
    )


def _task_eligible_for_cleanup(doc: dict[str, Any], cutoff: datetime) -> bool:
    task_status = doc["task"]["status"]
    completion_status = doc["completion"]["status"]
    if task_status not in ("succeeded", "failed", "cancelled"):
        return False
    if completion_status not in ("not_applicable", "sent", "failed", "skipped"):
        return False
    created = doc["timestamps"].get("created_at")
    if not isinstance(created, str):
        return False
    try:
        created_dt = datetime.fromisoformat(created)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return created_dt <= cutoff


def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    validate_create_payload(payload)
    task_id = generate_task_id()
    now = io.utc_now_iso()
    task_in = payload["task"]
    mode = task_in["mode"]
    completion_action = payload["completion"]["action"]
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
        "worker": copy.deepcopy(payload["worker"]),
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
    candidates: list[dict[str, Any]] = []
    root = io.ensure_registry_layout()
    for path in sorted(root.glob("task_*.json")):
        doc = io.read_task_document(path.stem)
        task = doc["task"]
        dispatch = doc["dispatch"]
        if task["status"] != "created" or dispatch["status"] != "pending":
            continue
        updated = _parse_registry_timestamp(doc["timestamps"]["updated_at"])
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
    from orchestrator.live_agent import LiveAgentError, verify_live_agent_context

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
    try:
        verify_live_agent_context(
            worker_name,
            expected_worktree_path=Path(doc["context"]["worktree"]["path"]),
            expected_repository_identity=doc["context"]["repository"]["identity"],
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
            if result_ref is None:
                raise RegistryError("invalid_input", message="result.ref required")
            require_result_artifact(worktree, result_ref)

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


def _try_delete_task_if_eligible(task_id: str, cutoff: datetime) -> bool:
    path = io.task_file_path(task_id)
    with io.task_mutation_lock(task_id):
        if not path.exists():
            return False
        doc = io.read_task_document_unlocked(path)
        if not _task_eligible_for_cleanup(doc, cutoff):
            return False
        doc = io.read_task_document_unlocked(path)
        if not _task_eligible_for_cleanup(doc, cutoff):
            return False
        path.unlink()
        handoff_lock_path(task_id).unlink(missing_ok=True)
    return True


def cleanup_tasks(*, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    with io.cleanup_run_lock() as acquired:
        if not acquired:
            return {"deleted": [], "skipped_rate_limit": True}
        root = io.ensure_registry_layout()
        stamp_raw = io.read_cleanup_stamp(root)
        if stamp_raw:
            try:
                last = datetime.fromisoformat(stamp_raw)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if current - last < timedelta(days=1):
                    return {"deleted": [], "skipped_rate_limit": True}
            except ValueError:
                pass

        deleted: list[str] = []
        cutoff = current - timedelta(days=_RETENTION_DAYS)
        for path in sorted(root.glob("task_*.json")):
            if ".json.lock" in path.name:
                continue
            task_id = path.stem
            try:
                if _try_delete_task_if_eligible(task_id, cutoff):
                    deleted.append(task_id)
            except RegistryError:
                continue

        io.write_cleanup_stamp(root, current.replace(microsecond=0).isoformat())
        return {"deleted": deleted, "skipped_rate_limit": False}
