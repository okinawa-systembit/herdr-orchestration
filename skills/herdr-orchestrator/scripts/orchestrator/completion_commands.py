from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from orchestrator.handoff_commands import build_task_envelope
from orchestrator.herdr_cli import HerdrCliError, run_herdr
from orchestrator.live_agent import LiveAgentError, verify_live_agent_context
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops

_PROMPTABLE_REQUESTER = frozenset({"idle", "done"})
_SKIP_REQUESTER_STATES = frozenset({"working", "blocked", "unknown"})


class CompletionCommandError(Exception):
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


def _registry_to_completion(exc: RegistryError) -> CompletionCommandError:
    return CompletionCommandError(
        exc.code,
        message=exc.message,
        retryable=exc.retryable,
        task_id=exc.task_id,
    )


def _herdr_error_code(exc: HerdrCliError) -> str:
    payload = exc.payload or {}
    herdr_err = payload.get("herdr") if isinstance(payload.get("herdr"), dict) else {}
    inner = herdr_err.get("error") if isinstance(herdr_err.get("error"), dict) else {}
    if isinstance(inner.get("code"), str):
        return inner["code"]
    return "herdr_prompt_failed"


def _completion_outcome_from_prompt_error(exc: HerdrCliError) -> tuple[str, str | None]:
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


def _require_live_pane_id(snap: Any, *, task_id: str) -> str:
    pane_id = snap.pane_id
    if not isinstance(pane_id, str) or not pane_id.strip():
        raise CompletionCommandError(
            "live_pane_id_missing",
            task_id=task_id,
            message="requester live agent has no pane_id",
        )
    return pane_id


def _finish_requester_completion(
    task_id: str,
    *,
    outcome: str,
    reason: str | None,
) -> dict[str, Any]:
    try:
        return registry_ops.finish_completion(task_id, outcome=outcome, reason=reason)
    except RegistryError as exc:
        raise _registry_to_completion(exc) from exc


def _execute_requester_completion(task_id: str) -> dict[str, Any]:
    doc = registry_ops.get_task(task_id)
    if doc["completion"]["status"] != "processing":
        raise CompletionCommandError(
            "registry_conflict",
            task_id=task_id,
            message="completion must be processing",
        )

    requester_name = doc["requester"]["agent_name"]
    worktree = Path(doc["context"]["worktree"]["path"])
    identity = doc["context"]["repository"]["identity"]

    def resolve_requester():
        return verify_live_agent_context(
            requester_name,
            expected_worktree_path=worktree,
            expected_repository_identity=identity,
            require_promptable=False,
        )

    try:
        requester = resolve_requester()
    except LiveAgentError as exc:
        _finish_requester_completion(
            task_id,
            outcome="skipped",
            reason="requester_unavailable",
        )
        doc = registry_ops.get_task(task_id)
        return _completion_payload(task_id, doc, prompt_sent=False)

    if requester.agent_status in _SKIP_REQUESTER_STATES:
        _finish_requester_completion(
            task_id,
            outcome="skipped",
            reason=f"requester_{requester.agent_status}",
        )
        doc = registry_ops.get_task(task_id)
        return _completion_payload(task_id, doc, prompt_sent=False)

    if requester.agent_status not in _PROMPTABLE_REQUESTER:
        _finish_requester_completion(
            task_id,
            outcome="skipped",
            reason="requester_not_promptable",
        )
        doc = registry_ops.get_task(task_id)
        return _completion_payload(task_id, doc, prompt_sent=False)

    try:
        requester = resolve_requester()
    except LiveAgentError:
        _finish_requester_completion(
            task_id,
            outcome="skipped",
            reason="requester_context_mismatch",
        )
        doc = registry_ops.get_task(task_id)
        return _completion_payload(task_id, doc, prompt_sent=False)

    _require_live_pane_id(requester, task_id=task_id)
    prompt_sent = False
    try:
        run_herdr(["agent", "prompt", requester.name, build_task_envelope(task_id)])
        prompt_sent = True
        _finish_requester_completion(task_id, outcome="sent", reason=None)
    except HerdrCliError as exc:
        outcome, reason = _completion_outcome_from_prompt_error(exc)
        if outcome == "skipped":
            outcome = "failed"
        _finish_requester_completion(task_id, outcome=outcome, reason=reason)

    doc = registry_ops.get_task(task_id)
    return _completion_payload(task_id, doc, prompt_sent=prompt_sent)


