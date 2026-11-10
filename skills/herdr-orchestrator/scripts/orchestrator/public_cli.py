from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable

from orchestrator.cli_output import (
    emit_command_error,
    emit_completion_command_error,
    emit_handoff_command_error,
    emit_herdr_env_error,
    emit_json,
    emit_registry_error,
    emit_result_command_error,
)
from orchestrator.completion_commands import (
    CompletionCommandError,
    run_recover_completion,
    run_reset_completion,
    run_resume_task,
)
from orchestrator.dispatch_commands import run_doctor, run_reconcile_dispatch
from orchestrator.handoff_commands import HandoffCommandError, HandoffInput, run_handoff
from orchestrator.identity import (
    GLOBAL_SCOPE_ID,
    GitRepositoryNotInitializedError,
    RepositoryIdentityError,
    derive_directory_scope_id,
    get_or_create_repository_identity,
    get_repository_identity,
    resolve_git_common_dir,
)
from orchestrator.live_agent import (
    LiveAgentError,
    check_cwd_affinity,
    compute_role_peer_context,
    evaluate_role_target_live_status,
    format_pane_display_summary,
    inspect_pane_for_role_set,
    list_live_panes_with_ui,
    sync_herdr_pane_label,
    verify_live_agent_context,
)
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.paths import tasks_dir
from orchestrator.env import HerdrEnvError, require_herdr_env
from orchestrator.errors import emit_error
from orchestrator.registry.schema import STANDARD_ROLES, TASK_STATUSES
from orchestrator.registry.task_id import validate_task_id
from orchestrator.result_commands import ResultCommandError, result_submit, task_result, task_status
from orchestrator.list_commands import run_task_list
from orchestrator.role_bindings import (
    RoleBindingError,
    _utc_now_iso,
    clear_project,
    clear_role_binding,
    get_project_catalog,
    get_role_binding_document,
    get_role_bindings,
    register_project,
    set_role_binding,
    validate_display_name,
    validate_pane_id,
    validate_project_alias,
    validate_role_name,
)
from orchestrator.worktree import WorktreeError, resolve_worktree_root

PUBLIC_COMMANDS = (
    "doctor",
    "handoff",
    "result-submit",
    "task-status",
    "task-result",
    "task-list",
    "resume-task",
    "reset-completion",
    "recover-completion",
    "reconcile-dispatch",
    "identity",
    "role",
    "project",
    "cleanup-registry",
)

_COMMAND_HELP: dict[str, str] = {
    "doctor": "環境診断を実行（JSON を標準出力に出力。HERDR_ENV≠1 の場合は終了コード 1）",
    "handoff": "Worker エージェントへタスクを委託（同期または非同期）",
    "result-submit": "Worker がタスク結果を Registry に提出",
    "task-status": "Task Registry のスナップショットを取得",
    "task-result": "タスクの結果フィールドを取得",
    "task-list": "Task Registry のタスクを一覧取得（読み取り専用）",
    "resume-task": "結果確認後、Requester の completion を再開してプロンプト",
    "reset-completion": "再試行のために completion 状態をリセット",
    "recover-completion": "再開可能な場合に completion をリセットして自動再開",
    "reconcile-dispatch": "応答のない未確定 dispatch を安全に復旧",
    "identity": "リポジトリ識別子（Repository Identity UUID）を管理（init または show）",
    "role": "リポジトリごとの論理ロールと pane_id の紐づけを管理（set, show, clear）",
    "project": "プロジェクトカタログ（projects.json）を管理（register, show, clear）",
    "cleanup-registry": "Task Registry の古いタスクとロックファイルを整理（--dry-run 対応、HERDR_ENV 不要）",
}


def _cmd_doctor(_: argparse.Namespace) -> int:
    payload, exit_code = run_doctor()
    return emit_json(payload, exit_code=exit_code)


def _cmd_cleanup_registry(ns: argparse.Namespace) -> int:
    dry_run = getattr(ns, "dry_run", False)
    try:
        res = registry_ops.cleanup_tasks(
            nonblocking=False,
            ignore_rate_limit=True,
            dry_run=dry_run,
        )
    except RegistryError as exc:
        emit_registry_error(exc)

    out = {
        "command": "cleanup-registry",
        "scope": "global",
        "shared_across_repositories": True,
        "registry_path": str(tasks_dir()),
        "retention_days": 30,
        "dry_run": dry_run,
        "deleted": res.get("deleted", []),
        "deleted_count": res.get("deleted_count", len(res.get("deleted", []))),
        "deleted_by_repository": res.get("deleted_by_repository", {}),
        "orphan_locks_deleted": res.get("orphan_locks_deleted", 0),
        "last_cleanup_at": res.get("last_cleanup_at"),
    }
    return emit_json(out, exit_code=0)


