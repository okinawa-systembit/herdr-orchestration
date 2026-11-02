---
name: repo-review
description: >-
  herdr-orchestration repo review via sync handoff, repo-review-helper CLI,
  and herdr-orchestrator. Use for レビュー依頼, 再レビュー依頼, レビュー対応,
  or reviewing Orchestrator design, implementation, and tests in this repository.
---

# herdr-orchestration — repository レビュー Skill

中央 Skill（グローバル）: **herdr-orchestration** + **herdr** 公式 Skill。  
本 Skill は **このリポジトリ専用**の Review Scope・調査検証手順・成果物・連携フロー（設計 §12 / §11.4）。
内部レビューの排他・補助状態・採番管理は `scripts/repo-review-helper` CLI を介して行う。

## 前提・資料読み込み

- `HERDR_ENV=1` の pane のみ。
- 事前: `npm run install:orchestrator-dev`（`herdr-orchestrator` on PATH）。
- リポジトリ helper: `scripts/repo-review-helper`。
- レビュー基準: [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)（開始前に必ず精読）。
- 詳細基準・テンプレート: [references.md](references.md)（開始前に必ず精読）。
- Registry / handoff: [worker-receive.md](../../../skills/herdr-orchestrator/references/worker-receive.md) / [requester-receive.md](../../../skills/herdr-orchestrator/references/requester-receive.md)（複製しない）。

## コマンド方針（Codex rule 汚染を避ける）

- Orchestrator: **`herdr-orchestrator` のみ**（`bash -lc` で長文を載せない）。
- レビュー補助操作: **`scripts/repo-review-helper`** を使用し、直接のファイル手動配置・手動採番を避ける。
- **`result-submit` の `--summary` は 200 字以内**。詳細は `result.ref` 側。
- **`--result-ref` はリポジトリ相対 1 パス**（例: `docs/reviews/internal/<instance-id>/RV001.md`）。
- Herdr 操作: 公式 **herdr** Skill。`herdr agent prompt` は **1 行・ファイル参照のみ**（[references.md](references.md)）。sync handoff 成功時は不要。

## レビュー運用の責務境界と成果物（正本）

本リポジトリでは、運用の目的に応じて **「Herdr 内部レビュー」** と **「GitHub 管理対象レビュー」** を明確に区別する（設計書: [docs/reviews/REVIEW_SKILL_PHASE1_DESIGN.md](../../../docs/reviews/REVIEW_SKILL_PHASE1_DESIGN.md)）。

### 1. Herdr 内部レビュー（エージェント間開発支援）

- **対象**: リポジトリ内での日常的な実装支援、エージェント間の手戻り防止、sync handoff によるレビュー依頼と対応。
- **正本・成果物**: 非公開作業領域のファイル群（Git 追跡除外）。
  - `.inbox/review-request.<token>.md`: 一時依頼ファイル（ディレクトリ権限 0700、ファイル 0600）
  - `docs/reviews/internal/<instance-id>/RVNNN.md`: 個別レビュー記録の正本（ディレクトリ 0700、ファイル 0600）
  - `docs/reviews/internal/<instance-id>/INDEX.md`: 個別記録から派生生成される履歴一覧
  - `<git-common-dir>/herdr-review/state.json`: 非公開補助状態（instance ID、worktree、採番カウンター）
  - `<git-common-dir>/herdr-review.lock`: 排他制御用共有 lock（0600、削除厳禁）
  - ※ 旧 `.review-inbox/` は読み取り専用の旧履歴として残存し、新規依頼には使用しない。
