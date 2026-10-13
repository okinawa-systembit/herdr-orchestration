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

    def test_prerequisites_check_missing_flock_fails(self) -> None:
        """Verify that missing flock fails pre-environment check."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_bin = Path(tmp) / "restricted_bin"
            tmp_bin.mkdir()
            for cmd in ["bash", "curl", "tar", "python3"]:
                real_cmd = shutil.which(cmd)
                if real_cmd:
                    (tmp_bin / cmd).symlink_to(real_cmd)

            env = os.environ.copy()
            env["PATH"] = str(tmp_bin)
            proc_run = subprocess.run(
                [str(INSTALL_SH), "--version", "v0.1.0"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc_run.returncode, 1)
            self.assertIn("Required command not found: flock", proc_run.stderr)

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

    def test_installer_holds_lock_during_execution_and_rejects_concurrent_installer(self) -> None:
        """Verify that installer A holds the flock until completion and rejects concurrent installer B."""
        import time

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            sync_file = tmp_path / "sync_wait"
            running_file = tmp_path / "running_a"

            # Create a custom tarball where scripts/herdr-orchestrator waits for sync_file to be removed
            custom_tarball = tmp_path / "waiting.tar.gz"
            waiting_script = (
                f"#!/bin/bash\n"
                f"touch '{running_file}'\n"
                f"while [ -f '{sync_file}' ]; do sleep 0.02; done\n"
                f"exit 0\n"
            ).encode("utf-8")

            sync_file.write_text("wait")

            with tarfile.open(custom_tarball, "w:gz") as tar:
                import io

                ti = tarfile.TarInfo(name="SKILL.md")
                ti.size = 5
                tar.addfile(ti, io.BytesIO(b"skill"))

                ti_cli = tarfile.TarInfo(name="scripts/herdr-orchestrator")
                ti_cli.size = len(waiting_script)
                ti_cli.mode = 0o755
                tar.addfile(ti_cli, io.BytesIO(waiting_script))

                ti_ref = tarfile.TarInfo(name="references")
                ti_ref.type = tarfile.DIRTYPE
                tar.addfile(ti_ref)

            dest_a = tmp_path / "skill_a"
            dest_b = tmp_path / "skill_b"
            bin_dir = tmp_path / "bin"
            env = os.environ.copy()
            env["HOME"] = str(home_dir)
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(custom_tarball)

            # Start installer A in background
            proc_a = subprocess.Popen(
                [str(INSTALL_SH), "--dest", str(dest_a), "--bin-dir", str(bin_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            try:
                # Wait until installer A reaches CLI verification (lock held!)
                waited = 0.0
                while not running_file.is_file() and waited < 5.0:
                    time.sleep(0.05)
                    waited += 0.05
                self.assertTrue(running_file.is_file(), "Installer A should be running with lock held")

                # Launch installer B while installer A is still running
                proc_b = subprocess.run(
                    [str(INSTALL_SH), "--dest", str(dest_b), "--bin-dir", str(bin_dir)],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(proc_b.returncode, 1)
                self.assertIn("Another installation or update process is currently running", proc_b.stderr)

            finally:
                # Signal installer A to proceed and finish
                if sync_file.is_file():
                    sync_file.unlink()
                stdout_a, stderr_a = proc_a.communicate(timeout=10)
                self.assertEqual(proc_a.returncode, 0, stderr_a)

            # After installer A finishes, installer C should be able to acquire lock and run
            proc_c = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest_b), "--bin-dir", str(bin_dir)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc_c.returncode, 0, proc_c.stderr)

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

    def test_rollback_failure_preserves_backup_and_notifies_user(self) -> None:
        """Verify that if rollback fails, error is clearly reported, success is NOT printed, and backup is preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest_parent = tmp_path / "protected_parent"
            dest_parent.mkdir()
            dest = dest_parent / "skill"
            dest.mkdir()
            (dest / "orig.txt").write_text("orig")

            backup = tmp_path / "backup_dir"
            backup.mkdir()
            (backup / "backup_marker.txt").write_text("must_be_preserved")

            content = INSTALL_SH.read_text(encoding="utf-8")
            main_pos = content.find("main() {")
            preamble = content[:main_pos]

            sim_script = f"""
            {preamble}
            CLEANUP_DEST="{dest}"
            CLEANUP_BACKUP_DIR="{backup}"
            CLEANUP_SWAPPED=1

            # Make dest_parent read-only so rm or mv fails
            chmod 555 "{dest_parent}"

            # Trigger failure exit
            exit 1
            """
            try:
                proc = subprocess.run(
                    ["bash", "-c", sim_script],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                dest_parent.chmod(0o755)

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Error: Rollback to previous installation failed!", proc.stderr)
            self.assertIn("Your previous installation backup is preserved at:", proc.stderr)
            self.assertIn("To restore manually, ensure", proc.stderr)
            self.assertIn("Check destination: ls -ld", proc.stderr)
            self.assertIn("Restore previous installation: mv", proc.stderr)
            self.assertNotIn("Rolled back successfully", proc.stderr)
            self.assertTrue((backup / "backup_marker.txt").is_file(), "Backup must NOT be deleted when rollback fails")

    def test_staging_move_failure_after_backup_rolls_back_original_installation(self) -> None:
        """Verify that if new staging move fails after backup creation in Step 11, the original installation is rolled back."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "skill"
            backup = tmp_path / "backup_dir"
            backup.mkdir()
            (backup / "original_data.txt").write_text("keep_this_original_version")

            content = INSTALL_SH.read_text(encoding="utf-8")
            main_pos = content.find("main() {")
            preamble = content[:main_pos]

            # In Step 11, mv "$dest" "$backup_dir" sets CLEANUP_BACKUP_CREATED=1.
            # Then mv "$staging_dir" "$dest" fails.
            sim_script = f"""
            {preamble}
            CLEANUP_DEST="{dest}"
            CLEANUP_BACKUP_DIR="{backup}"
            CLEANUP_BACKUP_CREATED=1
            CLEANUP_NEW_PLACED=0

            # Simulate failure right before/during staging move (dest is currently empty)
            echo "Simulated error during staging move" >&2
            exit 1
            """
            proc = subprocess.run(
                ["bash", "-c", sim_script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Rolling back to previous installation", proc.stderr)
            self.assertIn("Rolled back successfully to previous installation", proc.stderr)
            self.assertTrue((dest / "original_data.txt").is_file(), "Original installation must be restored to dest")
            self.assertFalse(backup.is_dir(), "Backup dir should be moved back to dest")

    def test_rollback_succeeds_even_if_old_install_lacks_skill_md(self) -> None:
        """Verify that rollback succeeds and warns without error if restored previous installation lacked SKILL.md."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "skill"
            backup = tmp_path / "backup_dir"
            backup.mkdir()
            # Notice: NO SKILL.md here
            (backup / "custom_config.json").write_text("custom")

            content = INSTALL_SH.read_text(encoding="utf-8")
            main_pos = content.find("main() {")
            preamble = content[:main_pos]

            sim_script = f"""
            {preamble}
            CLEANUP_DEST="{dest}"
            CLEANUP_BACKUP_DIR="{backup}"
            CLEANUP_BACKUP_CREATED=1

            exit 1
            """
            proc = subprocess.run(
                ["bash", "-c", sim_script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Rolled back successfully to previous installation.", proc.stderr)
            self.assertIn("Notice: Restored previous installation is missing SKILL.md.", proc.stderr)
            self.assertNotIn("Error: Rollback to previous installation failed!", proc.stderr)
            self.assertTrue((dest / "custom_config.json").is_file())

    def test_initial_install_failure_cleans_up_incomplete_destination(self) -> None:
        """Verify that if initial installation fails in Step 12, the incomplete dest is cleaned up."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "fresh_skill"
            self.assertFalse(dest.exists())

            valid_tarball = tmp_path / "valid.tar.gz"
            create_sample_tarball(valid_tarball)

            # Bad bin-dir causes failure in Step 12
            bad_bin_dir = tmp_path / "bad_bin"
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
            self.assertIn("Cleaning up incomplete installation", proc.stderr)
            self.assertFalse(dest.exists(), "Incomplete dest must be cleaned up on initial install failure")

    def test_symlink_target_existing_regular_file_rejected_without_force(self) -> None:
        """Verify that if link_path is a regular file, installation is rejected unless --force is given."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "skill"
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            existing_bin = bin_dir / "herdr-orchestrator"
            existing_bin.write_text("#!/bin/sh\necho custom_tool\n")

            valid_tarball = tmp_path / "valid.tar.gz"
            create_sample_tarball(valid_tarball)

            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(valid_tarball)

            # 1. Without --force -> fails
            proc = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest), "--bin-dir", str(bin_dir)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("already exists and is not a symlink", proc.stderr)
            self.assertFalse(existing_bin.is_symlink(), "Existing file must not be modified without --force")

            # 2. With --force -> succeeds and replaces regular file with symlink
            force_proc = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest), "--bin-dir", str(bin_dir), "--force"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(force_proc.returncode, 0, force_proc.stderr)
            self.assertTrue(existing_bin.is_symlink(), "Link must be created with --force")
            self.assertTrue((dest / "SKILL.md").is_file())

    def test_existing_symlink_is_updated_without_force(self) -> None:
        """Verify that existing symlink is smoothly updated without requiring --force."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest = tmp_path / "skill"
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            link_path = bin_dir / "herdr-orchestrator"
            dummy_target = tmp_path / "dummy"
            dummy_target.touch()
            link_path.symlink_to(dummy_target)

            valid_tarball = tmp_path / "valid.tar.gz"
            create_sample_tarball(valid_tarball)

            env = os.environ.copy()
            env["HERDR_INSTALL_LOCAL_TARBALL"] = str(valid_tarball)

            proc = subprocess.run(
                [str(INSTALL_SH), "--dest", str(dest), "--bin-dir", str(bin_dir)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(link_path.is_symlink())
            self.assertEqual(link_path.resolve(), (dest / "scripts" / "herdr-orchestrator").resolve())


if __name__ == "__main__":
    unittest.main()
