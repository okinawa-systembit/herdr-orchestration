"""
Phase-5 dispatch doctor / reconcile vs §18.3 (stage-5 gate):
  74/83 doctor stale A/B
  75/80 reconcile terminal + result-submit
  84/107 await_human default
  93/106 handoff flock blocks reconcile
  88/113 failed reason + post-failed handoff guard
  98 B' repair via worker trace
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

from orchestrator.handoff_lock import HandoffLockSession
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"
_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]

_FAKE_HERDR_PY = """#!/usr/bin/env python3
import json, os, sys

def agent_payload(name, pane):
    return {"result": {"agent": {
        "name": name,
        "agent_status": "idle",
        "foreground_cwd": os.environ.get("FAKE_AGENT_CWD", "/tmp/herdr-orchestrator-fixture"),
        "pane_id": pane,
    }}}

if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "get":
    target = sys.argv[3]
    worker = os.environ.get("FAKE_WORKER_AGENT", "org-repo-reviewer")
    if target in (worker, "w11:p2"):
        print(json.dumps(agent_payload(worker, "w11:p2")))
        raise SystemExit(0)
    raise SystemExit(99)

if len(sys.argv) >= 3 and sys.argv[1] == "agent" and sys.argv[2] == "read":
    trace = os.environ.get("HERDR_ORCHESTRATOR_WORKER_TRACE_TASK_ID", "")
    text = trace if trace else ""
    print(json.dumps({"result": {"text": text}}))
    raise SystemExit(0)

if len(sys.argv) >= 2 and sys.argv[1] == "--version":
    print("fake-herdr")
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
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(repo), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    artifact = repo / "artifacts" / "review.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("review\n", encoding="utf-8")
    return repo


def _sample_create_payload(*, worktree: Path, mode: str = "async") -> dict:
    return {
        "task": {
            "type": "review",
            "mode": mode,
            "instruction": "review",
        },
        "context": {
            "repository": {"identity": "github.com/org/repo"},
            "worktree": {
                "path": str(worktree.resolve()),
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
        "completion": {"action": "resume_requester" if mode == "async" else "none"},
    }


def _backdate_updated_at(state_home: Path, task_id: str, *, seconds: float) -> None:
    path = state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    old = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    doc["timestamps"]["updated_at"] = old.isoformat()
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


class Phase5DispatchTests(unittest.TestCase):
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
        self.env = os.environ.copy()
        self.env["HERDR_ENV"] = "1"
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["PATH"] = f"{self.fake_bin}:{self.env.get('PATH', '')}"
        self.env["HERDR_ORCHESTRATOR_STALE_DISPATCH_SEC"] = "60"
        self.worktree = _init_worktree(root)
        self.env["FAKE_AGENT_CWD"] = str(self.worktree)
        self.env["FAKE_WORKER_AGENT"] = "org-repo-reviewer"

    def _create_pending(
        self,
        *,
        prompt_started: bool = False,
        mode: str = "async",
    ) -> str:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree, mode=mode))
            task_id = doc["task"]["id"]
            if prompt_started:
                registry_ops.record_prompt_start(task_id)
        _backdate_updated_at(self.state_home, task_id, seconds=120)
        return task_id

    def test_doctor_reports_stale_a_and_b(self) -> None:
        stale_a = self._create_pending(prompt_started=False)
        stale_b = self._create_pending(prompt_started=True)
        proc = _run_cli("doctor", env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["stage"], 5)
        ids = {c["task_id"]: c["scenario"] for c in doc["stale_dispatch"]["candidates"]}
        self.assertEqual(ids.get(stale_a), "A")
        self.assertEqual(ids.get(stale_b), "B")

    def test_doctor_ignores_fresh_pending(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
        proc = _run_cli("doctor", env=self.env)
        doc = json.loads(proc.stdout)
        reported = {c["task_id"] for c in doc["stale_dispatch"]["candidates"]}
        self.assertNotIn(task_id, reported)

    def test_reconcile_default_await_human_no_registry_change(self) -> None:
        task_id = self._create_pending(prompt_started=False)
        path = self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json"
        before = path.read_text(encoding="utf-8")
        proc = _run_cli("reconcile-dispatch", task_id, env=self.env)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertFalse(out["updated"])
        self.assertEqual(out["action"], "await_human")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_reconcile_uncertain_then_result_submit(self) -> None:
        task_id = self._create_pending(prompt_started=True)
        proc = _run_cli(
            "reconcile-dispatch",
            task_id,
            "--dispatch-outcome",
            "uncertain",
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertTrue(out["updated"])
        self.assertEqual(out["dispatch"]["status"], "uncertain")
        self.assertTrue(out["needs_result_reregistration"])
        submit = _run_cli(
            "result-submit",
            task_id,
            "--result-status",
            "succeeded",
            "--result-ref",
            "artifacts/review.md",
            env={
                **self.env,
                "HERDR_PANE_ID": "w11:p2",
                "FAKE_AGENT_CWD": str(self.worktree),
            },
        )
        self.assertEqual(submit.returncode, 0, submit.stdout)

    def test_reconcile_failed_requires_reason(self) -> None:
        task_id = self._create_pending(prompt_started=False)
        proc = _run_cli(
            "reconcile-dispatch",
            task_id,
            "--dispatch-outcome",
            "failed",
            env=self.env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "invalid_input")

    def test_reconcile_failed_terminal_blocks_record_prompt_start(self) -> None:
        task_id = self._create_pending(prompt_started=False)
        proc = _run_cli(
            "reconcile-dispatch",
            task_id,
            "--dispatch-outcome",
            "failed",
            "--dispatch-reason",
            "operator_confirmed_no_delivery",
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        with _env_patch(self.env):
            with self.assertRaises(RegistryError) as ctx:
                registry_ops.record_prompt_start(task_id)
        self.assertEqual(ctx.exception.code, "registry_conflict")

    def test_reconcile_blocked_while_handoff_lock_held(self) -> None:
        task_id = self._create_pending(prompt_started=True)
        with _env_patch(self.env):
            lock = HandoffLockSession(task_id)
            lock.acquire()
            self.addCleanup(lock.release)
            proc = _run_cli(
                "reconcile-dispatch",
                task_id,
                "--dispatch-outcome",
                "uncertain",
                env=self.env,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stdout)["error"], "registry_conflict")

    def test_reconcile_b_prime_repairs_prompt_timestamp(self) -> None:
        task_id = self._create_pending(prompt_started=False)
        env = {**self.env, "HERDR_ORCHESTRATOR_WORKER_TRACE_TASK_ID": task_id}
        proc = _run_cli(
            "reconcile-dispatch",
            task_id,
            "--dispatch-outcome",
            "uncertain",
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertTrue(out.get("repaired_b_prime"))
        self.assertEqual(out["scenario"], "B")
        path = self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsNotNone(doc["dispatch"].get("herdr_prompt_started_at"))

    def test_reconcile_rejects_non_pending_dispatch(self) -> None:
        task_id = self._create_pending(prompt_started=True)
        with _env_patch(self.env):
            registry_ops.mark_dispatch(task_id, outcome="sent")
        proc = _run_cli(
            "reconcile-dispatch",
            task_id,
            "--dispatch-outcome",
            "uncertain",
            env=self.env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "registry_conflict")


if __name__ == "__main__":
    unittest.main()
