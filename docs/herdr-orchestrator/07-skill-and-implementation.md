# 既存実装整理・Skill 構成・承認設計・中央管理

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-13"></a>

## 13. 既存実装の整理

<a id="sec-13-1"></a>

### 13.1 公式 Skill へ委譲するもの

現在の処理 新しい扱い

---

Herdr 環境判定 公式 Skill
agent list 公式 Skill
pane list 公式 Skill
agent get 公式 Skill
agent prompt 公式 Skill
agent wait 公式 Skill
agent read 公式 Skill
blocked 判定 公式 Skill
timeout / stalled 処理 公式 Skill
pane split 公式 Skill
agent start 公式 Skill

<a id="sec-13-2"></a>

### 13.2 中央 Orchestrator へ移すもの

- design role
- reviewer role
- implementer role
- orchestrator role
- role routing
- sync / async handoff 規約
- async callback 規約
- requester / worker 関係

<a id="sec-13-3"></a>

### 13.3 Repository に残すもの

- Review Scope
- RNNN
- INDEX
- Finding
- Follow-up
- Review Status
- lint
- build
- format
- Review Prompt
- Review Response
- プロジェクト固有レビュー基準

<a id="sec-13-4"></a>

### 13.4 廃止候補

以下は中央方式への移行後、通常レビューでは不要になる。

```text
ensure-reviewee-main.sh
reviewee_target=main
mainへのrename
通常レビュー完了時の「レビュー対応して」callback
```

async handoff 用 callback の考え方自体は残す。

---

<a id="sec-14"></a>

## 14. Skill 構成

初期構成:

```text
herdr-orchestrator/
├── SKILL.md
├── scripts/
│   └── task-registry.py
└── references/
    ├── roles.md
    └── handoff.md
```

<a id="sec-14-1"></a>

### 14.1 SKILL.md

`SKILL.md` は control plane として小さく保つ。

記載対象:

- Herdr 公式 Skill を利用すること
- role routing
- sync / async 選択
- handoff 原則
- callback 原則
- repository 固有処理との責務境界

Herdr CLI コマンド一覧は記載しない。

<a id="sec-14-2"></a>

### 14.2 scripts

`herdr-orchestrator` wrapper は固定 orchestration operation を実装し、必要な範囲で Herdr の**公式 CLI / API を直接呼び出す**。公式 Herdr Skill は script から呼び出す実行ライブラリではなく、Agent が Herdr を安全に扱うための公式手順・意味論として参照する。

責務境界:

```text
Official Herdr Skill
  = Agent向けの公式操作ルール・安全前提・現在の意味論

herdr-orchestrator Skill
  = role / task contract / handoff policy / workflow guidance

herdr-orchestrator wrapper
  = HERDR_ENV guard / validation / official CLI/API call / Registry transaction
```

wrapper は公式前提を迂回してはならない。少なくとも Herdr 操作前に `HERDR_ENV=1` を必須確認し、未設定時は Herdr CLI/API を呼ばず失敗させる。

Herdr CLI の仕様自体は公式 Herdr Skill を正とする。

一方、以下のような決定的・反復的な処理は script / CLI へ集約する。

- Task Registry の競合制御
- 状態遷移
- 原子的更新
- repository / worktree affinity の定型確認
- completion claim / recovery
- 承認対象コマンドを安定させるための定型 orchestration 操作

初期必須 script:

```text
scripts/task-registry.py
scripts/herdr-orchestrator
```

`task-registry.py` 内部 operation（Registry JSON 更新。**handoff flock は wrapper プロセスが保持** — §6.6）:

```text
create
get <task.id>
record-prompt-start <task.id>
assert-handoff-continuable <task.id>
mark-dispatch <task.id>
reconcile-dispatch <task.id>
update-result <task.id>
claim-completion <task.id>
finish-completion <task.id> <sent|failed|uncertain|skipped>
inspect-completion <task.id>
reset-completion <task.id>
cleanup
```

これらは `herdr-orchestrator` public CLI の内部 operation であり、Agent 向けの任意 state mutation surface として公開しない。

Task Registry 要件:

