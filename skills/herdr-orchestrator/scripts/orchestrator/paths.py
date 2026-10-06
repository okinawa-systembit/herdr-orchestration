from __future__ import annotations

import os
from pathlib import Path


def tasks_dir() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    if state:
        base = Path(state)
    else:
        base = Path.home() / ".local" / "state"
    return base / "herdr-orchestrator" / "tasks"


def task_json_path(task_id: str) -> Path:
    return tasks_dir() / f"{task_id}.json"


def handoff_lock_path(task_id: str) -> Path:
    return tasks_dir() / f"{task_id}.handoff.lock"
