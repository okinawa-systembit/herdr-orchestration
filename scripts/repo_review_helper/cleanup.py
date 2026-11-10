"""Review record and input directory cleanup candidates scanner."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestrator.identity import get_repository_identity

from .recorder import RV_PATTERN, get_instance_reviews_dir, parse_review_metadata
from .state import get_git_common_dir, load_state
from .task_checker import fetch_task_list

RETENTION_DAYS = 30


def get_active_task_ids(
    worktree: Path,
    repo_identity: str | None = None,
) -> tuple[set[str], bool]:
    """
    Fetch active task IDs across all task types.
    Returns (set_of_active_task_ids, is_failed_or_truncated).
    """
    if not repo_identity:
        try:
            repo_identity = get_repository_identity(worktree)
        except Exception as exc:
            sys.stderr.write(f"[WARN] Failed to resolve repository identity: {exc}\n")
            return set(), True

    try:
        data = fetch_task_list(
            worktree,
            repo_identity,
            use_cli=False,
            task_type=None,
            statuses=["created", "in_progress", "unknown"],
        )
    except Exception as exc:
        sys.stderr.write(f"[WARN] Failed to fetch task list: {exc}\n")
        return set(), True

    complete = data.get("complete", False)
    truncated = data.get("truncated", False)
    if not complete or truncated:
        sys.stderr.write(
            "[WARN] task-list returned incomplete or truncated registry data; falling back to active task protection\n"
        )
        return set(), True

    active_ids: set[str] = set()
    for item in data.get("tasks", []):
        task_info = item.get("task", {})
        task_id = task_info.get("id")
        if task_id:
            active_ids.add(task_id)

    return active_ids, False


def scan_cleanup_candidates(
    worktree: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Scan reviews_dir and .input/ for cleanup candidates older than RETENTION_DAYS.
    Returns structured dictionary matching the JSON schema.
    """
    worktree = worktree.resolve()
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 1. Resolve active instance
    instance_id: str | None = None
    reviews_dir: Path | None = None
    try:
        git_common_dir = get_git_common_dir(worktree)
        state = load_state(git_common_dir)
        if state is not None:
            reg_worktree = Path(state.get("worktree_path", "")).resolve()
            if reg_worktree == worktree:
                inst = state.get("instance_id")
                if isinstance(inst, str) and inst:
                    instance_id = inst
                    reviews_dir = get_instance_reviews_dir(worktree, instance_id)
    except Exception:
        instance_id = None
        reviews_dir = None

    # 2. Query active tasks
    active_task_ids, active_tasks_failed = get_active_task_ids(worktree)

    archive_candidates: list[dict[str, Any]] = []
    delete_candidates: list[dict[str, Any]] = []

    protected_within_retention = 0
    protected_active_task = 0
    protected_future_or_unreadable = 0
    protected_control_file = 0
    protected_symlink = 0

    # 3. Scan docs/reviews/internal/<instance-id>/
    if reviews_dir and reviews_dir.exists():
        if reviews_dir.is_symlink():
            sys.stderr.write(f"[WARN] Skipping symlink: {reviews_dir}\n")
            protected_symlink += 1
        elif reviews_dir.is_dir():
            try:
                entries = sorted(reviews_dir.iterdir(), key=lambda p: p.name)
            except OSError as exc:
                sys.stderr.write(f"[WARN] Failed to list {reviews_dir}: {exc}\n")
                entries = []
                protected_future_or_unreadable += 1

            for entry in entries:
                if entry.is_symlink():
                    sys.stderr.write(f"[WARN] Skipping symlink: {entry}\n")
                    protected_symlink += 1
                    continue

                if entry.is_dir() or entry.name.startswith(".") or entry.name == "INDEX.md":
                    continue

                if not RV_PATTERN.match(entry.name):
                    continue

                try:
                    meta = parse_review_metadata(entry)
                except Exception as exc:
                    sys.stderr.write(f"[WARN] Failed to read {entry}: {exc}\n")
                    protected_future_or_unreadable += 1
                    continue

                try:
                    st = entry.lstat()
                except OSError as exc:
                    sys.stderr.write(f"[WARN] Failed to lstat {entry}: {exc}\n")
                    protected_future_or_unreadable += 1
                    continue

                mtime_dt = datetime.fromtimestamp(st.st_mtime, timezone.utc)
                created_at_raw = meta.get("created_at")
                created_dt: datetime | None = None
                if isinstance(created_at_raw, str) and created_at_raw != "-":
                    try:
                        dt = datetime.fromisoformat(created_at_raw)
                        if dt.tzinfo is not None:
                            created_dt = dt
                    except ValueError:
                        created_dt = None

                if created_dt is not None:
                    timestamp = created_dt
                    source = "created_at"
                else:
                    timestamp = mtime_dt
                    source = "mtime"

                # Check future timestamp
                if (mtime_dt > now) or (created_dt is not None and created_dt > now):
                    protected_future_or_unreadable += 1
                    continue

                age_delta = now - timestamp
                age_seconds = age_delta.total_seconds()
                age_days = round(age_seconds / 86400.0, 1)

                if age_delta <= timedelta(days=RETENTION_DAYS):
                    protected_within_retention += 1
                else:
                    if active_tasks_failed:
                        protected_active_task += 1
                    else:
                        task_id = meta.get("task_id", "-")
                        if task_id in active_task_ids:
                            protected_active_task += 1
                        else:
                            rel_path = str(entry.relative_to(worktree))
                            archive_to = str((entry.parent / "archive" / entry.name).relative_to(worktree))
                            archive_candidates.append({
                                "path": rel_path,
                                "archive_to": archive_to,
                                "review_id": meta.get("review_id", entry.stem),
                                "task_id": task_id,
                                "timestamp_source": source,
                                "age_days": age_days,
                            })

    # Sort archive_candidates by review_id
    archive_candidates.sort(key=lambda x: x["review_id"])

    # 4. Scan .input/inbox and .input/review/sent
    input_dirs = [
        (worktree / ".input" / "inbox", "inbox"),
        (worktree / ".input" / "review" / "sent", "review_sent"),
    ]

    for dir_path, category in input_dirs:
        if not dir_path.exists():
            continue

        if dir_path.is_symlink():
            sys.stderr.write(f"[WARN] Skipping symlink: {dir_path}\n")
            protected_symlink += 1
            continue

        if not dir_path.is_dir():
            continue

        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            sys.stderr.write(f"[WARN] Failed to list {dir_path}: {exc}\n")
            protected_future_or_unreadable += 1
            continue

        for entry in entries:
            if entry.is_symlink():
                sys.stderr.write(f"[WARN] Skipping symlink: {entry}\n")
                protected_symlink += 1
                continue

            if entry.is_dir() or entry.name.startswith("."):
                continue

            try:
                st = entry.lstat()
            except OSError as exc:
                sys.stderr.write(f"[WARN] Failed to lstat {entry}: {exc}\n")
                protected_future_or_unreadable += 1
                continue

            mtime_dt = datetime.fromtimestamp(st.st_mtime, timezone.utc)
            if mtime_dt > now:
                protected_future_or_unreadable += 1
                continue

            age_delta = now - mtime_dt
            age_seconds = age_delta.total_seconds()
            age_days = round(age_seconds / 86400.0, 1)

            if age_delta <= timedelta(days=RETENTION_DAYS):
                protected_within_retention += 1
            else:
                rel_path = str(entry.relative_to(worktree))
                delete_candidates.append({
                    "path": rel_path,
                    "category": category,
                    "age_days": age_days,
                })

    delete_candidates.sort(key=lambda x: x["path"])

    # 5. Control files protection
    control_files = [
        worktree / ".input" / "review" / "state.json",
        worktree / ".input" / "review" / "reviewer-draft.md",
        worktree / ".input" / "review" / "incoming.md",
    ]
    for cf in control_files:
        if cf.is_symlink() or cf.exists():
            protected_control_file += 1

    total_protected = (
        protected_within_retention
        + protected_active_task
        + protected_future_or_unreadable
        + protected_control_file
        + protected_symlink
    )

    return {
        "retention_days": RETENTION_DAYS,
        "instance_id": instance_id,
        "archive_candidates": archive_candidates,
        "delete_candidates": delete_candidates,
        "protected_count": {
            "total": total_protected,
            "by_reason": {
                "within_retention": protected_within_retention,
                "active_task": protected_active_task,
                "future_or_unreadable": protected_future_or_unreadable,
                "control_file": protected_control_file,
                "symlink": protected_symlink,
            },
        },
    }
