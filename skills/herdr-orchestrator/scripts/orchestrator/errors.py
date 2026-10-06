from __future__ import annotations

import json
import sys
from typing import Any, NoReturn


def emit_error(
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


def emit_not_implemented(command: str) -> NoReturn:
    emit_error(
        "not_implemented",
        retryable=False,
        exit_code=2,
        command=command,
        hint="See docs/herdr-orchestrator-implementation-index.md for rollout stage.",
    )
