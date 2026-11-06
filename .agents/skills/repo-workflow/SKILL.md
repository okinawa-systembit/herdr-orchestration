---
name: repo-workflow
description: >-
  Repository-side Herdr Orchestrator workflow and common entry point for all
  agent tasks in this repo: role workflows for 設計担当 (design / 設計依頼 / 設計書)
  and 実装担当 (implementation / 実装依頼), Bot PR creation (review-sync.sh pr-create /
  PR作成), inter-agent discussion (相談), sync/async handoff, worker Registry
  verification (§11.4), result-submit, task-result readback, GitHub write rules,
  and Issue vs PR guidelines.
---

# Herdr Orchestrator — repository workflow（本リポジトリ共通入口）

中央 Skill: [skills/herdr-orchestrator/SKILL.md](../../../skills/herdr-orchestrator/SKILL.md)

本スキルは、このリポジトリで各ロール（`メイン`、`設計担当`、`実装担当`、`レビュワー`）がタスクを実行する際の**共通入口**である。個別の専門手順は [repo-review](../repo-review/SKILL.md)、[review-finding](../review-finding/SKILL.md)、[review-skill-audit](../review-skill-audit/SKILL.md)、[design-doc-markdown-format](../design-doc-markdown-format/SKILL.md) へリンクする。

---

## 1. 前提（環境と同一 worktree 直列運用ルール）

- `HERDR_ENV=1` の Agent pane からのみ Orchestrator CLI を実行する。
- Herdr 操作の意味論は **herdr** 公式 Skill に従う（CLI 説明をここに複製しない）。
- Registry 更新は **herdr-orchestrator** public CLI のみ。
- 実行前に中央 Skill を **`~/.agents/skills/herdr-orchestration`** へ install 済みであること（開発: `npm run install:orchestrator-dev`）。CLI は **`herdr-orchestrator`**（`~/.local/bin` → install 先 scripts）。

```bash
herdr-orchestrator --help
```