- **運用原則**: エージェント間で完結する閉じたローカルループであり、**Herdr 内部レビューはローカルの非公開成果物への記録を標準とし、既定（デフォルト）では GitHub への外部書き込みを行わない**。
  - **GitHub Finding Issue へのコメント投稿条件（明示的指示時のみ許可）**:
    1. **投稿トリガー**: 依頼元から明示的な指示（`review-request` または handoff の `--instruction` での指定）がある場合に限り、対象 Finding Issue へのレビュー結果コメント投稿（`gh issue comment`）を許可する。
    2. **事前確認**: コメント投稿前に、対象 Finding Issue の番号・リポジトリ・レビュー対象コミット SHA が一致していることを確認する。
    3. **投稿内容の限定**: 投稿内容は「判定・総評・指摘事項・検証結果ログ」に限定し、レビューGO を「Finding 解決完了」と表現せず「修正承認。PR マージ待ち」と明記する。
    4. **レビュワーの操作禁止事項**: **Finding Issue のクローズ（`gh issue close`）、再オープン、ラベル変更（`status:verified` 等）はレビュワー側で厳禁**とする（これらは PR マージ・再検証を経て依頼元のみが実施する）。
    5. **API 失敗時の非ブロッキング**: コメント投稿が API エラー等で失敗しても、内部レビュー結果自体（`herdr-orchestrator result-submit`）は成功（succeeded）として完了し、コメント投稿の失敗は警告（Warning）として依頼元に報告する（レビュー自体のブロッキングにしない）。

### 2. GitHub 管理対象レビュー（公開記録・追跡管理）

- **対象**: 通常 Pull Request のレビュー、初回 20 Scope レビュー、および継続追跡する未解決指摘 Issue。
- **正本**: **GitHub（Pull Request, Issues, Reviews）**。
- **運用原則**: 明示的に GitHub 管理対象として依頼された場合のみ適用し、同一レビュー情報をリポジトリ内台帳と二重管理しない。

| レビュー種別                 | 正本（Source of Truth）               | 格納場所・管理単位                                                           | 連携インターフェース                                  |
| ---------------------------- | ------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------- |
| **Herdr 内部レビュー**       | **ローカル Markdown**                 | `docs/reviews/internal/<instance-id>/RV*.md`                                 | `herdr-orchestrator result-submit`                    |
| **通常 PR レビュー**         | **GitHub Pull Request**               | PR 会話タイムライン / Files Changed                                          | `gh pr review --comment`                              |
| **ワイドレビュー（総点検）** | **ローカル Markdown / Finding Issue** | `docs/reviews/internal/<instance-id>/RV*.md`（発見事項は GitHub Issue 起票） | `herdr-orchestrator result-submit` / `review-finding` |
| **初回 20 Scope レビュー**   | **GitHub Issues**                     | Scope 単位の個別 Issue（`#101` 等）                                          | `gh issue comment`                                    |
| **継続追跡する指摘**         | **GitHub Issues**                     | 指摘単位の個別 Issue（`#105` 等）                                            | `gh issue create`                                     |

※ いずれのレビュー種別であっても、適用する**レビュー品質基準（2段階レビュー、指摘分類、重大度判定、反証確認、未実施検証明記）は完全に共通**である。

### 3. ワイドレビュー（総点検 / 全体点検 / 総チェック）

