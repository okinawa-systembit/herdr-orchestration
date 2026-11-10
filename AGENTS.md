# リポジトリ作業ガイド

## 基本方針（Core Principles）

本リポジトリおよびオーケストレーション基盤の設計・開発・レビューでは、過剰な複雑化を防ぎ、長期的な保守性と安全性を確保するため、以下の原則と指針を遵守する。詳細な解説・具体規則・移行注記を含む単一正本は **[基本方針正本](docs/core-principles.md)** を参照する（[Issue #113](https://github.com/okinawa-systembit/herdr-orchestration/issues/113)）。

### 7 大設計原則（要約）

1. **シンプル is ベスト（KISS 原則）**: 複雑な仕組み・過剰な状態管理・パーミッション縛りを排除し、最も単純で壊れにくいアプローチを優先する。ファイルの作成・更新は各エージェント環境の標準的なファイル操作機能を利用し、複雑なシェルヒアドキュメント（`cat << 'EOF'`）や変数代入チェーンは避ける（特定のツール名は強制しない）。
2. **保守性を高める・人間中心の可観測性**: ブラックボックスを作らず、作業領域は `.input/` に一元化して人間が直感的に把握できるようにする。
3. **車輪の再発明はしない**: `git`、`gh` CLI、Prettier、Unix 標準コマンド等の既存ツール・公式機能に素直に委譲・活用する。
4. **依存管理の境界分離**: 開発環境は柔軟にツールを導入し、配布物（中央 Skill・CLI）は Python 標準ライブラリと OS 標準コマンドのみ（ゼロ依存）とする。
5. **秘密は使う 1 か所だけにインラインで渡す**: 認証トークンや鍵の存在範囲をコマンドの一瞬に限定し、トークンが見える環境で他のツールを動かさない。
6. **1 つの事実は 1 か所だけに持たせ、機械検査とセットで定義する**: ステータスや設定の多重管理を禁止し、単一正本と機械検査（lint/check）をセットで運用する。
7. **修正ラウンドでスコープを広げない**: レビュー指摘の修正コミットには指摘の是正のみを入れ、新機能追加や大規模移行を混ぜない。

### 3 大実践指針（要約）

1. **（指針 8）機械検査とエージェント判断の境界**: 致命的事故（漏洩・改ざん）や固定形式は機械で止め、自由な文章の意味や妥当性はエージェント判断に任せる（「ただし〜を除く」が 2 つ以上必要な機械チェックは作らない）。
2. **（指針 9）テンプレート主導による記述と機械制御値の完全分離**: 機械制御値（役割、Round、Status）は外側ヘッダーとして付与し、本文はテンプレートに沿った自由記述として構文解析・自動挿入・過剰バリデーションを行わない（※ review-sync のテンプレート主導移行および制御値完全分離は [Issue #116](https://github.com/okinawa-systembit/herdr-orchestration/issues/116) および [Issue #119](https://github.com/okinawa-systembit/herdr-orchestration/issues/119) にて完了）。
3. **（指針 10）黙って失敗しない**: 補助機能のスキップ時も警告を標準エラーに出し、テストは引数変換だけでなく実際の結果を検証する。

## Herdr Orchestrator 実装

作業順序は [実装 INDEX](docs/herdr-orchestrator-implementation-index.md)。CLI 入口は `skills/herdr-orchestrator/scripts/herdr-orchestrator`。段階 0–8 の自動テスト: `npm run test:orchestrator`。**開発時ローカルインストール**: `npm run install:orchestrator-dev`（`~/.agents/skills/herdr-orchestration`）— [dev-local-install.md](skills/herdr-orchestrator/references/dev-local-install.md)。導入前確認: `npm run verify:orchestrator`。配布方針: [distribution-install.md](skills/herdr-orchestrator/references/distribution-install.md)。§18 実機記録: [docs/herdr-orchestrator-verification-log.md](docs/herdr-orchestrator-verification-log.md)。
PR を作成するときは必ず `bash scripts/review-sync.sh pr-create`（Bot）を使用する（役割別の作業・相談手順の共通入口は [repo-workflow](.agents/skills/repo-workflow/SKILL.md) を参照）。

## Herdr Orchestrator レビュー（本リポジトリ）

sync handoff + `repo-review-helper` CLI + `herdr-orchestrator` CLI: [repo-review](.agents/skills/repo-review/SKILL.md)。Finding起票・品質検証: [review-finding](.agents/skills/review-finding/SKILL.md)。レビュースキル見直し・改修検証: [review-skill-audit](.agents/skills/review-skill-audit/SKILL.md)。役割別ワークフロー・PR作成・相談・handoff/復旧の共通入口: [repo-workflow](.agents/skills/repo-workflow/SKILL.md)。

内部レビュー改善: [設計書](docs/reviews/REVIEW_SKILL_PHASE1_DESIGN.md)、[詳細実装設計書](docs/reviews/REVIEW_SKILL_PHASE1_IMPLEMENTATION.md)、[Phase 2 設計書](docs/reviews/REVIEW_SKILL_PHASE2_DESIGN.md)。CLI 入口は `scripts/repo-review-helper`。作業領域（`.input/`）および内部レビュー記録は30日間保持され、整理は必ず `scripts/repo-review-helper cleanup-candidates` で出力された候補に限って実行する（ワイルドカード削除禁止、30日以内削除要求は人間へ委任、秘密混入時は即時個別削除例外 — 詳細は [Phase 2 設計書](docs/reviews/REVIEW_SKILL_PHASE2_DESIGN.md) および [repo-workflow](.agents/skills/repo-workflow/SKILL.md) 参照）。GitHub App 連携・レビュー運用規約: [運用規約方針](docs/reviews/GITHUB_APP_AND_REVIEW_POLICY.md)。CLI 入口は `scripts/review-sync.sh`。

## Herdr Orchestrator のレビュー

Herdr Orchestrator の設計書・実装・テスト仕様をレビューするときは、レビュー開始前に[エージェントレビューガイドライン](docs/herdr-agent-review-guidelines.md)を読み、その基準に従う。特に Herdr の公式仕様と独自設計を区別し、必要に応じて現行の公式資料・実機仕様を確認する。Herdr 公式ドキュメントを読むときは [https://herdr.dev/llms.txt](https://herdr.dev/llms.txt) から開始する。

## 設計書について

Herdr Orchestrator の設計書は、関連するテーマごとに複数のファイルに分けて管理する。全体目次は[設計書の目次](docs/herdr-orchestrator-design.md)から確認する。

### 設計書の構成

- [01-foundation.md](docs/herdr-orchestrator/01-foundation.md#sec-1): 目的、設計原則、コンポーネント（§1–3）
- [02-roles-and-task-contract.md](docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-4): 論理 role、routing identity、共通 task contract（§4–5）
- [03-task-registry.md](docs/herdr-orchestrator/03-task-registry.md#sec-6): canonical schema、4軸の状態遷移、Registry 操作、保持期間（§6）
- [04-handoff-and-requester.md](docs/herdr-orchestrator/04-handoff-and-requester.md#sec-7): sync / async handoff、requester の識別（§7–8）
- [05-completion-and-recovery.md](docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9): completion、通知、復旧（§9）
- [06-results-and-workflows.md](docs/herdr-orchestrator/06-results-and-workflows.md#sec-10): Result Contract、worker 解決、reviewer workflow（§10–12）
- [07-skill-and-implementation.md](docs/herdr-orchestrator/07-skill-and-implementation.md#sec-13): 実装境界、Skill 構成、承認、中央管理（§13–16）
- [08-adoption-and-verification.md](docs/herdr-orchestrator/08-adoption-and-verification.md#sec-17): 導入、動作確認、repository 移行（§17–19）
- [09-maintenance-and-roadmap.md](docs/herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20): Herdr のバージョン方針、更新、制約、今後の検証、将来構成（§20–24）
- [herdr-orchestrator-identity-uuid.md](docs/herdr-orchestrator-identity-uuid.md): Repository Identity UUID化 設計書（Issue #50）
- [herdr-orchestrator-task-list.md](docs/herdr-orchestrator-task-list.md): Task 一覧参照コマンド `task-list` 設計書（Issue #62）
- [herdr-orchestrator-cross-workspace.md](docs/herdr-orchestrator-cross-workspace.md): ワークスペース跨ぎ配送・グローバルロール・日本語ロール名・ペイン自動追従 設計書（Issue #74）
- [herdr-orchestrator-task-registry-cleanup.md](docs/herdr-orchestrator-task-registry-cleanup.md): Task Registry クリーンアップ機構 設計書（Issue #157）

### 文書の編集ルール

- 設計書の Markdown を作成・変更・修正したら、作業完了前に必ず `npm run format:md` と `npm run check:md` を通す（手順は [.agents/skills/design-doc-markdown-format/SKILL.md](.agents/skills/design-doc-markdown-format/SKILL.md)）。
- **個別設計書のステータス管理と正本**:
  - 各個別設計書（`docs/herdr-orchestrator-*.md`）のヘッダーにある `- **ステータス**:` 行をステータスの唯一の正本とする（`AGENTS.md` や目次でステータスを重複保持しない）。
  - ステータス値は次の 5 語彙に限定する: `設計中` / `設計承認` / `実装中` / `実装完了（PR #NN にてマージ済み）`（※ リリース後に `/ vX.Y.Z` を追記） / `廃止`。
  - 実装 PR を作成・マージする際、PR が `Closes #N` / `Fixes #N` を含む場合は、該当 Issue を対象とする設計書のステータスを `実装完了（PR #NN にてマージ済み）` へ更新することを必須とする（差分外波及チェック D-1）。リリースバージョン表記（`/ vX.Y.Z`）はリリース作業時に追記する。
- 既存参照との対応を保つため、章番号は維持する。章・節のアンカーには `sec-N` / `sec-N-M` 形式を使い、見出し変更時もアンカーを維持する。
- ファイル構成、章の配置、章タイトルを変更した場合は、設計書の目次も更新する。
- 章・節を参照するときは、章番号だけで済ませず、該当箇所への Markdown リンクを付ける。
- 各方針の正本は一箇所に保つ。共通 task schema と状態遷移は Task Registry の章を正本とし、workflow 側では複製せずリンクする。
- repository 固有のレビュー手順や成果物は、明示的な例として扱う場合を除き、共通 Orchestrator 設計に含めない。
- 章を追加した場合は、この一覧と設計書の目次をあわせて更新する。
