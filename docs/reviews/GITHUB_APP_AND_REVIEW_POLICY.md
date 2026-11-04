# GitHub App 導入に伴うレビュー運用規約とスキル仕様改訂方針

GitHub App 自動認証基盤（Phase 1）および安全な連携ラッパー（Phase 2）の導入に伴い、AI エージェントと人間の責務境界、レビュー承認（Approve）およびマージ権限の運用規約、ならびにスキル仕様（`.agents/skills/repo-review/SKILL.md`）の改訂方針を定義する。

| 項目       | 内容                                                                                                                                                 |
| :--------- | :--------------------------------------------------------------------------------------------------------------------------------------------------- |
| 対象       | 開発者、AI コーディングエージェント（Antigravity, Claude Code 等）                                                                                   |
| スコープ   | レビュー運用規約、承認・マージ権限、GitHub App 権限、スキル仕様改訂方針                                                                              |
| 関連 Issue | [#85](https://github.com/okinawa-systembit/herdr-orchestration/issues/85), [#88](https://github.com/okinawa-systembit/herdr-orchestration/issues/88) |
| 言語方針   | 日本語正本                                                                                                                                           |
| ステータス | 合意済み（Approved）                                                                                                                                 |

---

## 1. 背景と課題（RV063 P2-1 の整理）

### 1.1 表面的な衝突と論点

先行レビュー（RV063 内部レビュー記録 §3 P2-1）において、親 Issue #85 の目的と現行スキル規約の間に以下の文言上の衝突が指摘された：

1. **親 Issue #85 の目的の記述**:
   - 「同一アカウント制約の解消: エージェントが Bot として動作することで、同一個人アカウント運用における自 PR 制限（Approve 不可等）を回避可能にする。」
2. **現行の `.agents/skills/repo-review/SKILL.md`（L299）の安全制約**:
   - 「AI エージェントによる `--approve` および `gh pr merge` は実行禁止。コード確認、正式なレビュー承認（Approve）、およびマージは人間が担当する。」

この記述は、「AI エージェントが Bot 化されることで、AI 自身が GitHub の公式 Approve（`gh pr review --approve`）を実行するのではないか」という懸念を生じさせていた。

### 1.2 「自 PR 制限の回避」の本来の意図と実態

先行プロジェクト（`staff-portal-system`）の実績および GitHub のアクセス制御仕様を精査した結果、「自 PR 制限の回避」の本来の目的は以下の通りであることが確認された：

- **GitHub の仕様（自 PR の承認禁止）**:
  - GitHub では、ブランチ保護の有無に関わらず、PR 作成者本人のアカウントによる自 PR の Approve は常に禁止されている（GitHub API 422 エラー: `Can not approve your own pull request`）。
  - リポジトリ保護ルール（`Require approval from pull request reviews: 1`）が有効化された環境では、PR 作成者以外の第三者による公式 Approve がマージの必須要件となる。
- **個人 PAT 運用での問題点**:
  - エージェントが開発者の個人 PAT（個人アカウント）を使って PR を作成すると、PR 作成者が「人間本人」として認識される。
  - このため、人間自身がその PR を正式に確認・承認（公式 Approve）しようとしても、自 PR 制限に引っかかって公式 Approve を押せなくなる。
- **GitHub App (Bot) 導入による解決**:
  - エージェントが専用 GitHub App の `<app-slug>[bot]` として PR を作成・操作する。
  - これにより、**人間の開発者が自アカウントで正式な公式 Approve（レビュー承認）を行えるようになり、自 PR 制限を安全に回避できる**。
- **結論**:
  - **「自 PR 制限の回避」とは、「人間が自アカウントで公式 Approve を行えるようにすること」であり、「AI エージェント自身に公式 Approve を押させること」ではない**。

---

## 2. レビュー承認・マージの基本原則（承認とマージの分離）

本リポジトリでは、ガバナンスと安全性を最優先とし、先行実績のある「承認とマージの分離」原則を堅持する。

### 2.1 AI エージェントの権限と役割

1. **レビュー指摘・回答の投稿**:
   - AI レビュワーおよび AI レビューイは、PR レビューコメントおよび Issue コメントの投稿（`gh pr review --comment` または `gh issue comment`）を行う。
2. **合意形成の表明（メタデータ）**:
   - AI レビュワーは、未解消の Blocking 指摘がなくマージ可能と判断した場合（判定基準は references.md §4.1 参照）、ドラフトおよびコメント本文先頭のメタデータにおいて `- Status: Approved` を明記して合意を表明する（要修正および NO-GO の場合は `- Status: Changes requested` を明記。内部判定とメタデータの対応表は [.agents/skills/repo-review/references.md#review-status-mapping](../../.agents/skills/repo-review/references.md#review-status-mapping) 参照）。
3. **厳格な禁止事項（安全制約の堅持）**:
   - **GitHub 公式の Approve 操作（`gh pr review --approve`）の実行禁止**:
     - Bot トークンを利用している場合であっても、AI エージェント自身が GitHub 公式の Approve を送信することは厳格に禁止する。
   - **GitHub 公式の Changes requested（`gh pr review --request-changes`）の実行禁止**:
     - 未解決指摘がある場合も公式の `--request-changes` は使わず、`--comment` 本文および `- Status: Changes requested` にて表明する。
   - **Pull Request の自動マージ（`gh pr merge`）の実行禁止**:
     - PR のマージ操作は、いかなる場合も人間による明示的な指示または直接操作によってのみ行われる。

### 2.2 人間の開発者の権限と役割

1. **最終確認と GitHub 公式 Approve**:
   - AI レビュワーが `- Status: Approved`（または Herdr レビュー記録で `GO`）を表明したことを確認し、人間の開発者が自アカウントで PR を確認した上で、GitHub 公式の Approve を実行する。
2. **PR マージの最終決定**:
   - CI チェック（`checks`）の通過、および公式 Approve の完了を確認し、人間の開発者が PR をマージ（`gh pr merge`）する。

### 2.3 ブランチ保護設定とコミット push 主体に関する留意事項

ブランチ保護ルールで「Require approval of the most recent reviewable push」が有効化されている場合、最後にコミットを push した人物のアカウントによる Approve は無効（カウント対象外）となる。
開発者が自アカウントで git push を行った場合、PR 作成者が Bot であってもその開発者による Approve が無効化される可能性がある。
§3 の通り GitHub App の権限は `Contents: Read-only`（push 不可）であるため、本制約への対策としては「該当保護ルール項目の無効化」、または「コードを push した開発者とは別のレビュワーが Approve を担当する運用」を採用する。

---

## 3. GitHub App の最小権限（Least Privilege）方針

GitHub App に付与する権限（Repository permissions）は、レビュー連携に必要な最小限のスコープに限定し、不要な特権を排除する。

| 権限項目（Permission）  | アクセスレベル | 用途・理由                                                                            |
| :---------------------- | :------------- | :------------------------------------------------------------------------------------ |
| **Pull requests**       | `Read & write` | PR 作成（`pr create`）、レビューコメント取得（`pull`）、コメント投稿（`comment`）     |
| **Issues**              | `Read & write` | 監査 Issue・Finding Issue の指摘取得（`pull-issue`）、コメント投稿（`comment-issue`） |
| **Contents**            | `Read-only`    | コード、差分（diff）、ブランチ情報の読み取り                                          |
| **Metadata**            | `Read-only`    | リポジトリ基本情報の取得（GitHub API 必須要件）                                       |
| **Administration**      | `No access`    | 設定変更・権限変更の防止                                                              |
| **Secrets / Variables** | `No access`    | 機密情報へのアクセス遮断                                                              |
| **Workflows**           | `No access`    | CI ワークフロー改変の防止                                                             |

---

## 4. 安全な操作ラッパー (`scripts/review-sync.sh`) との整合性

Phase 2 で実装された `scripts/review-sync.sh` は、本規約に完全に合致する設計となっている：

1. **常に `--comment` のみを使用**:
   - `submit_comment` 関数では `gh pr review --comment --body-file ...` のみを使用し、`--approve` や `--request-changes` フラグを呼び出すコードは一切存在しない。
2. **静的検証 (`check`) によるロール統制**:
   - `reviewer` はメタデータ部に `- Status: (Approved|Changes requested)` が必須。
   - `reviewee` はメタデータ部に判定 `Status:` を記述不可（Finding 内の個別 Status のみ更新可能）。
3. **機密情報漏洩検査**:
   - 送信前に GitHub トークン（`ghs_` 等）や秘密鍵ヘッダーの混入を機械的にスキャンし遮断。

### 4.1 Bot トークンを利用した PR 作成手順 (`review-sync.sh pr-create`)

エージェントが Bot を主体として PR を作成する場合は、`scripts/review-sync.sh pr-create` を使用する。
内部で改ざん検知を実施した上で、`github_app_auth.py` より発行された Installation Access Token を `GH_TOKEN` に自動設定して `gh pr create` を安全に呼び出す。認証エラー時は個人認証へ暗黙にフォールバックさせず、直ちに停止する：

```bash
# Bot トークンによる PR 作成 (PR 作成者を <app-slug>[bot] とする)
bash scripts/review-sync.sh pr-create --title "..." --body "..."
```

GitHub App 設定が存在しない環境では、個人認証による自 PR 制限に関する警告を表示した上でフォールバック実行される。
これにより PR の作成者が Bot となり、ブランチ保護下で人間の開発者が自アカウントから公式 Approve を押すことが可能となる。

---

## 5. スキル仕様 (`.agents/skills/repo-review/SKILL.md`) の改訂内容

`.agents/skills/repo-review/SKILL.md` の「安全制約」記述を以下のように明確化・更新する：

### 改訂前 (旧規定)

```markdown
3. レビュー結果の投稿:
   - 原則として --comment でレビュー結果を投稿する：
   - ※ 運用ポリシーで許可されている場合のみ、Blocking 指摘時に --request-changes を使用可能。
   - 安全制約: AI エージェントによる --approve および gh pr merge は実行禁止。コード確認、正式なレビュー承認（Approve）、およびマージは人間が担当する。
```

### 改訂後

```markdown
3. レビュー結果の投稿:
   - 原則として --comment でレビュー結果を投稿する（Blocking 指摘時も --request-changes は使用せず、- Status: Changes requested を含む --comment で表明する）：
     gh pr review <pr_number> --comment -F review-result.md
   - レビュー投稿は scripts/review-sync.sh comment reviewer（または --body-file 指定の gh pr review --comment）を使用する。
   - 安全制約（承認とマージの分離）:
     1. AI エージェントの禁止事項: GitHub App (Bot) 認証下であっても、AI エージェントによる GitHub 公式の承認（gh pr review --approve）、変更要求（--request-changes）、およびマージ（gh pr merge）の実行は厳格に禁止する。レビューの合意形成は、常に --comment 投稿本文の先頭メタデータ（- Status: Approved または - Status: Changes requested）にて行う。
     2. 自 PR 制限回避と人間の役割: エージェントが Bot 主体として PR を作成・操作することにより、開発者本人の個人アカウントとの同一視（自 PR 制限: 422 エラー）が回避される。GitHub 公式の Approve（承認ボタンの押下）および PR のマージ操作は、エージェントの合意表明を確認した上で人間の開発者のみが担当する。
```

---

## 6. 親 Issue #85 の文言明確化

親 Issue #85 の「目的 2」を以下の通り明確化し、誤解を解消する：

- **改訂前**:
  - `同一アカウント制約の解消: エージェントが Bot として動作することで、同一個人アカウント運用における自 PR 制限（Approve 不可等）を回避可能にする。`
- **改訂後**:
  - `同一アカウント制約の解消: エージェントが Bot として PR 作成・操作を行うことで、PR 作成者と開発者本人の同一視を回避し、ブランチ保護ルール下において人間の開発者が自アカウントで安全に公式 Approve を実施可能にする（エージェント自身の Approve 禁止規約は堅持）。`

---

## 7. 機械検査とエージェント判断の境界およびテンプレート主導運用方針（Issue #113）

リポジトリ基本方針（[Issue #113](https://github.com/okinawa-systembit/herdr-orchestration/issues/113)）に基づき、レビュー同期基盤およびレビューワークフローにおける機械検査とエージェント判断の責務を以下のように切り分ける。

### 7.1 機械検査の対象（安全門番と固定規約）

- **秘密情報の漏洩防止**: トークンや秘密鍵等の致命的機密情報の混入検査。
- **改ざん検知と安全防壁**: `origin/main` からのスクリプト不正改変の遮断。
- **制御メタデータの整合性**: ロールと Status（`Approved` / `Changes requested`）の整合など、自分たちで定めた外側制御値の整合性。

### 7.2 エージェント判断への委譲（自由記述と構文解釈）

- **本文の品質・妥当性**: 指摘内容、反証確認、評価、修正理由の意味解釈はエージェントおよび人間に委ね、スクリプトで文章の完成度や形式を過剰にバリデーションしない。
- **Markdown の構文と見た目**: コードブロックの閉じ忘れや混在フェンスの構文解釈をスクリプトで中途半端に機械検査（数え上げや状態追跡）せず、Prettier による自動整形およびエージェントの読み返しに委ねる。
- **テンプレート主導**: 機械が制御する値（Round, Status 等）は本文外側のヘッダーとして付与し、本文はエージェントがテンプレートに沿って記述する。スクリプトが本文をパースして値を抽出したり、本文内に Decision 欄を機械的に自動挿入する処理は行わない。
