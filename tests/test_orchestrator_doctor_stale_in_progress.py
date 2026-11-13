"""
Tests for doctor stale_in_progress detection & await_human (Issue #174 / DT-01 - DT-08).
Conforms to docs/herdr-orchestrator-doctor-stale-in-progress.md §5.2.
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

from orchestrator.dispatch_commands import (
    DEFAULT_STALE_IN_PROGRESS_SEC,
    run_doctor,
    stale_in_progress_threshold_sec,
)
from orchestrator.paths import tasks_dir
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.io import read_task_document
from orchestrator.result_commands import result_submit


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


class DoctorStaleInProgressTests(unittest.TestCase):
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
                "HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC": None,
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "w15:p1",
            }
        )
        self.env_ctx.__enter__()

        self.req_wt = self.tmp_root / "req_repo"
        self.worker_wt = self.tmp_root / "worker_repo"
        self.other_wt = self.tmp_root / "other_repo"
        self.req_wt.mkdir(parents=True, exist_ok=True)
        self.worker_wt.mkdir(parents=True, exist_ok=True)
        self.other_wt.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.env_ctx.__exit__(None, None, None)
        self._tmp.cleanup()

    def _create_task(
        self,
        *,
        task_status: str = "in_progress",
        result_status: str = "pending",
        dispatch_status: str = "sent",
        worker_cwd: str | None = None,
        worktree_path: str | None = None,
        worker_pane_id: str | None = "w15:p2",
        worker_agent_name: str = "w15:p2",
        updated_at: datetime | None = None,
    ) -> str:
        wt_str = worktree_path if worktree_path is not None else str(self.req_wt)
        worker_payload: dict[str, object] = {
            "role": "review",
            "agent_name": worker_agent_name,
            "pane_id": worker_pane_id,
        }
        if worker_cwd is not None:
            worker_payload["cwd"] = worker_cwd
        doc = registry_ops.create_task(
            {
                "task": {
                    "type": "review",
                    "mode": "async",
                    "instruction": "Check stale in_progress",
                },
                "context": {
                    "repository": {"identity": "26797854-8826-4acf-a5ad-4cea9caaf947"},
                    "worktree": {
                        "path": wt_str,
                        "branch": "main",
                        "commit": None,
                    },
                },
                "requester": {
                    "role": "メイン",
                    "agent_name": "w15:p1",
                    "pane_id": "w15:p1",
                },
                "worker": worker_payload,
                "completion": {"action": "resume_requester"},
            }
        )
        tid = doc["task"]["id"]
        loaded = read_task_document(tid)
        loaded["task"]["status"] = task_status
        loaded["dispatch"]["status"] = dispatch_status
        loaded["result"]["status"] = result_status
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
    # DT-01: worktree_missing & cross-workspace false-positive prevention
    # -------------------------------------------------------------------------
    def test_dt01_worktree_missing_and_cross_workspace_safe(self) -> None:
        now = datetime.now(timezone.utc)
        recent = now - timedelta(seconds=10)
        missing_dir = str(self.tmp_root / "deleted_wt")

        # in_progress with missing worker.cwd
        t_inprog = self._create_task(
            task_status="in_progress",
            worker_cwd=missing_dir,
            worktree_path=str(self.req_wt),
            updated_at=recent,
        )
        # unknown with missing fallback context.worktree.path (worker.cwd unset)
        t_unknown = self._create_task(
            task_status="unknown",
            worker_cwd=None,
            worktree_path=missing_dir,
            updated_at=recent,
        )
        # cross-workspace: requester worktree missing, but worker.cwd exists
        missing_req = str(self.tmp_root / "deleted_req_wt")
        t_cross_ok = self._create_task(
            task_status="in_progress",
            worker_cwd=str(self.worker_wt),
            worktree_path=missing_req,
            updated_at=recent,
        )

        panes = [
            {"pane_id": "w15:p2", "agent": "claude", "agent_status": "idle", "cwd": str(self.worker_wt)},
        ]
        cands = registry_ops.find_stale_in_progress_candidates(
            min_age_sec=86400.0,
            live_panes=panes,
            now=now,
        )
        by_id = {c["task_id"]: c for c in cands}

        self.assertIn(t_inprog, by_id)
        self.assertEqual(by_id[t_inprog]["signal_strength"], "strong")
        self.assertIn("worktree_missing", by_id[t_inprog]["reasons"])
        self.assertEqual(by_id[t_inprog]["suggested_action"], "cancel-task")
        self.assertFalse(by_id[t_inprog]["worker_cwd_exists"])

        self.assertIn(t_unknown, by_id)
        self.assertEqual(by_id[t_unknown]["signal_strength"], "strong")
        self.assertEqual(by_id[t_unknown]["task_status"], "unknown")
        self.assertIn("worktree_missing", by_id[t_unknown]["reasons"])

        self.assertNotIn(t_cross_ok, by_id)

    # -------------------------------------------------------------------------
    # DT-02: worker pane non-live & same/cross worktree movement tolerance
    # -------------------------------------------------------------------------
    def test_dt02_worker_pane_signals_and_movement_tolerance(self) -> None:
        now = datetime.now(timezone.utc)
        recent = now - timedelta(seconds=30)

        worker_sub = self.worker_wt / "subdir"
        worker_sub.mkdir(parents=True, exist_ok=True)
        req_sub = self.req_wt / "sub_pkg"
        req_sub.mkdir(parents=True, exist_ok=True)

        # 1. missing pane
        t_missing = self._create_task(
            worker_cwd=str(self.worker_wt),
            worker_pane_id="w99:p9",
            worker_agent_name="w99:p9",
            updated_at=recent,
        )
        # 2. agent not running (shell idle)
        t_no_agent = self._create_task(
            worker_cwd=str(self.worker_wt),
            worker_pane_id="w15:p3",
            worker_agent_name="w15:p3",
            updated_at=recent,
        )
        # 3. pane context changed to completely different repo
        t_ctx_changed = self._create_task(
            worker_cwd=str(self.worker_wt),
            worker_pane_id="w15:p4",
            worker_agent_name="w15:p4",
            updated_at=recent,
        )
        # 4. cross-workspace: worker moved into subdirectory of worker.cwd -> OK
        t_cross_sub_ok = self._create_task(
            worker_cwd=str(self.worker_wt),
            worktree_path=str(self.req_wt),
            worker_pane_id="w15:p5",
            worker_agent_name="w15:p5",
            updated_at=recent,
        )
        # 5. same worktree: worker.cwd was subdir, pane moved up to worktree root -> OK
        t_same_wt_root_ok = self._create_task(
            worker_cwd=str(req_sub),
            worktree_path=str(self.req_wt),
            worker_pane_id="w15:p6",
            worker_agent_name="w15:p6",
            updated_at=recent,
        )
        # 6. cross-workspace: worker moved into requester's worktree -> context_changed!
        t_cross_to_req_bad = self._create_task(
            worker_cwd=str(self.worker_wt),
            worktree_path=str(self.req_wt),
            worker_pane_id="w15:p7",
            worker_agent_name="w15:p7",
            updated_at=recent,
        )

        panes = [
            {"pane_id": "w15:p3", "agent": None, "agent_status": "none", "cwd": str(self.worker_wt)},
            {"pane_id": "w15:p4", "agent": "codex", "agent_status": "busy", "cwd": str(self.other_wt)},
            {"pane_id": "w15:p5", "agent": "claude", "agent_status": "busy", "cwd": str(worker_sub)},
            {"pane_id": "w15:p6", "agent": "claude", "agent_status": "idle", "cwd": str(self.req_wt)},
            {"pane_id": "w15:p7", "agent": "claude", "agent_status": "idle", "cwd": str(self.req_wt)},
        ]

        with patch("orchestrator.live_agent.list_live_panes_with_ui", return_value=panes):
            doc, code = run_doctor()

        self.assertEqual(code, 0)
        stale = doc["stale_in_progress"]
        self.assertEqual(stale["action"], "await_human")
        self.assertTrue(stale["pane_check_available"])
        by_id = {c["task_id"]: c for c in stale["candidates"]}

        self.assertEqual(by_id[t_missing]["reasons"], ["worker_pane_missing"])
        self.assertEqual(by_id[t_missing]["worker_pane_status"], "missing")
        self.assertEqual(by_id[t_missing]["suggested_action"], "cancel-task")

        self.assertEqual(by_id[t_no_agent]["reasons"], ["worker_agent_not_running"])
        self.assertEqual(by_id[t_no_agent]["worker_pane_status"], "agent_not_running")
        self.assertEqual(by_id[t_no_agent]["suggested_action"], "recover_or_cancel")

        self.assertEqual(by_id[t_ctx_changed]["reasons"], ["worker_pane_context_changed"])
        self.assertEqual(by_id[t_ctx_changed]["worker_pane_status"], "context_changed")
        self.assertEqual(by_id[t_ctx_changed]["suggested_action"], "cancel-task")

        self.assertNotIn(t_cross_sub_ok, by_id)
        self.assertNotIn(t_same_wt_root_ok, by_id)

        self.assertIn(t_cross_to_req_bad, by_id)
        self.assertEqual(by_id[t_cross_to_req_bad]["reasons"], ["worker_pane_context_changed"])

    # -------------------------------------------------------------------------
    # DT-03: HERDR_ENV != 1 / pane list unavailable fallback
    # -------------------------------------------------------------------------
    def test_dt03_pane_unavailable_fallback(self) -> None:
        missing_dir = str(self.tmp_root / "gone_dir")
        t_live_dir = self._create_task(worker_cwd=str(self.worker_wt), worker_pane_id="w99:p9")
        t_gone_dir = self._create_task(worker_cwd=missing_dir, worker_pane_id="w99:p9")

        with _env_patch({"HERDR_ENV": None}):
            doc, code = run_doctor()

        self.assertEqual(code, 1)
        stale = doc["stale_in_progress"]
        self.assertFalse(stale["pane_check_available"])
        by_id = {c["task_id"]: c for c in stale["candidates"]}

        # Should NOT falsely flag missing pane when pane list is unavailable
        self.assertNotIn(t_live_dir, by_id)

        # worktree_missing should still be detected with worker_pane_status == "unavailable"
        self.assertIn(t_gone_dir, by_id)
        self.assertEqual(by_id[t_gone_dir]["worker_pane_status"], "unavailable")
        self.assertEqual(by_id[t_gone_dir]["reasons"], ["worktree_missing"])

    # -------------------------------------------------------------------------
    # DT-04: threshold boundary, env override, and invalid env fallback
    # -------------------------------------------------------------------------
    def test_dt04_threshold_boundary_and_env_override(self) -> None:
        now = datetime.now(timezone.utc)
        t_under = self._create_task(
            worker_cwd=str(self.worker_wt),
            updated_at=now - timedelta(seconds=99),
        )
        t_over = self._create_task(
            worker_cwd=str(self.worker_wt),
            updated_at=now - timedelta(seconds=105),
        )
        t_strong_and_old = self._create_task(
            worker_cwd=str(self.tmp_root / "missing_wt"),
            updated_at=now - timedelta(seconds=120),
        )

        panes = [
            {"pane_id": "w15:p2", "agent": "claude", "agent_status": "busy", "cwd": str(self.worker_wt)},
        ]
        with _env_patch({"HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC": "100"}):
            self.assertEqual(stale_in_progress_threshold_sec(), 100.0)
            cands = registry_ops.find_stale_in_progress_candidates(
                min_age_sec=stale_in_progress_threshold_sec(),
                live_panes=panes,
                now=now,
            )

        by_id = {c["task_id"]: c for c in cands}
        self.assertNotIn(t_under, by_id)
        self.assertIn(t_over, by_id)
        self.assertEqual(by_id[t_over]["signal_strength"], "weak")
        self.assertEqual(by_id[t_over]["reasons"], ["updated_at_stale"])
        self.assertEqual(by_id[t_over]["suggested_action"], "verify_live_task")

        self.assertIn(t_strong_and_old, by_id)
        self.assertEqual(by_id[t_strong_and_old]["signal_strength"], "strong")
        self.assertIn("worktree_missing", by_id[t_strong_and_old]["reasons"])
        self.assertIn("updated_at_stale", by_id[t_strong_and_old]["reasons"])

        # Invalid env values must warn on stderr and fallback to DEFAULT_STALE_IN_PROGRESS_SEC
        for bad_val in ("abc", "nan", "inf", "-1"):
            buf = py_io.StringIO()
            with _env_patch({"HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC": bad_val}):
                with patch("sys.stderr", buf):
                    val = stale_in_progress_threshold_sec()
            self.assertEqual(val, DEFAULT_STALE_IN_PROGRESS_SEC)
            self.assertIn("warning:", buf.getvalue())

    # -------------------------------------------------------------------------
    # DT-05: healthy long-running task is not flagged
    # -------------------------------------------------------------------------
    def test_dt05_healthy_long_running_task_not_flagged(self) -> None:
        now = datetime.now(timezone.utc)
        t_long = self._create_task(
            worker_cwd=str(self.worker_wt),
            updated_at=now - timedelta(hours=1),
        )
        panes = [
            {"pane_id": "w15:p2", "agent": "claude", "agent_status": "busy", "cwd": str(self.worker_wt)},
        ]
        cands = registry_ops.find_stale_in_progress_candidates(
            min_age_sec=DEFAULT_STALE_IN_PROGRESS_SEC,
            live_panes=panes,
            now=now,
        )
        self.assertNotIn(t_long, [c["task_id"] for c in cands])

    # -------------------------------------------------------------------------
    # DT-06: excluded statuses (created, terminal, result != pending)
    # -------------------------------------------------------------------------
    def test_dt06_excluded_statuses(self) -> None:
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=5)
        missing_dir = str(self.tmp_root / "gone_wt")

        excluded_ids = [
            self._create_task(task_status="created", dispatch_status="pending", worker_cwd=missing_dir, updated_at=old),
            self._create_task(task_status="succeeded", result_status="succeeded", worker_cwd=missing_dir, updated_at=old),
            self._create_task(task_status="failed", result_status="failed", worker_cwd=missing_dir, updated_at=old),
            self._create_task(task_status="cancelled", result_status="pending", worker_cwd=missing_dir, updated_at=old),
            self._create_task(task_status="in_progress", result_status="succeeded", worker_cwd=missing_dir, updated_at=old),
        ]
        cands = registry_ops.find_stale_in_progress_candidates(
            min_age_sec=DEFAULT_STALE_IN_PROGRESS_SEC,
            live_panes=[],
            now=now,
        )
        cand_ids = {c["task_id"] for c in cands}
        for tid in excluded_ids:
            self.assertNotIn(tid, cand_ids)

    # -------------------------------------------------------------------------
    # DT-07: read-only, corrupt JSON tolerance, exit code preserved
    # -------------------------------------------------------------------------
    def test_dt07_readonly_corrupt_tolerance_and_exit_code(self) -> None:
        # 1. Empty XDG_STATE_HOME: tasks/ must NOT be created
        tdir = tasks_dir()
        self.assertFalse(tdir.exists())
        doc, code = run_doctor()
        self.assertEqual(code, 0)
        self.assertFalse(tdir.exists())
        self.assertEqual(doc["stale_in_progress"]["candidates"], [])
        self.assertEqual(doc["stale_dispatch"]["candidates"], [])

        # 2. With candidate + corrupt JSON file: no crash, no mutation, no lock files created
        missing_dir = str(self.tmp_root / "gone_wt")
        tid = self._create_task(worker_cwd=missing_dir)
        task_file = tdir / f"{tid}.json"
        corrupt_file = tdir / "task_00000000-0000-4000-8000-000000000000.json"
        corrupt_file.write_text("{corrupt json", encoding="utf-8")

        before_content = task_file.read_text(encoding="utf-8")
        before_mtime = task_file.stat().st_mtime_ns
        before_files = sorted(p.name for p in tdir.iterdir())

        with patch("orchestrator.live_agent.list_live_panes_with_ui", return_value=[]):
            doc_env1, code_env1 = run_doctor()
        self.assertEqual(code_env1, 0)
        self.assertEqual(doc_env1["stale_in_progress"]["summary"]["total_candidates"], 1)

        with _env_patch({"HERDR_ENV": None}):
            doc_noenv, code_noenv = run_doctor()
        self.assertEqual(code_noenv, 1)
        self.assertEqual(doc_noenv["stale_in_progress"]["summary"]["total_candidates"], 1)

        after_content = task_file.read_text(encoding="utf-8")
        after_mtime = task_file.stat().st_mtime_ns
        after_files = sorted(p.name for p in tdir.iterdir())
        self.assertEqual(before_content, after_content)
        self.assertEqual(before_mtime, after_mtime)
        self.assertEqual(before_files, after_files)

    # -------------------------------------------------------------------------
    # DT-08: disappears from candidates after terminalization
    # -------------------------------------------------------------------------
    def test_dt08_disappears_after_cancel_or_result_submit(self) -> None:
        t_cancel = self._create_task(worker_cwd=str(self.tmp_root / "gone_wt"))
        t_submit = self._create_task(worker_cwd=str(self.worker_wt))

        with patch("orchestrator.live_agent.list_live_panes_with_ui", return_value=[]):
            doc1, _ = run_doctor()
        ids1 = {c["task_id"] for c in doc1["stale_in_progress"]["candidates"]}
        self.assertIn(t_cancel, ids1)
        self.assertIn(t_submit, ids1)

        registry_ops.cancel_task(t_cancel, reason="cleanup after doctor detection")
        with _env_patch({"HERDR_PANE_ID": "w15:p2"}):
            with patch(
                "orchestrator.result_commands.verify_worker_submit_context",
                return_value={"verified": True},
            ):
                result_submit(
                    t_submit,
                    result_status="unavailable",
                    result_ref=None,
                    summary="worker stopped",
                )

        with patch("orchestrator.live_agent.list_live_panes_with_ui", return_value=[]):
            doc2, _ = run_doctor()
        ids2 = {c["task_id"] for c in doc2["stale_in_progress"]["candidates"]}
        self.assertNotIn(t_cancel, ids2)
        self.assertNotIn(t_submit, ids2)


if __name__ == "__main__":
    unittest.main()