def _cmd_reconcile_dispatch(ns: argparse.Namespace) -> int:
    try:
        require_herdr_env()
    except HerdrEnvError as exc:
        emit_herdr_env_error(exc)
    if not ns.task_id:
        emit_error("invalid_input", retryable=False, message="task.id required")
    try:
        validate_task_id(ns.task_id)
        payload, exit_code = run_reconcile_dispatch(
            ns.task_id,
            dispatch_outcome=ns.dispatch_outcome,
            dispatch_reason=ns.dispatch_reason,
        )
    except RegistryError as exc:
        emit_registry_error(exc)
    return emit_json(payload, exit_code=exit_code)


def _run_read_command(ns: argparse.Namespace, *, runner) -> int:
    try:
        require_herdr_env()
    except HerdrEnvError as exc:
        emit_herdr_env_error(exc)
    if not ns.task_id:
        emit_error("invalid_input", retryable=False, message="task.id required")
    try:
        validate_task_id(ns.task_id)
        payload = runner(ns.task_id)
    except RegistryError as exc:
        emit_registry_error(exc)
    except ResultCommandError as exc:
        emit_result_command_error(exc)
    return emit_json(payload)


def _cmd_task_status(ns: argparse.Namespace) -> int:
    return _run_read_command(ns, runner=task_status)


def _cmd_task_result(ns: argparse.Namespace) -> int:
    return _run_read_command(ns, runner=task_result)


def _limit_type(val: str) -> int:
    try:
        n = int(val)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {val!r}")
    if n < 1 or n > 1000:
        raise argparse.ArgumentTypeError(f"--limit must be between 1 and 1000 (got {n})")
    return n


def _cmd_task_list(ns: argparse.Namespace) -> int:
    try:
        require_herdr_env()
    except HerdrEnvError as exc:
        emit_herdr_env_error(exc)
    payload, exit_code = run_task_list(
        repository=ns.repository,
        worktree=ns.worktree,
        type=ns.type,
        statuses=ns.status,
        limit=ns.limit,
    )
    return emit_json(payload, exit_code=exit_code)


def _cmd_handoff(ns: argparse.Namespace) -> int:
    try:
        require_herdr_env()
    except HerdrEnvError as exc:
        emit_herdr_env_error(exc)
    raw_role = ns.worker_role or getattr(ns, "to_role", None)
    if not raw_role:
        emit_error("invalid_input", retryable=False, message="--worker-role or --to is required")
    if ns.worker_role and getattr(ns, "to_role", None):
        try:
            if validate_role_name(ns.worker_role) != validate_role_name(ns.to_role):
                emit_error(
                    "invalid_input",
                    retryable=False,
                    message="conflicting --worker-role and --to values",
                )
        except RoleBindingError as exc:
            emit_error(exc.code, retryable=False, message=exc.message)
    task_type = ns.task_type or getattr(ns, "type_alias", None) or "general"
    try:
        payload = run_handoff(
            HandoffInput(
                mode=ns.mode,
                worker_role=raw_role,
                task_type=task_type,
                instruction=ns.instruction,
                result_wait_timeout_sec=ns.result_wait_timeout_sec,
                scope=getattr(ns, "scope", None),
                project=getattr(ns, "project", None),
            )
        )
    except HandoffCommandError as exc:
        emit_handoff_command_error(exc)
    return emit_json(payload)


def _run_completion_command(ns: argparse.Namespace, *, runner) -> int:
    try:
        require_herdr_env()
    except HerdrEnvError as exc:
        emit_herdr_env_error(exc)
    if not ns.task_id:
        emit_error("invalid_input", retryable=False, message="task.id required")
    try:
        validate_task_id(ns.task_id)
        payload = runner(ns.task_id)
    except CompletionCommandError as exc:
        emit_completion_command_error(exc)
    return emit_json(payload)


def _cmd_resume_task(ns: argparse.Namespace) -> int:
    return _run_completion_command(ns, runner=run_resume_task)


def _cmd_reset_completion(ns: argparse.Namespace) -> int:
    return _run_completion_command(ns, runner=run_reset_completion)


def _cmd_recover_completion(ns: argparse.Namespace) -> int:
    return _run_completion_command(ns, runner=run_recover_completion)


