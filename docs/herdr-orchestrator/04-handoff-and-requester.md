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

対象例（worker の logical role。`tester` / `researcher` は [§4](02-roles-and-task-contract.md#sec-4) のとおり初期標準外）:

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

専任 `tester` / `researcher` role 追加までは、上記のとおり既存4 role に委譲先を割り当てる。

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

async handoff では、依頼時に requester の live agent name と実際の pane ID を requester context として worker へ渡し、`task.id` と関連付ける。completion 時は Herdr 公式の routing target である requester の live `agent.name` を再解決し、pane ID 等へ fallback しない。

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

既存の検証実装では、callback 先を安定させるため依頼元 Agent を `main` へ rename していたが、中央設計では標準から外す。

requester は、取得できる場合は live agent name と pane ID の両方を保持する。

```yaml
requester:
  agent_name: git-example-repo-design
  pane_id: w1:p1
```

理由:

- 元の Agent 名を強制変更しない。
- `main` の名前競合を避ける。
- callback 時に live agent name を再解決できる。
- pane 移動等で public pane ID が変化しても固定 ID のみに依存しない。

<a id="sec-8-2"></a>

### 8.2 Pane ID

pane ID は推測・固定値化しない。現在の Herdr セッションから取得した実測値を **diagnostic metadata** として保持する。

pane ID は requester の routing fallback として使用しない。

```text
agent_name
  = Herdr公式のlive routing locator

pane_id
  = diagnostic metadata
```

requester の live `agent.name` が解決できない場合は、古い pane ID へ送信せず `skipped / requester_unavailable` とする。

---

<a id="sec-8-3"></a>

### 8.3 pane_id の扱い

`pane_id` は診断、トラブルシュート、履歴確認のために保持してよいが、`resume_requester` の宛先決定には使用しない。

`resume_requester` は live `agent.name` の再解決だけを使用する。解決できない場合:

```text
completion.status = skipped
completion.reason = requester_unavailable
```

とする。

<a id="sec-8-4"></a>

### 8.4 Requester Validation

Herdr は callback 用の immutable Agent instance ID を公開 routing identity として提供していない。

そのため Orchestrator は「依頼時と同じ Agent 個体を完全に証明した」とは扱わない。

代わりに、保存済み `agent.name` で live Agent を解決し、Task Envelope / Registry に保存された context と照合する。

```text
agent.name 解決
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

このとき別 target へ fallback しない。

`resume_requester` が保証するのは、依頼時と同一の Agent プロセスへの返却ではなく、**Registry に記録した `requester.role` と作業 context を継承できる live Agent への結果返却**である（`requester.role` の決定・照合は [§8.5](#sec-8-5)）。

標準の作業 context 判定では、少なくとも次を照合する。

```text
必須:
  agent.name
  repository identity
  worktree path

workflow が固定を要求する場合:
  branch
  commit

補助 sanity check:
  agent kind
  cwd / foreground cwd
```

依頼時の Agent が終了した後でも、同じ `agent.name` で解決された live Agent が必須 context を満たす場合、その Agent を requester role の継承先として扱ってよい。これは Agent 個体の同一性を保証するものではない。

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

Herdr は logical role を提供しない。Orchestrator は **live `agent.name` から導出**し、Registry の `requester.role` として保存する。

初期標準 role（[§4](02-roles-and-task-contract.md#sec-4)）のみ MVP で許可する。

```text
design | reviewer | implementer | orchestrator
```

#### `create` 時（決定）

1. handoff 実行時に live requester Agent を解決し、`agent.name` を取得する（取得不能なら `create` 前に失敗）。
2. 初期標準4 role それぞれについて、[§4.1](02-roles-and-task-contract.md#sec-4-1) 手順 1–8 で `context.repository.identity` から期待 `agent.name` を算出する。
3. live `agent.name` が 2 の期待名と **完全一致**する role を1つ特定する。0 件または2件以上は `create` 前に失敗する。
4. 特定した role を `requester.role` として Registry に書き込む。
5. handoff 入力に `requester.role` を含める場合は、4 の値と **一致必須**。不一致は `create` 前に失敗する。
6. `requester.agent_name` は live 解決値（= 4 に対応する期待名）を保存する。別名へ **変更しない**。衝突時は handoff を中止する。

`worker.role` は handoff 入力で指定し、[§4.1](02-roles-and-task-contract.md#sec-4-1) / [§11.1](06-results-and-workflows.md#sec-11-1) で期待 `agent.name` を固定したうえで live worker を解決し、Registry へ書き込む（[§6.2](03-task-registry.md#sec-6-2)）。requester 側も同じ4 role 語彙に限定する。

#### `resume_requester` 時（照合）

1. 宛先は保存済み `requester.agent_name` から live Agent を再解決する（pane ID へ fallback しない）。
2. [§8.4](#sec-8-4) の context sanity check を行う。
3. [§4.1](02-roles-and-task-contract.md#sec-4-1) 手順 1–8 で `(Registry の repository identity, requester.role)` から再算出した期待 `agent.name` と live `agent.name` が一致することを確認する。不一致は `completion.reason = requester_context_mismatch` とする。
4. `requester.role` は workflow メタデータとして保持する。live 解決と context 確認を通過した Agent へ結果を返す。
