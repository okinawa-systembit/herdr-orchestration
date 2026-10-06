---
name: repo-review
description: >-
  herdr-orchestration repo review via sync handoff, .review-inbox/ artifacts,
  and herdr-orchestrator CLI. Use for レビュー依頼, 再レビュー依頼, レビュー対応,
  or reviewing Orchestrator design, implementation, and tests in this repository.
---

# herdr-orchestration — repository レビュー Skill

中央 Skill（グローバル）: **herdr-orchestration** + **herdr** 公式 Skill。  
本 Skill は **このリポジトリ専用**の Review Scope・成果物・手順（設計 §12 / §11.4）。

## 前提

- `HERDR_ENV=1` の pane のみ。
- 事前: `npm run install:orchestrator-dev`（`herdr-orchestrator` on PATH）。
- レビュー基準: [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)（開始前に読む）。
- Registry / handoff: [worker-receive.md](../../../skills/herdr-orchestrator/references/worker-receive.md) / [requester-receive.md](../../../skills/herdr-orchestrator/references/requester-receive.md)（複製しない）。

## コマンド方針（Codex rule 汚染を避ける）

- Orchestrator: **`herdr-orchestrator` のみ**（`bash -lc` で長文を載せない）。
- **`result-submit` の `--summary` は 200 字以内**。詳細は inbox / `result.ref` 側。
- **`--result-ref` はリポジトリ相対 1 パス**（例: `.review-inbox/review-response.md`）。
- Herdr 操作: 公式 **herdr** Skill。`herdr agent prompt` は **1 行・ファイル参照のみ**（[references.md](references.md)）。sync handoff 成功時は不要。

## 成果物（正本）

| ファイル                           | 担当      | 用途           |
| ---------------------------------- | --------- | -------------- |
| `.review-inbox/review-request.md`  | requester | 依頼本文       |
| `.review-inbox/review-response.md` | reviewer  | 指摘・GO/NO-GO |
| `.review-inbox/INDEX.md`           | reviewer  | ラウンド履歴   |

テンプレ: `.review-inbox/*.example.md`。形式: [references.md](references.md)。

---

## Requester（implementer）— 「レビュー依頼を出して / 再レビュー依頼」

1. [エージェントレビューガイドライン](../../../docs/herdr-agent-review-guidelines.md) の観点で **review-request.md** を更新（example からコピー可）。
2. `npm run test:orchestrator`（必要なら `npm run verify:orchestrator`）を実行し、結果を request に記載。
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

## Worker（reviewer）— handoff 受信後

[worker-receive.md](../../../skills/herdr-orchestrator/references/worker-receive.md) **必須**（prompt 単体を信頼しない）。

1. `task.id` → `herdr-orchestrator task-status <task.id>` → 自分の `agent.name` / context 一致。
2. **review-request.md** を読み、ガイドラインに沿ってレビュー（設計書は `docs/herdr-orchestrator/`、実装は `skills/` / `tests/`）。
3. **review-response.md** を更新（**先頭行 verbatim: `レビュー対応をして`**）。[references.md](references.md) の判定・指摘形式。
4. **INDEX.md** 先頭にラウンド追記。
5. 結果登録:

```bash
herdr-orchestrator result-submit <task.id> \
  --result-status succeeded \
  --result-ref .review-inbox/review-response.md \
  --summary 'Review round recorded in inbox.'
```

`--summary` は上記程度に留める（長文禁止）。blocking 指摘時も ref は response ファイル。

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

## 参照

- 汎用 handoff / 復旧: [repo-workflow SKILL.md](../repo-workflow/SKILL.md)
