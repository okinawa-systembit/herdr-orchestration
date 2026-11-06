---
name: review-finding
description: >-
  Validates, standardizes, and creates GitHub Finding Issues from code review
  findings. Ensures severity/blocking consistency, refutation checks, and completion/regression criteria.
---

# review-finding — GitHub レビュー指摘（Finding Issue）起票・品質検証 Skill

本 Skill は、`herdr-orchestration` リポジトリにおいて、コードレビュー（通常 PR レビュー、ワイドレビュー等）で検出された未解決指摘（Finding）を **GitHub Finding Issue として起票する前に品質検証を行い、整合性を保って起票・相互リンクおよび解決検証を行うリポジトリ専用 Skill** である。

正本（Source of Truth）は **GitHub Issues** であり、リポジトリ内に独自の台帳や二重管理用ファイルは追加しない。

---

## 1. 目的と責務境界

レビュー担当エージェント（`repo-review` 等）と、Issue 起票・品質検証を担当する本 Skill、および人間の責務を明確に分離する（権限マトリクスの正本は [repo-workflow §7](../repo-workflow/SKILL.md#github-write-matrix)）。

| 機能                              | 担当             | 責務と制約                                                                                                                                |
| :-------------------------------- | :--------------- | :---------------------------------------------------------------------------------------------------------------------------------------- |
| **不具合探索・反証確認**          | `repo-review`    | コードの批判的精査、8つの反証確認（[references.md §2](../repo-review/references.md#refutation-checks)）。                                 |
| **重大度・Blocking 判定**         | `repo-review`    | 技術的影響度（P0〜P3）とマージ可否（Blocking/Non-blocking）の決定（[references.md §1](../repo-review/references.md#review-principles)）。 |
| **Finding 本文の構造化**          | `review-finding` | テンプレートに沿ったメタデータ、詳細、是正完了条件の成型。                                                                                |
| **起票前品質バリデーション**      | `review-finding` | 必須項目、具体性、重大度・Blocking の整合性検証。                                                                                         |
| **既存 Issue との重複確認**       | `review-finding` | GitHub 上の既存 Finding との突合・類似度チェック。                                                                                        |
| **GitHub Issue 起票・相互リンク** | `review-finding` | `gh issue create` による起票、検出元 PR / Issue へのリンク記録。                                                                          |
| **Finding マージ後再検証・記録**  | `review-finding` | PR マージ確認、マージコミットに対する再検証、解決確認コメント（人間へのクローズ推奨報告）の記録。                                         |
| **Issue クローズ・ラベル変更**    | **人間のみ**     | `gh issue close` / `reopen` / `edit`（`status:verified` 付与等）は人間が実施（エージェント実行厳禁）。                                    |

> [!CAUTION]
> **判定改変の禁止**:  
> `review-finding` は、レビュー担当が下した重大度（P0〜P3）や Blocking 判定を**エージェントの独断で変更してはならない**。論理矛盾や根拠不足（例: P2 Blocking なのに理由が未記載、P3 なのに Blocking 指定等）がある場合は起票を保留し、**レビュー担当エージェントへ差し戻す**こと。

> [!CAUTION]
> **エージェントによる Issue クローズ・ラベル変更の禁止（[repo-workflow §7](../repo-workflow/SKILL.md#github-write-matrix) 準拠）**:  
> Finding Issue を含む GitHub Issue のクローズ（`gh issue close`）、再オープン（`gh issue reopen`）、およびラベル変更（`gh issue edit`）は**人間のみが実施する**。エージェントは PR マージ後の再検証（Phase 1）と検証結果コメントの投稿（人間へのクローズ・再オープン推奨報告）までを担当する。

---

## 2. 適用範囲

- **GitHub 管理対象レビュー**:
  - 通常 Pull Request のレビューにおいて、マージ前是正または将来追跡（`defer`）が必要な未解決指摘。
  - ワイドレビュー（総点検）で検出され、人間と合意した Finding Issue 起票候補。
- **Herdr 内部レビューでの扱い**:
  - エージェント間の日常的な handoff レビュー（ローカル内部レビュー完結）では GitHub 起票は**不要**であり、本 Skill による GitHub 投稿は行わない。

---

## 3. 起票前品質バリデーション仕様

GitHub への書き込み（`gh issue create`）を実行する前に、以下の 4 段階の検査を実施する。1 つでも違反がある場合、Issue 起票を行わずレビュー担当へ差し戻す。

### 段階 1: メタデータ整合性検証

1. **検出元の確認 (RF-01)**:
   - 検出元 PR または Issue 番号が明記され、GitHub 上で実在していること（Open / Closed の状態は問わない。Closed の検出元をエージェントが Re-open することは禁止）。
2. **対象コミット**:
   - 検出対象のコミット SHA（原則 7 文字以上の正規 SHA）が記載されていること。
3. **重大度と Blocking 判定・ラベルの整合性 (RF-02)**:
   - 重大度（P0〜P3）・Blocking 判定規則・P1 の 4 要件の定義は、唯一の正本である **[repo-review/references.md §1](../repo-review/references.md#review-principles)** に従う。
   - **ラベル整合**:
     - **P0 / P1（`Blocking: Yes`）**: 起票時に `blocking:yes` ラベル付与対象。
     - **P2**: 原則 `Blocking: No`（`blocking:yes` なし）。例外的に `Blocking: Yes` とする場合は、具体的な重大影響と Blocking 理由が記述されていることを確認した上で `blocking:yes` ラベルを付与する。
     - **P3**: `Blocking: No` のみ（`Blocking: Yes` 指定は差し戻し）。

### 段階 2: 不具合内容の具体性・客観的根拠の検証

1. **対象箇所の実在性**: 対象ファイルパスがリポジトリ内に実在し、該当行番号（`file.py:L123`）またはシンボルが特定されていること。
2. **発生条件の具体性**: どのような入力引数、環境条件、並行実行順序、状態遷移の際に問題が発生するかが具体的に記述されていること。
3. **期待動作と実際の動作の対比**: 「本来どう動くべきか（仕様・契約）」と「実際どう動いてしまうか（例外、データ破壊、放置等）」が明確に対比されていること。
4. **反証確認の確認**: 正本である **[repo-review/references.md §2（8つの反証確認の問い）](../repo-review/references.md#refutation-checks)**（テンプレートは [references.md#refutation-checks](references.md#refutation-checks)）を確認した結果が記載されていること。

### 段階 3: 是正完了・品質保証項目の検証

1. **`## 是正完了条件`**: 修正確認時に客観的に成否をチェックできる **Markdown チェックリスト（`- [ ] ...`）が最低 2 項目以上** 記載されていること。
2. **`## 回帰テスト条件`**: 修正確認時に実行すべきテストコマンド（例: `npm run test:orchestrator`）または新規追加すべきテストケース要件が明記されていること。
3. **`## 維持すべき既存保証`**: 修正によって損なわれてはならない既存の契約・正常系動作・品質ゲートが明記されていること。

### 段階 4: 既存 Issue 重複検証 (RF-03, RF-05)

1. **多層検索の実施 (RF-03)**:
   ```bash
   gh issue list --label review-finding --state all --limit 300
   gh issue list --label review-finding --state all --search "<対象ファイル名または関数名>"
   ```
2. **安全側判定**: 検索結果が上限（limit）に到達した場合や API エラー等で結果が不完全な場合は、対象パスでの絞り込み検索を実施し、それでも不確実な場合は「重複未検証」として差し戻す。
3. **重複時のアクション (RF-05)**:
   - 同一原因の既存 Issue が存在する場合は**新規 Issue の起票を中止（差し戻し/保留）**し、重複対象の Issue 番号および判断根拠を報告する。
   - **既存 Issue へのコメント追記は自動実行しない**（利用者の明示的な指示がある場合のみ実施）。

---

## 4. GitHub Issue 起票と相互リンク手順

バリデーションをすべて通過した場合のみ、各エージェント環境の標準ファイル操作機能で `.input/tmp/finding-<id>.md`（[基本方針 原則 1](../../../docs/core-principles.md#principle-1)・[原則 2](../../../docs/core-principles.md#principle-2)）を作成し、以下の手順で起票する。

### 1. Finding Issue の起票 (RF-02)

```bash
# Blocking: Yes の場合（P0, P1, または例外的な P2 Blocking）
gh issue create \
  --title "[Finding] <PR/ScopeID>-<連番>: <指摘概要>" \
  --body-file .input/tmp/finding-<id>.md \
  --label "review-finding,severity:<pX>,blocking:yes,status:ready-to-fix" && rm -f .input/tmp/finding-<id>.md

# Blocking: No の場合（通常の P2, P3）
gh issue create \
  --title "[Finding] <PR/ScopeID>-<連番>: <指摘概要>" \
  --body-file .input/tmp/finding-<id>.md \
  --label "review-finding,severity:<pX>,status:ready-to-fix" && rm -f .input/tmp/finding-<id>.md
```

### 2. 検出元への相互リンク記録 (RF-01)

起票された Finding Issue の番号を取得し、検出元の PR または Issue に記録する（PR コメントまたは `bash scripts/review-sync.sh comment-issue` / `gh issue comment`）。`gh` CLI が失敗した場合は後続処理を中断し、人間に報告する。

---

## 5. Finding 解決完了の管理手順（ライフサイクル・マージ後再検証）

Finding Issue のライフサイクルにおいて、**「レビューGO」「PRマージ完了」「Finding解決完了」** の 3 つの状態を明確に分離して管理する。

### 5.1 PR 本文の Issue リンク方式（通常 Issue と Finding Issue の区別）

- **通常の設計・実装 Issue**: PR 本文に `Closes #<Issue番号>` を記載し、人間の PR マージに伴う自動クローズを標準とする（[repo-workflow §7](../repo-workflow/SKILL.md#github-write-matrix)）。
- **Finding Issue**: マージ後の再検証（Phase 1）を経てから人間がクローズするため、修正 PR の本文には原則として **`Refs #<FindingIssue番号>`**（非クローズ型参照）を記載する（※ 人間から明示的に `Closes #<FindingIssue番号>` の指定があった場合のみ自動クローズ方式を許容する）。

### 5.2 状態遷移モデル

| 状態                   | Issue State        | ラベル                                             | 条件・説明                                                                                    |
| :--------------------- | :----------------- | :------------------------------------------------- | :-------------------------------------------------------------------------------------------- |
| **修正待ち**           | Open               | `status:ready-to-fix`                              | 指摘が受理され、修正作業中（初期状態）。                                                      |
| **レビュー合格**       | Open               | `status:ready-to-fix`                              | 修正 PR のレビューで GO を獲得。PR マージ待ち（**Issue は Open を維持**）。                   |
| **マージ済み・未検証** | Open または Closed | `status:ready-to-fix` 等（`status:verified` なし） | 人間により PR がマージされた直後の状態。                                                      |
| **解決完了**           | Closed             | `status:verified`                                  | エージェントによるマージ後再検証（Phase 1）が合格し、**人間がラベル更新・クローズした状態**。 |
| **再検証失敗**         | Open               | `status:ready-to-fix` 等                           | マージ後の再検証で不合格・回帰が検出され、人間により Open 維持または Re-open された状態。     |

### 5.3 解決完了の 2 段階手順（エージェント検証と人間による状態更新の分離）

[repo-workflow §7](../repo-workflow/SKILL.md#github-write-matrix) に基づき、**Phase 1（エージェントによるマージ後再検証と結果記録）** と **Phase 2（人間によるラベル更新・クローズ）** を分離する。

#### Phase 1: エージェント（依頼元）によるマージ後再検証とコメント記録

人間によって修正 PR がマージされた後、依頼元エージェントは以下の 6 条件を検証し、結果を Finding Issue に記録（`bash scripts/review-sync.sh comment-issue` または人間への報告）する：

1. **修正 PR の特定**: 対象 Finding に対応する修正 PR 番号および URL が特定されていること。
2. **PR マージの完了**: 修正 PR が対象ブランチへマージ済み（Merged）であること。
3. **マージコミットの確認**: マージ先ブランチ（`main` 等）とマージコミット SHA を確認できること。
4. **再検証の合格**: マージコミットに対して、単体テスト・全体自動テスト・回帰テストが実行され、すべて合格していること。
5. **是正完了条件の達成**: Finding Issue 本文の「是正完了条件」チェックリスト（`- [ ]`）をすべて満たしていること。
6. **結果と SHA の記録**: 再検証結果、対象マージコミット SHA、および**人間向けのクローズ推奨（または再検証失敗時は Open 維持 / Re-open 推奨）の案内**がコメントとして記録されていること（テンプレートは [references.md §6](references.md#reverification-comment-template) 参照）。

#### Phase 2: 人間による状態更新（ラベル変更・クローズ / 再オープン）

- Phase 1 の再検証合格報告を受けた後、**人間**が `status:ready-to-fix` の削除、`status:verified` の付与、および Issue のクローズ（または再検証失敗時の Re-open）を実施する。
- **エージェントの禁止事項**: エージェント（レビュワー・依頼元とも）が `gh issue close`、`gh issue reopen`、`gh issue edit`（ラベル変更）を実行することは厳禁とする。

---

## 6. 参照資料

- チェックリスト・詳細テンプレート: [references.md](references.md)
- レビュー判定・品質基準の正本: [repo-review/references.md](../repo-review/references.md#review-principles)
- レビュー手順本体: [repo-review/SKILL.md](../repo-review/SKILL.md)
- ロール別 GitHub 書き込み権限の正本: [repo-workflow §7](../repo-workflow/SKILL.md#github-write-matrix)
- エージェントガイドライン: [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)
