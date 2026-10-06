from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.worktree import WorktreeError, resolve_worktree_root

_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(worktree), *args],
        check=True,
        capture_output=True,
    )


def _init_repo_with_commit(root: Path) -> Path:
    repo = root / "primary"
    subprocess.run(
        ["git", *_GIT_IDENTITY, "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


class WorktreeResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.primary = _init_repo_with_commit(self.root)

    def test_subdirectory_resolves_to_same_root(self) -> None:
        nested = self.primary / "sub" / "nested"
        nested.mkdir(parents=True)
        resolved = resolve_worktree_root(nested)
        self.assertEqual(resolved, self.primary.resolve())

    def test_linked_worktree_resolves_to_its_own_root(self) -> None:
        linked = self.root / "linked"
        _git(self.primary, "worktree", "add", "-b", "linked-wt", str(linked), "main")
        from_primary = resolve_worktree_root(self.primary)
        from_linked = resolve_worktree_root(linked)
        self.assertNotEqual(from_primary, from_linked)
        self.assertEqual(from_linked, linked.resolve())

    def test_non_git_directory_raises_worktree_error(self) -> None:
        outside = self.root / "not-a-repo"
        outside.mkdir()
        with self.assertRaises(WorktreeError):
            resolve_worktree_root(outside)


if __name__ == "__main__":
    unittest.main()
