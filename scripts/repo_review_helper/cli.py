"""Command-line interface implementation for repo-review-helper."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .lock import LockAcquisitionError, ReviewLock
from .recorder import (
    TaskRecordConflictError,
    allocate_next_review_id,
    get_instance_reviews_dir,
    get_internal_reviews_root,
    record_save_atomic,
    save_review_record,
    scan_existing_review_numbers,
    update_index,
)
from .state import (
    CorruptedStateError,
    SecurityPermissionError,
    WorktreeMismatchError,
    create_initial_state,
    get_git_common_dir,
    get_state_file_path,
    load_state,
    safe_ensure_dir,
    save_state,
)
from .task_checker import check_active_review_tasks


def _resolve_worktree(worktree_arg: str | None) -> Path:
    if worktree_arg:
        p = Path(worktree_arg).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Worktree path does not exist: {worktree_arg}")
        return p
    return Path.cwd().resolve()


def _get_head_commit(worktree: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()
    except Exception:
        return "unknown"


def cmd_init(args: argparse.Namespace) -> int:
    worktree = _resolve_worktree(args.worktree)
    git_common_dir = get_git_common_dir(worktree)

    try:
        with ReviewLock(git_common_dir):
            existing_state = None
            state_corrupted = False
            try:
                existing_state = load_state(git_common_dir)
            except CorruptedStateError as e:
                state_corrupted = True
                print(f"[WARN] Existing state is corrupted: {e}", file=sys.stderr)

            if existing_state is not None and not args.force:
                reg_worktree = Path(existing_state["worktree_path"]).resolve()
                if reg_worktree == worktree:
                    print(f"Already initialized for instance {existing_state['instance_id']} at {worktree}")
                    return 0
                else:
                    print(
                        f"[ERROR] State exists but registered for different worktree: {reg_worktree} (current: {worktree}). Use --force with --abandon-old-tasks to reinitialize.",
                        file=sys.stderr,
                    )
                    return 1

            # State is missing, corrupted, or --force requested
            task_check = check_active_review_tasks(worktree)

            if not task_check.can_safely_initialize:
                if not args.abandon_old_tasks:
                    print(f"[ERROR] Cannot safely initialize review state: {task_check.message}", file=sys.stderr)
                    if task_check.has_active_tasks:
                        print(f"Active tasks: {task_check.active_task_ids}", file=sys.stderr)
                        print(
                            "If you wish to abandon the previous tasks and start fresh, specify --abandon-old-tasks.",
                            file=sys.stderr,
                        )
                    else:
                        print("Task registry status is uncertain. Resolve pending tasks or specify --abandon-old-tasks.", file=sys.stderr)
                    return 1
                else:
                    print(f"[WARN] Abandoning old review tasks as requested. (Old tasks: {task_check.active_task_ids})", file=sys.stderr)

            import secrets as _sec
            new_instance_id = f"inst_{_sec.token_hex(6)}"
            state = create_initial_state(new_instance_id, worktree, counter=0)
            save_state(git_common_dir, state)

            # Ensure .inbox and docs/reviews/internal/<instance-id> are initialized
            inbox_dir = worktree / ".inbox"
            safe_ensure_dir(inbox_dir, 0o700, base_dir=worktree)

            internal_root = get_internal_reviews_root(worktree)
            safe_ensure_dir(internal_root, 0o700, base_dir=worktree)

            reviews_dir = get_instance_reviews_dir(worktree, new_instance_id)
            safe_ensure_dir(reviews_dir, 0o700, base_dir=worktree)
            update_index(worktree, new_instance_id)

            print(f"Successfully initialized review helper: instance_id={new_instance_id}, worktree={worktree}")
            return 0
    except (SecurityPermissionError, LockAcquisitionError) as e:
        print(f"[SECURITY ERROR] {e}", file=sys.stderr)
        return 2


def cmd_status(args: argparse.Namespace) -> int:
    worktree = _resolve_worktree(args.worktree)
    git_common_dir = get_git_common_dir(worktree)

    try:
        with ReviewLock(git_common_dir):
            try:
                state = load_state(git_common_dir)
            except SecurityPermissionError as e:
                if args.json:
                    print(json.dumps({"error": "security_permission_error", "message": str(e)}, indent=2))
                else:
                    print(f"[SECURITY ERROR] {e}", file=sys.stderr)
                return 2
            except CorruptedStateError as e:
                if args.json:
                    print(json.dumps({"error": "corrupted_state", "message": str(e)}, indent=2))
                else:
                    print(f"[ERROR] State file is corrupted: {e}", file=sys.stderr)
                return 1

            if state is None:
                if args.json:
                    print(json.dumps({"initialized": False}, indent=2))
                else:
                    print("Review helper is NOT initialized in this repository.")
                return 0

            reg_worktree = Path(state["worktree_path"]).resolve()
            worktree_match = (reg_worktree == worktree)

            instance_id = state["instance_id"]
            reviews_dir = get_instance_reviews_dir(worktree, instance_id)
            existing_numbers = scan_existing_review_numbers(reviews_dir)
            existing_max = max(existing_numbers, default=0)

            task_check = check_active_review_tasks(worktree)

            status_data = {
                "initialized": True,
                "version": state["version"],
                "instance_id": instance_id,
                "registered_worktree": str(reg_worktree),
                "current_worktree": str(worktree),
                "worktree_match": worktree_match,
                "state_counter": state["counter"],
                "disk_max_counter": existing_max,
                "next_preview_id": f"RV{max(existing_max, state['counter']) + 1:03d}",
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
                "has_active_tasks": task_check.has_active_tasks,
                "active_task_ids": task_check.active_task_ids,
                "uncertain": task_check.uncertain,
            }

            if args.json:
                print(json.dumps(status_data, indent=2, ensure_ascii=False))
            else:
                print("=== Herdr Review Helper Status ===")
                print(f"Instance ID:       {instance_id}")
                print(f"Registered Tree:   {reg_worktree} ({'MATCH' if worktree_match else 'MISMATCH'})")
                print(f"Counter (state):   {state['counter']}")
                print(f"Max Record (disk): {existing_max}")
                print(f"Next Review ID:    {status_data['next_preview_id']}")
                print(f"Active Tasks:      {len(task_check.active_task_ids)} ({task_check.active_task_ids})")
                print(f"Task Certainty:    {'UNCERTAIN' if task_check.uncertain else 'OK'}")

            return 0
    except (SecurityPermissionError, LockAcquisitionError) as e:
        if args.json:
            print(json.dumps({"error": "security_lock_error", "message": str(e)}, indent=2))
        else:
            print(f"[SECURITY ERROR] {e}", file=sys.stderr)
        return 2


def cmd_request_create(args: argparse.Namespace) -> int:
    worktree = _resolve_worktree(args.worktree)
    git_common_dir = get_git_common_dir(worktree)

    try:
        with ReviewLock(git_common_dir):
            state = load_state(git_common_dir)
            if state is None:
                print("[ERROR] Review helper is not initialized. Run 'init' first.", file=sys.stderr)
                return 1

            reg_worktree = Path(state["worktree_path"]).resolve()
            if reg_worktree != worktree:
                print(f"[ERROR] Worktree mismatch: registered={reg_worktree}, current={worktree}", file=sys.stderr)
                return 1

            inbox_dir = worktree / ".inbox"
            safe_ensure_dir(inbox_dir, 0o700, base_dir=worktree)

            target_commit = args.target_commit or _get_head_commit(worktree)
            tier = args.tier.upper()
            now_iso = datetime.now(timezone.utc).isoformat()

            content = f"""# レビュー依頼書 (Review Request)

