#!/usr/bin/env python3
"""scripts/check_docs_status.py の単体テスト。"""

import os
import shutil
import tempfile
import unittest

from scripts.check_docs_status import (
    check_agents_md,
    check_design_md,
    check_doc_headers,
)


class TestCheckDocsStatus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_doc_headers_valid_and_invalid(self):
        docs_dir = os.path.join(self.temp_dir, "docs")
        os.makedirs(docs_dir, exist_ok=True)

        # 正常な設計書
        doc1 = os.path.join(docs_dir, "herdr-orchestrator-sample.md")
        with open(doc1, "w", encoding="utf-8") as f:
            f.write("# Sample Doc\n\n- **対象 Issue**: #1\n- **ステータス**: 実装完了（PR #10 / v0.1.0 にてマージ済み）\n")

        errors = check_doc_headers(self.temp_dir)
        self.assertEqual(errors, [])

        # 不正なステータスを持つ設計書
        doc2 = os.path.join(docs_dir, "herdr-orchestrator-invalid.md")
        with open(doc2, "w", encoding="utf-8") as f:
            f.write("# Invalid Doc\n\n- **対象 Issue**: #2\n- **ステータス**: 実装前設計案\n")

        errors = check_doc_headers(self.temp_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("無効なステータス表記", errors[0])

    def test_agents_md_forbidden_status_words(self):
        # 正常な AGENTS.md（ステータス語なし）
        agents_path = os.path.join(self.temp_dir, "AGENTS.md")
        with open(agents_path, "w", encoding="utf-8") as f:
            f.write("### 設計書の構成\n\n- [herdr-orchestrator-sample.md](docs/herdr-orchestrator-sample.md): Sample Doc (Issue #1)\n\n### 次の節\n")

        errors = check_agents_md(self.temp_dir)
        self.assertEqual(errors, [])

        # 違反のある AGENTS.md（「実装済み」混入）
        with open(agents_path, "w", encoding="utf-8") as f:
            f.write("### 設計書の構成\n\n- [herdr-orchestrator-sample.md](docs/herdr-orchestrator-sample.md): Sample Doc（Issue #1 実装済み）\n\n### 次の節\n")

        errors = check_agents_md(self.temp_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("単一正本原則違反", errors[0])

        # 違反のある AGENTS.md（「実装中」「廃止」「設計承認」混入の検出 P3-1）
        for word in ["実装中", "廃止", "設計承認"]:
            with open(agents_path, "w", encoding="utf-8") as f:
                f.write(f"### 設計書の構成\n\n- [herdr-orchestrator-sample.md](docs/herdr-orchestrator-sample.md): Sample Doc（Issue #1 {word}）\n\n### 次の節\n")
            errs = check_agents_md(self.temp_dir)
            self.assertEqual(len(errs), 1, f"Expected error for forbidden word: {word}")
            self.assertIn(word, errs[0])

    def test_design_md_forbidden_status_words(self):
        docs_dir = os.path.join(self.temp_dir, "docs")
        os.makedirs(docs_dir, exist_ok=True)
        design_path = os.path.join(docs_dir, "herdr-orchestrator-design.md")

        # 正常な design.md
        with open(design_path, "w", encoding="utf-8") as f:
            f.write("## 個別機能設計書\n\n- [Sample](herdr-orchestrator-sample.md): Sample Doc\n\n## 基本方針\n")

        errors = check_design_md(self.temp_dir)
        self.assertEqual(errors, [])

        # 違反のある design.md（「対応案」混入）
        with open(design_path, "w", encoding="utf-8") as f:
            f.write("## 個別機能設計書\n\n- [Sample](herdr-orchestrator-sample.md): Sample Doc 対応案\n\n## 基本方針\n")

        errors = check_design_md(self.temp_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("単一正本原則違反", errors[0])


if __name__ == "__main__":
    unittest.main()
