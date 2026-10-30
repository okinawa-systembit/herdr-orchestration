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
