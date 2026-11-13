# Handoff 概要

正本: [§7–8](../../../docs/herdr-orchestrator/04-handoff-and-requester.md#sec-7)、[§6.11](../../../docs/herdr-orchestrator/03-task-registry.md#sec-6-11)（単体配置時は [design-links.md](design-links.md)）。

- sync: Registry `result.status` terminal で完了（`agent prompt --wait` だけに依存しない）。
- async: dispatch 確定後 return。completion は `result-submit` 連鎖または `resume-task`。
- 配送安全: [§11.4](../../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4)（最小 prompt + 受信側 Registry 検証）。

`herdr-orchestrator handoff` は public CLI で提供（段階 4+）。repository 側は [worker-receive.md](worker-receive.md) / [requester-receive.md](requester-receive.md) を Skill に組み込む。

## 委託前（Requester）の手順

1. **登録ロールの確認と柔軟マッチング**:
   - `handoff` 実行前に `herdr-orchestrator role show` を実行し、現在有効な登録ロール一覧を確認する。
   - ユーザーの指示（例: 「レビュー依頼をして」「調査依頼をして」）に対して、一覧の中から最も意味の合致する登録ロール名（例: `レビュワー`、`reviewer`、`調査担当`、`ログ調査`）を AI が判断し、その正確な登録名を `--to <role>` または `--worker-role <role>` に指定する。
   - **曖昧時の人間確認**: 指示語と完全一致する登録ロールがなく、かつ意味的に合致する候補が 0 件、または 2 件以上存在する場合（例: `調査` と `ログ調査`、`reviewer` と `レビュワー` が併存しどちらか特定できない場合）は、AI 自身の独断で委託してはならない。必ず `herdr-orchestrator role show` の登録ロール一覧を提示し、人間に宛先ロールの確認を仰ぐこと。
2. **セルフ作業の禁止**:
   - ユーザーから「依頼をして」「委託して」と指示されたタスクについて、依頼元（Requester）ペイン自身で作業（コードレビューや調査・ファイル編集など）を直接セルフ実行してはならない。必ず `handoff` を用いて委託すること。

## クロスワークスペース配送・スコープ重複・ペイン状態変更時の対応

正本: [herdr-orchestrator-cross-workspace.md](../../../docs/herdr-orchestrator-cross-workspace.md)

- **ローカルとグローバルの同名ロール重複（`worker_role_scope_ambiguous`）**:
  - `--scope` 未指定時にローカルとグローバルの両方に同名ロールが登録されている場合、CLI は自動選択せず `worker_role_scope_ambiguous` で停止する。
  - エージェントは出力 JSON の `local_candidate.display_summary` と `global_candidate.display_summary`（**ワークスペース番号・ワークスペース名・ロール表示名・エージェント種別**）を人間に提示し、どちらへ依頼するか確認した上で `--scope local` または `--scope global` を付けて再実行する。
- **旧形式 Role Binding の検出（`role_binding_outdated`）**:
  - `targets` 指紋を持たない旧形式（v0.3.1 以前）の Role Binding が検出された場合、プロンプト送信前に `role_binding_outdated` で停止する。人間に報告し、対象ペインで `herdr-orchestrator role set <role> --pane <pane_id> --label "<表示名>"` を再実行するよう案内する（依頼元ペインに誤登録しないよう注意）。
- **ペイン状態変更・閉鎖時の単一候補自動追従と確認停止**:
  - 同一ペインでのエージェント再起動や、ペイン閉鎖後の単一候補ペインへの自動追従が安全条件を満たす場合は、CLI が自動で追従・指紋更新して配送する。
  - 別フォルダへの `cd`、別エージェント種別への変更、または複数候補が存在して特定できない場合（`role_target_context_changed`, `role_target_pane_closed_multiple_candidates`, `role_target_pane_not_found`）は配送前に停止する。出力 JSON の `candidates`（ワークスペース名主体の表示）を人間に提示して確認を仰ぐこと。
- **プロジェクトカタログ指定による別プロジェクトへの委託（`--project <alias>`）**:
  - `--project <alias>` を指定することで、プロジェクトカタログに登録された相手プロジェクトのローカルロールへ委託できます（`--project` と `--scope` は排他指定）。
  - **フォールバック禁止**: 指定プロジェクトのローカルにロールが見つからない場合、グローバルロールや自スコープへのフォールバックは一切行わず、即座にエラー（`worker_role_not_bound`）でフェイルクローズ停止します。
  - **相手先 Role Binding 書き込み禁止（読み取り専用）**: 相手先プロジェクトの Role Binding に対する自動追従や指紋更新による書き込みは行いません。相手ペインが閉鎖またはコンテキスト変化している場合は、推測追従を行わずエラー（`worker_pane_closed_or_missing` または `worker_context_changed`）で安全停止します。

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

## Recovery Runbook（Worker 途中停止時の復旧手順）

正本: [herdr-orchestrator-task-cancellation.md §6](../../../docs/herdr-orchestrator-task-cancellation.md#sec-6)

`handoff --mode async` 後に完了通知が返らず、Worker の利用枠切れ・クラッシュ・ペイン閉鎖等で未完了のまま滞留している場合は、以下の判断フローで終端化する（Registry JSON の直接編集や `rm` は厳禁）：

1. **残存未完了タスクの確認**:
   `herdr-orchestrator task-list --status in_progress`（または `unknown`）および `herdr-orchestrator task-status <task_id>` で対象タスクの状態を確認する。
2. **Worker ペイン・worktree 生存確認**:
   `herdr pane list` 等で対象 Worker ペイン（`worker.pane_id`）および `context.worktree.path` が生存しているかを確認する。
3. **分岐 A: Worker ペインが生存（同一 cwd / 同一リポジトリ維持）**:
   同一ペインでエージェントが再起動・枠回復可能な場合は、その Worker ペインから `herdr-orchestrator result-submit <task_id> --result-status unavailable --summary "<中断理由>"` を提出させて正規に `failed` 終端化する。なお、`worker_context_unverified` 等で `result-submit` が拒否された場合はフォールバックとして分岐 B へ進む。
4. **分岐 B: Worker ペイン喪失・worktree 削除・別用途転用等で復帰不能**:
   `herdr-orchestrator cancel-task <task_id> --reason "<中断理由>"` を実行し、`task.status = cancelled`（および必要な場合は `completion.status = skipped`）へ安全に終端化する（30 日経過後に `cleanup-registry` の自動整理対象となる）。
