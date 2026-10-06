from __future__ import annotations

import re
import uuid

from orchestrator.registry.errors import RegistryError

_TASK_ID_RE = re.compile(
    r"^task_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def generate_task_id() -> str:
    return f"task_{uuid.uuid4()}"


def validate_task_id(task_id: str) -> str:
    if not task_id or ".." in task_id or "/" in task_id or "\\" in task_id:
        raise RegistryError("invalid_task_id", message="malformed task.id")
    if not _TASK_ID_RE.match(task_id):
        raise RegistryError("invalid_task_id", message="task.id must be task_<uuidv4>")
    return task_id
