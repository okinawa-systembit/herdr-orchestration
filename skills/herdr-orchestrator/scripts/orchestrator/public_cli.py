from __future__ import annotations

import argparse
import sys
from typing import Callable

from orchestrator.cli_output import (
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
from orchestrator.registry.errors import RegistryError
from orchestrator.env import HerdrEnvError, require_herdr_env
from orchestrator.errors import emit_error
from orchestrator.registry.task_id import validate_task_id
from orchestrator.result_commands import ResultCommandError, result_submit, task_result, task_status

PUBLIC_COMMANDS = (
    "handoff",
    "result-submit",
    "task-status",
    "task-result",
    "resume-task",
    "reset-completion",
    "recover-completion",
    "doctor",
    "reconcile-dispatch",
)

_COMMAND_HELP: dict[str, str] = {
    "handoff": "Dispatch task to worker (sync or async)",
    "result-submit": "Worker submits result to Registry",
    "task-status": "Read task Registry snapshot",
    "task-result": "Read task result fields",
    "resume-task": "Claim and prompt requester after result (§9.3)",
    "reset-completion": "Reset completion for retry (§9.14)",
    "recover-completion": "Reset then resume completion when eligible",
    "reconcile-dispatch": "Recover stale dispatch (§6.12)",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herdr-orchestrator",
        description="Herdr Orchestrator public CLI (see docs/herdr-orchestrator-design.md)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "doctor",
        help="Environment diagnostics (stdout JSON; exit 1 if HERDR_ENV≠1)",
    )

    for name in PUBLIC_COMMANDS:
        if name == "doctor":
            continue
        p = sub.add_parser(name, help=_COMMAND_HELP.get(name, name))
        if name == "reconcile-dispatch":
            p.add_argument("task_id", metavar="task.id")
            p.add_argument("--dispatch-outcome", choices=("uncertain", "failed"))
            p.add_argument("--dispatch-reason")
        elif name == "handoff":
            p.add_argument("--mode", required=True, choices=("sync", "async"))
            p.add_argument(
                "--worker-role",
                required=True,
                choices=("design", "reviewer", "implementer", "orchestrator"),
            )
            p.add_argument("--task-type", required=True)
            p.add_argument("--instruction", required=True)
            p.add_argument(
                "--result-wait-timeout-sec",
                type=int,
                default=30 * 60,
                help="sync only: wait for terminal result (default 30m)",
            )
        elif name == "result-submit":
            p.add_argument("task_id", nargs="?", metavar="task.id")
            p.add_argument(
                "--result-status",
                required=True,
                choices=("succeeded", "failed", "unavailable"),
            )
            p.add_argument("--result-ref", default=None)
            p.add_argument("--summary", default=None)
        else:
            p.add_argument("task_id", nargs="?", metavar="task.id")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "doctor": _cmd_doctor,
        "handoff": _cmd_handoff,
        "task-status": _cmd_task_status,
        "task-result": _cmd_task_result,
        "result-submit": _cmd_result_submit,
        "reconcile-dispatch": _cmd_reconcile_dispatch,
        "resume-task": _cmd_resume_task,
        "reset-completion": _cmd_reset_completion,
        "recover-completion": _cmd_recover_completion,
    }
    handler = handlers[ns.command]
    return handler(ns)


if __name__ == "__main__":
    raise SystemExit(main())
