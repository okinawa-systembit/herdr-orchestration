#!/usr/bin/env python3
"""Markdown 内の相対ファイルリンクおよび見出しアンカー実在性のオフライン検証スクリプト。

対象:
  リポジトリ内の管理対象 Markdown ファイル（AGENTS.md, README.md, docs/**/*.md,
  skills/**/*.md, .agents/skills/**/*.md ※ docs/reviews/internal/** は除外）。

検証項目:
  1. 相対リンク先ファイルの実在性
  2. #anchor が指定されている場合、対象ファイル内の明示的アンカー（<a id="..."> 等）
     または対応する見出しスラッグの実在性
"""

import argparse
import os
import re
import sys
import unicodedata

# 走査から除外するディレクトリ
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".input",
    ".inbox",
}

# 走査から除外するパスプレフィックス（リポジトリルート相対）
EXCLUDE_PATH_PREFIXES = (
    os.path.normpath("docs/reviews/internal"),
)

# 無視する URL スキーム
IGNORED_SCHEMES = (
    "http://",
    "https://",
    "mailto:",
    "file://",
    "ftp://",
    "irc://",
)

# 見出し行パターン
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
# フェンスブロック開始・終了パターン
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
# 明示的 HTML アンカーパターン
HTML_ANCHOR_RE = re.compile(r"""<(?:a|span|div)[^>]*\s(?:id|name)=["']([^"']+)["']""", re.IGNORECASE)
# Markdown リンクパターン: [text](target)
LINK_RE = re.compile(r"""(?<!\!)\[([^\]]*)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)""")
# 画像リンクパターン
IMAGE_LINK_RE = re.compile(r"""\!\[([^\]]*)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)""")


def github_slugify(text: str) -> str:
    """GitHub Flavored Markdown 互換の見出しスラッグ化。"""
    # 見出し内の Markdown リンク [Link Text](url) はリンクテキストのみ残す
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 見出し末尾の閉じハッシュ（例: `## Heading ##`）を除去
    text = re.sub(r"\s+#+\s*$", "", text)
    # インラインコード `...` を抽出し、その内部の <, > が HTML タグと誤認されないよう一時退避
    def mask_code(m: re.Match) -> str:
        s = m.group(1)
        s = s.replace("<", "\x01").replace(">", "\x02")
        return s

    text = re.sub(r"`([^`]+)`", mask_code, text)
    # 残った HTML タグ（<a ...>, <br>, etc.）を除去
    text = re.sub(r"<[^>]+>", "", text)
    # 一時置換を復元
    text = text.replace("\x01", "<").replace("\x02", ">")
    # 小文字化と前後の空白除去
    text = text.lower().strip()

    result = []
    for ch in text:
        cat = unicodedata.category(ch)
        # 文字 (L*)、数字 (N*)、ハイフン、アンダースコアを保持
        if cat.startswith(("L", "N")) or ch in ("-", "_"):
            result.append(ch)
        elif ch in (" ", "\t"):
            # GitHub は空白文字1文字につきハイフン1個に変換
            result.append("-")
    return "".join(result)


def is_excluded_path(rel_path: str) -> bool:
    """指定パスが除外対象プレフィックスに一致するか判定する。"""
    norm = os.path.normpath(rel_path)
    for prefix in EXCLUDE_PATH_PREFIXES:
        if norm == prefix or norm.startswith(prefix + os.sep):
            return True
    return False


def extract_anchors_from_content(content: str) -> set[str]:
    """Markdown コンテンツから有効なアンカー一覧（HTML アンカー + 見出しスラッグ）を抽出する。"""
    anchors = set()

    # 1. 明示的 HTML アンカー
    for m in HTML_ANCHOR_RE.finditer(content):
        anchors.add(m.group(1))

    # 2. Markdown 見出しスラッグ
    header_counts: dict[str, int] = {}
    active_fence_char: str | None = None
    active_fence_len = 0

    for line in content.splitlines():
        trimmed = line.strip()
        fence_m = FENCE_RE.match(trimmed)
        if fence_m:
            fence_str = fence_m.group(1)
            char = fence_str[0]
            length = len(fence_str)
            if active_fence_char is None:
                active_fence_char = char
                active_fence_len = length
                continue
            elif active_fence_char == char and length >= active_fence_len:
                active_fence_char = None
                active_fence_len = 0
                continue

        if active_fence_char is not None:
            continue

        m = HEADING_RE.match(trimmed)
        if m:
            heading_text = m.group(2).strip()
            slug = github_slugify(heading_text)
            if slug:
                count = header_counts.get(slug, 0)
                if count == 0:
                    anchors.add(slug)
                else:
                    anchors.add(f"{slug}-{count}")
                header_counts[slug] = count + 1

    return anchors


class AnchorCache:
    """ファイルごとのアンカー一覧キャッシュ。"""

    def __init__(self) -> None:
        self._cache: dict[str, set[str] | None] = {}

    def get_anchors(self, file_path: str) -> set[str] | None:
        norm = os.path.normpath(file_path)
        if norm not in self._cache:
            if not os.path.isfile(norm):
                self._cache[norm] = None
            else:
                try:
                    with open(norm, "r", encoding="utf-8") as f:
                        content = f.read()
                    self._cache[norm] = extract_anchors_from_content(content)
                except Exception:
                    self._cache[norm] = None
        return self._cache[norm]


