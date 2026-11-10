# 内部レビュー記録・作業領域の保持期間とアーカイブ設計書（Phase 2）

- **ステータス**: 設計中
- **対象 Issue**: [Issue #97](https://github.com/okinawa-systembit/herdr-orchestration/issues/97)
- **対象**: このリポジトリの内部レビュー記録（`docs/reviews/internal/`）および作業領域（`.input/`）

本書は [Issue #97](https://github.com/okinawa-systembit/herdr-orchestration/issues/97) に基づき、[Phase 1 設計書 §10](REVIEW_SKILL_PHASE1_DESIGN.md#sec-10) で次フェーズへ委譲されていた内部レビュー記録（`docs/reviews/internal/`）および作業領域（`.input/`）の保持期間ポリシー、過去記録のアーカイブ手順、ならびにエージェントによる誤削除を防止するセーフティガードレールと補助 CLI（`repo-review-helper cleanup-candidates`）の仕様を定義する。

## 目次

- [1. 目的・背景と適用範囲](#sec-1)
- [2. 用語・正本と30日保持ポリシー](#sec-2)
- [3. 内部レビュー記録のアーカイブと `archive/INDEX.md` 仕様](#sec-3)
- [4. 作業領域（`.input/`）のクリーンアップ仕様（許可リスト方式）](#sec-4)
- [5. 非厳密性・欠落の許容（ベストエフォート原則）](#sec-5)
- [6. セーフティガードレール（エージェント誤削除防止と例外規定）](#sec-6)
- [7. CLI 仕様（`repo-review-helper cleanup-candidates`）](#sec-7)
- [8. 関連スキル・文書の改訂仕様と受け入れ検証基準](#sec-8)

---

<a id="sec-1"></a>

## 1. 目的・背景と適用範囲

### 1.1 背景と課題

[Phase 1 設計書](REVIEW_SKILL_PHASE1_DESIGN.md)および[Phase 1 詳細実装設計書](REVIEW_SKILL_PHASE1_IMPLEMENTATION.md)により、`scripts/repo-review-helper` を用いた内部レビュー記録（`docs/reviews/internal/<instance-id>/RV*.md`）の原子的保存・採番および `.input/inbox/` を介した配送基盤が確立された。一方で、Phase 1 では「通常運用での保持期間・明示的な廃止手順は別フェーズで決める」（[Phase 1 設計書 §10](REVIEW_SKILL_PHASE1_DESIGN.md#sec-10)）としていたため、長期運用に伴い以下の課題が生じていた。

1. **記録・一時ファイルの蓄積**: `docs/reviews/internal/<instance-id>/` 直下の `RV*.md`（130件超）や `.input/inbox/`、`.input/review/sent/` のファイルが整理基準のないまま蓄積し、一覧性（[基本方針: 原則 2 人間中心の可観測性](../core-principles.md#principle-2)）が低下する。
2. **保持期間内の早期削除による `artifact_missing` 事故**: レビュー記録（`RV*.md`）、相談回答（`discussion-*-response.md`）、実装依頼書下書き（`implementation-request-*.md`）、PR 最終コメントの送信済み控え（`.input/review/sent/*`）は Orchestrator の `result-submit --result-ref` に指定される成果物（[Result Contract §10](../herdr-orchestrator/06-results-and-workflows.md#sec-10)）である。中央 Task Registry の保持期間（30日）内にこれらを削除すると、`herdr-orchestrator task-result` が `artifact_missing` エラーとなる。
3. **自然言語指示による誤削除リスク**: 人間から「掃除して」「不要ファイルを消して」と指示された際、エージェントが保護期間や制御ファイルの役割を考慮せず `rm -rf .input/*` 等を実行すると、進行中タスクの破壊や保持期間内の参照切れを引き起こす。

### 1.2 適用範囲と責務境界（A 領域 vs B 領域）

本設計書（Phase 2）は、**リポジトリ固有の非公開作業・履歴領域（A 領域）**を対象とする。中央 Task Registry（B 領域）のクリーンアップ実装とは以下の通り責務を分離する。

| 領域区分                        | 対象パス                                                                 | 管轄 CLI / 設計正本                                                                                                                                                                        |
| :------------------------------ | :----------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A 領域（本設計書の対象）**    | `docs/reviews/internal/`、`.input/`                                      | `scripts/repo-review-helper` / 本設計書（[Issue #97](https://github.com/okinawa-systembit/herdr-orchestration/issues/97)）                                                                 |
| **B 領域（中央 Orchestrator）** | `${XDG_STATE_HOME:-~/.local/state}/herdr-orchestrator/tasks/task_*.json` | `herdr-orchestrator` / [Task Registry 設計書 §6.8](../herdr-orchestrator/03-task-registry.md#sec-6-8)（[Issue #157](https://github.com/okinawa-systembit/herdr-orchestration/issues/157)） |

---

<a id="sec-2"></a>

## 2. 用語・正本と30日保持ポリシー

### 2.1 基準日数の一本化（単一正本との整合）

A 領域における保持期間は、中央 Task Registry の保持期間（[Task Registry 設計書 §6.8](../herdr-orchestrator/03-task-registry.md#sec-6-8)）に揃え、すべての対象で **「30日経過（`now - timestamp > 30 days`、すなわち丸30日＝31日目以降）」** に統一する（[基本方針: 原則 6](../core-principles.md#principle-6)）。

- **30日以内（`now - timestamp <= 30 days`）**: **保持期間内（Protected）**。`herdr-orchestrator task-result` の参照整合性（`artifact_missing` 防止）および直近の監査証跡を保護するため、アーカイブ・削除の対象外とする。
- **30日超（`now - timestamp > 30 days`）**: **整理対象候補（Eligible）**。後述する Active Task 保護や制御ファイル保護に該当しない限り、`RV*.md` はアーカイブ移動（`mv`）、`.input/` 内の対象ファイルは削除（`rm`）の対象候補とする。

### 2.2 経過日数のタイムスタンプ判定ルール

対象ファイルの種別ごとに、以下の規則で基準日時（`timestamp`）を決定する。

1. **内部レビュー記録（`docs/reviews/internal/<instance-id>/RV*.md`）**:
   - **既存関数の再利用**: 新規の独自パーサーは作成せず、`scripts/repo_review_helper/recorder.py` の既存関数 `parse_review_metadata` を再利用して `created_at` を取得する（[原則 6](../core-principles.md#principle-6)・[指針 8](../core-principles.md#guideline-8)）。
   - **フォールバック規則**:
     - `created_at` がタイムゾーン付きの有効な ISO 8601 日時文字列である場合は、その日時を基準とする（PC 移行や `git clone` / ファイルコピー時に `mtime` が変化しても作成日時で正確に判定するため）。
     - `created_at` が未設定（`"-"`）、パース不能、または**タイムゾーン情報を持たない（naive datetime）場合は、暗黙に UTC とみなさず、ファイルの更新日時（`os.lstat` の `st_mtime`）へフォールバック**する。
   - **未来日時・読み取り不能時の保護**:
     - 有効な `created_at` と `st_mtime` の**どちらか一方でも現在時刻（`now`）より未来（`> now`）である場合**、またはファイルの読み取り・`lstat` に失敗した場合は、安全側に倒して**保護対象（`future_or_unreadable`）**とし、候補から除外する。
2. **作業領域の整理対象ファイル（`.input/inbox/*` および `.input/review/sent/*`）**:
   - ファイルの自由記述本文は一切パースせず（[指針 8](../core-principles.md#guideline-8)・[指針 9](../core-principles.md#guideline-9)）、`os.lstat` の更新日時（`st_mtime`）のみを基準とする。
   - `st_mtime > now`（未来日時）または `lstat` 失敗時は、同じく**保護対象（`future_or_unreadable`）**として候補から除外する。

### 2.3 Active Task 保護の判定方法（機械判定とエージェント判断の分担）

進行中（非 terminal）のタスクに関連するファイルが誤って移動・削除されることを防ぐため、[基本方針: 指針 8（機械検査とエージェント判断の境界）](../core-principles.md#guideline-8)に基づき、対象ごとに以下の通り判定方法を分担する。

| 対象パス                                 | タスクとの紐づけ構造                                                                  | 判定主体と保護ルール                                                                                                                                                                                                                                                                                                                                                                                           |
| :--------------------------------------- | :------------------------------------------------------------------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `docs/reviews/internal/<id>/RV*.md`      | フロントマターに機械制御値 `task_id` が記録されている                                 | **機械判定（`cleanup-candidates`）**: 非 terminal（`task.status ∉ {succeeded, failed, cancelled}`、[Task Registry 設計書 §6.3](../herdr-orchestrator/03-task-registry.md#sec-6-3)・[§6.8](../herdr-orchestrator/03-task-registry.md#sec-6-8)）のタスクのみを `task-list`（[task-list 設計書](../herdr-orchestrator-task-list.md)）で取得し、該当 `task_id` が含まれれば機械的に `active_task` として保護する。 |
| `.input/inbox/*`、`.input/review/sent/*` | `task.instruction`（自由記述）または `result.ref`（`task-list` 非出力）から参照される | **機械（30日閾値）＋エージェント判断**: 自由記述の構文解析は行わない。CLI は30日超（`> 30 days`）の閾値のみを機械判定し、現在進行中のタスクで使用中でないかは削除実行前にエージェントが確認する。                                                                                                                                                                                                              |

- **`RV*.md` における Active Task 機械判定の詳細**:
  - **非 terminal 状態での絞り込み（1000件上限による `truncated` 回避）**: 完了済みタスクの蓄積により `task-list` の上限（1000件）に達して全 RV が永久保護される事態を防ぐため、`task-list` 呼び出し時は `--status created --status in_progress --status unknown`（必要に応じて `--repository <identity> --type review --limit 1000`）を指定して **active（非 terminal）なタスクのみ**を取得する。
  - **ID 照合と不在時の扱い**: 取得した active タスクの ID 集合に `RV*.md` の `task_id` が含まれる場合は **`active_task`（保護）** とする。一方、active タスク集合に含まれない場合（既に terminal となったタスク、Registry からクリーンアップ済みの古い `task_id`、またはフロントマターを持たない旧形式レコードの `task_id == "-"`）は active ではない（**`eligible`**）と判定する。
  - **`task-list` 取得失敗・切り詰め時のフェイルセーフ**: `task-list` の呼び出し自体に失敗した場合、または active タスクのみで絞り込んでもなお `complete == false` や `truncated == true` となった場合は、安全側に倒して30日超の対象 `RV*.md` を **`active_task`（保護）** として扱い、`stderr` に警告を出力する（[指針 10](../core-principles.md#guideline-10)）。
- **補足（`.input/` における30日超タスクの非対称性について）**: 作成から30日を超えてなお実行中（非 terminal）のまま残るタスクは通常運用では極めて稀であるため、`.input/` 側を「30日閾値の機械判定＋削除前のエージェント確認」としても実運用上のリスクはない。

---

<a id="sec-3"></a>

## 3. 内部レビュー記録のアーカイブと `archive/INDEX.md` 仕様

### 3.1 `archive/` への移動手順

30日経過（`> 30 days`）し、`cleanup-candidates` で `archive_candidates` に抽出された個別記録 `docs/reviews/internal/<instance-id>/RVNNN.md` は、同一インスタンスディレクトリ配下の `archive/` サブディレクトリへ移動（`mv`）する。

1. `mkdir -p docs/reviews/internal/<instance-id>/archive` を実行する。
2. `cleanup-candidates` がリストアップした個別ファイルパスを指定して `mv docs/reviews/internal/<instance-id>/RVNNN.md docs/reviews/internal/<instance-id>/archive/RVNNN.md` を実行する。
3. 後述の `archive/INDEX.md` を作成・追記する。
4. `scripts/repo-review-helper reindex` を実行し、直下の `docs/reviews/internal/<instance-id>/INDEX.md` を直下に残る現役レコード（アーカイブされなかったレコード）のみの一覧に更新する。

### 3.2 `archive/INDEX.md` の位置付けと形式

- **目的**: `docs/reviews/internal/<instance-id>/archive/INDEX.md` は、[review-skill-audit](../../.agents/skills/review-skill-audit/SKILL.md)（モード A: 定期見直し・全体監査）や設計・実装時の振り返りにおいて、過去にどのようなレビュー指摘・議論があったかを一覧するための索引である。
- **自由記述形式（機械パース非対象）**:
  - [基本方針: 指針 8](../core-principles.md#guideline-8) および [指針 9](../core-principles.md#guideline-9) に従い、`archive/INDEX.md` はエージェントまたは人間が作成・追記する**自由記述の Markdown（表または箇条書きリスト）**とする。
  - 推奨記載項目は「Review ID（または期間範囲）、対象 Issue / PR / テーマ、主な指摘傾向・知見の要約、ファイル名（`RVNNN.md`）」とするが、`repo-review-helper` による厳密な構文パース、ヘッダー強制、自動再生成（`reindex` 対象化）は行わない。

### 3.3 既存の採番・`reindex` 実装との共存保証

既存の `scripts/repo_review_helper/recorder.py` の実装構造により、`archive/` サブディレクトリの導入は既存の採番・索引機能と無改修で完全に共存する。

1. **`reindex`（`update_index`）との共存**: `update_index` および `scan_existing_review_numbers` は `<instance-id>/` 直下の通常ファイル（`RV_PATTERN = ^RV(\d+)\.md$`）のみを走査し、サブディレクトリ `archive/` や `archive/INDEX.md` には一切触れない。
2. **採番カウンタ（high-water mark）の維持**: `allocate_next_review_id` は `next_num = max(existing_max, counter) + 1` で採番し、`<git-common-dir>/herdr-review/state.json` の `counter` を原子的更新している（[Phase 1 詳細実装設計書 §5.1](REVIEW_SKILL_PHASE1_IMPLEMENTATION.md#sec-5)）。したがって、直下の `RV*.md` がすべて `archive/` へ移動されて `existing_max == 0` となった場合でも、次回採番は `state.json` の `counter + 1` から継続され、`RV001` へ巻き戻ることはない。

---

<a id="sec-4"></a>

## 4. 作業領域（`.input/`）のクリーンアップ仕様（許可リスト方式）

`.input/` 配下の整理は**許可リスト（Allowlist）方式**を採用する。**以下の表で「30日超で削除対象」または「即時削除許容」として明示的に列挙されたパスのみが削除対象となり、列挙されていないすべてのディレクトリ・ファイルは対象外（保護）とする。**

| 区分                                  | 対象パス（直下の通常ファイルのみ）                                                                                 | 保持期間・整理方法                                                                                                                         | 役割・理由                                                                                                                                    |
| :------------------------------------ | :----------------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. 30日超で削除対象（Eligible）**   | `.input/inbox/*`、`.input/review/sent/*`                                                                           | **30日経過後（`> 30 days`）に `rm`**（`cleanup-candidates` の `delete_candidates` に基づき個別削除）                                       | レビュー依頼書（`review-request.*.md`）、相談・回答（`discussion-*.md`）、実装依頼書（`implementation-request-*.md`）、送信済みPRコメント履歴 |
| **2. 即時削除許容（一時バッファ）**   | `.input/tmp/*`                                                                                                     | **コマンド完了直後に即時 `rm` 可**（30日保護対象外、`cleanup-candidates` 走査対象外）                                                      | `review-sync.sh pr-create --body-file` や `record-save --body-file` に本文を渡すための単発バッファ（`result.ref` 非対象）                     |
| **3. 永久削除禁止（制御ファイル）**   | `.input/review/state.json`、`.input/review/reviewer-draft.md`、`.input/review/incoming.md`                         | **期間に関わらず削除禁止（絶対保護）**                                                                                                     | `scripts/review-sync.sh` が PR / Issue 同期とドラフト管理に使用する制御・作業ファイル                                                         |
| **4. 走査対象外（その他・外部状態）** | 上記以外の `.input/` 配下パス、サブディレクトリ、`.` 始まり隠しファイル（参考: `<git-common-dir>/herdr-review/*`） | **走査対象外・削除禁止**（`herdr-review.lock` は [Phase 1 詳細設計 §3.2](REVIEW_SKILL_PHASE1_IMPLEMENTATION.md#sec-3) により永久削除禁止） | 許可リスト外の領域、`.gitkeep` 等、および Git common directory 内の排他ロック・採番状態                                                       |

- **非再帰走査の原則**: `.input/inbox/*` および `.input/review/sent/*` の走査は**各ディレクトリ直下の通常ファイルのみ**を対象とし、サブディレクトリが存在する場合は再帰走査せず対象外（保護）とする。また、`.` で始まる隠しファイル（`.gitkeep` や書き込み中の一時ファイル `.review-request.tmp.*` 等）も候補から除外する。

---

<a id="sec-5"></a>

## 5. 非厳密性・欠落の許容（ベストエフォート原則）

[Phase 1 設計書 §5.1（データ喪失の許容）](REVIEW_SKILL_PHASE1_DESIGN.md#sec-5)の基本原則を継承し、Phase 2 のアーカイブおよびクリーンアップ運用においても**非厳密性（ベストエフォート）**を徹底する。

1. **リンク切れ・ファイル欠落・欠番の完全許容**:
   - `docs/reviews/internal/` および `.input/` はすべて `.gitignore` により Git 管理外に置かれたローカルデータである。
   - `INDEX.md` や `archive/INDEX.md` に記載されたファイルが実ディスク上に存在しない（リンク切れ）、`RV` 番号に飛び（欠番）がある、あるいは `archive/` ディレクトリが空・不在であっても、**一切エラー停止させず正常な状態として許容する**。
2. **30日経過後の `artifact_missing` の想定内扱い**:
   - 中央 Task Registry のクリーンアップ（[Issue #157](https://github.com/okinawa-systembit/herdr-orchestration/issues/157)）は lazy cleanup または手動コマンドで行われるため、30日経過後に A 領域の `RV*.md` が `archive/` へ移動されたり `.input/inbox/*` の回答ファイルが削除されたりした後、まだ Registry 側に残っている30日超の古い Task に対して `herdr-orchestrator task-result <old_task_id>` を実行すると `artifact_missing` が返る場合がある。
   - **作成・更新から30日を過ぎた Task における `artifact_missing` は保持期間満了に伴う想定内の正常動作**であり、エージェントはこれをシステム異常や障害として報告しない。

---

<a id="sec-6"></a>

## 6. セーフティガードレール（エージェント誤削除防止と例外規定）

人間からの曖昧な指示やエージェントの過剰な忖度による誤削除事故を防ぐため、すべてのロールのエージェントに以下の行動規範（セーフティガードレール）を課す。

### 6.1 `cleanup-candidates` 経由の厳守とワイルドカード削除の禁止

- エージェントが `docs/reviews/internal/` または `.input/`（`.input/tmp/*` を除く）の整理を行う際は、**必ず事前に `scripts/repo-review-helper cleanup-candidates` を実行し、同コマンドが候補（`archive_candidates` / `delete_candidates`）として出力した個別ファイルパスに限って `mv` / `rm` を実行しなければならない**。
- 保護対象領域に対して `rm -rf .input/*`、`rm -f .input/inbox/*`、`find ... -delete`、`git clean -fdx` などのワイルドカード・ディレクトリ一括削除を実行することは厳禁とする。

### 6.2 30日以内ファイルの削除要求に対する拒否と人間への委任

- 人間から自然言語で「掃除して」「不要ファイルを消して」と指示された場合は、30日を超えた候補（`cleanup-candidates` の出力対象）のみを整理し、30日以内のファイル（コマンド引数用の一時バッファ `.input/tmp/*` を除く）には一切手を触れない。
- 万が一、人間から**「30日以内のものも含めて全部消して」「強制的にリセットして」と明示的に指示された場合であっても、エージェントは自ら削除を実行してはならない**。
- この場合、エージェントは以下の点を人間に説明し、**人間自身の端末操作（手動での `rm` / `mv` 実行）に委任する**：
  1. 30日以内のファイルを削除すると、進行中タスクの破壊や `herdr-orchestrator task-result` における `artifact_missing` エラーが発生するリスクがあること。
  2. セーフティガードレールによりエージェントによる30日以内ファイルの削除は禁止されているため、どうしても削除が必要な場合は人間が直接コマンドを実行する必要があること（対象パスの案内のみ行う）。

### 6.3 唯一の即時削除例外（秘密情報の混入時）

- **秘密情報漏洩防止の優先（[基本方針: 原則 5](../core-principles.md#principle-5)）**:
  - 認証トークン（`ghs_...` 等）、秘密鍵、クレデンシャル等の秘密情報が誤って `.input/` や `docs/reviews/internal/` 内のファイルに書き込まれたことが判明した場合は、**30日保持期間に関わらず、エージェントは直ちに該当ファイルを個別削除（`rm`）し、人間に報告しなければならない**。
  - **報告時の必須事項**: 秘密混入により削除したファイルが Task の `result.ref`（`RV*.md` や相談回答ファイル等）であった場合は、削除によって該当 Task の `herdr-orchestrator task-result` が `artifact_missing` になる旨を併せて人間に報告する（参照整合性よりも秘密漏洩の即時遮断を優先し、整合性の崩れを受け入れる）。

---

<a id="sec-7"></a>

## 7. CLI 仕様（`repo-review-helper cleanup-candidates`）

### 7.1 コマンド概要と書式

`scripts/repo-review-helper` に読み取り専用のサブコマンド `cleanup-candidates` を追加する。本コマンドはファイルの作成・変更・移動・削除を一切行わない（副作用ゼロ）。

```bash
scripts/repo-review-helper cleanup-candidates [--worktree <path>] [--json]
```

- **オプション**:
  - `--worktree <path>`: 対象 worktree のパス（省略時はカレントディレクトリ）。
  - `--json`: 機械・エージェント処理向けの JSON 形式で標準出力へ出力する（省略時は人間向けのテキストサマリを出力）。
- **期間短縮・強制オプションの不採用（フェイルセーフ）**:
  - `--days`、`--force`、`--all` 等の保持期間を変更・バイパスするオプションは**仕様として一切設けない**（[原則 1 KISS](../core-principles.md#principle-1)）。未知のフラグが渡された場合は `argparse` の標準動作により終了コード `2` で拒絶する。
- **保持期間定数の共有**:
  - `repo-review-helper` 側の保持期間定数（`RETENTION_DAYS = 30`）は、Orchestrator Registry 側の `_RETENTION_DAYS`（`skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`）と同一の値（`30`）とし、単体テスト `P2-01` で両者の値が一致することを検証する。

### 7.2 走査・判定の動作規則

1. **対象インスタンス（現行 Active Instance のみ）**:
   - `<git-common-dir>/herdr-review/state.json` を読み取り、現在有効な `instance_id` のディレクトリ `docs/reviews/internal/<instance-id>/` 直下のみを走査する。
   - 過去の再初期化等で残存している旧 `inst_*` ディレクトリは自動走査の対象外とし、人間の判断に委ねる。
   - 補助状態が未初期化（`state == None`）または登録 worktree 不一致の場合は、`instance_id: null`、`archive_candidates: []` として扱い（エラー終了はしない）、`.input/inbox/` および `.input/review/sent/` の走査のみを実施する。
2. **除外エントリ**:
   - `docs/reviews/internal/<instance-id>/` 直下において、`INDEX.md`、`archive/` ディレクトリ、`.` で始まる隠しファイル、および `^RV(\d+)\.md$` に一致しないファイルは候補から除外する。
   - `.input/inbox/` および `.input/review/sent/` 直下において、サブディレクトリおよび `.` で始まる隠しファイル（`.gitkeep`、`.review-request.tmp.*` 等）は候補から除外する。
   - `.input/review/` 直下の制御ファイル（`state.json`、`reviewer-draft.md`、`incoming.md`）が存在する場合は、保護内訳の `control_file` としてカウントし、削除候補には絶対に含めない。
3. **Symlink 非追従（`os.lstat` 検査）**:
   - 走査対象のディレクトリ自体（`docs/reviews/internal/<instance-id>`、`.input/inbox`、`.input/review/sent`）および各エントリに対して `os.lstat`（`Path.is_symlink()`）で検査を行い、**symlink は絶対にたどらない**（※ 既存の `scan_existing_review_numbers` 等で使われている `entry.is_file()` は symlink をたどるため、必ず `entry.is_symlink()` を先に判定する）。
   - symlink を検出した場合は候補から除外して `protected_count.by_reason.symlink` に加算し、`stderr` に `[WARN] Skipping symlink: <path>` を出力する。
4. **常時 `rc=0` と警告出力（[指針 10](../core-principles.md#guideline-10)）**:
   - 対象ディレクトリが存在しない場合、ファイル読み取りエラーが発生した場合、または `task-list` 取得に失敗した場合でも、コマンド自体は例外終了させず終了コード `0`（`rc=0`）を返し、異常・スキップの詳細は `stderr` に `[WARN]` として出力する（終了コードが非0となるのは `argparse` の引数不正 `rc=2` のみ）。

### 7.3 JSON 出力スキーマ（`--json`）

```json
{
  "retention_days": 30,
  "instance_id": "inst_a1b2c3d4e5f6",
  "archive_candidates": [
    {
      "path": "docs/reviews/internal/inst_a1b2c3d4e5f6/RV001.md",
      "archive_to": "docs/reviews/internal/inst_a1b2c3d4e5f6/archive/RV001.md",
      "review_id": "RV001",
      "task_id": "task_00000000-0000-4000-8000-000000000001",
      "timestamp_source": "created_at",
      "age_days": 34.2
    }
  ],
  "delete_candidates": [
    {
      "path": ".input/inbox/review-request.a1b2c3d4.md",
      "category": "inbox",
      "age_days": 31.5
    },
    {
      "path": ".input/review/sent/20261001-120000-000000-reviewer-draft.md",
      "category": "review_sent",
      "age_days": 40.1
    }
  ],
  "protected_count": {
    "total": 12,
    "by_reason": {
      "within_retention": 8,
      "active_task": 1,
      "future_or_unreadable": 0,
      "control_file": 3,
      "symlink": 0
    }
  }
}
```

- **各フィールドの定義**:
  - `retention_days`: 固定整数 `30`。
  - `instance_id`: 現在の active instance ID（未初期化・取得不能時は `null`）。
  - `archive_candidates`: 30日超かつ非アクティブな `RV*.md` の配列（Review ID 昇順）。`path` と `archive_to` は worktree ルートからの相対パス。`timestamp_source` は `"created_at"` または `"mtime"`。`age_days` は小数第1位（`round(age_seconds / 86400, 1)`）。
  - `delete_candidates`: `.input/inbox/`（`category: "inbox"`）および `.input/review/sent/`（`category: "review_sent"`）内の30日超の通常ファイル配列（パス昇順）。`path` は worktree ルートからの相対パス。
  - `protected_count`: 保護されたファイル件数の集計と理由別内訳（`by_reason`）：
    - `within_retention`: 30日以内（`<= 30 days`）のため保護された `RV*.md`、`.input/inbox/*`、`.input/review/sent/*` の通常ファイル数。
    - `active_task`: 30日超だが `task-list` で非 terminal（または `task-list` 失敗・切り詰めにより安全側保護）と判定された `RV*.md` の件数。
    - `future_or_unreadable`: 未来日時（`created_at > now` または `st_mtime > now`）または読み取り失敗により保護されたファイル数。
    - `control_file`: `.input/review/{state.json,reviewer-draft.md,incoming.md}` のうち実在する制御ファイル数。
    - `symlink`: 走査中に検出・スキップされた symlink の件数。

---

<a id="sec-8"></a>

## 8. 関連スキル・文書の改訂仕様と受け入れ検証基準

### 8.1 設計 PR と実装 PR の段階的反映計画（D-1〜D-3 整合）

存在しない CLI サブコマンドを SKILL 本文に先行記載することによる D-2 不整合（`--help` と SKILL の不一致）を防ぎつつ、即時削除による `artifact_missing` 事故を直ちに止めるため、以下の2段階で反映する。

1. **本設計 PR（`Refs #97`）で反映する項目**:
   - 本設計書 `docs/reviews/REVIEW_SKILL_PHASE2_DESIGN.md` の作成。
   - [AGENTS.md](../../AGENTS.md)（「Herdr Orchestrator レビュー（本リポジトリ）」節）および [Phase 1 設計書 §10](REVIEW_SKILL_PHASE1_DESIGN.md#sec-10) への参照リンク追加。
   - **[repo-workflow Skill](../../.agents/skills/repo-workflow/SKILL.md)（§3.3, §3.4, §6.4）の前倒し改訂**: 「用済みとなった実装依頼書や相談・回答ファイルを即削除する」という旧指示を撤回し、「削除せず保持する（30日保持・クリーンアップ基準は Phase 2 設計書を参照）」へ改訂する。
   - **[repo-review チェックリスト](../../.agents/skills/repo-review/references/checklists.md)（ワイドレビュー A-7）の前倒し改訂**: `.input/` の残存ファイル点検基準を「30日を超えて残っているファイル」へ明確化する。
2. **後続の実装 PR（`Closes #97` または実装サブ Issue）で反映する項目**:
   - `scripts/repo_review_helper/` への `cleanup-candidates` サブコマンド実装と単体テスト追加。
   - 本設計書ヘッダーの `- **ステータス**: 実装完了（PR #NN にてマージ済み）` への更新（※ `scripts/check_docs_status.py` は `docs/herdr-orchestrator-*.md` のみを機械検査するため、実装 PR レビュー時に D-1 として手動確認する）。
   - [repo-workflow Skill](../../.agents/skills/repo-workflow/SKILL.md)、[review-skill-audit Skill](../../.agents/skills/review-skill-audit/SKILL.md)（モード A での `archive/INDEX.md` 参照）、および [AGENTS.md](../../AGENTS.md) への `cleanup-candidates` 実行手順・セーフティガードレール要約の追記。

### 8.2 受け入れ検証基準・自動テスト計画（`P2-01`〜`P2-08`）

実装時は `tests/test_repo_review_helper.py` に以下のテストケースを追加し、`npm run test:orchestrator` で検証する（[基本方針: 指針 10](../core-principles.md#guideline-10) に従い、実際の出力 JSON・終了コード・`stderr`・ファイル状態をアサートする）。

| テスト ID | 検証項目                                     | 期待される結果                                                                                                                                                                                                                                                                                                                                                                                                              |
| :-------- | :------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **P2-01** | **保持期間定数の一致検証**                   | `repo-review-helper` の保持期間定数（`RETENTION_DAYS = 30`）が `orchestrator.registry.operations._RETENTION_DAYS` と一致し、出力 JSON の `retention_days` が `30` であること。                                                                                                                                                                                                                                              |
| **P2-02** | **`RV*.md` のタイムスタンプ判定と境界値**    | `parse_review_metadata` の `created_at` が30日超（例: 31日前）なら `archive_candidates` に入り、30日以内（例: 30日ちょうど・29日前）なら `within_retention` になること。タイムゾーンなし・不正値時は `st_mtime` にフォールバックし、`created_at` または `st_mtime` が未来時刻なら `future_or_unreadable` として保護されること。                                                                                             |
| **P2-03** | **`RV*.md` の Active Task 機械保護**         | 30日超の `RV*.md` について、`task-list`（非 terminal 状態 `created` / `in_progress` / `unknown` で絞り込み）に含まれる `task_id` は `active_task` として保護され、含まれない `task_id`（terminal 済み・Registry 不在・`"-"`）は `archive_candidates`（`eligible`）になること。また、`task-list` がエラー・`complete=false`・`truncated=true` の場合は安全側に倒して `active_task` として保護し、`stderr` に警告を出すこと。 |
| **P2-04** | **`.input/` の許可リストと制御ファイル保護** | `.input/inbox/*` および `.input/review/sent/*` の直下通常ファイルで `st_mtime > 30 days` のものだけが `delete_candidates` に入り、`<= 30 days` は `within_retention` となること。`.input/review/{state.json,reviewer-draft.md,incoming.md}` は期間に関わらず `control_file` として保護され、サブディレクトリや `.` 始まりファイル、`.input/tmp/*` は候補に含まれないこと。                                                  |
| **P2-05** | **Symlink 非追従（`lstat` 検査）**           | `<instance-id>/`、`.input/inbox/`、`.input/review/sent/` 内の symlink（30日超のファイルへのリンクを含む）をたどらず候補から除外し、`protected_count.by_reason.symlink` に計上して `stderr` に `[WARN]` を出力すること。                                                                                                                                                                                                     |
| **P2-06** | **未初期化・ディレクトリ不在時の耐障害性**   | レビュー補助状態が未初期化、または `.input/inbox/`・`.input/review/sent/` が存在しない環境でも `rc=0` で正常終了し、`instance_id: null`・空配列を返すこと。                                                                                                                                                                                                                                                                 |
| **P2-07** | **期間短縮・強制オプションの不在確認**       | `--days` や `--force` を渡した際に `argparse` が未定義オプションとして `rc=2` で拒絶すること。                                                                                                                                                                                                                                                                                                                              |
| **P2-08** | **`archive/` 移動後の `reindex`・採番継続**  | `RV001.md`〜`RV003.md` を `archive/` へ移動し `archive/INDEX.md` を作成した状態で `reindex` を実行してもエラーにならず直下 `INDEX.md` が更新され、続く `record-save` で `RV004` が採番されること（`RV001` に巻き戻らないこと）。                                                                                                                                                                                            |
