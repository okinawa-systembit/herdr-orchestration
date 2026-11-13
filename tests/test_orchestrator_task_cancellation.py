"""
Tests for Task Cancellation (cancel-task) & Recovery (Issue #173 / CT-01 - CT-10).
Conforms to docs/herdr-orchestrator-task-cancellation.md §7.2.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from orchestrator.completion_commands import CompletionCommandError, run_reset_completion, run_resume_task
from orchestrator.handoff_commands import HandoffCommandError, _wait_for_result_terminal
from orchestrator.list_commands import run_task_list
from orchestrator.paths import tasks_dir
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.errors import RegistryError
from orchestrator.registry.schema import validate_loaded_task
from orchestrator.result_commands import ResultCommandError, result_submit

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"


@contextlib.contextmanager
def _env_patch(env_vars: dict[str, str | None]):
    old_env = {}
    for k, v in env_vars.items():
        old_env[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _sample_create_payload(*, mode: str = "async") -> dict:
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
            "pane_id": "w1:p1",
        },
        "worker": {
            "role": "reviewer",
            "agent_name": "org-repo-reviewer",
            "pane_id": "w1:p2",
        },
        "completion": {"action": "none" if mode == "sync" else "resume_requester"},
    }


class TaskCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_home = Path(self.tmp.name)
        self.env = {
            "XDG_STATE_HOME": str(self.state_home),
            "HERDR_ENV": "1",
            "HERDR_PANE_ID": "w1:p1",
            "HERDR_WORKSPACE_ID": "w1",
            "HERDR_TAB_ID": "w1:t1",
        }
        self.env_patcher = patch.dict(os.environ, self.env)
        self.env_patcher.start()

        self.fixture_dir = Path("/tmp/herdr-orchestrator-fixture")
        self.fixture_dir.mkdir(parents=True, exist_ok=True)
        (self.fixture_dir / "out.md").write_text("ok\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.tmp.cleanup()

    def _create_in_progress_task(self, *, mode: str = "async") -> str:
        doc = registry_ops.create_task(_sample_create_payload(mode=mode))
        task_id = doc["task"]["id"]
        registry_ops.record_prompt_start(task_id)
        registry_ops.mark_dispatch(task_id, outcome="sent")
        return task_id

    def _create_unknown_task(self, *, mode: str = "async") -> str:
        doc = registry_ops.create_task(_sample_create_payload(mode=mode))
        task_id = doc["task"]["id"]
        registry_ops.record_prompt_start(task_id)
        registry_ops.mark_dispatch(task_id, outcome="uncertain")
        return task_id

    def _run_cli(self, *args: str, env_override: dict[str, str | None] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy()
        if env_override:
            for k, v in env_override.items():
                if v is None:
                    run_env.pop(k, None)
                else:
                    run_env[k] = v
        return subprocess.run(
            [sys.executable, str(ORCHESTRATOR_CLI), *args],
            env=run_env,
            capture_output=True,
            text=True,
        )

    # -------------------------------------------------------------------------
    # CT-01: Transition from in_progress
    # -------------------------------------------------------------------------
    def test_ct01_cancel_from_in_progress(self) -> None:
        task_id = self._create_in_progress_task(mode="async")
        proc = self._run_cli("cancel-task", task_id, "--reason", "Worker pane lost")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        self.assertEqual(data["command"], "cancel-task")
        self.assertTrue(data["cancelled"])
        self.assertEqual(data["task"]["status"], "cancelled")
        self.assertEqual(data["task"]["cancel_reason"], "Worker pane lost")
        self.assertIsInstance(data["task"]["cancelled_by"], dict)
        self.assertEqual(data["dispatch"]["status"], "sent")
        self.assertEqual(data["result"]["status"], "pending")
        self.assertEqual(data["completion"]["status"], "skipped")

        # Validate persisted schema
        persisted = registry_ops.get_task(task_id)
        validate_loaded_task(persisted)
        self.assertEqual(persisted["task"]["status"], "cancelled")
        self.assertEqual(persisted["task"]["cancel_reason"], "Worker pane lost")

    # -------------------------------------------------------------------------
    # CT-02: Transition from unknown (dispatch=uncertain)
    # -------------------------------------------------------------------------
    def test_ct02_cancel_from_unknown(self) -> None:
        task_id = self._create_unknown_task(mode="async")
        before = registry_ops.get_task(task_id)
        self.assertEqual(before["task"]["status"], "unknown")
        self.assertEqual(before["dispatch"]["status"], "uncertain")

        proc = self._run_cli("cancel-task", task_id, "--reason", "Timeout and pane gone")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        self.assertEqual(data["task"]["status"], "cancelled")
        self.assertEqual(data["dispatch"]["status"], "uncertain")
        self.assertEqual(data["result"]["status"], "pending")
        self.assertEqual(data["completion"]["status"], "skipped")

    # -------------------------------------------------------------------------
    # CT-03: Completion status transitions (async vs sync)
    # -------------------------------------------------------------------------
    def test_ct03_completion_transitions(self) -> None:
        async_id = self._create_in_progress_task(mode="async")
        sync_id = self._create_in_progress_task(mode="sync")

        res_async = registry_ops.cancel_task(async_id, reason="abort async")
        self.assertEqual(res_async["completion"]["action"], "resume_requester")
        self.assertEqual(res_async["completion"]["status"], "skipped")

        res_sync = registry_ops.cancel_task(sync_id, reason="abort sync")
        self.assertEqual(res_sync["completion"]["action"], "none")
        self.assertEqual(res_sync["completion"]["status"], "not_applicable")

    # -------------------------------------------------------------------------
    # CT-04: Reject when completion.status == processing
    # -------------------------------------------------------------------------
    def test_ct04_reject_completion_processing(self) -> None:
        task_id = self._create_in_progress_task(mode="async")
        # Force completion.status = processing (simulate in-flight completion)
        path = tasks_dir() / f"{task_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["completion"]["status"] = "processing"
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

        proc = self._run_cli("cancel-task", task_id, "--reason", "try cancel while processing")
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "registry_conflict")

        # Verify Registry unchanged
        after = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(after["task"]["status"], "in_progress")
        self.assertEqual(after["completion"]["status"], "processing")

    # -------------------------------------------------------------------------
    # CT-05: Reject created / terminal tasks & reset-completion / resume-task on cancelled
    # -------------------------------------------------------------------------
    def test_ct05_reject_created_terminal_and_reset_resume(self) -> None:
        # 1. created
        created_doc = registry_ops.create_task(_sample_create_payload(mode="async"))
        created_id = created_doc["task"]["id"]
        with self.assertRaises(RegistryError) as ctx_created:
            registry_ops.cancel_task(created_id, reason="not started")
        self.assertEqual(ctx_created.exception.code, "registry_conflict")

        # 2. succeeded
        succ_id = self._create_in_progress_task(mode="async")
        registry_ops.update_result(succ_id, result_status="succeeded", result_ref="out.md", summary="ok")
        with self.assertRaises(RegistryError) as ctx_succ:
            registry_ops.cancel_task(succ_id, reason="already done")
        self.assertEqual(ctx_succ.exception.code, "registry_conflict")

        # 3. failed
        fail_id = self._create_in_progress_task(mode="async")
        registry_ops.update_result(fail_id, result_status="failed", result_ref="out.md", summary="err")
        with self.assertRaises(RegistryError) as ctx_fail:
            registry_ops.cancel_task(fail_id, reason="already failed")
        self.assertEqual(ctx_fail.exception.code, "registry_conflict")

        # 4. cancelled -> double cancel, reset-completion, resume-task rejected
        canc_id = self._create_in_progress_task(mode="async")
        registry_ops.cancel_task(canc_id, reason="first cancel")

        with self.assertRaises(RegistryError) as ctx_double:
            registry_ops.cancel_task(canc_id, reason="second cancel")
        self.assertEqual(ctx_double.exception.code, "registry_conflict")

        with self.assertRaises(RegistryError) as ctx_reset:
            registry_ops.reset_completion(canc_id)
        self.assertEqual(ctx_reset.exception.code, "registry_conflict")

        with self.assertRaises(CompletionCommandError) as ctx_run_reset:
            run_reset_completion(canc_id)
        self.assertEqual(ctx_run_reset.exception.code, "registry_conflict")

        with self.assertRaises(CompletionCommandError) as ctx_resume:
            run_resume_task(canc_id)
        self.assertEqual(ctx_resume.exception.code, "registry_conflict")

    # -------------------------------------------------------------------------
    # CT-06: --reason required, non-empty, <= 2000 chars
    # -------------------------------------------------------------------------
    def test_ct06_reason_required_and_nonempty(self) -> None:
        task_id = self._create_in_progress_task(mode="async")

        # Missing --reason (argparse exits with 2)
        proc_missing = self._run_cli("cancel-task", task_id)
        self.assertEqual(proc_missing.returncode, 2)

        # Empty string
        proc_empty = self._run_cli("cancel-task", task_id, "--reason", "")
        self.assertEqual(proc_empty.returncode, 1)
        self.assertEqual(json.loads(proc_empty.stdout)["error"], "invalid_input")

        # Whitespace only
        proc_ws = self._run_cli("cancel-task", task_id, "--reason", "   \n\t  ")
        self.assertEqual(proc_ws.returncode, 1)
        self.assertEqual(json.loads(proc_ws.stdout)["error"], "invalid_input")

        # Exceeds 2000 chars
        proc_long = self._run_cli("cancel-task", task_id, "--reason", "x" * 2001)
        self.assertEqual(proc_long.returncode, 1)
        self.assertEqual(json.loads(proc_long.stdout)["error"], "invalid_input")

        # Verify task remained untouched
        doc = registry_ops.get_task(task_id)
        self.assertEqual(doc["task"]["status"], "in_progress")

    # -------------------------------------------------------------------------
    # CT-07: Late result-submit after cancel rejected
    # -------------------------------------------------------------------------
    @patch("orchestrator.result_commands.verify_worker_submit_context")
    def test_ct07_reject_late_result_submit_after_cancel(self, _mock_verify) -> None:
        task_id = self._create_in_progress_task(mode="async")
        registry_ops.cancel_task(task_id, reason="worker lost")

        with self.assertRaises(ResultCommandError) as ctx:
            result_submit(
                task_id,
                result_status="succeeded",
                result_ref="out.md",
                summary="late result",
            )
        self.assertEqual(ctx.exception.code, "registry_conflict")

        doc = registry_ops.get_task(task_id)
        self.assertEqual(doc["task"]["status"], "cancelled")
        self.assertEqual(doc["result"]["status"], "pending")

    # -------------------------------------------------------------------------
    # CT-08: 30-day cleanup-registry deletion of cancelled tasks
    # -------------------------------------------------------------------------
    def test_ct08_cleanup_after_30_days(self) -> None:
        now = datetime.now(timezone.utc)
        async_id = self._create_in_progress_task(mode="async")
        sync_id = self._create_in_progress_task(mode="sync")
        registry_ops.cancel_task(async_id, reason="old async cancel")
        registry_ops.cancel_task(sync_id, reason="old sync cancel")

        # Age both tasks by 35 days
        old_ts = (now - timedelta(days=35)).replace(microsecond=0).isoformat()
        for tid in (async_id, sync_id):
            p = tasks_dir() / f"{tid}.json"
            raw = json.loads(p.read_text(encoding="utf-8"))
            raw["timestamps"]["created_at"] = old_ts
            p.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

        proc = self._run_cli("cleanup-registry", env_override={"HERDR_ENV": None})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        self.assertIn(async_id, data["deleted"])
        self.assertIn(sync_id, data["deleted"])
        self.assertFalse((tasks_dir() / f"{async_id}.json").exists())
        self.assertFalse((tasks_dir() / f"{sync_id}.json").exists())

    # -------------------------------------------------------------------------
    # CT-09: HERDR_ENV exempt & task-list overview.state / I7 consistency
    # -------------------------------------------------------------------------
    def test_ct09_no_herdr_env_and_task_list_overview(self) -> None:
        sent_id = self._create_in_progress_task(mode="async")
        unc_id = self._create_unknown_task(mode="async")

        # Run cancel-task without HERDR_ENV / HERDR_PANE_ID
        for tid in (sent_id, unc_id):
            proc = self._run_cli(
                "cancel-task",
                tid,
                "--reason",
                "external terminal cleanup",
                env_override={
                    "HERDR_ENV": None,
                    "HERDR_PANE_ID": None,
                    "HERDR_WORKSPACE_ID": None,
                    "HERDR_TAB_ID": None,
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            data = json.loads(proc.stdout)
            self.assertEqual(data["task"]["cancelled_by"]["role"], "unbound")
            self.assertIsNone(data["task"]["cancelled_by"]["pane_id"])

        # Add an inconsistent cancelled task (e.g. result.status=succeeded)
        bad_id = self._create_in_progress_task(mode="async")
        registry_ops.cancel_task(bad_id, reason="then corrupted")
        bad_path = tasks_dir() / f"{bad_id}.json"
        bad_raw = json.loads(bad_path.read_text(encoding="utf-8"))
        bad_raw["result"]["status"] = "succeeded"
        bad_path.write_text(json.dumps(bad_raw, indent=2) + "\n", encoding="utf-8")

        # Check task-list overview.state and warnings
        list_res, exit_code = run_task_list()
        self.assertEqual(exit_code, 0)
        by_id = {t["task"]["id"]: t for t in list_res["tasks"]}

        for valid_tid in (sent_id, unc_id):
            self.assertEqual(by_id[valid_tid]["overview"]["state"], "closed")
            self.assertEqual(by_id[valid_tid]["overview"]["warnings"], [])

        self.assertIn("inconsistent_state_combination", by_id[bad_id]["overview"]["warnings"])

    # -------------------------------------------------------------------------
    # CT-10: sync handoff _wait_for_result_terminal detects cancellation
    # -------------------------------------------------------------------------
    def test_ct10_sync_handoff_wait_detects_cancellation(self) -> None:
        sync_id = self._create_in_progress_task(mode="sync")

        def cancel_after_delay() -> None:
            time.sleep(0.2)
            registry_ops.cancel_task(sync_id, reason="cancelled while waiting")

        t = threading.Thread(target=cancel_after_delay, daemon=True)
        t.start()

        start = time.monotonic()
        with patch("orchestrator.handoff_commands._observe_worker_during_result_wait"):
            with self.assertRaises(HandoffCommandError) as ctx:
                _wait_for_result_terminal(sync_id, timeout_sec=30)
        elapsed = time.monotonic() - start

        t.join(timeout=2.0)
        self.assertEqual(ctx.exception.code, "task_cancelled")
        self.assertFalse(ctx.exception.retryable)
        self.assertIn("cancelled while waiting", ctx.exception.message)
        self.assertLess(elapsed, 5.0, "Should return immediately on cancellation instead of waiting full timeout")


if __name__ == "__main__":
    unittest.main()