def _cmd_result_submit(ns: argparse.Namespace) -> int:
    try:
        require_herdr_env()
    except HerdrEnvError as exc:
        emit_herdr_env_error(exc)
    if not ns.task_id:
        emit_error("invalid_input", retryable=False, message="task.id required")
    try:
        validate_task_id(ns.task_id)
        payload = result_submit(
            ns.task_id,
            result_status=ns.result_status,
            result_ref=ns.result_ref,
            summary=ns.summary,
        )
    except RegistryError as exc:
        emit_registry_error(exc)
    except ResultCommandError as exc:
        emit_result_command_error(exc)
    return emit_json(payload)


def _cmd_identity(ns: argparse.Namespace) -> int:
    action = getattr(ns, "identity_action", None)
    cwd = Path.cwd()
    if action == "init":
        try:
            repo_id = get_or_create_repository_identity(cwd)
        except GitRepositoryNotInitializedError as exc:
            emit_command_error(
                "git_repository_not_initialized",
                retryable=False,
                message=str(exc),
                exit_code=1,
            )
        except RepositoryIdentityError as exc:
            emit_command_error(
                "repository_identity_error",
                retryable=False,
                message=str(exc),
                exit_code=1,
            )
        return emit_json({
            "command": "identity",
            "action": "init",
            "repository_id": repo_id,
        })
    elif action == "show":
        try:
            repo_id = get_repository_identity(cwd)
        except GitRepositoryNotInitializedError as exc:
            emit_command_error(
                "git_repository_not_initialized",
                retryable=False,
                message=str(exc),
                exit_code=1,
            )
        except RepositoryIdentityError as exc:
            code = (
                "repository_identity_not_initialized"
                if "not initialized" in str(exc)
                else "repository_identity_error"
            )
            emit_command_error(
                code,
                retryable=False,
                message=str(exc),
                exit_code=1,
            )
        return emit_json({
            "command": "identity",
            "action": "show",
            "repository_id": repo_id,
        })
    emit_command_error(
        "invalid_input",
        retryable=False,
        message="identity action must be 'init' or 'show'",
        exit_code=1,
    )


def _resolve_role_scope_id(cwd: Path, *, is_global: bool, create_if_missing: bool) -> str:
    if is_global:
        return GLOBAL_SCOPE_ID
    try:
        wt = resolve_worktree_root(cwd)
        if create_if_missing:
            return get_or_create_repository_identity(wt)
        return get_repository_identity(wt)
    except WorktreeError:
        try:
            resolve_git_common_dir(cwd)
        except GitRepositoryNotInitializedError:
            return derive_directory_scope_id(cwd)
        except RepositoryIdentityError as exc:
            emit_command_error(
                "repository_identity_error",
                retryable=False,
                message=str(exc),
                exit_code=1,
            )
        emit_command_error(
            "repository_identity_error",
            retryable=False,
            message=f"failed to resolve worktree root for {cwd}",
            exit_code=1,
        )
    except GitRepositoryNotInitializedError:
        return derive_directory_scope_id(cwd)
    except RepositoryIdentityError as exc:
        code = (
            "repository_identity_not_initialized"
            if "not initialized" in str(exc)
            else "repository_identity_error"
        )
        emit_command_error(
            code,
            retryable=False,
            message=str(exc),
            exit_code=1,
        )


def _enrich_targets_with_live_status(
    targets: dict[str, dict[str, object]],
    *,
    roles: dict[str, str],
    chosen_scope_id: str,
    local_scope_id: str,
    local_doc: dict[str, Any],
    global_doc: dict[str, Any],
    panes: list[dict[str, Any]] | None,
) -> tuple[dict[str, dict[str, object]], list[tuple[str, dict[str, Any]]]]:
    enriched: dict[str, dict[str, object]] = {}
    evaluations: list[tuple[str, dict[str, Any]]] = []
    is_global = (chosen_scope_id == GLOBAL_SCOPE_ID)

    all_roles = sorted(set(roles.keys()) | set(targets.keys()))
    for role in all_roles:
        bound_pane_id = roles.get(role)
        meta = targets.get(role)
        if meta is None and bound_pane_id:
            meta = {"pane_id": bound_pane_id}

        if not bound_pane_id and isinstance(meta, dict):
            bound_pane_id = str(meta.get("pane_id") or "")

        expected_cwd: Path | None = None
        if isinstance(meta, dict) and meta.get("cwd"):
            try:
                expected_cwd = Path(str(meta["cwd"])).resolve()
            except Exception:
                expected_cwd = None

        peer_ctx = compute_role_peer_context(
            chosen_scope_id=chosen_scope_id,
            canonical_role=role,
            bound_pane_id=bound_pane_id or "",
            expected_cwd=expected_cwd,
            local_scope_id=local_scope_id,
            local_doc=local_doc,
            global_doc=global_doc,
        )

        live_eval = evaluate_role_target_live_status(
            bound_pane_id=bound_pane_id or "",
            target_meta=meta if isinstance(meta, dict) else None,
            canonical_role=role,
            is_global=is_global,
            panes=panes,
            peer_context=peer_ctx,
        )
        evaluations.append((role, live_eval))

        if isinstance(meta, dict):
            enriched[role] = {
                **meta,
                "display_summary": format_pane_display_summary(meta, fallback_role=role),
                "live_status": live_eval,
            }

    return enriched, evaluations


