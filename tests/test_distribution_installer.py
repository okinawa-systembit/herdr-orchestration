"""
Tests for distribution installer script (install.sh).
Verifies options, dry-run, archive validation, integrity checks, and swap/rollback.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"
SKILL_ROOT = REPO_ROOT / "skills" / "herdr-orchestrator"


def create_sample_tarball(target_path: Path, *, extra_files: dict[str, str] | None = None) -> None:
    """Create a valid distribution tarball matching distribution-install specifications."""
    with tarfile.open(target_path, "w:gz") as tar:
        # Add SKILL.md
        skill_md = SKILL_ROOT / "SKILL.md"
        tar.add(skill_md, arcname="SKILL.md")

        # Add scripts directory
        scripts_dir = SKILL_ROOT / "scripts"
        tar.add(scripts_dir, arcname="scripts")

        # Add references directory
        ref_dir = SKILL_ROOT / "references"
        tar.add(ref_dir, arcname="references")

        if extra_files:
            for arcname, content in extra_files.items():
                import io
                data = content.encode("utf-8")
                ti = tarfile.TarInfo(name=arcname)
                ti.size = len(data)
                ti.mode = 0o644
                tar.addfile(ti, io.BytesIO(data))


class DistributionInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(INSTALL_SH.is_file(), "install.sh must exist at repo root")
        self.assertTrue(os.access(INSTALL_SH, os.X_OK), "install.sh must be executable")

    def test_syntax(self) -> None:
        subprocess.run(["bash", "-n", str(INSTALL_SH)], check=True)

    def test_help_option(self) -> None:
        proc = subprocess.run(
            [str(INSTALL_SH), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Usage: install.sh", proc.stdout)
        self.assertIn("--dry-run", proc.stdout)
        self.assertIn("--version", proc.stdout)
        self.assertIn("--dest", proc.stdout)
        self.assertIn("--bin-dir", proc.stdout)

    def test_unknown_option_fails(self) -> None:
        proc = subprocess.run(
            [str(INSTALL_SH), "--invalid-option-xyz"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Error: Unknown option", proc.stderr)

    def test_invalid_version_format_fails(self) -> None:
        for bad_version in ("v-invalid", "1.0.0", "v1.0", "v1.0.0_beta"):
            proc = subprocess.run(
                [str(INSTALL_SH), "--version", bad_version],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 1, f"Expected {bad_version} to be rejected")
            self.assertIn("Invalid version format", proc.stderr)

    def test_dry_run_does_not_modify_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "skill"
            bin_dir = tmp_path / "bin"
            proc = subprocess.run(
                [
                    str(INSTALL_SH),
                    "--version", "v0.1.0",
                    "--dest", str(dest),
                    "--bin-dir", str(bin_dir),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Dry-run: Herdr Orchestrator Distribution Installation", proc.stdout)
            self.assertIn("Planned Actions:", proc.stdout)
            # Filesystem must remain untouched
            self.assertFalse(dest.exists())
            self.assertFalse(bin_dir.exists())

    def test_successful_installation_from_local_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball_path = tmp_path / "package.tar.gz"
            create_sample_tarball(tarball_path)

            dest = tmp_path / "installed_skill"
            bin_dir = tmp_path / "installed_bin"

            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(tarball_path)

            proc = subprocess.run(
                [
                    str(INSTALL_SH),
                    "--dest", str(dest),
                    "--bin-dir", str(bin_dir),
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("Successfully installed", proc.stdout)

            # Verify files installed
            self.assertTrue((dest / "SKILL.md").is_file())
            self.assertTrue((dest / "scripts" / "herdr-orchestrator").is_file())
            self.assertTrue((dest / "scripts" / "task-registry.py").is_file())
            self.assertTrue(os.access(dest / "scripts" / "herdr-orchestrator", os.X_OK))

            # Verify CLI symlink
            cli_link = bin_dir / "herdr-orchestrator"
            self.assertTrue(cli_link.is_symlink())
            self.assertEqual(cli_link.resolve(), (dest / "scripts" / "herdr-orchestrator").resolve())

            # Verify CLI executes
            cli_proc = subprocess.run(
                [str(cli_link), "--help"],
                capture_output=True,
                text=True,
                env={"HERDR_ENV": "", "PATH": os.environ.get("PATH", "")},
                check=False,
            )
            self.assertEqual(cli_proc.returncode, 0, cli_proc.stderr)
            self.assertIn("Herdr Orchestrator public CLI", cli_proc.stdout)

    def test_checksum_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball_path = tmp_path / "package.tar.gz"
            create_sample_tarball(tarball_path)

            bad_checksum = tmp_path / "bad.sha256"
            bad_checksum.write_text("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  package.tar.gz\n")

            dest = tmp_path / "skill"
            bin_dir = tmp_path / "bin"

            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(tarball_path)
            env["HERDR_INSTALL_LOCAL_CHECKSUM"] = str(bad_checksum)

            proc = subprocess.run(
                [
                    str(INSTALL_SH),
                    "--dest", str(dest),
                    "--bin-dir", str(bin_dir),
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Checksum verification failed", proc.stderr)
            self.assertFalse(dest.exists(), "Destination should not be created on checksum failure")

    def test_rejects_archive_with_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball_path = tmp_path / "malicious_traversal.tar.gz"
            with tarfile.open(tarball_path, "w:gz") as tar:
                import io
                ti = tarfile.TarInfo(name="../escape.txt")
                ti.size = 5
                tar.addfile(ti, io.BytesIO(b"hello"))

            dest = tmp_path / "skill"
            bin_dir = tmp_path / "bin"
            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(tarball_path)

            proc = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest), "--bin-dir", str(bin_dir)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Security Error: Archive contains path traversal", proc.stderr)
            self.assertFalse(dest.exists())

    def test_rejects_archive_with_unexpected_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tarball_path = tmp_path / "unexpected.tar.gz"
            create_sample_tarball(tarball_path, extra_files={"malicious_script.sh": "echo evil"})

            dest = tmp_path / "skill"
            bin_dir = tmp_path / "bin"
            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(tarball_path)

            proc = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest), "--bin-dir", str(bin_dir)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Security Error: Archive contains unexpected entry", proc.stderr)
            self.assertFalse(dest.exists())

    def test_rejects_git_repository_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "dev_skill"
            dest.mkdir()
            (dest / ".git").mkdir()
            (dest / "local_edit.txt").write_text("important")

            tarball_path = tmp_path / "package.tar.gz"
            create_sample_tarball(tarball_path)

            bin_dir = tmp_path / "bin"
            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(tarball_path)

            proc = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest), "--bin-dir", str(bin_dir)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("appears to be a git repository", proc.stderr)
            self.assertTrue((dest / "local_edit.txt").is_file(), "Existing files must not be deleted")

            # With --force, installation proceeds
            force_proc = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest), "--bin-dir", str(bin_dir), "--force"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(force_proc.returncode, 0, force_proc.stderr)
            self.assertTrue((dest / "SKILL.md").is_file())

    def test_exclusive_lock_prevents_concurrent_install(self) -> None:
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            tmp_home = Path(tmp)
            lock_dir = tmp_home / ".agents" / "skills"
            lock_dir.mkdir(parents=True)
            lock_file = lock_dir / ".herdr-orchestrator.install.lock"

            # Hold exclusive lock in this process
            fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                env = os.environ.copy()
                env["HOME"] = str(tmp_home)
                proc = subprocess.run(
                    [str(INSTALL_SH), "--version", "v0.1.0"],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(proc.returncode, 1)
                self.assertIn("Another installation or update process is currently running", proc.stderr)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def test_broken_cli_aborts_and_preserves_existing_installation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "skill"
            dest.mkdir()
            (dest / "existing_marker.txt").write_text("keep_me")

            # Create a tarball with broken/non-executable herdr-orchestrator
            broken_tarball = tmp_path / "broken.tar.gz"
            with tarfile.open(broken_tarball, "w:gz") as tar:
                import io
                skill_ti = tarfile.TarInfo(name="SKILL.md")
                skill_ti.size = 5
                tar.addfile(skill_ti, io.BytesIO(b"skill"))

                # Broken script that exits with error on --help
                broken_script = b"#!/bin/sh\nexit 42\n"
                cli_ti = tarfile.TarInfo(name="scripts/herdr-orchestrator")
                cli_ti.size = len(broken_script)
                cli_ti.mode = 0o755
                tar.addfile(cli_ti, io.BytesIO(broken_script))

                ref_ti = tarfile.TarInfo(name="references")
                ref_ti.type = tarfile.DIRTYPE
                tar.addfile(ref_ti)

            bin_dir = tmp_path / "bin"
            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(broken_tarball)

            proc = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest), "--bin-dir", str(bin_dir)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("failed pre-verification execution", proc.stderr)
            self.assertTrue((dest / "existing_marker.txt").is_file(), "Existing install must be preserved")

    def test_signal_handlers_exit_with_non_zero_status(self) -> None:
        """Verify that SIGINT, SIGTERM, and SIGHUP exit with standard non-zero codes (130, 143, 129)."""
        content = INSTALL_SH.read_text(encoding="utf-8")
        main_pos = content.find("main() {")
        preamble = content[:main_pos]

        for sig_name, expected_code in [("INT", 130), ("TERM", 143), ("HUP", 129)]:
            sim_script = f"""
            {preamble}
            kill -{sig_name} $$
            """
            proc = subprocess.run(
                ["bash", "-c", sim_script],
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, expected_code, f"Signal {sig_name} should result in exit code {expected_code}")

    def test_signal_during_swapped_state_executes_rollback(self) -> None:
        """Verify that receiving a signal when CLEANUP_SWAPPED=1 rolls back backup and exits non-zero."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "skill"
            backup = tmp_path / "backup"
            backup.mkdir()
            (backup / "backup_marker.txt").write_text("restored_backup")

            content = INSTALL_SH.read_text(encoding="utf-8")
            main_pos = content.find("main() {")
            preamble = content[:main_pos]

            # Simulate swapped state and signal interruption
            sim_script = f"""
            {preamble}
            CLEANUP_DEST="{dest}"
            CLEANUP_BACKUP_DIR="{backup}"
            CLEANUP_SWAPPED=1
            true # last command succeeded ($? == 0)
            kill -INT $$ # send SIGINT to trigger trap
            """
            proc = subprocess.run(
                ["bash", "-c", sim_script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 130)
            self.assertIn("Rolling back to previous installation", proc.stderr)
            self.assertTrue((dest / "backup_marker.txt").is_file(), "Backup must be rolled back into dest")
            self.assertFalse(backup.is_dir(), "Backup dir should be moved back to dest")

    def test_symlink_failure_triggers_automatic_rollback(self) -> None:
        """Verify that if symlink creation fails in Step 12, the original installation is rolled back."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "skill"
            dest.mkdir()
            (dest / "original_version.txt").write_text("v1_original")

            valid_tarball = tmp_path / "valid.tar.gz"
            create_sample_tarball(valid_tarball)

            # Make bin_dir an unwritable file so ln / mkdir fails
            bad_bin_dir = tmp_path / "bin_as_file"
            bad_bin_dir.write_text("not_a_directory")

            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(valid_tarball)

            proc = subprocess.run(
                [
                    str(INSTALL_SH),
                    "--dest", str(dest),
                    "--bin-dir", str(bad_bin_dir),
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Rolling back to previous installation", proc.stderr)
            self.assertTrue((dest / "original_version.txt").is_file(), "Original installation must be preserved")


if __name__ == "__main__":
    unittest.main()
