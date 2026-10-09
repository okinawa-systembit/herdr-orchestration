"""
Phase-6 async completion vs §18.3 (stage-6 gate):
  69 result-submit best-effort completion chain
  95 reset-completion / 108 initial dispatch failure reset reject
  96 recover-completion reset + claim
  resume-task claim eligibility / requester working skip
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"
_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]

_FAKE_HERDR_PY = """#!/usr/bin/env python3
import json, os, sys

def agent(name, status, cwd, pane):
    return {"result": {"agent": {
        "name": name,
        "agent_status": status,
        "foreground_cwd": cwd,
        "pane_id": pane,
    }}}

if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "get":
    t = sys.argv[3]
    cwd = os.environ.get("FAKE_AGENT_CWD", "/nonexistent")
    req = os.environ.get("FAKE_REQUESTER_AGENT", "org-repo-design")
    wrk = os.environ.get("FAKE_WORKER_AGENT", "org-repo-reviewer")
    st = os.environ.get("FAKE_REQUESTER_STATUS", "idle")
    if t in (req, "w11:p1"):
        print(json.dumps(agent(req, st, cwd, "w11:p1")))
        raise SystemExit(0)
    if t in (wrk, "w11:p2"):
        print(json.dumps(agent(wrk, "idle", cwd, "w11:p2")))
        raise SystemExit(0)
    raise SystemExit(99)

