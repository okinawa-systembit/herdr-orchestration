from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"
sys.path.insert(0, str(REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts"))

_UUID_1 = "11111111-1111-4111-8111-111111111111"
_UUID_2 = "22222222-2222-4222-8222-222222222222"
_REPO_ID_1 = "repo-uuid-1111"
_REPO_ID_2 = "repo-uuid-2222"
_WORKTREE_1 = "/path/to/worktree1"
_WORKTREE_2 = "/path/to/worktree2"


def _make_canonical_task_doc(
    *,
    task_id: str,
    task_type: str = "review",
    mode: str = "sync",
    task_status: str = "in_progress",
    repo_identity: str = _REPO_ID_1,
    worktree_path: str = _WORKTREE_1,
    branch: str | None = "main",
    requester_role: str = "design",
    requester_name: str = "r-agent-design",
    requester_pane: str = "w1:p1",
    worker_role: str = "reviewer",
    worker_name: str = "r-agent-reviewer",
    worker_pane: str = "w1:p2",
    dispatch_status: str = "sent",
    dispatch_started_at: str | None = "2026-10-22T02:00:00+00:00",
    result_status: str = "pending",
    completion_action: str = "none",
    completion_status: str = "not_applicable",
    completion_recovery_count: int = 0,
    created_at: str = "2026-10-22T02:00:00+00:00",
    updated_at: str = "2026-10-22T02:05:00+00:00",
) -> dict:
    return {
        "schema_version": 1,
        "task": {
            "id": task_id,
            "type": task_type,
            "mode": mode,
            "instruction": "Do review of latest diff",
            "status": task_status,
        },
        "context": {
            "repository": {"identity": repo_identity},
            "worktree": {
                "path": worktree_path,
                "branch": branch,
                "commit": "abcdef1234567890",
            },
        },
        "requester": {
            "role": requester_role,
            "agent_name": requester_name,
            "pane_id": requester_pane,
        },
        "worker": {
            "role": worker_role,
            "agent_name": worker_name,
            "pane_id": worker_pane,
        },
        "dispatch": {
            "status": dispatch_status,
            "reason": "normal dispatch",
            "herdr_prompt_started_at": dispatch_started_at,
        },
        "result": {
            "status": result_status,
            "ref": "artifacts/result.md",
            "summary": "Everything looks good",
        },
        "completion": {
            "action": completion_action,
            "status": completion_status,
            "reason": None,
            "recovery_count": completion_recovery_count,
        },
        "handoff_execution": {
            "lock_owner_pid": 12345,
            "locked_at": "2026-10-22T02:00:00+00:00",
        },
        "timestamps": {
            "created_at": created_at,
            "updated_at": updated_at,
        },
    }


class OrchestratorTaskListTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.state_home = Path(self._tmpdir.name) / "state"
        self.tasks_dir = self.state_home / "herdr-orchestrator" / "tasks"
        self.env = os.environ.copy()
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["HERDR_ENV"] = "1"

    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        target_env = self.env if env is None else env
        return subprocess.run(
            [str(CLI), *args],
            capture_output=True,
            text=True,
            env=target_env,
        )

    def write_task_file(self, task_id: str, doc: dict) -> Path:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        # Ensure mode 700 for directories per security contract
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)
        path = self.tasks_dir / f"{task_id}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def test_registry_directory_not_exists(self) -> None:
        """When registry directory does not exist, return exit 0 with exists=False and do not create dir."""
        self.assertFalse(self.tasks_dir.exists())
        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertEqual(data["command"], "task-list")
        self.assertEqual(data["list_format_version"], 1)
        self.assertFalse(data["registry"]["exists"])
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["total_matched"], 0)
        self.assertEqual(data["returned"], 0)
        self.assertFalse(data["truncated"])
        self.assertTrue(data["complete"])
        self.assertEqual(data["anomalies"], [])
        self.assertEqual(data["concurrent_changes"], [])
        # Directory must NOT be created!
        self.assertFalse(self.tasks_dir.exists())

    def test_registry_empty_directory(self) -> None:
        """When registry directory exists but has 0 tasks, return exit 0 with exists=True and empty tasks."""
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)
        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertTrue(data["registry"]["exists"])
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["total_matched"], 0)
        self.assertTrue(data["complete"])

    def test_multiple_tasks_projection_and_fields(self) -> None:
        """Tasks are projected strictly without instruction, result.ref, result.summary, reason, etc."""
        doc1 = _make_canonical_task_doc(
            task_id=f"task_{_UUID_1}",
            task_type="review",
            mode="sync",
            task_status="in_progress",
        )
        self.write_task_file(f"task_{_UUID_1}", doc1)

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 1)
        self.assertEqual(data["returned"], 1)
        task_item = data["tasks"][0]

        # Verify projected fields
        self.assertEqual(task_item["task"]["id"], f"task_{_UUID_1}")
        self.assertEqual(task_item["task"]["type"], "review")
        self.assertEqual(task_item["task"]["mode"], "sync")
        self.assertEqual(task_item["task"]["status"], "in_progress")
        self.assertEqual(task_item["context"]["repository"]["identity"], _REPO_ID_1)
        self.assertEqual(task_item["context"]["worktree"]["path"], _WORKTREE_1)
        self.assertEqual(task_item["context"]["worktree"]["branch"], "main")
        self.assertEqual(task_item["requester"]["role"], "design")
        self.assertEqual(task_item["requester"]["agent_name"], "r-agent-design")
        self.assertEqual(task_item["worker"]["role"], "reviewer")
        self.assertEqual(task_item["worker"]["agent_name"], "r-agent-reviewer")
        self.assertEqual(task_item["worker"]["pane_id"], "w1:p2")
        self.assertEqual(task_item["dispatch"]["status"], "sent")
        self.assertEqual(task_item["dispatch"]["herdr_prompt_started_at"], "2026-10-22T02:00:00+00:00")
        self.assertEqual(task_item["result"]["status"], "pending")
        self.assertEqual(task_item["completion"]["action"], "none")
        self.assertEqual(task_item["completion"]["status"], "not_applicable")
        self.assertEqual(task_item["completion"]["recovery_count"], 0)
        self.assertEqual(task_item["timestamps"]["created_at"], "2026-10-22T02:00:00+00:00")
        self.assertEqual(task_item["timestamps"]["updated_at"], "2026-10-22T02:05:00+00:00")
        self.assertEqual(task_item["overview"]["state"], "open")
        self.assertEqual(task_item["overview"]["warnings"], [])

        # Excluded fields must NOT be present
        self.assertNotIn("instruction", task_item["task"])
        self.assertNotIn("ref", task_item["result"])
        self.assertNotIn("summary", task_item["result"])
        self.assertNotIn("reason", task_item["dispatch"])
        self.assertNotIn("reason", task_item["completion"])
        self.assertNotIn("commit", task_item["context"]["worktree"])
        self.assertNotIn("handoff_execution", task_item)
        self.assertNotIn("pane_id", task_item["requester"])

    def test_filters_and_or_logic(self) -> None:
        """Test --repository, --worktree, --type, --status filters with AND / OR combinations."""
        # Task 1: repo1, wt1, review, in_progress
        doc1 = _make_canonical_task_doc(
            task_id=f"task_{_UUID_1}",
            repo_identity=_REPO_ID_1,
            worktree_path="/abs/worktree1",
            task_type="review",
            task_status="in_progress",
            dispatch_status="sent",
            result_status="pending",
        )
        # Task 2: repo2, wt2, test, succeeded
        doc2 = _make_canonical_task_doc(
            task_id=f"task_{_UUID_2}",
            repo_identity=_REPO_ID_2,
            worktree_path="/abs/worktree2",
            task_type="test",
            task_status="succeeded",
            dispatch_status="sent",
            result_status="succeeded",
            completion_status="not_applicable",
        )
        self.write_task_file(f"task_{_UUID_1}", doc1)
        self.write_task_file(f"task_{_UUID_2}", doc2)

        # 1. Filter by repo1
        proc = self.run_cli("task-list", "--repository", _REPO_ID_1)
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 1)
        self.assertEqual(data["tasks"][0]["task"]["id"], f"task_{_UUID_1}")
        self.assertEqual(data["filters"]["type"], None)

        # 2. Filter by worktree with trailing slash and normalization
        proc = self.run_cli("task-list", "--worktree", "/abs/worktree2/")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 1)
        self.assertEqual(data["tasks"][0]["task"]["id"], f"task_{_UUID_2}")

        # 3. Filter by type (single string per §3.2)
        proc = self.run_cli("task-list", "--type", "review")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 1)
        self.assertEqual(data["filters"]["type"], "review")

        # 4. Filter by status OR
        proc = self.run_cli("task-list", "--status", "created", "--status", "in_progress")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 1)
        self.assertEqual(data["tasks"][0]["task"]["id"], f"task_{_UUID_1}")

        # 5. Combined AND filter (repo1 AND status=succeeded -> 0 match)
        proc = self.run_cli("task-list", "--repository", _REPO_ID_1, "--status", "succeeded")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 0)

    def test_worktree_relative_path_rejected(self) -> None:
        """--worktree must reject relative path with exit 1 and invalid_input error JSON."""
        proc = self.run_cli("task-list", "--worktree", "relative/path")
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["error"], "invalid_input")
        self.assertFalse(data["retryable"])

    def test_sort_order_created_at_desc_and_tie_breaker(self) -> None:
        """Sort tasks by created_at desc (timezone-aware), tie-breaker task.id asc, unparseable at end."""
        uuid_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        uuid_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        uuid_c = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        uuid_d = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

        # Task A: 03:00 UTC (latest)
        doc_a = _make_canonical_task_doc(task_id=f"task_{uuid_a}", created_at="2026-10-22T03:00:00+00:00")
        # Task B: 11:00 JST (= 02:00 UTC) -> tie with C
        doc_b = _make_canonical_task_doc(task_id=f"task_{uuid_b}", created_at="2026-10-22T11:00:00+09:00")
        # Task C: 02:00 UTC -> tie with B, but uuid_b < uuid_c
        doc_c = _make_canonical_task_doc(task_id=f"task_{uuid_c}", created_at="2026-10-22T02:00:00+00:00")
        # Task D: unparseable timestamp
        doc_d = _make_canonical_task_doc(task_id=f"task_{uuid_d}", created_at="invalid-iso-time")

        self.write_task_file(f"task_{uuid_a}", doc_a)
        self.write_task_file(f"task_{uuid_b}", doc_b)
        self.write_task_file(f"task_{uuid_c}", doc_c)
        self.write_task_file(f"task_{uuid_d}", doc_d)

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 4)
        task_ids = [t["task"]["id"] for t in data["tasks"]]
        # Expected order: A (03:00) -> B (02:00, id b) -> C (02:00, id c) -> D (unparseable at end)
        self.assertEqual(task_ids, [f"task_{uuid_a}", f"task_{uuid_b}", f"task_{uuid_c}", f"task_{uuid_d}"])
        self.assertIn("created_at_unparseable", data["tasks"][3]["overview"]["warnings"])

    def test_limit_and_truncation(self) -> None:
        """--limit truncates returned list, sets truncated=True, total_matched retains full count."""
        for i in range(5):
            uuid_i = f"{i:08x}-0000-4000-8000-000000000000"
            doc = _make_canonical_task_doc(
                task_id=f"task_{uuid_i}",
                created_at=f"2026-10-22T0{i}:00:00+00:00",
            )
            self.write_task_file(f"task_{uuid_i}", doc)

        proc = self.run_cli("task-list", "--limit", "2")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 5)
        self.assertEqual(data["returned"], 2)
        self.assertTrue(data["truncated"])
        self.assertTrue(data["complete"])
        self.assertEqual(len(data["tasks"]), 2)

    def test_overview_state_classification(self) -> None:
        """
        Test overview.state priority rules (B-1 fix):
        1: uncertain: (disp=uncertain and res=pending) or comp=uncertain or task=unknown
        2: open: task in {created, in_progress} or res=pending or comp in {pending, processing, failed}
        3: closed: task in {succeeded, failed, cancelled} and res in {succeeded, failed, unavailable} and comp in {not_applicable, sent, skipped}
        """
        uuid_unc = "11111111-0000-4000-8000-000000000000"
        uuid_open = "22222222-0000-4000-8000-000000000000"
        uuid_closed = "33333333-0000-4000-8000-000000000000"
        uuid_delayed_closed = "44444444-0000-4000-8000-000000000000"
        uuid_delayed_open = "55555555-0000-4000-8000-000000000000"

        # 1. uncertain: dispatch=uncertain and result=pending
        doc_unc = _make_canonical_task_doc(
            task_id=f"task_{uuid_unc}",
            task_status="unknown",
            dispatch_status="uncertain",
            result_status="pending",
        )
        # 2. open: in_progress, result=pending
        doc_open = _make_canonical_task_doc(
            task_id=f"task_{uuid_open}",
            task_status="in_progress",
            dispatch_status="sent",
            result_status="pending",
        )
        # 3. closed: succeeded, result=succeeded, completion=not_applicable
        doc_closed = _make_canonical_task_doc(
            task_id=f"task_{uuid_closed}",
            task_status="succeeded",
            dispatch_status="sent",
            result_status="succeeded",
            completion_status="not_applicable",
        )
        # 4. Delayed result arrived, completion closed -> must be CLOSED (not uncertain! B-1 fix)
        doc_delayed_closed = _make_canonical_task_doc(
            task_id=f"task_{uuid_delayed_closed}",
            task_status="succeeded",
            dispatch_status="uncertain",
            result_status="succeeded",
            completion_status="not_applicable",
        )
        # 5. Delayed result arrived, completion pending -> must be OPEN (recoverable! B-1 fix)
        doc_delayed_open = _make_canonical_task_doc(
            task_id=f"task_{uuid_delayed_open}",
            mode="async",
            task_status="succeeded",
            dispatch_status="uncertain",
            result_status="succeeded",
            completion_action="resume_requester",
            completion_status="pending",
        )

        self.write_task_file(f"task_{uuid_unc}", doc_unc)
        self.write_task_file(f"task_{uuid_open}", doc_open)
        self.write_task_file(f"task_{uuid_closed}", doc_closed)
        self.write_task_file(f"task_{uuid_delayed_closed}", doc_delayed_closed)
        self.write_task_file(f"task_{uuid_delayed_open}", doc_delayed_open)

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        task_by_id = {t["task"]["id"]: t for t in data["tasks"]}
        self.assertEqual(task_by_id[f"task_{uuid_unc}"]["overview"]["state"], "uncertain")
        self.assertEqual(task_by_id[f"task_{uuid_open}"]["overview"]["state"], "open")
        self.assertEqual(task_by_id[f"task_{uuid_closed}"]["overview"]["state"], "closed")
        self.assertEqual(task_by_id[f"task_{uuid_delayed_closed}"]["overview"]["state"], "closed")
        self.assertEqual(task_by_id[f"task_{uuid_delayed_open}"]["overview"]["state"], "open")

    def test_invariants_I1_to_I7_diagnostic_warnings(self) -> None:
        """
        Verify invariant violations trigger inconsistent_state_combination warning,
        and legitimate corner cases do NOT trigger warnings.
        """
        # Case A (Legitimate): delayed result arrival (dispatch=uncertain, task=succeeded, result=succeeded)
        uuid_legit1 = "00000001-0000-4000-8000-000000000000"
        doc_legit1 = _make_canonical_task_doc(
            task_id=f"task_{uuid_legit1}",
            task_status="succeeded",
            dispatch_status="uncertain",
            result_status="succeeded",
            completion_status="not_applicable",
        )
        # Case B (Legitimate): worker unavailable (dispatch=sent, task=failed, result=unavailable)
        uuid_legit2 = "00000002-0000-4000-8000-000000000000"
        doc_legit2 = _make_canonical_task_doc(
            task_id=f"task_{uuid_legit2}",
            task_status="failed",
            dispatch_status="sent",
            result_status="unavailable",
            completion_status="not_applicable",
        )
        # Case C (Violation of I5 & I6): external review example:
        # task=succeeded, dispatch=failed, result=succeeded, completion=sent (mode=async)
        uuid_viol = "00000003-0000-4000-8000-000000000000"
        doc_viol = _make_canonical_task_doc(
            task_id=f"task_{uuid_viol}",
            mode="async",
            task_status="succeeded",
            dispatch_status="failed",
            result_status="succeeded",
            completion_action="resume_requester",
            completion_status="sent",
        )
        # Case D (Violation of I1): dispatch=pending but task=in_progress
        uuid_viol_i1 = "00000004-0000-4000-8000-000000000000"
        doc_viol_i1 = _make_canonical_task_doc(
            task_id=f"task_{uuid_viol_i1}",
            task_status="in_progress",
            dispatch_status="pending",
            result_status="pending",
        )

        self.write_task_file(f"task_{uuid_legit1}", doc_legit1)
        self.write_task_file(f"task_{uuid_legit2}", doc_legit2)
        self.write_task_file(f"task_{uuid_viol}", doc_viol)
        self.write_task_file(f"task_{uuid_viol_i1}", doc_viol_i1)

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        task_by_id = {t["task"]["id"]: t for t in data["tasks"]}

        # Legitimate tasks must have NO inconsistent_state_combination
        self.assertNotIn(
            "inconsistent_state_combination",
            task_by_id[f"task_{uuid_legit1}"]["overview"]["warnings"],
        )
        self.assertNotIn(
            "inconsistent_state_combination",
            task_by_id[f"task_{uuid_legit2}"]["overview"]["warnings"],
        )

        # Inconsistent task C must be classified as closed per truth table, BUT must have warning
        self.assertEqual(task_by_id[f"task_{uuid_viol}"]["overview"]["state"], "closed")
        self.assertIn(
            "inconsistent_state_combination",
            task_by_id[f"task_{uuid_viol}"]["overview"]["warnings"],
        )

        # Inconsistent task D must have warning
        self.assertIn(
            "inconsistent_state_combination",
            task_by_id[f"task_{uuid_viol_i1}"]["overview"]["warnings"],
        )

    def test_ignored_auxiliary_files(self) -> None:
        """Verify normal locks, stamps, dotfiles, non-task json files, and subdirs are silently ignored."""
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)
        (self.tasks_dir / "task_11111111-1111-4111-8111-111111111111.json.lock").write_text("lock\n")
        (self.tasks_dir / "task_11111111-1111-4111-8111-111111111111.handoff.lock").write_text("lock\n")
        (self.tasks_dir / ".tmp.12345").write_text("tmp\n")
        (self.tasks_dir / "cleanup.stamp").write_text("stamp\n")
        (self.tasks_dir / "stale-dispatch.log").write_text("log\n")
        (self.tasks_dir / "last_cleanup_at").write_text("2026-10-22\n")
        (self.tasks_dir / "cleanup.run.lock").write_text("lock\n")
        (self.tasks_dir / "notes.txt").write_text("notes\n")
        # Non-task json file must be ignored per §5.1 (B-5 fix)
        (self.tasks_dir / "notes.json").write_text('{"note": "ignored"}\n')
        sub = self.tasks_dir / "subdir"
        sub.mkdir()
        (sub / "nested.json").write_text("{}\n")

        # Plus one normal task
        doc = _make_canonical_task_doc(task_id=f"task_{_UUID_1}")
        self.write_task_file(f"task_{_UUID_1}", doc)

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total_matched"], 1)
        self.assertEqual(data["anomalies"], [])
        self.assertTrue(data["complete"])

    def test_anomalies_and_exit_code_3(self) -> None:
        """
        Verify all anomaly codes per §5.2:
        invalid_filename, unsafe_entry, invalid_encoding, invalid_json, unsupported_schema, task_id_mismatch, invalid_schema.
        Must yield exit code 3, complete=false, while valid tasks are still returned.
        """
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)

        # 1. Valid task
        doc_valid = _make_canonical_task_doc(task_id=f"task_{_UUID_1}")
        self.write_task_file(f"task_{_UUID_1}", doc_valid)

        # 2. invalid_filename: starts with task_ and ends with .json, but stem is not uuidv4
        (self.tasks_dir / "task_bad_id.json").write_text(json.dumps(doc_valid), encoding="utf-8")

        # 3. unsafe_entry: symlink
        sym_target = self.tasks_dir / f"task_{_UUID_1}.json"
        sym_link = self.tasks_dir / f"task_{_UUID_2}.json"
        try:
            sym_link.symlink_to(sym_target)
        except OSError:
            pass

        # 4. invalid_encoding: non-UTF-8 bytes
        uuid_bad_enc = "33333333-3333-4333-8333-333333333333"
        (self.tasks_dir / f"task_{uuid_bad_enc}.json").write_bytes(b"\xff\xfe\x00\x00")

        # 5. invalid_json: syntax error
        uuid_bad_json = "44444444-4444-4444-8444-444444444444"
        (self.tasks_dir / f"task_{uuid_bad_json}.json").write_text("{broken json", encoding="utf-8")

        # 6. invalid_json: root is array instead of object
        uuid_array_json = "55555555-5555-4555-8555-555555555555"
        (self.tasks_dir / f"task_{uuid_array_json}.json").write_text("[1, 2, 3]", encoding="utf-8")

        # 7. unsupported_schema: schema_version = 2
        uuid_unsupported = "66666666-6666-4666-8666-666666666666"
        doc_unsupported = _make_canonical_task_doc(task_id=f"task_{uuid_unsupported}")
        doc_unsupported["schema_version"] = 2
        (self.tasks_dir / f"task_{uuid_unsupported}.json").write_text(json.dumps(doc_unsupported), encoding="utf-8")

        # 8. task_id_mismatch: filename stem does not match doc["task"]["id"]
        uuid_mismatch = "77777777-7777-4777-8777-777777777777"
        doc_mismatch = _make_canonical_task_doc(task_id=f"task_{_UUID_1}")
        (self.tasks_dir / f"task_{uuid_mismatch}.json").write_text(json.dumps(doc_mismatch), encoding="utf-8")

        # 9. invalid_schema: missing task.type
        uuid_bad_schema = "88888888-8888-4888-8888-888888888888"
        doc_bad_schema = _make_canonical_task_doc(task_id=f"task_{uuid_bad_schema}")
        del doc_bad_schema["task"]["type"]
        (self.tasks_dir / f"task_{uuid_bad_schema}.json").write_text(json.dumps(doc_bad_schema), encoding="utf-8")

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 3, f"Expected exit 3 on anomaly, got {proc.returncode}\n{proc.stdout}")
        data = json.loads(proc.stdout)
        self.assertFalse(data["complete"])
        self.assertEqual(data["returned"], 1)
        self.assertEqual(data["tasks"][0]["task"]["id"], f"task_{_UUID_1}")

        anomaly_codes = {a["code"] for a in data["anomalies"]}
        self.assertIn("invalid_filename", anomaly_codes)
        self.assertIn("invalid_encoding", anomaly_codes)
        self.assertIn("invalid_json", anomaly_codes)
        self.assertIn("unsupported_schema", anomaly_codes)
        self.assertIn("task_id_mismatch", anomaly_codes)
        self.assertIn("invalid_schema", anomaly_codes)
        if sym_link.is_symlink():
            self.assertIn("unsafe_entry", anomaly_codes)

    def test_fifo_handled_as_unsafe_entry(self) -> None:
        """A FIFO entry must not hang task-list and must be recorded as unsafe_entry (B-3 fix)."""
        uuid_fifo = "99999999-9999-4999-8999-999999999999"
        fifo_path = self.tasks_dir / f"task_{uuid_fifo}.json"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)

        if not hasattr(os, "mkfifo"):
            self.skipTest("os.mkfifo not available")

        try:
            os.mkfifo(fifo_path)
        except OSError:
            self.skipTest("mkfifo failed on environment")

        try:
            proc = self.run_cli("task-list")
            self.assertEqual(proc.returncode, 3)
            data = json.loads(proc.stdout)
            self.assertFalse(data["complete"])
            fifo_anomalies = [a for a in data["anomalies"] if a["file"] == f"task_{uuid_fifo}.json"]
            self.assertEqual(len(fifo_anomalies), 1)
            self.assertEqual(fifo_anomalies[0]["code"], "unsafe_entry")
        finally:
            if fifo_path.exists():
                fifo_path.unlink()

    def test_registry_directory_safety_verification(self) -> None:
        """Verify registry_unsafe_path error on symlink directory (symlink rejected) and allow writable directory (§6.9)."""
        # 1. tasks directory is symlink
        real_tasks = self.state_home / "real_tasks"
        real_tasks.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o755)
        os.chmod(real_tasks, 0o755)
        self.tasks_dir.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.tasks_dir.parent, 0o755)

        try:
            self.tasks_dir.symlink_to(real_tasks)
        except OSError:
            self.skipTest("symlink creation failed")

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["error"], "registry_unsafe_path")

        # Cleanup symlink
        self.tasks_dir.unlink()

        # 2. tasks directory is group/world writable (mode 777) - should be allowed under relaxed permissions (§6.9)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.tasks_dir, 0o777)
        try:
            proc = self.run_cli("task-list")
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            self.assertTrue(data["complete"])
        finally:
            os.chmod(self.tasks_dir, 0o755)

    def test_registry_directory_unreadable_on_permission_denied(self) -> None:
        """When tasks directory has no read permissions (mode 000), return registry_unreadable (E-1 fix)."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root user ignores permission bits")

        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o000)
        try:
            proc = self.run_cli("task-list")
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            self.assertEqual(data["error"], "registry_unreadable")
        finally:
            os.chmod(self.tasks_dir, 0o700)

    def test_concurrent_changes_removed(self) -> None:
        """When an entry disappears between scandir and open (ENOENT), record in concurrent_changes and keep complete=True."""
        from orchestrator.list_commands import run_task_list

        doc1 = _make_canonical_task_doc(task_id=f"task_{_UUID_1}")
        self.write_task_file(f"task_{_UUID_1}", doc1)

        uuid_removed = "99999999-9999-4999-8999-999999999999"
        doc_removed = _make_canonical_task_doc(task_id=f"task_{uuid_removed}")
        self.write_task_file(f"task_{uuid_removed}", doc_removed)

        original_open = os.open

        def mock_open(path, flags, *args, **kwargs):
            if str(path).endswith(f"task_{uuid_removed}.json"):
                raise FileNotFoundError(f"No such file: {path}")
            return original_open(path, flags, *args, **kwargs)

        with patch("os.open", side_effect=mock_open):
            with patch.dict(os.environ, self.env):
                payload, exit_code = run_task_list()

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["complete"])
        self.assertEqual(len(payload["concurrent_changes"]), 1)
        self.assertEqual(payload["concurrent_changes"][0]["file"], f"task_{uuid_removed}.json")
        self.assertEqual(payload["concurrent_changes"][0]["task_id"], f"task_{uuid_removed}")
        self.assertEqual(payload["concurrent_changes"][0]["change"], "removed")

    def test_cli_argument_errors(self) -> None:
        """Verify argparse enforces limit range and valid choices with exit 2."""
        # limit 0
        proc = self.run_cli("task-list", "--limit", "0")
        self.assertEqual(proc.returncode, 2)
        # limit 1001
        proc = self.run_cli("task-list", "--limit", "1001")
        self.assertEqual(proc.returncode, 2)
        # limit invalid string
        proc = self.run_cli("task-list", "--limit", "abc")
        self.assertEqual(proc.returncode, 2)
        # status invalid
        proc = self.run_cli("task-list", "--status", "not_a_status")
        self.assertEqual(proc.returncode, 2)
        # unknown flag
        proc = self.run_cli("task-list", "--unknown-flag")
        self.assertEqual(proc.returncode, 2)

    def test_herdr_env_missing(self) -> None:
        """When HERDR_ENV is missing, exit 1 with canonical herdr_env_required error JSON."""
        env_without_herdr = self.env.copy()
        env_without_herdr.pop("HERDR_ENV", None)
        proc = self.run_cli("task-list", env=env_without_herdr)
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["error"], "herdr_env_required")
        self.assertFalse(data["retryable"])


    def test_task_candidate_directory_handled_as_unsafe_entry(self) -> None:
        """A directory named like task_<uuid>.json must be recorded as unsafe_entry (D-1 improvement)."""
        uuid_dir = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        candidate_dir = self.tasks_dir / f"task_{uuid_dir}.json"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)
        candidate_dir.chmod(0o700)

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 3)
        data = json.loads(proc.stdout)
        self.assertFalse(data["complete"])
        unsafe_entries = [a for a in data["anomalies"] if a["code"] == "unsafe_entry"]
        self.assertEqual(len(unsafe_entries), 1)
        self.assertEqual(unsafe_entries[0]["file"], f"task_{uuid_dir}.json")

    def test_missing_task_key_is_invalid_schema_and_fixed_messages(self) -> None:
        """A document missing task key is invalid_schema, and error messages do not leak content (D-2 improvement)."""
        uuid_no_task = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)
        (self.tasks_dir / f"task_{uuid_no_task}.json").write_text('{"schema_version": 1}\n', encoding="utf-8")

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 3)
        data = json.loads(proc.stdout)
        anom = next(a for a in data["anomalies"] if a["file"] == f"task_{uuid_no_task}.json")
        self.assertEqual(anom["code"], "invalid_schema")


    def test_file_size_exceeding_limit_recorded_as_read_error(self) -> None:
        """Task file exceeding 1MB limit must be recorded as read_error anomaly (R62-09)."""
        uuid_valid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        uuid_huge = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)

        # 1. Valid task
        doc_valid = _make_canonical_task_doc(task_id=f"task_{uuid_valid}")
        self.write_task_file(f"task_{uuid_valid}", doc_valid)

        # 2. Huge file (> 1MB)
        huge_bytes = b"x" * (1024 * 1024 + 100)
        (self.tasks_dir / f"task_{uuid_huge}.json").write_bytes(huge_bytes)

        proc = self.run_cli("task-list")
        self.assertEqual(proc.returncode, 3)
        data = json.loads(proc.stdout)
        self.assertFalse(data["complete"])
        self.assertEqual(data["returned"], 1)
        self.assertEqual(data["tasks"][0]["task"]["id"], f"task_{uuid_valid}")

        huge_anom = next(a for a in data["anomalies"] if a["file"] == f"task_{uuid_huge}.json")
        self.assertEqual(huge_anom["code"], "read_error")
        self.assertIn("exceeds maximum allowed limit", huge_anom["message"])

    def test_dir_fd_race_directory_replaced_after_open(self) -> None:
        """
        Verify that even if the tasks directory path is replaced/renamed during scanning,
        listing continues reading from the verified inode via dir_fd (R62-10).
        """
        uuid_orig = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        uuid_fake = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_home, 0o700)
        os.chmod(self.tasks_dir.parent, 0o700)
        os.chmod(self.tasks_dir, 0o700)

        # 1. Write original task in tasks_dir
        doc_orig = _make_canonical_task_doc(task_id=f"task_{uuid_orig}")
        self.write_task_file(f"task_{uuid_orig}", doc_orig)

        real_scandir = os.scandir

        def hooked_scandir(fd_or_path):
            # Simulate directory replacement before scandir reads entries:
            # move original tasks dir and create new one with fake doc
            if self.tasks_dir.exists():
                old_dir = self.tasks_dir.parent / "tasks_old"
                os.rename(self.tasks_dir, old_dir)
                self.tasks_dir.mkdir(mode=0o700)
                doc_fake = _make_canonical_task_doc(task_id=f"task_{uuid_fake}")
                (self.tasks_dir / f"task_{uuid_fake}.json").write_text(json.dumps(doc_fake), encoding="utf-8")
            return real_scandir(fd_or_path)

        from orchestrator.registry.listing import read_task_candidates

        with unittest.mock.patch.dict(
            os.environ,
            {"XDG_STATE_HOME": str(self.state_home), "HERDR_ENV": "1"},
        ):
            with unittest.mock.patch("orchestrator.registry.listing.os.scandir", side_effect=hooked_scandir):
                exists, valid_docs, anomalies, concurrent_changes = read_task_candidates()

        self.assertTrue(exists)
        self.assertEqual(len(valid_docs), 1)
        # Must have read doc_orig from the verified inode, NOT doc_fake from the new path!
        self.assertEqual(valid_docs[0]["task"]["id"], f"task_{uuid_orig}")
        self.assertEqual(anomalies, [])
        self.assertEqual(concurrent_changes, [])


if __name__ == "__main__":
    unittest.main()
