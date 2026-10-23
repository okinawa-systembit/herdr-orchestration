"""Review ID allocation, record persistence, and index generation."""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import (
    CorruptedStateError,
    SecurityPermissionError,
    WorktreeMismatchError,
    check_and_enforce_permissions,
    load_state,
    safe_ensure_dir,
    save_state,
)

RV_PATTERN = re.compile(r"^RV(\d+)\.md$")
INDEX_FILENAME = "INDEX.md"


class TaskRecordConflictError(Exception):
    """Raised when re-recording an existing task ID with different content."""


class ReviewRecordExistsError(Exception):
    """Raised when attempting to overwrite an existing review record."""


def get_internal_reviews_root(worktree: str | Path) -> Path:
    return Path(worktree).resolve() / "docs" / "reviews" / "internal"


def get_instance_reviews_dir(worktree: str | Path, instance_id: str) -> Path:
    return get_internal_reviews_root(worktree) / instance_id


def scan_existing_review_numbers(reviews_dir: Path) -> list[int]:
    """Scan existing RVNNN.md files in reviews_dir and return sorted list of numbers."""
    if not reviews_dir.exists():
        return []
    check_and_enforce_permissions(reviews_dir, 0o700, is_dir=True)
    numbers: list[int] = []
    for entry in reviews_dir.iterdir():
        if entry.is_file():
            m = RV_PATTERN.match(entry.name)
            if m:
                numbers.append(int(m.group(1)))
    return sorted(numbers)


def find_existing_record_by_task_id(
    reviews_dir: Path,
    task_id: str,
) -> tuple[str, Path, str] | None:
    """
    Search reviews_dir for an existing record with matching task_id.
    Returns (review_id, file_path, file_content) or None.
    """
    if not reviews_dir.exists():
        return None
    check_and_enforce_permissions(reviews_dir, 0o700, is_dir=True)
    for entry in reviews_dir.iterdir():
        if entry.is_file() and RV_PATTERN.match(entry.name):
            try:
                meta = parse_review_metadata(entry)
                if meta.get("task_id") == task_id:
                    content = entry.read_text(encoding="utf-8")
                    return meta.get("review_id", entry.stem), entry, content
            except Exception:
                continue
    return None


def format_review_id(num: int) -> str:
    """Format review number: minimum 3 digits with zero padding (e.g. RV001, RV042, RV1000)."""
    return f"RV{num:03d}"


def allocate_next_review_id(
    git_common_dir: str | Path,
    current_worktree: str | Path,
) -> tuple[str, dict[str, Any]]:
    """
    Allocate the next Review ID under lock.
    Verifies worktree registration, scans existing files, updates state.json counter atomically.
    Returns (review_id, updated_state).
    """
    current_worktree = Path(current_worktree).resolve()
    state = load_state(git_common_dir)
    if state is None:
        raise CorruptedStateError("No review state initialized in this repository")

    registered_worktree = Path(state["worktree_path"]).resolve()
    if registered_worktree != current_worktree:
        raise WorktreeMismatchError(
            f"Worktree mismatch: registered={registered_worktree}, current={current_worktree}"
        )

    instance_id = state["instance_id"]
    reviews_dir = get_instance_reviews_dir(current_worktree, instance_id)
    existing_numbers = scan_existing_review_numbers(reviews_dir)
    existing_max = max(existing_numbers, default=0)

    counter = state.get("counter", 0)
    next_num = max(existing_max, counter) + 1
    review_id = format_review_id(next_num)

    # Update state counter immediately so a crash results in a skipped ID rather than a duplicate
    state["counter"] = next_num
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(git_common_dir, state)

    return review_id, state


