---
name: review-finding
description: >-
  Validates, standardizes, and creates GitHub Finding Issues from code review
  findings. Ensures severity/blocking consistency, refutation checks, and completion/regression criteria.
---

# review-finding — GitHub レビュー指摘（Finding Issue）起票・品質検証 Skill

本 Skill は、`herdr-orchestration` リポジトリにおいて、コードレビュー（通常 PR レビュー、Scope レビュー等）で検出された未解決指摘（Finding）を **GitHub Finding Issue として起票する前に品質検証を行い、整合性を保って起票・相互リンクを行うリポジトリ専用 Skill** である。

正本（Source of Truth）は **GitHub Issues** であり、リポジトリ内に独自の台帳や二重管理用ファイルは追加しない。

---

## 1. 目的と責務境界

レビュー担当エージェント（`repo-review` 等）と、Issue 起票・品質検証を担当する本 Skill の責務を明確に分離する。

| 機能                              | 担当 Skill       | 責務と制約                                                                         |
| :-------------------------------- | :--------------- | :--------------------------------------------------------------------------------- |
| **不具合探索・反証確認**          | `repo-review`    | コードの批判的精査、8つの反証確認。                                                |
| **重大度・Blocking 判定**         | `repo-review`    | 技術的影響度（P0〜P3）とマージ可否（Blocking/Non-blocking）の決定。                |
| **Finding 本文の構造化**          | `review-finding` | テンプレートに沿ったメタデータ、詳細、是正完了条件の成型。                         |
| **起票前品質バリデーション**      | `review-finding` | 必須項目、具体性、重大度・Blocking の整合性検証。                                  |
| **既存 Issue との重複確認**       | `review-finding` | GitHub 上の既存 Finding との突合・類似度チェック。                                 |
| **GitHub Issue 起票・相互リンク** | `review-finding` | `gh issue create` による起票、Scope/PR へのリンク記録。                            |
| **Finding 解決完了・再検証管理**  | `review-finding` | PR マージ確認、マージ後再検証、ステータス遷移（`status:verified` 付与/クローズ）。 |

> [!CAUTION]
> **判定改変の禁止（エージェント制約）**:  
> `review-finding` は、レビュー担当が下した重大度（P0〜P3）や Blocking 判定を**エージェントの独断で勝手に変更してはならない**。  
> 判定に論理矛盾や根拠不足（例: P2 Blocking なのに理由が未記載、P3 なのに Blocking 指定等）がある場合は、起票を保留し、**レビュー担当エージェントへ修正・根拠提示を差し戻す**こと。

> [!CAUTION]
> **早期クローズの禁止（エージェント制約）**:  
> レビューGO（修正内容の承認）だけを根拠として Finding Issue を Closed にしてはならない。  
> レビューGO は「変更内容がマージ可能であること」を示すのみであり、解決完了を意味しない。Finding Issue の解決完了は、**① 修正 PR のマージ完了、② マージコミットに対する再検証完了、③ 是正完了条件の達成、④ 結果記録** のすべてを満たした場合にのみ成立する。PR マージ前のクローズは厳禁とする。

---

## 2. 適用範囲

- **GitHub 管理対象レビュー**:
  - 初回 20 Scope レビューなどの Scope Issue レビュー。
  - 通常 Pull Request のレビューにおいて、マージ前是正または将来追跡が必要な未解決指摘。
- **Herdr 内部レビューでの扱い**:
  - エージェント間の日常的な handoff レビュー（ローカル内部レビュー完結）では GitHub 起票は**不要**であり、本 Skill による GitHub 投稿は強制しない。

---

## 3. 起票前品質バリデーション仕様

GitHub への書き込み（`gh issue create`）を実行する前に、以下の 4 段階の検査を必ず実施する。1 つでも違反がある場合、Issue 起票を行わずレビュー担当へ差し戻す。

### 段階 1: メタデータ整合性検証

