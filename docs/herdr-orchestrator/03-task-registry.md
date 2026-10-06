# Task Registry と状態管理

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-6"></a>

## 6. Task Registry / Canonical Schema

Task Registry は async 専用ではなく、**Orchestrator 経由の handoff のうち `create` まで到達した task** を記録する durable state store とする。sync も async も同一 schema・同一 state machine を使用する。sync では `completion.action = none`、`completion.status = not_applicable` を標準とする。

`create` 前に失敗した handoff（CLI 入力不正、`HERDR_ENV` 未設定、live worker 未解決、precondition 不通過など）は **Task Registry に JSON を作成しない**。`task.id` は発行されない。利用者向けの失敗は `handoff` CLI の stderr / exit code と、人間起点 handoff 時の requester チャット報告（[§9.10](05-completion-and-recovery.md#sec-9-10)）で扱う。MVP では未 `create` 失敗の監査ログは必須としない。

Herdr の Agent lifecycle state (`idle` / `working` / `blocked` / `done` / `unknown`) と Orchestrator の task state は別物として扱う。Herdr Agent が `done` になったことだけを task 成功の根拠にしない。

<a id="sec-6-1"></a>

### 6.1 保存先

```text
${XDG_STATE_HOME:-$HOME/.local/state}/herdr-orchestrator/tasks/
```

1 task = 1 JSON file とし、`<task.id>.json` で保存する。

<a id="sec-6-2"></a>

### 6.2 Canonical Schema v1

実装・設計・CLI が参照する唯一の Registry schema を次とする。

```json
{
  "schema_version": 1,
  "task": {
    "id": "task_7f3c81a2-75dd-4b75-a412-6e887eb62bd5",
    "type": "review",
    "mode": "async",
    "instruction": "現在の変更内容をレビューし、重大度別に問題を報告する",
    "status": "in_progress"
  },
  "context": {
    "repository": {
      "identity": "git.example/repo"
    },
    "worktree": {
      "path": "/home/user/Gitrepo/repo",
      "branch": "development",
      "commit": null
    }
  },
  "requester": {
    "role": "design",
    "agent_name": "git-example-repo-design",
    "pane_id": "w1:p1"
  },
  "worker": {
    "role": "reviewer",
    "agent_name": "git-example-repo-reviewer",
    "pane_id": "w1:p2"
  },
  "dispatch": {
    "status": "sent",
    "reason": null,
    "herdr_prompt_started_at": "2026-10-02T12:00:00+09:00"
  },
  "result": {
    "status": "pending",
    "ref": null,
    "summary": null
  },
  "completion": {
    "action": "resume_requester",
    "status": "pending",
    "reason": null,
    "recovery_count": 0
  },
  "handoff_execution": {
    "lock_owner_pid": null,
    "locked_at": null
  },
  "timestamps": {
    "created_at": "2026-10-02T12:00:00+09:00",
    "updated_at": "2026-10-02T12:00:01+09:00"
  }
}
```

`schema_version` は必須とし、MVP は `1` のみ受け入れる。未知 version は推測して処理せずエラーにする。

`context.repository.identity` は repository の論理 identity であり path として使用しない。`context.worktree.path` は task が実際に操作する checkout / worktree root の絶対 path とし、成果物の保存・参照・path safety の基準に使用する。Git の共通 repository directory や別 worktree の root を `context.worktree.path` として代用しない。

`worker.role` は handoff 入力の logical role（例: `reviewer`）とする。`worker.agent_name` / `worker.pane_id` は **live worker 解決後**に Orchestrator が確定し、`create` 時点で Registry へ書き込む必須フィールドとする。Agent から任意の worker 名を受け付けない。JSON 例は dispatch 完了後の状態を示す。

`requester.role` は初期標準 role のいずれかとし、live requester の `agent.name` から [§4.1](02-roles-and-task-contract.md#sec-4-1) / [§8.5](04-handoff-and-requester.md#sec-8-5) の規則で **導出**する。handoff 入力の `requester.role` は導出値との一致検証にのみ用い、単独では信頼しない。

`requester.agent_name` / `requester.pane_id` も同様に、handoff 実行時に Orchestrator が live Agent から取得できた値を `create` へ渡す。取得不能な場合は handoff を `create` 前に失敗させる。

`dispatch.herdr_prompt_started_at` は、Herdr へ `agent prompt` を呼び出す直前の atomic Registry 更新で ISO 8601 時刻を記録する。未送信の task では `null` とする。

`context.worktree.branch` / `context.worktree.commit` は task 作成時点の期待値を保存する。`null` は「この軸での固定照合を要求しない」を意味する。`null` でない値は completion / requester 再解決時に **必ず照合**する（[§8.4](04-handoff-and-requester.md#sec-8-4)）。

`handoff_execution` は canonical schema v1 の **任意だが推奨**フィールド（診断用）。排他の正本は Registry 外の `<tasks-dir>/<task.id>.handoff.lock` に対する **flock** である。

```json
"handoff_execution": {
  "lock_owner_pid": 12345,
  "locked_at": "2026-10-02T12:00:00+09:00"
}
```

未ロック時は `lock_owner_pid=null`, `locked_at=null` とする。`lock_owner_pid` は **flock 保持の証明ではない**（診断・`doctor` 用の best-effort 記録）。復旧判断は flock 取得可否と Registry 状態を正とする。

**TTL による奪取は行わない。** 生存プロセスが flock を保持している間、`reconcile-dispatch` は terminal 化しない。保持プロセス終了後のみ stale lock として `doctor` / reconcile が扱う。

<a id="sec-6-3"></a>

### 6.3 Four-axis State Machine

状態は4軸で独立管理する。Herdr の Agent lifecycle state と Orchestrator の task state を混同しない。

| Axis                | Meaning                                   | States                                                                              |
| ------------------- | ----------------------------------------- | ----------------------------------------------------------------------------------- |
| `task.status`       | task 全体の現在状態                       | `created`, `in_progress`, `succeeded`, `failed`, `cancelled`, `unknown`             |
| `dispatch.status`   | worker への初回配送について観測できた結果 | `pending`, `sent`, `failed`, `uncertain`, `skipped`                                 |
| `result.status`     | durable result の状態                     | `pending`, `succeeded`, `failed`, `unavailable`                                     |
| `completion.status` | requester への復帰配送                    | `not_applicable`, `pending`, `processing`, `sent`, `failed`, `uncertain`, `skipped` |

`dispatch.status` は初回配送時の観測事実として扱い、後から worker result が届いても `uncertain -> sent` のように書き換えない。後から確実な task result が得られた場合は `task.status` / `result.status` を更新して `unknown` を解消する。

`dispatch.status = uncertain` および `completion.status = uncertain` は未配送を意味しない。timeout / stalled 等では blind resend を禁止する。

#### Event Transition Matrix

| Event                                                                                     | `task.status` | `dispatch.status` | `result.status` | `completion.status`                                                             |
| ----------------------------------------------------------------------------------------- | ------------- | ----------------- | --------------- | ------------------------------------------------------------------------------- |
| sync task create                                                                          | `created`     | `pending`         | `pending`       | `not_applicable`                                                                |
| async task create (`completion.action=resume_requester`)                                  | `created`     | `pending`         | `pending`       | `pending`                                                                       |
| async task create (`completion.action=none`)                                              | `created`     | `pending`         | `pending`       | `not_applicable`                                                                |
| initial dispatch success                                                                  | `in_progress` | `sent`            | `pending`       | 維持                                                                            |
| initial dispatch skipped by safety/context validation                                     | `failed`      | `skipped`         | `unavailable`   | `resume_requester` は `skipped`; `none` / sync は `not_applicable`              |
| initial dispatch definite failure                                                         | `failed`      | `failed`          | `unavailable`   | `resume_requester` は `skipped`; `none` / sync は `not_applicable`              |
| initial dispatch uncertain (`resume_requester`)                                           | `unknown`     | `uncertain`       | `pending`       | `pending`                                                                       |
| initial dispatch uncertain (`none`)                                                       | `unknown`     | `uncertain`       | `pending`       | `not_applicable`                                                                |
| worker result succeeded (`resume_requester`)                                              | `succeeded`   | 維持              | `succeeded`     | `pending`                                                                       |
| worker result succeeded (`none`)                                                          | `succeeded`   | 維持              | `succeeded`     | `not_applicable`                                                                |
| worker result failed (`resume_requester`)                                                 | `failed`      | 維持              | `failed`        | `pending`                                                                       |
| worker result failed (`none`)                                                             | `failed`      | 維持              | `failed`        | `not_applicable`                                                                |
| worker result unavailable (`resume_requester`)                                            | `failed`      | 維持              | `unavailable`   | `pending`                                                                       |
| worker result unavailable (`none`)                                                        | `failed`      | 維持              | `unavailable`   | `not_applicable`                                                                |
| completion claim                                                                          | 維持          | 維持              | 維持            | `pending -> processing`                                                         |
| completion send success                                                                   | 維持          | 維持              | 維持            | `processing -> sent`                                                            |
| completion definite failure                                                               | 維持          | 維持              | 維持            | `processing -> failed`                                                          |
| completion delivery uncertain                                                             | 維持          | 維持              | 維持            | `processing -> uncertain`                                                       |
| completion intentionally not sent                                                         | 維持          | 維持              | 維持            | `pending / processing -> skipped`                                               |
| explicit task cancellation before terminal result **（MVP では event 無効・将来拡張用）** | `cancelled`   | 現在値を保持      | 現在値を保持    | `resume_requester` で pending なら `skipped`; `none` / sync は `not_applicable` |

`completion.action=resume_requester` の初回 dispatch が `failed` / `skipped` で worker が task を受け取っていないことが確定している場合、`completion.status` を `pending` のまま残さず `skipped` に確定する。この terminal パターンは [§9.13](05-completion-and-recovery.md#sec-9-13) claim および [§9.14](05-completion-and-recovery.md#sec-9-14) reset の対象外とする。`completion.action=none` は async でも `not_applicable` を維持する。人間起点の handoff では requester チャットへ配送不能を報告する。

`dispatch.status = uncertain` 後に worker から canonical result が届いた場合は、`dispatch.status = uncertain` を履歴として保持したまま `task.status` を確定する。

```text
dispatch=uncertain / task=unknown
  ↓ worker result succeeded
result=succeeded / task=succeeded

# dispatch は uncertain のまま保持
```

同様に worker result が `failed` または `unavailable` なら `task.status = failed` へ確定する。

worker result event（上表の `worker result *` 行）は、`task.status` が `in_progress` または `unknown` の task にのみ適用する。`task.status = created` かつ `dispatch.status = pending` のまま result を受理しない。`dispatch.status` が terminal（`sent` / `failed` / `uncertain` / `skipped`）であることを result 受理の前提とする。

#### Allowed Transitions

MVP では `cancelled` への遷移は発生しない（[Mutation Rules](#mutation-rules)）。下表の `-> cancelled` は将来拡張用に予約する。

`task.status`:

```text
created
  -> in_progress
  -> failed
  -> unknown
  -> cancelled  # MVP 無効

in_progress
  -> succeeded
  -> failed
  -> cancelled  # MVP 無効

unknown
  -> succeeded
  -> failed
  -> cancelled  # MVP 無効

succeeded / failed / cancelled
  -> terminal（通常フローでは逆行しない）
```

`dispatch.status`:

```text
pending
  -> sent
  -> failed
  -> uncertain
  -> skipped

sent / failed / uncertain / skipped
  -> terminal observation（通常フローでは書き換えない）
```

`result.status`:

```text
pending
  -> succeeded
  -> failed
  -> unavailable

succeeded / failed / unavailable
  -> terminal（通常フローでは逆行しない）
```

`completion.status`:

```text
not_applicable
  -> terminal

pending
  -> processing
  -> skipped

processing
  -> sent
  -> failed
  -> uncertain
  -> skipped

failed / uncertain / skipped
  -> pending  # inspect + explicit reset の場合のみ

sent
  -> terminal
```

`processing -> pending` も [§9.7](05-completion-and-recovery.md#sec-9-7) の明示的な recovery の場合のみ許可する。`failed` / `uncertain` / `skipped` からの再実行も、自動遷移ではなく人間または明示的な診断・復旧操作を経た `reset-completion` のみ許可する。

<a id="mutation-rules"></a>

#### Mutation Rules

- `handoff` は Registry 書き込み前に、(1) CLI 入力 schema / context の検証、(2) live worker 解決と precondition 確認を完了する。live 解決前に `create` しない。
- `create` は mode と `completion.action` に応じて4軸の初期値を一度に設定する。`sync` および `completion.action=none` は `completion.status=not_applicable`、`async` かつ `resume_requester` は `pending` とする。`create` 時点で `worker.agent_name` / `worker.pane_id` および `requester` の live 識別子を必須とする。
- `record-prompt-start` は `task.status = created` かつ `dispatch.status = pending` の task にのみ受理する。`task.status` が terminal（`failed` / `succeeded` 等）または `dispatch.status` が terminal の task では拒否する。拒否時は Registry を変更しない。
- `record-prompt-start` は file lock 下で task JSON を再読込し、上記条件を満たす場合のみ `dispatch.herdr_prompt_started_at` を記録する（[§6.13](03-task-registry.md#sec-6-13)）。
- `mark-dispatch` は `task.status = created` かつ `dispatch.status = pending` かつ `dispatch.herdr_prompt_started_at != null` の task にのみ受理する。terminal task では拒否する。
- `mark-dispatch` は上表の初回 dispatch event だけを適用し、terminal な `dispatch.status` を通常処理で書き換えない。
- `reconcile-dispatch` は [§6.12](03-task-registry.md#sec-6-12) の A/B/B' 手順に従う。更新は [§6.13](03-task-registry.md#sec-6-13) の条件付き atomic update。**handoff 実行ロックを生存プロセスが保持**している task では terminal 化しない（TTL 奪取なし）。
- handoff 実行 flock は **wrapper プロセスが fd 保持**（§6.6）。Registry file lock とは別。
- `assert-handoff-continuable` は [§11.4](06-results-and-workflows.md#sec-11-4) に従い Registry lock 下で Registry 状態に加え **live worker**（名前・state・context）を直前再検証する。`agent prompt` は **wrapper が flock fd を保持したクリティカル区間内**で、assert 成功後ただちに呼び出す。再確認は競合窓を狭めるだけで消去しない。
- `update-result` は `result.status` と `task.status` を上表に従って同時更新する。
- `update-result` は `task.status ∈ {in_progress, unknown}` の task にのみ受理する。`created` または terminal task への submit は拒否する。
- `update-result` は `dispatch.status = pending` の task を拒否する。`result-submit` は retryable な拒否（[§10.2](06-results-and-workflows.md#sec-10-2)）を返し、payload を Registry に書かない。
- `update-result` は `task.status = unknown` から `succeeded` / `failed` への解消を許可する。
- `result-submit` は [§10.3](06-results-and-workflows.md#sec-10-3) worker submit context を満たす場合のみ `update-result` する。`agent.name` のみの一致では不十分。context 確認不能は拒否する。
- `result.status` が既に terminal の task へ同一 canonical payload を再 submit した場合は成功 replay とし Registry を変更しない。payload が異なる場合は拒否する。
- handoff の手動再試行は原則として新しい `task.id` を発行する。同一依頼の dedup キーは MVP では導入しない。
- MVP では public CLI に task cancellation を公開しない。`cancelled` と Matrix 上の cancellation event は将来拡張用に予約し、通常運用では遷移しない。
- `claim-completion` は [§9.13](05-completion-and-recovery.md#sec-9-13) の **completion eligibility** を満たす task に限り `pending -> processing` を atomic claim する。`completion.action=none` / `not_applicable` の task は claim 対象にしない。
- `reset-completion` public CLI は [§9.14](05-completion-and-recovery.md#sec-9-14) の reset 受理条件を満たす task のみ `processing|failed|uncertain|skipped -> pending` とする。claim eligibility は要求しない。
- `finish-completion` は `processing -> sent|failed|uncertain|skipped` だけを通常遷移として許可する。
- 状態機械にない遷移は Registry writer が拒否し、既存 JSON を変更しない。

<a id="sec-6-4"></a>

### 6.4 Single Writer Rule

Task Registry JSON を直接更新できる実装主体は `task-registry.py` のみとする。Agent、repository Skill、worker は JSON file を直接編集しない。

```text
Agent / repository Skill
  -> herdr-orchestrator public CLI
  -> task-registry.py internal operation
  -> Task Registry JSON
```

Registry update は file lock、temporary file、atomic replace を使用する。

<a id="sec-6-5"></a>

### 6.5 Public CLI Contract

Agent が使用する public surface は固定する。

| Command              | Main input                                                                                                      | Registry side effect                                                                                          | Output                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `handoff`            | task type/mode/instruction, target role, context, completion action                                             | create + dispatch update                                                                                      | canonical task summary + `task.id`                                  |
| `result-submit`      | `task.id`, result status/ref/summary                                                                            | result + task update; `completion.action=resume_requester` なら completion claim/finalize を best-effort 続行 | canonical result summary (+ completion 結果があれば同梱)            |
| `task-status`        | `task.id`                                                                                                       | none                                                                                                          | canonical task/status JSON                                          |
| `task-result`        | `task.id`                                                                                                       | none                                                                                                          | validated result metadata/content locator                           |
| `resume-task`        | `task.id`                                                                                                       | completion claim/finalize（eligibility 必須）                                                                 | completion result                                                   |
| `reset-completion`   | `task.id`                                                                                                       | completion を `pending` へ戻す（recovery 履歴付き）                                                           | reset 結果                                                          |
| `recover-completion` | `task.id`                                                                                                       | 診断 + 必要時 `reset-completion` + claim/finalize                                                             | recovery + completion 結果                                          |
| `doctor`             | none                                                                                                            | none                                                                                                          | environment diagnostics + stale dispatch 候補（§6.12 の A/B）の報告 |
| `reconcile-dispatch` | `task.id`、任意 `--dispatch-outcome {uncertain\|failed}`、`failed` 時は必須 `--dispatch-reason <code>`（§6.12） | dispatch / task update（明示復旧）                                                                            | reconcile 結果                                                      |

`reconcile-dispatch` の `--dispatch-outcome`（A 向け。省略時は Registry 不変の **人間判断待ち**）:

| 値          | 対応手順 | Registry 更新                                                                       |
| ----------- | -------- | ----------------------------------------------------------------------------------- |
| （省略）    | A-4b     | なし                                                                                |
| `uncertain` | A-4a     | `dispatch=uncertain`, `task=unknown`                                                |
| `failed`    | A-5      | `dispatch=failed`, `task=failed`, `result=unavailable`, completion は Matrix どおり |

B 向けの `failed` / `sent` / `uncertain` 確定も同一 CLI の precondition 分岐で行う（入力は引き続き `task.id` を正本とし、B/B' では `--dispatch-outcome` を省略した自動観測経路を実装してよい）。

`handoff` は `HERDR_ENV=1` を確認し、CLI 入力 schema と context を検証したうえで live worker を解決し、その後 `create` して配送する（入力検証と live 解決の順序は [§6.11](03-task-registry.md#sec-6-11)）。Herdr CLI の結果に応じて `dispatch.status` と `task.status` を内部更新する。

`resume-task` は [§9.13](05-completion-and-recovery.md#sec-9-13) eligibility を満たす task の claim/finalize に使用する（通常 `completion.status=pending`）。worker は `resume-task` を呼ばない。

`reset-completion` は stuck / failed / uncertain / skipped completion を人間または診断後に `pending` へ戻す。自動再送は行わない。

`recover-completion` は診断のうえ、§9.14 を満たせば reset、続けて §9.13 を満たせば claim/finalize する。reset だけ成功して claim しない終了を許可する。

<a id="sec-6-6"></a>

### 6.6 Internal Registry Operations

内部 operation は public CLI の副作用としてのみ呼び出す。Agent に任意 state mutation API を公開しない。

```text
create
record-prompt-start
assert-handoff-continuable
mark-dispatch
reconcile-dispatch
update-result
claim-completion
finish-completion
inspect-completion
reset-completion
cleanup
```

`create` は Orchestrator が `task.id` を内部生成し、live 解決済みの canonical schema v1 必須情報を受け取る。任意 ID 指定は受け付けない。

#### handoff 実行 flock の保持主体

**`herdr-orchestrator handoff` wrapper プロセス**（短命な子プロセスではない）が、`<task.id>.handoff.lock` を open し **flock 用 file descriptor を handoff 完了まで保持**する。

```text
handoff wrapper プロセス
  → open(handoff.lock) + flock(LOCK_EX)   # ステップ 4
  → record-prompt-start / assert / agent prompt / mark-dispatch / sync poll
  → flock 解放 + close                     # ステップ 10
```

`task-registry.py` の Registry 更新は同一 wrapper 内から **ライブラリ呼び出しまたは in-process 呼び出し**とし、`acquire-handoff-lock` を **別プロセスの都度起動**して flock を取らない。子プロセス終了で fd が閉じ、flock が解放される実装は禁止する。

`reconcile-dispatch` は同じ `handoff.lock` に対し **非ブロッキング flock** を試行し、**取得できた場合のみ** §6.12 A/B/B' を実行する（= handoff wrapper がまだロック中なら conflict）。JSON の `lock_owner_pid` だけを根拠に reconcile 可否を決めない。

`assert-handoff-continuable` と `agent prompt` は **wrapper が flock fd を保持したまま**実行する連続クリティカル区間とする。失敗時は prompt しない。

`record-prompt-start` は外部 `agent prompt` の直前に wrapper が呼び出す。失敗した場合、handoff は **Herdr `agent prompt` を呼ばず** エラーで終了する。

`mark-dispatch` は Herdr prompt 操作の結果から wrapper が呼び出す。prompt 成功判定直後の **単一** atomic update とし、成功後に別の Registry write を挟まない。受理条件を満たさない場合は更新せず handoff は外部副作用を増やさない（prompt 済みで Registry だけ未更新の場合は [§6.12](03-task-registry.md#sec-6-12) B / `reconcile-dispatch`）。Agent が `sent` 等を自己申告して直接更新しない。

`reconcile-dispatch` は [§6.12](03-task-registry.md#sec-6-12) の dispatch 復旧専用 operation とする。public CLI からも呼び出せる。

`update-result` は result contract と [§6.3](03-task-registry.md#sec-6-3) Mutation Rules の受理条件を検証してから、Event Transition Matrix に従って `result.*` と `task.status` を同一 atomic update で更新する。`result.status=succeeded` は `task.status=succeeded`、`result.status=failed|unavailable` は `task.status=failed` とする。`task.status=unknown` からの確定も同じ規則で行う。

`result-submit` public CLI は `update-result` の後、[§9.13](05-completion-and-recovery.md#sec-9-13) eligibility を満たす task について、同一プロセス内で `claim-completion` → `finish-completion` 相当を best-effort 実行する。eligibility 不一致時は result のみ確定し completion は起動しない。

`claim-completion` は `pending -> processing` を atomic claim し、`finish-completion` が [§6.3](03-task-registry.md#sec-6-3) に従って `sent|failed|uncertain|skipped` へ確定する。`reset-completion` は inspect 後の明示復旧時だけ `processing|failed|uncertain|skipped -> pending` を許可する。

<a id="sec-6-7"></a>

### 6.7 Result Lookup / Path Safety

`context.repository.identity` は [§11.0](06-results-and-workflows.md#sec-11-0) の canonical 文字列（例: `git.example/repo`）であり、filesystem path ではない。

`context.worktree.path` は当該 task が実際に操作する checkout / worktree root の絶対 path とする。通常 checkout と `git worktree` のどちらでも、task の成果物はこの path を基準に扱う。

`result.ref` は **worktree-relative path** のみを許可する。絶対 path、`..`、`~`、`context.worktree.path` 外へ解決される symlink を拒否する。

```text
task.id
  -> Registry
  -> result.status
  -> result.ref
  -> context.worktree.path / result.ref を安全に resolve
  -> resolve 後も context.worktree.path 配下であることを確認
  -> repository 固有成果物
```

Task Registry は task context / state / result locator の正本、worktree 内の repository 固有成果物は詳細結果の正本とする。`result.status ∈ {succeeded, failed}` 時の `result.ref` 必須と artifact 存在は [§10](06-results-and-workflows.md#sec-10) に従う。

<a id="sec-6-8"></a>

### 6.8 Retention / Cleanup

cron / systemd timer を要求せず lazy cleanup とする。削除可能な task は次をすべて満たすものとする。

```text
task.status ∈ {succeeded, failed, cancelled}
completion.status ∈ {not_applicable, sent, failed, uncertain, skipped}
created から 30 日以上経過
```

`completion.status` が `pending` または `processing` の task は、`task.status` が terminal でも自動削除しない。`created` / `in_progress` / `unknown` も自動削除しない。cleanup は最大1日1回とする。

<a id="sec-6-9"></a>

### 6.9 Registry Security

Registry root / tasks dir は `0700`、task JSON / lock file は `0600` とする。Registry root、task JSON、lock file の symlink を拒否し、temporary file は Registry root 配下に作成する。owner が現在ユーザーでない file や shared writable directory を利用しない。

<a id="sec-6-10"></a>

### 6.10 Durable Task Context

requester Agent の会話履歴がなくても `task.id` から復元できるよう、`task.instruction`、context、requester/worker metadata、4軸state、`result.ref`、`result.summary` を保存する。元チャット全文は保存しない。

<a id="sec-6-11"></a>

### 6.11 Handoff / Result / Completion の実行順序

`handoff` wrapper は次の順序で Registry と Herdr を更新する。`mark-dispatch` は prompt 送信直後（手順 8）に行い、sync の Registry result 確定待機（§6.14）より **前**に完了させる。

```text
1. CLI 入力 schema / HERDR_ENV / context 検証（Registry 未作成）
2. live worker 解決・precondition 確認（Registry 未作成）
3. create（requester / worker の live 識別子を含む。task.status=created, dispatch.status=pending）
4. handoff wrapper が `handoff.lock` を open し flock 取得（§6.6 保持主体。fd は 10 まで保持）
5. record-prompt-start（失敗時は 6 以降を実行しない）
6. assert-handoff-continuable（[§11.4](06-results-and-workflows.md#sec-11-4)。Registry + live worker 直前再検証。失敗時は prompt しない）
7. Herdr agent prompt 送信（flock 保持中・assert 成功直後。sync では `--wait` は付けない。本文は §11.4 の最小 Task Envelope）
8. prompt 結果に応じて mark-dispatch を単一 atomic update（7 完了〜8 完了の間、worker が `dispatch.status=pending` を読むことは正常。受信側は [§11.4](06-results-and-workflows.md#sec-11-4) 手順 4a で bounded 再 poll する）
9. sync のみ: [§6.14](03-task-registry.md#sec-6-14) の result 確定待機
10. flock 解放・lock fd close（`handoff_execution` 診断フィールドを null 化）
11. async: 8 完了後に 10 を実行して handoff CLI を return
```

`mark-dispatch` により `dispatch.status` が terminal になる前に worker の `result-submit` が到着しないよう、8 を 9 より前に必ず実行する。sync では 8 完了後に worker が `result-submit` しても、`task.status` は `in_progress` 以上である。

worker の `result-submit` は 8 完了後の task に対してのみ受理する（[§6.3](03-task-registry.md#sec-6-3) Mutation Rules）。7〜8 の短い窓で早着した submit は拒否され、worker は [§10.2](06-results-and-workflows.md#sec-10-2) に従い再試行する。

Herdr への prompt 送信と Registry 更新は単一 atomic 操作にできない。3 完了後 5 前、5 後 8 前、または 7 成功後 8 前に Orchestrator が停止した場合の復旧は [§6.12](03-task-registry.md#sec-6-12) に従う。早着 result の再登録も [§10.2](06-results-and-workflows.md#sec-10-2) に従う。

初回 async completion の起動責任は `herdr-orchestrator`（`result-submit` 内連鎖）が担う。失敗時の再開は人間または `reset-completion` 後の `resume-task` が担う。worker Agent は completion を起動しない。

<a id="sec-6-12"></a>

### 6.12 Dispatch クラッシュ窓と `reconcile-dispatch`

handoff 途中の停止により、Registry と Herdr の実状態がずれる窓がある。代表例:

**A. prompt 送信前（`create` 直後）**

```text
task.status = created
dispatch.status = pending
dispatch.herdr_prompt_started_at = null
```

外部 `agent prompt` は **未呼び出し**のはず。async では `completion.status=pending` のまま放置され得る。

**B. prompt 送信後・`mark-dispatch` 前**

```text
dispatch.status = pending
dispatch.herdr_prompt_started_at != null
task.status = created
worker 側には Task Envelope が届いている可能性
```

いずれも [Mutation Rules](#mutation-rules) により `result-submit` を拒否する。自動 blind resend は禁止する。

`doctor` は次の **2 種類**の stale dispatch 候補を報告する（いずれも `updated_at` から一定時間経過後。実装既定値）。

```text
A: task.status = created
   dispatch.status = pending
   dispatch.herdr_prompt_started_at = null

B: task.status = created
   dispatch.status = pending
   dispatch.herdr_prompt_started_at != null
```

復旧は明示 operation `reconcile-dispatch <task.id>` とする。**受理は `task.status=created` かつ `dispatch.status=pending` のみ**（§6.13）。`dispatch` が既に terminal（`sent` / `uncertain` 等）の task へは適用しない。自動再送は行わない。

**A 向け（prompt 未開始の確認）**

`herdr_prompt_started_at = null` は **Orchestrator が prompt 開始を Registry に記録していない**ことの証拠であり、worker 未配送の **確定証拠ではない**（[§6.13 B'](03-task-registry.md#sec-6-13)）。Herdr の recent output は取得できる履歴に限り、**痕跡が見えないことは未配送の証明にならない**。

```text
1. Registry で herdr_prompt_started_at = null を確認
2. worker recent output / Agent state で当該 task.id・Task Envelope の **肯定痕跡**（受理・表示・応答開始等）を探す
3. 肯定痕跡あり → B' 手順へ（§6.13。`herdr_prompt_started_at` 漏れ等）
4. 肯定痕跡なし → recent output 欠落だけでは failed と断定しない。次のいずれかを **明示**に選ぶ（暗黙の terminal 化はしない）。公開 CLI `reconcile-dispatch <task.id>` の **既定**は **b（人間判断待ち）** とする（Registry 不変で exit 非 0、観測サマリを stderr 出力）。
   a. **機械確定（uncertain）:** `reconcile-dispatch <task.id> --dispatch-outcome uncertain`（名称は実装で固定）で lock 下更新。`dispatch.status=uncertain`, `task.status=unknown` とし、`dispatch.reason` に判定不能理由（例: `reconcile_indeterminate_no_worker_trace`）を記録して terminal 化する。
   b. **人間判断待ち（既定）:** Registry の4軸は **変更しない**（`task.status=created`, `dispatch.status=pending`, `herdr_prompt_started_at=null` を維持）。`reconcile-dispatch` は更新せず終了し、stderr / CLI 出力に `task.id`・観測内容・推奨操作（Herdr UI / worker 再確認、のち `--dispatch-outcome uncertain` または手順 5、または新 `task.id` handoff）を返す。requester 起点の場合は [§9.10](05-completion-and-recovery.md#sec-9-10) で同一チャットへ報告する。**人間の判断結果**は、後続の `reconcile-dispatch`（手順 5 または B/B'）または **新 `task.id` handoff** としてのみ Registry に反映する。
5. 人間または追加観測で未配送が **確定**した場合のみ、`reconcile-dispatch <task.id> --dispatch-outcome failed --dispatch-reason <code>` で lock 下更新（名称は実装で固定）。`--dispatch-reason` は **必須**（監査・後追い用。例: `operator_confirmed_no_delivery`, `herdr_ui_worker_absent`）。Registry は initial dispatch definite failure 相当で次へ遷移する。
     dispatch.status=failed, task.status=failed, result.status=unavailable
     completion.action=resume_requester なら completion.status=skipped
     completion.action=none / sync なら completion.status=not_applicable
   （§9.13・§9.14 の worker 未受領 terminal。以降 completion claim / reset 不可）
6. blind resend しない
```

**B 向け（prompt 送信済み・dispatch 未確定）**

```text
1. Task Registry / worker.agent_name / Herdr の worker 現在状態を確認
2. worker recent output 等で当該 task.id / Task Envelope の受理痕跡を確認
3. 配送不能が確定 → mark-dispatch 相当で failed|skipped を記録（Matrix に従う）
4. 受理・作業開始が確定 → sent + task.status=in_progress
5. 受理済みか判定不能 → uncertain + task.status=unknown
6. 判定不能の場合、人間へ報告し blind resend しない
```

`reconcile-dispatch` により `dispatch.status` が terminal になった後のみ、通常の `result-submit` を受理する。`reconcile-dispatch` 完了時、`result.status` が `pending` なら CLI 出力に **result 再登録が必要**である旨を含める。手動 handoff 再試行は新しい `task.id` を発行し、旧 task へ同じ prompt を自動再送しない。

早着 submit の拒否後に worker が終了した場合、durable artifact が worktree に残っていれば、同一 `task.id` へ [§10.2](06-results-and-workflows.md#sec-10-2) の再登録手順で Registry を更新する。blind resend（Task Envelope の再配送）は行わない。

<a id="sec-6-13"></a>

### 6.13 handoff と `reconcile-dispatch` の競合制御

`create` 以降の handoff ステップ（4〜10）と、別プロセス・別操作者による `reconcile-dispatch` は同時に走り得る。Registry writer は **file lock 下で再読込した現状態**を前提に、条件を満たす場合のみ更新する（compare-and-set）。

#### 条件付き更新の共通パターン

```text
file lock 取得
task JSON 読込
operation 固有の precondition を検証
precondition 不一致 → lock 解放、conflict エラー（Registry 不変）
precondition 一致   → 遷移適用、atomic replace、lock 解放
```

競合時の機械可読例: `{ "error": "registry_conflict", "retryable": false, "task_id": "…" }`

#### handoff 側の停止条件

| ステップ                       | precondition 不一致時                                                                                             |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| handoff flock 保持中           | handoff wrapper が `handoff.lock` fd を保持中は、`reconcile-dispatch` の flock 取得が失敗し **terminal 化しない** |
| 6→7 クリティカル区間           | flock 保持下で assert 成功後 **直ちに** prompt。区間外では prompt しない                                          |
| 5 `record-prompt-start`        | **`agent prompt` を呼ばない**。handoff をエラー終了                                                               |
| 6 `assert-handoff-continuable` | **`agent prompt` を呼ばない**                                                                                     |
| 8 `mark-dispatch`              | prompt 再送しない。未確定なら B 復旧へ委譲                                                                        |

典型シナリオ:

```text
create 後、wrapper が flock 取得前に終了
  → flock 未保持 → reconcile-dispatch (A) が uncertain / 人間判断 または B' へ
  → 終了した wrapper は再開しない

wrapper が flock 保持中（プロセス生存）、assert 前後で一時停止
  → reconcile-dispatch は handoff.lock の flock 取得に失敗（conflict）
  → wrapper 再開後、同一 fd 下で handoff 継続（reconcile は進まない）

wrapper が flock 保持中に終了
  → flock 解放 → reconcile (A/B/B') が可能
  → 終了した wrapper は再開しない。新規 handoff は新 task.id
```

```text
prompt 後・mark-dispatch 前に wrapper 終了
  → flock 解放 → reconcile-dispatch (B/B') が dispatch terminal 化
  → 終了した wrapper は mark-dispatch を再試行しない

prompt 後・mark-dispatch 前に wrapper 生存・flock 保持
  → reconcile-dispatch は flock 競合で conflict
  → wrapper が mark-dispatch を完了
```

```text
handoff が create 直後、record-prompt-start 前
  → doctor が stale (A) を報告
  → 同時に handoff が record-prompt-start 成功
  → reconcile (A) は herdr_prompt_started_at != null で precondition 不一致 → 中止または B 手順へ
```

`doctor` の stale 報告は **復旧推奨**であり排他ロックではない。最終判断は lock 下の precondition とする。

#### `reconcile-dispatch` の precondition

| 手順     | 前提（すべての手順で共通）                                                                                                 | Registry lock 下の状態（§6.12 観測手順に加え）                                   |
| -------- | -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| （共通） | **`<task.id>.handoff.lock` の非ブロッキング flock(LOCK_EX) 取得に成功**（handoff wrapper が fd 保持中ならここで conflict） | `dispatch.status=pending`、`dispatch.status` が terminal の task は対象外        |
| A        | 上記成功後                                                                                                                 | `task.status=created`, `herdr_prompt_started_at=null`                            |
| B        | 上記成功後                                                                                                                 | `task.status=created`, `herdr_prompt_started_at!=null`                           |
| B'       | 上記成功後                                                                                                                 | `task.status=created`, `herdr_prompt_started_at=null`, worker 側に task 痕跡あり |

B' では Registry lock 下で `herdr_prompt_started_at` を観測時刻で **修復記録**してから B 手順へ進む。

`lock_owner_pid` は flock 可否の判定に使わない。flock 取得失敗、Registry 状態不一致、`dispatch.status` terminal のいずれかで **更新せず conflict**。blind resend しない。

<a id="sec-6-14"></a>

### 6.14 sync handoff の result 確定待機

sync の正常終了条件は、Herdr Agent lifecycle が `idle` / `done` になったこと **単独**ではない。Agent 状態は個別 task の canonical result 確定を示さない（[Herdr Agent automation](https://herdr.dev/docs/agent-automation/)）。Orchestrator は Registry の `result.status` を正本とする。

sync handoff は `mark-dispatch` 後、次を **期限付き**で poll する。

```text
1. Registry 上の result.status が terminal（succeeded|failed|unavailable）になるまで待つ
2. 推奨 poll 間隔: 2〜5 秒、上限: repository Skill または handoff 入力で指定（未指定時は実装既定値、例 30 分）
3. Herdr worker state は補助観測とする（blocked / unknown の報告、早期異常検知）
```

| 観測                                                      | handoff の戻り（Registry 4 軸は **現状維持**。上書きしない）                                                                                                                                                                                                                                |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `result.status` terminal                                  | **成功**。requester は `task-result` で検証                                                                                                                                                                                                                                                 |
| 期限超過、`result` 未確定、worker `working`               | **retryable 待機終了**（exit 例: `result_wait_timeout`）。`task.status` は dispatch どおり（通常 `in_progress`、`dispatch=uncertain` なら `unknown` のまま）。prompt **再送禁止**                                                                                                           |
| 期限超過、`result` 未確定、worker `idle`/`done`/`unknown` | 同上。worker が idle でも result 未登録なら成功扱いにしない。`task-status` / `task-result` 継続確認または §10.2 / 人間判断                                                                                                                                                                  |
| worker `blocked`                                          | **異常終了**を報告。state は維持。人間が Herdr UI で応答                                                                                                                                                                                                                                    |
| worker 不在（live 解決不能）かつ `dispatch.status=sent`   | handoff は **異常終了**。Registry は維持。**`reconcile-dispatch` は案内しない**（dispatch 確定済みのため対象外）。worker 復帰後の `result-submit`、worktree 上の成果物からの [§10.2](06-results-and-workflows.md#sec-10-2) 再登録、`task-status` / `task-result` 監視、人間による未完了判断 |
| worker 不在かつ `dispatch.status=uncertain`               | `task.status=unknown` と `dispatch=uncertain` を **維持**（書き換えない）。**`reconcile-dispatch` は案内しない**。§10.2 の result 再登録、成果物確認、`doctor` / 人間判断                                                                                                                   |
| `dispatch.status=pending`（mark-dispatch 未完了）         | sync poll に入らない。§6.12 + `reconcile-dispatch`（pending のみ）後に poll 再開                                                                                                                                                                                                            |

期限超過時も **4 軸を勝手に terminal 化しない**。`dispatch` 確定済み task の follow-up は **`result-submit` / §10.2 / `recover-completion` / `task-status`**。`reconcile-dispatch` は **created + dispatch pending** の stale task のみ。

worker 実行中に requester へ **result 再 submit を促さない**。

---
