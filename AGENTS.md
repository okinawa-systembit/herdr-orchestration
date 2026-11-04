# リポジトリ作業ガイド

## 基本方針（Core Principles）

本リポジトリおよびオーケストレーション基盤では、過剰な複雑化を防ぎ、長期的な保守性と安全性を確保するため、以下の原則と指針をすべての設計・実装・レビューで遵守する（[Issue #113](https://github.com/okinawa-systembit/herdr-orchestration/issues/113)）。

### 7 大設計原則

1. **シンプル is ベスト（KISS 原則）**:
   - 複雑な仕組み、過剰な状態管理、過剰なパーミッション縛り、必要以上の多段フォールバックを排除する。
   - 最も単純で、壊れにくく、コード量が最小限で済むアプローチを優先する。
2. **保守性を高める・人間中心の可観測性（Human-Centric Observability）**:
   - AI エージェント専用のブラックボックスな構造を作らない。
   - 人間の開発者がファイル一覧、ディレクトリ構成、ログ、コミット履歴、レビュー結果をエディタやターミナルで直感的に把握できるようにする。作業領域は `.input/` に一元化し、隠しディレクトリを乱立させない。
3. **車輪の再発明はしない（Don't Reinvent the Wheel）**:
   - `git`、`gh` CLI、Prettier、Unix 標準コマンド等の既存ツール・公式機能に素直に委譲・活用する。
   - 自前でパーサーや状態同期エンジンを作り込まず、既存の枯れたエコシステムに任せる。
4. **依存管理の境界分離（開発環境 vs 配布物）**:
   - **開発環境（本リポジトリ内）**: 開発効率・品質検証・整形の向上のため、有用なライブラリやツール（Prettier, markdownlint-cli2 等）を柔軟に導入・活用する。
   - **配布物（中央 Skill・配布 CLI）**: 一般環境への導入障壁・トラブルをゼロにするため、Python 標準ライブラリ（組み込みモジュール・関数）および OS 標準コマンドのみで完結させる（外部依存ゼロ・高い可搬性の保証）。
5. **秘密は使う 1 か所だけにインラインで渡す（Least Privilege for Secrets）**:
   - 認証トークンや秘密鍵の存在範囲を極小化する。全体に環境変数をばらまかないことで、各スクリプトでの過剰な防御コードを削り、シンプルさを維持する。
6. **1 つの事実は 1 か所だけに持たせ、機械検査とセットで定義する（Single Source of Truth with Automated Checks）**:
   - ステータスや設定の多重管理を禁止する。正本を 1 つに限定し、人間向けの一覧は自動生成ビューとして提供するか、機械検査（lint/check スクリプト）でズレを検知する。
7. **修正ラウンドでスコープを広げない（漸進的な改善・No Scope Creep in Fixes）**:
   - レビュー指摘の修正コミットには指摘の是正のみを入れ、新しい機能追加や大規模移行を混ぜない。

### 3 大実践指針

1. **（指針 8）機械検査とエージェント判断の境界（無理に機械化しない）**:
   - **機械で止めるもの**: 秘密漏洩、宛先誤り、改ざん等、取り返しのつかない致命的事故の防止。自分たちで決めた固定形式（メタデータ行、マーカー等）の整合性。
   - **エージェントに任せるもの**: 自由な文章の意味、妥当性、完成度、Markdown の見た目。
   - **判断ルール**: 規則に「ただし〜を除く」が 2 つ以上必要なら機械チェックを作らない（パーサー化の禁止）。境界値を理由に 2 回修正された機械チェックは機能自体を削るか見直す。
2. **（指針 9）テンプレート主導による記述と機械制御値の完全分離**:
   - **機械が使う制御値（役割、Round、判定 Status、宛先）**: スクリプトが引数や状態ファイルから決定し、外側のヘッダーとして付与する。本文に書かせて読み戻さない。
   - **本文（エージェントが書く文章）**: テンプレートに沿った自由な文章とし、スクリプトは本文の構文を一切解析しない。書く側も読む側も LLM であるため、多少の表記ゆれは読む側のエージェントが自然に吸収する。スクリプトによる本文パース・自動挿入・過剰バリデーションは行わない。
3. **（指針 10）黙って失敗しない**:
   - ベストエフォートの補助機能（Prettier 整形等）であっても、スキップ時は警告を標準エラー出力に出す（`>/dev/null 2>&1 || true` で痕跡を消さない）。
   - テストは引数変換だけでなく「実際の結果」を検証する。

## Herdr Orchestrator 実装

作業順序は [実装 INDEX](docs/herdr-orchestrator-implementation-index.md)。CLI 入口は `skills/herdr-orchestrator/scripts/herdr-orchestrator`。段階 0–8 の自動テスト: `npm run test:orchestrator`。**開発時ローカルインストール**: `npm run install:orchestrator-dev`（`~/.agents/skills/herdr-orchestration`）— [dev-local-install.md](skills/herdr-orchestrator/references/dev-local-install.md)。導入前確認: `npm run verify:orchestrator`。配布方針: [distribution-install.md](skills/herdr-orchestrator/references/distribution-install.md)。§18 実機記録: [docs/herdr-orchestrator-verification-log.md](docs/herdr-orchestrator-verification-log.md)。

## Herdr Orchestrator レビュー（本リポジトリ）

sync handoff + `repo-review-helper` CLI + `herdr-orchestrator` CLI: [repo-review](.agents/skills/repo-review/SKILL.md)。Finding起票・品質検証: [review-finding](.agents/skills/review-finding/SKILL.md)。汎用 handoff/復旧: [repo-workflow](.agents/skills/repo-workflow/SKILL.md)。

内部レビュー改善（Phase 1 実装中、Issue #61 / #64）: [設計書](docs/reviews/REVIEW_SKILL_PHASE1_DESIGN.md)、[詳細実装設計書](docs/reviews/REVIEW_SKILL_PHASE1_IMPLEMENTATION.md)。CLI 入口は `scripts/repo-review-helper`。GitHub App 連携・レビュー運用規約（Issue #85 / #88）: [運用規約方針](docs/reviews/GITHUB_APP_AND_REVIEW_POLICY.md)。CLI 入口は `scripts/review-sync.sh`。

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
