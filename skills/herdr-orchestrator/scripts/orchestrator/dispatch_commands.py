from __future__ import annotations

import os
from typing import Any

from orchestrator.env import herdr_env_enabled, herdr_env_payload
from orchestrator.herdr_cli import HerdrCliError, run_herdr
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.task_id import validate_task_id

DEFAULT_STALE_DISPATCH_SEC = 300.0


def stale_dispatch_threshold_sec() -> float:
    raw = os.environ.get("HERDR_ORCHESTRATOR_STALE_DISPATCH_SEC")
    if raw is None or raw == "":
        return DEFAULT_STALE_DISPATCH_SEC
    return float(raw)


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
    threshold = stale_dispatch_threshold_sec()
    out: dict[str, Any] = {
        "command": "doctor",
        "stage": 5,
        **herdr_env_payload(),
        "stale_dispatch": {
            "threshold_sec": threshold,
            "candidates": registry_ops.find_stale_dispatch_candidates(min_age_sec=threshold),
        },
        "outdated_role_bindings": _find_outdated_role_bindings(),
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
