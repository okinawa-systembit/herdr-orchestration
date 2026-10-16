# herdr-orchestration 分割コードレビュー計画書 (Review Plan)

本書は、`herdr-orchestration` リポジトリ全体を安全かつ網羅的に再レビューするための分割実行計画である。
AIモデルの利用枠（5時間枠・週間枠）の制約下で品質を一切妥協せず、機能上の責務と依存関係に基づいて分割管理する。
初回 20 Scope の定義一覧は [INDEX.md](INDEX.md) を、レビューの進捗・判定・結果の正本は各 [GitHub Scope Issue](https://github.com/okinawa-systembit/herdr-orchestration/labels/scope-review) を参照すること。
運用の詳細設計は [CONTINUOUS_REVIEW_SIMPLIFIED_DESIGN.md](CONTINUOUS_REVIEW_SIMPLIFIED_DESIGN.md) に基づく。

---

## 1. レビュー計画概要

### 1.1 目的

リポジトリ全体の実装コード、テスト、Skill、CI/CD、インストーラ、設計書について、既存の `repo-review` Skill および [エージェントレビューガイドライン](../../docs/herdr-agent-review-guidelines.md) に基づく厳格な再レビューを実施し、潜在的欠陥・不整合・回帰リスクを排除する。

### 1.2 前提条件

- **対象リポジトリ**: `okinawa-systembit/herdr-orchestration`
- **対象ブランチ**: `main`
- **初期基準コミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **共通対象コミットSHA**: 初回全体レビュー開始時に確定した共通対象コミットSHA（全 20 Scope で原則同一のコミットを検証。レビュー途中で main が更新されても自動変更せず固定検証する）
- **実行環境**: WSL Ubuntu 24.04（WSL終了・電源断からの復旧保証は対象外、Windowsネイティブ・macOS対応確認は不要）
- **レビュー基準**:
  - [.agents/skills/repo-review/SKILL.md](../../.agents/skills/repo-review/SKILL.md)
  - [.agents/skills/repo-review/references.md](../../.agents/skills/repo-review/references.md)
  - [docs/herdr-agent-review-guidelines.md](../../docs/herdr-agent-review-guidelines.md)
  - 既存Skillのルール（2段階レビュー、反証確認、重大度とBlockingの分離、既存保証の維持確認等）を省略・緩和しない。

### 1.3 分割と負荷制御の方針

1. **責務・依存関係による機能単位分割**: 単純な行数・ディレクトリ割ではなく、論理レイヤーとデータの流れ（Registry → Handoff → Dispatch → Result → Completion → CLI → 配布）に沿って分割する。
2. **1セッション完了可能な粒度**: 過去の測定値（約8.8KB/260行のファイル1つで Claude Sonnet 4.6 は 5時間枠の約76%を消費）を踏まえ、1タスクあたり主対象コード量を原則150〜350行程度に収める。
3. **高負荷・高複雑度領域の細分化**: 状態遷移の中核である `operations.py` (550行) は、ライフサイクル・Dispatch系 (R006A) と Result・Completion・Claim系 (R006B) の2タスクに分割する。
4. **主対象と参照対象の明確な分離**: 各タスクで主レビュー対象を明確に限定し、参照ファイルはインターフェースと状態遷移の整合性確認に留めることでトークン消費を抑える。
5. **結合レビューの独立設置**: 個別レビューでは見落とされがちなタスク間の連携・契約を検証する「結合レビュー（I001〜I005）」を計画する。

### 1.4 全体完了条件

1. 全 20 の Scope Issue（R001〜R014, I001〜I005）のレビュー作業が完了し、Closed であること（各実施時点のレビュー判定・指摘が記録されていること）。
2. 検出されたすべての Blocking Finding（`blocking:yes` ラベルが付与された P0/P1 および例外的な P2）が解決済み（Closed かつ `status:verified`）であること。
3. Non-blocking 指摘（`blocking:yes` ラベルを持たない `severity:p2`, `severity:p3`）について、保留または採否が明確に合意されていること。
4. 修正に伴う再レビューが行われた場合は、その再検証結果と対象 SHA が記録されていること。
5. すべてのタスクが同一の共通対象コミットSHA（および修正コミット）に対して検証されていること。

---

## 2. 分割レビュー一覧

| ID        | 機能・責務                               | 主対象ファイル                                                                                                      | 関連テスト                                                                                                  |  負荷  | 依存タスク                    |
| --------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | :----: | ----------------------------- |
| **R001**  | 基盤・パス・環境・エラー・命名規則       | `paths.py`, `env.py`, `errors.py`, `worktree.py`, `naming.py`                                                       | `test_orchestrator_phase0.py`, `test_orchestrator_phase1_naming.py`, `test_orchestrator_phase1_worktree.py` | **小** | なし                          |
| **R002**  | リポジトリ識別 & Herdr CLI 基盤          | `identity.py`, `herdr_cli.py`                                                                                       | `test_orchestrator_phase1_identity.py`, `test_orchestrator_phase1_identity_git.py`                          | **中** | R001                          |
| **R003**  | Live Agent 検証 & Worker 追跡            | `live_agent.py`, `worker_trace.py`                                                                                  | `test_orchestrator_phase1_live_agent.py`                                                                    | **中** | R001, R002                    |
| **R004**  | Registry スキーマ・ID・安全パス          | `registry/schema.py`, `registry/task_id.py`, `registry/paths_safe.py`, `registry/errors.py`, `registry/__init__.py` | `test_orchestrator_phase2_registry.py` (一部)                                                               | **中** | R001                          |
| **R005**  | Registry 永続化・排他制御・I/O           | `registry/io.py`, `handoff_lock.py`                                                                                 | `test_orchestrator_phase2_registry.py` (排他・I/O部)                                                        | **中** | R001, R004                    |
| **R006A** | Registry ライフサイクル & Dispatch 遷移  | `registry/operations.py` (L1–L247: 作成、stale検出、prompt開始、dispatch更新)                                       | `test_orchestrator_phase2_registry.py` (前半), `test_orchestrator_phase5_dispatch.py`                       | **中** | R004, R005                    |
| **R006B** | Registry Result & Completion 遷移・Claim | `registry/operations.py` (L248–L551: 結果更新、claim、finish、reset、eligibility)                                   | `test_orchestrator_phase2_registry.py` (後半), `test_orchestrator_phase3_result.py`                         | **中** | R004, R005, R006A             |
| **R007**  | Dispatch コマンド & 診断・復旧           | `dispatch_commands.py`                                                                                              | `test_orchestrator_phase5_dispatch.py`                                                                      | **小** | R003, R005, R006A             |
| **R008**  | Handoff オーケストレーション & Sync      | `handoff_commands.py`                                                                                               | `test_orchestrator_phase4_handoff.py`                                                                       | **中** | R002, R003, R005, R006A       |
| **R009**  | Result 登録・読み取りコマンド            | `result_commands.py`                                                                                                | `test_orchestrator_phase3_result.py`                                                                        | **中** | R003, R004, R006B             |
| **R010**  | Async Completion & リカバリー            | `completion_commands.py`                                                                                            | `test_orchestrator_phase6_completion.py`                                                                    | **中** | R002, R003, R006B, R008, R009 |
| **R011**  | Public CLI & レガシー CLI                | `herdr-orchestrator`, `public_cli.py`, `cli_output.py`, `task-registry.py`                                          | `test_orchestrator_phase0.py`, `test_orchestrator_phase2_registry.py`                                       | **中** | R001〜R010                    |
| **R012**  | インストーラ & 環境セットアップ          | `install.sh`, `skills/.../install/*.sh`, `scripts/*.sh`, 設定類                                                     | `test_distribution_installer.py`, `test_orchestrator_phase8_adoption.py`                                    | **中** | R011                          |
| **R013**  | Skills 定義 & 運用リファレンス           | `skills/.../SKILL.md`, `references/*.md`, `.agents/skills/*`                                                        | `test_orchestrator_phase7_skill.py`                                                                         | **中** | R011, R012                    |
| **R014**  | CI/CD パイプライン & リリース            | `.github/workflows/*.yml`, `.github/workflows/release.yml`, `package.json`                                          | ワークフロー実機・構文検証                                                                                  | **小** | R012, R013                    |

---

## 3. 各個別レビュータスク詳細

---

### R001: 基盤・パス・環境・エラー・命名規則 (Foundation & Identity Primitives)

- **タスクID・名称**: `R001-primitives`
- **目的**: システム全体が前提とする実行環境の検証、基底例外階層、XDGパス解決、Git worktreeルート導出、論理roleおよび命名プレフィックス生成の厳密性を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/paths.py` (21行)
  - `skills/herdr-orchestrator/scripts/orchestrator/env.py` (31行)
  - `skills/herdr-orchestrator/scripts/orchestrator/errors.py` (29行)
  - `skills/herdr-orchestrator/scripts/orchestrator/worktree.py` (23行)
  - `skills/herdr-orchestrator/scripts/orchestrator/naming.py` (54行)
- **参照ファイル**: なし（最下層ライブラリ）
- **参照設計書**:
  - [01-foundation.md](../../docs/herdr-orchestrator/01-foundation.md#sec-1) (§1–3: 目的、設計原則、コンポーネント)
  - [02-roles-and-task-contract.md](../../docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-4) (§4: 論理 role、routing identity)
- **関連テスト**:
  - `tests/test_orchestrator_phase0.py`
  - `tests/test_orchestrator_phase1_naming.py`
  - `tests/test_orchestrator_phase1_worktree.py`
- **重点確認事項**:
  - `HERDR_ENV=1` 未設定時に安全にエラー終了するか（迂回・フォールバックの禁止）。
  - `XDG_STATE_HOME` のフォールバック先（`~/.local/state`）およびディレクトリトラバーサル防止。
  - Git worktree ルート解決において、サブモジュールや detached HEAD、非Gitディレクトリで適切な例外が発生するか。
  - `agent.name` プレフィックス規則（`{repo}-{role}` 等）における英数字・ハイフンの正規化と境界値処理。
- **想定される不具合・失敗経路**:
  - 環境変数不在時のサイレント動作、存在しないパスへのアクセス例外、命名正規化の衝突・エスケープ漏れ。
- **依存タスク**: なし
- **完了条件**: 対象コードの2段階レビューが完了し、対応する GitHub Scope Issue に結果（判定・指摘・未実施検証）が記録されていること。

---

### R002: リポジトリ識別 & Herdr CLI 基盤 (Repository Identity & Herdr CLI Client)

- **タスクID・名称**: `R002-identity-cli`
- **目的**: Git リポジトリのグローバル一意な識別子導出ロジック、および外部 Herdr CLI プロセス実行・エラー分類契約の堅牢性を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/identity.py` (148行)
  - `skills/herdr-orchestrator/scripts/orchestrator/herdr_cli.py` (127行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/paths.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/errors.py`
- **参照設計書**:
  - [02-roles-and-task-contract.md](../../docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-4) (§4–5: 論理 role、routing identity)
  - [06-results-and-workflows.md](../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-0) (§11.0: repository identity 正規化)
  - [09-maintenance-and-roadmap.md](../../docs/herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20) (§20: Herdr CLI バージョン・エラー仕様)
- **関連テスト**:
  - `tests/test_orchestrator_phase1_identity.py`
  - `tests/test_orchestrator_phase1_identity_git.py`
- **重点確認事項**:
  - Git remote URL（SSH, HTTPS, scp 形式等）から `host/owner/repo` への正規化が正確か。大文字小文字・`.git` 接尾辞の除去。
  - 複数 remote 存在時の優先度と一致検証（曖昧な設定の拒否）。
  - `run_herdr` でのサブプロセス呼び出し時、引数インジェクションやシェル解釈が存在しないか（`shell=False`）。
  - Herdr CLI 失敗時の JSON エラー抽出と、`classify_prompt_error` による明確な失敗 (`failed`) と結果不明 (`uncertain`) の厳格な切り分け。
- **想定される不具合・失敗経路**:
  - 不正な Git remote URL パースエラー、タイムアウト時のプロセスゾンビ化、`agent_not_found` と `agent_blocked` の誤分類。
- **依存タスク**: R001
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R003: Live Agent 検証 & Worker 追跡 (Live Agent Context & Worker Trace)

- **タスクID・名称**: `R003-live-agent`
- **目的**: 実行中の Herdr Live Agent の状態取得・検証、worktree 親和性照合、および worker trace による多重起動防止機構を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/live_agent.py` (152行)
  - `skills/herdr-orchestrator/scripts/orchestrator/worker_trace.py` (36行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/herdr_cli.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/identity.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/worktree.py`
- **参照設計書**:
  - [02-roles-and-task-contract.md](../../docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-4) (§4: routing identity)
  - [04-handoff-and-requester.md](../../docs/herdr-orchestrator/04-handoff-and-requester.md#sec-8) (§8: requester の識別)
  - [06-results-and-workflows.md](../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4) (§11.4: worker 解決と直前再確認)
- **関連テスト**:
  - `tests/test_orchestrator_phase1_live_agent.py`
- **重点確認事項**:
  - `get_live_agent` における `herdr agent get` 出力 JSON の厳格バリデーション。
  - `verify_worktree_affinity` におけるパス解決の正規化（`resolve()` によるシンボリックリンク解消と実パス比較）。
  - `assert_promptable_state` による `idle` / `done` のみの許可と、`working` / `blocked` / `unknown` の安全な拒否。
  - `worker_trace_matches_task` における task_id 照合の正確性。
- **想定される不具合・失敗経路**:
  - 作業ディレクトリがシンボリックリンクの場合の不一致誤判定、Agent プロセス停止時の例外伝播の不備。
- **依存タスク**: R001, R002
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R004: Registry スキーマ・ID・安全パス (Registry Schema, ID & Path Safety)

- **タスクID・名称**: `R004-registry-schema`
- **目的**: Task Registry の正本データモデル (canonical schema v1)、task_id 形式、および成果物パスのディレクトリトラバーサル防止機構を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/schema.py` (173行)
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/task_id.py` (22行)
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/paths_safe.py` (60行)
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/errors.py` (17行)
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/__init__.py` (4行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/paths.py`
- **参照設計書**:
  - [02-roles-and-task-contract.md](../../docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-5) (§5: 共通 task contract)
  - [03-task-registry.md](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-1) (§6.1–6.3: canonical schema, 4軸状態定義, §6.8: path safety)
- **関連テスト**:
  - `tests/test_orchestrator_phase2_registry.py` (スキーマ検証、パス安全性テスト)
- **重点確認事項**:
  - canonical schema v1 の必須フィールド網羅性、型チェック、追加不正フィールドの拒否。
  - 4軸ステータス（`task`, `dispatch`, `result`, `completion`）の許容値定義の完全性。
  - `task_id`（`task_` + UUID v4）の形式チェックが正規表現で厳格になされているか。
  - `require_result_artifact` / `resolve_result_ref` において、`../` や絶対パス、シンボリックリンクによるリポジトリ外参照が確実に拒否されるか。
- **想定される不具合・失敗経路**:
  - スキーマバリデーション漏れによる不正 JSON 保存、パストラバーサルによるシステム重要ファイルへのアクセス。
- **依存タスク**: R001
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R005: Registry 永続化・排他制御・I/O (Registry Persistence & Atomic Lock)

- **タスクID・名称**: `R005-registry-io`
- **目的**: Task Registry JSON のファイル読み書き、排他制御（`flock`）、一時ファイル書込＋アトミックリプレイス、パーミッション管理、保持期間パージ機構を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/io.py` (275行)
  - `skills/herdr-orchestrator/scripts/orchestrator/handoff_lock.py` (39行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/schema.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/task_id.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/paths.py`
- **参照設計書**:
  - [03-task-registry.md](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-4) (§6.4–6.7: 整合性と排他制御, §6.9: 保持期間とクリーンアップ, §6.10: handoff 実行排他)
- **関連テスト**:
  - `tests/test_orchestrator_phase2_registry.py` (ファイルIO、ロック競合、パージテスト)
- **重点確認事項**:
  - `mutate_task` における `fcntl.flock(LOCK_EX)` 取得、最新状態読込、mutator 実行、一時ファイル書込、`os.replace`、ロック解放の原子性と確実な解放（`finally` 節）。
  - 作成されるファイルおよびディレクトリのパーミッション（ファイル: `0600`、ディレクトリ: `0700`）の強制。
  - シンボリックリンクが存在する場合のオープン拒絶（`O_NOFOLLOW` 等）。
  - `purge_expired_tasks` による古い完了タスクの安全な削除処理。
  - `handoff_lock.py` による同一タスクの多重ディスパッチ排他制御。
- **想定される不具合・失敗経路**:
  - 例外発生時のロック残留、アトミック置換前の一時ファイル放置、競合更新時のデータ先祖返り。
- **依存タスク**: R001, R004
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R006A: Registry ライフサイクル & Dispatch 遷移 (Registry Operations: Lifecycle & Dispatch)

- **タスクID・名称**: `R006A-registry-lifecycle`
- **目的**: Task Registry のタスク作成、プロンプト開始記録、Dispatch 4値遷移（`sent`, `failed`, `uncertain`, `skipped`）、stale タスク検出機構を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py` (L1–L247)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/io.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/schema.py`
- **参照設計書**:
  - [03-task-registry.md](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-3) (§6.3: 4軸の状態遷移マトリクス, §6.6: 内部操作, §6.12–6.13: クラッシュ窓と復旧)
- **関連テスト**:
  - `tests/test_orchestrator_phase2_registry.py`
  - `tests/test_orchestrator_phase5_dispatch.py`
- **重点確認事項**:
  - `create_task` 時の初期ステータス（`task=created`, `dispatch=pending`, `result=pending`, `completion=pending/not_applicable`）の正確性。
  - `record_prompt_start` の二重実行防止（`herdr_prompt_started_at` が既に存在する場合の拒否）。
  - `mark_dispatch` によるステータス連動（`sent` → `task=in_progress`、`failed/skipped` → `task=failed, result=unavailable`、`uncertain` → `task=unknown`）の厳格性。
  - `find_stale_dispatch_candidates` による Scenario A/B 判定ロジック。
- **想定される不具合・失敗経路**:
  - 状態遷移マトリクス違反の更新受理、dispatch 失敗時の result/completion 連動漏れ。
- **依存タスク**: R004, R005
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R006B: Registry Result & Completion 遷移・Claim (Registry Operations: Result & Completion)

- **タスクID・名称**: `R006B-registry-ops`
- **目的**: Result 登録時の状態遷移、Completion claim eligibility (§9.13) のアトミック検証、Completion reset 受理条件 (§9.14)、4値完了遷移を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py` (L248–L551)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/io.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/paths_safe.py`
- **参照設計書**:
  - [03-task-registry.md](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-3) (§6.3: 4軸の状態遷移マトリクス)
  - [05-completion-and-recovery.md](../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-13) (§9.13: Completion Eligibility, §9.14: Reset 受理条件)
  - [06-results-and-workflows.md](../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-10) (§10: Result Contract)
- **関連テスト**:
  - `tests/test_orchestrator_phase2_registry.py`
  - `tests/test_orchestrator_phase3_result.py`
- **重点確認事項**:
  - `update_result` における早着 result (`dispatch=pending`) の `dispatch_not_ready` (retryable) 拒絶。
  - terminal result の冪等 replay（同一 payload の再登録許可、不一致時の `registry_conflict`）。
  - 初回配送失敗 terminal タスクに対する `update_result` および `reset_completion` の拒否。
  - `assert_completion_claim_eligible` による 5 つの必須条件の厳密な判定。
  - `claim_completion` の排他性（`mutate_task` 内での `pending -> processing` 遷移）。
  - `reset_completion` における `recovery_count` のインクリメントと状態の `pending` 復帰。
- **想定される不具合・失敗経路**:
  - worker 未受領タスクの不正な completion claim、二重完了更新による競合例外。
- **依存タスク**: R004, R005, R006A
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R007: Dispatch コマンド & 診断・復旧 (Dispatch Commands & Recovery)

- **タスクID・名称**: `R007-dispatch`
- **目的**: 未確定 dispatch の診断 CLI (`doctor`)、クラッシュ窓復旧 (`reconcile-dispatch`)、および手動確定インターフェースの安全性を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/dispatch_commands.py` (81行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/worker_trace.py`
- **参照設計書**:
  - [03-task-registry.md](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-12) (§6.12–6.13: クラッシュ窓の検出と復旧)
  - [05-completion-and-recovery.md](../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-10) (§9.10: Human Awareness Contract)
- **関連テスト**:
  - `tests/test_orchestrator_phase5_dispatch.py`
- **重点確認事項**:
  - `doctor` における非破壊診断（Registry を変更しないことの保証）。
  - 生存中の wrapper が handoff flock を保持している間の `reconcile-dispatch` の更新拒否。
  - Scenario A（未送信確定）と Scenario B（送信開始後クラッシュ）の正確な分岐。
  - 未配送確定時の `--dispatch-reason` 必須チェックおよび blind resend の絶対禁止。
- **想定される不具合・失敗経路**:
  - 実行中プロセスのタスクを誤って reconcile することによる二重ディスパッチ、不完全な復旧引数での更新。
- **依存タスク**: R003, R005, R006A
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R008: Handoff オーケストレーション & Sync (Handoff Pipeline & Sync Timeout)

- **タスクID・名称**: `R008-handoff`
- **目的**: Handoff パイプライン（事前検証 → ロック → Registry 作成 → prompt 送信 → 状態確定）、最小 Task Envelope、および sync handoff の結果確定ポーリング・タイムアウト機構を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/handoff_commands.py` (304行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/handoff_lock.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/live_agent.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/herdr_cli.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`
- **参照設計書**:
  - [03-task-registry.md](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-11) (§6.11: handoff 実行シーケンス, §6.14: sync 完了待ち)
  - [04-handoff-and-requester.md](../../docs/herdr-orchestrator/04-handoff-and-requester.md#sec-7) (§7: sync / async handoff)
  - [06-results-and-workflows.md](../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4) (§11.4: 受信側手順と最小 prompt)
- **関連テスト**:
  - `tests/test_orchestrator_phase4_handoff.py`
- **重点確認事項**:
  - Registry 作成前に caller live agent および worker live agent の事前検証が完了しているか。
  - `build_task_envelope` が `task.id` と Registry 読取指示のみの最小プロンプトになっているか。
  - `run_herdr` 呼び出し前後のクラッシュ窓に対する `record_prompt_start` と `mark_dispatch` の順序。
  - sync handoff におけるポーリング間隔、タイムアウト処理、早期失敗（`task.status=failed`）検知と即時終了。
- **想定される不具合・失敗経路**:
  - handoff lock 解放漏れ、prompt 送信失敗時の未確定状態残留、sync タイムアウト時の無限ループ。
- **依存タスク**: R002, R003, R005, R006A
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R009: Result 登録・読み取りコマンド (Result Commands & Durable Result Locator)

- **タスクID・名称**: `R009-result`
- **目的**: Worker による結果登録 (`result-submit`)、Caller コンテキスト検証、Task 状態照会 (`task-status`)、結果取得 (`task-result`)、および best-effort completion 連鎖呼び出しを検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/result_commands.py` (178行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/live_agent.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/completion_commands.py`
- **参照設計書**:
  - [03-task-registry.md](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-5) (§6.5: Registry 操作インターフェース)
  - [05-completion-and-recovery.md](../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9-5) (§9.5: Result Before Completion)
  - [06-results-and-workflows.md](../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-10) (§10: Result Contract)
- **関連テスト**:
  - `tests/test_orchestrator_phase3_result.py`
- **重点確認事項**:
  - `verify_worker_submit_context` による caller agent 名、worktree パス、リポジトリ識別子の厳格な一致確認。
  - `result_submit` 成功後の best-effort completion 連鎖が非破壊的に呼び出されるか（連鎖失敗時も result 登録自体は成功を維持すること）。
  - `task_status` / `task_result` における機微情報の適切なマスクと完全なタスク情報の返却。
- **想定される不具合・失敗経路**:
  - 第三者エージェントによる不正な結果登録、completion 連鎖の例外が result submit をクラッシュさせる不具合。
- **依存タスク**: R003, R004, R006B
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R010: Async Completion & リカバリー (Completion Commands & Requester Resume)

- **タスクID・名称**: `R010-completion`
- **目的**: Async completion の自動連鎖、明示的タスク再開 (`resume-task`)、リセット (`reset-completion`)、リカバリー (`recover-completion`)、直前再確認 TOCTOU、および二重配送抑止を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/completion_commands.py` (260行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/live_agent.py`
  - `skills/herdr-orchestrator/scripts/orchestrator/herdr_cli.py`
- **参照設計書**:
  - [05-completion-and-recovery.md](../../docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9) (§9: Completion / Callback 設計, §9.3: resume_requester, §9.7: processing 復旧, §9.13–9.14)
- **関連テスト**:
  - `tests/test_orchestrator_phase6_completion.py`
- **重点確認事項**:
  - `_execute_requester_completion` において、`_require_live_pane_id` の例外発生時や各種失敗時に `finish_completion` が確実に呼ばれ、`completion.status` が `"processing"` のまま永続スタックしないか。
  - 2回目の `resolve_requester()` 取得後に `agent_status` のチェック（`idle`/`done` のみ許容、`working`/`blocked`/`unknown` は `skipped`）が漏れなく適用されているか。
  - `run_recover_completion` における `sent`, `uncertain`, `processing` に対する二重配送抑止ガード。
  - `reset-completion` と `recover-completion` の 2 段階分離設計（reset 成功と claim 実行の分離）。
- **想定される不具合・失敗経路**:
  - `pane_id` 不在時の `"processing"` 放置、作業中エージェントへの不正な割り込み prompt 送信、未処理例外による呼び出し元巻き込みクラッシュ。
- **依存タスク**: R002, R003, R006B, R008, R009
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R011: Public CLI & レガシー CLI (Public CLI & Output Formatting)

- **タスクID・名称**: `R011-public-cli`
- **目的**: 統一 CLI エントリポイント、引数構文解析、JSON 出力契約、エラーハンドリング、終了コード規約、および旧 `task-registry.py` との整合性を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/scripts/herdr-orchestrator` (15行)
  - `skills/herdr-orchestrator/scripts/orchestrator/public_cli.py` (240行)
  - `skills/herdr-orchestrator/scripts/orchestrator/cli_output.py` (65行)
  - `skills/herdr-orchestrator/scripts/task-registry.py` (250行)
- **参照ファイル**: 全コマンドモジュール
- **参照設計書**:
  - [07-skill-and-implementation.md](../../docs/herdr-orchestrator/07-skill-and-implementation.md#sec-14) (§14.2: public CLI と内部 operation の分離)
  - 各機能章の CLI 仕様
- **関連テスト**:
  - `tests/test_orchestrator_phase0.py`
  - `tests/test_orchestrator_phase2_registry.py`
- **重点確認事項**:
  - すべてのコマンドで `--help` および不正引数が適切に処理されるか。
  - 正常終了時は終了コード 0 かつ stdout に有効な JSON 単一出力、異常時は終了コード 1 かつ `{"error": ...}` JSON 出力となっているか（不要な stdout 出力の混入禁止）。
  - 各種例外（`HandoffCommandError`, `ResultCommandError`, `CompletionCommandError`, `RegistryError`, `HerdrEnvError`）の一貫したハンドリング。
  - `task-registry.py` の提供機能と `herdr-orchestrator` の重複・互換性維持。
- **想定される不具合・失敗経路**:
  - 予期しない例外によるスタックトレースの生出力、JSON パースエラーを招く標準出力の汚染。
- **依存タスク**: R001〜R010
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R012: インストーラ & 環境セットアップ (Installer & Distribution Scripts)

- **タスクID・名称**: `R012-installer`
- **目的**: リモート／ローカル配布インストーラ (`install.sh`)、開発用セットアップスクリプト、シンボリックリンク生成、検証スクリプト、Codex 実行ポリシーの安全性を検証する。
- **主レビュー対象ファイル**:
  - `install.sh` (446行)
  - `skills/herdr-orchestrator/install/dev-local-install.sh` (99行)
  - `skills/herdr-orchestrator/install/setup.sh` (72行)
  - `skills/herdr-orchestrator/install/link-cli.sh` (33行)
  - `skills/herdr-orchestrator/install/codex-execpolicy-snippet.rules`
  - `skills/herdr-orchestrator/install/README.md`
  - `scripts/verify-orchestrator.sh` (20行)
  - `scripts/print-automated-verification-snippet.sh` (23行)
- **参照ファイル**:
  - `skills/herdr-orchestrator/references/distribution-install.md`
  - `skills/herdr-orchestrator/references/dev-local-install.md`
- **参照設計書**:
  - [07-skill-and-implementation.md](../../docs/herdr-orchestrator/07-skill-and-implementation.md#sec-16) (§16: 中央 Skill の配布と更新)
  - [08-adoption-and-verification.md](../../docs/herdr-orchestrator/08-adoption-and-verification.md#sec-17) (§17: 導入前提と環境要件)
- **関連テスト**:
  - `tests/test_distribution_installer.py` (767行)
  - `tests/test_orchestrator_phase8_adoption.py` (148行)
- **重点確認事項**:
  - `curl | bash` での安全な実行（途中中断対策、`set -euo pipefail`）。
  - インストール先ディレクトリ（`~/.agents/skills/herdr-orchestration`）のパーミッションと既存ファイルのバックアップ／上書き保護。
  - PATH へのシンボリックリンク作成時の既存コマンド衝突検知。
  - アンインストール／クリーンアップ手順の完全性。
- **想定される不具合・失敗経路**:
  - 不完全なダウンロードによるスクリプト部分実行、誤ったディレクトリ削除、権限不足時の不完全状態残留。
- **依存タスク**: R011
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R013: Skills 定義 & 運用リファレンス (Skill Definitions & Reference Docs)

- **タスクID・名称**: `R013-skills`
- **目的**: 中央 Skill (`skills/herdr-orchestrator/SKILL.md`)、リポジトリ固有 Skill (`repo-review`, `repo-workflow`, `design-doc-markdown-format`)、および各種リファレンス文書の記述正確性と規則整合性を検証する。
- **主レビュー対象ファイル**:
  - `skills/herdr-orchestrator/SKILL.md`
  - `skills/herdr-orchestrator/references/*.md` (8ファイル: `adoption.md`, `design-links.md`, `dev-local-install.md`, `distribution-install.md`, `handoff.md`, `public-cli.md`, `requester-receive.md`, `roles.md`, `worker-receive.md`)
  - `.agents/skills/repo-review/SKILL.md`, `references.md`
  - `.agents/skills/repo-workflow/SKILL.md`
  - `.agents/skills/design-doc-markdown-format/SKILL.md`
- **参照ファイル**: 設計書全般、CLI 実装
- **参照設計書**:
  - [06-results-and-workflows.md](../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4) (§11.4: worker 側手順, §12: reviewer workflow)
  - [07-skill-and-implementation.md](../../docs/herdr-orchestrator/07-skill-and-implementation.md#sec-14) (§14: Skill 構成)
  - [08-adoption-and-verification.md](../../docs/herdr-orchestrator/08-adoption-and-verification.md#sec-19) (§19: 移行シナリオ)
- **関連テスト**:
  - `tests/test_orchestrator_phase7_skill.py`
- **重点確認事項**:
  - 各 Skill の YAML frontmatter（`name`, `description`）の構文妥当性。
  - Skill 内に記載された CLI コマンド例が実装と 100% 一致しているか（オプション名、引数順序、出力形式）。
  - Herdr 公式 Skill との責務分離（Herdr 操作の直接説明を複製せず、委譲しているか）。
  - レビュー依頼・結果成果物（`.review-inbox/*.md`）のフォーマット契約の整合性。
- **想定される不具合・失敗経路**:
  - 古い CLI オプションの記載によるエージェント実行時エラー、Skill 間の定義矛盾。
- **依存タスク**: R011, R012
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

### R014: CI/CD パイプライン & リリース (CI/CD Workflows & Release Automation)

- **タスクID・名称**: `R014-cicd`
- **目的**: GitHub Actions ワークフロー (`ci.yml`, `release.yml`)、npm スクリプト定義、およびリリース自動化のセキュリティ・整合性を検証する。
- **主レビュー対象ファイル**:
  - `.github/workflows/ci.yml` (56行)
  - `.github/workflows/release.yml` (193行)
  - `package.json`
- **参照ファイル**:
  - `install.sh`
  - `scripts/verify-orchestrator.sh`
- **参照設計書**:
  - [08-adoption-and-verification.md](../../docs/herdr-orchestrator/08-adoption-and-verification.md#sec-18) (§18: 動作確認と完了条件)
  - [09-maintenance-and-roadmap.md](../../docs/herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20) (§20: バージョン方針)
- **関連テスト**:
  - ワークフロー実機ログ、`npm run test:orchestrator`, `npm run verify:orchestrator`
- **重点確認事項**:
  - CI の checkout 安全性、concurrency 制御による無駄な並行実行・競合防止。
  - テストゲート（Markdown フォーマット、Lint、unittest 199テスト、検証スクリプト）の完全性。
  - リリース自動化におけるタグトリガー、アセットパッケージング、GitHub Releases 発行手順の権限最小化（`GITHUB_TOKEN` 権限）。
  - `package.json` 内のスクリプト定義と実際のファイルパスの一致。
- **想定される不具合・失敗経路**:
  - リリースタグ作成時のパーミッション不足、未テスト状態でのリリース成果物公開。
- **依存タスク**: R012, R013
- **完了条件**: 2段階レビュー完了、反証確認実施、結果記録。

---

## 4. 結合レビュー計画 (Integration Reviews)

個別レビューでは見落とされがちな、複数機能の連携・ライフサイクル全体の契約を検証するタスクである。

---

### I001: Registry と Handoff / Dispatch の状態整合性結合レビュー

- **タスクID・名称**: `I001-registry-handoff`
- **目的**: タスク発行からワーカーへの配送に至る一連のフェーズにおいて、ロック排他・状態遷移・クラッシュ復旧の整合性を検証する。
- **関連個別タスク**: R005 (Registry IO/Lock), R006A (Registry Lifecycle), R007 (Dispatch), R008 (Handoff)
- **重点確認事項**:
  - handoff lock (`task_<id>.handoff.lock`) と Registry file lock (`task_<id>.json.lock`) の取得順序とデッドロック非発生の確認。
  - `create_task` 直前・直後・`record_prompt_start` 直前・直後・`mark_dispatch` 直前・直後の各障害点における状態整合性。
  - A/B/B' クラッシュ窓における `find_stale_dispatch_candidates` と `reconcile-dispatch` の復旧動作の完全性。
- **想定される不具合**:
  - デッドロック、送信完了前の stale 誤判定、クラッシュ復旧後の多重配送。
- **完了条件**: 複合シナリオでの検証完了、結果記録。

---

### I002: Handoff 配送と Result 登録の契約結合レビュー

- **タスクID・名称**: `I002-handoff-result`
- **目的**: ワーカーによるタスク受領から成果物保存、Registry への結果登録に至る契約を検証する。
- **関連個別タスク**: R003 (Live Agent), R006B (Registry Ops), R008 (Handoff), R009 (Result)
- **重点確認事項**:
  - ワーカーが Task Envelope を受信してから `task-status` で正本確認する手順の遵守（偽プロンプト対策）。
  - 早着 `result-submit`（`dispatch=pending`）の retryable 拒否と、dispatch 確定後の再登録の冪等性。
  - ワーカーの caller pane / worktree / repository 照合が意図しないエージェントからの結果登録を完全に遮断できるか。
- **想定される不具合**:
  - 別エージェントによる結果上書き、早着リザルトによるディスパッチ状態破綻。
- **完了条件**: 複合シナリオでの検証完了、結果記録。

---

### I003: Result 登録と Completion 通知・復旧の連携結合レビュー

- **タスクID・名称**: `I003-result-completion`
- **目的**: 結果確定後の requester 再開連鎖、eligibility 検証、および失敗時の安全な復旧フローを検証する。
- **関連個別タスク**: R006B (Registry Ops), R009 (Result), R010 (Completion)
- **重点確認事項**:
  - `result-submit` 直後の best-effort completion 連鎖における claim eligibility (§9.13) の厳密性。
  - 初回配送失敗（`dispatch=failed|skipped`）のタスクに対する completion claim および reset の完全な拒絶。
  - `recover-completion` による reset と claim の分離動作（eligibility 未達時に reset のみで安全停止すること）。
- **想定される不具合**:
  - 未確定タスクへの誤通知、二重再送による無限ループ。
- **完了条件**: 複合シナリオでの検証完了、結果記録。

---

### I004: CLI と内部コマンドのエラー契約・終了コード結合レビュー

- **タスクID・名称**: `I004-cli-contract`
- **目的**: CLI エントリポイントから各コマンド、内部操作に至る例外・エラー・終了コード・JSON 出力の統一性を検証する。
- **関連個別タスク**: R007 (Dispatch), R008 (Handoff), R009 (Result), R010 (Completion), R011 (Public CLI)
- **重点確認事項**:
  - すべてのコマンドエラーが適切な HTTP/CLI エラーコード構造を持つ JSON で stdout に出力されるか。
  - 成功時 exit code 0、失敗時 exit code 1 の徹底。
  - `task-status` や `task-result` などの診断コマンドが異常時でも JSON を壊さないこと。
- **想定される不具合**:
  - 標準エラー出力（stderr）への JSON 漏洩、パース不能な例外メッセージの混入。
- **完了条件**: CLI インターフェース契約の網羅確認、結果記録。

---

### I005: インストーラ・CI/CD・Skills のエコシステム整合性結合レビュー

- **タスクID・名称**: `I005-ecosystem`
- **目的**: 配布パッケージング、インストールスクリプト、Skills、リポジトリワークフロー、CI/CD ゲートの全体的な整合性を検証する。
- **関連個別タスク**: R012 (Installer), R013 (Skills), R014 (CI/CD)
- **重点確認事項**:
  - `install.sh` で導入した Skill 環境が `repo-review` や `repo-workflow` から正しく呼び出せるか。
  - CI パイプラインでの自動テストがインストーラや検証スクリプトの全網羅条件を満たしているか。
  - 設計書・運用リファレンス・実コード間の用語・設定値の完全一致。
- **想定される不具合**:
  - 環境導入後の PATH 不通、CI を通過したが実機で動作しないインストーラ不整合。
- **完了条件**: 全体エコシステムの結合整合性確認、結果記録。

---

## 5. カバレッジ確認（全ファイル対応表）

リポジトリ内の全ファイルと担当タスクの完全な対応マッピングである。
**主担当タスクのない実装・設定ファイルは存在しない。**

| ファイルパス                                                                        | 種別                 | 主担当タスク | 参照タスク                   |
| ----------------------------------------------------------------------------------- | -------------------- | :----------: | ---------------------------- |
| `skills/herdr-orchestrator/scripts/orchestrator/paths.py`                           | Python (基盤)        |   **R001**   | R004, R005, R011             |
| `skills/herdr-orchestrator/scripts/orchestrator/env.py`                             | Python (基盤)        |   **R001**   | R002, R007, R011             |
| `skills/herdr-orchestrator/scripts/orchestrator/errors.py`                          | Python (基盤)        |   **R001**   | R011                         |
| `skills/herdr-orchestrator/scripts/orchestrator/worktree.py`                        | Python (基盤)        |   **R001**   | R002, R003, R008             |
| `skills/herdr-orchestrator/scripts/orchestrator/naming.py`                          | Python (基盤)        |   **R001**   | R008                         |
| `skills/herdr-orchestrator/scripts/orchestrator/identity.py`                        | Python (識別)        |   **R002**   | R003, R008                   |
| `skills/herdr-orchestrator/scripts/orchestrator/herdr_cli.py`                       | Python (外部連携)    |   **R002**   | R003, R007, R008, R010       |
| `skills/herdr-orchestrator/scripts/orchestrator/live_agent.py`                      | Python (Agent)       |   **R003**   | R008, R009, R010, I002       |
| `skills/herdr-orchestrator/scripts/orchestrator/worker_trace.py`                    | Python (Agent)       |   **R003**   | R006A, R007, I001            |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/__init__.py`               | Python (Registry)    |   **R004**   | -                            |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/errors.py`                 | Python (Registry)    |   **R004**   | R005, R006A, R006B, R011     |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/task_id.py`                | Python (Registry)    |   **R004**   | R005, R006A, R006B, R011     |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/paths_safe.py`             | Python (Registry)    |   **R004**   | R006B, R009                  |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/schema.py`                 | Python (Registry)    |   **R004**   | R005, R006A, R006B           |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/io.py`                     | Python (Registry)    |   **R005**   | R006A, R006B, I001           |
| `skills/herdr-orchestrator/scripts/orchestrator/handoff_lock.py`                    | Python (排他)        |   **R005**   | R008, I001                   |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py` (L1–L247)   | Python (Registry)    |  **R006A**   | R007, R008, I001             |
| `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py` (L248–L551) | Python (Registry)    |  **R006B**   | R009, R010, I002, I003       |
| `skills/herdr-orchestrator/scripts/orchestrator/dispatch_commands.py`               | Python (Command)     |   **R007**   | R011, I001, I004             |
| `skills/herdr-orchestrator/scripts/orchestrator/handoff_commands.py`                | Python (Command)     |   **R008**   | R010, R011, I001, I002, I004 |
| `skills/herdr-orchestrator/scripts/orchestrator/result_commands.py`                 | Python (Command)     |   **R009**   | R011, I002, I003, I004       |
| `skills/herdr-orchestrator/scripts/orchestrator/completion_commands.py`             | Python (Command)     |   **R010**   | R009, R011, I003, I004       |
| `skills/herdr-orchestrator/scripts/herdr-orchestrator`                              | CLI (エントリ)       |   **R011**   | I004, I005                   |
| `skills/herdr-orchestrator/scripts/orchestrator/public_cli.py`                      | Python (CLI)         |   **R011**   | I004                         |
| `skills/herdr-orchestrator/scripts/orchestrator/cli_output.py`                      | Python (CLI)         |   **R011**   | I004                         |
| `skills/herdr-orchestrator/scripts/task-registry.py`                                | Python (CLI)         |   **R011**   | -                            |
| `install.sh`                                                                        | Shell (インストーラ) |   **R012**   | I005                         |
| `skills/herdr-orchestrator/install/dev-local-install.sh`                            | Shell (開発導入)     |   **R012**   | I005                         |
| `skills/herdr-orchestrator/install/setup.sh`                                        | Shell (セットアップ) |   **R012**   | I005                         |
| `skills/herdr-orchestrator/install/link-cli.sh`                                     | Shell (リンク)       |   **R012**   | I005                         |
| `skills/herdr-orchestrator/install/codex-execpolicy-snippet.rules`                  | 設定 (ポリシー)      |   **R012**   | I005                         |
| `skills/herdr-orchestrator/install/README.md`                                       | ドキュメント         |   **R012**   | I005                         |
| `scripts/verify-orchestrator.sh`                                                    | Shell (検証)         |   **R012**   | R014, I005                   |
| `scripts/print-automated-verification-snippet.sh`                                   | Shell (検証)         |   **R012**   | R014, I005                   |
| `skills/herdr-orchestrator/SKILL.md`                                                | Skill (中央)         |   **R013**   | I005                         |
| `skills/herdr-orchestrator/references/*.md` (8ファイル)                             | ドキュメント         |   **R013**   | I005                         |
| `.agents/skills/repo-review/SKILL.md`, `references.md`                              | Skill (レビュー)     |   **R013**   | I005                         |
| `.agents/skills/repo-workflow/SKILL.md`                                             | Skill (ワークフロー) |   **R013**   | I005                         |
| `.agents/skills/design-doc-markdown-format/SKILL.md`                                | Skill (フォーマット) |   **R013**   | I005                         |
| `.github/workflows/ci.yml`                                                          | CI/CD (Workflow)     |   **R014**   | I005                         |
| `.github/workflows/release.yml`                                                     | CI/CD (Workflow)     |   **R014**   | I005                         |
| `package.json`, `package-lock.json`                                                 | ビルド・依存設定     |   **R014**   | I005                         |
| `prettier.config.mjs`, `.markdownlint-cli2.mjs`, `.gitignore`, `.prettierignore`    | 静的検証設定         |   **R014**   | -                            |

---

## 6. 実行・進捗管理・再開方法 (GitHub 運用)

### 6.1 ディレクトリ構成と成果物

各タスクのレビュー対象および観点定義は `docs/reviews/*/request.md` として静的に管理する。
レビュー結果・判定・再レビューの記録はローカルファイル（`result.md` 等）への二重記録を行わず、対応する GitHub Scope Issue のコメントに蓄積する。

```text
docs/reviews/
├── INDEX.md                 # 初回20 Scope 定義一覧・GitHub Issue リンク集
├── PLAN.md                  # 本計画書
├── CONTINUOUS_REVIEW_SIMPLIFIED_DESIGN.md  # 継続的レビュー運用設計書 (正本)
├── CONTINUOUS_REVIEW_DESIGN.md             # 旧アーキテクチャ検討履歴 (アーカイブ)
├── R001-primitives/
│   └── request.md           # レビュー依頼書・対象定義 (静的)
├── R002-identity-cli/
│   └── request.md
...
├── I001-registry-handoff/
│   └── request.md
...
```

### 6.2 指摘管理体系 (GitHub Finding Issue)

検出された指摘事項は GitHub Finding Issue として起票・管理する。

- 形式: 1 指摘 = 1 Issue
- ラベル体系（`scripts/setup-review-labels.sh` に定義）:
  - 種別: `review-finding`
  - Blocking: `blocking:yes` (Global GOをブロックする未是正の指摘。P0/P1または例外BlockingのP2)
  - 重大度: `severity:p0` (致命的), `severity:p1` (重大・Blocking), `severity:p2` (通常・原則Non-blocking / 例外Blockingあり), `severity:p3` (軽微・改善)
  - 進行ステータス: `status:ready-to-fix` (修正待ち), `status:verified` (修正確認完了) ※レビュー中は `status:in-review`
- 追跡: Finding Issue 本文に対象 Scope Issue への参照リンク（例: `Scope: #15`）を含め、Scope Issue 側からも検出指摘一覧として相互リンクする。

### 6.3 進捗ステータス管理

Scope Issue（レビュー作業の進捗）と Finding Issue（指摘事項の解決進捗）の責務を分離して管理する：

1. **Scope Issue**:
   - `Open`: レビュー未着手、またはレビュー解析・結果記録が進行中
   - `Closed`: レビュー解析、総合判定、結果コメント投稿、および未解決指摘がある場合の Finding Issue 起票・相互リンクが完了（レビュー作業完了）
   - ※ Scope Issue のクローズはレビュー作業自体の完了を示すものであり、未解決 Finding が存在しないことの証明ではない。
2. **Finding Issue**:
   - **状態の分離管理**: 「レビューGO（修正承認）」「PRマージ完了」「Finding解決完了」の3つの状態を明確に区別する。
     - **レビューGO**: 修正内容のレビューが合格した状態（マージ可能であることを示すのみ。Issue は Open を維持）。
     - **PRマージ完了**: 修正コードが対象ブランチ（`main` 等）へマージされた状態。
     - **Finding解決完了**: PR マージ後にマージコミットに対する再検証が完了し、全是正完了条件を満たしてクローズされた状態。
     - ※ **レビューGO だけを根拠として Finding Issue をクローズすることは厳禁とする**。

#### Finding Issue ライフサイクル状態遷移

| 状態                   | Issue State        | ラベル                                             | 条件・説明                                                                            |
| :--------------------- | :----------------- | :------------------------------------------------- | :------------------------------------------------------------------------------------ |
| **修正待ち**           | Open               | `status:ready-to-fix`                              | 指摘が受理され、修正作業中（初期状態）。                                              |
| **レビュー合格**       | Open               | `status:ready-to-fix`                              | 修正 PR のレビューで GO を獲得。PR マージ待ち（**Issue は Open を維持**）。           |
| **マージ済み・未検証** | Open または Closed | `status:ready-to-fix` 等（`status:verified` なし） | PR がマージされた直後の状態。自動クローズ方式時は一時的に Closed となるが未検証扱い。 |
| **解決完了**           | Closed             | `status:verified`                                  | マージコミットでの再検証に合格し、全是正完了条件を満たしてクローズされた状態。        |
| **再検証失敗**         | Open               | `status:ready-to-fix` 等                           | マージ後の再検証で不合格となった状態。Closed だった場合は Re-open して再修正。        |

- **クローズ方式の選択**:
  - **手動クローズ方式（標準）**: PR 本文に `Refs #XX` を記載。PR マージ後に再検証を実施し、合格後に `status:ready-to-fix` 削除、`status:verified` 付与、Issue クローズを行う。原則として本方式を標準とする。
  - **GitHub 自動クローズ方式（許容・不発時フォールバック必須）**: PR 本文に `Closes #XX` を記載。PR マージ後に直ちに Issue の実状態を再取得する。Closed の場合は一時的未検証状態として再検証へ進む。リポジトリ設定等の影響で自動クローズが不発（Open のまま）となった場合は、Open のまま放置せず手動クローズへ安全にフォールバックする。再検証失敗時は直ちに `gh issue reopen` して未解決に戻す。
- **解決完了の判定構造（2段階判定）**:
  - **Phase 1: 完了処理の実行可否判定（事前確認）**:
    - 対応する修正 PR が特定され、マージ済みであること。
    - マージ先ブランチとマージコミット SHA が確認できること。
    - マージコミットに対する再検証（テスト等）がすべて合格していること。
    - Finding 本文の「是正完了条件」をすべて満たしていること。
    - 再検証結果と対象 SHA が Issue コメントに記録されていること。
    - ※ Phase 1 をすべて満たす場合のみ Phase 2 へ進行可能（未達時の `status:verified` 付与禁止）。
  - **Phase 2: 状態更新と事後確認（事後検証）**:
    - `status:ready-to-fix` の削除、`status:verified` の付与、Issue クローズの実行。
    - `gh issue view` による再取得で、`Closed` かつ `status:verified` を機械的に最終確認すること。
    - 途中失敗時は成功扱いとせず、現在の状態と残作業を報告して安全停止すること。

### 6.4 中断と再開のプロトコル

1. **中断時**:
   - 対象 GitHub Scope Issue のコメントに「中断理由」「確認完了範囲」「未確認ファイル/項目」を投稿する。
   - **中断タスクを GO と判定することは絶対に禁止する**。
2. **再開時**:
   - 初回全体レビューで確定した共通対象コミットSHAを維持し、前回のコメントの「未確認項目」からレビューを再開する。
   - 修正対応後の再レビュー時は、変更前後のコミットSHAおよび差分の再検証範囲を記録する。

### 6.5 全体完了判定 (Global GO Gate)

リポジトリ全体の初回レビュー完了判定は、単一 Scope の完了ではなく、以下のチェックリストをすべて満たした場合にのみ下される：

- [ ] 全 15 の個別レビュー Scope Issue（R001〜R014）のレビュー作業が完了し、Closed であること（各実施時点のレビュー判定・指摘が記録されていること）。
- [ ] 全 5 の結合レビュー Scope Issue（I001〜I005）のレビュー作業が完了し、Closed であること（各実施時点のレビュー判定・指摘が記録されていること）。
- [ ] 検出されたすべての Blocking 指摘（`blocking:yes` ラベルが付与された Finding Issue、すなわち `severity:p0`, `severity:p1`、および例外的に Blocking 指定された `severity:p2`）が是正・検証され、Closed（`status:verified`）であること。
  - **判定手順 1（未是正 Open 件数の全件確認）**: `gh api --paginate "repos/:owner/:repo/issues?labels=review-finding,blocking:yes&state=open&per_page=100" --jq '.[] | select(has("pull_request") | not) | "#\(.number): \(.title)"'` の実行結果が 0 件（出力行なし、終了コード 0）であること。※ API エラーや通信障害による取得失敗を 0 件と誤判定しないこと。
  - **判定手順 2（修正未検証 Closed の全件検出）**: `gh api --paginate "repos/:owner/:repo/issues?labels=review-finding,blocking:yes&state=closed&per_page=100" --jq '.[] | select(has("pull_request") | not) | select(.labels | map(.name) | index("status:verified") | not) | "#\(.number): \(.title)"'` の実行結果が 0 件（`status:verified` が付与されずに Closed となった Blocking Finding が 0 件、終了コード 0）であること。未検証のまま Closed にされた Issue が 1 件でも存在する場合は Global GO を保留する。
- [ ] Non-blocking 指摘（`blocking:yes` ラベルを持たない `severity:p2`, `severity:p3`）について、保留または採否が明確に合意されていること。
- [ ] 修正に伴う再レビューが行われた場合は、その再検証結果と対象 SHA が記録されていること。
- [ ] すべてのレビュー対象が同一の共通確定コミットSHA（および是正コミット）に対して検証されていること。
