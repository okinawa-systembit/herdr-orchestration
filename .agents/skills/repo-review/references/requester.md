# レビュー依頼側（Requester）手順

<a id="requester-procedure"></a>

## 1. 依頼前の準備・コミット作成（C-15）

- **作業ツリーの確認**:
  - `git status` で変更内容を確認する。
  - **ローカルコミットの作成（原則必須）**: レビュー記録の `target_commit` SHA を一意に追跡可能とするため、ローカルレビュー依頼前に必ずコミット（作業中であれば WIP コミット可）を作成する。未コミット差分でのレビュー依頼は例外的な場面に限定する。
- **テストの実行**:
  - `npm run test:orchestrator`（必要に応じて `npm run format:md && npm run check:md`）を実行し、自己確認を行う。

## 2. 依頼ファイルの作成

`scripts/repo-review-helper` を使用して `.input/inbox/` に原子的作成する：

```bash
# 状態確認（必要なら初期化）
scripts/repo-review-helper init

# 通常レビュー依頼（STANDARD）
scripts/repo-review-helper request-create --tier STANDARD --summary "最新の変更内容についてのレビューを依頼します。"

# ワイドレビュー（総点検）依頼
scripts/repo-review-helper request-create --tier WIDE --summary "リポジトリ全体の総点検（ワイドレビュー）を実施します。"
```

※ 出力された `.input/inbox/review-request.<token>.md` を開き、重点確認事項や再レビュー参照先を必要に応じて追記する。

## 3. レビュワーへの handoff（sync handoff）

同一 worktree・requester pane から `herdr-orchestrator handoff` を実行する（日本語ロール名 `レビュワー` を指定）：

```bash
herdr-orchestrator handoff \
  --mode sync \
  --to レビュワー \
  --task-type review \
  --instruction "Read .input/inbox/review-request.<token>.md and review per repo-review SKILL. Save record via repo-review-helper record-save. DO NOT close or modify any GitHub Finding issue."
```

※ **GitHub Finding Issue へのコメント投稿を明示的に依頼する場合（オプトイン）**:

```bash
herdr-orchestrator handoff \
  --mode sync \
  --to レビュワー \
  --task-type review \
  --instruction "Read .input/inbox/review-request.<token>.md and review per repo-review SKILL. Save record via repo-review-helper record-save. Post review result as comment to Finding Issue #<no> after verifying target SHA. DO NOT close or modify any GitHub issue."
```

## 4. 結果確認と人間への報告

sync 完了後、結果を確認する：

```bash
herdr-orchestrator task-result <task.id>
```

- `result.status`（`succeeded`）および `result.ref`（`docs/reviews/internal/<instance-id>/RVNNN.md`）を確認する。
- レビュー結果（判定・指摘事項）を読み込み、[template.md#human-report](template.md#human-report) 形式で人間へチャット報告する。