def find_markdown_files(repo_root: str) -> list[str]:
    """検査対象の Markdown ファイル一覧を取得する。"""
    target_files = []
    repo_root = os.path.abspath(repo_root)

    for root, dirs, files in os.walk(repo_root):
        # 除外ディレクトリの枝刈り
        dirs[:] = [
            d for d in dirs
            if d not in EXCLUDE_DIRS
            and not is_excluded_path(os.path.relpath(os.path.join(root, d), repo_root))
        ]

        rel_root = os.path.relpath(root, repo_root)
        if is_excluded_path(rel_root):
            continue

        for f in files:
            if f.endswith(".md"):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, repo_root)
                if not is_excluded_path(rel_path):
                    target_files.append(full_path)

    return sorted(target_files)


def check_file_links(file_path: str, repo_root: str, cache: AnchorCache) -> tuple[list[str], int]:
    """1つの Markdown ファイル内のリンクを検査する。"""
    errors: list[str] = []
    link_count = 0

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return [f"{file_path}: ファイル読み込みエラー: {e}"], 0

    rel_source = os.path.relpath(file_path, repo_root)
    file_dir = os.path.dirname(file_path)
    active_fence_char: str | None = None
    active_fence_len = 0

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        fence_m = FENCE_RE.match(line)
        if fence_m:
            fence_str = fence_m.group(1)
            char = fence_str[0]
            length = len(fence_str)
            if active_fence_char is None:
                active_fence_char = char
                active_fence_len = length
                continue
            elif active_fence_char == char and length >= active_fence_len:
                active_fence_char = None
                active_fence_len = 0
                continue

        if active_fence_char is not None:
            continue

        # インラインコード内のリンク例を除外するため、インラインコードをマスク
        masked_line = re.sub(r"`[^`\n]+`", "", raw_line)

        # 通常リンクおよび画像リンクを走査
        matches = LINK_RE.findall(masked_line) + IMAGE_LINK_RE.findall(masked_line)
        for link_text, raw_target in matches:
            target = raw_target.strip()
            if not target:
                continue

            # スキップすべき外部 URL / プロトコル
            if any(target.startswith(scheme) for scheme in IGNORED_SCHEMES):
                continue
            # プロトコル形式 (xxx://) の除外
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target):
                continue

            # # のみの空アンカーはスキップ
            if target == "#":
                continue

            link_count += 1
            path_part, has_anchor, anchor_part = target.partition("#")

            # 解決先ファイルパスの特定
            if path_part:
                dest_path = os.path.normpath(os.path.join(file_dir, path_part))
            else:
                dest_path = file_path

            # 1. リンク先ファイルの実在性
            if not os.path.exists(dest_path):
                rel_dest = os.path.relpath(dest_path, repo_root) if os.path.isabs(dest_path) else dest_path
                errors.append(
                    f"{rel_source}:{line_no}: リンク先ファイルが存在しません: '{target}' "
                    f"(解決先: {rel_dest})"
                )
                continue

            # 2. アンカーの実在性（対象が Markdown ファイルかつアンカー指定がある場合）
            if has_anchor and anchor_part:
                if os.path.isfile(dest_path) and dest_path.endswith(".md"):
                    anchors = cache.get_anchors(dest_path)
                    if anchors is None:
                        errors.append(
                            f"{rel_source}:{line_no}: アンカー検査対象ファイルの読み込みに失敗しました: '{target}'"
                        )
                    elif anchor_part not in anchors:
                        rel_dest = os.path.relpath(dest_path, repo_root)
                        errors.append(
                            f"{rel_source}:{line_no}: アンカーが存在しません: '{target}' "
                            f"(#{anchor_part} not found in {rel_dest})"
                        )

    return errors, link_count


def check_markdown_links(
    repo_root: str,
    target_files: list[str] | None = None,
    verbose: bool = False,
    quiet: bool = False,
) -> int:
    """Markdown リンクの検証を実行する。"""
    repo_root = os.path.abspath(repo_root)
    cache = AnchorCache()

    if target_files is None:
        files_to_check = find_markdown_files(repo_root)
    else:
        files_to_check = [os.path.abspath(f) for f in target_files]

    total_errors: list[str] = []
    total_links = 0

    for fpath in files_to_check:
        errs, count = check_file_links(fpath, repo_root, cache)
        total_errors.extend(errs)
        total_links += count
        if verbose and not errs:
            print(f"  OK: {os.path.relpath(fpath, repo_root)} ({count} links)")

    if total_errors:
        print(f"エラー: {len(total_errors)} 件のリンク切れが検出されました:", file=sys.stderr)
        for err in total_errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    if not quiet:
        print(
            f"OK: 全 {len(files_to_check)} ファイルの相対リンクおよびアンカー検証に合格しました"
            f"（検証リンク数: {total_links}）。"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Markdown ファイル内の相対リンクおよび見出しアンカーを検証します。")
    parser.add_argument("--root", default=".", help="リポジトリルートディレクトリ (既定値: .)")
    parser.add_argument("--files", nargs="*", default=None, help="検査対象ファイルを明示指定")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細ログ出力")
    parser.add_argument("-q", "--quiet", action="store_true", help="成功時の出力抑制")
    args = parser.parse_args()

    rc = check_markdown_links(
        repo_root=args.root,
        target_files=args.files,
        verbose=args.verbose,
        quiet=args.quiet,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
