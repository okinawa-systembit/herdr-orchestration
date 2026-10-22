# Herdr Orchestrator Task 一覧参照コマンド `task-list` 設計書

- **対象 Issue**: [#62 — Task Registry一覧参照コマンド task-list の追加](https://github.com/okinawa-systembit/herdr-orchestration/issues/62)
- **ステータス**: 実装前設計案
- **関連 Issue**: [#61 — レビューSkillのPhase 1改善](https://github.com/okinawa-systembit/herdr-orchestration/issues/61)（クラッシュ後の状態確認で本コマンドを利用する想定）
- **関連ドキュメント**:
  - [03-task-registry.md](herdr-orchestrator/03-task-registry.md#sec-6) (§6: Task Registry / Canonical Schema / 4軸状態遷移)
  - [03-task-registry.md](herdr-orchestrator/03-task-registry.md#sec-6-5) (§6.5: Public CLI Contract)
  - [03-task-registry.md](herdr-orchestrator/03-task-registry.md#sec-6-9) (§6.9: Registry Security)
  - [05-completion-and-recovery.md](herdr-orchestrator/05-completion-and-recovery.md#sec-9) (§9: Completion・通知・復旧)
  - [07-skill-and-implementation.md](herdr-orchestrator/07-skill-and-implementation.md#sec-13) (§13: 実装境界)

---

## 1. 背景と目的

### 1.1 現状の課題

公開 CLI で Task Registry を参照する手段は `task-status <task.id>` と `task-result <task.id>` だけである（[§6.5](herdr-orchestrator/03-task-registry.md#sec-6-5)）。どちらも `task.id` を事前に知っている必要がある。

WSL の OOM、プロセス異常終了、Windows の強制終了などで requester Agent の会話履歴が失われると、Agent は `task.id` を把握できない。再起動後に作業を再開する際、次の判断材料が得られない。

- Registry にどの task が残っているか
- どの task が確定済みで、どの task が未完了・不確定か
- どの task が対象 repository / worktree に属するか
- どの task に追加確認（`task-status` / `task-result` / 復旧操作）が必要か

このままでは repository 固有 Skill や helper が Registry の JSON を直接列挙することになる。これは [§6.4 Single Writer Rule](herdr-orchestrator/03-task-registry.md#sec-6-4) の趣旨（Registry 内部構造を CLI の内側に閉じ込める）に反し、異常データの扱いも各 repository でばらつく。

### 1.2 目的

Task Registry に残存する task を **読み取り専用で一覧取得する公開 CLI `task-list`** を中央 Orchestrator に追加する。

1. Agent が `task.id` を知らなくても、残存 task とその4軸状態を把握できる。
2. WSL / Windows 再起動後の作業状況確認を容易にする。
3. 未完了・不確定な task を機械的に発見できる。
4. repository / worktree 単位で task を確認できる。
5. repository 固有 Skill による Registry 内部ファイルへの直接アクセスをなくす。

> [!IMPORTANT]
> 本機能は状態確認と判断材料の提供が目的であり、task の復旧・再実行・状態変更は行わない。復旧は既存の `reconcile-dispatch` / `reset-completion` / `recover-completion` / `resume-task` と人間の判断に委ねる。

---

## 2. 基本設計方針

### 2.1 厳密な読み取り専用

`task-list` は Registry に対して **いかなる書き込みも行わない**。ファイルの作成・変更・削除、パーミッション変更、lock file の作成を含む。

| 行わないこと                                                                 | 理由・補足                                                                                                                                             |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| task の作成・削除・状態変更                                                  | Registry の writer は `task-registry.py` の内部 operation のみ（[§6.4](herdr-orchestrator/03-task-registry.md#sec-6-4)）                               |
| dispatch 再送・result 登録・completion 実行                                  | 自動復旧はスコープ外。blind resend 禁止（[§6.12](herdr-orchestrator/03-task-registry.md#sec-6-12)）                                                    |
| 破損データの修復・削除                                                       | 異常データは報告のみ（[§5](#5-異常データの扱い)）                                                                                                      |
| lazy cleanup（[§6.8](herdr-orchestrator/03-task-registry.md#sec-6-8)）の起動 | 一覧取得が保持期間処理のトリガーになると、参照のたびに Registry が変化する                                                                             |
| Registry ディレクトリの作成・`chmod`                                         | 既存の `ensure_registry_layout()` は不在時に作成し `0700` へ `chmod` する。`task-list` はこれを呼ばない（[§6.2](#62-registry-ディレクトリの読み取り)） |
| `<task.id>.json.lock` / `.handoff.lock` の取得                               | 既存 lock は `O_CREAT` で lock file を作成するため副作用になる。整合性は atomic replace に依拠する（[§6.4](#64-並行更新との整合性)）                   |

### 2.2 4軸状態をそのまま提供する

task の状態を単一の完了・未完了フラグへ変換しない。各 task について [§6.3 Four-axis State Machine](herdr-orchestrator/03-task-registry.md#sec-6-3) の `task.status` / `dispatch.status` / `result.status` / `completion.status` をそのまま出力する。

一覧での把握を助ける **派生分類 `overview.state`**（[§4.4](#44-派生分類-overviewstate)）を併記するが、これは4軸から決定的に計算される参考値であり、正本は常に4軸とする。

### 2.3 不完全な一覧を完全な一覧として返さない

`task-list` は「Registry に残存し、読み取り・検証できた task の一覧」を返す。次は保証しない。

- 削除済み task・保持期間（[§6.8](herdr-orchestrator/03-task-registry.md#sec-6-8)）を超えた履歴の取得
- クラッシュ前に存在したすべての task の復元
- 4軸状態と worker の実稼働状態との一致（`task.status=in_progress` でも worker が稼働中とは限らない）
- 並行更新中の Registry 全体についての原子的スナップショット

その一方で、**読み取りに失敗したエントリが1件でもあれば、出力は機械的に「不完全」と判定できなければならない**。異常データを黙ってスキップし、正常終了の完全一覧として返す実装は禁止する（[§5](#5-異常データの扱い)）。

### 2.4 出力項目は canonical schema の部分射影とする

一覧の各要素は canonical schema v1（[§6.2](herdr-orchestrator/03-task-registry.md#sec-6-2)）と **同じネスト構造・同じキー名** のサブセットとする。`task_id` のような flat キーは使わない（`schema.py` の `FORBIDDEN_FLAT_KEYS` と同じ方針）。これにより、一覧と `task-status` の出力を同じパスで参照できる。

依頼本文（`task.instruction`）、result 本文・要約（`result.summary`）、成果物 locator（`result.ref`）は一覧に含めない。詳細が必要な場合は `task-status` / `task-result` を使う。

### 2.5 Registry 内部構造を CLI に閉じ込める

Registry のディレクトリ配置・ファイル命名・付随ファイル（lock、一時ファイル、cleanup stamp）の知識は `task-list` 実装の内側に置く。repository 固有 Skill / helper は Registry ディレクトリを直接列挙しない。

---

## 3. CLI 仕様

### 3.1 コマンド

```bash
herdr-orchestrator task-list [OPTIONS]
```

既存の読み取り系コマンド（`task-status` / `task-result`）と同じく、`HERDR_ENV=1` を必須とする。未設定時は `herdr_env_required` 系の既存エラー（`emit_herdr_env_error`）で exit 1 とする。

> [!NOTE]
> `task-list` 自体は Herdr CLI を呼び出さないため、`HERDR_ENV` なしで動作させる案もある。しかし公開 CLI の読み取り系で前提条件を揃え、Agent Skill・execpolicy の記述を単純に保つことを優先し、`task-status` と同じ扱いとする。

### 3.2 オプション

| オプション                | 値                               | 既定値 | 説明                                                                                  |
| ------------------------- | -------------------------------- | ------ | ------------------------------------------------------------------------------------- |
| `--repository <identity>` | 文字列                           | なし   | `context.repository.identity` の完全一致で絞り込む                                    |
| `--worktree <path>`       | 絶対 path                        | なし   | `context.worktree.path` との一致で絞り込む（正規化は [§3.3](#33-フィルタの照合規則)） |
| `--type <type>`           | 文字列                           | なし   | `task.type` の完全一致で絞り込む                                                      |
| `--status <status>`       | `task.status` の値（複数指定可） | なし   | `task.status` で絞り込む。複数回指定した場合は **いずれかに一致（OR）**               |
| `--limit <n>`             | 整数 `1`〜`1000`                 | `100`  | 返却する task の最大件数。ソート後に先頭から切り出す                                  |
| `--json`                  | なし                             | —      | JSON 出力を明示する。出力は常に JSON のため動作は変わらない（[§3.5](#35-出力形式)）   |

- 異なるオプション間は **AND** 条件とする。
- フィルタは canonical schema v1 に存在する項目に限定する。派生分類 `overview.state` によるフィルタは MVP では提供しない（[§9](#9-対象外と将来拡張)）。
- 入力エラーの検証境界と終了コードは次のように分ける（[§5.5](#55-終了コード)）。
  - argparse で検証する CLI 構文エラー（`--status` に `TASK_STATUSES` 外の値、`--limit` に範囲外の値や非整数値、未知のオプションなど）は **exit 2** とし、stderr に usage を出力する。
  - CLI ハンドラ内部で検証するセマンティックな入力エラー（`--worktree` に相対 path を与えた場合など）は **exit 1** とし、stdout に `invalid_input` の error JSON を出力する。

### 3.3 フィルタの照合規則

| フィルタ       | 照合方法                                                                                                                                                                          |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--repository` | 文字列の完全一致。正規化しない（identity は [§11.0](herdr-orchestrator/06-results-and-workflows.md#sec-11-0) の canonical 文字列であり、path ではない）                           |
| `--worktree`   | 入力を `Path(input).resolve(strict=False)` で正規化し、Registry 値との文字列一致を判定する。Registry 側は handoff 時に `resolve_worktree_root()` で解決済みの値のため正規化しない |
| `--type`       | 文字列の完全一致                                                                                                                                                                  |
| `--status`     | `task.status` が指定値のいずれかと一致                                                                                                                                            |

`--worktree` は worktree が既に削除されていても照合できるよう `strict=False` で解決する。worktree の存在確認は行わない。

### 3.4 ソート順と件数制限

1. フィルタに一致した task を `timestamps.created_at` の **降順**（新しい順）に並べる。同時刻は `task.id` の昇順で並べ、出力を決定的にする。
2. `created_at` は `datetime.fromisoformat()` で解析し、タイムゾーンなしの値は UTC とみなす（既存 `_parse_registry_timestamp` と同じ規則）。解析できない task は末尾に置き、その task の `overview.warnings` に `created_at_unparseable` を付ける（task 自体は canonical schema 上は有効なため異常データ扱いにしない）。
3. ソート後、先頭から `--limit` 件を返す。

件数制限による切り詰め（`truncated`）と、読み取り失敗による不完全性（`complete`）は **別のフィールドで表現** し、混同しない（[§4.2](#42-トップレベルフィールド)）。

#### `truncated=true` 時の Agent 利用手順

`--limit`（既定値: 100）を超える task が存在する場合、`truncated=true` となり古い task が結果から除外される。クラッシュ後の調査において対象 task が一覧に含まれない可能性があるため、Agent は次の規則に従う。

1. **切り詰め通知**: `truncated=true` の場合、Agent は「取得結果が最大件数（`limit`）で切り詰められている」旨を利用者に報告する。
2. **安易な不存在断定の禁止**: `truncated=false`（全件取得）でない限り、「対象の task が存在しない」と断定してはならない。
3. **絞り込み・上限変更による再確認**: 目的の task が一覧に見当たらない場合、`--limit` を増やす（例: `--limit 500`）、または `--repository` / `--worktree` / `--type` / `--status` などのフィルタを指定して照合対象を絞り込んで再実行する。MVP でのページング機能は提供しない。
4. **上限 1000 件超過時の人間へのエスカレーション**: フィルタで最大限絞り込んでも一致件数が `--limit` の最大値（1000 件）を超え、目的の task が切り詰めから漏れている可能性がある場合、Agent は独断で不存在と判断せず、利用者（人間）に対象 task の ID または詳細条件を報告して指示を仰ぐ。必要に応じて将来拡張（[§9.2](#92-将来拡張の候補)）の `--since` やページングの導入、または手動調査を検討する。

### 3.5 出力形式

既存の公開 CLI はすべて JSON を標準出力に出す（`emit_json`）。`task-list` も同じく **JSON のみ** を出力する。人間向けの表形式出力は MVP では提供しない。

Issue #62 の実行例（`--json`）との互換のため `--json` フラグは受け付けるが、指定の有無で出力は変わらない。

### 3.6 実行例

```bash
# Registry に残存する全 task（新しい順に最大 100 件）
herdr-orchestrator task-list

# 対象 repository の task
herdr-orchestrator task-list --repository <repository-identity>

# 現在の worktree の task
herdr-orchestrator task-list --worktree "$(git rev-parse --show-toplevel)"

# review task の JSON
herdr-orchestrator task-list --type review --json

# task.status が未確定の task
herdr-orchestrator task-list --status created --status in_progress --status unknown
```

`--status` は `task.status` 軸だけを見る。`task.status` が terminal でも completion が未完了の task（例: `task.status=succeeded` かつ `completion.status=pending`）は含まれないため、未完了の網羅的な確認には `overview.state`（[§4.4](#44-派生分類-overviewstate)）を使う。

---

## 4. 出力契約

### 4.1 正常時の出力例

```json
{
  "command": "task-list",
  "list_format_version": 1,
  "listed_at": "2026-10-22T03:00:00+00:00",
  "registry": {
    "exists": true
  },
  "filters": {
    "repository": "4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
    "worktree": null,
    "type": null,
    "status": []
  },
  "sort": "created_at_desc",
  "limit": 100,
  "complete": true,
  "total_matched": 2,
  "returned": 2,
  "truncated": false,
  "tasks": [
    {
      "task": {
        "id": "task_7f3c81a2-75dd-4b75-a412-6e887eb62bd5",
        "type": "review",
        "mode": "async",
        "status": "in_progress"
      },
      "context": {
        "repository": { "identity": "4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f" },
        "worktree": {
          "path": "/home/user/Gitrepo/repo",
          "branch": "development"
        }
      },
      "requester": {
        "role": "design",
        "agent_name": "r-0123456789abcdef-design"
      },
      "worker": {
        "role": "reviewer",
        "agent_name": "r-0123456789abcdef-reviewer",
        "pane_id": "w1:p2"
      },
      "dispatch": {
        "status": "sent",
        "herdr_prompt_started_at": "2026-10-22T02:50:00+00:00"
      },
      "result": { "status": "pending" },
      "completion": {
        "action": "resume_requester",
        "status": "pending",
        "recovery_count": 0
      },
      "timestamps": {
        "created_at": "2026-10-22T02:50:00+00:00",
        "updated_at": "2026-10-22T02:50:01+00:00"
      },
      "overview": {
        "state": "open",
        "warnings": []
      }
    },
    {
      "task": {
        "id": "task_2a1b3c4d-5e6f-4a5b-8c7d-9e0f1a2b3c4d",
        "type": "review",
        "mode": "async",
        "status": "succeeded"
      },
      "context": {
        "repository": { "identity": "4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f" },
        "worktree": {
          "path": "/home/user/Gitrepo/repo",
          "branch": "development"
        }
      },
      "requester": {
        "role": "design",
        "agent_name": "r-0123456789abcdef-design"
      },
      "worker": {
        "role": "reviewer",
        "agent_name": "r-0123456789abcdef-reviewer",
        "pane_id": "w1:p2"
      },
      "dispatch": {
        "status": "sent",
        "herdr_prompt_started_at": "2026-10-22T02:30:00+00:00"
      },
      "result": { "status": "succeeded" },
      "completion": {
        "action": "resume_requester",
        "status": "sent",
        "recovery_count": 0
      },
      "timestamps": {
        "created_at": "2026-10-22T02:30:00+00:00",
        "updated_at": "2026-10-22T02:45:00+00:00"
      },
      "overview": {
        "state": "closed",
        "warnings": []
      }
    }
  ],
  "anomalies": [],
  "concurrent_changes": []
}
```

### 4.2 トップレベルフィールド

| フィールド            | 型             | 意味                                                                                                                                                                                                                |
| --------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `command`             | `"task-list"`  | 既存 CLI と同じくコマンド名                                                                                                                                                                                         |
| `list_format_version` | 整数           | 本出力形式のバージョン。MVP は `1`。Registry の `schema_version` とは独立                                                                                                                                           |
| `listed_at`           | ISO 8601 (UTC) | ディレクトリ列挙を開始した時刻。一覧はこの時刻以降の各ファイル単位の状態であり、全体スナップショットではない                                                                                                        |
| `registry.exists`     | 真偽値         | tasks ディレクトリが存在したか。`false` の場合 `tasks` は空で `complete=true`                                                                                                                                       |
| `filters`             | オブジェクト   | 適用したフィルタ（正規化後の `worktree` を含む）。未指定は `null` / 空配列                                                                                                                                          |
| `sort`                | 文字列         | MVP は `"created_at_desc"` 固定                                                                                                                                                                                     |
| `limit`               | 整数           | 適用した上限                                                                                                                                                                                                        |
| `complete`            | 真偽値         | **`anomalies` が空のときに限り `true`**。読み取り対象として残存していた task に対する検証の完全性を示す。並行削除は `concurrent_changes` で通知され、本フラグは `true` を維持する（[§6.4](#64-並行更新との整合性)） |
| `total_matched`       | 整数           | 正常に読めた task のうちフィルタに一致した件数（`limit` 適用前）                                                                                                                                                    |
| `returned`            | 整数           | `tasks` の要素数                                                                                                                                                                                                    |
| `truncated`           | 真偽値         | `total_matched > returned` のとき `true`                                                                                                                                                                            |
| `tasks`               | 配列           | [§4.3](#43-task-要素のフィールド) の要素                                                                                                                                                                            |
| `anomalies`           | 配列           | 読み取り・検証に失敗したエントリ（[§5.2](#52-anomaly-の表現)）。**フィルタに関係なくすべて報告する**                                                                                                                |
| `concurrent_changes`  | 配列           | 列挙後・読み取り前に消えたエントリ（[§6.4](#64-並行更新との整合性)）。完全性には影響しない                                                                                                                          |

利用側は次の順で判定する。

```text
exit code = 0 かつ complete = true   → 一覧は完全（truncated=true なら件数上限で切り詰め）
exit code = 3 かつ complete = false  → 一覧は不完全。anomalies を確認し、利用者へ報告する
exit code = 1                         → 一覧を取得できていない（error JSON）
```

### 4.3 task 要素のフィールド

canonical schema v1 から次のキーだけを射影する。キーが Registry 上で `null` の場合も省略せず `null` を出力する。

| パス                                                                    | 備考                                          |
| ----------------------------------------------------------------------- | --------------------------------------------- |
| `task.id` / `task.type` / `task.mode` / `task.status`                   | `task.instruction` は含めない                 |
| `context.repository.identity`                                           |                                               |
| `context.worktree.path` / `context.worktree.branch`                     | `commit` は一覧での判断に不要なため含めない   |
| `requester.role` / `requester.agent_name`                               | requester の再解決は `task-status` 経由で行う |
| `worker.role` / `worker.agent_name` / `worker.pane_id`                  | Herdr UI で worker を確認するための識別情報   |
| `dispatch.status` / `dispatch.herdr_prompt_started_at`                  | §6.12 の A/B 判別に必要                       |
| `result.status`                                                         | `ref` / `summary` は含めない                  |
| `completion.action` / `completion.status` / `completion.recovery_count` |                                               |
| `timestamps.created_at` / `timestamps.updated_at`                       |                                               |
| `overview.state` / `overview.warnings`                                  | 派生値（[§4.4](#44-派生分類-overviewstate)）  |

`dispatch.reason` / `completion.reason` は自由記述を含み得るため一覧には含めない。`handoff_execution` は診断用 best-effort 記録（[§6.2](herdr-orchestrator/03-task-registry.md#sec-6-2)）であり、一覧には含めない。

### 4.4 派生分類 `overview.state`

Issue #62 の受け入れ条件「完了済み・未完了・不確定な task を区別して確認できる」を満たすため、4軸（`task.status`, `dispatch.status`, `result.status`, `completion.status`）から決定的に計算する派生分類を付与する。**Registry には保存しない。** 判定は上の行から順に評価し、最初に一致した分類を採用する。

| 順  | `overview.state` | 判定条件（4軸照合）                                                                                                                                                                      | 意味                                                                                                |
| --- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 1   | `uncertain`      | `task.status = unknown`、または (`dispatch.status = uncertain` かつ `result.status = pending`)、または `completion.status = uncertain`                                                   | worker への配送成否または requester への復帰配送成否が観測できていない。blind resend 禁止の対象     |
| 2   | `open`           | `task.status ∈ {created, in_progress}`、または `result.status = pending`、または `completion.status ∈ {pending, processing, failed}`                                                     | 通常フロー（タスク処理中・結果待ち）または明示的な復旧操作（completion 再開）による続きが残っている |
| 3   | `closed`         | 上記以外（正常系では `task.status ∈ {succeeded, failed, cancelled}` かつ `result.status ∈ {succeeded, failed, unavailable}` かつ `completion.status ∈ {not_applicable, sent, skipped}`） | task・result・completion がすべて terminal。処理は完結している（成功・失敗は問わない）              |

#### 条件の詳細と4軸状態遷移との整合性

1. **`dispatch.status = uncertain` の扱い**:
   - 初回 dispatch が timeout / stalled となった直後は `task=unknown`, `dispatch=uncertain`, `result=pending` となり、第1行により `uncertain` に分類される。
   - その後、worker から遅れて成果物が提出（`result-submit`）された場合、マトリクス（[§6.3](herdr-orchestrator/03-task-registry.md#sec-6-3)）に従って `dispatch.status=uncertain` は履歴として保持されたまま `task.status` が `succeeded` / `failed`、`result.status` が `succeeded` / `failed` へ遷移する。このとき結果が確定しているため第1行の `result.status = pending` 条件を満たさず、第2行（completion 未完了なら `open`）または第3行（completion 完了なら `closed`）へ適切に遷移する。
2. **`result.status` の影響**:
   - `result.status = pending` は worker の処理が未完了、または成果物提出待ちであることを示すため、`open`（または dispatch 未確定時は `uncertain`）の判定根拠として機能する。
   - `result.status` が `succeeded` / `failed` / `unavailable` に到達して初めて `closed` の候補となる。
3. **`completion.status` の扱い**:
   - `completion.status = failed` は requester への復帰配送が失敗した terminal 状態だが、`reset-completion` / `recover-completion` による対応余地があるため `open` に分類する。
   - `completion.status = uncertain` は復帰配送の成否が不明であるため、安全のため第1行で `uncertain` に分類する。
4. **到達不能な4軸組み合わせ（状態不整合）の判定基準（不変条件リスト）**:
   - canonical schema の `validate_loaded_task()` は各軸の Enum 値と `validate_mode_action_consistency` を検査するが、状態遷移マトリクスの全組み合わせ整合までは拘束しない。
   - クラッシュや外部手動改変等により、状態遷移マトリクス（[§6.3](herdr-orchestrator/03-task-registry.md#sec-6-3)）および completion 仕様（[§9.13](herdr-orchestrator/05-completion-and-recovery.md#sec-9-13), [§9.14](herdr-orchestrator/05-completion-and-recovery.md#sec-9-14)）に反する状態不整合が存在する場合、schema 上はロード可能なため anomaly（exit 3）とはせず一覧に含めつつ、`overview.warnings` に `"inconsistent_state_combination"` を付与する。
   - **判定に用いる不変条件リスト（以下のいずれかに違反した場合に warning 付与）**:
     - **I1（初回作成と配送）**: `dispatch.status = pending` ⇔ `task.status = created`
     - **I2（未完了タスクの結果待ち）**: `task.status ∈ {created, in_progress, unknown}` ⇒ `result.status = pending`
     - **I3（タスク完了と結果終端の整合）**:
       - `task.status = succeeded` ⇔ `result.status = succeeded`
       - `task.status = failed` ⇔ `result.status ∈ {failed, unavailable}`
     - **I4（進行中・不確定時の配送状態）**:
       - `task.status = in_progress` ⇒ `dispatch.status = sent`
       - `task.status = unknown` ⇒ `dispatch.status = uncertain`
     - **I5（初回配送失敗・スキップ時の終端整合）**:
       - `dispatch.status ∈ {failed, skipped}` ⇒ `task.status = failed` ∧ `result.status = unavailable` ∧ `completion.status ∈ {skipped, not_applicable}`
     - **I6（完了復帰処理と結果終端性）**:
       - `completion.status ∈ {processing, sent, failed, uncertain}` ⇒ `result.status ∈ {succeeded, failed, unavailable}` ∧ `dispatch.status ∈ {sent, uncertain}`
     - **I7（MVP 未実装状態の検出）**:
       - `task.status = cancelled` は MVP では遷移イベントが無効（将来拡張用）であるため、検出された場合は warning 対象とする。
     - ※ 本リストは違反時に確実に不整合と言える最小限の条件を定めたものであり、すべての到達不能な組み合わせを網羅的に検出するものではない（warning が付与されないことは 4軸の完全な整合を保証するものではない）。
   - **正当に到達し得るケース（誤判定してはならない正常状態）**:
     - 遅延 result 到達時: `dispatch=uncertain`, `task=succeeded|failed`, `result=succeeded|failed` は I1〜I7 すべてを満たし warning 対象外。
     - worker unavailable: `task=failed`, `dispatch=sent`, `result=unavailable` は I1〜I7 を満たし warning 対象外。
     - completion リセット後: `reset-completion` により `completion.status` が `pending` に戻った状態も I1〜I7 を満たし warning 対象外。
   - **状態分類（`overview.state`）の決定**:
     - 不整合が検出された場合でも、`overview.state` は安全側に倒し、判定表の優先順位（1: `uncertain` → 2: `open` → 3: `closed`）に従って機械的・決定的に判定する（未完了軸が1つでも残っていれば `open` または `uncertain` となる）。
5. **注意点**:
   - `closed` は「Orchestrator 上の処理が終わった」ことを示し、タスクの成功を意味しない。成否は `task.status` / `result.status` で確認する。
   - `open` であっても worker が現在稼働中とは限らない。`task.status=in_progress` のまま worker が失われている可能性がある（[§7](#7-クラッシュ後の利用シナリオ)）。
   - 分類規則を変更する場合は `list_format_version` を上げる。

`overview.warnings` は task 単位の注意事項を列挙する。MVP で定義する値は次の2点とする。

- `created_at_unparseable`: `timestamps.created_at` を ISO 8601 時刻として解析できなかった場合。
- `inconsistent_state_combination`: 4軸状態の組み合わせが不変条件 I1〜I7 のいずれかに違反している場合。

---

## 5. 異常データの扱い

### 5.1 列挙対象と除外対象

tasks ディレクトリの直下エントリを `os.scandir()` で列挙し、名前で次のように分類する。

| エントリ名                                 | 扱い                                                                |
| ------------------------------------------ | ------------------------------------------------------------------- |
| `task_*.json`                              | **task 候補**。[§5.3](#53-task-候補の読み取り手順) の手順で読み取る |
| `task_*.json.lock` / `task_*.handoff.lock` | Registry の付随ファイル。無視する                                   |
| `.task_*.json.tmp.*`                       | atomic write の一時ファイル。無視する                               |
| `last_cleanup_at` / `cleanup.run.lock`     | cleanup の付随ファイル。無視する                                    |
| 上記以外                                   | 無視する（Registry 外のファイルを task とみなさない）               |

`task_*.json` に一致するがファイル名の stem が `validate_task_id()` を通らないものは、task 候補として扱ったうえで `invalid_filename` の anomaly とする。

### 5.2 anomaly の表現

```json
{
  "file": "task_0b8c...json",
  "task_id": "task_0b8c3c55-1d2e-4f3a-9b4c-5d6e7f8a9b0c",
  "code": "invalid_schema",
  "message": "invalid dispatch.status"
}
```

| フィールド | 内容                                                                                                            |
| ---------- | --------------------------------------------------------------------------------------------------------------- |
| `file`     | tasks ディレクトリからの相対ファイル名（絶対 path は出さない）                                                  |
| `task_id`  | ファイル名 stem が有効な task ID ならその値、そうでなければ `null`。**JSON 本文中の値は信頼しないため使わない** |
| `code`     | 下表のいずれか                                                                                                  |
| `message`  | 原因の短い説明。`RegistryError.message` または OSError の `strerror` を用い、**ファイル内容の断片を含めない**   |

| `code`               | 条件                                                                                                            |
| -------------------- | --------------------------------------------------------------------------------------------------------------- |
| `invalid_filename`   | ファイル名 stem が `task_<uuidv4>` 形式でない                                                                   |
| `unsafe_entry`       | symlink、通常ファイル以外、所有者が現在ユーザーでない（[§6.9](herdr-orchestrator/03-task-registry.md#sec-6-9)） |
| `read_error`         | `open` / `read` が `ENOENT` 以外の OSError で失敗（権限不足など）                                               |
| `invalid_encoding`   | UTF-8 として復号できない                                                                                        |
| `invalid_json`       | JSON として解析できない、またはルートがオブジェクトでない                                                       |
| `unsupported_schema` | `schema_version` が `1` でない                                                                                  |
| `invalid_schema`     | `validate_loaded_task()` が `invalid_schema` / `invalid_task_id` で失敗                                         |
| `task_id_mismatch`   | 内容の `task.id` がファイル名 stem と一致しない                                                                 |

### 5.3 task 候補の読み取り手順

```text
1. os.open(path, O_RDONLY | O_NOFOLLOW)            # symlink を開かない（ELOOP → unsafe_entry）
   ENOENT → concurrent_changes へ記録して次へ
2. os.fstat(fd) で通常ファイル・所有者 uid を検証   # 開いた inode 自体を検証する
3. fd から全量を読み、UTF-8 で復号、json.loads
4. ルートがオブジェクトであること、schema_version、validate_loaded_task()
5. task.id とファイル名 stem の一致
6. フィルタ判定・射影・overview 計算
```

`validate_loaded_task()` は既存の検証ロジックをそのまま再利用し、`task-status` と同じ基準で「正常な task」を判定する。一方 `read_task_document_unlocked()` は path ベースの `is_symlink()` 検査と `read_text()` の間に競合窓があり、内部で `ensure_registry_layout()` を経由する呼び出し経路もあるため、`task-list` では使わずに上記の fd ベース読み取りを新設する。

### 5.4 フィルタとの関係

anomaly となったエントリは内容を信頼できず、所属 repository / worktree / type を判定できない。そのため **anomaly はフィルタに関係なくすべて報告し、1件でもあれば `complete=false`** とする。

この結果、別 repository の task ファイル1件の破損でも、対象 repository の一覧が `complete=false` になる。これは意図した保守的な挙動であり、「対象 repository の task が破損ファイルの中に含まれていない」ことを `task-list` は証明できないためである。

### 5.5 終了コード

本コマンドの終了コード契約を次のとおり定める（exit 3 は本コマンドで新設）。

| exit code | 状態                          | 条件                                                                                                                                             | 出力先 | 出力形式                                                                     |
| --------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------ | ---------------------------------------------------------------------------- |
| `0`       | 正常（完全一覧）              | 一覧取得に成功し、`anomalies` が空（`complete=true`）。0件・`truncated` を含む                                                                   | stdout | [§4.1](#41-正常時の出力例) の一覧 JSON                                       |
| `3`       | 部分成功（不完全一覧）        | 一覧取得は行えたが、`anomalies` が1件以上（`complete=false`）                                                                                    | stdout | 一覧 JSON（正常取得できた task と `anomalies` の両方を含む）                 |
| `1`       | 実行時エラー / 入力検証エラー | 前提条件違反（`HERDR_ENV` 不足）、CLI 内部でのセマンティック入力検証エラー（`--worktree` 相対パス等）、Registry ディレクトリ自体の検証・列挙失敗 | stdout | 既存形式の error JSON (`{"error": ..., "retryable": false, "message": ...}`) |
| `2`       | CLI 構文エラー                | argparse の引数パースエラー（未知のオプション、`choices` 違反、型変換失敗等）                                                                    | stderr | argparse 既定の usage / エラーテキスト（非 JSON）                            |

#### 終了コードと出力形式の契約保証

1. **exit 3 の保証**:
   - exit 3 は「一覧取得自体は実行できたが、破損や不正なエントリが存在した」ことを表す専用コードである。
   - **契約保証**: exit 3 の場合であっても、検証を通過した正常な task は `tasks` 配列に格納され、`anomalies` とともに標準出力の一覧 JSON として必ず返却される。
2. **exit 2 と exit 1 の検証境界**:
   - **exit 2（argparse 構文エラー）**: CLI 呼び出しの構文レベルの不備（例: `--status` に未定義の値を指定、`--limit` に数値以外を指定など）。argparse の標準機能により stderr に使用方法が出力される。
   - **exit 1（セマンティック入力・環境エラー）**: 引数の構文は正しいが、ドメイン要件を満たさない場合（例: `--worktree` に相対パスが渡された場合、`HERDR_ENV=1` が未設定の場合）や、Registry ディレクトリ自体のアクセス権・symlink 不正。stdout に機械可読な error JSON が出力される。
3. **呼び出し Agent による機械的エラー処理手順**:
   - `exit = 0`: stdout の JSON を取得。`complete=true` であり完全な一覧として処理する（`truncated=true` なら件数切り詰めを認識）。
   - `exit = 3`: stdout の JSON を取得。`complete=false` を認識し、取得できた task と `anomalies` の内容を利用者へ報告する。
   - `exit = 1`: stdout の JSON を parse し、`error` / `message` を取得して利用者に失敗原因を報告する。
   - `exit = 2`: stderr のテキストを取得し、CLI 呼び出し構文の誤りとして利用者に報告またはコマンドを修正する。

### 5.6 一覧取得自体のエラー

| `error`                | 条件                                                                                                                                                                                | `retryable` |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| `herdr_env_required`   | `HERDR_ENV=1` でない（既存 `HerdrEnvError` の code に従う）                                                                                                                         | `false`     |
| `invalid_input`        | `--worktree` が相対 path                                                                                                                                                            | `false`     |
| `registry_unsafe_path` | tasks ディレクトリまたはその親（`herdr-orchestrator/`）が symlink・ディレクトリでない・所有者不一致・group/world writable（[§6.9](herdr-orchestrator/03-task-registry.md#sec-6-9)） | `false`     |
| `registry_unreadable`  | tasks ディレクトリの列挙（`scandir`）が OSError で失敗                                                                                                                              | `false`     |

これらの場合は一覧 JSON を出さず、既存の `{"error": ..., "retryable": ..., "message": ...}` 形式で exit 1 とする。Registry ディレクトリ自体を信頼できない状態で部分的な一覧を返すと、利用側が誤って「該当 task なし」と判断し得るためである。

---

## 6. 実装設計

### 6.1 モジュール構成

| 変更対象                                   | 内容                                                                                                   |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `orchestrator/registry/listing.py`（新規） | ディレクトリ検証、エントリ列挙、fd ベース読み取り、anomaly 分類。Registry 内部構造の知識をここに閉じる |
| `orchestrator/list_commands.py`（新規）    | フィルタ・ソート・`limit`・射影・`overview` 計算・出力 payload 組み立て、exit code 決定                |
| `orchestrator/public_cli.py`               | `PUBLIC_COMMANDS` / `_COMMAND_HELP` / `build_parser()` / handler に `task-list` を追加                 |
| `orchestrator/registry/schema.py`          | 変更しない（`validate_loaded_task()` と状態定数を import して再利用）                                  |
| `orchestrator/registry/io.py`              | 変更しない                                                                                             |

`task-registry.py`（内部 writer CLI）には一覧 operation を追加しない。読み取り専用処理を writer の operation 群と分離するためである。

### 6.2 Registry ディレクトリの読み取り

`ensure_registry_layout()` は作成・`chmod` を伴うため使わない。`listing.py` に副作用のない検証関数を用意する。

```text
1. paths.tasks_dir() で tasks ディレクトリを得る
2. tasks ディレクトリが存在しない（lstat が ENOENT）
     → registry.exists=false、tasks=[]、complete=true、exit 0
3. 親 herdr-orchestrator/ と tasks ディレクトリについて lstat で次を検証
     - symlink でない / ディレクトリである / 所有者が現在ユーザー / group・world writable でない
   不一致 → registry_unsafe_path（exit 1）
4. os.scandir(tasks_dir) で直下エントリを列挙
   OSError → registry_unreadable（exit 1）
```

検証条件は既存 `_assert_private_directory()` と同等とし、判定ロジックを共通化する場合も **作成・`chmod` を行わない経路** を分離する。

### 6.3 処理フロー

```text
require_herdr_env()
引数検証（--worktree の絶対 path 確認と正規化）
listed_at = 現在時刻
ディレクトリ検証・列挙（§6.2）
for entry in 列挙結果（名前順）:
    除外対象なら skip（§5.1）
    読み取り（§5.3）→ 成功: records へ / 失敗: anomalies へ / ENOENT: concurrent_changes へ
records をフィルタ（§3.3）→ total_matched
created_at 降順・task.id 昇順でソート（§3.4）
先頭 limit 件を射影（§4.3）し overview を付与（§4.4）
complete = (anomalies が空)
emit_json(payload, exit_code = 0 if complete else 3)
```

### 6.4 並行更新との整合性

Registry の更新は一時ファイルへの書き込みと `os.replace()` による atomic replace で行われる（[§6.4](herdr-orchestrator/03-task-registry.md#sec-6-4)、`io._atomic_write_json()`）。このため `task-list` は lock を取らず、次の性質に依拠する。

1. **`complete` の定義と完全性の範囲**:
   - `complete` は **「読み取り対象として残存していた task に対する検証の完全性（破損・検証失敗した anomaly が 0 件であること）」** と定義する。
   - Registry 全体の原子的スナップショットであることを保証するものではない。
2. **列挙後の並行削除と `concurrent_changes`**:
   - `os.scandir()` の列挙開始から個々のファイル `open` までの間に、lazy cleanup（[§6.8](herdr-orchestrator/03-task-registry.md#sec-6-8)）や別プロセスによってファイルが削除された場合（`open` が `ENOENT`）、そのエントリは `concurrent_changes` に `{"file": ..., "task_id": ..., "change": "removed"}` として記録する。
   - **`complete=true` を維持する理由**: 該当ファイルは Registry から正式に消滅したものであり、残存データの破損や読み取り失敗ではないため、データ整合性の観点では不完全（`anomaly`）と扱わない。
   - **Agent の判断**: Agent は `complete=true` だけを見て「列挙開始時点に存在した全 task を取得できた」と誤認してはならない。`concurrent_changes` に要素が存在する場合、「読み取り処理の最中に並行して削除された task がある」と判断し、必要に応じて再取得またはログを確認する。
3. **削除以外の並行更新の扱い**:
   - **列挙後の新規作成**: `listed_at` 以降に作成された task は今回の一覧には含まれないが、完全性（`complete`）には影響しない。
   - **読み取り中の atomic replace**: `open` に成功した fd は特定の inode を指す。読み取りの最中に別プロセスが `os.replace()` しても、旧 inode の完全な内容を読み切る。書きかけの断片を読むことはなく、完全性に影響しない。
   - **読み取り完了後の更新**: 一覧は各エントリの読み取り時点の状態を反映する。
4. **lock を取らない理由**:
   - 既存の `task_mutation_lock()` は lock file を `O_CREAT` で作成し、排他 lock のため writer を待たせる。読み取り専用・非干渉の原則（[§2.1](#21-厳密な読み取り専用)）に反するため使わない。

Registry 外の主体が非 atomic に直接書き込んだ途中のファイルを読んだ場合は `invalid_json` 等の anomaly として報告され、`complete=false`（exit 3）となる。

### 6.5 性能

保持期間 30 日（[§6.8](herdr-orchestrator/03-task-registry.md#sec-6-8)）により task 数は通常数百件程度に収まる想定である。全件を読み込んでからフィルタ・ソートする単純な実装とし、index ファイルやキャッシュは導入しない（導入すると Registry に新たな書き込み主体が生じる）。`--limit` は出力サイズ（Agent のコンテキスト消費）の制御を主目的とする。

---

## 7. クラッシュ後の利用シナリオ

### 7.1 想定手順

1. requester Agent が reviewer へ async task を handoff する。
2. WSL の OOM または Windows の強制終了で処理が中断する。
3. PC / WSL を再起動し、Herdr と Agent を起動し直す（環境変数 `HERDR_ENV=1` が有効な状態）。
4. 人間が Agent へ作業再開を指示する。
5. Agent が `herdr-orchestrator task-list --worktree "$(git rev-parse --show-toplevel)"` を実行する。
6. **exit code と出力の確認**:
   - `exit = 0` かつ `complete=true`: 正常に一覧を取得完了。
   - `exit = 3`（`complete=false`）: 一覧取得はできたが一部ファイルが破損。`anomalies` を利用者へ報告し、一覧が不完全であることを明示した上で、取得できた `tasks` を確認する。
   - `exit = 1`: 一覧取得自体が失敗（ディレクトリ権限不正等）。エラーメッセージを利用者へ報告して停止する。
   - `exit = 2`: コマンド引数エラー。引数指定を修正する。
7. **切り詰めと並行変更の確認**:
   - `truncated=true` の場合、Agent は最大件数で切り詰められている旨を利用者へ報告する。対象 task が見当たらない場合、`truncated=false` でない限り不存在と断定せず、`--limit` を増やすかフィルタ条件（`--status` 等）で絞り込んで再実行する。
   - `concurrent_changes` に削除記録がある場合、並行クリーンアップ等の影響を考慮する。
8. **未完了・不確定 task の詳細確認**:
   - `overview.state` が `open` / `uncertain` の task について `task-status <task-id>` / `task-result <task-id>` で詳細を確認する。
9. **報告と判断**:
   - 確定済み・未完了・不確定の task を利用者へ報告する。
   - 利用者の判断または既存の運用ルールに従い、継続（`result-submit` 待ち・`recover-completion` 等）・放棄・新規 handoff を選ぶ。

### 7.2 分類ごとの読み方

| 典型的な状態                                                      | `overview.state` | 次の確認・操作（すべて人間または明示的な判断を経る）                                                                                 |
| ----------------------------------------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `task=created`, `dispatch=pending`                                | `open`           | `doctor` の stale dispatch 候補と照合し、[§6.12](herdr-orchestrator/03-task-registry.md#sec-6-12) の `reconcile-dispatch` を検討     |
| `task=in_progress`, `dispatch=sent`, `result=pending`             | `open`           | worker の生存・成果物を確認。worker 消失時は [§10.2](herdr-orchestrator/06-results-and-workflows.md#sec-10-2) の再登録または人間判断 |
| `task=unknown`, `dispatch=uncertain`                              | `uncertain`      | blind resend しない。worker 側の痕跡・成果物を確認                                                                                   |
| `task=succeeded`, `completion=pending` / `processing` / `failed`  | `open`           | `recover-completion` / `resume-task` を検討（[§9](herdr-orchestrator/05-completion-and-recovery.md#sec-9)）                          |
| `completion=uncertain`                                            | `uncertain`      | requester への到達を確認してから `reset-completion` を検討                                                                           |
| `task=succeeded/failed`, `completion=not_applicable/sent/skipped` | `closed`         | 必要なら `task-result` で成果物を確認                                                                                                |

### 7.3 重要な制約

- `task.status=in_progress` は worker が現在稼働中であることを意味しない。
- `result.status=pending` を失敗・完了のどちらともみなさない。
- `task-list` の実行だけで task 状態は変わらない。
- 過去 task の自動再配送は行わない。
- 失われた task や成果物の復元は要求しない。

---

## 8. 既存仕様・成果物への影響

本設計を実装する際、正本の一箇所性（[AGENTS.md](../AGENTS.md) 文書編集ルール）を保つため次を更新する。出力契約の正本は本設計書とし、主設計書からはリンクする。

| 対象                                                                       | 更新内容                                                                                                                                                                      |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [§6.5 Public CLI Contract](herdr-orchestrator/03-task-registry.md#sec-6-5) | 表に `task-list` 行を追加（入力: フィルタ・`limit`、副作用: none、出力: 一覧 JSON、終了コード: 0 / 1 / 2 / 3。exit 3 は部分成功専用コードとして新設）。詳細は本設計書へリンク |
| [設計書の目次](herdr-orchestrator-design.md)                               | 「機能設計・改訂案」に本設計書を追加                                                                                                                                          |
| `skills/herdr-orchestrator/SKILL.md` / `references/public-cli.md`          | 読み取り系コマンドとして `task-list` を追記。クラッシュ後の確認手順に [§7.1](#71-想定手順) を要約して記載                                                                     |
| `skills/herdr-orchestrator/install/codex-execpolicy-snippet.rules`         | 読み取り系 allow ルール（`doctor` / `task-status` / `task-result`）に `task-list` を追加                                                                                      |
| `README.md`                                                                | コマンド一覧に `task-list` を追加                                                                                                                                             |
| [実装 INDEX](herdr-orchestrator-implementation-index.md)                   | 段階 3（読み取り CLI）の追加項目として記載                                                                                                                                    |

`task-status` / `task-result` / `handoff` / `result-submit` / completion 系コマンドの動作、および Task Registry schema v1 は変更しない。

---

## 9. 対象外と将来拡張

### 9.1 本 Issue の対象外

- task の自動復旧・自動再実行・自動再配送
- task の手動キャンセル・強制完了
- Registry 破損データの修復・削除、Registry のバックアップ
- 削除済み task の復元
- reviewer 固有の履歴管理、Review ID / Chain ID の採番管理
- repository 固有 Skill の変更（Issue #61 側で扱う）

### 9.2 将来拡張の候補

必要になった時点で別 Issue として検討する。

| 候補                                                            | 概要                                                                       |
| --------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `--state <open\|uncertain\|closed>`                             | 派生分類 `overview.state` による絞り込み                                   |
| `--dispatch-status` / `--result-status` / `--completion-status` | 他の状態軸による絞り込み                                                   |
| `--here`                                                        | カレントディレクトリから repository identity / worktree を解決して絞り込む |
| `--since <datetime>` / ページング                               | 期間指定や大量件数への対応                                                 |
| 人間向け表形式出力                                              | `--format table` 等                                                        |

---

## 10. テスト計画

`tests/test_orchestrator_task_list.py` を新設し、`XDG_STATE_HOME` を一時ディレクトリへ向けて実行する。

### 10.1 正常系

- Registry ディレクトリが存在しない → `registry.exists=false`、空一覧、`complete=true`、exit 0。**ディレクトリが作成されていない**こと。
- ディレクトリは存在し task 0 件 → 空一覧、`complete=true`、exit 0。
- 複数 task → 4軸・射影フィールドが canonical 値と一致し、`instruction` / `result.ref` / `result.summary` / `reason` が含まれない。
- `--repository` / `--worktree` / `--type` / `--status`（単独・複数指定・組み合わせ）の AND / OR 条件。
- `--worktree` の正規化（末尾スラッシュ、`..` を含む絶対 path、存在しない path）と相対 path の拒否（exit 1、error JSON）。
- `created_at` 降順・同時刻の `task.id` 昇順。タイムゾーン混在（`+09:00` と `+00:00`）でも時刻順になる。
- `--limit` による切り詰めで `truncated=true`、`total_matched` が上限前の件数、`complete=true` のまま。
- `overview.state` の判定表（[§4.4](#44-派生分類-overviewstate)）の全条件（`uncertain` / `open` / `closed`）、`dispatch.status=uncertain` の結果到達前後の遷移。
- 不変条件 I1〜I7 の検証:
  - 正当到達ケース（遅延 result、worker unavailable、reset 後 pending）で `inconsistent_state_combination` が付与されないこと。
  - 各不変条件（I1〜I7）への違反ケースで `overview.warnings` に `inconsistent_state_combination` が付与され、優先順位に従って安全側に分類されること。
- lock / handoff.lock / 一時ファイル / cleanup stamp / 無関係ファイルが一覧にも anomaly にも現れない。

### 10.2 異常系

- 各 anomaly code（[§5.2](#52-anomaly-の表現)）を発生させ、`complete=false`・exit 3・正常な task は一覧に残り、**標準出力の JSON に tasks と anomalies の双方が含まれること**。
- フィルタで対象外になるはずの repository に属する破損ファイルでも `complete=false` になること。
- `message` にファイル内容の断片が含まれないこと。
- tasks ディレクトリが symlink / group writable / 所有者不一致 → `registry_unsafe_path`、exit 1、一覧 JSON なし。
- `scandir` 失敗（権限 `0000`）→ `registry_unreadable`、exit 1。
- `HERDR_ENV` 未設定 → exit 1。
- argparse による CLI 構文エラー（未定義オプション、型・choices 違反）→ exit 2、stderr に usage（非 JSON）。
- CLI 内部でのセマンティック入力エラー（`--worktree` 相対パス）→ exit 1、stdout に `invalid_input` error JSON。

### 10.3 読み取り専用性・並行性

- 実行前後で tasks ディレクトリの全エントリ（名前・内容・mode・mtime）が一致し、lock file が新規作成されないこと。
- 列挙後・読み取り前にファイルを削除するケース（`scandir` 結果をモックで差し込む）で `concurrent_changes` に記録され、`complete=true` のままであること。
- 読み取り中の atomic replace（fd open 後に `os.replace`）で旧内容が正しく読めること。
- lazy cleanup が起動されないこと（`last_cleanup_at` が変化しない）。

### 10.4 回帰

- 既存の `npm run test:orchestrator` がすべて成功すること。
- `herdr-orchestrator --help` に `task-list` が日本語ヘルプ付きで表示されること。

---

## 11. 受け入れ条件

- [ ] 公開 CLI `herdr-orchestrator task-list` で登録済み task を一覧取得できる。
- [ ] task ID、task 種別、repository identity、worktree path、worker 識別情報、4軸状態、作成日時を取得できる。
- [ ] repository / worktree / type / status による絞り込みができ、複数フィルタは AND で評価される。
- [ ] 出力 JSON の契約（[§4](#4-出力契約)）が文書化され、Agent が機械的に処理できる。
- [ ] task 0 件・Registry 未作成の場合に正常な空一覧を返す。
- [ ] `overview.state` により確定済み・未完了・不確定の task を区別できる。
- [ ] Registry の破損・読み取り不能を検出し、不完全な一覧を `complete=false` かつ exit 3 で返す。異常データを黙ってスキップしない。
- [ ] 並行更新時の挙動が [§6.4](#64-並行更新との整合性) どおりである。
- [ ] 一覧取得によって Registry のファイル・パーミッション・lock file が変更・作成されない。
- [ ] 既存の `task-status` / `task-result` / handoff / completion に回帰がない。
- [ ] 再起動後を想定したシナリオ（[§7](#7-クラッシュ後の利用シナリオ)）で残存 task を確認できる。

> [!NOTE]
> 本機能によってクラッシュ前の処理を必ず復旧できるようになるわけではない。目標は、残存 task の状態を把握し、利用者と Agent が安全に次の操作を判断できるようにすることである。
