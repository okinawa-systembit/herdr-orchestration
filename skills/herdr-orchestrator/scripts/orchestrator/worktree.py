from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    pass


def resolve_worktree_root(foreground_cwd: Path) -> Path:
    """§11.0 step 2: git rev-parse --show-toplevel, absolute."""
    cwd = foreground_cwd.resolve()
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise WorktreeError("not a git worktree or git failed")
    root = Path(proc.stdout.strip()).resolve()
    return root