if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "prompt":
    log = os.environ.get("FAKE_PROMPT_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("prompt\\n")
    mode = os.environ.get("FAKE_PROMPT_MODE", "ok")
    if mode == "blocked":
        sys.stderr.write(json.dumps({"error": {"code": "agent_blocked", "message": "blocked"}}))
        raise SystemExit(1)
    if mode == "stalled":
        sys.stderr.write(json.dumps({"error": {"code": "agent_prompt_stalled", "message": "stalled"}}))
        raise SystemExit(1)
    if mode == "timeout":
        import time
        time.sleep(2)
        raise SystemExit(0)
    if mode == "unknown_cli_err":
        sys.stderr.write("unexpected failure in herdr daemon\\n")
        raise SystemExit(1)
    if mode == "agent_not_found":
        sys.stderr.write(json.dumps({"error": {"code": "agent_not_found", "message": "agent not found"}}))
        raise SystemExit(1)
    if mode == "invalid_timeout":
        sys.stderr.write(json.dumps({"error": {"code": "invalid_timeout_setting", "message": "invalid timeout"}}))
        raise SystemExit(1)
    raise SystemExit(0)

raise SystemExit(99)
"""


class _env_patch:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self._previous: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self.env.items():
            if key.startswith("XDG_") or key in ("HERDR_ENV", "HERDR_PANE_ID", "PATH"):
                self._previous[key] = os.environ.get(key)
                os.environ[key] = value

    def __exit__(self, *args: object) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run_cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        check=False,
    )


def _init_worktree(root: Path) -> Path:
    repo = root / "wt"
    subprocess.run(
        ["git", *_GIT_IDENTITY, "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(repo), "remote", "add", "origin", "https://github.com/org/repo.git"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", *_GIT_IDENTITY, "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", *_GIT_IDENTITY, "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    out = repo / "out.md"
    out.write_text("ok\n", encoding="utf-8")
    return repo


def _sample_create_payload(*, worktree: Path) -> dict:
    return {
        "task": {"type": "review", "mode": "async", "instruction": "review"},
        "context": {
            "repository": {"identity": "github.com/org/repo"},
            "worktree": {"path": str(worktree.resolve()), "branch": "main", "commit": None},
        },
        "requester": {"role": "design", "agent_name": "org-repo-design", "pane_id": "w11:p1"},
        "worker": {"role": "reviewer", "agent_name": "org-repo-reviewer", "pane_id": "w11:p2"},
        "completion": {"action": "resume_requester"},
    }


def _async_task_result_pending(env: dict[str, str], worktree: Path) -> str:
    with _env_patch(env):
        doc = registry_ops.create_task(_sample_create_payload(worktree=worktree))
        task_id = doc["task"]["id"]
        registry_ops.record_prompt_start(task_id)
        registry_ops.mark_dispatch(task_id, outcome="sent")
        registry_ops.update_result(
            task_id,
            result_status="succeeded",
            result_ref="out.md",
            summary="done",
        )
    return task_id


class Phase6CompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        root = Path(self._tmpdir.name)
        self.state_home = root / "state"
        self.fake_bin = root / "bin"
        self.fake_bin.mkdir()
        fake_herdr = self.fake_bin / "herdr"
        fake_herdr.write_text(_FAKE_HERDR_PY, encoding="utf-8")
        fake_herdr.chmod(fake_herdr.stat().st_mode | stat.S_IXUSR)
        self.prompt_log = root / "prompt.log"
        self.worktree = _init_worktree(root)
        self.env = os.environ.copy()
        self.env["HERDR_ENV"] = "1"
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["PATH"] = f"{self.fake_bin}:{self.env.get('PATH', '')}"
        self.env["FAKE_AGENT_CWD"] = str(self.worktree)
        self.env["FAKE_REQUESTER_AGENT"] = "org-repo-design"
        self.env["FAKE_WORKER_AGENT"] = "org-repo-reviewer"
        self.env["FAKE_PROMPT_LOG"] = str(self.prompt_log)

    def test_result_submit_best_effort_completion_sent(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
        proc = _run_cli(
            "result-submit",
            task_id,
            "--result-status",
            "succeeded",
            "--result-ref",
            "out.md",
            env={**self.env, "HERDR_PANE_ID": "w11:p2"},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertIn("completion_chain", out)
        self.assertEqual(out["completion_chain"]["completion"]["status"], "sent")
        self.assertTrue(self.prompt_log.exists())

    def test_resume_task_after_reset_from_skipped(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        with _env_patch(self.env):
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="skipped", reason="requester_working")
            registry_ops.reset_completion(task_id)
        proc = _run_cli("resume-task", task_id, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(json.loads(proc.stdout)["completion"]["status"], "sent")

    def test_reset_rejects_initial_dispatch_failure(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="failed")
        proc = _run_cli("reset-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "registry_conflict")

    def test_resume_task_rejects_without_terminal_result(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
        proc = _run_cli("resume-task", task_id, env=self.env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "registry_conflict")

    def test_requester_working_skips_without_prompt(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="out.md",
                summary="done",
            )
        env = {**self.env, "FAKE_REQUESTER_STATUS": "working"}
        proc = _run_cli("resume-task", task_id, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(json.loads(proc.stdout)["completion"]["status"], "skipped")
        self.assertFalse(self.prompt_log.exists())

    def test_recover_completion_reset_and_resume(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        with _env_patch(self.env):
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="failed", reason="herdr_prompt_failed")
        proc = _run_cli("recover-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertTrue(out["reset_applied"])
        self.assertTrue(out["resumed"])
        self.assertEqual(out["completion"]["status"], "sent")

    def test_recover_reset_only_when_not_eligible(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            path = self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["completion"]["status"] = "failed"
            raw["completion"]["failure_reason"] = "something"
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        proc = _run_cli("recover-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertTrue(out["reset_applied"])
        self.assertFalse(out["resumed"])

    def test_recover_completion_rejects_uncertain_to_prevent_double_dispatch(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        with _env_patch(self.env):
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="uncertain", reason="agent_prompt_stalled")
        proc = _run_cli("recover-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "registry_conflict")
        with _env_patch(self.env):
            doc = registry_ops.get_task(task_id)
            self.assertEqual(doc["completion"]["status"], "uncertain")

    def test_recover_completion_rejects_processing_to_prevent_double_dispatch(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        with _env_patch(self.env):
            registry_ops.claim_completion(task_id)
        proc = _run_cli("recover-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "registry_conflict")
        with _env_patch(self.env):
            doc = registry_ops.get_task(task_id)
            self.assertEqual(doc["completion"]["status"], "processing")

    def test_recover_completion_rejects_sent_to_prevent_double_dispatch(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        with _env_patch(self.env):
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="sent")
        proc = _run_cli("recover-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "registry_conflict")

    def test_reset_completion_allows_processing_and_uncertain(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        with _env_patch(self.env):
            registry_ops.claim_completion(task_id)
        # reset-completion works on processing, and includes warning
        proc = _run_cli("reset-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertIn("warning", out)
        self.assertIn("processing", out["warning"])
        with _env_patch(self.env):
            doc = registry_ops.get_task(task_id)
            self.assertEqual(doc["completion"]["status"], "pending")

        # reset-completion works on uncertain, and includes warning
        with _env_patch(self.env):
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="uncertain", reason="agent_prompt_stalled")
        proc = _run_cli("reset-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertIn("warning", out)
        self.assertIn("uncertain", out["warning"])
        with _env_patch(self.env):
            doc = registry_ops.get_task(task_id)
            self.assertEqual(doc["completion"]["status"], "pending")

    def test_reset_completion_no_warning_on_failed(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        with _env_patch(self.env):
            registry_ops.claim_completion(task_id)
            registry_ops.finish_completion(task_id, outcome="failed", reason="agent_not_found")
        proc = _run_cli("reset-completion", task_id, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertNotIn("warning", out)
        with _env_patch(self.env):
            doc = registry_ops.get_task(task_id)
            self.assertEqual(doc["completion"]["status"], "pending")

    def test_resume_task_prompt_timeout_and_unknown_err_marks_uncertain(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        env = {**self.env, "FAKE_PROMPT_MODE": "timeout", "HERDR_CLI_TIMEOUT_SEC": "0.2"}
        proc = _run_cli("resume-task", task_id, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["completion"]["status"], "uncertain")

        # Unknown CLI error also marks uncertain
        task_id2 = _async_task_result_pending(self.env, self.worktree)
        env2 = {**self.env, "FAKE_PROMPT_MODE": "unknown_cli_err"}
        proc2 = _run_cli("resume-task", task_id2, env=env2)
        self.assertEqual(proc2.returncode, 0, proc2.stdout)
        doc2 = json.loads(proc2.stdout)
        self.assertEqual(doc2["completion"]["status"], "uncertain")

    def test_resume_task_prompt_agent_not_found_marks_failed(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        env = {**self.env, "FAKE_PROMPT_MODE": "agent_not_found"}
        proc = _run_cli("resume-task", task_id, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["completion"]["status"], "failed")

    def test_resume_task_prompt_invalid_timeout_setting_marks_failed(self) -> None:
        task_id = _async_task_result_pending(self.env, self.worktree)
        env = {**self.env, "FAKE_PROMPT_MODE": "invalid_timeout"}
        proc = _run_cli("resume-task", task_id, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["completion"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
