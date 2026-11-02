"""Integration test runner for review-sync.sh test suite."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestReviewSync(unittest.TestCase):
    """review-sync.sh テストスイートの実行ラッパー."""

    def test_review_sync_test_suite(self) -> None:
        """tests/test_review_sync.sh を実行し全テストがパスすることを検証."""
        test_script = REPO_ROOT / "tests" / "test_review_sync.sh"
        self.assertTrue(test_script.is_file(), f"{test_script} が存在しません")

        proc = subprocess.run(
            ["bash", str(test_script)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode != 0:
            sys.stderr.write(proc.stdout)
            sys.stderr.write(proc.stderr)

        self.assertEqual(
            proc.returncode,
            0,
            f"test_review_sync.sh が失敗しました (code: {proc.returncode}):\n{proc.stderr}\n{proc.stdout}",
        )
        self.assertIn("Results: 28 passed, 0 failed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
