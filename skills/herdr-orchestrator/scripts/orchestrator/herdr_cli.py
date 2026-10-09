from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any


class HerdrCliError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.payload = payload or {}


def herdr_binary() -> str:
    path = shutil.which("herdr")
    if not path:
        raise HerdrCliError(
            "herdr CLI not found in PATH",
            payload={"error": "herdr_cli_missing"},
        )
    return path


def run_herdr(args: list[str], *, timeout_sec: float | None = None) -> dict[str, Any]:
    """Run herdr with JSON on stdout. Caller must have verified HERDR_ENV when required."""
    binary = herdr_binary()
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
