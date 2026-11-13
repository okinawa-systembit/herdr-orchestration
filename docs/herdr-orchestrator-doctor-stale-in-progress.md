# Herdr Orchestrator `doctor` での stale `in_progress` タスク検出・要確認一覧（`await_human`）設計書

- **対象 Issue**: [Issue #174](https://github.com/okinawa-systembit/herdr-orchestration/issues/174)
- **ステータス**: 実装完了（PR #180 にてマージ済み）
- **関連文書**:
  - [03-task-registry.md](herdr-orchestrator/03-task-registry.md#sec-6-5): Public CLI Contract（`doctor`）および `stale_dispatch` 診断（§6.5 / §6.12）
  - [herdr-orchestrator-task-cancellation.md](herdr-orchestrator-task-cancellation.md): タスクキャンセル（`cancel-task`）および復旧ランブック（Issue #173）
  - [herdr-orchestrator-task-registry-cleanup.md](herdr-orchestrator-task-registry-cleanup.md#sec-4): `doctor` 可視化における完全 Read-Only・終了コード非影響原則（Issue #157 §4）
  - [herdr-orchestrator-task-list.md](herdr-orchestrator-task-list.md#44-派生分類-overviewstate): `task-list` による状態一覧との責務分離（§4.4）

---

<a id="sec-1"></a>

## 1. 背景と目的

<a id="sec-1-1"></a>

### 1.1 背景と課題

現行の `herdr-orchestrator doctor`（`dispatch_commands.py` の `run_doctor()` / `operations.py` の `find_stale_dispatch_candidates()`）は、`task.status == "created"` かつ `dispatch.status == "pending"` のみを検査対象としている。そのため、async handoff による配送完了後（`task.status ∈ {in_progress, unknown}` かつ `result.status == "pending"`）に、Worker ペインの終了、エージェント停止、別ワークツリーへの転用、または worktree 削除等によって途中停止し放置されたタスクが `doctor` で一切可視化されない。

[Issue #173](https://github.com/okinawa-systembit/herdr-orchestration/issues/173) により `cancel-task` と復旧ランブックが整備される一方で、Requester や人間が「どのタスクが Worker 喪失等で放置されているか」を安全に把握するための読み取り専用の診断入口が必要となっている。

<a id="sec-1-2"></a>

### 1.2 目的

本設計書は、[基本方針正本](core-principles.md) の原則 1（KISS 原則）、原則 2（人間中心の可観測性）、原則 6（単一正本）、指針 8（機械検査とエージェント判断の境界）、および指針 10（黙って失敗しない）に基づき、以下を実現することを目的とする。

1. 経過時間のみに頼る誤検知（正常な長時間実行タスクの誤認）を避け、**機械で確定できる事実（強い信号）を主軸**とした 2 段階信号で stale `in_progress` / `unknown` タスクを検出する。
2. `herdr-orchestrator doctor` の出力 JSON に読み取り専用の要確認一覧（`stale_in_progress` / `action: "await_human"`）を追加し、`cancel-task` や `result-submit --result-status unavailable` への判断材料を提示する。
3. [Task Registry クリーンアップ機構 設計書 §4.1](herdr-orchestrator-task-registry-cleanup.md#sec-4-1) と同様に、**完全 Read-Only・終了コード非影響**の契約を厳守する。

---

<a id="sec-2"></a>

## 2. 基本設計方針とスコープ境界

<a id="sec-2-1"></a>

### 2.1 採用方針

| 設計項目                       | 採用方針                                                                                                                                                                      |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **判定軸（2 段階信号）**       | **強い信号**（`worktree` 不在・Worker ペイン非生存）を主軸とし、経過時間は **弱い信号**（参考: 既定 24 時間）として明確に分離する（指針 8）                                   |
| **実行モードと副作用**         | 完全 Read-Only（Registry 書き込み・ロック作成・自動キャンセル・自動再試行は一切行わない）。`tasks/` 未作成時もディレクトリを作らず空一覧を返す                                |
| **終了コード**                 | `doctor` 既存の終了コード仕様（`HERDR_ENV=1` なら `0`、`HERDR_ENV≠1` なら `1`）を変更しない。要確認候補の有無や件数で終了コードを変えない                                     |
| **ロジック単一集約（原則 6）** | 検出ロジックは `registry/operations.py` の `find_stale_in_progress_candidates()` に 1 か所だけ定義し、`task-list` の `overview.warnings` 等へ同じ判定ロジックを重複実装しない |
| **Skill ドキュメント正本**     | Issue #174 本文では `references/doctor.md` への言及があるが、現行 Skill では `references/public-cli.md` §2.1 が `doctor` 説明の正本であるため、新設せず同節を更新する         |

<a id="sec-2-2"></a>

### 2.2 対象外（スコープ外）

1. 放置タスクの自動キャンセル（`cancel-task` 自動実行）および自動再試行（`handoff` 自動再送）。
2. Worker 死亡のリアルタイム監視やバックグラウンドポーリングデーモンの導入。
3. `task-list` の `overview.warnings` への判定ロジック重複実装。

---

<a id="sec-3"></a>

## 3. 検出ロジック（`find_stale_in_progress_candidates`）仕様

<a id="sec-3-1"></a>

### 3.1 対象タスクの事前抽出条件

`registry/operations.py` に `find_stale_in_progress_candidates(*, min_age_sec: float, live_panes: list[dict[str, Any]] | None = None, now: datetime | None = None) -> list[dict[str, Any]]` を新設する。

- **対象条件**: `task.status ∈ {"in_progress", "unknown"}` かつ `result.status == "pending"`。
- **除外・安全条件**:
  - `task.status == "created"`: 既存の `find_stale_dispatch_candidates()`（`stale_dispatch`）の責務であるため除外。
  - `task.status ∈ {"succeeded", "failed", "cancelled"}` または `result.status != "pending"`: 既に終端化または結果提出済みのため除外。
  - **完全 Read-Only・破損ファイル耐性**: `tasks_dir().exists()` が `False` の場合はディレクトリを作成せず空リストを返す。また、個別の破損 JSON 等により読込失敗したファイルがある場合は `doctor` 全体をクラッシュさせずスキップする（本 Issue のスコープで、既存の `find_stale_dispatch_candidates()` も `inspect_cleanup_status()` と同様に `tasks_dir().exists()` 分岐と破損ファイルスキップへ揃える）。

<a id="sec-3-2"></a>

### 3.2 2 段階信号の判定基準

#### A. 強い信号（確定事実: `signal_strength = "strong"`）

以下のいずれかに該当する場合、`timestamps.updated_at` からの経過時間にかかわらず即座に `signal_strength = "strong"` として検出する。

- **Worker 作業パスの解決基準（跨ぎ配送対応）**:
  ワークスペース跨ぎ配送・プロジェクト跨ぎ配送（Issue #74 / #151）では、`context.worktree.path` は Requester 側の worktree パスであり、Worker 側の作業場所は `worker.cwd` に記録される。そのため、`result-submit` の本人確認（`verify_worker_submit_context`）と同様に **`target_worker_path = worker.cwd or context.worktree.path`** の優先順で Worker の作業場所を決定する。Requester 側の `context.worktree.path` のみが失われた場合は強い信号にせず、参考フィールド `worktree_exists` のみに出力する。

| 理由コード                    | 判定条件                                                                                                                                                                     | 判定可能環境                    |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| `worktree_missing`            | `target_worker_path`（`worker.cwd` 優先、未設定時は `context.worktree.path`）が非空文字列で設定されており、かつディレクトリとして存在しない（`not Path(...).is_dir()`）      | 常時（`HERDR_ENV` 不要）        |
| `worker_pane_missing`         | `live_panes is not None` かつ、タスクに記録された Worker ペインが `live_panes` に存在しない                                                                                  | `HERDR_ENV=1`（`pane list` 可） |
| `worker_agent_not_running`    | `live_panes is not None` かつ、該当ペインは存在するが `not live_pane.get("agent") or live_pane.get("agent_status") == "none"`（シェル待機状態）                              | `HERDR_ENV=1`（`pane list` 可） |
| `worker_pane_context_changed` | `live_panes is not None` かつ、該当ペインにエージェントが稼働中だが `live_pane.get("cwd")` が存在し、`target_worker_path` と一致せず、かつその配下にも属さない（別用途転用） | `HERDR_ENV=1`（`pane list` 可） |

- **Worker ペインの照合ルール（`live_panes` との突合）**:
  - `run_doctor()` は `live_agent.list_live_panes_with_ui()`（内部で `pane list` / `workspace list` / `tab list` をタスク数に依存しない固定回数・最大 3 回のみ呼び出す既存ヘルパー）を呼び出し、その戻り値 `list[dict[str, Any]] | None` を `find_stale_in_progress_candidates()` へ渡す（タスクごとの N+1 呼び出しを行わない）。
  - 照合キーは原則 `worker.pane_id` とし、`live_pane.get("pane_id") == worker_pane_id` で検索する。後方互換のため `worker.pane_id` が未設定で `worker.agent_name` がペイン ID 形式（`^w[0-9A-Za-z]+:p[0-9A-Za-z]+$`）の場合はそれを `pane_id` として照合し、それ以外の旧形式名（Pattern A 等）は `live_pane.get("name") == worker_agent_name` で照合する。
  - **偽陽性防止（`live_panes is None` の扱い）**: `HERDR_ENV != "1"` または `herdr pane list` 非対応環境で `live_panes is None` の場合は、ペイン生存確認を実行せず `worker_pane_status = "unavailable"` と記録する（ペイン不在と誤認しない）。
  - **サブディレクトリ・worktree 内移動の許容**: `worker_pane_context_changed` の判定では `is_path_within_worktree(live_cwd, Path(target_worker_path).resolve())`（既存ヘルパー）を併用し、`target_worker_path` 配下のサブディレクトリへの移動は転用とみなさない。さらに、`worker.cwd` と `context.worktree.path` の双方が設定されており、かつ `is_path_within_worktree(Path(worker.cwd).resolve(), Path(context.worktree.path).resolve())` が真（＝同一 worktree 内の配送）である場合は、`worker.cwd` がサブディレクトリであっても `is_path_within_worktree(live_cwd, Path(context.worktree.path).resolve())` が真であれば worktree ルート等への移動を転用とみなさない（跨ぎ配送で Worker が誤って Requester 側 worktree へ移動した場合は転用として検出する）。

#### B. 弱い信号（参考: `signal_strength = "weak"`）

- 上記の強い信号が **0 件** であり、かつ `age_sec = (now - updated_at).total_seconds() >= min_age_sec` を満たす場合、`signal_strength = "weak"`、`reasons = ["updated_at_stale"]` として参考一覧に含める。
- 強い信号に該当するタスクが同時に `age_sec >= min_age_sec` を満たす場合、`signal_strength` は `"strong"` とし、`reasons` に `"updated_at_stale"` を併記して経過時間も観測できるようにする。
- **正常な長時間タスクの扱い**: Worker の作業中は `timestamps.updated_at` が更新されない（`task-status` は読み取り専用）ため、`updated_at_stale` は実質的に「配送（または最終更新）からの経過時間」を表し、24 時間を超える正常な長時間タスクも `weak` 候補に入る。そのため、`weak` 候補はあくまで確認用参考情報とし、自動キャンセルやエラー扱いはしない。また、`age_sec < min_age_sec` かつ強い信号 0 件のタスクは **候補に含めない**。

<a id="sec-3-3"></a>

### 3.3 閾値と環境変数

`dispatch_commands.py` の `stale_dispatch_threshold_sec()` と同じ設計パターンで以下を定義する。

- 定数: `DEFAULT_STALE_IN_PROGRESS_SEC = 86400.0`（24 時間）
- 関数: `stale_in_progress_threshold_sec() -> float`
- 環境変数: `HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC`（未設定・空文字時は `DEFAULT_STALE_IN_PROGRESS_SEC` を返す。非数値・`NaN`/`inf`・負数が指定された場合は `stderr` へ警告を出力し、`DEFAULT_STALE_IN_PROGRESS_SEC` へフォールバックする（終了コード非影響かつ黙って失敗しない原則の維持））

---

<a id="sec-4"></a>

## 4. `doctor` 出力スキーマと `cancel-task` 連携

<a id="sec-4-1"></a>

### 4.1 出力 JSON スキーマ（`stale_in_progress`）

`run_doctor()` の返却 JSON にトップレベルキー `"stale_in_progress"` を追加する。

```json
{
  "command": "doctor",
  "stage": 5,
  "herdr_env": true,
  "stale_dispatch": { "...": "..." },
  "stale_in_progress": {
    "action": "await_human",
    "threshold_sec": 86400.0,
    "pane_check_available": true,
    "summary": {
      "total_candidates": 2,
      "strong_signal_count": 1,
      "weak_signal_count": 1
    },
    "candidates": [
      {
        "task_id": "task_33333333-3333-4333-8333-333333333333",
        "task_status": "in_progress",
        "dispatch_status": "sent",
        "result_status": "pending",
        "signal_strength": "strong",
        "reasons": ["worktree_missing", "worker_pane_missing"],
        "updated_at": "2026-10-19T01:10:05Z",
        "age_sec": 2160000.0,
        "repository_identity": "26797854-8826-4acf-a5ad-4cea9caaf947",
        "worktree_path": "/tmp/tmp123/repo",
        "worktree_exists": false,
        "worker_role": "review",
        "worker_cwd": "/tmp/tmp123/repo",
        "worker_cwd_exists": false,
        "worker_pane_id": "w15:p2",
        "worker_agent_name": "w15:p2",
        "worker_pane_status": "missing",
        "suggested_action": "cancel-task"
      }
    ],
    "guidance": [
      "strong_signal のタスクは Worker ペインまたは worktree が失われているため、事実確認の上で `herdr-orchestrator cancel-task <task_id> --reason \"<理由>\"` による終端化、または同一ペイン生存時は `result-submit --result-status unavailable` を検討してください（Issue #173）。",
      "weak_signal のみのタスクは正常な長時間実行の可能性があるため、自動キャンセルせず `herdr-orchestrator task-status <task_id>` や対象ペインの状態を確認してください。"
    ]
  },
  "outdated_role_bindings": [],
  "registry_cleanup": { "...": "..." }
}
```

<a id="sec-4-2"></a>

### 4.2 候補フィールド定義と `suggested_action` 規則

| フィールド            | 型                             | 説明                                                                                                     |
| --------------------- | ------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `task_id`             | `string`                       | 対象タスク ID                                                                                            |
| `task_status`         | `"in_progress"` \| `"unknown"` | 現在の `task.status`                                                                                     |
| `dispatch_status`     | `string`                       | 現在の `dispatch.status`                                                                                 |
| `result_status`       | `"pending"`                    | 現在の `result.status`                                                                                   |
| `signal_strength`     | `"strong"` \| `"weak"`         | 強い信号が 1 件以上あれば `"strong"`、時間超過のみなら `"weak"`                                          |
| `reasons`             | `list[string]`                 | 該当した信号コードの配列（§3.2 の理由コードおよび `"updated_at_stale"`）                                 |
| `updated_at`          | `string`                       | `timestamps.updated_at`（ISO 8601）                                                                      |
| `age_sec`             | `number`                       | 現在時刻との経過秒数（小数点以下 3 桁丸め）                                                              |
| `repository_identity` | `string`                       | `context.repository.identity`                                                                            |
| `worktree_path`       | `string` \| `null`             | `context.worktree.path`（参考: Requester 側 worktree パス）                                              |
| `worktree_exists`     | `boolean` \| `null`            | `worktree_path` のディレクトリ存在有無（未設定時は `null`）                                              |
| `worker_role`         | `string` \| `null`             | `worker.role`                                                                                            |
| `worker_cwd`          | `string` \| `null`             | `worktree_missing` / `context_changed` 判定に用いたパス（`worker.cwd` 優先、未設定時は `worktree_path`） |
| `worker_cwd_exists`   | `boolean` \| `null`            | `worker_cwd` のディレクトリ存在有無（未設定時は `null`）                                                 |
| `worker_pane_id`      | `string` \| `null`             | `worker.pane_id`                                                                                         |
| `worker_agent_name`   | `string` \| `null`             | `worker.agent_name`                                                                                      |
| `worker_pane_status`  | `string`                       | `"live"` / `"missing"` / `"agent_not_running"` / `"context_changed"` / `"unavailable"`                   |
| `suggested_action`    | `string`                       | 推奨アクション（下表参照。実際の実行判断は人間または Requester が行う）                                  |

`suggested_action` の決定規則（上から順に評価）:

1. `reasons` に `worktree_missing` / `worker_pane_missing` / `worker_pane_context_changed` のいずれかを含む → `"cancel-task"`
2. `reasons` に `worker_agent_not_running` を含む（ペインと worktree は生存） → `"recover_or_cancel"`（同一ペインでの再起動・`result-submit --result-status unavailable` または不能時 `cancel-task`）
3. 上記以外（`updated_at_stale` のみの `weak`） → `"verify_live_task"`

---

<a id="sec-5"></a>

## 5. 実装・検証計画（テスト計画 DT-01〜DT-08）

<a id="sec-5-1"></a>

### 5.1 変更対象ファイル一覧

| 対象ファイル                                                             | 変更内容                                                                                                                                                                                                     |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`  | `find_stale_in_progress_candidates()` の実装（強い信号 4 種・弱い信号 1 種、Read-Only 安全保護）、および既存の `find_stale_dispatch_candidates()` への `tasks_dir().exists()` 分岐・破損ファイルスキップ追加 |
| `skills/herdr-orchestrator/scripts/orchestrator/dispatch_commands.py`    | `DEFAULT_STALE_IN_PROGRESS_SEC` / `stale_in_progress_threshold_sec()` 追加、`run_doctor()` へ `stale_in_progress` 統合                                                                                       |
| `skills/herdr-orchestrator/SKILL.md`                                     | Public CLI 説明の `doctor` 診断項目に `stale_in_progress` を追記（D-2）                                                                                                                                      |
| `skills/herdr-orchestrator/references/public-cli.md`                     | §2.1 `doctor` に `stale_in_progress`・`await_human`・`HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC` を追記（D-2）                                                                                                |
| `skills/herdr-orchestrator/references/handoff.md`                        | Recovery Runbook の確認ステップに `herdr-orchestrator doctor`（`stale_in_progress`）確認を補助導線として追記                                                                                                 |
| `docs/herdr-orchestrator/03-task-registry.md`                            | Public CLI Contract テーブルの `doctor` 行へ `stale_in_progress`（Issue #174）参照を追記                                                                                                                     |
| `docs/herdr-orchestrator-doctor-stale-in-progress.md`                    | 実装 PR（`Closes #174`）作成時にヘッダーの `- **ステータス**:` を `実装完了（PR #NN にてマージ済み）` へ更新（D-1 必須）                                                                                     |
| `tests/test_orchestrator_phase5_dispatch.py`（または専用テストファイル） | 下記 §5.2 のテストケース（DT-01〜DT-08）を追加                                                                                                                                                               |

<a id="sec-5-2"></a>

### 5.2 テスト計画（DT-01〜DT-08）

指針 10（「テストは引数変換だけでなく実際の結果を検証する」）に基づき、実装時に以下の自動テストを追加・検証する。

| テスト ID | 検証項目                                            | テスト内容と期待される結果                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **DT-01** | `worktree_missing`・跨ぎ配送誤検知防止              | `task.status ∈ {in_progress, unknown}` かつ `result.status = pending` で `worker.cwd`（未設定時 `context.worktree.path`）が存在しない場合、経過時間に関わらず `signal_strength = "strong"`（`worktree_missing`、`suggested_action = "cancel-task"`）で検出されること。また跨ぎ配送（`worker.cwd != context.worktree.path`）で Requester 側 `context.worktree.path` のみが不在でも `worker.cwd` が存在すれば `worktree_missing` と誤検知しないこと |
| **DT-02** | Worker ペイン非生存・跨ぎ/同一 worktree 移動許容    | `HERDR_ENV=1` 下で fake `herdr pane list` を用い、ペイン不在（`worker_pane_missing`）、エージェント未起動（`worker_agent_not_running`）、別ディレクトリ転用（`worker_pane_context_changed`）をそれぞれ検出し、跨ぎ配送で `worker.cwd` 配下サブディレクトリへ移動した場合や同一 worktree 内配送で `worker.cwd`（サブディレクトリ）から worktree ルートへ移動した場合は `worker_pane_context_changed` と誤検知しないこと                            |
| **DT-03** | `HERDR_ENV != 1` / `pane list` 非対応フォールバック | `pane_check_available = false` かつ `worker_pane_status = "unavailable"` となり、ペイン不在を誤って `worker_pane_missing` と判定しないこと（`worktree_missing` 等は引き続き検出）                                                                                                                                                                                                                                                                 |
| **DT-04** | 閾値境界値・環境変数上書き                          | `HERDR_ORCHESTRATOR_STALE_IN_PROGRESS_SEC` 設定時、`age_sec < threshold` は非検出、`age_sec >= threshold` は `signal_strength = "weak"`, `reasons = ["updated_at_stale"]` として検出されること                                                                                                                                                                                                                                                    |
| **DT-05** | 正常長時間タスクの非誤検知                          | `age_sec` が長時間（例: 1 時間、既定 24 時間未満）であっても、`worker.cwd` 存在かつ Worker ペインが live であれば `stale_in_progress.candidates` に含まれないこと                                                                                                                                                                                                                                                                                 |
| **DT-06** | 対象外ステータスの除外                              | `created`（`stale_dispatch` 担当）、`succeeded` / `failed` / `cancelled`、または `result.status != "pending"` のタスクが `stale_in_progress.candidates` に含まれないこと                                                                                                                                                                                                                                                                          |
| **DT-07** | `doctor` 全体の完全 Read-Only・破損耐性・終了コード | 空の `XDG_STATE_HOME`（`tasks/` 未作成）で `run_doctor()` を実行しても `tasks/` ディレクトリが作成されず空候補を返すこと、破損 JSON をスキップすること、候補が存在しても `HERDR_ENV=1` で exit 0（`HERDR_ENV!=1` で exit 1）が変わらないこと、タスク JSON やロックファイルが変更されないこと                                                                                                                                                      |
| **DT-08** | 終端化後の候補解消                                  | `cancel-task`（または `result-submit`）で対象タスクが終端化した後、再度の `doctor` 実行では `stale_in_progress.candidates` から除外されること                                                                                                                                                                                                                                                                                                     |

<a id="sec-5-3"></a>

### 5.3 実機確認項目

実装完了後の実機検証フェーズ（[docs/herdr-orchestrator-verification-log.md](herdr-orchestrator-verification-log.md)）において、以下を確認・記録する。

1. `herdr-orchestrator doctor` 実行時に `stale_in_progress` が出力され、worktree 消失またはペイン閉鎖状態のテスト用 `in_progress` タスクが `signal_strength = "strong"` として検出されること。
2. 検出されたタスクを `cancel-task`（Issue #173）で終端化した後、再度の `doctor` で候補から消えること。
