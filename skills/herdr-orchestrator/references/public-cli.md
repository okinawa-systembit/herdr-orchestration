# Public CLI（固定 surface）

正本: [§6.5](../../../docs/herdr-orchestrator/03-task-registry.md#sec-6-5)、[§14.2](../../../docs/herdr-orchestrator/07-skill-and-implementation.md#sec-14-2)

リポジトリ内入口:

```text
skills/herdr-orchestrator/scripts/herdr-orchestrator
```

| コマンド                                                  | 用途                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handoff`                                                 | sync / async 配送（`--mode`, `--worker-role`/`--to`, `--task-type`/`--type`, `--scope local\|global`, `--instruction`）                                                                                                                                                         |
| `task-status` / `task-result`                             | Registry 読取（`HERDR_ENV=1` 下では `recipient_verification` による受信者・ロール照合結果を付与）                                                                                                                                                                               |
| `task-list`                                               | Registry 一覧参照（読み取り専用。`--repository`, `--worktree`, `--type`, `--status`, `--limit`、[task-list 設計書](../../../docs/herdr-orchestrator-task-list.md)）                                                                                                             |
| `result-submit`                                           | worker 結果登録（`--result-ref` または `--summary` 単独提出可。async では completion 連鎖の best-effort）                                                                                                                                                                       |
| `resume-task` / `reset-completion` / `recover-completion` | completion 再試行・復旧                                                                                                                                                                                                                                                         |
| `doctor`                                                  | 環境 + stale dispatch 候補 + 旧形式 Role Binding（`outdated_role_bindings`）検出                                                                                                                                                                                                |
| `reconcile-dispatch`                                      | dispatch 復旧（§6.12）                                                                                                                                                                                                                                                          |
| `identity`                                                | リポジトリ識別子 UUID の管理（`init`, `show`、[herdr-orchestrator-identity-uuid.md](../../../docs/herdr-orchestrator-identity-uuid.md#5-cli-仕様)）                                                                                                                             |
| `role`                                                    | ローカル / グローバルの論理 role と `pane_id` の紐づけ管理（`set <role> [<pane_id>] [--pane <pane_id>] [--label <name>] [--global]`, `show [--global]`, `clear [<role>\|--all] [--global]`、[クロスワークスペース設計書](../../../docs/herdr-orchestrator-cross-workspace.md)） |
| `update`                                                  | 配布環境の最新化（Release Tarball 取得・展開、`--force`, `--version`、[distribution-install.md](distribution-install.md)）                                                                                                                                                      |

Registry JSON の直接編集は禁止。内部 writer は `scripts/task-registry.py`（Agent 非公開）。
※ `update`、`identity`、および `role show` / `role clear` コマンドはセッション外メンテナンス・人間による手動実行を考慮し、`HERDR_ENV=1` の環境制約を免除する（ライブペインの指紋を取得する `role set` や `handoff` を含むその他のコマンドはすべて `HERDR_ENV=1` 必須）。

## クラッシュ再起動後の状態確認手順

PC/WSL の再起動や異常終了後、残存タスクを確認する際は `task-list` を使用する（詳細は [task-list 設計書 §7.1](../../../docs/herdr-orchestrator-task-list.md#71-想定手順)）。

1. `herdr-orchestrator task-list --worktree "$(git rev-parse --show-toplevel)"` を実行。
2. `exit = 0` かつ `complete=true` で正常取得。`exit = 3` の場合は一部ファイルが破損（`anomalies` を人間に報告）。
3. 各タスクの `overview.warnings` を必ず確認。`inconsistent_state_combination` が付与されている場合は、`overview.state` が `closed` であっても正常完了とみなさず、状態不整合として人間に報告する。
4. `open` / `uncertain` や警告ありのタスクは `task-status <task-id>` で詳細を確認し、次の操作を判断する。
