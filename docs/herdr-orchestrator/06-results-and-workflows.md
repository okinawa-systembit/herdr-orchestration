# Result・Worker Resolution・レビュー workflow

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-10"></a>

## 10. Result Contract

worker は結果を canonical schema の `result` object として submit する。

```yaml
task:
  id: task_7f3c81a2-75dd-4b75-a412-6e887eb62bd5
result:
  status: succeeded
  ref: docs/reviews/R052-example.md
  summary: "重大な指摘なし。軽微な指摘2件。"
```

`result.status` は `pending | succeeded | failed | unavailable` の canonical state を使用する。worker が業務上 `blocked` を報告する必要がある場合は、Result Contract の自由な state を増やさず、workflow 側の summary / artifact に理由を残し、Orchestrator は検証可能な結果の有無に基づいて canonical state へ写像する。

`result.ref` は [§6.7](03-task-registry.md#sec-6-7) の path safety に従う **worktree-relative path** のみ。

| `result.status` | `result.ref`                 | 成果物                                                                      |
| --------------- | ---------------------------- | --------------------------------------------------------------------------- |
| `succeeded`     | **非 null 必須**             | `context.worktree.path` 基準で resolve でき、**当該ファイルが存在**すること |
| `failed`        | **非 null 必須**             | 同上（失敗報告・部分成果の durable artifact を正本とする）                  |
| `unavailable`   | **null 必須**                | 正本 artifact を持たない。`ref` が非 null なら拒否                          |
| `pending`       | Registry 既定（通常 `null`） | submit 前                                                                   |

`succeeded` / `failed` で `ref=null`、または `unavailable` で `ref≠null` の submit は **拒否**する。URL、絶対 path、任意 identifier、worktree 外 path は拒否する。

`result.summary` は Agent 交代時や completion prompt 受信後の文脈復元に使用する短い概要であり、詳細成果物の正本ではない。requester は Registry から取得する。詳細は `result.ref` の repository 固有成果物を正とする。

Orchestrator は少なくとも次を検証する。

- `task.id` が Registry の task と一致する。
- result submit が canonical schema に適合する。
- `task.status ∈ {in_progress, unknown}` かつ `dispatch.status ≠ pending` である（[§6.3](03-task-registry.md#sec-6-3) Mutation Rules）。
- [§10.3](#sec-10-3) **worker submit context** を満たす（`agent.name` 一致に加え repository identity / worktree path を Registry と照合）。
- `result.status ∈ {succeeded, failed}` のとき `result.ref` が非 null で、`context.worktree.path` 配下へ安全に解決でき、**resolve 先ファイルが存在**する。
- `result.status = unavailable` のとき `result.ref` が null である。
- **独立検証方針**: 汎用 Orchestrator では上記のような schema 適合や path の安全・存在確認といった「汎用検証」のみを行い、workflow やタスク内容に関する業務固有の判定（例: レビューが適切に行われたか、テストが通ったか等）はすべて Repository 側の固有 Skill に委ねる。

<a id="sec-10-1"></a>

### 10.1 Result Submit の冪等性

`result.status` が既に terminal の task へ、同一 `status` / `ref` / `summary` を再 submit した場合は成功 replay とし、Registry JSON は変更しない。いずれかが異なる場合は拒否する。

handoff の再試行は新しい `task.id` を発行する。同一 `task.id` に対する result の競合更新は state machine と file lock で拒否する。

<a id="sec-10-2"></a>

### 10.2 早着 Result と再登録

`dispatch.status = pending` の間に worker が `result-submit` すると、Registry は更新せず **retryable 拒否**とする。MVP の機械可読出力例:

```json
{
  "error": "dispatch_not_ready",
  "retryable": true,
  "task_id": "task_…",
  "hint": "dispatch が terminal になるまで同一 payload で再試行する"
}
```

#### Worker 側（通常経路）

repository 固有 Skill / worker workflow は次を必須とする。

```text
1. result.status=succeeded|failed の場合は durable artifact を worktree に保存し、その worktree-relative path を result.ref に設定してから result-submit する（ref 非 null 必須）
   result.status=unavailable の場合は result.ref=null のみ（artifact 不要）
2. dispatch_not_ready / retryable 拒否を受けたら、同一 payload で bounded retry する
3. 推奨: 最大 10 回、指数バックオフ（初回 1 秒、上限 30 秒）
4. retry 上限到達または非 retryable 拒否の場合、task.id と result.ref を summary に残し、異常終了または人間へ報告する
5. result-submit 成功（初回または idempotent replay）まで、task 完了として扱わない
```

Orchestrator は worker の早着 submit を受理しないが、**worker の再試行**により `mark-dispatch` 完了後に同じ `task.id` へ結果を登録できる。新しい `task.id` は発行しない。

#### dispatch 復旧後（`reconcile-dispatch` 経路）

`dispatch.status = pending` の task で、[§6.12 B](03-task-registry.md#sec-6-12)（`herdr_prompt_started_at` 設定済み）または [§6.13 B'](03-task-registry.md#sec-6-13) 修復後に `reconcile-dispatch` したあと、**同一 `task.id` への result 再登録**に適用する。

**再登録の前提（すべて必須）:**

```text
result.status = pending
dispatch.status ∈ {sent, uncertain}
task.status ∈ {in_progress, unknown}（Matrix / reconcile 結果に従う）
```

`reconcile-dispatch` または Matrix により `dispatch.status=failed|skipped` かつ `task.status=failed`・`result.status=unavailable` に **確定**した task へ `result-submit` は受理しない（[§6.3](03-task-registry.md#sec-6-3) Mutation Rules）。成果物が残っていても §10.2 再登録対象外とし、新 `task.id` で handoff 再試行する。

```text
dispatch.status = pending（B または B' 修復後）
  ↓
reconcile-dispatch → dispatch.status = sent または uncertain（result は pending 維持）
  ↓
worker または操作者が同一 canonical payload で result-submit を再実行
  ↓
update-result 成功 → 必要なら completion 連鎖
```

A 向け手順 4a で `dispatch.status=uncertain`・`task.status=unknown` に terminal 化した場合も、上記前提を満たせば worker から canonical result を [§6.3](03-task-registry.md#sec-6-3) の通常遷移で登録できる（prompt 再送はしない）。

worker Agent が既に終了している場合、操作者は worker pane を再起動するか、Registry 上の `worker.agent_name` を満たし **§10.3 の context 照合にも合格する** live Agent から同一 `result-submit` を実行する。`result.status ∈ {succeeded, failed}` では artifact 存在を必須検証する。Task Envelope の再配送は行わない。

#### sync handoff との関係

requester 側 sync `handoff` は [§6.14](03-task-registry.md#sec-6-14) により **`result.status` terminal** を正常終了条件とする。worker がまだ実行中の間、requester が worker へ result 再 submit を促さない。期限付き Registry poll で未確定の場合は `task-status` / `task-result` の継続確認、**`dispatch.status=pending` のときのみ** `reconcile-dispatch`、それ以外は §10.2 / 人間判断とする。

<a id="sec-10-3"></a>

### 10.3 Worker Submit Context（result 受理の提出元検証）

`agent.name` は live alias であり Agent 個体の永続 ID ではない（[§4.1](02-roles-and-task-contract.md#sec-4-1) / Herdr 公式）。`result-submit` は名前一致だけでは受理しない。

submit 時に Orchestrator は `HERDR_ENV=1` 下で submit 元 live Agent を解決し、Registry の当該 task と次を照合する。

```text
1. live agent.name == Registry worker.agent_name
2. submit 元の repository identity == context.repository.identity
3. [§11.0](#sec-11-0) で解決した worktree root / repository identity が Registry と一致
```

submit 元 context は [§11.0](#sec-11-0) に従って導出する（`handoff` worker 選定と同一規則）。取得不能・曖昧・矛盾がある場合は **拒否**する（`error: worker_context_unverified`、原則 **retryable: false**）。

Herdr が `foreground_cwd` を返さない場合、または §11.0 の Git 解決が失敗する場合も安全側で **拒否**する。

同名 live Agent が別 repository / worktree にいる場合、2 または 3 の不一致で拒否する。別 Agent 個体への worker 交代は、**同一 logical role 名かつ Registry context と一致する live Agent** からの submit を許可する。

Herdr Agent lifecycle と workflow completion / Result Contract を同一視しない。

---

<a id="sec-11"></a>

## 11. Worker Resolution

worker 選定は二段階で行う。

<a id="sec-11-0"></a>

### 11.0 Worktree Root 解決（handoff / result-submit 共通）

Herdr の `foreground_cwd` は **foreground process の cwd** であり、Registry の `context.worktree.path`（checkout / git worktree root）と同一とは限らない。Agent がリポジトリ内サブディレクトリにいる場合、`foreground_cwd` を worktree root と直接比較すると誤拒否・誤受理の原因になる（[Herdr CLI reference](https://herdr.dev/docs/cli-reference/)）。

Orchestrator は次の手順で **resolved worktree root** を求め、Registry と照合する。`handoff`（worker 選定）と `result-submit`（§10.3）は **同一手順**を用いる。

```text
1. foreground_cwd を Herdr CLI/API から取得し、絶対 path に正規化
   - 取得不能・空 → 拒否（配送不可 / worker_context_unverified）
2. resolved_worktree := git -C <foreground_cwd> rev-parse --show-toplevel
   - 非ゼロ終了、空出力、Git 管理外 → 拒否
   - stdout を絶対 path に正規化（realpath 相当）
3. resolved_worktree == context.worktree.path（正規化後の一致）
4. repository identity := handoff / create 時と同じ規則で resolved_worktree から導出
   - 複数 remote 等で一意に決まらない → 拒否
5. 導出した repository identity == context.repository.identity
```

`foreground_cwd` を worktree root の代用にしない。pane ID / terminal ID も代用にしない。

#### repository identity の正規化（handoff / submit 共通）

`context.repository.identity` は Git remote URL そのものではなく、Orchestrator が **1 関数**で決定論的に生成する **canonical 文字列**とする。Registry schema・handoff・`result-submit` は **同一形式のみ**を保存・比較する（owner/repo だけなど host を落とした別表記は使わない）。

**Canonical 形式**

```text
標準 port   →  <host>/<path>           例: git.example/repo
非標準 port →  <host>:<port>/<path>    例: git.example.com:8443/org/repo
```

- `<host>` は URL の hostname を **小文字**に正規化する（path の大文字小文字は URL 由来を保持）
- `<path>` は先頭 `/` なし・末尾 `.git` なし（ネスト path を含めてよい）
- scheme / user / userinfo / query / fragment は含めない

`handoff` の `create` と `result-submit` の §11.0 手順 4〜5 は **必ず同一関数**を呼ぶ。

```text
1. resolved_worktree で git remote（既定: origin、無ければ拒否）の fetch URL を **全件**取得
   - 例: `git remote get-url --all origin`（`--all` なしの単一 URL だけに依存しない）
2. 各 URL をパースし、HTTPS の userinfo（ユーザー名・token 等）・query・fragment は **破棄**（Registry に保存しない）
3. 対応する Git URL 形式を上記 **Canonical 文字列**へ正規化（未対応形式は拒否）
   - scp 形式 `git@github.com:org/repo` → `github.com/org/repo`
   - HTTPS `https://github.com/org/repo`（userinfo 除去後）
     - port **省略または 443（HTTPS 既定）** → `host/path`（`:443` は文字列に含めない）
     - port が **443 以外の明示値** → `host:port/path`
   - SSH URL 形式 `ssh://user@host[:port]/path/to/repo`（user は破棄）
     - port **省略または 22（SSH 既定）** → `host/path`
     - port が **22 以外の明示値** → `host:port/path`
   - scp / HTTPS（port 443 相当）/ `ssh://`（port 22 相当）の 3 形式は同一 repository なら **同一 canonical 文字列**になる
   - 非標準 port の `ssh://` または HTTPS を標準 port 形式と同一視する **エイリアス設定は MVP では行わない**（衝突回避のため明示 port は常に identity に残す）
4. origin の fetch URL 群が 1 つの論理 identity に収束しない → 拒否
5. 正規化結果を context.repository.identity として保存・比較
6. worktree 内の **全 remote 名**について fetch URL から identity を導出し、2 つ以上の remote で identity が一致しない → 拒否
   - fork で `origin`（自分の fork）と `upstream`（本家）を併用する構成は、通常 **異なる identity** となり handoff / submit は拒否される（MVP の運用制約。identity 正本は origin のみに限定しない）
```

repository 固有 Skill が identity を明示指定する場合は、**canonical 文字列**（`host/path` または `host:port/path`）を handoff 入力の正本とし、§11.0 手順 4 の導出結果と **完全一致**することのみを要求する（`owner/repo` のみの省略形は不可）。

<a id="sec-11-1"></a>

### 11.1 Candidate Resolution

Orchestrator は次を使って candidate worker を選定する。

```text
1. current repository identity を取得
2. requested logical role を確定
3. Herdr 公式の live Agent 情報から routing candidate を取得
4. [§4.1](02-roles-and-task-contract.md#sec-4-1) 手順 1–8 で `(identity, requested logical role)` から決定した `agent.name` を **唯一の** 期待名として照合（衝突時に別名へ変更しない）
5. Agent state を確認
6. 公式に取得可能な pane / process context から [§11.0](#sec-11-0) で worktree root を解決
7. 解決した worktree / repository identity が handoff 入力の期待値と矛盾しないことを確認
8. 安全に一意の candidate を選定できた場合のみ配送対象とする
9. 解決不能・曖昧・context 不一致の場合は配送不可とし、別 name / pane ID / role 単独名へ fallback しない
```

worktree / repository の一致証明には §11.0 を用い、`foreground_cwd` の生値だけでは判定しない。本来別用途の `agent_session_id` や `terminal_id` を routing identity に転用しない。

§11.0 を完了できない、または期待 context と矛盾する candidate は配送不可とする。

<a id="sec-11-2"></a>

### 11.2 Worker Self-Verification

candidate worker は task 実行前に Task Envelope の期待値と自分の実環境を照合する。手順の正本は [§11.4](#sec-11-4)（Registry を読んでから作業。prompt 単体を信頼しない）。

最低限確認する（dispatch readiness の bounded poll は [§11.4](#sec-11-4) 手順 4）。

```text
task.id で task-status を取得し Registry worker.agent_name が自分と一致
dispatch.status ∈ {sent, uncertain}（pending は即拒否せず §11.4 4a）
repository identity
worktree path
```

workflow で指定されている場合に確認する。

```text
branch
commit
```

概念:

```text
Task Envelope
  expected repository
  expected worktree
  expected branch?
  expected commit?
        ↓
worker current environment
        ↓
match?
  ├─ YES -> task開始
  └─ NO  -> taskを実行しない
```

repository / worktree が不一致なら誤配送として扱い、作業を開始しない。

commit は常に必須ではない。
未commit差分を対象とするレビュー等では commit 一致を要求しない。

<a id="sec-11-3"></a>

### 11.3 Worker State Precondition

handoff 前に worker の状態を確認する。

利用可能な状態:

```text
idle
done
```

原則として利用しない状態:

```text
working
blocked
unknown
```

`working` の worker へ新しい sync handoff を送らない。進行中 task との混線を避け、[§6.14](03-task-registry.md#sec-6-14) が前提とする Registry `result.status` 確定待機と整合させるためである。Herdr Agent lifecycle の `idle` / `done` は **task result の確定を意味しない**。

Herdr の live `agent.name` は同時に一意であるため、同一 `<project-slug>-<role>` を満たす「別 live worker」を追加探索することはできない。別 `agent.name` で second worker を起動しても routing 規約外となる。初回 §11.1 で解決した Agent が `working` / `blocked` / `unknown` の場合、MVP では **待機またはユーザーへ報告**を基本とし、暗黙の別名 worker 起動・別 role への自動切替は行わない。

```text
1. 同一 worker の idle 化または人間判断を待つ
2. 待機不能・期限切れの場合は handoff 失敗または retryable 終了として報告（§6.14 / §9.10）
3. 自動 fallback（別 role 名・pane ID・role 単独名・別 agent.name）は行わない
```

別 logical role への切り替えは新しい handoff 入力として扱い、暗黙の代替 worker 探索とはみなさない。

`blocked` / `unknown` を成功・完了として扱わない。

<a id="sec-11-4"></a>

### 11.4 配送時 routing 安全（TOCTOU）

Herdr の `agent prompt` は **確認と送信を一体の条件付き操作にできない**。`handoff.lock` は Registry 更新を直列化するが、**live Agent の終了・置換・状態変化は固定しない**。確認時 `idle` でも送信直前に `working` になり得る。`agent.name` は alias であり、確認後に **別 occupant** が同じ名前を担う可能性も [§4.2](02-roles-and-task-contract.md#sec-4-2) のとおり残る。

**再確認だけでは競合を消せない。** fail closed（**誤った Agent に task を実行させない**）は **送信側の直前再確認** と **受信側の Registry 正本検証** の二段で満たす。prompt の **表示** や Herdr 仕様上の **作業中 Agent への入力** 自体は、この二段では防げない（下表「残存リスク」）。

#### Orchestrator（handoff）

`assert-handoff-continuable`（[§6.6](03-task-registry.md#sec-6-6)）は flock 保持下、`agent prompt` の **直前**に Registry **と** live Herdr 観測を再検証する。

```text
1. Registry lock 下: task.status=created, dispatch.status=pending, herdr_prompt_started_at 整合
2. Registry の worker.agent_name を live 再解決
3. live Agent 不在 → prompt しない（handoff 失敗 / reconcile 委譲）
4. live agent.name == Registry worker.agent_name（不一致 → prompt しない）
5. Agent state ∈ {idle, done}（working / blocked / unknown → prompt しない。§11.3）
6. [§11.0](#sec-11-0) で live worktree / repository identity が Registry context と一致
7. 不一致・曖昧 → prompt しない
8. 成功後 **遅延なく** agent prompt（同一 flock 区間）
```

手順 2 の worker 解決（[§11.1](#sec-11-1)）から時間が空いた場合も、手順 6 の assert で **必ず** live 側を再読する。

**Task Envelope（prompt 本文）** は routing 判断の正本にしない。最低限:

```text
task.id
herdr-orchestrator task-status / task-result（または repository Skill 定義の同等 CLI）を実行し Registry を読んでから作業すること
```

`task.instruction` の正本は Registry（[§6.10](03-task-registry.md#sec-6-10)）。Orchestrator は prompt 本文を **最小 Task Envelope に限定**する（[§14.2](07-skill-and-implementation.md#sec-14-2)）。Herdr 表示に余分なテキストが混ざっても、受信側は **Registry 検証成功前に作業を開始してはならない**。

#### Worker 受信側（必須）

[§11.2](#sec-11-2) を拡張し、**prompt を受け取った Agent** は次を **すべて**満たすまで task を開始しない。

Herdr への prompt 配送（[§6.11](03-task-registry.md#sec-6-11) 手順 7）と `mark-dispatch`（手順 8）は **別操作**である。worker が prompt 表示直後に `task-status` を読むと `dispatch.status=pending` になり得る。**pending 単体は即時拒否（誤配送）としない。** 期限付きで Registry を再読し、terminal 化を待ってから context 検証へ進む。

```text
1. prompt から task.id を取得
2. herdr-orchestrator task-status <task.id>（正本は Registry）
3. Registry worker.agent_name == 自分の live agent.name（不一致 → 作業しない・誤配送）
4. dispatch.status の扱い:
   a. pending → 同一 task.id で task-status を **bounded 再 poll**（[§10.2](#sec-10-2) と同型: 推奨 最大 10 回、指数バックオフ初回 1 秒・上限 30 秒）。sent / uncertain になったら 5 へ
   b. sent / uncertain → 5 へ（task / result 状態が当該 role の作業開始を許可することを確認）
   c. failed / skipped → 作業しない（task.id と dispatch terminal を summary / 人間報告）
   d. 4a の retry 上限到達で pending のまま → **task 作業は開始しない**。task.id と `dispatch_pending_timeout` を summary / 人間報告し、復旧待ち（`task-status` 監視・§6.12 reconcile・人間）とする。自動で別 task を開始しない
5. repository identity / worktree path（および workflow 要求時 branch / commit）が自分の環境と一致（不一致 → 作業しない・誤配送）
6. 5 まで成功したら task 作業を開始する
```

手順 4a は **Orchestrator の mark-dispatch 待ち**用であり、Registry 上 `herdr_prompt_started_at` 設定済みで長時間 pending の場合は dispatch クラッシュ窓（§6.12）の可能性がある。4d 到達後も **同一 task.id** で `task-status` を人間または workflow が再確認すれば、後から `sent` になった時点で 3–5 をやり直して開始してよい（新 prompt なし）。

Herdr が作業中 Agent に入力を配送した場合でも、3 / 5 により **Registry と不一致なら no-op** とする（作業中への入力そのものは Herdr 仕様上禁止できない残存リスク）。

#### Requester 受信側（completion）

`resume_requester` の prompt も同様とする。`finish-completion` 前の **直前再確認**（[§9.3](05-completion-and-recovery.md#sec-9-3) 手順 6 直前）で、requester の live 解決・state・context を再検証する。prompt 本文は handoff と同様 **task.id + Registry 読取指示**を最小とする。

requester Agent は completion メッセージだけを信頼せず、`task-status` / `task-result` で `result.status` terminal と context を確認してから後続 workflow を開始する。

#### 残存リスクと運用

| リスク                  | 緩和                                  | 残るもの                                   |
| ----------------------- | ------------------------------------- | ------------------------------------------ |
| 確認後に同名別 occupant | 受信側 Registry + context 検証        | 無関係な Agent が prompt を **表示**し得る |
| 確認後に `working` 化   | assert 直前 state 再読 + 受信側 no-op | Herdr が working Agent へ **入力**し得る   |
| prompt 内容の改ざん     | 正本は Registry                       | 表示テキストと Registry の差異             |

MVP では Herdr API に送信時条件付き照合はない（0.9.3 schema 確認済み）。上記二段検証を **必須**とし、[§18](08-adoption-and-verification.md#sec-18) で直前 state 変化・受信側拒否を試験する。

---

<a id="sec-12"></a>

## 12. Reviewer Workflow

<a id="sec-12-1"></a>

### 12.1 標準レビュー

ユーザー操作:

```text
レビュー依頼を出して
```

標準フロー:

```text
User
 │
 ▼
Requesting Agent
 │
 ├─ Repository review workflow
 │    ├─ Scope決定
 │    ├─ lint/build
 │    └─ Review Prompt生成
 │
 ▼
herdr-orchestrator
 │
 ├─ role = reviewer
 └─ mode = sync
 │
 ▼
Herdr公式 Skill
 │
 └─ reviewerへhandoff
 │
 ▼
reviewer
 │
 ├─ Repository code-review
 ├─ RNNN
 └─ INDEX
 │
 ▼
Requesting Agent
 │
 └─ Repository review-response
```

<a id="sec-12-2"></a>

### 12.2 長時間レビュー

長時間処理と判断した場合のみ:

```text
mode = async
```

とする。

```text
requester
 │
 ├─ task.id + requester contextをhandoff
 │
 ▼
reviewer
 │
 ├─ review
 └─ result保存
 │
 ▼
callback requester を再解決して notify
```

---