def _try_resolve_local_scope_id(dir_path: Path | str | None) -> str | None:
    """Safely resolve local scope_id (repository UUID or dir hash) without raising or exiting on errors."""
    if not dir_path:
        return None
    try:
        p = Path(dir_path).resolve()
    except Exception:
        return None

    # 1. Try Git worktree repository identity
    try:
        wt = resolve_worktree_root(p)
        try:
            return get_repository_identity(wt)
        except Exception:
            # Git repository present but identity uninitialized: no local role binding exists
            return None
    except WorktreeError:
        pass
    except Exception:
        pass

    # 2. Try Non-Git directory scope
    try:
        resolve_git_common_dir(p)
        # Inside a git repository: do not fall back to dir-scope binding
        return None
    except GitRepositoryNotInitializedError:
        return derive_directory_scope_id(p)
    except Exception:
        return None


def _sync_pane_label_after_unbind(
    pane_id: str,
    cwd: Path,
    *,
    target_cwd: str | Path | None = None,
) -> dict[str, object]:
    """Sync Herdr UI pane label after an unbind, preserving label if the pane still holds other roles."""
    surviving_display_name: str | None = None

    # Determine local directories to check: target_cwd first, then command cwd
    local_dirs_to_check: list[Path] = []
    if target_cwd:
        try:
            p_target = Path(target_cwd).resolve()
            local_dirs_to_check.append(p_target)
        except Exception:
            pass
    try:
        p_cwd = Path(cwd).resolve()
        if p_cwd not in local_dirs_to_check:
            local_dirs_to_check.append(p_cwd)
    except Exception:
        pass

    # 1. Check local scopes for surviving roles
    for d in local_dirs_to_check:
        local_scope = _try_resolve_local_scope_id(d)
        if local_scope:
            try:
                local_doc = get_role_binding_document(local_scope)
                for r_name, p_id in local_doc.get("roles", {}).items():
                    if p_id == pane_id:
                        t_meta = local_doc.get("targets", {}).get(r_name, {})
                        if isinstance(t_meta, dict):
                            surviving_display_name = str(t_meta.get("display_name") or r_name)
                        else:
                            surviving_display_name = str(r_name)
                        break
            except Exception:
                pass
        if surviving_display_name is not None:
            break

    # 2. Check global scope if not found in local scope
    if surviving_display_name is None:
        try:
            global_doc = get_role_binding_document(GLOBAL_SCOPE_ID)
            for r_name, p_id in global_doc.get("roles", {}).items():
                if p_id == pane_id:
                    t_meta = global_doc.get("targets", {}).get(r_name, {})
                    if isinstance(t_meta, dict):
                        surviving_display_name = str(t_meta.get("display_name") or r_name)
                    else:
                        surviving_display_name = str(r_name)
                    break
        except Exception:
            pass

    if surviving_display_name is not None:
        return sync_herdr_pane_label(pane_id, surviving_display_name)
    return sync_herdr_pane_label(pane_id, clear=True)


