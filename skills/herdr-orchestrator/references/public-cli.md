# Herdr Orchestrator — CLI リファレンス（開発者・Agent 向け別紙）

正本: [§6.5](../../../docs/herdr-orchestrator/03-task-registry.md#sec-6-5)、[§14.2](../../../docs/herdr-orchestrator/07-skill-and-implementation.md#sec-14-2)

コマンドラインツール `herdr-orchestrator` の詳細なコマンド仕様、引数・オプション、およびターミナルからの直接操作手順です。日常の利用で AI Agent に自然言語で指示を出す場合は [README.md](../../../README.md) を参照してください。

リポジトリ内入口: `skills/herdr-orchestrator/scripts/herdr-orchestrator`  
インストール後リンク: `~/.local/bin/herdr-orchestrator`

---

## 1. コマンド一覧

| コマンド                                                  | 説明                                                                                                                   | `HERDR_ENV=1` 制約 |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------ |
| `doctor`                                                  | 環境健全性診断、古い Role Binding 検出、stale dispatch 候補検出                                                        | 必須               |
| `identity init`                                           | Git リポジトリ固有の UUID（Repository Identity）を初期化                                                               | 免除（ペイン外可） |
| `identity show`                                           | 現在のリポジトリ固有 UUID を表示                                                                                       | 免除（ペイン外可） |
| `role set <role> [<pane_id>]`                             | ロールとペインを紐づけ（`--global`, `--label <name>`, `--pane <pane_id>`）                                             | 必須               |
| `role show`                                               | 登録済みロール一覧とペイン指紋メタデータを表示（`--global`）                                                           | 免除（ペイン外可） |
| `role clear [<role>\|--all]`                              | ロール登録を解除（`--global`）                                                                                         | 免除（ペイン外可） |
| `handoff`                                                 | タスクを作成して Worker へ委託（`--mode sync\|async`, `--to <role>`, `--instruction <text>`, `--scope local\|global`） | 必須               |
| `task-status <task_id>`                                   | タスクの進行状態と受信者照合（`recipient_verification`）を確認                                                         | 必須               |
| `task-list`                                               | Task Registry 内のタスク一覧を読み取り専用で取得（`--worktree`, `--status`, `--limit` 等）                             | 必須               |
| `result-submit <task_id>`                                 | Worker によるタスク結果の提出（`--result-status succeeded\|failed`, `--result-ref <path>`, `--summary <text>`）        | 必須               |
| `task-result <task_id>`                                   | Requester による結果確定データの取得・成果物パス解決                                                                   | 必須               |
| `resume-task` / `reset-completion` / `recover-completion` | completion 状態の再試行・復旧                                                                                          | 必須               |
| `reconcile-dispatch`                                      | 未確定 dispatch の整合性復旧                                                                                           | 必須               |
| `update`                                                  | 【未実装・将来仕様】配布環境の最新化（現行版の更新は `install.sh` を再実行。README『アップデート』参照）               | 免除（ペイン外可） |

※ Task Registry の JSON ファイル直接編集は禁止されています。すべての操作は本 CLI を介してアトミックに行われます。

---

## 2. 初期セットアップと環境診断

### 2.1 環境診断 (`doctor`)

Herdr セッションペイン内で実行し、環境変数や古い設定ファイルの有無を確認します。

```bash
herdr-orchestrator doctor
```

正常時は終了コード 0 で診断 JSON が出力されます。セッション外で実行した場合は `herdr_env: false` となり終了コード 1 が返ります。

### 2.2 リポジトリ識別子の初期化 (`identity init` / `identity show`)

Git リポジトリ固有の UUID を生成・設定します（Git 管理ディレクトリ内で `role set` を実行した際にも自動的に初期化されます）。

```bash
cd /path/to/repo
herdr-orchestrator identity init
herdr-orchestrator identity show
```

---

## 3. ロール管理 (`role`)

論理ロール名と Herdr の `pane_id`（例: `w15:p2`）を紐づけ、Herdr UI 上の表示名（ペインラベル）を連動させます。

### 3.1 ロールの登録 (`role set`)

```bash
# 自ペインを「メイン」として登録（herdr pane list で CWD を確認して --pane を明示することを推奨）
herdr-orchestrator role set メイン --pane w15:p1 --label "メイン"

# 特定のペイン（w15:p2）を「レビュワー」として登録
herdr-orchestrator role set レビュワー --pane w15:p2 --label "レビュワー"

# 英語ロール名で登録し、Herdr UI 上の表示名を日本語にする場合
herdr-orchestrator role set reviewer --pane w15:p2 --label "レビュワー"

# 別ワークスペースのペインをグローバル共通ロールとして登録する場合
herdr-orchestrator role set 調査担当 --pane w16:p1 --label "調査担当" --global
```

- `--pane <pane_id>`: 対象ペイン ID（位置引数 `<pane_id>` でも可）。省略時は環境変数 `HERDR_PANE_ID` を使用しますが、エージェントの常駐プロセス再利用等による環境変数残留を防ぐため、`herdr pane list` で CWD を確認して `--pane` を明示することを推奨します。
- `--global`: 全ワークスペース・全リポジトリ共通のロールとして登録します（`~/.local/state/herdr-orchestrator/roles/global.json` に保存）。
- `--label <display_name>`: Herdr UI のペイン表示名（タブ上のラベル）を個別に指定します（先頭の `--` は自動除去されます）。
- 出力 JSON には `label_sync`（同期成否・アクション）および `target.label_synced`（同期成功フラグ）が含まれます。

### 3.2 登録内容の確認 (`role show`)

```bash
# ローカルロール一覧
herdr-orchestrator role show

# グローバルロール一覧
herdr-orchestrator role show --global
```

### 3.3 ロールの解除 (`role clear`)

```bash
# 特定ロールを解除（他スコープに残存ロールがなければ Herdr UI ペイン名も --clear で解除）
herdr-orchestrator role clear レビュワー

# 全ロールを一括解除
herdr-orchestrator role clear --all
herdr-orchestrator role clear --all --global
```

※ **残存ロールの UI ラベル保護**: 解除対象のペインが別スコープ（ローカルまたはグローバル）で他の現役ロールを兼任している場合、UI ペイン表示名は `--clear` されず残存ロールの表示名が自動維持されます。どのスコープにもロールが存在しなくなった場合のみ `--clear` が実行されます。

---

## 4. タスク委託 (`handoff`)

Worker Agent へタスクを送信します。

```bash
# 1. 同期委託（Worker の完了まで待機して結果を受け取る）
herdr-orchestrator handoff \
  --mode sync \
  --to レビュワー \
  --task-type review \
  --instruction "PR #56 のコードレビューを実施してください。"

# 2. 非同期委託（即時復帰しタスク ID を取得する）
herdr-orchestrator handoff \
  --mode async \
  --to 調査担当 \
  --scope global \
  --instruction "最新のサーバーエラーログを解析してください。"
```

- `--mode sync|async`: 同期（完了待機）または非同期（即時復帰。既定値: `sync`）。
- `--to <role>`（または `--worker-role <role>`）: 登録済みロール名を指定（日本語名可）。
- `--task-type <type>`（または `--type <type>`）: タスクの種別（`review`, `research`, `implementation`, `general` 等。既定値: `general`）。
- `--scope local|global`: ローカルとグローバルで同名ロールが重複している場合にスコープを限定。
- `--instruction <text>`: Worker Agent への指示本文。
- `--result-wait-timeout-sec <seconds>`: 同期（`--mode sync`）実行時の完了待機タイムアウト秒数（既定値: 1800 秒 / 30分）。

---

## 5. タスク確認と結果操作

### 5.1 タスク状態と受信者照合 (`task-status`)

```bash
herdr-orchestrator task-status <task_id>
```

出力 JSON の `recipient_verification` には、現在の呼び出し元ペインが対象タスクの正当な受信者（Worker / Requester）かどうかの照合結果が含まれます。`verified: false` の場合は誤配送防止のため作業を着手してはなりません。

### 5.2 タスク一覧の参照とクラッシュ再起動後の状態確認手順 (`task-list`)

Task Registry 内のタスクを読み取り専用で一覧表示します。PC や Herdr の再起動・異常終了後に残存タスクを確認する際にも使用します（詳細は [task-list 設計書 §7.1](../../../docs/herdr-orchestrator-task-list.md#71-想定手順)）。

```bash
herdr-orchestrator task-list --worktree "$(git rev-parse --show-toplevel)"
```

- `--worktree <path>`: 対象 worktree 絶対パスで絞り込み。
- `--repository <uuid>`: 対象リポジトリ identity UUID で絞り込み。
- `--type <type>`: タスク種別で絞り込み。
- `--status <status>`: タスクステータスで絞り込み（`cancelled`, `created`, `failed`, `in_progress`, `succeeded`, `unknown`。複数指定時は OR）。
- `--limit <int>`: 返却するタスクの最大件数（1〜1000、既定値: 100）。
- `--json`: JSON 出力を明示（指定の有無に関わらず常に JSON 出力）。

#### クラッシュ再起動後の状態確認手順

1. `herdr-orchestrator task-list --worktree "$(git rev-parse --show-toplevel)"` を実行する（`HERDR_ENV=1` ペイン内）。
2. `exit = 0` かつ `complete=true` で正常取得。`exit = 3` の場合は一部ファイルが破損（`anomalies` を人間に報告）。
3. `truncated=true` の場合は最大件数で切り詰められているため、対象タスクが見当たらない場合でも `truncated=false` でない限り不存在と断定せず、`--limit` を増やすかフィルタ条件（`--status` 等）で絞り込んで再実行する。
4. 各タスクの `overview.warnings` を必ず確認。`inconsistent_state_combination` が付与されている場合は、`overview.state` が `closed` であっても正常完了とみなさず、状態不整合として人間に報告する。
5. `open` / `uncertain` や警告ありのタスクは `task-status <task-id>` で詳細を確認し、次の操作を判断する。

### 5.3 結果の提出 (`result-submit`)

作業を完了した Worker Agent が結果を登録します（`HERDR_ENV=1` かつ Worker ペインから実行）。

```bash
# 成果物ファイル（相対パス）と要約を提出
herdr-orchestrator result-submit <task_id> \
  --result-status succeeded \
  --result-ref "docs/reviews/internal/<instance-id>/RV001.md" \
  --summary "コードレビュー完了。指摘なし（GO）。"

# 成果物ファイルを作らず、要約のみで提出（クロスワークスペース調査等、2000文字以内）
herdr-orchestrator result-submit <task_id> \
  --result-status succeeded \
  --summary "調査完了。設定ファイルのタイムアウト値不足が原因でした。"
```

### 5.4 結果の読み取り (`task-result`)

Requester Agent が Worker の提出結果を取得します。

```bash
herdr-orchestrator task-result <task_id>
```

### 5.5 未確定 dispatch の復旧 (`reconcile-dispatch`)

handoff の途中停止等により `dispatch.status: pending` のまま残ったタスクを、`uncertain` または `failed` に確定させて安全に復旧します（詳細は [03-task-registry.md](../../../docs/herdr-orchestrator/03-task-registry.md#sec-6-12) を参照）。

```bash
herdr-orchestrator reconcile-dispatch <task_id> \
  --dispatch-outcome failed \
  --dispatch-reason "Worker ペインがビジー状態でタイムアウト"
```

- `--dispatch-outcome uncertain|failed`: dispatch 確定結果のステータス（未指定時はプロンプト未開始シナリオで `action: await_human` を返却。プロンプト送信開始後は指定必須）。
- `--dispatch-reason <text>`: dispatch 結果の理由または説明（`--dispatch-outcome failed` 指定時は必須）。

※ `task.status: created` かつ `dispatch.status: pending` のタスクのみが対象です。

---

## 6. Codex Agent 実行承認設定 (`codex execpolicy`)

Codex を利用する場合、自律実行の中断を防ぐために `~/.codex/rules/default.rules` へ許可ルールを追加します（テンプレート: `skills/herdr-orchestrator/install/codex-execpolicy-snippet.rules`）。

```python
# 読み取り・診断系（自動許可）
prefix_rule(pattern=["herdr-orchestrator", ["doctor", "task-status", "task-result", "task-list"]], decision="allow")
prefix_rule(pattern=["herdr-orchestrator", "identity", "show"], decision="allow")
prefix_rule(pattern=["herdr-orchestrator", "role", "show"], decision="allow")

# 結果提出（自動許可）
prefix_rule(pattern=["herdr-orchestrator", "result-submit"], decision="allow")
```

> [!IMPORTANT]
> **副作用を伴うコマンド（`role set`, `role clear`, `handoff`）の扱い**  
> `role set` や `handoff` は Role Binding の書き換えや UI 表示名変更、相手ペインへのプロンプト送信などの環境変更（副作用）を伴うため、公式テンプレートでは意図的に自動許可（`allow`）から除外されており、Codex 側の通常承認プロンプト（Human-in-the-loop）が表示されます（方針の詳細は [install/README.md](../install/README.md#21-コマンド分類と許可方針) を参照）。  
> ※ `role` コマンド全体を一括許可（`pattern=["herdr-orchestrator", "role"]`）すると、`role clear --all --global` まで無条件で自動実行されるリスクがあります。設定変更や委託は人間の承認を経て実行することが推奨されます。
