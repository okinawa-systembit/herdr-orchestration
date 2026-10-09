from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"
REGISTRY_CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "task-registry.py"


def run_cli(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    base = os.environ.copy()
    if env is not None:
        base.update(env)
    return subprocess.run(
        [str(CLI), *args],
        capture_output=True,
        text=True,
        env=base,
        cwd=str(cwd or REPO_ROOT),
        check=False,
    )


class Phase0EnvGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.state_home = Path(self._tmpdir.name) / "state"
        self.state_home.mkdir()
        self.env_base = {
            "XDG_STATE_HOME": str(self.state_home),
        }
        self.fake_bin = Path(self._tmpdir.name) / "bin"
        self.fake_bin.mkdir()
        fake_herdr = self.fake_bin / "herdr"
        self.fake_marker = Path(self._tmpdir.name) / "herdr-invoked"
        fake_herdr.write_text(
            "#!/bin/sh\n"
            f'test -n "$FAKE_HERDR_MARKER" && echo 1 > "$FAKE_HERDR_MARKER"\n'
            "exit 99\n",
            encoding="utf-8",
        )
        fake_herdr.chmod(fake_herdr.stat().st_mode | stat.S_IXUSR)

    def env_without_herdr(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.env_base)
        env["HERDR_ENV"] = ""
        env["PATH"] = f"{self.fake_bin}:{env.get('PATH', '')}"
        env["FAKE_HERDR_MARKER"] = str(self.fake_marker)
        return env

    def env_with_herdr(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.env_base)
        env["HERDR_ENV"] = "1"
        env["PATH"] = f"{self.fake_bin}:{env.get('PATH', '')}"
        env["FAKE_HERDR_MARKER"] = str(self.fake_marker)
        return env

    def tasks_dir(self) -> Path:
        return self.state_home / "herdr-orchestrator" / "tasks"

    def test_help_exits_zero_without_herdr_env(self) -> None:
        proc = run_cli("--help", env=self.env_without_herdr())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("handoff", proc.stdout)

    def test_doctor_without_herdr_env_skips_herdr_cli(self) -> None:
        proc = run_cli("doctor", env=self.env_without_herdr())
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertFalse(self.fake_marker.exists())
        doc = json.loads(proc.stdout)
        self.assertFalse(doc["herdr_env"])
        self.assertTrue(doc.get("herdr_cli_skipped"))
        self.assertNotIn("herdr_version", doc)

    def test_doctor_with_herdr_env_invokes_herdr_cli(self) -> None:
        proc = run_cli("doctor", env=self.env_with_herdr())
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(self.fake_marker.exists(), "fake herdr should run when HERDR_ENV=1")
        doc = json.loads(proc.stdout)
        self.assertIn("herdr_version_error", doc)

    def test_handoff_without_herdr_env_fails_and_creates_no_registry(self) -> None:
        proc = run_cli(
            "handoff",
            "--mode",
            "sync",
            "--worker-role",
            "reviewer",
            "--task-type",
            "review",
            "--instruction",
            "review",
            env=self.env_without_herdr(),
        )
        self.assertEqual(proc.returncode, 1, proc.stdout)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "herdr_env_required")
        self.assertFalse(self.tasks_dir().exists())

    def test_task_status_without_herdr_env_fails(self) -> None:
        proc = run_cli(
            "task-status",
            "task_00000000-0000-4000-8000-000000000001",
            env=self.env_without_herdr(),
        )
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "herdr_env_required")

    def test_handoff_with_herdr_env_fails_before_registry_without_context(self) -> None:
        proc = run_cli(
            "handoff",
            "--mode",
            "sync",
            "--worker-role",
            "reviewer",
            "--task-type",
            "review",
            "--instruction",
            "review",
            env=self.env_with_herdr(),
        )
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertIn(err["error"], ("requester_context_unverified", "worker_unavailable"))
        self.assertFalse(self.tasks_dir().exists())


