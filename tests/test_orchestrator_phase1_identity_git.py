from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.identity import RepositoryIdentityError, derive_repository_identity

_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(worktree), *args],
        check=True,
        capture_output=True,
    )


def _init_bare_repo_with_commit(tmp_root: Path) -> Path:
    bare = tmp_root / "remote.git"
    subprocess.run(
        ["git", *_GIT_IDENTITY, "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
    )
    clone = tmp_root / "seed"
    subprocess.run(
        ["git", *_GIT_IDENTITY, "clone", str(bare), str(clone)],
        check=True,
        capture_output=True,
    )
    (clone / "README.md").write_text("x\n", encoding="utf-8")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-m", "init")
    _git(clone, "push", "-u", "origin", "main")
    return bare


class DeriveRepositoryIdentityGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.bare = _init_bare_repo_with_commit(self.root)
        self.worktree = self.root / "wt"
        subprocess.run(
            ["git", *_GIT_IDENTITY, "clone", str(self.bare), str(self.worktree)],
            check=True,
            capture_output=True,
        )
        _git(
            self.worktree,
            "remote",
            "set-url",
            "origin",
            "https://github.com/org/fixture-repo",
        )

    def test_upstream_only_no_origin_fails(self) -> None:
        _git(self.worktree, "remote", "rename", "origin", "upstream")
        with self.assertRaises(RepositoryIdentityError) as ctx:
            derive_repository_identity(self.worktree)
        self.assertIn("origin", str(ctx.exception).lower())

    def test_origin_only_succeeds(self) -> None:
        identity = derive_repository_identity(self.worktree)
        self.assertEqual(identity, "github.com/org/fixture-repo")

    def test_origin_and_upstream_same_url_succeeds(self) -> None:
        url = _git_remote_url(self.worktree, "origin")
        _git(self.worktree, "remote", "add", "upstream", url)
        identity = derive_repository_identity(self.worktree)
        self.assertTrue(identity)

    def test_origin_and_upstream_different_fails(self) -> None:
        _git(self.worktree, "remote", "add", "upstream", "https://github.com/other/repo")
        with self.assertRaises(RepositoryIdentityError) as ctx:
            derive_repository_identity(self.worktree)
        self.assertIn("disagree", str(ctx.exception).lower())

    def test_origin_multiple_fetch_urls_same_identity(self) -> None:
        _git(
            self.worktree,
            "remote",
            "set-url",
            "--add",
            "origin",
            "git@github.com:org/fixture-repo.git",
        )
        identity = derive_repository_identity(self.worktree)
        self.assertEqual(identity, "github.com/org/fixture-repo")


def _git_remote_url(worktree: Path, remote: str) -> str:
    proc = subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(worktree), "remote", "get-url", remote],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


if __name__ == "__main__":
    unittest.main()
