from __future__ import annotations

import math
import os
import sys
from typing import Any

from orchestrator.env import herdr_env_enabled, herdr_env_payload
from orchestrator.herdr_cli import HerdrCliError, run_herdr
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.task_id import validate_task_id

DEFAULT_STALE_DISPATCH_SEC = 300.0
DEFAULT_STALE_IN_PROGRESS_SEC = 86400.0


def stale_dispatch_threshold_sec() -> float:
    raw = os.environ.get("HERDR_ORCHESTRATOR_STALE_DISPATCH_SEC")
    if raw is None or raw == "":
        return DEFAULT_STALE_DISPATCH_SEC
    return float(raw)


def stale_in_progress_threshold_sec() -> float:
    raw = os.environ.get("HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC")
    if raw is None or raw == "":
        return DEFAULT_STALE_IN_PROGRESS_SEC
    try:
        val = float(raw)
    except ValueError:
        sys.stderr.write(
            f"warning: invalid HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC={raw!r}; "
            f"using default {DEFAULT_STALE_IN_PROGRESS_SEC}\n"
        )
        return DEFAULT_STALE_IN_PROGRESS_SEC
    if math.isnan(val) or math.isinf(val) or val < 0:
        sys.stderr.write(
            f"warning: out-of-range HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC={raw!r}; "
            f"using default {DEFAULT_STALE_IN_PROGRESS_SEC}\n"
        )
        return DEFAULT_STALE_IN_PROGRESS_SEC
    return val


def _find_outdated_role_bindings() -> list[dict[str, Any]]:
    from orchestrator.paths import roles_dir
    from orchestrator.role_bindings import RoleBindingError, get_role_binding_document

    rdir = roles_dir()
    if not rdir.exists() or not rdir.is_dir():
        return []
    outdated_list: list[dict[str, Any]] = []
    for path in sorted(rdir.glob("*.json")):
        if path.name.endswith(".json.lock"):
            continue
        scope_id = path.stem
        try:
            doc = get_role_binding_document(scope_id)
        except RoleBindingError:
            continue
        if doc.get("outdated_roles"):
            outdated_list.append(
                {
                    "scope_id": scope_id,
                    "outdated_roles": doc["outdated_roles"],
                }
            )
    return outdated_list


def run_doctor() -> tuple[dict[str, Any], int]:
    from orchestrator.live_agent import list_live_panes_with_ui

    threshold = stale_dispatch_threshold_sec()
    in_progress_threshold = stale_in_progress_threshold_sec()
    live_panes = list_live_panes_with_ui()
    in_progress_candidates = registry_ops.find_stale_in_progress_candidates(
        min_age_sec=in_progress_threshold,
        live_panes=live_panes,
    )
    strong_count = sum(
        1 for c in in_progress_candidates if c.get("signal_strength") == "strong"
    )
    weak_count = sum(
        1 for c in in_progress_candidates if c.get("signal_strength") == "weak"
    )

    try:
        cleanup_status = registry_ops.inspect_cleanup_status()
    except Exception as exc:
        cleanup_status = {"error": str(exc)}

    out: dict[str, Any] = {
        "command": "doctor",
        "stage": 5,
        **herdr_env_payload(),
        "stale_dispatch": {
            "threshold_sec": threshold,
            "candidates": registry_ops.find_stale_dispatch_candidates(min_age_sec=threshold),
        },
        "stale_in_progress": {
            "action": "await_human",
            "threshold_sec": in_progress_threshold,
            "pane_check_available": live_panes is not None,
            "summary": {
                "total_candidates": len(in_progress_candidates),
                "strong_signal_count": strong_count,
                "weak_signal_count": weak_count,
            },
            "candidates": in_progress_candidates,
            "guidance": [
                "strong_signal のタスクは Worker ペインまたは worktree が失われているため、事実確認の上で `herdr-orchestrator cancel-task <task_id> --reason \"<理由>\"` による終端化、または同一ペイン生存時は `result-submit --result-status unavailable` を検討してください（Issue #173）。",
                "weak_signal のみのタスクは正常な長時間実行の可能性があるため、自動キャンセルせず `herdr-orchestrator task-status <task_id>` や対象ペインの状態を確認してください。",
            ],
        },
        "outdated_role_bindings": _find_outdated_role_bindings(),
        "registry_cleanup": cleanup_status,
    }
    exit_code = 0
    if herdr_env_enabled():
        try:
            out["herdr_version"] = run_herdr(["--version"])
        except HerdrCliError as exc:
            out["herdr_version_error"] = str(exc)
            out.update(exc.payload)
    else:
        out["herdr_cli_skipped"] = True
        out["herdr_cli_skip_reason"] = "HERDR_ENV=1 required to invoke Herdr CLI"
        exit_code = 1
    return out, exit_code


def _reconcile_guidance(info: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    if info.get("action") == "await_human":
        steps.append(
            "Herdr UI / worker で task.id の受理痕跡を確認し、"
            "`reconcile-dispatch --dispatch-outcome uncertain|failed` または新 task.id で handoff"
        )
    if info.get("needs_result_reregistration"):
        steps.append(
            "dispatch terminal 後、worker から `result-submit` で result を登録する（§10.2）"
        )
    return steps


def run_reconcile_dispatch(
    task_id: str,
    *,
    dispatch_outcome: str | None,
    dispatch_reason: str | None,
) -> tuple[dict[str, Any], int]:
    validate_task_id(task_id)
    info = registry_ops.reconcile_dispatch(
        task_id,
        dispatch_outcome=dispatch_outcome,
        dispatch_reason=dispatch_reason,
    )
    payload: dict[str, Any] = {"command": "reconcile-dispatch", **info}
    guidance = _reconcile_guidance(info)
    if guidance:
        payload["guidance"] = guidance
    if info.get("updated"):
        return payload, 0
    if info.get("action") == "await_human":
        return payload, 1
    return payload, 0
