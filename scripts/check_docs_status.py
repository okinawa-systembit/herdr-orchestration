#!/usr/bin/env python3
"""設計書ステータスおよび単一正本ルールのオフライン検証スクリプト。

検証項目:
1. docs/herdr-orchestrator-*.md のヘッダーにある `- **ステータス**:` が、
   規定の 5 語彙（設計中 / 設計承認 / 実装中 / 実装完了（PR #NN...） / 廃止）のいずれかであること。
2. AGENTS.md の設計書構成一覧において、個別設計書リンクにステータス語
   （設計中、設計承認、実装中、実装完了、廃止、実装済み、対応案、設計案 等）が混入せず、
   単一正本原則が維持されていること。
3. docs/herdr-orchestrator-design.md の「個別機能設計書」一覧においても、
   各項目にステータス語が混入していないこと。

※ 本スクリプトはオフライン検証であり、GitHub Issue の実際の open/closed 状態や
   git tag の存在確認は行いません（これらはワイドレビュー A-1 で手動/定期確認します）。
"""

import glob
import os
import re
import sys

ALLOWED_STATUS_PATTERNS = [
    r"^設計中$",
    r"^設計承認$",
    r"^実装中$",
    r"^実装完了（.*PR #\d+.*）$",
    r"^廃止$",
]

FORBIDDEN_STATUS_WORDS_IN_LIST = [
    "設計中",
    "設計承認",
    "実装中",
    "実装完了",
    "廃止",
    "実装済み",
    "対応案",
    "設計案",
]


def check_doc_headers(repo_root: str) -> list[str]:
    errors = []
    pattern = os.path.join(repo_root, "docs", "herdr-orchestrator-*.md")
    files = sorted(glob.glob(pattern))

    # 除外対象（全体目次・INDEX・検証ログ）
    excludes = {
        "docs/herdr-orchestrator-design.md",
        "docs/herdr-orchestrator-implementation-index.md",
        "docs/herdr-orchestrator-verification-log.md",
    }

    status_re = re.compile(r"^[ \t]*- \*\*ステータス\*\*:\s*(.+)$", re.MULTILINE)

    for fpath in files:
        rel_path = os.path.relpath(fpath, repo_root)
        if rel_path in excludes:
            continue

        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        m = status_re.search(content)
        if not m:
            errors.append(f"{rel_path}: '- **ステータス**:' 行が見つかりません。")
            continue

        status_val = m.group(1).strip()
        matched = any(re.match(pat, status_val) for pat in ALLOWED_STATUS_PATTERNS)
        if not matched:
            errors.append(
                f"{rel_path}: 無効なステータス表記です: '{status_val}'\n"
                f"  許可されている語彙: 設計中 / 設計承認 / 実装中 / 実装完了（PR #NN / vX.Y.Z にてマージ済み） / 廃止"
            )

    return errors


def check_agents_md(repo_root: str) -> list[str]:
    errors = []
    fpath = os.path.join(repo_root, "AGENTS.md")
    if not os.path.exists(fpath):
        return ["AGENTS.md が見つかりません。"]

    with open(fpath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    in_structure_section = False
    for idx, line in enumerate(lines, start=1):
        if "### 設計書の構成" in line:
            in_structure_section = True
            continue
        elif in_structure_section and line.startswith("### "):
            in_structure_section = False
            continue

        if in_structure_section and "herdr-orchestrator-" in line:
            for word in FORBIDDEN_STATUS_WORDS_IN_LIST:
                if word in line:
                    errors.append(
                        f"AGENTS.md:{idx}: 個別設計書一覧にステータス表現 '{word}' が含まれています（単一正本原則違反）。\n"
                        f"  行内容: {line.strip()}\n"
                        f"  ステータスは各設計書のヘッダーのみに記載してください。"
                    )

    return errors


def check_design_md(repo_root: str) -> list[str]:
    errors = []
    fpath = os.path.join(repo_root, "docs", "herdr-orchestrator-design.md")
    if not os.path.exists(fpath):
        return ["docs/herdr-orchestrator-design.md が見つかりません。"]

    with open(fpath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    in_custom_section = False
    for idx, line in enumerate(lines, start=1):
        if "## 個別機能設計書" in line:
            in_custom_section = True
            continue
        elif in_custom_section and line.startswith("## "):
            in_custom_section = False
            continue

        if in_custom_section and "herdr-orchestrator-" in line:
            for word in FORBIDDEN_STATUS_WORDS_IN_LIST:
                if word in line:
                    errors.append(
                        f"docs/herdr-orchestrator-design.md:{idx}: 個別設計書一覧にステータス表現 '{word}' が含まれています（単一正本原則違反）。\n"
                        f"  行内容: {line.strip()}\n"
                        f"  ステータスは各設計書のヘッダーのみに記載してください。"
                    )

    return errors


def main() -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    all_errors = []

    all_errors.extend(check_doc_headers(repo_root))
    all_errors.extend(check_agents_md(repo_root))
    all_errors.extend(check_design_md(repo_root))

    if all_errors:
        print("[ERROR] 設計書ステータス検証に失敗しました:", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("OK: 設計書ステータスおよび単一正本検証に合格しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
