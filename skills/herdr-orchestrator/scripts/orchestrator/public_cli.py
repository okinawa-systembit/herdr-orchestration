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
    GitRepositoryNotInitializedError,
    RepositoryIdentityError,
    get_or_create_repository_identity,
    get_repository_identity,
)
from orchestrator.live_agent import LiveAgentError, verify_live_agent_context
from orchestrator.registry.errors import RegistryError
from orchestrator.env import HerdrEnvError, require_herdr_env
from orchestrator.errors import emit_error
from orchestrator.registry.schema import STANDARD_ROLES, TASK_STATUSES
from orchestrator.registry.task_id import validate_task_id
from orchestrator.result_commands import ResultCommandError, result_submit, task_result, task_status
from orchestrator.list_commands import run_task_list
from orchestrator.role_bindings import (
    RoleBindingError,
    clear_role_binding,
    get_role_bindings,
    set_role_binding,
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
    try:
        payload = run_handoff(
            HandoffInput(
                mode=ns.mode,
                worker_role=ns.worker_role,
                task_type=ns.task_type,
                instruction=ns.instruction,
                result_wait_timeout_sec=ns.result_wait_timeout_sec,
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


def _cmd_role(ns: argparse.Namespace) -> int:
    action = getattr(ns, "role_action", None)
    cwd = Path.cwd()
    if action == "set":
        pane_id = (ns.pane or os.environ.get("HERDR_PANE_ID") or "").strip()
        if not pane_id:
            emit_command_error(
                "invalid_input",
                retryable=False,
                message="--pane or HERDR_PANE_ID is required for 'role set'",
                exit_code=1,
            )
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
        try:
            roles = set_role_binding(repo_id, ns.role, pane_id)
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )
        out_payload: dict[str, object] = {
            "command": "role",
            "action": "set",
            "repository_id": repo_id,
            "role": ns.role,
            "pane_id": roles[ns.role],
            "roles": roles,
        }
        if os.environ.get("HERDR_ENV") == "1":
            try:
                wt_root = resolve_worktree_root(cwd)
                verify_live_agent_context(
                    roles[ns.role],
                    expected_worktree_path=wt_root,
                    expected_repository_identity=repo_id,
                    require_promptable=False,
                )
            except (WorktreeError, LiveAgentError) as exc:
                out_payload["warning"] = (
                    f"bound pane {roles[ns.role]!r} could not be verified in current worktree: {exc}"
                )
        return emit_json(out_payload)
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
        try:
            roles = get_role_bindings(repo_id)
        except RoleBindingError as exc:
            emit_command_error(
                exc.code,
                retryable=False,
                message=exc.message,
                exit_code=1,
            )
        return emit_json({
            "command": "role",
            "action": "show",
            "repository_id": repo_id,
            "roles": roles,
        })
    elif action == "clear":
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
        try:
            roles = clear_role_binding(
                repo_id,
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
        return emit_json({
            "command": "role",
            "action": "clear",
            "repository_id": repo_id,
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
                required=True,
                choices=("sync", "async"),
                help="委託方式（sync: 完了待機, async: 即時返還）",
            )
            p.add_argument(
                "--worker-role",
                required=True,
                choices=("design", "reviewer", "implementer", "orchestrator"),
                help="委託先 Worker の論理ロール",
            )
            p.add_argument(
                "--task-type",
                required=True,
                help="タスク種別（例: review, implementation）",
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
                help="論理ロールと pane_id を紐づけて保存",
                description="論理ロールと pane_id を紐づけて保存",
            )
            role_set.add_argument(
                "role",
                choices=sorted(STANDARD_ROLES),
                help="紐づける論理ロール（design, reviewer, implementer, orchestrator）",
            )
            role_set.add_argument(
                "--pane",
                default=None,
                help="対象の pane_id（省略時は HERDR_PANE_ID 環境変数を使用）",
            )
            role_sub.add_parser(
                "show",
                help="現在のリポジトリのロールと pane_id の紐づけ一覧を表示",
                description="現在のリポジトリのロールと pane_id の紐づけ一覧を表示",
            )
            role_clear = role_sub.add_parser(
                "clear",
                help="登録済みのロールと pane_id の紐づけを解除",
                description="登録済みのロールと pane_id の紐づけを解除",
            )
            role_clear.add_argument(
                "role",
                nargs="?",
                choices=sorted(STANDARD_ROLES),
                default=None,
                help="解除する論理ロール（--all 指定時は不要）",
            )
            role_clear.add_argument(
                "--all",
                action="store_true",
                help="すべてのロール紐づけを一括解除",
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
