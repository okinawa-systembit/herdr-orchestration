from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from orchestrator.identity import get_or_create_repository_identity, get_repository_identity
from orchestrator.live_agent import LiveAgentError, _agent_from_herdr_payload, verify_live_agent_context
from orchestrator.naming import expected_agent_name
from orchestrator.role_bindings import (
    RoleBindingError,
    clear_role_binding,
    get_role_bindings,
    resolve_pane_by_role,
    resolve_role_by_pane,
    role_bindings_file_path,
    set_role_binding,
)
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"
_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]
_SAMPLE_UUID = "550e8400-e29b-41d4-a716-446655440000"


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(worktree), *args],
        check=True,
        capture_output=True,
    )


def _init_repo(root: Path, name: str = "wt") -> Path:
    repo = root / name
    subprocess.run(
        ["git", *_GIT_IDENTITY, "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    (repo / "artifacts").mkdir(exist_ok=True)
    (repo / "artifacts" / "review.md").write_text("LGTM\n", encoding="utf-8")
    return repo


def _run_cli(
    *args: str,
    env: dict[str, str],
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or REPO_ROOT),
        check=False,
    )


class RoleBindingsUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.state_home = Path(self._tmpdir.name) / "state"
        self._old_xdg = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.state_home)

    def tearDown(self) -> None:
        if self._old_xdg is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._old_xdg

    def test_set_get_and_1to1_bijection(self) -> None:
        self.assertEqual(get_role_bindings(_SAMPLE_UUID), {})
        b1 = set_role_binding(_SAMPLE_UUID, "implementer", "w15:p1")
        self.assertEqual(b1, {"implementer": "w15:p1"})
        b2 = set_role_binding(_SAMPLE_UUID, "reviewer", "w15:p2")
        self.assertEqual(b2, {"implementer": "w15:p1", "reviewer": "w15:p2"})

        self.assertEqual(resolve_pane_by_role(_SAMPLE_UUID, "implementer"), "w15:p1")
        self.assertEqual(resolve_pane_by_role(_SAMPLE_UUID, "reviewer"), "w15:p2")
        self.assertEqual(resolve_role_by_pane(_SAMPLE_UUID, "w15:p1"), "implementer")
        self.assertEqual(resolve_role_by_pane(_SAMPLE_UUID, "w15:p2"), "reviewer")

        # Reassigning w15:p1 to 'design' removes 'implementer' binding (1-to-1 bijection)
        b3 = set_role_binding(_SAMPLE_UUID, "design", "w15:p1")
        self.assertEqual(b3, {"design": "w15:p1", "reviewer": "w15:p2"})
        self.assertIsNone(resolve_pane_by_role(_SAMPLE_UUID, "implementer"))
        self.assertEqual(resolve_role_by_pane(_SAMPLE_UUID, "w15:p1"), "design")

    def test_clear_single_and_all(self) -> None:
        set_role_binding(_SAMPLE_UUID, "implementer", "w15:p1")
        set_role_binding(_SAMPLE_UUID, "reviewer", "w15:p2")

        after_single = clear_role_binding(_SAMPLE_UUID, role="implementer")
        self.assertEqual(after_single, {"reviewer": "w15:p2"})

        after_all = clear_role_binding(_SAMPLE_UUID, clear_all=True)
        self.assertEqual(after_all, {})

    def test_validation_errors(self) -> None:
        with self.assertRaises(RoleBindingError):
            set_role_binding(_SAMPLE_UUID, "unbound", "w15:p1")
        with self.assertRaises(RoleBindingError):
            set_role_binding(_SAMPLE_UUID, "bad role with spaces", "w15:p1")
        with self.assertRaises(RoleBindingError):
            set_role_binding(_SAMPLE_UUID, "-flag", "w15:p1")
        with self.assertRaises(RoleBindingError):
            set_role_binding(_SAMPLE_UUID, "a/b", "w15:p1")
        with self.assertRaises(RoleBindingError):
            set_role_binding(_SAMPLE_UUID, "reviewer", "")
        with self.assertRaises(RoleBindingError):
            set_role_binding(_SAMPLE_UUID, "reviewer", "bad pane id with spaces")
        with self.assertRaises(RoleBindingError):
            clear_role_binding(_SAMPLE_UUID, role="reviewer", clear_all=True)
        with self.assertRaises(RoleBindingError):
            clear_role_binding(_SAMPLE_UUID)

    def test_rejects_symlink_role_bindings_file(self) -> None:
        set_role_binding(_SAMPLE_UUID, "implementer", "w15:p1")
        path = role_bindings_file_path(_SAMPLE_UUID)
        target = Path(self._tmpdir.name) / "evil.json"
        target.write_text("{}", encoding="utf-8")
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(RoleBindingError) as ctx:
            get_role_bindings(_SAMPLE_UUID)
        self.assertEqual(ctx.exception.code, "role_binding_unsafe_path")

    def test_unnamed_live_agent_falls_back_to_pane_id(self) -> None:
        payload = {
            "result": {
                "agent": {
                    "agent": "claude",
                    "agent_status": "idle",
                    "foreground_cwd": "/tmp/repo",
                    "pane_id": "w15:p2",
                }
            }
        }
        snap = _agent_from_herdr_payload(payload)
        self.assertEqual(snap.name, "w15:p2")
        self.assertEqual(snap.pane_id, "w15:p2")

    def test_corrupted_role_bindings_file_raises_role_binding_corrupted(self) -> None:
        set_role_binding(_SAMPLE_UUID, "implementer", "w15:p1")
        path = role_bindings_file_path(_SAMPLE_UUID)
        path.write_text("{corrupted json", encoding="utf-8")
        with self.assertRaises(RoleBindingError) as ctx:
            get_role_bindings(_SAMPLE_UUID)
        self.assertEqual(ctx.exception.code, "role_binding_corrupted")

        path.write_text(json.dumps({"schema_version": 999, "roles": {}}), encoding="utf-8")
        with self.assertRaises(RoleBindingError) as ctx2:
            get_role_bindings(_SAMPLE_UUID)
        self.assertEqual(ctx2.exception.code, "role_binding_corrupted")

    def test_resolve_role_by_pane_returns_none_on_manual_duplicate_panes(self) -> None:
        set_role_binding(_SAMPLE_UUID, "implementer", "w15:p1")
        path = role_bindings_file_path(_SAMPLE_UUID)
        # Manually craft duplicate pane mapping on disk
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repository_id": _SAMPLE_UUID,
                    "roles": {"implementer": "w15:p1", "design": "w15:p1"},
                }
            ),
            encoding="utf-8",
        )
        self.assertIsNone(resolve_role_by_pane(_SAMPLE_UUID, "w15:p1"))

    def test_concurrent_set_role_binding_maintains_valid_1to1_state(self) -> None:
        errors: list[Exception] = []
        roles = ["implementer", "reviewer", "design", "orchestrator"]

        def worker(idx: int) -> None:
            try:
                for step in range(10):
                    role = roles[(idx + step) % len(roles)]
                    pane = f"w15:p{(idx + step) % 4 + 1}"
                    set_role_binding(_SAMPLE_UUID, role, pane)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        final_bindings = get_role_bindings(_SAMPLE_UUID)
        # Verify 1-to-1 uniqueness of pane_id values
        panes = list(final_bindings.values())
        self.assertEqual(len(panes), len(set(panes)))


