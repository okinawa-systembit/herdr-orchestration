"""
Phase-2 registry tests vs docs §18.3 (stage-2 gate):
  1–2 schema v1 / no flat keys — test_create_sync_initial_axes, test_create_rejects_missing_pane_id
  8 task.id UUIDv4 — test_create_sync_initial_axes
  9 bad task.id — test_invalid_task_id_rejected
  10–12 paths, perms, symlinks — test_permissions_on_create, test_symlink_task_json_rejected,
      test_tasks_dir_symlink_rejected, test_json_lock_symlink_rejected, test_rejects_world_writable_parent
  33 cleanup retention — test_cleanup_deletes_eligible_task, test_cleanup_keeps_non_terminal_completion,
      test_cleanup_30_day_boundary_keeps_young, test_cleanup_rate_limit_once_per_day
  3 four-axis / uncertain+result — test_mark_dispatch_uncertain_* , test_uncertain_then_result_* ,
      test_mark_dispatch_skipped_terminal_four_axes
  65 invalid transitions — test_mark_dispatch_without_prompt_start_conflicts, test_record_prompt_start_rejects_terminal_dispatch,
      test_skipped_terminal_rejects_update_result_unchanged, test_sent_dispatch_rejects_second_mark_unchanged
  86 registry not corrupted — test_atomic_write_failure_preserves_json, test_write_all_rejects_zero_byte_write,
      test_cleanup_re_read_skips_delete_when_reset_made_pending,
      test_try_delete_after_reset_leaves_pending, test_try_delete_before_reset_raises_not_found,
      test_barrier_try_delete_vs_reset_explicit_outcomes, test_cleanup_run_lock_serializes_parallel_sweeps
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.registry import io as registry_io
from orchestrator.registry.task_id import validate_task_id

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "task-registry.py"


_FIXTURE_WORKTREE = Path("/tmp/herdr-orchestrator-fixture")


def _ensure_result_artifacts() -> None:
    for ref in (
        "artifacts/review.md",
        "artifacts/out.md",
        "artifacts/err.md",
        "out.md",
        "x.md",
    ):
        path = _FIXTURE_WORKTREE / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("fixture\n", encoding="utf-8")


def _sample_create_payload(*, mode: str = "sync") -> dict:
    return {
        "task": {
            "type": "review",
            "mode": mode,
            "instruction": "review changes",
        },
        "context": {
            "repository": {"identity": "github.com/org/repo"},
            "worktree": {
                "path": "/tmp/herdr-orchestrator-fixture",
                "branch": "main",
                "commit": None,
            },
        },
        "requester": {
            "role": "design",
            "agent_name": "org-repo-design",
            "pane_id": "w11:p1",
        },
        "worker": {
            "role": "reviewer",
            "agent_name": "org-repo-reviewer",
            "pane_id": "w11:p2",
        },
        "completion": {"action": "none" if mode == "sync" else "resume_requester"},
    }


class RegistryStage2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.state_home = Path(self._tmpdir.name) / "state"
        self.env = os.environ.copy()
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        _ensure_result_artifacts()

    def tasks_dir(self) -> Path:
        return self.state_home / "herdr-orchestrator" / "tasks"

    def test_create_sync_initial_axes(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(mode="sync"))
        self.assertEqual(doc["schema_version"], 1)
        self.assertTrue(doc["task"]["id"].startswith("task_"))
        self.assertEqual(doc["task"]["status"], "created")
        self.assertEqual(doc["dispatch"]["status"], "pending")
        self.assertEqual(doc["result"]["status"], "pending")
        self.assertEqual(doc["completion"]["status"], "not_applicable")
        self.assertNotIn("task_id", doc)

    def test_invalid_task_id_rejected(self) -> None:
        with self.assertRaises(RegistryError):
            validate_task_id("../escape")
        with self.assertRaises(RegistryError):
            validate_task_id("not-a-task")

    def test_permissions_on_create(self) -> None:
        with _env_patch(self.env):
            registry_ops.create_task(_sample_create_payload())
        tasks = self.tasks_dir()
        self.assertEqual(stat.S_IMODE(tasks.stat().st_mode), 0o700)
        json_file = next(tasks.glob("task_*.json"))
        self.assertEqual(stat.S_IMODE(json_file.stat().st_mode), 0o600)

    def test_symlink_task_json_rejected(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
        task_id = doc["task"]["id"]
        path = self.tasks_dir() / f"{task_id}.json"
        path.unlink()
        decoy = self.tasks_dir() / "decoy.json"
        decoy.write_text("{}", encoding="utf-8")
        path.symlink_to(decoy)
        with _env_patch(self.env):
            with self.assertRaises(RegistryError):
                registry_ops.get_task(task_id)

    def test_mark_dispatch_without_prompt_start_conflicts(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            before = (self.tasks_dir() / f"{task_id}.json").read_text(encoding="utf-8")
            with self.assertRaises(RegistryError):
                registry_ops.mark_dispatch(task_id, outcome="sent")
            after = (self.tasks_dir() / f"{task_id}.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_dispatch_flow_and_result_update(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            with self.assertRaises(RegistryError) as ctx:
                registry_ops.update_result(
                    task_id,
                    result_status="succeeded",
                    result_ref="../outside",
                    summary="ok",
                )
            self.assertEqual(ctx.exception.code, "invalid_result_ref")
            updated = registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="artifacts/review.md",
                summary="ok",
            )
        self.assertEqual(updated["task"]["status"], "succeeded")
        self.assertEqual(updated["result"]["status"], "succeeded")

    def _through_prompt_start(self, *, mode: str = "sync", completion_action: str | None = None) -> str:
        payload = _sample_create_payload(mode=mode)
        if completion_action is not None:
            payload["completion"] = {"action": completion_action}
        doc = registry_ops.create_task(payload)
        task_id = doc["task"]["id"]
        registry_ops.record_prompt_start(task_id)
        return task_id

    def test_mark_dispatch_uncertain_async_resume_requester_axes(self) -> None:
        with _env_patch(self.env):
            task_id = self._through_prompt_start(mode="async", completion_action="resume_requester")
            doc = registry_ops.mark_dispatch(task_id, outcome="uncertain")
        self.assertEqual(doc["dispatch"]["status"], "uncertain")
        self.assertEqual(doc["task"]["status"], "unknown")
        self.assertEqual(doc["result"]["status"], "pending")
        self.assertEqual(doc["completion"]["status"], "pending")

    def test_uncertain_then_result_success_preserves_dispatch(self) -> None:
        with _env_patch(self.env):
            task_id = self._through_prompt_start(mode="async", completion_action="resume_requester")
            registry_ops.mark_dispatch(task_id, outcome="uncertain")
            doc = registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="artifacts/out.md",
                summary="ok",
            )
        self.assertEqual(doc["task"]["status"], "succeeded")
        self.assertEqual(doc["result"]["status"], "succeeded")
        self.assertEqual(doc["dispatch"]["status"], "uncertain")
        self.assertEqual(doc["completion"]["status"], "pending")

    def test_uncertain_then_result_failed_preserves_dispatch(self) -> None:
        with _env_patch(self.env):
            task_id = self._through_prompt_start(mode="async", completion_action="resume_requester")
            registry_ops.mark_dispatch(task_id, outcome="uncertain")
            doc = registry_ops.update_result(
                task_id,
                result_status="failed",
                result_ref="artifacts/err.md",
                summary="fail",
            )
        self.assertEqual(doc["task"]["status"], "failed")
        self.assertEqual(doc["result"]["status"], "failed")
        self.assertEqual(doc["dispatch"]["status"], "uncertain")

    def test_uncertain_then_result_unavailable_preserves_dispatch(self) -> None:
        with _env_patch(self.env):
            task_id = self._through_prompt_start(mode="async", completion_action="resume_requester")
            registry_ops.mark_dispatch(task_id, outcome="uncertain")
            doc = registry_ops.update_result(
                task_id,
                result_status="unavailable",
                result_ref=None,
                summary="lost",
            )
        self.assertEqual(doc["task"]["status"], "failed")
        self.assertEqual(doc["result"]["status"], "unavailable")
        self.assertEqual(doc["dispatch"]["status"], "uncertain")

    def test_mark_dispatch_uncertain_async_none_keeps_not_applicable(self) -> None:
        with _env_patch(self.env):
            task_id = self._through_prompt_start(mode="async", completion_action="none")
            doc = registry_ops.mark_dispatch(task_id, outcome="uncertain")
        self.assertEqual(doc["dispatch"]["status"], "uncertain")
        self.assertEqual(doc["task"]["status"], "unknown")
        self.assertEqual(doc["completion"]["status"], "not_applicable")

    def test_mark_dispatch_skipped_terminal_four_axes(self) -> None:
        with _env_patch(self.env):
            task_id = self._through_prompt_start(mode="async", completion_action="resume_requester")
            doc = registry_ops.mark_dispatch(task_id, outcome="skipped")
        self.assertEqual(doc["dispatch"]["status"], "skipped")
        self.assertEqual(doc["task"]["status"], "failed")
        self.assertEqual(doc["result"]["status"], "unavailable")
        self.assertEqual(doc["completion"]["status"], "skipped")

    def test_skipped_terminal_rejects_update_result_unchanged(self) -> None:
        with _env_patch(self.env):
            task_id = self._through_prompt_start(mode="async", completion_action="resume_requester")
            registry_ops.mark_dispatch(task_id, outcome="skipped")
            path = self.tasks_dir() / f"{task_id}.json"
            before = path.read_text(encoding="utf-8")
            with self.assertRaises(RegistryError):
                registry_ops.update_result(
                    task_id,
                    result_status="succeeded",
                    result_ref="out.md",
                    summary="x",
                )
            after = path.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_sent_dispatch_rejects_second_mark_unchanged(self) -> None:
        with _env_patch(self.env):
            task_id = self._through_prompt_start(mode="sync")
            registry_ops.mark_dispatch(task_id, outcome="sent")
            path = self.tasks_dir() / f"{task_id}.json"
            before = path.read_text(encoding="utf-8")
            with self.assertRaises(RegistryError):
                registry_ops.mark_dispatch(task_id, outcome="uncertain")
            after = path.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_update_result_rejects_pending_dispatch(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            with self.assertRaises(RegistryError) as ctx:
                registry_ops.update_result(
                    task_id,
                    result_status="succeeded",
                    result_ref="x.md",
                    summary="x",
                )
        self.assertEqual(ctx.exception.code, "dispatch_not_ready")
        self.assertTrue(ctx.exception.retryable)

    def test_claim_completion_single_winner(self) -> None:
        with _env_patch(self.env):
            payload = _sample_create_payload(mode="async")
            doc = registry_ops.create_task(payload)
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="out.md",
                summary="done",
            )
            barrier = threading.Barrier(2)
            results: list[str] = []

            def worker() -> None:
                barrier.wait()
                try:
                    registry_ops.claim_completion(task_id)
                    results.append("ok")
                except RegistryError:
                    results.append("fail")

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("fail"), 1)

    def test_cleanup_keeps_non_terminal_completion(self) -> None:
        with _env_patch(self.env):
            payload = _sample_create_payload(mode="async")
            doc = registry_ops.create_task(payload)
            task_id = doc["task"]["id"]
            path = self.tasks_dir() / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            old = (datetime.now(timezone.utc) - timedelta(days=40)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = old.isoformat()
            raw["task"]["status"] = "succeeded"
            raw["completion"]["status"] = "pending"
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            info = registry_ops.cleanup_tasks(now=datetime.now(timezone.utc))
        self.assertTrue(path.exists())
        self.assertNotIn(task_id, info["deleted"])

    def test_cleanup_keeps_uncertain_completion(self) -> None:
        with _env_patch(self.env):
            payload = _sample_create_payload(mode="async")
            doc = registry_ops.create_task(payload)
            task_id = doc["task"]["id"]
            path = self.tasks_dir() / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            old = (datetime.now(timezone.utc) - timedelta(days=40)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = old.isoformat()
            raw["task"]["status"] = "succeeded"
            raw["completion"]["status"] = "uncertain"
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            info = registry_ops.cleanup_tasks(now=datetime.now(timezone.utc))
        self.assertTrue(path.exists())
        self.assertNotIn(task_id, info["deleted"])

    def test_cleanup_deletes_eligible_task(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            path = self.tasks_dir() / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            old = (datetime.now(timezone.utc) - timedelta(days=40)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = old.isoformat()
            raw["task"]["status"] = "succeeded"
            raw["completion"]["status"] = "not_applicable"
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            info = registry_ops.cleanup_tasks(now=datetime.now(timezone.utc))
        self.assertIn(task_id, info["deleted"])
        self.assertFalse(path.exists())

    def test_create_rejects_missing_pane_id(self) -> None:
        payload = _sample_create_payload()
        del payload["worker"]["pane_id"]
        with _env_patch(self.env):
            with self.assertRaises(RegistryError):
                registry_ops.create_task(payload)
        self.assertFalse(self.tasks_dir().exists())

    def test_lock_file_mode_after_mutation(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
        lock_path = self.tasks_dir() / f"{task_id}.json.lock"
        self.assertTrue(lock_path.exists())
        self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_reset_rejects_initial_dispatch_failure(self) -> None:
        with _env_patch(self.env):
            payload = _sample_create_payload(mode="async")
            doc = registry_ops.create_task(payload)
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="failed", reason="worker_missing")
            before = (self.tasks_dir() / f"{task_id}.json").read_text(encoding="utf-8")
            with self.assertRaises(RegistryError):
                registry_ops.reset_completion(task_id)
            after = (self.tasks_dir() / f"{task_id}.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_reset_allows_completion_skipped_after_result(self) -> None:
        with _env_patch(self.env):
            payload = _sample_create_payload(mode="async")
            doc = registry_ops.create_task(payload)
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="out.md",
                summary="ok",
            )
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="skipped")
            updated = registry_ops.reset_completion(task_id)
        self.assertEqual(updated["completion"]["status"], "pending")

    def test_cleanup_stamp_symlink_rejected(self) -> None:
        with _env_patch(self.env):
            registry_ops.create_task(_sample_create_payload())
            root = self.tasks_dir()
            stamp = root / "last_cleanup_at"
            stamp.unlink(missing_ok=True)
            decoy = root / "decoy_stamp"
            decoy.write_text("x", encoding="utf-8")
            stamp.symlink_to(decoy)
            with self.assertRaises(RegistryError):
                registry_ops.cleanup_tasks(now=datetime.now(timezone.utc))

    def test_record_prompt_start_rejects_terminal_dispatch(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            before = (self.tasks_dir() / f"{task_id}.json").read_text(encoding="utf-8")
            with self.assertRaises(RegistryError):
                registry_ops.record_prompt_start(task_id)
            after = (self.tasks_dir() / f"{task_id}.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_rejects_world_writable_parent(self) -> None:
        orch = self.state_home / "herdr-orchestrator"
        orch.mkdir(parents=True)
        os.chmod(orch, 0o777)
        with _env_patch(self.env):
            with self.assertRaises(RegistryError) as ctx:
                registry_ops.create_task(_sample_create_payload())
        self.assertIn("shared writable", str(ctx.exception.message).lower())
        self.assertFalse((orch / "tasks").exists())

    def test_tasks_dir_symlink_rejected(self) -> None:
        orch = self.state_home / "herdr-orchestrator"
        orch.mkdir(parents=True)
        real_tasks = orch / "tasks-real"
        real_tasks.mkdir()
        (orch / "tasks").symlink_to(real_tasks)
        with _env_patch(self.env):
            with self.assertRaises(RegistryError):
                registry_ops.create_task(_sample_create_payload())

    def test_json_lock_symlink_rejected(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            lock_path = self.tasks_dir() / f"{task_id}.json.lock"
            lock_path.unlink(missing_ok=True)
            decoy = self.tasks_dir() / "decoy.lock"
            decoy.write_text("", encoding="utf-8")
            lock_path.symlink_to(decoy)
            with self.assertRaises(RegistryError):
                registry_ops.record_prompt_start(task_id)

    def test_atomic_write_failure_preserves_json(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            path = self.tasks_dir() / f"{task_id}.json"
            before = path.read_text(encoding="utf-8")
            with patch.object(registry_io, "_write_all", side_effect=OSError("short write")):
                with self.assertRaises(RegistryError):
                    registry_ops.record_prompt_start(task_id)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_normalizes_orchestrator_root_from_755(self) -> None:
        orch = self.state_home / "herdr-orchestrator"
        orch.mkdir(parents=True)
        os.chmod(orch, 0o755)
        with _env_patch(self.env):
            registry_ops.create_task(_sample_create_payload())
        self.assertEqual(stat.S_IMODE(orch.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.tasks_dir().stat().st_mode), 0o700)

    def test_write_all_rejects_zero_byte_write(self) -> None:
        real_write = os.write

        def write_some_then_stall(fd: int, data: bytes) -> int:
            if not hasattr(write_some_then_stall, "once"):
                write_some_then_stall.once = True
                return min(3, len(data))
            return 0

        with patch("orchestrator.registry.io.os.write", side_effect=write_some_then_stall):
            with self.assertRaises(RegistryError):
                with _env_patch(self.env):
                    registry_ops.create_task(_sample_create_payload())

    def test_cleanup_re_read_skips_delete_when_reset_made_pending(self) -> None:
        """Second read under lock must observe reset (stale first read simulated)."""
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            payload = _sample_create_payload(mode="async")
            doc = registry_ops.create_task(payload)
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="out.md",
                summary="ok",
            )
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="skipped")
            path = self.tasks_dir() / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            old = (now - timedelta(days=40)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = old.isoformat()
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            registry_ops.reset_completion(task_id)
            stale = json.loads(path.read_text(encoding="utf-8"))
            stale["completion"]["status"] = "skipped"
            real_read = registry_io.read_task_document_unlocked
            calls = {"n": 0}

            def read_side_effect(p: Path) -> dict:
                calls["n"] += 1
                if calls["n"] == 1:
                    return stale
                return real_read(p)

            with patch.object(registry_io, "read_task_document_unlocked", side_effect=read_side_effect):
                deleted = registry_ops._try_delete_task_if_eligible(
                    task_id, now - timedelta(days=30)
                )
        self.assertFalse(deleted)
        self.assertTrue(path.exists())
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["completion"]["status"],
            "pending",
        )

    def _setup_aged_completion_skipped(self, now: datetime) -> tuple[str, Path]:
        doc = registry_ops.create_task(_sample_create_payload(mode="async"))
        task_id = doc["task"]["id"]
        path = self.tasks_dir() / f"{task_id}.json"
        registry_ops.record_prompt_start(task_id)
        registry_ops.mark_dispatch(task_id, outcome="sent")
        registry_ops.update_result(
            task_id,
            result_status="succeeded",
            result_ref="out.md",
            summary="ok",
        )
        registry_ops.claim_completion(task_id)
        registry_ops.finish_completion(task_id, outcome="skipped")
        raw = json.loads(path.read_text(encoding="utf-8"))
        old = (now - timedelta(days=40)).replace(microsecond=0)
        raw["timestamps"]["created_at"] = old.isoformat()
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        return task_id, path

    def test_try_delete_after_reset_leaves_pending(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            task_id, path = self._setup_aged_completion_skipped(now)
            registry_ops.reset_completion(task_id)
            deleted = registry_ops._try_delete_task_if_eligible(
                task_id, now - timedelta(days=30)
            )
            self.assertFalse(deleted)
            self.assertEqual(registry_ops.get_task(task_id)["completion"]["status"], "pending")

    def test_try_delete_before_reset_raises_not_found(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            task_id, path = self._setup_aged_completion_skipped(now)
            cutoff = now - timedelta(days=30)
            self.assertTrue(registry_ops._try_delete_task_if_eligible(task_id, cutoff))
            with self.assertRaises(RegistryError) as ctx:
                registry_ops.reset_completion(task_id)
            self.assertEqual(ctx.exception.code, "task_not_found")
            self.assertFalse(path.exists())

    def test_barrier_try_delete_vs_reset_explicit_outcomes(self) -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=30)
        with _env_patch(self.env):
            task_id, path = self._setup_aged_completion_skipped(now)
            barrier = threading.Barrier(2)
            reset_errors: list[RegistryError] = []
            thread_errors: list[BaseException] = []

            def reset_side() -> None:
                try:
                    barrier.wait(timeout=5)
                    registry_ops.reset_completion(task_id)
                except RegistryError as exc:
                    reset_errors.append(exc)
                except BaseException as exc:  # pragma: no cover - test must fail
                    thread_errors.append(exc)

            def delete_side() -> None:
                try:
                    barrier.wait(timeout=5)
                    registry_ops._try_delete_task_if_eligible(task_id, cutoff)
                except BaseException as exc:
                    thread_errors.append(exc)

            t_reset = threading.Thread(target=reset_side)
            t_delete = threading.Thread(target=delete_side)
            t_reset.start()
            t_delete.start()
            t_reset.join(timeout=10)
            t_delete.join(timeout=10)
            self.assertFalse(thread_errors, thread_errors)

            if path.exists():
                self.assertEqual(
                    registry_ops.get_task(task_id)["completion"]["status"],
                    "pending",
                )
                self.assertEqual(reset_errors, [])
            else:
                self.assertFalse(path.exists())
                self.assertEqual(len(reset_errors), 1)
                self.assertNotEqual(reset_errors[0].code, "invalid_schema")

    def test_cleanup_run_lock_serializes_parallel_sweeps(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            path = self.tasks_dir() / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            old = (now - timedelta(days=40)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = old.isoformat()
            raw["task"]["status"] = "succeeded"
            raw["completion"]["status"] = "not_applicable"
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

            barrier = threading.Barrier(2)
            results: list[dict] = []
            thread_errors: list[BaseException] = []

            def run_cleanup() -> None:
                try:
                    barrier.wait(timeout=5)
                    results.append(registry_ops.cleanup_tasks(now=now))
                except BaseException as exc:
                    thread_errors.append(exc)

            t1 = threading.Thread(target=run_cleanup)
            t2 = threading.Thread(target=run_cleanup)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)
            self.assertFalse(thread_errors, thread_errors)
            self.assertEqual(len(results), 2)
            limited = sum(1 for r in results if r["skipped_rate_limit"])
            self.assertEqual(limited, 1)
            deleted_total = sum(len(r["deleted"]) for r in results)
            self.assertEqual(deleted_total, 1)

    def test_cleanup_vs_reset_completion(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            payload = _sample_create_payload(mode="async")
            doc = registry_ops.create_task(payload)
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="out.md",
                summary="ok",
            )
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="skipped")
            path = self.tasks_dir() / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            old = (now - timedelta(days=40)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = old.isoformat()
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            registry_ops.reset_completion(task_id)
            info = registry_ops.cleanup_tasks(now=now)
        self.assertTrue(path.exists())
        self.assertNotIn(task_id, info["deleted"])

    def test_cleanup_30_day_boundary_keeps_young(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            path = self.tasks_dir() / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            ts = (now - timedelta(days=29)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = ts.isoformat()
            raw["task"]["status"] = "succeeded"
            raw["completion"]["status"] = "not_applicable"
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            info = registry_ops.cleanup_tasks(now=now)
        self.assertNotIn(task_id, info["deleted"])
        self.assertTrue(path.exists())

    def test_cleanup_rate_limit_once_per_day(self) -> None:
        now = datetime.now(timezone.utc)
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
            path = self.tasks_dir() / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            old = (now - timedelta(days=40)).replace(microsecond=0)
            raw["timestamps"]["created_at"] = old.isoformat()
            raw["task"]["status"] = "succeeded"
            raw["completion"]["status"] = "not_applicable"
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            first = registry_ops.cleanup_tasks(now=now)
            self.assertIn(task_id, first["deleted"])
            doc2 = registry_ops.create_task(_sample_create_payload())
            task_id2 = doc2["task"]["id"]
            path2 = self.tasks_dir() / f"{task_id2}.json"
            raw2 = json.loads(path2.read_text(encoding="utf-8"))
            raw2["timestamps"]["created_at"] = old.isoformat()
            raw2["task"]["status"] = "succeeded"
            raw2["completion"]["status"] = "not_applicable"
            path2.write_text(json.dumps(raw2, indent=2) + "\n", encoding="utf-8")
            second = registry_ops.cleanup_tasks(now=now + timedelta(hours=1))
        self.assertTrue(second["skipped_rate_limit"])
        self.assertNotIn(task_id2, second["deleted"])

    def test_cli_get_roundtrip(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload())
            task_id = doc["task"]["id"]
        proc = subprocess.run(
            [str(REGISTRY_CLI), "get", task_id],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=str(REPO_ROOT),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["result"]["task"]["id"], task_id)


class _env_patch:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self._previous: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self.env.items():
            if key.startswith("XDG_") or key == "XDG_STATE_HOME":
                self._previous[key] = os.environ.get(key)
                os.environ[key] = value

    def __exit__(self, *args: object) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
