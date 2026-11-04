#!/usr/bin/env python3
"""GitHub PR / Issue コメント取り込み・ドラフト生成モジュール.

PR / Issue のコメント、レビュー、およびインラインコメントを解析し、
時系列でソートした上で最新の相手投稿を抽出し、作業用ドラフトを生成します。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


def parse_and_generate_draft(
    target_file: Path,
    role: str,
    opposite_role: str,
    is_issue: bool,
    target_id: str,
    comments_reviews_data: dict[str, Any],
    inline_comments_data: list[Any],
) -> None:
    """コメント・レビュー・インラインコメントから最新の相手投稿を抽出しドラフトを書き出す."""
    # インラインコメントのフラット化 (--paginate --slurp による [[...], [...]] の展開)
    flat_inlines: list[dict[str, Any]] = []
    if isinstance(inline_comments_data, list):
        for item in inline_comments_data:
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict):
                        flat_inlines.append(sub)
            elif isinstance(item, dict):
                flat_inlines.append(item)

    all_items: list[dict[str, Any]] = []

    for c in comments_reviews_data.get("comments", []):
        author = ""
        if isinstance(c.get("author"), dict):
            author = c["author"].get("login", "")
        all_items.append({
            "type": "comment",
            "body": c.get("body", ""),
            "created": c.get("createdAt", ""),
            "author": author,
        })

    for r in comments_reviews_data.get("reviews", []):
        author = ""
        if isinstance(r.get("author"), dict):
            author = r["author"].get("login", "")
        all_items.append({
            "type": "review",
            "body": r.get("body", ""),
            "created": r.get("submittedAt", "") or r.get("createdAt", ""),
            "author": author,
        })

    for inc in flat_inlines:
        path = inc.get("path", "")
        line = inc.get("line") or inc.get("original_line") or "?"
        body = inc.get("body", "")
        author = ""
        if isinstance(inc.get("user"), dict):
            author = inc["user"].get("login", "")
        formatted_body = f"[{path}:{line}] {body}"
        all_items.append({
            "type": "inline",
            "body": formatted_body,
            "raw_body": body,
            "created": inc.get("created_at", ""),
            "author": author,
        })

    # ISO 8601 時刻文字列で昇順ソート (時系列順)
    all_items.sort(key=lambda x: str(x.get("created", "")))

    # 相手ロールの正規マーカー (本文の先頭に配置されていること)
    opposite_marker_pattern = re.compile(
        rf"^\s*<!--\s*review-sync\s+role={re.escape(opposite_role)}\s+round=([0-9]+)\s*-->"
    )
    # 自身ロールの正規マーカー (本文の先頭に配置されていること)
    own_marker_pattern = re.compile(
        rf"^\s*<!--\s*review-sync\s+role={re.escape(role)}\s+round=([0-9]+)\s*-->"
    )
    # 任意の review-sync 正規マーカー (先頭行に配置されているもの)
    any_marker_pattern = re.compile(
        r"^\s*<!--\s*review-sync\s+role=(?:reviewer|reviewee)\s+round=[0-9]+\s*-->"
    )

    latest_opposite_body = ""
    latest_round = 1
    has_opposite_marker = False
    max_own_round = 0
    human_comments: list[str] = []

    for item in all_items:
        body = item["body"]
        m_opp = opposite_marker_pattern.match(body)
        m_own = own_marker_pattern.match(body)
        if m_opp:
            latest_opposite_body = body
            latest_round = int(m_opp.group(1))
            has_opposite_marker = True
        elif m_own:
            r = int(m_own.group(1))
            if r > max_own_round:
                max_own_round = r
        elif not any_marker_pattern.match(body):
            # 先頭に正規マーカーを持たないコメント（人間コメント、インラインコメント、本文中でのマーカー引用等）
            if body.strip():
                author_str = f" (@{item['author']})" if item.get("author") else ""
                human_comments.append(f"{body.strip()}{author_str}")

    if has_opposite_marker:
        base_round = latest_round if role == "reviewee" else latest_round + 1
    else:
        base_round = 1

    if max_own_round > 0:
        next_round = max(base_round, max_own_round + 1)
    else:
        next_round = base_round

    target_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. 状態管理ファイル (state.json) の更新 (Issue #119: 制御値の本文外分離)
    state_file = target_file.parent / "state.json"
    state_data: dict[str, Any] = {}
    if state_file.is_file():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(state_data, dict):
                state_data = {}
        except Exception:
            state_data = {}

    state_data[role] = {
        "round": next_round,
        "target_id": str(target_id),
        "target_type": "issue" if is_issue else "pr",
    }
    state_file.write_text(
        json.dumps(state_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 2. incoming (受信内容) の保存 (Issue #119: incoming / outgoing の分離)
    incoming_file = target_file.parent / "incoming.md"
    with incoming_file.open("w", encoding="utf-8") as f:
        if latest_opposite_body:
            clean_incoming = opposite_marker_pattern.sub("", latest_opposite_body).strip()
            f.write(clean_incoming + "\n")
        else:
            f.write(f"# No incoming messages from {opposite_role}\n")

        if human_comments:
            f.write("\n\n## Human & Inline Comments\n\n")
            for hc in human_comments:
                f.write(f"{hc}\n\n---\n\n")

    # 3. 作業用ドラフト (outgoing) の生成
    with target_file.open("w", encoding="utf-8") as f:
        if latest_opposite_body:
            clean_body = opposite_marker_pattern.sub("", latest_opposite_body).strip()
            lines = clean_body.split("\n")
            out_lines: list[str] = []
            in_header = True
            for line_str in lines:
                if line_str.startswith("## "):
                    in_header = False

                if in_header:
                    if re.match(r"^\s*-?\s*Role:", line_str, re.IGNORECASE):
                        out_lines.append(f"- Role: {role}")
                    elif re.match(r"^\s*-?\s*Round:", line_str, re.IGNORECASE):
                        out_lines.append(f"- Round: {next_round}")
                    elif role == "reviewee" and re.match(
                        r"^\s*-?\s*Status:\s*(Approved|Changes requested)",
                        line_str,
                        re.IGNORECASE,
                    ):
                        continue
                    else:
                        out_lines.append(line_str)
                else:
                    out_lines.append(line_str)
            f.write("\n".join(out_lines))
        else:
            header_title = f"Issue #{target_id}" if is_issue else f"PR #{target_id}"
            f.write(f"# Review Draft: {header_title}\n\n")
            f.write(f"- Role: {role}\n")
            f.write(f"- Round: {next_round}\n")
            if not is_issue:
                f.write(f"- PR: #{target_id}\n")
            if role == "reviewer":
                f.write("\n## Summary\n\n<要約>\n\n## Findings\n\n")
            else:
                f.write("\n## 判定に対する応答\n\n## 指摘への対応 (Decision: accept / reject / defer)\n\n")

        if human_comments:
            f.write("\n\n## Human & Inline Comments\n\n")
            for hc in human_comments:
                f.write(f"{hc}\n\n---\n\n")


def main() -> int:
    """CLI エントリポイント.

    引数:
      argv[1]: target_file
      argv[2]: role (reviewer / reviewee)
      argv[3]: opposite_role (reviewee / reviewer)
      argv[4]: is_issue ('1' or '0')
      argv[5]: target_id (PR番号またはIssue番号)
      argv[6]: comments_file (PR/Issue コメント JSON ファイルパス)
      argv[7]: inlines_file (インラインコメント JSON ファイルパス)
    """
    if len(sys.argv) < 8:
        sys.stderr.write(
            "使用方法: pull_review.py <target_file> <role> <opposite_role> "
            "<is_issue> <target_id> <comments_json_file> <inlines_json_file>\n"
        )
        return 1

    target_file = Path(sys.argv[1])
    role = sys.argv[2]
    opposite_role = sys.argv[3]
    is_issue = sys.argv[4] == "1"
    target_id = sys.argv[5]
    comments_file = Path(sys.argv[6])
    inlines_file = Path(sys.argv[7])

    try:
        raw_comments = comments_file.read_text(encoding="utf-8")
        comments_data = json.loads(raw_comments)
    except Exception as err:
        sys.stderr.write(f"エラー: コメント JSON のパースに失敗しました: {err}\n")
        return 1

    inlines_data: list[Any] = []
    if inlines_file.is_file():
        try:
            raw_inlines = inlines_file.read_text(encoding="utf-8")
            if raw_inlines.strip():
                inlines_data = json.loads(raw_inlines)
        except Exception as err:
            sys.stderr.write(f"エラー: インラインコメント JSON のパースに失敗しました: {err}\n")
            return 1

    try:
        parse_and_generate_draft(
            target_file=target_file,
            role=role,
            opposite_role=opposite_role,
            is_issue=is_issue,
            target_id=target_id,
            comments_reviews_data=comments_data,
            inline_comments_data=inlines_data,
        )
    except Exception as err:
        sys.stderr.write(f"エラー: ドラフト生成に失敗しました: {err}\n")
        return 1

    sys.stdout.write(f"OK: {target_file} を作成・更新しました。\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