def _completion_payload(task_id: str, doc: dict[str, Any], *, prompt_sent: bool) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "prompt_sent": prompt_sent,
        "completion": copy.deepcopy(doc["completion"]),
        "task": {"status": doc["task"]["status"]},
        "result": {"status": doc["result"]["status"]},
    }


def run_resume_task(task_id: str) -> dict[str, Any]:
    try:
        registry_ops.claim_completion(task_id)
    except RegistryError as exc:
        raise _registry_to_completion(exc) from exc
    body = _execute_requester_completion(task_id)
    return {"command": "resume-task", **body}


def run_reset_completion(task_id: str) -> dict[str, Any]:
    try:
        doc = registry_ops.reset_completion(task_id)
    except RegistryError as exc:
        raise _registry_to_completion(exc) from exc
    return {
        "command": "reset-completion",
        "task_id": task_id,
        "completion": copy.deepcopy(doc["completion"]),
    }


def run_recover_completion(task_id: str) -> dict[str, Any]:
    doc = registry_ops.get_task(task_id)
    completion = doc["completion"]
    if completion["action"] != "resume_requester":
        raise CompletionCommandError("registry_conflict", task_id=task_id)

    reset_applied = False
    status = completion["status"]
    if status in ("processing", "failed", "uncertain", "skipped"):
        try:
            doc = registry_ops.reset_completion(task_id)
            reset_applied = True
        except RegistryError as exc:
            raise _registry_to_completion(exc) from exc
    elif status == "sent":
        raise CompletionCommandError(
            "registry_conflict",
            task_id=task_id,
            message="completion already sent",
        )
    elif status not in ("pending",):
        raise CompletionCommandError("registry_conflict", task_id=task_id)

    resumed = False
    resume_body: dict[str, Any] | None = None
    doc = registry_ops.get_task(task_id)
    try:
        registry_ops.assert_completion_claim_eligible(doc)
    except RegistryError:
        return {
            "command": "recover-completion",
            "task_id": task_id,
            "reset_applied": reset_applied,
            "resumed": False,
            "completion": copy.deepcopy(doc["completion"]),
            "guidance": ["§9.13 eligibility 未達のため claim せず停止（reset のみ成功の場合あり）"],
        }

    try:
        registry_ops.claim_completion(task_id)
    except RegistryError as exc:
        raise _registry_to_completion(exc) from exc
    resume_body = _execute_requester_completion(task_id)
    resumed = True
    return {
        "command": "recover-completion",
        "task_id": task_id,
        "reset_applied": reset_applied,
        "resumed": resumed,
        **resume_body,
    }


def best_effort_completion_after_result(task_id: str) -> dict[str, Any]:
    doc = registry_ops.get_task(task_id)
    if doc["completion"]["action"] != "resume_requester":
        return {"attempted": False, "reason": "completion_action_none"}
    try:
        registry_ops.assert_completion_claim_eligible(doc)
    except RegistryError as exc:
        return {"attempted": False, "reason": exc.code}

    try:
        registry_ops.claim_completion(task_id)
    except RegistryError as exc:
        return {"attempted": True, "claim_error": exc.code}

    try:
        body = _execute_requester_completion(task_id)
    except CompletionCommandError as exc:
        return {
            "attempted": True,
            "pipeline_error": exc.code,
            "message": exc.message,
        }
    return {"attempted": True, **body}