def _cmd_role(ns: argparse.Namespace) -> int:
    action = getattr(ns, "role_action", None)
    is_global = bool(getattr(ns, "is_global", False))
    cwd = Path.cwd()
    if action == "set":
        pos_pane = getattr(ns, "pane_id", None)
        opt_pane = getattr(ns, "pane", None)
        if pos_pane and opt_pane and pos_pane.strip() != opt_pane.strip():
            emit_command_error(
                "invalid_input",
                retryable=False,
                message="conflicting positional pane_id and --pane values",
                exit_code=1,
            )
        is_implicit_pane = not (opt_pane or pos_pane)
        pane_id = (opt_pane or pos_pane or os.environ.get("HERDR_PANE_ID") or "").strip()
        if not pane_id:
            emit_command_error(
                "invalid_input",
                retryable=False,
                message="<pane_id>, --pane, or HERDR_PANE_ID is required for 'role set'",
                exit_code=1,
            )
        try:
            canonical_role = validate_role_name(ns.role)
            valid_pane = validate_pane_id(pane_id)
            display_name = validate_display_name(
                getattr(ns, "label", None),
                default_from_role_arg=ns.role,
            )
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )

        try:
            require_herdr_env()
        except HerdrEnvError as exc:
            emit_herdr_env_error(exc)

        try:
            target_meta = inspect_pane_for_role_set(
                valid_pane,
                display_name=display_name,
                bound_at=_utc_now_iso(),
            )
        except LiveAgentError as exc:
            emit_command_error(
                "role_target_agent_not_running",
                retryable=False,
                message=str(exc),
                exit_code=1,
            )

        if is_implicit_pane:
            pane_target_cwd = target_meta.get("cwd") or ""
            if not check_cwd_affinity(pane_target_cwd, cwd):
                emit_command_error(
                    "env_pane_cwd_mismatch",
                    retryable=False,
                    message=(
                        f"環境変数 HERDR_PANE_ID から解決されたペイン {valid_pane!r} の作業ディレクトリ ({pane_target_cwd}) が、"
                        f"カレントディレクトリ ({cwd}) と一致しません。過去のセッションの環境変数が残留している可能性があります。"
                        f"'--pane <pane_id>' を明示して実行してください。"
                    ),
                    exit_code=1,
                )

        scope_id = _resolve_role_scope_id(cwd, is_global=is_global, create_if_missing=True)
        try:
            before_doc = get_role_binding_document(scope_id)
            before_roles = before_doc.get("roles", {})
            old_pane_for_role = before_roles.get(canonical_role)
            old_target_meta = before_doc.get("targets", {}).get(canonical_role, {})
            old_cwd = old_target_meta.get("cwd") if isinstance(old_target_meta, dict) else None
            roles = set_role_binding(
                scope_id,
                canonical_role,
                valid_pane,
                target_meta=target_meta,
            )
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )
        if old_pane_for_role and old_pane_for_role != valid_pane:
            _sync_pane_label_after_unbind(old_pane_for_role, cwd, target_cwd=old_cwd)

        effective_label = display_name
        if is_global:
            # 兼任ペインにおけるローカル UI ラベル優先保護（Issue #151）:
            # 同一ペインが既にローカルスコープで有効なロールを保持している場合、Herdr UI ラベルはローカルロールの display_name を優先維持する
            local_display_name: str | None = None
            local_dirs_to_check: list[Path] = []
            if target_meta.get("cwd"):
                try:
                    local_dirs_to_check.append(Path(target_meta["cwd"]).resolve())
                except Exception:
                    pass
            try:
                p_cwd = cwd.resolve()
                if p_cwd not in local_dirs_to_check:
                    local_dirs_to_check.append(p_cwd)
            except Exception:
                pass
            for d in local_dirs_to_check:
                local_scope = _try_resolve_local_scope_id(d)
                if local_scope:
                    try:
                        local_doc = get_role_binding_document(local_scope)
                        for r_name, p_id in local_doc.get("roles", {}).items():
                            if p_id == valid_pane:
                                t_meta = local_doc.get("targets", {}).get(r_name, {})
                                if isinstance(t_meta, dict):
                                    local_display_name = str(t_meta.get("display_name") or r_name)
                                else:
                                    local_display_name = str(r_name)
                                break
                    except Exception:
                        pass
                if local_display_name is not None:
                    break
            if local_display_name is not None:
                effective_label = local_display_name

        label_sync = sync_herdr_pane_label(valid_pane, effective_label)
        target_with_summary = {
            **target_meta,
            "display_summary": format_pane_display_summary(target_meta, fallback_role=canonical_role),
            "label_synced": label_sync.get("synced", False),
        }
        out_payload: dict[str, object] = {
            "command": "role",
            "action": "set",
            "scope": "global" if is_global else "local",
            "repository_id": scope_id,
            "role": canonical_role,
            "display_name": display_name,
            "pane_id": roles[canonical_role],
            "target": target_with_summary,
            "label_sync": label_sync,
            "roles": roles,
        }
        return emit_json(out_payload)
    elif action == "show":
        scope_id = _resolve_role_scope_id(cwd, is_global=is_global, create_if_missing=False)
        try:
            doc = get_role_binding_document(scope_id)
            global_doc = get_role_binding_document(GLOBAL_SCOPE_ID) if not is_global else doc
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )

        panes = list_live_panes_with_ui()
        local_scope_id = GLOBAL_SCOPE_ID if is_global else scope_id

        enriched_targets, local_evals = _enrich_targets_with_live_status(
            doc["targets"],
            roles=doc["roles"],
            chosen_scope_id=scope_id,
            local_scope_id=local_scope_id,
            local_doc=doc if not is_global else {},
            global_doc=global_doc if not is_global else doc,
            panes=panes,
        )

        show_payload: dict[str, object] = {
            "command": "role",
            "action": "show",
            "scope": "global" if is_global else "local",
            "repository_id": scope_id,
            "roles": doc["roles"],
            "targets": enriched_targets,
            "outdated_roles": doc["outdated_roles"],
        }

        all_evals_for_warning = list(local_evals)

        if not is_global:
            enriched_global_targets, global_evals = _enrich_targets_with_live_status(
                global_doc["targets"],
                roles=global_doc["roles"],
                chosen_scope_id=GLOBAL_SCOPE_ID,
                local_scope_id=local_scope_id,
                local_doc=doc,
                global_doc=global_doc,
                panes=panes,
            )
            show_payload["global_roles"] = global_doc["roles"]
            show_payload["global_targets"] = enriched_global_targets
            show_payload["global_outdated_roles"] = global_doc["outdated_roles"]
            all_evals_for_warning.extend(global_evals)

        warn_items: list[str] = []
        for role, live_eval in all_evals_for_warning:
            status = live_eval.get("status")
            reason = live_eval.get("reason")
            if status != "live" or reason == "agent_not_running":
                label = "agent_not_running" if reason == "agent_not_running" else status
                warn_items.append(f"{role}: {label}")

        if warn_items:
            sys.stderr.write(
                f"[WARN] role show: non-live or inactive role bindings detected ({', '.join(warn_items)}). "
                "See live_status.hint in JSON output.\n"
            )
            sys.stderr.flush()

        return emit_json(show_payload)
    elif action == "clear":
        scope_id = _resolve_role_scope_id(cwd, is_global=is_global, create_if_missing=False)
        try:
            before_doc = get_role_binding_document(scope_id)
            before_roles = before_doc.get("roles", {})
            before_targets = before_doc.get("targets", {})
            roles = clear_role_binding(
                scope_id,
                role=getattr(ns, "role", None),
                clear_all=bool(getattr(ns, "all", False)),
            )
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )
        cleared_panes = set(before_roles.values()) - set(roles.values())
        label_sync_results: dict[str, object] = {}
        for c_pane in sorted(cleared_panes):
            c_cwd = None
            for r_name, p_id in before_roles.items():
                if p_id == c_pane:
                    t_meta = before_targets.get(r_name, {})
                    if isinstance(t_meta, dict) and t_meta.get("cwd"):
                        c_cwd = str(t_meta["cwd"])
                        break
            label_sync_results[c_pane] = _sync_pane_label_after_unbind(c_pane, cwd, target_cwd=c_cwd)
        return emit_json({
            "command": "role",
            "action": "clear",
            "scope": "global" if is_global else "local",
            "repository_id": scope_id,
            "roles": roles,
            "label_sync": label_sync_results,
        })
    emit_command_error(
        "invalid_input",
        retryable=False,
        message="role action must be 'set', 'show', or 'clear'",
        exit_code=1,
    )