class TaskRegistryCliSmokeTests(unittest.TestCase):
    def test_get_missing_task(self) -> None:
        proc = subprocess.run(
            [str(REGISTRY_CLI), "get", "task_00000000-0000-4000-8000-000000000001"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        err = json.loads(proc.stdout)
        self.assertEqual(err["error"], "task_not_found")


class HerdrCliTimeoutValidationTests(unittest.TestCase):
    def test_validate_timeout_rejects_non_positive_and_non_finite(self) -> None:
        from orchestrator.herdr_cli import HerdrCliError, validate_timeout

        for bad in (0, -1, -0.5, float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(HerdrCliError) as ctx:
                validate_timeout(bad)
            self.assertEqual(ctx.exception.payload.get("error"), "invalid_timeout_setting")

    def test_validate_timeout_accepts_positive_finite(self) -> None:
        from orchestrator.herdr_cli import validate_timeout

        self.assertEqual(validate_timeout(0.1), 0.1)
        self.assertEqual(validate_timeout(30.0), 30.0)
        self.assertIsNone(validate_timeout(None))

    def test_run_herdr_rejects_invalid_timeout_env(self) -> None:
        from unittest.mock import patch
        from orchestrator.herdr_cli import HerdrCliError, run_herdr

        for bad_str in ("0", "-1", "nan", "inf", "-inf", "abc"):
            with patch.dict(os.environ, {"HERDR_CLI_TIMEOUT_SEC": bad_str}):
                with self.assertRaises(HerdrCliError) as ctx:
                    run_herdr(["--version"])
                self.assertEqual(ctx.exception.payload.get("error"), "invalid_timeout_setting")


class ClassifyPromptErrorTests(unittest.TestCase):
    def test_external_known_undelivered_marks_failed(self) -> None:
        from orchestrator.herdr_cli import HerdrCliError, classify_prompt_error

        for code in ("agent_not_found", "agent_not_ready", "agent_blocked"):
            exc = HerdrCliError("failed", payload={"herdr": {"error": {"code": code}}})
            outcome, reason = classify_prompt_error(exc)
            self.assertEqual(outcome, "failed")
            self.assertEqual(reason, code)

    def test_external_timeout_and_unknown_and_colliding_code_marks_uncertain(self) -> None:
        from orchestrator.herdr_cli import HerdrCliError, classify_prompt_error

        for code in (
            "herdr_cli_timeout",
            "agent_prompt_stalled",
            "unknown_error",
            "invalid_timeout_setting",  # External CLI returning same code as internal error
            "herdr_cli_missing",        # External CLI returning same code as internal error
        ):
            exc = HerdrCliError("failed", payload={"herdr": {"error": {"code": code}}})
            outcome, reason = classify_prompt_error(exc)
            self.assertEqual(outcome, "uncertain")
            self.assertEqual(reason, code)

    def test_internal_pre_execution_errors_marks_failed(self) -> None:
        from orchestrator.herdr_cli import HerdrCliError, classify_prompt_error

        for err in ("herdr_cli_missing", "invalid_timeout_setting"):
            exc = HerdrCliError("failed", payload={"error": err})
            outcome, reason = classify_prompt_error(exc)
            self.assertEqual(outcome, "failed")
            self.assertEqual(reason, err)

    def test_internal_unknown_or_malformed_marks_uncertain(self) -> None:
        from orchestrator.herdr_cli import HerdrCliError, classify_prompt_error

        exc = HerdrCliError("failed", payload={"error": "unexpected_internal_err"})
        outcome, reason = classify_prompt_error(exc)
        self.assertEqual(outcome, "uncertain")
        self.assertEqual(reason, "unexpected_internal_err")

        exc2 = HerdrCliError("failed", payload={})
        outcome2, reason2 = classify_prompt_error(exc2)
        self.assertEqual(outcome2, "uncertain")
        self.assertEqual(reason2, "herdr_prompt_failed")


if __name__ == "__main__":
    unittest.main()

