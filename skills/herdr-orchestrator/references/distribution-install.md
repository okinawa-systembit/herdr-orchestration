# 配布インストール（方針・未実装）

開発時は [dev-local-install.md](dev-local-install.md) と `install/dev-local-install.sh` を使う。本書は **clone なしで入れる**配布の想定のみ（実装・ホスト URL はこれから）。

## 想定 UX

固定 URL のインストーラを **curl で取得してパイプ実行**する。

```bash
curl -fsSL 'https://<host>/herdr-orchestration/install.sh' | bash
```

オプション例（確定前）:

```bash
curl -fsSL '.../install.sh' | bash -s -- --dest "$HOME/.agents/skills/herdr-orchestration"
```

## 配布物が行うこと（案）

1. リリース tarball または git sparse checkout 相当の Skill ツリー（`SKILL.md` / `scripts/` / `references/`）を取得。
2. 配置先 default: **`~/.agents/skills/herdr-orchestration`**（社内: Cursor / Codex / Agy 共通）。
3. `scripts/herdr-orchestrator` を executable にする。
4. `install/link-cli.sh` と同様に **`~/.local/bin/herdr-orchestrator`** を install 先 CLI へ symlink（開発 install と同一）。
5. 公式 **herdr** Skill と名前がぶつからないこと（Skill `name:` / ディレクトリ **`herdr-orchestration`**、CLI は **`herdr-orchestrator`**）。

配布 README 併記案:

```bash
npx skills add <org>/herdr-orchestration --skill herdr-orchestration -g
```

## 開発用 `dev-local-install.sh` との関係

|        | 開発                                        | 配布                         |
| ------ | ------------------------------------------- | ---------------------------- |
| 入口   | リポジトリ内 `install/dev-local-install.sh` | 将来 `install.sh`（curl 先） |
| ソース | 手元 checkout                               | リリースアーティファクト     |
| 配置先 | 同上 `~/.agents/skills/herdr-orchestration` | 同上（方針）                 |

配布 `install.sh` は、中身のコピー処理を開発用スクリプトと揃える（または共通関数化）予定。

## curl \| bash を使うときの注意

- **HTTPS + 固定ドメイン**、可能ならチェックサムまたはバージョンタグ付き URL。
- パイプ前に `curl ... -o /tmp/install.sh` して中身を確認できる `--dry-run` / ドキュメント化。
- CI では `verify:orchestrator` 相当のゲートをリリース前に通す。

## レガシー

`install/setup.sh`（default `~/.cursor/skills/herdr-orchestrator`）は MVP 段階の Cursor 向けテンプレート。社内標準の `~/.agents/skills/herdr-orchestration` に寄せた後、配布 `install.sh` に統合または deprecated 扱いにする。
