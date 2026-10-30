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
    format_pane_display_summary,
    inspect_pane_for_role_set,
    sync_herdr_pane_label,
    verify_live_agent_context,
)
from orchestrator.registry.errors import RegistryError
from orchestrator.env import HerdrEnvError, require_herdr_env
from orchestrator.errors import emit_error
from orchestrator.registry.schema import STANDARD_ROLES, TASK_STATUSES
from orchestrator.registry.task_id import validate_task_id
from orchestrator.result_commands import ResultCommandError, result_submit, task_result, task_status
from orchestrator.list_commands import run_task_list
from orchestrator.role_bindings import (
    RoleBindingError,
    _utc_now_iso,
    clear_role_binding,
    get_role_binding_document,
    get_role_bindings,
    set_role_binding,
    validate_display_name,
    validate_pane_id,
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
}


def _cmd_doctor(_: argparse.Namespace) -> int:
    payload, exit_code = run_doctor()
    return emit_json(payload, exit_code=exit_code)


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


def _enrich_targets_with_summary(targets: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    enriched: dict[str, dict[str, object]] = {}
    for role, meta in targets.items():
        if isinstance(meta, dict):
            enriched[role] = {
                **meta,
                "display_summary": format_pane_display_summary(meta, fallback_role=role),
            }
    return enriched


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

        scope_id = _resolve_role_scope_id(cwd, is_global=is_global, create_if_missing=True)
        try:
            before_roles = get_role_bindings(scope_id)
            old_pane_for_role = before_roles.get(canonical_role)
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
            sync_herdr_pane_label(old_pane_for_role, clear=True)
        sync_herdr_pane_label(valid_pane, display_name)
        target_with_summary = {
            **target_meta,
            "display_summary": format_pane_display_summary(target_meta, fallback_role=canonical_role),
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
        show_payload: dict[str, object] = {
            "command": "role",
            "action": "show",
            "scope": "global" if is_global else "local",
            "repository_id": scope_id,
            "roles": doc["roles"],
            "targets": _enrich_targets_with_summary(doc["targets"]),
            "outdated_roles": doc["outdated_roles"],
        }
        if not is_global:
            show_payload["global_roles"] = global_doc["roles"]
            show_payload["global_targets"] = _enrich_targets_with_summary(global_doc["targets"])
            show_payload["global_outdated_roles"] = global_doc["outdated_roles"]
        return emit_json(show_payload)
    elif action == "clear":
        scope_id = _resolve_role_scope_id(cwd, is_global=is_global, create_if_missing=False)
        try:
            before_roles = get_role_bindings(scope_id)
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
        for c_pane in sorted(cleared_panes):
            sync_herdr_pane_label(c_pane, clear=True)
        return emit_json({
            "command": "role",
            "action": "clear",
            "scope": "global" if is_global else "local",
            "repository_id": scope_id,
            "roles": roles,
        })
    emit_command_error(
        "invalid_input",
        retryable=False,
        message="role action must be 'set', 'show', or 'clear'",
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
    }
    handler = handlers[ns.command]
    return handler(ns)


if __name__ == "__main__":
    raise SystemExit(main())
