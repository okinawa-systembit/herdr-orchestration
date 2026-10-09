from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from typing import Any


class HerdrCliError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.payload = payload or {}


def validate_timeout(val: float | None, label: str = "timeout_sec") -> float | None:
    if val is None:
        return None
    if not math.isfinite(val) or val <= 0:
        raise HerdrCliError(
            f"invalid {label} {val!r}: must be a positive finite number",
            payload={"error": "invalid_timeout_setting"},
        )
    return val


def herdr_binary() -> str:
    path = shutil.which("herdr")
    if not path:
        raise HerdrCliError(
            "herdr CLI not found in PATH",
            payload={"error": "herdr_cli_missing"},
        )
    return path


def run_herdr(args: list[str], *, timeout_sec: float | None = None) -> dict[str, Any]:
    """Run herdr with JSON on stdout. Caller must have verified HERDR_ENV when required.

    Payload Contract:
    Any error raised after the external process is launched must maintain process
    execution evidence in exc.payload (exit_code, stderr, or herdr), enabling
    classify_prompt_error() to reliably distinguish it from pre-execution internal errors.
    """
    binary = herdr_binary()
    if timeout_sec is not None:
        validate_timeout(timeout_sec)
    else:
        raw = os.environ.get("HERDR_CLI_TIMEOUT_SEC")
        if raw is not None and raw.strip():
            try:
                parsed = float(raw)
            except ValueError as exc:
                raise HerdrCliError(
                    f"invalid HERDR_CLI_TIMEOUT_SEC {raw!r}: must be a positive finite number",
                    payload={"error": "invalid_timeout_setting"},
                ) from exc
            validate_timeout(parsed, label="HERDR_CLI_TIMEOUT_SEC")
            timeout_sec = parsed
    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HerdrCliError(
            f"herdr command timed out after {exc.timeout} seconds",
            payload={"herdr": {"error": {"code": "herdr_cli_timeout"}}},
        ) from exc
    if proc.returncode == 0:
        text = proc.stdout.strip()
        if not text:
            return {}
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"text": text}

    stderr = proc.stderr.strip()
    message = stderr or proc.stdout.strip() or f"herdr exited {proc.returncode}"
    payload: dict[str, Any] = {"error": "herdr_cli_failed", "exit_code": proc.returncode}
    try:
        if stderr:
            payload["herdr"] = json.loads(stderr)
    except json.JSONDecodeError:
        payload["stderr"] = stderr
    raise HerdrCliError(message, exit_code=proc.returncode, payload=payload)


def classify_prompt_error(exc: HerdrCliError) -> tuple[str, str]:
    """Classify a Herdr prompt error into (outcome, reason).

    Maintains the Payload Contract:
    Distinguishes external CLI execution errors (identified by herdr, stderr, or
    exit_code in payload) from internal pre-execution errors (identified by error
    in payload alone without external keys).
    """

    payload = exc.payload or {}

    # External Herdr CLI error (reported via CLI execution / JSON payload)
    if "herdr" in payload or "stderr" in payload or "exit_code" in payload:
        herdr_err = payload.get("herdr") if isinstance(payload.get("herdr"), dict) else {}
        inner = herdr_err.get("error") if isinstance(herdr_err.get("error"), dict) else {}
        cli_code = inner.get("code") if isinstance(inner.get("code"), str) else "herdr_prompt_failed"
        if cli_code in ("agent_not_found", "agent_not_ready", "agent_blocked"):
            return "failed", cli_code
        return "uncertain", cli_code

    # Internal Orchestrator pre-execution error (CLI not run or config invalid)
    internal_error = payload.get("error")
    if isinstance(internal_error, str):
        if internal_error in ("herdr_cli_missing", "invalid_timeout_setting"):
            return "failed", internal_error
        return "uncertain", internal_error

    return "uncertain", "herdr_prompt_failed"


