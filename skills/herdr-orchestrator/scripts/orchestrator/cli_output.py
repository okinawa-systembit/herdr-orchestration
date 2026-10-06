from __future__ import annotations

import json
import sys
from typing import Any, NoReturn

from orchestrator.env import HerdrEnvError
from orchestrator.registry.errors import RegistryError
from orchestrator.completion_commands import CompletionCommandError
from orchestrator.handoff_commands import HandoffCommandError
from orchestrator.result_commands import ResultCommandError


def emit_json(payload: dict[str, Any], *, exit_code: int = 0) -> int:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return exit_code


def emit_command_error(
    error: str,
    *,
    retryable: bool = False,
    exit_code: int = 1,
    **fields: Any,
) -> NoReturn:
    payload: dict[str, Any] = {"error": error, "retryable": retryable, **fields}
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()
    raise SystemExit(exit_code)


def emit_completion_command_error(exc: CompletionCommandError) -> NoReturn:
    fields: dict[str, Any] = {"message": exc.message}
    if exc.task_id:
        fields["task_id"] = exc.task_id
    emit_command_error(exc.code, retryable=exc.retryable, **fields)


def emit_handoff_command_error(exc: HandoffCommandError) -> NoReturn:
    fields: dict[str, Any] = {"message": exc.message}
    if exc.task_id:
        fields["task_id"] = exc.task_id
    emit_command_error(exc.code, retryable=exc.retryable, **fields)


def emit_result_command_error(exc: ResultCommandError) -> NoReturn:
    fields: dict[str, Any] = {"message": exc.message}
    if exc.task_id:
        fields["task_id"] = exc.task_id
    if exc.hint:
        fields["hint"] = exc.hint
    emit_command_error(exc.code, retryable=exc.retryable, **fields)


def emit_registry_error(exc: RegistryError) -> NoReturn:
    fields: dict[str, Any] = {"message": exc.message}
    if exc.task_id:
        fields["task_id"] = exc.task_id
    emit_command_error(exc.code, retryable=exc.retryable, **fields)


def emit_herdr_env_error(exc: HerdrEnvError) -> NoReturn:
    emit_command_error(exc.code, retryable=False, message=exc.message)
