#!/usr/bin/env python3
"""scripts/check_markdown_links.py の単体テストスイート。"""

import os
import shutil
import tempfile
import unittest

from scripts.check_markdown_links import (
    AnchorCache,
    check_file_links,
    check_markdown_links,
    extract_anchors_from_content,
    find_markdown_files,
    github_slugify,
)


class TestGithubSlugify(unittest.TestCase):
    """GitHub Flavored Markdown 互換スラッグ化のテスト。"""

    def test_ascii_heading(self) -> None:
        self.assertEqual(github_slugify("Hello World"), "hello-world")
        self.assertEqual(github_slugify("Heading 1.2: Details"), "heading-12-details")

    def test_japanese_heading(self) -> None:
        self.assertEqual(
            github_slugify("全体の流れ（利用イメージ）"),
            "全体の流れ利用イメージ",
        )
        self.assertEqual(
            github_slugify("開発者・Agent 向け別紙（CLI 操作・詳細仕様）"),
            "開発者agent-向け別紙cli-操作詳細仕様",
        )
        self.assertEqual(
            github_slugify("1. 役割（ロール）を設定する"),
            "1-役割ロールを設定する",
        )

    def test_inline_code_in_heading(self) -> None:
        self.assertEqual(
            github_slugify("パターン A: `r-<hash16>-<role>`"),
            "パターン-a-r-hash16-role",
        )

    def test_html_tag_in_heading(self) -> None:
        self.assertEqual(
            github_slugify('<a id="sec-1"></a>§1 Foundation'),
            "1-foundation",
        )

    def test_link_in_heading(self) -> None:
        self.assertEqual(
            github_slugify("See [Link Text](http://example.com) Heading"),
            "see-link-text-heading",
        )

    def test_closing_hashes_in_heading(self) -> None:
        self.assertEqual(
            github_slugify("Heading with closing hashes ###"),
            "heading-with-closing-hashes",
        )


class TestExtractAnchors(unittest.TestCase):
    """アンカー抽出のテスト。"""

    def test_html_anchors(self) -> None:
        content = """
<a id="explicit-a"></a>
<a name="explicit-name"></a>
<span id="explicit-span">Text</span>
"""
        anchors = extract_anchors_from_content(content)
        self.assertIn("explicit-a", anchors)
        self.assertIn("explicit-name", anchors)
        self.assertIn("explicit-span", anchors)

    def test_markdown_headings(self) -> None:
        content = """
# First Heading
## Second Heading
### Second Heading
"""
        anchors = extract_anchors_from_content(content)
        self.assertIn("first-heading", anchors)
        self.assertIn("second-heading", anchors)
        self.assertIn("second-heading-1", anchors)

    def test_headings_in_code_block_ignored(self) -> None:
        content = """
# Real Heading
```markdown
# Fake Heading in Code Block
```
~~~markdown
## Fake Heading 2
~~~
"""
        anchors = extract_anchors_from_content(content)
        self.assertIn("real-heading", anchors)
        self.assertNotIn("fake-heading-in-code-block", anchors)
        self.assertNotIn("fake-heading-2", anchors)

    def test_mixed_fence_blocks(self) -> None:
        content = """
~~~markdown
```
Fake inside tilde
```
~~~
## Heading After Tilde Block
"""
        anchors = extract_anchors_from_content(content)
        self.assertIn("heading-after-tilde-block", anchors)


