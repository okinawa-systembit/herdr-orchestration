# Handoff 概要

正本: [§7–8](../../../docs/herdr-orchestrator/04-handoff-and-requester.md#sec-7)、[§6.11](../../../docs/herdr-orchestrator/03-task-registry.md#sec-6-11)（単体配置時は [design-links.md](design-links.md)）。

- sync: Registry `result.status` terminal で完了（`agent prompt --wait` だけに依存しない）。
- async: dispatch 確定後 return。completion は `result-submit` 連鎖または `resume-task`。
- 配送安全: [§11.4](../../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4)（最小 prompt + 受信側 Registry 検証）。

`herdr-orchestrator handoff` は public CLI で提供（段階 4+）。repository 側は [worker-receive.md](worker-receive.md) / [requester-receive.md](requester-receive.md) を Skill に組み込む。

## Git 未初期化ディレクトリでのエラー対応

正本: [herdr-orchestrator-identity-uuid.md](../../../docs/herdr-orchestrator-identity-uuid.md#7-git-未初期化ディレクトリの取り扱い)

- **CLI 側の責務**:
  - 対象ディレクトリが Git 管理外であることを検出した場合、専用エラーコード `git_repository_not_initialized` を返して非ゼロ終了する（一時 UUID 発行や不完全なタスク登録は行わない）。
  - ※ 権限不足やリポジトリ破損など、未初期化以外の Git コマンド失敗は `git_repository_not_initialized` と区別して安全停止する。
- **Skill 側の責務**:
  - CLI から `git_repository_not_initialized` を受信した場合、タスク委託（handoff）を即座に中止する。
  - **エージェントが自律的・自動的に `git init` を実行することは禁止する**。
  - 人間にチャットで原因と対処方法を報告する（以下のフォーマットに準拠）：

```text
Herdrのタスク委託を開始できませんでした。

原因:
対象ディレクトリがGitリポジトリとして初期化されていません。

対処方法:
対象ディレクトリで次のコマンドを実行してください。

  git init

Git初期化後、タスク委託を再実行してください。
Gitの初期化は自動実行していません。
```
