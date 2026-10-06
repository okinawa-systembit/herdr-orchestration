"""
Phase-7 central Skill + repository workflow contract (stage-7 gate, docs/skill):
  §14 boundary, §11.4 linked from central + repository skill, public CLI surface
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"
CENTRAL_SKILL = REPO_ROOT / "skills" / "herdr-orchestrator" / "SKILL.md"
REPO_SKILL = REPO_ROOT / ".agents" / "skills" / "repo-workflow" / "SKILL.md"
REFERENCES = REPO_ROOT / "skills" / "herdr-orchestrator" / "references"


class Phase7SkillContractTests(unittest.TestCase):
    def test_central_skill_links_worker_and_requester_receive(self) -> None:
        text = CENTRAL_SKILL.read_text(encoding="utf-8")
        self.assertIn("worker-receive.md", text)
        self.assertIn("requester-receive.md", text)
        self.assertIn("public-cli.md", text)

    def test_reference_files_exist_and_cite_cli(self) -> None:
        for name in ("worker-receive.md", "requester-receive.md", "public-cli.md"):
            path = REFERENCES / name
            self.assertTrue(path.is_file(), name)
            body = path.read_text(encoding="utf-8")
            self.assertIn("task-status", body)
            self.assertIn("herdr-orchestrator", body)

    def test_worker_receive_documents_dispatch_poll(self) -> None:
        body = (REFERENCES / "worker-receive.md").read_text(encoding="utf-8")
        self.assertIn("pending", body)
        self.assertIn("result-submit", body)
        self.assertIn("dispatch_not_ready", body)

    def test_repository_skill_wires_handoff_and_submit(self) -> None:
        self.assertTrue(REPO_SKILL.is_file())
        text = REPO_SKILL.read_text(encoding="utf-8")
        self.assertIn("handoff", text)
        self.assertIn("result-submit", text)
        self.assertIn("worker-receive.md", text)
        self.assertIn("task-result", text)

    def test_public_cli_help_lists_completion_commands(self) -> None:
        env = os.environ.copy()
        env["HERDR_ENV"] = ""
        proc = subprocess.run(
            [str(CLI), "--help"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for cmd in (
            "handoff",
            "resume-task",
            "reset-completion",
            "recover-completion",
            "reconcile-dispatch",
            "doctor",
        ):
            self.assertIn(cmd, proc.stdout, cmd)


if __name__ == "__main__":
    unittest.main()
