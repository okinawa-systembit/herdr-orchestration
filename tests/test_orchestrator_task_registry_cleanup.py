"""
Tests for Task Registry Cleanup Mechanism (Issue #157 / RC-01 - RC-10).
Conforms to docs/herdr-orchestrator-task-registry-cleanup.md §8.2.
"""
from __future__ import annotations

import contextlib
import fcntl
import io as py_io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from orchestrator.paths import tasks_dir
from orchestrator.registry import io as registry_io
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.errors import RegistryError
from orchestrator.result_commands import result_submit
from orchestrator.dispatch_commands import run_doctor

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"


@contextlib.contextmanager
def _env_patch(env_vars: dict[str, str]):
    old_env = {}
    for k, v in env_vars.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _sample_create_payload(*, mode: str = "sync", repo_id: str = "github.com/org/repo") -> dict:
    return {
        "task": {
            "type": "review",
            "mode": mode,
            "instruction": "review changes",
        },
        "context": {
            "repository": {"identity": repo_id},
            "worktree": {
                "path": "/tmp/herdr-orchestrator-fixture",
                "branch": "main",
                "commit": None,
            },
        },
        "requester": {
            "role": "design",
            "agent_name": "org-repo-design",
            "pane_id": "w1:p1",
        },
        "worker": {
            "role": "reviewer",
            "agent_name": "org-repo-reviewer",
            "pane_id": "w1:p2",
        },
        "completion": {"action": "none" if mode == "sync" else "resume_requester"},
    }


class TaskRegistryCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_home = Path(self.tmp.name)
        self.env = {
            "XDG_STATE_HOME": str(self.state_home),
            "HERDR_ENV": "1",
            "HERDR_PANE_ID": "w1:p2",
            "HERDR_WORKSPACE_ID": "w1",
            "HERDR_TAB_ID": "w1:t1",
        }
        # Ensure fixture worktree exists
        self.fixture_dir = Path("/tmp/herdr-orchestrator-fixture")
        self.fixture_dir.mkdir(parents=True, exist_ok=True)
        (self.fixture_dir / "out.md").write_text("ok\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _setup_terminal_task(
        self,
        now: datetime,
        days_ago: int = 40,
        repo_id: str = "github.com/org/repo",
        *,
        task_status: str = "succeeded",
        completion_status: str = "sent",
    ) -> tuple[str, Path]:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(mode="async", repo_id=repo_id))
            task_id = doc["task"]["id"]
            path = tasks_dir() / f"{task_id}.json"
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="out.md",
                summary="done",
            )
            registry_ops.claim_completion(task_id)
            if completion_status == "sent":
                registry_ops.finish_completion(task_id, outcome="sent")
            elif completion_status == "skipped":
                registry_ops.finish_completion(task_id, outcome="skipped")
            elif completion_status == "failed":
                registry_ops.finish_completion(task_id, outcome="failed")
            # If need custom task_status or completion_status
            raw = json.loads(path.read_text(encoding="utf-8"))
            if task_status != "succeeded":
                raw["task"]["status"] = task_status
            if completion_status not in ("sent", "skipped", "failed"):
                raw["completion"]["status"] = completion_status
            old_time = (now - timedelta(days=days_ago)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = old_time.isoformat()
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            return task_id, path

    # -------------------------------------------------------------------------
    # RC-01: 2-stage predicate, ineligible task & corruption protection
    # -------------------------------------------------------------------------
    def test_rc01_two_stage_predicate_skips_ineligible_and_corrupt(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            # 1. Young terminal task (10 days old)
            id_young, path_young = self._setup_terminal_task(now, days_ago=10)
            # 2. Old non-terminal task.status (in_progress)
            id_in_prog, path_in_prog = self._setup_terminal_task(now, days_ago=40, task_status="in_progress")
            # 3. Old non-terminal completion.status (pending)
            id_comp_pend, path_comp_pend = self._setup_terminal_task(now, days_ago=40, completion_status="pending")
            # 4. Old non-terminal completion.status (processing)
            id_comp_proc, path_comp_proc = self._setup_terminal_task(now, days_ago=40, completion_status="processing")
            # 5. Corrupted JSON file
            path_corrupt = tasks_dir() / "task_corrupted.json"
            path_corrupt.write_text("{broken json invalid syntax", encoding="utf-8")
            # 6. JSON with missing required keys
            path_bad_keys = tasks_dir() / "task_bad_keys.json"
            path_bad_keys.write_text(json.dumps({"some_key": "val"}), encoding="utf-8")

            # Remove .json.lock files to verify Phase 1 does not inadvertently create them
            for tid in (id_young, id_in_prog, id_comp_pend, id_comp_proc):
                (tasks_dir() / f"{tid}.json.lock").unlink(missing_ok=True)

            locked_task_ids: list[str] = []
            real_lock = registry_io.task_mutation_lock

            @contextlib.contextmanager
            def spy_task_mutation_lock(task_id: str):
                locked_task_ids.append(task_id)
                with real_lock(task_id):
                    yield

            with patch.object(registry_io, "task_mutation_lock", side_effect=spy_task_mutation_lock):
                # Execute cleanup
                res = registry_ops.cleanup_tasks(now=now, ignore_rate_limit=True)

            self.assertEqual(res["deleted"], [])
            self.assertEqual(res["deleted_count"], 0)
            self.assertTrue(path_young.exists())
            self.assertTrue(path_in_prog.exists())
            self.assertTrue(path_comp_pend.exists())
            self.assertTrue(path_comp_proc.exists())
            self.assertTrue(path_corrupt.exists())
            self.assertTrue(path_bad_keys.exists())

            # Verification of Phase 1 lock-free filtering: none of the ineligible tasks should acquire lock
            self.assertEqual(locked_task_ids, [], "Phase 1 must filter out ineligible tasks without acquiring lock")
            # Verification that no .json.lock files were created for un-locked tasks
            for tid in (id_young, id_in_prog, id_comp_pend, id_comp_proc):
                self.assertFalse(
                    (tasks_dir() / f"{tid}.json.lock").exists(),
                    f"{tid}.json.lock must not be created by cleanup check",
                )

    # -------------------------------------------------------------------------
    # RC-02: .json.lock complete unlinking
    # -------------------------------------------------------------------------
    def test_rc02_json_lock_completely_unlinked_after_delete(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            task_id, path = self._setup_terminal_task(now, days_ago=35)
            json_lock = tasks_dir() / f"{task_id}.json.lock"
            handoff_lock = tasks_dir() / f"{task_id}.handoff.lock"

            # Ensure lock files exist
            json_lock.touch()
            handoff_lock.touch()
            self.assertTrue(path.exists())
            self.assertTrue(json_lock.exists())
            self.assertTrue(handoff_lock.exists())

            # Test _try_delete_task_if_eligible directly (isolated from _sweep_orphan_locks)
            cutoff = now - timedelta(days=30)
            deleted = registry_ops._try_delete_task_if_eligible(task_id, cutoff)
            self.assertTrue(deleted, "_try_delete_task_if_eligible must return True for eligible task")
            self.assertFalse(path.exists(), "task JSON must be deleted")
            self.assertFalse(json_lock.exists(), "task_*.json.lock must be deleted immediately after lock release")
            self.assertFalse(handoff_lock.exists(), "task_*.handoff.lock must be completely unlinked")

    # -------------------------------------------------------------------------
    # RC-03: Orphan lock files sweep
    # -------------------------------------------------------------------------
    def test_rc03_sweep_orphan_locks(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            root = registry_io.ensure_registry_layout()
            # Create orphan lock files (no corresponding task JSON)
            orphan_json_lock = root / "task_00000000-0000-4000-8000-000000000001.json.lock"
            orphan_handoff_lock = root / "task_00000000-0000-4000-8000-000000000002.handoff.lock"
            orphan_json_lock.touch()
            orphan_handoff_lock.touch()

            # Create an active locked file held by flock
            active_locked = root / "task_00000000-0000-4000-8000-000000000003.json.lock"
            active_locked.touch()
            fd = os.open(str(active_locked), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                res = registry_ops.cleanup_tasks(now=now, ignore_rate_limit=True)
                self.assertEqual(res["orphan_locks_deleted"], 2)
                self.assertFalse(orphan_json_lock.exists())
                self.assertFalse(orphan_handoff_lock.exists())
                # Active locked file must NOT be deleted
                self.assertTrue(active_locked.exists())
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    # -------------------------------------------------------------------------
    # RC-04: result-submit lazy cleanup
    # -------------------------------------------------------------------------
    @patch("orchestrator.result_commands.verify_worker_submit_context")
    def test_rc04_result_submit_triggers_lazy_cleanup(self, _mock_verify) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            # Setup an old terminal task to be cleaned up
            old_id, old_path = self._setup_terminal_task(now, days_ago=45)

            # Create a new active task for result-submit
            doc = registry_ops.create_task(_sample_create_payload(mode="sync"))
            cur_id = doc["task"]["id"]
            registry_ops.record_prompt_start(cur_id)
            registry_ops.mark_dispatch(cur_id, outcome="sent")

            # Call result_submit
            res = result_submit(
                cur_id,
                result_status="succeeded",
                result_ref="out.md",
                summary="job done",
            )
            self.assertEqual(res["result"]["status"], "succeeded")
            self.assertFalse(res.get("replay", False))

            # Verify that old task was deleted and stamp recorded
            self.assertFalse(old_path.exists())
            stamp = registry_io.read_cleanup_stamp(tasks_dir())
            self.assertIsNotNone(stamp)

    # -------------------------------------------------------------------------
    # RC-05: Rate limit and replay skip
    # -------------------------------------------------------------------------
    @patch("orchestrator.result_commands.verify_worker_submit_context")
    def test_rc05_rate_limit_and_replay_skip(self, _mock_verify) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            old_id, old_path = self._setup_terminal_task(now, days_ago=45)

            # Set stamp to 1 hour ago
            recent_stamp = (now - timedelta(hours=1)).replace(microsecond=0).isoformat()
            registry_io.write_cleanup_stamp(tasks_dir(), recent_stamp)

            # Lazy cleanup via cleanup_tasks with ignore_rate_limit=False
            res = registry_ops.cleanup_tasks(now=now, ignore_rate_limit=False)
            self.assertTrue(res["skipped_rate_limit"])
            self.assertTrue(old_path.exists())

            # Test replay: true in result_submit
            doc = registry_ops.create_task(_sample_create_payload(mode="sync"))
            tid = doc["task"]["id"]
            registry_ops.record_prompt_start(tid)
            registry_ops.mark_dispatch(tid, outcome="sent")
            result_submit(tid, result_status="succeeded", result_ref="out.md", summary="s1")

            # Monkey-patch cleanup_tasks to ensure it is NOT called during replay
            with patch.object(registry_ops, "cleanup_tasks") as mock_cleanup:
                replay_res = result_submit(tid, result_status="succeeded", result_ref="out.md", summary="s1")
                self.assertTrue(replay_res.get("idempotent_replay", False))
                mock_cleanup.assert_not_called()

    # -------------------------------------------------------------------------
    # RC-06: result-submit error isolation
    # -------------------------------------------------------------------------
    @patch("orchestrator.result_commands.verify_worker_submit_context")
    def test_rc06_result_submit_error_isolation(self, _mock_verify) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(mode="sync"))
            cur_id = doc["task"]["id"]
            registry_ops.record_prompt_start(cur_id)
            registry_ops.mark_dispatch(cur_id, outcome="sent")

            # Patch cleanup_tasks to raise an exception
            with patch.object(registry_ops, "cleanup_tasks", side_effect=RuntimeError("disk full simulated")):
                stderr_capture = py_io.StringIO()
                with patch("sys.stderr", stderr_capture):
                    res = result_submit(
                        cur_id,
                        result_status="succeeded",
                        result_ref="out.md",
                        summary="still ok",
                    )
                self.assertEqual(res["result"]["status"], "succeeded")
                err_output = stderr_capture.getvalue()
                self.assertIn("warning: lazy registry cleanup failed:", err_output)
                self.assertIn("disk full simulated", err_output)

    # -------------------------------------------------------------------------
    # RC-07: doctor Read-Only visualization
    # -------------------------------------------------------------------------
    def test_rc07_doctor_read_only_visualization(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            # Create an old eligible task and an orphan lock
            old_id, _ = self._setup_terminal_task(now, days_ago=40, repo_id="my-repo-uuid")
            orphan_lock = tasks_dir() / "task_11111111-2222-4333-8444-555555555555.json.lock"
            orphan_lock.touch()

            payload, code = run_doctor()
            self.assertEqual(code, 0)
            self.assertIn("registry_cleanup", payload)
            rc = payload["registry_cleanup"]
            self.assertEqual(rc["shared_across_repositories"], True)
            self.assertEqual(rc["retention_days"], 30)
            self.assertGreaterEqual(rc["total_tasks"], 1)
            self.assertGreaterEqual(rc["eligible_count"], 1)
            self.assertIn("my-repo-uuid", rc["eligible_by_repository"])
            self.assertGreaterEqual(rc["orphan_lock_files_count"], 1)

            # Verify files still exist (read-only)
            self.assertTrue(orphan_lock.exists())
            self.assertTrue((tasks_dir() / f"{old_id}.json").exists())

    # -------------------------------------------------------------------------
    # RC-08: cleanup-registry --dry-run
    # -------------------------------------------------------------------------
    def test_rc08_cleanup_registry_dry_run(self) -> None:
        now = datetime.now(timezone.utc)
        # HERDR_ENV not set, run as external terminal
        ext_env = {
            "XDG_STATE_HOME": str(self.state_home),
            "PATH": os.environ["PATH"],
        }
        with _env_patch(self.env):
            old_id, old_path = self._setup_terminal_task(now, days_ago=40, repo_id="dry-run-repo")
            orphan_lock = tasks_dir() / "task_22222222-3333-4444-8555-666666666666.json.lock"
            orphan_lock.touch()

        # Run herdr-orchestrator cleanup-registry --dry-run
        proc = subprocess.run(
            [sys.executable, str(ORCHESTRATOR_CLI), "cleanup-registry", "--dry-run"],
            env=ext_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertEqual(data["command"], "cleanup-registry")
        self.assertEqual(data["scope"], "global")
        self.assertTrue(data["shared_across_repositories"])
        self.assertTrue(data["dry_run"])
        self.assertIn(old_id, data["deleted"])
        self.assertGreaterEqual(data["deleted_count"], 1)
        self.assertIn("dry-run-repo", data["deleted_by_repository"])
        self.assertGreaterEqual(data["orphan_locks_deleted"], 1)

        # Confirm nothing was unlinked
        self.assertTrue(old_path.exists())
        self.assertTrue(orphan_lock.exists())
        stamp = registry_io.read_cleanup_stamp(tasks_dir())
        self.assertIsNone(stamp)

    # -------------------------------------------------------------------------
    # RC-09: cleanup-registry real deletion bypasses rate limit
    # -------------------------------------------------------------------------
    def test_rc09_cleanup_registry_real_deletion_bypasses_rate_limit(self) -> None:
        now = datetime.now(timezone.utc)
        ext_env = {
            "XDG_STATE_HOME": str(self.state_home),
            "PATH": os.environ["PATH"],
        }
        with _env_patch(self.env):
            old_id, old_path = self._setup_terminal_task(now, days_ago=50, repo_id="real-clean-repo")
            # Write stamp from 10 minutes ago
            recent_stamp = (now - timedelta(minutes=10)).replace(microsecond=0).isoformat()
            registry_io.write_cleanup_stamp(tasks_dir(), recent_stamp)

        proc = subprocess.run(
            [sys.executable, str(ORCHESTRATOR_CLI), "cleanup-registry"],
            env=ext_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertFalse(data["dry_run"])
        self.assertIn(old_id, data["deleted"])
        self.assertFalse(old_path.exists(), "Old task must be deleted even within 24h of last cleanup")
        new_stamp = registry_io.read_cleanup_stamp(tasks_dir())
        self.assertNotEqual(new_stamp, recent_stamp, "Stamp must be updated")

    # -------------------------------------------------------------------------
    # RC-10: Non-blocking contention avoidance
    # -------------------------------------------------------------------------
    def test_rc10_nonblocking_avoids_contention(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            root = registry_io.ensure_registry_layout()
            run_lock_path = root / "cleanup.run.lock"
            run_lock_path.touch()

            # Hold exclusive lock on cleanup.run.lock in separate fd
            fd = os.open(str(run_lock_path), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                # Call cleanup_tasks with nonblocking=True
                res = registry_ops.cleanup_tasks(now=now, nonblocking=True)
                self.assertTrue(res["skipped_lock"], "Must immediately return with skipped_lock=True")
                self.assertEqual(res["deleted"], [])
                self.assertEqual(res["deleted_count"], 0)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
