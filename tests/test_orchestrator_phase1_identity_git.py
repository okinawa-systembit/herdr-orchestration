from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.identity import (
    GitRepositoryNotInitializedError,
    RepositoryIdentityError,
    get_or_create_repository_identity,
    get_repository_identity,
    is_valid_uuid_v4,
)

_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(worktree), *args],
        check=True,
        capture_output=True,
    )


class DeriveRepositoryIdentityGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.worktree = self.root / "wt"
        subprocess.run(
            ["git", *_GIT_IDENTITY, "init", "-b", "main", str(self.worktree)],
            check=True,
            capture_output=True,
        )
        (self.worktree / "README.md").write_text("test\n", encoding="utf-8")
        _git(self.worktree, "add", "README.md")
        _git(self.worktree, "commit", "-m", "init")

    def test_uninitialized_directory_raises_not_initialized(self) -> None:
        empty_dir = self.root / "empty"
        empty_dir.mkdir()
        with self.assertRaises(GitRepositoryNotInitializedError):
            get_or_create_repository_identity(empty_dir)
        with self.assertRaises(GitRepositoryNotInitializedError):
            get_repository_identity(empty_dir)

    def test_get_or_create_generates_uuid_v4(self) -> None:
        uuid1 = get_or_create_repository_identity(self.worktree)
        self.assertTrue(is_valid_uuid_v4(uuid1))

    def test_get_or_create_idempotent_reutilizes_existing(self) -> None:
        uuid1 = get_or_create_repository_identity(self.worktree)
        uuid2 = get_or_create_repository_identity(self.worktree)
        self.assertEqual(uuid1, uuid2)
        uuid3 = get_repository_identity(self.worktree)
        self.assertEqual(uuid1, uuid3)

    def test_get_repository_identity_uninitialized_raises(self) -> None:
        # Before get_or_create, read-only get should fail
        with self.assertRaises(RepositoryIdentityError) as ctx:
            get_repository_identity(self.worktree)
        self.assertIn("not initialized", str(ctx.exception))

    def test_origin_independent(self) -> None:
        # Generate identity without origin
        uuid1 = get_or_create_repository_identity(self.worktree)
        self.assertTrue(is_valid_uuid_v4(uuid1))

        # Add origin and change origin
        _git(self.worktree, "remote", "add", "origin", "https://github.com/org/repo.git")
        uuid2 = get_repository_identity(self.worktree)
        self.assertEqual(uuid1, uuid2)

        _git(self.worktree, "remote", "set-url", "origin", "https://github.com/other/repo.git")
        uuid3 = get_repository_identity(self.worktree)
        self.assertEqual(uuid1, uuid3)

        _git(self.worktree, "remote", "remove", "origin")
        uuid4 = get_repository_identity(self.worktree)
        self.assertEqual(uuid1, uuid4)

    def test_invalid_uuid_format_raises_error(self) -> None:
        # Directly inject invalid format into git config
        _git(self.worktree, "config", "--file", ".git/config", "herdr.repository-id", "invalid-uuid")
        with self.assertRaises(RepositoryIdentityError) as ctx:
            get_repository_identity(self.worktree)
        self.assertIn("invalid repository identity format", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
