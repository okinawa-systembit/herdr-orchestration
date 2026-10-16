---
name: review-finding
description: >-
  Validates, standardizes, and creates GitHub Finding Issues from code review
  findings. Ensures severity/blocking consistency, refutation checks, and completion/regression criteria.
---

# review-finding — GitHub レビュー指摘（Finding Issue）起票・品質検証 Skill

本 Skill は、`herdr-orchestration` リポジトリにおいて、コードレビュー（通常 PR レビュー、Scope レビュー等）で検出された未解決指摘（Finding）を **GitHub Finding Issue として起票する前に品質検証を行い、整合性を保って起票・相互リンクを行うリポジトリ専用 Skill** である。

正本（Source of Truth）は **GitHub Issues** であり、リポジトリ内に独自の台帳や二重管理用ファイルは追加しない。

---

## 1. 目的と責務境界

レビュー担当エージェント（`repo-review` 等）と、Issue 起票・品質検証を担当する本 Skill の責務を明確に分離する。

| 機能                              | 担当 Skill       | 責務と制約                                                          |
| :-------------------------------- | :--------------- | :------------------------------------------------------------------ |
| **不具合探索・反証確認**          | `repo-review`    | コードの批判的精査、8つの反証確認。                                 |
| **重大度・Blocking 判定**         | `repo-review`    | 技術的影響度（P0〜P3）とマージ可否（Blocking/Non-blocking）の決定。 |
| **Finding 本文の構造化**          | `review-finding` | テンプレートに沿ったメタデータ、詳細、是正完了条件の成型。          |
| **起票前品質バリデーション**      | `review-finding` | 必須項目、具体性、重大度・Blocking の整合性検証。                   |
| **既存 Issue との重複確認**       | `review-finding` | GitHub 上の既存 Finding との突合・類似度チェック。                  |
| **GitHub Issue 起票・相互リンク** | `review-finding` | `gh issue create` による起票、Scope/PR へのリンク記録。             |

> [!CAUTION]
> **判定改変の禁止（エージェント制約）**:  
> `review-finding` は、レビュー担当が下した重大度（P0〜P3）や Blocking 判定を**エージェントの独断で勝手に変更してはならない**。  
> 判定に論理矛盾や根拠不足（例: P2 Blocking なのに理由が未記載、P3 なのに Blocking 指定等）がある場合は、起票を保留し、**レビュー担当エージェントへ修正・根拠提示を差し戻す**こと。

---

## 2. 適用範囲

- **GitHub 管理対象レビュー**:
  - 初回 20 Scope レビューなどの Scope Issue レビュー。
  - 通常 Pull Request のレビューにおいて、マージ前是正または将来追跡が必要な未解決指摘。
- **Herdr 内部レビューでの扱い**:
  - エージェント間の日常的な handoff レビュー（ローカル `.review-inbox/` 完結）では GitHub 起票は**不要**であり、本 Skill による GitHub 投稿は強制しない。

---

## 3. 起票前品質バリデーション仕様

GitHub への書き込み（`gh issue create`）を実行する前に、以下の 4 段階の検査を必ず実施する。1 つでも違反がある場合、Issue 起票を行わずレビュー担当へ差し戻す。

### 段階 1: メタデータ整合性検証

1. **検出元の確認**:
   - 検出元 Scope Issue または PR 番号が明記され、GitHub 上で実在・Open であること。
2. **対象コミット**:
   - 検出対象のコミット SHA（原則 7 文字以上の正規 SHA）が記載されていること。
3. **重大度と Blocking 判定の整合性**:
   - **P0 / P1**: `Blocking: Yes` が必須（マージ前是正必須）。
   - **P2**: 原則 `Blocking: No`。例外的に `Blocking: Yes` とする公算がある場合は、**「Blocking 理由（なぜマージ前に是正が不可欠か）」が客観的・具体的に記述されていること**。
   - **P3**: 原則 `Blocking: No`。`Blocking: Yes` の指定は禁止（エラー差し戻し）。

### 段階 2: 不具合内容の具体性・客観的根拠の検証

1. **対象箇所の実在性**:
   - 対象ファイルパスがリポジトリ内に実在し、該当行番号（`file.py:L123`）またはシンボルが特定されていること。
2. **発生条件の具体性**:
   - どのような入力引数、環境条件、並行実行順序、状態遷移の際に問題が発生するかが具体的に記述されていること（「〜の場合がある」等の曖昧・抽象的表現のみは不可）。
3. **期待動作と実際の動作の対比**:
   - 「本来どう動くべきか（仕様・契約）」と「実際どう動いてしまうか（例外、データ破壊、放置等）」が明確に対比されていること。
4. **反証確認の確認**:
   - [8つの反証確認の問い](references.md#refutation-checks)（既存処理での防止、失敗条件の成立性等）を確認した結果が記載されていること。

### 段階 3: 是正完了・品質保証項目の検証（ばらつき防止）

1. **`## 是正完了条件`**:
   - 単なる修正方針の文章ではなく、修正確認時に客観的に成否をチェックできる **Markdown チェックリスト（`- [ ] ...`）が最低 2 項目以上** 記載されていること。
2. **`## 回帰テスト条件`**:
   - 修正確認時に実行すべきテストコマンド（例: `npm run test:orchestrator`、`python3 -m unittest ...`）または新規追加すべきテストケース要件が明記されていること。
3. **`## 維持すべき既存保証`**:
   - 修正によって損なわれてはならない既存の契約・正常系動作・品質ゲートが明記されていること。

### 段階 4: 既存 Issue 重複検証

1. **既存 Issue 検索**:
   ```bash
   gh issue list --label review-finding --state all --limit 50
   ```
2. **重複判定**:
   - 同一の対象ファイル・同一関数・同一の原因・同一の失敗条件に関する Issue が既に Open（または是正中）でないか確認する。
3. **重複時のアクション**:
   - 既存 Issue に包含される、または同一原因の別側面である場合は、**新規 Issue を起票せず、既存 Issue へのコメント追記（関連 Scope / PR の言及）にとどめる**。

---

## 4. GitHub Issue 起票と相互リンク手順

バリデーションをすべて通過した場合のみ、以下の手順で起票する。

### 1. Finding Issue の起票

```bash
gh issue create \
  --title "[Finding] <ScopeID/PR>-<連番>: <指摘概要>" \
  --body-file /tmp/finding_<id>.md \
  --label "review-finding,severity:<pX>"
```

※ ラベルは `review-finding` および重大度に応じた `severity:p0` 〜 `severity:p3` を付与する。

### 2. 検出元（Scope Issue または PR）への相互リンク記録

起票された Finding Issue の URL / 番号を取得し、検出元の Scope Issue または PR にコメント追記する：

```bash
gh issue comment <source_issue_no> --body "検出された指摘を Finding Issue #<new_issue_no> として起票しました。"
```

### 3. 起票失敗時の安全停止

`gh` CLI がネットワーク障害や認証エラーで失敗した場合、後続のクローズ処理や完了処理を中断し、作業ツリーを保持したまま安全停止して人間に報告する。

---

## 5. 参照資料

- チェックリスト・詳細テンプレート: [references.md](references.md)
- レビュー基準本体: [repo-review/SKILL.md](../repo-review/SKILL.md)
- エージェントガイドライン: [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)