class RoleBindingE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.worktree = _init_repo(self.root)
        self.state_home = self.root / "state"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.prompt_log = self.root / "prompts.log"

        # Fake herdr that returns UNNAMED agents (no "name" field in payload, matching native Herdr resume)
        fake_herdr = self.fake_bin / "herdr"
        fake_herdr.write_text(
            """#!/usr/bin/env python3
import json, os, sys

cwd = os.environ.get("FAKE_AGENT_CWD", "/nonexistent")
p1_name = os.environ.get("FAKE_P1_NAME", "")

if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "get":
    target = sys.argv[3]
    if target == "w15:p1" or (p1_name and target == p1_name):
        agent_obj = {
            "agent": "agy",
            "agent_status": os.environ.get("FAKE_P1_STATUS", "idle"),
            "foreground_cwd": cwd,
            "pane_id": "w15:p1",
            "terminal_id": "term_w15:p1",
        }
        if p1_name:
            agent_obj["name"] = p1_name
        print(json.dumps({"result": {"agent": agent_obj}}))
        raise SystemExit(0)
    if target in ("w15:p2", "w15:p3", "w15:p4"):
        print(json.dumps({
            "result": {
                "agent": {
                    "agent": "claude",
                    "agent_status": os.environ.get("FAKE_P2_STATUS", "idle"),
                    "foreground_cwd": cwd,
                    "pane_id": target,
                    "terminal_id": f"term_{target}",
                }
            }
        }))
        raise SystemExit(0)
    sys.stderr.write(json.dumps({"error": {"code": "agent_not_found", "message": f"not found: {target}"}}))
    raise SystemExit(1)

if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "prompt":
    target = sys.argv[3]
    text = sys.argv[4] if len(sys.argv) >= 5 else ""
    log = os.environ.get("FAKE_PROMPT_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"{target}|{text.splitlines()[0] if text else ''}\\n")
    print(json.dumps({"result": {"prompt": "sent"}}))
    raise SystemExit(0)

raise SystemExit(99)
""",
            encoding="utf-8",
        )
        fake_herdr.chmod(fake_herdr.stat().st_mode | stat.S_IXUSR)

        self.env = os.environ.copy()
        self.env["HERDR_ENV"] = "1"
        self.env["HERDR_PANE_ID"] = "w15:p1"
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["PATH"] = f"{self.fake_bin}:{self.env.get('PATH', '')}"
        self.env["FAKE_AGENT_CWD"] = str(self.worktree)
        self.env["FAKE_PROMPT_LOG"] = str(self.prompt_log)

    def test_role_cli_help_japanese_and_no_section_symbol(self) -> None:
        for args in (
            ("role", "--help"),
            ("role", "set", "--help"),
            ("role", "show", "--help"),
            ("role", "clear", "--help"),
        ):
            proc = _run_cli(*args, env=self.env, cwd=self.worktree)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("§", proc.stdout)

    def test_role_cli_set_show_clear_workflow(self) -> None:
        # 1. 'role set' using implicit HERDR_PANE_ID (w15:p1) auto-initializes repo UUID
        res_set1 = _run_cli("role", "set", "implementer", env=self.env, cwd=self.worktree)
        self.assertEqual(res_set1.returncode, 0, res_set1.stderr)
        data1 = json.loads(res_set1.stdout)
        self.assertEqual(data1["command"], "role")
        self.assertEqual(data1["action"], "set")
        self.assertEqual(data1["role"], "implementer")
        self.assertEqual(data1["pane_id"], "w15:p1")
        self.assertNotIn("warning", data1)
        repo_id = get_repository_identity(self.worktree)
        self.assertEqual(data1["repository_id"], repo_id)

        # 2. 'role set' with explicit --pane w15:p2
        res_set2 = _run_cli(
            "role", "set", "reviewer", "--pane", "w15:p2", env=self.env, cwd=self.worktree
        )
        self.assertEqual(res_set2.returncode, 0, res_set2.stderr)
        data2 = json.loads(res_set2.stdout)
        self.assertNotIn("warning", data2)
        self.assertEqual(
            data2["roles"],
            {"implementer": "w15:p1", "reviewer": "w15:p2"},
        )

        # 3. 'role show'
        res_show = _run_cli("role", "show", env=self.env, cwd=self.worktree)
        self.assertEqual(res_show.returncode, 0, res_show.stderr)
        data_show = json.loads(res_show.stdout)
        self.assertEqual(
            data_show["roles"],
            {"implementer": "w15:p1", "reviewer": "w15:p2"},
        )

        # 4. 'role clear reviewer'
        res_clear1 = _run_cli("role", "clear", "reviewer", env=self.env, cwd=self.worktree)
        self.assertEqual(res_clear1.returncode, 0, res_clear1.stderr)
        self.assertEqual(json.loads(res_clear1.stdout)["roles"], {"implementer": "w15:p1"})

        # 5. 'role clear --all'
        res_clear_all = _run_cli("role", "clear", "--all", env=self.env, cwd=self.worktree)
        self.assertEqual(res_clear_all.returncode, 0, res_clear_all.stderr)
        self.assertEqual(json.loads(res_clear_all.stdout)["roles"], {})

        # 6. 'role set' on nonexistent/unstarted pane in HERDR_ENV=1 fails with role_target_agent_not_running (CW-15)
        res_err = _run_cli(
            "role", "set", "design", "--pane", "w99:p9", env=self.env, cwd=self.worktree
        )
        self.assertEqual(res_err.returncode, 1)
        err_out = json.loads(res_err.stdout)
        self.assertEqual(err_out["error"], "role_target_agent_not_running")

    def test_unnamed_agents_handoff_result_submit_and_completion_via_pane_id(self) -> None:
        # Bind implementer -> w15:p1, reviewer -> w15:p2
        _run_cli("role", "set", "implementer", "--pane", "w15:p1", env=self.env, cwd=self.worktree)
        _run_cli("role", "set", "reviewer", "--pane", "w15:p2", env=self.env, cwd=self.worktree)

        # 1. Async handoff from w15:p1 (implementer) to reviewer (w15:p2) without any agent rename
        res_handoff = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--worker-role",
            "reviewer",
            "--task-type",
            "review",
            "--instruction",
            "review unnamed pane routing",
            env=self.env,
            cwd=self.worktree,
        )
        self.assertEqual(res_handoff.returncode, 0, res_handoff.stdout + res_handoff.stderr)
        handoff_out = json.loads(res_handoff.stdout)
        task_id = handoff_out["task_id"]
        self.assertTrue(handoff_out["prompt_sent"])
        self.assertEqual(handoff_out["dispatch"]["status"], "sent")

        # Verify task-status records pane_id routing targets
        res_status = _run_cli("task-status", task_id, env=self.env, cwd=self.worktree)
        self.assertEqual(res_status.returncode, 0)
        task_doc = json.loads(res_status.stdout)["task"]
        self.assertEqual(task_doc["requester"]["role"], "implementer")
        self.assertEqual(task_doc["requester"]["pane_id"], "w15:p1")
        self.assertEqual(task_doc["requester"]["agent_name"], "w15:p1")
        self.assertEqual(task_doc["worker"]["role"], "reviewer")
        self.assertEqual(task_doc["worker"]["pane_id"], "w15:p2")
        self.assertEqual(task_doc["worker"]["agent_name"], "w15:p2")

        # Verify wrong pane (w15:p1) cannot submit worker result
        bad_submit = _run_cli(
            "result-submit",
            task_id,
            "--result-status",
            "succeeded",
            "--result-ref",
            "artifacts/review.md",
            env=self.env,
            cwd=self.worktree,
        )
        self.assertEqual(bad_submit.returncode, 1)
        self.assertEqual(json.loads(bad_submit.stdout)["error"], "worker_agent_mismatch")

        # 2. Worker (w15:p2) submits result -> triggers completion back to w15:p1
        worker_env = self.env.copy()
        worker_env["HERDR_PANE_ID"] = "w15:p2"
        ok_submit = _run_cli(
            "result-submit",
            task_id,
            "--result-status",
            "succeeded",
            "--result-ref",
            "artifacts/review.md",
            "--summary",
            "all good",
            env=worker_env,
            cwd=self.worktree,
        )
        self.assertEqual(ok_submit.returncode, 0, ok_submit.stdout + ok_submit.stderr)
        submit_out = json.loads(ok_submit.stdout)
        self.assertEqual(submit_out["task"]["status"], "succeeded")
        self.assertEqual(submit_out["completion_chain"]["completion"]["status"], "sent")

        # Verify prompt log: 1st prompt to w15:p2, 2nd prompt (completion) to w15:p1
        prompts = self.prompt_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(prompts, [f"w15:p2|{task_id}", f"w15:p1|{task_id}"])

    def test_rebind_during_inflight_task_uses_saved_registry_locators_only(self) -> None:
        # B-1 / N-1 regression test:
        # Start async task with implementer=w15:p1, reviewer=w15:p2
        _run_cli("role", "set", "implementer", "--pane", "w15:p1", env=self.env, cwd=self.worktree)
        _run_cli("role", "set", "reviewer", "--pane", "w15:p2", env=self.env, cwd=self.worktree)

        res_handoff = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--worker-role",
            "reviewer",
            "--task-type",
            "review",
            "--instruction",
            "in-flight rebind test",
            env=self.env,
            cwd=self.worktree,
        )
        self.assertEqual(res_handoff.returncode, 0, res_handoff.stdout + res_handoff.stderr)
        task_id = json.loads(res_handoff.stdout)["task_id"]

        # Mid-flight: rebind implementer -> w15:p3 and reviewer -> w15:p4
        _run_cli("role", "set", "implementer", "--pane", "w15:p3", env=self.env, cwd=self.worktree)
        _run_cli("role", "set", "reviewer", "--pane", "w15:p4", env=self.env, cwd=self.worktree)

        # N-1: newly bound reviewer pane (w15:p4) must NOT be allowed to submit for task assigned to w15:p2
        new_worker_env = self.env.copy()
        new_worker_env["HERDR_PANE_ID"] = "w15:p4"
        bad_submit = _run_cli(
            "result-submit",
            task_id,
            "--result-status",
            "succeeded",
            "--result-ref",
            "artifacts/review.md",
            env=new_worker_env,
            cwd=self.worktree,
        )
        self.assertEqual(bad_submit.returncode, 1)
        self.assertEqual(json.loads(bad_submit.stdout)["error"], "worker_agent_mismatch")

        # B-1: original worker pane (w15:p2) submits -> completion must go to original requester (w15:p1), NOT w15:p3
        orig_worker_env = self.env.copy()
        orig_worker_env["HERDR_PANE_ID"] = "w15:p2"
        ok_submit = _run_cli(
            "result-submit",
            task_id,
            "--result-status",
            "succeeded",
            "--result-ref",
            "artifacts/review.md",
            "--summary",
            "completed by original worker",
            env=orig_worker_env,
            cwd=self.worktree,
        )
        self.assertEqual(ok_submit.returncode, 0, ok_submit.stdout + ok_submit.stderr)
        submit_out = json.loads(ok_submit.stdout)
        self.assertEqual(submit_out["completion_chain"]["completion"]["status"], "sent")

        prompts = self.prompt_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(prompts, [f"w15:p2|{task_id}", f"w15:p1|{task_id}"])

    def test_pattern_a_requester_rejected_when_role_is_bound_to_different_pane(self) -> None:
        # B-1 condition 2 regression test:
        # Repo has implementer bound to w15:p3, reviewer bound to w15:p2.
        # Caller is on w15:p1 (unbound) but carries Pattern A name for implementer.
        repo_id = get_or_create_repository_identity(self.worktree)
        _run_cli("role", "set", "implementer", "--pane", "w15:p3", env=self.env, cwd=self.worktree)
        _run_cli("role", "set", "reviewer", "--pane", "w15:p2", env=self.env, cwd=self.worktree)

        mixed_env = self.env.copy()
        mixed_env["HERDR_PANE_ID"] = "w15:p1"
        mixed_env["FAKE_P1_NAME"] = expected_agent_name(repo_id, "implementer")

        res_handoff = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--worker-role",
            "reviewer",
            "--task-type",
            "review",
            "--instruction",
            "should fail due to conflicting role binding",
            env=mixed_env,
            cwd=self.worktree,
        )
        self.assertEqual(res_handoff.returncode, 1)
        err = json.loads(res_handoff.stdout)
        self.assertEqual(err["error"], "requester_context_unverified")
        self.assertIn("bound to pane", err["message"])


