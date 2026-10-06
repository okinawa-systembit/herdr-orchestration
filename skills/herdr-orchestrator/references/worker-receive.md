# Worker 受信側（§11.4）

正本: [§11.4](../../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4)

prompt だけを信頼しない。Registry を正本とする。

## 手順

1. 最小 Task Envelope から `task.id` を取り出す。
2. `herdr-orchestrator task-status <task.id>` を実行し JSON を読む。
3. Registry の `worker.agent_name` が自分の live `agent.name` と一致することを確認（不一致なら作業しない）。
4. `dispatch.status` の扱い:
   - `pending` → 同一 `task.id` で `task-status` を **bounded 再 poll**（最大 10 回、初回 1 秒・上限 30 秒の指数バックオフ）。`sent` / `uncertain` になったら 5 へ。
   - `sent` / `uncertain` → 5 へ（task / result が当該 role の作業開始を許可するか確認）。
   - `failed` / `skipped` → 作業しない（summary / 人間報告）。
   - 4a 上限到達で `pending` のまま → 作業開始しない。`dispatch_pending_timeout` を報告し、`task-status` 監視・reconcile・人間判断を待つ。
5. `context.repository.identity` / `context.worktree.path`（必要なら branch / commit）が自分の環境と一致（不一致なら作業しない）。
6. 5 まで成功したら `task.instruction`（Registry 正本）に従って作業する。

## 結果登録

- 成果物を worktree に保存する。
- `herdr-orchestrator result-submit <task.id> --result-status … --result-ref …`（caller は Registry の worker pane、`HERDR_ENV=1`）。
- `dispatch_not_ready`（retryable）なら **同一 payload** で bounded retry（[§10.2](../../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-10-2)）。

CLI: **`herdr-orchestrator`** on PATH（`npm run install:orchestrator-dev` 後。開発・配布で同一）。
