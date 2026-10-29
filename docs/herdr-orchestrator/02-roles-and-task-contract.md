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

### 4.1 logical role と routing target（Role Binding / agent.name）

Herdr 上の手動起動エージェントおよびサーバー再起動時に自動再開（`resume`）されたエージェントは既定で無名（`name` 未設定）であり、`pane_id`（例: `w15:p1`, `w15:p2`）を公式の Agent routing target として使用する。また、`herdr agent rename` で一時的な名前を付与した場合も、エージェントプロセスの終了・置換やサーバー再起動時には保持されない。

Herdr 公式 Skill の仕様上、エージェント操作コマンド（`herdr agent get` / `herdr agent prompt` 等）は live `agent.name` または現在そのエージェントをホストしている `pane_id` を target として受け付け、閉じたタブ・ペインの ID は同一セッション内で再利用されない。

そのため、`herdr-orchestrator` では **Role Binding（リポジトリごとの論理 role と `pane_id` の 1対1 マッピング）** を標準のルーティング解決手段とし、`herdr agent rename` によるエージェント名変更を不要とする。

- 保存先: `${XDG_STATE_HOME:-$HOME/.local/state}/herdr-orchestrator/roles/<repository_id>.json`
- 操作 CLI: `herdr-orchestrator role set <role> [--pane <pane_id>]`、`herdr-orchestrator role show`、`herdr-orchestrator role clear [<role>|--all]`
- **解決タイミングと不変性**: Role Binding を参照するのは **`handoff` 開始時の初期解決のみ** とする。Role Binding が登録されている場合、Orchestrator は `handoff` 時点で対象 role の `pane_id` を routing locator として解決し、Task Registry の `requester.agent_name`（および `requester.pane_id`）と `worker.agent_name`（および `worker.pane_id`）にその `pane_id` を保存する。後続の段階（`assert-handoff-continuable`、`result-submit` の [§10.3](06-results-and-workflows.md#sec-10-3) 提出元検証、`reconcile-dispatch`、`resume_requester` 等の completion）はすべて **Task Registry に保存された locator（`requester.agent_name` / `worker.agent_name`）のみ** を参照し、実行途中の Role Binding 再設定（`role set`）によって in-flight task の宛先や照合先が別ペインへ切り替わることはない。
- **受け入れるリスクと安全対策（Fail Closed）**:
  - 同一ペイン内でのエージェント終了・別 occupant 起動、または `herdr pane move` による新しい `pane_id` の採番、あるいはサーバー再起動時にレイアウト復元で `pane_id` が維持されない場合があり得る。これらに対しては、handoff・`result-submit`・completion の各段階で必ず `foreground_cwd` からの worktree root および `repository_identity` 照合（[§11.0](06-results-and-workflows.md#sec-11-0)）と受信側 Registry 検証（[§11.4](06-results-and-workflows.md#sec-11-4)）を実施し、対象不在または別 worktree / 別リポジトリによる context 不一致時は別ペインへ自動 fallback せず安全停止（`worker_unavailable` / `requester_unavailable` / `requester_context_mismatch`）する。なお、同一 worktree 内で複数ペインの担当 role（例: `implementer` と `reviewer` のペイン）を入れ替えた場合は worktree 照合だけでは区別できないため、ペイン配置変更時や再起動後は運用上 `herdr-orchestrator role show` / `role set` でバインディングを確認・更新する。
  - Role Binding は `repository_id`（UUID）単位で共有されるため（Pattern A 命名と同様、「同一リポジトリ内では 1 role につき 1 エージェント稼働」が基本前提）、同一リポジトリの複数 worktree を別ペインで切り替えて運用する場合は、作業対象 worktree のペインに対して `role set` を更新して使用する（別 worktree のペインがバインドされたままの場合は [§11.0](06-results-and-workflows.md#sec-11-0) の `worktree.path` 照合により誤配送が防止される）。

Role Binding が未登録の環境（または名前付き起動運用・レガシー環境）におけるフォールバックとして、live agent の `agent.name` は次の論理形式（または [Repository Identity UUID 化設計書](../herdr-orchestrator-identity-uuid.md#621-agent-名生成規則の確定パターン-a-r-hash16-role) の Pattern A `r-<hash16>-<role>`）を使用する。ただし、Pattern A 名で導出した role が Role Binding 上で別ペインにバインドされている混在状態では、曖昧性を排除するため handoff を拒否（`requester_context_unverified`）する。`agent.name` は永続的な Agent identity とはみなさない。

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

Agent 間配送は Herdr 公式の Agent routing surface（`herdr agent get <target>` / `herdr agent prompt <target>`）を使用する。

標準の routing locator は、`handoff` 開始時に [§4.1](#sec-4-1) の Role Binding から解決した `pane_id`（Role Binding 未設定時は Pattern A の live `agent.name`）とし、`create` 時に Task Registry の `requester.agent_name` / `worker.agent_name` として確定・保存する。

次を Orchestrator の routing ID に転用しない。

```text
terminal_id
agent_session_id
```

役割分担:

```text
task.id
  = durable task identity

requester.agent_name / worker.agent_name
  = handoff 時に確定・保存した Herdr 公式の live routing locator
    （Role Binding 利用時は pane_id、フォールバック時は live agent.name）
  = Agent 個体の永続 identity ではない

pane_id
  = terminal location / Role Binding 利用時の live routing target

terminal_id
  = terminal identity / diagnostic metadata

agent_session_id
  = native agent session resume 用
  = Orchestrator routing には使用しない
```

`pane_id` 上の occupant は終了・置換され得るほか、名前付き運用時の `agent.name` も再利用され得るため、locator 一致だけで「依頼時と同じ Agent 個体」と断定しない。

callback / handoff 前には、Task Registry に保存された locator（`requester.agent_name` / `worker.agent_name`）で現在解決された live Agent が、Task Envelope / Registry の期待 context と矛盾していないことを確認する。確認と `agent prompt` 送信の間に Agent が変わり得るため、直前再確認と受信側 Registry 検証は [§11.4](06-results-and-workflows.md#sec-11-4) に従う。

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

Task Registry に保存された locator（`requester.agent_name` / `worker.agent_name`）以外の別 Agent name、別 pane ID、現在の Role Binding 上の新しい pane ID、terminal ID、agent_session_id へ自動 fallback してはならない。

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
