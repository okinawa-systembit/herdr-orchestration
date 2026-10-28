"""
Phase-4 handoff vs §18.3 (stage-4 gate, orchestrator side):
  66 mark-dispatch before sync result wait
  73 worker resolved before create
  91 sync waits on result terminal
  prompt/assert/sync failure paths
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from orchestrator.handoff_lock import HandoffLockSession
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"
REGISTRY_CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "task-registry.py"

from orchestrator.naming import expected_agent_name

_IDENTITY = "550e8400-e29b-41d4-a716-446655440000"
_REQUESTER_AGENT = expected_agent_name(_IDENTITY, "design")
_WORKER_AGENT = expected_agent_name(_IDENTITY, "reviewer")

_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]

_FAKE_HERDR_PY = """#!/usr/bin/env python3
import json, os, sys

def payload(name, status, cwd, pane):
    agent = {
        "name": name,
        "agent_status": status,
        "foreground_cwd": cwd,
    }
    if pane is not None:
        agent["pane_id"] = pane
    return {"result": {"agent": agent}}

def worker_status():
    blocked_flag = os.environ.get("FAKE_WORKER_BLOCKED_FLAG")
    if blocked_flag and os.path.exists(blocked_flag):
        return "blocked"
    st = os.environ.get("FAKE_WORKER_STATUS", "idle")
    after_n = int(os.environ.get("FAKE_WORKER_WORKING_AFTER_N", "0") or "0")
    if after_n:
        count_path = os.environ.get("FAKE_WORKER_GET_COUNT")
        n = 0
        if count_path and os.path.exists(count_path):
            n = int(open(count_path, encoding="utf-8").read().strip() or "0")
        n += 1
        if count_path:
            open(count_path, "w", encoding="utf-8").write(str(n))
        if n >= after_n:
            st = "working"
    return st

if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "get":
    t = sys.argv[3]
    cwd = os.environ.get("FAKE_AGENT_CWD", "/nonexistent")
    req = os.environ.get("FAKE_REQUESTER_AGENT", "github-com-org-repo-design")
    wrk = os.environ.get("FAKE_WORKER_AGENT", "github-com-org-repo-reviewer")
    req_pane = None if os.environ.get("FAKE_OMIT_REQUESTER_PANE") == "1" else "w11:p1"
    wrk_pane = None if os.environ.get("FAKE_OMIT_WORKER_PANE") == "1" else "w11:p2"
    if t in ("w11:p1", req):
        print(json.dumps(payload(req, "idle", cwd, req_pane)))
        raise SystemExit(0)
    if t in ("w11:p2", wrk):
        gone_flag = os.environ.get("FAKE_WORKER_GONE_FLAG")
        if gone_flag and os.path.exists(gone_flag):
            raise SystemExit(99)
        print(json.dumps(payload(wrk, worker_status(), cwd, wrk_pane)))
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
    if mode == "cli_json_err":
        sys.stderr.write(json.dumps({"error": {"message": "something without code"}}))
        raise SystemExit(1)
    if mode == "invalid_timeout":
        sys.stderr.write(json.dumps({"error": {"code": "invalid_timeout_setting", "message": "invalid timeout"}}))
        raise SystemExit(1)
    raise SystemExit(0)

