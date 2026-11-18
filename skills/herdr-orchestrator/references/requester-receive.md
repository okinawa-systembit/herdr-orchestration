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

- 自分が `async handoff` したタスクの結果を待っていて次に進めない場合に限り（下記の 1:N 回収の限定例外を除き）、completion prompt を待たずその `task.id` 単体を `herdr-orchestrator task-status <task.id>` / `task-result <task.id>` で 1 回確認してよい。`result.status` が terminal になっていれば上記手順 4〜5 に従って処理を続行する（正本: [§9.4](../../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-4)・[§9.5](../../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-5)）。
- **禁止事項**:
  - Requester 自身のターン内で `recover-completion` を実行しない（実行時点で自分が `working` のため再び `skipped` になる）。回復実行は人間または第三者が Requester の `idle` / `done` 状態を確認してから行う。
  - 下記の 1:N 回収の限定例外を除き、毎ターン終了時の `task-list` 全体走査や定期的なポーリングは行わない。

### 1:N 非同期回収手順（限定例外）

- **対象範囲**: 自分が同一ターン内で複数の依頼先へ `--mode async` で配送し、相談・ローカルレビューのような短い作業について全件の結果がそろうまで次の処理へ進めない場合に限る（設計・実装のように 30 分を超えうる作業は従来どおり async で配って待たず、取りこぼしは人間による `doctor` / `recover-completion` に任せる）。
- **確認手段**: `handoff` 出力から `task_id` を全件控え、その ID 群に限定して `herdr-orchestrator task-status <task.id>` を 1 件ずつ、間隔を空けて確認する。`task-list` の全体走査や、上限のない待機は行わない。
- **待機上限**: `sync` 既定の **1800 秒**に合わせる。数え始めは「最後の `handoff` を出してから全件そろうまでの合計」とし、task ごとの 1800 秒ではない。上限到達時は、未完了の `task.id`・依頼先・`task.status` を人間へ報告してターンを終える。
- **終端判定表**（上から順に評価）:

| 観測された状態（`task-status`）                                                              | 扱い・対応                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `result.status ∈ {succeeded, failed, unavailable}`                                           | **終端**。`completion.status` が `skipped` でも `sent` でも同じ扱いで、`herdr-orchestrator task-result <task.id>` で回収する。                                                                                       |
| `task.status = cancelled`                                                                    | **終端（結果なし）**。まとめの報告に「キャンセル済み」と記載する。                                                                                                                                                   |
| `task.status = unknown`、または `dispatch.status = uncertain` かつ `result.status = pending` | **即時報告**。自分で再送や `reset-completion` を行わず、即時に人間へ報告する（`task-list` の `overview.state = uncertain` 相当。`completion.status = uncertain` は含めず、`result.status` 終端時は回収を優先する）。 |
| `task.status = in_progress` かつ `result.status = pending`                                   | **待機継続**（上限 1800 秒まで間隔を空けて `task-status` で確認を続ける）。                                                                                                                                          |

- **ペイン消失の補助確認（1 回限り）**: 待機上限に達したとき、または上記の即時報告を行うときに、`herdr-orchestrator doctor` の `stale_in_progress`（strong 信号: `worker_pane_missing` / `worker_agent_not_running` / `worker_pane_context_changed` / `worktree_missing`）を **1 回だけ**確認し、該当があれば報告に含める（毎回の確認では呼ばない）。
- **想定内の `skipped` 警告**: この運用では依頼元が `working` のため `completion.status = skipped` は正常であり、依頼先の画面に出る `requester was not notified` 警告も想定内である旨を、まとめの報告に 1 行書く。
- **二重作業の防止**: 回収済みの task について後から `recover-completion` 等による `resume_requester` の completion prompt を受けた場合は、後続 workflow を再実行せず、処理済みである旨を報告して終える。