class CrossWorkspaceRoleBindingTests(unittest.TestCase):
    """Comprehensive tests for Issue #74 (CW-01 through CW-21)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.repo_a = _init_repo(self.root, "repo-a")
        self.repo_b = _init_repo(self.root, "repo-b")
        self.nogit_dir = self.root / "nogit-logs"
        self.nogit_dir.mkdir()
        self.ticket_dir = self.root / "tickets-mcp-workspace"
        self.ticket_dir.mkdir()

        self.state_home = self.root / "state"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.prompt_log = self.root / "prompts.log"
        self.rename_log = self.root / "renames.log"
        self.herdr_state_file = self.root / "herdr_state.json"

        self._write_herdr_state(
            workspaces=[
                {"workspace_id": "w15", "label": "project-a-ws", "number": 1},
                {"workspace_id": "w16", "label": "tickets-ws", "number": 2},
                {"workspace_id": "w17", "label": "logs-ws", "number": 3},
            ],
            tabs=[
                {"tab_id": "w15:t1", "workspace_id": "w15", "number": 1, "label": "Tab 1"},
                {"tab_id": "w16:t1", "workspace_id": "w16", "number": 1, "label": "Tab 1"},
                {"tab_id": "w17:t1", "workspace_id": "w17", "number": 1, "label": "Tab 1"},
            ],
            panes=[
                {
                    "pane_id": "w15:p1",
                    "workspace_id": "w15",
                    "tab_id": "w15:t1",
                    "terminal_id": "term_15_1",
                    "agent": "agy",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_a_impl"},
                    "foreground_cwd": str(self.repo_a.resolve()),
                },
                {
                    "pane_id": "w15:p2",
                    "workspace_id": "w15",
                    "tab_id": "w15:t1",
                    "terminal_id": "term_15_2",
                    "agent": "claude",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_a_rev"},
                    "foreground_cwd": str(self.repo_a.resolve()),
                },
                {
                    "pane_id": "w16:p1",
                    "workspace_id": "w16",
                    "tab_id": "w16:t1",
                    "terminal_id": "term_16_1",
                    "agent": "claude",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_ticket_1"},
                    "foreground_cwd": str(self.ticket_dir.resolve()),
                },
                {
                    "pane_id": "w17:p1",
                    "workspace_id": "w17",
                    "tab_id": "w17:t1",
                    "terminal_id": "term_17_1",
                    "agent": "agy",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_logs_1"},
                    "foreground_cwd": str(self.nogit_dir.resolve()),
                },
            ],
        )

        fake_herdr = self.fake_bin / "herdr"
        fake_herdr.write_text(
            """#!/usr/bin/env python3