- **Tier**: {tier}
- **対象コミット**: {target_commit}
- **依頼日時**: {now_iso}
- **Instance ID**: {state['instance_id']}

## レビュー対象・概要

{args.summary or '最新の変更内容についてのレビューを依頼します。'}

## 指示事項

- レビュー結果は `repo-review-helper record-save` で保存し、`result-submit` の `result.ref` に指定してください。
- GitHub への外部書き込み（Issue 作成やコメント投稿）は、明示的な指示がない限り行わないでください。
- 総合判定は references.md に従い「GO / 要修正 / NO-GO」で記載してください。
"""

            # Exclusive atomic file allocation using temp file + os.link
            request_path = None
            for _ in range(10):
                token = secrets.token_hex(4)
                tmp_filename = f".review-request.tmp.{os.getpid()}.{token}.md"
                tmp_path = inbox_dir / tmp_filename
                cand_filename = f"review-request.{token}.md"
                cand_path = inbox_dir / cand_filename

                fd = os.open(str(tmp_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, content.encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)

                try:
                    os.link(str(tmp_path), str(cand_path))
                    os.unlink(str(tmp_path))
                    request_path = cand_path
                    break
                except FileExistsError:
                    if tmp_path.exists():
                        os.unlink(str(tmp_path))
                    continue
                except Exception:
                    if tmp_path.exists():
                        os.unlink(str(tmp_path))
                    raise

            if request_path is None:
                print("[ERROR] Failed to allocate unique review request file", file=sys.stderr)
                return 1

            if args.output_token:
                print(request_path.name)
            else:
                print(str(request_path))
            return 0
    except (SecurityPermissionError, LockAcquisitionError) as e:
        print(f"[SECURITY ERROR] {e}", file=sys.stderr)
        return 2


def cmd_record_save(args: argparse.Namespace) -> int:
    worktree = _resolve_worktree(args.worktree)
    git_common_dir = get_git_common_dir(worktree)

    try:
        with ReviewLock(git_common_dir):
            state = load_state(git_common_dir)
            if state is None:
                print("[ERROR] Review helper is not initialized. Run 'init' first.", file=sys.stderr)
                return 1

            reg_worktree = Path(state["worktree_path"]).resolve()
            if reg_worktree != worktree:
                print(f"[ERROR] Worktree mismatch: registered={reg_worktree}, current={worktree}", file=sys.stderr)
                return 1

            if args.body_file:
                if args.body_file == "-":
                    body_content = sys.stdin.read()
                else:
                    bf = Path(args.body_file).resolve()
                    if not bf.exists():
                        print(f"[ERROR] Body file not found: {args.body_file}", file=sys.stderr)
                        return 1
                    body_content = bf.read_text(encoding="utf-8")
            elif args.body:
                body_content = args.body
            else:
                print("[ERROR] Either --body-file or --body must be specified", file=sys.stderr)
                return 1

            target_commit = args.target_commit or _get_head_commit(worktree)
            outcome = args.outcome
            task_status = args.task_status or "succeeded"
            if args.result_status:
                if args.result_status in ("GO", "要修正", "NO-GO", "CONDITIONAL GO"):
                    outcome = args.result_status
                else:
                    print(
                        f"[ERROR] --result-status '{args.result_status}' is not a valid review outcome (choices: GO, 要修正, NO-GO, CONDITIONAL GO). "
                        "Specify --outcome for review outcome and --task-status for task execution status.",
                        file=sys.stderr,
                    )
                    return 2

            if not outcome:
                print(
                    "[ERROR] --outcome is required (choices: GO, 要修正, NO-GO, CONDITIONAL GO)",
                    file=sys.stderr,
                )
                return 2

            try:
                review_id, out_path, is_new = record_save_atomic(
                    git_common_dir,
                    worktree,
                    args.task_id,
                    body_content,
                    outcome=outcome,
                    task_status=task_status,
                    target_commit=target_commit,
                )
            except TaskRecordConflictError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                return 1

            if is_new:
                print(f"Saved review record {review_id} -> {out_path}")
            else:
                print(f"Reused existing review record {review_id} -> {out_path} (idempotent)")
            return 0
    except (SecurityPermissionError, LockAcquisitionError) as e:
        print(f"[SECURITY ERROR] {e}", file=sys.stderr)
        return 2


def cmd_reindex(args: argparse.Namespace) -> int:
    worktree = _resolve_worktree(args.worktree)
    git_common_dir = get_git_common_dir(worktree)

    try:
        with ReviewLock(git_common_dir):
            instance_id = args.instance_id
            if not instance_id:
                state = load_state(git_common_dir)
                if state is None:
                    print("[ERROR] Cannot reindex: Review helper is not initialized and no --instance-id provided.", file=sys.stderr)
                    return 1
                instance_id = state["instance_id"]

            index_path = update_index(worktree, instance_id)
            print(f"Reindexed {instance_id} -> {index_path}")
            return 0
    except (SecurityPermissionError, LockAcquisitionError) as e:
        print(f"[SECURITY ERROR] {e}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-review-helper",
        description="Helper CLI for internal repository review management",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize or verify review state")
    p_init.add_argument("--worktree", help="Path to worktree (default: cwd)")
    p_init.add_argument("--abandon-old-tasks", action="store_true", help="Abandon any prior active tasks and force fresh instance initialization")
    p_init.add_argument("--force", action="store_true", help="Force reinitialization even if existing state is valid")
    p_init.set_defaults(func=cmd_init)

    # status
    p_status = subparsers.add_parser("status", help="Show review helper status")
    p_status.add_argument("--worktree", help="Path to worktree (default: cwd)")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")
    p_status.set_defaults(func=cmd_status)

    # request-create
    p_req = subparsers.add_parser("request-create", help="Create a review request file in .inbox")
    p_req.add_argument("--worktree", help="Path to worktree (default: cwd)")
    p_req.add_argument("--tier", choices=["LIGHT", "STANDARD", "DEEP"], default="STANDARD", help="Review tier")
    p_req.add_argument("--target-commit", help="Commit SHA being reviewed (default: HEAD)")
    p_req.add_argument("--summary", help="Review summary / instructions")
    p_req.add_argument("--output-token", action="store_true", help="Print only filename token")
    p_req.set_defaults(func=cmd_request_create)

    # record-save
    p_rec = subparsers.add_parser("record-save", help="Allocate ID, save review record, and update INDEX")
    p_rec.add_argument("--worktree", help="Path to worktree (default: cwd)")
    p_rec.add_argument("--task-id", required=True, help="Orchestrator Task ID (required)")
    p_rec.add_argument("--body-file", help="Path to file containing review content ('-' for stdin)")
    p_rec.add_argument("--body", help="Inline markdown review content")
    p_rec.add_argument("--outcome", choices=["GO", "要修正", "NO-GO", "CONDITIONAL GO"], default=None, help="Review outcome / verdict (required, choices: GO, 要修正, NO-GO, CONDITIONAL GO)")
    p_rec.add_argument("--task-status", choices=["succeeded", "failed"], default="succeeded", help="Orchestrator task execution status (default: succeeded)")
    p_rec.add_argument("--result-status", help="Legacy alias for outcome (e.g. GO, 要修正, NO-GO)")
    p_rec.add_argument("--target-commit", help="Reviewed commit SHA")
    p_rec.set_defaults(func=cmd_record_save)

    # reindex
    p_reindex = subparsers.add_parser("reindex", help="Rebuild INDEX.md from individual records")
    p_reindex.add_argument("--worktree", help="Path to worktree (default: cwd)")
    p_reindex.add_argument("--instance-id", help="Instance ID to reindex (default: active instance)")
    p_reindex.set_defaults(func=cmd_reindex)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
