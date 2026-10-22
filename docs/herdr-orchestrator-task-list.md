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
- `--status` に `TASK_STATUSES` 外の値、`--limit` に範囲外の値を与えた場合は argparse の `choices` / 型検証で拒否する（exit 2、argparse 既定）。
- `--worktree` に相対 path を与えた場合は `invalid_input` で exit 1 とする。

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
    }
  ],
  "anomalies": [],
  "concurrent_changes": []
}
```

### 4.2 トップレベルフィールド

| フィールド            | 型             | 意味                                                                                                         |
| --------------------- | -------------- | ------------------------------------------------------------------------------------------------------------ |
| `command`             | `"task-list"`  | 既存 CLI と同じくコマンド名                                                                                  |
| `list_format_version` | 整数           | 本出力形式のバージョン。MVP は `1`。Registry の `schema_version` とは独立                                    |
| `listed_at`           | ISO 8601 (UTC) | ディレクトリ列挙を開始した時刻。一覧はこの時刻以降の各ファイル単位の状態であり、全体スナップショットではない |
| `registry.exists`     | 真偽値         | tasks ディレクトリが存在したか。`false` の場合 `tasks` は空で `complete=true`                                |
| `filters`             | オブジェクト   | 適用したフィルタ（正規化後の `worktree` を含む）。未指定は `null` / 空配列                                   |
| `sort`                | 文字列         | MVP は `"created_at_desc"` 固定                                                                              |
| `limit`               | 整数           | 適用した上限                                                                                                 |
| `complete`            | 真偽値         | **`anomalies` が空のときに限り `true`**。フィルタ・`limit` の影響を受けない                                  |
| `total_matched`       | 整数           | 正常に読めた task のうちフィルタに一致した件数（`limit` 適用前）                                             |
| `returned`            | 整数           | `tasks` の要素数                                                                                             |
| `truncated`           | 真偽値         | `total_matched > returned` のとき `true`                                                                     |
| `tasks`               | 配列           | [§4.3](#43-task-要素のフィールド) の要素                                                                     |
| `anomalies`           | 配列           | 読み取り・検証に失敗したエントリ（[§5.2](#52-anomaly-の表現)）。**フィルタに関係なくすべて報告する**         |
| `concurrent_changes`  | 配列           | 列挙後・読み取り前に消えたエントリ（[§6.4](#64-並行更新との整合性)）。完全性には影響しない                   |

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

Issue #62 の受け入れ条件「完了済み・未完了・不確定な task を区別して確認できる」を満たすため、4軸から決定的に計算する派生分類を付与する。**Registry には保存しない。** 判定は上の行から順に評価し、最初に一致した分類を採用する。

| 順  | `overview.state` | 条件                                                                                                                  | 意味                                                            |
| --- | ---------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| 1   | `uncertain`      | `task.status = unknown`、または `completion.status = uncertain`                                                       | 配送・完了通知の成否が観測できていない。blind resend 禁止の対象 |
| 2   | `open`           | `task.status ∈ {created, in_progress}`、または `completion.status ∈ {pending, processing, failed}`                    | 通常フローまたは明示的な復旧操作による続きが残っている          |
| 3   | `closed`         | 上記以外（`task.status ∈ {succeeded, failed, cancelled}` かつ `completion.status ∈ {not_applicable, sent, skipped}`） | task と completion がともに terminal。成功・失敗は問わない      |

- `closed` は「処理が終わった」ことを示し、成功を意味しない。成否は `task.status` / `result.status` で確認する。
- `completion.status = failed` は requester への復帰配送が失敗した terminal 状態だが、`reset-completion` / `recover-completion` による対応余地があるため `open` に分類する。
- `open` であっても worker が稼働中とは限らない。`task.status=in_progress` のまま worker が失われている可能性がある（[§7](#7-クラッシュ後の利用シナリオ)）。
- 分類規則を変更する場合は `list_format_version` を上げる。

`overview.warnings` は task 単位の注意事項（例: `created_at_unparseable`）を列挙する。MVP で定義する値は `created_at_unparseable` のみとする。

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

| exit code | 条件                                                                           | 標準出力                                           |
| --------- | ------------------------------------------------------------------------------ | -------------------------------------------------- |
| `0`       | 一覧取得に成功し、`anomalies` が空（`complete=true`）。0件・`truncated` を含む | [§4.1](#41-正常時の出力例) の一覧 JSON             |
| `3`       | 一覧取得は行えたが、`anomalies` が1件以上（`complete=false`）                  | 一覧 JSON（読めた task と `anomalies` を両方含む） |
| `1`       | 前提条件違反・入力不正・Registry ディレクトリ自体の異常                        | 既存形式の error JSON                              |
| `2`       | argparse の引数エラー（`choices` / 型違反）                                    | argparse 既定の usage（stderr）                    |

exit `3` は既存の公開 CLI で未使用の値であり、「部分的な結果あり」を表す専用コードとして割り当てる。exit `1` と分けることで、呼び出し側は「一覧が全く取れていない」状況と「一覧は取れたが不完全」な状況を区別できる。

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

- **ファイル単位の整合性**: `open` した fd は特定の inode を指す。読み取り中に replace されても、旧 inode の完全な内容を読み切る。書きかけの JSON を読むことはない。
- **全体スナップショットではない**: 列挙後に作成された task は含まれず、各 task は読み取り時点の状態を示す。`listed_at` はその起点を示す。
- **列挙後の削除**: lazy cleanup（[§6.8](herdr-orchestrator/03-task-registry.md#sec-6-8)）等により列挙後に消えたファイルは `concurrent_changes` に `{"file": ..., "task_id": ..., "change": "removed"}` として記録する。存在しなくなった task は一覧の欠落ではないため `complete` には影響しない。
- **lock を取らない理由**: 既存の `task_mutation_lock()` は lock file を `O_CREAT` で作成し、排他 lock のため writer を待たせる。読み取り専用・非干渉の原則（[§2.1](#21-厳密な読み取り専用)）に反するため使わない。

Registry 外の主体が非 atomic に書き込んだ途中のファイルを読んだ場合は `invalid_json` 等の anomaly として報告される。

### 6.5 性能

保持期間 30 日（[§6.8](herdr-orchestrator/03-task-registry.md#sec-6-8)）により task 数は通常数百件程度に収まる想定である。全件を読み込んでからフィルタ・ソートする単純な実装とし、index ファイルやキャッシュは導入しない（導入すると Registry に新たな書き込み主体が生じる）。`--limit` は出力サイズ（Agent のコンテキスト消費）の制御を主目的とする。

---

## 7. クラッシュ後の利用シナリオ

### 7.1 想定手順

1. requester Agent が reviewer へ async task を handoff する。
2. WSL の OOM または Windows の強制終了で処理が中断する。
3. PC / WSL を再起動し、Herdr と Agent を起動し直す。
4. 人間が Agent へ作業再開を指示する。
5. Agent が `herdr-orchestrator task-list --worktree "$(git rev-parse --show-toplevel)"` を実行する。
6. exit code と `complete` を確認する。`complete=false` なら `anomalies` を利用者へ報告し、一覧が不完全であることを明示する。
7. `overview.state` が `open` / `uncertain` の task について `task-status` / `task-result` で詳細を確認する。
8. 確定済み・未完了・不確定の task を利用者へ報告する。
9. 利用者の判断または既存の運用ルールに従い、継続（`result-submit` 待ち・`recover-completion` 等）・放棄・新規 handoff を選ぶ。

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

| 対象                                                                       | 更新内容                                                                                                    |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| [§6.5 Public CLI Contract](herdr-orchestrator/03-task-registry.md#sec-6-5) | 表に `task-list` 行を追加（入力: フィルタ・`limit`、副作用: none、出力: 一覧 JSON）。詳細は本設計書へリンク |
| [設計書の目次](herdr-orchestrator-design.md)                               | 「機能設計・改訂案」に本設計書を追加                                                                        |
| `skills/herdr-orchestrator/SKILL.md` / `references/public-cli.md`          | 読み取り系コマンドとして `task-list` を追記。クラッシュ後の確認手順に [§7.1](#71-想定手順) を要約して記載   |
| `skills/herdr-orchestrator/install/codex-execpolicy-snippet.rules`         | 読み取り系 allow ルール（`doctor` / `task-status` / `task-result`）に `task-list` を追加                    |
| `README.md`                                                                | コマンド一覧に `task-list` を追加                                                                           |
| [実装 INDEX](herdr-orchestrator-implementation-index.md)                   | 段階 3（読み取り CLI）の追加項目として記載                                                                  |

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
- `--worktree` の正規化（末尾スラッシュ、`..` を含む絶対 path、存在しない path）と相対 path の拒否。
- `created_at` 降順・同時刻の `task.id` 昇順。タイムゾーン混在（`+09:00` と `+00:00`）でも時刻順になる。
- `--limit` による切り詰めで `truncated=true`、`total_matched` が上限前の件数、`complete=true` のまま。
- `overview.state` の判定表（[§4.4](#44-派生分類-overviewstate)）の全行。
- lock / handoff.lock / 一時ファイル / cleanup stamp / 無関係ファイルが一覧にも anomaly にも現れない。

### 10.2 異常系

- 各 anomaly code（[§5.2](#52-anomaly-の表現)）を発生させ、`complete=false`・exit 3・正常な task は一覧に残ること。
- フィルタで対象外になるはずの repository に属する破損ファイルでも `complete=false` になること。
- `message` にファイル内容の断片が含まれないこと。
- tasks ディレクトリが symlink / group writable / 所有者不一致 → `registry_unsafe_path`、exit 1、一覧 JSON なし。
- `scandir` 失敗（権限 `0000`）→ `registry_unreadable`、exit 1。
- `HERDR_ENV` 未設定 → exit 1。

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
