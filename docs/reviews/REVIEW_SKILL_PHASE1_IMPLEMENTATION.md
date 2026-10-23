# 内部レビュースキル改善 Phase 1 詳細実装設計書

> 状態: 実装設計初版。Issue #64 対応。
>
> 対象: このリポジトリの内部レビュー helper 実装。比較対象の固有情報は記載しない。

本書は、[Issue #61](https://github.com/okinawa-systembit/herdr-orchestration/issues/61) および [Issue #64](https://github.com/okinawa-systembit/herdr-orchestration/issues/64) に基づき、[Phase 1 設計書](REVIEW_SKILL_PHASE1_DESIGN.md) の各仕様を具体的に実装するための詳細設計を定義する。

## 目次

- [1. 目的と位置付け](#sec-1)
- [2. 配置・ファイル構成と権限](#sec-2)
- [3. 排他制御（共有 lock と inode 照合）](#sec-3)
- [4. 補助状態の仕様と安全な再初期化](#sec-4)
- [5. 採番と個別記録・INDEX の原子的保存](#sec-5)
- [6. helper CLI インターフェース設計](#sec-6)
- [7. テスト計画と外部レビュー確認事項](#sec-7)

<a id="sec-1"></a>

## 1. 目的と位置付け

Phase 1 設計書で「実装着手前の詳細実装設計で定める」と保留されていた次の仕様を確定する。

1. Git common directory 内の非公開状態領域の配置、形式、アクセス権限
2. 共有 lock ファイルの固定パス、作成手順、および `fstat` / `stat` による inode 照合手順
3. 補助状態（`state.json`）のファイル形式、instance ID 生成規則、破損検出判定
4. 旧 Task 放棄確認の明示的操作仕様と、`task-list` 連携による再初期化フロー
5. repository 固有 helper CLI のモジュール構成とコマンド仕様

<a id="sec-2"></a>

## 2. 配置・ファイル構成と権限

すべての非公開管理領域は、ローカル Git 管理の枠組みを利用し、公開リポジトリ（Git 追跡領域）への誤コミットを構造的に防ぐ。

### 2.1 Git common directory と非公開状態領域

- **Git common directory の解決**: `git rev-parse --git-common-dir` を使用して絶対パスを取得する（通常の単一 checkout では `.git`、worktree では親リポジトリの `.git` を指す）。
- **固定共有 lock ファイル**: `<git-common-dir>/herdr-review.lock`
  - 権限: `0600`（所有者のみ読み書き）
  - 本ファイルは補助状態の切り離し・再初期化の対象外とし、常に同一パス・同一境界を維持する。
- **補助状態ディレクトリ**: `<git-common-dir>/herdr-review/`
  - 権限: `0700`（所有者のみアクセス可能）
- **状態ファイル**: `<git-common-dir>/herdr-review/state.json`
  - 権限: `0600`（所有者のみ読み書き）

### 2.2 worktree 内の作業・成果物領域（Git 除外）

`.gitignore` に次のエントリを追加し、追跡対象から完全に除外する。

- `.inbox/`: 一時依頼・応答ファイル配置領域（ディレクトリ権限 `0700`、ファイル権限 `0600`）
- `docs/reviews/internal/`: 非公開レビュー履歴の正本領域（ディレクトリ権限 `0700`、ファイル権限 `0600`）

<a id="sec-3"></a>

## 3. 排他制御（共有 lock と inode 照合）

同一 Git common directory を共有するすべての worktree・helper プロセス間で並行実行・採番衝突・二重初期化を防止するため、OS レベルの排他制御を行う。

### 3.1 ロック取得手順

1. `lock_path = os.path.join(git_common_dir, "herdr-review.lock")` を特定する。
2. `fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)` でオープンする。
3. `fcntl.flock(fd, fcntl.LOCK_EX)` で排他ロックを取得する（ブロッキング待機、または必要に応じてタイムアウト付きノンブロッキング試行）。
4. **inode 照合（TOCTOU 回避）**:
   - `fstat_res = os.fstat(fd)`
   - `stat_res = os.stat(lock_path)`
   - `fstat_res.st_ino != stat_res.st_ino` または `fstat_res.st_dev != stat_res.st_dev` の場合、ロック取得中に別プロセスによってファイルが置換・再作成されたと判断し、`fd` をクローズしてステップ 2 からリトライする（最大リトライ回数: 5 回）。
5. 照合に成功した場合、ロック取得完了とする。

### 3.2 ロック解放手順

1. ロック保護下の処理完了後、`fcntl.flock(fd, fcntl.LOCK_UN)` を実行する。
2. `os.close(fd)` でクローズする。
3. **lock ファイルは絶対に削除（`unlink`）しない**。削除による別 inode への分岐を防止する。
4. プロセスがクラッシュ・強制終了した場合でも、OS により `flock` は自動的に全解放され、永久ロックにはならない。

<a id="sec-4"></a>

## 4. 補助状態の仕様と安全な再初期化

### 4.1 `state.json` の形式

```json
{
  "version": 1,
  "instance_id": "inst_a1b2c3d4e5f6",
  "worktree_path": "/home/user/repo",
  "counter": 42,
  "created_at": "2026-10-23T10:00:00Z",
  "updated_at": "2026-10-23T11:00:00Z"
}
```

- `version`: 整数（現行は `1`）
- `instance_id`: `inst_` + 12 文字の 16 進数ランダム文字列（小文字）
- `worktree_path`: 登録された worktree の正規化実パス（`os.path.realpath`）
- `counter`: 最後に発行された Review ID の連番（整数、初期値 `0`）

### 4.2 状態の原子的更新

`state.json` を更新する際は、同一ディレクトリ内に一時ファイル（例: `state.json.tmp.<pid>.<token>`）を作成して書き込み、`os.fsync` でディスクに永続化させた後、`os.replace`（不可逆 rename）によって原子的に置き換える。

### 4.3 破損検出と再初期化フロー

共有 lock の保護下で以下の手順を実施する。

1. **状態ファイルの検証**:
   - 不在の場合: 新規環境または過去の全損と判定。
   - 存在するが JSON 解析エラー、または必須キー（`version`, `instance_id`, `worktree_path`, `counter`）の欠落・型不正の場合: 破損と判定。
   - 所有者が実行ユーザーと異なる、またはパーミッションが `0600` 以外の場合: **セキュリティ疑義として直ちに停止**し、自動初期化は行わずエラー詳細と手動復旧手順を表示する。
2. **残存 Task の確認（`task-list` 連携）**:
   - 初期化または再初期化が必要な場合、`herdr-orchestrator task-list --repository <identity> --type review` を実行する。
   - `complete=false` または `truncated=true` の場合: 残存なしと断定できないため、停止して人間に確認を求める。
   - 一覧に `result.status=pending` や不確定な review Task が存在する場合: 旧 Task が実行中である可能性があるため、自動初期化を停止する。
3. **人間による放棄確認と再初期化の実行**:
   - 旧 Task が存在しない場合: 自動的に新規 instance ID を発行し、初期化を完了する。
   - 旧 Task が存在・不確定な場合: 通常の実行では停止し、人間に状況確認を促す。人間が明示的に再初期化オプション（`--abandon-old-tasks`）を指定した場合に限り、旧 Task を Registry 上でそのまま残した上で、古い補助状態ディレクトリをバックアップ隔離または再生成し、新しい instance ID（`counter=0`）で再初期化する。

<a id="sec-5"></a>

## 5. 採番と個別記録・INDEX の原子的保存

### 5.1 採番アルゴリズム

共有 lock の保護下で次を実施する。

1. 登録 worktree パスと現在の実行 worktree パス（`os.path.realpath`）を照合する。一致しなければ別 worktree からの保存としてエラー停止する。
2. 個別記録ディレクトリ `docs/reviews/internal/<instance-id>/` 内に存在する既存の `RV*.md` ファイルを走査し、ファイル名から最大の番号 `existing_max` を算出する（ファイルが存在しなければ `0`）。
3. `state.json` の `counter` を読み取る。
4. `next_num = max(existing_max, counter) + 1` を次の Review ID 番号とする。
5. Review ID は `RV` + 最小 3 桁（3 桁未満はゼロパディング、1000 以上はそのまま、例: `RV001`, `RV042`, `RV1000`）とする。
6. `state.json` の `counter` を `next_num` に原子的更新する。この時点でクラッシュした場合、番号は欠番となるが再利用はされず、ID 衝突を防ぐ。

### 5.2 個別記録の原子的公開

1. 一時ファイル `docs/reviews/internal/<instance-id>/.tmp.RVNNN.md.<pid>.<token>` にレビュー記録本文を書き出す。
2. `os.fsync` でディスクに確実にフラッシュする。
3. 不可分リンク `os.link` により `docs/reviews/internal/<instance-id>/RVNNN.md` へ原子的に公開後、一時ファイルを unlink する。既存ファイルが存在する場合は上書きせず `ReviewRecordExistsError` となり、既存記録の上書き破壊を防止する。権限は `0600`。
4. 書き込み途中の不完全ファイルが完成データとして扱われることはない。

### 5.3 INDEX の派生更新

個別記録が正本である。個別記録の公開後、同一ディレクトリ内の全 `RV*.md` を読み込み、`docs/reviews/internal/<instance-id>/INDEX.md` を派生情報として原子的更新する。INDEX が破損・消失しても、残存する個別記録からいつでも再生成できる。

<a id="sec-6"></a>

## 6. helper CLI インターフェース設計

repository 固有の操作をカプセル化する CLI として `scripts/repo-review-helper`（Python スクリプト）を実装する。

### サブコマンド仕様

| コマンド         | 役割                                          | 主な引数・動作                                                                                                                       |
| ---------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `init`           | 補助状態の初期化・検証                        | `--worktree <path>`, `--abandon-old-tasks`（不確定時の強制初期化）, `--force`（既存状態の上書き初期化）                              |
| `status`         | 現在の補助状態・worktree 登録・最新採番を表示 | 出力は JSON または人向けサマリ                                                                                                       |
| `request-create` | 配送依頼ファイルを一時領域に原子的作成        | `--tier <LIGHT\|STANDARD\|DEEP>`, `--target-commit <SHA>`, `--summary <text>`, `--output-token`（作成パス出力）                      |
| `record-save`    | 採番、個別記録の原子的保存、INDEX更新         | `--task-id <ID>`, `--body-file <path>` (または `--body <text>`), `--outcome <GO\|要修正\|NO-GO>` (必須), `--task-status <status>` 等 |
| `reindex`        | 個別記録から INDEX.md を再生成                | `--instance-id <ID>`（省略時は現 active instance）                                                                                   |

<a id="sec-7"></a>

## 7. テスト計画と外部レビュー確認事項

`tests/test_repo_review_helper.py` を作成し、`npm run test:orchestrator`（`unittest`）で網羅的にテストする。

### テストケース一覧

1. **共有 lock と inode 照合**:
   - 通常のロック取得・解放、多重取得の排他待ち確認
   - プロセス終了時の `flock` 自動解放確認（永久ロック防止）
   - lock 取得中のファイル置換・再作成に対する inode 照合とリトライ検出
   - lock ファイルが再初期化時も削除されず同一 inode を維持することの確認
2. **補助状態管理と原子的更新**:
   - `state.json` の原子的書き込みと整合性検証
   - パーミッション異常（0777等）におけるセキュリティ安全停止
   - 登録外 worktree からのアクセス拒否
3. **残存 Task 確認と再初期化**:
   - `task-list` に未完了 Task がある場合の自動初期化停止
   - `truncated=true` / `complete=false` 時の安全停止
   - 人間の明示的オプション（`--abandon-old-tasks`）による安全な新 instance 発行
   - 旧 Task の結果が新 instance ID の配下に取り込まれないことの確認
4. **原子的採番と個別記録公開**:
   - 並列プロセスからの同時採番要求で Review ID が衝突しないこと
   - 一時ファイル書き出し途中のクラッシュ注入と不完全ファイル除外
   - 欠番の発生許容と次番号発行の継続性
   - 個別記録からの `INDEX.md` 再生成