def _cmd_project(ns: argparse.Namespace) -> int:
    action = getattr(ns, "project_action", None)
    cwd = Path.cwd()

    if action == "register":
        alias = getattr(ns, "alias", None)
        if not alias:
            emit_command_error(
                "invalid_input",
                retryable=False,
                message="project alias required",
                exit_code=1,
            )
        scope_id = _resolve_role_scope_id(cwd, is_global=False, create_if_missing=True)
        try:
            reg_info = register_project(alias, scope_id)
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )
        return emit_json({
            "command": "project",
            "action": "register",
            "alias": reg_info["alias"],
            "scope_id": reg_info["scope_id"],
            "registered_at": reg_info["registered_at"],
            "updated_at": reg_info["updated_at"],
        })

    elif action == "show":
        try:
            catalog = get_project_catalog()
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )
        panes = list_live_panes_with_ui()
        projects_payload: dict[str, object] = {}
        for alias, p_info in sorted(catalog.get("projects", {}).items()):
            p_scope_id = p_info["scope_id"]
            reg_at = p_info.get("registered_at")
            try:
                p_doc = get_role_binding_document(p_scope_id)
            except RoleBindingError as exc:
                sys.stderr.write(f"warning: cannot read role binding for project {alias!r} ({p_scope_id}): {exc.message}\n")
                p_doc = {"roles": {}, "targets": {}, "outdated_roles": []}

            enriched_targets, _ = _enrich_targets_with_live_status(
                p_doc.get("targets", {}),
                roles=p_doc.get("roles", {}),
                chosen_scope_id=p_scope_id,
                local_scope_id=p_scope_id,
                local_doc=p_doc,
                global_doc={},
                panes=panes,
            )
            projects_payload[alias] = {
                "scope_id": p_scope_id,
                "registered_at": reg_at,
                "roles": p_doc.get("roles", {}),
                "targets": enriched_targets,
                "outdated_roles": p_doc.get("outdated_roles", []),
            }

        return emit_json({
            "command": "project",
            "action": "show",
            "schema_version": catalog.get("schema_version", 1),
            "updated_at": catalog.get("updated_at"),
            "projects": projects_payload,
        })

    elif action == "clear":
        alias = getattr(ns, "alias", None)
        clear_all = bool(getattr(ns, "clear_all", False))
        try:
            res = clear_project(alias, clear_all=clear_all)
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )
        out_payload: dict[str, object] = {
            "command": "project",
            "action": "clear",
            **res,
        }
        return emit_json(out_payload)

    emit_command_error(
        "invalid_input",
        retryable=False,
        message="project action must be 'register', 'show', or 'clear'",
        exit_code=1,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herdr-orchestrator",
        description="Herdr Orchestrator 公開 CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in PUBLIC_COMMANDS:
        p = sub.add_parser(
            name,
            help=_COMMAND_HELP.get(name, name),
            description=_COMMAND_HELP.get(name),
        )
        if name == "doctor":
            continue
        elif name == "reconcile-dispatch":
            p.add_argument("task_id", metavar="task.id", help="対象タスクID")
            p.add_argument(
                "--dispatch-outcome",
                choices=("uncertain", "failed"),
                help="dispatch 確定結果のステータス",
            )
            p.add_argument(
                "--dispatch-reason",
                help="dispatch 結果の理由または説明",
            )
        elif name == "handoff":
            p.add_argument(
                "--mode",
                default="sync",
                choices=("sync", "async"),
                help="委託方式（sync: 完了待機, async: 即時返還。既定値: sync）",
            )
            p.add_argument(
                "--worker-role",
                default=None,
                help="委託先 Worker の論理ロール（標準ロール・日本語ロール名・カスタムロール名）",
            )
            p.add_argument(
                "--to",
                dest="to_role",
                default=None,
                help="--worker-role の短縮エイリアス（例: --to reviewer, --to チケット管理）",
            )
            p.add_argument(
                "--scope",
                choices=("local", "global"),
                default=None,
                help="同名ロール重複時に参照するスコープを明示指定（local または global）",
            )
            p.add_argument(
                "--project",
                default=None,
                help="プロジェクトカタログに登録された別プロジェクトのローカルロールへ委託（例: --project my-app）",
            )
            p.add_argument(
                "--task-type",
                default=None,
                help="タスク種別（例: review, implementation, research。省略時: general）",
            )
            p.add_argument(
                "--type",
                dest="type_alias",
                default=None,
                help="--task-type の短縮エイリアス",
            )
            p.add_argument(
                "--instruction",
                required=True,
                help="Worker への指示内容",
            )
            p.add_argument(
                "--result-wait-timeout-sec",
                type=int,
                default=30 * 60,
                help="sync モード時の結果待機タイムアウト秒（デフォルト: 1800秒 / 30分）",
            )
        elif name == "result-submit":
            p.add_argument("task_id", nargs="?", metavar="task.id", help="対象タスクID")
            p.add_argument(
                "--result-status",
                required=True,
                choices=("succeeded", "failed", "unavailable"),
                help="タスク結果ステータス",
            )
            p.add_argument(
                "--result-ref",
                default=None,
                help="成果物への参照パスまたは URI",
            )
            p.add_argument(
                "--summary",
                default=None,
                help="タスク結果の要約メッセージ",
            )
        elif name == "task-list":
            p.add_argument(
                "--repository",
                default=None,
                help="対象リポジトリ identity UUID で絞り込み",
            )
            p.add_argument(
                "--worktree",
                default=None,
                help="対象 worktree 絶対パスで絞り込み",
            )
            p.add_argument(
                "--type",
                default=None,
                help="タスク種別で絞り込み",
            )
            p.add_argument(
                "--status",
                action="append",
                choices=sorted(TASK_STATUSES),
                default=None,
                help="タスクステータスで絞り込み（複数指定時は OR）",
            )
            p.add_argument(
                "--limit",
                type=_limit_type,
                default=100,
                help="返却するタスクの最大件数（1〜1000、既定値: 100）",
            )
            p.add_argument(
                "--json",
                action="store_true",
                help="JSON 出力を明示（指定の有無に関わらず常に JSON 出力）",
            )
        elif name == "cleanup-registry":
            p.add_argument(
                "--dry-run",
                action="store_true",
                default=False,
                help="ファイルの削除および last_cleanup_at の更新を行わず対象タスク一覧を表示",
            )
            p.add_argument(
                "--json",
                action="store_true",
                default=True,
                help="JSON 形式で出力（互換性フラグ）",
            )
        elif name == "identity":
            id_sub = p.add_subparsers(dest="identity_action", required=True)
            id_sub.add_parser(
                "init",
                help="リポジトリ固有の UUID を初期化または取得",
                description="リポジトリ固有の UUID を初期化または取得",
            )
            id_sub.add_parser(
                "show",
                help="登録済みのリポジトリ UUID を表示",
                description="登録済みのリポジトリ UUID を表示",
            )
        elif name == "role":
            role_sub = p.add_subparsers(dest="role_action", required=True)
            role_set = role_sub.add_parser(
                "set",
                help="論理ロールと pane_id を紐づけて保存（UIラベルも自動同期）",
                description="論理ロールと pane_id を紐づけて保存（UIラベルも自動同期）",
            )
            role_set.add_argument(
                "role",
                help="紐づけるロール（標準ロール・日本語・カスタムロール名）",
            )
            role_set.add_argument(
                "pane_id",
                nargs="?",
                default=None,
                help="対象の pane_id（省略時は --pane または HERDR_PANE_ID 環境変数を使用）",
            )
            role_set.add_argument(
                "--pane",
                default=None,
                help="対象の pane_id（省略時は位置引数または HERDR_PANE_ID 環境変数を使用）",
            )
            role_set.add_argument(
                "--label",
                default=None,
                help="Herdr UI および人間向け報告に表示するラベル名（省略時はロール引数を使用）",
            )
            role_set.add_argument(
                "--global",
                dest="is_global",
                action="store_true",
                help="全ワークスペース・全プロジェクト共通のグローバルロールとして登録",
            )
            role_show = role_sub.add_parser(
                "show",
                help="ロールと pane_id の紐づけ一覧を表示",
                description="ロールと pane_id の紐づけ一覧を表示",
            )
            role_show.add_argument(
                "--global",
                dest="is_global",
                action="store_true",
                help="グローバルロールのみを表示",
            )
            role_clear = role_sub.add_parser(
                "clear",
                help="登録済みのロールと pane_id の紐づけを解除",
                description="登録済みのロールと pane_id の紐づけを解除",
            )
            role_clear.add_argument(
                "role",
                nargs="?",
                default=None,
                help="解除するロール（--all 指定時は不要）",
            )
            role_clear.add_argument(
                "--all",
                action="store_true",
                help="すべてのロール紐づけを一括解除",
            )
            role_clear.add_argument(
                "--global",
                dest="is_global",
                action="store_true",
                help="グローバルロールの紐づけを解除",
            )
        elif name == "project":
            proj_sub = p.add_subparsers(dest="project_action", required=True)
            proj_reg = proj_sub.add_parser(
                "register",
                help="カレントディレクトリのプロジェクトをエイリアスでカタログに登録",
                description="カレントディレクトリのプロジェクトをエイリアスでカタログに登録",
            )
            proj_reg.add_argument(
                "alias",
                help="プロジェクトエイリアス（1〜32文字）",
            )
            proj_sub.add_parser(
                "show",
                help="プロジェクトカタログの一覧と各プロジェクトのロール・稼働状況を表示",
                description="プロジェクトカタログの一覧と各プロジェクトのロール・稼働状況を表示",
            )
            proj_clear = proj_sub.add_parser(
                "clear",
                help="プロジェクトカタログから登録を解除",
                description="プロジェクトカタログから登録を解除",
            )
            proj_clear.add_argument(
                "alias",
                nargs="?",
                default=None,
                help="登録解除するプロジェクトエイリアス（--all 指定時は不要）",
            )
            proj_clear.add_argument(
                "--all",
                dest="clear_all",
                action="store_true",
                help="すべてのプロジェクト登録を一括解除",
            )
        else:
            p.add_argument("task_id", nargs="?", metavar="task.id", help="対象タスクID")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "doctor": _cmd_doctor,
        "handoff": _cmd_handoff,
        "task-status": _cmd_task_status,
        "task-result": _cmd_task_result,
        "task-list": _cmd_task_list,
        "result-submit": _cmd_result_submit,
        "reconcile-dispatch": _cmd_reconcile_dispatch,
        "resume-task": _cmd_resume_task,
        "reset-completion": _cmd_reset_completion,
        "recover-completion": _cmd_recover_completion,
        "identity": _cmd_identity,
        "role": _cmd_role,
        "project": _cmd_project,
        "cleanup-registry": _cmd_cleanup_registry,
    }
    handler = handlers[ns.command]
    return handler(ns)


if __name__ == "__main__":
    raise SystemExit(main())