import json, os, sys

state_path = os.environ["FAKE_HERDR_STATE"]
state = json.loads(open(state_path, encoding="utf-8").read())

if "--version" in sys.argv:
    print("herdr 0.7.0")
    raise SystemExit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "workspace" and sys.argv[2] == "list":
    print(json.dumps({"result": {"workspaces": state.get("workspaces", [])}}))
    raise SystemExit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "tab" and sys.argv[2] == "list":
    print(json.dumps({"result": {"tabs": state.get("tabs", [])}}))
    raise SystemExit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "pane" and sys.argv[2] == "list":
    print(json.dumps({"result": {"panes": state.get("panes", [])}}))
    raise SystemExit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "pane" and sys.argv[2] == "rename":
    rlog = os.environ.get("FAKE_RENAME_LOG")
    if rlog:
        with open(rlog, "a", encoding="utf-8") as fh:
            fh.write(" ".join(sys.argv[3:]) + "\\n")
    print(json.dumps({"result": {"renamed": True}}))
    raise SystemExit(0)

if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "get":
    target = sys.argv[3]
    for p in state.get("panes", []):
        if p.get("pane_id") == target or p.get("name") == target:
            if not p.get("agent") or p.get("agent_status") == "none":
                break
            print(json.dumps({"result": {"agent": p}}))
            raise SystemExit(0)
    sys.stderr.write(json.dumps({"error": {"code": "agent_not_found", "message": f"not found: {target}"}}))
    raise SystemExit(1)

if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "prompt":
    target = sys.argv[3]
    text = sys.argv[4] if len(sys.argv) >= 5 else ""
    plog = os.environ.get("FAKE_PROMPT_LOG")
    if plog:
        with open(plog, "a", encoding="utf-8") as fh:
            fh.write(f"{target}|{text.splitlines()[0] if text else ''}\\n")
    print(json.dumps({"result": {"prompt": "sent"}}))
    raise SystemExit(0)