raise SystemExit(99)
"""


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(worktree), *args],
        check=True,
        capture_output=True,
    )


def _init_worktree(root: Path) -> Path:
    repo = root / "wt"
    subprocess.run(
        ["git", *_GIT_IDENTITY, "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "config", "herdr.repository-id", _IDENTITY)
    _git(repo, "remote", "add", "origin", "https://github.com/org/repo.git")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    artifact = repo / "artifacts" / "review.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("ok\n", encoding="utf-8")
    return repo


def _run_cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        check=False,
    )


def _sample_create_payload(*, worktree: Path, mode: str = "sync") -> dict:
    return {
        "task": {"type": "review", "mode": mode, "instruction": "x"},
        "context": {
            "repository": {"identity": _IDENTITY},
            "worktree": {"path": str(worktree.resolve()), "branch": "main", "commit": None},
        },
        "requester": {
            "role": "design",
            "agent_name": _REQUESTER_AGENT,
            "pane_id": "w11:p1",
        },
        "worker": {
            "role": "reviewer",
            "agent_name": _WORKER_AGENT,
            "pane_id": "w11:p2",
        },
        "completion": {"action": "none" if mode == "sync" else "resume_requester"},
    }


class _env_patch:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self._previous: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self.env.items():
            if key.startswith("XDG_") or key == "XDG_STATE_HOME" or key.startswith("HERDR_"):
                self._previous[key] = os.environ.get(key)
                os.environ[key] = value

    def __exit__(self, *args: object) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class Phase4HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        root = Path(self._tmpdir.name)
        self.worktree = _init_worktree(root)
        self.state_home = root / "state"
        self.fake_bin = root / "bin"
        self.fake_bin.mkdir()
        fake_herdr = self.fake_bin / "herdr"
        fake_herdr.write_text(_FAKE_HERDR_PY, encoding="utf-8")
        fake_herdr.chmod(fake_herdr.stat().st_mode | stat.S_IXUSR)
        self.prompt_log = root / "prompt.log"
        self.worker_get_count = root / "worker_get.count"
        self.env = os.environ.copy()
        self.env["HERDR_ENV"] = "1"
        self.env["HERDR_PANE_ID"] = "w11:p1"
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["PATH"] = f"{self.fake_bin}:{self.env.get('PATH', '')}"
        self.env["FAKE_AGENT_CWD"] = str(self.worktree / "sub")
        self.env["FAKE_REQUESTER_AGENT"] = _REQUESTER_AGENT
        self.env["FAKE_WORKER_AGENT"] = _WORKER_AGENT
        self.env["FAKE_PROMPT_LOG"] = str(self.prompt_log)
        self.env["FAKE_WORKER_GET_COUNT"] = str(self.worker_get_count)
        self.env["HERDR_ORCHESTRATOR_RESULT_POLL_SEC"] = "0.05"
        (self.worktree / "sub").mkdir(exist_ok=True)

    def _wait_for_prompt(self, timeout_sec: float = 5.0) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self.prompt_log.exists():
                return
            time.sleep(0.01)
        raise TimeoutError("fake herdr prompt was not recorded within deadline")

    def _wait_for_dispatch_sent(self, timeout_sec: float = 5.0) -> str:
        self._wait_for_prompt(timeout_sec=timeout_sec)
        tasks = self.state_home / "herdr-orchestrator" / "tasks"
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            found = list(tasks.glob("task_*.json"))
            if found:
                with _env_patch(self.env):
                    try:
                        doc = registry_ops.get_task(found[0].stem)
                        if doc["dispatch"]["status"] == "sent":
                            return found[0].stem
                    except Exception:
                        pass
            time.sleep(0.01)
        raise TimeoutError("dispatch status 'sent' was not reached within deadline")

    def _handoff_base_args(self) -> list[str]:
        return [
            "handoff",
            "--mode",
            "sync",
            "--worker-role",
            "reviewer",
            "--task-type",
            "review",
            "--instruction",
            "please review",
        ]

    def test_async_handoff_returns_without_result_wait(self) -> None:
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["dispatch"]["status"], "sent")
        self.assertEqual(doc["result"]["status"], "pending")
        self.assertNotIn("result_wait", doc)
        self.assertTrue(doc["prompt_sent"])

    def test_sync_handoff_waits_for_terminal_result(self) -> None:
        def submit_later() -> None:
            task_id = self._wait_for_dispatch_sent()
            with _env_patch(self.env):
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    try:
                        registry_ops.update_result(
                            task_id,
                            result_status="succeeded",
                            result_ref="artifacts/review.md",
                            summary="done",
                        )
                        return
                    except RegistryError as exc:
                        if exc.code in ("dispatch_not_ready", "registry_conflict"):
                            time.sleep(0.01)
                            continue
                        raise

        threading.Thread(target=submit_later, daemon=True).start()
        proc = _run_cli(
            *self._handoff_base_args(),
            "--result-wait-timeout-sec",
            "5",
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertTrue(doc["result_wait"]["completed"])
        self.assertEqual(doc["result"]["status"], "succeeded")

    def test_worker_working_rejects_before_create(self) -> None:
        env = self.env.copy()
        env["FAKE_WORKER_STATUS"] = "working"
        proc = _run_cli(*self._handoff_base_args(), env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "worker_unavailable")
        self.assertFalse((self.state_home / "herdr-orchestrator" / "tasks").exists())

    def test_missing_worker_pane_id_rejects_before_create(self) -> None:
        env = self.env.copy()
        env["FAKE_OMIT_WORKER_PANE"] = "1"
        proc = _run_cli(*self._handoff_base_args(), env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "live_pane_id_missing")
        self.assertFalse((self.state_home / "herdr-orchestrator" / "tasks").exists())

    def test_missing_requester_pane_id_rejects_before_create(self) -> None:
        env = self.env.copy()
        env["FAKE_OMIT_REQUESTER_PANE"] = "1"
        proc = _run_cli(*self._handoff_base_args(), env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "live_pane_id_missing")

    def test_prompt_blocked_marks_dispatch_failed(self) -> None:
        env = self.env.copy()
        env["FAKE_PROMPT_MODE"] = "blocked"
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertFalse(doc["prompt_sent"])
        self.assertEqual(doc["dispatch"]["status"], "failed")
        self.assertEqual(doc["task"]["status"], "failed")

    def test_prompt_stalled_marks_dispatch_uncertain(self) -> None:
        env = self.env.copy()
        env["FAKE_PROMPT_MODE"] = "stalled"
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertFalse(doc["prompt_sent"])
        self.assertEqual(doc["dispatch"]["status"], "uncertain")
        self.assertEqual(doc["task"]["status"], "unknown")

    def test_prompt_timeout_marks_dispatch_uncertain(self) -> None:
        env = self.env.copy()
        env["FAKE_PROMPT_MODE"] = "timeout"
        env["HERDR_CLI_TIMEOUT_SEC"] = "0.2"
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertFalse(doc["prompt_sent"])
        self.assertEqual(doc["dispatch"]["status"], "uncertain")
        self.assertEqual(doc["task"]["status"], "unknown")

    def test_prompt_unknown_cli_err_marks_dispatch_uncertain(self) -> None:
        env = self.env.copy()
        env["FAKE_PROMPT_MODE"] = "unknown_cli_err"
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertFalse(doc["prompt_sent"])
        self.assertEqual(doc["dispatch"]["status"], "uncertain")
        self.assertEqual(doc["task"]["status"], "unknown")

    def test_prompt_cli_json_err_without_code_marks_dispatch_uncertain(self) -> None:
        env = self.env.copy()
        env["FAKE_PROMPT_MODE"] = "cli_json_err"
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertFalse(doc["prompt_sent"])
        self.assertEqual(doc["dispatch"]["status"], "uncertain")
        self.assertEqual(doc["task"]["status"], "unknown")

    def test_prompt_external_invalid_timeout_code_marks_dispatch_uncertain(self) -> None:
        env = self.env.copy()
        env["FAKE_PROMPT_MODE"] = "invalid_timeout"
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertFalse(doc["prompt_sent"])
        self.assertEqual(doc["dispatch"]["status"], "uncertain")
        self.assertEqual(doc["task"]["status"], "unknown")

    def test_prompt_internal_invalid_timeout_setting_marks_dispatch_failed(self) -> None:
        from orchestrator.handoff_commands import _dispatch_outcome_from_prompt_error
        from orchestrator.herdr_cli import HerdrCliError

        exc = HerdrCliError("invalid timeout", payload={"error": "invalid_timeout_setting"})
        outcome, reason = _dispatch_outcome_from_prompt_error(exc)
        self.assertEqual(outcome, "failed")
        self.assertEqual(reason, "invalid_timeout_setting")



    def test_assert_failure_skips_prompt(self) -> None:
        env = self.env.copy()
        env["FAKE_WORKER_WORKING_AFTER_N"] = "2"
        if self.worker_get_count.exists():
            self.worker_get_count.unlink()
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=env)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(json.loads(proc.stdout)["error"], "handoff_not_continuable")
        self.assertFalse(self.prompt_log.exists())

    def test_sync_timeout_with_idle_worker_no_resend(self) -> None:
        proc = _run_cli(
            *self._handoff_base_args(),
            "--result-wait-timeout-sec",
            "1",
            env=self.env,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "result_wait_timeout")
        self.assertTrue(err["retryable"])
        if self.prompt_log.exists():
            self.assertEqual(self.prompt_log.read_text(encoding="utf-8").count("prompt"), 1)

    def test_sync_worker_blocked_during_wait(self) -> None:
        blocked_flag = Path(self._tmpdir.name) / "worker_blocked.flag"
        env = self.env.copy()
        env["FAKE_WORKER_BLOCKED_FLAG"] = str(blocked_flag)

        def flip_blocked() -> None:
            self._wait_for_dispatch_sent()
            blocked_flag.write_text("1", encoding="utf-8")

        threading.Thread(target=flip_blocked, daemon=True).start()
        proc = _run_cli(
            *self._handoff_base_args(),
            "--result-wait-timeout-sec",
            "3",
            env=env,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(json.loads(proc.stdout)["error"], "worker_blocked")

    def test_sync_worker_absent_after_dispatch_sent(self) -> None:
        gone_flag = Path(self._tmpdir.name) / "worker_gone.flag"
        env = self.env.copy()
        env["FAKE_WORKER_GONE_FLAG"] = str(gone_flag)

        def mark_gone() -> None:
            self._wait_for_dispatch_sent()
            gone_flag.write_text("1", encoding="utf-8")

        threading.Thread(target=mark_gone, daemon=True).start()
        proc = _run_cli(
            *self._handoff_base_args(),
            "--result-wait-timeout-sec",
            "3",
            env=env,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(json.loads(proc.stdout)["error"], "worker_unavailable")

    def test_mark_dispatch_before_result_submit_window(self) -> None:
        proc = _run_cli(*self._handoff_base_args(), "--mode", "async", env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        task_id = json.loads(proc.stdout)["task_id"]
        submit = _run_cli(
            "result-submit",
            task_id,
            "--result-status",
            "succeeded",
            "--result-ref",
            "artifacts/review.md",
            env={**self.env, "HERDR_PANE_ID": "w11:p2"},
        )
        self.assertEqual(submit.returncode, 0, submit.stdout)

    def test_assert_handoff_registry_lock_sees_race(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
            with self.assertRaises(RegistryError) as ctx:
                registry_ops.assert_handoff_continuable(task_id)
        self.assertEqual(ctx.exception.code, "registry_conflict")

    def test_handoff_lock_session_idempotent_acquire(self) -> None:
        """HandoffLockSession の多重 acquire() 呼び出しがデッドロックせず冪等に動作することを確認"""
        task_id = "task_01234567-89ab-4def-a123-456789abcdef"
        session = HandoffLockSession(task_id)
        with _env_patch(self.env):
            session.acquire()
            session.acquire()  # 2nd call should no-op without deadlocking
            with session:
                pass
            session.release()
            self.assertIsNone(session._fd)

    def test_handoff_lock_write_failure_returns_json_error(self) -> None:
        """handoff ロックファイル作成失敗時に未捕捉例外ではなく構造化エラー registry_write_failed が返る"""
        from unittest.mock import patch
        from orchestrator import public_cli
        import io as py_io
        from contextlib import redirect_stdout, redirect_stderr

        with patch.dict(os.environ, self.env):
            with patch("orchestrator.handoff_lock.os.open", side_effect=PermissionError("Permission denied")):
                stdout_buf = py_io.StringIO()
                stderr_buf = py_io.StringIO()
                with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                    with self.assertRaises(SystemExit) as ctx:
                        public_cli.main([
                            "handoff",
                            "--mode",
                            "async",
                            "--worker-role",
                            "reviewer",
                            "--task-type",
                            "review",
                            "--instruction",
                            "test lock failure",
                        ])
                self.assertEqual(ctx.exception.code, 1)
                data = json.loads(stdout_buf.getvalue())
                self.assertEqual(data.get("error"), "registry_write_failed")
                self.assertIn("Permission denied", data.get("message", ""))


if __name__ == "__main__":
    unittest.main()