1. **検出元の確認 (RF-01)**:
   - 検出元 Scope Issue または PR 番号が明記され、GitHub 上で実在していること（Open / Closed の状態は問わない）。
   - 存在しない Issue 番号は拒否する。
   - 検出元が Closed であっても新規 Finding の起票を妨げない。ただし、検出元の Closed 状態をエージェントが自動で Re-open することは禁止する。
2. **対象コミット**:
   - 検出対象のコミット SHA（原則 7 文字以上の正規 SHA）が記載されていること。
3. **重大度と Blocking 判定・ラベルの整合性 (RF-02)**:
   - **P0 / P1**: `Blocking: Yes` が必須（マージ前是正必須）。起票時に `blocking:yes` ラベル付与対象。
   - **P2**: 原則 `Blocking: No`。例外的に `Blocking: Yes` とする公算がある場合は、**「Blocking 理由（なぜマージ前に是正が不可欠か）」が客観的・具体的に記述されていること**。この場合のみ起票時に `blocking:yes` ラベル付与対象。
   - **P3**: 原則 `Blocking: No`。`Blocking: Yes` の指定は禁止（エラー差し戻し）。
   - **本文とラベルの完全一致**: 本文内の `Blocking: Yes / No` と起票予定の `blocking:yes` ラベルの有無が論理的に一致していること。

### 段階 2: 不具合内容の具体性・客観的根拠の検証

1. **対象箇所の実在性**:
   - 対象ファイルパスがリポジトリ内に実在し、該当行番号（`file.py:L123`）またはシンボルが特定されていること。
2. **発生条件の具体性**:
   - どのような入力引数、環境条件、並行実行順序、状態遷移の際に問題が発生するかが具体的に記述されていること（「〜の場合がある」等の曖昧・抽象的表現のみは不可）。
3. **期待動作と実際の動作の対比**:
   - 「本来どう動くべきか（仕様・契約）」と「実際どう動いてしまうか（例外、データ破壊、放置等）」が明確に対比されていること。
