"""
Tests for completion.status=skipped visibility in doctor / task-list / result-submit (Issue #183 / ST-01 - ST-06).
Conforms to docs/herdr-orchestrator-skipped-completion.md §5.2.
"""
from __future__ import annotations

import contextlib
import io as py_io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from orchestrator.completion_commands import run_recover_completion
from orchestrator.dispatch_commands import run_doctor
from orchestrator.list_commands import run_task_list
from orchestrator.paths import tasks_dir
from orchestrator.public_cli import main as public_cli_main
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.io import read_task_document


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


class SkippedCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.xdg_state = self.tmp_root / "xdg_state"
        self.xdg_config = self.tmp_root / "xdg_config"
        self.xdg_state.mkdir(parents=True, exist_ok=True)
        self.xdg_config.mkdir(parents=True, exist_ok=True)
        self.env_ctx = _env_patch(
            {
                "XDG_STATE_HOME": str(self.xdg_state),
                "XDG_CONFIG_HOME": str(self.xdg_config),
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "w15:p1",
            }
        )
        self.env_ctx.__enter__()

        self.worktree = self.tmp_root / "repo"
        self.worktree.mkdir(parents=True, exist_ok=True)
        (self.worktree / "out.md").write_text("ok\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.env_ctx.__exit__(None, None, None)
        self._tmp.cleanup()

    def _create_task(
        self,
        *,
        mode: str = "async",
        task_status: str = "succeeded",
        dispatch_status: str = "sent",
        result_status: str = "succeeded",
        result_ref: str | None = "out.md",
        completion_action: str = "resume_requester",
        completion_status: str = "skipped",
        completion_reason: str | None = "requester_working",
        recovery_count: int = 0,
        updated_at: datetime | None = None,
    ) -> str:
        doc = registry_ops.create_task(
            {
                "task": {
                    "type": "review",
                    "mode": mode,
                    "instruction": "Review skipped completion",
                },
                "context": {
                    "repository": {"identity": "4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"},
                    "worktree": {
                        "path": str(self.worktree),
                        "branch": "main",
                        "commit": None,
                    },
                },
                "requester": {
                    "role": "メイン",
                    "agent_name": "w16:p1",
                    "pane_id": "w16:p1",
                },
                "worker": {
                    "role": "review",
                    "agent_name": "w15:p2",
                    "pane_id": "w15:p2",
                    "cwd": str(self.worktree),
                },
                "completion": {"action": completion_action},
            }
        )
        tid = doc["task"]["id"]
        loaded = read_task_document(tid)
        loaded["task"]["status"] = task_status
        loaded["dispatch"]["status"] = dispatch_status
        loaded["result"]["status"] = result_status
        loaded["result"]["ref"] = result_ref
        loaded["result"]["summary"] = "done" if result_status != "pending" else None
        loaded["completion"]["action"] = completion_action
        loaded["completion"]["status"] = completion_status
        loaded["completion"]["reason"] = completion_reason
        loaded["completion"]["recovery_count"] = recovery_count
        if updated_at is not None:
            iso = updated_at.astimezone(timezone.utc).isoformat()
            loaded["timestamps"]["updated_at"] = iso
            loaded["timestamps"]["created_at"] = iso
        (tasks_dir() / f"{tid}.json").write_text(
            json.dumps(loaded, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return tid

    # -------------------------------------------------------------------------
    # ST-01: is_recoverable_skipped_completion boundary predicate
    # -------------------------------------------------------------------------
    def test_st01_predicate_boundary(self) -> None:
        for reason in (
            "requester_working",
            "requester_blocked",
            "requester_unknown",
            "requester_unavailable",
            "requester_context_mismatch",
            "live_pane_id_missing",
        ):
            tid = self._create_task(completion_reason=reason)
            self.assertTrue(
                registry_ops.is_recoverable_skipped_completion(read_task_document(tid)),
                f"Expected True for reason={reason}",
            )

        # uncertain dispatch + failed task + unavailable result -> still recoverable
        tid_unc = self._create_task(
            dispatch_status="uncertain",
            task_status="failed",
            result_status="unavailable",
            result_ref=None,
            completion_reason="requester_working",
        )
        self.assertTrue(
            registry_ops.is_recoverable_skipped_completion(read_task_document(tid_unc))
        )

        # Exclusions:
        excluded = [
            # Initial dispatch failure terminal (failed)
            self._create_task(
                dispatch_status="failed",
                task_status="failed",
                result_status="unavailable",
                result_ref=None,
                completion_status="skipped",
                completion_reason="dispatch_failed",
            ),
            # Initial dispatch failure terminal (skipped)
            self._create_task(
                dispatch_status="skipped",
                task_status="failed",
                result_status="unavailable",
                result_ref=None,
                completion_status="skipped",
                completion_reason="dispatch_skipped",
            ),
            # Cancelled task
            self._create_task(
                task_status="cancelled",
                result_status="pending",
                result_ref=None,
                completion_status="skipped",
            ),
            # action == none
            self._create_task(
                mode="sync",
                completion_action="none",
                completion_status="not_applicable",
                completion_reason=None,
            ),
            # Already sent
            self._create_task(completion_status="sent", completion_reason=None),
            # Pending completion
            self._create_task(completion_status="pending", completion_reason=None),
        ]
        for tid in excluded:
            self.assertFalse(
                registry_ops.is_recoverable_skipped_completion(read_task_document(tid)),
                f"Expected False for excluded task {tid}",
            )
        self.assertFalse(registry_ops.is_recoverable_skipped_completion(None))
        self.assertFalse(registry_ops.is_recoverable_skipped_completion({}))

    # -------------------------------------------------------------------------
    # ST-02: doctor skipped_completion payload & descending updated_at sort
    # -------------------------------------------------------------------------
    def test_st02_doctor_output_and_descending_sort(self) -> None:
        now = datetime.now(timezone.utc)
        t_old = self._create_task(
            completion_reason="requester_working",
            updated_at=now - timedelta(minutes=30),
        )
        t_mid = self._create_task(
            completion_reason="requester_blocked",
            recovery_count=1,
            updated_at=now - timedelta(minutes=10),
        )
        t_new = self._create_task(
            task_status="failed",
            result_status="failed",
            result_ref=None,
            completion_reason="requester_unknown",
            updated_at=now - timedelta(minutes=2),
        )
        # Initial dispatch failure must be excluded
        t_init_fail = self._create_task(
            dispatch_status="failed",
            task_status="failed",
            result_status="unavailable",
            result_ref=None,
            completion_status="skipped",
            completion_reason="dispatch_failed",
            updated_at=now - timedelta(minutes=1),
        )

        with patch("orchestrator.live_agent.list_live_panes_with_ui", return_value=[]):
            doc, code = run_doctor()

        self.assertEqual(code, 0)
        skipped = doc["skipped_completion"]
        self.assertEqual(skipped["action"], "await_human")
        self.assertEqual(skipped["summary"]["total_candidates"], 3)
        self.assertTrue(len(skipped["guidance"]) >= 2)

        cands = skipped["candidates"]
        self.assertEqual([c["task_id"] for c in cands], [t_new, t_mid, t_old])
        self.assertNotIn(t_init_fail, [c["task_id"] for c in cands])

        first = cands[0]
        for key in (
            "task_id",
            "task_type",
            "task_mode",
            "task_status",
            "result_status",
            "result_ref",
            "completion_reason",
            "recovery_count",
            "repository_identity",
            "worktree_path",
            "requester_role",
            "requester_agent_name",
            "updated_at",
            "age_sec",
        ):
            self.assertIn(key, first)
        self.assertEqual(first["completion_reason"], "requester_unknown")
        self.assertEqual(first["repository_identity"], "4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f")
        self.assertEqual(first["worktree_path"], str(self.worktree))

    # -------------------------------------------------------------------------
    # ST-03: read-only, corrupt JSON tolerance, exit code, no pane list call
    # -------------------------------------------------------------------------
    def test_st03_readonly_corrupt_tolerance_and_no_pane_list(self) -> None:
        tdir = tasks_dir()
        self.assertFalse(tdir.exists())

        with patch("orchestrator.live_agent.list_live_panes_with_ui", return_value=None) as m_panes:
            doc, code = run_doctor()
        self.assertEqual(code, 0)
        self.assertFalse(tdir.exists())
        self.assertEqual(doc["skipped_completion"]["summary"]["total_candidates"], 0)
        self.assertEqual(doc["skipped_completion"]["candidates"], [])
        self.assertEqual(m_panes.call_count, 1)

        # Direct call to find_skipped_completion_candidates never calls list_live_panes_with_ui
        tid = self._create_task(completion_reason="requester_working")
        corrupt = tdir / "task_00000000-0000-4000-8000-000000000000.json"
        corrupt.write_text("{corrupt", encoding="utf-8")

        with patch("orchestrator.live_agent.list_live_panes_with_ui") as m_no_call:
            cands = registry_ops.find_skipped_completion_candidates()
        self.assertEqual(m_no_call.call_count, 0)
        self.assertEqual([c["task_id"] for c in cands], [tid])

        # Exit code unaffected when HERDR_ENV != 1
        with _env_patch({"HERDR_ENV": None}):
            doc_noenv, code_noenv = run_doctor()
        self.assertEqual(code_noenv, 1)
        self.assertEqual(doc_noenv["skipped_completion"]["summary"]["total_candidates"], 1)

    # -------------------------------------------------------------------------
    # ST-04: task-list completion_skipped warning & schema compatibility
    # -------------------------------------------------------------------------
    def test_st04_task_list_warning_and_compat(self) -> None:
        t_rec = self._create_task(completion_reason="requester_working")
        t_init_fail = self._create_task(
            dispatch_status="failed",
            task_status="failed",
            result_status="unavailable",
            result_ref=None,
            completion_status="skipped",
            completion_reason="dispatch_failed",
        )
        t_sent = self._create_task(completion_status="sent", completion_reason=None)

        res, code = run_task_list()
        self.assertEqual(code, 0)
        self.assertEqual(res["list_format_version"], 1)

        by_id = {t["task"]["id"]: t for t in res["tasks"]}
        self.assertEqual(by_id[t_rec]["overview"]["state"], "closed")
        self.assertIn("completion_skipped", by_id[t_rec]["overview"]["warnings"])
        self.assertNotIn("inconsistent_state_combination", by_id[t_rec]["overview"]["warnings"])
        self.assertNotIn("reason", by_id[t_rec]["completion"])

        self.assertNotIn("completion_skipped", by_id[t_init_fail]["overview"]["warnings"])
        self.assertNotIn("completion_skipped", by_id[t_sent]["overview"]["warnings"])

    # -------------------------------------------------------------------------
    # ST-05: result-submit stderr 1-line warning & stdout compatibility
    # -------------------------------------------------------------------------
    def test_st05_result_submit_stderr_warning_and_stdout_compat(self) -> None:
        # 1. Skipped completion -> emits 1-line warning on stderr, stdout JSON unchanged
        t_skip = self._create_task(
            task_status="in_progress",
            result_status="pending",
            result_ref=None,
            completion_status="pending",
            completion_reason=None,
        )
        skipped_chain = {
            "attempted": True,
            "task_id": t_skip,
            "prompt_sent": False,
            "completion": {
                "action": "resume_requester",
                "status": "skipped",
                "reason": "requester_working",
                "recovery_count": 0,
            },
            "task": {"status": "succeeded"},
            "result": {"status": "succeeded"},
        }

        out_buf = py_io.StringIO()
        err_buf = py_io.StringIO()
        with _env_patch({"HERDR_PANE_ID": "w15:p2"}):
            with patch(
                "orchestrator.result_commands.verify_worker_submit_context",
                return_value={"verified": True},
            ), patch(
                "orchestrator.result_commands.best_effort_completion_after_result",
                return_value=skipped_chain,
            ), patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
                rc = public_cli_main(
                    [
                        "result-submit",
                        t_skip,
                        "--result-status",
                        "succeeded",
                        "--result-ref",
                        "out.md",
                        "--summary",
                        "done",
                    ]
                )
        self.assertEqual(rc, 0)
        parsed = json.loads(out_buf.getvalue())
        self.assertEqual(parsed["command"], "result-submit")
        self.assertFalse(parsed["idempotent_replay"])
        self.assertEqual(parsed["completion_chain"], skipped_chain)
        self.assertIn(
            f"[herdr-orchestrator] warning: completion skipped (requester_working); "
            f"requester was not notified. Human: run 'recover-completion {t_skip}' after requester becomes idle.",
            err_buf.getvalue(),
        )

        # 2. Idempotent replay -> NO stderr completion skipped warning
        out_replay = py_io.StringIO()
        err_replay = py_io.StringIO()
        with _env_patch({"HERDR_PANE_ID": "w15:p2"}):
            with patch(
                "orchestrator.result_commands.verify_worker_submit_context",
                return_value={"verified": True},
            ), patch("sys.stdout", out_replay), patch("sys.stderr", err_replay):
                rc_replay = public_cli_main(
                    [
                        "result-submit",
                        t_skip,
                        "--result-status",
                        "succeeded",
                        "--result-ref",
                        "out.md",
                        "--summary",
                        "done",
                    ]
                )
        self.assertEqual(rc_replay, 0)
        self.assertTrue(json.loads(out_replay.getvalue())["idempotent_replay"])
        self.assertNotIn("completion skipped", err_replay.getvalue())

        # 3. Sent completion -> NO stderr completion skipped warning
        t_sent = self._create_task(
            task_status="in_progress",
            result_status="pending",
            result_ref=None,
            completion_status="pending",
            completion_reason=None,
        )
        sent_chain = {
            "attempted": True,
            "task_id": t_sent,
            "prompt_sent": True,
            "completion": {
                "action": "resume_requester",
                "status": "sent",
                "reason": None,
                "recovery_count": 0,
            },
            "task": {"status": "succeeded"},
            "result": {"status": "succeeded"},
        }
        out_sent = py_io.StringIO()
        err_sent = py_io.StringIO()
        with _env_patch({"HERDR_PANE_ID": "w15:p2"}):
            with patch(
                "orchestrator.result_commands.verify_worker_submit_context",
                return_value={"verified": True},
            ), patch(
                "orchestrator.result_commands.best_effort_completion_after_result",
                return_value=sent_chain,
            ), patch("sys.stdout", out_sent), patch("sys.stderr", err_sent):
                rc_sent = public_cli_main(
                    [
                        "result-submit",
                        t_sent,
                        "--result-status",
                        "succeeded",
                        "--result-ref",
                        "out.md",
                        "--summary",
                        "done",
                    ]
                )
        self.assertEqual(rc_sent, 0)
        self.assertNotIn("completion skipped", err_sent.getvalue())

    # -------------------------------------------------------------------------
    # ST-06: disappears from doctor & task-list warnings after recover-completion
    # -------------------------------------------------------------------------
    def test_st06_disappears_after_recover_completion(self) -> None:
        tid = self._create_task(completion_reason="requester_working")

        with patch("orchestrator.live_agent.list_live_panes_with_ui", return_value=[]):
            doc1, _ = run_doctor()
        self.assertEqual(
            [c["task_id"] for c in doc1["skipped_completion"]["candidates"]], [tid]
        )
        list1, _ = run_task_list()
        self.assertIn("completion_skipped", list1["tasks"][0]["overview"]["warnings"])

        # Mock requester live & prompt success during recover-completion
        with patch(
            "orchestrator.completion_commands.verify_live_party_cwd",
            return_value=type(
                "Snap",
                (),
                {"agent_name": "w16:p1", "pane_id": "w16:p1", "agent_status": "idle"},
            )(),
        ), patch(
            "orchestrator.completion_commands.run_herdr",
            return_value={"ok": True},
        ):
            rec = run_recover_completion(tid)
        self.assertEqual(rec["completion"]["status"], "sent")

        with patch("orchestrator.live_agent.list_live_panes_with_ui", return_value=[]):
            doc2, _ = run_doctor()
        self.assertEqual(doc2["skipped_completion"]["candidates"], [])

        list2, _ = run_task_list()
        self.assertNotIn("completion_skipped", list2["tasks"][0]["overview"]["warnings"])


if __name__ == "__main__":
    unittest.main()