def parse_review_metadata(file_path: Path) -> dict[str, Any]:
    """Extract summary information from an RVNNN.md file. Frontmatter takes strict precedence."""
    content = file_path.read_text(encoding="utf-8")
    meta: dict[str, Any] = {
        "review_id": file_path.stem,
        "filename": file_path.name,
        "outcome": "UNKNOWN",
        "status": "succeeded",
        "task_id": "-",
        "target_commit": "-",
        "summary": "-",
        "created_at": "-",
    }

    has_frontmatter = False
    # Extract frontmatter if present
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            has_frontmatter = True
            fm_text = parts[1]
            for line in fm_text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip('"').strip("'")
                    if k in meta:
                        meta[k] = v
            # Backwards compatibility: if outcome not set but status is a review outcome
            if meta["outcome"] == "UNKNOWN" and meta["status"] in ("GO", "要修正", "NO-GO", "CONDITIONAL GO"):
                meta["outcome"] = meta["status"]
                meta["status"] = "succeeded"

    # Fallback search in Markdown headers/bullets ONLY if no frontmatter exists
    if not has_frontmatter:
        for line in content.splitlines():
            line_clean = line.strip()
            if line_clean.startswith("- **総合判定**:") or line_clean.startswith("- **判定**:"):
                meta["outcome"] = line_clean.split(":", 1)[1].strip().strip("*").strip()
            elif line_clean.startswith("- **Status**:") or line_clean.startswith("- **Task Status**:"):
                meta["status"] = line_clean.split(":", 1)[1].strip().strip("*").strip()
            elif line_clean.startswith("- **Task ID**:"):
                meta["task_id"] = line_clean.split(":", 1)[1].strip().strip("*").strip()
            elif line_clean.startswith("- **Target Commit**:") or line_clean.startswith("- **対象コミット**:"):
                meta["target_commit"] = line_clean.split(":", 1)[1].strip().strip("*").strip()
            elif line_clean.startswith("- **Created At**:") or line_clean.startswith("- **日時**:"):
                meta["created_at"] = line_clean.split(":", 1)[1].strip().strip("*").strip()

    return meta


def update_index(
    current_worktree: str | Path,
    instance_id: str,
) -> Path:
    """
    Atomically rebuild and write INDEX.md derived from all RV*.md files in reviews_dir.
    """
    current_worktree = Path(current_worktree).resolve()
    internal_root = get_internal_reviews_root(current_worktree)
    safe_ensure_dir(internal_root, 0o700, base_dir=current_worktree)

    reviews_dir = get_instance_reviews_dir(current_worktree, instance_id)
    safe_ensure_dir(reviews_dir, 0o700, base_dir=current_worktree)

    records: list[dict[str, Any]] = []
    for entry in sorted(reviews_dir.iterdir(), key=lambda p: p.name):
        if entry.is_file() and RV_PATTERN.match(entry.name):
            try:
                meta = parse_review_metadata(entry)
                records.append(meta)
            except Exception:
                records.append({
                    "review_id": entry.stem,
                    "filename": entry.name,
                    "status": "ERROR",
                    "task_id": "-",
                    "target_commit": "-",
                    "summary": "Failed to parse",
                    "created_at": "-",
                })

    now_iso = datetime.now(timezone.utc).isoformat()
    lines = [
        f"# 内部レビュー履歴一覧 (Internal Review Index)",
        "",
        f"- **Instance ID**: `{instance_id}`",
        f"- **最終更新**: {now_iso}",
        f"- **総記録数**: {len(records)}",
        "",
        "| Review ID | 日時 | Task ID | 対象コミット | 判定 | ファイル |",
        "|---|---|---|---|---|---|",
    ]

    for r in records:
        rid = r["review_id"]
        fname = r["filename"]
        dt = r["created_at"]
        tid = r["task_id"]
        commit = r["target_commit"]
        st = r.get("outcome", r.get("status", "UNKNOWN"))
        lines.append(f"| {rid} | {dt} | {tid} | `{commit}` | **{st}** | [{fname}]({fname}) |")

    lines.append("")
    payload = "\n".join(lines)

    token = secrets.token_hex(4)
    tmp_path = reviews_dir / f".{INDEX_FILENAME}.tmp.{os.getpid()}.{token}"
    index_path = reviews_dir / INDEX_FILENAME

    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)

    os.replace(str(tmp_path), str(index_path))
    os.chmod(str(index_path), 0o600)
    return index_path


def save_review_record(
    git_common_dir: str | Path,
    current_worktree: str | Path,
    review_id: str,
    content: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """
    Atomically publish an individual review record under docs/reviews/internal/<instance-id>/RVNNN.md.
    Enforces mode 0700 on directories and 0600 on files.
    Triggers derived INDEX.md rebuild atomically.
    """
    current_worktree = Path(current_worktree).resolve()
    state = load_state(git_common_dir)
    if state is None:
        raise CorruptedStateError("No review state initialized in this repository")

    instance_id = state["instance_id"]
    internal_root = get_internal_reviews_root(current_worktree)
    safe_ensure_dir(internal_root, 0o700, base_dir=current_worktree)

    reviews_dir = get_instance_reviews_dir(current_worktree, instance_id)
    safe_ensure_dir(reviews_dir, 0o700, base_dir=current_worktree)

    # Format document with metadata header
    final_content = content
    if metadata:
        fm_lines = ["---"]
        for k, v in metadata.items():
            fm_lines.append(f"{k}: \"{v}\"")
        fm_lines.append("---")
        fm_lines.append("")
        final_content = "\n".join(fm_lines) + content

    target_file = reviews_dir / f"{review_id}.md"
    token = secrets.token_hex(4)
    tmp_path = reviews_dir / f".tmp.{review_id}.md.{os.getpid()}.{token}"

    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, final_content.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)

    # R64-05: Publish atomically without overwriting using os.link
    try:
        os.link(str(tmp_path), str(target_file))
        os.unlink(str(tmp_path))
    except FileExistsError as e:
        if tmp_path.exists():
            os.unlink(str(tmp_path))
        raise ReviewRecordExistsError(
            f"Review record already exists and cannot be overwritten: {target_file}"
        ) from e
    except Exception:
        if tmp_path.exists():
            os.unlink(str(tmp_path))
        raise

    os.chmod(str(target_file), 0o600)

    # Derive INDEX.md
    update_index(current_worktree, instance_id)

    return target_file


