"""
Tests for Codex execpolicy approval rules template (Issue #59):
  skills/herdr-orchestrator/install/codex-execpolicy-snippet.rules
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = (
    REPO_ROOT
    / "skills"
    / "herdr-orchestrator"
    / "install"
    / "codex-execpolicy-snippet.rules"
)


class CodexExecpolicyStaticTests(unittest.TestCase):
    """Static inspection of the execpolicy rules file structure and contents."""

    def setUp(self) -> None:
        self.assertTrue(RULES_PATH.is_file(), f"Rules file not found: {RULES_PATH}")
        self.content = RULES_PATH.read_text(encoding="utf-8")

    def test_file_exists_and_has_documentation(self) -> None:
        self.assertGreater(len(self.content), 100)
        self.assertIn("Herdr Orchestrator: Codex execpolicy approval rules snippet", self.content)
        self.assertIn("identity show", self.content)
        self.assertIn("identity init", self.content)
        self.assertIn("result-submit", self.content)
        self.assertIn("handoff", self.content)

    def test_identity_show_is_explicitly_allowed(self) -> None:
        # Must allow "identity", "show"
        self.assertIn('["herdr-orchestrator", "identity", "show"]', self.content)
        self.assertIn('["GLOBAL_INSTALL_ABS/scripts/herdr-orchestrator", "identity", "show"]', self.content)

    def test_identity_init_is_not_allowed(self) -> None:
        # Must NOT include identity init as an allowed pattern
        self.assertNotIn('"identity", "init"', self.content)
        self.assertNotIn('["identity", "init"]', self.content)
        # Must not allow generic "identity" without subcommands
        self.assertNotIn('pattern=["herdr-orchestrator", "identity"]', self.content)

    def test_dangerous_commands_are_not_allowed(self) -> None:
        banned_commands = (
            "handoff",
            "resume-task",
            "reset-completion",
            "recover-completion",
            "reconcile-dispatch",
        )
        for cmd in banned_commands:
            # Command should be mentioned in comments explaining exclusion,
            # but NOT in a prefix_rule pattern definition
            rule_pattern_matches = [
                line
                for line in self.content.splitlines()
                if line.strip().startswith("prefix_rule") and f'"{cmd}"' in line
            ]
            self.assertEqual(
                rule_pattern_matches,
                [],
                f"Command {cmd} must not be present in any prefix_rule pattern",
            )

    def test_result_submit_has_side_effect_documentation(self) -> None:
        self.assertIn('["herdr-orchestrator", "result-submit"]', self.content)
        # Side-effects must be documented
        self.assertIn("Task Registry", self.content)
        self.assertIn("completion", self.content)

    def test_npm_rules_are_retained(self) -> None:
        self.assertIn('["npm", "run", "test:orchestrator"]', self.content)
        self.assertIn('["npm", "run", "verify:orchestrator"]', self.content)


class CodexExecpolicyDynamicEvaluationTests(unittest.TestCase):
    """Dynamic evaluation tests using `codex execpolicy check` CLI when available."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.codex_bin = shutil.which("codex")

    def setUp(self) -> None:
        if not self.codex_bin:
            self.skipTest("codex CLI is not installed in the current environment")

    def _check_command(
        self, rules_file: Path, command_tokens: list[str]
    ) -> dict[str, object]:
        proc = subprocess.run(
            [
                str(self.codex_bin),
                "execpolicy",
                "check",
                "--rules",
                str(rules_file),
                "--pretty",
                "--",
                *command_tokens,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"codex execpolicy check failed: {proc.stderr}\nCommand: {command_tokens}",
        )
        return json.loads(proc.stdout)

    def test_path_commands_allowed_and_unallowed_boundaries(self) -> None:
        cases = [
            # Allowed commands
            (["herdr-orchestrator", "doctor"], True),
            (["herdr-orchestrator", "task-status", "task_001"], True),
            (["herdr-orchestrator", "task-result", "task_001"], True),
            (["herdr-orchestrator", "identity", "show"], True),
            (["herdr-orchestrator", "result-submit", "task_001", "--result-status", "succeeded"], True),
            # Unallowed / Prompt-required commands
            (["herdr-orchestrator", "identity", "init"], False),
            (["herdr-orchestrator", "identity"], False),
            (["herdr-orchestrator", "handoff", "--mode", "sync"], False),
            (["herdr-orchestrator", "resume-task", "task_001"], False),
            (["herdr-orchestrator", "reset-completion", "task_001"], False),
            (["herdr-orchestrator", "recover-completion", "task_001"], False),
            (["herdr-orchestrator", "reconcile-dispatch"], False),
            # Other commands
            (["herdr-orchestrator", "unknown-command"], False),
        ]
        for cmd, expected_allow in cases:
            with self.subTest(cmd=cmd, expected_allow=expected_allow):
                res = self._check_command(RULES_PATH, cmd)
                matched = res.get("matchedRules", [])
                decision = res.get("decision")
                if expected_allow:
                    self.assertEqual(decision, "allow", f"Expected allow for {cmd}, got: {res}")
                    self.assertTrue(len(matched) > 0)
                else:
                    self.assertEqual(matched, [], f"Expected no match for {cmd}, got: {res}")

    def test_absolute_path_commands_with_placeholder_replacement(self) -> None:
        test_dir = "/tmp/test-herdr-install"
        replaced_content = RULES_PATH.read_text(encoding="utf-8").replace(
            "GLOBAL_INSTALL_ABS", test_dir
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".rules") as tf:
            tf.write(replaced_content)
            tf.flush()
            temp_rules_path = Path(tf.name)

            custom_cli = f"{test_dir}/scripts/herdr-orchestrator"
            cases = [
                ([custom_cli, "doctor"], True),
                ([custom_cli, "task-status", "task_001"], True),
                ([custom_cli, "task-result", "task_001"], True),
                ([custom_cli, "identity", "show"], True),
                ([custom_cli, "result-submit", "task_001"], True),
                ([custom_cli, "identity", "init"], False),
                ([custom_cli, "handoff"], False),
                ([custom_cli, "resume-task", "task_001"], False),
                ([custom_cli, "reset-completion", "task_001"], False),
                ([custom_cli, "recover-completion", "task_001"], False),
                ([custom_cli, "reconcile-dispatch"], False),
            ]
            for cmd, expected_allow in cases:
                with self.subTest(cmd=cmd, expected_allow=expected_allow):
                    res = self._check_command(temp_rules_path, cmd)
                    matched = res.get("matchedRules", [])
                    decision = res.get("decision")
                    if expected_allow:
                        self.assertEqual(decision, "allow", f"Expected allow for {cmd}, got: {res}")
                        self.assertTrue(len(matched) > 0)
                    else:
                        self.assertEqual(matched, [], f"Expected no match for {cmd}, got: {res}")

    def test_npm_commands(self) -> None:
        cases = [
            (["npm", "run", "test:orchestrator"], True),
            (["npm", "run", "verify:orchestrator"], True),
            (["npm", "run", "build"], False),
            (["npm", "test"], False),
        ]
        for cmd, expected_allow in cases:
            with self.subTest(cmd=cmd, expected_allow=expected_allow):
                res = self._check_command(RULES_PATH, cmd)
                matched = res.get("matchedRules", [])
                decision = res.get("decision")
                if expected_allow:
                    self.assertEqual(decision, "allow", f"Expected allow for {cmd}, got: {res}")
                else:
                    self.assertEqual(matched, [], f"Expected no match for {cmd}, got: {res}")


if __name__ == "__main__":
    unittest.main()
