# Role と Task Contract

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-4"></a>

## 4. Role 設計

`herdr-orchestrator` では、タスク上の **論理 role** と、Herdr 上で稼働中の **live agent name** を分離する。

初期標準の論理 role を以下とする。

| logical role   | 用途                             |
| -------------- | -------------------------------- |
| `design`       | 要件・設計の整理と判断           |
| `reviewer`     | コード・設計・仕様レビュー       |
| `implementer`  | 実装と実装に伴うテスト           |
| `orchestrator` | 依頼の委譲・進行確認・結果の整理 |

論理 role は担当する作業を表し、Herdr の `agent.name` そのものではない。4つの role は4つの live Agent の常駐、固定の handoff 順序、人間と `design` だけが対話する運用を要求しない。タスクごとの requester / worker 関係と sync / async の選択は [§7](04-handoff-and-requester.md#sec-7) に従う。

`orchestrator` は委譲や進行確認を担当できる Agent の role であり、Task Registry の状態遷移を実行する `herdr-orchestrator` CLI とは別である。Agent は Registry を直接編集しない。`tester` / `researcher` は初期標準の role には含めず、専任の委譲先が必要になった場合に追加する。

専任 role 追加前の委譲先は次に統一する。

| 依頼の種類（例）           | worker logical role                                           |
| -------------------------- | ------------------------------------------------------------- |
| コード・設計・仕様レビュー | `reviewer`                                                    |
| 実装・実装に伴うテスト     | `implementer`                                                 |
| 要件整理・設計判断・調査   | `design`                                                      |
| 委譲・進行確認（作業本体） | 該当 worker を [§7](04-handoff-and-requester.md#sec-7) で明示 |

「テスト依頼」「調査依頼」等の自然言語 handoff も、repository Skill / orchestrator が上表に従い worker role を決定する。`tester` / `researcher` への委譲は [§24](09-maintenance-and-roadmap.md#sec-24) の将来拡張まで設計・例示・動作確認から除外する。

<a id="sec-4-1"></a>

### 4.1 logical role と agent.name

live agent の `agent.name` は Herdr 公式の Agent routing target として扱い、原則として次の論理形式を使用する。`agent.name` は永続的な Agent identity とはみなさない。

```text
<project-slug>-<role>                    # 32 文字以内
<prefix>-<hash4>-<role>                  # 32 文字超（手順 6）
```

`<hash4>` は `context.repository.identity` の canonical 文字列（[§11.0](06-results-and-workflows.md#sec-11-0)）の SHA-256 先頭4文字（16進小文字）。`<prefix>` は正規化済み `project-slug` の先頭から **固定長**で切り出した部分（手順 6）。

Herdr の agent name 制約を必ず満たす。

```text
[a-z][a-z0-9_-]{0,31}
```

最大長は 32 文字とする。

`project-slug` は `context.repository.identity`（[§11.0](06-results-and-workflows.md#sec-11-0) の canonical 文字列。例: `git.example/repo`）から決定論的に生成する。

正規化規則（`context.repository.identity` の canonical 文字列から `project-slug` を生成）:

1. UTF-8 文字列として小文字化する。
2. `[a-z0-9_-]` 以外の各文字を `-` に置換する。
3. `-` または `_` が **1文字以上連続**する箇所を、単一の `-` に置換する（separator は `-` に統一する）。
4. 先頭および末尾の `-` を削除する。
5. 結果が空文字列のときは `project-slug = "repo"` とする。
6. 先頭文字が `[a-z]` でないとき（数字始まり等）は、先頭に **`r-` を1回だけ**付加する（例: `0123-foo` → `r-0123-foo`）。
7. `candidate = "<project-slug>-<role>"` とする。**32 文字まで**（`len(candidate) ≤ 32`）は通常形式 `agent.name = candidate` として手順 9 へ。
8. **33 文字以上**（`len(candidate) > 32`）のときのみ、次で **1 つの** 短縮名を決定する（切り取り位置は role 長に依存し、実装者の裁量で変えない）。
   - `hash4` = canonical `context.repository.identity` を UTF-8 で SHA-256 した **16進表現**（`hexdigest()`、64文字・小文字）の **先頭4文字**（Python `hashlib.sha256(...).hexdigest()[:4]` と同等）。
   - `prefix_len = 32 - len(<role>) - 6`（6 は `-` + `hash4` + `-` の3要素の合計文字数）。
   - `prefix` = 正規化済み `project-slug` の **先頭から `prefix_len` 文字**（`prefix_len ≤ 0` の場合は handoff / 命名を **中止**し、利用者へ報告する）。
   - `prefix` 末尾が `-` のときは末尾の `-` を削除してから連結する（`--` を避ける）。
   - `agent.name = "<prefix>-<hash4>-<role>"`（総長は construction 上 32 文字以下）。
9. 各 `(context.repository.identity, logical role)` から、手順 1–8 で **1 つの** `agent.name` のみを決定する。MVP では衝突時に hash 規則を調整したり、別の `agent.name` を生成したりしない。
10. Herdr 上で当該 `agent.name` が既に live Agent として存在する場合、[§11.1](06-results-and-workflows.md#sec-11-1) と同様に repository / worktree context を検証する。handoff 期待 context と一致すればその Agent を routing target として用いる。**一致しなければ配送を中止**し、利用者へ報告する（[§9.10](05-completion-and-recovery.md#sec-9-10)）。
11. 最終結果を Herdr の agent name regex で再検証する。不一致なら handoff / 命名を **中止**する。

例:

```text
logical role = reviewer
agent.name    = git-example-repo-reviewer
pane.label    = レビュワー
```

同じ repository identity から生成する他の初期標準 role の agent name は、`git-example-repo-design`、`git-example-repo-implementer`、`git-example-repo-orchestrator` となる。

長い repository 名の例（`role = reviewer` のとき `prefix_len = 32 - 8 - 6 = 18`）:

```text
agent.name = project-slug[0:18] + "-" + hash4 + "-reviewer"
```

（`hash4` は canonical `context.repository.identity` から算出。identity が変われば `hash4` も変わる。）

`hash4` は Python 標準ライブラリ `hashlib` 等で、同一 canonical identity から常に同じ値を生成する。

`pane.label` は人間向け UI 表示として扱う。

`reviewer` のような role 単独の `agent.name` は、複数 repository を同一 Herdr セッションで扱う場合に誤配送を起こし得るため、標準ルーティングでは使用しない。期待する routing target を安全に解決できない場合は別名・pane ID・role 単独名へ fallback せず、配送不可として扱う。

agent name が既存 live Agent と衝突した場合、名前一致だけを理由に無関係な Agent を再利用しない。repository / worktree identity を検証し、一致すればその live Agent を用いる。**一致しなければ worker 起動・handoff を中止**する。MVP では Orchestrator が別 `agent.name` を自動決定しない（Herdr が保証するのは live `agent.name` の一意性であり、衝突時の独自再命名規則は [§4.1](#sec-4-1) 手順 9–10 に存在しない）。

`agent.name` は Herdr 公式の live routing locator であり、repository / worktree 一致や同一 Agent 個体の永続的証明には使用しない。最終的な worker 選定は [§11](06-results-and-workflows.md#sec-11) の Worker Resolution に従う。`agent_session_id`、`terminal_id` 等を本来の用途から転用して独自 routing ID として使用しない。

---

<a id="sec-4-2"></a>

### 4.2 Routing Identity Policy

Agent 間配送は Herdr 公式の Agent routing surface を使用する。

標準 routing target は Herdr が認識している live `agent.name` とする。

次を独自 routing ID として使用しない。

```text
pane_id
terminal_id
agent_session_id
```

役割分担:

```text
task.id
  = durable task identity

agent.name
  = Herdr 公式の live routing locator
  = 永続 identity ではない

pane_id
  = terminal location / diagnostic metadata

terminal_id
  = terminal identity / diagnostic metadata

agent_session_id
  = native agent session resume 用
  = Orchestrator routing には使用しない
```

`agent.name` は Agent 終了・置換後に再利用され得るため、名前一致だけで「依頼時と同じ Agent 個体」と断定しない。

callback / handoff 前には、現在解決された live Agent が Task Envelope の期待 context と明らかに矛盾していないことを確認する。確認と `agent prompt` 送信の間に Agent が変わり得るため、直前再確認と受信側 Registry 検証は [§11.4](06-results-and-workflows.md#sec-11-4) に従う。

確認候補:

```text
repository
worktree
agent kind
cwd / foreground cwd
```

これらは immutable identity ではなく sanity check として扱う。

十分に安全確認できない場合は配送しない。

```text
completion.status = skipped
completion.reason = requester_unavailable
または
completion.reason = requester_context_mismatch
```

別の Agent name、古い pane ID、terminal ID、agent_session_id へ自動 fallback してはならない。

<a id="sec-5"></a>

## 5. Canonical Task Contract

すべての handoff は sync / async / parallel を問わず同一の canonical schema と Task Registry を使用する。`task.id` は Orchestrator が生成し、依頼・dispatch・result・completion・ログを同一 task として対応付ける。

Task Envelope、Task Registry、CLI 出力、completion prompt、Result Contract は [§6.2](03-task-registry.md#sec-6-2) の canonical schema の語彙を使用する。Registry JSON では旧 flat 名 `task_id` / `task_type` / `task_status` / `result_ref` を使用せず、次へ統一する。

```text
task.id
task.type
task.mode
task.status
result.status
result.ref
result.summary
```

workflow が branch / commit 固定を要求する場合のみ `context.worktree.branch` / `context.worktree.commit` を照合する。未 commit diff を扱う review 等では commit 一致を必須にしない。

<a id="sec-5-1"></a>

### 5.1 `task.id`

`task.id` は Orchestrator が生成し、外部から任意文字列を指定させない。

標準形式:

```text
task_<UUIDv4>
```

受け入れる ID は次へ限定する。

```text
^task_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$
```

Ubuntu 22.04 標準 `python3` と Python 標準ライブラリ `uuid.uuid4()` で生成する。Registry path を生成する前に ID を検証し、resolve 後も Registry root 配下であることを確認する。

`task.id` は sync / async / parallel の全 handoff で必須とする。

---
