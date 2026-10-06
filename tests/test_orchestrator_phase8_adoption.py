"""
Phase-8 adoption vs implementation INDEX stage 8:
  installer (§16–17), adoption docs (§19), verification log template
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP = REPO_ROOT / "skills" / "herdr-orchestrator" / "install" / "setup.sh"
DEV_INSTALL = REPO_ROOT / "skills" / "herdr-orchestrator" / "install" / "dev-local-install.sh"
VERIFY = REPO_ROOT / "scripts" / "verify-orchestrator.sh"
SNIPPET = REPO_ROOT / "scripts" / "print-automated-verification-snippet.sh"
ADOPTION = REPO_ROOT / "skills" / "herdr-orchestrator" / "references" / "adoption.md"
VERIFY_LOG = REPO_ROOT / "docs" / "herdr-orchestrator-verification-log.md"
SKILL_ROOT = REPO_ROOT / "skills" / "herdr-orchestrator"


class Phase8AdoptionTests(unittest.TestCase):
    def test_setup_sh_syntax_and_help(self) -> None:
        self.assertTrue(SETUP.is_file())
        subprocess.run(["bash", "-n", str(SETUP)], check=True)
        proc = subprocess.run(
            ["bash", str(SETUP), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--dry-run", proc.stdout)

    def test_setup_dry_run_lists_copy_actions(self) -> None:
        proc = subprocess.run(
            ["bash", str(SETUP), "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SKILL.md", proc.stdout)
        self.assertIn("scripts", proc.stdout)

    def test_dev_local_install_defaults_to_herdr_orchestration_name(self) -> None:
        self.assertTrue(DEV_INSTALL.is_file())
        subprocess.run(["bash", "-n", str(DEV_INSTALL)], check=True)
        proc = subprocess.run(
            ["bash", str(DEV_INSTALL), "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("herdr-orchestration", proc.stdout)
        self.assertIn("would ln -sf", proc.stdout)

    def test_dev_local_installs_to_temp_dest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "herdr-orchestration"
            bin_link = Path(tmp) / "bin" / "herdr-orchestrator"
            env = os.environ.copy()
            env["HERDR_ORCHESTRATOR_BIN_LINK"] = str(bin_link)
            proc = subprocess.run(
                ["bash", str(DEV_INSTALL), "--dest", str(dest)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertTrue((dest / "SKILL.md").is_file())
            skill_text = (dest / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("name: herdr-orchestration", skill_text)
            cli = dest / "scripts" / "herdr-orchestrator"
            self.assertTrue(cli.is_file())
            self.assertTrue(cli.stat().st_mode & 0o111)
            self.assertTrue(bin_link.is_symlink())
            self.assertEqual(bin_link.resolve(), cli.resolve())

    def test_setup_installs_to_temp_dest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "skill"
            proc = subprocess.run(
                ["bash", str(SETUP), "--dest", str(dest)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertTrue((dest / "SKILL.md").is_file())
            cli = dest / "scripts" / "herdr-orchestrator"
            self.assertTrue(cli.is_file())
            self.assertTrue(cli.stat().st_mode & 0o111)

    def test_adoption_doc_links_installer_and_migration(self) -> None:
        text = ADOPTION.read_text(encoding="utf-8")
        self.assertIn("dev-local-install.sh", text)
        self.assertIn("herdr-orchestration", text)
        self.assertIn("Phase", text)
        self.assertIn("test:orchestrator", text)
        self.assertIn("verification-log", text)
        self.assertIn("verify:orchestrator", text)

    def test_verify_and_snippet_scripts_exist(self) -> None:
        for path in (VERIFY, SNIPPET):
            self.assertTrue(path.is_file(), path)
            subprocess.run(["bash", "-n", str(path)], check=True)

    def test_verification_log_template_links_sec18(self) -> None:
        text = VERIFY_LOG.read_text(encoding="utf-8")
        self.assertIn("sec-18-3", text)
        self.assertIn("test:orchestrator", text)

    def test_public_cli_help_describes_mvp_commands(self) -> None:
        cli = SKILL_ROOT / "scripts" / "herdr-orchestrator"
        proc = subprocess.run(
            [str(cli), "--help"],
            capture_output=True,
            text=True,
            env={"HERDR_ENV": "", "PATH": os.environ.get("PATH", "")},
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("implementation staged", proc.stdout)
        self.assertIn("resume-task", proc.stdout)


if __name__ == "__main__":
    unittest.main()