raise SystemExit(99)
""",
            encoding="utf-8",
        )
        fake_herdr.chmod(fake_herdr.stat().st_mode | stat.S_IXUSR)

        self.env = os.environ.copy()
        self.env["HERDR_ENV"] = "1"
        self.env["HERDR_PANE_ID"] = "w15:p1"
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["PATH"] = f"{self.fake_bin}:{self.env.get('PATH', '')}"
        self.env["FAKE_HERDR_STATE"] = str(self.herdr_state_file)
        self.env["FAKE_PROMPT_LOG"] = str(self.prompt_log)
        self.env["FAKE_RENAME_LOG"] = str(self.rename_log)

    def _write_herdr_state(
        self,
        *,
        workspaces: list[dict],
        tabs: list[dict],
        panes: list[dict],
    ) -> None:
        self.herdr_state_file.write_text(
            json.dumps({"workspaces": workspaces, "tabs": tabs, "panes": panes}),
            encoding="utf-8",
        )

    def _update_panes(self, panes: list[dict]) -> None:
        state = json.loads(self.herdr_state_file.read_text(encoding="utf-8"))
        state["panes"] = panes
        self.herdr_state_file.write_text(json.dumps(state), encoding="utf-8")

    def test_cw01_cw02_japanese_and_custom_roles_and_pane_rename(self) -> None:
        # CW-01 & CW-02: Implicit $HERDR_PANE_ID, Japanese role stored as-is ('レビュワー' -> 'レビュワー'),
        # NFKC + lowercase normalization ('ＴＩＣＫＥＴ管理' -> 'ticket管理'), and Herdr UI pane rename (display_name and --clear)
        rev_env = {**self.env, "HERDR_PANE_ID": "w15:p2"}
        r1 = _run_cli("role", "set", "レビュワー", env=rev_env, cwd=self.repo_a)
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        d1 = json.loads(r1.stdout)
        self.assertEqual(d1["role"], "レビュワー")
        self.assertEqual(d1["display_name"], "レビュワー")
        self.assertEqual(d1["pane_id"], "w15:p2")
        self.assertIn("ワークスペース #1「project-a-ws」の『レビュワー』", d1["target"]["display_summary"])

        # Global custom role registration using positional pane_id and full-width NFKC normalization
        r2 = _run_cli("role", "set", "ＴＩＣＫＥＴ管理", "w16:p1", "--global", env=self.env, cwd=self.repo_a)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        d2 = json.loads(r2.stdout)
        self.assertEqual(d2["scope"], "global")
        self.assertEqual(d2["role"], "ticket管理")
        self.assertEqual(d2["display_name"], "ＴＩＣＫＥＴ管理")
        self.assertIn("ワークスペース #2「tickets-ws」の『ＴＩＣＫＥＴ管理』", d2["target"]["display_summary"])

        # Clear global role -> triggers `herdr pane rename w16:p1 --clear`
        r_clear = _run_cli("role", "clear", "ticket管理", "--global", env=self.env, cwd=self.repo_a)
        self.assertEqual(r_clear.returncode, 0)
        renames = self.rename_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(
            renames,
            [
                "w15:p2 レビュワー",
                "w16:p1 ＴＩＣＫＥＴ管理",
                "w16:p1 --clear",
            ],
        )

        # CW-17: Leading '--' stripped from display_name to prevent option collision (e.g., '--clear' -> 'clear')
        r_strip = _run_cli("role", "set", "reviewer", "w15:p2", "--label=--clear", env=self.env, cwd=self.repo_a)
        self.assertEqual(r_strip.returncode, 0, r_strip.stdout)
        d_strip = json.loads(r_strip.stdout)
        self.assertEqual(d_strip["display_name"], "clear")
        renames = self.rename_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(renames[-1], "w15:p2 clear")

        # But bare '--' becomes empty after stripping leading '--' -> rejected as invalid_input
        r_empty = _run_cli("role", "set", "reviewer", "w15:p2", "--label=--", env=self.env, cwd=self.repo_a)
        self.assertEqual(r_empty.returncode, 1)
        d_empty = json.loads(r_empty.stdout)
        self.assertEqual(d_empty["error"], "invalid_input")

    def test_cw03_scope_ambiguity_and_explicit_scope_resolution(self) -> None:
        # CW-03: Same role name ('調査') bound in both local and global
        _run_cli("role", "set", "調査", "w15:p2", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "調査", "w17:p1", "--global", env=self.env, cwd=self.nogit_dir)

        # Without --scope -> stops with worker_role_scope_ambiguous and workspace names
        res_amb = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "調査",
            "--instruction",
            "investigate",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_amb.returncode, 1)
        err = json.loads(res_amb.stdout)
        self.assertEqual(err["error"], "worker_role_scope_ambiguous")
        self.assertIn("ワークスペース #1「project-a-ws」", err["message"])
        self.assertIn("ワークスペース #3「logs-ws」", err["message"])
        self.assertEqual(len(err["candidates"]), 2)

        # With --scope global -> succeeds and emits warning worker_role_shadowed_by_explicit_scope
        res_ok = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "調査",
            "--scope",
            "global",
            "--instruction",
            "investigate global",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_ok.returncode, 0, res_ok.stdout)
        out = json.loads(res_ok.stdout)
        self.assertEqual(out["warnings"][0]["code"], "worker_role_shadowed_by_explicit_scope")
        prompts = self.prompt_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(prompts[-1].split("|")[0], "w17:p1")

    def test_cw04_cw05_cross_workspace_and_nongit_handoff_and_summary_only_submit(self) -> None:
        # CW-04 & CW-05: Unbound requester in Non-Git directory (w17:p1) sends handoff to global ticket role (w16:p1)
        _run_cli("role", "set", "チケット管理", "w16:p1", "--global", env=self.env, cwd=self.ticket_dir)

        nogit_env = {**self.env, "HERDR_PANE_ID": "w17:p1"}
        res_h = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "チケット管理",
            "--instruction",
            "add ticket item",
            env=nogit_env,
            cwd=self.nogit_dir,
        )
        self.assertEqual(res_h.returncode, 0, res_h.stdout + res_h.stderr)
        task_id = json.loads(res_h.stdout)["task_id"]

        # Verify task record: requester.role == 'unbound', context.repository.identity == 'dir_...', worker.cwd == ticket_dir
        st_res = _run_cli("task-status", task_id, env=nogit_env, cwd=self.nogit_dir)
        self.assertEqual(st_res.returncode, 0)
        st_json = json.loads(st_res.stdout)
        # CW-13b: Requester calling task-status gets verified=True, caller_role_in_task='requester'
        self.assertTrue(st_json["recipient_verification"]["verified"])
        self.assertEqual(st_json["recipient_verification"]["caller_role_in_task"], "requester")
        task_doc = st_json["task"]
        self.assertEqual(task_doc["requester"]["role"], "unbound")
        self.assertTrue(task_doc["context"]["repository"]["identity"].startswith("dir_"))
        self.assertEqual(task_doc["worker"]["cwd"], str(self.ticket_dir.resolve()))

        # Worker (w16:p1 in ticket_dir) submits result with --summary only (no --result-ref)
        ticket_env = {**self.env, "HERDR_PANE_ID": "w16:p1"}
        sub_res = _run_cli(
            "result-submit",
            task_id,
            "--result-status",
            "succeeded",
            "--summary",
            "未完了チケットは2件です: 1. 設計レビュー, 2. テスト追加",
            env=ticket_env,
            cwd=self.ticket_dir,
        )
        self.assertEqual(sub_res.returncode, 0, sub_res.stdout + sub_res.stderr)
        sub_json = json.loads(sub_res.stdout)
        self.assertIsNone(sub_json["result"]["ref"])
        self.assertIn("未完了チケットは2件です", sub_json["result"]["summary"])
        self.assertEqual(sub_json["completion_chain"]["completion"]["status"], "sent")

    def test_cw06_same_pane_same_terminal_agent_restart_continues_transparently(self) -> None:
        # CW-06: Repo A has 2 roles in the same cwd (implementer=w15:p1, reviewer=w15:p2).
        # Reviewer exits and restarts inside the SAME terminal_id (term_15_2), getting a new session_id.
        _run_cli("role", "set", "実装者", "w15:p1", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "レビュワー", "w15:p2", env=self.env, cwd=self.repo_a)

        state = json.loads(self.herdr_state_file.read_text(encoding="utf-8"))
        for p in state["panes"]:
            if p["pane_id"] == "w15:p2":
                p["agent_session"] = {"value": "sess_a_rev_restarted_after_update"}
        self._update_panes(state["panes"])

        res = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "レビュワー",
            "--instruction",
            "after agent update in same terminal",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res.returncode, 0, res.stdout)

    def test_cw07_auto_tracking_on_pane_close_or_restart(self) -> None:
        # CW-07 Condition A (session match) & Condition B (1-folder 1-role single cwd+agent match)
        _run_cli("role", "set", "チケット管理", "w16:p1", "--global", env=self.env, cwd=self.ticket_dir)

        # Simulate Herdr restart: w16:p1 is gone, replaced by w22:p1 with a new session in ticket_dir (Condition B)
        state = json.loads(self.herdr_state_file.read_text(encoding="utf-8"))
        for p in state["panes"]:
            if p["pane_id"] == "w16:p1":
                p["pane_id"] = "w22:p1"
                p["terminal_id"] = "term_22_1"
                p["agent_session"] = {"value": "sess_ticket_new"}
        self._update_panes(state["panes"])

        res = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "チケット管理",
            "--instruction",
            "track to w22:p1",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res.returncode, 0, res.stdout)
        prompts = self.prompt_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(prompts[-1].split("|")[0], "w22:p1")

        # Verify global.json was updated to w22:p1
        show_res = _run_cli("role", "show", "--global", env=self.env, cwd=self.repo_a)
        self.assertEqual(json.loads(show_res.stdout)["roles"]["チケット管理"], "w22:p1")

    def test_cw08_cw09_cw10_stop_on_ambiguous_candidates_cwd_change_or_shell_state(self) -> None:
        _run_cli("role", "set", "チケット管理", "w16:p1", "--global", env=self.env, cwd=self.ticket_dir)

        # CW-10: Agent exited to shell (agent=None, agent_status="none") -> worker_agent_not_running
        state = json.loads(self.herdr_state_file.read_text(encoding="utf-8"))
        for p in state["panes"]:
            if p["pane_id"] == "w16:p1":
                p["agent"] = None
                p["agent_status"] = "none"
        self._update_panes(state["panes"])

        res_shell = _run_cli(
            "handoff", "--mode", "async", "--to", "チケット管理", "--instruction", "x", env=self.env, cwd=self.repo_a
        )
        self.assertEqual(res_shell.returncode, 1)
        self.assertEqual(json.loads(res_shell.stdout)["error"], "worker_agent_not_running")

        # CW-09: Agent moved to a different directory -> worker_context_changed
        for p in state["panes"]:
            if p["pane_id"] == "w16:p1":
                p["agent"] = "claude"
                p["agent_status"] = "idle"
                p["foreground_cwd"] = str(self.repo_b.resolve())
        self._update_panes(state["panes"])

        res_cwd = _run_cli(
            "handoff", "--mode", "async", "--to", "チケット管理", "--instruction", "x", env=self.env, cwd=self.repo_a
        )
        self.assertEqual(res_cwd.returncode, 1)
        self.assertEqual(json.loads(res_cwd.stdout)["error"], "worker_context_changed")

        # CW-08: Old pane w16:p1 closed, and TWO panes (w20:p1, w21:p1) have ticket_dir + claude -> worker_ambiguous_candidates
        state["panes"] = [p for p in state["panes"] if p["pane_id"] != "w16:p1"] + [
            {
                "pane_id": "w20:p1",
                "workspace_id": "w16",
                "tab_id": "w16:t1",
                "terminal_id": "term_20_1",
                "agent": "claude",
                "agent_status": "idle",
                "agent_session": {"value": "sess_new_1"},
                "foreground_cwd": str(self.ticket_dir.resolve()),
            },
            {
                "pane_id": "w21:p1",
                "workspace_id": "w17",
                "tab_id": "w17:t1",
                "terminal_id": "term_21_1",
                "agent": "claude",
                "agent_status": "idle",
                "agent_session": {"value": "sess_new_2"},
                "foreground_cwd": str(self.ticket_dir.resolve()),
            },
        ]
        self._update_panes(state["panes"])

        res_amb = _run_cli(
            "handoff", "--mode", "async", "--to", "チケット管理", "--instruction", "x", env=self.env, cwd=self.repo_a
        )
        self.assertEqual(res_amb.returncode, 1)
        err_amb = json.loads(res_amb.stdout)
        self.assertEqual(err_amb["error"], "worker_ambiguous_candidates")
        self.assertEqual(len(err_amb["candidates"]), 2)

    def test_cw11_cw12a_cw12b_multi_role_same_cwd_collision_prevention(self) -> None:
        # Setup 2 roles in the SAME cwd (self.repo_a) with the SAME agent type ("claude")
        state = json.loads(self.herdr_state_file.read_text(encoding="utf-8"))
        for p in state["panes"]:
            if p["pane_id"] == "w15:p1":
                p["agent"] = "claude"
        self._update_panes(state["panes"])

        _run_cli("role", "set", "実装者", "w15:p1", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "レビュワー", "w15:p2", env=self.env, cwd=self.repo_a)

        # CW-12a: Old pane w15:p2 now hosts implementer's known session ('sess_a_impl') -> worker_context_changed
        for p in state["panes"]:
            if p["pane_id"] == "w15:p2":
                p["agent_session"] = {"value": "sess_a_impl"}
        self._update_panes(state["panes"])

        res_12a = _run_cli(
            "handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "x", env=self.env, cwd=self.repo_a
        )
        self.assertEqual(res_12a.returncode, 1)
        self.assertEqual(json.loads(res_12a.stdout)["error"], "worker_context_changed")

        # CW-12b (RV018 B-1): Full restart -> new terminal_id AND new session_id in w15:p2 while multiple roles share repo_a -> stops with worker_context_changed
        for p in state["panes"]:
            if p["pane_id"] == "w15:p2":
                p["terminal_id"] = "term_after_server_restart"
                p["agent_session"] = {"value": "sess_brand_new_unknown"}
        self._update_panes(state["panes"])

        res_12b = _run_cli(
            "handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "x", env=self.env, cwd=self.repo_a
        )
        self.assertEqual(res_12b.returncode, 1)
        self.assertEqual(json.loads(res_12b.stdout)["error"], "worker_context_changed")

        # CW-11 (RV017 B-1): Old pane w15:p2 is closed, and a new pane w15:p9 has a brand new session in repo_a -> does NOT auto-track because is_unique_cwd_role is False!
        state["panes"] = [p for p in state["panes"] if p["pane_id"] != "w15:p2"] + [
            {
                "pane_id": "w15:p9",
                "workspace_id": "w15",
                "tab_id": "w15:t1",
                "terminal_id": "term_15_9",
                "agent": "claude",
                "agent_status": "idle",
                "agent_session": {"value": "sess_brand_new_p9"},
                "foreground_cwd": str(self.repo_a.resolve()),
            }
        ]
        self._update_panes(state["panes"])

        res_11 = _run_cli(
            "handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "x", env=self.env, cwd=self.repo_a
        )
        self.assertEqual(res_11.returncode, 1)
        self.assertEqual(json.loads(res_11.stdout)["error"], "worker_pane_closed_or_missing")

    def test_cw13a_recipient_verification_detects_mismatch_on_worker_or_other_pane(self) -> None:
        _run_cli("role", "set", "実装者", "w15:p1", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "レビュワー", "w15:p2", env=self.env, cwd=self.repo_a)

        res_h = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "レビュワー",
            "--instruction",
            "check recipient verification",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_h.returncode, 0, res_h.stdout)
        task_id = json.loads(res_h.stdout)["task_id"]

        # 1. Correct worker (w15:p2 in repo_a) -> verified=True, caller_role_in_task="worker"
        w_env = {**self.env, "HERDR_PANE_ID": "w15:p2"}
        st_ok = json.loads(_run_cli("task-status", task_id, env=w_env, cwd=self.repo_a).stdout)
        self.assertTrue(st_ok["recipient_verification"]["verified"])
        self.assertEqual(st_ok["recipient_verification"]["caller_role_in_task"], "worker")

        # 2. Unrelated pane (w16:p1) -> verified=False, caller_role_in_task="other", action_required="STOP_AND_REPORT_TO_HUMAN"
        other_env = {**self.env, "HERDR_PANE_ID": "w16:p1"}
        st_other = json.loads(_run_cli("task-status", task_id, env=other_env, cwd=self.ticket_dir).stdout)
        self.assertFalse(st_other["recipient_verification"]["verified"])
        self.assertEqual(st_other["recipient_verification"]["caller_role_in_task"], "other")
        self.assertEqual(st_other["recipient_verification"]["action_required"], "STOP_AND_REPORT_TO_HUMAN")

        # 3. Worker pane w15:p2 moved to repo_b after dispatch -> verified=False, caller_role_in_task="worker"
        state = json.loads(self.herdr_state_file.read_text(encoding="utf-8"))
        for p in state["panes"]:
            if p["pane_id"] == "w15:p2":
                p["foreground_cwd"] = str(self.repo_b.resolve())
        self._update_panes(state["panes"])

        st_moved = json.loads(_run_cli("task-status", task_id, env=w_env, cwd=self.repo_b).stdout)
        self.assertFalse(st_moved["recipient_verification"]["verified"])
        self.assertEqual(st_moved["recipient_verification"]["caller_role_in_task"], "worker")
        self.assertEqual(st_moved["recipient_verification"]["reason"], "recipient_role_or_cwd_mismatch")

    def test_cw14_cw16_outdated_role_binding_rejected_and_null_fingerprints_never_match(self) -> None:
        # CW-14 (RV019 B-1 / RV020 N-2): Write legacy v0.3.1 Role Binding JSON (no `targets`)
        repo_id = get_or_create_repository_identity(self.repo_a)
        path = self.state_home / "herdr-orchestrator" / "roles" / f"{repo_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repository_id": repo_id,
                    "roles": {"implementer": "w15:p1", "reviewer": "w15:p2"},
                }
            ),
            encoding="utf-8",
        )

        # `role show` and `doctor` report outdated_roles
        show_doc = json.loads(_run_cli("role", "show", env=self.env, cwd=self.repo_a).stdout)
        self.assertEqual(show_doc["outdated_roles"], ["implementer", "reviewer"])
        doc_out = json.loads(_run_cli("doctor", env=self.env, cwd=self.repo_a).stdout)
        self.assertTrue(any(item["scope_id"] == repo_id for item in doc_out["outdated_role_bindings"]))

        # `handoff` stops before dispatch with `role_binding_outdated`
        res_outdated = _run_cli(
            "handoff", "--mode", "async", "--to", "reviewer", "--instruction", "x", env=self.env, cwd=self.repo_a
        )
        self.assertEqual(res_outdated.returncode, 1)
        self.assertEqual(json.loads(res_outdated.stdout)["error"], "role_binding_outdated")

        # CW-15: `role set` without HERDR_ENV=1 fails with herdr_env_required
        env_no_herdr = {k: v for k, v in self.env.items() if k != "HERDR_ENV"}
        res_no_herdr = _run_cli("role", "set", "reviewer", "w15:p2", env=env_no_herdr, cwd=self.repo_a)
        self.assertEqual(res_no_herdr.returncode, 1)
        self.assertEqual(json.loads(res_no_herdr.stdout)["error"], "herdr_env_required")

        # CW-16 (RV020 N-1 / RV021 N-1): Null fingerprints (terminal_id=None, agent_session_id=None) never match `null == null`
        from orchestrator.handoff_commands import _non_empty_str_match

        self.assertFalse(_non_empty_str_match(None, None))
        self.assertFalse(_non_empty_str_match("", ""))
        self.assertFalse(_non_empty_str_match("term_1", None))
        self.assertTrue(_non_empty_str_match("term_1", "term_1"))

    def test_pattern_a_failure_includes_available_roles_in_details(self) -> None:
        # CW-18 (B-1): Register 'レビュワー', then try to handoff to 'reviewer'.
        # Falls back to Pattern A (r-<hash16>-reviewer) which is unavailable,
        # but the error output must provide available_roles to guide the user.
        _run_cli("role", "set", "実装者", "w15:p1", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "レビュワー", "w15:p2", env=self.env, cwd=self.repo_a)

        res = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "reviewer",
            "--instruction",
            "test b-1",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res.returncode, 1)
        err = json.loads(res.stdout)
        self.assertEqual(err["error"], "worker_unavailable")
        self.assertIn("available_roles", err)
        self.assertIn("レビュワー", err["available_roles"]["local"])

    def test_cas_mismatch_during_handoff_stops_with_worker_context_changed(self) -> None:
        # CW-19 (N-1): If compare_and_update_role_binding returns False due to concurrent change,
        # handoff must stop with worker_context_changed rather than delivering to wrong target.
        _run_cli("role", "set", "reviewer", "w15:p2", env=self.env, cwd=self.repo_a)

        from unittest.mock import patch
        from orchestrator.handoff_commands import run_handoff, HandoffInput, HandoffCommandError
        import os

        old_env = os.environ.copy()
        old_cwd = os.getcwd()
        try:
            os.environ.update(self.env)
            os.chdir(self.repo_a)
            h_input = HandoffInput(
                mode="async",
                worker_role="reviewer",
                instruction="test n-1",
                task_type="general",
                result_wait_timeout_sec=0,
            )
            with patch(
                "orchestrator.handoff_commands.compare_and_update_role_binding",
                return_value=False,
            ):
                with self.assertRaises(HandoffCommandError) as ctx:
                    run_handoff(h_input)
                self.assertEqual(ctx.exception.code, "worker_context_changed")
                self.assertIn("並行操作により変更", ctx.exception.message)
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            os.chdir(old_cwd)

    def test_cw20_multi_role_pane_verification_and_submit(self) -> None:
        # CW-20: Same pane bound to local role ('レビュワー') and global role ('調査担当').
        # 1. When receiving task for global role, recipient_verification returns verified=True,
        #    caller_role_in_task='worker', and bound_role='調査担当'. result-submit succeeds.
        # 2. When receiving task for local role, recipient_verification returns verified=True,
        #    caller_role_in_task='worker', and bound_role='レビュワー'. result-submit succeeds.
        # 3. When querying task bound to another pane ('テスター' on w15:p3), recipient_verification returns
        #    verified=False, caller_role_in_task='other', and reason='caller_not_task_participant'.
        # 4. If pane is rebound to an unrelated role ('記録係') after task creation, recipient_verification returns
        #    verified=False (reason='recipient_role_or_cwd_mismatch'), and result-submit fails (worker_context_unverified).
        _run_cli("role", "set", "メイン", "w15:p1", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "レビュワー", "w15:p2", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "調査担当", "w15:p2", "--global", env=self.env, cwd=self.repo_a)

        p2_env = {**self.env, "HERDR_PANE_ID": "w15:p2"}

        # --- Case 1: Global task for '調査担当' ---
        res_h_global = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "調査担当",
            "--scope",
            "global",
            "--instruction",
            "investigate logs",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_h_global.returncode, 0, res_h_global.stdout)
        global_task_id = json.loads(res_h_global.stdout)["task_id"]

        res_stat_global = _run_cli("task-status", global_task_id, env=p2_env, cwd=self.repo_a)
        self.assertEqual(res_stat_global.returncode, 0, res_stat_global.stdout)
        stat_global_data = json.loads(res_stat_global.stdout)
        rec_global = stat_global_data["recipient_verification"]
        self.assertTrue(rec_global["verified"])
        self.assertEqual(rec_global["caller_role_in_task"], "worker")
        self.assertEqual(rec_global["actual_caller"]["bound_role"], "調査担当")
        self.assertEqual(rec_global["actual_caller"]["bound_roles"], {"local": "レビュワー", "global": "調査担当"})

        res_sub_global = _run_cli(
            "result-submit",
            global_task_id,
            "--result-status",
            "succeeded",
            "--summary",
            "investigation complete",
            env=p2_env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_sub_global.returncode, 0, res_sub_global.stdout)
        self.assertEqual(json.loads(res_sub_global.stdout)["task_id"], global_task_id)

        # --- Case 2: Local task for 'レビュワー' ---
        res_h_local = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "レビュワー",
            "--scope",
            "local",
            "--instruction",
            "review PR",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_h_local.returncode, 0, res_h_local.stdout)
        local_task_id = json.loads(res_h_local.stdout)["task_id"]

        res_stat_local = _run_cli("task-status", local_task_id, env=p2_env, cwd=self.repo_a)
        self.assertEqual(res_stat_local.returncode, 0, res_stat_local.stdout)
        stat_local_data = json.loads(res_stat_local.stdout)
        rec_local = stat_local_data["recipient_verification"]
        self.assertTrue(rec_local["verified"])
        self.assertEqual(rec_local["caller_role_in_task"], "worker")
        self.assertEqual(rec_local["actual_caller"]["bound_role"], "レビュワー")

        res_sub_local = _run_cli(
            "result-submit",
            local_task_id,
            "--result-status",
            "succeeded",
            "--summary",
            "review complete",
            env=p2_env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_sub_local.returncode, 0, res_sub_local.stdout)
        self.assertEqual(json.loads(res_sub_local.stdout)["task_id"], local_task_id)

        # --- Case 3: Task for another pane ('チケット管理' on w16:p1) queried from w15:p2 ---
        res_set_other = _run_cli("role", "set", "チケット管理", "w16:p1", env=self.env, cwd=self.repo_a)
        self.assertEqual(res_set_other.returncode, 0, res_set_other.stdout)
        res_h_other = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "チケット管理",
            "--instruction",
            "manage tickets",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_h_other.returncode, 0, res_h_other.stdout)
        other_task_id = json.loads(res_h_other.stdout)["task_id"]

        res_stat_other = _run_cli("task-status", other_task_id, env=p2_env, cwd=self.repo_a)
        self.assertEqual(res_stat_other.returncode, 0, res_stat_other.stdout)
        stat_other_data = json.loads(res_stat_other.stdout)
        rec_other = stat_other_data["recipient_verification"]
        self.assertFalse(rec_other["verified"])
        self.assertEqual(rec_other["caller_role_in_task"], "other")
        self.assertEqual(rec_other["reason"], "caller_not_task_participant")

        # --- Case 4: Rebound to unrelated role after task creation ---
        res_h_rebound = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "レビュワー",
            "--instruction",
            "check style",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_h_rebound.returncode, 0, res_h_rebound.stdout)
        rebound_task_id = json.loads(res_h_rebound.stdout)["task_id"]

        # Overwrite w15:p2 with unrelated role, clearing previous bindings
        _run_cli("role", "clear", "--all", env=self.env, cwd=self.repo_a)
        _run_cli("role", "clear", "--all", "--global", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "メイン", "w15:p1", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "記録係", "w15:p2", env=self.env, cwd=self.repo_a)

        res_stat_rebound = _run_cli("task-status", rebound_task_id, env=p2_env, cwd=self.repo_a)
        self.assertEqual(res_stat_rebound.returncode, 0, res_stat_rebound.stdout)
        stat_rebound_data = json.loads(res_stat_rebound.stdout)
        rec_rebound = stat_rebound_data["recipient_verification"]
        self.assertFalse(rec_rebound["verified"])
        self.assertEqual(rec_rebound["caller_role_in_task"], "worker")
        self.assertEqual(rec_rebound["reason"], "recipient_role_or_cwd_mismatch")
        self.assertEqual(rec_rebound["action_required"], "STOP_AND_REPORT_TO_HUMAN")

        res_sub_rebound = _run_cli(
            "result-submit",
            rebound_task_id,
            "--result-status",
            "succeeded",
            "--summary",
            "should fail",
            env=p2_env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_sub_rebound.returncode, 1)
        self.assertEqual(json.loads(res_sub_rebound.stdout)["error"], "worker_context_unverified")

    def test_cw21_multi_role_pane_single_sided_follow_and_rebind(self) -> None:
        # CW-21 (P2-1 / RV034 B-1): When a pane is bound to both local and global roles,
        # and pane id changes (e.g. w15:p2 -> w15:p9) with same session/terminal,
        # handoff to the first role successfully auto-follows to w15:p9 (Case K).
        # However, the other role's binding remains on the old pane id (w15:p2).
        # Subsequent handoff to the other role sees w15:p9 in other_bound_panes,
        # so it safely stops with worker_pane_closed_or_missing.
        # After explicitly rebinding with `role set`, handoff succeeds.
        _run_cli("role", "set", "メイン", "w15:p1", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "レビュワー", "w15:p2", env=self.env, cwd=self.repo_a)
        _run_cli("role", "set", "調査担当", "w15:p2", "--global", env=self.env, cwd=self.repo_a)

        # Pane rearrangement: w15:p2 is replaced by w15:p9 with same terminal_id and session_id
        state = json.loads(self.herdr_state_file.read_text(encoding="utf-8"))
        for p in state["panes"]:
            if p["pane_id"] == "w15:p2":
                p["pane_id"] = "w15:p9"
        self._update_panes(state["panes"])

        # 1. Handoff to 'レビュワー': Condition A (session match) succeeds and auto-tracks to w15:p9
        res_h1 = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "レビュワー",
            "--instruction",
            "review task after pane move",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_h1.returncode, 0, res_h1.stdout)
        h1_data = json.loads(res_h1.stdout)
        self.assertEqual(h1_data["command"], "handoff")
        prompts = self.prompt_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(prompts[-1].split("|")[0], "w15:p9")

        # Verify local binding for 'レビュワー' was updated to w15:p9
        show_local = _run_cli("role", "show", env=self.env, cwd=self.repo_a)
        self.assertEqual(json.loads(show_local.stdout)["roles"]["レビュワー"], "w15:p9")

        # 2. Handoff to '調査担当': Old pane w15:p2 is closed, and w15:p9 is already bound to 'レビュワー'
        # (in other_bound_panes), so it safely stops with worker_pane_closed_or_missing.
        res_h2 = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "調査担当",
            "--scope",
            "global",
            "--instruction",
            "investigate task after pane move",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_h2.returncode, 1)
        err_h2 = json.loads(res_h2.stdout)
        self.assertEqual(err_h2["error"], "worker_pane_closed_or_missing")

        # 3. Explicitly rebind '調査担当' to w15:p9 per §5.2 procedure
        res_rebind = _run_cli(
            "role",
            "set",
            "調査担当",
            "w15:p9",
            "--global",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_rebind.returncode, 0, res_rebind.stdout)
        show_global = _run_cli("role", "show", "--global", env=self.env, cwd=self.repo_a)
        self.assertEqual(json.loads(show_global.stdout)["roles"]["調査担当"], "w15:p9")

        # 4. Subsequent handoff to '調査担当' now succeeds!
        res_h3 = _run_cli(
            "handoff",
            "--mode",
            "async",
            "--to",
            "調査担当",
            "--scope",
            "global",
            "--instruction",
            "investigate task after rebind",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(res_h3.returncode, 0, res_h3.stdout)
        h3_data = json.loads(res_h3.stdout)
        self.assertEqual(h3_data["command"], "handoff")
        prompts2 = self.prompt_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(prompts2[-1].split("|")[0], "w15:p9")

    def test_cw22_role_clear_preserves_surviving_role_pane_label(self) -> None:
        # CW-22 (Issue #79): Pane bound to both local and global roles.
        # Clearing one scope preserves the other scope's display_name rather than clearing the UI label.
        # 1. Bind w15:p2 to local role 'レビュワー'
        r1 = _run_cli("role", "set", "レビュワー", "w15:p2", env=self.env, cwd=self.repo_a)
        self.assertEqual(r1.returncode, 0)
        d1 = json.loads(r1.stdout)
        self.assertTrue(d1["target"]["label_synced"])
        self.assertTrue(d1["label_sync"]["synced"])
        self.assertEqual(d1["label_sync"]["action"], "rename")

        # 2. Bind same w15:p2 to global role 'ticket管理' with custom label
        r2 = _run_cli(
            "role",
            "set",
            "チケット管理",
            "w15:p2",
            "--label=チケット管理 (共通)",
            "--global",
            env=self.env,
            cwd=self.repo_a,
        )
        self.assertEqual(r2.returncode, 0)
        d2 = json.loads(r2.stdout)
        self.assertTrue(d2["target"]["label_synced"])
        self.assertEqual(d2["display_name"], "チケット管理 (共通)")

        renames = self.rename_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(renames[-1], "w15:p2 チケット管理 (共通)")

        # 3. Clear local role 'レビュワー'.
        # Since w15:p2 still holds global role 'ticket管理', the label must be preserved / re-synced, NOT cleared!
        r_clear_local = _run_cli("role", "clear", "レビュワー", env=self.env, cwd=self.repo_a)
        self.assertEqual(r_clear_local.returncode, 0)
        d_clear_local = json.loads(r_clear_local.stdout)
        self.assertIn("w15:p2", d_clear_local["label_sync"])
        self.assertTrue(d_clear_local["label_sync"]["w15:p2"]["synced"])
        self.assertEqual(d_clear_local["label_sync"]["w15:p2"]["action"], "rename")
        self.assertEqual(d_clear_local["label_sync"]["w15:p2"]["label"], "チケット管理 (共通)")

        renames_after_local = self.rename_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertNotEqual(renames_after_local[-1], "w15:p2 --clear")
        self.assertEqual(renames_after_local[-1], "w15:p2 チケット管理 (共通)")

        # 4. Clear global role 'チケット管理'.
        # Now w15:p2 holds no more roles in either scope -> UI label should now be cleared!
        r_clear_global = _run_cli("role", "clear", "チケット管理", "--global", env=self.env, cwd=self.repo_a)
        self.assertEqual(r_clear_global.returncode, 0)
        d_clear_global = json.loads(r_clear_global.stdout)
        self.assertIn("w15:p2", d_clear_global["label_sync"])
        self.assertTrue(d_clear_global["label_sync"]["w15:p2"]["synced"])
        self.assertEqual(d_clear_global["label_sync"]["w15:p2"]["action"], "clear")

        renames_after_global = self.rename_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(renames_after_global[-1], "w15:p2 --clear")


if __name__ == "__main__":
    unittest.main()
