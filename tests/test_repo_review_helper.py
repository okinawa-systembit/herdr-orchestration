"""Tests for repository review helper modules and CLI."""

from __future__ import annotations

import fcntl
import io
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
if str(REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts"))

from repo_review_helper.cli import main as cli_main
from repo_review_helper.lock import LockAcquisitionError, ReviewLock
from repo_review_helper.recorder import (
    ReviewRecordExistsError,
    TaskRecordConflictError,
    allocate_next_review_id,
    get_instance_reviews_dir,
    get_internal_reviews_root,
    parse_review_metadata,
    record_save_atomic,
    save_review_record,
    scan_existing_review_numbers,
    update_index,
)
from repo_review_helper.state import (
    SCHEMA_VERSION,
    CorruptedStateError,
    SecurityPermissionError,
    WorktreeMismatchError,
    create_initial_state,
    generate_instance_id,
    get_git_common_dir,
    get_state_dir,
    get_state_file_path,
    load_state,
    save_state,
)
from repo_review_helper.task_checker import (
    TaskCheckResult,
    check_active_review_tasks,
    fetch_task_list,
)


def _worker_acquire_lock_and_hold(git_common_dir_str: str, queue: multiprocessing.Queue):
    """Worker process: acquires lock, signals parent, waits for exit signal."""
    lock = ReviewLock(git_common_dir_str)
    lock.acquire()
    queue.put("ACQUIRED")
    # Wait until parent sends stop
    _ = queue.get()
    lock.release()


def _worker_allocate_id(git_common_dir_str: str, worktree_str: str, results_queue: multiprocessing.Queue):
    """Worker process: allocates next Review ID under lock."""
    with ReviewLock(git_common_dir_str):
        rid, _ = allocate_next_review_id(git_common_dir_str, worktree_str)
        results_queue.put(rid)


class TestReviewLock(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.common_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_lock_acquire_and_release(self):
        lock_file = self.common_dir / ReviewLock.LOCK_FILENAME
        self.assertFalse(lock_file.exists())

        with ReviewLock(self.common_dir):
            self.assertTrue(lock_file.exists())
            # Check permissions are 0600
            st = lock_file.stat()
            self.assertEqual(st.st_mode & 0o777, 0o600)

        # File should NOT be deleted after unlock
        self.assertTrue(lock_file.exists())

    def test_lock_non_blocking_conflict(self):
        lock1 = ReviewLock(self.common_dir)
        lock1.acquire()
        try:
            lock2 = ReviewLock(self.common_dir, non_blocking=True)
            with self.assertRaises(LockAcquisitionError):
                lock2.acquire()
        finally:
            lock1.release()

    def test_lock_symlink_rejection(self):
        """R64-04: Reject lock acquisition if lock file is a symlink."""
        target_file = self.common_dir / "target.txt"
        target_file.write_text("target", encoding="utf-8")
        lock_file = self.common_dir / ReviewLock.LOCK_FILENAME
        lock_file.symlink_to(target_file)

        lock = ReviewLock(self.common_dir)
        with self.assertRaises(LockAcquisitionError) as ctx:
            lock.acquire()
        self.assertIn("symlink", str(ctx.exception).lower())

    def test_lock_non_regular_file_rejection(self):
        """R64-04: Reject lock acquisition if lock file is not a regular file (e.g. directory)."""
        lock_file = self.common_dir / ReviewLock.LOCK_FILENAME
        lock_file.mkdir()

        lock = ReviewLock(self.common_dir)
        with self.assertRaises(LockAcquisitionError) as ctx:
            lock.acquire()
        self.assertTrue(
            "not a regular file" in str(ctx.exception)
            or "is a directory" in str(ctx.exception).lower()
            or "permission" in str(ctx.exception).lower()
        )

    def test_lock_released_on_process_exit(self):
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        p = ctx.Process(target=_worker_acquire_lock_and_hold, args=(str(self.common_dir), queue))
        p.start()

        # Wait until child acquires lock
        msg = queue.get(timeout=5)
        self.assertEqual(msg, "ACQUIRED")

        # In parent, non-blocking lock should fail
        lock_parent = ReviewLock(self.common_dir, non_blocking=True)
        with self.assertRaises(LockAcquisitionError):
            lock_parent.acquire()

        # Terminate child abruptly (simulating crash)
        p.terminate()
        p.join()

        # Parent should now be able to acquire lock immediately (OS auto-released flock)
        lock_parent.acquire()
        lock_parent.release()

    def test_lock_inode_mismatch_retry(self):
        """Simulate TOCTOU file replacement during open."""
        lock = ReviewLock(self.common_dir)
        original_open = os.open

        call_count = 0

        def open_with_race(path, flags, mode=0o600):
            nonlocal call_count
            fd = original_open(path, flags, mode)
            if call_count == 0 and Path(path).name == ReviewLock.LOCK_FILENAME:
                call_count += 1
                # Replace the file on disk so stat(path).st_ino differs from fstat(fd).st_ino
                os.unlink(path)
                new_fd = original_open(path, flags, mode)
                os.close(new_fd)
            return fd

        with patch("os.open", side_effect=open_with_race):
            lock.acquire()
            self.assertIsNotNone(lock.fd)
            lock.release()

        self.assertGreaterEqual(call_count, 1)

    def test_lock_permission_rejection(self):
        """N-3.1: Reject lock file if permissions are not 0600 without silently repairing."""
        lock_file = self.common_dir / ReviewLock.LOCK_FILENAME
        lock_file.write_text("dummy", encoding="utf-8")
        os.chmod(str(lock_file), 0o644)

        lock = ReviewLock(self.common_dir)
        with self.assertRaises(LockAcquisitionError) as cm:
            lock.acquire()
        self.assertIn("permissions are", str(cm.exception))

    def test_lock_symlink_rejection(self):
        """N-2: Reject lock file if it is a symlink."""
        target_file = self.common_dir / "target.lock"
        target_file.write_text("dummy", encoding="utf-8")
        os.chmod(str(target_file), 0o600)

        symlink_lock = self.common_dir / ReviewLock.LOCK_FILENAME
        symlink_lock.symlink_to(target_file)

        lock = ReviewLock(self.common_dir)
        with self.assertRaises(LockAcquisitionError) as cm:
            lock.acquire()
        self.assertIn("is a symlink", str(cm.exception))


class TestStateManagement(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.common_dir = Path(self.temp_dir.name)
        self.worktree = self.common_dir / "worktree"
        self.worktree.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initial_state_and_save_load(self):
        self.assertIsNone(load_state(self.common_dir))

        inst_id = generate_instance_id()
        self.assertTrue(inst_id.startswith("inst_"))

        st = create_initial_state(inst_id, self.worktree, counter=0)
        save_state(self.common_dir, st)

        state_file = get_state_file_path(self.common_dir)
        self.assertTrue(state_file.exists())
        self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state_file.parent.stat().st_mode & 0o777, 0o700)

        loaded = load_state(self.common_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["instance_id"], inst_id)
        self.assertEqual(loaded["counter"], 0)
        self.assertEqual(loaded["worktree_path"], str(self.worktree.resolve()))

    def test_corrupted_state_detection(self):
        state_dir = get_state_dir(self.common_dir)
        state_dir.mkdir(parents=True, mode=0o700)
        state_file = get_state_file_path(self.common_dir)

        # Invalid JSON
        state_file.write_text("{invalid json", encoding="utf-8")
        os.chmod(str(state_file), 0o600)
        with self.assertRaises(CorruptedStateError):
            load_state(self.common_dir)

        # Missing required key
        state_file.write_text(json.dumps({"version": SCHEMA_VERSION, "instance_id": "inst_123"}), encoding="utf-8")
        os.chmod(str(state_file), 0o600)
        with self.assertRaises(CorruptedStateError):
            load_state(self.common_dir)

        # Unsupported schema version (N-3.2)
        state_file.write_text(
            json.dumps({
                "version": 99,
                "instance_id": "inst_123456789012",
                "worktree_path": "/tmp",
                "counter": 0,
            }),
            encoding="utf-8",
        )
        os.chmod(str(state_file), 0o600)
        with self.assertRaises(CorruptedStateError) as cm:
            load_state(self.common_dir)
        self.assertIn("Unsupported 'version': 99", str(cm.exception))

    def test_security_permission_error_on_insecure_mode(self):
        st = create_initial_state("inst_123456789012", self.worktree, counter=0)
        save_state(self.common_dir, st)

        state_file = get_state_file_path(self.common_dir)
        # Change file permission to 0666 or 0644
        os.chmod(str(state_file), 0o666)

        with self.assertRaises(SecurityPermissionError):
            load_state(self.common_dir)

        # Fix file permission, break dir permission
        os.chmod(str(state_file), 0o600)
        os.chmod(str(state_file.parent), 0o777)

        with self.assertRaises(SecurityPermissionError):
            load_state(self.common_dir)

    def test_symlink_state_rejection(self):
        """N-2: Reject state directory or file if it is a symlink."""
        real_state_dir = self.common_dir / "real_state"
        real_state_dir.mkdir(mode=0o700)
        symlink_dir = get_state_dir(self.common_dir)
        symlink_dir.symlink_to(real_state_dir)

        with self.assertRaises(SecurityPermissionError) as cm:
            load_state(self.common_dir)
        self.assertIn("is a symlink", str(cm.exception))


class TestRecorder(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.common_dir = Path(self.temp_dir.name)
        self.worktree = self.common_dir / "worktree"
        self.worktree.mkdir()

        self.inst_id = "inst_abc123def456"
        self.state = create_initial_state(self.inst_id, self.worktree, counter=0)
        save_state(self.common_dir, self.state)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_allocation_and_numbering(self):
        with ReviewLock(self.common_dir):
            rid1, st1 = allocate_next_review_id(self.common_dir, self.worktree)
            self.assertEqual(rid1, "RV001")
            self.assertEqual(st1["counter"], 1)

            rid2, st2 = allocate_next_review_id(self.common_dir, self.worktree)
            self.assertEqual(rid2, "RV002")
            self.assertEqual(st2["counter"], 2)

    def test_allocation_worktree_mismatch(self):
        other_worktree = self.common_dir / "other_wt"
        other_worktree.mkdir()
        with ReviewLock(self.common_dir):
            with self.assertRaises(WorktreeMismatchError):
                allocate_next_review_id(self.common_dir, other_worktree)

    def test_parallel_allocation_no_collision(self):
        ctx = multiprocessing.get_context("spawn")
        results_queue = ctx.Queue()

        num_workers = 5
        processes = [
            ctx.Process(
                target=_worker_allocate_id,
                args=(str(self.common_dir), str(self.worktree), results_queue),
            )
            for _ in range(num_workers)
        ]

        for p in processes:
            p.start()
        for p in processes:
            p.join(timeout=10)

        allocated_ids = []
        while not results_queue.empty():
            allocated_ids.append(results_queue.get())

        self.assertEqual(len(allocated_ids), num_workers)
        self.assertEqual(len(set(allocated_ids)), num_workers)

        loaded = load_state(self.common_dir)
        self.assertEqual(loaded["counter"], num_workers)

    def test_save_record_and_derive_index(self):
        with ReviewLock(self.common_dir):
            rid, _ = allocate_next_review_id(self.common_dir, self.worktree)
            saved_path = save_review_record(
                self.common_dir,
                self.worktree,
                rid,
                "# Review RV001 Content\n\nAll looks good.",
                metadata={
                    "status": "GO",
                    "task_id": "task_123",
                    "target_commit": "abcdef1",
                },
            )

        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.stat().st_mode & 0o777, 0o600)

        # N-3.3: docs/reviews/internal must also be 0700
        internal_root = get_internal_reviews_root(self.worktree)
        self.assertEqual(internal_root.stat().st_mode & 0o777, 0o700)

        # Verify INDEX.md
        reviews_dir = get_instance_reviews_dir(self.worktree, self.inst_id)
        index_file = reviews_dir / "INDEX.md"
        self.assertTrue(index_file.exists())
        self.assertEqual(index_file.stat().st_mode & 0o777, 0o600)

        index_text = index_file.read_text(encoding="utf-8")
        self.assertIn("RV001", index_text)
        self.assertIn("task_123", index_text)
        self.assertIn("abcdef1", index_text)
        self.assertIn("GO", index_text)

    def test_record_save_atomic_idempotent(self):
        """N-1 & M-1: Re-recording the same task_id with same content reuses review_id without creating a new file, even with bullets in body."""
        # Case (a) & (b): Body contains bullets with status and quotes previous task ID
        body = "## Summary\n- **総合判定**: GO\n- **Task ID**: task_prev_quoted\n- **Status**: GO\nAll approved."
        with ReviewLock(self.common_dir):
            # First save with result_status="succeeded"
            rid1, path1, is_new1 = record_save_atomic(
                self.common_dir,
                self.worktree,
                "task_idempotent_1",
                body,
                result_status="succeeded",
                target_commit="commit1",
            )
            self.assertEqual(rid1, "RV001")
            self.assertTrue(is_new1)

            # Metadata must reflect frontmatter, not body bullets
            meta = parse_review_metadata(path1)
            self.assertEqual(meta["task_id"], "task_idempotent_1")
            self.assertEqual(meta["status"], "succeeded")

            # Second save: same task_id, same content
            rid2, path2, is_new2 = record_save_atomic(
                self.common_dir,
                self.worktree,
                "task_idempotent_1",
                body,
                result_status="succeeded",
                target_commit="commit1",
            )
            self.assertEqual(rid2, "RV001")
            self.assertEqual(path1, path2)
            self.assertFalse(is_new2)

            # Counter should remain 1
            st = load_state(self.common_dir)
            self.assertEqual(st["counter"], 1)

    def test_record_save_atomic_conflict(self):
        """N-1: Re-recording the same task_id with different content raises TaskRecordConflictError."""
        with ReviewLock(self.common_dir):
            record_save_atomic(
                self.common_dir,
                self.worktree,
                "task_conflict_1",
                "Body 1",
                result_status="GO",
            )

            with self.assertRaises(TaskRecordConflictError):
                record_save_atomic(
                    self.common_dir,
                    self.worktree,
                    "task_conflict_1",
                    "Different Body 2",
                    result_status="GO",
                )

    def test_record_save_atomic_conflict_commit(self):
        """R64-02: Conflicting target_commit raises TaskRecordConflictError."""
        with ReviewLock(self.common_dir):
            record_save_atomic(
                self.common_dir,
                self.worktree,
                "task_commit_conflict",
                "Review body text",
                outcome="GO",
                target_commit="commit_aaa",
            )

            with self.assertRaises(TaskRecordConflictError) as ctx:
                record_save_atomic(
                    self.common_dir,
                    self.worktree,
                    "task_commit_conflict",
                    "Review body text",
                    outcome="GO",
                    target_commit="commit_bbb",
                )
            self.assertIn("target_commit mismatch", str(ctx.exception))

    def test_record_save_atomic_conflict_outcome(self):
        """R64-02: Conflicting outcome raises TaskRecordConflictError."""
        with ReviewLock(self.common_dir):
            record_save_atomic(
                self.common_dir,
                self.worktree,
                "task_outcome_conflict",
                "Review body text",
                outcome="GO",
                target_commit="commit_aaa",
            )

            with self.assertRaises(TaskRecordConflictError) as ctx:
                record_save_atomic(
                    self.common_dir,
                    self.worktree,
                    "task_outcome_conflict",
                    "Review body text",
                    outcome="要修正",
                    target_commit="commit_aaa",
                )
            self.assertIn("outcome mismatch", str(ctx.exception))

    def test_record_save_atomic_conflict_body_whitespace(self):
        """R64-02: Byte-level discrepancy in body content raises TaskRecordConflictError."""
        with ReviewLock(self.common_dir):
            record_save_atomic(
                self.common_dir,
                self.worktree,
                "task_ws_conflict",
                "Review body line\n",
                outcome="GO",
                target_commit="commit_aaa",
            )

            with self.assertRaises(TaskRecordConflictError) as ctx:
                record_save_atomic(
                    self.common_dir,
                    self.worktree,
                    "task_ws_conflict",
                    "Review body line  \n",
                    outcome="GO",
                    target_commit="commit_aaa",
                )
            self.assertIn("body content mismatch", str(ctx.exception))

    def test_save_review_record_prevent_overwrite(self):
        """R64-05: save_review_record uses atomic link and raises ReviewRecordExistsError if destination exists."""
        with ReviewLock(self.common_dir):
            rid = "RV001"
            path1 = save_review_record(
                self.common_dir,
                self.worktree,
                rid,
                "Initial review content",
                metadata={"status": "GO"},
            )
            self.assertTrue(path1.exists())
            self.assertEqual(path1.read_text(encoding="utf-8").strip().splitlines()[-1], "Initial review content")

            with self.assertRaises(ReviewRecordExistsError):
                save_review_record(
                    self.common_dir,
                    self.worktree,
                    rid,
                    "Overwriting content attempt",
                    metadata={"status": "GO"},
                )

            # Target file content must be unchanged
            self.assertEqual(path1.read_text(encoding="utf-8").strip().splitlines()[-1], "Initial review content")

            # No dangling tmp files in reviews directory
            reviews_dir = get_instance_reviews_dir(self.worktree, self.inst_id)
            tmp_files = list(reviews_dir.glob(".tmp.*"))
            self.assertEqual(len(tmp_files), 0)

    def test_skipped_number_tolerance(self):
        """Simulate crash after allocation: counter incremented but record not saved."""
        with ReviewLock(self.common_dir):
            rid1, _ = allocate_next_review_id(self.common_dir, self.worktree)
            self.assertEqual(rid1, "RV001")

            rid2, _ = allocate_next_review_id(self.common_dir, self.worktree)
            self.assertEqual(rid2, "RV002")

            saved_path = save_review_record(
                self.common_dir,
                self.worktree,
                rid2,
                "Content of RV002",
                metadata={"status": "GO"},
            )
            self.assertTrue(saved_path.exists())

        reviews_dir = get_instance_reviews_dir(self.worktree, self.inst_id)
        existing = scan_existing_review_numbers(reviews_dir)
        self.assertEqual(existing, [2])

        with ReviewLock(self.common_dir):
            rid3, _ = allocate_next_review_id(self.common_dir, self.worktree)
            self.assertEqual(rid3, "RV003")

    def test_record_save_atomic_content_starting_with_triple_dash(self):
        """X-2: Content starting with '---' receives helper frontmatter and supports idempotent replay."""
        body = "---\ntitle: Nested Frontmatter\n---\nActual content line."
        with ReviewLock(self.common_dir):
            rid1, path1, is_new1 = record_save_atomic(
                self.common_dir,
                self.worktree,
                "task_triple_dash",
                body,
                outcome="GO",
                task_status="succeeded",
                target_commit="commit_dash",
            )
            self.assertTrue(is_new1)
            self.assertTrue(path1.exists())

            meta = parse_review_metadata(path1)
            self.assertEqual(meta["task_id"], "task_triple_dash")
            self.assertEqual(meta["outcome"], "GO")

            # Re-saving same content must be idempotent
            rid2, path2, is_new2 = record_save_atomic(
                self.common_dir,
                self.worktree,
                "task_triple_dash",
                body,
                outcome="GO",
                task_status="succeeded",
                target_commit="commit_dash",
            )
            self.assertFalse(is_new2)
            self.assertEqual(rid1, rid2)
            self.assertEqual(path1, path2)


class TestTaskChecker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("repo_review_helper.task_checker.fetch_task_list")
    @patch("repo_review_helper.task_checker.get_or_create_repository_identity")
    def test_no_active_tasks(self, mock_identity, mock_fetch):
        mock_identity.return_value = "repo-id-123"
        mock_fetch.return_value = {
            "complete": True,
            "truncated": False,
            "tasks": [
                {
                    "task": {"id": "t1", "type": "review", "status": "succeeded"},
                    "result": {"status": "succeeded"},
                    "overview": {"state": "closed"},
                }
            ],
        }

        res = check_active_review_tasks(self.worktree)
        self.assertTrue(res.can_safely_initialize)
        self.assertFalse(res.has_active_tasks)
        self.assertFalse(res.uncertain)

    @patch("repo_review_helper.task_checker.fetch_task_list")
    @patch("repo_review_helper.task_checker.get_or_create_repository_identity")
    def test_active_task_detected(self, mock_identity, mock_fetch):
        mock_identity.return_value = "repo-id-123"
        mock_fetch.return_value = {
            "complete": True,
            "truncated": False,
            "tasks": [
                {
                    "task": {"id": "task_running", "type": "review", "status": "in_progress"},
                    "result": {"status": "pending"},
                    "overview": {"state": "open"},
                }
            ],
        }

        res = check_active_review_tasks(self.worktree)
        self.assertFalse(res.can_safely_initialize)
        self.assertTrue(res.has_active_tasks)
        self.assertEqual(res.active_task_ids, ["task_running"])

    @patch("repo_review_helper.task_checker.fetch_task_list")
    @patch("repo_review_helper.task_checker.get_or_create_repository_identity")
    def test_uncertain_truncated(self, mock_identity, mock_fetch):
        mock_identity.return_value = "repo-id-123"
        mock_fetch.return_value = {
            "complete": True,
            "truncated": True,
            "tasks": [],
        }

        res = check_active_review_tasks(self.worktree)
        self.assertFalse(res.can_safely_initialize)
        self.assertTrue(res.uncertain)

    @patch("repo_review_helper.task_checker.fetch_task_list")
    @patch("repo_review_helper.task_checker.get_or_create_repository_identity")
    def test_inconsistent_state_combination_warning(self, mock_identity, mock_fetch):
        """R64-06: Task with inconsistent_state_combination warning triggers safety stop even if state is closed."""
        mock_identity.return_value = "repo-id-123"
        mock_fetch.return_value = {
            "complete": True,
            "truncated": False,
            "tasks": [
                {
                    "task": {"id": "task_inconsistent", "type": "review", "status": "succeeded"},
                    "result": {"status": "succeeded"},
                    "overview": {
                        "state": "closed",
                        "warnings": ["inconsistent_state_combination"],
                    },
                }
            ],
        }

        res = check_active_review_tasks(self.worktree)
        self.assertFalse(res.can_safely_initialize)
        self.assertTrue(res.uncertain)
        self.assertIn("task_inconsistent", res.active_task_ids)
        self.assertIn("inconsistent state combination", res.message)

    @patch("repo_review_helper.task_checker.run_task_list")
    def test_fetch_task_list_passes_limit_1000(self, mock_run_task_list):
        """R64-01: fetch_task_list specifies limit=1000 to prevent truncated list under 1000 tasks."""
        mock_run_task_list.return_value = ({"complete": True, "truncated": False, "tasks": []}, 0)

        data = fetch_task_list(self.worktree, "repo-id-123")
        self.assertTrue(data.get("complete"))
        mock_run_task_list.assert_called_once_with(
            repository="repo-id-123",
            type="review",
            limit=1000,
        )

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_fetch_task_list_cli_passes_limit_1000(self, mock_which, mock_subproc):
        """R64-01: fetch_task_list via CLI passes '--limit 1000' argument."""
        mock_which.return_value = "/usr/local/bin/herdr-orchestrator"
        mock_subproc.return_value = MagicMock(
            stdout=json.dumps({"complete": True, "truncated": False, "tasks": []}),
            returncode=0,
        )

        data = fetch_task_list(self.worktree, "repo-id-123", use_cli=True)
        self.assertTrue(data.get("complete"))
        self.assertTrue(mock_subproc.called)
        args, kwargs = mock_subproc.call_args
        cmd = args[0]
        self.assertIn("--limit", cmd)
        limit_idx = cmd.index("--limit")
        self.assertEqual(cmd[limit_idx + 1], "1000")


class TestCliIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.temp_dir.name)
        subprocess.run(["git", "init"], cwd=str(self.worktree), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(self.worktree), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.worktree), check=True)

        readme = self.worktree / "README.md"
        readme.write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self.worktree), check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(self.worktree), check=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("repo_review_helper.cli.check_active_review_tasks")
    def test_cli_init_status_request_record_reindex(self, mock_check):
        mock_check.return_value = TaskCheckResult(
            can_safely_initialize=True,
            has_active_tasks=False,
            uncertain=False,
        )

        wt_str = str(self.worktree)

        # 1. status before init
        code = cli_main(["status", "--worktree", wt_str, "--json"])
        self.assertEqual(code, 0)

        # 2. init
        code = cli_main(["init", "--worktree", wt_str])
        self.assertEqual(code, 0)

        # 3. status after init
        code = cli_main(["status", "--worktree", wt_str, "--json"])
        self.assertEqual(code, 0)

        # 4. request-create (B-1 check)
        code = cli_main([
            "request-create",
            "--worktree", wt_str,
            "--tier", "STANDARD",
            "--summary", "Test request",
        ])
        self.assertEqual(code, 0)
        inbox_dir = self.worktree / ".inbox"
        self.assertTrue(inbox_dir.exists())
        req_files = list(inbox_dir.glob("review-request.*.md"))
        self.assertEqual(len(req_files), 1)

        req_text = req_files[0].read_text(encoding="utf-8")
        # B-1: Must NOT instruct creation of GitHub Finding Issues
        self.assertNotIn("GitHub Finding Issue を作成", req_text)
        # B-1: Assessment category must be GO / 要修正 / NO-GO per references.md
        self.assertIn("GO / 要修正 / NO-GO", req_text)

        # 5. record-save (N-1 idempotency check)
        code = cli_main([
            "record-save",
            "--worktree", wt_str,
            "--task-id", "task_abc",
            "--body", "## Review Summary\n\nApproved.",
            "--result-status", "GO",
        ])
        self.assertEqual(code, 0)

        # Re-run identical record-save: should be idempotent and exit 0
        code = cli_main([
            "record-save",
            "--worktree", wt_str,
            "--task-id", "task_abc",
            "--body", "## Review Summary\n\nApproved.",
            "--result-status", "GO",
        ])
        self.assertEqual(code, 0)

        state = load_state(get_git_common_dir(self.worktree))
        self.assertIsNotNone(state)
        inst_dir = get_instance_reviews_dir(self.worktree, state["instance_id"])
        rv1 = inst_dir / "RV001.md"
        self.assertTrue(rv1.exists())
        # RV002 should NOT exist
        rv2 = inst_dir / "RV002.md"
        self.assertFalse(rv2.exists())

        # 6. reindex
        code = cli_main(["reindex", "--worktree", wt_str])
        self.assertEqual(code, 0)
        index_file = inst_dir / "INDEX.md"
        self.assertTrue(index_file.exists())
        self.assertIn("RV001", index_file.read_text(encoding="utf-8"))

    @patch("repo_review_helper.cli.check_active_review_tasks")
    def test_cli_record_save_stdin(self, mock_check):
        """N-5: Support reading body content from stdin via '--body-file -'."""
        mock_check.return_value = TaskCheckResult(
            can_safely_initialize=True,
            has_active_tasks=False,
            uncertain=False,
        )
        wt_str = str(self.worktree)
        cli_main(["init", "--worktree", wt_str])

        with patch("sys.stdin", io.StringIO("## Stdin Review\nPassed")):
            code = cli_main([
                "record-save",
                "--worktree", wt_str,
                "--task-id", "task_stdin",
                "--body-file", "-",
                "--result-status", "GO",
            ])
            self.assertEqual(code, 0)

        state = load_state(get_git_common_dir(self.worktree))
        inst_dir = get_instance_reviews_dir(self.worktree, state["instance_id"])
        rv1 = inst_dir / "RV001.md"
        self.assertIn("Stdin Review", rv1.read_text(encoding="utf-8"))

    def test_symlink_inbox_rejection_cli(self):
        """N-2: Reject operation if .inbox is a symlink pointing outside worktree."""
        outside_dir = Path(tempfile.mkdtemp())
        try:
            os.chmod(str(outside_dir), 0o755)
            inbox_symlink = self.worktree / ".inbox"
            inbox_symlink.symlink_to(outside_dir)

            wt_str = str(self.worktree)
            # init should fail with SecurityPermissionError (exit 2)
            code = cli_main(["init", "--worktree", wt_str])
            self.assertEqual(code, 2)
            # outside_dir permissions must not be changed
            self.assertEqual(outside_dir.stat().st_mode & 0o777, 0o755)
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    @patch("repo_review_helper.cli.check_active_review_tasks")
    def test_cli_init_blocked_when_active_tasks_without_abandon(self, mock_check):
        mock_check.return_value = TaskCheckResult(
            can_safely_initialize=False,
            has_active_tasks=True,
            uncertain=False,
            active_task_ids=["task_old_1"],
            message="Old task running",
        )
        wt_str = str(self.worktree)

        # Init without --abandon-old-tasks should fail
        code = cli_main(["init", "--worktree", wt_str])
        self.assertEqual(code, 1)

        # Init with --abandon-old-tasks should succeed
        code = cli_main(["init", "--worktree", wt_str, "--abandon-old-tasks"])
        self.assertEqual(code, 0)

    def test_status_lock_permission_error_exit_2(self):
        """M-3: cmd_status exits with code 2 on LockAcquisitionError instead of traceback."""
        wt_str = str(self.worktree)
        git_common_dir = get_git_common_dir(self.worktree)
        lock_file = git_common_dir / ReviewLock.LOCK_FILENAME
        lock_file.write_text("dummy", encoding="utf-8")
        os.chmod(str(lock_file), 0o644)

        code = cli_main(["status", "--worktree", wt_str])
        self.assertEqual(code, 2)

        code_json = cli_main(["status", "--worktree", wt_str, "--json"])
        self.assertEqual(code_json, 2)

    @patch("repo_review_helper.cli.check_active_review_tasks")
    def test_cli_record_save_outcome_and_task_status(self, mock_check):
        """R64-03: Separation of review outcome (--outcome) and task status (--task-status)."""
        mock_check.return_value = TaskCheckResult(
            can_safely_initialize=True,
            has_active_tasks=False,
            uncertain=False,
        )
        wt_str = str(self.worktree)
        cli_main(["init", "--worktree", wt_str])

        code = cli_main([
            "record-save",
            "--worktree", wt_str,
            "--task-id", "task_r64_03",
            "--body", "## Review Summary\n\nChanges requested.",
            "--outcome", "要修正",
            "--task-status", "succeeded",
            "--target-commit", "fedcba9876",
        ])
        self.assertEqual(code, 0)

        state = load_state(get_git_common_dir(self.worktree))
        inst_dir = get_instance_reviews_dir(self.worktree, state["instance_id"])
        rv1 = inst_dir / "RV001.md"
        self.assertTrue(rv1.exists())

        meta = parse_review_metadata(rv1)
        self.assertEqual(meta["outcome"], "要修正")
        self.assertEqual(meta["status"], "succeeded")
        self.assertEqual(meta["target_commit"], "fedcba9876")

        index_file = inst_dir / "INDEX.md"
        self.assertTrue(index_file.exists())
        index_text = index_file.read_text(encoding="utf-8")
        self.assertIn("要修正", index_text)
        self.assertIn("fedcba9876", index_text)

    @patch("repo_review_helper.cli.check_active_review_tasks")
    def test_cli_record_save_outcome_required_exit_2(self, mock_check):
        """X-1: Omitting --outcome or providing invalid outcome via legacy --result-status exits 2."""
        mock_check.return_value = TaskCheckResult(
            can_safely_initialize=True,
            has_active_tasks=False,
            uncertain=False,
        )
        wt_str = str(self.worktree)
        cli_main(["init", "--worktree", wt_str])

        # 1. Omitting --outcome
        code1 = cli_main([
            "record-save",
            "--worktree", wt_str,
            "--task-id", "task_missing_outcome",
            "--body", "Review content",
        ])
        self.assertEqual(code1, 2)

        # 2. Specifying legacy --result-status succeeded (which is not an outcome)
        code2 = cli_main([
            "record-save",
            "--worktree", wt_str,
            "--task-id", "task_legacy_succeeded",
            "--body", "Review content",
            "--result-status", "succeeded",
        ])
        self.assertEqual(code2, 2)


if __name__ == "__main__":
    unittest.main()
