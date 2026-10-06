# 導入・移行・検証記録

正本: [§17–19](../../../docs/herdr-orchestrator/08-adoption-and-verification.md#sec-17)

**開発時**は [dev-local-install.md](dev-local-install.md) と `install/dev-local-install.sh`（`~/.agents/skills/herdr-orchestration`）。**配布**（curl \| bash）は [distribution-install.md](distribution-install.md)。本書 §2 の `setup.sh` は Cursor レガシー path 用。

## 1. 前提（§17.1–17.2）

- `herdr --version` / `herdr --help` で利用中 Herdr を確認（自動更新しない）。
- 公式 **herdr** Skill を導入（`herdr --skill` 等、当時の公式手順に従う）。

## 2. 中央 Skill のインストール（§17.3）

社内（Cursor / Codex / Agy）: **`~/.agents/skills/herdr-orchestration`**（公式 **herdr** Skill と別名）。開発 clone から:

```bash
skills/herdr-orchestrator/install/dev-local-install.sh --dry-run
skills/herdr-orchestrator/install/dev-local-install.sh
```

レガシー（Cursor `~/.cursor/skills/herdr-orchestrator`）:

```bash
skills/herdr-orchestrator/install/setup.sh --dry-run
skills/herdr-orchestrator/install/setup.sh
```

clone なし配布: [distribution-install.md](distribution-install.md)。

個別 repository へ中央 Skill を **コピーしない**。repository 側は [.agents/skills/repo-workflow/](../../../.agents/skills/repo-workflow/SKILL.md) のように **workflow のみ**置く（パス名は repo ごとに短くてよい）。

## 3. Registry と CLI

- Task Registry: `${XDG_STATE_HOME:-~/.local/state}/herdr-orchestrator/tasks/`
- 日常操作: [public-cli.md](public-cli.md) の固定 `herdr-orchestrator` コマンド（`HERDR_ENV=1` pane から）。

## 4. Repository 移行（§19 Phase 1–5）

| Phase | 内容                                                                             |
| ----- | -------------------------------------------------------------------------------- |
| 1     | 中央 Skill 導入。既存 repo Skill（例: herdr-review / 簡易 inbox）は **並行維持** |
| 2     | 通常レビューを sync `handoff` へ                                                 |
| 3     | 通常レビュー callback 整理（async 一般化）                                       |
| 4     | repo Skill 内 Herdr CLI 手順の削除（公式 herdr Skill へ）                        |
| 5     | 各 repo で回帰確認後、中央方式へ切替                                             |

本リポジトリ: [.review-inbox/](../../../.review-inbox/README.md) + [repo-review](../../../.agents/skills/repo-review/SKILL.md)（sync handoff レビュー）。

## 5. §18 検証記録

自動テスト: リポジトリ root で `npm run test:orchestrator`（段階 0–8 の CLI/Registry ゲート）。導入前の一括確認: `npm run verify:orchestrator`（テスト + Markdown lint + installer dry-run）。

検証ログ用スニペット: `bash scripts/print-automated-verification-snippet.sh`

実機（Herdr 上の sync/async、§18.1–18.2）は [verification-log テンプレート](../../../docs/herdr-orchestrator-verification-log.md) に Herdr / Skill バージョンと結果を記録する。
