---
name: repo-review
description: >-
  herdr-orchestration repo review via sync handoff, .review-inbox/ artifacts,
  and herdr-orchestrator CLI. Use for レビュー依頼, 再レビュー依頼, レビュー対応,
  or reviewing Orchestrator design, implementation, and tests in this repository.
---

# herdr-orchestration — repository レビュー Skill

中央 Skill（グローバル）: **herdr-orchestration** + **herdr** 公式 Skill。  
本 Skill は **このリポジトリ専用**の Review Scope・調査検証手順・成果物・連携フロー（設計 §12 / §11.4）。

## 前提・資料読み込み

- `HERDR_ENV=1` の pane のみ。
- 事前: `npm run install:orchestrator-dev`（`herdr-orchestrator` on PATH）。
- レビュー基準: [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)（開始前に必ず精読）。
- 詳細基準・テンプレート: [references.md](references.md)（開始前に必ず精読）。
- Registry / handoff: [worker-receive.md](../../../skills/herdr-orchestrator/references/worker-receive.md) / [requester-receive.md](../../../skills/herdr-orchestrator/references/requester-receive.md)（複製しない）。

## コマンド方針（Codex rule 汚染を避ける）

- Orchestrator: **`herdr-orchestrator` のみ**（`bash -lc` で長文を載せない）。
- **`result-submit` の `--summary` は 200 字以内**。詳細は inbox / `result.ref` 側。
- **`--result-ref` はリポジトリ相対 1 パス**（例: `.review-inbox/review-response.md`）。
- Herdr 操作: 公式 **herdr** Skill。`herdr agent prompt` は **1 行・ファイル参照のみ**（[references.md](references.md)）。sync handoff 成功時は不要。

## 成果物（正本）

| ファイル                           | 担当      | 用途                               |
| ---------------------------------- | --------- | ---------------------------------- |
| `.review-inbox/review-request.md`  | requester | 依頼本文（基準、差分、目的等）     |
| `.review-inbox/review-response.md` | reviewer  | 指摘・判定・検証結果（先頭行固定） |
| `.review-inbox/INDEX.md`           | reviewer  | ラウンド履歴                       |

テンプレ: `.review-inbox/*.example.md`。詳細形式・基準: [references.md](references.md)。

---

## Requester（implementer）— 「レビュー依頼を出して / 再レビュー依頼」

1. [references.md#review-request-body](references.md#review-request-body) および [レビューガイドライン](../../../docs/herdr-agent-review-guidelines.md) に沿って **review-request.md** を作成・更新する。
   - 基準ブランチ/コミット、対象コミット、レビュー種別（新規/再レビュー）、変更目的、重点確認事項、既知の制約を明記。
   - 再レビュー時は前回レビュー結果の参照先（response / INDEX）を必ず明記。
2. `npm run test:orchestrator`（必要なら `npm run verify:orchestrator`）を実行し、pass 数・結果を request に記載。
3. 同一 worktree・requester pane から **sync handoff**（instruction は短く、inbox を指す）:

```bash
herdr-orchestrator handoff \
  --mode sync \
  --worker-role reviewer \
  --task-type review \
  --instruction 'Read .review-inbox/review-request.md and review per repo-review SKILL.'
```

4. sync 完了後:

```bash
herdr-orchestrator task-result <task.id>
```

5. `result.status` / `result.ref` を確認してから人間へ報告。再依頼は 1 から（新 task）。

handoff 失敗時: stderr を人間へ。blind 再送しない（設計 §9）。

---

## Worker（reviewer）— handoff 受信後の調査・検証手順

[worker-receive.md](../../../skills/herdr-orchestrator/references/worker-receive.md) **必須**（prompt 単体を信頼しない）。

### 1. 前提・資料確認

- `task.id` → `herdr-orchestrator task-status <task.id>` で自分の `agent.name` / context 一致を確認。
- `docs/herdr-agent-review-guidelines.md` と `references.md` が読み込み可能であることを確認。

### 2. レビュー対象の確定

- **review-request.md** から基準コミット、対象コミット、変更目的を把握。
- `git diff <base>..<target>` 等で実際の変更範囲を特定する（推測で対象を決めない）。

### 3. 関連コード・設計の追跡

- 変更差分だけを見て「問題なし」と即断しない。
- 変更された関数・クラス・設定について、必要に応じて以下を追跡・確認する：
  - 呼び出し元・呼び出し先の依存関係
  - 関連する状態遷移・エラー処理・復旧ロジック
  - 既存テストおよび関連設計書（`docs/herdr-orchestrator/`）との整合性

### 4. 不具合の探索と検証

- 正常系・異常系・境界条件、並行処理・競合・冪等性、セキュリティ・権限・パス検証、互換性を検証。
- 指摘候補について「原因」「発生条件」「影響」「他処理での防止有無」「修正の妥当性」を確認する。
- 発生条件が不明なものや単なるスタイルの違いは確定不具合とせず「要確認事項」へ分類する。

### 5. 再レビュー時の検証（該当する場合）

- **前回指摘の再検証**: 最新コードで各指摘を `対応済み` / `部分対応` / `未対応` / `前回指摘の誤り` / `検証不能` に分類。
- **回帰不具合の確認**: 修正によって別の問題やテスト・状態破綻が生じていないか確認。
- **新規問題の探索**: 新たな差分および周辺コードについて前回見落とした問題がないか探索。

### 6. 判定・成果物作成

- [references.md#review-principles](references.md#review-principles) に基づき重大度（P0〜P3）と確信度（高/中/低）を判定。
- **review-response.md** を作成・更新（**先頭行 verbatim: `レビュー対応をして`**）。
  - 総評、判定（GO / 要修正 / NO-GO）、Blocking 指摘、Non-blocking 指摘、要確認事項、再レビュー結果、テスト結果、調査範囲と未確認事項を記載。
- **INDEX.md** 先頭に新しいラウンドを追記。

### 7. 結果登録

```bash
herdr-orchestrator result-submit <task.id> \
  --result-status succeeded \
  --result-ref .review-inbox/review-response.md \
  --summary 'Review round recorded in inbox.'
```

`--summary` は 200 字以内の簡潔な記録に留める（詳細は response ファイル側）。

---

## Requester — 「review-response.md を読め / レビュー対応」

[requester-receive.md](../../../skills/herdr-orchestrator/references/requester-receive.md) に従い `task-status` / `task-result` で terminal を確認後:

1. **review-response.md** を正本として指摘対応。
2. 人間チャットへ [references.md#human-report](references.md#human-report) 形式で報告。

---

## レビュー Scope（本 repo）

- Orchestrator 設計: `docs/herdr-orchestrator/`（公式 Herdr vs 独自の区別）
- 実装 / CLI: `skills/herdr-orchestrator/`
- テスト: `tests/test_orchestrator_*.py`、`npm run test:orchestrator`
- 責務: 中央 Skill vs repository Skill（本 Skill / workflow）

詳細チェックリスト: [references.md#review-scope](references.md#review-scope)。
