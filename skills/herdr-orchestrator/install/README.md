# herdr-orchestrator インストール・初期設定

全体手順・利用方法: [リポジトリ README](../../../README.md)

## 1. インストール手順

| 用途                                                         | 手順                                                                                  |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| **開発**（checkout を編集 → Agent に反映）                   | [dev-local-install.sh](dev-local-install.sh) → `~/.agents/skills/herdr-orchestration` |
| **配布**（Release Tarball / curl \| bash / update）          | [references/distribution-install.md](../references/distribution-install.md)           |
| **レガシー**（Cursor `~/.cursor/skills/herdr-orchestrator`） | [setup.sh](setup.sh)                                                                  |

```bash
# 開発（Cursor / Codex / Agy 共通）— Skill コピー + ~/.local/bin/herdr-orchestrator
./dev-local-install.sh --dry-run
./dev-local-install.sh
herdr-orchestrator --help
```

---

## 2. Codex 承認設定（任意・設定例）

herdr-orchestration は複数の Agent（Codex、Cursor、Antigravity 等）での利用を想定しています。  
本節は、**Codex Agent を利用する場合のみの任意設定例（テンプレート）**です。Codex を利用しない場合は設定不要であり、Codex CLI のインストールも本ツールの必須要件ではありません。プロジェクト全体の CI やテスト要件にも含まれません。

Codex Agent が `herdr-orchestrator` を自律的に実行する際の承認プロンプトによる中断を防ぐため、`~/.codex/rules/default.rules` への承認ルールの追加設定例を提供しています。

設定用テンプレート: [codex-execpolicy-snippet.rules](codex-execpolicy-snippet.rules)

### 2.1 コマンド分類と許可方針

| コマンド                      | 分類       | 許可方針         | 理由・備考                                                                                                      |
| ----------------------------- | ---------- | ---------------- | --------------------------------------------------------------------------------------------------------------- |
| `doctor`                      | 診断       | **allow**        | 環境診断。副作用なし                                                                                            |
| `task-status`                 | 読み取り   | **allow**        | タスク状態の取得。副作用なし                                                                                    |
| `task-result`                 | 読み取り   | **allow**        | 提出結果の取得。副作用なし                                                                                      |
| `identity show`               | 読み取り   | **allow**        | Repository Identity の参照。副作用なし                                                                          |
| `identity init`               | 設定変更   | **allow しない** | `.git/config` の変更を伴うため無条件許可しない                                                                  |
| `role show`                   | 読み取り   | **allow**        | Role Binding の参照。副作用なし                                                                                 |
| `role set` / `role clear`     | 設定変更   | **allow しない** | ロールと `pane_id` の紐づけ変更および `herdr pane rename` を伴うため無条件許可しない                            |
| `result-submit`               | 書き込み   | **allow**        | Task Registry 更新・完了通知を伴うが、Worker の自動完了報告のために必須                                         |
| `handoff`                     | タスク登録 | **allow しない** | 新規タスク発行・Worker 起動・自動追従時の Role Binding 指紋更新・`herdr pane rename` を伴うため通常承認に委ねる |
| `resume-task`                 | 復旧・再開 | **allow しない** | Requester 再開を伴うため通常承認に委ねる                                                                        |
| `reset-completion`            | 状態変更   | **allow しない** | completion 状態の初期化を伴うため通常承認に委ねる                                                               |
| `recover-completion`          | 状態変更   | **allow しない** | completion 復旧処理を伴うため通常承認に委ねる                                                                   |
| `reconcile-dispatch`          | 状態変更   | **allow しない** | dispatch 整合性復旧を伴うため通常承認に委ねる                                                                   |
| `npm run test:orchestrator`   | 開発・検証 | **allow**        | リポジトリ開発者向け任意ルール                                                                                  |
| `npm run verify:orchestrator` | 開発・検証 | **allow**        | リポジトリ開発者向け任意ルール                                                                                  |

※「allow しない」コマンドは、Codex 側の通常承認プロンプトまたはサンドボックス設定に基づいて個別に判断されます。

### 2.2 テンプレートの適用方法

1. テンプレートの内容を `~/.codex/rules/default.rules` に追記します。
2. 絶対パスルール内の `GLOBAL_INSTALL_ABS` を実際の Skill インストール先ディレクトリに置換します。
   - 通常配布（標準）: `/home/<user>/.agents/skills/herdr-orchestration`
   - Cursor レガシー: `/home/<user>/.cursor/skills/herdr-orchestrator`

### 2.3 設定の検証方法（Codex 利用者向けの任意手順）

Codex CLI が利用可能な環境では、`codex execpolicy check` コマンドを使用して、ルールが正しく適用されているか任意で検証できます。

```bash
# 1. 診断・読み取り系（allow になることを確認）
codex execpolicy check --rules ~/.codex/rules/default.rules -- herdr-orchestrator doctor
codex execpolicy check --rules ~/.codex/rules/default.rules -- herdr-orchestrator identity show

# 2. 設定変更系（allow に一致しないことを確認）
codex execpolicy check --rules ~/.codex/rules/default.rules -- herdr-orchestrator identity init
```