4. **反証確認の確認**:
   - [8つの反証確認の問い](references.md#refutation-checks)（既存処理での防止、失敗条件の成立性等）を確認した結果が記載されていること。

### 段階 3: 是正完了・品質保証項目の検証（ばらつき防止）

1. **`## 是正完了条件`**:
   - 単なる修正方針の文章ではなく、修正確認時に客観的に成否をチェックできる **Markdown チェックリスト（`- [ ] ...`）が最低 2 項目以上** 記載されていること。
2. **`## 回帰テスト条件`**:
   - 修正確認時に実行すべきテストコマンド（例: `npm run test:orchestrator`、`python3 -m unittest ...`）または新規追加すべきテストケース要件が明記されていること。
3. **`## 維持すべき既存保証`**:
   - 修正によって損なわれてはならない既存の契約・正常系動作・品質ゲートが明記されていること。

### 段階 4: 既存 Issue 重複検証 (RF-03, RF-05)

1. **多層検索の実施 (RF-03)**:
   - 全体検索（上限を引き上げて広域に取得）:
     ```bash
     gh issue list --label review-finding --state all --limit 300
     ```
   - 対象ファイル・シンボルのキーワード絞り込み検索:
     ```bash
     gh issue list --label review-finding --state all --search "<対象ファイル名または関数名>"
     ```
2. **安全側判定**:
   - 検索結果が上限（limit）に到達した場合や API エラー等で結果が不完全な場合は、「重複なし」と断定せずに対象パスでの絞り込み検索を必ず実施する。それでも不確実な場合は「重複未検証」として差し戻す。
3. **重複判定**:
   - 同一の対象ファイル・同一関数・同一の原因・同一の失敗条件に関する Issue が既に Open（または是正中）でないか確認する。
4. **重複時のアクション (RF-05)**:
   - 既存 Issue に包含される、または同一原因の別側面であると判断された場合は、**新規 Issue の起票を中止（差し戻し/保留）**し、重複対象の Issue 番号および重複判断根拠をレビュー担当へ報告する。
   - **既存 Issue へのコメント追記は自動実行しない**。利用者の明示的な指示または承認がある場合のみ実施する（誤判定による情報混入の防止）。

---

## 4. GitHub Issue 起票と相互リンク手順

バリデーションをすべて通過した場合のみ、以下の手順で起票する。

### 1. Finding Issue の起票 (RF-02)

```bash
# Blocking: Yes の場合（P0, P1, または例外的な P2 Blocking）
gh issue create \
  --title "[Finding] <ScopeID/PR>-<連番>: <指摘概要>" \
  --body-file /tmp/finding_<id>.md \
  --label "review-finding,severity:<pX>,blocking:yes"

# Blocking: No の場合（通常の P2, P3）
gh issue create \
  --title "[Finding] <ScopeID/PR>-<連番>: <指摘概要>" \
  --body-file /tmp/finding_<id>.md \
  --label "review-finding,severity:<pX>"
```

※ ラベルは `review-finding`、重大度に応じた `severity:p0` 〜 `severity:p3` に加え、`Blocking: Yes` の場合は必ず `blocking:yes` を付与する。

### 2. 検出元（Scope Issue または PR）への相互リンク記録 (RF-01)

起票された Finding Issue の URL / 番号を取得し、検出元の Scope Issue または PR にコメント追記する（検出元が Closed であってもコメント追記は可能）：

```bash
gh issue comment <source_issue_no> --body "検出された指摘を Finding Issue #<new_issue_no> として起票しました。"
```

### 3. 起票失敗時の安全停止

`gh` CLI がネットワーク障害や認証エラーで失敗した場合、後続のクローズ処理や完了処理を中断し、作業ツリーを保持したまま安全停止して人間に報告する。

---

## 5. Finding 解決完了の管理手順（ライフサイクル・マージ後再検証）

Finding Issue のライフサイクルにおいて、**「レビューGO」「PRマージ完了」「Finding解決完了」** の 3 つの状態を明確に分離して管理する。

### 5.1 状態遷移モデル

| 状態                   | Issue State        | ラベル                                             | 条件・説明                                                                                          |
| :--------------------- | :----------------- | :------------------------------------------------- | :-------------------------------------------------------------------------------------------------- |
| **修正待ち**           | Open               | `status:ready-to-fix`                              | 指摘が受理され、修正作業中（初期状態）。                                                            |
| **レビュー合格**       | Open               | `status:ready-to-fix`                              | 修正 PR のレビューで GO を獲得。PR マージ待ち（**Issue は Open を維持**）。                         |
| **マージ済み・未検証** | Open または Closed | `status:ready-to-fix` 等（`status:verified` なし） | PR が対象ブランチへマージされた直後の状態。自動クローズ方式時は一時的に Closed となるが未検証扱い。 |
| **解決完了**           | Closed             | `status:verified`                                  | マージ済みコミットでの再検証が合格し、全是正完了条件を満たしてクローズされた状態。                  |
| **再検証失敗**         | Open               | `status:ready-to-fix` 等                           | マージ後の再検証で不合格または回帰が検出された状態。Closed だった場合は Re-open して再修正。        |

※ 新規のステータスラベルは追加せず、既存のラベル体系で運用する。

### 5.2 解決完了の判定構造（2段階判定）

チェックリストの循環依存を防止し、状態遷移を安全に実行するため、解決完了処理を **Phase 1（実行可否判定）** と **Phase 2（状態更新と事後確認）** の 2 段階に明確に分離する。

#### Phase 1: 完了処理の実行可否判定（事前確認）

以下の 6 条件を**すべて**満たす場合のみ、Phase 2（ラベル更新・クローズ処理）への進行を許可する：

1. **修正 PR の特定**: 対象 Finding に対応する修正 PR 番号および URL が特定されていること。
2. **PR マージの完了**: 修正 PR が対象ブランチへマージ済み（Merged）であること。
3. **マージコミットの確認**: マージ先ブランチ（`main` 等）とマージコミット SHA を確認できること。
4. **再検証の合格**: マージコミットに対して、単体テスト・全体自動テスト・回帰テストが実行され、すべて合格していること。
5. **是正完了条件の達成**: Finding Issue 本文に記載された「是正完了条件」チェックリスト（Markdown の `- [ ]`）をすべて満たしていること。
6. **結果と SHA の記録**: 再検証結果および対象マージコミット SHA が Finding Issue のコメントに記録されていること。

> [!CAUTION]
> Phase 1 の条件を 1 つでも満たさない場合、Phase 2 へ進んではならず、`status:verified` を付与してはならない。

#### Phase 2: 状態更新と事後確認（事後検証）

Phase 1 をすべて満たした後にのみ、以下の順序で状態更新と事後検証を実行する：

1. `status:ready-to-fix` ラベルの削除
2. `status:verified` ラベルの付与
3. Issue のクローズ（手動クローズ方式、または自動クローズ不発時のフォールバック実行）
4. **Issue 状態の再取得と事後確認**: `gh issue view <issue_no> --json state,labels` を実行し、Issue が `state: CLOSED` かつ `status:verified` ラベルが付与されていることを機械的に確認する。

> [!IMPORTANT]
> **途中失敗時の安全停止**:  
> ラベル更新やクローズ処理の途中で API エラー等が発生した場合、完了成功と報告してはならない。処理を中断し、現在の Issue 状態（どの操作まで完了し、何が未完了か）を人間に報告して安全停止すること。

### 5.3 クローズ方式の選択方針

原則として、**手動クローズ方式を標準とする**。  
理由: 本リポジトリの設計方針（`PLAN.md`）では、PR マージ後の再検証を経て初めて Finding 解決完了と定義しているためである。  
※ クローズ方式の指定がない、または判断がつかない場合は、エージェントが独断で自動クローズを選択せず、標準の手動クローズ方式に従うこと。

### 5.4 手動クローズ手順（標準方式）

1. **PR 作成**: PR 本文ではクローズ型キーワードを避け、非クローズ型参照（例: `Refs #36`）を記載する。
2. **コードレビュー**: `repo-review` 等でレビューを実施し、GO を確認する。依頼元から明示的な指示がある場合に限り、レビュアーがレビュー結果コメントを Finding Issue に投稿（`gh issue comment`）してよい。ただし**クローズ・再オープン・ラベル変更の権限は依頼元のみにあり、レビュアーが Issue の状態を変更することは厳禁である**。この時点で Issue は Open（`status:ready-to-fix`）のまま維持する。
3. **PR マージ**: 人間が PR をマージする（エージェントによる `gh pr merge` は禁止）。
4. **Phase 1 検証（実行可否判定）**:
   - マージ先ブランチ（`main` 等）を最新化し、マージコミット SHA に対して単体テスト・全体自動テストを実行して是正完了条件を検証する。
   - Finding Issue に再検証結果、対象マージコミット SHA、テストログをコメント投稿する。
   - Phase 1 の 6 要件がすべて満たされていることを確認する。
5. **Phase 2 実行（状態更新と事後確認）**:
   - `status:ready-to-fix` ラベルを削除:
     ```bash
     gh issue edit <issue_no> --remove-label "status:ready-to-fix"
     ```
   - `status:verified` ラベルを付与:
     ```bash
     gh issue edit <issue_no> --add-label "status:verified"
     ```
   - Issue をクローズ:
     ```bash
     gh issue close <issue_no> --reason "completed"
     ```
   - **事後確認**:
     ```bash
     gh issue view <issue_no> --json state,labels
     ```
     Issue が `state: CLOSED` かつ `labels` に `status:verified` が含まれていることを確認する。

### 5.5 GitHub 自動クローズ方式の運用（不発時フォールバック必須）

PR 本文で GitHub 標準の自動クローズキーワード（例: `Closes #36`）が明示的に使用された場合の運用手順：

1. **マージ前クローズの禁止**: PR マージ前にエージェントが手動で Issue をクローズすることは厳禁とする。
2. **マージ後の Issue 実状態の再取得（必須）**:
   - PR マージ後、直ちに実際の Issue 状態を取得する：
     ```bash
     gh issue view <issue_no> --json state,labels
     ```
   - ※ API 取得失敗時は処理を中断し、推測で処理を続行しない（安全側停止）。
3. **状態別の対応分岐**:
   - **Case A: Closed の場合（自動クローズ正常発生）**:
     - 一時的な未検証状態（`Closed` かつ `status:verified` なし）と認識する。
     - マージ後再検証（Phase 1）を実施する。
     - 成功時: Phase 2（`status:ready-to-fix` 削除、`status:verified` 付与、事後確認）を実行する。
   - **Case B: Open の場合（自動クローズ不発時）**:
     - リポジトリ設定やマージ先ブランチの影響で自動クローズが発生しなかったと記録する。
     - **重要**: Issue が Open のまま `status:verified` だけを付与して放置することを厳禁とする（不完全な状態で完了扱いとしない）。
     - マージ後再検証（Phase 1）を実施する。
     - 成功時: 手動クローズ手順（5.4 の Phase 2）へフォールバックし、`status:ready-to-fix` 削除、`status:verified` 付与、`gh issue close` を実行して、最終的に `Closed` かつ `status:verified` を確立・確認する。
4. **再検証失敗時**:
   - Case A（Closed だった場合）: 直ちに Issue を Re-open する：
     ```bash
     gh issue reopen <issue_no> --comment "マージ後再検証で不具合が検出されたため、Re-open して再修正を行います。"
     ```
   - Case B（Open のままだった場合）: そのまま Open を維持し、失敗理由をコメント記録する。
   - いずれの場合も `status:verified` は絶対に付与せず、未解決として追跡する。

### 5.6 自動操作の安全制約

GitHub Issue の状態変更は外部システムへの永続化操作であるため、以下の制約を厳守する：

- **判定段階の分離遵守**: チェックリストの循環依存を防止するため、Phase 1（実行可否判定）が完了する前に Phase 2（ラベル更新・クローズ）を実行してはならない。
- **自動クローズ不発時の Open 放置禁止**: 自動クローズが不発だった場合、Open のまま `status:verified` のみ付与して処理を終了してはならない。必ず手動クローズへフォールバックして最終状態を確認する。
- **途中失敗時の安全停止と報告**: 状態更新の途中で失敗した場合、成功扱いとせず、現在の状態と未完了操作を報告する。
- **Skill 実行を理由としたクローズの禁止**: Skill の起動やテスト通過のみを理由として Issue をクローズしてはならない。
- **レビューGO 契機の自動クローズ禁止**: レビューGO を検知しても、Finding Issue を自動クローズしてはならない。
- **マージ前クローズの禁止**: 修正 PR がマージされる前の Finding Issue クローズは一切禁止する。
- **レビュアーによるクローズ・状態変更禁止（クローズ実行権限の限定）**: Finding Issue のクローズ（`gh issue close`）、再オープン、ラベル変更は、PR マージ後の再検証を完了した**依頼元（requester / implementer）のみが実施する**。レビュー結果コメント投稿は依頼元からの明示的指示がある場合のみレビュアーに許可されるが、レビュー担当のレビュアーエージェント（reviewer）が自律的に Issue をクローズまたはラベル変更することは厳禁とする。
- **人間の承認方針の遵守**: Issue のクローズ・再オープン・ラベル更新は、実行権限と人間の指示・承認方針に従って行う。
- **未達時の現状維持と報告**: 完了条件を満たしていない場合は、状態を変更せず、未達理由を人間に報告する。
- **API 失敗時の安全側判定**: `gh` コマンドの API エラーや通信障害時は、成功扱いとせず処理を中断する。
- **PR マージの禁止**: AI エージェントによる `gh pr merge` は禁止を維持する。

---

## 6. 参照資料

- チェックリスト・詳細テンプレート: [references.md](references.md)
- レビュー基準本体: [repo-review/SKILL.md](../repo-review/SKILL.md)
- エージェントガイドライン: [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)
