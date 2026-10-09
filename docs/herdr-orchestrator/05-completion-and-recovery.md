# Completion・通知・復旧

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-9"></a>

## 9. Completion / Callback 設計

sync handoff では明示 callback を使用しない。

async handoff では、worker 完了後の処理を `completion.action` で明示する。

標準 action:

```text
resume_requester
none
```

正常な completion の人向け通知は中央 Orchestrator の completion action に含めない。

一方、人間が requester Agent のチャットから明示的に開始した handoff で、worker への初回配送が安全に実行できない場合は、requester Agent が同じチャットで配送不能を即時に報告する。人間はその通知を受けて Herdr Space で worker の停止・移動・状態を確認し、必要に応じて運用対処する。Orchestrator は別 target への自動 fallback を行わない。

人間は Herdr の Space / Agent sidebar の state marker で `working` / `blocked` / `done` / `idle` 等の状態を確認できる。正常完了の追加通知が必要な場合は repository 固有 Skill / workflow がチャットで通知してよい。

<a id="sec-9-1"></a>

### 9.1 Completion State

`completion.status` の状態は canonical schema の定義に従う。

```text
not_applicable
pending
processing
sent
failed
uncertain
skipped
```

`not_applicable` は sync handoff または `completion.action = none` に使用し、completion 処理の対象にしない。`uncertain` は prompt 配送済みか判定できない場合に使用し、自動再送しない。

以下の通常遷移は `completion.action = resume_requester` の task に適用する。`completion.action = none` は task 作成時から `not_applicable` を維持する。

標準遷移:

```text
pending
  ↓
processing
  ↓
sent
```

失敗時:

```text
pending
  ↓
processing
  ↓
failed
```

`processing` は completion 実行権を獲得済みであることを表す。

同じ `task.id` に対して複数の実行主体が completion を処理しようとした場合でも、`pending -> processing` の遷移に成功した実行主体だけが実際の再開・通知を行う。

<a id="sec-9-2"></a>

### 9.2 重複抑止

Task Registry の completion 更新は競合し得るため、原子的に扱う。

推奨実装:

```text
file lock
  ↓
task JSON 読込
  ↓
completion.status == pending を確認
  ↓
processing へ更新
  ↓
temporary file へ書込
  ↓
atomic replace
  ↓
lock 解放
```

`processing` の task を別実行主体が見つけても自動再送しない。

外部通知または Agent prompt 成功後、`sent` 更新前に処理主体が終了する可能性があるため、本設計では exactly-once 配送を保証しない。曖昧な状態では自動再送せず、Task Registry、対象 Agent、durable result を確認して復旧する。

<a id="sec-9-3"></a>

### 9.3 `resume_requester`

requester Agent を自動的に再開する。

主な用途:

- async review（worker: `reviewer`）
- 長時間の実装・検証（worker: `implementer`）
- 大規模な要件・設計調査（worker: `design`）
- worker 完了後に requester が後続処理を継続する workflow