def _extract_body_without_frontmatter(text: str) -> str:
    """Extract body content without leading frontmatter, preserving exact bytes/whitespace."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
            # Strip newline(s) immediately following frontmatter closing delimiter
            if body.startswith("\r\n\r\n"):
                return body[4:]
            elif body.startswith("\n\n"):
                return body[2:]
            elif body.startswith("\r\n"):
                return body[2:]
            elif body.startswith("\n"):
                return body[1:]
            return body
    return text


def record_save_atomic(
    git_common_dir: str | Path,
    current_worktree: str | Path,
    task_id: str,
    content: str,
    *,
    outcome: str = "GO",
    task_status: str = "succeeded",
    result_status: str | None = None,
    target_commit: str = "unknown",
) -> tuple[str, Path, bool]:
    """
    Idempotently save review record for task_id under lock.
    Returns (review_id, file_path, is_new).
    Raises TaskRecordConflictError if task_id already recorded with different content, commit, or outcome.
    """
    if result_status is not None:
        if result_status in ("GO", "要修正", "NO-GO", "CONDITIONAL GO"):
            outcome = result_status
        else:
            task_status = result_status
    current_worktree = Path(current_worktree).resolve()
    state = load_state(git_common_dir)
    if state is None:
        raise CorruptedStateError("No review state initialized in this repository")

    instance_id = state["instance_id"]
    reviews_dir = get_instance_reviews_dir(current_worktree, instance_id)

    # 1. Check for existing record with this task_id
    existing = find_existing_record_by_task_id(reviews_dir, task_id)
    if existing is not None:
        existing_rid, existing_path, existing_content = existing
        existing_meta = parse_review_metadata(existing_path)
        existing_body = _extract_body_without_frontmatter(existing_content)
        # If the input content already contains helper frontmatter header, strip it too
        if content.startswith("---") and "task_id:" in content[:300]:
            new_body = _extract_body_without_frontmatter(content)
        else:
            new_body = content

        # R64-02 & R64-07: Strict verification of target_commit, outcome, task_status, and payload body
        commit_match = (existing_meta.get("target_commit") == target_commit)
        existing_outcome = existing_meta.get("outcome")
        if existing_outcome == "UNKNOWN":
            existing_outcome = existing_meta.get("status")
        outcome_match = (existing_outcome == outcome)
        status_match = (existing_meta.get("status") == task_status)
        body_match = (existing_body == new_body)

        if commit_match and outcome_match and status_match and body_match:
            # Idempotent replay: return existing record without altering any files
            return existing_rid, existing_path, False
        else:
            diffs = []
            if not commit_match:
                diffs.append(
                    f"target_commit mismatch (existing={existing_meta.get('target_commit')}, requested={target_commit})"
                )
            if not outcome_match:
                diffs.append(
                    f"outcome mismatch (existing={existing_outcome}, requested={outcome})"
                )
            if not status_match:
                diffs.append(
                    f"task_status mismatch (existing={existing_meta.get('status')}, requested={task_status})"
                )
            if not body_match:
                diffs.append("body content mismatch")
            raise TaskRecordConflictError(
                f"Task ID '{task_id}' was already recorded in {existing_path} with conflicting payload: {'; '.join(diffs)}"
            )

    # 2. Allocate next ID (atomically increments counter)
    review_id, updated_state = allocate_next_review_id(git_common_dir, current_worktree)

    metadata = {
        "review_id": review_id,
        "task_id": task_id,
        "instance_id": updated_state["instance_id"],
        "outcome": outcome,
        "status": task_status,
        "target_commit": target_commit,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path = save_review_record(
        git_common_dir,
        current_worktree,
        review_id,
        content,
        metadata=metadata,
    )

    return review_id, out_path, True
