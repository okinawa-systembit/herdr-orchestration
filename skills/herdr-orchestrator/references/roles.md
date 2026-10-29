# Logical roles（初期4 role）

正本: [§4](../../../docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-4)（グローバル Skill 単体配置時は [design-links.md](design-links.md)）

| Role           | 用途                 |
| -------------- | -------------------- |
| `design`       | 設計・仕様           |
| `reviewer`     | コード・設計レビュー |
| `implementer`  | 実装と実装伴うテスト |
| `orchestrator` | 委譲・進行確認       |

## Role と `pane_id` の紐づけ（Role Binding）

Herdr 本体の自動セッション復元（`resume`）に干渉しないよう、`herdr agent rename` によるエージェント名の変更は行わず、`herdr-orchestrator role` コマンドでリポジトリごとの論理 role と `pane_id`（例: `w15:p1`, `w15:p2`）を紐づけて管理する。

```bash
# 現在のペイン（HERDR_PANE_ID）に role を紐づける
herdr-orchestrator role set implementer

# 対象の pane_id を明示して紐づける
herdr-orchestrator role set reviewer --pane w15:p2

# 現在のリポジトリの紐づけ一覧を確認する
herdr-orchestrator role show
```

Role Binding が未設定の環境では、後方互換として Pattern A 命名（`r-<hash16>-<role>`）による `agent.name` 解決へフォールバックする。

※ `HERDR_ENV=1` 下で `herdr-orchestrator role set` を実行した際、指定した `pane_id` の実在または現在の worktree との一致が確認できない場合は、紐づけの保存は行われつつ出力 JSON に `"warning"` フィールドが付与される。`"warning"` が出力された場合は `herdr-orchestrator role show` や `herdr agent list` で対象ペインを確認すること。
