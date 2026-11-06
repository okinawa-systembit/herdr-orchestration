# GitHub PR 連携レビュー手順

<a id="github-pr-review"></a>

## 1. 前提と適用範囲

- **適用範囲**: 本手順は、通常 PR などの「GitHub 管理対象レビュー」として明示的に依頼された場合にのみ適用する。通常の Herdr 内部レビュー（エージェント間 handoff）では本手順は不要であり、`repo-review-helper` によるローカル記録と `result-submit` だけで完結する（不要な GitHub 投稿は行わない）。
- **共通基準**: レビュー品質基準（2 段階レビュー、指摘 3 分類、重大度 P0〜P3、反証確認、未実施検証の明示）は Herdr 連携時と完全に同一の基準（[criteria.md](criteria.md)）を適用する。

---

## 2. レビュワーによる PR レビュー手順

### 2.1 差分とメタデータの確認・ドラフト取り込み（--pr 明示指定による main 維持）

`scripts/review-sync.sh` は `--pr <PR番号>` オプションをサポートしている。同一 worktree 運用（通常時は `main`）においてレビュワーが PR レビュー・最終判定コメントを行う際は、対象 PR のブランチへ switch する必要はなく、**`main` ブランチに留まったまま `--pr <PR番号>` を指定**してドラフトを取り込む：

```bash
bash scripts/review-sync.sh pull reviewer --pr <対象PR番号>
```

差分と CI チェックの確認：

```bash
gh pr view <対象PR番号> --json number,title,baseRefName,headRefOid,commits
gh pr diff <対象PR番号>
gh pr checks <対象PR番号>
```

ローカルでテスト実行やファイル内容の検証が必要な場合は、ブランチ switch を行わず、スクラッチ領域へ展開して検証する（例: `git archive origin/<branch> | tar -x -C <一時ディレクトリ>`。repo-workflow §1 の作業ツリー不変ルールに準拠）。

### 2.2 2段階レビューの実施

- `repo-review` 基準に沿って、第 1 段階（仕様・設計整合性）および第 2 段階（批判的不具合探索）を実施。
- `.input/review/reviewer-draft.md` を編集し、指摘事項（P0〜P3）、総合判定（GO / 要修正 / NO-GO）、**内部レビュー（RV）の経緯要約（3〜5 行。RV ファイル自体は Git 追跡外のため人間が PR だけで経緯を追えるようにする）**、および未実施検証を記載。

### 2.3 レビュー結果の投稿（main に留まったまま実行）

レビュー投稿は `scripts/review-sync.sh comment reviewer --pr <対象PR番号>` を使用する（判定 Status は `--status Approved` または `--status "Changes requested"` を CLI 引数で明示的に指定する）。レビュワーは `main` に留まったまま直接 PR 番号を指定して投稿できるため、ブランチ切り替え（`git switch`）は不要である：

```bash
# 判定 Status と対象 PR 番号を CLI オプションで指定して投稿
bash scripts/review-sync.sh comment reviewer --pr <対象PR番号> --status Approved
# または
bash scripts/review-sync.sh comment reviewer --pr <対象PR番号> --status "Changes requested"
```

- 原則として `--comment` でレビュー結果を投稿する（Blocking 指摘時も `--request-changes` は使用せず、`- Status: Changes requested` を含む `--comment` で表明する）。

### 2.4 改ざん検知と安全制約

- **`scripts/review-sync.sh` や `scripts/lib/` 変更時の改ざん検知対応**:
  - ※ レビュワーが `main` に留まったまま `--pr` オプションで実行する場合は作業ツリーに差分が生じないため、通常この例外対応は不要である。PR ブランチ上で直接テスト実行・投稿を行う場合のみ以下が該当する：
  - PR が `scripts/lib/*.md`（ドキュメントのみ）を変更している場合は、`git diff --quiet origin/main -- scripts/review-sync.sh scripts/lib/*.py`（実行ファイル）および未追跡ファイルがないことを確認した上で、`ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer ...` で投稿する（投稿本文にもその理由を明記する）。
  - 実行コード（`*.py` や `review-sync.sh`）自体を変更している PR のブランチでは `review-sync.sh` を実行せず、内部レビュー（RV）記録を正本として人間に報告する。
- **安全制約（承認とマージの分離）**:
  - **AI エージェントの禁止事項**: GitHub App (Bot) 認証下であっても、AI エージェントによる GitHub 公式の承認（`gh pr review --approve`）、変更要求（`--request-changes`）、およびマージ（`gh pr merge`）の実行は**厳格に禁止**する。レビューの合意形成は、常に `--comment` 投稿本文の先頭メタデータ（`- Status: Approved` または `- Status: Changes requested`）にて行う。
  - **自 PR 制限回避と人間の役割**: エージェントが Bot 主体として PR を作成・操作することにより、開発者本人の個人アカウントとの同一視（自 PR 制限: 422 エラー）が回避される。GitHub 公式の Approve（承認ボタンの押下）および PR のマージ操作は、エージェントの合意表明を確認した上で**人間の開発者のみが担当**する（詳細は [docs/reviews/GITHUB_APP_AND_REVIEW_POLICY.md](../../../../docs/reviews/GITHUB_APP_AND_REVIEW_POLICY.md) 参照）。

---

## 3. レビューイによる PR 対応ワークフロー

1. **ドラフト取り込み**: `bash scripts/review-sync.sh pull reviewee` で最新レビューを取り込む。
2. **事前評価（即時修正禁止）**: 各指摘を評価し Decision（`accept` / `reject` / `defer`）を策定する（[reviewee.md](reviewee.md) 参照）。
3. **人間チャット提示**: 人間へトリアージ結果を提示し合意を得る。
4. **修正実施**: 合意された `accept` の項目のみ修正し、テストを実行。
5. **回答投稿と再レビュー依頼**:
   `.input/review/reviewee-draft.md` を完成させ、`bash scripts/review-sync.sh check reviewee` で検証後、`bash scripts/review-sync.sh comment reviewee` で投稿し、レビュワーへ再レビューを依頼する。
