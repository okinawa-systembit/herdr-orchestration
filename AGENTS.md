# リポジトリ作業ガイド

## Herdr Orchestrator 実装

作業順序は [実装 INDEX](docs/herdr-orchestrator-implementation-index.md)。CLI 入口は `skills/herdr-orchestrator/scripts/herdr-orchestrator`。段階 0–8 の自動テスト: `npm run test:orchestrator`。**開発時ローカルインストール**: `npm run install:orchestrator-dev`（`~/.agents/skills/herdr-orchestration`）— [dev-local-install.md](skills/herdr-orchestrator/references/dev-local-install.md)。導入前確認: `npm run verify:orchestrator`。配布方針: [distribution-install.md](skills/herdr-orchestrator/references/distribution-install.md)。§18 実機記録: [docs/herdr-orchestrator-verification-log.md](docs/herdr-orchestrator-verification-log.md)。

## Herdr Orchestrator レビュー（本リポジトリ）

sync handoff + `.review-inbox/` + `herdr-orchestrator` CLI: [repo-review](.agents/skills/repo-review/SKILL.md)。汎用 handoff/復旧: [repo-workflow](.agents/skills/repo-workflow/SKILL.md)。

## Herdr Orchestrator のレビュー

Herdr Orchestrator の設計書・実装・テスト仕様をレビューするときは、レビュー開始前に[エージェントレビューガイドライン](docs/herdr-agent-review-guidelines.md)を読み、その基準に従う。特に Herdr の公式仕様と独自設計を区別し、必要に応じて現行の公式資料・実機仕様を確認する。

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

### 文書の編集ルール

- 設計書の Markdown を作成・変更・修正したら、作業完了前に必ず `npm run format:md` と `npm run check:md` を通す（手順は [.agents/skills/design-doc-markdown-format/SKILL.md](.agents/skills/design-doc-markdown-format/SKILL.md)）。
- 既存参照との対応を保つため、章番号は維持する。章・節のアンカーには `sec-N` / `sec-N-M` 形式を使い、見出し変更時もアンカーを維持する。
- ファイル構成、章の配置、章タイトルを変更した場合は、設計書の目次も更新する。
- 章・節を参照するときは、章番号だけで済ませず、該当箇所への Markdown リンクを付ける。
- 各方針の正本は一箇所に保つ。共通 task schema と状態遷移は Task Registry の章を正本とし、workflow 側では複製せずリンクする。
- repository 固有のレビュー手順や成果物は、明示的な例として扱う場合を除き、共通 Orchestrator 設計に含めない。
- 章を追加した場合は、この一覧と設計書の目次をあわせて更新する。
