#!/usr/bin/env python3
"""
Internal Task Registry operations. Not a public Agent surface.

Invoked by herdr-orchestrator wrapper only (see docs §6.4, §6.6).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.task_id import validate_task_id

INTERNAL_OPS = (
    "create",
    "get",
    "record-prompt-start",
    "assert-handoff-continuable",
    "mark-dispatch",
    "reconcile-dispatch",
    "update-result",
    "claim-completion",
    "finish-completion",
    "inspect-completion",
    "reset-completion",
    "cleanup",
)


def _emit_ok(payload: dict[str, Any]) -> int:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _emit_registry_error(exc: RegistryError) -> int:
    payload: dict[str, Any] = {
        "error": exc.code,
        "retryable": exc.retryable,
        "message": exc.message,
    }
    if exc.task_id:
        payload["task_id"] = exc.task_id
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if not exc.retryable else 1


def _cmd_create(_: argparse.Namespace) -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return _emit_registry_error(
            RegistryError("invalid_input", message="create requires JSON on stdin")
        )
    if not isinstance(payload, dict):
        return _emit_registry_error(RegistryError("invalid_input", message="create payload must be object"))
    try:
        doc = registry_ops.create_task(payload)
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": doc})


def _cmd_get(ns: argparse.Namespace) -> int:
    try:
        validate_task_id(ns.task_id)
        doc = registry_ops.get_task(ns.task_id)
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": doc})


def _cmd_assert_handoff(ns: argparse.Namespace) -> int:
    try:
        info = registry_ops.assert_handoff_continuable(ns.task_id)
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": info})


def _cmd_record_prompt_start(ns: argparse.Namespace) -> int:
    try:
        doc = registry_ops.record_prompt_start(ns.task_id)
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": doc})


def _cmd_mark_dispatch(ns: argparse.Namespace) -> int:
    try:
        doc = registry_ops.mark_dispatch(
            ns.task_id,
            outcome=ns.outcome,
            reason=ns.reason,
        )
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": doc})


def _cmd_update_result(ns: argparse.Namespace) -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return _emit_registry_error(
            RegistryError("invalid_input", message="update-result requires JSON on stdin")
        )
    try:
        doc = registry_ops.update_result(
            ns.task_id,
            result_status=payload["result_status"],
            result_ref=payload.get("result_ref"),
            summary=payload.get("summary"),
        )
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": doc})


def _cmd_claim_completion(ns: argparse.Namespace) -> int:
    try:
        doc = registry_ops.claim_completion(ns.task_id)
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": doc})


def _cmd_finish_completion(ns: argparse.Namespace) -> int:
    try:
        doc = registry_ops.finish_completion(
            ns.task_id,
            outcome=ns.outcome,
            reason=ns.reason,
        )
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": doc})


def _cmd_reset_completion(ns: argparse.Namespace) -> int:
    try:
        doc = registry_ops.reset_completion(ns.task_id)
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": doc})


def _cmd_inspect_completion(ns: argparse.Namespace) -> int:
    try:
        info = registry_ops.inspect_completion(ns.task_id)
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": info})


def _cmd_reconcile_dispatch(ns: argparse.Namespace) -> int:
    try:
        info = registry_ops.reconcile_dispatch(
            ns.task_id,
            dispatch_outcome=ns.dispatch_outcome,
            dispatch_reason=ns.dispatch_reason,
        )
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": info})


def _cmd_cleanup(_: argparse.Namespace) -> int:
    try:
        info = registry_ops.cleanup_tasks()
    except RegistryError as exc:
        return _emit_registry_error(exc)
    return _emit_ok({"result": info})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="task-registry.py", description="Internal registry writer")
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("create")
    sub.add_parser("cleanup")

    get_p = sub.add_parser("get")
    get_p.add_argument("task_id", metavar="task.id")

    for op in (
        "record-prompt-start",
        "assert-handoff-continuable",
        "claim-completion",
        "reset-completion",
        "inspect-completion",
    ):
        p = sub.add_parser(op)
        p.add_argument("task_id", metavar="task.id")

    mark_p = sub.add_parser("mark-dispatch")
    mark_p.add_argument("task_id", metavar="task.id")
    mark_p.add_argument(
        "outcome",
        choices=("sent", "failed", "skipped", "uncertain"),
    )
    mark_p.add_argument("--reason")

    finish_p = sub.add_parser("finish-completion")
    finish_p.add_argument("task_id", metavar="task.id")
    finish_p.add_argument("outcome", choices=("sent", "failed", "uncertain", "skipped"))
    finish_p.add_argument("--reason")

    upd_p = sub.add_parser("update-result")
    upd_p.add_argument("task_id", metavar="task.id")

    rec_p = sub.add_parser("reconcile-dispatch")
    rec_p.add_argument("task_id", metavar="task.id")
    rec_p.add_argument("--dispatch-outcome", choices=("uncertain", "failed"))
    rec_p.add_argument("--dispatch-reason")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    handlers = {
        "create": _cmd_create,
        "get": _cmd_get,
        "record-prompt-start": _cmd_record_prompt_start,
        "assert-handoff-continuable": lambda ns: _cmd_assert_handoff(ns),
        "mark-dispatch": _cmd_mark_dispatch,
        "update-result": _cmd_update_result,
        "claim-completion": _cmd_claim_completion,
        "finish-completion": _cmd_finish_completion,
        "inspect-completion": _cmd_inspect_completion,
        "reset-completion": _cmd_reset_completion,
        "reconcile-dispatch": _cmd_reconcile_dispatch,
        "cleanup": _cmd_cleanup,
    }
    return handlers[ns.operation](ns)


if __name__ == "__main__":
    raise SystemExit(main())
