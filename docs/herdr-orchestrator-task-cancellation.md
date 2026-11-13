# Herdr Orchestrator タスクキャンセル（`cancel-task`）および復旧ランブック 設計書

- **対象 Issue**: [Issue #173](https://github.com/okinawa-systembit/herdr-orchestration/issues/173)
- **ステータス**: 設計承認
- **関連文書**:
  - [03-task-registry.md](herdr-orchestrator/03-task-registry.md#sec-6-3): Canonical Schema v1・Four-axis State Machine・Mutation Rules・Public CLI Contract（§6.2〜§6.5）
  - [05-completion-and-recovery.md](herdr-orchestrator/05-completion-and-recovery.md#sec-9-13): Completion Eligibility（§9.13）および Completion Reset 受理条件（§9.14）
  - [07-skill-and-implementation.md](herdr-orchestrator/07-skill-and-implementation.md#sec-15-2): Approval Minimization 原則（§15.2〜§15.4）
  - [herdr-orchestrator-task-list.md](herdr-orchestrator-task-list.md#44-派生分類-overviewstate): `task-list` 診断用不変条件 I1〜I7（§4.4）
  - [herdr-orchestrator-task-registry-cleanup.md](herdr-orchestrator-task-registry-cleanup.md#sec-7): 30 日保護および直接 `rm` 禁止ガードレール（§7）

---

<a id="sec-1"></a>

## 1. 背景と目的

<a id="sec-1-1"></a>

### 1.1 背景と課題

実機運用において、`herdr-orchestrator handoff --mode async` 実行後に Worker ペイン（Codex / Antigravity 等）が利用枠切れ、プロセスクラッシュ、ペイン閉鎖、worktree 削除などの外部要因で途中停止した場合、Task Registry 上のタスクが `task.status = in_progress`、`dispatch.status = sent`、`result.status = pending`、`completion.status = pending` のまま永久に残留する事象が確認された。

現行実装では、以下の構造的ギャップにより、**CLI 経由で安全にタスクを終端化できない状態**が発生していた。

1. `task.status` を `in_progress`（または `unknown`）から終端状態（`succeeded` / `failed`）に遷移させる唯一の手段である `result-submit` は、`verify_worker_submit_context` により同一 Worker ペイン・同一 cwd・同一 repository identity からの提出を要求する。そのため、Worker ペインや worktree が失われた場合は実行できない。
2. [03-task-registry.md §6.3](herdr-orchestrator/03-task-registry.md#sec-6-3) および `schema.py` にはすでに `cancelled` 状態と遷移表が予約されていたが、「MVP では event 無効」として公開 CLI が未提供であった。
3. [03-task-registry.md §6.8](herdr-orchestrator/03-task-registry.md#sec-6-8) および [Task Registry クリーンアップ機構 設計書](herdr-orchestrator-task-registry-cleanup.md) の `cleanup-registry` は未完了状態（`created` / `in_progress` / `unknown`）を自動削除せず、また直接 `rm` による Registry 編集も禁止されている。

<a id="sec-1-2"></a>

### 1.2 目的

本設計書は、[基本方針正本](core-principles.md) の原則 1（KISS 原則）、原則 2（人間中心の可観測性）、原則 6（単一正本）、および指針 10（黙って失敗しない）に基づき、以下を実現することを目的とする。

1. 設計上すでに予約されている `cancelled` 状態への遷移を有効化し、公開 CLI **`herdr-orchestrator cancel-task <task_id> --reason "<理由>"`** を提供する。
2. `result.status = pending` を保持したまま安全に終端化（`task.status = cancelled`、必要な場合は `completion.status = skipped`）し、30 日後の `cleanup-registry` の対象に乗せる。
3. Worker ペインが生存している場合は `result-submit --result-status unavailable` による終端化を優先し、ペイン・worktree 喪失時のみ `cancel-task` を使用する **Requester 向け復旧ランブック（Recovery Runbook）** を Skill（`references/handoff.md`）に整備する。

---

<a id="sec-2"></a>

## 2. 基本設計方針とスコープ境界

<a id="sec-2-1"></a>

### 2.1 採用方針（予約済み `cancelled` 状態の有効化）

新たな終端状態や迂回路を新設するのではなく、[03-task-registry.md §6.3](herdr-orchestrator/03-task-registry.md#sec-6-3) で予約済みの `cancelled` 状態および cancellation イベントを有効化する。

<a id="sec-2-2"></a>

### 2.2 対象外（不採用とするアプローチ）

原則 6（単一正本）および原則 7（スコープ限定）に基づき、以下は本設計のスコープ外とし採用しない。

| 不採用・対象外項目                                         | 理由                                                                                                                                                                     |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `abort-task` の新設および `completion.status = suppressed` | `suppressed` はスキーマに存在せず、予約済みの `cancelled` と意味が重複する（原則 6 違反）。また `result.status = unavailable` を非 Worker が書くと結果の出所が曖昧になる |
| `result-submit --force` 等の代行オプション                 | Worker 本人確認（`verify_worker_submit_context`）は誤配送・別タスク誤提出を防ぐ中核保証であり、迂回路を作ると通常運用で乱用されるリスクがある                            |
| 最小 Task Envelope（§11.4）の拡張                          | Envelope は「Registry を読んでから作業すること」に限定し、手順詳細は Skill 側に置く方針を維持する                                                                        |
| Worker 死亡のリアルタイム監視・自動キャンセル              | 外部プロセスの死活監視は Orchestrator の責務外。人間または Requester が事実確認の上で明示的に `cancel-task` を実行する                                                   |
| `doctor` での stale `in_progress` 検出                     | 読み取り専用診断としての `doctor` 拡張はスコープを分離し、別 Issue として扱う                                                                                            |

---

<a id="sec-3"></a>

## 3. 状態遷移・Schema 拡張・Completion 整合

状態遷移表および Mutation Rules の正本は [03-task-registry.md §6.3](herdr-orchestrator/03-task-registry.md#sec-6-3) とし、本節では `cancel-task` 有効化に伴う定義と周辺モジュールとの整合仕様を定める。

<a id="sec-3-1"></a>

### 3.1 4 軸の状態遷移ルール

| 軸                  | `cancel-task` 適用時の遷移                                                                                        | 補足                                                                                                                    |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `task.status`       | `{in_progress, unknown} -> cancelled`                                                                             | `created`（`reconcile-dispatch` 対象）または既に terminal（`succeeded` / `failed` / `cancelled`）は `registry_conflict` |
| `dispatch.status`   | **現在値を保持**（`pending` / `sent` / `uncertain` 等を変更しない）                                               | 初回配送の観測事実を保存する                                                                                            |
| `result.status`     | **現在値を保持**（`pending` のまま維持）                                                                          | terminal result は提出されていないという事実を保持する                                                                  |
| `completion.status` | `completion.action == "resume_requester"` かつ `pending` なら `skipped`；`none` / `sync` は `not_applicable` 維持 | `completion.status == "processing"` の場合は `registry_conflict` で拒否                                                 |

- **`created` タスクを対象外とする理由**:
  `task.status = created` は Worker へまだ配送完了していない段階（`dispatch.status = pending`）であり、既存の `reconcile-dispatch`（[03-task-registry.md §6.12](herdr-orchestrator/03-task-registry.md#sec-6-12)）が終端化の責務を持つため、`cancel-task` では受理しない。
- **キャンセル後の遅れた `result-submit` の拒否**:
  `update-result` は `task.status ∈ {in_progress, unknown}` のみを受理するため、`cancelled` 遷移後に Worker から遅れて `result-submit` が実行されても既存の Mutation Rules により `registry_conflict` で自然に拒否される。
- **sync handoff 待機中のキャンセル検知**:
  `handoff --mode sync` の結果待機ループ（`handoff_commands.py` の `_wait_for_result_terminal`）が待機中に `doc["task"]["status"] == "cancelled"` を観測した場合は、タイムアウトを待たずに即座に非リトライ可能エラー `task_cancelled`（`retryable=False`、終了コード `1`）で失敗終了する。

<a id="sec-3-2"></a>

### 3.2 Canonical Schema v1 の拡張（`task.cancel_reason` / `task.cancelled_by`）

後方互換性を維持し `schema_version = 1` を据え置くため、`task` オブジェクトに以下の**任意フィールド**を追加定義する（未キャンセルの既存タスクには存在しなくてよい）。

```json
"task": {
  "id": "task_7f3c81a2-75dd-4b75-a412-6e887eb62bd5",
  "type": "review",
  "mode": "async",
  "instruction": "...",
  "status": "cancelled",
  "cancel_reason": "Workerペイン喪失およびworktree削除のため中断",
  "cancelled_by": {
    "role": "unbound",
    "pane_id": "w15:p1",
    "agent_name": null
  }
}
```

| フィールド           | 型       | 必須・任意                 | 説明                                                                                                                                  |
| -------------------- | -------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `task.cancel_reason` | `string` | 任意（キャンセル時は必須） | `--reason` で指定されたキャンセル理由（前後空白を除去した非空文字列、最大 2000 文字）。存在時は非空文字列であることを検証             |
| `task.cancelled_by`  | `object` | 任意（キャンセル時に記録） | 監査用の実行者情報。`role`（文字列、取得不能時は `"unbound"`）、`pane_id`（文字列または `null`）、`agent_name`（文字列または `null`） |

> [!NOTE]
> `validate_loaded_task()`（`schema.py`）は、`task.cancel_reason` が存在する場合に文字列（空文字禁止・`None` 許容または非空文字列）として検証し、`task.cancelled_by` が存在する場合に `dict` 型および各フィールドの型を検証する。`task.status == "cancelled"` 以外の既存タスクでは両フィールドが省略されていてもエラーにしない。

<a id="sec-3-3"></a>

### 3.3 Completion（§9.13 / §9.14）との整合

1. **Claim 対象外（[§9.13](herdr-orchestrator/05-completion-and-recovery.md#sec-9-13)）**:
   `claim-completion` / `resume-task` は `task.status ∈ {succeeded, failed}` かつ `result.status ∈ {succeeded, failed, unavailable}` を要求するため、`cancelled` タスクは自動的に対象外となる。
2. **Reset 拒否（[§9.14](herdr-orchestrator/05-completion-and-recovery.md#sec-9-14)）**:
   `cancel-task` により `completion.status = skipped` に確定したタスクに対して `reset-completion` / `recover-completion` を実行すると、`completion.status` が `pending` に戻ってしまう。これを防ぐため、`reset_completion()`（`operations.py`）に `doc["task"]["status"] == "cancelled"` の拒否条件（`registry_conflict`）を追加し、[§9.14](herdr-orchestrator/05-completion-and-recovery.md#sec-9-14) に明記する。

<a id="sec-3-4"></a>

### 3.4 `task-list` の `overview.state` および診断用不変条件 I7 との同期

[03-task-registry.md §6.3](herdr-orchestrator/03-task-registry.md#sec-6-3) の「診断用不変条件 I1〜I7 との同期」規定および [herdr-orchestrator-task-list.md §4.4](herdr-orchestrator-task-list.md#44-派生分類-overviewstate) に従い、`cancelled` 有効化に合わせて `overview.state` 判定と不変条件 I7 および `list_commands.py` を以下のとおり同期する。

- **`overview.state` の判定順序（`list_commands.py` の `_calculate_overview_state`）**:
  `unknown` 由来のキャンセルでは `dispatch.status = uncertain` かつ `result.status = pending` が保持され、また通常キャンセルでも `result.status = pending` が保持されるため、従来の判定順序（`uncertain` → `open` → `closed`）のままでは `uncertain` または `open` に誤分類される。これを防ぐため、判定表の先頭（順 1）に **`task.status = cancelled` かつ `result.status = pending` かつ `completion.status ∈ {not_applicable, skipped}` → `closed`** を追加する（`cancelled` 有効化に伴う既定意味論への整合であり、`list_format_version = 1` は据え置く）。
- **不変条件 I7（改定前）**: `task.status = cancelled` は MVP 無効のため無条件で `inconsistent_state_combination` 警告対象。
- **不変条件 I7（改定後）**: `task.status = cancelled` ⇒ `result.status = pending` ∧ `completion.status ∈ {not_applicable, skipped}`。正常にキャンセルされたタスクには警告を付与せず、上記条件に反する不整合がある場合のみ警告する。

---

<a id="sec-4"></a>

## 4. `cancel-task` CLI 仕様

<a id="sec-4-1"></a>

### 4.1 コマンド構文と実行環境要件

```text
herdr-orchestrator cancel-task <task_id> --reason "<理由>" [--json]
```

| 引数 / オプション | 必須 | 説明                                                                                                              |
| ----------------- | ---- | ----------------------------------------------------------------------------------------------------------------- |
| `task_id`         | 必須 | 対象タスクの `task.id`（`validate_task_id` で形式検証）                                                           |
| `--reason`        | 必須 | キャンセル理由の非空文字列（前後空白のみは拒否、最大 2000 文字）                                                  |
| `--json`          | 任意 | 他の公開 CLI サブコマンドと同様に JSON 出力を明示する互換フラグ（指定の有無にかかわらず標準出力は常に JSON 出力） |

1. **`HERDR_ENV=1` 非必須（免除）**:
   ペインや worktree が失われた後に、人間が通常のターミナルからも滞留タスクを片付けられるようにするため、`cleanup-registry` と同様に **`require_herdr_env()` を呼び出さない**。
2. **Worker 本人確認の免除と監査用 `caller` 記録**:
   Worker ペイン喪失時の復旧手段であるため、`verify_worker_submit_context()` は実行しない。代わりに、コマンド実行時の環境情報からベストエフォートで実行者情報（`cancelled_by`）を解決して Registry および出力 JSON に記録する。
   - 環境変数 `HERDR_PANE_ID` が設定されている場合は `pane_id` として記録し、可能であれば（`HERDR_ENV=1` 下で Role Binding が解決できる場合）該当リポジトリの `role` を記録する。解決できない場合や通常のターミナルからの実行時は `role: "unbound"`、`pane_id: os.environ.get("HERDR_PANE_ID") or None`、`agent_name: null` とする（`herdr` CLI の起動失敗で `cancel-task` 自体を失敗させない）。
   - **監査メタデータの位置づけ**: `cancelled_by` は実行時の環境変数（`HERDR_PANE_ID` 等）に基づくベストエフォートの監査メタデータであり、残留環境変数等により実行者本人の厳密な同一性・正確性を保証するものではない。

<a id="sec-4-2"></a>

### 4.2 排他制御・受理条件・エラーコード

`operations.cancel_task(task_id, *, reason: str, cancelled_by: dict[str, Any] | None = None)` は、`io.mutate_task(task_id, mutator)` により `<task_id>.json.lock` の排他ロック下で JSON を再読込し、compare-and-set で検証・更新する。

| 検証条件                                                               | エラーコード        | 終了コード                                | 説明                                                                           |
| ---------------------------------------------------------------------- | ------------------- | ----------------------------------------- | ------------------------------------------------------------------------------ |
| `task_id` 形式不正、`--reason` 空文字・空白のみ・2000 文字超過         | `invalid_input`     | `1`（構文欠落時は `argparse` により `2`） | Registry を変更せず拒否                                                        |
| 対象タスク JSON が存在しない                                           | `task_not_found`    | `1`                                       | Registry を変更せず拒否                                                        |
| `task.status ∉ {in_progress, unknown}`（`created` または既に終端状態） | `registry_conflict` | `1`                                       | `created` は `reconcile-dispatch` を案内。終端状態は再遷移・二重キャンセル不可 |
| `completion.status == "processing"`                                    | `registry_conflict` | `1`                                       | 進行中の completion との競合を防止                                             |

<a id="sec-4-3"></a>

### 4.3 出力 JSON スキーマ

```json
{
  "command": "cancel-task",
  "task_id": "task_7f3c81a2-75dd-4b75-a412-6e887eb62bd5",
  "cancelled": true,
  "task": {
    "id": "task_7f3c81a2-75dd-4b75-a412-6e887eb62bd5",
    "status": "cancelled",
    "cancel_reason": "Workerペイン喪失のため中断",
    "cancelled_by": {
      "role": "unbound",
      "pane_id": "w15:p1",
      "agent_name": null
    }
  },
  "dispatch": {
    "status": "sent"
  },
  "result": {
    "status": "pending"
  },
  "completion": {
    "action": "resume_requester",
    "status": "skipped"
  }
}
```

<a id="sec-4-4"></a>

### 4.4 30 日保持および `cleanup-registry` との連携

`_task_eligible_for_cleanup()`（`operations.py`）はすでに `task.status in ("succeeded", "failed", "cancelled")` かつ `completion.status in ("not_applicable", "sent", "failed", "skipped")` を削除対象としている。したがって、`cancel-task` で終端化されたタスクは、作成（`timestamps.created_at`）から 30 日経過後に既存の `result-submit` lazy cleanup および `cleanup-registry` により自動的に整理される。

---

<a id="sec-5"></a>

## 5. 実行承認（Allowlist）方針

[07-skill-and-implementation.md §15.2〜§15.4](herdr-orchestrator/07-skill-and-implementation.md#sec-15-2) に基づき、`cancel-task` は **`allow` しない（通常承認に委ねる）** 方針とする。

- **理由**: `cancel-task` は Worker 本人確認なしに非 terminal タスクを強制的に `cancelled` へ終端化できる状態変更コマンドであり、`reconcile-dispatch`、`reset-completion`、`recover-completion` と同等の復旧・片付け操作に分類されるため、エージェントが無承認で自動実行すべきではない。
- **適用**: `skills/herdr-orchestrator/install/README.md` §2.1 のコマンド分類表に `cancel-task` を「状態変更・復旧 / **allow しない**」として追加し、`codex-execpolicy-snippet.rules` には `allow` ルールを追加しない。

---

<a id="sec-6"></a>

## 6. Recovery Runbook（Requester 向け運用手順）

`handoff --mode async` 後に完了通知が返らず、Worker 途中停止や未完了放置が疑われる場合に、Requester および人間が参照する標準手順を `skills/herdr-orchestrator/references/handoff.md` に追記する（詳細は正本リンクで誘導し、簡潔な判断フローを記載する）。

1. **残存未完了タスクの確認**:
   `herdr-orchestrator task-list --status in_progress`（または `unknown`）および `herdr-orchestrator task-status <task_id>` で状態を確認する。
2. **Worker ペイン・worktree 生存確認**:
   `herdr pane list` 等で対象 Worker ペイン（`worker.pane_id`）および `context.worktree.path` が生存しているかを確認する。
3. **分岐 A: Worker ペインが生存（同一 cwd / 同一リポジトリ維持）**:
   同一ペインでエージェントが再起動・枠回復可能な場合は、その Worker ペインから `herdr-orchestrator result-submit <task_id> --result-status unavailable --summary "<中断理由>"` を提出させて正規に `failed` 終端化する。なお、`worker_context_unverified` 等で `result-submit` が拒否された場合は分岐 B（`cancel-task`）へ進む。
4. **分岐 B: Worker ペイン喪失・worktree 削除・別用途転用等で復帰不能**:
   `herdr-orchestrator cancel-task <task_id> --reason "<中断理由>"` を実行し、`task.status = cancelled`（および `completion.status = skipped`）へ安全に終端化する（JSON ファイルの直接編集・直接 `rm` は行わない）。

---

<a id="sec-7"></a>

## 7. 実装・検証計画（テスト計画 CT-01〜CT-10）

<a id="sec-7-1"></a>

### 7.1 変更対象ファイル一覧

| 対象ファイル                                                             | 変更内容                                                                                                                                                     |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/schema.py`      | `validate_loaded_task` に任意の `task.cancel_reason` / `task.cancelled_by` の型・非空検証を追加                                                              |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`  | `cancel_task()` の実装、および `reset_completion()` での `task.status == "cancelled"` 拒否追加                                                               |
| `skills/herdr-orchestrator/scripts/orchestrator/public_cli.py`           | `PUBLIC_COMMANDS` / `_COMMAND_HELP` / `argparse` への `cancel-task` 追加と `_cmd_cancel_task` 実装（`HERDR_ENV=1` 非必須）                                   |
| `skills/herdr-orchestrator/scripts/orchestrator/handoff_commands.py`     | `_wait_for_result_terminal()` で `task.status == "cancelled"` を検知した際に即座に `task_cancelled`（`retryable=False`、exit 1）を返す処理を追加             |
| `skills/herdr-orchestrator/scripts/orchestrator/list_commands.py`        | `_calculate_overview_state()` 先頭へ `cancelled` の `closed` 判定を追加し、`_has_inconsistent_state()` の I7 判定を改定仕様（§3.4）に同期                    |
| `skills/herdr-orchestrator/references/handoff.md`                        | §6 の Recovery Runbook（Worker 途中停止時の確認・`unavailable` 提出・`cancel-task` 判断フロー）を追記                                                        |
| `skills/herdr-orchestrator/references/worker-receive.md`                 | Worker が `task.status = cancelled` を観測した場合、または `result-submit` 時にキャンセル等で拒否された場合に作業を中断し人間/Requester へ報告する案内を追記 |
| `skills/herdr-orchestrator/references/public-cli.md`                     | 公開 CLI 一覧および `cancel-task` の使用例・仕様を追記（D-2）                                                                                                |
| `skills/herdr-orchestrator/SKILL.md`                                     | Public CLI の復旧・再開欄に `cancel-task` を追記（D-2）                                                                                                      |
| `skills/herdr-orchestrator/install/README.md`                            | §2.1 コマンド分類表に `cancel-task`（allow しない）を追記（D-2）                                                                                             |
| `docs/herdr-orchestrator/03-task-registry.md`                            | 設計 PR で更新済みの「未実装」NOTE を実装完了に合わせて解除                                                                                                  |
| `docs/herdr-orchestrator/07-skill-and-implementation.md`                 | §14.2 等の内部操作一覧に `cancel_task`（`cancel`）を追記                                                                                                     |
| `docs/herdr-orchestrator-task-cancellation.md`                           | 実装 PR（`Closes #173`）作成時にヘッダーの `- **ステータス**:` を `実装完了（PR #NN にてマージ済み）` へ更新（D-1 必須）                                     |
| `tests/test_orchestrator_phase2_registry.py`（または専用テストファイル） | 下記 §7.2 のテストケース（CT-01〜CT-10）を追加                                                                                                               |

<a id="sec-7-2"></a>

### 7.2 テスト計画（CT-01〜CT-10）

指針 10（「テストは引数変換だけでなく実際の結果を検証する」）に基づき、実装時に以下の自動テストを追加・検証する。

| テスト ID | 検証項目                                  | テスト内容と期待される結果                                                                                                                                                                                                                                                                                                                  |
| --------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CT-01** | `in_progress` からの遷移                  | `task.status = in_progress` のタスクに `cancel-task` を実行すると、`task.status = cancelled` となり `dispatch` と `result`（`pending`）が保持され、`task.cancel_reason` と `task.cancelled_by` が記録されること                                                                                                                             |
| **CT-02** | `unknown` からの遷移                      | `task.status = unknown`・`dispatch.status = uncertain` のタスクに `cancel-task` を実行すると、`task.status = cancelled` となり `dispatch.status = uncertain`・`result.status = pending` が保持されること                                                                                                                                    |
| **CT-03** | `completion` の状態別遷移                 | `completion.action = resume_requester` かつ `pending` の場合は `skipped` に遷移し、`none` / `sync` の場合は `not_applicable` を維持すること                                                                                                                                                                                                 |
| **CT-04** | `completion = processing` 拒否            | `completion.status = processing` のタスクに対する `cancel-task` が `registry_conflict`（exit 1）で拒否され、Registry が変更されないこと                                                                                                                                                                                                     |
| **CT-05** | `created`・終端拒否・`reset-completion`   | `created` / `succeeded` / `failed` / `cancelled` のタスクへの `cancel-task` が `registry_conflict` で拒否されること（`created` は `reconcile-dispatch` 対象）。また `cancelled` タスクへの `reset-completion` / `resume-task` も `registry_conflict` で拒否されること                                                                       |
| **CT-06** | `--reason` 必須・空文字拒否               | `--reason` が空文字・空白のみ・未指定の場合にエラー（Registry 未変更）となること                                                                                                                                                                                                                                                            |
| **CT-07** | キャンセル後の遅れた `result-submit` 拒否 | `cancelled` に遷移したタスクに対して後から `result-submit` を実行しても、`registry_conflict` で拒否され `task.status = cancelled` が維持されること                                                                                                                                                                                          |
| **CT-08** | 30 日後の `cleanup-registry` 削除         | `cancelled` かつ `completion.status ∈ {skipped, not_applicable}` のタスクが、作成から 30 日経過後に `cleanup-registry`（および `cleanup_tasks`）で削除対象になること                                                                                                                                                                        |
| **CT-09** | `HERDR_ENV` 非必須・`task-list` 整合      | `HERDR_ENV` 未設定環境から `cancel-task` が実行でき、`cancelled_by.role = "unbound"` を含む JSON を返し、`in_progress` 由来（`dispatch=sent`）および `unknown` 由来（`dispatch=uncertain`）双方の `cancelled` タスクで `task-list` の `overview.state == "closed"`・`overview.warnings == []` となること（不整合 `cancelled` には警告付与） |
| **CT-10** | sync handoff 待機中のキャンセル検知       | `handoff --mode sync` の結果待機中（`_wait_for_result_terminal`）に別経路で対象タスクが `cancelled` になった場合、タイムアウトを待たずに `task_cancelled`（`retryable=False`、exit 1）で終了すること                                                                                                                                        |

<a id="sec-7-3"></a>

### 7.3 実機確認項目

実装完了後の実機検証フェーズ（[docs/herdr-orchestrator-verification-log.md](herdr-orchestrator-verification-log.md)）において、以下の 2 点を確認・記録する。

1. **分岐 A（Worker ペイン生存）**: 同一ペインで再起動・回復した Worker エージェントから `result-submit --result-status unavailable` により正規に `failed` 終端化できること。
2. **分岐 B（Worker ペイン喪失・worktree 削除）**: 疑似的な Worker ペイン閉鎖・worktree 削除を伴う状態で `cancel-task` を実行し、`task-list` 上で `overview.state = closed` として表示されること。
