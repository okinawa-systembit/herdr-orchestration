# Requester 受信側（completion / §11.4）

正本: [§9.3](../../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-3)、[§11.4 requester](../../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4)

`resume_requester` の prompt も handoff と同型の最小 Envelope のみ。メッセージ本文を正本にしない。

## 手順

1. prompt から `task.id` を取得。
2. `herdr-orchestrator task-status <task.id>` と `task-result <task.id>` で Registry を読む。
3. `result.status` が terminal（`succeeded` / `failed` / `unavailable`）であることを確認。
4. `requester.agent_name` / context が自分の live 環境と一致することを確認（不一致なら後続 workflow を開始しない）。
5. 4 まで成功したら repository 固有 workflow（例: review response）を開始する。

Orchestrator 側の再開は `resume-task` / `recover-completion` / `result-submit` 内連鎖が担う。requester Agent は completion prompt だけで判断しない。
