# herdr-orchestration（仮 README）

Herdr 上の **中央オーケストレーション**（Task Registry、role、handoff、`herdr-orchestrator` CLI）。  
設計・実装の正本は [docs/herdr-orchestrator-design.md](docs/herdr-orchestrator-design.md) / [実装 INDEX](docs/herdr-orchestrator-implementation-index.md)。

> **仮段階** — 本 README は導入のクイックメモ。詳細は [skills/herdr-orchestrator/references/](skills/herdr-orchestrator/references/) に順次移す。

## 前提

- [Herdr](https://herdr.dev) と公式 **herdr** Skill（pane / agent の意味論）
- Orchestrator CLI は **`HERDR_ENV=1` の pane 内**でのみ実行

## 開発時ローカルインストール

社内（Cursor / Codex / Agy）共通: Skill を **`~/.agents/skills/herdr-orchestration`** にコピー（公式 `herdr` と別名）。

```bash
git clone <this-repo-url> herdr-orchestration
cd herdr-orchestration
npm install
npm run test:orchestrator

npm run install:orchestrator-dev
# または
skills/herdr-orchestrator/install/dev-local-install.sh --dry-run
skills/herdr-orchestrator/install/dev-local-install.sh
```

| 項目          | 値                                                                                  |
| ------------- | ----------------------------------------------------------------------------------- |
| 編集の正本    | リポジトリ内 `skills/herdr-orchestrator/`                                           |
| Skill `name:` | `herdr-orchestration`                                                               |
| CLI           | **`herdr-orchestrator`** on PATH → `~/.agents/skills/herdr-orchestration/scripts/…` |
| Registry      | `~/.local/state/herdr-orchestrator/tasks/`（リポジトリ外）                          |

`install:orchestrator-dev` は Skill コピー + **`~/.local/bin/herdr-orchestrator` symlink**（配布 install も同じ予定）。  
Skill / CLI を直したら **再実行**する（repo 直 PATH は使わない）。

```bash
herdr-orchestrator --help
```

詳細: [skills/herdr-orchestrator/references/dev-local-install.md](skills/herdr-orchestrator/references/dev-local-install.md)

## 配布インストール（予定・未実装）

```bash
# 案（URL・install.sh は未確定）
curl -fsSL 'https://<host>/herdr-orchestration/install.sh' | bash

# 配置先 default 案
curl -fsSL '.../install.sh' | bash -s -- --dest "$HOME/.agents/skills/herdr-orchestration"
```

併記候補:

```bash
npx skills add <org>/herdr-orchestration --skill herdr-orchestration -g
```

配布 `install.sh` は開発用 `dev-local-install.sh` と同じコピー処理を共有する想定。  
方針のみ: [skills/herdr-orchestrator/references/distribution-install.md](skills/herdr-orchestrator/references/distribution-install.md)

レガシー（Cursor `~/.cursor/skills/herdr-orchestrator`）: `skills/herdr-orchestrator/install/setup.sh`

## Codex の承認（execpolicy）— README に書くか、インストール脚本か

### 結論（現時点）

| 方式                                                               | 推奨           | 理由                                                                                                                                                                       |
| ------------------------------------------------------------------ | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **README + スニペットファイル**（手動で `~/.codex/rules/` に追記） | **主**         | 設計 §15.6 — allowlist は **各 Agent 製品のユーザ設定**。他プロジェクトの rule と衝突しやすく、脚本が `default.rules` を勝手にマージするのは安全・可搬性の面で望ましくない |
| **インストール脚本が rule を自動追記**                             | **しない**     | 上書き・重複・パス差分のリスク。Codex 専用で Cursor CLI 設定とは別                                                                                                         |
| **脚本がインストール後に 1 行を表示**（`--print-codex-rule` 等）   | **任意の将来** | パスを `$HOME` 展開した **コピペ用**を出すだけなら許容（書き込みはしない）                                                                                                 |

Cursor / Agy は Codex と **別の permissions モデル**のため、中央インストーラで一括設定はしない。製品ごとの doc を [references/](skills/herdr-orchestrator/references/) に足す想定。

### Codex: `~/.codex/rules/default.rules` に追記

テンプレ: [codex-execpolicy-snippet.rules](skills/herdr-orchestrator/install/codex-execpolicy-snippet.rules)（**PATH 名 `herdr-orchestrator` が主**、絶対パス 1 行はエージェントがフルパスを使う場合の fallback）

検証:

```bash
codex execpolicy check --rules ~/.codex/rules/default.rules -- \
  herdr-orchestrator task-status task_example
```

**`/bin/bash -lc "…"`** 1 文字列で実行される場合は prefix が別 argv になり、上記 rule だけでは足りないことがある。可能なら **CLI をシェルラップせず直接実行**。どうしても `-lc` のときは TUI の allow が **全文一致**になりやすい（task 固定 rule が増える）。`pattern=["/bin/bash","-lc"]` のみ allow は非推奨（全 `-lc` が無承認になる）。

`handoff` / `reconcile-dispatch` / completion 復旧系は **allow しない**（明示承認のまま）。

## 検証

```bash
npm run verify:orchestrator
```

## 本リポジトリのレビュー

`.agents/skills/repo-review/` — sync `handoff`、`.review-inbox/`、短い `herdr-orchestrator` / `result-submit`（[AGENTS.md](AGENTS.md)）。

## 関連

- [AGENTS.md](AGENTS.md) — リポジトリ作業ガイド
- [skills/herdr-orchestrator/install/README.md](skills/herdr-orchestrator/install/README.md) — インストール入口
