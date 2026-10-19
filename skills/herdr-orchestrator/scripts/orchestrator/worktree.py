from __future__ import annotations

import os
import subprocess
from pathlib import Path


class WorktreeError(Exception):
    pass


def resolve_worktree_root(foreground_cwd: Path) -> Path:
    """§11.0 step 2: git rev-parse --show-toplevel, absolute."""
    cwd = foreground_cwd.resolve()
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise WorktreeError(f"not a git worktree or git failed: {proc.stderr.strip()}")
    root = Path(proc.stdout.strip()).resolve()
    return root
