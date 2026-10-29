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
            set_role_binding(_SAMPLE_UUID, "invalid_role", "w15:p1")
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

        # 6. 'role set' on nonexistent pane in HERDR_ENV=1 succeeds with diagnostic warning
        res_warn = _run_cli(
            "role", "set", "design", "--pane", "w99:p9", env=self.env, cwd=self.worktree
        )
        self.assertEqual(res_warn.returncode, 0, res_warn.stderr)
        warn_out = json.loads(res_warn.stdout)
        self.assertEqual(warn_out["pane_id"], "w99:p9")
        self.assertIn("warning", warn_out)

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


if __name__ == "__main__":
    unittest.main()
