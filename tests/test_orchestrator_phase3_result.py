"""
Phase-3 result CLI vs docs §18.3 (stage-3 gate):
  19–22 durable result / ref / path safety / task lookup
  59 ref escape
  63 failed|unavailable → task.status=failed
  67–68 worker context + idempotent replay
  78–80 dispatch pending retryable + reconcile path via registry
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.registry import operations as registry_ops

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"

_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(worktree), *args],
        check=True,
        capture_output=True,
    )


def _init_worktree(root: Path, *, origin: str = "https://github.com/org/repo.git") -> Path:
    repo = root / "wt"
    if repo.exists():
        repo = root / f"wt_{origin.split('/')[-1].replace('.git', '')}"
    subprocess.run(
        ["git", *_GIT_IDENTITY, "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "remote", "add", "origin", origin)
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    for ref in ("artifacts/review.md", "artifacts/out.md", "artifacts/err.md"):
        path = repo / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("review\n", encoding="utf-8")
    return repo


def _sample_create_payload(*, worktree: Path, mode: str = "sync") -> dict:
    return {
        "task": {
            "type": "review",
            "mode": mode,
            "instruction": "review changes",
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
        "completion": {"action": "none" if mode == "sync" else "resume_requester"},
    }


def _run_cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        check=False,
    )


def _submit_args(
    task_id: str,
    *,
    status: str = "succeeded",
    ref: str | None = "artifacts/review.md",
    summary: str | None = None,
) -> list[str]:
    args = ["result-submit", task_id, "--result-status", status]
    if ref is not None:
        args.extend(["--result-ref", ref])
    if summary is not None:
        args.extend(["--summary", summary])
    return args


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


class Phase3ResultCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        root = Path(self._tmpdir.name)
        self.worktree = _init_worktree(root)
        self.state_home = root / "state"
        self.fake_bin = root / "bin"
        self.fake_bin.mkdir()
        fake_herdr = self.fake_bin / "herdr"
        fake_herdr.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            'if len(sys.argv) >= 3 and sys.argv[1] == "agent" and sys.argv[2] == "get":\n'
            '    cwd = os.environ.get("FAKE_AGENT_CWD", "/nonexistent")\n'
            '    name = os.environ.get("FAKE_AGENT_NAME", "org-repo-reviewer")\n'
            "    print(json.dumps({\n"
            '        "result": {\n'
            '            "agent": {\n'
            '                "name": name,\n'
            '                "agent_status": "working",\n'
            '                "foreground_cwd": cwd,\n'
            '                "pane_id": os.environ.get("HERDR_PANE_ID", "w11:p2"),\n'
            "            }\n"
            "        }\n"
            "    }))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(99)\n",
            encoding="utf-8",
        )
        fake_herdr.chmod(fake_herdr.stat().st_mode | stat.S_IXUSR)
        self.env = os.environ.copy()
        self.env["HERDR_ENV"] = "1"
        self.env["HERDR_PANE_ID"] = "w11:p2"
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["PATH"] = f"{self.fake_bin}:{self.env.get('PATH', '')}"
        self.env["FAKE_AGENT_CWD"] = str(self.worktree / "sub")
        self.env["FAKE_AGENT_NAME"] = "org-repo-reviewer"
        (self.worktree / "sub").mkdir(exist_ok=True)

    def _ready_task(self) -> str:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="sent")
        return task_id

    def test_task_status_returns_canonical_task(self) -> None:
        task_id = self._ready_task()
        proc = _run_cli("task-status", task_id, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["command"], "task-status")
        self.assertEqual(doc["task"]["task"]["id"], task_id)

    def test_invalid_task_id_emits_json_error(self) -> None:
        proc = _run_cli("task-status", "not-a-task", env=self.env)
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "invalid_task_id")

    def test_task_result_resolves_ref_from_subdir_cwd(self) -> None:
        task_id = self._ready_task()
        with _env_patch(self.env):
            registry_ops.update_result(
                task_id,
                result_status="succeeded",
                result_ref="artifacts/review.md",
                summary="ok",
            )
        proc = _run_cli("task-result", task_id, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["result"]["status"], "succeeded")
        self.assertIn("artifacts/review.md", doc["ref_resolved_path"])

    def test_result_submit_success(self) -> None:
        task_id = self._ready_task()
        proc = _run_cli(
            *_submit_args(task_id, summary="done"),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertFalse(doc["idempotent_replay"])
        self.assertEqual(doc["result"]["status"], "succeeded")

    def test_result_submit_idempotent_replay(self) -> None:
        task_id = self._ready_task()
        args = _submit_args(task_id, summary="done")
        proc1 = _run_cli(*args, env=self.env)
        self.assertEqual(proc1.returncode, 0, proc1.stdout)
        proc2 = _run_cli(*args, env=self.env)
        self.assertEqual(proc2.returncode, 0, proc2.stdout)
        doc = json.loads(proc2.stdout)
        self.assertTrue(doc["idempotent_replay"])

    def test_result_submit_rejects_mismatch_payload_after_terminal(self) -> None:
        task_id = self._ready_task()
        base = _submit_args(task_id, summary="done")
        self.assertEqual(_run_cli(*base, env=self.env).returncode, 0)
        proc = _run_cli(*base, "--summary", "other", env=self.env)
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "registry_conflict")

    def test_dispatch_pending_retryable(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(_sample_create_payload(worktree=self.worktree))
            task_id = doc["task"]["id"]
        proc = _run_cli(*_submit_args(task_id), env=self.env)
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "dispatch_not_ready")
        self.assertTrue(err["retryable"])
        self.assertIn("hint", err)

    def test_caller_agent_mismatch_not_registry_worker(self) -> None:
        task_id = self._ready_task()
        env = self.env.copy()
        env["FAKE_AGENT_NAME"] = "org-repo-design"
        proc = _run_cli(*_submit_args(task_id), env=env)
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "worker_agent_mismatch")

    def test_worker_context_via_subdir_foreground_cwd(self) -> None:
        task_id = self._ready_task()
        proc = _run_cli(*_submit_args(task_id), env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_wrong_worktree_caller_rejected(self) -> None:
        other = _init_worktree(Path(self._tmpdir.name) / "other")
        (other / "nested").mkdir(exist_ok=True)
        task_id = self._ready_task()
        before = (self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
        env = self.env.copy()
        env["FAKE_AGENT_CWD"] = str(other / "nested")
        proc = _run_cli(*_submit_args(task_id), env=env)
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "worker_context_unverified")
        after = (self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(before, after)

    def test_non_git_caller_rejected(self) -> None:
        task_id = self._ready_task()
        nogit = Path(self._tmpdir.name) / "nogit"
        nogit.mkdir()
        before = (self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
        env = self.env.copy()
        env["FAKE_AGENT_CWD"] = str(nogit)
        proc = _run_cli(*_submit_args(task_id), env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "worker_context_unverified")
        after = (self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(before, after)

    def test_repository_identity_mismatch_same_worktree_rejected(self) -> None:
        task_id = self._ready_task()
        task_path = self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json"
        before = task_path.read_text(encoding="utf-8")
        _git(
            self.worktree,
            "remote",
            "set-url",
            "origin",
            "https://github.com/other/repo.git",
        )
        self.addCleanup(
            lambda: _git(
                self.worktree,
                "remote",
                "set-url",
                "origin",
                "https://github.com/org/repo.git",
            )
        )
        proc = _run_cli(*_submit_args(task_id), env=self.env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "worker_context_unverified")
        self.assertEqual(task_path.read_text(encoding="utf-8"), before)

    def test_symlink_ref_to_outside_file_rejected(self) -> None:
        outside = Path(self._tmpdir.name) / "outside-artifact.md"
        outside.write_text("secret\n", encoding="utf-8")
        link = self.worktree / "artifacts" / "outside-link.md"
        link.symlink_to(outside)
        task_id = self._ready_task()
        task_path = self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json"
        before = task_path.read_text(encoding="utf-8")
        proc = _run_cli(
            *_submit_args(task_id, ref="artifacts/outside-link.md"),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "invalid_result_ref")
        self.assertEqual(task_path.read_text(encoding="utf-8"), before)

    def test_result_failed_sets_task_status_failed(self) -> None:
        task_id = self._ready_task()
        proc = _run_cli(
            *_submit_args(task_id, status="failed", ref="artifacts/err.md", summary="failed run"),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["result"]["status"], "failed")
        self.assertEqual(doc["task"]["status"], "failed")

    def test_unavailable_sets_task_failed(self) -> None:
        task_id = self._ready_task()
        proc = _run_cli(
            *_submit_args(task_id, status="unavailable", ref=None, summary="lost"),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["task"]["status"], "failed")
        self.assertEqual(doc["result"]["status"], "unavailable")

    def test_missing_artifact_rejected(self) -> None:
        task_id = self._ready_task()
        proc = _run_cli(
            *_submit_args(task_id, ref="artifacts/missing.md"),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "artifact_missing")

    def test_path_escape_ref_rejected(self) -> None:
        task_id = self._ready_task()
        proc = _run_cli(
            *_submit_args(task_id, ref="../outside.md"),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "invalid_result_ref")

    def test_url_ref_rejected_even_when_colon_path_exists(self) -> None:
        colon_path = self.worktree / "https:" / "host" / "file"
        colon_path.parent.mkdir(parents=True, exist_ok=True)
        colon_path.write_text("decoy\n", encoding="utf-8")
        task_id = self._ready_task()
        proc = _run_cli(
            *_submit_args(task_id, ref="https://host/file"),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "invalid_result_ref")

    def test_initial_dispatch_failure_rejects_unavailable_replay(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(
                _sample_create_payload(worktree=self.worktree, mode="async")
            )
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.mark_dispatch(task_id, outcome="failed", reason="delivery_failed")
            before = (self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        proc = _run_cli(
            *_submit_args(task_id, status="unavailable", ref=None),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "registry_conflict")
        after = (self.state_home / "herdr-orchestrator" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(before, after)

    def test_reconcile_then_submit(self) -> None:
        with _env_patch(self.env):
            doc = registry_ops.create_task(
                _sample_create_payload(worktree=self.worktree, mode="async")
            )
            task_id = doc["task"]["id"]
            registry_ops.record_prompt_start(task_id)
            registry_ops.reconcile_dispatch(task_id, dispatch_outcome="uncertain")
        proc = _run_cli(
            *_submit_args(task_id, ref="artifacts/out.md"),
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