専任 `tester` / `researcher` は初期標準外のため、上記の worker role にマッピングする（[§4](02-roles-and-task-contract.md#sec-4)）。

再開手順:

```text
1. completion.status を pending -> processing へ claim
2. requester.agent_name を現在の live agent から再解決
3. 見つからなければ completion.status = skipped / reason = requester_unavailable
4. repository identity / worktree path、および workflow が要求する branch / commit を Task Registry の期待値と照合
5. context が不一致・確認不能・曖昧なら completion.status = skipped / reason = requester_context_mismatch
6. **prompt 直前**に手順 2–5 を live 観測で再実行する（[§11.4](06-results-and-workflows.md#sec-11-4) TOCTOU。再確認のみでは安全を保証しない）
7. requester state が idle / done の場合のみ Agent へ入力して再開（working / blocked / unknown は 8 へ）
8. working / blocked / unknown の場合は自動入力せず completion.status = skipped
9. prompt 本文は task.id と Registry 読取指示を最小とする（§11.4）
10. prompt 配送成功を確認できた場合 `completion.status = sent`
11. target 不在、context mismatch、CLI の明確な拒否など配送不能が確定した場合は `failed` または前述の `skipped` とする
12. timeout / stalled 等で配送済みか判定できない場合は `completion.status = uncertain` とし、自動再送しない
```

requester Agent は prompt 表示後も [§11.4](06-results-and-workflows.md#sec-11-4) の受信側手順（`task-status` / `task-result` で正本確認）を経てから後続 workflow を開始する。

`resume_requester` の Herdr 操作は wrapper が公式 CLI/API を直接呼ぶ。wrapper の実装・Agent の判断は公式 Herdr Skill の安全前提と意味論に従い、`HERDR_ENV=1` を迂回しない。

<a id="sec-9-4"></a>

### 9.4 Completion Skip Policy

`resume_requester` 対象が `working` / `blocked` / `unknown` のため意図的に自動入力を見送る場合、`completion.status = skipped` とする。

```text
working
blocked
unknown
  ↓
自動 prompt しない
  ↓
completion.status = skipped
```

`skipped` は必ずしも task failure を意味せず、安全上 completion action を実行しなかったことを意味する。理由は `completion.reason` に記録する。

async review の `resume_requester` が `skipped` になった場合も durable result を保持する。
人間は Herdr の Space / Agent state marker または repository 固有 workflow のチャット出力から状態を確認する。

---

<a id="sec-9-5"></a>

### 9.5 Result Before Completion

completion より先に durable result を保存する。

```text
worker
  ↓
result保存
  ↓
result-submit（Task Registry 更新）
  ↓
completion.action=resume_requester なら
  herdr-orchestrator が同一 CLI 内で claim → finish（best-effort）
  ↓
失敗時: completion.pending|processing|failed 等を保持
  ↓
人間または reset-completion + resume-task
```

completion は結果そのものではない。

初回 completion の起動責任は worker ではなく `herdr-orchestrator` が担う。`completion.action=none` / `not_applicable`、worker 未受領で `completion.status=skipped` 確定済みの task では completion を起動しない（[§9.13](#sec-9-13)・[§9.14](#sec-9-14)）。`resume-task` は **durable result 確定後**の連鎖失敗・completion `failed` / `uncertain` / requester 状態による `skipped` の明示再試行用とする。初回 `dispatch=failed|skipped` から `reset-completion` で completion だけ `pending` に戻して requester へ送る経路は **許可しない**。

`result-submit` が dispatch 未確定で拒否された場合、completion は起動しない。dispatch 確定後に result が Registry へ登録されて初めて completion 連鎖の対象になる（早着 result の再登録は [§10.2](06-results-and-workflows.md#sec-10-2)）。

```text
Repository 固有成果物
  = Source of Truth

Task Registry
  = result locator / task state / completion state

completion action
  = resume / no-op control
```

<a id="sec-9-6"></a>

### 9.6 Completion Failure

completion が失敗しても task result は失わない。

```text
completion failure
  ↓
task.id
  ↓
Task Registry
  ↓
result.ref
  ↓
durable result
```

`completion.status = failed` の task は自動再送しない。`completion.status = uncertain` は requester prompt が未配送だったことを意味しないため、特に blind resend を禁止する。再試行が必要な場合は Task Registry、requester Agent、Herdr の現在状態を確認したうえで明示的な復旧操作として扱う。

<a id="sec-9-7"></a>

### 9.7 `processing` 復旧

`completion.status = processing` のまま処理主体が停止した場合、自動的に `pending` へ戻さない。

復旧は public CLI **`reset-completion`** または **`recover-completion`** とする。Agent が internal operation を直接呼ばない。

```text
1. Task Registry を確認（recover-completion の診断出力または task-status）
2. durable result / result.ref を確認
3. requester Agent の現在状態を確認
4. completion が既に実行済みでないこと、または再実行してよいことを確認
5. `reset-completion`（[§9.14](#sec-9-14) の reset 受理条件を満たす場合のみ）
6. `resume-task` 相当の claim/finalize（[§9.13](#sec-9-13) eligibility を満たす場合のみ）
```

`recover-completion` は 1〜4 の診断のうえ、**5 と 6 を分離**する。5 は eligibility 不要（reset 受理条件のみ）。ただし、二重配送を防止するため `completion.status ∈ {uncertain, processing}` の場合は自動的な `reset-completion` と再実行（6）を行わず、人間による確認と明示的な `reset-completion`（単体操作）を要求する。6 は reset 後に `completion.status=pending` となり、§9.13 eligibility を満たす場合のみ実行する。eligibility 未達なら reset だけ成功して停止してよい。

復旧時には履歴を残す。

```json
{
  "completion": {
    "action": "resume_requester",
    "status": "pending",
    "recovery_count": 1,
    "recovered_at": "2026-10-01T18:00:00+09:00"
  }
}
```

`processing -> pending` は手動または明示的な診断・復旧フローでのみ許可する。

---

<a id="sec-9-8"></a>

### 9.8 Meaning of `sent`

`completion.status = sent` は、completion action の配送要求が成功したことだけを意味する。

`resume_requester` の場合:

```text
sent
  = requester への prompt 書き込みが成功
  != requester の新しい turn 開始を保証
  != requester の後続処理完了を保証
```

必要に応じて、requester の `working` 等への状態変化を別途観測する。

Herdr の prompt 成功と workflow completion を同一視しない。

<a id="sec-9-13"></a>

### 9.13 Completion Eligibility（共通 claim 条件）

`claim-completion`（`result-submit` 内連鎖、`resume-task`、`recover-completion` の claim 段階）は、次を **すべて**満たす task にのみ適用する。

```text
completion.action = resume_requester
completion.status = pending
dispatch.status ∈ {sent, uncertain}
result.status ∈ {succeeded, failed, unavailable}
task.status ∈ {succeeded, failed}
```

worker がまだ実行中（`task.status=in_progress` かつ `result.status=pending`）の task へ completion を送らない。dispatch が `pending` の task へも送らない。

**初回配送失敗・worker 未受領**（[§6.3](03-task-registry.md#sec-6-3) Matrix: `dispatch.status ∈ {failed, skipped}`、`task.status=failed`、`result.status=unavailable`、`completion.status=skipped`）は **claim 対象外**とする。Herdr への prompt 成功は worker の task 受領を意味しない。requester への案内は [§9.10](#sec-9-10) と **新 `task.id` handoff** とする（同一 task への `resume-task` 再開は不可）。

`result-submit` 成功直後の自動連鎖も、update-result 後に上記を再検証してから claim する。

<a id="sec-9-14"></a>

### 9.14 Completion Reset 受理条件

`reset-completion` / `recover-completion` の reset 段階は、claim eligibility（§9.13）とは **別**とする。

```text
completion.action = resume_requester
completion.status ∈ {processing, failed, uncertain, skipped}
```

次の **初回配送失敗・worker 未受領 terminal** パターンは reset **拒否**（`completion.status=skipped` を維持）:

```text
dispatch.status ∈ {failed, skipped}
task.status = failed
result.status = unavailable
```

§9.4 の requester 状態による `skipped`（通常 `dispatch.status=sent` かつ `result.status` terminal）とは区別する。

`sent` / `not_applicable` / 既に `pending` の task へ reset は不要（拒否または no-op）。reset 後は `completion.status=pending` となり、claim には §9.13 を適用する。

`processing` または `uncertain` の task を手動で `reset-completion` する場合、requester が既に prompt を受信している可能性があるため、Orchestrator CLI は二重配送リスクの警告（`warning`）を出力する。操作者は requester の実機状態（作業中か等）を確認した上で後続の `resume-task` を判断すること。

---

<a id="sec-9-9"></a>

### 9.9 `none`

Agent 間の自動再開を行わない。

```text
completion.action = none
  ↓
durable result / Task Registry 更新
  ↓
completion.status = not_applicable
```

`none` はエラーではない。
人間向け状態表示は Herdr の Space / Agent sidebar に任せる。
repository 固有 workflow が必要に応じてチャットで完了を報告してよい。

<a id="sec-9-10"></a>

### 9.10 Human Awareness Contract

正常進行・正常完了について、中央 Orchestrator は proactive な人向け push 通知を必須機能としない。

ただし、人間が requester Agent のチャットで `レビュー依頼して`、`テスト依頼して` 等を明示的に開始した handoff については、初回配送を安全に実行できないことを requester が検知した時点で、同じチャットに異常を報告する。人間を無通知のまま待機させない。

通知には最低限、次を含める。

```text
task.id（create 前失敗では未発行のため省略し、instruction / logical role / repository identity で代替）
requested role / target
配送不能または配送結果不明であること
自動 fallback / blind resend を行っていないこと
Herdr Space で対象 Agent の停止・移動・状態を確認する案内
```

`create` 前に失敗した handoff では Registry が存在しないため、`task.id` を通知に含められない（[§6 冒頭](03-task-registry.md#sec-6)）。

明確な配送不能と配送結果不明は区別する。`target not found`、context mismatch、送信前の blocked 等は配送不能として扱う。timeout / stalled 等、prompt が既に配送された可能性を排除できない状態は配送結果不明として扱い、二重配送防止のため自動再送しない。

このチャット報告は新しい `completion.action` や外部通知基盤ではなく、handoff を開始した requester Agent の通常応答として実施する。したがって正常 completion 用の独立した人向け通知 action は導入しない。

人が状態を確認する標準手段を次のように定義する。

```text
Herdr Space / Agent sidebar
  = Agent の現在状態を確認

herdr-orchestrator task-status <task.id>
  = Task Registry 上の task 状態を確認

herdr-orchestrator task-result <task.id>
  = task.id から result.ref / durable result を確認
```

`none` (`not_applicable`) や completion callback 側の `skipped` については、中央 Orchestrator は正常完了時の push 通知を保証しない。ただし requester が対話中に開始した初回 handoff の配送不能・配送結果不明は上記の通り requester チャットへ即時報告する。

repository 固有 Skill / workflow が必要と判断する場合は、チャット上で業務上の完了報告を行ってよい。

重要:

```text
Herdr state marker
  = Agent lifecycle の可視化

Task Registry
  = task lifecycle の正

Repository成果物
  = task result の正
```

Herdr の `done` 等だけを個別 `task.id` の成功判定として扱わない。

<a id="sec-9-11"></a>

### 9.11 Dispatch Failure Visibility

人間が requester Agent のチャットから `レビュー依頼して`、`テスト依頼して` 等の handoff を開始した場合、配送不能または配送結果不明を黙って終了してはならない。

requester Agent は現在のチャットで人間へ明示的に報告する。

#### 配送不能

例:

```text
target Agent が存在しない
requester context mismatch
worker context mismatch
blocked 等により公式 Agent routing が送信前に拒否された
```

処理:

```text
fallbackしない
create 前: Registry を作らず CLI / チャットのみ記録
create 後: Task Registry へ dispatch / task 状態と原因を記録
requester Agentがチャットで配送不能を報告
```

通知例の意味:

```text
レビュー依頼を配送できませんでした。
対象 Agent の状態または配置を Herdr で確認してください。
自動フォールバックは行っていません。
```

#### 配送結果不明

timeout / stalled 等で「未配送」と断定できない場合は、配送不能とは区別する。

処理:

```text
自動再送しない
Task Registryへ uncertain を記録
requester Agentがチャットで配送結果不明を報告
```

通知例の意味:

```text
レビュー依頼の配送結果を確認できませんでした。
二重配送防止のため自動再送していません。
Herdr 上で対象 Agent の状態を確認してください。
```

#### 配送エラー分類と発生元による安全制御

handoff および completion の配送試行時に発生したエラーは、二重配送リスクを最小化するため、エラーペイロードの発生元（外部 Herdr CLI の実行結果 vs Orchestrator 内部の事前検証）に基づいて以下の方針で決定論的に分類する。

1. **未配送確定（`failed`）**:
   - 外部 Herdr CLI 起因（`payload.herdr.error.code`）: `agent_not_found`、`agent_not_ready`、`agent_blocked`（公式 Herdr CLI 仕様に基づき送信前に拒否されたことが確定しているもの）
   - Orchestrator 内部起因（`payload.error`）: `herdr_cli_missing`（CLI 実行バイナリ不在）、`invalid_timeout_setting`（CLI 起動前の timeout 設定値検証失敗）
2. **配送結果不明（`uncertain`）**:
   - 外部 Herdr CLI 起因（`payload.herdr.error.code`）: `herdr_cli_timeout`、`agent_prompt_stalled`、その他解析不能な未知の CLI エラー、および内部エラーと同名のコード（外部 CLI 由来である限り未配送確定と保証できないため安全側へ倒す）
   - Orchestrator 内部起因（`payload.error`）: 上記以外の予期しない内部エラー

**エラーコード発生元区別の設計方針**:
クラス階層の肥大化や独自フラグの追加を避け、既存の `HerdrCliError.payload` 構造（外部 CLI の stderr 由来である `herdr` オブジェクトの有無）を利用して発生元を識別する。
外部 CLI から将来的に内部エラーと同名のエラーコードが返された場合や未知のエラーコードが返された場合でも、公式仕様で送信前拒否が明確に確認されている特定コード（`agent_not_*`、`agent_blocked`）以外はすべて保守的に `uncertain`（配送結果不明）へ倒すことで、Herdr のバージョン更新やコード衝突に対しても二重配送防止の安全原則を堅牢に担保する。

正常完了時の proactive user notification は中央 Orchestrator の責務としない。

異常時のチャット報告は、依頼元 Agent が人間の待ち続けを防ぐために行う fail-visible behavior とする。

<a id="sec-9-12"></a>

### 9.12 Completion Prompt Contract

`resume_requester` の completion prompt は [§11.4](06-results-and-workflows.md#sec-11-4) の最小 Task Envelope に限定し、`task.id` と Registry 読取指示だけを送る。元 requester の会話履歴が失われても、必要な情報は Task Registry から復元する。

prompt 例:

```text
task.id: task_7f3c81a2-75dd-4b75-a412-6e887eb62bd5
herdr-orchestrator task-status / task-result で Registry の依頼と結果を確認し、
受信側の context 検証後に必要な後続処理を継続すること。
```

requester は `task-status` / `task-result` から `task.type`、`task.status`、`result.status`、`result.ref`、`result.summary` を取得する。`result.ref` は Registry 上の項目として必須だが nullable とする。`result.status = unavailable` など成果物が存在しない場合は `result.ref: null` を使用し、`result.summary` に requester が再開判断できる簡潔な理由を記録する。MVP では別の `result.reason` field は追加しない。

completion prompt は task context の正本ではなく、再開トリガーとして扱う。

```text
completion prompt
  = 再開トリガー

Task Registry
  = durable task context の正

repository result
  = 詳細成果物の正
```

prompt を受けた requester Agent は、[§11.4](06-results-and-workflows.md#sec-11-4) に従い `task-status <task.id>` / `task-result <task.id>` で Registry を確認してから後続処理を行う。