- **ファイル作成・編集の原則**: ファイルの作成・更新は各エージェント環境の標準的なファイル操作機能を利用し、ヒアドキュメント等に依存しない（正本: [基本方針: 原則 1](../../../docs/core-principles.md#principle-1)）。
- **同一 worktree 直列運用（案 B）**:
  - 本リポジトリでは、`result-submit --result-ref`（Requester worktree 基準での成果物存在検査）および `scripts/repo-review-helper`（単一 worktree 固定状態）との整合性を保つため、全ロールが同一の Git worktree を共有する。
  - **ブランチを切り替える作業は同時に 1 つだけ**とし、`メイン` が直列にスケジュール管理する。
    - **ブランチを切り替える作業（直列）**: `設計担当` の設計ブランチ作業、`実装担当` の実装ブランチ作業、および `レビュワー` が PR 最終判定コメントを投稿する際の一時的ブランチ switch（[repo-review §A](../repo-review/SKILL.md)）。
    - **作業ツリーを変更しない作業（並行可）**: `git archive` 等でスクラッチ展開して検証するローカルレビュー（RV）、およびエージェント間相談（[#discussion](#discussion)）。
  - **返却時の clean & `main` 復帰義務**: 各ロールは、ブランチを切り替える作業を終えたら、`result-submit`（または完了報告）を実行する直前に必ず **`git switch main`** を実行し、`git status` が空（clean）であることを確認してからタスクを返却する（未コミット差分のままレビューを依頼する特例時を除く）。

---

## 2. 共通手順（全ロール共通）

<a id="worker-receive"></a>

### 2.1 タスク受信確認（Worker: §11.4）

[worker-receive.md](../../../skills/herdr-orchestrator/references/worker-receive.md) に従う（prompt 単体を信頼せず Registry を正本とする）。

1. `herdr-orchestrator task-status <task.id>` を実行する。
2. `recipient_verification.verified == true` かつ `recipient_verification.caller_role_in_task == "worker"`、`dispatch.status`（`sent` / `uncertain`）、および `worker.cwd` 一致を確認してから作業を開始する（不一致時は作業せず人間に報告して停止）。

### 2.2 ブランチの作成（毎回 `main` から新規作成）

ブランチを伴う作業を開始する際は、`git status` が空（clean）であることを確認した上で、**毎回必ず `main` から新しいブランチを作成する（PR がマージされた古いブランチの再利用・追加 push は厳禁）**：

```bash
git switch main && git pull --ff-only
git switch -c <新しいブランチ名>
```

- ※ `git pull` が失敗した場合（SSH 認証エラー等を含む）は、先に進まず人間に報告する。
- ブランチ命名例:
  - 設計タスク: `issue-<N>-<topic>-design`
  - 実装タスク: `issue-<N>-<topic>-impl`
  - 文書・単発タスク: `issue-<N>-<topic>`

<a id="pr-create"></a>

### 2.3 Pull Request の作成（正本: Bot アカウント必須）

エージェントが PR を作成する際は、設計 PR・実装 PR を問わず例外なく **`bash scripts/review-sync.sh pr-create`**（GitHub App Bot 認証ラッパー）を使用する。**個人の `gh pr create` を直接実行することは厳禁**とする。

- **理由**: PR 作成者を `<app-slug>[bot]` とすることで個人アカウントの自 PR 制限（GitHub API 422 エラー）を回避し、人間の開発者が自アカウントで公式 Approve を行えるようにするため（詳細は [GitHub App 運用規約 §4.1](../../../docs/reviews/GITHUB_APP_AND_REVIEW_POLICY.md) 参照）。
- **実行手順（必ず作業ブランチ上で push した直後、`git switch main` に戻る前に実行する）**:
  - PR 本文は、各エージェントの標準的なファイル操作機能で一時ファイル（例: `.input/tmp/pr-body.md`）に用意して `--body-file` で渡す（シェルのクォートや改行・バッククォート解釈の事故を防ぐため推奨。短い本文であれば `--body "..."` でもよい）：

```bash
git push -u origin <作業ブランチ名>

bash scripts/review-sync.sh pr-create \
  --title "<type>(<scope>): <変更要約>" \
  --body-file .input/tmp/pr-body.md
rm -f .input/tmp/pr-body.md
```

- ※ `git push` が失敗した場合（SSH 認証エラー等を含む）は、先に進まず人間に報告する。
- PR 作成後、`gh pr checks` で CI の通過を確認し、ローカルレビュー（`repo-review`）で GO を得たコミット SHA と PR の HEAD SHA が一致していることを確認してから、`git switch main` で作業ツリーを `main` に戻す。
  - ※ **GO 後の追加コミットの例外（D-1 PR 番号反映）**: ローカルレビュー GO 後に許容される追加コミットは、D-1 における設計書ヘッダーへの PR 番号反映（ステータス行の `PR #NN` 更新）のみとし、その差分は PR 最終判定コメント時にレビュワーへ伝える（それ以外の変更が入った場合は再度ローカルレビューを受ける）。

### 2.4 完了報告（Worker: `result-submit`）と結果確認（Requester: `task-result`）

- **Worker（`result-submit`）**:
  - `git switch main` 済みかつ `git status` が clean であることを確認した上で結果を提出する：
    ```bash
    herdr-orchestrator result-submit <task.id> \
      --result-status succeeded \
      [--result-ref <リポジトリ内相対パス>] \
      --summary "<200字以内の要約（PR番号・ローカルレビューRV番号等）>"
    ```
  - `--result-ref` には URL を指定できないため、リポジトリ内の相対パス（実装依頼書下書きや相談回答ファイル等）を指定するか、該当ファイルがない場合は `--summary` のみで提出する。
- **Requester（completion 後: §11.4 / §9.12）**:
  - [requester-receive.md](../../../skills/herdr-orchestrator/references/requester-receive.md) に従い、`herdr-orchestrator task-status <task.id>` / `task-result <task.id>` で result が terminal（`succeeded` / `failed`）になったことを確認してから次作業を開始する。

---

<a id="requester-main"></a>

## 3. Requester（メイン）: タスクの統括・依頼

1. **依頼前の作業ツリー確認**:
   - ブランチを使うタスク（設計・実装）を依頼する前に、`git status` が空であり現在のブランチが `main` であること、および他のロールがブランチ作業中でないことを確認する。
2. **handoff モードとロール確認**:
   - 実行前に `herdr-orchestrator role show` で登録ロール名を確認し、`--to <role>`（または `--worker-role <role>`）に指定する。
   - **設計・実装の依頼**: 所要時間が既定の sync 待機時間（1800 秒 / 30 分）を超えうるため、`--mode async` を基本とする。
   - **ローカルレビュー（[repo-review](../repo-review/SKILL.md)、レビュースキル自体の改修検証時は [review-skill-audit](../review-skill-audit/SKILL.md)）・相談（[#discussion](#discussion)）**: `--mode sync` を使用する。
   - ※ CLI が `git_repository_not_initialized` を返した場合は、自動で `git init` せず [handoff.md](../../../skills/herdr-orchestrator/references/handoff.md) のテンプレートに従いチャットで人間に報告する。
3. **設計から実装への引き継ぎ（ブランチ積み重ね防止）と PR 最終判定依頼**:
   - PR（設計 PR または実装 PR）が作成された後、PR 上に最終判定コメントを記録する場合は、他のロールがブランチ作業中でない（作業ツリーが `main` で clean である）ことを確認してから `レビュワー` に最終判定コメント投稿を依頼する。
   - `設計担当` から返却された実装依頼書の下書き（`.input/inbox/implementation-request-issue-<N>.md`）を確認する。
   - **原則として、設計 PR が人間によって `main` にマージされたことを確認してから**、`実装担当` へ `handoff` する（未マージの設計ブランチの上に実装ブランチを積み重ねない）。特例でマージ前に並行着手させる場合は、依頼書に「設計ブランチから切り、設計 PR マージ後に `main` へ rebase すること」を明記する。
   - 実装 PR がマージされた後、用済みとなった実装依頼書ファイルを削除して整理する。
4. **相談結果の正本化**:
   - エージェント間相談（[#discussion](#discussion)）で合意・決定した内容は、必ず GitHub Issue または設計書へ転記して正本化し、用済みの相談・回答ファイルは削除する。

---

<a id="worker-designer"></a>

## 4. Worker（設計担当）: 設計タスクを受けたとき

- **責務範囲**: Issue 要件の整理、設計書執筆・改定、CLI 仕様（`public-cli.md`）、テスト計画（`CW-xx` 等の ID と期待値）の策定、PR 前ローカル設計レビュー対応、Bot での設計 PR 作成、および実装依頼書の下書き作成までを担当する。**設計担当から実装担当へ直接 `handoff` は行わず、メインへ結果を返す**。

### 手順

1. **受信確認とブランチ作成**:
   - 共通手順（2.1・2.2）に従い、`main` から `issue-<N>-<topic>-design` を新規作成する。
2. **設計書の作成・改定とステータス管理**:
   - [AGENTS.md](../../../AGENTS.md) の「文書の編集ルール」（章番号・`sec-N` アンカー維持、目次更新、単一正本）に従う。
   - **新規の個別設計書を作る場合**: ヘッダーの `- **ステータス**:` を `設計中` で作成し、ローカル設計レビューで GO が出たら `設計承認` に更新する（※ `AGENTS.md` や全体目次の一覧にはステータス語を記載しない）。
   - **既に `実装完了` の設計書に未実装の節・仕様を追補する場合**: ファイル先頭の `- **ステータス**: 実装完了（...）` は変更せず、追補した節の冒頭に `> [!NOTE]` で「本節（§X.Y）および CW-xx は **設計承認済み・未実装**（実装 Issue #M にて実装予定）」の注記を置く（実装 PR で解除させる）。
   - [design-doc-markdown-format](../design-doc-markdown-format/SKILL.md) に従い、編集後に必ず以下を実行してエラー 0 件を確認し、**ローカルコミット（未 push）**を作成する：
     ```bash
     npm run format:md && npm run check:md
     ```
3. **PR 作成前のローカル設計レビュー依頼（必須）**:
   - [repo-review](../repo-review/SKILL.md) の Requester 手順に従い、PR を作成する前にローカルレビュー（RV）を依頼して **GO（Blocking 0 件）** を獲得する（`request-create` の標準出力に表示された `.input/inbox/review-request.<token>.md` のパスを指定する。※ レビュースキルや関連レビュー文書自体を見直し・改修する場合は、`repo-review` ではなく [review-skill-audit](../review-skill-audit/SKILL.md) モード B を使用する）：
     ```bash
     scripts/repo-review-helper request-create --tier STANDARD --summary "Issue #<N> の設計書レビューを依頼します。"

     herdr-orchestrator handoff \
       --mode sync \
       --to レビュワー \
       --task-type review \
       --instruction "Read .input/inbox/review-request.<token>.md and review per repo-review SKILL. Save record via repo-review-helper record-save. DO NOT close or modify any GitHub issue."
     ```
   - 要件・スコープを変えない指摘（`accept`）は修正コミットを作成して再レビューを回し、スコープ変更やトレードオフ判断を伴う指摘が出た場合は一旦 `result-submit` でメインへ判断を仰ぐ。
4. **設計 PR の作成（Bot）**:
   - レビュー GO 後、ステータス（`設計承認` 等）の更新があれば反映して `npm run format:md && npm run check:md` を通し、共通手順（[2.3](#pr-create)）に従って `git push` と `bash scripts/review-sync.sh pr-create` を実行し、`gh pr checks` を確認する。
5. **実装依頼書の下書き作成（後続の実装 Issue がある場合）**:
   - `.input/inbox/implementation-request-issue-<実装Issue番号>.md` を作成する（機微情報を含まない一時ファイルのため過剰なパーミッション強制は行わない）。仕様や汎用手順は書き写さず、以下の項目をリンク中心で記載する：
     - **冒頭注記**: 「本依頼書は補助資料であり、仕様・範囲の正本は GitHub Issue および設計書である（食い違いがある場合は Issue・設計書を優先する）」。
     - **対象 Issue / 設計正本**: `Closes #<実装Issue>` / `Refs #<親Issue>`、前提設計 PR `#<設計PR>`（ブランチ名・承認済み SHA）、および対象設計書の節アンカー（`#sec-X-Y`）へのリンク。
     - **作業の始め方と範囲**: 「設計 PR `#<設計PR>` が `main` にマージされた後、`main` から `issue-<N>-<topic>-impl` を新規作成すること」、およびやること・やらないこと（範囲外 Issue 番号）。
     - **完了条件と差分外波及チェック（D-1〜D-3）の具体指示**: 合格すべきテスト ID（`CW-xx` 等）・コマンド（`npm run test:orchestrator`, `npm run verify:orchestrator`）、および設計書側の更新箇所（例: 「D-1: 設計書ヘッダーを `実装完了（PR #NN にてマージ済み）` に更新」「§X.Y の『未実装』注記を削除」「D-2: `public-cli.md` と `--help` の一致確認」）。
     - **手順と許可範囲（必須）**: 「手順は `repo-workflow` の[Worker（実装担当）](#worker-implementer)に従うこと。ローカルレビュー GO 後の **`git commit`、`git push`、および `bash scripts/review-sync.sh pr-create`（Bot）による PR 作成までを許可**する。**PR のマージおよび Issue のクローズは厳禁**（人間が実施）」。
6. **作業ツリーの clean 化・`main` 復帰と完了報告**:
   - `git switch main` を実行し、`git status` が clean であることを確認した上で、共通手順（2.4）に従い `result-submit`（`--result-ref` に実装依頼書下書きパス、`--summary` に設計 PR 番号と RV 番号）を実行する。

---

<a id="worker-implementer"></a>

## 5. Worker（実装担当）: 実装タスクを受けたとき

### 手順

1. **受信確認と依頼書・設計書の精読**:
   - 共通手順（2.1）で受信確認し、指定された実装依頼書（`.input/inbox/implementation-request-issue-<N>.md`）およびリンク先の設計書正本を精読する。
2. **ブランチ作成**:
   - 共通手順（2.2）に従い、`main`（特例指示がある場合は指定ブランチ）から `issue-<N>-<topic>-impl` を新規作成する。
3. **実装・文書更新・テスト検証**:
   - コードおよび単体テスト（設計書指定のテスト ID）を実装する。
   - 依頼書で指定された **差分外波及チェック（D-1〜D-3）** の更新（設計書ヘッダーの `実装完了（PR #NN にてマージ済み）` 更新、節冒頭の「未実装」注記の解除、`public-cli.md` / `--help` 整合など）を行う。
   - `npm run test:orchestrator`（必要に応じ `npm run verify:orchestrator`）、および Markdown を変更した場合は `npm run format:md && npm run check:md` を実行し、すべてパスした状態で**ローカルコミット（未 push）**を作成する。
4. **PR 作成前のローカル実装レビュー依頼（必須）**:
   - [repo-review](../repo-review/SKILL.md) の Requester 手順（レビュースキルや関連レビュー文書自体の改修時は [review-skill-audit](../review-skill-audit/SKILL.md) モード B）に従い、`scripts/repo-review-helper request-create` で依頼書を作成してレビュワーへ `sync handoff` し、**GO（Blocking 0 件）** を獲得する（指摘修正時は修正コミットを作成し再レビューを受ける）。
5. **実装 PR の作成（Bot）**:
   - レビュー GO 後（依頼書で push・PR 作成が許可されている場合）、共通手順（[2.3](#pr-create)）に従って `git push -u origin <作業ブランチ>` と **`bash scripts/review-sync.sh pr-create`**（**個人の `gh pr create` 直接実行は厳禁**）を実行する。
   - D-1 で設計書ヘッダーに実際の PR 番号（`PR #NN`）を反映する必要がある場合は、PR 作成直後に番号を更新して `npm run format:md && npm run check:md` を通し、追加コミット＆ push する。
   - `gh pr checks` で CI の通過を確認する。
6. **作業ツリーの clean 化・`main` 復帰と完了報告**:
   - `git switch main` を実行し、`git status` が clean であることを確認した上で、共通手順（2.4）に従い `result-submit`（`--summary` に実装 PR 番号、テスト結果、内部レビュー RV 番号を記載）を実行する。

---

<a id="discussion"></a>

## 6. エージェント間相談（Discussion）手順

設計方針や運用ルールの整理など、レビュー判定（GO / NO-GO）を伴わないロール間の意見伺い・協議は以下の手順で行う（専用 CLI は設けず、内部レビュー RV の連番にも含めない）：

1. **相談ファイルの作成（Requester）**:
   - `.input/inbox/discussion-<topic>.md` を作成する（機微情報を含まない一時ファイルのため過剰なパーミッション強制は行わない。`<topic>` は英小文字とハイフン、例: `role-binding-cleanup`）。
2. **相談の送信（Requester）**:
   ```bash
   herdr-orchestrator handoff \
     --mode sync \
     --to <相談先ロール> \
     --task-type research \
     --instruction "Read .input/inbox/discussion-<topic>.md carefully. Write your reply to .input/inbox/discussion-<topic>-response.md and submit via result-submit. DO NOT write to GitHub, commit, or push."
   ```
3. **回答の作成と提出（Worker）**:
   - `.input/inbox/discussion-<topic>-response.md` を作成する（過剰なパーミッション強制は不要）。
   - **回答の冒頭に「結論（先に要点）」を置き**、コードやコマンドで確認した**事実**と**推論・提案**を分けて記述する。
   - `herdr-orchestrator result-submit <task.id> --result-status succeeded --result-ref .input/inbox/discussion-<topic>-response.md --summary "<200字以内の要約>"` で提出する（GitHub への投稿やコミット・push は行わない）。
4. **正本化と片付け（Requester）**:
   - **相談ファイルは正本ではない**（Git 追跡外）。相談で決まったことは、依頼元（メイン）が必ず **GitHub Issue または設計書へ転記して正本化**する（基本方針: 原則 6）。
   - 正本化が完了したら、依頼元（または作成者）が相談ファイルと回答ファイルを削除してよい（ワイドレビュー A-7 で残存有無を点検）。

---

<a id="github-write-matrix"></a>

## 7. GitHub 書き込み表（ロール別手段マトリクス）

本リポジトリでは、GitHub 公式のルールセット設定（**PR 必須、人間の承認 1 件必須 `required_approving_review_count: 1`、直列履歴必須 `required_linear_history`、マージ時ブランチ自動削除 `delete_branch_on_merge`**）により、`main` ブランチの安全性と直列性をプラットフォーム側で担保している（詳細は [GitHub App 運用規約](../../../docs/reviews/GITHUB_APP_AND_REVIEW_POLICY.md) 参照。※ `required_linear_history` は現在 `main` ブランチのみで運用しているため有効化されているが、将来 `develop` ブランチ等を導入してマージコミット運用を行う場合は本設定の適用・解除を再検討すること）。

各ロールが GitHub に書き込みを行う際は、以下の手段に従う：

| ロール                 | 書くもの                                                                                                                      | 手段                                                                                                                                                                                    |
| :--------------------- | :---------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **メイン**             | サブ Issue・Finding Issue の起票、親 Issue の進捗管理・コメント                                                               | 起票: [review-finding](../review-finding/SKILL.md) の手順（個人の `gh issue create`）<br>コメント: `bash scripts/review-sync.sh comment-issue`（Bot）                                   |
| **設計担当・実装担当** | PR の作成と本文（`Closes #<対象Issue>`、`Refs #<親Issue>`）                                                                   | `bash scripts/review-sync.sh pr-create`（Bot）<br>※ Issue への個別の完了報告コメントは不要（PR のリンクで代替）                                                                         |
| **レビュワー**         | PR への最終判定コメント（1 ラウンドに 1 件、内部レビュー RV の経緯要約 3〜5 行を含む）、および明示指示時の Finding Issue 投稿 | PR 判定: `bash scripts/review-sync.sh comment reviewer --status <Approved\|"Changes requested">`（Bot）<br>Finding Issue（明示指示時のみ）: [repo-review](../repo-review/SKILL.md) 手順 |
| **全員**               | 読み取り（`view` / `list` / `checks` / `diff` / `api` の GET）                                                                | 個人の `gh` CLI でよい                                                                                                                                                                  |

- **人間のみが行う操作**: PR の公式 **Approve（承認）**、**マージ（`gh pr merge`）**、および **Issue や PR のクローズ・再オープン・ラベル変更**は人間が行う（※ PR 本文の `Closes #N` によるマージ時の自動クローズは人間の操作に含まれる。初回 20 Scope レビューにおける Scope Issue クローズ等の明示手順は [repo-review](../repo-review/SKILL.md) に従う）。なお、ルールセットに緊急用の `bypass_actors`（`internal-dev` Team 等）が設定されている環境では、個人認証の `gh pr merge --admin` 等が技術的に通り得るため、エージェントが `gh pr merge` や `--admin` を使用することは厳禁とする（bypass の有無は人間の管理判断に委ねる）。

---

<a id="issue-vs-pr"></a>

## 8. Issue と PR の使い分け基準

判断の基準は 1 つとする：

> **「その内容は、PR をマージ（またはクローズ）した後も追う必要があるか？」**
>
> - **はい（PR 終了後も残る・追跡する）** ➔ **GitHub Issue**
> - **いいえ（この PR の差分についてだけの話）** ➔ **GitHub Pull Request**

| 書く内容                                            | 場所                                                           | 補足                                                                                                                        |
| :-------------------------------------------------- | :------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------- |
| 要件、背景、設計の方針、合意したこと                | **対象の Issue**（親 Issue があれば親）                        | 「何を・なぜ作るか」の正本                                                                                                  |
| 作業の進み具合（どのサブ Issue・PR まで進んだか）   | **親 Issue**（本文のチェックリストか、短いコメント）           | 詳しいレビューのやり取りは書かない                                                                                          |
| PR 作成前・修正ラウンド途中のエージェント間レビュー | **ローカルの RV**（`docs/reviews/internal/<instance-id>/RV*`） | 途中のやり取りはローカルで完結させ、GitHub のタイムラインを埋めない                                                         |
| この差分の最終レビュー結果、マージしてよいかの判定  | **PR コメント**（`review-sync.sh comment reviewer`）           | 「この変更をどうするか」。内部レビュー（RV）の経緯を 3〜5 行で要約して載せる（※ PR 本文を書き換えてレビュー結果を書かない） |
| 特定の行への指摘                                    | **PR のインラインコメント**                                    |                                                                                                                             |
| `defer` した指摘、PR の範囲外で見つけた不具合       | **Finding Issue**（PR から番号でリンク）                       | PR を閉じても残る必要がある（起票は [review-finding](../review-finding/SKILL.md)）                                          |
| 初回 20 Scope レビュー                              | **その Scope の Issue**                                        | 差分 PR がない監査レビューのため例外として現行運用を継続                                                                    |

---

## 9. 復旧 / 参照

### 復旧

- stale dispatch: `doctor` → `reconcile-dispatch`（[設計 §6.12](../../../docs/herdr-orchestrator/03-task-registry.md#sec-6-12)）。
- completion 失敗: `reset-completion` + `resume-task` または `recover-completion`（初回 dispatch 失敗 terminal は reset 不可）。

### 参照

- [public-cli.md](../../../skills/herdr-orchestrator/references/public-cli.md)
- [handoff.md](../../../skills/herdr-orchestrator/references/handoff.md)
- [roles.md](../../../skills/herdr-orchestrator/references/roles.md)
- [repo-review SKILL.md](../repo-review/SKILL.md)
- [review-finding SKILL.md](../review-finding/SKILL.md)
- [review-skill-audit SKILL.md](../review-skill-audit/SKILL.md)
- [design-doc-markdown-format SKILL.md](../design-doc-markdown-format/SKILL.md)