class TestCheckFileLinks(unittest.TestCase):
    """ファイル内リンク検証のテスト。"""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.cache = AnchorCache()

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_valid_links(self) -> None:
        f1 = os.path.join(self.test_dir, "doc1.md")
        f2 = os.path.join(self.test_dir, "doc2.md")
        img = os.path.join(self.test_dir, "image.png")
        with open(img, "w") as fp:
            fp.write("png")

        with open(f1, "w", encoding="utf-8") as fp:
            fp.write(
                """# Doc 1

<a id="my-anchor"></a>
- [Local heading](#doc-1)
- [Local HTML anchor](#my-anchor)
- [Remote heading](doc2.md#doc-2)
- [Remote explicit anchor](doc2.md#remote-anchor)
- [Remote file without anchor](doc2.md)
- [Image file](image.png)
- [External URL](https://example.com/test#anchor)
- [File protocol](file:///path/to/file#L1-L10)
- [Mailto](mailto:user@example.com)
- Inline code mask: `[Not a link](missing.md)`
```markdown
[Code block link](missing_in_code.md)
```
"""
            )

        with open(f2, "w", encoding="utf-8") as fp:
            fp.write(
                """# Doc 2

<a id="remote-anchor"></a>
## Remote Section
"""
            )

        errors, count = check_file_links(f1, self.test_dir, self.cache)
        self.assertEqual(errors, [])
        self.assertEqual(count, 6)

    def test_missing_file_error(self) -> None:
        f1 = os.path.join(self.test_dir, "doc.md")
        with open(f1, "w", encoding="utf-8") as fp:
            fp.write("[Missing](non_existent_file.md)\n")

        errors, _ = check_file_links(f1, self.test_dir, self.cache)
        self.assertEqual(len(errors), 1)
        self.assertIn("リンク先ファイルが存在しません", errors[0])

    def test_missing_local_anchor_error(self) -> None:
        f1 = os.path.join(self.test_dir, "doc.md")
        with open(f1, "w", encoding="utf-8") as fp:
            fp.write("# Title\n\n[Bad anchor](#no-such-anchor)\n")

        errors, _ = check_file_links(f1, self.test_dir, self.cache)
        self.assertEqual(len(errors), 1)
        self.assertIn("アンカーが存在しません", errors[0])

    def test_missing_remote_anchor_error(self) -> None:
        f1 = os.path.join(self.test_dir, "doc1.md")
        f2 = os.path.join(self.test_dir, "doc2.md")
        with open(f1, "w", encoding="utf-8") as fp:
            fp.write("[Bad remote anchor](doc2.md#no-such-section)\n")
        with open(f2, "w", encoding="utf-8") as fp:
            fp.write("# Doc 2\n")

        errors, _ = check_file_links(f1, self.test_dir, self.cache)
        self.assertEqual(len(errors), 1)
        self.assertIn("アンカーが存在しません", errors[0])


class TestDirectoryScanning(unittest.TestCase):
    """ディレクトリ走査および除外パターンのテスト。"""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_find_markdown_files_excludes(self) -> None:
        os.makedirs(os.path.join(self.test_dir, ".venv"))
        os.makedirs(os.path.join(self.test_dir, "node_modules"))
        os.makedirs(os.path.join(self.test_dir, ".input"))
        os.makedirs(os.path.join(self.test_dir, "docs", "reviews", "internal"))
        os.makedirs(os.path.join(self.test_dir, "docs", "valid"))

        # 走査されるべきファイル
        valid1 = os.path.join(self.test_dir, "README.md")
        valid2 = os.path.join(self.test_dir, "docs", "valid", "doc.md")
        # 除外されるべきファイル
        ex1 = os.path.join(self.test_dir, ".venv", "lib.md")
        ex2 = os.path.join(self.test_dir, "node_modules", "pkg.md")
        ex3 = os.path.join(self.test_dir, ".input", "inbox.md")
        ex4 = os.path.join(self.test_dir, "docs", "reviews", "internal", "RV001.md")

        for fpath in (valid1, valid2, ex1, ex2, ex3, ex4):
            with open(fpath, "w", encoding="utf-8") as fp:
                fp.write("# Test\n")

        files = find_markdown_files(self.test_dir)
        self.assertEqual(files, [valid1, valid2])

    def test_check_markdown_links_integration(self) -> None:
        f1 = os.path.join(self.test_dir, "README.md")
        with open(f1, "w", encoding="utf-8") as fp:
            fp.write("# Title\n\n[Valid link](#title)\n")

        rc = check_markdown_links(self.test_dir, quiet=True)
        self.assertEqual(rc, 0)

        with open(f1, "a", encoding="utf-8") as fp:
            fp.write("[Broken link](#not-exist)\n")

        rc_bad = check_markdown_links(self.test_dir, quiet=True)
        self.assertEqual(rc_bad, 1)


if __name__ == "__main__":
    unittest.main()
