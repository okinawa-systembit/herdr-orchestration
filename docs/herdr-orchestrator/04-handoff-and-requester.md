# Handoff と Requester 識別

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-7"></a>

## 7. Handoff 設計

Agent 間のタスク委譲には以下の2モードを定義する。

```text
handoff
├── sync
└── async
```

<a id="sec-7-1"></a>

### 7.1 Sync Handoff

通常のタスク委譲では sync を標準とする。

対象例（標準 4 role、または Role Binding で登録された日本語・カスタムロール名。[§4](02-roles-and-task-contract.md#sec-4) 参照）:

- コードレビュー・設計レビュー → `reviewer`
- 実装に伴うテスト・検証作業 → `implementer`
- 要件・設計向けの調査・整理 → `design`
- 委譲・進行確認のみ → `orchestrator`（worker ではなく requester 側で完結する場合もある）

概念フロー:

```text
requester
    │
    │ prompt worker（--wait なし）
    ▼
worker
    │
    │ task
    ▼
settled
    │
    ▼
requester
    │
    └── result read / next action
```

レビューの場合:

```text
requester
    │
    │ review request
    ▼
reviewer
    │
    ├── review
    └── result保存
    │
    ▼
requester
    │
    └── review-response
```

この方式では reviewer から requester への明示的な callback は不要とする。requester は handoff CLI が [§6.14](03-task-registry.md#sec-6-14) の **Registry `result.status` 確定待機**を完了した後、`task-result` 等で durable result を検証して後続処理を継続する。Herdr の `agent prompt --wait` / Agent lifecycle の settled だけを task 完了の根拠にしない（[Herdr Agent automation](https://herdr.dev/docs/agent-automation/)）。sync handoff も Task Registry に記録し、`completion.action = none` / `completion.status = not_applicable` とする。

<a id="sec-7-2"></a>

### 7.2 Async Handoff

以下の場合は async handoff を利用できる。

- 長時間レビュー（worker: `reviewer`）
- 長時間の実装・検証（worker: `implementer`）
- 大規模な要件・設計調査（worker: `design`）
- 複数 Agent への並列委譲
- requester を待機させたくない処理

Role Binding で専任の日本語・カスタムロール（`調査`, `todo管理` 等）を登録していない場合は、上記のとおり標準 4 role に委譲先を割り当てる。

概念フロー:

```text
requester
    │
    │ task.id + requester context
    │ task
    ▼
worker
    │
    │ long running task
    ▼
complete
    │
    │ callback
    ▼
requester
```

async handoff では、依頼時に確定した requester の routing locator（`requester.agent_name`。Role Binding 利用時は `pane_id`、フォールバック時は live `agent.name`）と `pane_id` を requester context として Task Registry に保存し、`task.id` と関連付ける。completion 時は Task Registry に保存された `requester.agent_name` のみを用いて live Agent を再解決し、保存値以外の別 pane ID・別 agent name・変更後の Role Binding 先へは自動 fallback しない。

<a id="sec-7-3"></a>

### 7.3 Handoff 実行順序（Registry との整合）

sync / async を問わず、`handoff` は [§6.11](03-task-registry.md#sec-6-11) の順序に従う。

```text
入力検証 → live worker 解決
  → create（worker 識別子確定済み）
  → handoff wrapper が handoff.lock を flock 取得（fd を handoff 終了まで保持）
  → record-prompt-start → assert-handoff-continuable（§11.4 直前再検証）
  → agent prompt 送信（sync は --wait なし。最小 Task Envelope）
  → mark-dispatch（単一 atomic update）
  → sync: result 確定待機（§6.14）/ async: return
  → 実行ロック解放
```

配送時 routing 安全（TOCTOU）の正本は [§11.4](06-results-and-workflows.md#sec-11-4)（直前再確認・最小 prompt・受信側 Registry 検証）。

handoff と `reconcile-dispatch` の同時実行は [§6.13](03-task-registry.md#sec-6-13) に従う。

`create` 直後（prompt 未開始）や prompt 送信後〜Registry 確定前に停止した場合は [§6.12](03-task-registry.md#sec-6-12) の `reconcile-dispatch` で復旧する。早着 `result-submit` は [§10.2](06-results-and-workflows.md#sec-10-2) の再試行・再登録で扱う。

live worker 未解決などで `create` 前に失敗した handoff は Task Registry に記録しない（[§6 冒頭](03-task-registry.md#sec-6)）。requester チャットへの配送不能報告で利用者へ通知する。

sync handoff は task 完了の正本を Task Registry とするため、`agent prompt` に `--wait` を付けない（Agent lifecycle の settled だけでは [§6.14](03-task-registry.md#sec-6-14) を満たせない）。[§11.3](06-results-and-workflows.md#sec-11-3) のとおり `working` / `blocked` / `unknown` の worker へ新規 sync handoff は送らない。

`mark-dispatch` 前に worker が `result-submit` すると Registry は拒否する。wrapper は prompt 送信結果を得た時点で dispatch を確定してから sync wait に入る。

手動で同じ依頼を再送する場合は新しい `task.id` を発行する。応答消失後の `result-submit` 再実行は、同一 canonical payload なら idempotent replay とする（[§6.3](03-task-registry.md#sec-6-3) Mutation Rules）。

---

<a id="sec-8"></a>

## 8. Requester の識別

<a id="sec-8-1"></a>

### 8.1 main rename を標準としない

既存の検証実装では、callback 先を安定させるため依頼元 Agent を `main` へ rename していたが、中央設計では標準から外す。さらに、Herdr のネイティブセッション復元（`resume`）に干渉しないよう、`herdr agent rename` 自体を不要とし、[§4.1](02-roles-and-task-contract.md#sec-4-1) の Role Binding（`role` ⇔ `pane_id`）を標準とする（ワークスペース跨ぎ配送・グローバルロール・未バインド Requester `"unbound"` の扱いは [クロスワークスペース設計書](../herdr-orchestrator-cross-workspace.md) を参照）。

requester は、Task Registry 上で routing locator（`agent_name`）と `pane_id` の両方を保持する（Role Binding 利用時は `agent_name` にも解決時の `pane_id` を保存する）。

```yaml
# Role Binding 利用時（標準）
requester:
  role: design
  agent_name: w1:p1
  pane_id: w1:p1

# Pattern A 命名フォールバック時
requester:
  role: design
  agent_name: r-0123456789abcdef-design
  pane_id: w1:p1
```

理由:

- 元の Agent 名を強制変更しない（無名エージェントのまま運用可能）。
- `main` の名前競合を避ける。
- callback 時に Task Registry に保存された `requester.agent_name` で live Agent を決定論的に再解決できる。

<a id="sec-8-2"></a>

### 8.2 Pane ID と保存済み Locator

`pane_id` は推測しない。現在の Herdr セッション（`HERDR_PANE_ID` および `herdr agent get`）から取得した実測値を使用する。

[§4.1](02-roles-and-task-contract.md#sec-4-1) の Role Binding が設定されている場合、`handoff` 開始時に解決された `pane_id` がそのまま Task Registry の `requester.agent_name` / `worker.agent_name`（保存済み routing locator）として記録される。一方、Role Binding 未設定の Pattern A フォールバック環境では、live `agent.name` が `requester.agent_name` に記録され、`pane_id` は diagnostic metadata として記録される。

```text
requester.agent_name / worker.agent_name
  = handoff 時に確定・保存した Herdr 公式の live routing locator
    （Role Binding 利用時は pane_id、フォールバック時は live agent.name）

requester.pane_id / worker.pane_id
  = handoff 時点の実測 pane_id
```

いずれの方式でも、handoff 後の後続段階（completion 等）で保存済み `requester.agent_name` が解決できない場合は、別 pane ID や変更後の Role Binding 先へ自動 fallback せず `skipped / requester_unavailable` とする。

---

<a id="sec-8-3"></a>

### 8.3 `resume_requester` における宛先決定（Role Binding 再参照の禁止）

`resume_requester` の宛先決定には、**Task Registry に保存された `requester.agent_name` の再解決だけ**を使用する（Role Binding 経由で作成された task では、保存された `pane_id` が `requester.agent_name` に入っている）。

in-flight task の途中で `herdr-orchestrator role set` により Role Binding が別 pane に変更された場合でも、`resume_requester` は現在の Role Binding を参照せず、必ず Registry 保存時の `requester.agent_name` に対して解決・検証を行う。また、Pattern A フォールバックで作成された task において `requester.agent_name`（名前）が解決できない場合に、`requester.pane_id` へ暗黙 fallback することも行わない。

解決できない場合:

```text
completion.status = skipped
completion.reason = requester_unavailable
```

とする。

<a id="sec-8-4"></a>

### 8.4 Requester Validation

Herdr は callback 用の immutable Agent instance ID を公開 routing identity として提供していない。

そのため Orchestrator は「依頼時と同じ Agent 個体を完全に証明した」とは扱わない。

代わりに、Task Registry に保存された `requester.agent_name` で live Agent を解決し、Task Envelope / Registry に保存された context と照合する。

```text
保存済み requester.agent_name 解決
  ↓
live Agent 不在
  -> skipped / requester_unavailable

live Agent 存在
  ↓
context sanity check
  ↓
明らかな不一致
  -> skipped / requester_context_mismatch

安全に配送可能
  ↓
state確認
```

context の取得不能や曖昧さにより安全確認できない場合も配送しない。

このとき別 target（別 pane や再バインド後の新 pane）へ fallback しない。

`resume_requester` が保証するのは、依頼時と同一の Agent プロセスへの返却ではなく、**Registry に記録した保存済み locator（`requester.agent_name`）と作業 context を継承できる live Agent への結果返却**である（`requester.role` の決定・照合は [§8.5](#sec-8-5)）。

標準の作業 context 判定では、少なくとも次を照合する。

```text
必須:
  保存済み requester.agent_name（live agent.name または pane_id の一致）
  repository identity
  worktree path

workflow が固定を要求する場合:
  branch
  commit

補助 sanity check:
  agent kind
  cwd / foreground cwd
```

依頼時の Agent が終了・再起動した後でも、同じ保存済み `requester.agent_name` で解決された live Agent が必須 context を満たす場合、その Agent を requester role の継承先として扱ってよい。これは Agent 個体の同一性を保証するものではない。

branch / commit は常時必須にしない。未 commit 差分を扱う review 等では commit 固定が workflow と矛盾するため、workflow が明示的に要求する場合のみ照合条件とする。

Registry 上の判定規則:

```text
context.worktree.branch == null  → branch 照合をスキップ
context.worktree.branch != null → live 環境の branch と一致必須（不一致は skipped / context mismatch）

context.worktree.commit == null  → commit 照合をスキップ
context.worktree.commit != null → live 環境の commit と一致必須
```

`handoff` 入力または repository Skill が branch / commit 固定を要求する場合、非 null を `create` 時に Registry へ保存する。Agent 交代後も Registry の値だけで同じ判定を再現する。

同一 Agent 個体であること自体を必須とする workflow は、現在の Herdr 公開 routing identity だけでは安全に保証できないため、自動 `resume_requester` の対象外とする。その場合は自動 callback を行わず `skipped` とし、durable result を保持する。

<a id="sec-8-5"></a>

### 8.5 `requester.role` の決定と検証

Herdr は logical role を提供しない。Orchestrator は **`handoff` 実行時に caller の Role Binding（`pane_id`）または Pattern A の live `agent.name` から導出**し、Registry の `requester.role` として保存する（詳細は [クロスワークスペース設計書 §4.1](../herdr-orchestrator-cross-workspace.md#sec-4-1)）。

Pattern A 命名フォールバックの対象は初期標準 4 role（`design | reviewer | implementer | orchestrator`）とし、Role Binding 利用時は任意の日本語・カスタムロール名および未バインド時のシステム予約値 `"unbound"` を許容する。

#### `create` 時（決定）

1. handoff 実行時に caller（`HERDR_PANE_ID`）の live requester Agent を解決し、`pane_id` および live `agent.name`（無名時は `pane_id`）を取得する（取得不能なら `create` 前に失敗）。
2. **ローカル Role Binding による解決（最優先）**: ローカルスコープ（Git `<uuid>.json` または Non-Git `dir_<hash16>.json`）の Role Binding に caller の `pane_id` が登録されている場合、そのバインドされた role を `requester.role` とし、`requester.agent_name = requester.pane_id = caller.pane_id` として確定する（ただし `targets` 指紋を持たない旧形式バインディングの場合は `role_binding_outdated` で停止する）。
3. **グローバル Role Binding による解決**: ローカルに未登録で、グローバルスコープ（`global.json`）に caller の `pane_id` が登録されている場合、そのバインドされた role を `requester.role` とし、`requester.agent_name = requester.pane_id = caller.pane_id` として確定する（旧形式の場合は `role_binding_outdated` で停止）。
4. **Pattern A 命名によるフォールバック解決または `"unbound"`**: caller の `pane_id` がローカル・グローバルのいずれにも未登録の場合、Git リポジトリ内であれば初期標準 4 role それぞれについて [§4.1](02-roles-and-task-contract.md#sec-4-1)（Pattern A `r-<hash16>-<role>`）で `context.repository.identity` から期待 `agent.name` を算出し、live `agent.name` と **完全一致**する role があればそれを特定する（特定した role がローカル Role Binding 上で別の `pane_id` にバインドされている場合は `requester_context_unverified` として拒否）。Pattern A 名とも一致しない無名ペインの場合（または Non-Git ディレクトリの場合）、委譲先 worker が Role Binding で解決されるタスクであればシステム予約値 **`requester.role = "unbound"`**（`requester.agent_name = caller.pane_id`）を設定する（worker も Role Binding 未登録の場合は `requester_context_unverified` として `create` 前に失敗させる）。
5. 確定した role を `requester.role`、`requester.agent_name` を Registry に書き込む。

`worker.role` は handoff 入力で指定し、[§4.1](02-roles-and-task-contract.md#sec-4-1) / [§11.1](06-results-and-workflows.md#sec-11-1) / [クロスワークスペース設計書 §4.1](../herdr-orchestrator-cross-workspace.md#sec-4-1) に従いローカルまたはグローバル Role Binding の `pane_id`（両方にある場合は `worker_role_scope_ambiguous` で確認停止、両方とも未登録時は標準 4 role のみ期待 `agent.name`）から live worker を解決し、Registry へ書き込む（[§6.2](03-task-registry.md#sec-6-2)）。

#### `resume_requester` 時（照合）

1. 宛先は Registry に保存された `requester.agent_name` から live Agent を再解決する（現在の Role Binding は参照せず、別の pane ID や agent name へも fallback しない）。
2. [§8.4](#sec-8-4) の context sanity check（`repository.identity` / `worktree.path`、および指定時の `branch` / `commit`）を行う。
3. 解決した live Agent の `name` または `pane_id` が、Registry の保存済み `requester.agent_name` と一致することを確認する。不一致は `completion.reason = requester_context_mismatch`（初回解決失敗時は `requester_unavailable`）とする。
4. `requester.role` は workflow メタデータとして保持する。live 解決と context 確認を通過した Agent へ結果を返す。
