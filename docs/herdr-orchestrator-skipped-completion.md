# Herdr Orchestrator `completion.status=skipped` 可視化（`doctor` / `task-list` / `result-submit`）設計書

- **対象 Issue**: [Issue #183](https://github.com/okinawa-systembit/herdr-orchestration/issues/183)
- **ステータス**: 実装完了（PR #187 / v0.8.1 にてマージ済み）
- **関連文書**:
  - [05-completion-and-recovery.md](herdr-orchestrator/05-completion-and-recovery.md#sec-9-4): Requester 状態による `completion.status=skipped` 遷移（§9.4）および復旧設計（§9.5, §9.13, §9.14）
  - [herdr-orchestrator-task-list.md](herdr-orchestrator-task-list.md#43-task-要素のフィールド): `task-list` 射影仕様（§4.3）および `overview.warnings`（§4.4）
  - [herdr-orchestrator-doctor-stale-in-progress.md](herdr-orchestrator-doctor-stale-in-progress.md#sec-4): `doctor` 出力スキーマ統一（`action: "await_human"` / Read-Only / 終了コード非影響）
  - [herdr-orchestrator-task-registry-cleanup.md](herdr-orchestrator-task-registry-cleanup.md#sec-4): `doctor` 可視化における完全 Read-Only 原則（Issue #157 §4）

---

<a id="sec-1"></a>

## 1. 背景と目的

<a id="sec-1-1"></a>

### 1.1 背景と課題

`async handoff` において、Worker が `result-submit` を実行した時点で Requester が別作業や結果集約のため `working` 状態にあると、[05-completion-and-recovery.md §9.4](herdr-orchestrator/05-completion-and-recovery.md#sec-9-4) の安全側判断により自動 prompt 送信を抑止し、`completion.status = "skipped"`（`reason = "requester_working"` 等）へ遷移する。

しかし現行実装では、この「タスク自体は終端化しているが Requester への完了通知がスキップされた状態」が `doctor` や `task-list` 上で可視化されておらず、`result-submit` 実行時の標準エラー警告も存在しないため、人間が通知未達に気づきにくい課題があった。

<a id="sec-1-2"></a>

### 1.2 目的

本設計書は、[基本方針正本](core-principles.md) の原則 1（KISS 原則）、原則 2（人間中心の可観測性）、原則 6（単一正本）、および指針 10（黙って失敗しない）に基づき、以下を実現することを目的とする。

1. `doctor`（`skipped_completion`）、`task-list`（`overview.warnings` の `"completion_skipped"`）、および `result-submit`（`stderr` 1 行警告）により、`completion.status = "skipped"` のうち回復対象となるタスクを可視化する。
2. 判定条件を `registry/operations.py` の単一述語 `is_recoverable_skipped_completion()` に集約し、初回配送失敗による終端 `skipped` と明確に区別する。
3. 自動再配送（lazy 再配送や `result-submit` 内リトライ）は採用せず、完全 Read-Only・終了コード非影響・`list_format_version: 1` 互換を維持する。

---

<a id="sec-2"></a>

## 2. 基本設計方針と不採用アプローチ（P1 不採用理由）

<a id="sec-2-1"></a>

### 2.1 採用方針

| 設計項目                     | 採用方針                                                                                                                                  |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **単一述語集約（原則 6）**   | `registry/operations.py` に `is_recoverable_skipped_completion(doc)` を 1 か所だけ定義し、`doctor` と `task-list` の双方で共有する        |
| **`doctor` 出力統一**        | Issue #174（`stale_in_progress`）と同じ `action: "await_human"`・`summary`・`candidates`・`guidance` 構造で `"skipped_completion"` を出力 |
| **Read-Only・終了コード**    | `doctor` / `task-list` は完全 Read-Only とし、`tasks/` 未作成時はディレクトリを作らず空結果を返す。終了コードに影響させない               |
| **`task-list` 後方互換**     | `list_format_version: 1` を維持し、§4.3 の契約どおり `completion.reason` は一覧投影に含めない（`overview.warnings` のみ拡張）             |
| **`result-submit` 警告出力** | 標準出力 JSON スキーマを変更せず、`completion_chain.completion.status == "skipped"` 時に `sys.stderr` へ 1 行警告を出す（指針 10）        |

<a id="sec-2-2"></a>

### 2.2 検討したが採用しなかった案（対象外）

| 不採用・対象外項目                                       | 不採用理由                                                                                                                                                                                                                                  |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **P1 推奨案**: CLI 実行時の lazy 自動再配送              | ① `doctor` / `task-list` 等の Read-Only 契約違反、② 別ワークスペース・別リポジトリへの暗黙 prompt 送信による可観測性低下（原則 2）、③ 既にチャット等で処理済みの過去タスクに対するゾンビ通知・重複作業リスク、④ `idle` 判定と送信間の競合。 |
| **P1 最小代替案**: `result-submit` 内の bounded リトライ | Live Registry 実測で `async handoff` から `result-submit` まで 1〜3 分で Requester が数分間 `working` を継続するケースが大半であり、最大 50 秒ブロックの割に救済効果が薄く Worker の報告遅延だけが増大するため。                            |
| **`task-list` 投影への `completion.reason` 追加**        | [task-list 設計書 §4.3](herdr-orchestrator-task-list.md#43-task-要素のフィールド) の自由記述非投影契約および `list_format_version: 1` 互換を維持するため（詳細は `task-status` / `doctor` で参照）。                                        |
| **`ack` / 既読状態の新設**                               | 4 軸状態モデルに不要な状態を増やさず（KISS 原則）、`doctor` では `updated_at` 降順と `age_sec` を提示して人間の判断に委ねる。                                                                                                               |

---

<a id="sec-3"></a>

## 3. 単一共通述語・抽出ロジック仕様

<a id="sec-3-1"></a>

### 3.1 単一共通述語 `is_recoverable_skipped_completion(doc)`

`registry/operations.py` に `is_recoverable_skipped_completion(doc: dict[str, Any]) -> bool` を新設し、回復対象の `completion.status = "skipped"` を以下の全条件の論理積で判定する。

```text
completion.action == "resume_requester"
∧ completion.status == "skipped"
∧ dispatch.status ∈ {"sent", "uncertain"}
∧ task.status ∈ {"succeeded", "failed"}
∧ result.status ∈ {"succeeded", "failed", "unavailable"}
∧ not _is_initial_dispatch_failure_terminal(doc)
```

- **初回配送失敗との区別**: `dispatch.status ∈ {"failed", "skipped"}` かつ `task.status == "failed"` かつ `result.status == "unavailable"` の場合は `_is_initial_dispatch_failure_terminal(doc)` により `False` となり、回復対象から機械的に除外される（[§9.14](herdr-orchestrator/05-completion-and-recovery.md#sec-9-14) の reset 拒否条件と完全整合）。
- **`reason` で絞り込まない理由**: `requester_working` だけでなく `requester_blocked` / `requester_unknown` / `requester_unavailable` / `requester_context_mismatch` / `live_pane_id_missing` 等も、`recover-completion` による回復対象となり得るため、`reason` によるフィルタは行わず候補フィールドとして `completion_reason` をそのまま出力する。

<a id="sec-3-2"></a>

### 3.2 `find_skipped_completion_candidates`（`registry/operations.py`）

`find_skipped_completion_candidates(*, now: datetime | None = None) -> list[dict[str, Any]]` を定義する。

1. **完全 Read-Only・ディレクトリ未作成保護**: `io.tasks_dir().exists()` が `False` の場合はディレクトリを作成せず空リスト `[]` を返す。
2. **破損ファイル耐性**: 個別の JSON 読込・検証に失敗したファイルはスキップし、`doctor` 全体を失敗させない。
3. **抽出・ソート順**: `is_recoverable_skipped_completion(doc)` が `True` のタスクのみ抽出し、**`timestamps.updated_at` の降順（新しい順）** で並べて返す（過去の処理済み候補で直近の未処理候補が埋もれないようにする）。
4. **軽量性**: Registry 走査のみで完結し、`herdr pane list` 等の外部 CLI 呼び出しは行わない。

---

<a id="sec-4"></a>

## 4. `doctor` / `task-list` / `result-submit` 出力契約

<a id="sec-4-1"></a>

### 4.1 `doctor` 出力スキーマ（`skipped_completion`）

`run_doctor()`（`dispatch_commands.py`）の返却 JSON にトップレベルキー `"skipped_completion"` を追加する。構造は [Issue #174 設計書 §4.1](herdr-orchestrator-doctor-stale-in-progress.md#sec-4-1) の `stale_in_progress` と統一する。

```json
{
  "command": "doctor",
  "stage": 5,
  "herdr_env": true,
  "stale_dispatch": { "...": "..." },
  "stale_in_progress": { "...": "..." },
  "skipped_completion": {
    "action": "await_human",
    "summary": {
      "total_candidates": 1
    },
    "candidates": [
      {
        "task_id": "task_85e815b6-0000-4000-8000-000000000001",
        "task_type": "review",
        "task_mode": "async",
        "task_status": "succeeded",
        "result_status": "succeeded",
        "result_ref": "docs/reviews/internal/inst_xxx/RV100.md",
        "completion_reason": "requester_working",
        "recovery_count": 0,
        "repository_identity": "4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
        "worktree_path": "/home/user/Gitrepo/other-repo",
        "requester_role": "メイン",
        "requester_agent_name": "w16:p1",
        "updated_at": "2026-11-17T01:00:00+00:00",
        "age_sec": 180.5
      }
    ],
    "guidance": [
      "まず `herdr-orchestrator task-result <task_id>` で結果を確認し、Requester 側で別経路（チャット等）により既に処理済みであれば何もしないでください（30 日経過後に自動整理されます）。",
      "未処理かつ Requester ペインが idle / done になっている場合は、人間が Requester 以外の `HERDR_ENV=1` が有効な Herdr ペインから `herdr-orchestrator recover-completion <task_id>` を実行してください（Requester / Worker 自身のターン内実行は再び skipped になり、HERDR_ENV≠1 環境ではエラーになります）。"
    ]
  },
  "outdated_role_bindings": [],
  "registry_cleanup": { "...": "..." }
}
```

| 候補フィールド         | 型                 | 説明                                             |
| ---------------------- | ------------------ | ------------------------------------------------ |
| `task_id`              | `string`           | 対象タスク ID                                    |
| `task_type`            | `string`           | `task.type`                                      |
| `task_mode`            | `string`           | `task.mode`                                      |
| `task_status`          | `string`           | `task.status`（`"succeeded"` \| `"failed"`）     |
| `result_status`        | `string`           | `result.status`                                  |
| `result_ref`           | `string` \| `null` | `result.ref`                                     |
| `completion_reason`    | `string` \| `null` | `completion.reason`（例: `"requester_working"`） |
| `recovery_count`       | `integer`          | `completion.recovery_count`                      |
| `repository_identity`  | `string`           | `context.repository.identity`                    |
| `worktree_path`        | `string` \| `null` | `context.worktree.path`                          |
| `requester_role`       | `string` \| `null` | `requester.role`                                 |
| `requester_agent_name` | `string` \| `null` | `requester.agent_name`                           |
| `updated_at`           | `string`           | `timestamps.updated_at`（ISO 8601）              |
| `age_sec`              | `number`           | 現在時刻との経過秒数（小数点以下 3 桁丸め）      |

<a id="sec-4-2"></a>

### 4.2 `task-list` 出力契約（`overview.warnings`）

- `list_commands.py` において、各タスク投影時に `registry_ops.is_recoverable_skipped_completion(doc)` を呼び出し、真であれば `overview.warnings` に `"completion_skipped"` を追加する。
- `overview.state` は `"closed"` を維持する（[herdr-orchestrator-task-list.md §4.4](herdr-orchestrator-task-list.md#44-派生分類-overviewstate) の 4 軸判定規則は変更しない）。
- `inconsistent_state_combination`（構造不整合・正常完了扱い禁止）とは異なり、`completion_skipped` は「タスク自体は終端化しており、通知だけがスキップされた要確認状態」を示す警告として区別する。

<a id="sec-4-3"></a>

### 4.3 `result-submit` の標準エラー警告

`result_commands.py` の `result_submit()` において、`not replay` 経路で `best_effort_completion_after_result(task_id)` を実行した結果、`chain.get("completion", {}).get("status") == "skipped"` であった場合、標準出力 JSON フォーマットは一切変更せず `sys.stderr` へ以下の 1 行警告を出力する。

```text
[herdr-orchestrator] warning: completion skipped (<reason>); requester was not notified. Human: run 'recover-completion <task_id>' after requester becomes idle.
```

---

<a id="sec-5"></a>

## 5. 実装・検証計画（テスト計画 ST-01〜ST-06）

<a id="sec-5-1"></a>

### 5.1 変更対象ファイル一覧

| 対象ファイル                                                                       | 変更内容                                                                                                                  |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`            | `is_recoverable_skipped_completion()` および `find_skipped_completion_candidates()` の実装                                |
| `skills/herdr-orchestrator/scripts/orchestrator/dispatch_commands.py`              | `run_doctor()` への `"skipped_completion"` 追加（`action: "await_human"`, `summary`, `candidates`, `guidance`）           |
| `skills/herdr-orchestrator/scripts/orchestrator/list_commands.py`                  | `is_recoverable_skipped_completion(doc)` 真時に `overview.warnings` へ `"completion_skipped"` を付与                      |
| `skills/herdr-orchestrator/scripts/orchestrator/result_commands.py`                | `completion_chain.completion.status == "skipped"` 時に `sys.stderr` へ 1 行警告を出力                                     |
| `skills/herdr-orchestrator/SKILL.md`                                               | Public CLI 説明の `doctor` 診断項目に `skipped_completion` を追記（D-2）                                                  |
| `skills/herdr-orchestrator/references/public-cli.md`                               | §2.1 `doctor`・`task-list`・`result-submit` 説明へ `skipped_completion` / `completion_skipped` / stderr 警告を追記（D-2） |
| `docs/herdr-orchestrator-skipped-completion.md`                                    | 実装 PR（`Closes #183`）作成時にヘッダーの `- **ステータス**:` を `実装完了（PR #NN にてマージ済み）` へ更新（D-1 必須）  |
| `tests/test_orchestrator_phase5_dispatch.py` / `test_orchestrator_task_list.py` 等 | 下記 §5.2 のテストケース（ST-01〜ST-06）を追加                                                                            |

<a id="sec-5-2"></a>

### 5.2 テスト計画（ST-01〜ST-06）

指針 10（「テストは引数変換だけでなく実際の結果を検証する」）に基づき、実装時に以下の自動テストを追加・検証する。

| テスト ID | 検証項目                                           | テスト内容と期待される結果                                                                                                                                                                                                               |
| --------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ST-01** | `is_recoverable_skipped_completion` 境界判定       | `requester_working` / `requester_blocked` / `requester_unknown` 等の回復対象 `skipped` が真となり、初回配送失敗（`dispatch ∈ {failed, skipped}`）・`cancelled`・`action = none`・`status ∈ {sent, pending}` が偽となること               |
| **ST-02** | `doctor` の `skipped_completion` 出力・降順ソート  | `run_doctor()` が `skipped_completion.action == "await_human"`、`summary.total_candidates`、`candidates`（`updated_at` 降順）、`guidance` を返し、初回配送失敗の `skipped` は含まれないこと                                              |
| **ST-03** | `doctor` の Read-Only・破損耐性・終了コード非影響  | 空の `XDG_STATE_HOME`（`tasks/` 未作成）で `tasks/` を作成せず空候補を返し、破損 JSON をスキップし、`skipped_completion` 候補があっても終了コード（`HERDR_ENV=1` で 0、`!=1` で 1）が変化せず、`herdr pane list` を呼ばないこと          |
| **ST-04** | `task-list` の `completion_skipped` 警告と互換維持 | 回復対象 `skipped` で `overview.warnings` に `"completion_skipped"` が付き、`overview.state == "closed"`・`list_format_version == 1`・`completion.reason` 非投影が維持されること。初回配送失敗には `"completion_skipped"` が付かないこと |
| **ST-05** | `result-submit` の stderr 1 行警告と stdout 互換   | `completion_chain.completion.status == "skipped"` となった場合のみ `stderr` に 1 行警告が出力され、`stdout` の JSON フォーマットが従来と同一であること（`sent` 時および `idempotent_replay=true` 時は stderr 警告を出さないこと）        |
| **ST-06** | `recover-completion` 後の候補・警告解消            | 対象タスクに対して `recover-completion` を実行して `completion.status = "sent"` に遷移させた後、再度の `doctor`（`skipped_completion.candidates`）および `task-list`（`overview.warnings`）から除外されること                            |