- **対象**: リリース前、マイルストーン完了時、またはユーザーから「ワイドレビュー」「総点検」「全体点検」「総チェック」の指示があった場合。
- **目的**: 単一 PR の合否判定とは独立して、リポジトリ全体の設計書ステータス、目次、CLI usage、リンク、Issue、作業領域を横断点検する（チェックリスト A-1〜A-8 は [references.md#wide-review](references.md#wide-review) 参照）。
- **判定と成果物**: PR の合否判定をブロックせず、内部レビュー記録 RVNNN.md を保存し、検出された問題は [review-finding](../review-finding/SKILL.md) スキルの手順に従って Finding Issue を起票する。

---

## Requester（implementer）— 「レビュー依頼を出して / 再レビュー依頼」

1. 初期状態確認（必要なら初期化）:
   ```bash
   scripts/repo-review-helper init
   ```
   ※ 旧 Task が残存して停止した場合は人間に確認し、放棄合意があれば `--abandon-old-tasks` を付与。
2. レビュー依頼ファイルを `.inbox/` に原子的作成:
   ```bash
   REQ_FILE=$(scripts/repo-review-helper request-create --tier STANDARD --summary "最新の変更内容についてのレビューを依頼します。")
   ```
   （必要に応じて生成された `$REQ_FILE` を編集し、重点確認事項や再レビュー参照先を追記）。
3. テスト実行と結果確認:
   `npm run test:orchestrator`（必要なら `npm run verify:orchestrator`）を実行。
4. 同一 worktree・requester pane から **sync handoff**（instruction は短く、inbox を指す）:
   ※ 対象ペインが標準ロール名 `reviewer`（日本語表示は `--label "レビュワー"`）で Role Binding 登録されている必要があります。日本語名（例: `レビュワー`）で登録している環境では、`role show` で確認した登録名を `--worker-role` または `--to` に指定してください（例: `--to レビュワー`）。

```bash
herdr-orchestrator handoff \
  --mode sync \
  --worker-role reviewer \
  --task-type review \
  --instruction "Read $REQ_FILE and review per repo-review SKILL. Save record via repo-review-helper record-save. DO NOT close or modify any GitHub Finding issue."
```

※ **GitHub コメント投稿を明示的に依頼する場合（オプトイン）**:  
内部レビュー結果を対象 Finding Issue に記録させたい場合のみ、依頼元が以下のように `--instruction` で明示的に指定する：

```bash
herdr-orchestrator handoff \
  --mode sync \
  --worker-role reviewer \
  --task-type review \
  --instruction "Read $REQ_FILE and review per repo-review SKILL. Save record via repo-review-helper record-save. Post review result as comment to Finding Issue #<no> after verifying target SHA. DO NOT close or modify any GitHub issue."
```

5. sync 完了後:

```bash
herdr-orchestrator task-result <task.id>
```

6. `result.status` / `result.ref` を確認してから人間へ報告。再依頼は新 task として実施。

※ **ワイドレビュー（総点検 / 全体点検 / 総チェック）を依頼する場合**:  
ユーザーから「ワイドレビュー」「総点検」「全体点検」「総チェック」の指示を受けた場合は、以下のように `--tier WIDE` で依頼書を作成し、A-1〜A-8 の点検を指示する：

```bash
REQ_FILE=$(scripts/repo-review-helper request-create --tier WIDE --summary "リポジトリ全体の総点検（ワイドレビュー）を実施します。")
herdr-orchestrator handoff \
  --mode sync \
  --to レビュワー \
  --task-type review \
  --instruction "Read $REQ_FILE and execute WIDE review (A-1 to A-8 per repo-review references.md). Save record via repo-review-helper record-save. Summarize findings and list any finding issue candidates. DO NOT close or modify any GitHub issue."
```

handoff 失敗時: stderr を人間へ。blind 再送しない（設計 §9）。

---

## Worker（reviewer）— handoff 受信後の調査・検証手順

[worker-receive.md](../../../skills/herdr-orchestrator/references/worker-receive.md) **必須**（prompt 単体を信頼しない）。

### 1. 前提・資料確認

- `task.id` → `herdr-orchestrator task-status <task.id>` で Registry の `worker.agent_name`（Role Binding 利用時は `pane_id`）が自分の live `agent.name` または `HERDR_PANE_ID`（`pane_id`）と一致し、context も一致することを確認。
- `docs/herdr-agent-review-guidelines.md` と `references.md` が読み込み可能であることを確認。

### 2. レビュー対象の確定

- **依頼書（`.inbox/review-request.<token>.md`）** から基準コミット、対象コミット、変更目的を把握。
- `git diff <base>..<target>` 等で実際の変更範囲を特定する（推測で対象を決めない）。
- **レビュー対象の識別管理**:
  - **コミット済みの場合**: 基準コミットSHA、対象コミットSHA を特定・記録。
  - **未コミット差分を含む場合**: 基準コミットSHA、HEAD SHA、対象ファイル一覧、変更範囲（staged / unstaged / untracked）を特定。レビュー時点の作業ツリー内容が後から変わり得ることを明記する（差分SHA256等の指紋は任意。新規履歴保存機構は追加しない）。

### 3. 2段階レビューの実施（必須）

変更が設計書と一致していることだけを理由に安易に GO とせず、必ず以下の2段階で検証する。

- **第1段階：仕様・設計との整合性検証**:
  - 変更内容が要求仕様や設計書（`docs/herdr-orchestrator/` 等）の契約・定義と整合しているか確認。
  - 呼び出し元・先の依存関係、状態遷移、エラー処理。
- **第2段階：批判的な不具合探索（必須）**:
  - **設計書と一致したことだけを理由に GO としない**。
  - 以下の観点を批判的に探索する：
    - 想定外の入力・不正引数
    - エラー発生時の処理と復旧
    - 境界条件・オフバイワン
    - 状態遷移の不整合・デッドロック
    - 並行処理・競合条件・冪等性
    - セキュリティ・権限・パス検証
    - 実行環境固有の制約（ローカル vs GitHub Actions、OS依存性等）
    - **宣言的成果物・文書間の規則整合性**（Skill・設計書・設定・テンプレート等の検証時）:
      - 同じ概念が複数箇所で異なる定義になっていないか
      - 原則と例外が関連する判定条件や表・リストに漏れなく反映されているか
      - 判定規則とテンプレートの出力区分が矛盾していないか
      - あるルールを適用した結果、別のルールと競合・矛盾しないか
      - 変更された規則の参照先や関連ドキュメントに更新漏れがないか
    - **既存保証の維持確認（変更前後での保証喪失・意図せぬ差分の検証）**:
      - **既存保証の喪失**: 変更によって従来保証されていた安全性・正確性・整合性・検証範囲が失われていないか。
      - **本来の目的の維持**: エラー回避や最適化によって、処理本来の目的や品質ゲートが損なわれていないか。
      - **変更前後の意味的な差分**: 入力・出力・実行対象・成功条件・失敗時挙動が意図せず変化していないか。
      - **設計自体の妥当性**: 実装が設計書に一致していても、その設計自体に欠陥や重要な見落としがないか。
      - **トレードオフの検証**: 変更で得られる利点だけでなく、失われる性質や新リスクを確認したか。
      - ※ **判定原則**: 「変更目的の達成」と「既存保証の維持」は別々に検証する（設計適合だけで GO としない）。ただし挙動差分自体を直ちに不具合と断定せず、意図的な仕様変更や許容されたトレードオフである可能性を確認し、具体的な実害・根拠がある場合に限り不具合として報告する。
      - ※ **重点適用分野**: CI/CD・ビルド・テスト、セキュリティ・認証、データ整合性、エラー処理・リトライ、並行処理・状態管理、API契約変更。

### 4. 指摘の3分類・反証確認と重大度判定

- **指摘の3分類**:
  - **A. 確認済み不具合**: コード・仕様・テスト等で裏付けられる具体的な問題（P0〜P3）。
  - **B. 未確認リスク**: 発生可能性はあるが重要な条件が未確認（原則として「要確認事項」へ分類）。
  - **C. 改善提案**: 現行実装が成立しており、保守性・可読性・診断性を高める提案（原則 P3 / Non-blocking。確定的不具合として扱わず、Blocking 指定禁止）。
- **重大度と Blocking 判定の分離管理**:
  - P0 / P1: **Blocking**（重大度のみで Blocking を推測せず、P1 は下記 4 要件必須）。
  - P2: 原則 **Non-blocking**。**具体的な重大影響とマージ前修正の必要性が客観的に裏付けられる場合のみ例外的に Blocking**（Blocking 理由の明記必須）。
  - P3: **Non-blocking**（理由なく Blocking 扱い禁止）。
- **重大度の過大評価禁止**:
  - P1 と判定する場合、(1) 発生条件 (2) 主要機能・データへの重大影響 (3) 根拠コード・検証 (4) Blockingが必要な理由 の4要素を必須とする。「追加検証したい」「仕様を明記したい」だけでの P1 判定を禁止する。
- **指摘候補に対する反証確認（必須）**:
  - 指摘を提示する前に、以下の反証問いを必ず確認する：
    - 既存の処理や上位層で防止されていないか？
    - 実際に失敗する具体的な条件が成立するか？
    - 単なる実装方式や好みの違いではないか？
    - 修正しなくても要求仕様を満たしているか？
    - 推奨案は実際に問題を解消するか？
    - 失われたと考える保証や機能は、従来本当に存在・意図されていたものか？（過大保証を前提にしていないか）
    - 別の仕組みや後続処理でその保証が実質的に維持されていないか？
    - その保証は現在の仕様で本当に必要か？意図的な仕様変更やトレードオフではないか？

### 5. 再レビュー時の検証（該当する場合）

- **前回結果の事前読み込み**: 再レビュー時は、**前回のレビュー記録（`docs/reviews/internal/<instance-id>/RVNNN.md` や `INDEX.md`、または依頼書で指定された前回参照先）から前回指摘の詳細を読み込んで保持する**。
- **参照不能時の安全措置**: 前回結果が消失・参照不能な場合は、推測で「対応済み」と断定せず、判定を `検証不能（前回結果参照不可）` とし、理由と確認できた範囲を報告する。
- **前回指摘の再検証**: 最新コードで各指摘を `対応済み` / `部分対応` / `未対応` / `前回指摘の誤り` / `検証不能` に分類。
- **回帰不具合の確認**: 修正によって別の問題やテスト・状態破綻が生じていないか確認。
- **新規問題の探索**: 新たな差分および周辺コードについて前回見落とした問題がないか探索。

### 6. 判定・成果物作成

- **GO判定の条件**:
  - GO は「問題が存在しないことの保証」ではない。以下を満たす場合に GO とする：
    1. 必須のレビュー観点（第1段階・第2段階）を確認した。
    2. **未解消の確認済み Blocking 指摘（P0 / P1、および P2 の例外的 Blocking を含む）が 0 件であること**（P2 Blocking 指摘が 1 件でもあれば GO 不可。P2 Non-blocking のみであれば他の GO 条件を満たす限り GO 可）。
    3. 未実施の検証を明示した。
    4. 重大な未確認リスクがあれば明記・報告した。
- **レビューGOとFinding解決完了の明確な区別（早期クローズの禁止とクローズ権限の分離）**:
  - **レビューGOは、レビュー対象の変更内容がマージ可能であることを示すのみであり、関連する Finding Issue の解決完了を意味しない**。
  - 修正 PR が Open の間は、レビューGO だけを根拠として Finding Issue を Closed にしてはならない。
  - **レビュー結果のコメント投稿条件とクローズ権限の厳格な分離**:
    - レビュワーエージェントによる対象 Finding Issue へのレビュー結果コメント投稿（`gh issue comment`）は、**依頼元から明示的に指示された場合（request や instruction での指定）に限り許可される**（自律的な独自判断による外部コメント投稿は行わず、ローカル非公開領域への記録保存を基本とする）。
    - **Finding Issue のクローズ（`gh issue close`）、再オープン、ラベル変更は、PR マージおよびマージ後再検証完了後に依頼元（requester / implementer）が実施する責務であり、レビュワー（reviewer）が自律的に実行することは厳禁とする（クローズ権限は依頼元のみに限定）**。
    - コメント投稿が API 障害等で失敗した場合でも内部レビュー結果自体（`result-submit`）は成功として完了し、失敗を警告として依頼元へ報告する（レビュー自体のブロッキングにしない）。
  - Finding Issue の解決完了・クローズは、PR マージおよび必要な再検証が完了した後に、依頼元が [review-finding](../review-finding/SKILL.md) Skill の完了管理手順に従って実施する。
  - レビュー結果を GitHub やチャット等に記録・報告する場合も、「修正内容を承認（マージ可能）。PR マージ待ち」のように、マージ前の承認であることを明確に区別して記載する。
- **事実と推論の区別**:
  - 実行して確認した事実、コード読解の事実、仕様・設計に基づく判断、推測・未検証の仮説を明確に区別して記載する（ローカルテスト成功を理由にリモートActions実行成功まで保証しない）。
- [references.md#review-principles](references.md#review-principles) に基づき重大度（P0〜P3）と確信度（高/中/低）を判定。
- レビュー結果本文を整形し（見出し構成は [references.md#review-response-body](references.md#review-response-body) に従う）、`scripts/repo-review-helper record-save` を使って保存する（自動採番、原子的保存、INDEX.md 派生更新）：
  ```bash
  # 安全な一時ファイル（0600）にレビュー本文を用意（構成は references.md に準拠）
  TMP_BODY=$(mktemp)
  chmod 0600 "$TMP_BODY"
  cat << 'EOF' > "$TMP_BODY"
  # レビュー記録 — <タイトル>

  ## レビュー条件・メタデータ
  - レビュワーモデル: ...
  - 対象コミットSHA: ...

  ## 判定
  - [x] GO

  ## 総評
  ...

  ## Blocking 指摘
  なし。

  ## Non-blocking 指摘
  ...

  ## テスト・検証結果
  ...

  ## 調査範囲と未確認事項
  ...
  EOF

  # 採番・個別記録の原子的公開・INDEX更新（--outcome にレビュー判定 GO/要修正/NO-GO を指定）
  scripts/repo-review-helper record-save \
    --task-id "<task.id>" \
    --body-file "$TMP_BODY" \
    --outcome "GO" \
    --target-commit "<target_commit>"
  rm -f "$TMP_BODY"
  ```

### 7. 結果登録

保存された個別記録パス（例: `docs/reviews/internal/<instance-id>/RV001.md`）を `--result-ref` に指定して提出する：

```bash
herdr-orchestrator result-submit <task.id> \
  --result-status succeeded \
  --result-ref "docs/reviews/internal/<instance-id>/<review-id>.md" \
  --summary 'Review record saved via repo-review-helper.'
```

※ **レビュー判定と Task 実行ステータスの分離**:  
レビュー判定が `GO` 以外（`要修正` や `NO-GO`）であっても、レビュー作業・検証自体が正常に完了して記録が保存されていれば、Orchestrator の Task 実行ステータスは `--result-status succeeded` として提出する（Task 自体の実行失敗時のみ `failed` とする）。  
`--summary` は 200 字以内の簡潔な記録に留める（詳細は result.ref 側）。

---

## GitHub 連携運用（PR レビュー & Scope レビュー）

GitHub を正本とするコードレビュー運用では、以下の手順で GitHub CLI (`gh`) を用いて記録・連携する。  
※ **適用範囲の明確化**: 本手順は、通常 PR や初回 20 Scope などの「GitHub 管理対象レビュー」として明示的に依頼された場合にのみ適用する。通常の Herdr 内部レビュー（エージェント間 handoff）では本手順は不要であり、`repo-review-helper` によるローカル記録と `result-submit` だけで完結する（不要な GitHub 投稿は行わない）。  
※ レビュー品質基準（2 段階レビュー、指摘 3 分類、重大度 P0〜P3、反証確認、未実施検証の明示）は Herdr 連携時と完全に同一の基準を適用する。

### A. 通常 Pull Request の継続的レビュー手順

0. **Pull Request の作成（Bot 認証）**:
   - エージェントが PR を作成する際は、自 PR 制限を回避するため `scripts/review-sync.sh pr-create` を使用する（PR 作成者が `<app-slug>[bot]` となり、人間が自アカウントで公式 Approve できるようになる）：
     ```bash
     bash scripts/review-sync.sh pr-create --title "..." --body "..."
     ```
1. **差分とメタデータの確認・ドラフト取り込み**:
   - レビュー対象ブランチをチェックアウトし、ドラフトを取り込む（※ PR 番号は現在のブランチから自動解決されるため引数にはロール名のみを指定する）：
     ```bash
     bash scripts/review-sync.sh pull reviewer
     ```
   - 差分と CI チェックの確認：
     ```bash
     gh pr view --json number,title,baseRefName,headRefOid,commits
     gh pr diff
     gh pr checks
     ```
2. **2段階レビューの実施**:
   - `repo-review` 基準に沿って、第 1 段階（仕様・設計整合性）および第 2 段階（批判的不具合探索）を実施。
   - `.input/review/reviewer-draft.md` を編集し、指摘事項（P0〜P3）、総合判定（GO / 要修正 / NO-GO）、未実施検証を記載。
3. **レビュー結果の投稿**:
   - レビュー投稿は `scripts/review-sync.sh comment reviewer` を使用する（※ PR 番号は現在のブランチから自動解決されるため引数にはロール名のみを指定する）：
     ```bash
     bash scripts/review-sync.sh comment reviewer
     ```
   - 原則として `--comment` でレビュー結果を投稿する（Blocking 指摘時も `--request-changes` は使用せず、`- Status: Changes requested` を含む `--comment` で表明する）。
   - **安全制約（承認とマージの分離）**:
     1. **AI エージェントの禁止事項**: GitHub App (Bot) 認証下であっても、AI エージェントによる GitHub 公式の承認（`gh pr review --approve`）、変更要求（`--request-changes`）、およびマージ（`gh pr merge`）の実行は**厳格に禁止**する。レビューの合意形成は、常に `--comment` 投稿本文の先頭メタデータ（`- Status: Approved` または `- Status: Changes requested`）にて行う（判定との対応表は [references.md#review-status-mapping](references.md#review-status-mapping) 参照）。
     2. **自 PR 制限回避と人間の役割**: エージェントが Bot 主体として PR を作成・操作することにより、開発者本人の個人アカウントとの同一視（自 PR 制限: 422 エラー）が回避される。GitHub 公式の Approve（承認ボタンの押下）および PR のマージ操作は、エージェントの合意表明を確認した上で**人間の開発者のみが担当**する（詳細は [docs/reviews/GITHUB_APP_AND_REVIEW_POLICY.md](../../../docs/reviews/GITHUB_APP_AND_REVIEW_POLICY.md) 参照）。
4. **レビューイ対応ワークフロー（指摘への対応・回答投稿・再レビュー依頼）**:
   - レビュワーから指摘を受領した際、**いきなりコード修正に走ることを厳禁**とする（手戻りや別不具合の誘発、PR 肥大化を防止するため、以下の必須 6 ステップを厳守する。詳細は [references.md#reviewee-response-workflow](references.md#reviewee-response-workflow) 参照）：
     1. **ドラフト取り込み**: `bash scripts/review-sync.sh pull reviewee` で最新レビューを取り込む（各指摘見出し直下に Decision 枠が自動展開される）。
     2. **事前評価（コード修正厳禁）**: 各指摘を客観的に評価し、「同意 / 部分同意 / 不同意」および Decision（`accept` / `reject` / `defer`）を策定する。この段階でコードを 1 行も修正してはならない。
     3. **チャット提示（必須・トリアージ）**: 人間（利用者）へチャットで各指摘の評価・Decision 案・代替案を提示する。
     4. **人間との合意・方針確定**: 人間からの合意・指示を得て対応方針を確定する。
     5. **修正の実施（`accept` のみ）**: 合意された `accept` の項目のみコード修正を行い、テスト（`npm run test:orchestrator`）および `npm run format:md && npm run check:md` で検証する。`reject` や `defer` の項目はコード変更しない。
     6. **回答投稿と再レビュー依頼**: `.input/review/reviewee-draft.md` を完成させ、`bash scripts/review-sync.sh comment reviewee` で投稿後、レビュワーへ再レビューを依頼する（sync handoff）。

### B. 初回 20 Scope レビュー手順

1. **対象 Scope Issue の確認**:
   ```bash
   gh issue view <issue_number>
   ```
2. **レビュー実施と結果記録**:
   - 対象コミット SHA をチェックアウトして検証。
   - レビュー結果を Scope Issue にコメント投稿：
     ```bash
     gh issue comment <issue_number> -F review-result.md
     ```
3. **未解決指摘の先行起票とクローズ (SIMP-01 順序厳守 & review-finding Skill 連携)**:
   - 未解決指摘（P0〜P2）が存在する場合、**Scope Issue をクローズする前に**、[review-finding](../review-finding/SKILL.md) Skill を呼び出して Finding Issue を起票する（クローズ後の追加発見時も Closed の検出元を参照して起票可能）。
   - **起票前品質バリデーション**:
     - `review-finding` 側で、重大度と Blocking 判定の整合性（P2 Blocking の理由明記必須、`blocking:yes` ラベル付与）、是正完了条件チェックリスト（2項目以上）、回帰テスト条件、維持すべき既存保証、および多層検索による既存 Issue との重複有無を事前検証する。
     - バリデーションを通過した場合のみ、`gh issue create` で Issue が起票される。
   - 起票した指摘 Issue 番号を Scope Issue のコメントに追記し、相互リンクを記録。
   - 全指摘の起票・リンク完了を確認後、Scope Issue をクローズ：
     ```bash
     gh issue close <issue_number> --reason "completed"
     ```
   - ※ 途中で失敗またはバリデーションエラーとなった場合は Scope Issue を Open のまま残して安全停止する。
   - **Scope Issue と Finding Issue のクローズ条件の厳格な区別（混同禁止）**:
     - Scope Issue のクローズは「その Scope に対するレビュー解析・判定・指摘起票の作業が完了したこと」を意味する。
     - 一方、起票された **Finding Issue はこの時点で絶対にクローズしてはならない**。Finding Issue は修正 PR の作成・マージ・再検証を経て、依頼元（requester / implementer）が [review-finding](../review-finding/SKILL.md) Skill の管理手順に従って初めて解決・クローズされる。
     - Scope Issue のクローズ手順（`gh issue close`）を Finding Issue に誤適用することは厳禁とする（レビュワーエージェントに Finding Issue のクローズ権限はなく、クローズは依頼元のみが実施する）。

### C. オフライン・API 障害時のフォールバック

GitHub API の一時停止や認証エラー時は、ローカルの `repo-review-helper record-save` で結果を出力し、人間へ手動反映を依頼する。

## Requester — 「レビュー結果の確認 / レビュー対応」

[requester-receive.md](../../../skills/herdr-orchestrator/references/requester-receive.md) に従い `task-status` / `task-result` で terminal を確認後、以下のレビューイ対応標準ワークフローに従って対応する（即時修正厳禁）：

1. **結果の確認**: `result.ref`（`docs/reviews/internal/<instance-id>/RVNNN.md`）を正本として指摘内容と判定を確認。
2. **事前評価（即時修正禁止）**: 各指摘を客観的に評価し、「同意 / 部分同意 / 不同意」および Decision（`accept` / `reject` / `defer`）を策定する（[references.md#reviewee-response-workflow](references.md#reviewee-response-workflow) 参照）。**この段階でコードを 1 行も修正してはならない**。
3. **人間チャットへの事前トリアージ提示**: 人間へ [references.md#reviewee-response-workflow](references.md#reviewee-response-workflow) 形式で各指摘の評価・Decision 案を提示し合意を得る。
4. **修正の実施（`accept` のみ）**: 人間と合意した `accept` の項目のみ修正を実施し、テストで検証する。
5. **人間チャットへ対応完了報告**: [references.md#human-report](references.md#human-report) 形式で報告。
6. **コミット・プッシュの先走り禁止（責務境界の厳守）**:
   - レビュー対応完了および人間報告の時点で**必ず処理を停止する**。
   - **Git のコミット・プッシュ等の永続化・リモート反映操作は、利用者の明示的な指示（例：「コミット＆プッシュして」）を得るまで決して実行してはならない**。

---

## レビュー Scope（本 repo）

- Orchestrator 設計: `docs/herdr-orchestrator/`（公式 Herdr vs 独自の区別）
- 実装 / CLI: `skills/herdr-orchestrator/`
- テスト: `tests/test_orchestrator_*.py`、`npm run test:orchestrator`
- 責務: 中央 Skill vs repository Skill（本 Skill / workflow）

詳細チェックリスト: [references.md#review-scope](references.md#review-scope)。
