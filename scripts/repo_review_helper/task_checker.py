"""Task status verification via herdr-orchestrator task-list."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.identity import get_or_create_repository_identity, resolve_git_common_dir
from orchestrator.list_commands import run_task_list


@dataclass
class TaskCheckResult:
    can_safely_initialize: bool
    has_active_tasks: bool
    uncertain: bool
    active_task_ids: list[str] = field(default_factory=list)
    message: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


def fetch_task_list(
    worktree: Path,
    repo_identity: str,
    *,
    use_cli: bool = False,
) -> dict[str, Any]:
    """
    Fetch review tasks using orchestrator list command or CLI.
    Defaults to in-process run_task_list, but can use CLI if requested.
    """
    if use_cli:
        cli_bin = shutil.which("herdr-orchestrator")
        if not cli_bin:
            # Check repo-local script
            repo_script = worktree / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"
            if repo_script.exists():
                cli_bin = str(repo_script)

        if cli_bin:
            proc = subprocess.run(
                [cli_bin, "task-list", "--repository", repo_identity, "--type", "review", "--limit", "1000"],
                cwd=str(worktree),
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                try:
                    return json.loads(proc.stdout)
                except json.JSONDecodeError:
                    pass

    # Fallback to direct Python call with max limit 1000 (R64-01)
    payload, code = run_task_list(
        repository=repo_identity,
        type="review",
        limit=1000,
    )
    if code != 0:
        return {"complete": False, "truncated": False, "tasks": [], "error_code": code}
    return payload


def check_active_review_tasks(
    worktree: Path,
    repo_identity: str | None = None,
    *,
    use_cli: bool = False,
) -> TaskCheckResult:
    """
    Check if any review tasks are currently active or if the state is uncertain.
    """
    worktree = worktree.resolve()
    if not repo_identity:
        try:
            repo_identity = get_or_create_repository_identity(worktree)
        except Exception as e:
            return TaskCheckResult(
                can_safely_initialize=False,
                has_active_tasks=False,
                uncertain=True,
                message=f"Failed to resolve repository identity: {e}",
            )

    data = fetch_task_list(worktree, repo_identity, use_cli=use_cli)

    complete = data.get("complete", False)
    truncated = data.get("truncated", False)

    if not complete or truncated:
        return TaskCheckResult(
            can_safely_initialize=False,
            has_active_tasks=False,
            uncertain=True,
            message="task-list returned truncated or incomplete registry data; cannot determine task state safely",
            raw_data=data,
        )

    tasks = data.get("tasks", [])
    active_ids: list[str] = []
    inconsistent_ids: list[str] = []

    for item in tasks:
        task_info = item.get("task", {})
        result_info = item.get("result", {})
        overview = item.get("overview", {})

        task_id = task_info.get("id", "unknown")
        task_type = task_info.get("type")
        if task_type != "review":
            continue

        task_st = task_info.get("status")
        result_st = result_info.get("status")
        overview_st = overview.get("state")
        warnings = overview.get("warnings", [])

        # R64-06: Check for inconsistent_state_combination
        if "inconsistent_state_combination" in warnings:
            inconsistent_ids.append(task_id)

        # An active or incomplete task is open or uncertain
        if overview_st in ("open", "uncertain") or task_st in ("created", "in_progress", "unknown") or result_st == "pending":
            active_ids.append(task_id)

    if inconsistent_ids:
        return TaskCheckResult(
            can_safely_initialize=False,
            has_active_tasks=bool(active_ids),
            uncertain=True,
            active_task_ids=list(set(active_ids + inconsistent_ids)),
            message=(
                f"Found {len(inconsistent_ids)} review task(s) with inconsistent state combination: "
                f"{', '.join(inconsistent_ids)}. Safety stop required. "
                f"Resolve task state or specify --abandon-old-tasks to reinitialize."
            ),
            raw_data=data,
        )

    if active_ids:
        return TaskCheckResult(
            can_safely_initialize=False,
            has_active_tasks=True,
            uncertain=False,
            active_task_ids=active_ids,
            message=f"Found {len(active_ids)} active review task(s): {', '.join(active_ids)}",
            raw_data=data,
        )

    return TaskCheckResult(
        can_safely_initialize=True,
        has_active_tasks=False,
        uncertain=False,
        active_task_ids=[],
        message="No active review tasks found. Safe to initialize.",
        raw_data=data,
    )
