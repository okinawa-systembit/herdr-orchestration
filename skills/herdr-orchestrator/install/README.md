# herdr-orchestrator インストール

仮の全体手順（開発・配布・Codex rule 方針）: [リポジトリ README（仮）](../../../README.md)

| 用途                                                         | 手順                                                                                  |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| **開発**（checkout を編集 → Agent に反映）                   | [dev-local-install.sh](dev-local-install.sh) → `~/.agents/skills/herdr-orchestration` |
| **配布**（curl \| bash、未実装）                             | [references/distribution-install.md](../references/distribution-install.md)           |
| **レガシー**（Cursor `~/.cursor/skills/herdr-orchestrator`） | [setup.sh](setup.sh)                                                                  |

```bash
# 開発（社内 Cursor / Codex / Agy 共通）— Skill コピー + ~/.local/bin/herdr-orchestrator
./dev-local-install.sh --dry-run
./dev-local-install.sh
herdr-orchestrator --help
```