- `create` が UUIDv4 の `task.id` を内部生成する
- 外部入力の任意 `task.id` で新規 task を作成しない
- 1 task = 1 JSON
- file lock を使用する
- JSON更新は temporary file + atomic replace を使用する
- `pending -> processing` を原子的に claim する
- `processing` を検出した場合は自動再送しない
- `reset-completion` 実行時は recovery 履歴を記録する
- cleanup は [§6.8](03-task-registry.md#sec-6-8) の条件（task terminal かつ completion terminal）を満たす task のみ対象とする
- `update-result` は `task.status ∈ {in_progress, unknown}` かつ `dispatch.status ≠ pending` のみ受理する
- `handoff` は [§6.11](03-task-registry.md#sec-6-11) の順序で live worker 解決 → create → handoff flock → record-prompt-start → assert → prompt（sync は --wait なし）→ mark-dispatch → sync は §6.14 result 確定 poll を実行する
- `reconcile-dispatch` と `doctor` の stale dispatch 報告（§6.12 A: prompt 未開始 / B: prompt 後）を実装する
- `record-prompt-start` / `mark-dispatch` / `reconcile-dispatch` は §6.13 の lock 下 compare-and-set precondition を実装する
- `herdr-orchestrator handoff` wrapper が `handoff.lock` fd を flock 保持（§6.6。短命子プロセス都度 acquire 禁止）し、assert→prompt クリティカル区間を実装する
- `reconcile-dispatch` は同一 `handoff.lock` の非ブロッキング flock 取得成功時のみ Registry 更新する
- handoff は `record-prompt-start` / `assert-handoff-continuable` 拒否時に `agent prompt` を呼ばない
- handoff / `resume_requester` の `agent prompt` 本文は [§11.4](06-results-and-workflows.md#sec-11-4) の最小 Task Envelope（`task.id` + Registry 読取指示）に限定する
- `finish-completion` / requester 再開は [§9.3](05-completion-and-recovery.md#sec-9-3) 手順 6 の直前再確認を実装する
- `reconcile-dispatch --dispatch-outcome failed` は `--dispatch-reason` 必須（§6.12）
- 中央 `herdr-orchestrator` Skill と各 repository Skill は worker / requester の [§11.4](06-results-and-workflows.md#sec-11-4) 受信側 Registry 検証を必須手順として記載する（worker は `dispatch.status=pending` を即拒否せず §11.4 4a と同型の bounded `task-status` poll を実装する）
- sync handoff は §6.14 の result 確定 poll を実装する（`agent wait` のみを正常終了条件にしない）
- `claim-completion` は §9.13 eligibility を `result-submit` / `resume-task` / `recover-completion` で共通適用する
- `reset-completion` は §9.14、`recover-completion` は reset 後に §9.13 を適用する
- public `reset-completion` / `recover-completion` を実装する
- live Agent 解決・context 照合は `orchestrator/live_agent.py`（`agent get` のみ。段階 4 で handoff / submit から呼ぶ）
- `result-submit` / worker 選定は §11.0（`git -C <foreground_cwd> rev-parse --show-toplevel`）で worktree root を解決してから Registry と照合する
- repository identity は §11.0 の正規化関数を handoff `create` と submit で共有する（出力は `host/path` または `host:port/path` の canonical 文字列のみ、origin fetch URL 全件、全 remote 名の identity 一致、HTTPS userinfo/query/fragment 除去、scp/HTTPS port 443 / `ssh://` port 22 相当の同一視、**非標準 HTTPS / SSH port は `host:port/path` に含める**）
- `result-submit` は §10.3 worker submit context と terminal result の idempotent replay を実装する
- `result-submit` 成功後、`completion.action=resume_requester` なら completion claim/finalize を best-effort 連鎖する
- `dispatch.status=pending` 拒否時は `dispatch_not_ready`（retryable）を返し、[§10.2](06-results-and-workflows.md#sec-10-2) の worker bounded retry を repository Skill で必須化する
- `reconcile-dispatch` 完了時、`result.status=pending` なら result 再登録を CLI 出力へ含める
- `result.ref` が非 null の場合、resolve 後 path が `context.worktree.path` 配下か検証する
- Registry / task / lock の symlink を拒否する
- Registry permission を 0700 / 0600 基準で管理する

`herdr-orchestrator` は Agent が日常的に呼び出す安定した command surface とする。
内部では Herdr 公式 Skill / CLI の規約に従い、必要な Herdr 操作と Task Registry 操作をまとめる。

例:

```text
herdr-orchestrator handoff ...
herdr-orchestrator task-status <task.id>
herdr-orchestrator resume-task <task.id>
herdr-orchestrator reset-completion <task.id>
herdr-orchestrator recover-completion <task.id>
herdr-orchestrator reconcile-dispatch <task.id>
herdr-orchestrator doctor
```

この wrapper は Herdr の独自互換レイヤーを作る目的ではない。
Herdr CLI の意味や状態モデルを再定義せず、承認頻度低減と定型処理の再現性向上を目的とする。

<a id="sec-14-3"></a>

### 14.3 Runtime Requirements

WSL / Linux の最低実行環境は Ubuntu 22.04 とする。

Orchestrator の補助 script は Ubuntu 22.04 標準の `python3`（Python 3.10 系）で実行可能であることを要件とする。

原則として Python 標準ライブラリのみを使用する。

想定利用:

```text
uuid
json
pathlib
os
re
datetime
hashlib
tempfile
fcntl
```

利用者へ次を必須要求しない。

```text
pip install
venv作成
npm package追加
追加Pythonライブラリのapt install
```

追加依存が必要になった場合は、標準ライブラリで代替できないことを設計レビューで確認してから導入する。

<a id="sec-15"></a>

## 15. Approval Minimization

<a id="sec-15-1"></a>

### 15.1 目的

Cursor / Codex / Antigravity などでは、Agent が terminal command を実行する際にユーザー承認を求める場合がある。

`herdr-orchestrator` は承認機構を回避しない。
代わりに、日常的な orchestration 操作を少数の安定した command surface へ集約し、各 Agent の許可設定で安全に扱いやすくする。

<a id="sec-15-2"></a>

### 15.2 原則

```text
Agent
  ↓
固定された herdr-orchestrator CLI
  ↓
Herdr CLI / Task Registry / repository check
```

原則:

1. Agent に長い shell pipeline や複雑な引数列を毎回生成させない。
2. 定型操作は `herdr-orchestrator` CLI に集約する。
3. CLI の command name / subcommand を安定させる。
4. 各 Agent の allowlist / approval policy は利用可能な範囲でユーザーグローバル設定として利用する。
5. approval bypass を動作要件にしない。
6. destructive operation は自動許可対象へ含めない。

<a id="sec-15-3"></a>

### 15.3 自動許可候補

環境側の権限機構が許す場合、次のような定型操作を allowlist 候補とする。

```text
herdr-orchestrator task-status
herdr-orchestrator task-result
herdr-orchestrator doctor
herdr-orchestrator handoff
```

ただし `handoff` は読み取り専用操作ではない。
別 Agent への実行指示やレビュー成果物の保存を伴うため、無条件の allowlist 対象にしてはならない。

`handoff` を allowlist 候補とする場合は、少なくとも次を満たすこと。

- current repository / worktree と一致する
- 対象 logical role が許可済みである
- 任意 shell command を引数として受け取らない
- repository 外の任意 path を書き込み先として受け取らない
- destructive action を内部で実行しない
- request 内容が固定 workflow の範囲に収まる

実際の allowlist の記述方法は Cursor / Codex / Antigravity の現行権限モデルに従う。
中央 Skill が各製品のセキュリティ設定を強制変更しない。

<a id="sec-15-4"></a>

### 15.4 自動許可しない操作

次は原則として明示承認を残す。

- Agent / pane / workspace の強制終了・削除
- 任意 shell command 実行
- 任意 path への書き込み
- branch 削除、reset、clean 等の destructive Git operation
- repository 外への任意ファイル削除
- permission / sandbox の変更
- 任意の `agent send-keys`
- blocked Agent への承認・質問応答を自動化する操作
- preview channel への変更や Herdr 自動更新

<a id="sec-15-5"></a>

### 15.5 セキュリティ境界

`herdr-orchestrator` CLI は「任意のshell commandを渡せるwrapper」にしない。

悪い例:

```text
herdr-orchestrator exec "<arbitrary command>"
```

良い例:

```text
herdr-orchestrator handoff ...
herdr-orchestrator task-status ...
herdr-orchestrator resume-task ...
```

引数は task ID、role、既知の mode など必要最小限に限定し、repository / worktree / Agent state の安全確認を内部で行う。

<a id="sec-15-6"></a>

### 15.6 製品差異

承認モデルは Agent 製品ごとに異なるため、中央設計の責務は「承認回避」ではなく次に限定する。

- 呼び出す command 数を減らす
- command prefix を安定させる
- 非破壊操作と破壊操作を分離する
- allowlist へ安全に登録できる粒度を提供する

各製品固有の allowlist / sandbox / approval 設定は導入手順側で管理する。

<a id="sec-15-7"></a>

### 15.7 Herdr 公式 Skill との関係

Herdr 公式 Skill / CLI は Herdr の操作仕様・状態モデルの正とする。

`herdr-orchestrator` CLI は、その仕様に従った定型 workflow を実行する wrapper であり、Herdr CLI の互換レイヤーや独自代替実装ではない。

```text
Herdr公式 Skill / CLI
  = Herdr操作仕様の正

herdr-orchestrator CLI
  = 定型workflowの安定した実行面
```

---

<a id="sec-16"></a>

## 16. 中央管理

構成例:

```text
agent-skills/
├── README.md
├── skills/
│   └── herdr-orchestrator/
│       ├── SKILL.md
│       ├── scripts/
│       │   ├── herdr-orchestrator
│       │   └── task-registry.py
│       └── references/
│           ├── roles.md
│           └── handoff.md
└── install/
    └── setup.sh
```

Herdr 公式 Skill 自体は中央リポジトリへコピーしない。

---
