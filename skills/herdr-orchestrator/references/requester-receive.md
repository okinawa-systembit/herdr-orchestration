# Requester 受信側（completion / §11.4）

正本: [§9.3](../../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-3)、[§11.4 requester](../../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4)

`resume_requester` の prompt も handoff と同型の最小 Envelope のみ。メッセージ本文を正本にしない。

## 手順

1. prompt から `task.id` を取得。
2. `herdr-orchestrator task-status <task.id>` と `task-result <task.id>` で Registry を読む。
3. **受信者・ロールの本人確認（`recipient_verification`）**:
   - 出力 JSON の `recipient_verification.verified == true` かつ `recipient_verification.caller_role_in_task == "requester"` であることを確認する。
   - **`verified == false` または `caller_role_in_task != "requester"` の場合**: 誤配送または別タスクの完了通知であるため、**後続 workflow を一切開始せず、自分のチャット画面（現在のペイン）で人間に「別タスク／別ロール宛ての完了通知（`task.id` / 理由: `recipient_verification.reason`）を受信したため停止した」旨を報告して即座に終了する**。
4. `result.status` が terminal（`succeeded` / `failed` / `unavailable`）であることを確認。
5. 4 まで成功したら repository 固有 workflow（例: review response）または後続作業を開始する。

Orchestrator 側の再開は `resume-task` / `recover-completion` / `result-submit` 内連鎖が担う。requester Agent は completion prompt だけで判断しない。

## 完了通知が届かない場合の確認

- 自分が `async handoff` したタスクの結果を待っていて次に進めない場合に限り、completion prompt を待たずその `task.id` 単体を `herdr-orchestrator task-status <task.id>` / `task-result <task.id>` で 1 回確認してよい。`result.status` が terminal になっていれば上記手順 4〜5 に従って処理を続行する（正本: [§9.4](../../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-4)・[§9.5](../../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-5)）。
- **禁止事項**:
  - Requester 自身のターン内で `recover-completion` を実行しない（実行時点で自分が `working` のため再び `skipped` になる）。回復実行は人間または第三者が Requester の `idle` / `done` 状態を確認してから行う。
  - 毎ターン終了時の `task-list` 全体走査や定期的なポーリングは行わない。
