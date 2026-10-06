from __future__ import annotations

import os

from orchestrator.herdr_cli import HerdrCliError, run_herdr


def probe_worker_task_trace(task_id: str, worker_agent_name: str) -> bool:
    """Best-effort affirmative trace of task.id in worker recent terminal output (§6.12 A step 2)."""
    forced = os.environ.get("HERDR_ORCHESTRATOR_WORKER_TRACE_TASK_ID")
    if forced is not None:
        return forced == task_id
    try:
        payload = run_herdr(
            [
                "agent",
                "read",
                worker_agent_name,
                "--source",
                "recent",
                "--lines",
                "200",
                "--format",
                "text",
            ]
        )
    except HerdrCliError:
        return False
    text = payload.get("text") if isinstance(payload.get("text"), str) else ""
    if not text and isinstance(payload.get("result"), dict):
        inner = payload["result"]
        if isinstance(inner.get("text"), str):
            text = inner["text"]
        elif isinstance(inner.get("output"), str):
            text = inner["output"]
    return task_id in text
