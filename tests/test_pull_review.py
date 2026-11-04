"""Unit tests for pull_review.py module."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts.lib.pull_review import (
    main as cli_main,
    parse_and_generate_draft,
)


class TestPullReview(unittest.TestCase):
    """pull_review モジュールの単体テスト."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.target_file = self.tmp_path / "test-draft.md"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_generate_initial_draft_pr_reviewer(self) -> None:
        """相手コメントなしの初期ドラフト生成 (PR / reviewer)."""
        parse_and_generate_draft(
            target_file=self.target_file,
            role="reviewer",
            opposite_role="reviewee",
            is_issue=False,
            target_id="101",
            comments_reviews_data={"comments": [], "reviews": []},
            inline_comments_data=[],
        )

        content = self.target_file.read_text(encoding="utf-8")
        self.assertIn("# Review Draft: PR #101", content)
        self.assertIn("- Role: reviewer", content)
        self.assertIn("- Round: 1", content)
        self.assertIn("- PR: #101", content)
        self.assertIn("- Status: Changes requested", content)
        self.assertIn("## Findings", content)

    def test_generate_initial_draft_issue_reviewee(self) -> None:
        """相手コメントなしの初期ドラフト生成 (Issue / reviewee)."""
        parse_and_generate_draft(
            target_file=self.target_file,
            role="reviewee",
            opposite_role="reviewer",
            is_issue=True,
            target_id="202",
            comments_reviews_data={"comments": []},
            inline_comments_data=[],
        )

        content = self.target_file.read_text(encoding="utf-8")
        self.assertIn("# Review Draft: Issue #202", content)
        self.assertIn("- Role: reviewee", content)
        self.assertIn("- Round: 1", content)
        self.assertNotIn("- Status: Changes requested", content)

    def test_extract_latest_opposite_comment_and_inlines(self) -> None:
        """相手のマーカー付きコメントおよびインラインコメントの取り込み."""
        comments_data = {
            "comments": [
                {
                    "body": "一般コメント",
                    "createdAt": "2026-10-30T10:00:00Z",
                    "author": {"login": "human_user"},
                }
            ],
            "reviews": [
                {
                    "body": (
                        "<!-- review-sync role=reviewer round=1 -->\n"
                        "# レビュー結果\n\n"
                        "- Role: reviewer\n"
                        "- Round: 1\n"
                        "- Status: Changes requested\n\n"
                        "## Findings\n\n"
                        "F001: 不具合の指摘\n"
                    ),
                    "submittedAt": "2026-10-30T10:05:00Z",
                    "author": {"login": "reviewer_bot"},
                }
            ],
        }

        inline_comments = [
            [
                {
                    "path": "scripts/foo.sh",
                    "line": 42,
                    "body": "ここを修正してください",
                    "created_at": "2026-10-30T10:06:00Z",
                    "user": {"login": "human_reviewer"},
                }
            ]
        ]

        # reviewee が pull した場合
        parse_and_generate_draft(
            target_file=self.target_file,
            role="reviewee",
            opposite_role="reviewer",
            is_issue=False,
            target_id="101",
            comments_reviews_data=comments_data,
            inline_comments_data=inline_comments,
        )

        content = self.target_file.read_text(encoding="utf-8")
        self.assertIn("- Role: reviewee", content)
        self.assertIn("- Round: 1", content)
        # reviewee のドラフトでは Status: Changes requested が除去されること
        self.assertNotIn("Status: Changes requested", content)
        self.assertIn("F001: 不具合の指摘", content)
        # 人間コメントおよびインラインコメントが含まれること
        self.assertIn("## Human & Inline Comments", content)
        self.assertIn("一般コメント (@human_user)", content)
        self.assertIn("[scripts/foo.sh:42] ここを修正してください", content)

    def test_extract_opposite_review_preserves_body_without_auto_inserting_decision(self) -> None:
        """reviewee で pull 時に相手の本文を改変せず（Decision等の自動挿入を行わず）そのまま保持すること (指針9 / Issue #116)."""
        comments_data = {
            "comments": [],
            "reviews": [
                {
                    "body": (
                        "<!-- review-sync role=reviewer round=1 -->\n"
                        "# レビュー結果\n\n"
                        "- Role: reviewer\n"
                        "- Round: 1\n"
                        "- Status: Changes requested\n\n"
                        "## Findings\n\n"
                        "### P1-1 [Blocking] 重大な不具合\n\n"
                        "- 問題: ロジック破綻。本文中に Decision: という単語があっても誤判定しないこと\n\n"
                        "### P3-1（Non-blocking）軽微な改善\n\n"
                        "- 問題: typo\n\n"
                        "### 実行したテストコマンド\n\n"
                        "- 指摘 ID を持たない一般見出し\n\n"
                        "```markdown\n"
                        "### P2-1 コードブロック内の見出し\n"
                        "コードブロック内\n"
                        "```\n"
                    ),
                    "submittedAt": "2026-10-30T10:05:00Z",
                    "author": {"login": "reviewer_bot"},
                }
            ],
        }

        parse_and_generate_draft(
            target_file=self.target_file,
            role="reviewee",
            opposite_role="reviewer",
            is_issue=False,
            target_id="101",
            comments_reviews_data=comments_data,
            inline_comments_data=[],
        )

        content = self.target_file.read_text(encoding="utf-8")
        self.assertIn("### P1-1 [Blocking] 重大な不具合", content)
        self.assertIn("### P3-1（Non-blocking）軽微な改善", content)
        # 本文中に機械的な Decision 枠が自動挿入されていないこと（指針 9: テンプレート主導）
        self.assertNotIn("- Decision: <accept | reject | defer>", content)
        self.assertNotIn("- レビューイ評価: <同意 | 部分同意 | 不同意>", content)

    def test_marker_in_middle_of_body_treated_as_human_comment(self) -> None:
        """本文途中でマーカーが引用された投稿がドラフトを上書きせず人間コメントに分類されること (RV064 P2-2 回帰テスト)."""
        comments_data = {
            "comments": [
                {
                    "body": (
                        "<!-- review-sync role=reviewer round=1 -->\n"
                        "# 正規レビュアー指摘\n\n"
                        "- Role: reviewer\n"
                        "- Round: 1\n"
                        "- Status: Changes requested\n\n"
                        "## Findings\n\n"
                        "F001: 正規の指摘内容\n"
                    ),
                    "createdAt": "2026-10-30T10:00:00Z",
                    "author": {"login": "reviewer_bot"},
                },
                {
                    "body": (
                        "quoting: <!-- review-sync role=reviewer round=7 -->\n"
                        "本文途中にマーカーが含まれる第三者コメント\n"
                    ),
                    "createdAt": "2026-10-30T10:05:00Z",
                    "author": {"login": "another_user"},
                },
            ],
            "reviews": [],
        }

        parse_and_generate_draft(
            target_file=self.target_file,
            role="reviewee",
            opposite_role="reviewer",
            is_issue=False,
            target_id="101",
            comments_reviews_data=comments_data,
            inline_comments_data=[],
        )

        content = self.target_file.read_text(encoding="utf-8")
        # 正規指摘の内容がドラフト本文として保持されていること (round=7 ではなく round=1 由来)
        self.assertIn("- Role: reviewee", content)
        self.assertIn("- Round: 1", content)
        self.assertIn("F001: 正規の指摘内容", content)
        # 本文途中にマーカーが含まれる投稿は Human & Inline Comments 側に隔離されること
        self.assertIn("## Human & Inline Comments", content)
        self.assertIn("quoting: <!-- review-sync role=reviewer round=7 -->", content)
        self.assertIn("(@another_user)", content)

    def test_consecutive_pull_increments_round_based_on_own_marker(self) -> None:
        """相手の返信がないまま同一ロールが連続 pull した場合の Round 繰り上げ (RV074 P3-2)."""
        comments_data = {
            "comments": [],
            "reviews": [
                {
                    "body": (
                        "<!-- review-sync role=reviewer round=1 -->\n"
                        "# レビュー結果\n\n"
                        "- Role: reviewer\n"
                        "- Round: 1\n"
                        "- Status: Changes requested\n\n"
                    ),
                    "submittedAt": "2026-10-30T10:00:00Z",
                    "author": {"login": "reviewer_bot"},
                }
            ],
        }

        # reviewer が相手 (reviewee) の返信なしで再度 pull した場合
        parse_and_generate_draft(
            target_file=self.target_file,
            role="reviewer",
            opposite_role="reviewee",
            is_issue=False,
            target_id="101",
            comments_reviews_data=comments_data,
            inline_comments_data=[],
        )

        content = self.target_file.read_text(encoding="utf-8")
        self.assertIn("- Role: reviewer", content)
        self.assertIn("- Round: 2", content)

    def test_cli_main_success(self) -> None:
        """CLI エントリポイントの正常動作検証."""
        comments_json = self.tmp_path / "comments.json"
        comments_json.write_text(json.dumps({"comments": [], "reviews": []}), encoding="utf-8")
        inlines_json = self.tmp_path / "inlines.json"
        inlines_json.write_text(json.dumps([]), encoding="utf-8")

        argv = [
            "pull_review.py",
            str(self.target_file),
            "reviewer",
            "reviewee",
            "0",
            "99",
            str(comments_json),
            str(inlines_json),
        ]

        with patch("sys.argv", argv):
            code = cli_main()

        self.assertEqual(code, 0)
        self.assertTrue(self.target_file.is_file())
        self.assertIn("- Role: reviewer", self.target_file.read_text(encoding="utf-8"))

    def test_cli_main_insufficient_args(self) -> None:
        """引数不足時のエラーハンドリング."""
        with patch("sys.argv", ["pull_review.py"]):
            code = cli_main()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
